#!/usr/bin/env python3

from __future__ import annotations

from projects.core.dsl import always, entrypoint, execute_tasks, retry, task
from projects.core.dsl.utils import write_text
from projects.core.dsl.utils.k8s import oc


@entrypoint
def run(
    *,
    datasciencecluster_name: str,
    namespace: str,
) -> int:
    """
    Wait for the llm_d DataScienceCluster to become ready.

    Args:
        datasciencecluster_name: Name of the DataScienceCluster to wait for
        namespace: Namespace containing the DataScienceCluster
    """

    execute_tasks(locals())
    return 0


@task
def capture_initial_dsc(args, ctx):
    """Capture the DataScienceCluster object before waiting begins"""

    # Ensure artifacts directory exists
    artifacts_dir = args.artifact_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = oc(
        "get",
        "datasciencecluster",
        args.datasciencecluster_name,
        "-n",
        args.namespace,
        "-o",
        "yaml",
        check=False,
        log_stdout=False,
    )

    if result.returncode == 0:
        write_text(artifacts_dir / "datasciencecluster-initial.yaml", result.stdout)
        return f"Captured initial DataScienceCluster {args.datasciencecluster_name}"
    else:
        write_text(
            artifacts_dir / "datasciencecluster-initial.yaml",
            "# DataScienceCluster did not exist initially\n",
        )
        return f"DataScienceCluster {args.datasciencecluster_name} did not exist initially"


@retry(attempts=90, delay=10, backoff=1.0)
@task
def wait_for_datasciencecluster_ready(args, ctx):
    """Wait for the DataScienceCluster phase to become Ready"""

    # Query only the status.phase field and show output
    result = oc(
        "get",
        "datasciencecluster",
        args.datasciencecluster_name,
        "-n",
        args.namespace,
        "-o",
        "jsonpath={.status.phase}",
        log_stdout=True,  # Show the output
        check=False,
    )

    # Abort if the DataScienceCluster is not found
    if result.returncode != 0:
        error_msg = f"DataScienceCluster {args.datasciencecluster_name} not found in namespace {args.namespace}"
        if result.stderr:
            error_msg += f": {result.stderr.strip()}"
        raise RuntimeError(error_msg)

    phase = result.stdout.strip() if result.stdout else None

    if phase == "Ready":
        return "DataScienceCluster ready"
    if phase in {"Failed", "Error"}:
        raise RuntimeError(f"DataScienceCluster entered terminal phase {phase}")

    # Provide specific reason for retry
    if not phase:
        return (False, "DataScienceCluster status.phase is empty, retrying...")
    else:
        return (False, f"DataScienceCluster is in {phase} phase, waiting for Ready...")


@always
@task
def capture_final_dsc(args, ctx):
    """Capture the DataScienceCluster object after waiting completes (always runs)"""

    # Ensure artifacts directory exists
    artifacts_dir = args.artifact_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = oc(
        "get",
        "datasciencecluster",
        args.datasciencecluster_name,
        "-n",
        args.namespace,
        "-o",
        "yaml",
        check=False,
        log_stdout=False,
    )

    if result.returncode == 0:
        write_text(artifacts_dir / "datasciencecluster-final.yaml", result.stdout)
        return f"Captured final DataScienceCluster {args.datasciencecluster_name}"
    else:
        write_text(
            artifacts_dir / "datasciencecluster-final.yaml", "# DataScienceCluster not found\n"
        )
        return f"DataScienceCluster {args.datasciencecluster_name} not found"


if __name__ == "__main__":
    run.main()
