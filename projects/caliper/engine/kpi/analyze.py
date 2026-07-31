"""Generic KPI regression analysis against historical baselines."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class AnalysisConfig:
    """Configuration for KPI regression analysis.

    comparison_keys: Label keys that define what we compare against.
        Records must differ on at least one comparison key to be distinct baselines.
        E.g. ["version"] means we test the current version against other versions.
    ignored_keys: Label keys excluded when matching current to baseline records.
        E.g. ["os"] means we match across operating systems.
    sorting_keys: Label keys used to order entries in the output report.
    max_relative_regression: Fraction threshold for flagging regression (0.1 = 10%).
    min_baseline_points: Minimum number of baseline data points required to run a test.
    """

    comparison_keys: list[str] = field(default_factory=list)
    ignored_keys: list[str] = field(default_factory=list)
    sorting_keys: list[str] = field(default_factory=list)
    max_relative_regression: float = 0.1
    min_baseline_points: int = 1


@dataclass
class KpiTestResult:
    """Result of a single KPI regression test."""

    kpi_id: str
    labels: dict[str, Any]
    current_value: float
    baseline_mean: float
    relative_change: float
    higher_is_better: bool
    regression: bool
    baseline_count: int


@dataclass
class AnalysisReport:
    """Full analysis report."""

    status: str  # "success", "no_regression", "regression_detected", "error"
    processed: dict[str, Any]
    tested: list[dict[str, Any]]
    results: list[dict[str, Any]]
    overall: dict[str, Any]


def _load_analysis_config(plugin_module: str) -> AnalysisConfig:
    """Load analysis config from plugin module if available, else use defaults.

    Plugins can expose an `analysis_config` dict or `get_analysis_config()` callable.
    """
    try:
        mod = __import__(plugin_module, fromlist=[""])
        if hasattr(mod, "get_analysis_config"):
            raw = mod.get_analysis_config()
        elif hasattr(mod, "analysis_config"):
            raw = mod.analysis_config
        else:
            return AnalysisConfig()

        if isinstance(raw, AnalysisConfig):
            return raw
        if isinstance(raw, dict):
            return AnalysisConfig(
                **{k: v for k, v in raw.items() if k in AnalysisConfig.__dataclass_fields__}
            )
    except (ImportError, Exception) as exc:
        logger.debug("Could not load analysis config from plugin %s: %s", plugin_module, exc)

    return AnalysisConfig()


def _match_key(
    labels: dict[str, Any], ignored_keys: list[str], comparison_keys: list[str]
) -> tuple:
    """Build a hashable match key from labels, excluding ignored and comparison keys."""
    excluded = set(ignored_keys) | set(comparison_keys)
    return tuple(sorted((k, str(v)) for k, v in labels.items() if k not in excluded))


def _extract_kpi_records_from_hierarchical(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract flat KPI records from a schema_version=2 hierarchical document."""
    records = []
    for test in data.get("tests", []):
        test_labels = test.get("labels", {})
        for kpi in test.get("kpis", []):
            records.append(
                {
                    "kpi_id": kpi.get("id"),
                    "value": kpi.get("value"),
                    "unit": kpi.get("unit"),
                    "higher_is_better": kpi.get("higher_is_better", True),
                    "labels": test_labels,
                    "run_id": test.get("run_id"),
                    "metadata": test.get("metadata", {}),
                }
            )
    return records


def _build_baseline_index(
    baseline_kpi_data: dict[Path, dict[str, Any]],
    config: AnalysisConfig,
) -> dict[tuple, list[dict[str, Any]]]:
    """Index baseline records by (kpi_id, match_key) for fast lookup.

    Returns mapping from (kpi_id, match_key) -> list of baseline records.
    """
    index: dict[tuple, list[dict[str, Any]]] = {}
    for _path, data in baseline_kpi_data.items():
        records = _extract_kpi_records_from_hierarchical(data)
        for rec in records:
            kpi_id = rec.get("kpi_id")
            labels = rec.get("labels", {})
            mk = _match_key(labels, config.ignored_keys, config.comparison_keys)
            key = (kpi_id, mk)
            index.setdefault(key, []).append(rec)
    return index


def _run_regression_test(
    current: dict[str, Any],
    baselines: list[dict[str, Any]],
    config: AnalysisConfig,
) -> KpiTestResult:
    """Run a regression test for a single KPI record against its baselines."""
    current_value = float(current["value"])
    higher_is_better = current.get("higher_is_better", True)
    baseline_values = [float(b["value"]) for b in baselines if b.get("value") is not None]
    baseline_mean = sum(baseline_values) / len(baseline_values)

    if baseline_mean == 0:
        relative_change = 0.0
    else:
        relative_change = (current_value - baseline_mean) / abs(baseline_mean)

    # Determine regression: if higher is better, a negative change is regression.
    # If lower is better, a positive change is regression.
    if higher_is_better:
        regression = relative_change < -config.max_relative_regression
    else:
        regression = relative_change > config.max_relative_regression

    return KpiTestResult(
        kpi_id=current["kpi_id"],
        labels=current.get("labels", {}),
        current_value=current_value,
        baseline_mean=round(baseline_mean, 6),
        relative_change=round(relative_change, 6),
        higher_is_better=higher_is_better,
        regression=regression,
        baseline_count=len(baseline_values),
    )


def _sort_results(results: list[KpiTestResult], sorting_keys: list[str]) -> list[KpiTestResult]:
    """Sort results by sorting keys extracted from labels, then by kpi_id."""

    def sort_key(r: KpiTestResult):
        label_key = tuple(str(r.labels.get(k, "")) for k in sorting_keys)
        return (*label_key, r.kpi_id)

    return sorted(results, key=sort_key)


def _build_yaml_report(
    results: list[KpiTestResult],
    config: AnalysisConfig,
    current_source: str,
    baseline_sources: list[str],
    skipped: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the final YAML-serializable report structure."""
    regressions = [r for r in results if r.regression]
    improvements = [r for r in results if not r.regression and r.relative_change != 0]

    if regressions:
        overall_status = "REGRESSION_DETECTED"
    else:
        overall_status = "PASS"

    report = {
        "analysis": {
            "status": overall_status,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "config": {
                "comparison_keys": config.comparison_keys,
                "ignored_keys": config.ignored_keys,
                "sorting_keys": config.sorting_keys,
                "max_relative_regression": config.max_relative_regression,
                "min_baseline_points": config.min_baseline_points,
            },
        },
        "processed": {
            "current_source": current_source,
            "baseline_sources": baseline_sources,
            "baseline_source_count": len(baseline_sources),
        },
        "tested": {
            "total_kpis": len(results),
            "regressions": len(regressions),
            "passes": len(results) - len(regressions),
            "skipped": len(skipped),
        },
        "results": [
            {
                "kpi_id": r.kpi_id,
                "labels": r.labels,
                "current_value": r.current_value,
                "baseline_mean": r.baseline_mean,
                "relative_change_pct": round(r.relative_change * 100, 2),
                "higher_is_better": r.higher_is_better,
                "verdict": "REGRESSION" if r.regression else "PASS",
                "baseline_count": r.baseline_count,
            }
            for r in results
        ],
        "overall": {
            "verdict": overall_status,
            "regression_count": len(regressions),
            "total_tested": len(results),
            "total_skipped": len(skipped),
        },
    }

    if skipped:
        report["skipped"] = skipped

    return report


def run_kpi_analysis(
    current_kpi_file: Path,
    historical_data_dir: Path,
    output_file: Path,
    plugin_module: str,
) -> int:
    """Run KPI regression analysis and generate a YAML report.

    Args:
        current_kpi_file: Path to current KPI JSON file (hierarchical schema v2)
        historical_data_dir: Directory containing historical KPI files (kpis.json)
        output_file: Path where YAML analysis report will be written
        plugin_module: Plugin module name (for loading analysis config)

    Returns:
        Exit code: 0=pass, 1=error, 2=warning (no baseline), 3=regression detected
    """
    try:
        logger.info("Running KPI regression analysis")
        logger.info("  current_kpi_file: %s", current_kpi_file)
        logger.info("  historical_data_dir: %s", historical_data_dir)
        logger.info("  output_file: %s", output_file)
        logger.info("  plugin_module: %s", plugin_module)

        if not current_kpi_file.exists():
            logger.error("Current KPI file not found: %s", current_kpi_file)
            return 1

        if not historical_data_dir.exists():
            logger.error("Historical data directory not found: %s", historical_data_dir)
            return 1

        config = _load_analysis_config(plugin_module)
        logger.info(
            "  config: comparison_keys=%s, ignored_keys=%s",
            config.comparison_keys,
            config.ignored_keys,
        )

        # Load current KPIs
        with open(current_kpi_file) as f:
            current_data = json.load(f)

        if current_data.get("schema_version") != "2":
            logger.error("Current KPI file must be schema_version 2 (hierarchical)")
            return 1

        current_records = _extract_kpi_records_from_hierarchical(current_data)
        if not current_records:
            logger.warning("No KPI records found in current file")
            return 1

        # Load baseline KPIs
        baseline_kpi_data = find_baseline_kpis(historical_data_dir)
        if not baseline_kpi_data:
            _write_warning_report(output_file, current_kpi_file, plugin_module)
            return 2

        # Build baseline index
        baseline_index = _build_baseline_index(baseline_kpi_data, config)

        # Run regression tests
        results: list[KpiTestResult] = []
        skipped: list[dict[str, Any]] = []

        for rec in current_records:
            kpi_id = rec.get("kpi_id")
            value = rec.get("value")

            # Skip non-scalar values (2D KPIs, lists, etc.)
            if not isinstance(value, (int, float)):
                skipped.append({"kpi_id": kpi_id, "reason": "non-scalar value"})
                continue

            labels = rec.get("labels", {})
            mk = _match_key(labels, config.ignored_keys, config.comparison_keys)
            key = (kpi_id, mk)

            baselines = baseline_index.get(key, [])
            # Filter to only scalar baselines
            scalar_baselines = [b for b in baselines if isinstance(b.get("value"), (int, float))]

            if len(scalar_baselines) < config.min_baseline_points:
                skipped.append(
                    {
                        "kpi_id": kpi_id,
                        "labels": labels,
                        "reason": f"insufficient baselines ({len(scalar_baselines)} < {config.min_baseline_points})",
                    }
                )
                continue

            result = _run_regression_test(rec, scalar_baselines, config)
            results.append(result)

        # Sort results
        results = _sort_results(results, config.sorting_keys)

        # Build report
        baseline_sources = [str(p) for p in baseline_kpi_data.keys()]
        report = _build_yaml_report(
            results=results,
            config=config,
            current_source=str(current_kpi_file),
            baseline_sources=baseline_sources,
            skipped=skipped,
        )

        # Write YAML report
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w") as f:
            yaml.dump(report, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

        regressions = report["overall"]["regression_count"]
        total = report["overall"]["total_tested"]
        logger.info(
            "Analysis complete: %d/%d KPIs tested, %d regressions",
            total,
            len(current_records),
            regressions,
        )

        return 3 if regressions > 0 else 0

    except Exception:
        logger.exception("KPI analysis failed")
        return 1


def _write_warning_report(output_file: Path, current_kpi_file: Path, plugin_module: str) -> None:
    """Write a warning-level report when no baselines are available."""
    report = {
        "analysis": {
            "status": "NO_BASELINE",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "processed": {
            "current_source": str(current_kpi_file),
            "baseline_sources": [],
            "baseline_source_count": 0,
        },
        "tested": {"total_kpis": 0, "regressions": 0, "passes": 0, "skipped": 0},
        "results": [],
        "overall": {
            "verdict": "NO_BASELINE",
            "message": "No historical KPI files found for regression testing",
            "regression_count": 0,
            "total_tested": 0,
            "total_skipped": 0,
        },
    }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as f:
        yaml.dump(report, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def analyze_kpis(
    postprocess_config,  # CaliperOrchestrationPostprocessConfig
    plugin_module: str,
    base_dir: Path,
    output_dir: Path,
    current_kpis_file: Path,
) -> dict[str, Any]:
    """Run KPI analysis step and return result status.

    This is the orchestration interface for KPI analysis.
    """
    if not postprocess_config.analyze.enabled:
        return {"status": "disabled", "reason": "analyze disabled"}

    analyze_config = postprocess_config.analyze

    historical_kpis_dir = Path(analyze_config.historical_kpis)
    if not historical_kpis_dir.is_absolute():
        historical_kpis_dir = output_dir / historical_kpis_dir

    output_path = output_dir / analyze_config.output

    try:
        if not current_kpis_file.exists():
            return {
                "status": "failed",
                "error": f"Current KPI file not found: {current_kpis_file}",
                "completed_at": time.time(),
            }

        if not historical_kpis_dir.exists():
            return {
                "status": "failed",
                "error": f"Historical KPIs directory not found: {historical_kpis_dir}",
                "completed_at": time.time(),
            }

        exit_code = run_kpi_analysis(
            current_kpi_file=current_kpis_file,
            historical_data_dir=historical_kpis_dir,
            output_file=output_path,
            plugin_module=plugin_module,
        )

        if exit_code == 0:
            return {
                "status": "success",
                "output_file": str(output_path),
                "completed_at": time.time(),
            }
        elif exit_code == 2:
            return {
                "status": "warning",
                "message": "no historical KPI found for regression testing",
                "output_file": str(output_path),
                "completed_at": time.time(),
            }
        elif exit_code == 3:
            return {
                "status": "regression_detected",
                "output_file": str(output_path),
                "completed_at": time.time(),
            }
        else:
            return {
                "status": "failed",
                "error": f"Analysis failed with exit code {exit_code}",
                "completed_at": time.time(),
            }

    except Exception as e:
        logger.exception("Analysis step failed")
        raise RuntimeError(f"KPI analysis failed: {e}") from e


def find_baseline_kpis(historical_dir: Path) -> dict[Path, dict[str, Any]]:
    """Load all kpis.json files from historical directory.

    Returns mapping of file paths to loaded hierarchical KPI data (schema v2 only).
    """
    baseline_kpis: dict[Path, dict[str, Any]] = {}
    kpi_files = list(historical_dir.rglob("kpis.json"))

    if not kpi_files:
        logger.warning("No kpis.json files found in: %s", historical_dir)
        return baseline_kpis

    logger.info("Found %d historical KPI files to load", len(kpi_files))

    for kpi_file in kpi_files:
        try:
            with open(kpi_file) as f:
                kpi_data = json.load(f)

            schema_version = kpi_data.get("schema_version", "unknown")
            if schema_version != "2":
                logger.warning(
                    "Skipping %s: unsupported schema version %s", kpi_file, schema_version
                )
                continue

            baseline_kpis[kpi_file] = kpi_data
            logger.debug("Loaded baseline: %s", kpi_file)

        except json.JSONDecodeError as e:
            logger.error("Failed to parse JSON in %s: %s", kpi_file, e)
        except Exception as e:
            logger.error("Failed to load %s: %s", kpi_file, e)

    logger.info("Successfully loaded %d historical KPI files", len(baseline_kpis))
    return baseline_kpis


def run_analyze(
    *,
    current_path: Any,
    baseline_kpis: dict[Path, dict[str, Any]],
    output_path: Any,
    plugin: Any = None,
) -> dict[str, Any]:
    """Run KPI analysis against ALL baseline files (CLI interface)."""
    if not baseline_kpis:
        return {
            "status": "failed",
            "error": "No baseline KPI files provided",
            "completed_at": time.time(),
        }

    first_baseline_path = next(iter(baseline_kpis.keys()))
    historical_dir = first_baseline_path.parent

    plugin_module = getattr(plugin, "__module__", "unknown") if plugin else "unknown"

    exit_code = run_kpi_analysis(
        current_kpi_file=Path(current_path),
        historical_data_dir=historical_dir,
        output_file=Path(output_path),
        plugin_module=plugin_module,
    )

    if exit_code == 0:
        return {"status": "success", "completed_at": time.time()}
    elif exit_code == 2:
        return {"status": "warning", "message": "no baselines found", "completed_at": time.time()}
    elif exit_code == 3:
        return {"status": "regression_detected", "completed_at": time.time()}
    else:
        return {"status": "failed", "error": f"exit code {exit_code}", "completed_at": time.time()}


# EOF
