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

class Proposal:
    proposed_yaml: dict
    change_id: str
    change_number: str
    gerrit_status: str = ""
    merged_ref: str = ""
    inconsistent_set: dict

    def __init__(self, proposed_yaml: dict = None,
                 change_id: str = None, change_number: str = None, inconsistent_set: dict = None):
        self.proposed_yaml = proposed_yaml
        self.change_id = change_id
        self.change_number = change_number
        self.inconsistent_set = inconsistent_set

    def __setattr__(self, key, value):
        if key == "change_number" and type(value) is int:
            self.__dict__[key] = str(value)
        else:
            self.__dict__[key] = value

    def __str__(self):
        return f"Proposal(change_id='{self.change_id}'," \
               f" change_number={self.change_number}" \
               f" gerrit_status='{self.gerrit_status}'" \
               f" inconsistent_set={self.inconsistent_set}," \
               f" merged_ref={self.merged_ref}," \
               f" proposed yaml={self.proposed_yaml})"

    def __bool__(self):
        if self.proposed_yaml or self.change_id or self.inconsistent_set:
            return True
        else:
            return False
