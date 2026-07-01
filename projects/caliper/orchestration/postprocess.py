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
    """Export AI evaluation payload using the plugin's build_ai_eval_payload method."""
    try:
        if not hasattr(plugin, "build_ai_eval_payload"):
            return {"status": "skipped", "reason": "plugin does not support AI evaluation"}

        # Log command to reproduce this step
        output_file = output_dir / "ai_eval_payload.json"
        log_ai_eval_command(
            base_dir=base_dir,
            plugin_module=plugin_module,
            output_file=output_file,
        )

        payload = plugin.build_ai_eval_payload(model)

        # Write AI eval payload
        output_file = output_dir / "ai_eval_payload.json"
        output_file.parent.mkdir(parents=True, exist_ok=True)

        import json

        with open(output_file, "w") as f:
            json.dump(payload, f, indent=2)

        logger.info(f"Generated AI evaluation payload in {output_file}")
        return {
            "status": "success",
            "output_file": str(output_file),
            "payload_schema_version": payload.get("schema_version", "unknown"),
        }
    except Exception as e:
        logger.error(f"AI eval export failed: {e}")
        return {"status": "failed", "error": str(e)}


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
    try:
        outcome = test_outcome or TestPhaseOutcome("NOT_AVAILABLE")
        steps: dict[str, Any] = {}
        parse_failed = False
        visualize_failed = False

        try:
            postprocess_config = CaliperOrchestrationPostprocessConfig.model_validate(
                postprocess_config_raw or {}
            )
        except ValidationError as e:
            logger.error("Invalid caliper postprocess config: %s", e)
            raise

        test_block = {"phase": outcome.phase, "message": outcome.message}

        if not postprocess_config.enabled:
            logger.info("caliper.postprocess.enabled is false — skipping post-processing steps")
            final = compute_final_postprocess_status(
                test_outcome=outcome,
                parse_failed=False,
                visualize_failed=False,
                kpi_generate_failed=False,
                kpi_export_failed=False,
                analyze_failed=False,
                has_regression=False,
                has_improvement=False,
            )
            return {
                "final_status": final,
                "test_phase": test_block,
                "steps": {},
            }

        tree_root, manifest_path, cache_path = _resolve_paths(
            postprocess_config, artifacts_dir=artifacts_dir
        )

        def _run_parse() -> dict[str, Any]:
            nonlocal parse_failed
            if not postprocess_config.parse.enabled:
                return {"status": "skipped", "reason": "parse disabled"}
            try:
                mod_str, plugin = _load_plugin(
                    postprocess_config, tree_root=tree_root, manifest_path=manifest_path
                )

                # Log command to reproduce this step
                log_parse_command(
                    base_dir=tree_root,
                    plugin_module=mod_str,
                    use_cache=not postprocess_config.parse.no_cache,
                    manifest_path=manifest_path,
                )

                model = run_parse(
                    base_dir=tree_root,
                    plugin_module=mod_str,
                    plugin=plugin,
                    use_cache=not postprocess_config.parse.no_cache,
                )
                return {
                    "status": "ok",
                    "plugin_module": mod_str,
                    "record_count": len(model.unified_result_records),
                    "parse_cache_ref": model.parse_cache_ref,
                }
            except Exception as e:  # noqa: BLE001
                parse_failed = True
                logger.exception("Caliper parse failed")
                return {"status": "failure", "detail": str(e), "traceback": traceback.format_exc()}

        def _run_visualize() -> dict[str, Any]:
            nonlocal visualize_failed
            if not postprocess_config.visualize.enabled:
                return {"status": "skipped", "reason": "visualize disabled"}

        try:
            mod_str, plugin = _load_plugin(
                postprocess_config, tree_root=tree_root, manifest_path=manifest_path
            )
            viz_cfg_path = _resolve_visualize_config_path(
                postprocess_config.visualize.visualize_config,
                artifact_tree=tree_root,
            )
            if visualize_output_dir is not None:
                output_dir = visualize_output_dir.expanduser().resolve()
            else:
                output_dir = _resolve_visualize_output_dir(
                    postprocess_config.visualize.output_dir,
                )

            # Log command to reproduce this step
            log_visualize_command(
                base_dir=tree_root,
                plugin_module=mod_str,
                output_dir=output_dir,
                reports_csv=postprocess_config.visualize.reports,
                report_group=postprocess_config.visualize.report_group,
                visualize_config_path=viz_cfg_path,
                include_pairs=tuple(postprocess_config.visualize.include_labels),
                exclude_pairs=tuple(postprocess_config.visualize.exclude_labels),
                use_cache=not postprocess_config.parse.no_cache,
            )
            paths = run_visualize(
                base_dir=tree_root,
                plugin_module=mod_str,
                plugin=plugin,
                output_dir=output_dir,
                reports_csv=postprocess_config.visualize.reports,
                report_group=postprocess_config.visualize.report_group,
                visualize_config_path=viz_cfg_path,
                include_pairs=tuple(postprocess_config.visualize.include_labels),
                exclude_pairs=tuple(postprocess_config.visualize.exclude_labels),
                use_cache=not postprocess_config.parse.no_cache,
                cache_path=cache_path,
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

            result = {
                "status": "ok",
                "plugin_module": mod_str,
                "output_dir": str(output_dir),
                "paths": relative_paths,
            }

            return result
        except Exception as e:  # noqa: BLE001
            visualize_failed = True
            logger.exception("Caliper visualize failed")
            return {"status": "failure", "detail": str(e), "traceback": traceback.format_exc()}

        any_step = (
            postprocess_config.parse.enabled
            or postprocess_config.visualize.enabled
            or postprocess_config.kpi.enabled
            or postprocess_config.analyze.enabled
        )
        if not any_step:
            logger.info("caliper.postprocess: no parse/visualize/kpi/analyze steps enabled")
            final = compute_final_postprocess_status(
                test_outcome=outcome,
                parse_failed=False,
                visualize_failed=False,
                kpi_generate_failed=False,
                kpi_export_failed=False,
                analyze_failed=False,
                has_regression=False,
                has_improvement=False,
            )
            return {"final_status": final, "test_phase": test_block, "steps": {}}

        kpi_generate_failed = False
        kpi_export_failed = False

        # Determine output directory for step logs
        step_logs_dir = (
            visualize_output_dir if visualize_output_dir else artifacts_dir / "postprocess_logs"
        )

        if postprocess_config.parse.enabled:
            with step_logging("caliper_parse", step_logs_dir):
                steps["parse"] = _run_parse()

        if postprocess_config.visualize.enabled:
            with step_logging("caliper_visualize", step_logs_dir):
                steps["visualize"] = _run_visualize()

        def _run_kpi_and_ai_eval():
            """Load plugin and model specifically for KPI and AI eval operations."""
            try:
                # Determine output directory
                if postprocess_config.visualize.enabled and visualize_output_dir:
                    output_dir = Path(visualize_output_dir)
                else:
                    output_dir = Path(artifacts_dir) / "postprocess_output"
                    output_dir.mkdir(parents=True, exist_ok=True)

                # Load plugin and model
                mod_str, plugin = _load_plugin(
                    postprocess_config, tree_root=tree_root, manifest_path=manifest_path
                )
                model = run_parse(
                    base_dir=tree_root,
                    plugin_module=mod_str,
                    plugin=plugin,
                    use_cache=not postprocess_config.parse.no_cache,
                )

                # Run KPI and AI eval operations
                kpi_results = {}
                ai_eval_results = {}

                # KPI JSONL generation
                if postprocess_config.kpi.generate.enabled:
                    with step_logging("caliper_kpi_generate", step_logs_dir):
                        kpi_results["kpi_generate"] = _run_kpi_generate(
                            postprocess_config, plugin, model, output_dir, mod_str, tree_root
                        )
                else:
                    kpi_results["kpi_generate"] = {
                        "status": "skipped",
                        "reason": "kpi.generate disabled",
                    }

                # KPI CSV export (uses the same computed KPIs)
                if postprocess_config.kpi.csv_export.enabled:
                    with step_logging("caliper_kpi_csv_export", step_logs_dir):
                        # Path to the JSONL file for reference in command logging
                        kpi_jsonl_path = output_dir / postprocess_config.kpi.generate.output
                        kpi_results["kpi_csv_export"] = _run_kpi_csv_export(
                            postprocess_config, plugin, model, output_dir, kpi_jsonl_path
                        )
                else:
                    kpi_results["kpi_csv_export"] = {
                        "status": "skipped",
                        "reason": "kpi.csv_export disabled",
                    }

                # KPI export to external systems
                if postprocess_config.kpi.export.enabled:
                    with step_logging("caliper_kpi_export", step_logs_dir):
                        kpi_results["kpi_export"] = _run_kpi_export(postprocess_config, output_dir)
                else:
                    kpi_results["kpi_export"] = {
                        "status": "skipped",
                        "reason": "kpi.export disabled",
                    }

                # Always try AI eval export
                with step_logging("caliper_ai_eval_export", step_logs_dir):
                    ai_eval_results["ai_eval_export"] = _run_ai_eval_export(
                        plugin, model, output_dir, mod_str, tree_root
                    )

                return {**kpi_results, **ai_eval_results}

            except Exception as e:
                logger.error(f"Failed to run KPI/AI eval operations: {e}")
                return {
                    "kpi_generate": {"status": "failed", "error": str(e)},
                    "kpi_csv_export": {"status": "failed", "error": str(e)},
                    "kpi_export": {"status": "skipped", "reason": "failed to load plugin"},
                    "ai_eval_export": {"status": "failed", "error": str(e)},
                }

        if postprocess_config.kpi.enabled:
            kpi_ai_results = _run_kpi_and_ai_eval()
            steps.update(kpi_ai_results)

            # Track failures
            if steps.get("kpi_generate", {}).get("status") == "failed":
                kpi_generate_failed = True
            if steps.get("kpi_csv_export", {}).get("status") == "failed":
                # CSV export failure doesn't affect overall status - it's supplementary
                logger.warning("KPI CSV export failed but continuing execution")
            if steps.get("kpi_export", {}).get("status") == "failed":
                kpi_export_failed = True

        if postprocess_config.analyze.enabled:
            with step_logging("caliper_analyze", step_logs_dir):
                # Load plugin info for analyze step
                mod_str, _ = _load_plugin(
                    postprocess_config, tree_root=tree_root, manifest_path=manifest_path
                )
                steps["analyze"] = _stub_analyze(postprocess_config, mod_str, tree_root)

        final = compute_final_postprocess_status(
            test_outcome=outcome,
            parse_failed=parse_failed,
            visualize_failed=visualize_failed,
            kpi_generate_failed=kpi_generate_failed,
            kpi_export_failed=kpi_export_failed,
            analyze_failed=False,
            has_regression=False,
            has_improvement=False,
        )

        result = {
            "final_status": final,
            "success": final == FINAL_SUCCESS,
            "test_phase": test_block,
            "steps": steps,
        }

        # Generate HTML reports if we have an output directory
        if visualize_output_dir is not None:
            output_dir = visualize_output_dir.resolve()

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

        return result

    finally:
        # Clean up step logging resources
        cleanup_step_logging()
