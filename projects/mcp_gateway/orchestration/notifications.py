"""
Per-project Slack notification provider for MCP Gateway.

Sends structured performance summaries with KPI comparison against the
previous MLflow run. Reuses shared helpers from the core notification
framework.

Channel ID is read from the project's config.yaml at
``notifications.slack.channel_id``.
"""

from __future__ import annotations

import json
import logging
import os

from projects.core.library import config
from projects.core.notifications.helpers import (
    build_comparison_table,
    build_current_kpis_list,
    extract_mlflow_url,
    find_kpis_jsonl,
    get_label_value,
    get_test_artifacts_root,
    read_test_duration,
)
from projects.core.notifications.provider import NotificationContext, SlackNotificationProvider
from projects.core.notifications.send import get_ocpci_link

logger = logging.getLogger(__name__)

TARGET_KPIS = [
    ("mcp_gw_requests_per_second", "RPS", "req/s"),
    ("mcp_gw_p95_ms", "P95 latency", "ms"),
    ("mcp_gw_p99_ms", "P99 latency", "ms"),
    ("mcp_gw_failure_rate", "Failure rate", "%"),
]


class MCPGatewaySlackProvider(SlackNotificationProvider):
    """Slack notification provider for the mcp_gateway project."""

    def get_channel_id(self) -> str:
        channel_id = config.project.get_config(
            "notifications.slack.channel_id", None, print=False, warn=False
        )
        if not channel_id:
            raise ValueError("notifications.slack.channel_id must be set in config.yaml")
        return channel_id

    def format_message(self, context: NotificationContext) -> str:
        header = _format_header(context)
        metadata = _format_metadata(context)
        kpi_table = _format_kpi_table(context)
        links = _format_standard_links(context)
        failure_info = _format_failure_info(context)

        parts = [header, metadata, kpi_table, links, failure_info]
        return "\n\n".join(filter(None, parts))

    def get_thread_anchor(self, context: NotificationContext) -> str:
        if context.pr_number:
            return f"Thread for mcp_gateway PR #{context.pr_number}"

        job_name = os.environ.get("FJOB_NAME") or os.environ.get("JOB_NAME_SAFE", "")
        if job_name:
            return f"Thread for mcp_gateway `{job_name}`"

        return "Thread for mcp_gateway run"

    def should_notify(self, context: NotificationContext) -> bool:
        return True


# ---------------------------------------------------------------------------
# Message sections (MCP Gateway specific)
# ---------------------------------------------------------------------------


def _format_header(context: NotificationContext) -> str:
    status_icon = ":done-circle-check:" if context.finish_reason == "success" else ":no-red-circle:"
    duration = read_test_duration(context)
    duration_str = f" after {duration}" if duration else ""
    return f"{status_icon} *mcp_gateway test finished{duration_str}* {status_icon}"


def _format_metadata(context: NotificationContext) -> str:
    version = os.environ.get("MCP_GATEWAY_VERSION", "")
    preset = os.environ.get("MCP_GATEWAY_PRESET", "")

    test_root = get_test_artifacts_root(context)
    if not version:
        version = get_label_value(test_root, "mcp_gateway_version") or "unknown"
    if not preset:
        preset = get_label_value(test_root, "preset") or "default"

    return f"*Version*: `{version}`  |  *Preset*: `{preset}`"


def _format_kpi_table(context: NotificationContext) -> str:
    """Build comparison table: current KPIs vs previous MLflow run."""
    current_kpis = _load_current_kpis(context)
    if not current_kpis:
        return ""

    previous_kpis, previous_run_name = _load_previous_kpis_from_mlflow()

    if previous_kpis:
        return build_comparison_table(current_kpis, previous_kpis, previous_run_name, TARGET_KPIS)
    else:
        return build_current_kpis_list(current_kpis, TARGET_KPIS)


def _format_standard_links(context: NotificationContext) -> str:
    """Generate artifact links reusing the core notification link builder."""
    lines = []

    try:
        ci_link = get_ocpci_link("", is_dir=True)
        if ci_link and "no_known_ci_engine" not in ci_link:
            lines.append(f"\u2022 <{ci_link}|Test results>")
            log_link = get_ocpci_link("run.log", is_raw_file=True)
            lines.append(f"\u2022 <{log_link}|Execution logs>")
    except Exception as e:
        logger.debug("CI link generation unavailable: %s", e)

    mlflow_url = extract_mlflow_url(context)
    if mlflow_url:
        lines.append(f"\u2022 <{mlflow_url}|MLflow run>")

    return "\n".join(lines) if lines else ""


def _format_failure_info(context: NotificationContext) -> str:
    """Include structured failure details when test failed."""
    if context.finish_reason == "success":
        return ""
    if not context.artifact_dir:
        return ""

    try:
        from projects.core.notifications.send import _get_notification_content

        def get_link(name, path, **kwargs):
            return f"<{get_ocpci_link(path, **kwargs)}|{name}>"

        def get_bold(text):
            return f"*{text}*"

        return _get_notification_content(context.artifact_dir, get_link, get_bold)
    except Exception as e:
        logger.warning("Failed to extract failure info: %s", e)
        return ""


# ---------------------------------------------------------------------------
# KPI loading (MCP Gateway specific)
# ---------------------------------------------------------------------------


def _load_current_kpis(context: NotificationContext) -> dict[str, float]:
    """Read KPI values from kpis.jsonl in the artifact directory."""
    test_root = get_test_artifacts_root(context)
    if not test_root:
        return {}

    kpis_file = find_kpis_jsonl(test_root)
    if not kpis_file:
        return {}

    kpis: dict[str, float] = {}
    try:
        with open(kpis_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                kpi_id = record.get("kpi_id", "")
                if kpi_id in {k[0] for k in TARGET_KPIS}:
                    kpis[kpi_id] = record.get("value", 0)
    except Exception as e:
        logger.warning("Failed to read kpis.jsonl: %s", e)

    return kpis


def _load_previous_kpis_from_mlflow() -> tuple[dict[str, float], str]:
    """Query MLflow for the most recent previous run's metrics.

    Returns (metrics_dict, run_name) or ({}, "") if unavailable.
    """
    try:
        from projects.caliper.engine.file_export.mlflow_secrets import (
            load_mlflow_secrets_yaml,
            mlflow_connection_env,
        )
        from projects.core.library import vault as vault_lib

        vault_name = config.project.get_config(
            "caliper.export.backend.mlflow.secrets.vault.name", None, print=False, warn=False
        )
        vault_secret = config.project.get_config(
            "caliper.export.backend.mlflow.secrets.vault.mlflow_secret",
            None,
            print=False,
            warn=False,
        )
        experiment_name = config.project.get_config(
            "caliper.export.backend.mlflow.config.experiment", None, print=False, warn=False
        )

        if not all([vault_name, vault_secret, experiment_name]):
            logger.info("MLflow config incomplete, skipping comparison")
            return {}, ""

        secrets_path = vault_lib.get_vault_content_path(vault_name, vault_secret)
        if not secrets_path or not secrets_path.exists():
            logger.info("MLflow secrets not available, skipping comparison")
            return {}, ""

        secrets = load_mlflow_secrets_yaml(secrets_path)

        with mlflow_connection_env(secrets):
            import mlflow

            client = mlflow.tracking.MlflowClient()
            exp = client.get_experiment_by_name(experiment_name)
            if not exp:
                logger.info("MLflow experiment '%s' not found", experiment_name)
                return {}, ""

            runs = client.search_runs(
                experiment_ids=[exp.experiment_id],
                order_by=["start_time DESC"],
                max_results=2,
            )

            if len(runs) < 2:
                logger.info("No previous MLflow run found for comparison")
                return {}, ""

            previous_run = runs[1]
            run_name = getattr(previous_run.info, "run_name", "") or previous_run.info.run_id[:8]
            metrics = previous_run.data.metrics or {}

            return metrics, run_name

    except Exception as e:
        logger.warning("MLflow comparison unavailable: %s", e)
        return {}, ""
