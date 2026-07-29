#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path

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
    servingruntime_manifest: dict,
    inferenceservice_manifest: dict,
):
    """Deploy a KServe InferenceService from pre-built manifests.

    Args:
        namespace: Target namespace for the deployment.
        servingruntime_manifest: Pre-built ServingRuntime manifest dict.
        inferenceservice_manifest: Pre-built InferenceService manifest dict.
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
    output_path = args.artifact_dir / "servingruntime.yaml"
    _write_manifest(args.servingruntime_manifest, output_path)
    oc("apply", "-f", str(output_path))
    name = args.servingruntime_manifest.get("metadata", {}).get("name", "")
    return f"Applied ServingRuntime {name}"


@task
def apply_inferenceservice(args, context):
    output_path = args.artifact_dir / "inferenceservice.yaml"
    _write_manifest(args.inferenceservice_manifest, output_path)
    oc("apply", "-f", str(output_path))
    name = args.inferenceservice_manifest.get("metadata", {}).get("name", "")
    return f"Applied InferenceService {name}"


def _write_manifest(manifest: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(manifest, f, default_flow_style=False, sort_keys=False)


if __name__ == "__main__":
    run.main()
