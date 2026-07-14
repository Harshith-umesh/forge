#!/usr/bin/env python3

from __future__ import annotations

import time

from projects.core.dsl import entrypoint, execute_tasks, task
from projects.core.dsl.utils.k8s import oc

SERVING_CONTROL_PLANE_DEPLOYMENTS = (
    "kserve-controller-manager",
    "llmisvc-controller-manager",
    "model-serving-api",
    "odh-model-controller",
)
SERVING_CONTROL_PLANE_STABILIZATION_SECONDS = 45


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


@task
def wait_for_stabilization(args, ctx):
    """Wait for serving control plane to stabilize after becoming available"""

    # The webhook deployments can report Available before leader election and informer
    # startup finish, which makes the first LLMInferenceService create race the webhook.
    time.sleep(SERVING_CONTROL_PLANE_STABILIZATION_SECONDS)

    return f"Waited {SERVING_CONTROL_PLANE_STABILIZATION_SECONDS}s for stabilization"


if __name__ == "__main__":
    run.main()
