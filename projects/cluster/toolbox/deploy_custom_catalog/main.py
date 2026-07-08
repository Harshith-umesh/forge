#!/usr/bin/env python3

from __future__ import annotations

import json
import logging

from projects.core.dsl import always, entrypoint, execute_tasks, retry, shell, task, template

logger = logging.getLogger("DSL")


@entrypoint
def run(
    catalog_source_name: str,
    catalog_namespace: str,
    catalog_image: str,
    *,
    display_name: str = "",
    publisher: str = "",
) -> int:
    """
    Deploy a CatalogSource from a custom index image and wait for it to become READY.

    Args:
        catalog_source_name: Name of the CatalogSource
        catalog_namespace: Namespace where the CatalogSource will be deployed
        catalog_image: Index image backing the CatalogSource
        display_name: Optional human-friendly label for logs
        publisher: Optional publisher label for the CatalogSource
    """

    execute_tasks(locals())
    return 0


@task
def setup_directories(args, ctx):
    """Create command source and artifact directories"""

    shell.mkdir("src")
    shell.mkdir("artifacts")
    ctx.display_name = args.display_name or args.catalog_source_name
    ctx.publisher = args.publisher or ""
    return f"Prepared directories for {ctx.display_name}"


@task
def validate_parameters(args, ctx):
    """Validate command parameters"""

    if not args.catalog_source_name:
        raise ValueError("catalog_source_name is required")
    if not args.catalog_namespace:
        raise ValueError("catalog_namespace is required")
    if not args.catalog_image:
        raise ValueError("catalog_image is required")

    return f"Validated custom catalog parameters for {ctx.display_name}"


@task
def render_catalog_source_manifest(args, ctx):
    """Render the CatalogSource manifest"""

    ctx.catalog_manifest_file = (
        args.artifact_dir / "src" / f"{args.catalog_source_name}-catalogsource.yaml"
    )
    template.render_template_to_file("catalogsource.yaml.j2", ctx.catalog_manifest_file)
    return f"Rendered CatalogSource manifest for {ctx.display_name}"


@task
def apply_catalog_source(args, ctx):
    """Apply the CatalogSource manifest"""

    shell.run(f"oc apply -f {ctx.catalog_manifest_file}")
    return f"Applied CatalogSource {args.catalog_source_name}"


@retry(attempts=60, delay=15)
@task
def wait_for_catalog_source_ready(args, ctx):
    """Wait for the CatalogSource to report READY"""

    result = shell.run(
        f"oc get catalogsource {args.catalog_source_name} -n {args.catalog_namespace} -o json",
        check=False,
        log_stdout=False,
    )
    if not result.success:
        return False

    payload = json.loads(result.stdout)
    ctx.catalog_payload = payload
    state = payload.get("status", {}).get("connectionState", {}).get("lastObservedState", "")
    if state == "READY":
        return f"CatalogSource {args.catalog_source_name} is READY"
    return False


@always
@task
def capture_catalog_source(args, ctx):
    """Capture CatalogSource YAML and JSON"""

    shell.run(
        f"oc get catalogsource {args.catalog_source_name} -n {args.catalog_namespace} -o yaml",
        stdout_dest=args.artifact_dir / "artifacts" / "catalogsource.yaml",
        check=False,
    )
    shell.run(
        f"oc get catalogsource {args.catalog_source_name} -n {args.catalog_namespace} -o json",
        stdout_dest=args.artifact_dir / "artifacts" / "catalogsource.json",
        check=False,
    )
    return "Captured CatalogSource state"


@always
@task
def capture_namespace_pods(args, ctx):
    """Capture pods from the catalog namespace"""

    shell.run(
        f"oc get pods -n {args.catalog_namespace} -o wide",
        stdout_dest=args.artifact_dir / "artifacts" / "pods.status",
        check=False,
    )
    shell.run(
        f"oc get pods -n {args.catalog_namespace} -o yaml",
        stdout_dest=args.artifact_dir / "artifacts" / "pods.yaml",
        check=False,
    )
    return "Captured catalog namespace pods"


if __name__ == "__main__":
    run.main()
