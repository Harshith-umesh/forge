"""Generic KPI analysis against historical baselines."""

from __future__ import annotations

from typing import Any

from projects.caliper.engine.model import RegressionFinding


def run_analyze(
    *,
    current_path: Any,
    baseline_path: Any,
    output_path: Any,
    plugin: Any = None,
) -> list[RegressionFinding]:
    """Run KPI analysis - hierarchical format only."""
    from pathlib import Path

    from .analyze_hierarchical import analyze_hierarchical_kpis

    result = analyze_hierarchical_kpis(
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
