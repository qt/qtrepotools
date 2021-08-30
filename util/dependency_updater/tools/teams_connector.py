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

import pymsteams as msteams
import yaml
from gerrit.changes import change as GerritChange
from .repo import Repo, PROGRESS
from typing import Union


def gerrit_link_maker(config, change: Union[GerritChange.GerritChange, Repo]) -> tuple[str, str]:
    repo = ""
    _change = None
    if type(change) == GerritChange:
        repo = change.project
        _change = change
    elif type(change) == Repo:
        repo = change.id
        _change = config.datasources.gerrit_client.changes.get(change.proposal.change_id)
    if not repo:
        return "", ""
    subject = _change.subject
    mini_sha = _change.get_revision("current").get_commit().commit[:10]
    url = f"{config.GERRIT_HOST}c/{repo}/+/{_change._number}"
    return f"({mini_sha}) {subject[:70]}{'...' if len(subject) > 70 else ''}", url


class TeamsConnector:
    def __init__(self, config):
        self.config = config
        self.endpoint = config.MS_TEAMS_NOTIFY_URL
        if self.endpoint:
            print("MS Teams connector initialized")
        else:
            print("MS Teams connector disabled: No webhook URL provided.")

    @staticmethod
    def _link_creator_failed_stage(card: msteams.connectorcard,
                                   repo) -> msteams.connectorcard:
        for change_id in repo.to_stage:
            card.addLinkButton(*gerrit_link_maker(repo, change_id))
        return card

    def _card_formatter_failed_stage(self, card: msteams.connectorcard,
                                     repo) -> msteams.connectorcard:
        card.color('#CB9E5A')
        card.title(f"{repo.id}: Dependency update warning")
        if len(repo.to_stage) == 1:
            card.text(f"Staging of dependency update in {repo.id} -> {repo.branch} failed.")
        else:
            card.text(f"Co-staging changes with the dependency update in {repo.id} -> {repo.branch}"
                      f" failed.\nChanges:")
        self._link_creator_failed_stage(card, repo)
        return card

    def send_teams_webhook_failed_stage(self, repo):
        if self.config.args.simulate:
            print(f"SIM: send Staging Failed Teams webhook for {repo.id}")
            return
        if self.endpoint:
            message_card = msteams.connectorcard(self.endpoint)
            message_card = self._card_formatter_failed_stage(message_card, repo)
            message_card.send()
            print(message_card.last_http_status.status_code, message_card.last_http_status.reason,
                  message_card.last_http_status.text)
            if message_card.last_http_status.status_code != 200:
                print(
                    f"WARN: Unable to send alert to webhook for {repo.id}")
                return False
        return True

    def send_teams_webhook_module_failed(self, repo, text_override: str = None, test_failures: str = None, pause_links: bool = False):
        if self.config.args.simulate:
            print(f"SIM: send Teams webhook for {repo.id} with text:"
                  + (text_override or f"Dependency update on *{repo.id}* failed in **{repo.branch}**")
                  + '\n' + test_failures)
            return
        if self.endpoint:
            message_card = msteams.connectorcard(self.endpoint)
            message_card.color('#FF0000')
            message_card.text(text_override or f"Dependency update on *{repo.id}* failed in **{repo.branch}*"
                                               f"*")
            message_card.addSection(msteams.cardsection().linkButton(*gerrit_link_maker(self.config, repo)))
            if pause_links:
                pause_section = msteams.cardsection()
                pause = msteams.potentialaction(f"Pause Updates on '{repo.branch}' (This failure can be fixed)", "HttpPOST")
                pause.payload["target"] = "https://qt-cherry-pick-bot.herokuapp.com/pause-submodule-updates"
                pause.payload["body"] = yaml.dump({"branch": repo.branch})
                msteams.connectorcard.addPotentialAction(pause_section, pause)
                resume = msteams.potentialaction(f"Resume Updates on '{repo.branch}'", "HttpPOST")
                resume.payload["target"] = "https://qt-cherry-pick-bot.herokuapp.com/resume-submodule-updates"
                resume.payload["body"] = yaml.dump({"branch": repo.branch})
                msteams.connectorcard.addPotentialAction(pause_section, resume)
                message_card.addSection(pause_section)
            if test_failures:
                message_card.addSection(
                    msteams.cardsection().text('```\nBuild/Test Log:\n' + test_failures))
            message_card.send()
            if message_card.last_http_status.status_code != 200:
                print(
                    f"WARN: Unable to send alert to webhook for {repo.id}")
                return False
        return True

    def send_teams_webhook_finish_failed(self, text: str, config, reset_links=False):
        if self.config.args.simulate:
            print(f"SIM: send Teams webhook for Round Finished Failed with text: {text}")
            return True
        if self.endpoint:
            message_card = msteams.connectorcard(self.endpoint)
            message_card.text(text)
            if reset_links:
                reset_section = msteams.cardsection()
                reset = msteams.potentialaction(
                    f"Reset round (New qtbase)", "HttpPOST")
                reset.payload[
                    "target"] = "https://qt-cherry-pick-bot.herokuapp.com/reset-submodule-updates"
                reset.payload["body"] = yaml.dump({"branch": config.args.branch})
                msteams.connectorcard.addPotentialAction(reset_section, reset)
                retry = msteams.potentialaction(
                    f"Retry current failed modules on '{config.args.branch}'", "HttpPOST")
                retry.payload[
                    "target"] = "https://qt-cherry-pick-bot.herokuapp.com/retry-submodule-updates"
                retry.payload["body"] = yaml.dump({"branch": config.args.branch})
                msteams.connectorcard.addPotentialAction(reset_section, retry)
                message_card.addSection(reset_section)
            failed_section = msteams.cardsection()
            failed_modules_text = "\n".join([r.id for r in config.state_data.values()
                                             if r.progress == PROGRESS.DONE_FAILED_BLOCKING])
            failed_section.text(f"```\nFailed Modules on {config.args.branch}:\n{failed_modules_text}")
            message_card.addSection(failed_section)
            message_card.send()
            if message_card.last_http_status.status_code != 200:
                print(
                    f"WARN: Unable to send alert to webhook for Round Failed Finished on {config.args.branch}")
                return False
        return True

    def send_teams_webhook_basic(self, text: str, repo: Repo = None, reset_links=False):
        if self.config.args.simulate:
            print(f"SIM: send Teams webhook for {repo.id} with text: {text}")
            return True
        if self.endpoint:
            message_card = msteams.connectorcard(self.endpoint)
            message_card.text(text)
            if repo and repo.proposal.change_id:
                message_card.addLinkButton(*gerrit_link_maker(self.config, repo))
            message_card.send()
            if message_card.last_http_status.status_code != 200:
                print(
                    f"WARN: Unable to send alert to webhook for {repo.id}")
                return False
        return True
