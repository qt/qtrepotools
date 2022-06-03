# Copyright (C) 2021 The Qt Company Ltd.
# SPDX-License-Identifier: LicenseRef-Qt-Commercial OR LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only

class Namespace(object):
    """Inheriting this class enables property-style 'object.attr'
    access to member attributes instead of relying on dict-style
    'object[attr]' and '.get(attr)' accessors.
    """

    def __init__(self, **kwargs): self.__dict__.update(kwargs)

    @property  # For use when serializing, to dump back to JSON
    def as_map(self): return self.__dict__

    def __repr__(self):
        return str(self.as_map)
