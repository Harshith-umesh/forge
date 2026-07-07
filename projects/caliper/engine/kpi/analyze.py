"""Regression analysis vs baseline KPI set."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def analyze_kpis_against_baselines(
    current_kpis_path: Path,
    baseline_kpis: dict[Path, dict[str, Any]],
    output_path: Path,
    plugin: Any = None,
) -> dict[str, Any]:
    """
    Analyze KPIs against ALL baseline files to build comprehensive baseline.

    Args:
        current_kpis_path: Path to current KPIs JSON file
        baseline_kpis: Dictionary mapping baseline file paths to their loaded KPI data
        output_path: Path where analysis results will be written
        plugin: Caliper plugin instance for KPI definitions and analysis rules

    Returns:
        Analysis result dictionary with status, findings, etc.
    """
    # STUB: For now, just log what we received and return a placeholder result
    logger.info(
        f"STUB: analyze_kpis_against_baselines called with {len(baseline_kpis)} baseline files"
    )
    logger.info(f"Current KPIs: {current_kpis_path}")
    logger.info(f"Output: {output_path}")
    logger.info(f"Baseline files: {list(baseline_kpis.keys())}")

    return {
        "status": "skipped",
        "reason": "analyze_kpis_against_baselines is not implemented yet - STUB",
        "completed_at": time.time(),
    }


def find_baseline_kpis(historical_dir: Path) -> dict[Path, dict[str, Any]]:
    """Load all kpis.json files from historical directory and return a mapping of path to loaded JSON object.

    Args:
        historical_dir: Directory to search for historical kpis.json files

    Returns:
        Dictionary mapping file paths to loaded KPI JSON objects
    """
    baseline_kpis = {}
    kpi_files = list(historical_dir.rglob("kpis.json"))

    if not kpi_files:
        logger.warning(f"No kpis.json files found in historical directory: {historical_dir}")
        return baseline_kpis

    logger.info(f"Found {len(kpi_files)} historical KPI files to load")

    for kpi_file in kpi_files:
        try:
            with open(kpi_file) as f:
                kpi_data = json.load(f)

            # Validate that it's a hierarchical format (schema_version 2)
            schema_version = kpi_data.get("schema_version", "unknown")
            if schema_version != "2":
                logger.warning(
                    f"Skipping {kpi_file}: unsupported schema version {schema_version} (only version 2 supported)"
                )
                continue

            baseline_kpis[kpi_file] = kpi_data
            logger.debug(f"Loaded KPI file: {kpi_file}")

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON in {kpi_file}: {e}")
            continue
        except FileNotFoundError as e:
            logger.error(f"KPI file not found: {kpi_file}: {e}")
            continue
        except Exception as e:
            logger.error(f"Failed to load KPI file {kpi_file}: {e}")
            continue

    logger.info(f"Successfully loaded {len(baseline_kpis)} historical KPI files")
    return baseline_kpis


def run_analyze(
    *,
    current_path: Any,
    baseline_kpis: dict[Path, dict[str, Any]],
    output_path: Any,
    plugin: Any = None,
) -> dict[str, Any]:
    """Run KPI analysis against ALL baseline files"""
    return analyze_kpis_against_baselines(
        current_kpis_path=Path(current_path),
        baseline_kpis=baseline_kpis,
        output_path=Path(output_path),
        plugin=plugin,
    )


# EOF
