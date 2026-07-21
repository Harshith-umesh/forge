#!/usr/bin/env python3

from __future__ import annotations

import yaml

from projects.core.dsl import entrypoint, execute_tasks, retry, task
from projects.core.dsl.utils.k8s import oc

SERVING_CONTROL_PLANE_DEPLOYMENTS = (
    "kserve-controller-manager",
    "llmisvc-controller-manager",
    "model-serving-api",
    "odh-model-controller",
)


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


@task
def wait_for_deployments(args, ctx):
    """Wait for all serving control plane deployments to be available"""

    for deployment_name in SERVING_CONTROL_PLANE_DEPLOYMENTS:
        result = oc(
            "wait",
            "--for=condition=Available",
            "--timeout=300s",
            f"deployment/{deployment_name}",
            "-n",
            args.namespace,
            check=False,
        )
        if result.returncode != 0:
            status_result = oc(
                "get",
                "deployments",
                "-n",
                args.namespace,
                "-o",
                "wide",
                check=False,
            )
            error_msg = (
                f"Timeout waiting for serving control plane deployment "
                f"{deployment_name} in {args.namespace}"
            )
            if status_result.returncode == 0:
                error_msg += f"\n\nCurrent deployment status:\n{status_result.stdout}"
            raise RuntimeError(error_msg)

    return "All serving control plane deployments are available"


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
