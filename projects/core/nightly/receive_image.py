"""Nightly receive-image handler.

Loads the project's resolvers module and calls get_receiver() to obtain
an ImageReceiver instance. The core never knows what registry or strategy
the project uses — it just calls get_latest_version() on whatever it gets.
"""

from __future__ import annotations

import importlib
import logging
import os
from pathlib import Path

from projects.core.library import config

logger = logging.getLogger(__name__)


def run():
    """Entrypoint for the receive-image phase."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    project = config.project.get_config("project.name")
    logger.info("Project: %s", project)

    module = importlib.import_module(f"projects.{project}.resolvers")
    receiver = module.get_receiver()

    logger.info("Using receiver: %s", type(receiver).__name__)
    version = receiver.get_latest_version()
    logger.info("Resolved version: %s", version)

    artifact_base = Path(os.environ["ARTIFACT_BASE_DIR"])
    version_file = artifact_base / "resolved_version.txt"
    version_file.write_text(version)
    logger.info("Wrote version to %s", version_file)

    artifact_dir = Path(os.environ["ARTIFACT_DIR"])
    (artifact_dir / "resolved_version.txt").write_text(version)

    print(f"RESOLVED_VERSION={version}")
