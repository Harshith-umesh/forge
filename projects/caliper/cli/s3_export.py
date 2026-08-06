"""S3 export functionality for Caliper postprocess artifacts."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import boto3
    import botocore.session
    from botocore.exceptions import BotoCoreError, ClientError

    BOTO3_AVAILABLE = True
except ImportError:
    boto3 = None
    botocore = None
    BotoCoreError = None
    ClientError = None
    BOTO3_AVAILABLE = False

from projects.core.library import vault as vault_lib

logger = logging.getLogger(__name__)


def build_s3_prefix(
    instance: str | None = None,
    directory: str | None = None,
    upload_id: str | None = None,
    trailing_slash: bool = True,
) -> str:
    """Build S3 prefix from components, skipping empty parts.

    Args:
        instance: Instance identifier (optional)
        directory: Directory identifier (optional)
        upload_id: Upload identifier (optional)
        trailing_slash: Whether to add trailing slash (default: True)

    Returns:
        Formatted S3 prefix path
    """
    components = []
    if instance:
        components.append(instance)
    if directory:
        components.append(directory)
    if upload_id:
        components.append(upload_id)

    if not components:
        return ""

    prefix = "/".join(components)
    return prefix + "/" if trailing_slash else prefix


def list_ai_data_files(ai_data_dir: Path) -> list[Path]:
    """List all files in the AI evaluation export directory.

    Args:
        ai_data_dir: Path to AI evaluation export directory

    Returns:
        List of file paths to upload
    """
    if not ai_data_dir.exists():
        logger.warning(f"AI eval directory does not exist: {ai_data_dir}")
        return []

    files = []
    for file_path in ai_data_dir.rglob("*"):
        if file_path.is_file():
            files.append(file_path)

    logger.info(f"Found {len(files)} files in AI eval directory: {ai_data_dir}")
    for file_path in files:
        relative_path = file_path.relative_to(ai_data_dir)
        logger.info(f"  - {relative_path} ({file_path.stat().st_size} bytes)")

    return files


def list_csv_files(output_dir: Path, postprocess_config=None) -> list[Path]:
    """List CSV files in the postprocess output directory.

    Args:
        output_dir: Path to postprocess output directory
        postprocess_config: Postprocess configuration to determine CSV file path

    Returns:
        List of CSV file paths to upload
    """
    csv_files = []

    if postprocess_config and postprocess_config.kpi.csv.enabled:
        # Validate configured CSV output is non-empty
        csv_output = postprocess_config.kpi.csv.output
        if not csv_output or not csv_output.strip():
            logger.error("CSV producer enabled but output path is empty - skipping CSV export")
        else:
            # Use configured CSV file path
            csv_file_path = output_dir / csv_output
            if csv_file_path.exists():
                csv_files.append(csv_file_path)
                logger.info(
                    f"Found configured CSV file: {csv_file_path.name} ({csv_file_path.stat().st_size} bytes)"
                )
            else:
                logger.error(
                    f"Configured CSV file not found: {csv_file_path} - skipping CSV export"
                )
    else:
        # Fallback to glob pattern for backward compatibility
        csv_files = list(output_dir.glob("*.csv"))
        logger.info(f"Found {len(csv_files)} CSV files in output directory: {output_dir}")
        for csv_file in csv_files:
            logger.info(f"  - {csv_file.name} ({csv_file.stat().st_size} bytes)")

    return csv_files


def list_kpi_json_files(output_dir: Path, postprocess_config=None) -> list[Path]:
    """List KPI JSON files in the postprocess output directory.

    Args:
        output_dir: Path to postprocess output directory
        postprocess_config: Postprocess configuration to determine KPI JSON file path

    Returns:
        List of KPI JSON file paths to upload
    """
    kpi_json_files = []

    if postprocess_config and postprocess_config.kpi.artifacts_to_kpis.enabled:
        # Validate configured KPI JSON output is non-empty
        kpi_output = postprocess_config.kpi.artifacts_to_kpis.output
        if not kpi_output or not kpi_output.strip():
            logger.error(
                "KPI JSON producer enabled but output path is empty - skipping KPI JSON export"
            )
        else:
            # Use configured KPI JSON file path
            kpi_file_path = output_dir / kpi_output
            if kpi_file_path.exists():
                kpi_json_files.append(kpi_file_path)
                logger.info(
                    f"Found configured KPI JSON file: {kpi_file_path.name} ({kpi_file_path.stat().st_size} bytes)"
                )
            else:
                logger.error(
                    f"Configured KPI JSON file not found: {kpi_file_path} - skipping KPI JSON export"
                )
    else:
        # Fallback to glob pattern for backward compatibility
        kpi_json_files = list(output_dir.glob("kpis.json"))
        logger.info(f"Found {len(kpi_json_files)} KPI JSON files in output directory: {output_dir}")
        for kpi_file in kpi_json_files:
            logger.info(f"  - {kpi_file.name} ({kpi_file.stat().st_size} bytes)")

    return kpi_json_files


def list_analysis_files(output_dir: Path, postprocess_config=None) -> list[Path]:
    """List all analysis output files in the output directory.

    Args:
        output_dir: Path to search for analysis files
        postprocess_config: Postprocess configuration to determine analysis file path

    Returns:
        List of analysis file paths to upload
    """
    files = []

    if postprocess_config and postprocess_config.analyze.enabled:
        # Validate configured analysis output is non-empty
        analysis_output = postprocess_config.analyze.output
        if not analysis_output or not analysis_output.strip():
            logger.error(
                "Analysis producer enabled but output path is empty - skipping analysis export"
            )
        else:
            # Use configured analysis file path
            analysis_file_path = output_dir / analysis_output
            if analysis_file_path.exists():
                files.append(analysis_file_path)
                logger.info(
                    f"Found configured analysis file: {analysis_file_path.name} ({analysis_file_path.stat().st_size} bytes)"
                )
            else:
                logger.error(
                    f"Configured analysis file not found: {analysis_file_path} - skipping analysis export"
                )
    else:
        # Fallback to glob patterns for backward compatibility
        analysis_patterns = ["kpi_analyze.json", "kpi_analysis.json", "*analyze*.json"]

        for pattern in analysis_patterns:
            for file_path in output_dir.glob(pattern):
                if file_path.is_file():
                    files.append(file_path)

        logger.info(f"Found {len(files)} analysis files in output directory: {output_dir}")
        for file_path in files:
            logger.info(f"  - {file_path.name} ({file_path.stat().st_size} bytes)")

    return files


def upload_file_to_s3(s3_client, file_path: Path, bucket: str, s3_key: str) -> bool:
    """Upload a single file to S3.

    Args:
        s3_client: Configured boto3 S3 client
        file_path: Local file path to upload
        bucket: S3 bucket name
        s3_key: S3 object key

    Returns:
        True if upload succeeded, False otherwise
    """
    try:
        logger.info(f"Uploading {file_path.name} to s3://{bucket}/{s3_key}")
        s3_client.upload_file(str(file_path), bucket, s3_key)
        logger.info(f"Successfully uploaded {file_path.name} ({file_path.stat().st_size} bytes)")
        return True
    except (ClientError, BotoCoreError) as e:
        logger.error(f"Failed to upload {file_path.name}: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error uploading {file_path.name}: {e}")
        return False


def create_s3_client(credentials_path: Path) -> Any:
    """Create boto3 S3 client using AWS credentials file.

    Args:
        credentials_path: Path to AWS credentials file

    Returns:
        Configured boto3 S3 client
    """
    if not BOTO3_AVAILABLE:
        raise ImportError("boto3 is not available. Install it with: pip install boto3")

    try:
        import configparser

        # Parse the AWS credentials file
        config = configparser.ConfigParser()
        config.read(credentials_path)

        # Get credentials from [default] section
        if "default" not in config.sections():
            raise ValueError(f"No [default] section found in credentials file: {credentials_path}")

        aws_access_key_id = config["default"].get("aws_access_key_id")
        aws_secret_access_key = config["default"].get("aws_secret_access_key")
        aws_session_token = config["default"].get("aws_session_token")  # Optional

        if not aws_access_key_id or not aws_secret_access_key:
            raise ValueError(
                f"Missing aws_access_key_id or aws_secret_access_key in credentials file: {credentials_path}"
            )

        # Create boto3 session with explicit credentials
        boto_session = boto3.Session(
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            aws_session_token=aws_session_token,  # Will be None if not present
        )
        s3_client = boto_session.client("s3")

        logger.info("S3 client created with provided credentials")

        return s3_client
    except (ClientError, BotoCoreError) as e:
        logger.error(f"Failed to authenticate with AWS S3: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error creating S3 client: {e}")
        raise


def get_aws_credentials(vault_name: str, credentials_file: str) -> Path | None:
    """Get AWS credentials file from vault.

    Args:
        vault_name: Name of the vault containing AWS credentials
        credentials_file: Name of credentials file within vault

    Returns:
        Path to credentials file or None if not found
    """

    credentials_path = vault_lib.get_vault_content_path(vault_name, credentials_file)

    if credentials_path is None:
        logger.error(f"Vault {vault_name}/{credentials_file} not found")
        return None

    if not credentials_path.exists():
        logger.error(f"Vault credentials file missing: {credentials_path}")
        return None

    logger.info(f"Found AWS credentials at: {credentials_path}")

    return credentials_path


def run_s3_export_with_explicit_paths(
    *,
    kpis_file: Path | None = None,
    csv_file: Path | None = None,
    ai_data_dir: Path | None = None,
    analysis_file: Path | None = None,
    bucket: str,
    instance: str | None = None,
    directory: str | None = None,
    upload_id: str | None = None,
    vault: str = "psap-forge-aws-s3-export",
    aws_credentials_file: str = "aws.credentials",
    dry_run: bool = False,
) -> dict[str, Any]:
    """S3 export functionality with explicit file paths.

    Args:
        kpis_file: Path to KPIs JSON file to upload
        csv_file: Path to CSV file to upload
        ai_data_dir: Path to AI data directory to upload
        analysis_file: Path to analysis file to upload
        bucket: S3 bucket name
        instance: Instance identifier for S3 organization
        directory: Directory identifier for S3 organization
        upload_id: Custom upload identifier (uses timestamp if not provided)
        vault: Vault containing AWS credentials
        aws_credentials_file: Credentials file name within vault
        dry_run: If True, show what would be uploaded without actually uploading

    Returns:
        Export status dictionary
    """
    start_time = time.time()

    # Check that at least one file/directory is provided
    if not any([kpis_file, csv_file, ai_data_dir, analysis_file]):
        return {
            "status": "failed",
            "error": "No files or directories specified for upload",
            "completed_at": time.time(),
        }

    # Check dry_run flag early
    if not dry_run and not BOTO3_AVAILABLE:
        return {
            "status": "failed",
            "error": "boto3 is not available. Install it with: pip install boto3",
            "completed_at": time.time(),
        }

    try:
        logger.info("Running S3 export with explicit file paths")
        logger.info(f"KPIs file: {kpis_file}")
        logger.info(f"CSV file: {csv_file}")
        logger.info(f"AI data directory: {ai_data_dir}")
        logger.info(f"Analysis file: {analysis_file}")

        # Use custom upload_id or generate collision-resistant timestamp
        if upload_id:
            upload_id = upload_id
            logger.info(f"Using configured upload ID: {upload_id}")
        else:
            # Generate collision-resistant upload_id with microsecond precision and random suffix
            now = datetime.now()
            timestamp = now.strftime("%y-%m-%d_%H%M%S")
            microseconds = now.strftime("%f")[:3]  # First 3 digits of microseconds (milliseconds)
            upload_id = f"{timestamp}_{microseconds}"
            logger.info(f"Using generated collision-resistant timestamp ID: {upload_id}")

        # Construct the full S3 path: {instance}/{directory}/{upload_id}/
        export_s3_prefix = build_s3_prefix(
            instance=instance, directory=directory, upload_id=upload_id
        )

        logger.info(f"Starting S3 export to bucket: {bucket}")
        logger.info(f"Full S3 export path: s3://{bucket}/{export_s3_prefix}")
        logger.info(f"Path structure: {export_s3_prefix}")
        if dry_run:
            logger.info("DRY RUN MODE: Files will not actually be uploaded")

        # For dry runs, check credentials availability but don't fail
        credentials_path = None
        if not dry_run:
            # Get AWS credentials for actual upload
            credentials_path = get_aws_credentials(vault, aws_credentials_file)
            if not credentials_path:
                return {
                    "status": "failed",
                    "error": f"Could not load AWS credentials from vault {vault}",
                    "completed_at": time.time(),
                }
        else:
            # For dry runs, just check if credentials would be available
            credentials_path = get_aws_credentials(vault, aws_credentials_file)
            if not credentials_path:
                logger.warning(
                    f"AWS credentials not available from vault {vault} - dry run will proceed but actual upload would fail"
                )

        # Collect files to upload
        upload_files = []
        upload_plan = []

        # Add KPIs JSON file
        if kpis_file and kpis_file.exists():
            upload_files.append(kpis_file)
            s3_key = f"{export_s3_prefix}{kpis_file.name}"
            upload_plan.append(
                {
                    "local_path": str(kpis_file),
                    "s3_key": s3_key,
                    "file_type": "kpis_json",
                }
            )
            logger.info(
                f"  - {kpis_file.name} → s3://{bucket}/{s3_key} ({kpis_file.stat().st_size} bytes)"
            )

        # Add CSV file
        if csv_file and csv_file.exists():
            upload_files.append(csv_file)
            s3_key = f"{export_s3_prefix}{csv_file.name}"
            upload_plan.append(
                {
                    "local_path": str(csv_file),
                    "s3_key": s3_key,
                    "file_type": "csv",
                }
            )
            logger.info(
                f"  - {csv_file.name} → s3://{bucket}/{s3_key} ({csv_file.stat().st_size} bytes)"
            )

        # Add analysis file
        if analysis_file and analysis_file.exists():
            upload_files.append(analysis_file)
            s3_key = f"{export_s3_prefix}{analysis_file.name}"
            upload_plan.append(
                {
                    "local_path": str(analysis_file),
                    "s3_key": s3_key,
                    "file_type": "analysis",
                }
            )
            logger.info(
                f"  - {analysis_file.name} → s3://{bucket}/{s3_key} ({analysis_file.stat().st_size} bytes)"
            )

        # Add AI data files
        ai_data_files = []
        if ai_data_dir and ai_data_dir.exists() and ai_data_dir.is_dir():
            ai_data_files = list_ai_data_files(ai_data_dir)
            logger.info(f"Found {len(ai_data_files)} AI data files in directory: {ai_data_dir}")

            for ai_file in ai_data_files:
                upload_files.append(ai_file)
                relative_path = ai_file.relative_to(ai_data_dir)
                s3_key = f"{export_s3_prefix}ai_data/{relative_path}"
                upload_plan.append(
                    {
                        "local_path": str(ai_file),
                        "s3_key": s3_key,
                        "file_type": "ai_data",
                    }
                )
                logger.info(
                    f"  - {relative_path} → s3://{bucket}/{s3_key} ({ai_file.stat().st_size} bytes)"
                )

        logger.info(f"Preparing to upload {len(upload_files)} files to S3")
        total_size = sum(f.stat().st_size for f in upload_files)
        logger.info(f"Total upload size: {total_size} bytes")

        if not upload_files:
            logger.warning("No files to upload")
            return {
                "status": "skipped",
                "reason": "no files to upload",
                "completed_at": time.time(),
            }

        # Handle dry run mode
        if dry_run:
            logger.info("DRY RUN: Skipping actual upload")

            return {
                "status": "success",
                "dry_run": True,
                "exported_path": f"s3://{bucket}/{export_s3_prefix}",
                "completed_at": time.time(),
                "duration": round(time.time() - start_time),
            }

        # Create S3 client for actual upload
        try:
            s3_client = create_s3_client(credentials_path)
        except Exception as e:
            logger.error(f"Failed to create S3 client: {e}")
            return {
                "status": "failed",
                "error": f"Could not create S3 client: {e}",
                "completed_at": time.time(),
            }

        # Upload files to S3
        uploaded_count = 0
        failed_uploads = []

        logger.info("Starting actual S3 upload...")

        for upload_item in upload_plan:
            local_path = Path(upload_item["local_path"])
            s3_key = upload_item["s3_key"]

            if upload_file_to_s3(s3_client, local_path, bucket, s3_key):
                uploaded_count += 1
            else:
                failed_uploads.append(str(local_path))

        # Check upload results
        if failed_uploads:
            logger.error(f"Failed to upload {len(failed_uploads)} files: {failed_uploads}")
            if uploaded_count == 0:
                return {
                    "status": "failed",
                    "error": f"All uploads failed. Failed files: {failed_uploads}",
                    "completed_at": time.time(),
                }
            else:
                logger.warning(
                    f"Partial success: {uploaded_count} uploaded, {len(failed_uploads)} failed"
                )

        target_url = f"s3://{bucket}/{export_s3_prefix}"
        logger.info(
            f"S3 upload completed: {uploaded_count}/{len(upload_files)} files uploaded successfully to {target_url}"
        )

        # Determine final status
        if failed_uploads:
            status = "partial_success" if uploaded_count > 0 else "failed"
        else:
            status = "success"

        return {
            "status": status,
            "upload_id": upload_id,
            "exported_path": f"s3://{bucket}/{export_s3_prefix}",
            "uploaded_files": uploaded_count,
            "failed_files": len(failed_uploads),
            "total_files": len(upload_files),
            "total_size": total_size,
            "completed_at": time.time(),
            "duration": time.time() - start_time,
        }

    except Exception as e:
        logger.exception("S3 export failed")
        return {
            "status": "failed",
            "error": str(e),
            "exception_type": type(e).__name__,
            "completed_at": time.time(),
        }


def run_s3_export(
    postprocess_config,
    output_dir: Path,
    ai_data_dir: Path | None = None,
) -> dict[str, Any]:
    """S3 export functionality for postprocess artifacts.

    Args:
        postprocess_config: Postprocess configuration object
        output_dir: Path to postprocess output directory
        ai_data_dir: Path to AI evaluation export directory (optional)

    Returns:
        Export status dictionary
    """
    start_time = time.time()

    if not postprocess_config.s3.export.enabled:
        return {
            "status": "disabled",
            "reason": "s3_export disabled",
            "completed_at": time.time(),
        }

    # Check dry_run flag early
    dry_run = postprocess_config.s3.export.dry_run

    if not dry_run and not BOTO3_AVAILABLE:
        return {
            "status": "failed",
            "error": "boto3 is not available. Install it with: pip install boto3",
            "completed_at": time.time(),
        }

    try:
        error_detected = False

        s3_parent_config = postprocess_config.s3
        s3_config = postprocess_config.s3.export
        bucket = s3_parent_config.bucket
        instance = s3_parent_config.instance
        directory = s3_parent_config.directory
        upload_id = s3_config.upload_id

        # Determine artifact directory for relative paths
        artifact_dir = output_dir
        if postprocess_config.artifacts_dir:
            artifact_dir = Path(postprocess_config.artifacts_dir)
        else:
            # If no explicit artifacts_dir, assume output_dir is a subdirectory and use parent
            if output_dir.name in ["postprocess_output", "postprocess"]:
                artifact_dir = output_dir.parent

        logger.info(f"Using artifact directory for relative paths: {artifact_dir}")

        # Use custom upload_id or generate collision-resistant timestamp
        if upload_id:
            upload_id = upload_id
            logger.info(f"Using configured upload ID: {upload_id}")
        else:
            # Generate collision-resistant upload_id with microsecond precision and random suffix
            now = datetime.now()
            timestamp = now.strftime("%y-%m-%d_%H%M%S")
            microseconds = now.strftime("%f")[:3]  # First 3 digits of microseconds (milliseconds)
            upload_id = f"{timestamp}_{microseconds}"
            logger.info(f"Using generated collision-resistant timestamp ID: {upload_id}")

        # Construct the full S3 path: {instance}/{directory}/{upload_id}/
        export_s3_prefix = build_s3_prefix(
            instance=instance, directory=directory, upload_id=upload_id
        )

        logger.info(f"Starting S3 export to bucket: {bucket}")
        logger.info(f"Full S3 export path: s3://{bucket}/{export_s3_prefix}")
        logger.info(f"Path structure: {export_s3_prefix}")
        if dry_run:
            logger.info("DRY RUN MODE: Files will not actually be uploaded")

        # For dry runs, check credentials availability but don't fail
        credentials_path = None
        if not dry_run:
            # Get AWS credentials for actual upload
            credentials_path = get_aws_credentials(
                s3_parent_config.vault.name, s3_parent_config.vault.aws_credentials_file
            )
            if not credentials_path:
                return {
                    "status": "failed",
                    "error": f"Could not load AWS credentials from vault {s3_parent_config.vault.name}",
                    "completed_at": time.time(),
                }
        else:
            # For dry runs, just check if credentials would be available
            credentials_path = get_aws_credentials(
                s3_parent_config.vault.name, s3_parent_config.vault.aws_credentials_file
            )
            if not credentials_path:
                logger.warning(
                    f"AWS credentials not available from vault {s3_parent_config.vault.name} - dry run will proceed but actual upload would fail"
                )

        # List local files to upload
        upload_files = []

        # Use postprocess config to determine file locations
        csv_files = []
        kpi_json_files = []
        ai_data_files = []

        if s3_config.include_csv:
            csv_files = list_csv_files(output_dir, postprocess_config)
            if not csv_files:
                error_detected = True
            upload_files.extend(csv_files)

        if s3_config.include_kpis_json:
            kpi_json_files = list_kpi_json_files(output_dir, postprocess_config)
            if not kpi_json_files:
                error_detected = True
            upload_files.extend(kpi_json_files)

        if True:
            analysis_files = list_analysis_files(output_dir, postprocess_config)
            if not analysis_files:
                error_detected = True
            upload_files.extend(analysis_files)

        if s3_config.include_ai_data:
            ai_data_files = list_ai_data_files(ai_data_dir)
            if not ai_data_files:
                error_detected = True
            upload_files.extend(ai_data_files)

        logger.info(f"Preparing to upload {len(upload_files)} files to S3")
        total_size = sum(f.stat().st_size for f in upload_files)
        logger.info(f"Total upload size: {total_size} bytes")

        if not upload_files:
            logger.warning("No files to upload")
            status = "failed" if error_detected else "skipped"
            return {
                "status": status,
                "reason": "no files to upload",
                "completed_at": time.time(),
            }

        # Show what would be uploaded
        logger.info(f"Files to be uploaded ({len(upload_files)} total):")
        upload_plan = []

        for file_path in upload_files:
            # Determine S3 key and type based on file membership in discovered file lists
            s3_key = None
            file_type = None

            if s3_config.include_ai_data and ai_data_dir and ai_data_dir in file_path.parents:
                relative_path = file_path.relative_to(ai_data_dir)
                s3_key = f"{export_s3_prefix}ai_data/{relative_path}"
                file_type = "ai_data"
            elif file_path in csv_files:
                s3_key = f"{export_s3_prefix}{file_path.name}"
                file_type = "csv"
            elif file_path in kpi_json_files:
                s3_key = f"{export_s3_prefix}{file_path.name}"
                file_type = "kpis_json"
            elif file_path in analysis_files:
                s3_key = f"{export_s3_prefix}{file_path.name}"
                file_type = "analysis"

            if s3_key:
                file_size = file_path.stat().st_size
                # Make path relative to artifact directory
                try:
                    relative_local_path = str(file_path.relative_to(artifact_dir))
                except ValueError:
                    # Fallback if file is not under artifact_dir
                    relative_local_path = str(file_path)

                upload_plan.append(
                    {
                        "local_path": relative_local_path,
                        "s3_key": s3_key,
                        "file_type": file_type,
                    }
                )
                logger.info(f"  - {file_path.name} → s3://{bucket}/{s3_key} ({file_size} bytes)")

        # Handle dry run mode
        if dry_run:
            logger.info("DRY RUN: Skipping actual upload")

            status = "failed" if error_detected else "success"
            response = {
                "status": status,
                "dry_run": True,
                "completed_at": time.time(),
                "duration": round(time.time() - start_time),
            }
            return response

        # Create S3 client for actual upload
        try:
            s3_client = create_s3_client(credentials_path)
        except Exception as e:
            logger.error(f"Failed to create S3 client: {e}")
            return {
                "status": "failed",
                "error": f"Could not create S3 client: {e}",
                "completed_at": time.time(),
            }

        # Upload files to S3
        uploaded_count = 0
        failed_uploads = []

        logger.info("Starting actual S3 upload...")

        for upload_item in upload_plan:
            local_path = upload_item["local_path"]
            s3_key = upload_item["s3_key"]

            # Convert relative path back to absolute for upload
            if Path(local_path).is_absolute():
                file_path = Path(local_path)
            else:
                file_path = artifact_dir / local_path

            if upload_file_to_s3(s3_client, file_path, bucket, s3_key):
                uploaded_count += 1
            else:
                failed_uploads.append(local_path)

        # Check upload results
        if failed_uploads:
            logger.error(f"Failed to upload {len(failed_uploads)} files: {failed_uploads}")
            if uploaded_count == 0:
                return {
                    "status": "failed",
                    "error": f"All uploads failed. Failed files: {failed_uploads}",
                    "completed_at": time.time(),
                }

            error_detected = True
            logger.warning(
                f"Partial success: {uploaded_count} uploaded, {len(failed_uploads)} failed"
            )

        target_url = f"s3://{bucket}/{export_s3_prefix}"
        logger.info(
            f"S3 upload completed: {uploaded_count}/{len(upload_files)} files uploaded successfully to {target_url}"
        )

        # Determine final status

        status = "failed" if error_detected else "success"

        return {
            "status": status,
            "upload_id": upload_id,
            "exported_path": f"s3://{bucket}/{export_s3_prefix}",
            "uploaded_files": uploaded_count,
            "failed_files": len(failed_uploads),
            "total_files": len(upload_files),
            "total_size": total_size,
            "completed_at": time.time(),
            "duration": time.time() - start_time,
        }

    except Exception as e:
        logger.exception("S3 export failed")
        return {
            "status": "failed",
            "error": str(e),
            "exception_type": type(e).__name__,
            "completed_at": time.time(),
        }
