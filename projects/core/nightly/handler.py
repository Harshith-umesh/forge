"""Nightly pipeline handler — single phase that orchestrates:

1. Resolve the latest available image version (via project's ImageReceiver)
2. Compare against last tested version (via project's NightlyVerifier)
3. Create a FournosJob if a new version is detected

Error visibility is provided via notification files in the CI metadata directory.
"""

from __future__ import annotations

import importlib
import logging
import os

from projects.core.library import ci as ci_lib
from projects.core.library import config, env

logger = logging.getLogger(__name__)


def _receive_image(project: str) -> str:
    """Resolve the latest available version from the project's receiver."""
    module = importlib.import_module(f"projects.{project}.nightly_resolvers")
    receiver = module.get_receiver()

    logger.info("Using receiver: %s", type(receiver).__name__)
    version = receiver.get_latest_version()
    logger.info("Resolved version: %s", version)

    version_file = env.BASE_ARTIFACT_DIR / "resolved_version.txt"
    version_file.write_text(version)

    (env.ARTIFACT_DIR / "resolved_version.txt").write_text(version)
    return version


def _confirm_nightly(project: str, image_version: str) -> None:
    """Compare resolved version against last tested, create FournosJob if new."""
    module = importlib.import_module(f"projects.{project}.nightly_resolvers")
    verifier = module.get_verifier()

    logger.info("Using verifier: %s", type(verifier).__name__)
    last_version = verifier.get_last_tested_version()
    logger.info("Last tested version: '%s'", last_version or "<none>")

    if image_version == last_version:
        logger.info("Version %s already tested. No new run needed.", image_version)
        ci_lib.add_notification_file(
            "nightly-noop", f"Version {image_version} already tested — no-op."
        )
        (env.ARTIFACT_DIR / "result.txt").write_text("NO-OP")
        return

    logger.info("New version detected: %s (previous: %s)", image_version, last_version or "<none>")
    _create_fournos_job(project, image_version)

    result_msg = f"FournosJob created for {project} version {image_version}"
    ci_lib.add_notification_file("nightly-triggered", result_msg)
    (env.ARTIFACT_DIR / "result.txt").write_text(result_msg)


def _create_fournos_job(project: str, version: str) -> None:
    """Create a FournosJob for the full test run using the fournos_launcher toolbox."""
    from projects.fournos_launcher.toolbox.submit_and_wait.main import run as submit_and_wait

    namespace = os.environ.get("FOURNOS_WORKLOAD_NAMESPACE", "fournos-workloads")
    cluster = config.project.get_config("nightly.cluster")
    version_key = config.project.get_config("nightly.version_key")
    pipeline = config.project.get_config("nightly.pipeline")
    preset = config.project.get_config("nightly.preset", "")
    owner = config.project.get_config("nightly.owner", "nightly-pipeline")

    args_list = [preset] if preset else []

    logger.info(
        "Creating FournosJob: project=%s, version=%s, cluster=%s, pipeline=%s",
        project,
        version,
        cluster,
        pipeline,
    )

    submit_and_wait(
        project,
        cluster_name=cluster,
        args=args_list,
        variables_overrides={version_key: version},
        namespace=namespace,
        owner=owner,
        display_name=f"nightly {project} {version}",
        pipeline_name=pipeline,
        exclusive=True,
        wait=False,
    )


def run():
    """Entrypoint for the nightly phase."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    project = config.project.get_config("project.name")
    logger.info("Project: %s", project)

    try:
        image_version = _receive_image(project)
    except Exception as e:
        logger.error("receive-image failed: %s", e)
        ci_lib.add_notification_file("nightly-receive-image-failed", f"FATAL: {e}")
        raise

    try:
        _confirm_nightly(project, image_version)
    except Exception as e:
        logger.error("confirm-nightly failed: %s", e)
        ci_lib.add_notification_file("nightly-confirm-failed", f"FATAL: {e}")
        raise
