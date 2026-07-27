#!/usr/bin/env python3

from __future__ import annotations

import logging
from pathlib import Path

from projects.core.dsl import entrypoint, execute_tasks, task
from projects.core.dsl.utils.k8s import oc

logger = logging.getLogger(__name__)


@entrypoint
def run(
    *,
    artifact_dir: Path,
    namespace: str,
    datasciencecluster_name: str,
    datasciencecluster_namespace: str,
    gateway_name: str,
    gateway_namespace: str,
    capture_namespace_events: bool = True,
) -> int:
    """
    Capture prepare phase state artifacts for diagnostics.

    Args:
        artifact_dir: Directory to write artifacts to
        namespace: Namespace for event capture
        datasciencecluster_name: Name of the DataScienceCluster
        datasciencecluster_namespace: Namespace of the DataScienceCluster
        gateway_name: Name of the Gateway
        gateway_namespace: Namespace of the Gateway
        capture_namespace_events: Whether to capture namespace events
    """

    execute_tasks(locals())
    return 0


@task
def setup_artifacts_directory(args, ctx):
    """Ensure artifacts directory exists"""

    ctx.artifacts_dir = args.artifact_dir / "artifacts"
    ctx.artifacts_dir.mkdir(parents=True, exist_ok=True)
    return f"Artifacts directory prepared: {ctx.artifacts_dir}"


@task
def capture_datasciencecluster_state(args, ctx):
    """Capture DataScienceCluster resource state"""

    destination = ctx.artifacts_dir / "datasciencecluster.yaml"

    result = oc(
        "get",
        "datasciencecluster",
        args.datasciencecluster_name,
        "-n",
        args.datasciencecluster_namespace,
        "-o",
        "yaml",
        check=False,
        log_stdout=False,
        stdout_dest=destination,
    )

    if result.returncode == 0:
        return f"Captured DataScienceCluster: {destination}"
    else:
        logger.warning("Failed to capture DataScienceCluster: %s", result.stderr)
        return "DataScienceCluster capture failed"


@task
def capture_gateway_state(args, ctx):
    """Capture Gateway resource state"""

    destination = ctx.artifacts_dir / "gateway.yaml"

    result = oc(
        "get",
        "gateway",
        args.gateway_name,
        "-n",
        args.gateway_namespace,
        "-o",
        "yaml",
        check=False,
        log_stdout=False,
        stdout_dest=destination,
    )

    if result.returncode == 0:
        return f"Captured Gateway: {destination}"
    else:
        logger.warning("Failed to capture Gateway: %s", result.stderr)
        return "Gateway capture failed"


@task
def capture_gateway_service_state(args, ctx):
    """Capture Gateway service state"""

    destination = ctx.artifacts_dir / "gateway.service.yaml"

    result = oc(
        "get",
        "service",
        "-A",
        "-l",
        f"gateway.networking.k8s.io/gateway-name={args.gateway_name}",
        "-o",
        "yaml",
        check=False,
        log_stdout=False,
        stdout_dest=destination,
    )

    if result.returncode == 0:
        return f"Captured Gateway service: {destination}"
    else:
        logger.warning("Failed to capture Gateway service: %s", result.stderr)
        return "Gateway service capture failed"


@task
def capture_namespace_events(args, ctx):
    """Capture namespace events for diagnostics"""

    if not args.capture_namespace_events:
        return "Namespace events capture disabled"

    destination = ctx.artifacts_dir / "namespace.events.txt"

    result = oc(
        "get",
        "events",
        "-n",
        args.namespace,
        "--sort-by=.metadata.creationTimestamp",
        check=False,
        log_stdout=False,
        stdout_dest=destination,
    )

    if result.returncode == 0:
        return f"Captured namespace events: {destination}"
    else:
        logger.warning("Failed to capture namespace events: %s", result.stderr)
        return "Namespace events capture failed"


if __name__ == "__main__":
    run.main()
