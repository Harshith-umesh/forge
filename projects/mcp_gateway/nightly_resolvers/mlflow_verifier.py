"""MLflow nightly verifier for mcp_gateway.

Connects to the MLflow tracking server, queries runs in the project's
experiment, and extracts the version from the most recent run name.

Uses the same secret loading mechanism as caliper's mlflow export.
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

from projects.caliper.engine.file_export.mlflow_secrets import (
    load_mlflow_secrets_yaml,
    mlflow_connection_env,
    validate_mlflow_secrets,
)
from projects.core.nightly.base_verifier import NightlyVerifier

logger = logging.getLogger(__name__)

VERSION_PATTERN = re.compile(r"-v(.+?)-\d{8}-\d{6}$")

MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 15


class MLflowVerifier(NightlyVerifier):
    """Check MLflow experiment runs for the last tested version.

    Reads its configuration from the project's config.yaml under:
        caliper.export.backend.mlflow.config.experiment
        caliper.export.backend.mlflow.config.workspace
        caliper.export.backend.mlflow.secrets.vault.name
        caliper.export.backend.mlflow.secrets.vault.mlflow_secret
    """

    NAME = "mlflow"

    def __init__(self):
        from projects.core.library import config

        self.experiment = config.project.get_config(
            "caliper.export.backend.mlflow.config.experiment"
        )
        self.workspace = config.project.get_config("caliper.export.backend.mlflow.config.workspace")
        vault_cfg = config.project.get_config("caliper.export.backend.mlflow.secrets.vault")
        self.secret_name = vault_cfg["name"]
        self.secret_file = vault_cfg["mlflow_secret"]

    def get_last_tested_version(self) -> str:
        connection = self._load_connection()
        if not connection:
            return ""

        last_error = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                with mlflow_connection_env(connection):
                    return self._query_latest_run()
            except Exception as e:
                last_error = e
                if attempt < MAX_ATTEMPTS:
                    logger.warning(
                        "MLflow attempt %d/%d failed: %s. Retrying in %ds...",
                        attempt,
                        MAX_ATTEMPTS,
                        e,
                        RETRY_DELAY_SECONDS,
                    )
                    time.sleep(RETRY_DELAY_SECONDS)
        raise RuntimeError(f"MLflow verifier failed after {MAX_ATTEMPTS} attempts") from last_error

    def _load_connection(self) -> dict:
        secrets_dir = Path(os.environ.get("FOURNOS_SECRETS", "/var/run/secrets/fournos"))
        secret_path = secrets_dir / self.secret_name / self.secret_file

        if not secret_path.exists():
            logger.warning("MLflow secret not found at expected path, assuming no previous run")
            return {}

        data = load_mlflow_secrets_yaml(secret_path)
        validate_mlflow_secrets(data)
        return data

    def _query_latest_run(self) -> str:
        import mlflow

        if self.workspace:
            os.environ["MLFLOW_WORKSPACE"] = self.workspace

        client = mlflow.tracking.MlflowClient()

        experiment = client.get_experiment_by_name(self.experiment)
        if experiment is None:
            logger.warning("Experiment '%s' not found in MLflow", self.experiment)
            return ""

        runs = client.search_runs(
            experiment_ids=[experiment.experiment_id],
            order_by=["start_time DESC"],
            max_results=1,
        )

        if not runs:
            logger.info("No runs found in experiment '%s'", self.experiment)
            return ""

        latest_run = runs[0]
        run_name = latest_run.info.run_name or ""
        logger.info("Latest MLflow run: %s", run_name)

        return self._extract_version(run_name)

    def _extract_version(self, run_name: str) -> str:
        """Extract the version from a run name like:
        forge-mcp-gateway-s150-u500-vsha-<sha>-20260714-224646

        Returns the raw SHA (without the 'sha-' prefix) to match
        what the receiver returns.
        """
        match = VERSION_PATTERN.search(run_name)
        if not match:
            logger.warning("Could not extract version from run name: %s", run_name)
            return ""

        version = match.group(1)

        if version.startswith("sha-"):
            return version[4:]

        return version
