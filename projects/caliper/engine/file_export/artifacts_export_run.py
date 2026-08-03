"""
Shared implementation for ``caliper artifacts export`` (CLI and orchestration).
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path
from typing import Any

import click
import yaml
from click.core import Context, ParameterSource

from projects.caliper.engine.file_export.mlflow_config import (
    project_metadata_fields,
    validate_mlflow_config,
)
from projects.caliper.engine.file_export.mlflow_secrets import (
    artifacts_export_mlflow_verbose_lines,
    assert_tracking_uri_has_no_userinfo,
    load_mlflow_secrets_yaml,
    project_secrets_fields,
    validate_mlflow_secrets,
)
from projects.caliper.engine.file_export.runner import run_file_export
from projects.caliper.engine.model import FileExportBackendResult


def merge_mlflow_files_with_cli(
    ctx: Context | None,
    *,
    secrets_data: dict[str, Any] | None,
    config_data: dict[str, Any] | None,
    cli_tracking_uri: str | None,
    cli_experiment: str | None,
    cli_run_id: str | None,
    cli_run_name: str | None,
) -> dict[str, Any]:
    """
    Merge settings YAML then secrets, then apply CLI or env (when ctx is the real CLI
    context). When ``ctx`` is None (orchestration), any non-None *cli_\\** value overrides
    merged YAML the same way as a command-line argument.
    """
    merged: dict[str, Any] = {}
    if config_data:
        merged.update(config_data)
    if secrets_data:
        merged.update(secrets_data)

    keys_from_yaml: set[str] = set()
    if secrets_data:
        keys_from_yaml |= set(secrets_data.keys())
    if config_data:
        keys_from_yaml |= set(config_data.keys())

    def apply_str(param: str, yaml_key: str, value: str | None) -> None:
        if value is None:
            return
        if ctx is None:
            merged[yaml_key] = value
            return
        src = ctx.get_parameter_source(param)
        if src == ParameterSource.COMMANDLINE:
            merged[yaml_key] = value
        elif src == ParameterSource.ENVIRONMENT:
            if yaml_key not in keys_from_yaml:
                merged[yaml_key] = value

    apply_str("mlflow_tracking_uri", "tracking_uri", cli_tracking_uri)
    apply_str("mlflow_experiment", "experiment", cli_experiment)
    apply_str("mlflow_run_id", "run_id", cli_run_id)
    apply_str("mlflow_run_name", "run_name", cli_run_name)

    return merged


def write_artifacts_status_yaml(path: Path, results: list[FileExportBackendResult]) -> None:
    """Write per-backend status; MLflow success includes run_url / experiment_url when available."""
    path.parent.mkdir(parents=True, exist_ok=True)
    backends_block: dict[str, Any] = {}
    for r in results:
        entry: dict[str, Any] = {"status": r.status, "detail": r.detail}
        if r.metadata:
            for k, v in r.metadata.items():
                if v is not None:
                    entry[k] = v
        backends_block[r.backend] = entry
    doc = {"caliper_artifacts_export": {"version": 1, "backends": backends_block}}
    path.write_text(
        yaml.safe_dump(doc, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def run_artifacts_export(
    *,
    from_path: Path,
    backend: tuple[str, ...] | list[str],
    mlflow_tracking_uri: str | None = None,
    mlflow_experiment: str | None = None,
    mlflow_run_id: str | None = None,
    mlflow_run_name: str | None = None,
    mlflow_insecure_tls: bool = False,
    mlflow_secrets_path: Path | None = None,
    mlflow_config_data: dict[str, Any] | None = None,
    dry_run: bool = False,
    verbose: bool = False,
    status_yaml_path: Path | None = None,
    upload_workers: int = 10,
    click_context: Context | None = None,
) -> int:
    """
    Run the multi-backend file export. Returns a process exit code (0 = success,
    1 = argument/configuration error, 4 = export or status write failure).

    Non-secret MLflow settings (experiment, run_name, etc.) are passed only as
    ``mlflow_config_data`` (a dict). The CLI and orchestration load a YAML file themselves
    when needed, then call this with the resulting dict.

    TLS and credentials belong in ``mlflow_secrets_path``.
    """
    backends = [b.strip().lower() for b in backend if b.strip()]
    if not backends:
        click.echo(
            "Specify at least one --backend: mlflow "
            "(e.g. --from ./out --backend mlflow --mlflow-endpoint http://...).",
            err=True,
        )
        return 1

    mlflow_connection: dict[str, Any] | None = None
    secrets_data: dict[str, Any] | None = None
    config_data: dict[str, Any] | None = None
    if mlflow_secrets_path is not None:
        try:
            secrets_data = load_mlflow_secrets_yaml(mlflow_secrets_path)
            validate_mlflow_secrets(secrets_data)
        except (OSError, ValueError, TypeError, yaml.YAMLError) as e:
            click.echo(f"Invalid MLflow secrets file: {e}", err=True)
            return 1
    if mlflow_config_data is not None:
        try:
            config_data = dict(mlflow_config_data)
            validate_mlflow_config(config_data)
        except (ValueError, TypeError) as e:
            click.echo(f"Invalid MLflow settings dict: {e}", err=True)
            return 1

    if secrets_data is not None or config_data is not None:
        merged_ml = merge_mlflow_files_with_cli(
            click_context,
            secrets_data=secrets_data,
            config_data=config_data,
            cli_tracking_uri=mlflow_tracking_uri,
            cli_experiment=mlflow_experiment,
            cli_run_id=mlflow_run_id,
            cli_run_name=mlflow_run_name,
        )
        secret_part = project_secrets_fields(merged_ml)
        if secret_part:
            try:
                validate_mlflow_secrets(secret_part)
            except (ValueError, TypeError) as e:
                click.echo(f"Invalid MLflow configuration: {e}", err=True)
                return 1
        if secrets_data is not None:
            mlflow_connection = secret_part if secret_part else None
        mlflow_tracking_uri = merged_ml.get("tracking_uri")
        mlflow_experiment = merged_ml.get("experiment")
        mlflow_run_id = merged_ml.get("run_id")
        mlflow_run_name = merged_ml.get("run_name")
        mlflow_workspace = merged_ml.get("workspace")
        meta = project_metadata_fields(merged_ml)
        mlflow_run_metadata = meta if meta else None
    else:
        mlflow_run_metadata = None
        mlflow_workspace = None

    # Ensure CLI insecure TLS flag is applied to connection
    if mlflow_insecure_tls:
        if mlflow_connection is None:
            mlflow_connection = {"insecure_tls": True}
        else:
            mlflow_connection = dict(mlflow_connection)
            mlflow_connection["insecure_tls"] = True

    if "mlflow" in backends and not dry_run:
        if not mlflow_tracking_uri:
            mlflow_tracking_uri = os.environ.get("MLFLOW_TRACKING_URI")
        if not mlflow_tracking_uri:
            click.echo(
                "MLflow backend requires a tracking URI: --mlflow-tracking-uri / MLFLOW_TRACKING_URI, "
                "or tracking_uri in --mlflow-secrets or --mlflow-config.",
                err=True,
            )
            return 1
    if "mlflow" in backends and mlflow_tracking_uri:
        try:
            assert_tracking_uri_has_no_userinfo(mlflow_tracking_uri)
        except ValueError as e:
            click.echo(f"Invalid MLflow tracking URI: {e}", err=True)
            return 1
    if verbose:
        click.echo("caliper artifacts export (verbose)", err=True)
        click.echo(f"  Source: {from_path}", err=True)
        click.echo(f"  Backends: {', '.join(backends)}", err=True)
        click.echo(f"  Dry run: {dry_run}", err=True)
        click.echo(f"  Upload workers: {upload_workers}", err=True)
        if "mlflow" in backends:
            for line in artifacts_export_mlflow_verbose_lines(
                tracking_uri=mlflow_tracking_uri,
                experiment=mlflow_experiment,
                run_id=mlflow_run_id,
                run_name=mlflow_run_name,
                config_is_inline=mlflow_config_data is not None,
                secrets_path=mlflow_secrets_path,
            ):
                click.echo(line, err=True)
        click.echo("", err=True)
    try:
        results = run_file_export(
            source=from_path,
            backends=backends,
            dry_run=dry_run,
            mlflow_tracking_uri=mlflow_tracking_uri,
            mlflow_experiment=mlflow_experiment,
            mlflow_run_id=mlflow_run_id,
            mlflow_run_name=mlflow_run_name,
            mlflow_insecure_tls=mlflow_insecure_tls,
            mlflow_connection=mlflow_connection,
            verbose=verbose,
            upload_workers=upload_workers,
            mlflow_run_metadata=mlflow_run_metadata,
            mlflow_workspace=mlflow_workspace,
        )
    except Exception as e:  # noqa: BLE001
        traceback.print_exception(e, file=sys.stderr)
        click.echo(f"artifacts export failed: {e}", err=True)
        return 4
    for r in results:
        click.echo(f"{r.backend}: {r.status} {r.detail}")
    if status_yaml_path is not None:
        try:
            write_artifacts_status_yaml(status_yaml_path, results)
            click.echo(f"Wrote status YAML to {status_yaml_path}")
        except OSError as e:
            click.echo(f"Failed to write status YAML ({status_yaml_path}): {e}", err=True)
            return 4
    if any(r.status == "failure" for r in results):
        return 4
    return 0
