"""
Config-driven Caliper parse / visualize / KPI / AI eval for FORGE orchestration.

KPI generation and AI evaluation export are now implemented. Regression analyze is still
a stub. All steps maintain a stable ``steps`` shape for caller compatibility.

Computes ``final_status`` from the FORGE test phase outcome plus all enabled step results.
"""

from __future__ import annotations

import logging
import time
import traceback
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from projects.caliper.engine.load_plugin import load_plugin
from projects.caliper.engine.parse import run_parse
from projects.caliper.engine.plugin_config import resolve_plugin_module_string
from projects.caliper.engine.visualize import run_visualize
from projects.caliper.orchestration.postprocess_config import (
    CaliperOrchestrationPostprocessConfig,
)
from projects.caliper.orchestration.postprocess_outcome import (
    FINAL_SUCCESS,
    TestPhaseOutcome,
    compute_final_postprocess_status,
)
from projects.caliper.orchestration.step_logging import (
    cleanup_step_logging,
    log_ai_data_command,
    log_analyze_command,
    log_artifacts_to_kpis_command,
    log_kpis_to_csv_command,
    log_parse_command,
    log_s3_export_command,
    log_s3_import_command,
    log_visualize_command,
    step_logging,
)
from projects.caliper.orchestration.subcommands.s3_export import run_s3_export
from projects.caliper.orchestration.subcommands.s3_import import run_s3_import
from projects.core.library import env

logger = logging.getLogger(__name__)

_STUB_REASON_ANALYZE = "orchestration stub: regression analyze is not wired here (use Caliper CLI or extend orchestration)."


def _resolve_paths(
    postprocess_config: CaliperOrchestrationPostprocessConfig,
    *,
    artifacts_dir: Path,
) -> tuple[Path, Path | None, Path | None]:
    manifest_path = (
        Path(postprocess_config.postprocess_config).expanduser().resolve()
        if postprocess_config.postprocess_config
        else None
    )
    # Always use default cache behavior - store cache files with each test result
    cache_path = None
    return artifacts_dir.resolve(), manifest_path, cache_path


def _resolve_visualize_output_dir(
    raw: str | None,
) -> Path:
    if raw is None or not str(raw).strip():
        raise ValueError(
            "caliper.postprocess.visualize.output_dir is required when no explicit visualize_output_directory is provided"
        )
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p.resolve()
    raise ValueError("caliper.postprocess.visualize.output_dir must be an absolute path")


def _resolve_visualize_config_path(
    raw: str | None,
    *,
    artifact_tree: Path,
) -> Path | None:
    if raw is None or not str(raw).strip():
        return None
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p.resolve()

    from projects.core.library import env

    return (env.FORGE_HOME / p).resolve()


def _load_plugin(
    postprocess_config: CaliperOrchestrationPostprocessConfig,
    *,
    tree_root: Path,
    manifest_path: Path | None,
) -> tuple[str, object]:
    mod_str, _manifest = resolve_plugin_module_string(
        base_dir=tree_root,
        postprocess_config=manifest_path,
        cli_plugin=postprocess_config.plugin_module,
    )
    return mod_str, load_plugin(mod_str)


def _transform_kpis_to_hierarchical_format(kpis: list[dict], model) -> dict:
    """
    Transform flat KPI list into hierarchical JSON structure.

    Groups KPIs by test (run_id), extracting common labels and organizing
    KPI metadata (name, help, unit, etc.) for improved readability.

    Args:
        kpis: List of flat KPI records from compute_kpis
        model: Unified model for accessing plugin metadata

    Returns:
        Hierarchical JSON structure organized by test
    """
    from collections import defaultdict

    from projects.caliper.engine.kpi.decorators import get_kpi_functions

    if not kpis:
        return {"schema_version": "2", "tests": []}

    # Group KPIs by test (run_id)
    tests_data = defaultdict(lambda: {"kpis": [], "labels": {}, "metadata": {}})

    # Get KPI function metadata from the plugin module
    try:
        plugin_module = __import__(model.plugin_module, fromlist=[""])
        kpi_functions = get_kpi_functions(plugin_module)
    except (ImportError, AttributeError):
        kpi_functions = {}

    for kpi in kpis:
        run_id = kpi.get("run_id", "unknown")
        test_data = tests_data[run_id]

        # Extract common labels (excluding KPI-specific ones)
        kpi_labels = kpi.get("labels", {})
        test_labels = {
            k: v for k, v in kpi_labels.items() if k not in ["higher_is_better"]
        }  # Exclude KPI-specific labels

        # Merge test labels (they should be the same for all KPIs in a test)
        if not test_data["labels"]:
            test_data["labels"] = test_labels

        # Store test metadata from first KPI
        if not test_data["metadata"]:
            test_data["metadata"] = {
                "timestamp": kpi.get("timestamp"),
                "source": kpi.get("source", {}),
                "run_id": run_id,
            }

        # Create KPI record with metadata
        kpi_id = kpi.get("kpi_id")
        raw_value = kpi.get("value")

        # Transform 2D KPI values to structured format
        if isinstance(raw_value, list) and raw_value and len(raw_value) > 0:
            # Check if this looks like 2D data: list of tuples/lists with 2 elements
            first_item = raw_value[0]
            if isinstance(first_item, list | tuple) and len(first_item) == 2:
                try:
                    # Convert list of tuples [(x1, y1), (x2, y2), ...] to structured format
                    structured_value = {
                        "data_points": [{"x": float(x), "y": float(y)} for x, y in raw_value],
                        "count": len(raw_value),
                    }
                    final_value = structured_value
                except (ValueError, TypeError, IndexError):
                    # If conversion fails, keep original value
                    final_value = raw_value
            else:
                final_value = raw_value
        else:
            final_value = raw_value

        kpi_record = {
            "id": kpi_id,
            "value": final_value,
            "unit": kpi.get("unit"),
            "higher_is_better": kpi_labels.get("higher_is_better", True),
        }

        # Add KPI metadata from function decorator if available
        if kpi_id in kpi_functions:
            func = kpi_functions[kpi_id]
            kpi_record.update(
                {
                    "name": (
                        func.__doc__.replace(" KPI.", "")
                        if func.__doc__
                        else kpi_id.replace("_", " ").title()
                    ),
                    "help": getattr(func, "_kpi_help", ""),
                }
            )

            # Add 2D-specific metadata if present
            if getattr(func, "_kpi_is_2d", False):
                kpi_record.update(
                    {
                        "is_2d": True,
                        "x_unit": getattr(func, "_kpi_x_unit", ""),
                        "x_help": getattr(func, "_kpi_x_help", ""),
                        "y_unit": getattr(func, "_kpi_y_unit", None) or kpi_record["unit"],
                        "y_help": getattr(func, "_kpi_y_help", None)
                        or getattr(func, "_kpi_help", ""),
                    }
                )
            else:
                kpi_record["is_2d"] = False

            # Add formatting info if available
            if hasattr(func, "_kpi_format"):
                kpi_record["format"] = func._kpi_format
        else:
            # Fallback if no function metadata available
            kpi_record.update(
                {
                    "name": kpi_id.replace("_", " ").title(),
                    "help": f"KPI: {kpi_id}",
                    "is_2d": isinstance(kpi.get("value"), list),
                }
            )

        test_data["kpis"].append(kpi_record)

    # Convert to final structure
    tests_list = []
    for run_id, test_data in tests_data.items():
        tests_list.append(
            {
                "run_id": run_id,
                "labels": test_data["labels"],
                "metadata": test_data["metadata"],
                "kpis": test_data["kpis"],
            }
        )

    return {"schema_version": "2", "tests": tests_list}


def _run_artifacts_to_kpis(
    postprocess_config: CaliperOrchestrationPostprocessConfig,
    plugin,
    model,
    output_dir: Path,
    plugin_module: str,
    base_dir: Path,
) -> dict[str, Any]:
    """Generate KPI JSON using the plugin's compute_kpis method."""

    if not postprocess_config.kpi.enabled:
        return {"status": "disabled", "reason": "kpi disabled", "completed_at": time.time()}
    if not postprocess_config.kpi.artifacts_to_kpis.enabled:
        return {
            "status": "disabled",
            "reason": "kpi.artifacts_to_kpis disabled",
            "completed_at": time.time(),
        }

    try:
        # Write KPI JSON
        output_file = output_dir / postprocess_config.kpi.artifacts_to_kpis.output

        # Log command to reproduce this step
        log_artifacts_to_kpis_command(
            base_dir=base_dir,
            plugin_module=plugin_module,
            output_file=output_file,
        )

        kpis = plugin.compute_kpis(model)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        import json

        # Transform flat KPI list into hierarchical structure
        hierarchical_data = _transform_kpis_to_hierarchical_format(kpis, model)

        with open(output_file, "w") as f:
            json.dump(hierarchical_data, f, indent=2)

        logger.info(
            f"Generated hierarchical KPI structure with {len(kpis)} total records in {output_file}"
        )
        return {
            "status": "success",
            "kpi_count": len(kpis),
            "output_file": str(output_file),
            "completed_at": time.time(),
        }
    except Exception as e:
        logger.error(f"KPI generation failed: {e}")
        return {"status": "failed", "error": str(e), "completed_at": time.time()}


def _run_s3_import(
    postprocess_config,
    output_dir: Path,
) -> dict[str, Any]:
    """Import historical data from S3."""

    if not postprocess_config.s3.import_.enabled:
        return {"status": "disabled", "reason": "s3_import disabled", "completed_at": time.time()}

    try:
        # Log command to reproduce this step
        s3_parent_config = postprocess_config.s3
        s3_config = postprocess_config.s3.import_
        import_prefix = (
            f"{s3_parent_config.instance}/{s3_parent_config.directory}"
            if s3_parent_config.instance and s3_parent_config.directory
            else ""
        )
        import_dir = output_dir / s3_config.output_dir

        log_s3_import_command(
            bucket=s3_parent_config.bucket,
            prefix=import_prefix,
            output_dir=import_dir,
        )

        result = run_s3_import(postprocess_config, output_dir)
        logger.info(f"S3 import result: {result}")
        return result

    except Exception as e:
        logger.exception("S3 import failed")
        return {"status": "failed", "error": str(e), "completed_at": time.time()}


def _run_artifacts_to_ai_data(
    postprocess_config,
    plugin,
    model,
    output_dir: Path,
    plugin_module: str,
    base_dir: Path,
) -> dict[str, Any]:
    """Export AI evaluation payload with structured directories and copied artifacts."""
    try:
        # Always log the step header, even for skipped operations
        ai_data_dir = output_dir / postprocess_config.kpi.artifacts_to_ai_data.output_dir
        output_file = ai_data_dir / "ai_data_payload.json"
        log_ai_data_command(
            base_dir=base_dir,
            plugin_module=plugin_module,
            output_file=output_file,
        )

        if not hasattr(plugin, "build_ai_data_payload"):
            return {
                "status": "skipped",
                "reason": "plugin does not support AI evaluation",
                "completed_at": time.time(),
            }

        # Create AI evaluation directory structure using configured output directory
        ai_data_dir.mkdir(parents=True, exist_ok=True)

        # Build payload from plugin
        payload = plugin.build_ai_data_payload(model)

        # Export structured test entries with artifact copying
        exported_entries = _export_test_entries_with_artifacts(model, ai_data_dir, base_dir, plugin)

        # Add exported entries info to payload
        payload["exported_test_entries"] = exported_entries

        # Write main AI eval payload
        output_file.parent.mkdir(parents=True, exist_ok=True)

        import json

        with open(output_file, "w") as f:
            json.dump(payload, f, indent=2)

        logger.info(f"Generated AI evaluation payload in {output_file}")
        logger.info(f"Exported {len(exported_entries)} test entries with artifacts")

        return {
            "status": "success",
            "output_file": str(output_file),
            "ai_data_dir": str(ai_data_dir),
            "exported_entries": len(exported_entries),
            "completed_at": time.time(),
        }
    except Exception as e:
        logger.error(f"AI eval export failed: {e}")
        return {"status": "failed", "error": str(e), "completed_at": time.time()}


def _load_test_labels(test_dir: Path) -> dict[str, Any]:
    """Load test labels from __test_labels__.yaml file if it exists.

    Args:
        test_dir: Directory to search for __test_labels__.yaml

    Returns:
        Dictionary containing test labels, or empty dict if file doesn't exist
    """
    import yaml

    test_labels_file = test_dir / "__test_labels__.yaml"
    if test_labels_file.exists():
        try:
            with open(test_labels_file, encoding="utf-8") as f:
                labels = yaml.safe_load(f)
                logger.debug(f"Loaded test labels from {test_labels_file}: {labels}")
                return labels or {}
        except Exception as e:
            logger.warning(f"Failed to load test labels from {test_labels_file}: {e}")
            return {}
    else:
        logger.debug(f"No test labels file found at {test_labels_file}")
        return {}


def _export_test_entries_with_artifacts(
    model, ai_data_dir: Path, base_dir: Path, plugin
) -> list[dict]:
    """
    Export test entries by creating directories and copying specific artifacts.

    Args:
        model: Unified model containing test results
        ai_data_dir: Directory where test entries should be exported
        base_dir: Base directory of the test artifacts (test directory)
        plugin: Plugin instance to get artifact file list

    Returns:
        List of exported test entry information
    """
    import shutil

    exported_entries = []

    for idx, record in enumerate(model.unified_result_records):
        # Create directory for this test entry
        test_entry_dir = ai_data_dir / f"test_entry_{idx:03d}"
        test_entry_dir.mkdir(parents=True, exist_ok=True)

        # Load test labels from __test_labels__.yaml if available
        test_dir = base_dir / record.test_base_path
        test_labels = _load_test_labels(test_dir)

        # Record test entry metadata
        entry_info = {
            "entry_id": f"test_entry_{idx:03d}",
            "test_base_path": str(record.test_base_path),
            "distinguishing_labels": record.distinguishing_labels,
            "test_labels": test_labels,
            "copied_files": [],
            "missing_files": [],
        }

        # Get artifact files specific to this test directory only (plugin is scoped to test directory)
        relevant_files = plugin.get_ai_data_artifact_files_for_test(test_dir)

        logger.debug(
            f"Test entry {idx}: found {len(relevant_files)} artifact files in test directory {test_dir}"
        )

        # Copy relevant files for this test entry (preserving directory structure)
        for target_file in relevant_files:
            # source is test_dir + test_relative_path, target is test_entry_dir + test_relative_path
            source_file = test_dir / target_file
            target_path = test_entry_dir / target_file

            if source_file.exists():
                target_path.parent.mkdir(parents=True, exist_ok=True)

                try:
                    shutil.copy2(source_file, target_path)
                    entry_info["copied_files"].append(
                        {
                            "source": str(source_file),
                            "target": str(target_path),
                            "relative_path": target_file,
                            "size_bytes": source_file.stat().st_size,
                        }
                    )
                    logger.debug(f"Copied {source_file} -> {target_path}")
                except Exception as e:
                    logger.warning(f"Failed to copy {source_file}: {e}")
                    entry_info["missing_files"].append({"file": str(source_file), "error": str(e)})
            else:
                entry_info["missing_files"].append(
                    {"file": str(source_file), "error": "File does not exist"}
                )

        # Write entry metadata
        entry_metadata_file = test_entry_dir / "entry_metadata.json"
        import json

        with open(entry_metadata_file, "w") as f:
            json.dump(entry_info, f, indent=2)

        exported_entries.append(entry_info)

    return exported_entries


def _run_kpis_to_csv(
    postprocess_config: CaliperOrchestrationPostprocessConfig,
    plugin,
    model,
    output_dir: Path,
    kpi_json_path: Path,
) -> dict[str, Any]:
    """Export KPI data to CSV format using the plugin's compute_kpis method."""

    if not postprocess_config.kpi.enabled:
        return {"status": "disabled", "reason": "kpi disabled", "completed_at": time.time()}
    if not postprocess_config.kpi.kpis_to_csv.enabled:
        return {
            "status": "disabled",
            "reason": "kpi.kpis_to_csv disabled",
            "completed_at": time.time(),
        }

    try:
        # Compute KPIs from the model
        kpi_records = plugin.compute_kpis(model)

        # Create output file path
        output_file = output_dir / postprocess_config.kpi.kpis_to_csv.output
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # Log command to reproduce this step
        log_kpis_to_csv_command(
            input_path=kpi_json_path,
            output_path=output_file,
        )

        # Import and use the CSV exporter
        from projects.guidellm.postprocess.guidellm.csv_export import quick_export_kpis_to_csv

        result_path = quick_export_kpis_to_csv(
            records=kpi_records,
            output_path=output_file,
            include_header_comments=postprocess_config.kpi.kpis_to_csv.include_header_comments,
        )

        logger.info(f"Exported {len(kpi_records)} KPI records to CSV: {result_path}")
        return {
            "status": "success",
            "kpi_count": len(kpi_records),
            "output_file": result_path,
            "completed_at": time.time(),
        }
    except Exception as e:
        logger.error(f"KPI CSV export failed: {e}")
        return {"status": "failed", "error": str(e), "completed_at": time.time()}


def _stub_analyze(
    postprocess_config: CaliperOrchestrationPostprocessConfig,
    plugin_module: str,
    base_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    if not postprocess_config.analyze.enabled:
        return {"status": "disabled", "reason": "analyze disabled"}

    try:
        # Determine paths for analysis
        analyze_config = postprocess_config.analyze

        # Current KPI file should be in the postprocess output directory
        current_kpis_path = output_dir / "kpis.json"

        # Historical KPIs directory path
        historical_kpis_dir = Path(analyze_config.historical_kpis)
        if not historical_kpis_dir.is_absolute():
            historical_kpis_dir = output_dir / historical_kpis_dir

        # Output path for analysis results
        output_path = output_dir / analyze_config.output

        # Log command to reproduce this step (after all paths are determined)
        log_analyze_command(
            base_dir=base_dir,
            plugin_module=plugin_module,
            current_kpis_path=current_kpis_path,
            historical_kpis_dir=historical_kpis_dir,
            output_path=output_path,
        )

        # Check if required files exist
        if not current_kpis_path.exists():
            return {
                "status": "failed",
                "error": f"Current KPI file not found: {current_kpis_path}",
                "completed_at": time.time(),
            }

        if not historical_kpis_dir.exists():
            return {
                "status": "failed",
                "error": f"Historical KPIs directory not found: {historical_kpis_dir}",
                "completed_at": time.time(),
            }

        # Find all kpis.json files in the historical directory
        kpi_files = list(historical_kpis_dir.rglob("kpis.json"))

        if not kpi_files:
            return {
                "status": "failed",
                "error": f"No kpis.json files found in historical directory: {historical_kpis_dir}",
                "completed_at": time.time(),
            }

        # Use the most recently modified KPI file as baseline
        baseline_file = max(kpi_files, key=lambda p: p.stat().st_mtime)
        baseline_path = historical_kpis_dir  # Use the directory path instead of specific file

        logger.info(
            f"Found {len(kpi_files)} historical KPI files, using most recent: {baseline_file}"
        )
        logger.info(f"Historical data directory: {baseline_path}")

        logger.info(f"Running KPI regression analysis: {current_kpis_path} vs {baseline_file}")

        # Add debug logging to understand the KPI structure before analysis
        try:
            from projects.caliper.engine.kpi.import_export import load_kpis_json

            logger.debug("Loading current KPI file for structure validation...")
            current_kpis = load_kpis_json(current_kpis_path)
            logger.info(f"Current KPI file loaded: {len(current_kpis)} KPIs")

            logger.debug("Loading baseline KPI file for structure validation...")
            baseline_kpis = load_kpis_json(baseline_file)
            logger.info(f"Baseline KPI file loaded: {len(baseline_kpis)} KPIs")

            # Check structure and validate KPI format
            invalid_files = []

            # Validate current KPI file format
            if current_kpis:
                try:
                    if isinstance(current_kpis, list) and len(current_kpis) > 0:
                        # Old list format
                        sample_current = current_kpis[0]
                        logger.debug(f"Current KPI format: list with {len(current_kpis)} items")
                        logger.debug(f"Sample current KPI keys: {list(sample_current.keys())}")
                    elif isinstance(current_kpis, dict):
                        # Hierarchical format
                        logger.debug(
                            f"Current KPI format: hierarchical dict with keys: {list(current_kpis.keys())}"
                        )
                    else:
                        raise ValueError(f"Unknown current KPI format: {type(current_kpis)}")
                except Exception as e:
                    logger.warning(f"Invalid current KPI file format: {e}")
                    invalid_files.append(("current", str(current_kpis_path), str(e)))

            # Validate baseline KPI file format
            if baseline_kpis:
                try:
                    if isinstance(baseline_kpis, list) and len(baseline_kpis) > 0:
                        # Old list format
                        sample_baseline = baseline_kpis[0]
                        logger.debug(f"Baseline KPI format: list with {len(baseline_kpis)} items")
                        logger.debug(f"Sample baseline KPI keys: {list(sample_baseline.keys())}")

                        # Check if baseline has the required kpi_id field for list format
                        if "kpi_id" not in sample_baseline:
                            raise KeyError(
                                f"Baseline KPI file missing 'kpi_id' field. Found keys: {list(sample_baseline.keys())}"
                            )
                    elif isinstance(baseline_kpis, dict):
                        # Hierarchical format
                        logger.debug(
                            f"Baseline KPI format: hierarchical dict with keys: {list(baseline_kpis.keys())}"
                        )
                    else:
                        raise ValueError(f"Unknown baseline KPI format: {type(baseline_kpis)}")
                except Exception as e:
                    logger.warning(f"Invalid baseline KPI file format: {e}")
                    invalid_files.append(("baseline", str(baseline_file), str(e)))

            # If we have invalid files, report them but continue
            if invalid_files:
                error_details = "; ".join(
                    [f"{role} file {path}: {error}" for role, path, error in invalid_files]
                )
                logger.error(f"Found {len(invalid_files)} invalid KPI files: {error_details}")

                # If both files are invalid, fail the analysis
                if len(invalid_files) >= 2:
                    return {
                        "status": "failed",
                        "error": f"Both current and baseline KPI files are invalid: {error_details}",
                        "invalid_files": invalid_files,
                        "completed_at": time.time(),
                    }

        except Exception as load_error:
            logger.error(f"Failed to load/validate KPI files: {load_error}")
            return {
                "status": "failed",
                "error": f"KPI file validation failed: {load_error}",
                "current_kpis_path": str(current_kpis_path),
                "baseline_path": str(baseline_path),
                "baseline_file_used": str(baseline_file),
                "completed_at": time.time(),
            }

        # Skip analysis for hierarchical format until analysis code is updated
        logger.info(
            "Skipping analysis: hierarchical format detected but analysis code needs updating for new format"
        )
        return {
            "status": "skipped",
            "reason": "not implemented",
            "completed_at": time.time(),
        }

    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        logger.exception(f"Analysis step failed with {error_msg}")
        return {
            "status": "failed",
            "error": error_msg,
            "error_type": type(e).__name__,
            "current_kpis_path": str(current_kpis_path)
            if "current_kpis_path" in locals()
            else "unknown",
            "baseline_path": str(baseline_path) if "baseline_path" in locals() else "unknown",
            "baseline_file_used": str(baseline_file) if "baseline_file" in locals() else "unknown",
            "completed_at": time.time(),
        }


class CaliperPostprocessOrchestrator:
    """
    Orchestrator for running Caliper postprocessing steps in sequence.

    Manages the execution of parse, visualize, KPI, AI evaluation, and analysis steps
    with proper state management, logging, and error handling.
    """

    def __init__(
        self,
        postprocess_config_raw: dict[str, Any] | None,
        *,
        artifacts_dir: Path,
        visualize_output_dir: Path | None = None,
        test_outcome: TestPhaseOutcome | None = None,
    ):
        self.artifacts_dir = artifacts_dir
        self.visualize_output_dir = visualize_output_dir
        self.test_outcome = test_outcome or TestPhaseOutcome("NOT_AVAILABLE")

        # State tracking
        self.steps: list[dict[str, Any]] = []
        self.parse_failed = False
        self.visualize_failed = False
        self.artifacts_to_kpis_failed = False
        self.ai_data_failed = False
        self.s3_import_failed = False
        self.analyze_failed = False
        self.s3_export_failed = False

        # Configuration
        try:
            self.config = CaliperOrchestrationPostprocessConfig.model_validate(
                postprocess_config_raw or {}
            )
        except ValidationError as e:
            logger.error("Invalid caliper postprocess config: %s", e)
            raise

        # Resolved paths - will be set in _setup_paths()
        self.tree_root: Path
        self.manifest_path: Path | None
        self.cache_path: Path
        self.step_logs_dir: Path

    def run(self) -> dict[str, Any]:
        """
        Run enabled parse / visualize steps and compute ``final_status``.

        Returns:
            Dictionary containing final_status, success flag, test_phase info, and step results
        """
        try:
            return self._execute_orchestration()
        finally:
            cleanup_step_logging()

    def _execute_orchestration(self) -> dict[str, Any]:
        """Main orchestration logic."""
        test_block = {"phase": self.test_outcome.phase, "message": self.test_outcome.message}

        # Check if postprocessing is enabled
        if not self.config.enabled:
            logger.info("caliper.postprocess.enabled is false — skipping post-processing steps")
            return self._build_result(
                compute_final_postprocess_status(
                    test_outcome=self.test_outcome,
                    parse_failed=False,
                    visualize_failed=False,
                    artifacts_to_kpis_failed=False,
                    ai_data_failed=False,
                    s3_import_failed=False,
                    analyze_failed=False,
                    s3_export_failed=False,
                    has_regression=False,
                    has_improvement=False,
                ),
                test_block,
            )

        # Setup paths and directories
        self._setup_paths()

        # Check if any steps are enabled
        if not self._any_step_enabled():
            logger.info("caliper.postprocess: no parse/visualize/kpi/analyze steps enabled")
            return self._build_result(
                compute_final_postprocess_status(
                    test_outcome=self.test_outcome,
                    parse_failed=False,
                    visualize_failed=False,
                    artifacts_to_kpis_failed=False,
                    ai_data_failed=False,
                    s3_import_failed=False,
                    analyze_failed=False,
                    s3_export_failed=False,
                    has_regression=False,
                    has_improvement=False,
                ),
                test_block,
            )

        # Execute steps in sequence
        logger.info("Starting postprocessing steps")
        self._run_parse_step()
        logger.info(f"After parse step: parse_failed={self.parse_failed}")
        self._run_visualize_step()
        logger.info(f"After visualize step: visualize_failed={self.visualize_failed}")
        self._run_kpi_and_ai_data_steps()
        logger.info(
            f"After KPI/AI steps: artifacts_to_kpis_failed={self.artifacts_to_kpis_failed}, ai_data_failed={self.ai_data_failed}"
        )
        logger.info("All postprocessing steps completed")

        # Compute final status and build result
        final_status = self._compute_final_status()
        result = self._build_result(final_status, test_block)

        # Generate HTML reports if output directory is available
        if self.visualize_output_dir:
            self._generate_reports(result)

        # Save postprocess status YAML for notifications
        self._save_postprocess_status_yaml(result)

        return result

    def _setup_paths(self) -> None:
        """Resolve and setup all required paths."""
        self.tree_root, self.manifest_path, self.cache_path = _resolve_paths(
            self.config, artifacts_dir=self.artifacts_dir
        )

        self.step_logs_dir = Path(env.ARTIFACT_DIR)
        self.step_logs_dir.mkdir(parents=True, exist_ok=True)

    def _add_step(self, step_name: str, step_data: dict[str, Any]) -> None:
        """Add a step result to the steps list."""
        self.steps.append({step_name: step_data})

    def _get_step(self, step_name: str) -> dict[str, Any]:
        """Get a step result by name."""
        for step in self.steps:
            if step_name in step:
                return step[step_name]
        return {}

    def _any_step_enabled(self) -> bool:
        """Check if any postprocessing step is enabled."""
        return (
            self.config.parse.enabled
            or self.config.visualize.enabled
            or self.config.kpi.enabled
            or self.config.analyze.enabled
        )

    def _build_result(self, final_status: str, test_block: dict[str, Any]) -> dict[str, Any]:
        """Build the final result dictionary."""
        return {
            "final_status": final_status,
            "success": final_status == FINAL_SUCCESS,
            "test_phase": test_block,
            "steps": self.steps,
        }

    def _run_parse_step(self) -> None:
        """Execute the parse step if enabled."""
        if not self.config.parse.enabled:
            return

        with step_logging("caliper_parse", self.step_logs_dir):
            try:
                mod_str, plugin = _load_plugin(
                    self.config, tree_root=self.tree_root, manifest_path=self.manifest_path
                )

                # Log command to reproduce this step
                log_parse_command(
                    base_dir=self.tree_root,
                    plugin_module=mod_str,
                    use_cache=not self.config.parse.no_cache,
                    manifest_path=self.manifest_path,
                )

                model = run_parse(
                    base_dir=self.tree_root,
                    plugin_module=mod_str,
                    plugin=plugin,
                    use_cache=not self.config.parse.no_cache,
                )

                self._add_step(
                    "parse",
                    {
                        "status": "success",
                        "plugin_module": mod_str,
                        "record_count": len(model.unified_result_records),
                        "parse_cache_ref": model.parse_cache_ref,
                        "completed_at": time.time(),
                    },
                )
            except Exception as e:  # noqa: BLE001
                self.parse_failed = True
                logger.exception("Caliper parse failed")
                self._add_step(
                    "parse",
                    {
                        "status": "failure",
                        "detail": str(e),
                        "traceback": traceback.format_exc(),
                        "completed_at": time.time(),
                    },
                )

    def _run_visualize_step(self) -> None:
        """Execute the visualize step if enabled."""
        if not self.config.visualize.enabled:
            return

        with step_logging("caliper_visualize", self.step_logs_dir):
            try:
                mod_str, plugin = _load_plugin(
                    self.config, tree_root=self.tree_root, manifest_path=self.manifest_path
                )

                viz_cfg_path = _resolve_visualize_config_path(
                    self.config.visualize.visualize_config,
                    artifact_tree=self.tree_root,
                )

                if self.visualize_output_dir is not None:
                    output_dir = self.visualize_output_dir.expanduser().resolve()
                else:
                    output_dir = _resolve_visualize_output_dir(
                        self.config.visualize.output_dir,
                    )

                # Log command to reproduce this step
                log_visualize_command(
                    base_dir=self.tree_root,
                    plugin_module=mod_str,
                    output_dir=output_dir,
                    reports_csv=self.config.visualize.reports,
                    report_group=self.config.visualize.report_group,
                    visualize_config_path=viz_cfg_path,
                    include_pairs=tuple(self.config.visualize.include_labels),
                    exclude_pairs=tuple(self.config.visualize.exclude_labels),
                    use_cache=not self.config.parse.no_cache,
                )

                paths = run_visualize(
                    base_dir=self.tree_root,
                    plugin_module=mod_str,
                    plugin=plugin,
                    output_dir=output_dir,
                    reports_csv=self.config.visualize.reports,
                    report_group=self.config.visualize.report_group,
                    visualize_config_path=viz_cfg_path,
                    include_pairs=tuple(self.config.visualize.include_labels),
                    exclude_pairs=tuple(self.config.visualize.exclude_labels),
                    use_cache=not self.config.parse.no_cache,
                    cache_path=self.cache_path,
                )

                # Convert paths to relative paths from output_dir
                relative_paths = []
                for path in paths:
                    try:
                        path_obj = Path(path)
                        relative_path = path_obj.relative_to(output_dir)
                        relative_paths.append(str(relative_path))
                    except ValueError:
                        # If path is not under output_dir, keep as-is
                        relative_paths.append(str(path))

                self._add_step(
                    "visualize",
                    {
                        "status": "success",
                        "plugin_module": mod_str,
                        "output_dir": str(output_dir),
                        "paths": relative_paths,
                        "completed_at": time.time(),
                    },
                )

            except Exception as e:  # noqa: BLE001
                self.visualize_failed = True
                logger.exception("Caliper visualize failed")
                self._add_step(
                    "visualize",
                    {
                        "status": "failure",
                        "detail": str(e),
                        "traceback": traceback.format_exc(),
                        "completed_at": time.time(),
                    },
                )

    def _run_kpi_and_ai_data_steps(self) -> None:
        """Execute KPI generation, CSV export, KPI export, and AI evaluation steps."""
        if not self.config.kpi.enabled:
            return

        try:
            # Determine output directory
            if self.config.visualize.enabled and self.visualize_output_dir:
                output_dir = Path(self.visualize_output_dir)
            else:
                output_dir = Path(self.artifacts_dir) / "postprocess_output"
                output_dir.mkdir(parents=True, exist_ok=True)

            # Load plugin and model
            mod_str, plugin = _load_plugin(
                self.config, tree_root=self.tree_root, manifest_path=self.manifest_path
            )
            model = run_parse(
                base_dir=self.tree_root,
                plugin_module=mod_str,
                plugin=plugin,
                use_cache=not self.config.parse.no_cache,
            )

            # KPI JSON generation
            self._run_artifacts_to_kpis_step(plugin, model, output_dir, mod_str)

            # KPI CSV export
            self._run_kpis_to_csv_step(plugin, model, output_dir)

            # AI evaluation export
            self._run_artifacts_to_ai_data_step(plugin, model, output_dir, mod_str)

            # S3 import (historical data)
            self._run_s3_import_step(output_dir)

            # Analyze KPIs (current vs historical) - moved before S3 export
            self._run_analyse_kpis_step(output_dir, mod_str)

            # S3 export
            self._run_s3_export_step(output_dir)

        except Exception as e:
            completion_time = time.time()
            logger.error(f"Failed to run KPI/AI eval operations: {e}")
            self._add_step(
                "artifacts_to_kpis",
                {
                    "status": "failed",
                    "error": str(e),
                    "completed_at": completion_time,
                },
            )
            self._add_step(
                "kpis_to_csv",
                {
                    "status": "failed",
                    "error": str(e),
                    "completed_at": completion_time,
                },
            )
            self._add_step(
                "artifacts_to_ai_data",
                {
                    "status": "failed",
                    "error": str(e),
                    "completed_at": completion_time,
                },
            )
            self._add_step(
                "s3_import",
                {
                    "status": "skipped",
                    "reason": "failed to load plugin",
                    "completed_at": completion_time,
                },
            )
            self._add_step(
                "analyse_kpis",
                {
                    "status": "skipped",
                    "reason": "failed to load plugin",
                    "completed_at": completion_time,
                },
            )
            self._add_step(
                "s3_export",
                {
                    "status": "skipped",
                    "reason": "failed to load plugin",
                    "completed_at": completion_time,
                },
            )
            self.artifacts_to_kpis_failed = True
            self.ai_data_failed = True
            self.s3_import_failed = True
            self.analyze_failed = True
            self.s3_export_failed = True

    def _run_artifacts_to_kpis_step(
        self, plugin: Any, model: Any, output_dir: Path, mod_str: str
    ) -> None:
        """Execute the KPI generation step."""
        if self.config.kpi.artifacts_to_kpis.enabled:
            with step_logging("caliper_artifacts_to_kpis", self.step_logs_dir):
                result = _run_artifacts_to_kpis(
                    self.config, plugin, model, output_dir, mod_str, self.tree_root
                )
                self._add_step("artifacts_to_kpis", result)
                if result.get("status") == "failed":
                    self.artifacts_to_kpis_failed = True
        else:
            self._add_step(
                "artifacts_to_kpis",
                {
                    "status": "disabled",
                    "reason": "kpi.artifacts_to_kpis disabled",
                    "completed_at": time.time(),
                },
            )

    def _run_kpis_to_csv_step(self, plugin: Any, model: Any, output_dir: Path) -> None:
        """Execute the KPI CSV export step."""
        if not self.config.kpi.kpis_to_csv.enabled:
            self._add_step(
                "kpis_to_csv",
                {
                    "status": "disabled",
                    "reason": "kpi.kpis_to_csv disabled",
                    "completed_at": time.time(),
                },
            )
            return

        with step_logging("caliper_kpis_to_csv", self.step_logs_dir):
            # Path to the JSON file for reference in command logging
            kpi_json_path = output_dir / self.config.kpi.artifacts_to_kpis.output
            result = _run_kpis_to_csv(self.config, plugin, model, output_dir, kpi_json_path)
            self._add_step("kpis_to_csv", result)
            if result.get("status") == "failed":
                # CSV export failure doesn't affect overall status - it's supplementary
                logger.warning("KPI CSV export failed but continuing execution")

    def _run_artifacts_to_ai_data_step(
        self, plugin: Any, model: Any, output_dir: Path, mod_str: str
    ) -> None:
        """Execute the AI evaluation export step."""
        if not self.config.kpi.artifacts_to_ai_data.enabled:
            self._add_step(
                "artifacts_to_ai_data",
                {
                    "status": "disabled",
                    "reason": "kpi.artifacts_to_ai_data disabled",
                    "completed_at": time.time(),
                },
            )
            return

        with step_logging("caliper_artifacts_to_ai_data", self.step_logs_dir):
            try:
                result = _run_artifacts_to_ai_data(
                    self.config, plugin, model, output_dir, mod_str, self.tree_root
                )
                self._add_step("artifacts_to_ai_data", result)
                logger.info(f"AI eval export result: {result}")

                # Check if the result indicates failure (since _run_artifacts_to_ai_data catches exceptions)
                if result.get("status") == "failed":
                    self.ai_data_failed = True

            except Exception as e:
                logger.exception("AI eval export failed")
                self._add_step("artifacts_to_ai_data", {"status": "failed", "error": str(e)})
                self.ai_data_failed = True

    def _run_s3_import_step(self, output_dir: Path) -> None:
        """Execute the S3 import step."""
        if not self.config.s3.import_.enabled:
            self._add_step(
                "s3_import",
                {
                    "status": "disabled",
                    "reason": "s3_import disabled",
                    "completed_at": time.time(),
                },
            )
            return

        with step_logging("caliper_s3_import", self.step_logs_dir):
            try:
                result = _run_s3_import(self.config, output_dir)
                self._add_step("s3_import", result)
                logger.info(f"S3 import result: {result}")

                if result.get("status") == "failed":
                    self.s3_import_failed = True

            except Exception as e:
                logger.exception("S3 import failed")
                self._add_step("s3_import", {"status": "failed", "error": str(e)})
                self.s3_import_failed = True

    def _run_analyse_kpis_step(self, output_dir: Path, plugin_module: str) -> None:
        """Execute the KPI analysis step."""
        if not self.config.analyze.enabled:
            self._add_step(
                "analyse_kpis",
                {
                    "status": "disabled",
                    "reason": "analyze disabled",
                    "completed_at": time.time(),
                },
            )
            return

        with step_logging("caliper_analyse_kpis", self.step_logs_dir):
            try:
                result = _stub_analyze(self.config, plugin_module, self.tree_root, output_dir)
                self._add_step("analyse_kpis", result)
                logger.info(f"KPI analysis result: {result}")

                if result.get("status") == "failed":
                    self.analyze_failed = True

            except Exception as e:
                logger.exception("KPI analysis failed")
                self._add_step("analyse_kpis", {"status": "failed", "error": str(e)})
                self.analyze_failed = True

    def _run_s3_export_step(self, output_dir: Path) -> None:
        """Execute the S3 export step."""
        if not self.config.s3.export.enabled:
            self._add_step(
                "s3_export",
                {
                    "status": "disabled",
                    "reason": "s3_export disabled",
                    "completed_at": time.time(),
                },
            )
            return

        with step_logging("caliper_s3_export", self.step_logs_dir):
            try:
                # Determine AI eval directory if it was generated
                ai_data_dir = None
                artifacts_to_ai_data_step = self._get_step("artifacts_to_ai_data")
                if (
                    self.config.kpi.artifacts_to_ai_data.enabled
                    and artifacts_to_ai_data_step.get("status") == "success"
                ):
                    ai_data_dir = output_dir / self.config.kpi.artifacts_to_ai_data.output_dir

                # Log the CLI command to reproduce this step
                s3_parent_config = self.config.s3
                export_config = s3_parent_config.export
                export_path = f"{s3_parent_config.instance}/{s3_parent_config.directory}"
                if export_config.prefix:
                    export_path += f"/{export_config.prefix}"

                # Only include AI data if it was actually generated successfully
                include_ai_data_actual = export_config.include_ai_data and ai_data_dir is not None

                log_s3_export_command(
                    bucket=s3_parent_config.bucket,
                    export_path=export_path,
                    from_dir=output_dir,
                    include_csv=export_config.include_csv,
                    include_kpis_json=export_config.include_kpis_json,
                    include_ai_data=include_ai_data_actual,
                )

                result = run_s3_export(self.config, output_dir, ai_data_dir)
                self._add_step("s3_export", result)

                if result.get("status") == "success":
                    # Format status for better readability
                    duration = result.get("duration", 0)
                    uploaded_files = result.get("uploaded_files", 0)
                    failed_files = result.get("failed_files", 0)
                    total_files = result.get("total_files", 0)
                    s3_path = result.get("exported_path", "")

                    logger.info(
                        f"S3 export completed successfully in {duration}s: "
                        f"{uploaded_files}/{total_files} files uploaded"
                        f"{f' ({failed_files} failed)' if failed_files > 0 else ''} "
                        f"to {s3_path}"
                    )
                else:
                    error = result.get("error", "unknown error")
                    logger.warning(f"S3 export failed: {error}")

            except Exception as e:
                logger.exception("S3 export step failed")
                self._add_step(
                    "s3_export",
                    {
                        "status": "failed",
                        "error": str(e),
                        "completed_at": time.time(),
                    },
                )

    def _compute_final_status(self) -> str:
        """Compute the final postprocessing status."""
        # Debug logging to identify what's causing failures
        logger.info("Computing final status with failure flags:")
        logger.info(f"  test_outcome.phase: {self.test_outcome.phase}")
        logger.info(f"  parse_failed: {self.parse_failed}")
        logger.info(f"  visualize_failed: {self.visualize_failed}")
        logger.info(f"  artifacts_to_kpis_failed: {self.artifacts_to_kpis_failed}")
        logger.info(f"  ai_data_failed: {self.ai_data_failed}")
        logger.info(f"  s3_import_failed: {self.s3_import_failed}")
        logger.info(f"  analyze_failed: {self.analyze_failed}")
        logger.info(f"  s3_export_failed: {self.s3_export_failed}")
        logger.info(f"  analyze_failed: {self.analyze_failed}")

        final_status = compute_final_postprocess_status(
            test_outcome=self.test_outcome,
            parse_failed=self.parse_failed,
            visualize_failed=self.visualize_failed,
            artifacts_to_kpis_failed=self.artifacts_to_kpis_failed,
            ai_data_failed=self.ai_data_failed,
            s3_import_failed=self.s3_import_failed,
            analyze_failed=self.analyze_failed,
            s3_export_failed=self.s3_export_failed,
            has_regression=False,
            has_improvement=False,
        )

        logger.info(f"Computed final status: {final_status}")
        return final_status

    def _generate_reports(self, result: dict[str, Any]) -> None:
        """Generate HTML reports if output directory is available."""
        output_dir = self.visualize_output_dir.resolve()

        # Import here to avoid circular imports
        from projects.core.library.postprocess import generate_postprocess_status_report
        from projects.core.library.reports_index import generate_caliper_reports_index

        try:
            generate_caliper_reports_index(result, output_dir, "reports_index.html")
        except Exception as e:
            logger.warning("Failed to generate reports index: %s", e)

        try:
            generate_postprocess_status_report(result, output_dir, "postprocess_status.html")
        except Exception as e:
            logger.warning("Failed to generate postprocessing status report: %s", e)

    def _save_postprocess_status_yaml(self, result: dict[str, Any]) -> None:
        """Save postprocess status as YAML for GitHub notifications."""
        try:
            import yaml

            # Use ARTIFACT_DIR if available, otherwise use the visualize output directory
            if env.ARTIFACT_DIR:
                output_dir = Path(env.ARTIFACT_DIR)
            elif self.visualize_output_dir:
                output_dir = Path(self.visualize_output_dir)
            else:
                logger.warning("No output directory available for postprocess status YAML")
                return

            output_dir.mkdir(parents=True, exist_ok=True)
            status_file = output_dir / "caliper_postprocess_status.yaml"

            with open(status_file, "w", encoding="utf-8") as f:
                yaml.dump(result, f, default_flow_style=False, sort_keys=True)

            logger.info(f"Saved postprocess status to {status_file}")

        except Exception as e:
            logger.warning(f"Failed to save postprocess status YAML: {e}")


def run_postprocess_from_orchestration_config(
    postprocess_config_raw: dict[str, Any] | None,
    *,
    artifacts_dir: Path,
    visualize_output_dir: Path | None = None,
    test_outcome: TestPhaseOutcome | None = None,
) -> dict[str, Any]:
    """
    Run enabled parse / visualize steps and compute ``final_status``.

    KPI and analyze sections only emit stub ``steps`` entries (never failures).

    Parse/visualize use ``artifacts_dir`` and ``visualize_output_dir``.
    """
    orchestrator = CaliperPostprocessOrchestrator(
        postprocess_config_raw,
        artifacts_dir=artifacts_dir,
        visualize_output_dir=visualize_output_dir,
        test_outcome=test_outcome,
    )
    return orchestrator.run()
