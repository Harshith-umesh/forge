#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path

import yaml

from projects.core.dsl import (
    EarlyReturn,
    always,
    entrypoint,
    execute_tasks,
    on_failure,
    retry,
    task,
)
from projects.core.dsl.utils import write_text
from projects.core.dsl.utils.k8s import (
    oc,
    oc_apply,
    oc_get_json,
)

from .on_failure_helpers import on_wait_pods_appear_failure


def load_yaml(path: Path):
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@entrypoint
def run(
    *,
    namespace: str,
    inference_service_manifest_path: str,
    gateway_status_address_name: str | None = "gateway-external",
    dry_run: bool = False,
    wait_pods_scheduled: bool = False,
) -> str:
    """
    Deploy an LLMInferenceService and wait for its endpoint.

    Args:
        namespace: Namespace used by llm_d
        inference_service_manifest_path: Path to the InferenceService YAML manifest file
        gateway_status_address_name: Gateway status address name for endpoint resolution
        dry_run: If True, only prepare the manifest without deploying
        wait_pods_scheduled: If True, wait for all pods to be scheduled before checking service readiness
    """

    ctx = execute_tasks(locals())

    if dry_run:
        return ctx.src_manifest_path

    # Ensure endpoint_url is available
    endpoint_url = getattr(ctx, "endpoint_url", None)
    if not endpoint_url:
        raise RuntimeError("Failed to resolve gateway endpoint URL after deployment")

    return endpoint_url


@task
def copy_manifest_to_src(args, ctx):
    """Copy inference service manifest to src directory and extract service name"""
    import shutil

    # Get the original manifest path
    original_path = Path(args.inference_service_manifest_path)

    # Load manifest to extract the service name
    manifest = load_yaml(original_path)
    ctx.inference_service_name = manifest["metadata"]["name"]
    ctx.selector = f"app.kubernetes.io/name={ctx.inference_service_name}"

    # Ensure the src directory exists
    src_dir = args.artifact_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    # Copy the manifest to src directory
    src_path = src_dir / original_path.name
    shutil.copy2(original_path, src_path)

    # Store the src path in context for other tasks to use
    ctx.src_manifest_path = str(src_path)

    return f"Copied manifest from {original_path} to {src_path} (service: {ctx.inference_service_name})"


@task
def check_dry_run(args, ctx):
    """Check if dry-run mode is enabled and return early if so"""
    if args.dry_run:
        return EarlyReturn(f"Dry-run completed: Prepared manifest for {ctx.inference_service_name}")
    return "Proceeding with full deployment"


@task
def delete_existing_service(args, ctx):
    """Delete existing LLMInferenceService"""

    name = ctx.inference_service_name
    oc(
        "delete",
        "llminferenceservice",
        name,
        "-n",
        args.namespace,
        "--ignore-not-found=true",
        check=False,
    )

    return f"Deleted existing LLMInferenceService {name}"


@retry(attempts=60, delay=10, backoff=1.0)
@task
def wait_old_pods_gone(args, ctx):
    """Wait for old llm-d pods to disappear"""

    result = oc(
        "get",
        "pods",
        "-n",
        args.namespace,
        "-l",
        ctx.selector,
        "--ignore-not-found=true",
        "--no-headers",
        check=False,
    )

    # Check if output is empty (no pods found)
    if not result.stdout.strip():
        return f"Old pods gone for {ctx.inference_service_name}"
    return False  # Retry


@task
def apply_inference_service(args, ctx):
    """Apply the LLMInferenceService manifest"""

    # Use the manifest copied to src directory
    src_manifest_path = ctx.src_manifest_path

    # Load and apply the manifest from src
    manifest = load_yaml(Path(src_manifest_path))
    oc_apply(src_manifest_path, manifest)
    return f"Applied LLMInferenceService manifest from {src_manifest_path} for {ctx.inference_service_name}"


@on_failure(on_wait_pods_appear_failure)
@retry(attempts=12, delay=5, backoff=1.0)
@task
def wait_pods_appear(args, ctx):
    """Wait for llm-d pods to appear"""

    pods = oc_get_json(
        "pods", namespace=args.namespace, selector=ctx.selector, ignore_not_found=True
    )
    if pods and pods.get("items"):
        return f"Pods appeared for {ctx.inference_service_name}"
    return False  # Retry


@always
@task
def capture_llmisv_description(args, ctx):
    """Capture LLMISV description with events and status for failure analysis"""

    if args.dry_run:
        return "Dry-run, nothing to do"

    try:
        # Ensure artifacts directory exists
        artifacts_dir = args.artifact_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        # Use LLMInferenceService name from context
        service_name = getattr(ctx, "inference_service_name", None)
        if not service_name:
            return "No service name available"

        # Capture LLMISV description
        result = oc(
            "describe",
            "llminferenceservice",
            service_name,
            "-n",
            args.namespace,
            log_stdout=False,
            check=False,
        )

        llmisv_desc_path = artifacts_dir / "llmisv_description.txt"
        with open(llmisv_desc_path, "w", encoding="utf-8") as f:
            f.write(result.stdout)

        return f"Captured LLMISV description to {llmisv_desc_path}"

    except Exception as e:
        return f"Failed to capture LLMISV description: {e}"


@always
@task
def capture_replicaset_description(args, ctx):
    """Capture ReplicaSet description for pod creation failure analysis"""

    if args.dry_run:
        return "Dry-run, nothing to do"

    try:
        # Ensure artifacts directory exists
        artifacts_dir = args.artifact_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        # Use LLMInferenceService name from context
        service_name = getattr(ctx, "inference_service_name", None)
        if not service_name:
            return "No service name available"

        # Get replicasets for the service
        rs_result = oc(
            "get",
            "replicaset",
            "-l",
            ctx.selector,
            "-n",
            args.namespace,
            "-o",
            "name",
            log_stdout=False,
            check=False,
        )

        replicaset_descriptions = []

        if rs_result.stdout.strip():
            # Describe each replicaset
            for rs_name in rs_result.stdout.strip().split("\n"):
                if not rs_name.strip():
                    continue

                rs_desc_result = oc(
                    "describe",
                    rs_name.strip(),
                    "-n",
                    args.namespace,
                    log_stdout=False,
                    check=False,
                )
                replicaset_descriptions.append(rs_desc_result.stdout)

        # Save all replicaset descriptions
        rs_desc_path = artifacts_dir / "replicaset_description.txt"
        with open(rs_desc_path, "w", encoding="utf-8") as f:
            if replicaset_descriptions:
                f.write("\n".join(replicaset_descriptions))
            else:
                f.write("No replicasets found for the service")

        return f"Captured ReplicaSet description to {rs_desc_path}"

    except Exception as e:
        return f"Failed to capture ReplicaSet description: {e}"


@always
@task
def capture_final_llmisvc_yaml(args, ctx):
    """Capture the final YAML state of the LLMInferenceService"""

    if args.dry_run:
        return "Dry-run, nothing to do"

    try:
        # Ensure artifacts directory exists
        artifacts_dir = args.artifact_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        # Use LLMInferenceService name from context
        service_name = getattr(ctx, "inference_service_name", None)
        if not service_name:
            return "No service name available"

        # Capture final YAML state
        result = oc(
            "get",
            "llminferenceservice",
            service_name,
            "-n",
            args.namespace,
            "-o",
            "yaml",
            log_stdout=False,
            check=False,
        )

        llmisvc_yaml_path = artifacts_dir / "llmisvc_final.yaml"
        with open(llmisvc_yaml_path, "w", encoding="utf-8") as f:
            f.write(result.stdout)

        return f"Captured final LLMInferenceService YAML to {llmisvc_yaml_path}"

    except Exception as e:
        return f"Failed to capture final LLMInferenceService YAML: {e}"


@always
@task
def capture_pod_status(args, ctx):
    """Capture pod status for debugging"""

    if args.dry_run:
        return "Dry-run, nothing to do"

    try:
        # Ensure artifacts directory exists
        artifacts_dir = args.artifact_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        # Use selector from context
        selector = getattr(ctx, "selector", None)
        if not selector:
            return "No selector available"

        # Capture pod status with wide output
        result = oc(
            "get",
            "pods",
            "-l",
            selector,
            "-n",
            args.namespace,
            "-o",
            "wide",
            log_stdout=False,
            check=False,
        )

        pod_status_path = artifacts_dir / "pod_status.txt"
        with open(pod_status_path, "w", encoding="utf-8") as f:
            f.write(result.stdout)

        return f"Captured pod status to {pod_status_path}"

    except Exception as e:
        return f"Failed to capture pod status: {e}"


@always
@task
def capture_pod_descriptions(args, ctx):
    """Capture pod descriptions for debugging"""

    if args.dry_run:
        return "Dry-run, nothing to do"

    try:
        # Ensure artifacts directory exists
        artifacts_dir = args.artifact_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        # Use selector from context
        selector = getattr(ctx, "selector", None)
        if not selector:
            return "No selector available"

        # Get pod names
        pod_result = oc(
            "get",
            "pods",
            "-l",
            selector,
            "-n",
            args.namespace,
            "-o",
            "jsonpath={.items[*].metadata.name}",
            log_stdout=False,
            check=False,
        )

        pod_names = pod_result.stdout.strip().split()
        if not pod_names or not pod_result.stdout.strip():
            pod_desc_path = artifacts_dir / "pod_descriptions.txt"
            with open(pod_desc_path, "w", encoding="utf-8") as f:
                f.write("No pods found for the service")
            return f"No pods found, wrote empty file to {pod_desc_path}"

        # Describe each pod
        pod_descriptions = []
        for pod_name in pod_names:
            describe_result = oc(
                "describe",
                "pod",
                pod_name,
                "-n",
                args.namespace,
                log_stdout=False,
                check=False,
            )
            pod_descriptions.append(
                f"=== Description for pod: {pod_name} ===\n{describe_result.stdout}"
            )

        # Save all pod descriptions
        pod_desc_path = artifacts_dir / "pod_descriptions.txt"
        with open(pod_desc_path, "w", encoding="utf-8") as f:
            f.write("\n\n".join(pod_descriptions))

        return f"Captured descriptions for {len(pod_names)} pods to {pod_desc_path}"

    except Exception as e:
        return f"Failed to capture pod descriptions: {e}"


@always
@task
def capture_pod_yaml(args, ctx):
    """Capture pod YAML definitions for debugging"""

    if args.dry_run:
        return "Dry-run, nothing to do"

    try:
        # Ensure artifacts directory exists
        artifacts_dir = args.artifact_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        # Use selector from context
        selector = getattr(ctx, "selector", None)
        if not selector:
            return "No selector available"

        # Capture all pod YAMLs
        result = oc(
            "get",
            "pods",
            "-l",
            selector,
            "-n",
            args.namespace,
            "-o",
            "yaml",
            log_stdout=False,
            check=False,
        )

        pod_yaml_path = artifacts_dir / "pod_definitions.yaml"
        with open(pod_yaml_path, "w", encoding="utf-8") as f:
            f.write(result.stdout)

        return f"Captured pod YAML definitions to {pod_yaml_path}"

    except Exception as e:
        return f"Failed to capture pod YAML: {e}"


@task
def query_service_status(args, ctx):
    """Query the status of the LLMInferenceService"""

    service_name = ctx.inference_service_name

    # Query only the Ready condition status
    result = oc(
        "get",
        "llminferenceservice",
        service_name,
        "-n",
        args.namespace,
        "-o",
        "jsonpath={.status.conditions[?(@.type=='Ready')].status}",
        log_stdout=False,
    )

    ready_status = result.stdout.strip()
    ctx.is_ready = ready_status == "True"

    if ctx.is_ready:
        return f"LLMInferenceService {service_name} status: Ready"
    else:
        return f"LLMInferenceService {service_name} status: Not Ready"


@task
def query_service_message(args, ctx):
    """Query detailed message from LLMInferenceService"""

    service_name = ctx.inference_service_name

    # Query the Ready condition details
    result = oc(
        "get",
        "llminferenceservice",
        service_name,
        "-n",
        args.namespace,
        "-o",
        "jsonpath={.status.conditions[?(@.type=='Ready')]}",
        log_stdout=False,
    )

    if result.stdout.strip():
        try:
            import json

            condition = json.loads(result.stdout)
            reason = condition.get("reason", "Unknown")
            message = condition.get("message", "No message")

            if not ctx.is_ready:
                return f"Not ready - Reason: {reason}, Message: {message}"
            else:
                return "Ready - Service is operational"
        except (json.JSONDecodeError, KeyError) as e:
            return f"Failed to parse Ready condition: {e}"
    else:
        return "No Ready condition found in status"


@retry(attempts=999999, delay=30, backoff=1.0)
@task
def wait_pods_scheduled(args, ctx):
    """Wait for all pods to be scheduled (optional task)"""

    # Check if this task is enabled
    if not args.wait_pods_scheduled:
        return "Pod scheduling wait disabled by parameter"

    service_name = ctx.inference_service_name

    # Get pod status using plain text output
    result = oc(
        "get",
        "pods",
        "-l",
        ctx.selector,
        "-n",
        args.namespace,
        "--no-headers",
        check=False,
        log_stdout=False,
    )

    if not result.stdout.strip():
        return False, "No pods found for the service yet"

    # Keep waiting if any pod is Pending
    if "Pending" in result.stdout:
        return False, "Waiting for pods to exit Pending state"

    return f"All pods for {service_name} are scheduled successfully"


@retry(attempts=90, delay=10, backoff=1.0)
@task
def wait_service_ready(args, ctx):
    """Wait for LLMInferenceService to be ready"""

    service_name = ctx.inference_service_name

    # Query the current status and show diagnostic info
    result = oc(
        "get",
        "llminferenceservice",
        service_name,
        "-n",
        args.namespace,
        "-o",
        "jsonpath={.status.conditions[?(@.type=='Ready')]}",
        log_stdout=True,
    )

    # Also show pod status for debugging
    oc(
        "get",
        "pods",
        "-l",
        ctx.selector,
        "-n",
        args.namespace,
        log_stdout=True,  # Show pod status in logs
    )

    if result.stdout.strip():
        try:
            import json

            condition = json.loads(result.stdout)
            status = condition.get("status", "Unknown")
            reason = condition.get("reason", "Unknown")
            message = condition.get("message", "No message")

            if status == "True":
                return f"LLMInferenceService {service_name} is ready"
            else:
                return (
                    False,
                    f"Service not ready - Status: {status}, Reason: {reason}, Message: {message}",
                )

        except (json.JSONDecodeError, KeyError) as e:
            return (False, f"Failed to parse Ready condition: {e}")
    else:
        return (False, f"No Ready condition found in status for {service_name}")


@retry(attempts=90, delay=10, backoff=1.0)
@task
def resolve_endpoint_task(args, ctx):
    """Resolve the gateway endpoint URL"""

    endpoint_url = try_resolve_endpoint_url(
        namespace=args.namespace,
        inference_service_name=ctx.inference_service_name,
        gateway_status_address_name=args.gateway_status_address_name,
    )
    if endpoint_url:
        ctx.endpoint_url = endpoint_url
        write_text(args.artifact_dir / "artifacts" / "endpoint.url", f"{endpoint_url}\n")
        return f"Endpoint resolved: {endpoint_url}"
    return False, "No endpoint URL available"


def try_resolve_endpoint_url(
    *, namespace: str, inference_service_name: str, gateway_status_address_name: str | None
) -> str | None:
    payload = oc_get_json("llminferenceservice", name=inference_service_name, namespace=namespace)

    for address in payload.get("status", {}).get("addresses", []):
        # When gateway_status_address_name is None, return the first address with a URL and append port 8000
        if gateway_status_address_name is None:
            if address.get("url"):
                url = address["url"]
                # Append port 8000 when not using gateway if no port is already specified
                if ":" not in url.split("/")[-1]:  # Check if no port in the hostname part
                    url = f"{url}:8000"
                return url
        # Otherwise, match by name
        elif address.get("name") == gateway_status_address_name and address.get("url"):
            return address["url"]
    return None


if __name__ == "__main__":
    run.main()
