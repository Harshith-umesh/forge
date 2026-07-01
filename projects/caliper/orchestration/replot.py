"""
Config-driven Caliper artifact replot for FORGE orchestration projects.

Handles MLflow artifact downloading and post-processing pipeline execution.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from projects.caliper.orchestration.postprocess import run_postprocess_from_orchestration_config
from projects.caliper.orchestration.postprocess_outcome import TestPhaseOutcome
from projects.core.library import vault as vault_lib

logger = logging.getLogger(__name__)


def _download_mlflow_artifacts_via_import(
    replot_url: str,
    replot_download_dir: Path,
    mlflow_secrets_path: Path,
) -> dict:
    """
    Download MLflow artifacts from a replot URL using the artifacts import command.

    Args:
        replot_url: MLflow URL containing run ID
        replot_download_dir: Local directory to download artifacts to
        mlflow_secrets_path: Path to MLflow secrets file

    Returns:
        Dict containing download status information

    Raises:
        ValueError: If URL parsing fails
        RuntimeError: If import command fails
    """
    import subprocess
    import sys

    # Extract run ID for status reporting
    run_id_match = re.search(r"[/#]runs/([^/?#]+)", replot_url)
    if not run_id_match:
        raise ValueError(f"Could not parse MLflow run ID from URL: {replot_url}")

    run_id = run_id_match.group(1)

    logger.info(f"Downloading artifacts using import command for run ID: {run_id}")

    # Construct command to call the existing artifacts import
    cmd = [
        sys.executable,
        "-m",
        "projects.caliper.cli.main",
        "artifacts",
        "import",
        "--from-mlflow-url",
        replot_url,
        "--output-dir",
        str(replot_download_dir),
        "--mlflow-secrets",
        str(mlflow_secrets_path),
        "--mlflow-insecure-tls",
    ]

    logger.debug(f"Running import command: {' '.join(cmd)}")

    try:
        # Run the import command
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )

        logger.info("Import command completed successfully")
        if result.stdout:
            logger.debug(f"Import stdout: {result.stdout}")
        if result.stderr:
            logger.debug(f"Import stderr: {result.stderr}")

        # Count downloaded files
        downloaded_files = list(replot_download_dir.rglob("*"))
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
            "run_id": run_id,
            "import_command": " ".join(cmd),
        }

    except subprocess.CalledProcessError as e:
        error_msg = f"Import command failed with exit code {e.returncode}"
        if e.stdout:
            error_msg += f"\nStdout: {e.stdout}"
        if e.stderr:
            error_msg += f"\nStderr: {e.stderr}"

        logger.error(error_msg)
        raise RuntimeError(f"MLflow artifact download via import failed: {error_msg}") from e
    except Exception as e:
        raise RuntimeError(f"MLflow artifact download via import failed: {e}") from e


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

    Note: Insecure TLS is enabled by default for MLflow connections to support
    servers with self-signed certificates.

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
                download_result = _download_mlflow_artifacts_via_import(
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
