############################################################################
##
## Copyright (C) 2020 The Qt Company Ltd.
## Contact: https://www.qt.io/licensing/
##
## This file is part of the utils of the Qt Toolkit.
##
## $QT_BEGIN_LICENSE:LGPL$
## Commercial License Usage
## Licensees holding valid commercial Qt licenses may use this file in
## accordance with the commercial license agreement provided with the
## Software or, alternatively, in accordance with the terms contained in
## a written agreement between you and The Qt Company. For licensing terms
## and conditions see https://www.qt.io/terms-conditions. For further
## information use the contact form at https://www.qt.io/contact-us.
##
## GNU Lesser General Public License Usage
## Alternatively, this file may be used under the terms of the GNU Lesser
## General Public License version 3 as published by the Free Software
## Foundation and appearing in the file LICENSE.LGPL3 included in the
## packaging of this file. Please review the following information to
## ensure the GNU Lesser General Public License version 3 requirements
## will be met: https://www.gnu.org/licenses/lgpl-3.0.html.
##
## GNU General Public License Usage
## Alternatively, this file may be used under the terms of the GNU
## General Public License version 2.0 or (at your option) the GNU General
## Public license version 3 or any later version approved by the KDE Free
## Qt Foundation. The licenses are as published by the Free Software
## Foundation and appearing in the file LICENSE.GPL2 and LICENSE.GPL3
## included in the packaging of this file. Please review the following
## information to ensure the GNU General Public License requirements will
## be met: https://www.gnu.org/licenses/gpl-2.0.html and
## https://www.gnu.org/licenses/gpl-3.0.html.
##
## $QT_END_LICENSE$
##
############################################################################
import copy
import json

from tools import toolbox
from .config import Config
from .proposal import Proposal
from .repo import Repo, PROGRESS


def recursive_prepare_updates(config) -> dict[str, Repo]:
    """Iterate through the list of repos and prepare updates for all
    in the READY state, or bump the repo to DONE_NO_UPDATE if it has
    no dependencies. Re-perform the operation as many times as needed
    until all eligible repos have had their update created."""
    reload = False
    for repo in config.state_data.values():
        config.state_data[repo.id], trigger_reload = prepare_update(config, repo)
        reload = bool(reload + trigger_reload)
    for repo in config.state_data.values():
        if repo.progress == PROGRESS.READY and not repo.dep_list and not repo.is_supermodule:
            reload = True
            repo.progress = PROGRESS.DONE_NO_UPDATE
            repo.proposal.merged_ref = repo.original_ref
            repo.proposal.proposed_yaml = repo.deps_yaml
    if reload:
        config.state_data = recursive_prepare_updates(config)
    return config.state_data


def prepare_update(config: Config, repo: Repo) -> tuple[Repo, bool]:
    """Bump progress of a repo if it's dependencies are met,
    then create a proposal if it doesn't already exist."""

    repo.progress, progress_changed = determine_ready(config, repo)
    if repo.is_supermodule:
        return repo, False
    repo.proposal = retrieve_or_generate_proposal(config, repo)
    reload = False
    if progress_changed and repo.progress >= PROGRESS.DONE:
        reload = True
    elif not repo.proposal and repo.progress < PROGRESS.WAIT_DEPENDENCY:
        print(f"moving {repo.id} to DONE_NO_UPDATE")
        repo.progress = PROGRESS.DONE_NO_UPDATE
        repo.proposal.merged_ref = repo.original_ref
        repo.proposal.proposed_yaml = repo.deps_yaml
        reload = True
    return repo, reload


def retrieve_or_generate_proposal(config: Config, repo) -> Proposal:
    """Return the proposed YAML if it exists and should not be updated,
    otherwise, generate a new one with the latest shas."""
    if repo.progress in [PROGRESS.DONE_FAILED_NON_BLOCKING, PROGRESS.DONE_FAILED_BLOCKING, PROGRESS.DONE, PROGRESS.DONE_NO_UPDATE,
                         PROGRESS.WAIT_DEPENDENCY, PROGRESS.WAIT_INCONSISTENT,
                         PROGRESS.IN_PROGRESS]:
        return repo.proposal
    else:
        # Create a new proposal for failed updates if the user has specified
        # --retryFailed in the arguments.
        if repo.progress == PROGRESS.RETRY and not config.args.retry_failed:
            return repo.proposal
        print(f"Creating new proposal for {repo.id}: {repo.progress.name}")
        proposal = copy.deepcopy(repo.deps_yaml)
        for dep in repo.deps_yaml.get("dependencies"):
            prefix, dep_name = toolbox.strip_prefix(dep)
            full_name = [n for n in repo.dep_list if dep_name in n].pop()
            proposal["dependencies"][dep]["ref"] = toolbox.get_head(config, full_name)
    if proposal == repo.deps_yaml:
        print(f"{repo.id} dependencies are already up-to-date")
    else:
        repo.proposal.proposed_yaml = proposal
    return repo.proposal


def check_subtree(config, source: Repo, source_ref: str, target: Repo) -> tuple[
    str, tuple[str, str]]:
    """Compare a sha between two repos' dependencies.yaml references for the same dependency.
    Recurse for each dependency which is not the same as the source.

    :returns: the id of a target repo which has a mismatching sha to the source_ref"""
    deps = target.deps_yaml.get(
        "dependencies") if target.progress < PROGRESS.DONE else target.proposal.proposed_yaml.get(
        "dependencies")
    for dependency in deps.keys():
        if source.name in dependency:
            if not source_ref == deps[dependency]["ref"]:
                if source.proposal.merged_ref == deps[dependency]["ref"]:
                    continue
                else:
                    print(f"source {source.name}:{source_ref[:10]} is not the same as {source.name}"
                          f" in {target.name}->{dependency}:{deps[dependency]['ref']}")  # Verbose!
                    return source.id, (target.id, deps[dependency]["ref"])
        else:
            clean_name = toolbox.strip_prefix(dependency)[1]
            new_target = config.state_data[toolbox.search_for_repo(config, clean_name).id]
            return check_subtree(config, source, source_ref, new_target)
    return tuple()


def discover_dep_inconsistencies(config: Config, repo: Repo) \
        -> dict[str, set[str]]:
    """Traverse the dependency tree of a repo, finding mismatching shas among dependency
    refrences. This allows us to determine the lowest-level module that must be updated
    in order to begin or continue a round."""
    mismatches = dict()
    if not repo.deps_yaml.get("dependencies"):
        return {}
    for top_dep in repo.deps_yaml["dependencies"].keys():
        top_dep_repo = toolbox.search_for_repo(config, top_dep)
        for other_dep in repo.deps_yaml["dependencies"].keys():
            other_dep_repo = toolbox.search_for_repo(config, other_dep)
            top_dep_ref = repo.deps_yaml["dependencies"][top_dep]["ref"]
            temp = check_subtree(config, top_dep_repo, top_dep_ref, other_dep_repo)
            if temp:
                if not temp[0] in mismatches.keys():
                    mismatches[temp[0]] = set()
                mismatches[temp[0]].add(temp[1])
    return mismatches


def discover_repo_dependencies(config: Config, repos_override: list[Repo] = None) -> dict[
    str, Repo]:
    """Traverse the dependency tree for a repo and add any repos found
    to the list of repos to update if it was not already specified
    or found to be part of qt5 default modules.
    Compile the list of dependencies for a given repo, direct and indirect."""
    for repo in repos_override or copy.deepcopy(list(config.state_data.values())):
        dep_list = set()
        if repo.progress >= PROGRESS.IN_PROGRESS and not config.rewind_module:
            # If a module is done, or had no update, we don't care about its dependencies.
            # This means that if we're discovering against qt5.git submodule shas,
            # we won't traverse the tree at all since we don't care about what a
            # direct dependency requires.
            continue
        if not repo.deps_yaml:
            repo.deps_yaml, repo.branch = toolbox.get_dependencies_yaml(config, repo)
        for dep in repo.deps_yaml.get('dependencies'):
            # Recurse through the tree until no more dependencies are found.
            relative_prefix, bare_dep = toolbox.strip_prefix(dep)
            key, dep_repo = toolbox.get_repos(config, [bare_dep], None).popitem()
            dep_list.add(dep_repo.id)
            config.state_data.update({key: dep_repo})
            # Retrieve the complete list of dependencies for this dependency.
            sub_deps = discover_repo_dependencies(config, [dep_repo])[dep_repo.id].dep_list
            # Add these dependencies to the master list for the repo we were first looking at.
            dep_list.update(sub_deps)
        repo.dep_list = list(dep_list)
        # Update this repo in our master list of repos.
        config.state_data.update({repo.id: repo})
        # Cross-check that we didn't miss anything
        config.state_data = discover_missing_dependencies(config, repo)
        if config.rewind_module and config.rewind_module.id in config.state_data[repo.id].dep_list:
            # If the module depends on the module we needed to rewind
            # to, either directly or indirectly, reset the state
            # and treat it as though it hasn't been updated.
            config.state_data[repo.id] = \
                toolbox.reset_module_properties(config, config.state_data[repo.id])
    return config.state_data


def cross_check_non_blocking_repos(config: Config) -> dict[str, Repo]:
    """Examine dependencies of all blocking repos. Convert a repo from non-blocking status
    to blocking if it is a dependency of a blocking repo to be updated."""
    for repo in config.state_data.values():
        if not repo.is_non_blocking:
            if repo.dep_list:
                for dep in repo.dep_list:
                    if config.state_data.get(dep) and config.state_data.get(dep).is_non_blocking:
                        config.state_data[dep].is_non_blocking = False
    return config.state_data


def discover_missing_dependencies(config: Config, repo: Repo) -> dict[str, Repo]:
    """Given a repo's dependency list, check for missing repos in the
    state_data and add them"""
    for dep in repo.dep_list:
        if dep not in config.state_data.keys():
            # Initialize the missing repo with the minimum data needed.
            temp_repo = toolbox.get_repos(config, repos_override=[dep], non_blocking_override=None)[dep]
            config.state_data[temp_repo.id] = temp_repo
            config.state_data = discover_repo_dependencies(config)
    return config.state_data


def determine_ready(config: Config, repo: Repo) -> tuple[PROGRESS, bool]:
    """Check to see if a repo is waiting on another, or if all
    dependency conflicts have been resolved and/or updated."""
    worst_state = PROGRESS.READY

    def is_worse(state):
        nonlocal worst_state
        if state > worst_state:
            worst_state = state

    if repo.proposal.inconsistent_set:
        is_worse(PROGRESS.WAIT_INCONSISTENT)
    if repo.progress < PROGRESS.IN_PROGRESS:
        for dependency in repo.dep_list:
            if dependency in config.state_data.keys():
                if config.state_data[dependency].progress < PROGRESS.DONE:
                    is_worse(PROGRESS.WAIT_DEPENDENCY)
                elif config.state_data[dependency].progress == PROGRESS.DONE_FAILED_NON_BLOCKING:
                    print(f"WARN: {repo.id} dependency {dependency} is a non-blocking module which"
                          f" failed. Marking {repo.id} as failed.")
                    is_worse(PROGRESS.DONE_FAILED_NON_BLOCKING)
                elif config.state_data[dependency].progress == PROGRESS.DONE_FAILED_BLOCKING:
                    print(f"WARN: {repo.id} dependency {dependency} is a blocking module which"
                          f" failed. Marking {repo.id} as failed-blocking.")
                    is_worse(PROGRESS.DONE_FAILED_BLOCKING)
        return worst_state, repo.progress != worst_state
    else:
        return repo.progress, False
