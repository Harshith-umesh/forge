"""KPI analysis CLI entrypoint for Caliper."""

import sys
from pathlib import Path

import click


@click.command("analyze")
@click.option(
    "--current",
    type=click.Path(path_type=Path, exists=True),
    required=True,
    help="Path to current KPI JSON file",
)
@click.option(
    "--historical-dir",
    type=click.Path(path_type=Path, exists=True),
    required=True,
    help="Directory containing historical KPI files",
)
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    required=True,
    help="Output file for analysis results",
)
@click.option(
    "--plugin",
    "plugin_module",
    required=True,
    help="Plugin module name for analysis",
)
def analyze_cli(current: Path, historical_dir: Path, output: Path, plugin_module: str) -> None:
    """CLI entrypoint for KPI analysis."""
    from projects.caliper.engine.kpi.analyze import (
        analyze_kpis_with_format_conversion,
        status_dict_to_exit_code,
    )

    try:
        # Call the core analysis function from engine (returns status dict)
        result = analyze_kpis_with_format_conversion(
            current_kpis_file=current,
            historical_kpis_dir=historical_dir,
            output_file=output,
            plugin_module=plugin_module,
        )

        # Convert status dict to exit code for CLI
        exit_code = status_dict_to_exit_code(result)

        if result.get("success"):
            click.echo(f"✅ Analysis completed successfully. Results written to: {output}")
            if result.get("regressions_detected"):
                click.echo("⚠️  Regressions detected in analysis results")
            elif result.get("message"):
                click.echo(f"ℹ️  {result.get('message')}")
        else:
            error_msg = result.get("error", "Unknown error")
            click.echo(f"❌ Analysis failed: {error_msg}", err=True)

        if exit_code != 0:
            sys.exit(exit_code)

    except Exception as e:
        click.echo(f"❌ Analysis failed: {e}", err=True)
        sys.exit(1)
