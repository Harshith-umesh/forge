#!/usr/bin/env python3

from __future__ import annotations

import yaml

from projects.core.dsl import (
    entrypoint,
    execute_tasks,
    task,
)
from projects.core.dsl.utils.k8s import oc, oc_resource_exists


@entrypoint
def run(
    *,
    namespace: str,
    servingruntime_file: str,
    inferenceservice_file: str,
):
    """Deploy a KServe InferenceService from pre-rendered YAML files.

    Args:
        namespace: Target namespace for the deployment.
        servingruntime_file: Path to a ServingRuntime YAML file.
        inferenceservice_file: Path to an InferenceService YAML file.
    """
    return execute_tasks(locals())


@task
def ensure_namespace(args, context):
    if oc_resource_exists("namespace", args.namespace):
        return f"Namespace {args.namespace} exists"
    oc("create", "namespace", args.namespace)
    return f"Created namespace {args.namespace}"


@task
def apply_servingruntime(args, context):
    oc("apply", "-f", args.servingruntime_file)
    with open(args.servingruntime_file) as f:
        name = yaml.safe_load(f).get("metadata", {}).get("name", "")
    return f"Applied ServingRuntime {name}"


@task
def apply_inferenceservice(args, context):
    oc("apply", "-f", args.inferenceservice_file)
    with open(args.inferenceservice_file) as f:
        name = yaml.safe_load(f).get("metadata", {}).get("name", "")
    return f"Applied InferenceService {name}"


if __name__ == "__main__":
    run.main()
