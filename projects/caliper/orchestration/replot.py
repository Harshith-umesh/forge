"""
Config-driven Caliper artifact replot for FORGE orchestration projects.

Handles MLflow artifact downloading and post-processing pipeline execution.
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import re
import shutil
from pathlib import Path

from projects.caliper.engine.file_export.mlflow_secrets import (
    load_mlflow_secrets_yaml,
    mlflow_connection_env,
)
from projects.caliper.orchestration.postprocess import run_postprocess_from_orchestration_config
from projects.caliper.orchestration.postprocess_outcome import TestPhaseOutcome
from projects.core.library import vault as vault_lib

logger = logging.getLogger(__name__)


def _download_mlflow_artifacts(
    replot_url: str,
    replot_download_dir: Path,
    mlflow_secrets_path: Path,
) -> dict:
    """
    Download MLflow artifacts from a replot URL.

    Args:
        replot_url: MLflow URL containing run ID
        replot_download_dir: Local directory to download artifacts to
        mlflow_secrets_path: Path to MLflow secrets file

    Returns:
        Dict containing download status information

    Raises:
        ValueError: If URL parsing or configuration validation fails
        FileNotFoundError: If vault secrets are not found
        RuntimeError: If MLflow download fails
    """
    # Handle MLflow web UI URLs like: https://mlflow.server.com/#/experiments/0/runs/RUN_ID
    # or API URLs like: https://mlflow.server.com/runs/RUN_ID

    # Extract run ID from either fragment (#/experiments/N/runs/ID) or path (/runs/ID)
    run_id_match = re.search(r"[/#]runs/([^/?#]+)", replot_url)
    if not run_id_match:
        raise ValueError(f"Could not parse MLflow run ID from URL: {replot_url}")

    run_id = run_id_match.group(1)

    # Extract base MLflow tracking URI (remove fragments and paths)
    # For https://mlflow.server.com/#/experiments/0/runs/ID -> https://mlflow.server.com
    # For https://mlflow.server.com/runs/ID -> https://mlflow.server.com
    from urllib.parse import urlparse

    parsed = urlparse(replot_url)
    mlflow_uri = f"{parsed.scheme}://{parsed.netloc}"

    # Load MLflow secrets and validate tracking URI
    mlflow_secrets = load_mlflow_secrets_yaml(mlflow_secrets_path)
    export_tracking_uri = mlflow_secrets.get("tracking_uri", "").rstrip("/")
    replot_tracking_uri = mlflow_uri.rstrip("/")

    logger.debug(f"Parsed tracking URI from replot URL: {replot_tracking_uri}")
    logger.debug(f"Export tracking URI from vault: {export_tracking_uri}")

    if export_tracking_uri != replot_tracking_uri:
        raise ValueError(
            f"Replot URL tracking URI ({replot_tracking_uri}) does not match "
            f"export configuration tracking URI ({export_tracking_uri}). "
            f"For security, replot can only download from the same MLflow server used for export."
        )

    logger.info(f"Downloading from MLflow: {mlflow_uri}, run_id: {run_id}")

    # Download artifacts using MLflow with proper authentication
    try:
        import mlflow

        # Suppress noisy connection warnings and progress bars
        import urllib3
        from mlflow.tracking import MlflowClient

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)
        logging.getLogger("botocore").setLevel(logging.WARNING)
        logging.getLogger("boto3").setLevel(logging.WARNING)

        # Suppress MLflow progress bars by setting environment variable
        os.environ["MLFLOW_ENABLE_ARTIFACTS_PROGRESS_BAR"] = "false"

        # Use the same MLflow connection setup as export
        with mlflow_connection_env(mlflow_secrets):
            mlflow.set_tracking_uri(mlflow_uri)
            client = MlflowClient()

            # Download artifacts to the output directory (suppress progress bar output)
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                downloaded_path = client.download_artifacts(
                    run_id=run_id,
                    path="",  # Download all artifacts
                    dst_path=str(replot_download_dir),
                )

            # Count downloaded files
            if Path(downloaded_path).is_file():
                downloaded_files = [Path(downloaded_path)]
            else:
                downloaded_files = list(Path(downloaded_path).rglob("*"))
                downloaded_files = [f for f in downloaded_files if f.is_file()]

            logger.info(f"Downloaded {len(downloaded_files)} files to {replot_download_dir}")
            if downloaded_files:
                logger.info("Downloaded files:")
                for file in downloaded_files[:10]:  # Show first 10
                    try:
                        relative_path = file.relative_to(replot_download_dir)
                        logger.info(f"  {relative_path}")
                    except ValueError:
                        logger.info(f"  {file}")
                if len(downloaded_files) > 10:
                    logger.info(f"  ... and {len(downloaded_files) - 10} more")

            return {
                "download_status": "success",
                "downloaded_files": len(downloaded_files),
                "validated_tracking_uri": export_tracking_uri,
                "run_id": run_id,
                "tracking_uri": mlflow_uri,
            }

    except Exception as e:
        raise RuntimeError(f"MLflow artifact download failed: {e}") from e


def run_replot_from_orchestration_config(
    replot_url: str,
    artifact_directory: Path,
    vault_name: str,
    vault_mlflow_secret: str,
    keep_replot_dir: bool = False,
    postprocess_config: dict | None = None,
) -> dict:
    """
    Run replotting logic with orchestration configuration.

    Args:
        replot_url: MLflow URL to download artifacts from
        artifact_directory: Directory for final artifacts output
        vault_name: Name of the vault containing secrets
        vault_mlflow_secret: MLflow secret key in the vault
        keep_replot_dir: Whether to keep the download directory after processing
        postprocess_config: Configuration for post-processing

    Returns:
        Dict containing replot operation status and results
    """
    replot_download_dir = artifact_directory / "replot"

    logger.info(f"Replotting artifacts from URL: {replot_url}")
    logger.info(f"Download directory: {replot_download_dir}")
    logger.info(f"Output directory: {artifact_directory}")

    # Get MLflow secrets from vault
    mlflow_secrets_path = vault_lib.get_vault_content_path(vault_name, vault_mlflow_secret)
    if mlflow_secrets_path is None or not mlflow_secrets_path.exists():
        raise FileNotFoundError(
            f"MLflow secrets not found in vault {vault_name}/{vault_mlflow_secret}"
        )

    logger.info(f"Using MLflow secrets from vault {vault_name}/{vault_mlflow_secret}")

    status = {
        "replot": {
            "url": replot_url,
            "download_directory": str(replot_download_dir),
            "output_directory": str(artifact_directory),
            "keep_download_dir": keep_replot_dir,
        }
    }

    try:
        # Step 1: Download artifacts
        logger.info("Downloading artifacts...")

        # Check if download directory already exists with content
        if replot_download_dir.exists() and any(replot_download_dir.iterdir()):
            logger.info(
                f"Replot download directory already exists with content, skipping download: {replot_download_dir}"
            )

            # Count existing files for status
            existing_files = list(replot_download_dir.rglob("*"))
            existing_files = [f for f in existing_files if f.is_file()]

            logger.info(f"Found {len(existing_files)} existing files")
            if existing_files:
                logger.info("Existing files:")
                for file in existing_files[:10]:  # Show first 10
                    try:
                        relative_path = file.relative_to(replot_download_dir)
                        logger.info(f"  {relative_path}")
                    except ValueError:
                        logger.info(f"  {file}")
                if len(existing_files) > 10:
                    logger.info(f"  ... and {len(existing_files) - 10} more")

            status["replot"].update(
                {
                    "download_status": "skipped",
                    "downloaded_files": len(existing_files),
                    "skip_reason": "directory_already_exists",
                }
            )
        else:
            # Create the download directory
            replot_download_dir.mkdir(parents=True, exist_ok=True)

            # Download artifacts based on URL type
            if "mlflow" in replot_url.lower() and "runs" in replot_url:
                download_result = _download_mlflow_artifacts(
                    replot_url, replot_download_dir, mlflow_secrets_path
                )
                status["replot"].update(download_result)
            else:
                raise ValueError(
                    f"Unsupported replot URL type: {replot_url}. Only MLflow URLs are currently supported."
                )

        logger.info("Artifacts downloaded successfully")

        # Step 2: Run post-processing on downloaded artifacts
        logger.info("Running post-processing...")

        postprocess_result = run_postprocess_from_orchestration_config(
            postprocess_config_raw=postprocess_config or {},
            artifacts_dir=replot_download_dir,
            visualize_output_dir=artifact_directory,
            test_outcome=TestPhaseOutcome("SUCCESS"),
        )

        # Log post-processing results
        if postprocess_result.get("steps", {}).get("visualize", {}).get("status") == "skipped":
            logger.info("Post-processing completed (visualizations skipped)")
        elif postprocess_result.get("steps", {}).get("visualize", {}).get("paths"):
            viz_paths = postprocess_result["steps"]["visualize"]["paths"]
            logger.info(f"Post-processing completed with {len(viz_paths)} visualizations generated")
        else:
            logger.info("Post-processing completed (parsing only)")

        status["replot"]["postprocess_status"] = (
            "success" if postprocess_result.get("success", False) else "failed"
        )
        status["replot"]["postprocess_result"] = postprocess_result

        # Step 3: Clean up download directory unless keeping
        if not keep_replot_dir:
            logger.info(f"Cleaning up download directory: {replot_download_dir}")
            shutil.rmtree(replot_download_dir)
            status["replot"]["cleanup_status"] = "completed"
        else:
            logger.info(f"Keeping download directory as requested: {replot_download_dir}")
            status["replot"]["cleanup_status"] = "skipped"

        status["replot"]["status"] = "success"
        status["replot"]["message"] = "Replot completed successfully"

    except Exception as e:
        logger.error(f"Replot failed: {e}")
        status["replot"]["status"] = "failed"
        status["replot"]["message"] = str(e)

        # Try to clean up on failure unless keeping
        if not keep_replot_dir and replot_download_dir.exists():
            try:
                shutil.rmtree(replot_download_dir)
                status["replot"]["cleanup_status"] = "completed_on_failure"
            except Exception as cleanup_e:
                logger.warning(f"Failed to cleanup download directory: {cleanup_e}")
                status["replot"]["cleanup_status"] = "failed"

        raise

    return status
