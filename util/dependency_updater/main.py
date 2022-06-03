# Copyright (C) 2021 The Qt Company Ltd.
# SPDX-License-Identifier: LicenseRef-Qt-Commercial OR LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only

import argparse
import os
import sys

import yaml

from tools import Namespace, config as Config, state, toolbox, dependency_resolver, repo as Repo


def parse_args(print_help: bool = False) -> Namespace:
    parser = argparse.ArgumentParser(description="Execute a round of dependency updates by branch.", formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('--sim', dest='simulate', action='store_true',
                        help="Simulate a run of the tool, but don't send any alerts or save\n"
                             "the final state.")
    parser.add_argument('--reset', dest='reset', action='store_true',
                        help="Forget current update state of [branch], then exit. Requires branch.")
    parser.add_argument('--pauseOnFail', dest='pause_on_finish_fail', action='store_true',
                        help="If the round finished with failures in blocking repos, do not reset\n"
                             "the round. Hold the current state until rewound or reset.")
    parser.add_argument('--retryFailed', dest='retry_failed', action='store_true',
                        help="Retries failed updates on top of the branch HEAD for a given module.\n"
                             "Generally only used when a round has failed via use of --pauseOnFail\n"
                             "or when non-blocking modules fail to merge.")
    parser.add_argument('--reset-stage-count', dest='reset_stage_count', action='store_true',
                        help="Reset all in-progress and retrying repos' stage attempt counters.\n"
                             "Useful intervention in an ongoing round which is about to fail.")
    parser.add_argument('-b', '--branch', dest='branch', type=str, default="dev",
                        help="Branch to update against.")
    parser.add_argument('--noState', dest='no_state', action='store_true',
                        help="Perform this update isolated from any saved state for this branch.\n"
                             "Do not save state when completed. Enable this switch to perform\n"
                             "one-off updates of repos independent of a normal round. Will not\n"
                             "interfere with ongoing rounds.",
                        default=False)
    parser.add_argument('--default-repos', dest='update_default_repos', action='store_true',
                        help="Update all modules in qt5 marked as 'essential',\n"
                             " 'addon', 'deprecated', 'ignore', or 'preview'",
                        default=False)
    parser.add_argument('-p', '--prefix', dest='repo_prefix', default="qt/",
                        help="Prefer repos with this prefix when choosing the sha to use in\n"
                             "the update. Intended for use against 'qt/tqtc-' repos.")
    parser.add_argument('--head', dest='use_head', action='store_true',
                        help="Use latest HEAD for all dependencies instead of the latest qt5\n"
                             "dependency map as the starting point.\n"
                             "Implied by --default-repos.")
    parser.add_argument('-c', '--sweepChanges', dest='sweep_changes', action='store_true',
                        help="Search gerrit for changes with the Submodule Update Bot\n"
                             "(or the current user) added as a reviewer on the change.\n"
                             "Sweep those changes in with this submodule update round.")
    parser.add_argument('--rewind', dest='rewind_module',
                        help="Rewind the round to the specified module and recalculate\n"
                             "dependencies. Useful to pull in a fix required by leaf modules\n"
                             "without restarting the round.")
    parser.add_argument('--dropDependency', dest="drop_dependency",
                        help="IMPORTANT: This action is destructive!\n"
                             " FORMAT: dependency[:repo,]\n"
                             " Specify the dependency to drop. If it should be selectively\n"
                             " dropped, follow the dependency with a colon ':', and a\n"
                             " comma-separated list of repos to drop the dependency from.\n"
                             " If a list of repos to drop the dependency from is not supplied,\n"
                             " the dependency will be dropped from ALL repos being processed.")
    parser.add_argument('-s', '--stage', dest='stage', action='store_true',
                        help="Automatically stage proposed updates if able to self-approve.")
    parser.add_argument('-q', '--qt5Update', dest='update_supermodule', action='store_true',
                        help="Perform an update to the qt5/qt6 supermodule when all\n"
                             "updates have succeeded")
    parser.add_argument('--yoctoUpdate', dest='update_yocto_meta', action='store_true',
                        help="Update the yocto/meta-qt6 repo with the shas from this round.")
    parser.add_argument('-r', '--repos', dest="repos", nargs='*',
                        help="List of repos to update.\n")
    parser.add_argument('-n', '--nonBlockingRepos', dest="non_blocking_repos", nargs='*',
                        help="List of non-blocking repos to update. These will be included in the\n"
                             "round but will not cause a failure if they fail to integrate unless\n"
                             "another blocking module depends on it.")
    if print_help:
        parser.print_help()
    args = parser.parse_args()

    if args.simulate:
        print("INFO: Running in simulation mode. No alerts will be sent,"
              " and state will not be saved!")
    return args


def clear():
    """Clear the console screen using the OS built-in methods."""
    if sys.platform == "win32":
        os.system('cls')
    else:
        os.system('clear')


def main():
    # Initial setup
    config = Config._load_config("config.yaml", parse_args())
    config.datasources.load_datasources(config)
    config.state_repo = state.check_create_local_repo(config)
    if config.args.reset:
        state.clear_state(config)
        exit()
    if config.args.update_default_repos:
        config.args.use_head = True
    if config.args.rewind_module:
        config.rewind_module = toolbox.search_for_repo(config, config.args.rewind_module)
    # Load the state cache
    config.state_data = state.load_updates_state(config)
    # Check to see if we should abort as finished-failed
    if config.state_data.get("pause_on_finish_fail"):
        if not any([config.args.retry_failed, config.args.rewind_module]):
            print(
                "Round is in Failed_finish state and this round was run in Paused On Finish Fail Mode.\n"
                "To move the round forward, run the script with one of the following --reset,"
                " --rewind, or --retry_failed")
            parse_args(print_help=True)
            exit()
        # Continue the round and try again.
        del config.state_data["pause_on_finish_fail"]
        if config.args.retry_failed:
            for module in [r for r in config.state_data.values()
                           if r.progress == Repo.PROGRESS.DONE_FAILED_BLOCKING]:
                toolbox.reset_module_properties(config, module)
    report_new_round = False
    if not config.state_data and config.args.update_default_repos:
        # State data is always empty if the round is fresh.
        report_new_round = True

    # Collect the list of qt5 modules for our reference.
    config.qt5_default = toolbox.get_qt5_submodules(config, ["essential", "addon", "deprecated",
                                                               "preview"])

    # Collect Repo objects for everything in the cache or list of qt5 modules, as necessary.
    repos = toolbox.get_repos(config)
    if repos.get(f"{config.args.repo_prefix}qtbase") and report_new_round:
        qtbase = repos[f"{config.args.repo_prefix}qtbase"]
        config.teams_connector.send_teams_webhook_basic(
            repo=qtbase,
            text=f"INFO: New round started on {qtbase.branch} with"
                 f" {qtbase.id}@{qtbase.original_ref}")
    # Update the working state with any newly added repos passed to the script.
    config.state_data = state.update_state_data(config.state_data, repos)

    # Update the progress of all repos in the state since the last run of the tool.
    for repo in config.state_data.values():
        repo.progress, repo.proposal.merged_ref, repo.proposal.gerrit_status = \
            toolbox.get_check_progress(config, repo)

    # Collect necessary data if dropping a dependency from a repo.
    if config.args.drop_dependency:
        split = config.args.drop_dependency.split(":")
        config.drop_dependency = toolbox.search_for_repo(config, split[0])
        if len(split) > 1:
            config.drop_dependency_from = \
                [toolbox.search_for_repo(config, r) for r in split[1].split(",")]
        else:
            config.drop_dependency_from = repos

    # Discover dependencies and add any missing repos to the list. We might need to update them too.
    config.state_data = dependency_resolver.discover_repo_dependencies(config)

    # Mark non-blocking repos as blocking if a blocking repo depends on it.
    config.state_data = dependency_resolver.cross_check_non_blocking_repos(config)

    # Undo any work done in modules which depend on rewind_module, if set.
    if config.args.rewind_module:
        # Set the module to rewind to so that we generate new proposals for
        # any modules which depend directly or indirectly on it.
        if config.state_data[config.rewind_module.id].progress < Repo.PROGRESS.DONE:
            # Rewinding to a module which hasn't merged yet will break the round!
            print(f"Unable to rewind to a not-yet-updated module. {config.rewind_module.id}"
                  f" is in state: {config.state_data[config.rewind_module.id].progress.name}."
                  f"\nHint: Try rewinding to one if its dependencies:"
                  f" {config.state_data[config.rewind_module.id].dep_list}")
        else:
            config.state_data[config.rewind_module.id].proposal.change_id = ""
            new_sha = toolbox.get_head(config, config.state_data[config.rewind_module.id], True)
            print(f"\nRewinding round to {config.rewind_module.id} @ {new_sha}\n")
            config.state_data[config.rewind_module.id].original_ref = new_sha
            config.state_data[config.rewind_module.id].proposal.merged_ref = new_sha
            config.state_data[config.rewind_module.id].progress = Repo.PROGRESS.DONE_NO_UPDATE
            config.teams_connector.send_teams_webhook_basic(
                repo=config.rewind_module,
                text=f"INFO: Rewinding '{config.args.branch}' to {new_sha}."
                     f" Modules depending on {config.rewind_module.id} have been reset.")
            if config.args.update_supermodule and config.state_data.get("qt/qt5") \
                    and not config.rewind_module.id == "yocto/meta-qt6":
                del config.state_data["qt/qt5"]
            if config.args.update_yocto_meta and config.state_data.get("yocto/meta-qt6") \
                    and not config.rewind_module.id == "qt/qt5":
                del config.state_data["yocto/meta-qt6"]

    # bump the progress of repos that have had updates pushed and merged.
    for repo in config.state_data.values():
        repo.progress, repo.proposal.merged_ref, repo.proposal.gerrit_status = \
            toolbox.get_check_progress(config, repo)

    # Retry any modules which are ready but have failed to merge in CI.
    for repo in [r for r in config.state_data.values() if r.progress == Repo.PROGRESS.RETRY]:
        if config.args.reset_stage_count:
            repo = toolbox.reset_stage_count(repo)
        if repo.retry_count < 3:
            if repo.retry_count == 1:
                # Send a warning message if the update has failed to merge twice.
                print(f"Collecting log snippet for failed integration in {repo.id}...")
                failed_tests_snip = toolbox.parse_failed_integration_log(config, repo)
                print(failed_tests_snip)
                config.teams_connector.send_teams_webhook_module_failed(repo,
                                                                        text_override=f"Dependency update on *{repo.id}*"
                                                                                      f" is failing in **{repo.branch}**. Two automatic retries left.",
                                                                        test_failures=failed_tests_snip,
                                                                        pause_links=True)
            if config.args.stage:
                repo = toolbox.retry_update(config, repo)
            else:
                print(
                    f"WARN: Unable to re-stage {repo.id} because automatic staging is not enabled.\n"
                    f"You must stage {repo.proposal.change_id} manually!")
        elif repo.is_non_blocking:
            print(f"Dependency Update to non-blocking repo {repo.id} failed.")
            repo.progress = Repo.PROGRESS.DONE_FAILED_NON_BLOCKING
        else:
            # Clear state and reset, or allow broken updates to fail so others can be updated.
            # state.clear_state(config)  # Hard-disabled reset for now due to long turn-around-time of bugfixes.
            print(f"Dependency Update to {repo.id} failed.")
            repo.progress = Repo.PROGRESS.DONE_FAILED_BLOCKING
            config.teams_connector.send_teams_webhook_module_failed(repo,
                                                                    test_failures=toolbox.parse_failed_integration_log(
                                                                        config, repo))

    # Check and see if we're ready to push a supermodule update if all the blocking repos
    # Have finished updating successfully.
    config.state_data = toolbox.do_try_supermodule_updates(config)

    # Finally, we're ready to start resolving dependencies for modules which are PROGRESS.READY
    for repo in [r for r in config.state_data.values() if r.progress < Repo.PROGRESS.IN_PROGRESS]:
        print(f"Checking inconsistencies in: {repo.id}")
        repo.proposal.inconsistent_set = \
            dependency_resolver.discover_dep_inconsistencies(config, repo)
        print(f"{repo.id}: {repo.proposal.inconsistent_set}")

    # Generate current_state for later comparison to comprehend if the script took any action
    current_state, formatted_state = toolbox.state_printer(config)
    print("\n-=-=-=-=-=-State before pushing updates-=-=-=-=-=-")
    print(formatted_state)
    print("-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=\n")

    # Create new dependencies.yaml proposals for all PROGRESS.READY modules.
    config.state_data = dependency_resolver.recursive_prepare_updates(config)

    for repo in [r for r in config.state_data.values() if r.progress == Repo.PROGRESS.READY]:
        print(f"Proposed update to {repo.id}:")
        print("-----------------------------")
        print(yaml.dump(repo.proposal.proposed_yaml))
        print("-----------------------------")
        print()

    # Do the actual gerrit pushes and staging of changes.
    if not config.args.simulate:
        for repo in [r for r in config.state_data.values() if
                     r.progress == Repo.PROGRESS.READY and not r.is_supermodule]:
            repo.proposal.change_id, repo.proposal.change_number \
                = toolbox.search_existing_change(config, repo, "Update dependencies")
            repo.proposal = toolbox.push_submodule_update(config, repo)
            if repo.proposal.change_id:
                repo.progress = Repo.PROGRESS.IN_PROGRESS
            elif repo.proposal.merged_ref:
                repo.progress = Repo.PROGRESS.DONE_NO_UPDATE
        for repo in [r for r in config.state_data.values() if
                     r.progress == Repo.PROGRESS.IN_PROGRESS]:
            if config.args.stage and toolbox.stage_update(config, repo):
                repo.stage_count += 1

    # Check a second time if we need to do a supermodule update, as the above step may
    # have resulted in a bunch of repos considered PROGRESS.DONE_NO_UPDATE
    config.state_data = toolbox.do_try_supermodule_updates(config)

    final_state, formatted_state = toolbox.state_printer(config)
    if final_state != current_state:
        print("\n-=-=-=-=-=-State after pushing updates-=-=-=-=-=-")
        print(formatted_state)
        print("-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=\n")
    else:
        print("No updates pushed this round. Nothing else to do this run.")

    # Determine how to exit
    clear_state = False
    if not any(r.progress < Repo.PROGRESS.DONE for r in config.state_data.values()):
        if config.args.simulate:
            print("INFO: Done with this round, but not clearing state because --sim was used.")
        elif (config.args.pause_on_finish_fail  # The args say to pause on failure
                and not config.state_data.get("pause_on_finish_fail")  # And not already paused
                # And are there any real failures that should cause us to pause.
                and any(r.progress == Repo.PROGRESS.DONE_FAILED_BLOCKING for r in
                        config.state_data.values())):
            # Set the flag and report the error.
            print(
                "Done with this round: Running in Pause On Finish Fail mode. Not resetting state.")
            config.state_data["pause_on_finish_fail"] = Repo.Repo(id="pause_on_finish_fail",
                                                                  prefix="",
                                                                  progress=Repo.PROGRESS.IGNORE_IS_META)
            config.teams_connector.send_teams_webhook_finish_failed(
                text=f"Update round on {config.args.branch} failed with errors."
                     f" Pausing the round until rewind/reset.", config=config, reset_links=True)
        else:
            # Everything was successful! Hooray! The round finished.
            print("Done with this round! Clearing state.")
            clear_state = True
            config.teams_connector.send_teams_webhook_basic(
                text=f"INFO: Reset/Finished update round on '{config.args.branch}'")

    # Dump the state to disk and save to codereview if available.
    state.save_updates_state(config, clear_state)


if __name__ == '__main__':
    main()
