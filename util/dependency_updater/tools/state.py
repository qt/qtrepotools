# Copyright (C) 2021 The Qt Company Ltd.
# SPDX-License-Identifier: LicenseRef-Qt-Commercial OR LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only

import os
import pickle
import shutil
from pathlib import Path
from time import sleep

import git.exc
from git import Git
from git import Repo as GitRepo, exc

from .repo import Repo


def fetch_and_checkout(config, repo):
    """Try to fetch the remote ref in the personal gerrit branch for
    the running user."""
    g = Git(repo.working_tree_dir)
    try:
        g.fetch(['origin', config._state_ref])
        g.checkout('FETCH_HEAD')
    except git.exc.GitCommandError as e:
        if "couldn't find remote ref refs/personal" in e.stderr:
            pass
        else:
            print(e)
    del g


def check_create_local_repo(config) -> GitRepo:
    """Create a local repo for saving state and push it to
    the user's personal ref. Checkout any existing version
    on the user's personal remote, or create a new commit"""
    path = Path(config.cwd, "_state")
    if not path.exists():
        os.mkdir(path)
    try:
        repo = GitRepo(path)
        if "origin" not in [r.name for r in repo.remotes] and config._state_ref:
            repo.create_remote('origin',
                               f"ssh://{config.GERRIT_HOST[8:]}/{config.GERRIT_STATE_PATH}")
    except exc.InvalidGitRepositoryError:
        repo = GitRepo.init(path)
        if config._state_ref:
            repo.create_remote('origin',
                               f"ssh://{config.GERRIT_HOST[8:]}/{config.GERRIT_STATE_PATH}")
            fetch_and_checkout(config, repo)
        state_path = Path(repo.working_tree_dir, "state.bin")
        if not state_path.exists():
            with open(state_path, 'wb') as state_file:
                pickle.dump({}, state_file)
            repo.index.add('state.bin')
            repo.index.commit("Empty state")
            if config._state_ref:
                repo.remotes.origin.push(['-f', f"HEAD:{config._state_ref}"])
    if not config._state_ref:
        print("\nWARN: Unable to create git remote for state!\n"
              "WARN: State will only be saved locally to _state/state.bin.\n"
              "INFO: Please configure an ssh user in ~/.ssh/config for your gerrit host\n"
              "INFO: as set by 'GERRIT_HOST' in config.yaml in order to save state in gerrit.\n")
    return repo


def load_updates_state(config) -> dict[str, Repo]:
    """Load previous state and apply retention policy if not simulating a run."""
    if config.args.no_state:
        print("Running in no-state mode! No state loaded, and progress will not be saved on exit!")
        return {}
    print("\nLoading saved update data from codereview...")
    if config._state_ref:
        fetch_and_checkout(config, config.state_repo)
    state_path = Path(config.state_repo.working_tree_dir, "state.bin")

    if not state_path.exists():
        with open(state_path, 'wb') as state_file:
            pickle.dump(dict(), state_file)
    state_data = {}
    with open(state_path, mode='rb') as state_file:
        state_data = pickle.load(state_file)

    print("Done loading state data!")
    if state_data.get(config.args.branch):
        return state_data[config.args.branch]
    else:
        return {}


def update_state_data(old_state: dict[str, Repo], new_data: dict[str, Repo]) -> dict[
    str, Repo]:
    """Merge two update set dicts"""
    updated = old_state
    for key in new_data.keys():
        if old_state.get(key):
            updated[key].merge(new_data[key])
        else:
            updated[key] = new_data[key]
    return updated


def save_updates_state(config, _clear_state: bool = False) -> None:
    """Save updates to the state file"""
    if not config.args.simulate:
        if _clear_state:
            clear_state(config)
            return
        print("Saving update state data to codereview...")
        state_path = Path(config.state_repo.working_tree_dir, "state.bin")
        data: dict[str, dict[str, Repo]] = {}
        with open(state_path, 'rb') as state_file:
            data = pickle.load(state_file)
            data[config.args.branch] = config.state_data
        with open(state_path, 'wb') as state_file:
            pickle.dump(data, state_file)
        config.state_repo.index.add("state.bin")
        config.state_repo.index.commit("Update state")
        if config._state_ref:
            config.state_repo.remotes.origin.push(['-f', f"HEAD:{config._state_ref}"])
    elif config.args.no_state:
        print("Running in no-state mode. Not saving state!")


def clear_state(config) -> None:
    """Clear state data. All branches are wiped if not specified!"""
    print("Clearing state and resetting updates...")
    if config.args.branch:
        config.state_data = {}
        save_updates_state(config)
        print(f"Clearing branch state for {config.args.branch}")
        return

    if config._state_ref:
        try:
            config.state_repo.remotes.origin.push(['-f', f":{config._state_ref}"])
            print("Cleared remote state on codereview...")
        except git.exc.GitCommandError:
            print(
                "WARN: Failed to push an empty commit, probably because the state is already clear.")
        del config.state_repo  # Need to tear down the instance of PyGit to close the file handle.
        sleep(5)  # workaround for sometimes slow closing of git handles.
    else:
        print("\nWARN: No state remote ref set! Only deleting local state.bin file.\n"
              "WARN: Run this script again with --reset after configuring an ssh user\n"
              "WARN: in ~/.ssh/config for your gerrit host as set by 'GERRIT_HOST' in config.yaml.\n"
              "WARN: If a remote state exists next time this script is run, it will likely\n"
              "WARN: cause unexpected behavior!")
    shutil.rmtree(Path(config.cwd, "_state"), onerror=_unlink_file)
    print("Deleted local state files.")


def _unlink_file(function, path, excinfo):
    """In the case that shutil.rmtree fails on a file."""
    os.unlink(path)
