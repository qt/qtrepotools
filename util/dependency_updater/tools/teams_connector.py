# Copyright (C) 2021 The Qt Company Ltd.
# SPDX-License-Identifier: LicenseRef-Qt-Commercial OR LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only

import pymsteams as msteams
import yaml
import json
from gerrit.changes import change as GerritChange
from .repo import Repo, PROGRESS
from typing import Union


def gerrit_link_maker(config, change_or_repo: Union[GerritChange.GerritChange, Repo, str],
                      change_id_override: str = None) -> tuple[str, str]:
    repo = ""
    change_override = ""
    _change = None
    if type(change_or_repo) == GerritChange:
        repo = change_or_repo.project
        _change = change_or_repo
    elif type(change_or_repo) == Repo:
        repo = change_or_repo.id
    _change = _change \
        or config.datasources.gerrit_client.changes.get(change_id_override
                                                        or change_or_repo.proposal.change_id)
    if not repo:
        return "", ""
    subject = _change.subject
    mini_sha = _change.get_revision("current").get_commit().commit[:10]
    url = f"{config.GERRIT_HOST}c/{repo}/+/{_change._number}"
    change_status = f"[{_change.status}]" if _change.status != "NEW" else ""
    return f"{change_status}({mini_sha}) {subject[:70]}{'...' if len(subject) > 70 else ''}", url


class TeamsConnector:
    def __init__(self, config):
        self.config = config
        self.endpoint = config.MS_TEAMS_NOTIFY_URL
        if self.endpoint:
            print("MS Teams connector initialized")
        else:
            print("MS Teams connector disabled: No webhook URL provided.")

    def _link_creator_failed_stage(self, card: msteams.connectorcard,
                                   repo) -> msteams.connectorcard:
        for change_id in repo.to_stage:
            card.addLinkButton(*gerrit_link_maker(self.config, repo, change_id))
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
        return True

    def send_teams_webhook_module_failed(self, repo, text_override: str = None,
                                         test_failures: str = None, pause_links: bool = False):
        if self.config.args.simulate:
            _text = text_override or f"Dependency update on *{repo.id}* failed in **{repo.branch}**"
            print(f"SIM: send Teams webhook for {repo.id} with text:"
                  f"{_text}\n{test_failures}")
            return
        if self.endpoint:
            message_card = msteams.connectorcard(self.endpoint)
            message_card.color('#FF0000')
            message_card.text(
                text_override or f"Dependency update on *{repo.id}* failed in **{repo.branch}*"
                                 f"*")
            message_card.addSection(
                msteams.cardsection().linkButton(*gerrit_link_maker(self.config, repo)))
            if pause_links:
                pause_section = msteams.cardsection()
                pause = msteams.potentialaction(
                    f"Pause Updates on '{repo.branch}' (This failure can be fixed)", "HttpPOST")
                pause.payload[
                    "target"] = "https://qt-cherry-pick-bot.herokuapp.com/pause-submodule-updates"
                pause.payload["body"] = json.dumps({"branch": repo.branch})
                msteams.connectorcard.addPotentialAction(pause_section, pause)
                resume = msteams.potentialaction(f"Resume Updates on '{repo.branch}'", "HttpPOST")
                resume.payload[
                    "target"] = "https://qt-cherry-pick-bot.herokuapp.com/resume-submodule-updates"
                resume.payload["body"] = json.dumps({"branch": repo.branch})
                msteams.connectorcard.addPotentialAction(pause_section, resume)
                message_card.addSection(pause_section)
            if test_failures:
                message_card.addSection(
                    msteams.cardsection().text('```\nBuild/Test Log:\n' + test_failures))
            message_card.send()
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
                    "Reset round (New qtbase)", "HttpPOST")
                reset.payload[
                    "target"] = "https://qt-cherry-pick-bot.herokuapp.com/reset-submodule-updates"
                reset.payload["body"] = json.dumps({"branch": config.args.branch})
                msteams.connectorcard.addPotentialAction(reset_section, reset)
                retry = msteams.potentialaction(
                    f"Retry current failed modules on '{config.args.branch}'", "HttpPOST")
                retry.payload[
                    "target"] = "https://qt-cherry-pick-bot.herokuapp.com/retry-submodule-updates"
                retry.payload["body"] = json.dumps({"branch": config.args.branch})
                msteams.connectorcard.addPotentialAction(reset_section, retry)
                message_card.addSection(reset_section)
            failed_section = msteams.cardsection()
            # Join the list of failed modules with their failed dependencies if there are any.
            # If there's no failed dependencies, it means the module itself had issues integrating.
            failed_modules_text = "\n".join(r.id for r in config.state_data.values()
                                            if r.progress == PROGRESS.DONE_FAILED_BLOCKING)
            failed_modules_text += "\n\nThe following modules could not be updated due to failed" \
                                   " dependencies:\n"
            failed_modules_text += "\n".join(f"{r.id}: {r.failed_dependencies}"
                                             for r in config.state_data.values()
                                             if r.progress == PROGRESS.DONE_FAILED_DEPENDENCY)
            failed_section.text(
                f"```\nFailed Modules on {config.args.branch}:\n{failed_modules_text}")
            message_card.addSection(failed_section)
            message_card.send()
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
        return True
