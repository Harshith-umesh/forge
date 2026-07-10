"""
Config-driven Caliper parse / visualize / KPI / AI eval for FORGE orchestration.

KPI generation and AI evaluation export are now implemented. Regression analyze is still
a stub. All steps maintain a stable ``steps`` shape for caller compatibility.

Computes ``final_status`` from the FORGE test phase outcome plus all enabled step results.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
import traceback
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from projects.caliper.cli.orchestration_entrypoints import (
    analyze_kpis_entrypoint,
    get_kpi_functions_entrypoint,
    load_plugin_entrypoint,
    parse_entrypoint,
)
from projects.caliper.orchestration.cli_builder import (
    build_ai_eval_export_command,
    build_kpi_csv_export_command,
    build_kpi_generate_command,
    build_parse_command,
    build_s3_export_command,
    build_s3_import_command,
    build_visualize_command,
    handle_caliper_output_and_completion,
    log_caliper_start_banner,
)
from projects.caliper.orchestration.postprocess_config import (
    CaliperOrchestrationPostprocessConfig,
)
from projects.caliper.orchestration.postprocess_outcome import (
    FINAL_KPI_PIPELINE_FAILED,
    FINAL_SUCCESS,
    TestPhaseOutcome,
    compute_final_postprocess_status,
)
from projects.caliper.orchestration.step_logging import (
    cleanup_step_logging,
    step_logging,
)
from projects.core.library import env

logger = logging.getLogger(__name__)


def _execute_caliper_command(
    command: list[str],
    step_name: str,
    status_file: Path,
    step_logs_dir: Path,
) -> tuple[subprocess.CompletedProcess, dict[str, Any], Path]:
    """Execute a Caliper CLI command and return result and status data.

    Args:
        command: CLI command arguments list
        step_name: Name for logging banners (e.g., "caliper parse", "caliper visualize")
        status_file: Path to read status YAML from
        step_logs_dir: Directory to create log file in

    Returns:
        Tuple of (subprocess result, parsed status data, log file path)
    """
    # Create log file path with proper step index
    from projects.caliper.orchestration.step_logging import _get_next_step_index

    step_index = _get_next_step_index(step_logs_dir)
    step_name_safe = step_name.replace(" ", "_")
    log_file = step_logs_dir / f"{step_index:03d}__{step_name_safe}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # Create script file for debugging
    script_file = env.ARTIFACT_DIR / f"{step_name_safe}.sh"

    # Log start banner and save script
    log_caliper_start_banner(command, script_file, step_name.upper())

    # Execute command with combined stdout/stderr
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # Combine stderr into stdout
        text=True,
    )

    # Handle output, status parsing, and log completion banner
    status_data = handle_caliper_output_and_completion(
        result, log_file, status_file, step_name.upper()
    )

    return result, status_data, log_file


def _make_path_relative_to_base(file_path: str | Path, base_dir: Path) -> str:
    """Convert absolute path to relative path from base directory.

    Args:
        file_path: Absolute or relative file path
        base_dir: Base directory to make path relative to

    Returns:
        Relative path as string
    """
    try:
        path_obj = Path(file_path)
        if path_obj.is_absolute():
            return str(path_obj.relative_to(base_dir))
        else:
            return str(path_obj)
    except (ValueError, TypeError):
        # If can't make relative, return the filename
        return Path(file_path).name


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
        # If empty, use env.ARTIFACT_DIR
        return env.ARTIFACT_DIR

    p = Path(raw).expanduser()
    if p.is_absolute():
        # If absolute, don't touch
        return p.resolve()
    else:
        # If relative, use env.ARTIFACT_DIR / output_dir
        return (env.ARTIFACT_DIR / p).resolve()


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


# _load_plugin function removed - now using load_plugin_entrypoint


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

    if not kpis:
        return {"schema_version": "2", "tests": []}

    # Group KPIs by test (run_id)
    tests_data = defaultdict(lambda: {"kpis": [], "labels": {}, "metadata": {}})

    # Get KPI function metadata from the plugin module
    kpi_functions = get_kpi_functions_entrypoint(model.plugin_module)

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
    manifest_path: Path | None,
    step_logs_dir: Path,
) -> dict[str, Any]:
    """Generate KPI JSON using fork/exec subprocess execution."""

    if not postprocess_config.kpi.enabled:
        return {"status": "disabled", "reason": "kpi disabled", "completed_at": time.time()}
    if not postprocess_config.kpi.artifacts_to_kpis.enabled:
        return {
            "status": "disabled",
            "reason": "kpi.artifacts_to_kpis disabled",
            "completed_at": time.time(),
        }

    try:
        # Prepare paths
        output_file = output_dir / postprocess_config.kpi.artifacts_to_kpis.output
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # Create temporary status file for subprocess communication
        status_file = output_dir / "kpi_generate_status.yaml"

        # Build CLI command
        command = build_kpi_generate_command(
            config=postprocess_config,
            tree_root=base_dir,
            manifest_path=manifest_path,
            status_file=status_file,
            output_file=output_file,
        )

        # Execute command using generic function
        result, status_data, log_file = _execute_caliper_command(
            command=command,
            step_name="caliper kpi generate",
            status_file=status_file,
            step_logs_dir=step_logs_dir,
        )

        # Clean up temporary status file
        try:
            status_file.unlink()
        except FileNotFoundError:
            pass

        # Convert to expected format
        if status_data.get("success"):
            return {
                "status": "success",
                "output_file": str(output_file),
                "completed_at": time.time(),
            }
        else:
            return {
                "status": "failed",
                "error": status_data.get("error", "Unknown error"),
                "completed_at": time.time(),
            }

    except Exception as e:
        # Log the full traceback to help with debugging
        import traceback

        full_traceback = traceback.format_exc()
        logger.error(f"KPI generation failed: {e}")
        logger.error(f"Full traceback:\n{full_traceback}")
        return {"status": "failed", "error": str(e), "completed_at": time.time()}


# _run_s3_import function removed - now using fork/exec subprocess execution directly


def _run_artifacts_to_ai_data(
    postprocess_config,
    plugin,
    model,
    output_dir: Path,
    plugin_module: str,
    base_dir: Path,
    manifest_path: Path | None,
    step_logs_dir: Path,
) -> dict[str, Any]:
    """Export AI evaluation payload using fork/exec subprocess execution."""
    try:
        # Create AI data directory and output file path
        ai_data_dir = output_dir / postprocess_config.kpi.artifacts_to_ai_data.output_dir
        ai_data_dir.mkdir(parents=True, exist_ok=True)
        output_file = ai_data_dir / "ai_data_payload.json"

        # Create temporary status file for subprocess communication
        status_file = output_dir / "ai_eval_export_status.yaml"

        # Build CLI command
        command = build_ai_eval_export_command(
            config=postprocess_config,
            tree_root=base_dir,
            manifest_path=manifest_path,
            status_file=status_file,
            output_file=output_file,
            use_cache=True,  # Default to using cache in orchestration
        )

        # Execute command using generic function
        result, status_data, log_file = _execute_caliper_command(
            command=command,
            step_name="caliper ai-eval-export",
            status_file=status_file,
            step_logs_dir=step_logs_dir,
        )

        # Clean up temporary status file
        try:
            status_file.unlink()
        except FileNotFoundError:
            pass

        # Convert to expected format
        if status_data.get("success"):
            return {
                "status": "success",
                "output_file": status_data.get("output_file", str(ai_data_dir)),
                "ai_data_dir": _make_path_relative_to_base(ai_data_dir, output_dir),
                "completed_at": time.time(),
            }
        else:
            return {
                "status": "failed",
                "error": status_data.get("error", "Unknown error"),
                "completed_at": time.time(),
            }

    except Exception as e:
        # Log the full traceback to help with debugging
        import traceback

        full_traceback = traceback.format_exc()
        logger.error(f"AI eval export failed: {e}")
        logger.error(f"Full traceback:\n{full_traceback}")
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
    base_dir: Path,
    manifest_path: Path | None,
    step_logs_dir: Path,
) -> dict[str, Any]:
    """Export KPI data to CSV format using fork/exec subprocess execution."""

    if not postprocess_config.kpi.enabled:
        return {"status": "disabled", "reason": "kpi disabled", "completed_at": time.time()}
    if not postprocess_config.kpi.kpis_to_csv.enabled:
        return {
            "status": "disabled",
            "reason": "kpi.kpis_to_csv disabled",
            "completed_at": time.time(),
        }

    try:
        # Create output file path
        output_file = output_dir / postprocess_config.kpi.kpis_to_csv.output
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # Create temporary status file for subprocess communication
        status_file = output_dir / "kpi_csv_export_status.yaml"

        # Build CLI command
        command = build_kpi_csv_export_command(
            config=postprocess_config,
            tree_root=base_dir,
            manifest_path=manifest_path,
            status_file=status_file,
            input_file=kpi_json_path,
            output_file=output_file,
        )

        # Execute command using generic function
        result, status_data, log_file = _execute_caliper_command(
            command=command,
            step_name="caliper kpi csv-export",
            status_file=status_file,
            step_logs_dir=step_logs_dir,
        )

        # Clean up temporary status file
        try:
            status_file.unlink()
        except FileNotFoundError:
            pass

        # Convert to expected format
        if status_data.get("success"):
            return {
                "status": "success",
                "kpi_count": status_data.get("kpi_count", 0),
                "output_file": str(output_file),
                "completed_at": time.time(),
            }
        else:
            return {
                "status": "failed",
                "error": status_data.get("error", "Unknown error"),
                "completed_at": time.time(),
            }

    except Exception as e:
        # Log the full traceback to help with debugging
        import traceback

        full_traceback = traceback.format_exc()
        logger.error(f"KPI CSV export failed: {e}")
        logger.error(f"Full traceback:\n{full_traceback}")
        return {"status": "failed", "error": str(e), "completed_at": time.time()}


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

        # Abort pipeline if parse failed - no point in running other steps without data
        if self.parse_failed:
            logger.error("Parse step failed - aborting remaining postprocessing steps")
            logger.info("Pipeline aborted due to parse failure")
        else:
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

    def _add_step(
        self, step_name: str, step_data: dict[str, Any], log_file: Path | None = None
    ) -> None:
        """Add a step result to the steps list."""
        if log_file:
            # Make log file path relative to artifact root where logs are actually stored
            try:
                artifact_root = Path(env.ARTIFACT_DIR)
                relative_log_path = log_file.relative_to(artifact_root)
                step_data["log_file"] = str(relative_log_path)
            except ValueError:
                # If log file is not under artifact root, use absolute path
                step_data["log_file"] = str(log_file)

        self.steps.append({step_name: step_data})

    def _check_step_result_and_set_failure(self, step_name: str, result: dict[str, Any]) -> bool:
        """Check step result status and set appropriate failure flag if needed.

        Args:
            step_name: Name of the step
            result: Step result dictionary

        Returns:
            True if step failed or warned, False if successful
        """
        status = result.get("status")
        if status in ("failed", "warning"):
            # Map step names to their failure flags
            step_failure_map = {
                "artifacts_to_kpis": "artifacts_to_kpis_failed",
                "artifacts_to_ai_data": "ai_data_failed",
                "s3_import": "s3_import_failed",
                "analyse_kpis": "analyze_failed",
                "s3_export": "s3_export_failed",
            }

            failure_attr = step_failure_map.get(step_name)
            if failure_attr:
                setattr(self, failure_attr, True)

                if status == "warning":
                    warning_msg = result.get("message", "unknown warning")
                    logger.warning(f"Step '{step_name}' completed with warning: {warning_msg}")
                elif status == "failed":
                    error_msg = result.get("error", "unknown error")
                    logger.error(f"Step '{step_name}' failed: {error_msg}")

            return True
        return False

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

    def _has_only_warnings(self) -> bool:
        """Check if all problematic steps are warnings (not actual failures)."""
        has_any_problematic_steps = False
        has_actual_failures = False

        for step_dict in self.steps:
            for _step_name, step_data in step_dict.items():
                step_status = step_data.get("status")
                if step_status in ("failed", "warning"):
                    has_any_problematic_steps = True
                    if step_status == "failed":
                        has_actual_failures = True

        return has_any_problematic_steps and not has_actual_failures

    def _build_result(self, final_status: str, test_block: dict[str, Any]) -> dict[str, Any]:
        """Build the final result dictionary."""
        # Determine success value based on final status and warning-only detection
        if final_status == FINAL_SUCCESS:
            success_value = True
        elif final_status == FINAL_KPI_PIPELINE_FAILED and self._has_only_warnings():
            success_value = "warning"  # Special case for warnings-only
        else:
            success_value = False

        return {
            "final_status": final_status,
            "success": success_value,
            "base_directory": str(Path(env.ARTIFACT_DIR)),
            "test_phase": test_block,
            "steps": self.steps,
        }

    def _run_parse_step(self) -> None:
        """Execute the parse step if enabled."""
        if not self.config.parse.enabled:
            return

        import tempfile

        # Create status file for CLI output
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as status_f:
            status_file = Path(status_f.name)

        try:
            # Build CLI command
            command = build_parse_command(
                config=self.config,
                tree_root=self.tree_root,
                manifest_path=self.manifest_path,
                status_file=status_file,
                use_cache=not self.config.parse.no_cache,
            )

            # Execute command using generic function
            result, status_data, log_file = _execute_caliper_command(
                command=command,
                step_name="caliper parse",
                status_file=status_file,
                step_logs_dir=self.step_logs_dir,
            )

            if result.returncode == 0 and status_data and status_data.get("success", False):
                self._add_step(
                    "parse",
                    {
                        "status": "success",
                        "plugin_module": status_data.get("plugin_module", "unknown"),
                        "record_count": status_data.get("parsed_records", 0),
                        "parse_cache_ref": status_data.get("cache_ref"),
                        "completed_at": time.time(),
                    },
                    log_file,
                )
            else:
                self.parse_failed = True
                error_msg = (status_data or {}).get(
                    "error", f"Command failed with exit code {result.returncode}"
                )
                logger.error("Caliper parse failed: %s", error_msg)
                self._add_step(
                    "parse",
                    {
                        "status": "failure",
                        "detail": error_msg,
                        "exit_code": result.returncode,
                        "completed_at": time.time(),
                    },
                    log_file,
                )

        except Exception as e:  # noqa: BLE001
            self.parse_failed = True
            logger.exception("Parse step execution failed")
            self._add_step(
                "parse",
                {
                    "status": "failure",
                    "detail": str(e),
                    "traceback": traceback.format_exc(),
                    "completed_at": time.time(),
                },
                None,  # No log file if we couldn't even start
            )
        finally:
            # Clean up temporary status file
            if status_file.exists():
                status_file.unlink()

    def _run_visualize_step(self) -> None:
        """Execute the visualize step if enabled."""
        if not self.config.visualize.enabled:
            return

        import tempfile

        # Create status file for CLI output
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as status_f:
            status_file = Path(status_f.name)

        try:
            # Resolve visualize output directory
            if self.visualize_output_dir is not None:
                output_dir = self.visualize_output_dir.expanduser().resolve()
                logger.info(f"Using explicit visualize output directory: {output_dir}")
            else:
                output_dir = _resolve_visualize_output_dir(
                    self.config.visualize.output_dir,
                )
                logger.info(
                    f"Resolved visualize output directory from config '{self.config.visualize.output_dir}': {output_dir}"
                )

            # Build CLI command
            command = build_visualize_command(
                config=self.config,
                tree_root=self.tree_root,
                manifest_path=self.manifest_path,
                status_file=status_file,
                output_dir=output_dir,
                use_cache=not self.config.parse.no_cache,
            )

            # Execute command using generic function
            result, status_data, log_file = _execute_caliper_command(
                command=command,
                step_name="caliper visualize",
                status_file=status_file,
                step_logs_dir=self.step_logs_dir,
            )

            if result.returncode == 0 and status_data and status_data.get("success", False):
                # Get output files and paths from status
                output_files = status_data.get("output_files", [])

                # Convert absolute paths to relative paths from output_dir if needed
                relative_paths = []
                for path in output_files:
                    try:
                        path_obj = Path(path)
                        if path_obj.is_absolute():
                            relative_path = path_obj.relative_to(output_dir)
                            relative_paths.append(str(relative_path))
                        else:
                            relative_paths.append(str(path))
                    except ValueError:
                        # If path is not under output_dir, keep as-is
                        relative_paths.append(str(path))

                # Calculate relative output_dir path from base_directory
                try:
                    relative_output_dir = str(output_dir.relative_to(env.ARTIFACT_DIR))
                except ValueError:
                    # If output_dir is not under ARTIFACT_DIR, use absolute path
                    relative_output_dir = str(output_dir)

                self._add_step(
                    "visualize",
                    {
                        "status": "success",
                        "plugin_module": status_data.get("plugin_module", "unknown"),
                        "output_files": relative_paths,
                        "output_dir": relative_output_dir,
                        "generated_files": status_data.get("generated_files", len(relative_paths)),
                        "completed_at": time.time(),
                    },
                    log_file,
                )
            else:
                self.visualize_failed = True
                error_msg = (status_data or {}).get(
                    "error", f"Command failed with exit code {result.returncode}"
                )
                logger.error("Caliper visualize failed: %s", error_msg)
                self._add_step(
                    "visualize",
                    {
                        "status": "failure",
                        "detail": error_msg,
                        "exit_code": result.returncode,
                        "completed_at": time.time(),
                    },
                    log_file,
                )

        except Exception as e:  # noqa: BLE001
            self.visualize_failed = True
            logger.exception("Visualize step execution failed")
            self._add_step(
                "visualize",
                {
                    "status": "failure",
                    "detail": str(e),
                    "traceback": traceback.format_exc(),
                    "completed_at": time.time(),
                },
                None,  # No log file if we couldn't even start
            )
        finally:
            # Clean up temporary status file
            if status_file.exists():
                status_file.unlink()

    def _run_kpi_and_ai_data_steps(self) -> None:
        """Execute KPI generation, CSV export, KPI export, and AI evaluation steps."""
        if not self.config.kpi.enabled:
            return

        try:
            # Determine output directory for KPI/AI data steps - use base artifact directory
            output_dir = env.ARTIFACT_DIR
            logger.info(f"KPI steps using base artifact directory: {output_dir}")
            output_dir.mkdir(parents=True, exist_ok=True)

            # Load plugin and model
            model, mod_str = parse_entrypoint(
                self.config,
                self.tree_root,
                self.manifest_path,
                use_cache=not self.config.parse.no_cache,
            )
            mod_str, plugin = load_plugin_entrypoint(
                self.config, tree_root=self.tree_root, manifest_path=self.manifest_path
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
            with step_logging("caliper_artifacts_to_kpis", self.step_logs_dir) as log_file:
                result = _run_artifacts_to_kpis(
                    self.config,
                    plugin,
                    model,
                    output_dir,
                    mod_str,
                    self.tree_root,
                    self.manifest_path,
                    self.step_logs_dir,
                )
                self._add_step("artifacts_to_kpis", result, log_file)
                self._check_step_result_and_set_failure("artifacts_to_kpis", result)
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

        with step_logging("caliper_kpis_to_csv", self.step_logs_dir) as log_file:
            # Path to the JSON file for reference in command logging
            kpi_json_path = output_dir / self.config.kpi.artifacts_to_kpis.output
            result = _run_kpis_to_csv(
                self.config,
                plugin,
                model,
                output_dir,
                kpi_json_path,
                self.tree_root,
                self.manifest_path,
                self.step_logs_dir,
            )
            self._add_step("kpis_to_csv", result, log_file)
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

        with step_logging("caliper_artifacts_to_ai_data", self.step_logs_dir) as log_file:
            try:
                result = _run_artifacts_to_ai_data(
                    self.config,
                    plugin,
                    model,
                    output_dir,
                    mod_str,
                    self.tree_root,
                    self.manifest_path,
                    self.step_logs_dir,
                )
                self._add_step("artifacts_to_ai_data", result, log_file)

                logger.info("AI eval export result:")
                logger.info(json.dumps(result, indent=2, default=str))

                # Check if the result indicates failure or warning
                self._check_step_result_and_set_failure("artifacts_to_ai_data", result)

            except Exception as e:
                logger.exception("AI eval export failed")
                step_result = {"status": "failed", "error": str(e)}
                self._add_step("artifacts_to_ai_data", step_result, log_file)
                self._check_step_result_and_set_failure("artifacts_to_ai_data", step_result)

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

        with step_logging("caliper_s3_import", self.step_logs_dir) as log_file:
            try:
                # Create temporary status file for subprocess communication
                status_file = output_dir / "s3_import_status.yaml"

                # Build CLI command
                command = build_s3_import_command(
                    config=self.config,
                    status_file=status_file,
                    output_dir=output_dir,
                )

                # Execute command using generic function
                result, status_data, log_file = _execute_caliper_command(
                    command=command,
                    step_name="caliper s3 import",
                    status_file=status_file,
                    step_logs_dir=self.step_logs_dir,
                )

                # Clean up temporary status file
                try:
                    status_file.unlink()
                except FileNotFoundError:
                    pass

                # Convert to expected format
                if status_data.get("success"):
                    # Get the actual import directory (where files were downloaded)
                    import_dir = output_dir / self.config.s3.import_.output_dir
                    step_result = {
                        "status": "success",
                        "output_dir": str(import_dir),
                        "file_count": status_data.get("file_count", 0),
                        "completed_at": time.time(),
                    }
                    if status_data.get("warning"):
                        step_result["warning"] = status_data["warning"]
                else:
                    step_result = {
                        "status": "failed",
                        "error": status_data.get("error", "Unknown error"),
                        "completed_at": time.time(),
                    }

                self._add_step("s3_import", step_result, log_file)

                logger.info("S3 import result:")
                logger.info(json.dumps(step_result, indent=2, default=str))

                self._check_step_result_and_set_failure("s3_import", step_result)

            except Exception as e:
                logger.exception("S3 import failed")
                step_result = {"status": "failed", "error": str(e), "completed_at": time.time()}
                self._add_step("s3_import", step_result, log_file)
                self._check_step_result_and_set_failure("s3_import", step_result)

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

        # Get KPI file path from artifacts_to_kpis step
        artifacts_to_kpis_step = self._get_step("artifacts_to_kpis")
        if not artifacts_to_kpis_step or artifacts_to_kpis_step.get("status") != "success":
            self._add_step(
                "analyse_kpis",
                {
                    "status": "failed",
                    "error": "artifacts_to_kpis step did not complete successfully",
                    "completed_at": time.time(),
                },
            )
            self.analyze_failed = True
            return

        current_kpis_file = artifacts_to_kpis_step.get("output_file")
        if not current_kpis_file:
            self._add_step(
                "analyse_kpis",
                {
                    "status": "failed",
                    "error": "No output file found in artifacts_to_kpis step",
                    "completed_at": time.time(),
                },
            )
            self.analyze_failed = True
            return

        with step_logging("caliper_analyse_kpis", self.step_logs_dir) as log_file:
            try:
                # Convert relative path to absolute for analyze function
                current_kpis_path = output_dir / current_kpis_file

                result = analyze_kpis_entrypoint(
                    self.config, plugin_module, self.tree_root, output_dir, current_kpis_path
                )

                # Make output_file path relative
                if "output_file" in result:
                    result["output_file"] = _make_path_relative_to_base(
                        result["output_file"], output_dir
                    )

                self._add_step("analyse_kpis", result, log_file)

                logger.info("KPI analysis result:")
                logger.info(json.dumps(result, indent=2, default=str))

                self._check_step_result_and_set_failure("analyse_kpis", result)

            except Exception as e:
                logger.exception("KPI analysis failed")
                step_result = {"status": "failed", "error": str(e)}
                self._add_step("analyse_kpis", step_result, log_file)
                self._check_step_result_and_set_failure("analyse_kpis", step_result)

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

        with step_logging("caliper_s3_export", self.step_logs_dir) as log_file:
            try:
                # Collect file paths from previous steps
                kpis_file = None
                csv_file = None
                ai_data_dir = None
                analysis_file = None

                # Get KPI JSON file from artifacts_to_kpis step
                artifacts_to_kpis_step = self._get_step("artifacts_to_kpis")
                if (
                    artifacts_to_kpis_step
                    and artifacts_to_kpis_step.get("status") == "success"
                    and artifacts_to_kpis_step.get("output_file")
                ):
                    kpis_file = output_dir / artifacts_to_kpis_step["output_file"]

                # Get CSV file from kpis_to_csv step
                kpis_to_csv_step = self._get_step("kpis_to_csv")
                if (
                    kpis_to_csv_step
                    and kpis_to_csv_step.get("status") == "success"
                    and kpis_to_csv_step.get("output_file")
                ):
                    csv_file = output_dir / kpis_to_csv_step["output_file"]

                # Get AI data directory from artifacts_to_ai_data step
                ai_data_step = self._get_step("artifacts_to_ai_data")
                logger.info(f"AI data step found: {ai_data_step}")
                if (
                    ai_data_step
                    and ai_data_step.get("status") == "success"
                    and ai_data_step.get("ai_data_dir")
                ):
                    ai_data_dir = output_dir / ai_data_step["ai_data_dir"]
                    logger.info(f"AI data directory set to: {ai_data_dir}")
                else:
                    logger.warning(
                        f"AI data directory not found - step status: {ai_data_step.get('status') if ai_data_step else 'step not found'}, ai_data_dir: {ai_data_step.get('ai_data_dir') if ai_data_step else 'N/A'}"
                    )

                # Get analysis file from analyse_kpis step
                analyze_step = self._get_step("analyse_kpis")
                if (
                    analyze_step
                    and analyze_step.get("status") in ("success", "warning")
                    and analyze_step.get("output_file")
                ):
                    analysis_file = output_dir / analyze_step["output_file"]

                # Create temporary status file for subprocess communication
                status_file = output_dir / "s3_export_status.yaml"

                # Build CLI command
                command = build_s3_export_command(
                    config=self.config,
                    status_file=status_file,
                    kpis_file=kpis_file,
                    csv_file=csv_file,
                    ai_data_dir=ai_data_dir,
                    analysis_file=analysis_file,
                )

                # Execute command using generic function
                result, status_data, log_file = _execute_caliper_command(
                    command=command,
                    step_name="caliper s3 export",
                    status_file=status_file,
                    step_logs_dir=self.step_logs_dir,
                )

                # Clean up temporary status file
                try:
                    status_file.unlink()
                except FileNotFoundError:
                    pass

                # Convert to expected format
                if status_data.get("success"):
                    step_result = {
                        "status": "success",
                        "exported_path": status_data.get("exported_path", ""),
                        "uploaded_files": status_data.get("uploaded_files", 0),
                        "total_files": status_data.get("total_files", 0),
                        "completed_at": time.time(),
                    }

                    # Format status for better readability
                    uploaded_files = step_result.get("uploaded_files", 0)
                    total_files = step_result.get("total_files", 0)
                    s3_path = step_result.get("exported_path", "")

                    logger.info(
                        f"S3 export completed successfully: "
                        f"{uploaded_files}/{total_files} files uploaded to {s3_path}"
                    )
                else:
                    step_result = {
                        "status": "failed",
                        "error": status_data.get("error", "Unknown error"),
                        "completed_at": time.time(),
                    }
                    self._check_step_result_and_set_failure("s3_export", step_result)

                self._add_step("s3_export", step_result, log_file)

            except Exception as e:
                error_msg = f"S3 export step failed with exception: {type(e).__name__}: {str(e)}"

                # Log programming errors more prominently
                if isinstance(e, AttributeError | NameError | TypeError):
                    logger.error(f"CRITICAL PROGRAMMING ERROR in S3 export: {error_msg}")
                    logger.exception("Full traceback for programming error:")
                else:
                    logger.error(error_msg)
                    logger.exception("Full traceback:")

                step_result = {
                    "status": "failed",
                    "error": error_msg,
                    "exception_type": type(e).__name__,
                    "completed_at": time.time(),
                }
                self._add_step("s3_export", step_result, log_file)
                self._check_step_result_and_set_failure("s3_export", step_result)

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
            status_file = output_dir / "postprocess_status.yaml"

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
