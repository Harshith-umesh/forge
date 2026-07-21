"""CLI command for S3 export functionality."""

from __future__ import annotations

import sys
from pathlib import Path

import click


@click.command("s3-export")
@click.option(
    "--kpis-file",
    type=click.Path(path_type=Path, exists=True),
    help="Path to the kpis.json file to upload",
)
@click.option(
    "--csv-file",
    type=click.Path(path_type=Path, exists=True),
    help="Path to the CSV file to upload",
)
@click.option(
    "--ai-data-dir",
    type=click.Path(path_type=Path, exists=True),
    help="Path to the AI data directory to upload",
)
@click.option(
    "--analysis-file",
    type=click.Path(path_type=Path, exists=True),
    help="Path to the analysis file to upload (optional)",
)
@click.option("--bucket", required=True, help="S3 bucket name")
@click.option("--prefix", default="", help="S3 object prefix/path")
@click.option("--instance", help="Instance identifier for S3 organization")
@click.option("--directory", help="Directory identifier for S3 organization")
@click.option("--upload-id", help="Custom upload identifier (uses timestamp if not provided)")
@click.option(
    "--vault", default="psap-forge-aws-s3-export", help="Vault containing AWS credentials"
)
@click.option(
    "--aws-credentials-file", default="aws.credentials", help="Credentials file name within vault"
)
@click.option(
    "--dry-run", is_flag=True, help="Show what would be uploaded without actually uploading"
)
@click.option("-v", "--verbose", is_flag=True, help="Show detailed progress information")
@click.option(
    "--status-file", type=click.Path(path_type=Path), help="YAML file to write operation status"
)
@click.pass_context
def s3_export_cmd(
    ctx: click.Context,
    kpis_file: Path | None,
    csv_file: Path | None,
    ai_data_dir: Path | None,
    analysis_file: Path | None,
    bucket: str,
    prefix: str,
    instance: str | None,
    directory: str | None,
    upload_id: str | None,
    vault: str,
    aws_credentials_file: str,
    dry_run: bool,
    verbose: bool,
    status_file: Path | None,
) -> None:
    """Upload postprocess artifacts to S3."""
    import yaml

    status_data = {"success": False}

    try:
        # Validate that at least one file/directory is provided
        if not any([kpis_file, csv_file, ai_data_dir, analysis_file]):
            error_msg = "At least one file or directory must be specified"
            status_data = {"success": False, "error": error_msg}
            click.echo("❌ Error: At least one file or directory must be specified", err=True)
            click.echo(
                "   Use --kpis-file, --csv-file, --ai-data-dir, or --analysis-file", err=True
            )
            if not status_file:
                sys.exit(1)

        # Import S3 functions
        from projects.caliper.cli.s3_export import run_s3_export_with_explicit_paths
        from projects.core.library import vault as vault_lib

        # Initialize vault system
        vault_lib.init(vaults=[vault] if vault else [])

        # Show command being executed
        enabled_flags = []
        if dry_run:
            enabled_flags.append("--dry-run")
        if verbose:
            enabled_flags.append("--verbose")

        click.echo(
            f"📤 Running S3 export with flags: {' '.join(enabled_flags) if enabled_flags else '(no optional flags)'}"
        )

        if verbose:
            click.echo("📁 Files to upload:")
            if kpis_file:
                click.echo(f"   • KPIs JSON: {kpis_file}")
            if csv_file:
                click.echo(f"   • CSV file: {csv_file}")
            if analysis_file:
                click.echo(f"   • Analysis file: {analysis_file}")
            if ai_data_dir:
                click.echo(f"   • AI data directory: {ai_data_dir}")
            click.echo(f"🪣 Target S3 bucket: {bucket}")
            if prefix:
                click.echo(f"📂 S3 prefix: {prefix}")
            if instance:
                click.echo(f"🏷️  Instance: {instance}")
            if directory:
                click.echo(f"📂 Directory: {directory}")
            if upload_id:
                click.echo(f"🆔 Upload ID: {upload_id}")

        # Run S3 export with explicit file paths
        result = run_s3_export_with_explicit_paths(
            kpis_file=kpis_file,
            csv_file=csv_file,
            ai_data_dir=ai_data_dir,
            analysis_file=analysis_file,
            bucket=bucket,
            instance=instance,
            directory=directory,
            upload_id=upload_id,
            vault=vault,
            aws_credentials_file=aws_credentials_file,
            dry_run=dry_run,
        )

        if result["status"] == "success":
            status_data = {
                "success": True,
                "exported_path": result.get("exported_path", ""),
                "uploaded_files": result.get("uploaded_files", 0),
                "total_files": result.get("total_files", 0),
                "dry_run": dry_run,
            }
            if dry_run:
                click.echo("✅ Dry run completed - see upload plan")
                if "dry_run_file" in result:
                    click.echo(f"📋 Upload plan saved to: {result['dry_run_file']}")
                    status_data["dry_run_file"] = result["dry_run_file"]
            else:
                click.echo("✅ S3 export completed successfully")
                if "uploaded_files" in result:
                    click.echo(f"📤 Uploaded {result['uploaded_files']} files")

            if verbose and "exported_path" in result:
                click.echo(f"🌍 S3 location: {result['exported_path']}")
        else:
            error_msg = result.get("error", "unknown error")
            status_data = {"success": False, "error": error_msg}
            click.echo(f"❌ S3 export failed: {error_msg}", err=True)
            if not status_file:
                sys.exit(1)

    except Exception as e:  # noqa: BLE001
        import traceback

        full_traceback = traceback.format_exc()
        status_data = {"success": False, "error": str(e), "traceback": full_traceback}
        click.echo(f"❌ S3 export failed: {e}", err=True)
        click.echo(f"Full traceback:\n{full_traceback}", err=True)
        if not status_file:
            sys.exit(2)
    finally:
        # Write status file if requested
        if status_file:
            try:
                with open(status_file, "w", encoding="utf-8") as f:
                    yaml.dump(status_data, f, default_flow_style=False)
            except Exception as status_err:
                click.echo(f"Failed to write status file {status_file}: {status_err}", err=True)
                sys.exit(4)

    # Exit with error code if operation failed
    if not status_data.get("success", False):
        sys.exit(3)
