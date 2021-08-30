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
import os
import signal
from getpass import getpass

import gerrit.utils.exceptions
from gerrit import GerritClient


def test_gerrit_auth(client: GerritClient) -> bool:
    try:
        client.projects.get("qt/qtbase").HEAD
    except gerrit.utils.exceptions.ClientError as e:
        print(e, e.args)
        return False
    print("Gerrit auth OK...")
    return True


def validate_gerrit_config(config, client):
    def input_with_maybe_timeout(func: [input, getpass], message: str) -> str:
        """Try to get input with timeout on Unix-based, or wait indefinitely on Windows."""

        def interrupted(signum, frame):
            """called when read times out"""
            raise TimeoutError

        if os.name == 'posix':
            signal.signal(signal.SIGALRM, interrupted)
            signal.alarm(15)
        try:
            i = func(message)
        except KeyboardInterrupt:
            if os.name == 'posix':
                signal.alarm(0)
            raise KeyboardInterrupt
        if os.name == 'posix':
            signal.alarm(0)
        return i

    while not test_gerrit_auth(client):
        print("Bad Gerrit user or password.\n"
              f"Authenticated access to {config.GERRIT_HOST} is recommended for operation.")
        print(f"\nConfigured username '{config.GERRIT_USERNAME}'")
        try:
            u = input_with_maybe_timeout(input,
                                         "Press Return to accept or re-enter your username now: ")
        except KeyboardInterrupt:
            print("\n\nGerrit Username input cancelled. Proceeding without gerrit authentication!")
            config.GERRIT_USERNAME = ""
            config.GERRIT_PASSWORD = ""
            return create_gerrit_client(config)
        if u:
            config.GERRIT_USERNAME = u
        try:
            p = input_with_maybe_timeout(getpass, "Please re-enter your password: ")
        except KeyboardInterrupt:
            print("\n\nGerrit Username input cancelled. Proceeding without gerrit authentication!")
            config.GERRIT_USERNAME = ""
            config.GERRIT_PASSWORD = ""
        if not p:
            config.GERRIT_USERNAME = ""
            config.GERRIT_PASSWORD = ""
        return create_gerrit_client(config)
    return client


def create_gerrit_client(config):
    """Create an instance of an Gerrit client.
        Will prompt for credentials if not configured."""
    client = GerritClient(base_url=config.GERRIT_HOST, username=config.GERRIT_USERNAME,
                          password=config.GERRIT_PASSWORD)
    client = validate_gerrit_config(config, client)
    return client
