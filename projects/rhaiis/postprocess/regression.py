"""RHAIIS-specific profile/metric definitions and Slack notification for regression analysis."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

PROFILE_MAP = {
    (1000, 1000): "profile1",
    (512, 2048): "profile2",
    (2048, 128): "profile3",
    (8000, 1000): "profile4",
}

METRICS = {
    "total_tok/sec": {"label": "Total Throughput", "higher_is_better": True, "threshold": 5},
    "output_tok/sec": {"label": "Output Throughput", "higher_is_better": True, "threshold": 5},
    "ttft_p95": {"label": "TTFT P95", "higher_is_better": False, "threshold": 10},
    "itl_p95": {"label": "ITL P95", "higher_is_better": False, "threshold": 5},
    "request_latency_median": {"label": "Median E2E Latency", "higher_is_better": False, "threshold": 5},
}

DASHBOARD_BASE_URL = "https://staging-aidash.apps.ocp4.intlab.redhat.com/"

PROFILE_DISPLAY_NAMES = {
    "profile1": "Profile A: Balanced (1k/1k)",
    "profile2": "Profile B: Variable Workload (512/2k)",
    "profile3": "Profile C: Large Prompt (2k/128)",
    "profile4": "Profile D: Prefill Heavy (8k/1k)",
}


def _build_dashboard_url(
    *,
    model: str = "",
    accelerator: str = "",
    current_version: str = "",
    compare_version: str = "",
    profiles: list[str] | None = None,
    tp: str = "",
) -> str:
    """Build a RHAIIS dashboard URL with filters pre-selected."""
    from urllib.parse import quote, urlencode

    params: dict[str, str] = {"view": "RHAIIS Dashboard"}

    if accelerator:
        params["accelerators"] = accelerator
    if model:
        params["models"] = model

    versions = ",".join(v for v in [current_version, compare_version] if v)
    if versions:
        params["versions"] = versions

    if profiles:
        display_names = [PROFILE_DISPLAY_NAMES.get(p, p) for p in profiles]
        params["profile"] = ",".join(display_names)

    if tp:
        try:
            params["tp_sizes"] = f"{float(tp):.1f}"
        except (ValueError, TypeError):
            params["tp_sizes"] = tp

    params["section"] = "performance_plots"

    return f"{DASHBOARD_BASE_URL}?{urlencode(params, quote_via=quote)}"


def send_regression_notification(
    analysis_result: dict,
    *,
    model: str = "",
    accelerator: str = "",
    job_id: str = "",
    slack_user: str = "",
    webhook_vault: str | None = None,
    notification_vault: str | None = None,
    dry_run: bool = False,
    report_url: str = "",
    tp: str = "",
    dp: str = "",
) -> bool:
    """Send regression/improvement Slack notification from analysis results.

    Uses a webhook URL (from *webhook_vault*) when available, falling
    back to the Bot-Token path (from *notification_vault*).

    Args:
        analysis_result: Output from run_regression_analysis()
        model: Model name for display
        accelerator: Accelerator name for display
        job_id: Job identifier
        slack_user: Slack user ID (e.g. U01ABC123) to @-mention, or display name
        webhook_vault: Vault containing slack-webhook-url file
        notification_vault: Fallback vault for Bot-Token Slack credentials
        dry_run: Log only, don't send
        report_url: Optional presigned URL to an agent analysis report
        tp: Tensor parallelism size for display
        dp: Data parallelism size for display

    Returns:
        True if notification sent successfully
    """
    from projects.core.notifications.send import (
        _get_webhook_url,
        _send_via_webhook,
        send_notification,
    )

    status = analysis_result.get("status")
    if status == "skipped":
        reason = analysis_result.get("reason", "unknown")
        logger.info("Regression analysis skipped: %s", reason)
        return True

    if status != "completed":
        return True

    regressions = analysis_result.get("regressions", [])
    improvements = analysis_result.get("improvements", [])

    if not regressions and not improvements:
        logger.info("No regressions or improvements to report")
        return True

    current_version = analysis_result.get("current_version", "")
    compare_version = analysis_result.get("compare_version", "")

    if regressions and improvements:
        icon = ":warning:"
        headline = "Performance regressions and improvements detected"
    elif regressions:
        icon = ":warning:"
        headline = "Performance regressions detected"
    else:
        icon = ":large_green_circle:"
        headline = "Performance improvements detected"

    detail_lines = []
    all_results = analysis_result.get("all_results", [])
    profiles = sorted({r["profile"] for r in all_results if r.get("is_regression") or r.get("is_improvement")})

    for profile in profiles:
        profile_results = [r for r in all_results if r["profile"] == profile and (r.get("is_regression") or r.get("is_improvement"))]
        detail_lines.append(f"\n*{profile}:*")
        for r in profile_results:
            if r.get("is_regression"):
                direction = "dropped" if r["pct_diff"] < 0 else "increased"
                detail_lines.append(
                    f"  :red_circle: *{r['metric']}*: {direction} {abs(r['pct_diff']):.1f}% "
                    f"({r['baseline']:.2f} \u2192 {r['current']:.2f})"
                )
            else:
                detail_lines.append(
                    f"  :large_green_circle: *{r['metric']}*: improved {abs(r['pct_diff']):.1f}% "
                    f"({r['baseline']:.2f} \u2192 {r['current']:.2f})"
                )

    details = "\n".join(detail_lines)

    if slack_user and re.match(r"^[UW][A-Z0-9]+$", slack_user):
        user_line = f"*Triggered by:* <@{slack_user}>\n"
    elif slack_user:
        user_line = f"*Triggered by:* {slack_user}\n"
    else:
        user_line = ""

    report_line = f"*Agent Analysis:* <{report_url}|View Report>\n" if report_url else ""

    parallelism_line = ""
    parallelism_parts = []
    if tp:
        parallelism_parts.append(f"TP={tp}")
    if dp:
        parallelism_parts.append(f"DP={dp}")
    if parallelism_parts:
        parallelism_line = f"*Parallelism:* {', '.join(parallelism_parts)}\n"

    dashboard_url = _build_dashboard_url(
        model=model,
        accelerator=accelerator,
        current_version=current_version,
        compare_version=compare_version,
        profiles=profiles if profiles else None,
        tp=tp,
    )
    dashboard_line = f"*Dashboard:* <{dashboard_url}|View Dashboard>\n"

    message = (
        f"{icon} *{headline}*\n"
        f"{user_line}"
        f"*Job:* `{job_id}`\n"
        f"*Model:* {model}\n"
        f"*Accelerator:* {accelerator}\n"
        f"{parallelism_line}"
        f"*Versions:* {current_version} vs {compare_version} (baseline)\n"
        f"{report_line}"
        f"{dashboard_line}"
        f"*Changes:*\n{details}"
    )

    if dry_run:
        logger.info("DRY RUN regression notification:\n%s", message)
        return True

    webhook_url = None
    if webhook_vault:
        webhook_url = _get_webhook_url(webhook_vault)

    if webhook_url:
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": message}}]
        return _send_via_webhook(webhook_url, blocks)

    return send_notification(
        message,
        github=False,
        slack=True,
        dry_run=dry_run,
        notification_vault=notification_vault,
    )
