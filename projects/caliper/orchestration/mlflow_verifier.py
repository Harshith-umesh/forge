"""Reusable MLflow nightly verifier.

Connects to the MLflow tracking server, queries runs in the project's
experiment, and extracts the version from the most recent run name.

Projects reuse this by subclassing in their nightly_resolvers/ package
and setting NAME = "mlflow".  The version extraction pattern is read from
``nightly.mlflow_verifier.version_pattern`` (regex with one capture group)
and an optional ``nightly.mlflow_verifier.strip_prefix``.

All MLflow connection settings come from the caliper export config that
every FORGE project already has.
"""

from __future__ import annotations

import logging
import os
import re
import time
import warnings

from projects.caliper.engine.file_export.mlflow_secrets import (
    load_mlflow_secrets_yaml,
    mlflow_connection_env,
    validate_mlflow_secrets,
)
from projects.core.library import vault
from projects.core.library.config import requires
from projects.core.nightly.base_verifier import NightlyVerifier

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 15


class CaliperMLflowVerifier(NightlyVerifier):
    """Check MLflow experiment runs for the last tested version.

    Config requirements (via @requires on __init__):
        caliper.export.backend.mlflow.config.experiment
        caliper.export.backend.mlflow.config.workspace
        caliper.export.backend.mlflow.secrets.vault   (dict with 'name' and 'mlflow_secret')
        nightly.mlflow_verifier.version_pattern        (regex with one capture group)
        nightly.mlflow_verifier.strip_prefix           (prefix to remove from captured version)
    """

    @requires(
        experiment="caliper.export.backend.mlflow.config.experiment",
        workspace="caliper.export.backend.mlflow.config.workspace",
        vault_cfg="caliper.export.backend.mlflow.secrets.vault",
        version_pattern="nightly.mlflow_verifier.version_pattern",
        strip_prefix="nightly.mlflow_verifier.strip_prefix",
    )
    def __init__(self, _cfg):
        self.experiment = _cfg.experiment
        self.workspace = _cfg.workspace
        self.secret_name = _cfg.vault_cfg["name"]
        self.secret_file = _cfg.vault_cfg["mlflow_secret"]
        self._version_re = re.compile(_cfg.version_pattern)
        self._strip_prefix = _cfg.strip_prefix or ""

    def get_last_tested_version(self) -> str:
        connection = self._load_connection()

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
        secret_path = vault.get_vault_content_path(self.secret_name, self.secret_file)

        if secret_path is None or not secret_path.exists():
            raise FileNotFoundError(
                f"MLflow secret '{self.secret_name}/{self.secret_file}' not found in vault. "
                "Ensure the vault configuration is correct."
            )

        data = load_mlflow_secrets_yaml(secret_path)
        validate_mlflow_secrets(data)
        return data

    def _query_latest_run(self) -> str:
        import mlflow
        import urllib3

        warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)

        previous_workspace = os.environ.get("MLFLOW_WORKSPACE")
        try:
            if self.workspace:
                os.environ["MLFLOW_WORKSPACE"] = self.workspace

            client = mlflow.tracking.MlflowClient()

            experiment = client.get_experiment_by_name(self.experiment)
            if experiment is None:
                raise ValueError(
                    f"MLflow experiment '{self.experiment}' not found. "
                    "Create the experiment first or check the configuration."
                )

            runs = client.search_runs(
                experiment_ids=[experiment.experiment_id],
                order_by=["start_time DESC"],
                max_results=1,
            )

            if not runs:
                raise ValueError(f"No runs found in MLflow experiment '{self.experiment}'.")

            latest_run = runs[0]
            run_name = latest_run.info.run_name or ""
            logger.info("Latest MLflow run: %s", run_name)

            return self._extract_version(run_name)
        finally:
            if previous_workspace is None:
                os.environ.pop("MLFLOW_WORKSPACE", None)
            else:
                os.environ["MLFLOW_WORKSPACE"] = previous_workspace

    def _extract_version(self, run_name: str) -> str:
        match = self._version_re.search(run_name)
        if not match:
            raise ValueError(
                f"Could not extract version from run name '{run_name}' "
                f"using pattern '{self._version_re.pattern}'."
            )

        version = match.group(1)

        if self._strip_prefix and version.startswith(self._strip_prefix):
            return version[len(self._strip_prefix) :]

        return version
