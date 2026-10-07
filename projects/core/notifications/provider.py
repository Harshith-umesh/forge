"""
Per-project Slack notification provider interface.

Projects that want custom Slack notifications subclass
SlackNotificationProvider and pass it to CIApp.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import projects.core.notifications.slack.api as slack_api
from projects.core.library import vault

logger = logging.getLogger(__name__)


@dataclass
class NotificationContext:
    """Context passed to a notification provider when a notification fires."""

    status: dict[str, Any]
    finish_reason: str
    project_name: str
    pr_number: str | None = None
    job_type: str | None = None
    artifact_dir: Path | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class SlackNotificationProvider(ABC):
    """Abstract base for per-project Slack notification providers.

    Subclass this and implement the abstract methods to give a FORGE
    project its own Slack channel and message format.

    Token resolution uses the standard forge vault (topsail-bot.slack-token
    from psap-forge-notifications). Override ``get_slack_token()`` only if
    you need a different token.
    """

    def get_slack_token(self) -> str | None:
        """Resolve the Slack token from the standard forge notifications vault.

        Override this only if your project needs a different bot token.
        """
        try:
            token_path = vault.get_vault_content_path(
                "psap-forge-notifications", "topsail-bot.slack-token"
            )
        except RuntimeError:
            logger.warning("Vault not initialized, cannot resolve Slack token")
            return None

        if not token_path or not token_path.exists():
            logger.warning("Slack token not found in psap-forge-notifications vault")
            return None

        return token_path.read_text().strip()

    @abstractmethod
    def get_channel_id(self) -> str:
        """Return the Slack channel ID to post to."""

    @abstractmethod
    def format_message(self, context: NotificationContext) -> str:
        """Format the Slack message body."""

    def get_thread_anchor(self, context: NotificationContext) -> str:
        """Return the thread anchor text for grouping messages in a thread."""
        if context.pr_number:
            return f"Thread for {context.project_name} PR #{context.pr_number}"
        if context.job_type == "periodic":
            job_name = os.environ.get("JOB_NAME_SAFE", context.project_name)
            return f"Thread for {context.project_name} periodic `{job_name}`"
        return f"Thread for {context.project_name} run"

    def get_thread_channel_message(self, context: NotificationContext, anchor: str) -> str:
        """Return the channel message that creates the thread.

        The anchor is used for searching; this message is what gets posted.
        Override to add extra info (e.g. PR title) to the visible message.
        """
        BASE_ANCHOR = f"🧵 {anchor}"

        if not context.pr_number:
            return BASE_ANCHOR

        from projects.core.library import config

        title = config.project.get_config("ci_job.gh.pr.title", None, print=False)
        if not title:
            return BASE_ANCHOR

        return f"{BASE_ANCHOR}\n```{title}```"

    def should_notify(self, context: NotificationContext) -> bool:
        """Return True if notification should be sent. Default: always notify."""
        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _save_message_to_file(message: str, context: NotificationContext) -> None:
        """Persist the formatted Slack message to the artifact directory."""
        if not context.artifact_dir:
            return
        try:
            notification_file = context.artifact_dir / "SLACK-NOTIFICATION.txt"
            notification_file.write_text(message + "\n")
            logger.info("Wrote Slack notification to %s", notification_file)
        except Exception:
            logger.warning("Failed to save Slack notification to file", exc_info=True)

    # ------------------------------------------------------------------
    # Dispatch (not meant to be overridden in most cases)
    # ------------------------------------------------------------------

    def notify(self, context: NotificationContext, *, dry_run: bool = False) -> bool:
        """Execute the full notification flow.

        Returns True on success, False on failure.
        """
        if not self.should_notify(context):
            logger.info("Provider %s: should_notify returned False, skipping", type(self).__name__)
            return True

        token = self.get_slack_token()
        if not token:
            logger.warning("Provider %s: no Slack token available", type(self).__name__)
            return False

        channel_id = self.get_channel_id()
        message = self.format_message(context)
        self._save_message_to_file(message, context)

        if context.extra.get("_skip_notification"):
            logger.info("Provider %s: _skip_notification set, skipping", type(self).__name__)
            return True

        anchor = self.get_thread_anchor(context)

        client = slack_api.init_client(token)
        if not client:
            logger.error("Provider %s: failed to init Slack client", type(self).__name__)
            return False

        channel_msg_ts, _ = slack_api.search_channel_message(client, anchor, channel_id=channel_id)

        if not channel_msg_ts:
            channel_message = self.get_thread_channel_message(context, anchor)
            if dry_run:
                logger.info("Would post channel message: %s", channel_message)
            else:
                channel_msg_ts, ok = slack_api.send_message(
                    client, message=channel_message, channel_id=channel_id
                )
                if not ok:
                    return False

        if dry_run:
            logger.info("Would post thread message:\n%s", message)
            return True

        _, ok = slack_api.send_message(
            client, message=message, main_ts=channel_msg_ts, channel_id=channel_id
        )
        return ok
