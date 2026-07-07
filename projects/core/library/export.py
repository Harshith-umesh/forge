"""
Shared Caliper "artifacts export" CLI for FORGE project orchestration.

Registers a :mod:`click` subcommand that reads ``caliper`` from project config and runs
:func:`projects.caliper.orchestration.export.run_from_orchestration_config`.
"""

from __future__ import annotations

import logging
import os
from enum import StrEnum
from pathlib import Path
from typing import Any

import click
import yaml

from projects.caliper.orchestration.export import run_from_orchestration_config
from projects.core.ci_entrypoint.prepare_ci import CI_METADATA_DIRNAME
from projects.core.library import ci as ci_lib
from projects.core.library import config, env, run


class StepStatus(StrEnum):
    """Status of a step execution."""

    SUCCESS = "success"
    FAILURE = "failure"
    ONGOING = "ongoing"
    UNKNOWN = "unknown"


logger = logging.getLogger(__name__)


class FinishReason(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    OTHER = "other"


def _update_fjob_export_status(status: dict):
    """Update FournosJob status with export artifacts status."""
    if os.environ.get("FOURNOS_CI") != "true":
        return

    # Unset KUBECONFIG to use the pod SA access
    original_kubeconfig = os.environ.get("KUBECONFIG")
    if "KUBECONFIG" in os.environ:
        del os.environ["KUBECONFIG"]

    try:
        import json

        fjob_name = os.environ["FJOB_NAME"]
        namespace = os.environ["FOURNOS_WORKLOAD_NAMESPACE"]

        # Get current fjob status
        get_cmd = f"oc get fjob/{fjob_name} -n {namespace} -ojson"
        result = run.run(get_cmd, capture_stdout=True, check=False)

        if result.returncode != 0:
            logger.warning(f"Failed to get fjob/{fjob_name}")
            return

        fjob_data = json.loads(result.stdout)

        # Initialize status.engine.status if it doesn't exist
        if "status" not in fjob_data:
            fjob_data["status"] = {}
        if "engineStatus" not in fjob_data["status"]:
            fjob_data["status"]["engineStatus"] = {}
        if "forge" not in fjob_data["status"]["engineStatus"]:
            fjob_data["status"]["engineStatus"]["forge"] = {}
        if "status" not in fjob_data["status"]["engineStatus"]["forge"]:
            fjob_data["status"]["engineStatus"]["forge"]["status"] = {}

        # Update with export-artifacts status
        fjob_data["status"]["engineStatus"]["forge"]["exportArtifacts"] = status

        # Patch the fjob
        patch_data = {"status": fjob_data["status"]}
        patch_cmd = f"oc patch fjob/{fjob_name} -n {namespace} --type=merge --subresource=status -p '{json.dumps(patch_data)}'"

        patch_result = run.run(patch_cmd, check=False)
        if patch_result.returncode == 0:
            logger.info(f"Updated fjob/{fjob_name} status with export artifacts status")
        else:
            logger.warning(f"Failed to update fjob status: {patch_cmd}")

    except Exception as e:
        logger.warning(f"Failed to update fjob status: {e}")
    finally:
        # Restore KUBECONFIG if it was set
        if original_kubeconfig is not None:
            os.environ["KUBECONFIG"] = original_kubeconfig


def send_notification(status: dict[str, Any], notification_provider=None) -> None:
    """Send job completion notifications based on caliper export status.

    Args:
        status: Caliper export status object containing backend results and metadata
        notification_provider: Optional per-project SlackNotificationProvider instance
    """
    # Extract notification parameters from status object
    project = _extract_project_from_status(status)
    operation = _extract_operation_from_status(status)
    finish_reason = _extract_finish_reason_from_status(status)
    duration_str = _extract_duration_from_status(status)

    # Apply minimal filtering logic
    if _should_skip_notification(project, operation, finish_reason):
        logger.info(f"Skipping notification for {project} {operation}")
        return

    # Send actual notifications
    logger.info(f"Sending notification: {project} {operation} {finish_reason}{duration_str}")

    # Build enhanced notification with fournos job info and artifact links
    notification_status = _build_enhanced_notification(project, finish_reason, duration_str, status)

    # Write notification to file for GitHub pickup
    try:
        if env.ARTIFACT_DIR:
            notification_file = Path(env.ARTIFACT_DIR) / "NOTIFICATION.html"
            with open(notification_file, "w", encoding="utf-8") as f:
                f.write(notification_status)
            logger.info("Wrote export notification file")
        else:
            logger.warning("ARTIFACT_DIR not available, skipping notification file")
    except Exception as e:
        logger.warning(f"Failed to write notification file: {e}")

    # Actually send notification through GitHub API
    try:
        from projects.core.notifications.send import send_notification as send_github_notification

        success = send_github_notification(
            message=notification_status, github=True, slack=False, dry_run=False
        )
        if success:
            logger.info("Successfully sent GitHub notification")
        else:
            logger.warning("GitHub notification sending failed")
    except Exception as e:
        logger.warning(f"Failed to send GitHub notification: {e}")

    # Per-project Slack notification via provider
    if notification_provider:
        try:
            from projects.core.notifications.provider import NotificationContext

            artifact_dir = Path(env.ARTIFACT_DIR) if env.ARTIFACT_DIR else None
            context = NotificationContext(
                status=status,
                finish_reason=str(finish_reason),
                project_name=project or "unknown",
                pr_number=os.environ.get("PULL_NUMBER"),
                job_type=os.environ.get("JOB_TYPE"),
                artifact_dir=artifact_dir,
            )
            ok = notification_provider.notify(context)
            if ok:
                logger.info("Successfully sent per-project Slack notification")
            else:
                logger.warning("Per-project Slack notification failed")
        except Exception as e:
            logger.warning(f"Failed to send per-project Slack notification: {e}")


def _get_project_and_args(project: str) -> tuple[str, str]:
    """Extract project name and args from fournos job or config."""
    fjob_project = project
    fjob_args_str = ""

    try:
        metadata_dir = ci_lib.get_ci_metadata_dir()
        fournos_fjob_path = metadata_dir / "fournos_fjob.yaml"
        if not fournos_fjob_path.exists():
            return fjob_project, fjob_args_str

        with open(fournos_fjob_path, encoding="utf-8") as f:
            fjob_data = yaml.safe_load(f)

        display_name = fjob_data.get("spec", {}).get("displayName", "")
        if not display_name:
            return fjob_project, fjob_args_str

        parts = display_name.split()
        if not parts:
            return fjob_project, fjob_args_str

        fjob_project = parts[0]
        fjob_args_str = " ".join(parts[1:]) if len(parts) > 1 else ""
    except Exception as e:
        logger.warning(f"Failed to read fournos job for project/args: {e}")

    if fjob_args_str:
        return fjob_project, fjob_args_str

    try:
        from projects.core.library import config

        job_args = config.project.get_config("ci_job.args")
        fjob_args_str = " ".join(job_args) if job_args else ""
    except Exception as e:
        logger.warning(f"Failed to get args from config: {e}")

    return fjob_project, fjob_args_str


def _get_execution_engine_config() -> str | None:
    """Read and format execution engine configuration."""
    try:
        metadata_dir = ci_lib.get_ci_metadata_dir()
        fournos_fjob_path = metadata_dir / "fournos_fjob.yaml"
        if not fournos_fjob_path.exists():
            return None

        with open(fournos_fjob_path, encoding="utf-8") as f:
            fjob_data = yaml.safe_load(f)

        execution_engine = fjob_data.get("spec", {}).get("executionEngine", {})
        if not execution_engine:
            return None

        engine_yaml = yaml.dump(execution_engine, default_flow_style=False, sort_keys=True)
        return f"```yaml\n{engine_yaml.strip()}\n```"
    except Exception as e:
        logger.warning(f"Failed to read fournos job config: {e}")
        return None


def _extract_artifact_links(status: dict[str, Any]) -> tuple[list[str], str | None]:
    """Extract artifact links and MLflow URL from status."""
    artifact_links = []
    mlflow_run_url = None

    caliper_export = status.get("caliper_artifacts_export", {})
    backends = caliper_export.get("backends", {})

    for backend_name, backend_result in backends.items():
        if not isinstance(backend_result, dict):
            continue

        if backend_result.get("experiment_url"):
            artifact_links.append(
                f"[{backend_name} Experiment]({backend_result['experiment_url']})"
            )

        if backend_result.get("run_url"):
            mlflow_run_url = backend_result["run_url"]
            artifact_links.append(f"[{backend_name} Results]({mlflow_run_url})")
        elif backend_result.get("artifact_url"):
            artifact_links.append(f"[{backend_name} Artifacts]({backend_result['artifact_url']})")
        elif backend_result.get("dashboard_url"):
            artifact_links.append(f"[{backend_name} Dashboard]({backend_result['dashboard_url']})")

    if status.get("artifact_url"):
        artifact_links.append(f"[Artifacts]({status['artifact_url']})")

    return artifact_links, mlflow_run_url


def _create_mlflow_url(mlflow_run_url: str, step_dir_name: str) -> str | None:
    """Create MLflow URL for step logs."""
    if "/artifacts" not in mlflow_run_url:
        logger.warning(f"Unexpected MLflow URL format: {mlflow_run_url}")
        return None

    if "#" in mlflow_run_url:
        base_domain, hash_fragment = mlflow_run_url.split("#", 1)
        if "/artifacts" not in hash_fragment:
            raise ValueError("Artifacts not found in hash fragment")

        hash_base, params = hash_fragment.split("/artifacts", 1)
        workspace_param = params if "?workspace=" in params else ""
        return f"{base_domain}#{hash_base}/artifacts/{step_dir_name}/run.log{workspace_param}"
    else:
        base_url, params = mlflow_run_url.split("/artifacts", 1)
        workspace_param = params if "?workspace=" in params else ""
        return f"{base_url}/artifacts/{step_dir_name}/run.log{workspace_param}"


def _create_mlflow_step_url(mlflow_run_url: str, step_dir_name: str) -> str | None:
    """Create MLflow URL for step directory (for file access)."""
    if "/artifacts" not in mlflow_run_url:
        logger.warning(f"Unexpected MLflow URL format: {mlflow_run_url}")
        return None

    if "#" in mlflow_run_url:
        base_domain, hash_fragment = mlflow_run_url.split("#", 1)
        if "/artifacts" not in hash_fragment:
            raise ValueError("Artifacts not found in hash fragment")

        hash_base, artifacts_part = hash_fragment.split("/artifacts", 1)
        # Extract workspace parameter if present, ignoring existing path
        workspace_param = ""
        if "?workspace=" in artifacts_part:
            workspace_param = artifacts_part[artifacts_part.find("?") :]
        return f"{base_domain}#{hash_base}/artifacts/{step_dir_name}{workspace_param}"
    else:
        base_url, artifacts_part = mlflow_run_url.split("/artifacts", 1)
        # Extract workspace parameter if present, ignoring existing path
        workspace_param = ""
        if "?workspace=" in artifacts_part:
            workspace_param = artifacts_part[artifacts_part.find("?") :]
        return f"{base_url}/artifacts/{step_dir_name}{workspace_param}"


def _create_mlflow_file_url_for_step(
    mlflow_run_url: str, step_dir_name: str, file_path: str
) -> str:
    """Create MLflow URL for a specific file within a step directory.

    Args:
        mlflow_run_url: Base MLflow run URL
        step_dir_name: Name of the step directory
        file_path: Relative path to file from step directory

    Returns:
        Full MLflow URL to the file

    Raises:
        ValueError: If URL format is unexpected
    """
    if "/artifacts" not in mlflow_run_url:
        raise ValueError(f"Unexpected MLflow URL format: {mlflow_run_url}")

    # Clean file path
    file_clean = file_path.lstrip("/")

    if "#" in mlflow_run_url:
        base_domain, hash_fragment = mlflow_run_url.split("#", 1)
        if "/artifacts" not in hash_fragment:
            raise ValueError("Artifacts not found in hash fragment")

        hash_base, artifacts_part = hash_fragment.split("/artifacts", 1)
        # Extract workspace parameter if present, ignoring existing path
        workspace_param = ""
        if "?workspace=" in artifacts_part:
            workspace_param = artifacts_part[artifacts_part.find("?") :]
        return f"{base_domain}#{hash_base}/artifacts/{step_dir_name}/{file_clean}{workspace_param}"
    else:
        base_url, artifacts_part = mlflow_run_url.split("/artifacts", 1)
        # Extract workspace parameter if present, ignoring existing path
        workspace_param = ""
        if "?workspace=" in artifacts_part:
            workspace_param = artifacts_part[artifacts_part.find("?") :]
        return f"{base_url}/artifacts/{step_dir_name}/{file_clean}{workspace_param}"


def _read_step_duration(step_dir: Path) -> str:
    """Read step duration from timing file."""
    timing_file = step_dir / CI_METADATA_DIRNAME / "test_duration.yaml"
    if not timing_file.exists():
        return ""

    try:
        with open(timing_file, encoding="utf-8") as f:
            timing_data = yaml.safe_load(f)

        formatted_duration = timing_data.get("duration", {}).get("formatted")
        return formatted_duration or ""
    except Exception as timing_error:
        logger.warning(f"Failed to read timing file {timing_file}: {timing_error}")
        return ""


def _process_caliper_postprocess_status(
    step_dir: Path, step_log_links: list[str], mlflow_run_url: str | None = None
) -> None:
    """Search for and process caliper_postprocess_status.yaml files in step directory."""
    status_files = list(step_dir.glob("**/caliper_postprocess_status.yaml"))

    for status_file in status_files:
        try:
            with open(status_file, encoding="utf-8") as f:
                status_data = yaml.safe_load(f)

            if not status_data:
                continue

            # Import notification functions from caliper
            from projects.caliper.orchestration.notification import (
                format_postprocess_status_notification,
                parse_postprocess_result,
            )

            # Parse status data into structured object
            result = parse_postprocess_result(status_data)
            if not result:
                continue

            # Create file link generator function
            get_file_link = None
            if mlflow_run_url:

                def get_file_link(file_path: str) -> str:
                    return _create_mlflow_file_url_for_step(
                        mlflow_run_url, step_dir.name, file_path
                    )

            # Generate notification text from the structured result
            notification_text = format_postprocess_status_notification(result, get_file_link)
            if notification_text:
                step_log_links.append(notification_text)

        except Exception as e:
            logger.warning(f"Failed to process caliper postprocess status file {status_file}: {e}")


def _process_notification_files(step_dir: Path, step_log_links: list[str]) -> None:
    """Process notification files from step directory."""
    notifications_dir = step_dir / CI_METADATA_DIRNAME / "notifications"
    if not (notifications_dir.exists() and notifications_dir.is_dir()):
        return

    import re

    for notification_file in sorted(notifications_dir.glob("*.txt")):
        try:
            with open(notification_file, encoding="utf-8") as f:
                content = f.read().strip()

            if not content:
                continue

            subtitle = notification_file.stem.replace("__", " ").replace("_", " ").title()
            subtitle = re.sub(r"^\d+\s+", "", subtitle)
            step_log_links.append(f"##### {subtitle}")

            for line in content.splitlines():
                step_log_links.append(f"> {line}")

        except Exception as file_error:
            logger.warning(f"Failed to read notification file {notification_file}: {file_error}")
            continue


def _process_step_logs(mlflow_run_url: str) -> list[str]:
    """Process step logs from parent directory."""
    if not mlflow_run_url:
        logging.warning("mlflow_run_url not set, skipping step log browsing")
        return []

    step_log_links = []
    parent_dir = Path(env.BASE_ARTIFACT_DIR).parent
    current_step_name = Path(env.BASE_ARTIFACT_DIR).name

    for step_dir in sorted(parent_dir.iterdir()):
        if not step_dir.is_dir():
            continue
        if step_dir.name.startswith("."):
            continue

        run_log = step_dir / "run.log"
        if not run_log.exists():
            continue

        try:
            mlflow_log_url = _create_mlflow_url(mlflow_run_url, step_dir.name)
            if not mlflow_log_url:
                continue

            step_name = step_dir.name.replace("__", " ").replace("_", " ").title()
            duration_str = _read_step_duration(step_dir)
            exit_status_emoji, exit_status = _read_step_exit_status(step_dir, current_step_name)

            if duration_str:
                step_log_links.append(
                    f"#### {exit_status_emoji} [{step_name}]({mlflow_log_url}) `{duration_str}`"
                )
            else:
                step_log_links.append(f"#### {exit_status_emoji} [{step_name}]({mlflow_log_url})")

            _process_notification_files(step_dir, step_log_links)

        except Exception as e:
            logger.warning(f"Failed to create MLflow link for {run_log}: {e}")
            continue

    return step_log_links


def _process_postprocess_status(mlflow_run_url: str | None = None) -> list[str]:
    """Process post-processing status from all step directories."""
    if not mlflow_run_url:
        return []

    postprocess_links = []
    parent_dir = Path(env.BASE_ARTIFACT_DIR).parent

    for step_dir in sorted(parent_dir.iterdir()):
        if not step_dir.is_dir():
            continue
        if step_dir.name.startswith("."):
            continue

        try:
            _process_caliper_postprocess_status(step_dir, postprocess_links, mlflow_run_url)
        except Exception as e:
            logger.warning(f"Failed to process postprocess status for {step_dir.name}: {e}")

    return postprocess_links


def _read_step_exit_status(
    step_dir: Path, current_step_name: str | None = None
) -> tuple[str, StepStatus]:
    """Read exit status from step directory and return emoji and status enum."""
    try:
        exit_status_file = step_dir / CI_METADATA_DIRNAME / "exit_status.yaml"
        if not exit_status_file.exists():
            # Check if this is the current ongoing step
            if current_step_name and step_dir.name == current_step_name:
                return "🔄", StepStatus.ONGOING  # Ongoing step
            return "❓", StepStatus.UNKNOWN  # Unknown status if file doesn't exist

        with open(exit_status_file, encoding="utf-8") as f:
            exit_data = yaml.safe_load(f)

        return_code = exit_data.get("return_code")
        if return_code is None or return_code == 0:
            return "✅", StepStatus.SUCCESS
        else:
            return "❌", StepStatus.FAILURE
    except Exception as e:
        logger.warning(f"Failed to read exit status from {step_dir}: {e}")
        # Check if this is the current ongoing step even on error
        if current_step_name and step_dir.name == current_step_name:
            return "🔄", StepStatus.ONGOING  # Ongoing step
        return "❓", StepStatus.UNKNOWN  # Unknown status on error


def _get_overall_status_from_steps() -> str:
    """Check all step exit statuses and return overall status emoji."""
    try:
        parent_dir = Path(env.BASE_ARTIFACT_DIR).parent
        current_step_name = Path(env.BASE_ARTIFACT_DIR).name

        step_statuses = []

        for step_dir in sorted(parent_dir.iterdir()):
            if not step_dir.is_dir():
                continue
            if step_dir.name.startswith("."):
                continue

            # Only check directories that have run.log (actual steps)
            run_log = step_dir / "run.log"
            if not run_log.exists():
                continue

            emoji, status = _read_step_exit_status(step_dir, current_step_name)
            step_statuses.append(status)

        # Priority: failure > ongoing > unknown > success
        if StepStatus.FAILURE in step_statuses:
            return "🔴"  # Any failure = red
        elif StepStatus.ONGOING in step_statuses:
            return "🟡"  # Ongoing = yellow
        elif StepStatus.UNKNOWN in step_statuses:
            return "🟠"  # Unknown = orange
        else:
            return "🟢"  # All successful = green

    except Exception as e:
        logger.warning(f"Failed to check step statuses: {e}")
        return "🔴"  # Error checking = red


def _build_enhanced_notification(
    project: str, finish_reason: FinishReason, duration_str: str, status: dict[str, Any]
) -> str:
    """Build enhanced notification with fournos job config and artifact links."""
    fjob_project, fjob_args_str = _get_project_and_args(project)

    # Check all step statuses for overall status emoji (takes priority over finish_reason)
    status_emoji = _get_overall_status_from_steps()
    base_status = f"<strong>{status_emoji} Execution of `{fjob_project}` {fjob_args_str} {status_emoji}</strong>"
    notification_parts = [base_status, "---"]

    execution_engine_config = _get_execution_engine_config()
    if execution_engine_config:
        notification_parts.append("**Execution Engine Configuration**")
        notification_parts.append(execution_engine_config)

    try:
        artifact_links, mlflow_run_url = _extract_artifact_links(status)
        step_log_links = _process_step_logs(mlflow_run_url)
        postprocess_status_links = _process_postprocess_status(mlflow_run_url)

        if artifact_links:
            notification_parts.append("")
            notification_parts.append("**Artifact Links**")
            notification_parts.extend([f"* {link}" for link in artifact_links])
        else:
            notification_parts.append("**Artifact Links:** No direct links available")

        if step_log_links:
            notification_parts.append("")
            notification_parts.append("**Test Logs**")
            notification_parts.extend(step_log_links)

        if postprocess_status_links:
            notification_parts.append("")
            notification_parts.extend(postprocess_status_links)

    except Exception as e:
        logger.warning(f"Failed to extract artifact links: {e}")
        notification_parts.append("**Artifact Links:** Error extracting links")

    return "\n".join(notification_parts)


def _extract_project_from_status(status: dict[str, Any]) -> str:
    """Extract project name from status object or environment."""
    # Try to get project from environment variables
    project = os.environ.get("PROJECT_NAME")
    if project:
        return project

    # Fallback to JOB_NAME parsing (common in CI environments)
    job_name = os.environ.get("JOB_NAME", "")
    if job_name and "-" in job_name:
        # Extract project from job name pattern like "project-operation-variant"
        return job_name.split("-")[0]

    return "unknown"


def _extract_operation_from_status(status: dict[str, Any]) -> str:
    """Extract operation name from status object."""
    return "export-artifacts"


def _extract_finish_reason_from_status(status: dict[str, Any]) -> FinishReason:
    """Extract finish reason from status object."""
    # Check if any backend failed in the status
    if not status:
        return FinishReason.ERROR

    # Look for backend results
    backends = status.get("backends", {})
    for backend_name, backend_result in backends.items():
        # Check both explicit success flag and status field
        if backend_result.get("success") is False or backend_result.get("status") not in (
            None,
            "success",
        ):
            logger.info(f"Backend {backend_name} failed, marking as error")
            return FinishReason.ERROR

    return FinishReason.SUCCESS


def _extract_duration_from_status(status: dict[str, Any]) -> str:
    """Extract duration from status object."""
    # Look for duration in status
    duration = status.get("duration")
    if duration:
        return f" after {duration}"
    return ""


def _should_skip_notification(project: str, operation: str, finish_reason: FinishReason) -> bool:
    """Apply minimal filtering logic to determine if notification should be skipped."""
    # Minimal filtering - no special cases for now
    return False


def run_caliper_orchestration_export(*, artifact_directory: Path | None):
    """Set optional ``caliper.export.from`` and run orchestration export."""

    if artifact_directory is None and "ARTIFACT_BASE_DIR" in os.environ:
        artifact_directory = os.environ["ARTIFACT_BASE_DIR"]

    if artifact_directory is not None:
        config.project.set_config("caliper.export.from", str(artifact_directory))

    # Use FJOB_NAME as fallback for mlflow run_name if not configured
    run_name = config.project.get_config(
        "caliper.export.backend.mlflow.config.run_name", None, print=False, warn=False
    )
    if run_name is None and "FJOB_NAME" in os.environ:
        config.project.set_config(
            "caliper.export.backend.mlflow.config.run_name", os.environ["FJOB_NAME"], print=False
        )

    caliper_cfg = config.project.get_config("caliper", print=False)

    return run_from_orchestration_config(caliper_cfg)


@click.command("export-artifacts")
@click.option(
    "--artifact-directory",
    "artifact_directory",
    type=click.Path(path_type=Path, exists=False, file_okay=True, dir_okay=True),
    default=None,
    help="If set, overrides caliper.export.from (artifact root directory).",
)
@click.pass_context
@ci_lib.safe_ci_entrypoint
def caliper_export_entrypoint(_ctx, artifact_directory: Path | None):
    """Export the file artifacts."""

    notification_provider = getattr(getattr(_ctx, "obj", None), "notification_provider", None)

    status = None
    try:
        status = run_caliper_orchestration_export(artifact_directory=artifact_directory)
        logger.info("Export status:\n" + yaml.dump(status, indent=4))

        # Update fjob status with export results
        _update_fjob_export_status(status)

    except Exception as e:
        logger.error(f"Export failed: {e}")
        # Create failure status for notification
        status = {"success": False, "error": str(e), "backends": {}}
        raise  # Re-raise to maintain error behavior

    finally:
        # Send completion notifications regardless of success/failure
        if status:
            try:
                send_notification(status, notification_provider=notification_provider)
            except Exception as e:
                logger.warning(f"Failed to send notifications: {e}")

        # Re-upload run.log with complete content (includes notification output)
        _try_update_run_log(status)

    return 0


def _try_update_run_log(status: dict[str, Any] | None) -> None:
    """Best-effort re-upload of run.log to MLflow after all post-export work is done."""
    if not status:
        return

    try:
        caliper_export = status.get("caliper_artifacts_export", {})
        backends = caliper_export.get("backends", {})
        mlflow_meta = backends.get("mlflow")
        if not isinstance(mlflow_meta, dict):
            return

        run_id = mlflow_meta.get("run_id")
        if not run_id:
            return

        artifact_from = config.project.get_config(
            "caliper.export.from", None, print=False, warn=False
        )
        if not artifact_from:
            return

        artifact_dir = os.environ.get("ARTIFACT_DIR", "")
        if not artifact_dir:
            return

        log_file = Path(artifact_dir) / "run.log"
        if not log_file.is_file():
            return

        artifact_root = Path(artifact_from)
        artifact_path = str(Path(artifact_dir).relative_to(artifact_root))

        tracking_uri = mlflow_meta.get("tracking_uri")

        vault_name = config.project.get_config(
            "caliper.export.backend.mlflow.secrets.vault.name", None, print=False, warn=False
        )
        vault_key = config.project.get_config(
            "caliper.export.backend.mlflow.secrets.vault.mlflow_secret",
            None,
            print=False,
            warn=False,
        )

        connection = None
        if vault_name and vault_key:
            from projects.caliper.engine.file_export.mlflow_secrets import (
                load_mlflow_secrets_yaml,
            )
            from projects.core.library import vault as vault_lib

            secrets_path = vault_lib.get_vault_content_path(vault_name, vault_key)
            if secrets_path and secrets_path.exists():
                connection = load_mlflow_secrets_yaml(secrets_path)

        from projects.caliper.engine.file_export.mlflow_backend import update_run_log_artifact

        update_run_log_artifact(
            run_id=run_id,
            log_file=log_file,
            tracking_uri=tracking_uri,
            artifact_path=artifact_path,
            connection=connection,
        )
        logger.info("Updated run.log in MLflow run %s", run_id)
    except Exception as e:
        logger.warning("Failed to update run.log in MLflow: %s", e)
