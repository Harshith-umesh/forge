#!/usr/bin/env python3

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import yaml

from projects.core.dsl import entrypoint, execute_tasks, retry, task
from projects.core.dsl.utils.k8s import oc

logger = logging.getLogger(__name__)


@entrypoint
def run(*, namespace: str) -> str:
    """
    Wait for the KServe serving control plane deployments to be ready.

    Args:
        namespace: Namespace where the serving control plane is deployed
    """

    task_args = {"namespace": namespace}
    execute_tasks(task_args)

    return f"Serving control plane is ready in {namespace}"


DEPLOYMENT_NAME_KEYWORDS = ("kserve", "llmisvc", "model")


@task
def discover_deployments(args, ctx):
    """Discover serving control plane deployments matching known keywords"""

    result = oc(
        "get",
        "deploy",
        "-n",
        args.namespace,
        "-oname",
        log_stdout=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to list deployments in {args.namespace}")

    names = [
        line.removeprefix("deployment.apps/")
        for line in result.stdout.splitlines()
        if any(kw in line for kw in DEPLOYMENT_NAME_KEYWORDS)
    ]

    if not names:
        raise RuntimeError(
            f"No deployments matching {DEPLOYMENT_NAME_KEYWORDS} found in {args.namespace}"
        )

    ctx.deployment_names = names


@retry(attempts=30, delay=10, backoff=1.0)
@task
def wait_for_deployments(args, ctx):
    """Wait for all serving control plane deployments to be available"""

    result = oc(
        "get",
        "deploy",
        *ctx.deployment_names,
        "-n",
        args.namespace,
        "-o",
        "json",
        check=False,
        log_stdout=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to get deployments in {args.namespace}")

    deployments = json.loads(result.stdout).get("items", [])

    # Process deployment status information
    deployment_status = []
    not_ready = []

    for d in deployments:
        name = d["metadata"]["name"]
        available_replicas = d["status"].get("availableReplicas", 0)
        desired_replicas = d["spec"].get("replicas", 1)
        is_ready = available_replicas >= desired_replicas

        status_info = {
            "name": name,
            "namespace": args.namespace,
            "ready": is_ready,
            "available_replicas": available_replicas,
            "desired_replicas": desired_replicas,
            "status": f"{available_replicas}/{desired_replicas}",
        }

        deployment_status.append(status_info)

        if not is_ready:
            not_ready.append(f"{name} ({available_replicas}/{desired_replicas})")

    # Save deployment status to artifacts
    artifacts_dir = Path("artifacts")
    artifacts_dir.mkdir(exist_ok=True)

    deployment_data = {
        "timestamp": datetime.now(UTC).isoformat(),
        "namespace": args.namespace,
        "total_deployments": len(deployments),
        "ready_deployments": len(deployments) - len(not_ready),
        "not_ready_deployments": len(not_ready),
        "deployments": deployment_status,
    }

    artifacts_file = artifacts_dir / "kserve_deployments.yaml"
    with open(artifacts_file, "w") as f:
        yaml.dump(deployment_data, f, default_flow_style=False, sort_keys=False)

    if not_ready:
        logger.info("Waiting for: %s", ", ".join(not_ready))
        return False

    return f"All {len(ctx.deployment_names)} deployments are available"


@retry(attempts=40, delay=5, backoff=1.0)
@task
def probe_webhook_ready(args, ctx):
    """Probe the admission webhook with a dry-run apply to confirm it is serving"""

    probe_manifest = {
        "apiVersion": "serving.kserve.io/v1alpha1",
        "kind": "LLMInferenceService",
        "metadata": {
            "name": "forge-webhook-probe",
            "namespace": args.namespace,
        },
        "spec": {
            "model": {
                "uri": "hf://probe/model",
                "name": "probe",
            },
        },
    }

    probe_dir = args.artifact_dir / "src"
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe_path = probe_dir / "webhook-probe.yaml"
    with open(probe_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(probe_manifest, f, sort_keys=False)

    result = oc(
        "apply",
        "--dry-run=server",
        "-f",
        str(probe_path),
        "-n",
        args.namespace,
        check=False,
    )

    if result.returncode == 0:
        return "Webhook is ready (dry-run accepted)"

    return False


@task
def capture_kserve_objects(args, ctx):
    """Capture KServe objects in YAML format (ignoring errors)"""

    # Create artifacts directory
    artifacts_dir = Path("artifacts")
    artifacts_dir.mkdir(exist_ok=True)

    # Capture ServingRuntimes (namespaced)
    oc(
        "get",
        "servingruntimes",
        "-n",
        args.namespace,
        "-oyaml",
        stdout_dest=artifacts_dir / "kserve.servingruntimes.yaml",
        check=False,
    )

    # Capture ClusterServingRuntimes (cluster-scoped)
    oc(
        "get",
        "clusterservingruntimes",
        "-oyaml",
        stdout_dest=artifacts_dir / "kserve.clusterservingruntimes.yaml",
        check=False,
    )

    # Capture InferenceServices (if any exist alongside LLMInferenceServices)
    oc(
        "get",
        "inferenceservices",
        "-n",
        args.namespace,
        "-oyaml",
        stdout_dest=artifacts_dir / "kserve.inferenceservices.yaml",
        check=False,
    )

    # Capture KServe MutatingWebhookConfigurations
    oc(
        "get",
        "mutatingwebhookconfigurations",
        "-l",
        "app.kubernetes.io/part-of=kserve",
        "-oyaml",
        stdout_dest=artifacts_dir / "kserve.mutatingwebhooks.yaml",
        check=False,
    )

    # Capture KServe ValidatingWebhookConfigurations
    oc(
        "get",
        "validatingwebhookconfigurations",
        "-l",
        "app.kubernetes.io/part-of=kserve",
        "-oyaml",
        stdout_dest=artifacts_dir / "kserve.validatingwebhooks.yaml",
        check=False,
    )

    return "KServe objects captured"


if __name__ == "__main__":
    run.main()
