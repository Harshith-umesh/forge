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

    # Use plain text output to show both READY and REASON columns
    result = oc(
        "get",
        "datasciencecluster",
        args.datasciencecluster_name,
        "-n",
        args.namespace,
        log_stdout=True,  # Show the table output
        check=False,
    )

    # Abort if the DataScienceCluster is not found
    if result.returncode != 0:
        error_msg = f"DataScienceCluster {args.datasciencecluster_name} not found in namespace {args.namespace}"
        if result.stderr:
            error_msg += f": {result.stderr.strip()}"
        raise RuntimeError(error_msg)

    # Parse the table output into a dict
    lines = result.stdout.strip().split("\n")
    if len(lines) < 2:
        parsed_data = {}
    else:
        header = lines[0].split()
        data = lines[1].split()
        # Handle case where REASON column might be empty
        while len(data) < len(header):
            data.append("")
        parsed_data = dict(zip(header, data, strict=True))

    # Check if we have the required columns
    if not parsed_data:
        return (False, "Unable to parse DataScienceCluster output, waiting...")

    ready_value = parsed_data.get("READY", "").strip()
    reason_value = parsed_data.get("REASON", "").strip()

    # Check if READY column shows True
    if ready_value.lower() == "true":
        return f"DataScienceCluster ready (READY={ready_value})"

    # Fail fast if READY shows False and REASON shows Error
    if ready_value.lower() == "false" and "error" in reason_value.lower():
        raise RuntimeError(
            f"DataScienceCluster is in Error state (READY={ready_value}, REASON={reason_value}). Check the DSC status and logs for details."
        )

    # Fail fast if REASON shows Failed
    if "failed" in reason_value.lower():
        raise RuntimeError(
            f"DataScienceCluster failed (REASON={reason_value}). Check the DSC status and logs for details."
        )

    # Still waiting - provide context for retry
    return (
        False,
        f"DataScienceCluster is not ready yet (READY={ready_value}, REASON={reason_value or 'N/A'}), waiting...",
    )


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
