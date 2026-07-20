"""Nightly confirm handler.

Loads the project's resolvers module to get a NightlyVerifier instance.
Compares the resolved version (from receive-image step) against what
the verifier reports as the last tested version. Creates a FournosJob
if a new version is detected.

The core never knows how or where history is stored — it just calls
get_last_tested_version() on whatever the project provides.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import subprocess
from pathlib import Path

from projects.core.library import config

logger = logging.getLogger(__name__)


def _read_resolved_version() -> str:
    """Read the version written by the receive-image step."""
    artifact_base = Path(os.environ["ARTIFACT_BASE_DIR"])
    version_file = artifact_base / "resolved_version.txt"

    if not version_file.exists():
        raise FileNotFoundError(
            f"resolved_version.txt not found at {version_file}. "
            "Did the receive-image step run successfully?"
        )

    version = version_file.read_text().strip()
    if not version:
        raise RuntimeError("resolved_version.txt is empty")
    return version


def _create_fournos_job(project: str, version: str) -> None:
    """Create a FournosJob for the full test run."""
    namespace = os.environ.get("FOURNOS_WORKLOAD_NAMESPACE", "fournos-workloads")
    cluster = config.project.get_config("nightly.cluster")
    version_key = config.project.get_config("nightly.version_key")
    pipeline = config.project.get_config("nightly.pipeline")
    preset = config.project.get_config("nightly.preset", "")
    owner = config.project.get_config("nightly.owner", "nightly-pipeline")

    args_list = [preset] if preset else []

    fjob_manifest = {
        "apiVersion": "fournos.dev/v1",
        "kind": "FournosJob",
        "metadata": {
            "generateName": f"nightly-{project.replace('_', '-')}-",
            "namespace": namespace,
        },
        "spec": {
            "cluster": cluster,
            "displayName": f"nightly {project} {version}",
            "owner": owner,
            "pipeline": pipeline,
            "exclusive": True,
            "executionEngine": {
                "forge": {
                    "project": project,
                    "args": args_list,
                    "configOverrides": {
                        version_key: version,
                    },
                },
            },
        },
    }

    manifest_json = json.dumps(fjob_manifest)
    logger.info(
        "Creating FournosJob: project=%s, version=%s, cluster=%s, pipeline=%s",
        project, version, cluster, pipeline,
    )

    result = subprocess.run(
        ["oc", "apply", "-f", "-"],
        input=manifest_json,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(f"Failed to create FournosJob: {result.stderr}")

    logger.info("FournosJob created: %s", result.stdout.strip())


def run():
    """Entrypoint for the confirm-nightly phase."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    project = config.project.get_config("project.name")
    logger.info("Project: %s", project)

    image_version = _read_resolved_version()
    logger.info("New version available: %s", image_version)

    module = importlib.import_module(f"projects.{project}.resolvers")
    verifier = module.get_verifier()

    logger.info("Using verifier: %s", type(verifier).__name__)
    last_version = verifier.get_last_tested_version()
    logger.info("Last tested version: '%s'", last_version or "<none>")

    if image_version == last_version:
        logger.info("Version %s already tested. No new run needed.", image_version)
        print("RESULT: NO-OP")
        artifact_dir = Path(os.environ["ARTIFACT_DIR"])
        (artifact_dir / "result.txt").write_text("NO-OP")
        return

    logger.info("New version detected: %s (previous: %s)", image_version, last_version or "<none>")

    _create_fournos_job(project, image_version)

    result_msg = f"FournosJob created for {project} version {image_version}"
    logger.info("RESULT: %s", result_msg)
    print(f"RESULT: {result_msg}")

    artifact_dir = Path(os.environ["ARTIFACT_DIR"])
    (artifact_dir / "result.txt").write_text(result_msg)
