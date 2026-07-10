#!/usr/bin/env python3

from projects.core.dsl import (
    entrypoint,
    execute_tasks,
    shell,
    task,
)


@entrypoint
def run(*, name: str, namespace: str, gate_value: str = "0", disable: bool = False):
    return execute_tasks(locals())


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
def toggle_profiler_gate(args, context):
    if args.disable:
        shell.run(
            f"oc exec {context.pod_name} -n {args.namespace} "
            "-- sh -c 'rm -f /tmp/profiler_gate'",
            check=False,
        )
        return f"Profiler gate disabled on {context.pod_name}"

    shell.run(
        f"oc exec {context.pod_name} -n {args.namespace} "
        f"-- sh -c 'echo {args.gate_value} > /tmp/profiler_gate'",
    )
    return f"Profiler gate enabled on {context.pod_name} (value={args.gate_value})"


if __name__ == "__main__":
    run.main()
