# Copyright (C) 2021 The Qt Company Ltd.
# SPDX-License-Identifier: LicenseRef-Qt-Commercial OR LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only

import sys

from gerrit import GerritClient

from tools.namespace import Namespace


class Datasources(Namespace):
    gerrit_client: GerritClient = None

    def load_datasources(self, config):
        print("Discovering and configuring datasources...")
        datasource_names = [o for o in Datasources.__dict__.keys() if o.endswith("_client")]
        for func_name in datasource_names:
            dict.__setattr__(self, func_name,
                             getattr(sys.modules["tools.datasources." + func_name],
                                     "create_" + func_name)(config))
        print("Done loading datasources!")
