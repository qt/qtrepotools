# Copyright (C) 2020 The Qt Company Ltd.
# SPDX-License-Identifier: LicenseRef-Qt-Commercial OR LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only

import base64
import copy
import difflib
import json
import re
import time
import urllib
from typing import Union
from urllib.parse import unquote

import requests
import yaml
from gerrit.changes import change as GerritChange, edit as GerritChangeEdit
from gerrit.utils import exceptions as GerritExceptions

from .config import Config
from .proposal import Proposal
from .repo import Repo, PROGRESS


def strip_prefix(repo: Union[str, Repo]) -> tuple[str, str]:
    """Separate the prefix (if exists) and repo name of an input.

    Matches namespace and project prefixes up to the last forward slash or the
    first hyphen thereafter if the final section contains more than one hyphen.
    qt-labs/tqtc-demo-moviedb matches on "qt-labs/tqtc-"
    playground/qt-creator/plugin-scripting matches on "playground/qt-creator/"
    """
    r = r'((?:.*/){1,}(?:(?!(.*-){2,})|(?:[^-]*-)))'
    raw_name = ""
    raw_prefix = ""
    raw_result = re.findall(r, repo)
    if type(repo) == Repo:
        repo = repo.id
    if len(raw_result):
        raw_prefix = raw_result.pop()[0]
        raw_name = repo.removeprefix(raw_prefix)
    else:
        raw_name = repo
    return raw_prefix, raw_name


def make_full_id(repo: Repo, change_id_override: str = "") -> str:
    """Create gerrit change IDs from smaller bits if necessary
    Format: repo~branch~change_id"""
    retstr = ""
    if (repo.id in change_id_override or urllib.parse.quote_plus(repo.id) in change_id_override)\
            and (repo.branch in change_id_override or urllib.parse.quote_plus(repo.branch) in change_id_override):
        # Already formatted!
        retstr = change_id_override
    elif change_id_override:
        retstr = f"{repo.id}~{repo.branch}~{change_id_override}"
    elif repo.proposal:
        retstr = f"{repo.id}~{repo.branch}~{repo.proposal.change_id}"
    return retstr


def gerrit_link_maker(config: Config, change: Union[GerritChange.GerritChange, Repo],
                      change_override: str = "") -> tuple[str, str]:
    """Make user-clickable links to changes on gerrit."""
    repo = ""
    _change = None
    if type(change) == GerritChange:
        repo = change.project
        _change = change
    elif change_override:
        repo = change.id
        _change = config.datasources.gerrit_client.changes.get(change_override)
    elif type(change) == Repo:
        repo = change.id
        _change = config.datasources.gerrit_client.changes.get(change.proposal.change_id)
    if not repo:
        return "", ""
    subject = _change.subject
    mini_sha = _change.get_revision("current").get_commit().commit[:10]
    url = f"{config.GERRIT_HOST}c/{repo}/+/{_change._number}"
    return f"({mini_sha}) {subject[:70]}{'...' if len(subject) > 70 else ''}", url


def get_repos(config: Config, repos_override: list[str] = None, non_blocking_override: Union[list[str], None] = []) -> dict[str, Repo]:
    """Create a dict of initialized Repo objects. If repos_override is not specified,
    repos from the application's config/arguments are initialized alongside qt5 submodules

    :argument repos_override: If set, returns a dict of Repo which only includes these repos."""
    base_repos = repos_override or config.args.repos or config.REPOS
    non_blocking_repos = non_blocking_override
    if not non_blocking_repos and non_blocking_repos is not None:
        non_blocking_repos = config.args.non_blocking_repos or config.NON_BLOCKING_REPOS
    other_repos = []
    if not repos_override or non_blocking_override:
        # Only look at the repos in the override if it is set.
        if config.args.update_default_repos or (base_repos and not config.args.use_head):
            other_repos = config.qt5_default.keys()
    repo_list = list(set(base_repos).union(set(other_repos)))

    assert repo_list, \
        ("ERROR: Must supply at least one repo to update!\n"
         "Set repos to update via positional arguments, "
         "config.yaml, or environment variable REPOS")
    # Gather all the repo names into Repo() objects.
    repos = [search_for_repo(config, r) for r in repo_list]
    if non_blocking_repos:
        for repo in non_blocking_repos:
            r = search_for_repo(config, repo)
            r.is_non_blocking = True
            repos.append(r)
    retdict = dict()
    for repo in repos:
        if repo.id in config.state_data.keys():
            # Use the one we've got in the state if it's there.
            retdict[repo.id] = config.state_data[repo.id]
            # override previous blocking switch. It will be disabled again if anything depends on it
            retdict[repo.id].is_non_blocking = repo.is_non_blocking
        else:
            # Initialize the new repo
            repo.deps_yaml, repo.branch = get_dependencies_yaml(config, repo)
            repo.original_ref = get_head(config, repo)
            retdict[repo.id] = repo
    if not config.args.update_default_repos and not config.args.use_head:
        for repo in retdict.keys():
            if repo in config.qt5_default.keys() and repo not in base_repos:
                retdict[repo].progress = PROGRESS.DONE_NO_UPDATE
                retdict[repo].proposal.merged_ref = retdict[repo].original_ref
                retdict[repo].proposal.proposed_yaml = retdict[repo].deps_yaml
    return retdict


def get_state_repo(config, repo: Union[str, Repo]) -> Union[None, Repo]:
    """Locate a repo in the state file by string name or Repo.id"""
    if type(repo) == str:
        if repo in config.state_data.keys():
            return config.state_data[repo]
    elif type(Repo) == Repo:
        if repo.id in config.state_data.keys():
            return config.state_data[repo.id]
    return None


def search_for_repo(config, repo: Union[str, Repo]) -> Union[None, Repo]:
    """Search gerrit for a repository.
    :returns: bare repo initialized, or the repo from the state file if it exists."""
    gerrit = config.datasources.gerrit_client
    raw_prefix, raw_name = strip_prefix(repo)
    if raw_prefix + raw_name in config.state_data.keys():
        return config.state_data[raw_prefix + raw_name]
    search_response = gerrit.projects.regex('.*/.*' + raw_name if raw_name else repo.name)
    repo_names = [unquote(value.id) for value in search_response]
    for name in repo_names:
        if name.startswith(config.args.repo_prefix):
            ret_repo = get_state_repo(config, name)
            return ret_repo if ret_repo else Repo(name, config.args.repo_prefix)
    # If we didn't find a default [prefix] repo name, check to see if the original prefix and
    # name were in the search results, otherwise return the best guess with the prefix it has.
    if raw_prefix + raw_name in repo_names:  # Exact match
        ret_repo = get_state_repo(config, repo)
        return ret_repo if ret_repo else Repo(repo, raw_prefix)
    try:
        guess_id = str(difflib.get_close_matches(raw_name, repo_names, n=1).pop())
    except IndexError:
        try:
            prefixes, names = zip(*[strip_prefix(n) for n in repo_names])
            guess_name = str(difflib.get_close_matches(raw_name, names, n=1).pop())
            guess_id = prefixes[names.index(guess_name)] + guess_name
            ret_repo = get_state_repo(config, guess_id)
            return ret_repo if ret_repo else Repo(guess_id, prefixes[names.index(guess_name)])
        except IndexError:
            if not config.suppress_warn:
                print(f"WARN: No close match found for {raw_prefix + raw_name} among {repo_names}")
        return None
    guess_prefix, guess_name = strip_prefix(guess_id)
    print(f"INFO: Guessing fuzzy match {guess_id} for {repo}")
    ret_repo = get_state_repo(config, guess_id)
    return ret_repo if ret_repo else Repo(guess_id, guess_prefix)


def parse_gitmodules(config: Config, repo: [Repo, str], branch: str = "", ref: str = "") -> dict[
                     str, dict[str, str]]:
    """Retrieve .gitmodules and parse it into a dict.

    :param branch branch: exclusive with ref. Pull from branch head.
    :param ref ref: exclusive with branch. Pull from given ref.

    schema:
      {
        submodule_name: {
          key: value,
        }
      }
    """
    repo_id = repo.id if type(repo) == Repo else repo
    branch = branch if branch.startswith("refs/heads/") else 'refs/heads/' + branch
    gerrit = config.datasources.gerrit_client
    retdict = dict()
    try:
        if ref:
            gitmodules = gerrit.projects.get(repo_id).get_commit(ref) \
                .get_file_content(".gitmodules")
        else:
            gitmodules = gerrit.projects.get(repo_id).branches.get(branch) \
                .get_file_content('.gitmodules')
    except GerritExceptions.NotFoundError:
        print(f"WARN: {repo_id} does not contain .gitmodules! "
              f"It probably doesn't have any submodules.")
        return retdict
    raw_response = bytes.decode(base64.b64decode(gitmodules), "utf-8")
    for module_text in raw_response.split('[submodule '):
        if not module_text:
            continue
        split_module = module_text.split('\n')
        item = split_module.pop(0)  # was '[submodule "<name>"]' line before splitting
        assert item.startswith('"') and item.endswith('"]'), module_text
        item = item[1:-2]  # module name; followed by key = value lines, then an empty line
        data = dict(line.strip().split(' = ') for line in split_module if line)
        retdict[item] = data
    return retdict


def get_qt5_submodules(config: Config, types: list[str]) -> dict[str, Repo]:
    """Collect the list of submodules in qt5.git for a given branch."""
    if not types:
        print("WARN: No types passed to get_qt5_submodules!")
        return dict()
    gitmodules = parse_gitmodules(config, config.args.repo_prefix + 'qt5', config.args.branch)
    retdict = dict()
    for item, data in gitmodules.items():
        if data.get('status') in types:
            submodule_repo = search_for_repo(config, item)
            if config.drop_dependency and f"{config.args.repo_prefix}qt5" in config.drop_dependency_from:
                continue  # Ignore this repo. We'll be dropping it.
            if submodule_repo.id in config.state_data.keys():
                retdict[submodule_repo.id] = config.state_data[submodule_repo.id]
            else:
                retdict[submodule_repo.id] = submodule_repo
    return retdict


def get_head(config: Config, repo: Union[Repo, str], pull_head: bool = False) -> str:
    """Fetch the branch head of a repo from codereview, or return the
    saved ref from state if the repo progress is >= PROGRESS.DONE.
    Override state refs and pull remote branch HEAD with pull_head=True"""
    gerrit = config.datasources.gerrit_client
    if type(repo) == str:
        repo = search_for_repo(config, repo)
    if not pull_head and repo.id in config.state_data.keys() \
            and config.state_data[repo.id].progress >= PROGRESS.DONE:
        if config.state_data[repo.id].proposal.merged_ref:
            return config.state_data[repo.id].proposal.merged_ref
        else:
            return config.state_data[repo.id].original_ref
    if repo.id in config.qt5_default.keys() and not config.args.use_head:
        r = gerrit.projects.get(config.args.repo_prefix + 'qt5').branches.get(
            'refs/heads/' + config.args.branch).get_file_content(repo.name)
        return bytes.decode(base64.b64decode(r), "utf-8")
    else:
        branches = [config.args.branch, "dev", "master"]
        branch_head = None
        for branch in branches:
            try:
                branch_head = gerrit.projects.get(repo.id).branches.get("refs/heads/" + branch)
                if branch != config.args.branch and not config.suppress_warn:
                    print(f"INFO: Using {branch} instead of {config.args.branch} "
                          f"as the reference for {repo}")
                break
            except GerritExceptions.UnknownBranch:
                continue
        if not branch_head:
            if not config.suppress_warn:
                print(f"Exhausted branch options for {repo}! Tried {branches}")
                return ""
        else:
            return branch_head.revision


def get_top_integration_sha(config, repo: Repo) -> str:
    """Use a Repo's change ID to fetch the gerrit comments
    to look for the top-most change integrated at the same time.
    Use the sha of that change as the merged-sha for the repo.
    This ensures that dependencies are correct in leaf modules
    which may expect all the co-staged changes to be available."""
    if not repo.proposal.change_id:
        return ""
    gerrit = config.datasources.gerrit_client
    change = gerrit.changes.get(repo.proposal.change_id)
    messages = change.messages.list()
    integration_id = ""
    for message in messages:
        # Look for the message from COIN
        if "Continuous Integration: Passed" in message.message:
            m_lines = message.message.splitlines()
            for line in m_lines:
                # Here begins the list of changes integrated together.
                if line.strip().startswith("Details: "):
                    url = line.strip().split(" ")[1]  # Grab the COIN URL from the line.
                    integration_id = url.split("/")[-1]  # Get just the integration ID
                    break
            break
    if integration_id:
        r = requests.get(f"https://testresults.qt.io/coin/api/integration/{repo.id}/tasks/{integration_id}")
        if r.status_code == 200:
            sha = json.loads(r.text)[4]["1"]["rec"]["6"]["str"]
            print(f"Found integration sha {sha} from Integration ID: {integration_id}")
            return sha
        else:
            # Fallback to internal COIN if available. The task probably hadn't replicated to
            # testresults yet.
            r = requests.get(f"http://coin/coin/api/integration/{repo.id}/tasks/{integration_id}")
            if r.status_code == 200:
                sha = json.loads(r.text)[4]["1"]["rec"]["6"]["str"]
                print(f"Found integration sha {sha} from Integration ID: {integration_id}")
                return sha
        print(f"ERROR: Failed to retrieve integration sha from testresults/coin for integration ID"
              f" {integration_id}.\n"
              f"\tRepo: {repo.id}, submodule update change ID: {repo.proposal.change_id}\n"
              f"\t{gerrit_link_maker(config, change)}"
              f"DISABLING AUTOMATIC STAGING AND CONTINUING...")
        config.args.stage = False
        config.teams_connector.send_teams_webhook_basic(
            f"Error in updating {repo.id}."
            f" Could not retrieve merged sha for {repo.proposal.change_id}!", repo=repo)
    return ""


def strip_agent_from_test_log(text: str):
    """Strip the first number of characters from lines in COIN logs.
    Makes displaying logs more friendly."""
    r = re.compile(r'agent.+go:[\d]+:\s')
    return re.sub(r, "", text)


def parse_failed_integration_log(config, repo: Repo = None, log_url: str = "") -> str:
    """Use a Repo's change ID to fetch the gerrit comments
    to look for the most recent integration failure.
    Retrieve the log and parse it to snip out failed test cases."""
    if not log_url and not (repo and repo.proposal.change_id):
        return ""
    if repo:
        gerrit = config.datasources.gerrit_client
        change = gerrit.changes.get(repo.proposal.change_id)
        messages = change.messages.list()
        for message in reversed(messages):
            # Look for the message from COIN from the bottom up:
            if "Continuous Integration: Passed" in message.message:
                # Return if the integration passed. We don't need to parse the log.
                return ""
            elif "Continuous Integration: Failed" in message.message:
                m_lines = message.message.splitlines()
                start = False
                for line in m_lines:
                    # Locate the build log
                    if line.strip().startswith("Build log:"):
                        start = True
                        if line.removeprefix("Build log:").strip().startswith("http"):
                            log_url = line.removeprefix("Build log:").strip()
                            break
                        else:
                            continue
                    if line.strip().startswith("Details: "):
                        break
                    if start:
                        log_url += line.strip()
                break

    if not log_url:
        # No integrations yet?
        return ""
    r = requests.get(log_url)
    if r.status_code == 200:
        try:
            log_text = r.content.decode("utf-8")
        except UnicodeDecodeError:
            print(f"Error decoding integration failure log for"
                  f" {repo.proposal.change_id if repo else ''} at {log_url}")
            return ""
        if repo:
            print(f"Found integration failure log for {repo.proposal.change_id}")
    else:
        if repo:
            print(f"Error retrieving build log for {repo.proposal.change_id}")
        return ""
    ret_str = ""
    build_failure = parse_log_build_failures(log_text)
    if build_failure:
        return build_failure

    test_failures = parse_log_test_failures(log_text)

    if test_failures:
        ret_str += f"Total failed test cases: {len(test_failures)}\n"
        for fail_case in test_failures.keys():
            ret_str += fail_case + "\n"
            ret_str += '\n'.join(test_failures[fail_case]) + '\n'
            # ret_str += "\n"
    ret_str = strip_agent_from_test_log(ret_str)
    return ret_str


def parse_log_test_failures(logtext: str) -> dict[str, list[str]]:
    """Parse out some basic failure cases from a COIN log.
    This is only basic parsing. Many fail reasons are not caught by this parsing."""
    ret_dict = {}
    # Normal failures
    pattern = re.compile(r'[0-9]+(?<!0) failed,')
    if pattern.search(logtext):  # Find match(es) with the regex pattern above.
        # Iterate through the matches for failed tests and strip out the individual cases.
        for match in pattern.finditer(logtext):
            tstnameindex = 0
            tstnameindex = logtext.rfind("********* Start testing of ", 0,
                                         match.span()[0]) + 27  # Search for the test case.

            tstname = logtext[tstnameindex:
                              logtext.find("*********", tstnameindex) - 1]
            ret_dict[tstname] = []

            fail_count = int(re.match(r'[0-9]*', match.group(0)).group(0))
            if fail_count > 5:
                ret_dict[tstname].append(f"Too many fail cases ({fail_count}). See full log for details.")
                break

            # Save a snip of the log with the failed test(s)
            logsnip = logtext[tstnameindex: logtext.find("********* Finished testing of ", tstnameindex)]

            for failcasematch in re.finditer('FAIL!  : ', logsnip):  # Find the actual fail case
                # Look for the end of the case. We know coin always prints the file location.
                # Grab up the newline when we find that.
                failcasestring = logsnip[failcasematch.span()[0]:
                                         logsnip.find('\n', logsnip.find("Loc: ", failcasematch.span()[0]))] + "\n"
                ret_dict[tstname].append(failcasestring)

    # Crashes
    crash_index = logtext.rfind("ERROR: Uncontrolled test CRASH!")
    if crash_index > 0:
        tstnameindex = logtext.find("CMake Error at ", crash_index) + 15  # Search for test case
        re_tstname = re.compile(r"Test\s+#[0-9]+:\s(tst_.+)\s\.+\*\*\*Failed")
        try:
            tstname = re_tstname.search(logtext, tstnameindex).groups()[0]
        except AttributeError:
            # Failed to parse the end of the crash, ignore it and let a human read the log.
            return ret_dict
        ret_dict[tstname] = []

        # Save a snip of the log with the failed test(s)
        logsnip = logtext[
                  crash_index: logtext.find("\n", logtext.find("***Failed ", tstnameindex))]

        ret_dict[tstname].append(logsnip)

    return ret_dict


def parse_log_build_failures(logtext: str) -> str:
    """Parse the most basic of build errors."""
    # Normal failures
    err_pos = logtext.find(" error generated.")
    if err_pos > 0:
        start_pos = logtext.rfind("FAILED: ", err_pos)
        return logtext[start_pos:err_pos]
    return ""


def get_dependencies_yaml(config, repo: Repo, fetch_head: bool = False) -> tuple[yaml, str]:
    """Fetches the dependencies.yaml file of a repo from gerrit,
    or returns the saved dependencies.yaml from state if the repo
    progress is >= PROGRESS.DONE"""
    gerrit = config.datasources.gerrit_client
    found_branch = config.args.branch
    r = None
    if repo.id in config.qt5_default.keys() and not config.args.use_head and not fetch_head:
        if repo.id in config.state_data.keys():
            print(f"Using state data for {repo.id}")
            return config.state_data[repo.id].deps_yaml, config.state_data[repo.id].branch
        qt5_repo_sha = get_head(config, repo)
        try:
            r = gerrit.projects.get(repo.id).get_commit(qt5_repo_sha).get_file_content(
                'dependencies.yaml')
        except GerritExceptions.NotFoundError:
            pass

    if not r:
        if repo.id in config.state_data.keys() and not fetch_head and \
                config.state_data[repo.id].progress >= PROGRESS.DONE:
            print(f"Using state deps.yaml from merged repo {repo.id}")
            if config.state_data[repo.id].proposal:
                return config.state_data[repo.id].proposal.proposed_yaml, config.state_repo[
                    repo.id].branch
            else:
                return config.state_data[repo.id].deps_yaml, config.state_repo[repo.id].branch
        branches = [config.args.branch, "dev", "master"]
        for branch in branches:
            try:
                r = gerrit.projects.get(repo.id).branches.get(
                    'refs/heads/' + branch).get_file_content('dependencies.yaml')
                found_branch = branch
                if not branch == config.args.branch:
                    if not config.suppress_warn:
                        print(f"INFO: Found dependencies.yaml in {repo.id} on branch {branch}"
                              f" instead of {config.args.branch}")
                break
            except (GerritExceptions.UnknownBranch, GerritExceptions.NotFoundError):
                continue

    if not r:
        print(f"WARN: {repo.id} doesn't seem to have a dependencies.yaml file!\n")
        # "Disabling automatic staging, as this may cause unintended behavior.")  # TODO: Determine if this needs to disable smartly.
        # config.args.stage = False
        return {"dependencies": {}}, found_branch

    d = dict()
    try:
        d = yaml.load(bytes.decode(base64.b64decode(r), "utf-8"), Loader=yaml.FullLoader)
        if config.drop_dependency and repo in config.drop_dependency_from:
            drop = [k for k in d.get("dependencies").keys() if repo.name in k]
            if drop:
                del d["dependencies"][drop.pop()]
    except yaml.YAMLError as e:
        if not config.suppress_warn:
            print(f"ERROR: Failed to load dependencies yaml file.\nYAML Exception: {e}")
    return d, found_branch


def get_check_progress(config: Config, repo: Repo) -> (PROGRESS, str, str):
    """Determine the progress status of a submodule update

    :returns: progress: PROGRESS, merged_ref: str, gerrit_change_status: str[NEW, MERGED, STAGED, INTEGRATING, ABANDONED]"""
    if repo.progress in [PROGRESS.DONE, PROGRESS.DONE_NO_UPDATE, PROGRESS.IGNORE_IS_META]:
        return repo.progress, repo.proposal.merged_ref, "MERGED"
    elif repo.proposal.proposed_yaml and not repo.proposal.change_id:
        if repo.proposal.inconsistent_set:
            return PROGRESS.WAIT_INCONSISTENT, "", ""
        else:
            return PROGRESS.READY, "", ""
    elif repo.proposal.change_id:
        # This condition also catches DONE_FAILED_BLOCKING and DONE_FAILED_NON_BLOCKING
        # So that if a change was manually merged without the bot's help,
        # it gets picked up and marked as merged.
        change = config.datasources.gerrit_client.changes.get(repo.proposal.change_id)
        remote_status = change.status
        if remote_status == "NEW" and repo.progress == PROGRESS.IN_PROGRESS and repo.stage_count > 0:
            return PROGRESS.RETRY, "", remote_status
        elif remote_status == "STAGED" or remote_status == "INTEGRATING":
            return PROGRESS.IN_PROGRESS, "", remote_status
        elif remote_status == "MERGED":
            integration_top_sha = get_top_integration_sha(config, repo)
            return PROGRESS.DONE, integration_top_sha, remote_status
        elif remote_status == "ABANDONED":
            return PROGRESS.ERROR, "", remote_status
    return repo.progress, "", repo.proposal.gerrit_status


def retry_update(config: Config, repo: Repo) -> Repo:
    """Restage changes from a failed integration attempt and increment
     the retry counter."""
    repo.retry_count += 1
    if stage_update(config, repo):
        repo.stage_count += 1
    return repo


def post_gerrit_comment(config: Config, change_id: str, message: str) -> None:
    """Post a simple comment to a gerrit change."""
    if config.args.simulate:
        print(f'SIM: Post gerrit comment on {change_id} with message: "{message}"')
    change = config.datasources.gerrit_client.changes.get(change_id)
    try:
        change.get_revision("current").set_review({"message": message})
    except GerritExceptions.ConflictError as e:
        print(f"WARN: Failed to post comment on {change_id}: {e}")


def stage_update(config: Config, repo: Repo) -> bool:
    """Perform a 'safe stage' on the update by attempting to stage all
    updates together, but cancel the attempt and unstage if a conflict
    is generated during staging."""
    if repo.proposal.gerrit_status in ["STAGED", "INTEGRATING", "MERGED"]:
        print(f"{repo.id} update is already {repo.proposal.gerrit_status}. Skipping.")
        return False
    if repo.proposal.change_id not in repo.to_stage:
        repo.to_stage.append(repo.proposal.change_id)
    if config.args.sweep_changes:
        repo.to_stage = list(set(repo.to_stage).union(gather_costaging_changes(config, repo)))
    print(f"Preparing to stage changes for {repo.id}: {repo.to_stage}")
    error = False
    # Create a list of links for each change staged.
    gerrit_link_self = " ".join(gerrit_link_maker(config, repo))
    costaging_changes_links = "\n" + "\n".join(
        [" ".join(gerrit_link_maker(config, repo, change_override=make_full_id(repo, change_id))) for change_id in repo.to_stage if change_id != repo.proposal.change_id])
    for change_id in repo.to_stage:
        if repo.proposal.change_id == change_id:
            if len(repo.to_stage) > 1:
                message = f"Staging this update with other changes:\n{costaging_changes_links}"
            else:
                message = ""
        else:
            message = "Staging this change automatically with the dependency update for this" \
                      f" module:\n" \
                      f"{gerrit_link_self}"
        if stage_change(config, change_id, message):
            print(f"{repo.id}: Staged "
                  f"{'submodule update' if repo.proposal.change_id == change_id else 'related change'}"
                  f" {change_id}")
        else:
            if repo.proposal.change_id == change_id:
                post_gerrit_comment(config, change_id,
                                    f"Failed to stage this dependency update automatically.\n"
                                    f"{'Co-staged with ' + costaging_changes_links if len(repo.to_stage) > 1 else ''}")
            else:
                post_gerrit_comment(config, change_id, "Failed to stage this change automatically"
                                                       " with the dependency update for this repo."
                                                       " It probably created a merge conflict."
                                                       " Please review.\n"
                                                       f"See: {gerrit_link_self}.")
            error = True
    if error:
        print(f"failed to stage {repo.id}: {repo.to_stage}\n")
        config.teams_connector.send_teams_webhook_failed_stage(repo)
        # for change_id in repo.to_stage:
        #     unstage_change(config, change_id)
        # print(f"Changes to be staged together for {repo.id} have now been unstaged:\n"
        #       ', '.join(repo.to_stage))
        return False
    print()
    return True


def stage_change(config: Config, change_id: str, comment: str = "") -> bool:
    """Stage a change in gerrit. Requires the QtStage permission."""
    if config.args.simulate:
        print(f"SIM: Simulated successful staging of {change_id}")
        return True
    change = config.datasources.gerrit_client.changes.get(change_id)
    # Sleep for one second to give gerrit a second to cool off. If the change was just
    # created, sometimes gerrit can be slow to release the lock, resulting in a 409 response code.
    time.sleep(1)
    try:
        change.stage()
        if comment:
            post_gerrit_comment(config, change_id, comment)
    except GerritExceptions.NotAllowedError:
        print(f"WARN: Unable to stage {change_id} automatically.\n"
              f"Either you do not have permissions to stage this change, or the branch is closed.")
        return False
    except GerritExceptions.ConflictError:
        print(f"ERROR: Unable to stage {change_id} automatically.\n"
              "The change contains conflicts and cannot be staged. Please verify that no other\n"
              "changes currently staged conflict with this update.")
        return False
    return True


def unstage_change(config: Config, change_id: str) -> bool:
    """Unstage a change from gerrit"""
    change = config.datasources.gerrit_client.changes.get(change_id)
    try:
        change.unstage()
    except GerritExceptions.ConflictError:
        return False
    return True


def gather_costaging_changes(config: Config, repo: Repo) -> list[str]:
    """Gather changes where the bot is tagged as reviewer"""
    gerrit = config.datasources.gerrit_client
    changes = gerrit.changes.search(f'q=reviewer:{config.GERRIT_USERNAME}'
                                    f'+status:open'
                                    f'+label:"Code-Review=2"'
                                    f'+label:"Sanity-Review=1"'
                                    f'+branch:{repo.branch}'
                                    f'+repo:{repo.id}')
    ret_list: list[str] = []
    for change in changes:
        if change.change_id not in repo.to_stage:
            # Append the fully scoped change ID since it's possible
            # These change ids may exist in multiple branches.
            ret_list.append(change.id)
    return ret_list


def search_existing_change(config: Config, repo: Repo, message: str) -> tuple[str, str]:
    """Try to re-use open changes the bot created where possible
    instead of spamming new changes"""
    changes = config.datasources.gerrit_client.changes \
        .search(f'q=message:"{message}"'
                f'+owner:{config.GERRIT_USERNAME}'
                f'+(status:open+OR+status:staged+OR+status:integrating)'
                f'+branch:{repo.branch}'
                f'+repo:{repo.id}')
    if changes:
        change = changes.pop()
        return change.change_id, change._number
    return "", ""


def push_submodule_update(config: Config, repo: Repo, retry: bool = False) -> Proposal:
    """Push the submodule update to codereview"""
    deps_yaml_file = yaml.dump(repo.proposal.proposed_yaml)

    print()

    current_head_deps, _ = get_dependencies_yaml(config, repo, fetch_head=True)
    if current_head_deps == repo.proposal.proposed_yaml:
        repo.proposal.merged_ref = get_head(config, repo, pull_head=True)
        repo.proposal.change_id = ""
        repo.proposal.change_number = ""
        print(f"Branch head for {repo.id} is already up-to-date! Not pushing an update!")
        return repo.proposal

    change, edit = acquire_change_edit(config, repo,
                                       f"Update dependencies on '{repo.branch}' in {repo.id}")
    if not edit:
        # This can occur if the round was reset or rewound while a patch was still
        # integrating. Instead of creating a new change, locate it, compare our yaml file
        # with the integrating one.
        current_patch_deps = yaml.load(bytes.decode(base64.b64decode(
            change.get_revision("current").get_commit().get_file_content("dependencies.yaml")),
                                                    'utf-8'), Loader=yaml.Loader)
        if current_patch_deps == repo.proposal.proposed_yaml:
            repo.proposal.gerrit_status = change.status
            print(f"Currently {change.status} change in {repo.id} is already up-to-date!")
        else:
            # If the found change's file doesn't match our proposal, then our proposal is newer.
            # We must abort the update for this repo and wait until the currently integrating
            # change merges or fails to integrate.
            repo.proposal.change_id = ""
            repo.proposal.change_number = ""
            print(current_patch_deps, "\n", deps_yaml_file)
            print(f"WARN: Found a currently {change.status} change which doesn't match "
                  f"the proposed update! Waiting until {repo.id} -> {change.change_id} "
                  f"merges or fails.")
        return repo.proposal
    try:
        change.rebase({"base": ""})
        print(f"Rebased change {change.change_id}")
    except GerritExceptions.ConflictError:
        if not change.get_revision("current").get_commit().parents[0]["commit"]\
               == get_head(config, repo, True):
            print("WARN: Failed to rebase change due to conflicts."
                  " Abandoning and recreating the change.")
            # Failed to rebase because of conflicts
            edit.delete()
            post_gerrit_comment(config, change.change_id, "Abandoning this change because"
                                                          "it cannot be rebased without conflicts.")
            change.abandon()
            repo.proposal.change_id = ""
            repo.proposal.change_number = ""
            repo.proposal = push_submodule_update(config, repo)
            return repo.proposal
        else:
            # Already on HEAD. OK to move on.
            pass
    try:
        edit.put_change_file_content("dependencies.yaml", deps_yaml_file)
        file_content_edit = bytes.decode(base64.b64decode(edit.get_change_file_content("dependencies.yaml")))
        print(f"Push file succeeded? {deps_yaml_file == file_content_edit}\n{file_content_edit if deps_yaml_file != file_content_edit else ''}")
        time.sleep(1)
        edit.publish({
            "notify": "NONE"
        })
        print(f"Published edit as new patchset on {change.change_id}")
    except GerritExceptions.ConflictError:
        # A conflict error at this point just means that no
        # changes were made. So just catch the exception and
        # move on.
        change.abandon()
        repo.proposal.change_id = ""
        repo.proposal.change_number = ""
        if not retry:
            print("Retrying update with a fresh change...")
            repo.proposal = push_submodule_update(config, repo, retry=True)
            return repo.proposal
    approve_change_id(change, repo.id)
    return repo.proposal


def do_try_supermodule_updates(config: Config) -> dict[str, Repo]:
    """Push supermodule updates if needed"""
    blocking_repos = [r for r in config.state_data.values() if not r.is_non_blocking]
    if not any((r.progress < PROGRESS.DONE or r.progress == PROGRESS.DONE_FAILED_BLOCKING)
               and r.id not in [f"{config.args.repo_prefix}qt5", "yocto/meta-qt6"]
               for r in blocking_repos):
        if config.args.update_supermodule:
            supermodule = push_supermodule_update(config)
            config.state_data[supermodule.id] = supermodule

        if config.args.update_yocto_meta:
            yocto = push_yocto_update(config)
            config.state_data[yocto.id] = yocto
    return config.state_data


def push_supermodule_update(config: Config, retry: bool = False) -> Repo:
    """Push the meta-update with all the new shas to the supermodule repo"""
    gerrit = config.datasources.gerrit_client
    qt5_name = config.args.repo_prefix + "qt5"
    qt5_repo = search_for_repo(config, qt5_name)
    if qt5_repo.progress >= PROGRESS.IN_PROGRESS:
        return qt5_repo
    qt5_repo.is_supermodule = True
    qt5_repo.branch = config.args.branch
    qt5_repo.proposal.change_id, qt5_repo.proposal.change_number \
        = search_existing_change(config, qt5_repo, "Update Submodules")
    gitmodules_orig = bytes.decode(base64.b64decode(gerrit.projects.get(qt5_name).branches.get(
        f"refs/heads/{config.args.branch}").get_file_content(".gitmodules")), 'utf-8')
    gitmodules_updated = copy.deepcopy(gitmodules_orig)

    qt5_modules = get_qt5_submodules(config, ['essential', 'addon', 'deprecated', 'ignore', 'preview'])
    qt5_repo.dep_list = list(qt5_modules.keys())
    if config.args.simulate:
        print(f"{qt5_repo.id} submodule update proposal:")
        print("\n".join([f"{r.id}: {r.proposal.merged_ref}" for r in qt5_modules.values()]))
        print()
        return qt5_repo

    change, edit = acquire_change_edit(config, qt5_repo,
                                       f"Update submodules on '{config.args.branch} in {qt5_name}'")
    if not edit:
        diff: bool = False
        for repo in qt5_modules.values():
            submodule_patch_ref = bytes.decode(base64.b64decode(
                change.get_revision("current").get_commit().get_file_content(repo.name)), 'utf-8')
            if repo.proposal and submodule_patch_ref != repo.proposal.merged_ref:
                diff = True
        if diff:
            print(f"WARN: Found a currently {change.status} change which doesn't match "
                  f"the proposed update! Waiting until {qt5_repo.id} -> {change.change_id} "
                  f"merges or fails.")
            qt5_repo.proposal.change_id = ""
            qt5_repo.proposal.change_number = ""
        else:
            qt5_repo.proposal.gerrit_status = change.status
            print(f"Currently {change.status} change in {qt5_repo.id} is already up-to-date!")
        return qt5_repo

    for repo in qt5_modules.values():
        try:
            if repo.proposal.merged_ref:
                edit.put_change_file_content(repo.name, repo.proposal.merged_ref)
            else:
                continue  # The module didn't get updated this round.
            time.sleep(0.5)
        except GerritExceptions.ConflictError:
            # A conflict error at this point just means that no
            # changes were made. So just catch the exception and
            # move on. This would usually occur if the change is
            # reused some shas are already up-to-date.
            print(f"Submodule sha for {repo.id} is already up-to-date: {repo.proposal.merged_ref}")
            continue

    if config.drop_dependency:
        # Edit the .gitmodules file to remove references to the
        # module to drop. Remove it entirely if necessary, or just
        # from the depends/recommends list of other modules.
        if qt5_repo.id in config.drop_dependency_from:
            module_entry = snip_gitmodules(config.drop_dependency.name, gitmodules_orig)
            gitmodules_orig.replace(module_entry, "")
        for repo in config.drop_dependency_from:
            module_entry = snip_gitmodules(repo.name, gitmodules_orig)
            module_entry_lines = module_entry.splitlines()
            depends_orig = [line for line in module_entry_lines if "depends" in line]
            depends = depends_orig.pop().split(" ") if len(depends_orig) else []
            recommends_orig = [line for line in module_entry_lines if "recommends" in line]
            recommends = recommends_orig.pop().split(" ") if len(recommends_orig) else []

            if config.drop_dependency.name in depends:
                del depends[depends.index(repo.name)]
            if config.drop_dependency.name in recommends:
                del depends[depends.index(repo.name)]
            depends_new = " ".join(depends)
            recommends_new = " ".join(recommends)
            gitmodules_updated.replace(" ".join(depends_orig), depends_new)
            gitmodules_updated.replace(" ".join(recommends_orig), recommends_new)
    try:
        change.rebase({"base": ""})
        print(f"Rebased change {change.change_id}")
    except GerritExceptions.ConflictError:
        if not change.get_revision("current").get_commit().parents[0]["commit"]\
               == get_head(config, qt5_repo, True):
            print("WARN: Failed to rebase change due to conflicts."
                  " Abandoning and recreating the change.")
            # Failed to rebase because of conflicts
            edit.delete()
            post_gerrit_comment(config, change.change_id, "Abandoning this change because"
                                                          "it cannot be rebased without conflicts.")
            time.sleep(1)
            change.abandon()
            config.state_data[qt5_repo.id] = reset_module_properties(config, qt5_repo)
            qt5_repo = push_supermodule_update(config)
            return qt5_repo
        else:
            # Already on HEAD. OK to move on.
            pass
    if not gitmodules_orig == gitmodules_updated:
        try:
            edit.put_change_file_content(".gitmodules", gitmodules_updated)
        except GerritExceptions.ConflictError:
            print("WARN: Trying to push new .gitmodules, but the patchset is already up-to-date.")
            pass
    try:
        time.sleep(1)
        edit.publish({
            "notify": "NONE"
        })
        print(f"Published edit as new patchset on {change.change_id}")
        qt5_repo.progress = PROGRESS.IN_PROGRESS
        approve_change_id(change, qt5_repo.id)
        config.teams_connector.send_teams_webhook_basic(text=f"Updating {qt5_repo.id} with a consistent"
                              f" set of submodules in **{qt5_repo.branch}**", repo=qt5_repo)
    except GerritExceptions.ConflictError:
        print(f"No changes made to {qt5_repo.id}, possible that the current patchset is up-to-date")
        edit.delete()
        diff: bool = False
        for repo in qt5_modules.values():
            submodule_patch_ref = bytes.decode(base64.b64decode(
                change.get_revision("current").get_commit().get_file_content(repo.name)), 'utf-8')
            if repo.proposal and submodule_patch_ref == repo.proposal.merged_ref\
                    and not submodule_patch_ref == repo.original_ref:
                diff = True
        if not diff:
            # The current patchset is the same as HEAD. Don't stage empty changes!
            # TODO: Figure out a way to make rebased changes accept updated shas!
            change.abandon()
            if not retry:
                print("Retrying update with a fresh change...")
                qt5_repo = push_supermodule_update(config, retry=True)
                return qt5_repo
            else:
                # Still returned that everything is up-to-date even on a fresh change.
                # Odd, but probably true at this point.
                qt5_repo.progress = PROGRESS.DONE_NO_UPDATE
        else:
            # Seems we actually succeeded in publishing the change.
            qt5_repo.progress = PROGRESS.IN_PROGRESS
            approve_change_id(change, qt5_repo.id)

    return qt5_repo


def search_pinned_submodule(config: Config, module: Repo, submodule: [str, Repo]) -> str:
    """Fetch the gitmodules for a repo and retrieve pinned submodule
    sha for the given submodule."""
    gerrit = config.datasources.gerrit_client
    module_ref = module.proposal.merged_ref or module.original_ref
    gitmodules = parse_gitmodules(config, repo=module,
                                  ref=module_ref)
    submodule_name = submodule.name if type(submodule) == Repo else submodule
    for key, data in gitmodules.items():
        if submodule_name in key or submodule_name in data.get("url"):
            print(
                f"Found submodule {submodule_name} in {[d for d in [key, 'url: ' + data.get('url')] if submodule_name in d]}")
            # Fetch the pinned submodule ref
            r = gerrit.projects.get(module.id).get_commit(module_ref).get_file_content(
                data.get("path"))
            return bytes.decode(base64.b64decode(r), "utf-8")


def push_yocto_update(config: Config, retry: bool = False) -> Repo:
    """Push the meta-update with all the applicable shas to the yocto/meta-qt6 repo"""
    gerrit = config.datasources.gerrit_client
    yocto_repo = search_for_repo(config, "yocto/meta-qt6")
    filename = "recipes-qt/qt6/qt6-git.inc"

    if yocto_repo.progress >= PROGRESS.IN_PROGRESS:
        return yocto_repo
    yocto_repo.is_supermodule = True
    # LTS branch updates are expected to always use a prefix of tqtc/lts-
    # but yocto lts branches remain in the public repo, so strip off
    # the tqtc/ prefix
    yocto_repo.branch = config.args.branch.removeprefix('tqtc/')
    yocto_repo.proposal.change_id, yocto_repo.proposal.change_number \
        = search_existing_change(config, yocto_repo, "Update submodule refs")

    r = gerrit.projects.get(yocto_repo.id).branches.get(f"refs/heads/{yocto_repo.branch}") \
        .get_file_content(filename)
    old_file = bytes.decode(base64.b64decode(r), "utf-8")
    file_lines = old_file.splitlines()

    # The trial-and error nature of finding submodules can be a bit noisy, so suppress warnings.
    config.suppress_warn = True
    print("Preparing yocto/meta-qt6 update:")
    for i, line in enumerate(file_lines):
        if not line.startswith("SRCREV_"):
            continue
        SRCREV, sha = line.split(" = ")
        print("OLD: ", line)
        repo_name_maybe_submodule = SRCREV.split("_")[1]
        module_name = ""
        pinned_submodule_sha = ""
        # If the module name is hypenated, it may be a submodule. Try to find the parent
        # and use that sha.
        if "-" in repo_name_maybe_submodule:
            split = repo_name_maybe_submodule.split("-")
            maybe_parent = "-".join(split[:-1])
            parent_lines = [l for l in file_lines[:i] if l.startswith(f"SRCREV_{maybe_parent}")]
            if parent_lines:
                # Found a potential parent, run some checks to see if it's a known or
                # real repository.
                parent_line = parent_lines[-1].split(" = ")
                module_name = parent_line[0].split("_").pop()
                if "-" in module_name:
                    module_name = module_name.split("-").pop()
                module_repo = search_for_repo(config, module_name)
                module_repo.original_ref = parent_line[1].strip('"')
                submodule_name = split[-1]
                submodule_repo = search_for_repo(config, submodule_name)
                if submodule_repo:
                    pinned_submodule_sha = search_pinned_submodule(config, module_repo,
                                                                   submodule_repo)
                if not pinned_submodule_sha:
                    print(f"Couldn't find a submodule named {submodule_repo.id}"
                          f' in {module_repo.id}. Trying raw submodule name: "{submodule_name}"')
                    pinned_submodule_sha = search_pinned_submodule(config, module_repo,
                                                                   submodule_name)
                if pinned_submodule_sha:
                    print(f"Found {submodule_name} as a submodule"
                          f" to {module_name}@{module_repo.original_ref}")
            if not pinned_submodule_sha:
                print(f"Couldn't figure out {repo_name_maybe_submodule} as a submodule.\n"
                      f"Trying {repo_name_maybe_submodule} as a regular module instead.")
                module_name = repo_name_maybe_submodule
                submodule_name = ""
        else:  # Expected to be just a regular module
            module_name = repo_name_maybe_submodule
        module_repo = search_for_repo(config, module_name)
        if not module_repo.original_ref:
            module_repo.original_ref = get_head(config, module_repo)
        if pinned_submodule_sha:
            file_lines[i] = line.replace(sha, f'"{pinned_submodule_sha}"')
        else:
            file_lines[i] = line.replace(sha,
                                         f'"{module_repo.proposal.merged_ref or module_repo.original_ref}"')
        print("NEW: ", file_lines[i])

    config.suppress_warn = False

    if config.drop_dependency and (
            "yocto/meta-qt6" in config.drop_dependency_from or not config.drop_dependency_from):
        print(f"Deleting {config.drop_dependency} as a dependency from yocto/meta-qt6.")
        file_lines = [line for line in file_lines if config.drop_dependency.name not in line]

    new_file = "\n".join(file_lines) + "\n"
    print()
    if old_file == new_file:
        print("yocto/meta-qt6 is up-to-date. No changes necessary.")
        yocto_repo.progress = PROGRESS.DONE_NO_UPDATE
        return yocto_repo
    print("yocto/meta-qt6 proposed update:")
    print(new_file)

    if config.args.simulate:
        return yocto_repo

    change, edit = acquire_change_edit(config, yocto_repo,
                                       f"Update submodule refs on '{yocto_repo.branch}' in {yocto_repo.id}")
    if not edit:
        current_patch_file = bytes.decode(base64.b64decode(
            change.get_revision("current").get_commit().get_file_content(filename)),
            'utf-8')
        if current_patch_file == new_file:
            yocto_repo.proposal.gerrit_status = change.status
            print(f"Currently {change.status} change in {yocto_repo.id} is already up-to-date!")
        else:
            yocto_repo.proposal.change_id = ""
            yocto_repo.proposal.change_number = ""
            print(f"WARN: Found a currently {change.status} change which doesn't match "
                  f"the proposed update! Waiting until {yocto_repo.id} -> {change.change_id} "
                  f"merges or fails.")
        return yocto_repo
    print()
    # Try to rebase the change, or if not
    try:
        change.get_revision("current").rebase({"base": ""})
        print(f"Rebased change {change.change_id}")
    except GerritExceptions.ConflictError:
        if not change.get_revision("current").get_commit().parents[0]["commit"] == get_head(config, yocto_repo, True):
            print("WARN: Failed to rebase change due to conflicts."
                  " Abandoning and recreating the change.")
            # Failed to rebase because of conflicts
            edit.delete()
            post_gerrit_comment(config, change.change_id, "Abandoning this change because"
                                                          "it cannot be rebased without conflicts.")
            change.abandon()
            config.state_data[yocto_repo.id] = reset_module_properties(config, yocto_repo)
            yocto_repo = push_yocto_update(config)
            return yocto_repo
        else:
            # Already on HEAD. OK to move on.
            pass

    try:
        edit.put_change_file_content(filename, new_file)
        print(f"Pushed {filename}")
    except GerritExceptions.ConflictError:
        # A conflict error at this point just means that no
        # changes were made. So just catch the exception and
        # move on.
        print(f"WARN: No changes made to {filename}.")
    try:
        time.sleep(1)
        edit.publish({
            "notify": "NONE"
        })
        print(f"Published edit as new patchset on {change.change_id}")
    except GerritExceptions.ConflictError:
        print(
            f"No changes made to {yocto_repo.id}, possible that the current patchset is up-to-date")
        edit.delete()
        change.abandon()
        if not retry:
            print("Retrying update with a fresh change...")
            yocto_repo = push_yocto_update(config, retry=True)
            return yocto_repo
        else:
            # Still returned that everything is up-to-date even on a fresh change.
            # Odd, but probably true at this point.
            yocto_repo.progress = PROGRESS.DONE_NO_UPDATE
            return yocto_repo
    yocto_repo.progress = PROGRESS.IN_PROGRESS
    config.teams_connector.send_teams_webhook_basic(
        text=f"Updating {yocto_repo.id} with a consistent"
             f" set of submodules in **{yocto_repo.branch}**", repo=yocto_repo)
    approve_change_id(change, yocto_repo.id)

    return yocto_repo


def acquire_change_edit(config: Config, repo: Repo, subject: str) -> tuple[
                        GerritChange, GerritChangeEdit]:
    """Create a new codereview change if necessary and acquire an edit
    on the change"""
    gerrit = config.datasources.gerrit_client
    if repo.proposal.change_id:
        change = gerrit.changes.get(repo.proposal.change_id)
        if change.status in ["STAGED", "INTEGRATING"]:
            print(f"Change is in state: {change.status}. Cannot create an edit.")
            return change, None
    else:
        change = gerrit.changes.create({
            "project": repo.id,
            "subject": subject,
            "branch": repo.branch,
            "status": "NEW"
        })
        print(f"Created new change for {repo.id}: {change.change_id}")
        repo.proposal.change_id = change.change_id
        repo.proposal.change_number = change._number
        repo.proposal.gerrit_status = "NEW"
    try:
        change.create_empty_edit()
    except GerritExceptions.ConflictError:
        print(f"WARN: {repo.id} change {change.change_id} may already have edit."
              f" Attempting to clear it!")
        edit = change.get_edit()
        if edit:
            edit.delete()
        else:
            print(f"Change is in state: {change.status}")
            # Some issue creating the edit!
            return change, None
        change.create_empty_edit()
    edit = change.get_edit()
    return change, edit


def approve_change_id(change: GerritChange, repo_name: str) -> bool:
    """Give a +2 to a change. It's fine to self-approve a submodule update."""
    try:
        change.get_revision("current").set_review({
            "message": "Auto-approving submodule update.",
            "ready": "true",
            "labels": {
                "Code-Review": 2,
                "Sanity-Review": 1
            }
        })
        return True
    except GerritExceptions.NotAllowedError:
        print(f"WARN: You do not have self-approval rights to auto-approve in {repo_name}\n"
              f"You must have change ID {change.change_id} approved and"
              f" manually stage it.")
        return False


def snip_gitmodules(repo_name: str, gitmodules: str) -> str:
    """Get the snippet of gitmodules for a repo."""
    loc = gitmodules.find(repo_name)
    start = -1
    end = -1
    if loc >= 0:
        start = gitmodules.rfind('[', 0, loc - 1)
        end = gitmodules.find('[', start + 1) - 1
        if end < 0:
            end = len(gitmodules) - 1
    return gitmodules[start:end]


def reset_module_properties(config: Config, repo: Repo) -> Repo:
    """Resets a module to the default state and refreshes the head."""
    print(f"Resetting module state for {repo.id}")
    repo.progress = PROGRESS.UNSPECIFIED
    repo.proposal = Proposal()
    repo.stage_count = 0
    repo.retry_count = 0
    repo.to_stage = list()
    repo.original_ref = get_head(config, repo, True)
    return repo


def reset_stage_count(repo: Repo) -> Repo:
    """Drop the recorded staging attempts to 0"""
    repo.stage_count = 0
    repo.retry_count = 0
    return repo


def state_printer(config: Config) -> tuple[dict[PROGRESS, int], str]:
    """Assembles a prett-print string of the current state of updates"""
    ret_str = ""

    def _print(*val, end="\n"):
        buffer = ""
        for val in val:
            buffer += val
        buffer += end
        return buffer

    total_state = {state: 0 for state in PROGRESS}
    msg = "\nThe following repos are ready to be updated:"
    repos = list()
    for repo in config.state_data.keys():
        if config.state_data[repo].progress == PROGRESS.READY:
            total_state[PROGRESS.READY.value] += 1
            repos.append(repo)
    if repos:
        ret_str += _print(f"{msg}\n\t", "\n\t".join(repos))
    repos.clear()

    msg = "\nThe following repos are in-progress:"
    for repo in config.state_data.keys():
        if config.state_data[repo].progress in [PROGRESS.IN_PROGRESS, PROGRESS.RETRY]:
            total_state[PROGRESS.IN_PROGRESS.value] += 1
            repos.append(repo)
    if repos:
        ret_str += _print(msg)
        for repo in repos:
            ret_str += _print(
                f"\t{repo} - Change ID: {config.state_data[repo].proposal.change_id}\n"
                f"\t  {gerrit_link_maker(config, config.state_data[repo])[1]}")
    repos.clear()

    msg = "\nThe following repos are waiting on dependencies to be updated:"
    for repo in config.state_data.keys():
        if config.state_data[repo].progress in [PROGRESS.WAIT_DEPENDENCY,
                                                PROGRESS.WAIT_INCONSISTENT]:
            total_state[PROGRESS.WAIT_DEPENDENCY.value] += 1
            repos.append(repo)
    if repos:
        ret_str += _print(msg)
        for repo in repos:
            ret_str += _print("\t", repo, " depends on: ",
                              ", ".join(
                                  list(config.state_data[repo].deps_yaml["dependencies"].keys())))

    repos.clear()
    msg = "\nThe following repos have been updated and merged:"
    for repo in config.state_data.keys():
        if config.state_data[repo].progress == PROGRESS.DONE:
            total_state[PROGRESS.DONE.value] += 1
            repos.append(repo)
    if repos:
        ret_str += _print(msg)
        for repo in repos:
            ret_str += _print(f"\t{repo} - Change ID: {config.state_data[repo].proposal.change_id}")
    repos.clear()
    msg = "\nThe following repos did not require an update:"
    for repo in config.state_data.keys():
        if config.state_data[repo].progress == PROGRESS.DONE_NO_UPDATE:
            total_state[PROGRESS.DONE_NO_UPDATE.value] += 1
            repos.append(repo)
    if repos:
        ret_str += _print(msg)
        for repo in repos:
            ret_str += _print(f"\t{repo}")
    repos.clear()
    msg = "\nThe following repos failed to update:"
    for repo in config.state_data.keys():
        if config.state_data[repo].progress >= PROGRESS.DONE_FAILED_NON_BLOCKING:
            total_state[PROGRESS.DONE_FAILED_NON_BLOCKING.value] += 1
            repos.append(repo)
    if repos:
        ret_str += _print(msg)
        for repo in repos:
            ret_str += _print(f"\t{repo}")

    return total_state, ret_str
