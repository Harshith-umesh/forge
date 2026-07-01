"""
Config-driven Caliper parse / visualize / KPI / AI eval for FORGE orchestration.

KPI generation and AI evaluation export are now implemented. Regression analyze is still
a stub. All steps maintain a stable ``steps`` shape for caller compatibility.

Computes ``final_status`` from the FORGE test phase outcome plus all enabled step results.
"""

from __future__ import annotations

import logging
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
    log_ai_eval_command,
    log_analyze_command,
    log_kpi_csv_export_command,
    log_kpi_export_command,
    log_kpi_generate_command,
    log_parse_command,
    log_visualize_command,
    step_logging,
)
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


def _run_kpi_generate(
    postprocess_config: CaliperOrchestrationPostprocessConfig,
    plugin,
    model,
    output_dir: Path,
    plugin_module: str,
    base_dir: Path,
) -> dict[str, Any]:
    """Generate KPI JSONL using the plugin's compute_kpis method."""
    if not postprocess_config.kpi.enabled:
        return {"status": "skipped", "reason": "kpi disabled"}
    if not postprocess_config.kpi.generate.enabled:
        return {"status": "skipped", "reason": "kpi.generate disabled"}

    try:
        # Write KPI JSONL
        output_file = output_dir / postprocess_config.kpi.generate.output

        # Log command to reproduce this step
        log_kpi_generate_command(
            base_dir=base_dir,
            plugin_module=plugin_module,
            output_file=output_file,
        )

        kpis = plugin.compute_kpis(model)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        import json

        with open(output_file, "w") as f:
            for kpi in kpis:
                f.write(json.dumps(kpi) + "\n")

        logger.info(f"Generated {len(kpis)} KPI records in {output_file}")
        return {"status": "success", "kpi_count": len(kpis), "output_file": str(output_file)}
    except Exception as e:
        logger.error(f"KPI generation failed: {e}")
        return {"status": "failed", "error": str(e)}


def _run_kpi_export(
    postprocess_config: CaliperOrchestrationPostprocessConfig,
    output_dir: Path,
) -> dict[str, Any]:
    """Export KPIs to external system (placeholder for OpenSearch integration)."""
    if not postprocess_config.kpi.enabled:
        return {"status": "skipped", "reason": "kpi disabled"}
    if not postprocess_config.kpi.export.enabled:
        return {"status": "skipped", "reason": "kpi.export disabled"}

    # Log command to reproduce this step
    input_path = output_dir / postprocess_config.kpi.generate.output
    log_kpi_export_command(
        input_path=input_path,
        target_system="opensearch",
    )

    # TODO: Implement actual KPI export to OpenSearch when credentials/config available
    logger.info("KPI export is configured but not yet implemented")
    return {"status": "skipped", "reason": "KPI export not yet implemented"}


def _run_ai_eval_export(
    plugin,
    model,
    output_dir: Path,
    plugin_module: str,
    base_dir: Path,
) -> dict[str, Any]:
    """Export AI evaluation payload with structured directories and copied artifacts."""
    try:
        if not hasattr(plugin, "build_ai_eval_payload"):
            return {"status": "skipped", "reason": "plugin does not support AI evaluation"}

        # Create AI evaluation directory structure
        ai_eval_dir = output_dir / "ai_eval"
        ai_eval_dir.mkdir(parents=True, exist_ok=True)

        # Log command to reproduce this step
        output_file = ai_eval_dir / "ai_eval_payload.json"
        log_ai_eval_command(
            base_dir=base_dir,
            plugin_module=plugin_module,
            output_file=output_file,
        )

        # Build payload from plugin
        payload = plugin.build_ai_eval_payload(model)

        # Export structured test entries with artifact copying
        exported_entries = _export_test_entries_with_artifacts(model, ai_eval_dir, base_dir, plugin)

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
            "ai_eval_dir": str(ai_eval_dir),
            "exported_entries": len(exported_entries),
            "payload_schema_version": payload.get("schema_version", "unknown"),
        }
    except Exception as e:
        logger.error(f"AI eval export failed: {e}")
        return {"status": "failed", "error": str(e)}


def _export_test_entries_with_artifacts(model, ai_eval_dir: Path, base_dir: Path, plugin) -> list[dict]:
    """
    Export test entries by creating directories and copying specific artifacts.

    Args:
        model: Unified model containing test results
        ai_eval_dir: Directory where test entries should be exported
        base_dir: Base directory of the test artifacts (test directory)
        plugin: Plugin instance to get artifact file list

    Returns:
        List of exported test entry information
    """
    import shutil

    exported_entries = []

    # Get the specific files we want to copy from the plugin
    target_files = plugin.get_ai_eval_artifact_files(model)

    for idx, record in enumerate(model.unified_result_records):
        # Create directory for this test entry
        test_entry_dir = ai_eval_dir / f"test_entry_{idx:03d}"
        test_entry_dir.mkdir(parents=True, exist_ok=True)

        # Record test entry metadata
        entry_info = {
            "entry_id": f"test_entry_{idx:03d}",
            "test_base_path": str(record.test_base_path),
            "distinguishing_labels": record.distinguishing_labels,
            "copied_files": [],
            "missing_files": [],
        }

        # Copy target files if they exist
        for target_file in target_files:
            source_file = base_dir / target_file
            if source_file.exists():
                # Create target directory structure
                target_path = test_entry_dir / Path(target_file).name
                target_path.parent.mkdir(parents=True, exist_ok=True)

                try:
                    shutil.copy2(source_file, target_path)
                    entry_info["copied_files"].append(
                        {
                            "source": str(source_file),
                            "target": str(target_path),
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


def _run_kpi_csv_export(
    postprocess_config: CaliperOrchestrationPostprocessConfig,
    plugin,
    model,
    output_dir: Path,
    kpi_jsonl_path: Path,
) -> dict[str, Any]:
    """Export KPI data to CSV format using the plugin's compute_kpis method."""
    if not postprocess_config.kpi.enabled:
        return {"status": "skipped", "reason": "kpi disabled"}
    if not postprocess_config.kpi.csv_export.enabled:
        return {"status": "skipped", "reason": "kpi.csv_export disabled"}

    try:
        # Compute KPIs from the model
        kpi_records = plugin.compute_kpis(model)

        # Create output file path
        output_file = output_dir / postprocess_config.kpi.csv_export.output
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # Log command to reproduce this step
        log_kpi_csv_export_command(
            input_path=kpi_jsonl_path,
            output_path=output_file,
        )

        # Import and use the CSV exporter
        from projects.guidellm.postprocess.guidellm.csv_export import quick_export_kpis_to_csv

        result_path = quick_export_kpis_to_csv(
            records=kpi_records,
            output_path=output_file,
            include_header_comments=postprocess_config.kpi.csv_export.include_header_comments,
        )

        logger.info(f"Exported {len(kpi_records)} KPI records to CSV: {result_path}")
        return {
            "status": "success",
            "kpi_count": len(kpi_records),
            "output_file": result_path,
        }
    except Exception as e:
        logger.error(f"KPI CSV export failed: {e}")
        return {"status": "failed", "error": str(e)}


def _stub_analyze(
    postprocess_config: CaliperOrchestrationPostprocessConfig,
    plugin_module: str,
    base_dir: Path,
) -> dict[str, Any]:
    if not postprocess_config.analyze.enabled:
        return {"status": "skipped", "reason": "analyze disabled"}

    # Log command to reproduce this step
    log_analyze_command(
        base_dir=base_dir,
        plugin_module=plugin_module,
    )

    return {"status": "skipped", "reason": _STUB_REASON_ANALYZE}


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
        self.steps: dict[str, Any] = {}
        self.parse_failed = False
        self.visualize_failed = False
        self.kpi_generate_failed = False
        self.kpi_export_failed = False
        self.analyze_failed = False

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
                    kpi_generate_failed=False,
                    kpi_export_failed=False,
                    analyze_failed=False,
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
                    kpi_generate_failed=False,
                    kpi_export_failed=False,
                    analyze_failed=False,
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
        self._run_kpi_and_ai_eval_steps()
        logger.info(
            f"After KPI/AI steps: kpi_generate_failed={self.kpi_generate_failed}, kpi_export_failed={self.kpi_export_failed}"
        )
        self._run_analyze_step()
        logger.info(f"After analyze step: analyze_failed={self.analyze_failed}")
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

                self.steps["parse"] = {
                    "status": "ok",
                    "plugin_module": mod_str,
                    "record_count": len(model.unified_result_records),
                    "parse_cache_ref": model.parse_cache_ref,
                }
            except Exception as e:  # noqa: BLE001
                self.parse_failed = True
                logger.exception("Caliper parse failed")
                self.steps["parse"] = {
                    "status": "failure",
                    "detail": str(e),
                    "traceback": traceback.format_exc(),
                }

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

                self.steps["visualize"] = {
                    "status": "ok",
                    "plugin_module": mod_str,
                    "output_dir": str(output_dir),
                    "paths": relative_paths,
                }

            except Exception as e:  # noqa: BLE001
                self.visualize_failed = True
                logger.exception("Caliper visualize failed")
                self.steps["visualize"] = {
                    "status": "failure",
                    "detail": str(e),
                    "traceback": traceback.format_exc(),
                }

    def _run_kpi_and_ai_eval_steps(self) -> None:
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

            # KPI JSONL generation
            self._run_kpi_generate_step(plugin, model, output_dir, mod_str)

            # KPI CSV export
            self._run_kpi_csv_export_step(plugin, model, output_dir)

            # KPI export to external systems
            self._run_kpi_export_step(output_dir)

            # AI evaluation export
            self._run_ai_eval_export_step(plugin, model, output_dir, mod_str)

        except Exception as e:
            logger.error(f"Failed to run KPI/AI eval operations: {e}")
            self.steps.update(
                {
                    "kpi_generate": {"status": "failed", "error": str(e)},
                    "kpi_csv_export": {"status": "failed", "error": str(e)},
                    "kpi_export": {"status": "skipped", "reason": "failed to load plugin"},
                    "ai_eval_export": {"status": "failed", "error": str(e)},
                }
            )
            self.kpi_generate_failed = True
            self.kpi_export_failed = True

    def _run_kpi_generate_step(
        self, plugin: Any, model: Any, output_dir: Path, mod_str: str
    ) -> None:
        """Execute the KPI generation step."""
        if self.config.kpi.generate.enabled:
            with step_logging("caliper_kpi_generate", self.step_logs_dir):
                result = _run_kpi_generate(
                    self.config, plugin, model, output_dir, mod_str, self.tree_root
                )
                self.steps["kpi_generate"] = result
                if result.get("status") == "failed":
                    self.kpi_generate_failed = True
        else:
            self.steps["kpi_generate"] = {
                "status": "skipped",
                "reason": "kpi.generate disabled",
            }

    def _run_kpi_csv_export_step(self, plugin: Any, model: Any, output_dir: Path) -> None:
        """Execute the KPI CSV export step."""
        if self.config.kpi.csv_export.enabled:
            with step_logging("caliper_kpi_csv_export", self.step_logs_dir):
                # Path to the JSONL file for reference in command logging
                kpi_jsonl_path = output_dir / self.config.kpi.generate.output
                result = _run_kpi_csv_export(self.config, plugin, model, output_dir, kpi_jsonl_path)
                self.steps["kpi_csv_export"] = result
                if result.get("status") == "failed":
                    # CSV export failure doesn't affect overall status - it's supplementary
                    logger.warning("KPI CSV export failed but continuing execution")
        else:
            self.steps["kpi_csv_export"] = {
                "status": "skipped",
                "reason": "kpi.csv_export disabled",
            }

    def _run_kpi_export_step(self, output_dir: Path) -> None:
        """Execute the KPI export step."""
        if self.config.kpi.export.enabled:
            with step_logging("caliper_kpi_export", self.step_logs_dir):
                result = _run_kpi_export(self.config, output_dir)
                self.steps["kpi_export"] = result
                if result.get("status") == "failed":
                    self.kpi_export_failed = True
        else:
            self.steps["kpi_export"] = {
                "status": "skipped",
                "reason": "kpi.export disabled",
            }

    def _run_ai_eval_export_step(
        self, plugin: Any, model: Any, output_dir: Path, mod_str: str
    ) -> None:
        """Execute the AI evaluation export step."""
        with step_logging("caliper_ai_eval_export", self.step_logs_dir):
            try:
                result = _run_ai_eval_export(plugin, model, output_dir, mod_str, self.tree_root)
                self.steps["ai_eval_export"] = result
                logger.info(f"AI eval export result: {result}")
            except Exception as e:
                logger.exception("AI eval export failed")
                self.steps["ai_eval_export"] = {"status": "failed", "error": str(e)}
                # Note: AI eval failures don't affect overall postprocessing status

    def _run_analyze_step(self) -> None:
        """Execute the analyze step if enabled."""
        if not self.config.analyze.enabled:
            return

        with step_logging("caliper_analyze", self.step_logs_dir):
            # Load plugin info for analyze step
            mod_str, _ = _load_plugin(
                self.config, tree_root=self.tree_root, manifest_path=self.manifest_path
            )
            self.steps["analyze"] = _stub_analyze(self.config, mod_str, self.tree_root)

    def _compute_final_status(self) -> str:
        """Compute the final postprocessing status."""
        # Debug logging to identify what's causing failures
        logger.info("Computing final status with failure flags:")
        logger.info(f"  test_outcome.phase: {self.test_outcome.phase}")
        logger.info(f"  parse_failed: {self.parse_failed}")
        logger.info(f"  visualize_failed: {self.visualize_failed}")
        logger.info(f"  kpi_generate_failed: {self.kpi_generate_failed}")
        logger.info(f"  kpi_export_failed: {self.kpi_export_failed}")
        logger.info(f"  analyze_failed: {self.analyze_failed}")

        final_status = compute_final_postprocess_status(
            test_outcome=self.test_outcome,
            parse_failed=self.parse_failed,
            visualize_failed=self.visualize_failed,
            kpi_generate_failed=self.kpi_generate_failed,
            kpi_export_failed=self.kpi_export_failed,
            analyze_failed=self.analyze_failed,
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
