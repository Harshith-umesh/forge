#!/usr/bin/env python3

from __future__ import annotations

import json
import logging

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
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to get deployments in {args.namespace}")

    not_ready = [
        f"{d['metadata']['name']} ({d['status'].get('availableReplicas', 0)}/{d['spec'].get('replicas', 1)})"
        for d in json.loads(result.stdout).get("items", [])
        if d["status"].get("availableReplicas", 0) < d["spec"].get("replicas", 1)
    ]

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


if __name__ == "__main__":
    run.main()
