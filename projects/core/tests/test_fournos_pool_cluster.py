from pathlib import Path

import pytest
import yaml

from projects.core.ci_entrypoint.fournos import (
    load_fjob_yaml,
    transform_fournos_config_to_variable_overrides,
)


def _fjob(spec: dict, status: dict | None = None) -> dict:
    return {
        "metadata": {"name": "pool-job"},
        "spec": {"executionEngine": {"forge": {"project": "rhaiis"}}, **spec},
        "status": status or {},
    }


def test_pool_job_uses_assigned_cluster_for_forge_config() -> None:
    fjob = _fjob(
        {
            "gpuPool": "rhaiis-cpt-pool",
            "executionEngine": {
                "forge": {
                    "project": "rhaiis",
                    "configOverrides": {"ci_job.cluster": "zeus"},
                }
            },
        },
        {"cluster": "old-zeus"},
    )

    overrides = transform_fournos_config_to_variable_overrides(fjob)

    assert overrides["ci_job.cluster"] == "old-zeus"


def test_fournos_yaml_loader_preserves_assigned_cluster(tmp_path) -> None:
    fjob_path = tmp_path / "fournos_fjob.yaml"
    fjob_path.write_text(
        yaml.safe_dump(_fjob({"gpuPool": "rhaiis-cpt-pool"}, {"cluster": "old-zeus"}))
    )

    fjob, spec = load_fjob_yaml(fjob_path)

    assert spec["gpuPool"] == "rhaiis-cpt-pool"
    assert transform_fournos_config_to_variable_overrides(fjob)["ci_job.cluster"] == "old-zeus"


def test_pool_job_requires_an_assigned_cluster() -> None:
    fjob = _fjob({"gpuPool": "rhaiis-cpt-pool"})

    with pytest.raises(ValueError, match="no assigned cluster"):
        transform_fournos_config_to_variable_overrides(fjob)


def test_pinned_job_still_uses_spec_cluster() -> None:
    fjob = _fjob({"cluster": "zeus"}, {"cluster": "old-zeus"})

    overrides = transform_fournos_config_to_variable_overrides(fjob)

    assert overrides["ci_job.cluster"] == "zeus"


def test_old_zeus_cluster_preset_matches_assigned_cluster_name() -> None:
    presets_path = Path(__file__).parents[2] / "rhaiis/orchestration/presets.d/clusters.yaml"
    presets = yaml.safe_load(presets_path.read_text())

    assert presets["cluster_old-zeus"]["extends"] == ["nvidia", "old-zeus"]
