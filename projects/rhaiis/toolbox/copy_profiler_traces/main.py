#!/usr/bin/env python3

from projects.core.dsl import (
    entrypoint,
    execute_tasks,
    shell,
    task,
)


@entrypoint
def run(*, name: str, namespace: str):
    return execute_tasks(locals())


@task
def setup_directories(args, context):
    shell.mkdir("artifacts/traces")
    return "Traces directory created"


@task
def find_predictor_pod(args, context):
    result = shell.run(
        f"oc get pod -oname "
        f"-lserving.kserve.io/inferenceservice={args.name} "
        f"-n {args.namespace} "
        "| head -1",
        check=False,
    )
    pod_name = result.stdout.strip()
    if not pod_name:
        raise RuntimeError(f"No predictor pod found for {args.name} in {args.namespace}")
    context.pod_name = pod_name
    return f"Found pod: {pod_name}"


@task
def list_trace_files(args, context):
    result = shell.run(
        f"oc exec {context.pod_name} -n {args.namespace} "
        "-- sh -c 'ls /tmp/trace_*.json* 2>/dev/null || echo NO_TRACES'",
        check=False,
        log_stdout=False,
    )

    if "NO_TRACES" in result.stdout or not result.stdout.strip():
        raise RuntimeError(f"No profiler traces found in pod {context.pod_name}")

    trace_list = result.stdout.strip()
    context.trace_count = len(trace_list.splitlines())
    return f"Found {context.trace_count} trace files"


@task
def copy_traces(args, context):
    traces_dir = args.artifact_dir / "artifacts/traces"
    shell.run(
        f"oc exec {context.pod_name} -n {args.namespace} "
        "-- sh -c 'cd /tmp && tar cf - trace_*.json*' "
        f"| tar --no-same-owner -xf - -C {traces_dir}",
    )
    return f"Copied {context.trace_count} trace files to {traces_dir}"


if __name__ == "__main__":
    run.main()
