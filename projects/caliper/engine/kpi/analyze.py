"""Regression analysis vs baseline KPI set."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from projects.caliper.engine.model import RegressionFinding

logger = logging.getLogger(__name__)


def analyze_kpis(
    current_kpis_path: Path,
    baseline_kpis_path: Path,
    output_path: Path,
    plugin: Any = None,
) -> dict[str, Any]:
    """
    Analyze KPI for regressions.

    Args:
        current_kpis_path: Path to current KPIs JSON file
        baseline_kpis_path: Path to baseline KPIs JSON file
        output_path: Path where analysis results will be written
        plugin: Caliper plugin instance for KPI definitions and analysis rules

    Returns:
        Analysis result dictionary with status, findings, etc.
    """
    try:
        # Load KPI files
        with open(current_kpis_path) as f:
            current_data = json.load(f)

        with open(baseline_kpis_path) as f:
            baseline_data = json.load(f)

        # Extract schema version and validate
        current_schema = current_data.get("schema_version", "unknown")
        baseline_schema = baseline_data.get("schema_version", "unknown")

        if current_schema != "2" or baseline_schema != "2":
            return {
                "status": "failed",
                "error": f"Only schema_version 2 supported (current: {current_schema}, baseline: {baseline_schema})",
                "completed_at": time.time(),
            }

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
                        if "labels" in current_metric:
                            higher_is_better = current_metric["labels"].get(
                                "higher_is_better", False
                            )

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

        logger.info(f"Analysis completed: {regressions} regressions, {improvements} improvements")

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
        return None
    return max(kpi_files, key=lambda p: p.stat().st_mtime)


def run_analyze(
    *,
    current_path: Any,
    baseline_path: Any,
    output_path: Any,
    plugin: Any = None,
) -> list[RegressionFinding]:
    """Run KPI analysis"""
    result = analyze_kpis(
        current_kpis_path=Path(current_path),
        baseline_kpis_path=Path(baseline_path),
        output_path=Path(output_path),
        plugin=plugin,
    )

    # Convert to expected format for CLI
    if result["status"] == "success":
        return []  # Return empty list for now, actual findings are in the output file
    else:
        raise RuntimeError(f"Analysis failed: {result.get('error', 'Unknown error')}")
