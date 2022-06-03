# Copyright (C) 2021 The Qt Company Ltd.
# SPDX-License-Identifier: LicenseRef-Qt-Commercial OR LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only
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
