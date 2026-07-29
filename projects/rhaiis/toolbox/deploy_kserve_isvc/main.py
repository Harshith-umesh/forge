#!/usr/bin/env python3

from projects.core.dsl import (
    entrypoint,
    execute_tasks,
    task,
    template,
)
from projects.core.dsl.utils.k8s import oc, oc_resource_exists


@entrypoint
def run(
    *,
    deployment_name: str,
    namespace: str,
    model_id: str,
    serving_image: str,
    accelerator: str = "nvidia",
    engine: str = "vllm",
    engine_args: dict | None = None,
    engine_port: int = 8080,
    trtllm_config: dict | None = None,
    env_vars: dict | None = None,
    replicas: int = 1,
    cpu_request: str = "4",
    memory_request: str = "16Gi",
    storage_source: str = "hf",
    storage_pvc: str = "",
    image_pull_secrets: list | None = None,
    service_account_name: str = "",
    labels: dict | None = None,
):
    return execute_tasks(locals())


@task
def prepare_args(args, context):
    context.env_vars_list = []
    for key, value in (args.env_vars or {}).items():
        context.env_vars_list.append({"name": key, "value": str(value)})

    ea = args.engine_args or {}
    tp_size = ea.get("tensor-parallel-size") or ea.get("tp-size") or ea.get("tp_size") or 1
    context.gpu_count = int(tp_size)

    if args.storage_pvc:
        context.use_pvc = True
        context.pvc_name = args.storage_pvc
    else:
        context.use_pvc = False
        context.pvc_name = ""

    return f"engine={args.engine}, GPU count={context.gpu_count}, env_vars={len(context.env_vars_list)}, pvc={context.use_pvc}"


@task
def ensure_namespace(args, context):
    if oc_resource_exists("namespace", args.namespace):
        return f"Namespace {args.namespace} exists"
    oc("create", "namespace", args.namespace)
    return f"Created namespace {args.namespace}"


@task
def render_and_apply_servingruntime(args, context):
    output_path = args.artifact_dir / "servingruntime.yaml"
    template.render_template_to_file("servingruntime.yaml.j2", output_path)
    oc("apply", "-f", str(output_path))
    return f"Applied ServingRuntime {args.deployment_name}"


@task
def render_and_apply_inferenceservice(args, context):
    output_path = args.artifact_dir / "inferenceservice.yaml"
    template.render_template_to_file("inferenceservice.yaml.j2", output_path)
    oc("apply", "-f", str(output_path))
    return f"Applied InferenceService {args.deployment_name}"


if __name__ == "__main__":
    run.main()
