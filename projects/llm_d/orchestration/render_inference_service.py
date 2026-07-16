from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

from projects.core.dsl.utils import slugify_identifier, truncate_k8s_name


def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


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

    cache_spec = None
    if model_cache.get("enabled", False) and not source_uri.startswith(("pvc://", "pvc+hf://")):
        pvc_defaults = model_cache["pvc"]
        pvc_prefix = pvc_defaults["name_prefix"]
        cache_key = hashlib.sha256(source_uri.encode("utf-8")).hexdigest()[:10]
        pvc_name = truncate_k8s_name(
            f"{pvc_prefix}-{slugify_identifier(model_slug, max_length=32)}-{cache_key}"
        )
        model_path = pvc_defaults["model_directory_name"]

        cache_spec = {
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

    manifest["spec"]["model"]["uri"] = cache_spec["model_uri"] if cache_spec else source_uri
    manifest["spec"]["model"]["name"] = model_slug

    if is_pd_deployment:
        return _render_pd_deployment(manifest, deployment_profile, deployment_profile_name)
    else:
        return _render_standard_deployment(manifest, deployment_profile)


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
    manifest: dict[str, Any], deployment_profile: dict[str, Any]
) -> dict[str, Any]:
    """Render standard (non-P/D) deployment configuration."""
    manifest["spec"]["replicas"] = deployment_profile["replicas"]

    serving_container = manifest["spec"]["template"]["containers"][0]
    serving_container["resources"] = _build_serving_resources(deployment_profile)
    if deployment_profile.get("serving_image"):
        serving_container["image"] = deployment_profile["serving_image"]
    vllm_args = _build_vllm_args(deployment_profile.get("vllm_args", {}))
    tensor_parallelism = str(deployment_profile["tensor_parallelism"])
    if not _has_cli_arg(vllm_args, "tensor-parallel-size"):
        vllm_args.append(f"--tensor-parallel-size={tensor_parallelism}")
    if vllm_args:
        serving_container["args"] = vllm_args

    scheduler = deployment_profile.get("scheduler", {})
    if scheduler is None:
        manifest["spec"]["router"].pop("scheduler", None)
    else:
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
) -> dict[str, Any]:
    """Render P/D (Prefill/Decode) deployment configuration."""
    pd_config = deployment_profile["pd_config"]

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

    # Build VLLM environment variable configuration specific to prefill/decode
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
