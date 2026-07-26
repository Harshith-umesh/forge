#!/usr/bin/env python3

"""
Capture Prometheus TSDB Toolbox

Extracts cluster Prometheus metrics for a specific time window and saves them
as a compressed OpenMetrics archive. The output can later be imported into a
local Prometheus instance for offline querying.

Can be run standalone:
    python -m projects.cluster.toolbox.capture_prometheus.main \\
        --start-time "2026-07-26T10:00:00+00:00" \\
        --end-time "2026-07-26T10:20:00+00:00" \\
        --output-dir /path/to/output
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from projects.core.dsl import always, entrypoint, execute_tasks, shell, task

logger = logging.getLogger("DSL")

PROMETHEUS_NAMESPACE = "openshift-monitoring"
PROMETHEUS_POD = "prometheus-k8s-0"
PROMETHEUS_CONTAINER = "prometheus"
TSDB_PATH = "/prometheus"


@entrypoint
def run(
    start_time: datetime,
    end_time: datetime,
    output_dir: str | Path,
    *,
    pod_name: str = PROMETHEUS_POD,
    namespace: str = PROMETHEUS_NAMESPACE,
    container: str = PROMETHEUS_CONTAINER,
) -> int:
    """
    Capture Prometheus metrics for a specific time window.

    Extracts all metrics between start_time and end_time from the cluster's
    Prometheus TSDB and writes them as a compressed OpenMetrics file to output_dir.

    Args:
        start_time: Start of the capture window (UTC)
        end_time: End of the capture window (UTC)
        output_dir: Directory to write the metrics archive into
        pod_name: Prometheus pod to extract from
        namespace: Namespace where Prometheus runs
        container: Container name within the Prometheus pod
    """
    execute_tasks(locals())
    return 0


def _parse_time(value) -> datetime:
    """Parse a datetime from string (ISO format) or pass through if already datetime.

    Naive datetimes are assumed UTC. Aware datetimes are normalized to UTC.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@task
def validate_parameters(args, ctx):
    """Validate inputs and compute time bounds."""

    ctx.start_time = _parse_time(args.start_time)
    ctx.end_time = _parse_time(args.end_time)

    if ctx.end_time <= ctx.start_time:
        raise ValueError("end_time must be after start_time")

    max_duration_seconds = 2 * 3600
    duration = (ctx.end_time - ctx.start_time).total_seconds()
    if duration > max_duration_seconds:
        logger.warning(
            "Test duration (%ds) exceeds the 2-hour WAL window. "
            "Skipping Prometheus capture (persistent block handling not yet implemented).",
            int(duration),
        )
        ctx.skip_capture = True
        return f"SKIPPED: duration {int(duration)}s exceeds 2-hour WAL window"

    ctx.output_dir = Path(args.output_dir)
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    ctx.start_ms = int(ctx.start_time.timestamp() * 1000)
    ctx.end_ms = int(ctx.end_time.timestamp() * 1000)
    ctx.duration_seconds = int((ctx.end_time - ctx.start_time).total_seconds())

    ctx.namespace = args.namespace or PROMETHEUS_NAMESPACE
    ctx.pod_name = args.pod_name or PROMETHEUS_POD
    ctx.container = args.container or PROMETHEUS_CONTAINER

    return (
        f"Capture window: {ctx.start_time.isoformat()} → {ctx.end_time.isoformat()} "
        f"({ctx.duration_seconds}s)"
    )


@task
def validate_prometheus_pod(args, ctx):
    """Verify the Prometheus pod is running and accessible."""

    if getattr(ctx, "skip_capture", False):
        return "SKIPPED"

    result = shell.run(
        f"oc -n {ctx.namespace} get pod {ctx.pod_name} -o jsonpath='{{.status.phase}}'",
    )

    phase = result.stdout.strip()
    if phase != "Running":
        raise RuntimeError(
            f"Prometheus pod {ctx.pod_name} is in phase '{phase}', expected 'Running'"
        )

    return f"Prometheus pod {ctx.pod_name} is running"


@task
def create_temp_tsdb_dir(args, ctx):
    """Create a temp directory on the PVC with symlinks to WAL and chunks_head only.

    This limits promtool to scanning only recent in-memory data (~1-2 GB)
    instead of the full TSDB which can be tens of GB.
    """

    if getattr(ctx, "skip_capture", False):
        return "SKIPPED"

    ctx.temp_dir = f"{TSDB_PATH}/.capture-tmp-{ctx.start_ms}"

    shell.run(
        f"oc -n {ctx.namespace} exec {ctx.pod_name} -c {ctx.container} -- "
        f"sh -c '"
        f"mkdir -p {ctx.temp_dir} && "
        f"ln -sf {TSDB_PATH}/wal {ctx.temp_dir}/wal && "
        f"ln -sf {TSDB_PATH}/chunks_head {ctx.temp_dir}/chunks_head"
        f"'",
    )

    return f"Created temp TSDB dir at {ctx.temp_dir}"


@task
def dump_metrics(args, ctx):
    """Run promtool tsdb dump-openmetrics with time filtering against the temp dir."""

    if getattr(ctx, "skip_capture", False):
        return "SKIPPED"

    ctx.remote_raw = f"{TSDB_PATH}/.capture-metrics-{ctx.start_ms}"
    ctx.remote_output = f"{ctx.remote_raw}.gz"

    shell.run(
        f"oc -n {ctx.namespace} exec {ctx.pod_name} -c {ctx.container} -- "
        f"sh -c '"
        f"promtool tsdb dump-openmetrics "
        f"--min-time={ctx.start_ms} --max-time={ctx.end_ms} "
        f"{ctx.temp_dir} > {ctx.remote_raw} && gzip -f {ctx.remote_raw}"
        f"'",
    )

    size_result = shell.run(
        f"oc -n {ctx.namespace} exec {ctx.pod_name} -c {ctx.container} -- "
        f"stat -c %s {ctx.remote_output}",
        check=False,
    )

    ctx.archive_size_bytes = int(size_result.stdout.strip()) if size_result.success else 0

    return f"Dumped metrics ({ctx.archive_size_bytes} bytes compressed)"


@task
def copy_to_output(args, ctx):
    """Copy the compressed metrics file from the pod to the output directory."""

    if getattr(ctx, "skip_capture", False):
        return "SKIPPED"

    ctx.output_file = ctx.output_dir / "metrics.openmetrics.gz"

    shell.run(
        f"oc cp {ctx.namespace}/{ctx.pod_name}:{ctx.remote_output} "
        f"{ctx.output_file} -c {ctx.container}",
    )

    return f"Copied metrics to {ctx.output_file}"


@always
@task
def cleanup_pod(args, ctx):
    """Remove temp files from the Prometheus pod."""

    namespace = getattr(ctx, "namespace", None) or PROMETHEUS_NAMESPACE
    pod_name = getattr(ctx, "pod_name", None) or PROMETHEUS_POD
    container = getattr(ctx, "container", None) or PROMETHEUS_CONTAINER
    temp_dir = getattr(ctx, "temp_dir", None)
    remote_output = getattr(ctx, "remote_output", None)

    if temp_dir:
        shell.run(
            f"oc -n {namespace} exec {pod_name} -c {container} -- rm -rf {temp_dir}",
            check=False,
        )

    if remote_output:
        shell.run(
            f"oc -n {namespace} exec {pod_name} -c {container} -- rm -f {remote_output}",
            check=False,
        )

    remote_raw = getattr(ctx, "remote_raw", None)
    if remote_raw:
        shell.run(
            f"oc -n {namespace} exec {pod_name} -c {container} -- rm -f {remote_raw}",
            check=False,
        )

    return "Cleaned up temp files on Prometheus pod"


if __name__ == "__main__":
    run.main()
