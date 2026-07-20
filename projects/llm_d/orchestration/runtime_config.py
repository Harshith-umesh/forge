from __future__ import annotations

import ast
import copy
import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any

import yaml

from projects.core.dsl.utils import slugify_identifier, truncate_k8s_name
from projects.core.library import config, env, run

logger = logging.getLogger(__name__)
RUNTIME_DIR = Path(__file__).resolve().parent
PROJECT_DIR = RUNTIME_DIR.parent
ORCHESTRATION_DIR = PROJECT_DIR / "orchestration"
CONFIG_DIR = ORCHESTRATION_DIR


@dataclass(frozen=True)
class RunSpec:
    model_name: str
    model_slug: str
    deployment_profile_name: str
    deployment_profile_slug: str
    benchmark_key: str | None
    benchmark_slug: str | None
    namespace: str
    artifact_dirname: str
    namespace_is_managed: bool


def init() -> Path:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    env.init()
    run.init()
    if config.project is None:
        # Load llm_d project config when runtime is used without orchestration preparation.
        config.init(CONFIG_DIR)
    ensure_artifact_directories(env.ARTIFACT_DIR)
    return env.ARTIFACT_DIR


def ensure_artifact_directories(artifact_dir: Path) -> None:
    for relative in ("src", "artifacts", "artifacts/results"):
        (artifact_dir / relative).mkdir(parents=True, exist_ok=True)


def _get_runtime_value(key: str) -> Any:
    return config.project.get_config(f"runtime.{key}", None)


def _assert_no_legacy_model_key() -> None:
    legacy_model_key = _get_runtime_value("model_key")
    if legacy_model_key not in (None, ""):
        raise ValueError(
            "llm_d no longer supports runtime.model_key. "
            "Use runtime.model_name with a literal Hugging Face model name instead."
        )


def _normalize_string_or_list(value: Any, field_name: str) -> list[str]:
    """Normalize a value to a list of non-empty strings.

    Handles:
    - Lists: extracts string items
    - Bracket strings like "[a, b]": parses with ast.literal_eval
    - Plain strings: returns as single-item list
    - None/"": returns empty list
    """
    if value in (None, ""):
        return []

    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]

    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string or list of strings, got {type(value)}")

    value = value.strip()
    # Accept the "/var runtime.x: [a, b]" bracket form.
    # Try ast.literal_eval first for properly quoted lists like "['a', 'b']".
    # Fall back to manual comma-split for unquoted identifiers like "[a, b]".
    if value.startswith("[") and value.endswith("]"):
        try:
            parsed = ast.literal_eval(value)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except (ValueError, SyntaxError):
            # Fall back to simple comma-split for unquoted identifier lists
            items = [item.strip() for item in value[1:-1].split(",")]
            return [item for item in items if item]

    return [value] if value else []


def deep_merge(base: Any, override: Any) -> Any:
    if not isinstance(base, dict) or not isinstance(override, dict):
        return copy.deepcopy(override)

    merged = copy.deepcopy(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _extract_value_from_profile_name(profile_name: str, field_name: str) -> int:
    """Extract configuration values from profile names.

    Supports patterns:
    - simple-tp4-x4: tensor_parallelism=4, replicas=4
    - intelligentrouting-tp4-x4: tensor_parallelism=4, replicas=4
    - pd-d.x2-p.tp4-d.tp4-p.x1: prefill_pods=1, decode_pods=2, tensor_parallelism=4

    Args:
        profile_name: The deployment profile name
        field_name: The field to extract (tensor_parallelism, replicas, prefill_pods, decode_pods)

    Returns:
        Extracted integer value

    Raises:
        ValueError: If the pattern is not recognized or value cannot be extracted
    """
    # P/D pattern: pd-d.x2-p.tp4-d.tp4-p.x1
    if profile_name.startswith("pd-"):
        # P/D naming: pd-d.x{decode_pods}-p.tp{tensor_parallelism}-d.tp{decode_tensor_parallelism}-p.x{prefill_pods}
        # Extract different components
        if field_name == "tensor_parallelism":
            # Look for p.tp4 pattern (prefill tensor_parallelism)
            match = re.search(r"p\.tp(\d+)", profile_name)
            if match:
                return int(match.group(1))
            # Fallback: look for any tp pattern
            match = re.search(r"tp(\d+)", profile_name)
            if match:
                return int(match.group(1))
        elif field_name == "prefill_pods":
            # Look for p.x1 pattern
            match = re.search(r"p\.x(\d+)", profile_name)
            if match:
                return int(match.group(1))
        elif field_name == "decode_pods":
            # Look for d.x2 pattern
            match = re.search(r"d\.x(\d+)", profile_name)
            if match:
                return int(match.group(1))
    else:
        # Standard patterns: simple-tp4-x4, intelligentrouting-tp4-x4
        if field_name == "tensor_parallelism":
            # Look for tp4 pattern
            match = re.search(r"tp(\d+)", profile_name)
            if match:
                return int(match.group(1))
        elif field_name == "replicas":
            # Look for x4 pattern
            match = re.search(r"x(\d+)", profile_name)
            if match:
                return int(match.group(1))

    raise ValueError(f"Could not extract {field_name} from profile name: {profile_name}")


def _resolve_from_name_values(profile_name: str, profile_data: dict[str, Any]) -> dict[str, Any]:
    """Resolve FROM_NAME placeholders in profile configuration with values from profile name.

    Args:
        profile_name: The deployment profile name to extract values from
        profile_data: The profile configuration that may contain FROM_NAME placeholders

    Returns:
        Profile configuration with FROM_NAME placeholders resolved to actual values
    """
    resolved = copy.deepcopy(profile_data)

    def resolve_value(obj: Any, path: str = "") -> Any:
        if isinstance(obj, dict):
            result = {}
            for key, value in obj.items():
                current_path = f"{path}.{key}" if path else key
                result[key] = resolve_value(value, current_path)
            return result
        elif isinstance(obj, list):
            return [resolve_value(item, path) for item in obj]
        elif obj == "FROM_NAME":
            # Determine which field to extract based on the path
            field_name = path.split(".")[-1]  # Get the last part of the path
            try:
                return _extract_value_from_profile_name(profile_name, field_name)
            except ValueError as e:
                logger.warning(f"Failed to extract {field_name} from {profile_name}: {e}")
                return obj  # Return the original FROM_NAME if extraction fails
        else:
            return obj

    return resolve_value(resolved)


def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _load_scheduler_with_epp_config(
    config_dir: Path, scheduler_manifest: str, deployment_profile: dict[str, Any]
) -> dict[str, Any]:
    """Load scheduler configuration using router template with EPP config replacement.

    Uses configurable paths from runtime.router config.

    Args:
        config_dir: Configuration directory path
        scheduler_manifest: Path to EPP config file (e.g., "manifests/deployments/approximate-prefix-cache.yaml")
        deployment_profile: Resolved deployment profile for templating

    Returns:
        Scheduler data with EPP config content replacing placeholder
    """

    # Get configurable paths and placeholder
    router_template_file = template_config.get("runtime.router.template.file")
    router_placeholder = template_config.get("runtime.router.template.placeholder")
    epp_config_dir = router_config.get("runtime.router.template.epp")

    # Load the router template
    router_template_path = config_dir / router_template_file
    if not router_template_path.exists():
        # Fallback to legacy behavior if template doesn't exist
        raise FileNotFoundError(
            f"Router template not found at {router_template_path}"
        )

    # Load the EPP config content
    # Convert scheduler_manifest path to EPP config directory
    # e.g., "manifests/deployments/approximate-prefix-cache.yaml" -> "manifests/scheduler_config/approximate-prefix-cache.yaml"
    manifest_path = Path(scheduler_manifest)
    epp_config_filename = manifest_path.name
    epp_config_path = config_dir / epp_config_dir / epp_config_filename

    if not epp_config_path.exists():
        raise FileNotFoundError(
            f"EPP config not found at {epp_config_path}"
        )

    # Load both files
    router_template_data = _load_yaml(router_template_path)
    epp_config_data = _load_yaml(epp_config_path)

    # Replace placeholder in the parsed data structure (avoids YAML formatting issues)
    def replace_placeholder_in_structure(obj):
        if isinstance(obj, dict):
            return {key: replace_placeholder_in_structure(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [replace_placeholder_in_structure(item) for item in obj]
        elif isinstance(obj, str) and obj == router_placeholder:
            # Convert EPP config to YAML string for the --config-text argument
            return yaml.dump(epp_config_data, default_flow_style=False, sort_keys=False).strip()
        else:
            return obj

    # Perform the replacement in the structure
    replaced_data = replace_placeholder_in_structure(router_template_data)

    return replaced_data


def get_config_dir() -> Path:
    """Get the LLM-D configuration directory"""
    return ORCHESTRATION_DIR


def get_job_name() -> str:
    """Get the resolved job name"""
    job_name = _get_runtime_value("job_name")
    if job_name:
        return job_name

    deployment_profile = config.project.get_config("runtime.deployment_profile")
    return f"local-{deployment_profile}"


def get_platform_config() -> dict[str, Any]:
    """Get the normalized platform configuration"""
    return normalize_platform_config(copy.deepcopy(config.project.get_config("platform")))


def _derive_base_namespace() -> str:
    platform_data = get_platform_config()
    namespace_override = _get_runtime_value("namespace_override")
    namespace_config = platform_data["cluster"]["namespace"]
    default_namespace = namespace_config.get("name")

    if namespace_override:
        return namespace_override
    if default_namespace:
        return default_namespace

    return derive_namespace(
        get_job_name(),
        namespace_config["prefix"],
        namespace_config["max_length"],
    )


def get_namespace() -> str:
    """Get the resolved namespace for this execution"""
    return _derive_base_namespace()


_namespace_is_managed_override: bool | None = None


def get_namespace_is_managed() -> bool:
    """Check if namespace is managed (auto-derived) vs explicitly configured.

    Inside activate_run_spec(), returns the base managed state captured before
    the run-spec override was applied (otherwise the namespace_override set by
    activate_run_spec would always flip this to False).
    """
    if _namespace_is_managed_override is not None:
        return _namespace_is_managed_override

    namespace_override = _get_runtime_value("namespace_override")
    platform_data = get_platform_config()
    default_namespace = platform_data["cluster"]["namespace"].get("name")

    return namespace_override is None and default_namespace is None


def get_model_name() -> str:
    """Get the selected Hugging Face model name"""
    _assert_no_legacy_model_key()
    model_names = _normalize_string_or_list(_get_runtime_value("model_name"), "runtime.model_name")
    if len(model_names) != 1:
        raise ValueError(
            f"Expected exactly one runtime.model_name in the active llm_d run, got {model_names}"
        )
    return model_names[0]


def get_model_slug(model_name: str | None = None) -> str:
    return slugify_identifier(model_name or get_model_name(), max_length=32)


def get_model_uri(model_name: str | None = None) -> str:
    """Get the model URI, detecting scheme from model_name prefix.

    Supports:
    - Plain names (e.g., "meta-llama/Llama-3.1-8B") → "hf://{name}"
    - Full URIs (e.g., "oci://registry.../model:tag") → passed through
    """
    name = model_name or get_model_name()
    if name.startswith(("hf://", "oci://", "pvc://", "pvc+hf://")):
        return name
    return f"hf://{name}"


def get_served_model_name(model_name: str | None = None) -> str:
    return get_model_slug(model_name)


def get_model_cache_config() -> dict[str, Any]:
    """Get the model cache configuration"""
    return copy.deepcopy(config.project.get_config("model_cache"))


def get_benchmark_keys() -> list[str]:
    return _normalize_string_or_list(_get_runtime_value("benchmark_key"), "runtime.benchmark_key")


def _resolve_benchmark_config(benchmark_name: str) -> dict[str, Any]:
    benchmark = copy.deepcopy(
        config.project.get_config(f"workloads.benchmarks['{benchmark_name}']")
    )
    workload_defaults = copy.deepcopy(config.project.get_config("workloads"))

    default_keys = ("job_name", "image", "pvc_size", "timeout_seconds")
    for key in default_keys:
        if key in workload_defaults and key not in benchmark:
            benchmark[key] = workload_defaults[key]

    benchmark_args = benchmark.get("args", {})
    workload_args = workload_defaults.get("args", {})
    if workload_args:
        benchmark["args"] = deep_merge(workload_args, benchmark_args)

    return benchmark


def get_benchmark_config() -> dict[str, Any] | None:
    """Get the single active benchmark configuration if specified"""
    benchmark_keys = get_benchmark_keys()
    if not benchmark_keys:
        return None
    if len(benchmark_keys) != 1:
        raise ValueError(
            "Expected exactly one runtime.benchmark_key in the active llm_d run, "
            f"got {benchmark_keys}"
        )
    return _resolve_benchmark_config(benchmark_keys[0])


def get_benchmark_deployment_overrides() -> dict[str, Any]:
    """Return deployment overrides from the active benchmark config, if any."""
    benchmark = get_benchmark_config()
    if benchmark is None:
        return {}
    return benchmark.get("deployment_overrides", {})


def get_benchmark_job_name() -> str | None:
    """The k8s benchmark job name for the active run, or None when benchmarking is disabled."""
    benchmark = get_benchmark_config()
    if benchmark is None:
        return None
    return benchmark.get("job_name")


def get_deployment_profile_name() -> str:
    deployment_profiles = _normalize_string_or_list(
        _get_runtime_value("deployment_profile"),
        "runtime.deployment_profile",
    )
    if len(deployment_profiles) != 1:
        raise ValueError(
            "Expected exactly one runtime.deployment_profile in the active llm_d run, "
            f"got {deployment_profiles}"
        )
    return deployment_profiles[0]


def _resolve_template_profile(
    profile_name: str, deployment_config: dict[str, Any]
) -> dict[str, Any]:
    """Resolve a profile name to a template-based configuration if no direct match exists.

    Args:
        profile_name: The deployment profile name (e.g., "simple-tp4-x4")
        deployment_config: The full deployments configuration

    Returns:
        Template configuration to use, or raises ValueError if no template matches

    Raises:
        ValueError: If no template pattern matches the profile name
    """
    # Try to match profile name patterns to templates
    if profile_name.startswith("pd-"):
        template_name = "template__pd"
    elif "intelligentrouting" in profile_name:
        template_name = "template__intelligentrouting"
    elif profile_name.startswith("simple-") or re.search(r"-tp\d+-x\d+$", profile_name):
        template_name = "template__simple"
    else:
        raise ValueError(f"No template found for profile name pattern: {profile_name}")

    if template_name not in deployment_config:
        raise ValueError(f"Template {template_name} not found in deployment config")

    logger.info(f"Using template {template_name} for profile {profile_name}")
    return copy.deepcopy(deployment_config[template_name])


def get_deployment_profile() -> dict[str, Any]:
    profile_name = get_deployment_profile_name()
    deployment_config = copy.deepcopy(config.project.get_config("deployments"))

    # First try direct profile lookup
    if profile_name in deployment_config:
        profile = copy.deepcopy(deployment_config[profile_name])
    else:
        # Fall back to template-based resolution
        profile = _resolve_template_profile(profile_name, deployment_config)

    defaults = copy.deepcopy(deployment_config.get("defaults", {}))

    scheduler_manifest = profile.pop("scheduler_manifest", None)
    resolved_profile = deep_merge(defaults, profile)

    # Resolve FROM_NAME placeholders with values extracted from profile name
    resolved_profile = _resolve_from_name_values(profile_name, resolved_profile)

    if scheduler_manifest:
        RENAME THIS TO use_rhoai_defaults
        # When use_kserve_defaults is true, use simplified scheduler template
        use_kserve_defaults = resolved_profile.get("use_kserve_defaults", False)
        if use_kserve_defaults:
            # Create simplified scheduler with just basic container structure
            simplified_scheduler = {
                "scheduler": {
                    "template": {
                        "containers": [{"name": "main"}],
                        # force the scheduler to run on a GPU node. 'gpu.present: true' not working on AKS/CKS
                        "nodeSelector": {"nvidia.com/gpu.deploy.container-toolkit": "true"},
                    }
                }
            }
            resolved_profile = deep_merge(resolved_profile, simplified_scheduler)
        else:
            # When use_kserve_defaults is false, use __router_template__.yaml with EPP config replacement
            scheduler_data = _load_scheduler_with_epp_config(
                get_config_dir(), scheduler_manifest, resolved_profile
            )
            resolved_profile = deep_merge(resolved_profile, scheduler_data)

    return resolved_profile


def get_pd_config() -> dict[str, Any]:
    """Get P/D configuration from active deployment profile."""
    profile = get_deployment_profile()
    return profile.get("pd_config", {})


def get_prefill_pod_count() -> int:
    """Get number of prefill pods for current deployment profile."""
    pd_config = get_pd_config()
    return pd_config.get("prefill_pods", 1)


def get_decode_pod_count() -> int:
    """Get number of decode pods for current deployment profile."""
    pd_config = get_pd_config()
    return pd_config.get("decode_pods", 1)


def get_scheduler_config() -> str:
    """Get scheduler configuration for current deployment profile."""
    pd_config = get_pd_config()
    return pd_config.get("scheduler_config", "default")


def is_pd_deployment() -> bool:
    """Check if current deployment profile is a P/D deployment."""
    profile = get_deployment_profile()
    return "pd_config" in profile


def get_smoke_request() -> dict[str, Any]:
    """Get the smoke request configuration"""
    smoke_request_key = config.project.get_config("runtime.smoke_request_key")
    return copy.deepcopy(config.project.get_config(f"workloads.smoke_requests.{smoke_request_key}"))


def get_run_specs() -> list[RunSpec]:
    _assert_no_legacy_model_key()
    model_names = _normalize_string_or_list(_get_runtime_value("model_name"), "runtime.model_name")
    profile_names = _normalize_string_or_list(
        _get_runtime_value("deployment_profile"),
        "runtime.deployment_profile",
    )
    benchmark_keys = _normalize_string_or_list(
        _get_runtime_value("benchmark_key"),
        "runtime.benchmark_key",
    )

    if not model_names:
        raise ValueError("runtime.model_name must be set to a model name or list of model names")
    if not profile_names:
        raise ValueError(
            "runtime.deployment_profile must be set to a deployment profile or list of profiles"
        )

    if benchmark_keys:
        benchmark_entries = [
            (key, slugify_identifier(key, max_length=24)) for key in benchmark_keys
        ]
    else:
        benchmark_entries = [(None, None)]

    combinations = list(product(model_names, profile_names, benchmark_entries))
    base_namespace = _derive_base_namespace()
    namespace_max_length = get_platform_config()["cluster"]["namespace"]["max_length"]
    # Compute base managed state once (ignores per-spec overrides)
    namespace_is_managed = get_namespace_is_managed()
    run_specs: list[RunSpec] = []

    for model_name, profile_name, (bench_key, bench_slug) in combinations:
        model_slug = get_model_slug(model_name)
        profile_slug = slugify_identifier(profile_name, max_length=24)
        if len(combinations) == 1:
            namespace = base_namespace
            artifact_dirname = "llmd_run"
        else:
            ns_parts = [base_namespace, model_slug, profile_slug]
            dir_parts = ["llmd_run", profile_slug, model_slug]
            if bench_slug is not None:
                ns_parts.append(bench_slug)
                dir_parts.append(bench_slug)
            namespace = truncate_k8s_name(
                "-".join(ns_parts),
                max_length=namespace_max_length,
            )
            artifact_dirname = "_".join(dir_parts)

        run_specs.append(
            RunSpec(
                model_name=model_name,
                model_slug=model_slug,
                deployment_profile_name=profile_name,
                deployment_profile_slug=profile_slug,
                benchmark_key=bench_key,
                benchmark_slug=bench_slug,
                namespace=namespace,
                artifact_dirname=artifact_dirname,
                namespace_is_managed=namespace_is_managed,
            )
        )

    return run_specs


@contextmanager
def activate_run_spec(run_spec: RunSpec):
    """Temporarily activate a run spec by setting its runtime config values."""
    global _namespace_is_managed_override

    saved = {
        "model_name": _get_runtime_value("model_name"),
        "deployment_profile": _get_runtime_value("deployment_profile"),
        "namespace_override": _get_runtime_value("namespace_override"),
        "benchmark_key": _get_runtime_value("benchmark_key"),
    }
    prev_managed_override = _namespace_is_managed_override

    config.project.set_config("runtime.model_name", run_spec.model_name)
    config.project.set_config("runtime.deployment_profile", run_spec.deployment_profile_name)
    config.project.set_config("runtime.namespace_override", run_spec.namespace)
    config.project.set_config("runtime.benchmark_key", run_spec.benchmark_key)
    _namespace_is_managed_override = run_spec.namespace_is_managed
    try:
        yield
    finally:
        config.project.set_config("runtime.model_name", saved["model_name"])
        config.project.set_config("runtime.deployment_profile", saved["deployment_profile"])
        config.project.set_config("runtime.namespace_override", saved["namespace_override"])
        config.project.set_config("runtime.benchmark_key", saved["benchmark_key"])
        _namespace_is_managed_override = prev_managed_override


def normalize_platform_config(platform_data: dict[str, Any]) -> dict[str, Any]:
    cluster = platform_data["cluster"]
    if "namespace" not in cluster:
        cluster["namespace"] = {
            "name": cluster.pop("namespace_name", None),
            "prefix": cluster.pop("namespace_prefix"),
            "max_length": cluster.pop("namespace_max_length"),
        }

    operators = platform_data["operators"]
    if isinstance(operators, list):
        platform_data["operators"] = {
            operator_spec["package"]: {
                key: value for key, value in operator_spec.items() if key != "package"
            }
            for operator_spec in operators
        }

    return platform_data


def derive_namespace(job_name: str, prefix: str, max_length: int) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", job_name.lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    if not slug:
        slug = "run"

    if slug.startswith(f"{prefix}-"):
        namespace = slug
    else:
        namespace = f"{prefix}-{slug}"

    namespace = namespace[:max_length].rstrip("-")
    if not namespace:
        raise ValueError(f"Could not derive a valid namespace from job name: {job_name}")
    return namespace


def version_tuple(value: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", value)
    return tuple(int(number) for number in numbers[:3])
