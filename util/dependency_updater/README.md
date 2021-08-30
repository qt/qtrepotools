# Dependency Update Utility

### What is this for?

*This utility replaces the script at qt/qtqa/src/qtmoduleupdater*

Every submodule of qt6 includes a "dependencies.yaml" file which
specifies a revision of the other modules it directly depends on.
If checked out together, the given module should be guaranteed to
build and pass tests. In this model, a "dependency tree" forms,
where "leaf" modules depend on "trunk" or "root" modules.

In order to update a leaf module's dependencies, the tree must be
walked down to the root-most module it depends on which itself
has no dependencies. Then, starting from the root (often qtbase),
each module's dependencies.yaml file is updated with the latest SHA.
When a trunk module passes CI after being updated with new root SHAs,
leaf modules can be updated with the latest trunk SHAs.

### How does it work?
This script/bot takes a list of repositories, either explicitly or
gathered from qt5.git's .gitmodules file, and discovers the
dependency tree by cross-checking every module's dependencies.yaml file.
During this process, it collects the latest SHAs for each module, and
also checks for modules which need to be updated due to inconsistent
SHAs across modules for the same dependency. **Further, if a dependency
is discovered that was not explicitly passed to the script, it is
added to internal memory and will be updated as well if necessary.**

The script provides a number of modes of operation. See the script's
usage manual below.

In typical usage, the script will need to be run multiple times to
complete a round, as it creates change requests on
codereview.qt-project.org which must be integrated. When a tracked
change is integrated, the script should be re-run to progress the
update round. When all required module changes are successfully
integrated, if run with options to do so, the script will perform
an update to qt5.git and/or yocto/meta-qt6 with the updated submodule
SHAs collected during the round. When a round completes, the state
is cleared and a new round will begin when the script is run again.

Note: It is safe to run the script on a timer/trigger. Since the script
keeps track of actions it is taking, it should never duplicate work
if a dependency update is in-progress. If there is no work to be done,
such a message will be printed and the script will exit.

### Usage
A number of python modules are required. Usage of a Python virtual
environment such as pipenv is strongly suggested.

```
pipenv install
pipenv run python3 main.py [args]
```

See the script's --help output for a full list of options. The
example scenarios below cover some of the most commonly used situations.

Note that most scenarios require the utility to be run multiple times
to complete an update "round" since multiple modules in the dependency
tree often need to be updated separately, and in a specific order.

1. Clean and clear the utility's state and forget previous runs for a branch.
   1. `python3 main.py --branch dev --reset`
   2. This action clears state for the specified branch. You should always
      clear the working state before starting a fresh round/operation.
   3. Note that simply deleting the local __state/state.bin_ file will
      not necessarily clear the state. This utility also stores state
      data in codereview personal branches if ssh credentials are configured
      in ~/.ssh/config. Always run the utility with `--reset` to clear state.

2. Run the utility to perform a one-time sync of a module with qt/qt5.git
   submodule SHAs.
   1. `python3 main.py --branch dev --noState --repos
       qt-extensions/qthttpserver`
   2. This creates a new change for `qt-extensions/qthttpserver` in
      codereview which needs to go through the normal review process.
      The dependencies.yaml file is updated to the latest SHAs in qt5.git
   3. This scenario does not usually require more than a single run of
      the tool. As such, `--noState` can be used to avoid the need to
      reset the utility before running. This also prevents interference to
      any ongoing rounds since no state data is written.

3. Run the utility to simulate an action
   1. `python3 main.py --sim --branch dev --repos qt-extensions/qthttpserver`
   2. Performing a dry-run prints to the console which actions the script
      would take. No actions are actually performed on gerrit, and no
      changes will be created. Further, using --sim will never update the
      local persistent state of the tool.

4. Perform an update to one or more repos using the latest available
   SHAs from each dependency's own repo.
   1. `python3 main.py --head --branch dev --repos qt-extensions/qthttpserver`
   2. This assembles a full dependency map for the given repo(s) and updates
      each one, starting from the most base module.
   3. Performing an update with `--head` should be used with a clean state.
   4. When the first updates are merged, run the utility again with the same
      arguments to continue the "round".
   5. Continue to run the utility repeatedly to progress the round until
      the target module is updated with new SHAs for its dependencies.

5. Perform an update for one or more modules, then update qt/qt5.git and
   yocto/meta-qt6 with the new module SHA(s).
   1. `python3 main.py --head --branch dev --qt5Update --yoctoUpdate --repos
       qt-extensions/qthttpserver`
   2. Performs as (4) above, but when the target module(s) have been updated,
      a further run of the utility will update qt/qt5.git and/or
      yocto/meta-qt6.git with the merged SHAs of the target modules. Only
      modules which already exist in the super-repos will be updated.

6. Rewind an ongoing round to a specific dependency to pull in additional
   changes and continue the update round.
   1. `python3 main.py --head --branch dev --rewind qt/qtdeclarative --repos
       qt-extensions/qthttpserver`
   2. In this example, if the round has already successfully merged an update
      for _qt/qtdeclarative_ but _qt/qtwebsockets_ is broken until a further
      fix can be picked up in _qt/qtdeclarative_, using
      `--rewind qt/qtdeclarative` will clear the state for modules which
      depend on _qt/qtdeclarative_ and pull the new head of _qt/qtdeclarative_,
      then continue from there.

7. Remove a dependency from a module
   1. `python3 main.py --head --branch dev --dropDependency
       qt/qtsvg:qt-extensions/qthttpserver --repos qt-extensions/qthttpserver`
   2. If a repo no longer needs a dependency, it can be removed in this way.
   3. Combine `--dropDependency` with `--qt5Update` to remove a dependency
      from a module and ensure that qt/qt5.git's .gitmodules reference file
      also gets updated appropriately.
   4. _**Note:**_ This action is destructive! See the `--help` output for
      important information and detailed usage instructions

8. Update all current modules in qt/qt5.git
   1. `python3 main.py --default-repos --branch dev --qt5Update --yoctoUpdate`
   2. This collects a list of all modules in qt5 marked as 'essential',
      'addon', 'deprecated', 'ignore', or 'preview' in the _.gitmodules_
      file of _qt/qt5.git_ and updates the dependency tree to the latest
      branch HEAD of each module.
   3. When finished, qt/qt5.git and yocto/meta-qt6 are updated with the
      SHAs of all modules updated in the round

9. Include in a round additional repos/modules which should be considered
   "non-blocking" by the utility.
    1. `python3 main.py --default-repos --branch dev --qt5Update --yoctoUpdate
        --nonBlockingRepos qt-extensions/qthttpserver`
       1. This will perform an update round as requested, but if a non-blocking
          module update fails, it will be ignored for the rest of the round,
          allowing all other repos to continue normally.
       2. If any repos specified by `--repos` or gathered automatically by
          `--default-repos` require a repo specified as non-blocking, that
          non-blocking repo will be converted to a blocking-status since
          failure in it would lead to the failure of a normally blocking
          module.

11. Auto-approve and stage module updates
    1. `python3 main.py --stage --default-repos --branch dev --qt5Update
        --yoctoUpdate`
    2. If the user running the tool has provided codereview credentials which
       have Approver and QtStage rights access, the utility will self-approve
       created changes and automatically stage them.
    3. **_Note:_** Use of this option is generally discouraged outside of
       automation.


### Further Notes
- The script may take some time to run, depending on internet speed,
  responsiveness of Gerrit, and how many repositories it is updating.

- Unless run with `--noState`, a run of the script will attempt to use
  saved information about repositories and their dependencies/SHAs.
  If you experience trouble when trying to start a round, try running
  the script with --reset to clear data for the target branch before
  continuing.

- Use of `--noState` is intended to allow the user to perform atomic
  operations without the need to reset or save state, potentially
  contaminating an ongoing round for a given branch. Generally,
  this would be used when syncing a repo's dependencies.yaml
  with qt5.git's current submodule SHAs, as this usually does not
  require more than one run of the script.

- `--sweepChanges` will sweep which have the script's GERRIT_USERNAME
  as a reviewer on the change. In practice, this option is usually
  reserved for the Qt Submodule Update bot, but can be enabled ***if you
  know what you are doing.***

- Repo prefixes are fuzzy. The script defaults to "qt/", but in theory
  any prefix can be used. If you set the prefix to "playground/", the
  script will prefer dependencies which exist there. If a dependency
  cannot be found in the preferred namespace, a fuzzy-match search is
  performed and the best match is attempted. The user will be notified
  of any fuzzy-match repo selections which are made.

- Running the utility at-will during an ongoing round is a safe operation.
  The utility will simply update its internal state of ongoing changes
  in codereview and exit if no further action can be taken.
