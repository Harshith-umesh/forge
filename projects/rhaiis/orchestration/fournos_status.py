"""Publish the active RHAIIS inference resource on its FournosJob."""

from __future__ import annotations

import json
import logging
import os
import subprocess

logger = logging.getLogger(__name__)


def relevant_deployments_status_patch(reference: dict | None) -> dict:
    """Build a narrow merge patch for Forge-owned FournosJob status."""
    deployments = [reference] if reference is not None else None
    return {
        "status": {
            "engineStatus": {
                "forge": {
                    "relevantDeployments": deployments,
                }
            }
        }
    }


def patch_fjob_relevant_deployments(
    job_name: str,
    namespace: str,
    reference: dict | None,
) -> bool:
    """Best-effort status-subresource merge patch; never replaces sibling status."""
    if not job_name:
        return True

    command = [
        "oc",
        "patch",
        "fournosjob",
        job_name,
        "-n",
        namespace,
        "--type=merge",
        "--subresource=status",
        "-p",
        json.dumps(relevant_deployments_status_patch(reference)),
    ]
    management_env = {key: value for key, value in os.environ.items() if key != "KUBECONFIG"}
    action = "clear" if reference is None else "publish"
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env=management_env,
        )
    except Exception:
        logger.warning(
            "Failed to %s active inference-service reference on FournosJob %s",
            action,
            job_name,
            exc_info=True,
        )
        return False

    if result.returncode != 0:
        logger.warning(
            "Could not %s active inference-service reference on FournosJob %s (exit=%d): %s",
            action,
            job_name,
            result.returncode,
            (result.stderr or "").strip()[:500],
        )
        return False

    logger.info("%s active inference-service reference on FournosJob %s", action.title(), job_name)
    return True
