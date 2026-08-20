"""KPI analysis for hierarchical format."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _load_kpis_any_format(kpi_file_path: Path) -> dict[str, Any]:
    """Load KPIs from file, handling both v1 (JSONL) and v2 (hierarchical) formats.

    Returns:
        Dictionary in hierarchical format with schema_version and metrics
    """
    with open(kpi_file_path) as f:
        content = f.read().strip()

    try:
        # Try to parse as JSON (hierarchical format)
        data = json.loads(content)

        if isinstance(data, dict) and data.get("schema_version") == "2":
            # Already hierarchical format
            return data
        else:
            # Unknown JSON format, convert list to v1 JSONL handling
            raise ValueError("Unknown JSON format")

    except (json.JSONDecodeError, ValueError):
        # Try to parse as JSONL (v1 format)
        kpis = []
        for line in content.splitlines():
            line = line.strip()
            if line:
                try:
                    kpi = json.loads(line)
                    kpis.append(kpi)
                except json.JSONDecodeError:
                    continue  # Skip invalid lines

        # Convert v1 format to hierarchical format
        metrics = {}
        for kpi in kpis:
            kpi_id = kpi.get("kpi_id")
            if kpi_id:
                value = kpi.get("value")
                labels = kpi.get("labels", {})
                higher_is_better = kpi.get("higher_is_better", False)

                metrics[kpi_id] = {
                    "value": value,
                    "higher_is_better": higher_is_better,
                    "labels": labels,
                    "unit": kpi.get("unit"),
                    "run_id": kpi.get("run_id"),
                    "timestamp": kpi.get("timestamp"),
                }

        return {"schema_version": "2", "metrics": metrics, "converted_from": "v1_jsonl"}


def analyze_hierarchical_kpis(
    current_kpis_path: Path,
    baseline_kpis_path: Path,
    output_path: Path,
    plugin: Any = None,
) -> dict[str, Any]:
    """
    Analyze hierarchical KPI format for regressions.

    Args:
        current_kpis_path: Path to current KPIs JSON file (hierarchical format)
        baseline_kpis_path: Path to baseline KPIs JSON file (hierarchical format)
        output_path: Path where analysis results will be written
        plugin: Caliper plugin instance for KPI definitions and analysis rules

    Returns:
        Analysis result dictionary with status, findings, etc.
    """
    try:
        # Load KPI files (supports both v1 JSONL and v2 hierarchical formats)
        current_data = _load_kpis_any_format(current_kpis_path)
        baseline_data = _load_kpis_any_format(baseline_kpis_path)

        # Both files are now guaranteed to be in hierarchical format with schema_version 2
        logger.info(f"Loaded current KPIs: {current_data.get('converted_from', 'v2_hierarchical')}")
        logger.info(
            f"Loaded baseline KPIs: {baseline_data.get('converted_from', 'v2_hierarchical')}"
        )

        # Analyze metrics using plugin-aware logic
        current_metrics = current_data.get("metrics", {})
        baseline_metrics = baseline_data.get("metrics", {})

        findings = []
        regressions = 0
        improvements = 0

        # Get plugin-specific KPI definitions if available
        if plugin and hasattr(plugin, "compute_kpis"):
            try:
                # Try to get KPI metadata from plugin
                # This is a stub - plugins may expose KPI definitions differently
                logger.debug(f"Plugin available for analysis: {plugin.__class__.__name__}")
            except Exception as e:
                logger.warning(f"Could not get KPI definitions from plugin: {e}")

        # Compare common metrics
        for metric_name in current_metrics:
            if metric_name in baseline_metrics:
                current_metric = current_metrics[metric_name]
                baseline_metric = baseline_metrics[metric_name]

                current_value = current_metric.get("value")
                baseline_value = baseline_metric.get("value")

                if current_value is not None and baseline_value is not None:
                    try:
                        curr_val = float(current_value)
                        base_val = float(baseline_value)

                        # Get direction preference from metric metadata or use default
                        higher_is_better = current_metric.get("higher_is_better", False)

                        # Calculate change
                        change_percent = (
                            ((curr_val - base_val) / base_val) * 100
                            if base_val != 0
                            else float("inf")
                        )

                        # Default threshold: 5% change (can be made configurable via plugin)
                        threshold_percent = 5.0

                        # Determine if this is a regression based on direction
                        is_regression = False
                        is_improvement = False

                        if abs(change_percent) > threshold_percent:
                            if higher_is_better:
                                # For metrics where higher is better
                                if curr_val < base_val:  # Decreased
                                    is_regression = True
                                elif curr_val > base_val:  # Increased
                                    is_improvement = True
                            else:
                                # For metrics where lower is better
                                if curr_val > base_val:  # Increased
                                    is_regression = True
                                elif curr_val < base_val:  # Decreased
                                    is_improvement = True

                        if is_regression:
                            regressions += 1
                            findings.append(
                                {
                                    "metric": metric_name,
                                    "type": "regression",
                                    "current_value": curr_val,
                                    "baseline_value": base_val,
                                    "change_percent": change_percent,
                                    "higher_is_better": higher_is_better,
                                    "threshold": threshold_percent,
                                }
                            )
                        elif is_improvement:
                            improvements += 1
                            findings.append(
                                {
                                    "metric": metric_name,
                                    "type": "improvement",
                                    "current_value": curr_val,
                                    "baseline_value": base_val,
                                    "change_percent": change_percent,
                                    "higher_is_better": higher_is_better,
                                    "threshold": threshold_percent,
                                }
                            )

                    except (TypeError, ValueError):
                        logger.warning(
                            f"Could not compare metric {metric_name}: non-numeric values"
                        )

        # Create analysis results
        analysis_result = {
            "analysis_timestamp": time.time(),
            "current_file": str(current_kpis_path),
            "baseline_file": str(baseline_kpis_path),
            "schema_version": "2",
            "metrics_compared": len([m for m in current_metrics if m in baseline_metrics]),
            "findings_count": len(findings),
            "regressions_count": regressions,
            "improvements_count": improvements,
            "findings": findings,
            "summary": f"Found {regressions} regressions and {improvements} improvements across {len([m for m in current_metrics if m in baseline_metrics])} metrics",
        }

        # Write results
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(analysis_result, f, indent=2)
            f.write("\n")

        metrics_tested = len([m for m in current_metrics if m in baseline_metrics])
        logger.info(
            f"Analysis completed: {metrics_tested} verifications, {regressions} regressions, {improvements} improvements"
        )

        return {
            "status": "success",
            "findings_count": len(findings),
            "regressions_count": regressions,
            "improvements_count": improvements,
            "output_file": str(output_path),
            "metrics_compared": len([m for m in current_metrics if m in baseline_metrics]),
            "completed_at": time.time(),
        }

    except FileNotFoundError as e:
        return {
            "status": "failed",
            "error": f"File not found: {e}",
            "completed_at": time.time(),
        }
    except json.JSONDecodeError as e:
        return {
            "status": "failed",
            "error": f"Invalid JSON format: {e}",
            "completed_at": time.time(),
        }
    except Exception as e:
        logger.exception("Analysis failed")
        return {
            "status": "failed",
            "error": f"Analysis failed: {e}",
            "completed_at": time.time(),
        }


def find_most_recent_baseline(historical_dir: Path) -> Path | None:
    """Find the most recently modified kpis.json file in historical directory."""
    kpi_files = list(historical_dir.rglob("kpis.json"))
    if not kpi_files:
        logger.warning(f"No kpis.json files found in: {historical_dir}")
        return None

    logger.info(f"Discovered {len(kpi_files)} baseline candidate files:")

    # Check format of each file and log results
    v1_files = []
    v2_files = []
    failed_files = []

    for kpi_file in kpi_files:
        try:
            with open(kpi_file) as f:
                content = f.read().strip()

            try:
                data = json.loads(content)
                if isinstance(data, dict) and data.get("schema_version") == "2":
                    v2_files.append(kpi_file)
                    logger.info(f"  {kpi_file.relative_to(historical_dir)} → v2_hierarchical")
                else:
                    # Try parsing as JSONL (v1)
                    lines = [line.strip() for line in content.splitlines() if line.strip()]
                    if lines and all(json.loads(line) for line in lines):
                        v1_files.append(kpi_file)
                        logger.info(f"  {kpi_file.relative_to(historical_dir)} → v1_jsonl")
                    else:
                        failed_files.append(kpi_file)
                        logger.warning(
                            f"  {kpi_file.relative_to(historical_dir)} → FAILED (unknown format)"
                        )
            except (json.JSONDecodeError, ValueError):
                # Try as JSONL
                try:
                    lines = [line.strip() for line in content.splitlines() if line.strip()]
                    if lines and all(json.loads(line) for line in lines):
                        v1_files.append(kpi_file)
                        logger.info(f"  {kpi_file.relative_to(historical_dir)} → v1_jsonl")
                    else:
                        failed_files.append(kpi_file)
                        logger.warning(
                            f"  {kpi_file.relative_to(historical_dir)} → FAILED (invalid JSONL)"
                        )
                except Exception:
                    failed_files.append(kpi_file)
                    logger.warning(
                        f"  {kpi_file.relative_to(historical_dir)} → FAILED (parse error)"
                    )
        except Exception as e:
            failed_files.append(kpi_file)
            logger.error(f"  {kpi_file.relative_to(historical_dir)} → FAILED ({e})")

    logger.info(
        f"Summary: {len(v2_files)} v2_hierarchical, {len(v1_files)} v1_jsonl, {len(failed_files)} failed"
    )

    most_recent = max(kpi_files, key=lambda p: p.stat().st_mtime)
    logger.info(f"Selected most recent: {most_recent.relative_to(historical_dir)}")

    return most_recent
