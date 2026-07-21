from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from projects.core.library import env
from projects.llm_d.orchestration.utils import load_yaml, write_yaml


@dataclass(frozen=True)
class CleanupInputs:
    artifact_dir: Path
    namespace: str
    platform: dict[str, Any]
    benchmark: dict[str, Any] | None


@dataclass(frozen=True)
class PrepareModelCacheInputs:
    artifact_dir: Path
    preset_name: str
    namespace: str
    model_key: str
    model: dict[str, Any]
    model_cache: dict[str, Any]


@dataclass(frozen=True)
class PrepareInputs:
    artifact_dir: Path
    config_dir: Path
    preset_name: str
    namespace: str
    platform: dict[str, Any]
    model_key: str
    model: dict[str, Any]
    model_cache: dict[str, Any]
    benchmark: dict[str, Any] | None


@dataclass(frozen=True)
class TestInputs:
    artifact_dir: Path
    config_dir: Path
    preset_name: str
    namespace: str
    platform: dict[str, Any]
    model_key: str
    model: dict[str, Any]
    scheduler_profile_key: str
    scheduler_profile: dict[str, Any] | None
    model_cache: dict[str, Any]
    smoke_request: dict[str, Any]
    benchmark: dict[str, Any] | None


def write_cleanup_inputs() -> Path:
    """Write cleanup phase inputs using direct config access."""
    from projects.llm_d.orchestration import runtime_config

    path = env.ARTIFACT_DIR / "_meta" / "cleanup.inputs.yaml"
    write_yaml(
        path,
        {
            "artifact_dir": str(env.ARTIFACT_DIR),
            "namespace": runtime_config.get_namespace(),
            "platform": runtime_config.get_platform_config(),
            "benchmark": runtime_config.get_benchmark_config(),
        },
    )
    return path


def write_prepare_inputs_from_prepare(inputs: PrepareInputs) -> Path:
    path = inputs.artifact_dir / "_meta" / "prepare.inputs.yaml"
    write_yaml(
        path,
        {
            "artifact_dir": str(inputs.artifact_dir),
            "config_dir": str(inputs.config_dir),
            "preset_name": inputs.preset_name,
            "namespace": inputs.namespace,
            "platform": inputs.platform,
            "model_key": inputs.model_key,
            "model": inputs.model,
            "model_cache": inputs.model_cache,
            "benchmark": inputs.benchmark,
        },
    )
    return path


def load_cleanup_inputs(path: str | Path) -> CleanupInputs:
    payload = load_yaml(Path(path))
    return CleanupInputs(
        artifact_dir=Path(payload["artifact_dir"]),
        namespace=payload["namespace"],
        platform=payload["platform"],
        benchmark=payload["benchmark"],
    )


def build_cleanup_inputs(
    *,
    artifact_dir: str | Path,
    namespace: str,
    platform: dict[str, Any],
    benchmark: dict[str, Any] | None,
) -> CleanupInputs:
    return CleanupInputs(
        artifact_dir=Path(artifact_dir),
        namespace=namespace,
        platform=platform,
        benchmark=benchmark,
    )


def load_prepare_model_cache_inputs(path: str | Path) -> PrepareModelCacheInputs:
    payload = load_yaml(Path(path))
    return PrepareModelCacheInputs(
        artifact_dir=Path(payload["artifact_dir"]),
        preset_name=payload["preset_name"],
        namespace=payload["namespace"],
        model_key=payload["model_key"],
        model=payload["model"],
        model_cache=payload["model_cache"],
    )


def build_prepare_model_cache_inputs(
    *,
    artifact_dir: str | Path,
    preset_name: str,
    namespace: str,
    model_key: str,
    model: dict[str, Any],
    model_cache: dict[str, Any],
) -> PrepareModelCacheInputs:
    return PrepareModelCacheInputs(
        artifact_dir=Path(artifact_dir),
        preset_name=preset_name,
        namespace=namespace,
        model_key=model_key,
        model=model,
        model_cache=model_cache,
    )


def load_prepare_inputs(path: str | Path) -> PrepareInputs:
    payload = load_yaml(Path(path))
    return PrepareInputs(
        artifact_dir=Path(payload["artifact_dir"]),
        config_dir=Path(payload["config_dir"]),
        preset_name=payload["preset_name"],
        namespace=payload["namespace"],
        platform=payload["platform"],
        model_key=payload["model_key"],
        model=payload["model"],
        model_cache=payload["model_cache"],
        benchmark=payload["benchmark"],
    )


def build_prepare_inputs(
    *,
    artifact_dir: str | Path,
    config_dir: str | Path,
    preset_name: str,
    namespace: str,
    platform: dict[str, Any],
    model_key: str,
    model: dict[str, Any],
    model_cache: dict[str, Any],
    benchmark: dict[str, Any] | None,
) -> PrepareInputs:
    return PrepareInputs(
        artifact_dir=Path(artifact_dir),
        config_dir=Path(config_dir),
        preset_name=preset_name,
        namespace=namespace,
        platform=platform,
        model_key=model_key,
        model=model,
        model_cache=model_cache,
        benchmark=benchmark,
    )


def load_test_inputs(path: str | Path) -> TestInputs:
    payload = load_yaml(Path(path))
    return TestInputs(
        artifact_dir=Path(payload["artifact_dir"]),
        config_dir=Path(payload["config_dir"]),
        preset_name=payload["preset_name"],
        namespace=payload["namespace"],
        platform=payload["platform"],
        model_key=payload["model_key"],
        model=payload["model"],
        scheduler_profile_key=payload["scheduler_profile_key"],
        scheduler_profile=payload["scheduler_profile"],
        model_cache=payload["model_cache"],
        smoke_request=payload["smoke_request"],
        benchmark=payload["benchmark"],
    )


def build_test_inputs(
    *,
    artifact_dir: str | Path,
    config_dir: str | Path,
    preset_name: str,
    namespace: str,
    platform: dict[str, Any],
    model_key: str,
    model: dict[str, Any],
    scheduler_profile_key: str,
    scheduler_profile: dict[str, Any] | None,
    model_cache: dict[str, Any],
    smoke_request: dict[str, Any],
    benchmark: dict[str, Any] | None,
) -> TestInputs:
    return TestInputs(
        artifact_dir=Path(artifact_dir),
        config_dir=Path(config_dir),
        preset_name=preset_name,
        namespace=namespace,
        platform=platform,
        model_key=model_key,
        model=model,
        scheduler_profile_key=scheduler_profile_key,
        scheduler_profile=scheduler_profile,
        model_cache=model_cache,
        smoke_request=smoke_request,
        benchmark=benchmark,
    )


def cleanup_inputs_from_prepare(inputs: PrepareInputs) -> CleanupInputs:
    return CleanupInputs(
        artifact_dir=inputs.artifact_dir,
        namespace=inputs.namespace,
        platform=inputs.platform,
        benchmark=inputs.benchmark,
    )


def write_cleanup_inputs_from_prepare(inputs: PrepareInputs) -> Path:
    path = inputs.artifact_dir / "_meta" / "cleanup.inputs.yaml"
    cleanup_inputs = cleanup_inputs_from_prepare(inputs)
    write_yaml(
        path,
        {
            "artifact_dir": str(cleanup_inputs.artifact_dir),
            "namespace": cleanup_inputs.namespace,
            "platform": cleanup_inputs.platform,
            "benchmark": cleanup_inputs.benchmark,
        },
    )
    return path


def prepare_model_cache_inputs_from_prepare(inputs: PrepareInputs) -> PrepareModelCacheInputs:
    return PrepareModelCacheInputs(
        artifact_dir=inputs.artifact_dir,
        preset_name=inputs.preset_name,
        namespace=inputs.namespace,
        model_key=inputs.model_key,
        model=inputs.model,
        model_cache=inputs.model_cache,
    )


def write_prepare_model_cache_inputs_from_prepare(inputs: PrepareInputs) -> Path:
    path = inputs.artifact_dir / "_meta" / "prepare_model_cache.inputs.yaml"
    cache_inputs = prepare_model_cache_inputs_from_prepare(inputs)
    write_yaml(
        path,
        {
            "artifact_dir": str(cache_inputs.artifact_dir),
            "preset_name": cache_inputs.preset_name,
            "namespace": cache_inputs.namespace,
            "model_key": cache_inputs.model_key,
            "model": cache_inputs.model,
            "model_cache": cache_inputs.model_cache,
        },
    )
    return path
