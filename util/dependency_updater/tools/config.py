############################################################################
##
## Copyright (C) 2021 The Qt Company Ltd.
## Contact: https://www.qt.io/licensing/
##
## This file is part of the qtqa module of the Qt Toolkit.
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

import json
import os
from pathlib import Path
from shutil import copyfile

import urllib3 as urllib
import yaml
from url_normalize import url_normalize

from .datasources.datasources import Datasources
from .namespace import Namespace
from .repo import Repo
from .teams_connector import TeamsConnector


class Config(Namespace):
    """Configuration object. Also contains datasources for use."""
    args: Namespace
    cwd: os.PathLike
    datasources: Datasources = Datasources()
    teams_connector: TeamsConnector
    GERRIT_HOST: str
    GERRIT_STATE_PATH: str
    GERRIT_USERNAME: str
    GERRIT_PASSWORD: str
    MS_TEAMS_NOTIFY_URL: str
    state_repo: Repo
    state_data: dict[str, Repo] = {}
    _state_ref: str = None
    qt5_default: dict[str, Repo] = {}
    suppress_warn: bool = False
    REPOS: list[str]
    NON_BLOCKING_REPOS: list[str] = []
    rewind_module: Repo = None
    drop_dependency: Repo = None
    drop_dependency_from: list[Repo] = None


def _load_config(file, args):
    """Load configuration from disk or environment"""
    cwd = Path(__file__).parent.parent
    file = cwd.joinpath(file)
    c = dict()
    if file.exists():
        with open(file) as config_file:
            c = yaml.load(config_file, Loader=yaml.SafeLoader)
    else:
        try:
            copyfile(file.parent / (file.name + ".template"), file)
            print("Config file not found, so we created 'config.yaml' from the template.")
            with open(file) as config_file:
                c = yaml.load(config_file)
        except FileNotFoundError:
            print("ERROR: Unable to load config because config.yaml, or config.yaml.template\n"
                  "was not found on disk. Please pull/checkout config.yaml.template from\n"
                  "the repo again.")

    for key in c.keys():
        if os.environ.get(key):
            print(f'Overriding config option {key} with environment variable.')
            c[key] = os.environ[key]
    config = Config(**c)
    config.cwd = cwd
    config.args = args
    config.GERRIT_HOST = url_normalize(config.GERRIT_HOST)
    config.teams_connector = TeamsConnector(config)
    ssh_file = Path(os.path.expanduser('~'), ".ssh", "config")
    if ssh_file.exists():
        with open(ssh_file) as ssh_config:
            contents = ssh_config.read()
            gerrit_base_url = urllib.util.parse_url(config.GERRIT_HOST).host
            loc = contents.find(gerrit_base_url)
            user_loc = contents.find("User", loc)
            user_name = contents[user_loc:contents.find("\n", user_loc)].split(" ")[1]
            if user_name:
                config._state_ref = f"refs/personal/{user_name or config.GERRIT_USERNAME}" \
                                    f"/submodule_updater"
    return config
