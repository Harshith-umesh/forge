"""Caliper CLI."""

from __future__ import annotations

import re
import signal
import sys
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import parse_qs, urlparse

import click
import yaml

from projects.caliper.engine.ai_eval import run_ai_eval_export
from projects.caliper.engine.file_export.artifacts_export_run import run_artifacts_export
from projects.caliper.engine.file_export.mlflow_config import load_mlflow_config_yaml
from projects.caliper.engine.kpi.analyze import run_analyze
from projects.caliper.engine.kpi.generate import run_kpi_generate
from projects.caliper.engine.kpi.import_export import (
    export_kpis_to_index,
    import_kpis_snapshot,
    load_kpis_jsonl,
)
from projects.caliper.engine.load_plugin import load_plugin
from projects.caliper.engine.parse import run_parse
from projects.caliper.engine.plugin_config import resolve_plugin_module_string
from projects.caliper.engine.visualize import run_visualize

_ARTIFACTS_DIR_HELP = (
    "Root directory of the test artifact tree (directories containing "
    "__test_labels__.yaml). Optional manifest files (e.g. caliper.yaml) are searched here "
    "unless --postprocess-config is set."
)
_PLUGIN_MODULE_HELP = (
    "Python import path of the Caliper plugin module (must expose get_plugin()). "
    "Names the plugin implementation; overrides plugin_module in the manifest when both "
    "are set."
)
_POSTPROCESS_CONFIG_HELP = (
    "Path to the post-processing manifest (YAML). If omitted, conventional filenames "
    "are searched under the artifact tree (--artifacts-dir)."
)


def parse_mlflow_url(url: str) -> dict[str, str | None]:
    """
    Parse MLflow web UI URL and extract components.

    Supports URLs like:
    https://mlflow.apps.example.com/#/experiments/231/runs/3147e102.../artifacts/path?workspace=forge

    Returns dict with: endpoint, experiment_id, run_id, artifact_path, workspace
    """
    try:
        parsed = urlparse(url)

        # Extract base endpoint (scheme + netloc + path)
        endpoint = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

        # Parse fragment (the part after #) - this may contain query params too
        fragment = parsed.fragment or ""
        workspace = None
        artifact_path = None
        experiment_id = None
        run_id = None

        # Split fragment into path and query parts
        if "?" in fragment:
            fragment_path, fragment_query = fragment.split("?", 1)
            # Parse fragment query parameters (for workspace)
            fragment_params = parse_qs(fragment_query)
            workspace = fragment_params.get("workspace", [None])[0]
        else:
            fragment_path = fragment

        # Also check main URL query parameters (fallback)
        if not workspace and parsed.query:
            query_params = parse_qs(parsed.query)
            workspace = query_params.get("workspace", [None])[0]

        # Parse the fragment path: /experiments/231/runs/3147e102.../artifacts/path
        if fragment_path.startswith("/"):
            fragment_path = fragment_path[1:]  # Remove leading slash

        parts = fragment_path.split("/")

        # Expected patterns:
        # 1. experiments/231/runs/RUN_ID/artifacts/PATH (standard)
        # 2. experiments/231/runs/RUN_ID/PATH (direct artifact path)
        if len(parts) >= 4 and parts[0] == "experiments" and parts[2] == "runs":
            experiment_id = parts[1]
            run_id = parts[3]

            # Check for artifacts path
            if len(parts) > 4:
                if parts[4] == "artifacts":
                    # Standard format: /experiments/231/runs/RUN_ID/artifacts/PATH
                    if len(parts) > 5:
                        artifact_path = "/".join(parts[5:])
                else:
                    # Direct format: /experiments/231/runs/RUN_ID/PATH
                    artifact_path = "/".join(parts[4:])

        return {
            "endpoint": endpoint,
            "experiment_id": experiment_id,
            "run_id": run_id,
            "artifact_path": artifact_path,
            "workspace": workspace,
        }

    except Exception as e:
        raise ValueError(f"Could not parse MLflow URL: {e}") from e


def validate_mlflow_url_components(components: dict[str, str | None]) -> None:
    """Validate that required components were extracted from URL."""
    if not components["endpoint"]:
        raise ValueError("Could not extract MLflow server endpoint from URL")
    if not components["run_id"]:
        raise ValueError("Could not extract run ID from URL")
    if not re.match(r"^[0-9a-f]{32}$", (components["run_id"] or "").replace("-", "")):
        raise ValueError(f"Invalid run ID format: {components['run_id']}")


def parse_and_validate_url(
    mlflow_url: str | None, mlflow_run_id: str | None, verbose: bool = False
) -> tuple[str, str | None, str | None, str | None, str | None]:
    """
    Parse MLflow URL and extract components or validate existing parameters.

    Returns: (run_id, tracking_uri, experiment, workspace, artifact_path)
    """
    if mlflow_url:
        if mlflow_run_id:
            raise ValueError("Cannot specify both --from-mlflow and --from-mlflow-url")

        if verbose:
            click.echo("Parsing MLflow URL...")

        url_components = parse_mlflow_url(mlflow_url)
        if verbose:
            click.echo("URL parsed successfully")
        validate_mlflow_url_components(url_components)

        if verbose:
            click.echo("Extracted from URL:")
            click.echo(f"  - Experiment ID: {url_components['experiment_id'] or '(not found)'}")
            click.echo(f"  - Run ID: {url_components['run_id']}")
            click.echo(f"  - Artifact path: {url_components['artifact_path'] or '(all artifacts)'}")
            click.echo(f"  - Workspace: {url_components['workspace'] or '(not specified)'}")

        return (
            url_components["run_id"],
            url_components["endpoint"],
            url_components["experiment_id"],
            url_components["workspace"],
            url_components["artifact_path"],
        )
    else:
        if not mlflow_run_id:
            raise ValueError("Specify source: --from-mlflow RUN_ID or --from-mlflow-url URL")
        return (mlflow_run_id, None, None, None, None)


def setup_mlflow_connection(
    mlflow_secrets_path: Path | None,
    mlflow_insecure_tls: bool,
    tracking_uri: str | None,
    workspace: str | None,
    verbose: bool = False,
) -> tuple[str, bool]:
    """
    Setup MLflow connection with secrets and SSL configuration.

    Returns: (final_tracking_uri, final_insecure_tls)
    """
    import os

    final_tracking_uri = tracking_uri
    final_insecure_tls = mlflow_insecure_tls

    # Load secrets file if provided
    if mlflow_secrets_path:
        if verbose:
            click.echo(f"Loading MLflow secrets from: {mlflow_secrets_path}")

        from projects.caliper.engine.file_export.mlflow_secrets import (
            connection_to_env,
            load_mlflow_secrets_yaml,
            validate_mlflow_secrets,
        )

        secrets_data = load_mlflow_secrets_yaml(mlflow_secrets_path)
        validate_mlflow_secrets(secrets_data)

        # Apply secrets to environment using the proper function
        env_updates = connection_to_env(secrets_data)
        for key, value in env_updates.items():
            os.environ[key] = value
            # Update connection settings regardless of verbose mode
            if key == "MLFLOW_TRACKING_URI":
                final_tracking_uri = value
            elif key == "MLFLOW_TRACKING_INSECURE_TLS":
                final_insecure_tls = True

            # Log only in verbose mode
            if verbose:
                if key == "MLFLOW_TRACKING_TOKEN":
                    click.echo("Set authentication token from secrets")
                elif key == "MLFLOW_TRACKING_USERNAME":
                    click.echo("Set username from secrets")
                elif key == "MLFLOW_TRACKING_PASSWORD":
                    click.echo("Set password from secrets")
                elif key == "MLFLOW_TRACKING_URI":
                    click.echo("Set tracking URI from secrets")
                elif key == "MLFLOW_TRACKING_INSECURE_TLS":
                    click.echo("Enabled insecure TLS from secrets")

    # Handle workspace if provided
    if workspace:
        if verbose:
            click.echo(f"Setting MLflow workspace: {workspace}")
        os.environ["MLFLOW_WORKSPACE"] = workspace

    # Configure SSL verification
    if final_insecure_tls:
        if verbose:
            click.echo("Disabling SSL verification for MLflow server")
        os.environ["MLFLOW_TRACKING_INSECURE_TLS"] = "true"
        # Also disable urllib3 SSL warnings
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    elif verbose:
        click.echo("SSL verification enabled")

    return final_tracking_uri, final_insecure_tls


def setup_mlflow_client_and_experiment(
    tracking_uri: str, experiment_id: str | None, verbose: bool = False
):
    """
    Create MLflow client and set up experiment context.

    Returns: (client, run_info)
    """
    if verbose:
        click.echo("Connecting to MLflow server...")

    import mlflow
    from mlflow.tracking import MlflowClient

    mlflow.set_tracking_uri(tracking_uri)
    client = MlflowClient()

    # Set experiment if provided
    if experiment_id:
        if verbose:
            click.echo(f"Setting experiment: {experiment_id}")
        try:
            # Try to get experiment by ID first, then by name
            if experiment_id.isdigit():
                experiment = client.get_experiment(experiment_id)
                if verbose:
                    click.echo(
                        f"Found experiment by ID: {experiment.name} (ID: {experiment.experiment_id})"
                    )
            else:
                experiment = client.get_experiment_by_name(experiment_id)
                if verbose:
                    click.echo(
                        f"Found experiment by name: {experiment.name} (ID: {experiment.experiment_id})"
                    )

            # Set the experiment for this session
            mlflow.set_experiment(experiment_id=experiment.experiment_id)
        except Exception as e:
            click.echo(f"Warning: Could not set experiment '{experiment_id}': {e}", err=True)
            click.echo("Continuing without specific experiment...", err=True)

    return client


def download_artifacts_with_progress(
    client, run_id: str, artifact_path: str, output_dir: Path, timeout: int, verbose: bool = False
) -> Path:
    """
    Download artifacts with timeout and progress reporting.

    Returns: downloaded_path
    """
    # Validate run exists first
    if verbose:
        click.echo(f"Checking if run exists: {run_id}")

    run_info = client.get_run(run_id)
    if verbose:
        click.echo("Run details:")
        click.echo(f"  - Run ID: {run_info.info.run_id}")
        click.echo(f"  - Run Name: {run_info.info.run_name or '(unnamed)'}")
        click.echo(f"  - Experiment ID: {run_info.info.experiment_id}")
        click.echo(f"  - Status: {run_info.info.status}")
        click.echo(f"  - Lifecycle Stage: {run_info.info.lifecycle_stage}")
    click.echo(f"Found run: {run_info.info.run_name or run_id}")

    # Download artifacts
    output_dir.mkdir(parents=True, exist_ok=True)
    if verbose:
        click.echo(f"Output directory: {output_dir.absolute()}")
        if artifact_path:
            click.echo(f"Downloading specific artifact path: {artifact_path}")
        else:
            click.echo("Downloading all artifacts from run")

    # List available artifacts in verbose mode
    if verbose:
        try:
            click.echo("Listing available artifacts...")
            artifacts = client.list_artifacts(run_id=run_id, path=artifact_path)
            if artifacts:
                click.echo(f"Found {len(artifacts)} artifact(s) to download:")
                for artifact in artifacts:
                    if artifact.is_dir:
                        click.echo(f"  📁 {artifact.path}/ (directory)")
                    else:
                        size_str = f" ({artifact.file_size:,} bytes)" if artifact.file_size else ""
                        click.echo(f"  📄 {artifact.path}{size_str}")
            else:
                click.echo("No artifacts found for the specified path")
                if artifact_path:
                    click.echo(f"Check that artifact path '{artifact_path}' exists in the run")
        except Exception as e:
            click.echo(f"Warning: Could not list artifacts: {e}", err=True)
            click.echo("Proceeding with download anyway...", err=True)

    # Download artifacts to the output directory with timeout
    click.echo(f"Downloading artifacts from run {run_id}...")

    def timeout_handler(signum, frame):
        raise TimeoutError(f"Download operation timed out after {timeout} seconds")

    # Set up timeout
    if verbose:
        click.echo(f"Setting download timeout to {timeout} seconds")

    import signal

    old_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(timeout)

    try:
        if verbose:
            click.echo("Starting download...")
        downloaded_path = client.download_artifacts(
            run_id=run_id, path=artifact_path, dst_path=str(output_dir)
        )
        if verbose:
            click.echo(f"Download completed to: {downloaded_path}")
        return Path(downloaded_path)
    finally:
        # Restore the old signal handler and cancel the alarm
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def format_download_results(downloaded_path: Path, output_dir: Path, verbose: bool = False) -> None:
    """Format and display download results."""
    # Count downloaded files
    if verbose:
        click.echo("Analyzing downloaded files...")

    if downloaded_path.is_file():
        downloaded_files = [downloaded_path]
        if verbose:
            click.echo(f"Downloaded single file: {downloaded_path.name}")
    else:
        downloaded_files = list(downloaded_path.rglob("*"))
        downloaded_files = [f for f in downloaded_files if f.is_file()]
        if verbose:
            click.echo(f"Downloaded directory contains {len(downloaded_files)} files")

    click.echo(f"Downloaded {len(downloaded_files)} files to {output_dir}")

    if downloaded_files:
        if verbose:
            # Show all files in verbose mode with detailed info
            click.echo("Successfully downloaded files:")

            # Group files by directory for better organization
            from collections import defaultdict

            dirs = defaultdict(list)
            for file in downloaded_files:
                rel_path = file.relative_to(output_dir)
                parent_dir = str(rel_path.parent) if rel_path.parent.name else "."
                dirs[parent_dir].append((rel_path, file))

            for dir_path in sorted(dirs.keys()):
                if dir_path != ".":
                    click.echo(f"  📁 {dir_path}/")

                for rel_path, file in sorted(dirs[dir_path], key=lambda x: x[0].name):
                    file_size = file.stat().st_size
                    size_mb = file_size / (1024 * 1024)
                    if size_mb >= 1:
                        size_str = f"({size_mb:.1f} MB)"
                    else:
                        size_str = f"({file_size:,} bytes)"

                    # Add appropriate emoji based on file type
                    ext = rel_path.suffix.lower()
                    if ext in [".yaml", ".yml", ".json"]:
                        emoji = "📋"
                    elif ext in [".py", ".sh", ".js"]:
                        emoji = "📝"
                    elif ext in [".log", ".txt"]:
                        emoji = "📄"
                    elif ext in [".png", ".jpg", ".jpeg", ".svg"]:
                        emoji = "🖼️"
                    elif ext in [".pkl", ".model", ".pt", ".pth"]:
                        emoji = "🤖"
                    else:
                        emoji = "📄"

                    indent = "    " if dir_path != "." else "  "
                    click.echo(f"{indent}{emoji} {rel_path.name} {size_str}")
        else:
            # Show limited list in normal mode
            click.echo("Downloaded files:")
            for file in downloaded_files[:10]:  # Show first 10
                click.echo(f"  📄 {file.relative_to(output_dir)}")
            if len(downloaded_files) > 10:
                click.echo(f"  ... and {len(downloaded_files) - 10} more (use -v to see all)")

        if verbose:
            total_size = sum(f.stat().st_size for f in downloaded_files)
            total_mb = total_size / (1024 * 1024)
            if total_mb >= 1:
                click.echo(f"📊 Total downloaded: {len(downloaded_files)} files, {total_mb:.2f} MB")
            else:
                click.echo(
                    f"📊 Total downloaded: {len(downloaded_files)} files, {total_size:,} bytes"
                )


def _root_obj(ctx: click.Context) -> dict[str, Any]:
    while ctx.parent is not None:
        ctx = ctx.parent
    return ctx.obj


def _exit_with_help(ctx: click.Context, message: str, code: int = 1) -> NoReturn:
    """Print error line and this command's --help text."""
    click.echo(f"Error: {message}\n", err=True)
    click.echo(ctx.get_help(), err=True)
    ctx.exit(code)


def _require_artifacts_dir(ctx: click.Context) -> Path:
    obj = _root_obj(ctx)
    bd = obj.get("base_dir")
    if bd is None:
        _exit_with_help(
            ctx,
            "This command requires the test artifact tree root: "
            "`--artifacts-dir DIR` "
            "(before or after the subcommand).",
            code=1,
        )
    return bd  # type: ignore[return-value]


def _apply_workspace_cli_overrides(
    ctx: click.Context,
    *,
    artifacts_dir: Path | None,
    postprocess_config: Path | None,
    plugin_module_override: str | None,
) -> None:
    """Merge subcommand-level workspace flags into the root context (group options win if unset)."""
    obj = _root_obj(ctx)
    if artifacts_dir is not None:
        obj["base_dir"] = artifacts_dir
    if postprocess_config is not None:
        obj["postprocess_config"] = postprocess_config
    if plugin_module_override is not None:
        obj["plugin_cli"] = plugin_module_override


def _workspace_cli_options(cmd: Any) -> Any:
    """Repeat global workspace options so they may appear after the subcommand."""
    opts = (
        click.option(
            "--artifacts-dir",
            "artifacts_dir",
            type=click.Path(path_type=Path, exists=True),
            default=None,
            help=(
                "Test artifact tree root (same meaning as before COMMAND). "
                "Repeat here if you prefer flags after the subcommand."
            ),
        ),
        click.option(
            "--postprocess-config",
            type=click.Path(path_type=Path, dir_okay=False, exists=True),
            default=None,
            help=_POSTPROCESS_CONFIG_HELP + " Overrides the global option when set here.",
        ),
        click.option(
            "--plugin",
            "plugin_module_override",
            metavar="MODULE",
            default=None,
            help="Plugin import path; same as global --plugin.",
        ),
    )
    for opt in reversed(opts):
        cmd = opt(cmd)
    return cmd


def _plugin_tuple(ctx: click.Context) -> tuple[str, Any]:
    base_dir = _require_artifacts_dir(ctx)
    obj = _root_obj(ctx)
    pc: Path | None = obj["postprocess_config"]
    cli_p: str | None = obj["plugin_cli"]
    try:
        mod, _manifest_path = resolve_plugin_module_string(
            base_dir=base_dir,
            postprocess_config=pc,
            cli_plugin=cli_p,
        )
    except (ValueError, FileNotFoundError) as e:
        _exit_with_help(ctx, str(e), code=1)
    try:
        plugin = load_plugin(mod)
    except RuntimeError as e:
        _exit_with_help(ctx, str(e), code=2)
    return mod, plugin


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--artifacts-dir",
    "artifacts_dir",
    type=click.Path(path_type=Path, exists=True),
    default=None,
    help=_ARTIFACTS_DIR_HELP,
)
@click.option(
    "--postprocess-config",
    type=click.Path(path_type=Path, dir_okay=False, exists=True),
    default=None,
    help=_POSTPROCESS_CONFIG_HELP,
)
@click.option(
    "--plugin",
    "plugin_module",
    metavar="MODULE",
    default=None,
    help=_PLUGIN_MODULE_HELP,
)
@click.pass_context
def main(
    ctx: click.Context,
    artifacts_dir: Path | None,
    postprocess_config: Path | None,
    plugin_module: str | None,
) -> None:
    """Caliper — artifact post-processing."""
    ctx.ensure_object(dict)
    ctx.obj["base_dir"] = artifacts_dir
    ctx.obj["postprocess_config"] = postprocess_config
    ctx.obj["plugin_cli"] = plugin_module


@main.command("parse")
@_workspace_cli_options
@click.option("--no-cache", is_flag=True, help="Force full parse.")
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Override cache file path.",
)
@click.option(
    "--show-matrix/--no-show-matrix",
    default=True,
    help="Display parameter matrix summary after parsing (default: enabled).",
)
@click.pass_context
def parse_cmd(
    ctx: click.Context,
    no_cache: bool,
    cache_dir: Path | None,
    show_matrix: bool,
    artifacts_dir: Path | None,
    postprocess_config: Path | None,
    plugin_module_override: str | None,
) -> None:
    """
    Parse test artifacts into a unified data model.

    Discovers test directories marked with __test_labels__.yaml (or settings.yaml for
    MatrixBenchmarking), parses artifacts using the specified plugin, and creates a
    unified data model with caching for performance. Shows parameter matrix summary
    of discovered test variations.
    """
    _apply_workspace_cli_overrides(
        ctx,
        artifacts_dir=artifacts_dir,
        postprocess_config=postprocess_config,
        plugin_module_override=plugin_module_override,
    )
    mod, plugin = _plugin_tuple(ctx)
    artifact_root: Path = _root_obj(ctx)["base_dir"]
    try:
        model = run_parse(
            base_dir=artifact_root,
            plugin_module=mod,
            plugin=plugin,
            use_cache=not no_cache,
            show_parameter_matrix=show_matrix,
        )
    except Exception as e:  # noqa: BLE001
        click.echo(f"parse failed: {e}", err=True)
        sys.exit(2)
    click.echo(
        f"Parsed {len(model.unified_result_records)} record(s); cache={model.parse_cache_ref}"
    )


@main.command("visualize")
@_workspace_cli_options
@click.option("--reports", default=None, help="Comma-separated report ids.")
@click.option("--report-group", default=None)
@click.option("--visualize-config", type=click.Path(path_type=Path), default=None)
@click.option("--include-label", multiple=True)
@click.option("--exclude-label", multiple=True)
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path),
    required=True,
)
@click.pass_context
def visualize_cmd(
    ctx: click.Context,
    reports: str | None,
    report_group: str | None,
    visualize_config: Path | None,
    include_label: tuple[str, ...],
    exclude_label: tuple[str, ...],
    output_dir: Path,
    artifacts_dir: Path | None,
    postprocess_config: Path | None,
    plugin_module_override: str | None,
) -> None:
    """
    Generate visual reports and charts from parsed test data.

    Loads test data (runs parse if needed), applies label filters, and generates
    visualization reports using plugin capabilities. Specify individual reports
    via --reports, or use predefined groups via --report-group (from
    visualize-groups.yaml).

    Examples:
      caliper visualize --reports performance_analysis --output-dir /tmp/reports
      caliper visualize --report-group comprehensive --output-dir /tmp/reports
      caliper visualize --include-label model=llama --output-dir /tmp/reports
    """
    _apply_workspace_cli_overrides(
        ctx,
        artifacts_dir=artifacts_dir,
        postprocess_config=postprocess_config,
        plugin_module_override=plugin_module_override,
    )
    mod, plugin = _plugin_tuple(ctx)
    artifact_root: Path = _root_obj(ctx)["base_dir"]
    try:
        paths = run_visualize(
            base_dir=artifact_root,
            plugin_module=mod,
            plugin=plugin,
            output_dir=output_dir,
            reports_csv=reports,
            report_group=report_group,
            visualize_config_path=visualize_config,
            include_pairs=include_label,
            exclude_pairs=exclude_label,
            use_cache=True,
            cache_path=None,
        )
    except Exception as e:  # noqa: BLE001
        click.echo(f"visualize failed: {e}", err=True)
        sys.exit(2)

    click.echo("Wrote: " + ", ".join(paths))


@main.command("list-reports")
@_workspace_cli_options
@click.pass_context
def list_reports_cmd(
    ctx: click.Context,
    artifacts_dir: Path | None,
    postprocess_config: Path | None,
    plugin_module_override: str | None,
):
    """List available reports supported by the plugin."""
    try:
        _apply_workspace_cli_overrides(
            ctx,
            artifacts_dir=artifacts_dir,
            postprocess_config=postprocess_config,
            plugin_module_override=plugin_module_override,
        )
        mod, plugin = _plugin_tuple(ctx)

        # Get plugin docstring to extract report information
        plugin_doc = plugin.__class__.__doc__ or ""

        # Extract available reports from docstring
        reports = []
        in_reports_section = False

        for line in plugin_doc.split("\n"):
            line = line.strip()
            if "Available visual reports:" in line:
                in_reports_section = True
                continue
            elif in_reports_section and line.startswith("*"):
                # Extract report ID from lines like "* ``report_id`` — description"
                if "``" in line:
                    report_parts = line.split("``")
                    if len(report_parts) >= 3:
                        report_id = report_parts[1]
                        description = "``".join(report_parts[2:]).strip(" —").strip()
                        reports.append((report_id, description))
            elif in_reports_section and line and not line.startswith("*"):
                # End of reports section
                break

        if reports:
            click.echo(f"📊 Available reports for plugin {plugin.__class__.__name__}:")
            click.echo()
            for report_id, description in reports:
                click.echo(f"  {report_id:<35} {description}")
        else:
            click.echo(f"❌ No reports found in {plugin.__class__.__name__} plugin docstring")

    except Exception as e:
        click.echo(f"❌ Failed to list reports: {e}", err=True)
        sys.exit(1)


@main.group("kpi")
@click.pass_context
def kpi_group(ctx: click.Context) -> None:
    """KPI generate/import/export/analyze."""


@kpi_group.command("generate")
@_workspace_cli_options
@click.option("--output", type=click.Path(path_type=Path), required=True)
@click.pass_context
def kpi_generate(
    ctx: click.Context,
    output: Path,
    artifacts_dir: Path | None,
    postprocess_config: Path | None,
    plugin_module_override: str | None,
) -> None:
    _apply_workspace_cli_overrides(
        ctx,
        artifacts_dir=artifacts_dir,
        postprocess_config=postprocess_config,
        plugin_module_override=plugin_module_override,
    )
    mod, plugin = _plugin_tuple(ctx)
    artifact_root: Path = _root_obj(ctx)["base_dir"]
    try:
        run_kpi_generate(
            base_dir=artifact_root,
            plugin_module=mod,
            plugin=plugin,
            output=output,
            use_cache=True,
            cache_path=None,
        )
    except Exception as e:  # noqa: BLE001
        click.echo(f"kpi generate failed: {e}", err=True)
        sys.exit(2)
    click.echo(f"Wrote KPIs to {output}")


@kpi_group.command("import")
@click.option("--snapshot", type=click.Path(path_type=Path), required=True)
@click.pass_context
def kpi_import(ctx: click.Context, snapshot: Path) -> None:
    try:
        import_kpis_snapshot(snapshot_path=snapshot)
    except Exception as e:  # noqa: BLE001
        click.echo(f"kpi import failed: {e}", err=True)
        sys.exit(3)
    click.echo(f"Wrote snapshot {snapshot}")


@kpi_group.command("export")
@click.option("--input", "input_path", type=click.Path(path_type=Path), required=True)
@click.option("--dry-run", is_flag=True)
@click.pass_context
def kpi_export(ctx: click.Context, input_path: Path, dry_run: bool) -> None:
    try:
        rows = load_kpis_jsonl(input_path)
        if dry_run:
            click.echo(f"Would export {len(rows)} records")
            return
        export_kpis_to_index(rows)
    except Exception as e:  # noqa: BLE001
        click.echo(f"kpi export failed: {e}", err=True)
        sys.exit(3)
    click.echo("Export complete")


@kpi_group.command("analyze")
@click.option("--current", type=click.Path(path_type=Path), required=True)
@click.option("--baseline", type=click.Path(path_type=Path), required=True)
@click.option("--output", type=click.Path(path_type=Path), required=True)
@click.pass_context
def kpi_analyze(
    ctx: click.Context,
    current: Path,
    baseline: Path,
    output: Path,
) -> None:
    try:
        run_analyze(current_path=current, baseline_path=baseline, output_path=output)
    except Exception as e:  # noqa: BLE001
        click.echo(f"kpi analyze failed: {e}", err=True)
        sys.exit(3)
    click.echo(f"Wrote {output}")


@main.group("artifacts")
@click.pass_context
def artifacts_group(ctx: click.Context) -> None:
    """File artifact export and import."""


@artifacts_group.command("export")
@click.option(
    "--from",
    "from_path",
    type=click.Path(path_type=Path, exists=True),
    required=True,
    help="File or directory to upload (artifact path).",
)
@click.option("--backend", multiple=True, type=str, help="Repeat: mlflow.")
@click.option(
    "--mlflow-tracking-uri",
    "--mlflow-endpoint",
    "mlflow_tracking_uri",
    default=None,
    envvar="MLFLOW_TRACKING_URI",
    help="MLflow tracking server URI (required for mlflow backend unless MLFLOW_TRACKING_URI is set).",
)
@click.option("--mlflow-experiment", default=None, envvar="MLFLOW_EXPERIMENT_NAME")
@click.option("--mlflow-run-id", default=None, envvar="MLFLOW_RUN_ID")
@click.option(
    "--mlflow-run-name",
    default=None,
    envvar="CALIPER_MLFLOW_RUN_NAME",
    help=(
        "Display name for a new MLflow run (ignored when --mlflow-run-id is set; "
        "otherwise MLflow assigns a random name)."
    ),
)
@click.option(
    "--mlflow-insecure-tls",
    is_flag=True,
    help="Do not verify TLS for the MLflow tracking server (self-signed / private CA). "
    "Equivalent to MLFLOW_TRACKING_INSECURE_TLS=true.",
)
@click.option(
    "--mlflow-secrets",
    "mlflow_secrets_path",
    type=click.Path(path_type=Path, exists=True),
    default=None,
    help=(
        "YAML with credentials only: tracking_uri, token or username/password, TLS options. "
        "Keep separate from --mlflow-config. Values apply only for this process."
    ),
)
@click.option(
    "--mlflow-config",
    "mlflow_config_path",
    type=click.Path(path_type=Path, exists=True),
    default=None,
    help=(
        "YAML with non-secret MLflow settings: tracking_uri (optional), experiment, "
        "run_name, run_id. Separate from --mlflow-secrets; secrets file wins on overlapping keys."
    ),
)
@click.option("--dry-run", is_flag=True)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Print detailed progress and configuration on stderr (no secrets).",
)
@click.option(
    "--status-yaml",
    "status_yaml_path",
    type=click.Path(path_type=Path),
    default=None,
    help=(
        "Write a YAML summary of export outcomes, including MLflow run_url and experiment_url "
        "when the mlflow backend succeeds."
    ),
)
@click.option(
    "--upload-workers",
    type=click.IntRange(min=1, max=64),
    default=10,
    show_default=True,
    help="Parallel upload threads (MLflow).",
)
@click.pass_context
def artifacts_export(
    ctx: click.Context,
    from_path: Path,
    backend: tuple[str, ...],
    mlflow_tracking_uri: str | None,
    mlflow_experiment: str | None,
    mlflow_run_id: str | None,
    mlflow_run_name: str | None,
    mlflow_insecure_tls: bool,
    mlflow_secrets_path: Path | None,
    mlflow_config_path: Path | None,
    dry_run: bool,
    verbose: bool,
    status_yaml_path: Path | None,
    upload_workers: int,
) -> None:
    """Upload file artifacts to MLflow."""
    backends = [b.strip().lower() for b in backend if b.strip()]
    if not backends:
        _exit_with_help(
            ctx,
            "Specify at least one --backend: mlflow "
            "(e.g. --from ./out --backend mlflow --mlflow-endpoint http://...).",
            code=1,
        )
    mlflow_config_data: dict | None = None
    if mlflow_config_path is not None:
        try:
            mlflow_config_data = load_mlflow_config_yaml(mlflow_config_path)
        except (OSError, ValueError, TypeError, yaml.YAMLError) as e:
            click.echo(f"Invalid MLflow settings file ({mlflow_config_path}): {e}", err=True)
            sys.exit(1)

    code = run_artifacts_export(
        from_path=from_path,
        backend=backends,
        mlflow_tracking_uri=mlflow_tracking_uri,
        mlflow_experiment=mlflow_experiment,
        mlflow_run_id=mlflow_run_id,
        mlflow_run_name=mlflow_run_name,
        mlflow_insecure_tls=mlflow_insecure_tls,
        mlflow_secrets_path=mlflow_secrets_path,
        mlflow_config_data=mlflow_config_data,
        dry_run=dry_run,
        verbose=verbose,
        status_yaml_path=status_yaml_path,
        upload_workers=upload_workers,
        click_context=ctx,
    )
    if code != 0:
        sys.exit(code)


@main.command("ai-eval-export", hidden=True)
@_workspace_cli_options
@click.option("--output", type=click.Path(path_type=Path), required=True)
@click.pass_context
def ai_eval_export(
    ctx: click.Context,
    output: Path,
    artifacts_dir: Path | None,
    postprocess_config: Path | None,
    plugin_module_override: str | None,
) -> None:
    _apply_workspace_cli_overrides(
        ctx,
        artifacts_dir=artifacts_dir,
        postprocess_config=postprocess_config,
        plugin_module_override=plugin_module_override,
    )
    mod, plugin = _plugin_tuple(ctx)
    artifact_root: Path = _root_obj(ctx)["base_dir"]
    try:
        run_ai_eval_export(
            base_dir=artifact_root,
            plugin_module=mod,
            plugin=plugin,
            output=output,
            use_cache=True,
        )
    except Exception as e:  # noqa: BLE001
        click.echo(f"ai-eval-export failed: {e}", err=True)
        sys.exit(2)
    click.echo(f"Wrote {output}")


@artifacts_group.command("import")
@click.option("--from-mlflow", "mlflow_run_id", help="MLflow run ID to download artifacts from.")
@click.option(
    "--from-mlflow-url",
    "mlflow_url",
    help="MLflow web UI URL (alternative to specifying components separately). "
    "Example: https://mlflow.example.com/#/experiments/231/runs/RUN_ID/artifacts/path?workspace=ws",
)
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path),
    required=True,
    help="Local directory to download artifacts to.",
)
@click.option(
    "--mlflow-tracking-uri",
    envvar="MLFLOW_TRACKING_URI",
    help="MLflow tracking server URI (can be set via MLFLOW_TRACKING_URI).",
)
@click.option(
    "--artifact-path",
    default="",
    help="Specific artifact path to download (default: download all artifacts).",
)
@click.option(
    "--timeout",
    default=300,
    type=click.IntRange(min=1),
    help="Download timeout in seconds (default: 300).",
)
@click.option(
    "--mlflow-insecure-tls",
    is_flag=True,
    help="Do not verify TLS for the MLflow tracking server (self-signed / private CA). "
    "Equivalent to MLFLOW_TRACKING_INSECURE_TLS=true.",
)
@click.option(
    "--mlflow-experiment",
    default=None,
    help="MLflow experiment name or ID (helps locate the run).",
)
@click.option(
    "--mlflow-workspace",
    default=None,
    help="MLflow workspace name (if using multi-workspace setup).",
)
@click.option(
    "--mlflow-secrets",
    "mlflow_secrets_path",
    type=click.Path(path_type=Path, exists=True),
    default=None,
    help=(
        "YAML with credentials: tracking_uri, token or username/password, TLS options. "
        "Values apply only for this process."
    ),
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Show detailed progress and debug information.",
)
@click.pass_context
def import_command(
    ctx: click.Context,
    mlflow_run_id: str | None,
    mlflow_url: str | None,
    output_dir: Path,
    mlflow_tracking_uri: str | None,
    artifact_path: str,
    timeout: int,
    mlflow_insecure_tls: bool,
    mlflow_experiment: str | None,
    mlflow_workspace: str | None,
    mlflow_secrets_path: Path | None,
    verbose: bool,
) -> None:
    """Download artifact files from MLflow.

    Authentication:
      Use --mlflow-secrets to provide credentials via YAML file

    Examples:
      # Download using full MLflow URL (easiest - just copy from browser)
      caliper artifacts import --from-mlflow-url "https://mlflow.example.com/#/experiments/231/runs/RUN_ID/artifacts/path?workspace=ws" --output-dir ./out --mlflow-secrets credentials.yaml

      # Download with individual components
      caliper artifacts import --from-mlflow 3147e102d0d34fdda34b7a5aa6e0bb0d --mlflow-experiment 231 --mlflow-workspace forge-llmd --output-dir ./out --mlflow-secrets credentials.yaml -v

      # Connect to server with self-signed certificate
      caliper artifacts import --from-mlflow RUN_ID --output-dir ./out --mlflow-tracking-uri https://internal-mlflow.corp.com --mlflow-insecure-tls

    Secrets file format (credentials.yaml):
      tracking_uri: https://mlflow.example.com
      token: your_auth_token
      # OR username/password:
      # username: your_username
      # password: your_password
      insecure_tls: true  # optional
    """
    try:
        # Step 1: Parse URL or validate parameters
        (
            run_id,
            tracking_uri_from_url,
            experiment_from_url,
            workspace_from_url,
            artifact_path_from_url,
        ) = parse_and_validate_url(mlflow_url, mlflow_run_id, verbose)

        # Combine CLI args with URL values (CLI takes precedence)
        final_tracking_uri = mlflow_tracking_uri or tracking_uri_from_url
        final_experiment = mlflow_experiment or experiment_from_url
        final_workspace = mlflow_workspace or workspace_from_url
        final_artifact_path = artifact_path or artifact_path_from_url or ""

        if not final_tracking_uri:
            click.echo(
                "Error: MLflow tracking URI required. Set --mlflow-tracking-uri or use --from-mlflow-url with full URL.",
                err=True,
            )
            sys.exit(1)

        # Step 2: Import MLflow with timeout protection
        if verbose:
            click.echo("Importing MLflow...")

        def import_timeout_handler(signum, frame):
            raise TimeoutError(
                "MLflow import timed out after 30 seconds - check your Python environment"
            )

        old_handler = signal.signal(signal.SIGALRM, import_timeout_handler)
        signal.alarm(30)

        try:
            import mlflow

            if verbose:
                click.echo(f"Importing MLflow... done (version: {mlflow.__version__})")
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
        except Exception:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
            raise

        # Step 3: Setup connection (secrets, SSL, workspace)
        try:
            final_tracking_uri, final_insecure_tls = setup_mlflow_connection(
                mlflow_secrets_path,
                mlflow_insecure_tls,
                final_tracking_uri,
                final_workspace,
                verbose,
            )
        except (OSError, ValueError, TypeError) as e:
            click.echo(f"Error loading MLflow secrets file: {e}", err=True)
            sys.exit(1)

        # Step 4: Validate run ID format
        if verbose:
            click.echo(f"Validating run ID format: {run_id}")

        if not re.match(r"^[0-9a-f]{32}$", run_id.replace("-", "")):
            click.echo(
                f"Error: Invalid MLflow run ID format: {run_id}\n"
                f"Expected a UUID like: 3147e102d0d34fdda34b7a5aa6e0bb0d\n"
                f"Got what appears to be a URL path. Use only the run ID, not the full URL.",
                err=True,
            )
            sys.exit(1)

        # Step 5: Setup MLflow client and experiment
        client = setup_mlflow_client_and_experiment(final_tracking_uri, final_experiment, verbose)

        # Step 6: Download artifacts
        downloaded_path = download_artifacts_with_progress(
            client, run_id, final_artifact_path, output_dir, timeout, verbose
        )

        # Step 7: Display results
        format_download_results(downloaded_path, output_dir, verbose)

    except KeyboardInterrupt:
        click.echo("\nOperation cancelled by user (Ctrl+C)", err=True)
        sys.exit(1)
    except TimeoutError as e:
        click.echo(f"Error: Operation timed out: {e}", err=True)
        click.echo("Try again or check network connectivity to the MLflow server.", err=True)
        sys.exit(1)
    except PermissionError as e:
        click.echo(f"Error: Permission denied: {e}", err=True)
        click.echo("Check authentication credentials or file permissions.", err=True)
        sys.exit(1)
    except FileNotFoundError as e:
        click.echo(f"Error: File or directory not found: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        error_msg = str(e).lower()

        if "ssl" in error_msg or "certificate" in error_msg:
            click.echo(f"Error: SSL certificate verification failed: {str(e)}", err=True)
            click.echo(
                "For servers with self-signed certificates, use: --mlflow-insecure-tls", err=True
            )
        elif "connection" in error_msg or "network" in error_msg:
            click.echo(f"Error: Network connection failed: {str(e)}", err=True)
            click.echo("Check that the MLflow tracking URI is accessible.", err=True)
        elif "authentication" in error_msg or "unauthorized" in error_msg:
            click.echo("Error: Authentication failed", err=True)
            click.echo("Check your MLflow credentials and server access.", err=True)
        elif "timeout" in error_msg:
            click.echo(f"Error: Operation timed out: {str(e)}", err=True)
            click.echo("The MLflow server may be slow or unreachable.", err=True)
        else:
            click.echo(f"artifacts import failed: {str(e)}", err=True)
        sys.exit(2)


def run_cli() -> None:
    """Invoke CLI; on missing required options, print subcommand help."""
    try:
        # standalone_mode=False returns exit codes instead of calling sys.exit;
        # propagate them so failures are non-zero (e.g. ctx.exit(1) from _exit_with_help).
        rv = main.main(standalone_mode=False, prog_name="caliper")
        if isinstance(rv, int) and rv != 0:
            sys.exit(rv)
    except click.ClickException as exc:
        # Handle click exceptions including NoArgsIsHelpError and MissingParameter
        if isinstance(exc, click.MissingParameter):
            click.echo(f"Error: Missing required parameter: {exc.param.name}", err=True)
            if exc.param.name == "output_dir":
                click.echo("The --output-dir parameter is mandatory for artifact import.", err=True)
            click.echo("", err=True)
        if hasattr(exc, "ctx") and exc.ctx:
            click.echo(exc.ctx.get_help(), err=True)
        else:
            exc.show(sys.stderr)
        sys.exit(2)
    except SystemExit:
        raise


if __name__ == "__main__":
    run_cli()
