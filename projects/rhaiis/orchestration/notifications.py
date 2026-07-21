"""RHAIIS pipeline notification helpers."""

from __future__ import annotations

import logging
import os
import traceback

from projects.rhaiis.orchestration import runtime_config

logger = logging.getLogger(__name__)


def send_pipeline_failure_alert(
    exc: Exception,
    *,
    model_key: str,
    workload_keys: list[str],
) -> None:
    """Best-effort Slack alert when the pipeline fails."""
    try:
        from projects.core.library import config as _cfg
        from projects.rhaiis.postprocess.regression import send_failure_notification

        model_cfg = runtime_config.get_model(model_key)
        accelerator = runtime_config.get_accelerator()
        gpu_type = runtime_config.get_gpu_type(accelerator) or accelerator
        cluster_tag = _cfg.project.get_config("rhaiis.cluster_tag", "")
        vllm_args = runtime_config.merge_vllm_args(
            runtime_config.get_vllm_defaults(),
            model_cfg,
            runtime_config.get_workload(workload_keys[0]),
        )

        send_failure_notification(
            error=traceback.format_exception_only(type(exc), exc)[-1].strip(),
            model=model_cfg.get("hf_model_id", model_key),
            accelerator=f"{gpu_type}_{cluster_tag}".upper() if cluster_tag else gpu_type.upper(),
            job_id=os.environ.get("FJOB_NAME", ""),
            slack_user=_cfg.project.get_config("tests.rhaiis.slack_user", ""),
            notification_vault="psap-forge-notifications",
            tp=str(vllm_args.get("tensor-parallel-size", "")),
            dp=str(vllm_args.get("data-parallel-size", "")),
            version=_cfg.project.get_config("tests.rhaiis.version", ""),
            workload_keys=workload_keys,
            cluster=cluster_tag,
        )
    except Exception:
        logger.warning("Failed to send pipeline failure alert", exc_info=True)
