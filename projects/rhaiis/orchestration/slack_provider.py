"""RHAIIS Slack notification provider for pipeline completion."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from projects.core.library import env
from projects.core.notifications.provider import NotificationContext, SlackNotificationProvider

logger = logging.getLogger(__name__)


def _format_notification_content(content: str) -> str:
    """Format notification content for Slack: quote lines and convert bold markers."""
    return content.replace("\n", "\n>").replace("**", "*")


def _collect_notification_files(
    artifact_dir: Path | None,
) -> tuple[str, list[str], list[str]]:
    """Collect notification files from all 000__ci_metadata/notifications/ dirs.

    Returns (test_description, regular_notifications, failure_reviews).
    """
    if not artifact_dir or not artifact_dir.is_dir():
        return "", [], []

    import re

    from projects.core.ci_entrypoint.prepare_ci import CI_METADATA_DIRNAME

    test_description = ""
    regular = []
    failure_reviews = []

    for nf in sorted(artifact_dir.rglob(f"{CI_METADATA_DIRNAME}/notifications/*.txt")):
        content = nf.read_text().strip()
        if not content:
            continue

        stem = nf.stem
        name = re.sub(r"^\d+__", "", stem)
        formatted = _format_notification_content(content[:1500])

        if name == "TEST_DESCRIPTION":
            test_description = f">{formatted}"
        elif name.startswith("FAILURE_REVIEW"):
            failure_reviews.append(f">{formatted}")
        else:
            regular.append(f"* [notif] {name}\n>{formatted}")

    return test_description, regular, failure_reviews


def _collect_failure_errors(artifact_dir: Path | None) -> str:
    """Collect error summaries from FAILURE files across pipeline steps."""
    if not artifact_dir or not artifact_dir.is_dir():
        return ""

    errors = []
    for failure_file in sorted(artifact_dir.glob("*/FAILURE.txt")):
        content = failure_file.read_text().strip()
        summary = content.split("---")[0].strip() if content else "unknown error"
        errors.append(f"{summary}")

    return "\n".join(errors)


class RhaiisSlackProvider(SlackNotificationProvider):
    """Sends RHAIIS pipeline completion notifications to Slack."""

    def get_channel_id(self) -> str:
        from projects.core.library import config

        return config.project.get_config("tests.rhaiis.slack_channel_id", "")

    def should_notify(self, context: NotificationContext) -> bool:
        from projects.core.library import config

        failed = context.finish_reason in ("failed", "export failed", "aborted")
        if failed:
            return True

        return config.project.get_config("tests.rhaiis.slack_notify_always", False)

    def reply_broadcast(self, context: NotificationContext) -> bool:
        return False

    def format_message(self, context: NotificationContext) -> str:
        from projects.core.library import config
        from projects.rhaiis.orchestration import runtime_config
        from projects.rhaiis.postprocess.regression import (
            _build_dashboard_url,
            _build_mlflow_run_url,
            _format_owner_line,
            _format_slack_user_line,
        )

        status = context.status or {}
        success = status.get("success", False)
        failed = context.finish_reason in ("failed", "export failed", "aborted")

        model_key = config.project.get_config("tests.rhaiis.model_key", "unknown")
        try:
            model_cfg = runtime_config.get_model(model_key)
            model_name = model_cfg.get("hf_model_id", model_key)
        except Exception:
            model_name = model_key

        accelerator = runtime_config.get_accelerator()
        engine = runtime_config.get_engine()
        slack_user = config.project.get_config("tests.rhaiis.slack_user", "")
        owner = config.project.get_config("ci_job.owner", "") or ""
        version = config.project.get_config("tests.rhaiis.version", "")
        cluster = config.project.get_config("rhaiis.cluster_tag", "")
        workload_keys = config.project.get_config("tests.rhaiis.workload_keys", [])
        job_id = os.environ.get("FJOB_NAME", "")

        engine_defaults = runtime_config.get_engine_args(engine)
        ea = runtime_config.merge_engine_args(engine_defaults, model_cfg, {}, engine)
        tp = ea.get("tensor-parallel-size") or ea.get("tp-size") or ea.get("tp_size") or ""
        dp = ea.get("data-parallel-size") or ea.get("dp-size") or ""

        parallelism_parts = []
        if tp:
            parallelism_parts.append(f"TP={tp}")
        if dp:
            parallelism_parts.append(f"DP={dp}")
        parallelism_line = (
            f"*Parallelism:* {', '.join(parallelism_parts)}\n" if parallelism_parts else ""
        )

        user_line = _format_slack_user_line(slack_user)
        owner_line = _format_owner_line(owner)
        engine_line = f"*Engine:* {engine}\n" if engine else ""
        version_line = f"*Version:* {version}\n" if version else ""
        cluster_line = f"*Cluster:* {cluster}\n" if cluster else ""
        workloads_line = f"*Workloads:* {', '.join(workload_keys)}\n" if workload_keys else ""

        dashboard_url = _build_dashboard_url(
            model=model_name,
            accelerator=accelerator,
            current_version=version,
            profiles=workload_keys or None,
            tp=str(tp) if tp else "",
        )
        dashboard_line = f"*Dashboard:* <{dashboard_url}|View Dashboard>\n"

        mlflow_url = _build_mlflow_run_url()
        mlflow_line = f"*MLflow:* <{mlflow_url}|View Run>\n" if mlflow_url else ""

        if failed or not success:
            emoji = ":x:"
            title = "RHAIIS Pipeline Failed"
            error_text = (
                "```\n"
                + (_collect_failure_errors(env.BASE_ARTIFACT_DIR.parent) or context.finish_reason)
                + "\n```"
            )
        else:
            emoji = ":white_check_mark:"
            title = "RHAIIS Pipeline Succeeded"
            error_text = ""

        test_desc, regular_notifs, failure_reviews = _collect_notification_files(
            env.BASE_ARTIFACT_DIR.parent
        )

        parts = [
            f"{emoji} *{title}*\n",
        ]

        if test_desc:
            parts.append(test_desc)
            parts.append("")

        for notif in regular_notifs:
            parts.append("")
            parts.append(notif)
            parts.append("")

        parts.append(
            f"{user_line}"
            f"{owner_line}"
            f"*Job:* `{job_id}`\n"
            f"*Model:* {model_name}\n"
            f"*Accelerator:* {accelerator}\n"
            f"{parallelism_line}"
            f"{engine_line}"
            f"{version_line}"
            f"{cluster_line}"
            f"{workloads_line}"
            f"{dashboard_line}"
            f"{mlflow_line}"
        )

        if failure_reviews or error_text:
            parts.append("*Error:*")

        for review in failure_reviews:
            parts.append("")
            parts.append(review)
            parts.append("")

        if error_text:
            parts.append(error_text)

        return "\n".join(parts).rstrip("\n")
