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
import re
from enum import IntEnum
from urllib.parse import unquote

import yaml

from .namespace import Namespace
from .proposal import Proposal


class PROGRESS(IntEnum):
    ERROR = 0
    UNSPECIFIED = 1
    READY = 2
    WAIT_DEPENDENCY = 3
    WAIT_INCONSISTENT = 4
    IN_PROGRESS = 5
    RETRY = 6
    DONE = 7
    DONE_NO_UPDATE = 8
    DONE_FAILED_NON_BLOCKING = 9
    DONE_FAILED_BLOCKING = 10
    IGNORE_IS_META = 11


class Repo(Namespace):
    """Base information about a repository/submodule"""
    id: str = ""  # Fully qualified id such as qt/qtbase or qt/tqtc-qtbase
    prefix: str = ""  # Bare prefix such as qt/ or qt/tqtc-
    name: str = ""  # Bare name such as qtbase
    original_ref: str = ""  # Ref to associate with this repo. This value should never be changed.
    branch: str = ""  # Branch where dependencies.yaml was found. May differ from the specified branch.
    deps_yaml: yaml = dict()
    dep_list: list[str]
    proposal: Proposal = Proposal()
    to_stage: list[str]
    progress: PROGRESS = PROGRESS.UNSPECIFIED
    stage_count: int = 0
    retry_count: int = 0
    is_supermodule: bool = False  # Bypasses dependency calculation
    # Does not stop the round from continuing unless a blocking module depends on it.
    is_non_blocking: bool = False

    def __init__(self, id: str, prefix: str,
                 proposal: Proposal = None,
                 to_stage: list[str] = None, **kwargs):
        super().__init__(**kwargs)
        self.to_stage = list()
        self.dep_list = list()
        self.id = unquote(id)
        self.prefix = prefix
        self.name = id.removeprefix(prefix)
        self.proposal = proposal or Proposal()
        if to_stage is not None:
            self.to_stage = to_stage
        if proposal and proposal.change_id not in self.to_stage:
            self.to_stage.append(proposal.change_id)

    def __str__(self):
        return f"Repo(id='{self.id}', name='{self.name}'," \
               f" ref='{self.original_ref}', branch='{self.branch}'," \
               f" progress={self.progress}," \
               f" stage_count={self.stage_count}," \
               f" retry_count={self.retry_count}," \
               f" proposal={str(self.proposal)})"

    def __repr__(self):
        return self.id

    def __eq__(self, other: [str, 'Repo']):
        if type(other) == str:
            r = r'((?:.*/){1,}(?:(?!(.*-){2,})|(?:[^-]*-)))'
            re_other_prefix = re.findall(r, other)
            if len(re_other_prefix):
                other_prefix: str = re_other_prefix.pop()[0]
                # Strip relative prefixes from dependency.yaml inputs
                if other_prefix.startswith('../'):
                    return other.removeprefix(other_prefix) == self.name
                else:
                    return other == self.id
            else:
                return other == self.name
        return self.id == other.id

    def merge(self, other: "Repo"):
        if self.progress >= PROGRESS.DONE:
            # Anything marked as done should only ever be updated
            # with specific intention, not blindly merged.
            return
        for prop, val in vars(other).items():
            if val:
                self.__setattr__(prop, val)
