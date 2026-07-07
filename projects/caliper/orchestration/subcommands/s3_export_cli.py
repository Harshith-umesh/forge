"""CLI command for S3 export functionality."""

from __future__ import annotations

import sys
from pathlib import Path

import click


@click.command("s3-export")
@click.option(
    "--from-dir",
    "from_dir",
    type=click.Path(path_type=Path, exists=True),
    required=True,
    help="Directory containing files to upload",
)
@click.option("--bucket", required=True, help="S3 bucket name")
@click.option("--prefix", default="", help="S3 object prefix/path")
@click.option("--instance", help="Instance identifier for S3 organization")
@click.option("--directory", help="Directory identifier for S3 organization")
@click.option("--upload-id", help="Custom upload identifier (uses timestamp if not provided)")
@click.option("--include-csv", is_flag=True, default=True, help="Include CSV files in upload")
@click.option(
    "--include-kpis-json", is_flag=True, default=True, help="Include KPI JSON files in upload"
)
@click.option("--include-ai-data", is_flag=True, default=True, help="Include AI data in upload")
@click.option(
    "--ai-data-dir",
    type=click.Path(path_type=Path),
    help="AI data directory (if different from from-dir)",
)
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
@click.pass_context
def s3_export_cmd(
    ctx: click.Context,
    from_dir: Path,
    bucket: str,
    prefix: str,
    instance: str | None,
    directory: str | None,
    upload_id: str | None,
    include_csv: bool,
    include_kpis_json: bool,
    include_ai_data: bool,
    ai_data_dir: Path | None,
    vault: str,
    aws_credentials_file: str,
    dry_run: bool,
    verbose: bool,
) -> None:
    """Upload postprocess artifacts to S3."""
    try:
        # Import S3 functions
        from projects.caliper.orchestration.subcommands.s3_export import run_s3_export
        from projects.core.library import vault as vault_lib

        # Initialize vault system
        vault_lib.init(vaults=[vault] if vault else [])

        # Show command being executed with enabled flags
        enabled_flags = []
        if include_csv:
            enabled_flags.append("--include-csv")
        if include_kpis_json:
            enabled_flags.append("--include-kpis-json")
        if include_ai_data:
            enabled_flags.append("--include-ai-data")
        if dry_run:
            enabled_flags.append("--dry-run")
        if verbose:
            enabled_flags.append("--verbose")

        click.echo(
            f"📤 Running S3 export with flags: {' '.join(enabled_flags) if enabled_flags else '(no optional flags)'}"
        )

        if verbose:
            click.echo(f"📁 Source directory: {from_dir}")
            click.echo(f"🪣 Target S3 bucket: {bucket}")
            if prefix:
                click.echo(f"📂 S3 prefix: {prefix}")
            if instance:
                click.echo(f"🏷️  Instance: {instance}")
            if directory:
                click.echo(f"📂 Directory: {directory}")
            if upload_id:
                click.echo(f"🆔 Upload ID: {upload_id}")
            if ai_data_dir and ai_data_dir != from_dir:
                click.echo(f"🤖 AI data directory: {ai_data_dir}")
            click.echo("📋 Include flags:")
            click.echo(f"   • CSV files: {'✅' if include_csv else '❌'}")
            click.echo(f"   • KPI JSON files: {'✅' if include_kpis_json else '❌'}")
            ai_data_status = "✅" if include_ai_data else "❌"
            if include_ai_data and not ai_data_dir:
                ai_data_status += " (⚠️  no ai_data_dir specified)"
            click.echo(f"   • AI data files: {ai_data_status}")
            click.echo("   • Analysis files: ✅ (always included if available)")

        # Create a minimal config object for the S3 export function
        from projects.caliper.orchestration.postprocess_config import (
            CaliperOrchestrationPostprocessConfig,
            CaliperOrchestrationS3ExportSection,
            CaliperOrchestrationS3Section,
        )

        s3_export_config = CaliperOrchestrationS3ExportSection(
            enabled=True,
            prefix=prefix,
            upload_id=upload_id,
            dry_run=dry_run,
            include_csv=include_csv,
            include_kpis_json=include_kpis_json,
            include_ai_data=include_ai_data,
        )

        s3_config = CaliperOrchestrationS3Section(
            bucket=bucket,
            instance=instance,
            directory=directory,
            vault=vault,
            aws_credentials_file=aws_credentials_file,
            export=s3_export_config,
        )

        config = CaliperOrchestrationPostprocessConfig(s3=s3_config)

        # Run S3 export
        result = run_s3_export(config, from_dir, ai_data_dir)

        if result["status"] == "success":
            if dry_run:
                click.echo("✅ Dry run completed - see upload plan")
                if "dry_run_file" in result:
                    click.echo(f"📋 Upload plan saved to: {result['dry_run_file']}")
            else:
                click.echo("✅ S3 export completed successfully")
                if "uploaded_count" in result:
                    click.echo(f"📤 Uploaded {result['uploaded_count']} files")

            if verbose and "s3_path" in result:
                click.echo(f"🌍 S3 location: {result['s3_path']}")
        else:
            click.echo(f"❌ S3 export failed: {result.get('error', 'unknown error')}", err=True)
            sys.exit(1)

    except Exception as e:
        click.echo(f"❌ S3 export failed: {e}", err=True)
        sys.exit(2)
