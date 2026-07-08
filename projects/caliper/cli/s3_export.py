"""S3 export functionality for Caliper postprocess artifacts."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

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


def list_csv_files(output_dir: Path) -> list[Path]:
    """List CSV files in the postprocess output directory.

    Args:
        output_dir: Path to postprocess output directory

    Returns:
        List of CSV file paths to upload
    """
    csv_files = list(output_dir.glob("*.csv"))

    logger.info(f"Found {len(csv_files)} CSV files in output directory: {output_dir}")
    for csv_file in csv_files:
        logger.info(f"  - {csv_file.name} ({csv_file.stat().st_size} bytes)")

    return csv_files


def list_kpi_json_files(output_dir: Path) -> list[Path]:
    """List KPI JSON files in the postprocess output directory.

    Args:
        output_dir: Path to postprocess output directory

    Returns:
        List of KPI JSON file paths to upload
    """
    kpi_json_files = list(output_dir.glob("kpis.json"))

    logger.info(f"Found {len(kpi_json_files)} KPI JSON files in output directory: {output_dir}")
    for kpi_file in kpi_json_files:
        logger.info(f"  - {kpi_file.name} ({kpi_file.stat().st_size} bytes)")

    return kpi_json_files


def list_analysis_files(output_dir: Path) -> list[Path]:
    """List all analysis output files in the output directory.

    Args:
        output_dir: Path to search for analysis files

    Returns:
        List of analysis file paths to upload
    """
    files = []
    # Look for analysis output files (kpi_analyze.json is the most common)
    analysis_patterns = ["kpi_analyze.json", "kpi_analysis.json", "*analyze*.json"]

    for pattern in analysis_patterns:
        for file_path in output_dir.glob(pattern):
            if file_path.is_file():
                files.append(file_path)

    logger.info(f"Found {len(files)} analysis files in output directory: {output_dir}")
    for file_path in files:
        logger.info(f"  - {file_path.name} ({file_path.stat().st_size} bytes)")

    return files


def get_files_from_postprocess_status(
    output_dir: Path, include_csv: bool, include_kpis_json: bool, include_ai_data: bool
) -> tuple[list[Path], list[Path], list[Path], Path | None]:
    """Get file paths from postprocess status instead of directory searching.

    Args:
        output_dir: Postprocess output directory
        include_csv: Whether to include CSV files
        include_kpis_json: Whether to include KPI JSON files
        include_ai_data: Whether to include AI data files

    Returns:
        Tuple of (csv_files, kpi_json_files, analysis_files, ai_data_dir)
    """
    csv_files = []
    kpi_json_files = []
    analysis_files = []
    ai_data_dir = None

    # Look for postprocess status file
    status_file = output_dir / "caliper_postprocess_status.yaml"
    if not status_file.exists():
        logger.warning(f"Postprocess status file not found: {status_file}")
        logger.info("Falling back to directory search for files")
        # Fallback to original behavior
        if include_csv:
            csv_files = list_csv_files(output_dir)
        if include_kpis_json:
            kpi_json_files = list_kpi_json_files(output_dir)
        analysis_files = list_analysis_files(output_dir)
        return csv_files, kpi_json_files, analysis_files, ai_data_dir

    try:
        with open(status_file, encoding="utf-8") as f:
            status_data = yaml.safe_load(f)

        if not status_data or "steps" not in status_data:
            logger.warning("Invalid postprocess status file format")
            return csv_files, kpi_json_files, analysis_files

        # Extract file paths from steps
        steps = status_data["steps"]
        for step in steps:
            for step_name, step_data in step.items():
                if not isinstance(step_data, dict) or step_data.get("status") != "success":
                    continue

                # KPI JSON file
                if step_name == "artifacts_to_kpis" and include_kpis_json:
                    output_file = step_data.get("output_file")
                    if output_file:
                        kpi_file_path = output_dir / output_file
                        if kpi_file_path.exists():
                            kpi_json_files.append(kpi_file_path)
                            logger.info(
                                f"Found KPI JSON file: {output_file} ({kpi_file_path.stat().st_size} bytes)"
                            )
                        else:
                            logger.warning(
                                f"KPI JSON file specified but not found: {kpi_file_path}"
                            )

                # CSV file
                elif step_name == "kpis_to_csv" and include_csv:
                    output_file = step_data.get("output_file")
                    if output_file:
                        csv_file_path = output_dir / output_file
                        if csv_file_path.exists():
                            csv_files.append(csv_file_path)
                            logger.info(
                                f"Found CSV file: {output_file} ({csv_file_path.stat().st_size} bytes)"
                            )
                        else:
                            logger.warning(f"CSV file specified but not found: {csv_file_path}")

                # Analysis file
                elif step_name == "analyse_kpis":
                    output_file = step_data.get("output_file")
                    if output_file:
                        analysis_file_path = output_dir / output_file
                        if analysis_file_path.exists():
                            analysis_files.append(analysis_file_path)
                            logger.info(
                                f"Found analysis file: {output_file} ({analysis_file_path.stat().st_size} bytes)"
                            )
                        else:
                            logger.warning(
                                f"Analysis file specified but not found: {analysis_file_path}"
                            )

                # AI data directory
                elif step_name == "artifacts_to_ai_data" and include_ai_data:
                    ai_eval_dir = step_data.get("ai_eval_dir")
                    if ai_eval_dir:
                        ai_data_path = output_dir / ai_eval_dir
                        if ai_data_path.exists() and ai_data_path.is_dir():
                            ai_data_dir = ai_data_path
                            logger.info(f"Found AI data directory: {ai_eval_dir}")
                        else:
                            logger.warning(
                                f"AI data directory specified but not found: {ai_data_path}"
                            )

        # Log summary
        logger.info(
            f"From postprocess status - CSV: {len(csv_files)}, KPI JSON: {len(kpi_json_files)}, Analysis: {len(analysis_files)} files, AI data: {'Yes' if ai_data_dir else 'No'}"
        )

    except Exception as e:
        logger.warning(f"Failed to read postprocess status file: {e}")
        logger.info("Falling back to directory search for files")
        # Fallback to original behavior
        if include_csv:
            csv_files = list_csv_files(output_dir)
        if include_kpis_json:
            kpi_json_files = list_kpi_json_files(output_dir)
        analysis_files = list_analysis_files(output_dir)

    return csv_files, kpi_json_files, analysis_files, ai_data_dir


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

        # Construct the full S3 path: {bucket}/{instance}/{directory}/{upload_id}/
        s3_path_components = []
        if instance:
            s3_path_components.append(instance)
        if directory:
            s3_path_components.append(directory)
        s3_path_components.append(upload_id)

        export_s3_prefix = "/".join(s3_path_components) + "/"

        logger.info(f"Starting S3 export to bucket: {bucket}")
        logger.info(f"Full S3 export path: s3://{bucket}/{export_s3_prefix}")
        logger.info(f"Path structure: {instance}/{directory}/{upload_id}/")
        if dry_run:
            logger.info("DRY RUN MODE: Files will not actually be uploaded")

        # For dry runs, check credentials availability but don't fail
        credentials_path = None
        if not dry_run:
            # Get AWS credentials for actual upload
            credentials_path = get_aws_credentials(
                s3_parent_config.vault, s3_parent_config.aws_credentials_file
            )
            if not credentials_path:
                return {
                    "status": "failed",
                    "error": f"Could not load AWS credentials from vault {s3_parent_config.vault}",
                    "completed_at": time.time(),
                }
        else:
            # For dry runs, just check if credentials would be available
            credentials_path = get_aws_credentials(
                s3_parent_config.vault, s3_parent_config.aws_credentials_file
            )
            if not credentials_path:
                logger.warning(
                    f"AWS credentials not available from vault {s3_parent_config.vault} - dry run will proceed but actual upload would fail"
                )

        # List local files to upload
        upload_files = []

        # Get file paths from postprocess status instead of directory searching
        csv_files, kpi_json_files, analysis_files, status_ai_data_dir = (
            get_files_from_postprocess_status(
                output_dir,
                s3_config.include_csv,
                s3_config.include_kpis_json,
                s3_config.include_ai_data,
            )
        )

        upload_files.extend(csv_files)
        upload_files.extend(kpi_json_files)
        upload_files.extend(analysis_files)

        if s3_config.include_ai_data:
            # Use AI data directory from status if available, otherwise fallback to parameter
            actual_ai_data_dir = status_ai_data_dir or ai_data_dir
            if actual_ai_data_dir:
                ai_data_files = list_ai_data_files(actual_ai_data_dir)
                logger.info(
                    f"Found {len(ai_data_files)} AI data files in directory: {actual_ai_data_dir}"
                )
                upload_files.extend(ai_data_files)
            else:
                logger.warning(
                    "AI data export is enabled but no AI data directory found in status or parameters"
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

        # Show what would be uploaded
        logger.info(f"Files to be uploaded ({len(upload_files)} total):")
        upload_plan = []

        for file_path in upload_files:
            # Determine S3 key based on file type
            s3_key = None

            if s3_config.include_ai_data and ai_data_dir and ai_data_dir in file_path.parents:
                relative_path = file_path.relative_to(ai_data_dir)
                s3_key = f"{export_s3_prefix}ai_data/{relative_path}"
            elif s3_config.include_csv and file_path.suffix == ".csv":
                s3_key = f"{export_s3_prefix}{file_path.name}"
            elif s3_config.include_kpis_json and file_path.name == "kpis.json":
                s3_key = f"{export_s3_prefix}{file_path.name}"
            elif file_path.name.endswith("analyze.json") or "analyze" in file_path.name:
                # Include analysis files (e.g., kpi_analyze.json)
                s3_key = f"{export_s3_prefix}{file_path.name}"

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
                    }
                )
                logger.info(f"  - {file_path.name} → s3://{bucket}/{s3_key} ({file_size} bytes)")

        # Handle dry run mode
        if dry_run:
            logger.info("DRY RUN: Skipping actual upload")

            # Save dry run results to YAML file
            dry_run_data = {
                "s3_export_dry_run": {
                    "timestamp": datetime.now().isoformat(),
                    "export_config": {
                        "bucket": bucket,
                        "instance": instance,
                        "directory": directory,
                        "upload_id": upload_id,
                        "exported_path": f"s3://{bucket}/{export_s3_prefix}",
                    },
                    "csv_files": [],
                    "kpis_json_files": [],
                    "ai_data_files": [],
                    "upload_plan": upload_plan,
                    "summary": {
                        "total_files": len(upload_files),
                        "total_size_bytes": total_size,
                        "csv_files_count": len([f for f in upload_files if f.suffix == ".csv"]),
                        "kpis_json_files_count": len(
                            [f for f in upload_files if f.name == "kpis.json"]
                        ),
                        "ai_data_files_count": len(
                            [f for f in upload_files if ai_data_dir and ai_data_dir in f.parents]
                        ),
                    },
                }
            }

            # Collect CSV file details (use already collected files)
            for csv_file in csv_files:
                try:
                    relative_csv_path = str(csv_file.relative_to(artifact_dir))
                except ValueError:
                    relative_csv_path = str(csv_file)

                dry_run_data["s3_export_dry_run"]["csv_files"].append(
                    {
                        "name": csv_file.name,
                        "path": relative_csv_path,
                        "size_bytes": csv_file.stat().st_size,
                    }
                )

            # Collect KPI JSON file details
            if s3_config.include_kpis_json:
                kpi_json_files = list_kpi_json_files(output_dir)
                for kpi_file in kpi_json_files:
                    try:
                        relative_kpi_path = str(kpi_file.relative_to(artifact_dir))
                    except ValueError:
                        relative_kpi_path = str(kpi_file)

                    dry_run_data["s3_export_dry_run"]["kpis_json_files"].append(
                        {
                            "name": kpi_file.name,
                            "path": relative_kpi_path,
                            "size_bytes": kpi_file.stat().st_size,
                        }
                    )

            # Collect AI eval file details
            if s3_config.include_ai_data and ai_data_dir:
                ai_data_files = list_ai_data_files(ai_data_dir)
                for ai_data_file in ai_data_files:
                    relative_path = ai_data_file.relative_to(ai_data_dir)
                    try:
                        relative_full_path = str(ai_data_file.relative_to(artifact_dir))
                    except ValueError:
                        relative_full_path = str(ai_data_file)

                    dry_run_data["s3_export_dry_run"]["ai_data_files"].append(
                        {
                            "full_path": relative_full_path,
                            "size_bytes": ai_data_file.stat().st_size,
                        }
                    )

            # Write to YAML file
            dry_run_file = output_dir / "upload_dry_run.yaml"
            try:
                with open(dry_run_file, "w") as f:
                    yaml.dump(dry_run_data, f, default_flow_style=False, indent=2)
                logger.info(f"Dry run results saved to: {dry_run_file}")
            except Exception as e:
                logger.error(f"Failed to save dry run results: {e}")
                return {
                    "status": "failed",
                    "error": f"Failed to save dry run results: {e}",
                    "dry_run": True,
                    "completed_at": time.time(),
                    "duration": round(time.time() - start_time),
                }

            response = {
                "status": "success",
                "dry_run": True,
                "dry_run_file": str(dry_run_file),
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
