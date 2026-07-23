from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

import yaml

from projects.core.dsl.utils import slugify_identifier, truncate_k8s_name
from projects.core.library import config


def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _create_model_cache_spec(
    model_cache: dict[str, Any],
    source_uri: str,
    source_scheme: str,
    model_slug: str,
    namespace: str,
) -> dict[str, Any] | None:
    """Create model cache specification if caching is enabled and applicable."""
    if not model_cache.get("enabled", False) or source_uri.startswith(("pvc://", "pvc+hf://")):
        return None

    pvc_defaults = model_cache["pvc"]
    pvc_prefix = pvc_defaults["name_prefix"]
    cache_key = hashlib.sha256(source_uri.encode("utf-8")).hexdigest()[:10]
    pvc_name = truncate_k8s_name(
        f"{pvc_prefix}-{slugify_identifier(model_slug, max_length=32)}-{cache_key}"
    )
    model_path = pvc_defaults["model_directory_name"]

    return {
        "source_uri": source_uri,
        "source_scheme": source_scheme,
        "cache_key": cache_key,
        "namespace": namespace,
        "pvc_name": pvc_name,
        "pvc_size": pvc_defaults["size"],
        "access_mode": pvc_defaults["access_mode"],
        "storage_class_name": pvc_defaults.get("storage_class_name"),
        "model_path": model_path,
        "model_uri": f"pvc://{pvc_name}/{model_path}",
        "marker_filename": model_cache["marker_filename"],
        "marker_path": f"/cache/{model_path}/{model_cache['marker_filename']}",
        "download_job_name": truncate_k8s_name(f"{pvc_name}-download"),
        "hf_token_secret_name": model_cache["hf"].get("token_secret_name"),
        "hf_token_secret_key": model_cache["hf"].get("token_secret_key"),
    }


def render_inference_service_from_parts(
    *,
    config_dir: str | Path,
    namespace: str,
    inference_service: dict[str, Any],
    model_name: str,
    model_slug: str,
    deployment_profile: dict[str, Any],
    model_cache: dict[str, Any],
    deployment_profile_name: str | None = None,
    workload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Render an llm_d-owned LLMInferenceService manifest from concrete runtime inputs."""
    template_path = Path(config_dir) / inference_service["template"]
    manifest = _load_yaml(template_path)

    # Check if this is a P/D deployment
    is_pd_deployment = "prefill" in deployment_profile

    name = inference_service["name"]
    if deployment_profile_name:
        name = f"{name}-{deployment_profile_name}"
    # Normalize name to be Kubernetes compliant and limit to 25 characters
    name = slugify_identifier(name)
    name = truncate_k8s_name(name, max_length=25)
    manifest["metadata"]["name"] = name
    manifest["metadata"]["namespace"] = namespace
    manifest["metadata"].setdefault("labels", {})
    manifest["metadata"]["labels"].update(
        config.project.get_config("deployments.defaults.labels") or {}
    )

    # Add deployment profile name as annotation for testing
    manifest["metadata"].setdefault("annotations", {})
    if deployment_profile_name:
        manifest["metadata"]["annotations"]["forge.openshift.io/deployment-profile"] = (
            deployment_profile_name
        )

    if model_name.startswith("oci://"):
        source_uri = model_name
        source_scheme = "oci"
    elif model_name.startswith("hf://"):
        source_uri = model_name
        source_scheme = "hf"
    else:
        source_uri = f"hf://{model_name}"
        source_scheme = "hf"

    cache_spec = _create_model_cache_spec(
        model_cache=model_cache,
        source_uri=source_uri,
        source_scheme=source_scheme,
        model_slug=model_slug,
        namespace=namespace,
    )

    manifest["spec"]["model"]["uri"] = cache_spec["model_uri"] if cache_spec else source_uri
    manifest["spec"]["model"]["name"] = model_slug

    if is_pd_deployment:
        rendered_manifest = _render_pd_deployment(
            manifest, deployment_profile, deployment_profile_name, workload
        )
    else:
        rendered_manifest = _render_standard_deployment(
            manifest, deployment_profile, deployment_profile_name, workload
        )

    # Apply Kueue configuration if enabled
    _apply_kueue_configuration(rendered_manifest)

    return rendered_manifest


def handle_pd_resources(
    base_resources: dict[str, Any],
    deployment_profile: dict[str, Any],
    is_prefill: bool = False,
) -> None:
    """Handle P/D resource configuration, including DRA support.

    Args:
        base_resources: Base resources dict to modify in-place
        deployment_profile: Deployment profile configuration
        is_prefill: Whether this is for a prefill container
    """
    pd_resources = config.project.get_config("deployments.pd.resources")

    if pd_resources == "composite.dra.io/gpu-nic-pair":
        # Handle DRA (Dynamic Resource Allocation) case
        if is_prefill:
            # For prefill pods, use prefill tensor parallelism
            tensor_parallelism = deployment_profile["prefill"]["tensor_parallelism"]
        else:
            # For decode pods, use decode tensor parallelism
            tensor_parallelism = deployment_profile["decode"]["tensor_parallelism"]

        # Override GPU resources for DRA
        for bound in ("requests", "limits"):
            if bound not in base_resources:
                base_resources[bound] = {}
            # Set nvidia.com/gpu to 0 and add composite DRA resource
            base_resources[bound]["nvidia.com/gpu"] = "0"
            base_resources[bound][pd_resources] = str(tensor_parallelism)
    elif isinstance(pd_resources, dict):
        # Normal case: pd_resources is a dictionary to merge
        for bound in ("requests", "limits"):
            if bound not in base_resources:
                base_resources[bound] = {}
            base_resources[bound].update(pd_resources)
    elif pd_resources is not None:
        # Handle unexpected pd_resources type
        raise ValueError(
            f"Unexpected type for deployments.pd.resources: {type(pd_resources)}, value: {pd_resources}"
        )
    # If pd_resources is None, do nothing


def _build_serving_resources(deployment_profile: dict[str, Any]) -> dict[str, Any]:
    tensor_parallelism = deployment_profile["tensor_parallelism"]
    profile_resources = deployment_profile.get("resources", {})
    rendered_resources: dict[str, Any] = {}

    for bound in ("requests", "limits"):
        source = profile_resources.get(bound, {})
        rendered_bound = {"nvidia.com/gpu": str(tensor_parallelism)}
        for resource_name in ("cpu", "memory"):
            value = source.get(resource_name)
            if value not in (None, ""):
                rendered_bound[resource_name] = value
        rendered_resources[bound] = rendered_bound

    return rendered_resources


def _build_vllm_args(vllm_args: dict[str, Any] | list[str]) -> list[str]:
    if isinstance(vllm_args, list):
        return [str(arg) for arg in vllm_args]

    rendered_args: list[str] = []
    for key, value in vllm_args.items():
        cli_key = key.replace("_", "-")
        if isinstance(value, bool):
            if value:
                rendered_args.append(f"--{cli_key}")
            continue
        rendered_args.append(f"--{cli_key}={value}")
    return rendered_args


def _has_cli_arg(args: list[str], option_name: str) -> bool:
    prefix = f"--{option_name}="
    bare = f"--{option_name}"
    return any(arg == bare or arg.startswith(prefix) for arg in args)


def _build_vllm_additional_args(
    deployment_profile: dict[str, Any],
    workload: dict[str, Any] | None = None,
) -> str:
    """Build VLLM_ADDITIONAL_ARGS string based on deployment profile configuration.

    Args:
        deployment_profile: The deployment profile configuration
        workload: The workload configuration (merged benchmark config)

    Returns:
        String suitable for VLLM_ADDITIONAL_ARGS environment variable
    """

    vllm_extra = deployment_profile.get("vllm_extra", {})
    vllm_deploy_args = _build_vllm_args(vllm_extra.get("args", {}))

    # Add workload vllm_args if available
    if workload and "vllm_args" in workload:
        workload_vllm_args = _build_vllm_args(workload["vllm_args"])
        vllm_deploy_args.extend(workload_vllm_args)

    return " ".join(vllm_deploy_args)


def _render_standard_deployment(
    manifest: dict[str, Any],
    deployment_profile: dict[str, Any],
    deployment_profile_name: str | None = None,
    workload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Render standard (non-P/D) deployment configuration."""
    # Check if this is intelligent routing (scheduler_manifest exists)
    scheduler = deployment_profile.get("scheduler")
    has_scheduler_manifest = "scheduler_manifest" in deployment_profile

    name = f"llm-d-{deployment_profile_name}"
    name = slugify_identifier(name)
    name = truncate_k8s_name(name, max_length=25)
    manifest["metadata"]["name"] = name
    manifest["spec"]["replicas"] = deployment_profile["replicas"]

    serving_container = manifest["spec"]["template"]["containers"][0]
    serving_container["resources"] = _build_serving_resources(deployment_profile)
    if deployment_profile.get("serving_image"):
        serving_container["image"] = deployment_profile["serving_image"]

    vllm_additional_args = _build_vllm_additional_args(deployment_profile, workload)

    # Add environment variable (don't set generic env vars or args)
    if "env" not in serving_container:
        serving_container["env"] = []

    serving_container["env"].append({"name": "VLLM_ADDITIONAL_ARGS", "value": vllm_additional_args})

    # Configure router/scheduler
    has_scheduler_key = "scheduler" in deployment_profile
    is_simple_deployment = not has_scheduler_key and not has_scheduler_manifest

    if is_simple_deployment:
        # Simple deployments (no scheduler key, no scheduler_manifest) have no router section at all
        manifest["spec"].pop("router", None)
    elif scheduler is None:
        # Some deployments might have router but no scheduler
        manifest["spec"]["router"].pop("scheduler", None)
    else:
        # Configure scheduler for intelligent routing
        manifest["spec"]["router"]["scheduler"] = copy.deepcopy(scheduler)
        if deployment_profile.get("router_image"):
            manifest["spec"]["router"]["scheduler"]["template"]["containers"][0]["image"] = (
                deployment_profile["router_image"]
            )

    return manifest


def _render_pd_deployment(
    manifest: dict[str, Any],
    deployment_profile: dict[str, Any],
    deployment_profile_name: str | None = None,
    workload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Render P/D (Prefill/Decode) deployment configuration."""
    from .runtime_config import get_decode_pod_count, get_prefill_pod_count

    # Set manifest name with deployment profile
    name = f"llm-d-{deployment_profile_name}"
    name = slugify_identifier(name)
    name = truncate_k8s_name(name, max_length=25)
    manifest["metadata"]["name"] = name

    # Configure prefill section
    manifest["spec"]["prefill"] = {
        "replicas": get_prefill_pod_count(),
        "template": _build_pd_pod_template(
            deployment_profile, deployment_profile_name, is_prefill=True, workload=workload
        ),
    }

    # Configure main template (decode)
    manifest["spec"]["replicas"] = get_decode_pod_count()
    manifest["spec"]["template"] = _build_pd_pod_template(
        deployment_profile, deployment_profile_name, is_prefill=False, workload=workload
    )

    return manifest


def _build_pd_pod_template(
    deployment_profile: dict[str, Any],
    deployment_profile_name: str | None = None,
    is_prefill: bool = False,
    workload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build pod template for P/D deployment."""

    # Get P/D extra configuration from deployments config

    pd_vllm_extra = config.project.get_config("deployments.pd.vllm_extra")

    # Build VLLM args with correct tensor parallelism

    if is_prefill and deployment_profile_name:
        # For prefill pods, use prefill tensor parallelism
        from .runtime_config import _extract_value_from_profile_name

        try:
            prefill_tp = _extract_value_from_profile_name(
                deployment_profile_name, "prefill_tensor_parallelism"
            )
            # Create modified profile with prefill tensor parallelism
            prefill_profile = copy.deepcopy(deployment_profile)
            prefill_profile["tensor_parallelism"] = prefill_tp
            base_vllm_args = _build_vllm_additional_args(prefill_profile, workload)
        except ValueError:
            # Fallback to main tensor parallelism if extraction fails
            base_vllm_args = _build_vllm_additional_args(deployment_profile, workload)
    else:
        # For decode pods, use main tensor parallelism
        base_vllm_args = _build_vllm_additional_args(deployment_profile, workload)

    # Add P/D extra args

    pd_extra_args = pd_vllm_extra.get("args", [])
    all_vllm_args = base_vllm_args.split() + pd_extra_args

    vllm_additional_args = " ".join(all_vllm_args)

    # Build base environment variables
    base_env = [
        {"name": "VLLM_ADDITIONAL_ARGS", "value": vllm_additional_args},
    ]

    # Add P/D extra environment variables
    pd_extra_env = pd_vllm_extra.get("env", [])
    all_env = base_env + copy.deepcopy(pd_extra_env)

    # Build base resources with correct tensor parallelism
    if is_prefill and deployment_profile_name:
        # For prefill pods, use prefill tensor parallelism
        from .runtime_config import _extract_value_from_profile_name

        try:
            prefill_tp = _extract_value_from_profile_name(
                deployment_profile_name, "prefill_tensor_parallelism"
            )
            # Create modified profile with prefill tensor parallelism
            prefill_profile = copy.deepcopy(deployment_profile)
            prefill_profile["tensor_parallelism"] = prefill_tp
            base_resources = _build_serving_resources(prefill_profile)
        except ValueError:
            # Fallback to main tensor parallelism if extraction fails
            base_resources = _build_serving_resources(deployment_profile)
    else:
        # For decode pods, use main tensor parallelism
        base_resources = _build_serving_resources(deployment_profile)

    # Handle P/D extra resources
    handle_pd_resources(base_resources, deployment_profile, is_prefill)

    # Build container configuration
    container = {
        "name": "main",
        "resources": base_resources,
        "env": all_env,
    }

    # Add serving image if specified
    if deployment_profile.get("serving_image"):
        container["image"] = deployment_profile["serving_image"]

    return {"containers": [container]}


def _apply_kueue_configuration(manifest: dict[str, Any]) -> None:
    """Apply Kueue annotations and labels to the ISVC manifest.

    Based on the implementation from topsail's test_llmd.py.
    Can be enabled by setting runtime.kserve_use_kueue config.
    """
    # Check if kueue annotations should be enabled
    enable_kueue = config.project.get_config("runtime.kueue.enabled")

    if not enable_kueue:
        return

    # Configure kueue settings
    queue_name = config.project.get_config("runtime.kueue.queue_name")
    kueue_config = {
        "enabled": True,
        "prefix": "kueue.x-k8s.io/",
        "labels": {"queue-name": queue_name},
        "annotations": {"queue-name": queue_name},
    }

    # Get prefix for kueue labels/annotations
    kueue_prefix = kueue_config.get("prefix", "kueue.x-k8s.io/")

    # Ensure metadata sections exist
    if "metadata" not in manifest:
        manifest["metadata"] = {}
    if "labels" not in manifest["metadata"]:
        manifest["metadata"]["labels"] = {}
    if "annotations" not in manifest["metadata"]:
        manifest["metadata"]["annotations"] = {}

    # Apply Kueue labels
    kueue_labels = kueue_config.get("labels", {})
    for label_key, label_value in kueue_labels.items():
        full_label_key = f"{kueue_prefix}{label_key}"
        manifest["metadata"]["labels"][full_label_key] = label_value

    # Apply Kueue annotations
    kueue_annotations = kueue_config.get("annotations", {})
    for annotation_key, annotation_value in kueue_annotations.items():
        full_annotation_key = f"{kueue_prefix}{annotation_key}"
        manifest["metadata"]["annotations"][full_annotation_key] = annotation_value

    # Apply Kueue annotations to router scheduler if it exists
    if (
        "spec" in manifest
        and "router" in manifest["spec"]
        and "scheduler" in manifest["spec"]["router"]
    ):
        scheduler = manifest["spec"]["router"]["scheduler"]

        # Ensure annotations and labels exist on scheduler
        if "annotations" not in scheduler:
            scheduler["annotations"] = {}
        if "labels" not in scheduler:
            scheduler["labels"] = {}

        # Apply the same Kueue annotations to the scheduler
        for annotation_key, annotation_value in kueue_annotations.items():
            full_annotation_key = f"{kueue_prefix}{annotation_key}"
            scheduler["annotations"][full_annotation_key] = annotation_value

        # Apply the same Kueue labels to the scheduler
        for label_key, label_value in kueue_labels.items():
            full_label_key = f"{kueue_prefix}{label_key}"
            scheduler["labels"][full_label_key] = label_value

    # Calculate pod group total count: 1 scheduler + number of replicas
    replicas = manifest.get("spec", {}).get("replicas", 1)

    # For P/D deployments, we need to account for prefill replicas too
    prefill_replicas = 0
    if "spec" in manifest and "prefill" in manifest["spec"]:
        prefill_replicas = manifest["spec"]["prefill"].get("replicas", 0)

    # Total: main replicas + prefill replicas + (1 scheduler if router exists)
    has_scheduler = (
        "spec" in manifest
        and "router" in manifest["spec"]
        and "scheduler" in manifest["spec"]["router"]
    )

    scheduler_count = 1 if has_scheduler else 0
    pod_group_total_count = replicas + prefill_replicas + scheduler_count

    manifest["metadata"]["annotations"][f"{kueue_prefix}pod-group-total-count"] = str(
        pod_group_total_count
    )

    # Add required pod-group-name label using the ISVC name
    pod_group_name = manifest["metadata"]["name"]
    manifest["metadata"]["labels"][f"{kueue_prefix}pod-group-name"] = pod_group_name

    # Also add required Kueue annotations/labels to scheduler if it exists
    if has_scheduler:
        scheduler = manifest["spec"]["router"]["scheduler"]

        # Ensure annotations and labels exist on scheduler
        if "annotations" not in scheduler:
            scheduler["annotations"] = {}
        if "labels" not in scheduler:
            scheduler["labels"] = {}

        # Add the same pod-group annotations and labels to scheduler
        scheduler["annotations"][f"{kueue_prefix}pod-group-total-count"] = str(
            pod_group_total_count
        )
        scheduler["labels"][f"{kueue_prefix}pod-group-name"] = pod_group_name
