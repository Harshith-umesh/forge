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
) -> dict[str, Any]:
    """Render an llm_d-owned LLMInferenceService manifest from concrete runtime inputs."""
    template_path = Path(config_dir) / inference_service["template"]
    manifest = _load_yaml(template_path)

    # Check if this is a P/D deployment
    is_pd_deployment = "pd_config" in deployment_profile

    name = inference_service["name"]
    manifest["metadata"]["name"] = name
    manifest["metadata"]["namespace"] = namespace
    manifest["metadata"].setdefault("labels", {})
    manifest["metadata"]["labels"].update(
        {
            "app.kubernetes.io/managed-by": "forge",
            "forge.openshift.io/project": "llm_d",
        }
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
            manifest, deployment_profile, deployment_profile_name
        )
    else:
        rendered_manifest = _render_standard_deployment(
            manifest, deployment_profile, deployment_profile_name
        )

    # Apply Kueue configuration if enabled
    _apply_kueue_configuration(rendered_manifest)

    return rendered_manifest


def _build_serving_resources(deployment_profile: dict[str, Any]) -> dict[str, Any]:
    tensor_parallelism = str(deployment_profile["tensor_parallelism"])
    profile_resources = deployment_profile.get("resources", {})
    rendered_resources: dict[str, Any] = {}

    for bound in ("requests", "limits"):
        source = profile_resources.get(bound, {})
        rendered_bound = {"nvidia.com/gpu": tensor_parallelism}
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


def _render_standard_deployment(
    manifest: dict[str, Any],
    deployment_profile: dict[str, Any],
    deployment_profile_name: str | None = None,
) -> dict[str, Any]:
    """Render standard (non-P/D) deployment configuration."""
    # Check if this is intelligent routing (scheduler_manifest exists)
    scheduler = deployment_profile.get("scheduler")
    has_scheduler_manifest = "scheduler_manifest" in deployment_profile
    is_intelligent_routing = scheduler is not None and has_scheduler_manifest

    # Update service name for intelligent routing or use_kserve_defaults
    current_name = manifest["metadata"]["name"]

    manifest["metadata"]["name"] = f"llm-d-{deployment_profile_name}"

    manifest["spec"]["replicas"] = deployment_profile["replicas"]

    serving_container = manifest["spec"]["template"]["containers"][0]
    serving_container["resources"] = _build_serving_resources(deployment_profile)
    if deployment_profile.get("serving_image"):
        serving_container["image"] = deployment_profile["serving_image"]

    # Configure VLLM args based on deployment type and use_kserve_defaults flag
    tensor_parallelism = str(deployment_profile["tensor_parallelism"])
    use_kserve_defaults = deployment_profile.get("use_kserve_defaults", False)

    if use_kserve_defaults:
        # Use VLLM_ADDITIONAL_ARGS environment variable when use_kserve_defaults is set
        vllm_args = _build_vllm_args(deployment_profile.get("vllm_args", {}))
        if not _has_cli_arg(vllm_args, "tensor-parallel-size"):
            vllm_args.append(f"--tensor-parallel-size={tensor_parallelism}")

        vllm_additional_args = " ".join(vllm_args)

        # Add environment variable (don't set generic env vars or args)
        if "env" not in serving_container:
            serving_container["env"] = []
        serving_container["env"].append(
            {"name": "VLLM_ADDITIONAL_ARGS", "value": vllm_additional_args}
        )
    elif is_intelligent_routing:
        # Use environment variables for intelligent routing
        vllm_args_list = [
            f"--tensor-parallel-size={tensor_parallelism}",
            "--disable-uvicorn-access-log",
            "--enable-prefix-caching",
            "--uvicorn-log-level=debug",
            "--trust-remote-code",
            "--gpu-memory-utilization=0.92",
            "--max-model-len 40960",
        ]
        vllm_additional_args = " ".join(vllm_args_list)

        # Add or update environment variables
        if "env" not in serving_container:
            serving_container["env"] = []
        serving_container["env"].append(
            {"name": "VLLM_ADDITIONAL_ARGS", "value": vllm_additional_args}
        )
    else:
        # Use CLI args for basic deployments
        vllm_args = _build_vllm_args(deployment_profile.get("vllm_args", {}))
        if not _has_cli_arg(vllm_args, "tensor-parallel-size"):
            vllm_args.append(f"--tensor-parallel-size={tensor_parallelism}")
        if vllm_args:
            serving_container["args"] = vllm_args

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

        # Enhance scheduler for intelligent routing
        if is_intelligent_routing:
            # Ensure template structure exists
            if "template" not in manifest["spec"]["router"]["scheduler"]:
                manifest["spec"]["router"]["scheduler"]["template"] = {
                    "containers": [{"name": "main"}]
                }

            scheduler_template = manifest["spec"]["router"]["scheduler"]["template"]

            # Add nodeSelector and serviceAccountName (if not already set by runtime_config.py)
            if "nodeSelector" not in scheduler_template:
                scheduler_template["nodeSelector"] = {
                    "nvidia.com/gpu.deploy.container-toolkit": "true"
                }
            if "serviceAccountName" not in scheduler_template:
                scheduler_template["serviceAccountName"] = "llm-d-privileged"

    return manifest


def _render_pd_deployment(
    manifest: dict[str, Any],
    deployment_profile: dict[str, Any],
    deployment_profile_name: str | None = None,
) -> dict[str, Any]:
    """Render P/D (Prefill/Decode) deployment configuration."""

    # Update service name to include deployment profile for P/D
    current_name = manifest["metadata"]["name"]
    if current_name == "llm-d" and deployment_profile_name:  # Only modify if it's the default name
        manifest["metadata"]["name"] = f"llm-d-{deployment_profile_name}"

    # Set main replicas to 2 for P/D
    manifest["spec"]["replicas"] = 2

    # Configure prefill section
    manifest["spec"]["prefill"] = {
        "replicas": 2,
        "template": _build_pd_pod_template(deployment_profile, is_prefill=True),
    }

    # Configure main template (decode)
    manifest["spec"]["template"] = _build_pd_pod_template(deployment_profile, is_prefill=False)

    # Simplified router for P/D
    manifest["spec"]["router"] = {
        "scheduler": {"template": {"containers": [{"name": "main"}]}},
        "route": {},
        "gateway": {},
    }

    return manifest


def _build_pd_pod_template(
    deployment_profile: dict[str, Any], is_prefill: bool = False
) -> dict[str, Any]:
    """Build pod template for P/D deployment."""
    tensor_parallelism = str(deployment_profile["tensor_parallelism"])
    use_kserve_defaults = deployment_profile.get("use_kserve_defaults", False)

    # Build VLLM environment variable configuration
    if use_kserve_defaults:
        # Use deployment profile's vllm_args when use_kserve_defaults is set
        vllm_args = _build_vllm_args(deployment_profile.get("vllm_args", {}))
        if not _has_cli_arg(vllm_args, "tensor-parallel-size"):
            vllm_args.append(f"--tensor-parallel-size={tensor_parallelism}")
        vllm_additional_args = " ".join(vllm_args)
    else:
        # Use P/D-specific configuration when use_kserve_defaults is false
        if is_prefill:
            # Prefill configuration
            vllm_args_list = [
                "--disable-uvicorn-access-log",
                "--block-size 128",
                '--kv-transfer-config \'{"kv_connector":"NixlConnector", "kv_role":"kv_both"}\'',
                f"--tensor-parallel-size={tensor_parallelism}",
                "--disable-uvicorn-access-log",  # Appears twice in prefill
                "--enable-prefix-caching",
                "--uvicorn-log-level=debug",
                "--trust-remote-code",
                "--gpu-memory-utilization=0.92",
                "--max-model-len=40960",
            ]
        else:
            # Decode configuration
            vllm_args_list = [
                "--block-size 128",
                '--kv-transfer-config \'{"kv_connector":"NixlConnector", "kv_role":"kv_both"}\'',
                f"--tensor-parallel-size={tensor_parallelism}",
                "--disable-uvicorn-access-log",
                "--enable-prefix-caching",
                "--uvicorn-log-level=debug",
                "--trust-remote-code",
                "--gpu-memory-utilization=0.92",
                "--max-model-len 40960",  # Space instead of equals for decode
            ]
        vllm_additional_args = " ".join(vllm_args_list)

    container = {
        "name": "main",
        "env": [
            {"name": "VLLM_ADDITIONAL_ARGS", "value": vllm_additional_args},
            {
                "name": "VLLM_NIXL_SIDE_CHANNEL_HOST",
                "valueFrom": {"fieldRef": {"apiVersion": "v1", "fieldPath": "status.podIP"}},
            },
            {"name": "UCX_IB_GID_INDEX", "value": "3"},
        ],
        "livenessProbe": {
            "failureThreshold": 1000,
            "httpGet": {"path": "/health", "port": 8000, "scheme": "HTTPS"},
            "initialDelaySeconds": 900,
            "periodSeconds": 60,
            "timeoutSeconds": 60,
        },
        "readinessProbe": {
            "failureThreshold": 10000,
            "httpGet": {"path": "/health", "port": 8000, "scheme": "HTTPS"},
            "initialDelaySeconds": 60,
            "periodSeconds": 30,
            "successThreshold": 1,
            "timeoutSeconds": 30,
        },
        "resources": {
            "limits": {"dra.llm-d.io/gpu-nic-pair": tensor_parallelism},
            "requests": {
                "cpu": "4",
                "dra.llm-d.io/gpu-nic-pair": tensor_parallelism,
                "memory": "64Gi",
            },
        },
        "securityContext": {"capabilities": {"add": ["IPC_LOCK", "SYS_RAWIO"]}},
        "startupProbe": {
            "failureThreshold": 150,
            "httpGet": {"path": "/health", "port": 8000, "scheme": "HTTPS"},
            "periodSeconds": 10,
            "successThreshold": 1,
            "timeoutSeconds": 1,
        },
    }

    template = {"serviceAccountName": "llm-d-privileged", "containers": [container]}

    # Add tolerations only to main template (decode), not prefill
    if not is_prefill:
        template["tolerations"] = [
            {"effect": "NoSchedule", "key": "nvidia.com/gpu", "operator": "Exists"}
        ]

    return template


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

    # Apply Kueue annotations to router scheduler pod template if it exists
    if (
        "spec" in manifest
        and "router" in manifest["spec"]
        and "scheduler" in manifest["spec"]["router"]
    ):
        scheduler_template = manifest["spec"]["router"]["scheduler"].get("template", {})

        # Ensure metadata exists in scheduler template
        if "metadata" not in scheduler_template:
            scheduler_template["metadata"] = {}
        if "annotations" not in scheduler_template["metadata"]:
            scheduler_template["metadata"]["annotations"] = {}

        # Apply the same Kueue annotations to the scheduler pod template
        for annotation_key, annotation_value in kueue_annotations.items():
            full_annotation_key = f"{kueue_prefix}{annotation_key}"
            scheduler_template["metadata"]["annotations"][full_annotation_key] = annotation_value

        # Update the scheduler template back to the data structure
        manifest["spec"]["router"]["scheduler"]["template"] = scheduler_template

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
