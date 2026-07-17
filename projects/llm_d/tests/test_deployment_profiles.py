from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from projects.core.library import config as core_config
from projects.core.library import env

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROJECT_ORCHESTRATION_DIR = Path(__file__).resolve().parents[1] / "orchestration"
REFERENCE_DIR = Path(__file__).resolve().parent / "reference_deployments"

# Deployment presets to test
DEPLOYMENT_PRESETS = [
    "deployment-simple-tp4-x4",
    "deployment-intelligentrouting-tp4-x4",
    "deployment-pd-x2-ptp4-px1-dtp4",
]


@pytest.fixture(autouse=True)
def _reset_project_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path / "artifacts"))
    # Unset KUBECONFIG as requested
    monkeypatch.delenv("KUBECONFIG", raising=False)
    env.init()
    core_config.project = None
    yield
    core_config.project = None


@pytest.mark.parametrize("preset", DEPLOYMENT_PRESETS)
def test_deployment_preset_generates_expected_llmisvc(preset: str, tmp_path: Path):
    """Test that deployment presets generate the expected LLMISVC manifests."""
    _test_preset_generates_expected_llmisvc(preset, tmp_path)


def _test_preset_generates_expected_llmisvc(preset: str, tmp_path: Path):
    """Helper function to test a preset generates the expected LLMISVC manifest."""
    # Set up environment
    artifact_dir = tmp_path / "artifacts" / preset
    artifact_dir.mkdir(parents=True, exist_ok=True)

    # Create variable overrides to ensure dry-run mode
    variable_overrides_path = artifact_dir / "000__ci_metadata" / "variable_overrides.yaml"
    variable_overrides_path.parent.mkdir(parents=True, exist_ok=True)
    variable_overrides_path.write_text(
        yaml.safe_dump(
            {
                "runtime.kserve_dry_run": True,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    env_vars = os.environ.copy()
    env_vars["ARTIFACT_DIR"] = str(artifact_dir)
    env_vars.pop("KUBECONFIG", None)

    # Run the CI script with the preset
    ci_script = PROJECT_ROOT / "projects" / "llm_d" / "orchestration" / "ci.py"
    cmd = [str(ci_script), "--preset", preset, "test"]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        env=env_vars,
    )

    # Check that the command succeeded
    if result.returncode != 0:
        pytest.fail(
            f"CI command failed for preset {preset}:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    # Find the generated LLMISVC manifest
    generated_manifest = _find_generated_llmisvc(artifact_dir)
    if not generated_manifest.exists():
        pytest.fail(f"Generated LLMISVC manifest not found at {generated_manifest}")

    # Load the generated manifest
    with generated_manifest.open("r", encoding="utf-8") as f:
        generated_content = yaml.safe_load(f)

    # Load the reference manifest
    reference_manifest = REFERENCE_DIR / preset / "llmisvc.yaml"
    if not reference_manifest.exists():
        pytest.fail(f"Reference manifest not found at {reference_manifest}")

    with reference_manifest.open("r", encoding="utf-8") as f:
        reference_content = yaml.safe_load(f)

    # Compare the manifests
    if generated_content != reference_content:
        # Generate a diff for better error reporting
        import difflib

        generated_yaml = yaml.dump(generated_content, default_flow_style=False, sort_keys=True)
        reference_yaml = yaml.dump(reference_content, default_flow_style=False, sort_keys=True)

        diff = list(
            difflib.unified_diff(
                reference_yaml.splitlines(keepends=True),
                generated_yaml.splitlines(keepends=True),
                fromfile=f"reference/{preset}/llmisvc.yaml",
                tofile=f"generated/{preset}/llmisvc.yaml",
                lineterm="",
            )
        )

        pytest.fail(
            f"Generated LLMISVC does not match reference for preset {preset}:\n{''.join(diff)}"
        )


def _find_generated_llmisvc(artifact_dir: Path) -> Path:
    """Find the generated LLMISVC manifest in the artifact directory."""
    # Look for the pattern: $ARTIFACT_DIR/**/deploy_llmisvc/src/llminferenceservice.yaml
    deploy_llmisvc_dirs = list(artifact_dir.glob("**/*__deploy_llmisvc"))

    if not deploy_llmisvc_dirs:
        raise FileNotFoundError(f"No deploy_llmisvc directory found in {artifact_dir}")

    if len(deploy_llmisvc_dirs) > 1:
        raise ValueError(
            f"Multiple deploy_llmisvc directories found in {artifact_dir}: {deploy_llmisvc_dirs}"
        )

    llmisvc_path = deploy_llmisvc_dirs[0] / "src" / "llminferenceservice.yaml"
    return llmisvc_path
