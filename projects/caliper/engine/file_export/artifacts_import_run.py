"""
Shared implementation for ``caliper artifacts import`` (CLI and orchestration).
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

import click

from projects.caliper.engine.file_export.mlflow_secrets import (
    load_mlflow_secrets_yaml,
    mlflow_connection_env,
    validate_mlflow_secrets,
)

logger = logging.getLogger(__name__)


def run_artifacts_import(
    *,
    mlflow_run_id: str | None = None,
    mlflow_url: str | None = None,
    output_dir: Path,
    mlflow_tracking_uri: str | None = None,
    artifact_path: str = "",
    timeout: int = 300,
    mlflow_insecure_tls: bool = False,
    mlflow_experiment: str | None = None,
    mlflow_workspace: str | None = None,
    mlflow_secrets_path: Path | None = None,
    verbose: bool = False,
) -> None:
    """Download artifacts from MLflow."""
    try:
        import mlflow
    except ImportError as e:
        raise RuntimeError(
            "mlflow is required for MLflow import. Install with: pip install mlflow"
        ) from e

    # Parse MLflow URL if provided
    if mlflow_url and not mlflow_run_id:
        from projects.caliper.cli.main import parse_mlflow_url

        try:
            parsed = parse_mlflow_url(mlflow_url)
            mlflow_run_id = parsed.get("run_id")
            if not mlflow_tracking_uri:
                mlflow_tracking_uri = parsed.get("tracking_uri")
            if not mlflow_experiment:
                mlflow_experiment = parsed.get("experiment")
            if not mlflow_workspace:
                mlflow_workspace = parsed.get("workspace")
            # If URL contains artifact path, append to the user-specified one
            url_artifact_path = parsed.get("artifact_path", "")
            if url_artifact_path:
                if artifact_path:
                    artifact_path = f"{artifact_path}/{url_artifact_path}"
                else:
                    artifact_path = url_artifact_path

            if verbose:
                click.echo("Parsed MLflow URL:")
                click.echo(f"  Tracking URI: {mlflow_tracking_uri}")
                click.echo(f"  Run ID: {mlflow_run_id}")
                click.echo(f"  Experiment: {mlflow_experiment}")
                click.echo(f"  Workspace: {mlflow_workspace}")
                click.echo(f"  Artifact path: {artifact_path}")
        except Exception as e:
            raise ValueError(f"Failed to parse MLflow URL: {e}") from e

    if not mlflow_run_id:
        raise ValueError("Either --from-mlflow or --from-mlflow-url is required")

    if not mlflow_tracking_uri:
        mlflow_tracking_uri = os.environ.get("MLFLOW_TRACKING_URI")
        if not mlflow_tracking_uri:
            raise ValueError(
                "MLflow tracking URI is required: --mlflow-tracking-uri, MLFLOW_TRACKING_URI, "
                "or provide a complete --from-mlflow-url"
            )

    # Load secrets if provided
    connection_config = {}
    if mlflow_secrets_path:
        try:
            secrets_data = load_mlflow_secrets_yaml(mlflow_secrets_path)
            validate_mlflow_secrets(secrets_data)
            connection_config = secrets_data
        except Exception as e:
            raise ValueError(f"Failed to load MLflow secrets: {e}") from e

    # Set up insecure TLS if needed
    if mlflow_insecure_tls:
        connection_config["insecure_tls"] = True

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    if verbose:
        click.echo("MLflow artifact download:")
        click.echo(f"  Tracking URI: {mlflow_tracking_uri}")
        click.echo(f"  Run ID: {mlflow_run_id}")
        click.echo(f"  Artifact path: {artifact_path or '(root)'}")
        click.echo(f"  Output directory: {output_dir}")
        click.echo(f"  Workspace: {mlflow_workspace or '(none)'}")
        click.echo(f"  Timeout: {timeout}s")
        click.echo(f"  Insecure TLS: {mlflow_insecure_tls}")

    def _download_artifacts() -> None:
        # Set up MLflow environment
        if mlflow_workspace:
            os.environ["MLFLOW_WORKSPACE"] = mlflow_workspace

        if mlflow_insecure_tls:
            try:
                import urllib3

                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:
                pass

        # Set tracking URI and get client
        mlflow.set_tracking_uri(mlflow_tracking_uri)
        client = mlflow.tracking.MlflowClient()

        if verbose:
            click.echo("Connecting to MLflow...")

        # Verify run exists
        try:
            run = client.get_run(mlflow_run_id)
            if verbose:
                click.echo(f"Found run: {run.info.run_name or mlflow_run_id}")
                click.echo(f"Status: {run.info.status}")
                click.echo(f"Experiment ID: {run.info.experiment_id}")
        except Exception as e:
            raise ValueError(f"Failed to access MLflow run {mlflow_run_id}: {e}") from e

        # Download artifacts to a temporary directory first
        import tempfile

        with tempfile.TemporaryDirectory(prefix="caliper_mlflow_import_") as temp_dir:
            temp_path = Path(temp_dir)

            if verbose:
                click.echo(f"Downloading artifacts to temporary directory: {temp_path}")

            try:
                # Download artifacts
                artifact_uri = client.download_artifacts(
                    run_id=mlflow_run_id, path=artifact_path or "", dst_path=str(temp_path)
                )

                if verbose:
                    click.echo(f"Downloaded artifacts to: {artifact_uri}")

                # Find what was actually downloaded
                downloaded_path = Path(artifact_uri)
                if not downloaded_path.exists():
                    raise RuntimeError(
                        f"Download completed but path does not exist: {downloaded_path}"
                    )

                # Count files
                if downloaded_path.is_file():
                    downloaded_files = [downloaded_path]
                else:
                    downloaded_files = list(downloaded_path.rglob("*"))
                    downloaded_files = [f for f in downloaded_files if f.is_file()]

                if not downloaded_files:
                    click.echo("Warning: No files were downloaded")
                    return

                # Move files from temp directory to final output directory
                if downloaded_path.is_file():
                    # Single file download
                    target_file = output_dir / downloaded_path.name
                    shutil.move(str(downloaded_path), str(target_file))
                    if verbose:
                        click.echo(f"Moved file: {target_file.name}")
                else:
                    # Directory download - move contents
                    for file_path in downloaded_files:
                        # Calculate relative path from download root
                        try:
                            rel_path = file_path.relative_to(downloaded_path)
                        except ValueError:
                            # Fallback if paths don't match
                            rel_path = file_path.name

                        target_file = output_dir / rel_path
                        target_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(file_path), str(target_file))

                        if verbose:
                            click.echo(f"Moved file: {rel_path}")

                # Final count of moved files
                final_files = list(output_dir.rglob("*"))
                final_files = [f for f in final_files if f.is_file()]

                click.echo(f"✅ Successfully downloaded {len(final_files)} file(s) to {output_dir}")

                if verbose and final_files:
                    click.echo("Downloaded files:")
                    for file_path in sorted(final_files):
                        try:
                            rel_path = file_path.relative_to(output_dir)
                            click.echo(f"  {rel_path}")
                        except ValueError:
                            click.echo(f"  {file_path}")

            except Exception as e:
                raise RuntimeError(f"Failed to download artifacts from MLflow: {e}") from e

    # Execute download with proper connection context
    if connection_config:
        with mlflow_connection_env(connection_config):
            _download_artifacts()
    else:
        _download_artifacts()
