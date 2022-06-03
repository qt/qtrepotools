# Copyright (C) 2020 The Qt Company Ltd.
# SPDX-License-Identifier: LicenseRef-Qt-Commercial OR LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only

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
