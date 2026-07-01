"""
Shared "replot" CLI for FORGE project orchestration.

Registers a click subcommand for replotting artifacts and visualizations.
"""

from __future__ import annotations

import logging
from pathlib import Path

import click

from projects.caliper.orchestration.replot import run_replot_from_orchestration_config
from projects.core.library import ci as ci_lib
from projects.core.library import config, env

logger = logging.getLogger(__name__)


def run_replot(*, artifact_directory: Path | None):
    """Run replotting logic on the specified artifact directory."""

    if artifact_directory is None:
        artifact_directory = env.ARTIFACT_DIR

    # Get the replot URL from configuration
    replot_url = config.project.get_config("caliper.replot.url", None, print=False, warn=False)
    if not replot_url:
        raise ValueError(
            "caliper.replot.url is not configured. Use /replot.url directive or set in config."
        )

    keep_replot_dir = config.project.get_config(
        "caliper.replot.keep", False, print=False, warn=False
    )

    # Load MLflow configuration from export settings to get vault secrets
    export_config = config.project.get_config("caliper.export", {}, print=False, warn=False)
    mlflow_backend = export_config.get("backend", {}).get("mlflow", {})

    if not mlflow_backend.get("enabled", False):
        raise ValueError(
            "MLflow export is not enabled in configuration. Cannot determine authentication settings."
        )

    # Get vault secrets configuration (same as export)
    secrets_config = mlflow_backend.get("secrets", {}).get("vault", {})
    vault_name = secrets_config.get("name")
    vault_mlflow_secret = secrets_config.get("mlflow_secret")

    if not vault_name or not vault_mlflow_secret:
        raise ValueError(
            "MLflow vault configuration missing. Check caliper.export.backend.mlflow.secrets.vault settings."
        )

    # Get post-processing configuration
    postprocess_config = config.project.get_config(
        "caliper.postprocess", {}, print=False, warn=False
    )

    # Run the actual replot operation
    return run_replot_from_orchestration_config(
        replot_url=replot_url,
        artifact_directory=artifact_directory,
        vault_name=vault_name,
        vault_mlflow_secret=vault_mlflow_secret,
        keep_replot_dir=keep_replot_dir,
        postprocess_config=postprocess_config,
    )


@click.command("replot")
@click.option(
    "--artifact-directory",
    "artifact_directory",
    type=click.Path(path_type=Path, exists=False, file_okay=True, dir_okay=True),
    default=None,
    help="Artifact root directory to replot from (defaults to ARTIFACT_DIR).",
)
@click.pass_context
@ci_lib.safe_ci_command
def caliper_replot_entrypoint(_ctx, artifact_directory: Path | None):
    """Replot artifacts and visualizations from a remote URL."""

    status = run_replot(artifact_directory=artifact_directory)

    # Log the status
    import yaml

    logger.info("Replot status:")
    logger.info(yaml.dump(status, indent=2))

    # Check if replot was successful
    replot_status = status.get("replot", {}).get("status", "unknown")
    if replot_status != "success":
        return 1

    return 0
