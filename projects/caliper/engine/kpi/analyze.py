"""Generic KPI regression analysis against historical baselines."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from projects.caliper.engine.kpi.format import (
    flatten_hierarchical_kpis as _extract_kpi_records_from_hierarchical,
)

logger = logging.getLogger(__name__)


class Verdict(StrEnum):
    """Per-KPI test verdict."""

    PASS = "PASS"
    REGRESSION = "REGRESSION"
    IMPROVEMENT = "IMPROVEMENT"
    SKIPPED = "SKIPPED"


class OverallStatus(StrEnum):
    """Overall analysis status."""

    PASS = "PASS"
    REGRESSION_DETECTED = "REGRESSION_DETECTED"
    NO_BASELINE = "NO_BASELINE"
    NO_TEST_PERFORMED = "NO_TEST_PERFORMED"


class Algorithm(StrEnum):
    """Regression testing algorithm identifier."""

    SCALAR_RELATIVE_CHANGE = "SCALAR_RELATIVE_CHANGE"
    TWO_DIM_AUC_CHANGE = "TWO_DIM_AUC_CHANGE"


@dataclass
class AnalysisConfig:
    """Configuration for KPI regression analysis.

    comparison_labels: Label keys that define what we compare against.
        Records must differ on at least one comparison key to be relevant baselines.
        E.g. ["version"] means we test the current version against other versions.
    ignored_labels: Label keys excluded when matching current to baseline records.
        E.g. ["os"] means we match across operating systems.
    sorting_labels: Label keys used to order entries in the output report.
    max_relative_regression: Fraction threshold for flagging regression (0.1 = 10%).
    min_baseline_points: Minimum number of baseline data points required to run a test.
    """

    comparison_labels: list[str] = field(default_factory=list)
    ignored_labels: list[str] = field(default_factory=list)
    sorting_labels: list[str] = field(default_factory=list)
    regression_config: dict[str, Any] = field(default_factory=dict)


@dataclass
class AnalysisReport:
    """Full analysis report."""

    status: str  # "success", "no_regression", "regression_detected", "error"
    processed: dict[str, Any]
    tested: list[dict[str, Any]]
    results: list[dict[str, Any]]
    overall: dict[str, Any]


def _load_analysis_config(plugin_module: str) -> AnalysisConfig:
    """Load analysis config from plugin module.

    Plugins must expose an `analysis_config` dict or `get_analysis_config()` callable.
    Raises ValueError if config is not available.
    """
    try:
        mod = __import__(plugin_module, fromlist=[""])
    except ImportError as exc:
        raise ValueError(
            f"Failed to import plugin module '{plugin_module}': {exc}. "
            f"Check that the plugin module exists and is importable."
        ) from exc

    if hasattr(mod, "get_analysis_config"):
        try:
            raw = mod.get_analysis_config()
        except Exception as exc:
            raise ValueError(
                f"Plugin module '{plugin_module}' has get_analysis_config() but calling it failed: {exc}"
            ) from exc
    elif hasattr(mod, "analysis_config"):
        raw = mod.analysis_config
    else:
        raise ValueError(
            f"Plugin module '{plugin_module}' is missing required analysis configuration. "
            f"Plugin must provide either 'analysis_config' attribute or 'get_analysis_config()' function."
        )

    if isinstance(raw, AnalysisConfig):
        config = raw
    elif isinstance(raw, dict):
        try:
            config = AnalysisConfig(
                **{k: v for k, v in raw.items() if k in AnalysisConfig.__dataclass_fields__}
            )
        except Exception as exc:
            raise ValueError(
                f"Plugin module '{plugin_module}' analysis config has invalid format: {exc}. "
                f"Config must be a dict with valid AnalysisConfig fields or an AnalysisConfig instance."
            ) from exc
    else:
        raise ValueError(
            f"Plugin module '{plugin_module}' analysis config has unsupported type '{type(raw).__name__}'. "
            f"Must be a dict or AnalysisConfig instance."
        )

    # Validate the configuration fields
    try:
        _validate_analysis_config(config, plugin_module)
        return config
    except ValueError:
        raise


def _validate_analysis_config(config: AnalysisConfig, plugin_module: str) -> None:
    """Validate AnalysisConfig fields and raise ValueError for invalid values."""
    # Validate list fields contain only strings
    for field_name in ["comparison_labels", "ignored_labels", "sorting_labels"]:
        field_value = getattr(config, field_name)
        if not isinstance(field_value, list):
            raise ValueError(
                f"Plugin module '{plugin_module}' analysis config field '{field_name}' must be a list, "
                f"got {type(field_value).__name__}: {field_value}"
            )
        for i, item in enumerate(field_value):
            if not isinstance(item, str):
                raise ValueError(
                    f"Plugin module '{plugin_module}' analysis config field '{field_name}' "
                    f"must contain only strings, got {type(item).__name__} at index {i}: {item}"
                )

    if not isinstance(config.regression_config, dict):
        raise ValueError(
            f"Plugin module '{plugin_module}' analysis config field 'regression_config' "
            f"must be a dict, got {type(config.regression_config).__name__}"
        )


def _match_key(
    labels: dict[str, Any], ignored_labels: list[str], comparison_labels: list[str]
) -> tuple:
    """Build a hashable match key from labels, excluding ignored and comparison labels."""
    excluded = set(ignored_labels) | set(comparison_labels)
    return tuple(sorted((k, str(v)) for k, v in labels.items() if k not in excluded))


def _is_relevant_baseline(
    labels: dict[str, Any],
    current_keys: dict[str, set[str]],
    excluded_labels: set[str],
) -> bool:
    """Return True if a baseline entry is relevant to the current data.

    A baseline entry is irrelevant if:
    - It has a label key absent from the current data (unexpected label), or
    - For a non-excluded key, its value does not appear in the current data.
    """
    for k, v in labels.items():
        if k not in current_keys:
            return False
        if k in excluded_labels:
            continue
        if str(v) not in current_keys[k]:
            return False
    return True


def _build_baseline_index(
    baseline_kpi_data: dict[Path, dict[str, Any]],
    config: AnalysisConfig,
    current_keys: dict[str, set[str]],
) -> dict[tuple, dict[frozenset, dict[str, Any]]]:
    """Index baseline records by (kpi_id, match_key) for fast lookup.

    Records with unexpected labels or irrelevant values are excluded.
    Deduplication by comparison keys: only one record per unique comparison
    key combination is kept (last one wins across baseline files).

    Returns mapping from (kpi_id, match_key) -> {comparison_keys_frozenset: record}.
    Same-version entries are excluded at query time by the caller.
    """
    excluded_labels = set(config.ignored_labels) | set(config.comparison_labels)
    index: dict[tuple, dict[frozenset, dict[str, Any]]] = {}
    for _path, data in baseline_kpi_data.items():
        records = _extract_kpi_records_from_hierarchical(data)
        for rec in records:
            labels = rec.get("labels", {})
            if not _is_relevant_baseline(labels, current_keys, excluded_labels):
                continue
            kpi_id = rec.get("kpi_id")
            mk = _match_key(labels, config.ignored_labels, config.comparison_labels)
            ck = frozenset((k, labels[k]) for k in config.comparison_labels if k in labels)
            index.setdefault((kpi_id, mk), {})[ck] = rec
    return index


def _run_regression_test(
    current: dict[str, Any],
    baselines: list[dict[str, Any]],
    config: AnalysisConfig,
) -> dict[str, Any]:
    """Run a regression test for a single KPI record against its baselines.

    Handles all skip logic internally (non-scalar value, insufficient baselines).
    Returns a complete result dict ready for inclusion in the report.
    """
    kpi_id = current["kpi_id"]
    run_id = current.get("run_id")
    raw_labels = current.get("labels", {})
    value = current.get("value")
    higher_is_better = current.get("higher_is_better", True)
    is_2d = current.get("is_2d", False)

    ignored = set(config.ignored_labels)
    comparison = set(config.comparison_labels)
    distinct_keys = {
        k: v for k, v in raw_labels.items() if k not in comparison and k not in ignored
    }
    labels = {
        "comparison_keys": {k: raw_labels[k] for k in config.comparison_labels if k in raw_labels},
        "distinct_keys": distinct_keys,
        "ignore_keys": {k: raw_labels[k] for k in config.ignored_labels if k in raw_labels},
    }

    current_comparison_keys = labels["comparison_keys"]

    baseline_values_list = [
        {
            "comparison_keys": {
                k: b["labels"][k] for k in config.comparison_labels if k in b.get("labels", {})
            },
            "value": b["value"],
        }
        for b in baselines
    ]

    base: dict[str, Any] = {
        "kpi_id": kpi_id,
        "verdict": None,  # placehold for field order in the JSON
        "reason": None,  # placehold for field order in the JSON
        "labels": labels,
        "run_id": run_id,
        "is_2d": is_2d,
        "higher_is_better": higher_is_better,
        "current_value": {"comparison_keys": current_comparison_keys, "value": value},
        "baseline_values": baseline_values_list,
        "baseline_count": len(baseline_values_list),
    }

    if is_2d:
        return _2d_auc_change_regression(
            base, value, higher_is_better, baseline_values_list, config.regression_config
        )
    else:
        return _scalar_relative_change_regression(
            base, value, higher_is_better, baseline_values_list, config.regression_config
        )


def _scalar_relative_change_regression(
    base: dict[str, Any],
    current_value: float | int,
    higher_is_better: bool,
    baseline_values_list: list[dict[str, Any]],
    regression_config: dict[str, Any],
) -> dict[str, Any]:
    """Scalar regression mechanism: compare current value against baseline mean via relative change."""

    relative_change_config = regression_config.get(Algorithm.SCALAR_RELATIVE_CHANGE, {})
    min_baseline_points = relative_change_config.get("min_baseline_points", 1)
    max_relative_regression = relative_change_config.get("max_relative_regression", 0.1)

    scalar_entries = [e for e in baseline_values_list if isinstance(e["value"], (int, float))]

    if not isinstance(current_value, (int, float)):
        return {**base, "verdict": Verdict.SKIPPED, "reason": "non-scalar current value"}

    if len(scalar_entries) < min_baseline_points:
        return {
            **base,
            "verdict": Verdict.SKIPPED,
            "reason": f"insufficient baselines ({len(scalar_entries)} < {min_baseline_points})",
        }

    scalar_values = [entry["value"] for entry in scalar_entries]
    baseline_mean = sum(scalar_values) / len(scalar_values)
    relative_change = (
        0.0 if baseline_mean == 0 else (float(current_value) - baseline_mean) / abs(baseline_mean)
    )

    regression = abs(relative_change) > abs(max_relative_regression)
    reason = None

    if regression:
        direction = "decrease" if higher_is_better else "increase"
        reason = (
            f"relative {direction} of {abs(relative_change * 100):.1f}% "
            f"exceeds threshold {max_relative_regression * 100:.0f}%"
        )

    result = {
        **base,
        "verdict": Verdict.REGRESSION if regression else Verdict.PASS,
        "details": {
            "algorithm": Algorithm.SCALAR_RELATIVE_CHANGE,
            "baseline_mean": round(baseline_mean, 6),
            "relative_change": round(relative_change, 6),
            "config": {"max_relative_regression": max_relative_regression},
        },
    }

    if reason:
        result["reason"] = reason

    return result


def _2d_auc_change_regression(
    base: dict[str, Any],
    current_value: list,
    higher_is_better: bool,
    baseline_values_list: list[dict[str, Any]],
    regression_config: dict[str, Any],
) -> dict[str, Any]:
    """2D curve regression via AUC → scalar relative change.

    Converts each curve to a scalar Area Under Curve (trapezoidal rule),
    then applies the same relative change test as SCALAR_RELATIVE_CHANGE.
    baseline_values_list entries: {"comparison_keys": {...}, "value": [[x, y], ...]}
    """
    curve_config = regression_config.get(Algorithm.TWO_DIM_AUC_CHANGE, {})
    min_baseline_points = curve_config.get("min_baseline_points", 1)
    max_relative_regression = curve_config.get("max_relative_regression", 0.1)

    current_curve = current_value.get("data_points")
    if not current_curve:
        return {**base, "verdict": Verdict.SKIPPED, "reason": "no data points in the KPI"}

    auc_baselines = [e for e in baseline_values_list if e and "data_points" in e["value"]]

    if len(auc_baselines) < min_baseline_points:
        return {
            **base,
            "verdict": Verdict.SKIPPED,
            "reason": f"insufficient curve baselines ({len(auc_baselines)} < {min_baseline_points})",
        }

    current_auc = _compute_auc(current_curve)

    baseline_auc_entries = [
        {
            "value": round(_compute_auc(e["value"]["data_points"]), 6),
            "comparison_keys": e["comparison_keys"],
        }
        for e in auc_baselines
    ]
    baseline_auc_values = [entry["value"] for entry in baseline_auc_entries]
    baseline_mean_auc = sum(baseline_auc_values) / len(baseline_auc_values)

    relative_change = (
        0.0
        if baseline_mean_auc == 0
        else (current_auc - baseline_mean_auc) / abs(baseline_mean_auc)
    )
    regression = abs(relative_change) > abs(max_relative_regression)
    reason = None
    if regression:
        direction = "decrease" if higher_is_better else "increase"
        reason = (
            f"AUC relative {direction} of {abs(relative_change * 100):.1f}% "
            f"exceeds threshold {max_relative_regression * 100:.0f}%"
        )

    result = {
        **base,
        "verdict": Verdict.REGRESSION if regression else Verdict.PASS,
        "details": {
            "algorithm": Algorithm.TWO_DIM_AUC_CHANGE,
            "current_auc": round(current_auc, 6),
            "baseline_mean_auc": round(baseline_mean_auc, 6),
            "baseline_aucs": baseline_auc_entries,
            "relative_change": round(relative_change, 6),
            "config": {"max_relative_regression": max_relative_regression},
        },
    }
    if reason:
        result["reason"] = reason
    return result


def _compute_auc(curve: list) -> float:
    """Compute area under a curve using the trapezoidal rule.

    Accepts a list of [x, y] pairs or {"x": ..., "y": ...} dicts.
    Points are sorted by x before integration.
    """

    def _xy(point):
        if isinstance(point, dict):
            return float(point["x"]), float(point["y"])
        return float(point[0]), float(point[1])

    if len(curve) < 2:
        return 0.0

    points = sorted((_xy(p) for p in curve), key=lambda p: p[0])
    return sum(
        (points[i + 1][0] - points[i][0]) * (points[i][1] + points[i + 1][1]) / 2
        for i in range(len(points) - 1)
    )


def _sort_results(results: list[dict[str, Any]], sorting_labels: list[str]) -> list[dict[str, Any]]:
    """Sort results by sorting labels extracted from labels, then by kpi_id.
    SKIPPED entries are placed after tested entries."""

    verdict_order = {Verdict.PASS: 0, Verdict.REGRESSION: 1, Verdict.SKIPPED: 2}

    def sort_key(r: dict[str, Any]) -> tuple:
        label_key = tuple(str(r.get("labels", {}).get(k, "")) for k in sorting_labels)
        return (verdict_order.get(r.get("verdict"), 9), *label_key, r.get("kpi_id", ""))

    return sorted(results, key=sort_key)


def _summarize_label_sets(
    data: dict[str, Any],
    comparison_labels: list[str],
    ignored_labels: list[str],
    current_keys: dict[str, set[str]] | None = None,
) -> dict[str, Any]:
    """Summarize label sets found in a hierarchical KPI doc.

    Returns:
      - comparison_labels: unique values per comparison key
      - ignored_labels: unique values per ignored key
      - relevant_common_keys: labels whose value is identical across all test entries (as key=val string)
      - relevant_distinct_keys: per-entry labels that differ (as key=val string), relevant entries only
    """
    all_labels = [test.get("labels", {}) for test in data.get("tests", [])]

    def _unique_values(key: str) -> list[str]:
        seen: list[str] = []
        for labels in all_labels:
            val = str(labels[key]) if key in labels else None
            if val is not None and val not in seen:
                seen.append(val)
        return sorted(seen)

    # For relevant_labels, keep only relevant entries and exclude ignored labels
    ignored = set(ignored_labels)
    excluded_labels = ignored | set(comparison_labels)
    seen_all = []
    seen_filtered = []
    for labels in all_labels:
        filtered = {k: v for k, v in labels.items() if k not in ignored}
        if filtered and filtered not in seen_all:
            seen_all.append(filtered)
        if current_keys is not None:
            if not _is_relevant_baseline(labels, current_keys, excluded_labels):
                continue
        if filtered and filtered not in seen_filtered:
            seen_filtered.append(filtered)

    if seen_filtered:
        all_keys = set().union(*seen_filtered)
        common_keys = {
            k for k in all_keys if all(ls.get(k) == seen_filtered[0].get(k) for ls in seen_filtered)
        }
        common = [f"{k}={seen_filtered[0][k]}" for k in sorted(common_keys)]
        distinct_keys = [
            ",".join(f"{k}={v}" for k, v in sorted(ls.items()) if k not in common_keys)
            for ls in seen_filtered
        ]
        distinct_labels: list[str] = []
        for ls in seen_filtered:
            for k in sorted(ls.keys()):
                if k not in common_keys:
                    if k not in distinct_labels:
                        distinct_labels.append(k)
        distinct_labels.sort()

    else:
        common, distinct_keys, distinct_labels = [], [], []

    return {
        "comparison_keys": sorted(f"{k}={v}" for k in comparison_labels for v in _unique_values(k)),
        "ignored_keys": sorted(f"{k}={v}" for k in ignored_labels for v in _unique_values(k)),
        "relevant_common_keys": common,
        "relevant_distinct_keys": distinct_keys,
        "relevant_distinct_labels": distinct_labels,
        "relevant_count": len(seen_filtered),
        "irrelevant_count": len(seen_all) - len(seen_filtered),
    }


def _build_report(
    results: list[dict[str, Any]],
    config: AnalysisConfig,
    current_source: dict[str, Any],
    baseline_sources: list[dict[str, Any]],
    baseline_skipped: dict[str, int],
) -> dict[str, Any]:
    """Build the final report structure."""
    regressions = [r for r in results if r["verdict"] == Verdict.REGRESSION]
    passes = [r for r in results if r["verdict"] == Verdict.PASS]
    skipped = [r for r in results if r["verdict"] == Verdict.SKIPPED]

    if regressions:
        overall_status = OverallStatus.REGRESSION_DETECTED
    elif not passes:
        overall_status = OverallStatus.NO_TEST_PERFORMED
    else:
        overall_status = OverallStatus.PASS

    report = {
        "analysis": {
            "status": overall_status,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "config": {
            "comparison_labels": config.comparison_labels,
            "ignored_labels": config.ignored_labels,
            "sorting_labels": config.sorting_labels,
            "regression_config": config.regression_config,
        },
        "tested": {
            "total_kpis": len(results),
            "pass": len(passes),
            "regression": len(regressions),
            "skipped": len(skipped),
        },
        "overall": {
            "verdict": overall_status,
            "regression_count": len(regressions),
            "total_tested": len(passes) + len(regressions),
            "total_skipped": len(skipped),
        },
        "input_data": {
            "current_source": current_source,
            "baseline_sources": baseline_sources,
            "baseline_source_count": len(baseline_sources),
            "baseline_skipped": baseline_skipped,
        },
        "results": passes + regressions,
        "skipped": skipped,
    }

    if not skipped:
        report.pop("skipped")
    if not baseline_skipped:
        report["input_data"].pop("baseline_skipped")

    return report


def _log_baseline_miss(
    kpi_id: str,
    current_mk: tuple,
    baseline_index: dict[tuple, dict[frozenset, dict[str, Any]]],
) -> None:
    """Log why no baseline matched for a KPI record."""
    candidate_keys = [k for k in baseline_index if k[0] == kpi_id]
    logger.debug("")
    if not candidate_keys:
        logger.debug("  No baseline entry at all for kpi_id=%r", kpi_id)
        return

    current_mk_dict = dict(current_mk)
    logger.debug("  kpi_id=%r: no match. current match_key=\n%s", kpi_id, current_mk_dict)

    for _, baseline_mk in candidate_keys:
        baseline_mk_dict = dict(baseline_mk)
        in_baseline_not_current = {
            k: v for k, v in baseline_mk_dict.items() if current_mk_dict.get(k) != v
        }
        in_current_not_baseline = {
            k: v for k, v in current_mk_dict.items() if baseline_mk_dict.get(k) != v
        }
        logger.debug(
            "    candidate=\n%s\n| baseline_differs=\n%s\n| current_differs=\n%s",
            baseline_mk_dict,
            in_baseline_not_current,
            in_current_not_baseline,
        )


def run_kpi_analysis(
    current_kpi_file: Path,
    historical_data_dir: Path,
    output_file: Path,
    plugin_module: str,
) -> dict[str, Any]:
    """Run KPI regression analysis and generate a JSON report.

    Args:
        current_kpi_file: Path to current KPI JSON file (hierarchical schema v2)
        historical_data_dir: Directory containing historical KPI files (kpis.json)
        output_file: Path where JSON analysis report will be written
        plugin_module: Plugin module name (for loading analysis config)

    Returns:
        Status dictionary with success/error information and exit_code for CLI compatibility
    """
    try:
        logger.info("Running KPI regression analysis")
        logger.info("  current_kpi_file: %s", current_kpi_file)
        logger.info("  historical_data_dir: %s", historical_data_dir)
        logger.info("  output_file: %s", output_file)
        logger.info("  plugin_module: %s", plugin_module)

        if not current_kpi_file.exists():
            logger.error("Current KPI file not found: %s", current_kpi_file)
            return {
                "status": "failed",
                "success": False,
                "error": f"Current KPI file not found: {current_kpi_file}",
                "exit_code": 1,
                "completed_at": time.time(),
            }, None

        if not historical_data_dir.exists():
            logger.error("Historical data directory not found: %s", historical_data_dir)
            return {
                "status": "failed",
                "success": False,
                "error": f"Historical data directory not found: {historical_data_dir}",
                "exit_code": 1,
                "completed_at": time.time(),
            }, None

        try:
            config = _load_analysis_config(plugin_module)
        except ValueError as exc:
            logger.error("Failed to load analysis config: %s", exc)
            return {
                "status": "failed",
                "success": False,
                "error": f"Analysis configuration error: {exc}",
                "exit_code": 1,
                "completed_at": time.time(),
            }, None

        logger.info(
            "  config: comparison_labels=%s, ignored_labels=%s",
            config.comparison_labels,
            config.ignored_labels,
        )

        # Load current KPIs
        with open(current_kpi_file) as f:
            current_data = json.load(f)

        if current_data.get("schema_version") != "2":
            logger.error("Current KPI file must be schema_version 2 (hierarchical)")
            return {
                "status": "failed",
                "success": False,
                "error": "Current KPI file must be schema_version 2 (hierarchical)",
                "exit_code": 1,
                "completed_at": time.time(),
            }, None

        current_records = _extract_kpi_records_from_hierarchical(current_data)
        if not current_records:
            logger.warning("No KPI records found in current file")
            return {
                "status": "failed",
                "success": False,
                "error": "No KPI records found in current file",
                "exit_code": 1,
                "completed_at": time.time(),
            }, None

        # Load baseline KPIs
        baseline_kpi_data = find_baseline_kpis(historical_data_dir)
        if not baseline_kpi_data:
            _write_no_baseline_report(output_file, current_kpi_file, plugin_module)
            return {
                "status": "warning",
                "success": True,
                "message": "no historical KPI found for regression testing",
                "output_file": str(output_file),
                "exit_code": 2,
                "completed_at": time.time(),
            }, None

        # Build label universe from current data for baseline filtering
        current_keys: dict[str, set[str]] = {}
        for test in current_data.get("tests", []):
            for k, v in test.get("labels", {}).items():
                current_keys.setdefault(k, set()).add(str(v))

        # Build baseline index (irrelevant entries are filtered out)
        baseline_index = _build_baseline_index(baseline_kpi_data, config, current_keys)

        # Run regression tests
        results: list[dict[str, Any]] = []

        baseline_skipped_totals: dict[str, int] = {"same_version": 0, "duplicate": 0}

        for rec in current_records:
            labels = rec.get("labels", {})
            mk = _match_key(labels, config.ignored_labels, config.comparison_labels)
            key = (rec.get("kpi_id"), mk)

            baseline_dict = baseline_index.get(key, {})
            if config.comparison_labels:
                current_ck = frozenset(
                    (k, labels[k]) for k in config.comparison_labels if k in labels
                )
                baselines = [r for ck, r in baseline_dict.items() if ck != current_ck]
                baseline_skipped_totals["same_version"] += len(baseline_dict) - len(baselines)
            else:
                baselines = list(baseline_dict.values())

            if not baselines:
                _log_baseline_miss(rec.get("kpi_id"), mk, baseline_index)

            result = _run_regression_test(rec, baselines, config)
            if not result.get("reason"):
                result.pop("reason", ...)

            results.append(result)

        # Sort results
        results = _sort_results(results, config.sorting_labels)

        # Build report
        excluded_labels = set(config.ignored_labels) | set(config.comparison_labels)
        baseline_sources = []
        for path, data in baseline_kpi_data.items():
            summary = _summarize_label_sets(
                data, config.comparison_labels, config.ignored_labels, current_keys
            )

            unexpected_labels: list[str] = []
            irrelevant_keys: list[str] = []
            for test in data.get("tests", []):
                for k, v in test.get("labels", {}).items():
                    if k not in current_keys:
                        if k not in unexpected_labels:
                            unexpected_labels.append(k)
                        continue
                    if k in excluded_labels:
                        continue
                    sv = str(v)
                    if sv not in current_keys[k]:
                        entry = f"{k}={sv}"
                        if entry not in irrelevant_keys:
                            irrelevant_keys.append(entry)

            baseline_sources.append(
                {
                    "path": str(path),
                    **summary,
                    "unexpected_labels": sorted(unexpected_labels),
                    "irrelevant_keys": sorted(irrelevant_keys),
                }
            )
            if not unexpected_labels:
                baseline_sources[-1].pop("unexpected_labels")
            if not irrelevant_keys:
                baseline_sources[-1].pop("irrelevant_keys")

        current_source = {
            "path": str(current_kpi_file),
            **_summarize_label_sets(current_data, config.comparison_labels, config.ignored_labels),
        }

        if (irr_count := current_source.pop("irrelevant_count")) != 0:
            logging.error(
                f"Found {irr_count} irrelevant entries in the current_source. Expected 0."
            )

        if not baseline_skipped_totals["same_version"]:
            baseline_skipped_totals.pop("same_version")
        if not baseline_skipped_totals["duplicate"]:
            baseline_skipped_totals.pop("duplicate")

        report = _build_report(
            results=results,
            config=config,
            current_source=current_source,
            baseline_sources=baseline_sources,
            baseline_skipped=baseline_skipped_totals,
        )

        # Write JSON report
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        regressions = report["overall"]["regression_count"]
        total = report["overall"]["total_tested"]
        overall_verdict = report["overall"]["verdict"]
        logger.info(
            "Analysis complete: %d/%d KPIs tested, %d regressions",
            total,
            len(current_records),
            regressions,
        )

        # Return status based on the overall verdict from the report
        if overall_verdict == OverallStatus.REGRESSION_DETECTED:
            return {
                "status": "regression_detected",
                "success": False,
                "regressions_detected": True,  # For orchestration compatibility
                "output_file": str(output_file),
                "exit_code": 3,
                "completed_at": time.time(),
            }, report
        elif overall_verdict == OverallStatus.NO_TEST_PERFORMED:
            return {
                "status": "warning",
                "success": True,
                "message": "all KPIs were skipped, no regression test performed",
                "output_file": str(output_file),
                "exit_code": 2,
                "completed_at": time.time(),
            }, report
        else:  # OverallStatus.PASS
            return {
                "status": "success",
                "success": True,
                "output_file": str(output_file),
                "exit_code": 0,
                "completed_at": time.time(),
            }, report

    except Exception as e:
        logger.exception("KPI analysis failed")
        return {
            "status": "failed",
            "success": False,
            "error": f"KPI analysis failed: {e}",
            "exit_code": 1,
            "completed_at": time.time(),
        }, None


def _write_no_baseline_report(
    output_file: Path, current_kpi_file: Path, plugin_module: str
) -> None:
    """Write a warning-level report when no baselines are available."""
    report = {
        "analysis": {
            "status": OverallStatus.NO_BASELINE,
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
            "verdict": OverallStatus.NO_BASELINE,
            "message": "No historical KPI files found for regression testing",
            "regression_count": 0,
            "total_tested": 0,
            "total_skipped": 0,
        },
    }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)


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

        except json.JSONDecodeError:
            # Try parsing as JSONL (v1 format) fallback
            try:
                v1_records = []
                with open(kpi_file) as f:
                    for line in f:
                        if line.strip():
                            v1_records.append(json.loads(line))

                if v1_records:
                    v2_data = _convert_v1_to_v2(v1_records)
                    baseline_kpis[kpi_file] = v2_data
                    logger.debug("Loaded baseline (converted from v1): %s", kpi_file)
            except Exception:
                logger.error("Failed to parse %s as JSON or JSONL", kpi_file)
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

    return run_kpi_analysis(
        current_kpi_file=Path(current_path),
        historical_data_dir=historical_dir,
        output_file=Path(output_path),
        plugin_module=plugin_module,
    )


def _convert_v1_to_v2(v1_records: list[dict]) -> dict:
    """Convert v1 JSONL records to v2 hierarchical format."""
    metrics = {}
    for record in v1_records:
        # Extract metric name and value
        metric_name = record.get("name", "unknown_metric")
        value = record.get("value")
        unit = record.get("unit")
        labels = record.get("labels", {})

        # Create metric entry in v2 format
        metric_entry = {
            "value": value,
            "unit": unit,
            "labels": labels,
        }

        metric_entry["higher_is_better"] = record.get("higher_is_better", False)

        metrics[metric_name] = metric_entry

    return {
        "schema_version": "2",
        "metrics": metrics,
        "metadata": {
            "converted_from_v1": True,
            "original_record_count": len(v1_records),
        },
    }


def ensure_kpi_file_v2_format(file_path: Path) -> tuple[Path, bool]:
    """Convert KPI file to v2 hierarchical format if it's in v1 JSONL format.

    Args:
        file_path: Path to KPI file (v1 or v2 format)

    Returns:
        Tuple of (converted_file_path, is_temporary_file)
        - converted_file_path: Path to v2 format file
        - is_temporary_file: True if a temporary file was created and needs cleanup
    """
    import json
    import tempfile

    logger.debug("Checking format of KPI file: %s", file_path)

    try:
        with open(file_path) as f:
            content = f.read().strip()
            if not content:
                raise ValueError(f"KPI file {file_path} is empty")

        # Try to parse as v2 (hierarchical JSON) first
        try:
            data = json.loads(content)
            if isinstance(data, dict) and "schema_version" in data:
                # Already in v2 format
                logger.debug("KPI file %s is already in v2 format", file_path)
                return file_path, False
            elif isinstance(data, list):
                raise ValueError(f"KPI file {file_path} appears to be in unexpected list format")
            else:
                # Single dict but missing schema_version - might be v1 single record
                logger.debug(
                    "KPI file %s appears to be single record without schema_version", file_path
                )
        except json.JSONDecodeError:
            # Not valid single JSON, might be JSONL (v1 format)
            logger.debug("KPI file %s not valid single JSON, checking JSONL format", file_path)

        # Try to parse as v1 (JSONL) format
        lines = content.split("\n")
        non_empty_lines = [line.strip() for line in lines if line.strip()]

        if len(non_empty_lines) == 0:
            raise ValueError(f"KPI file {file_path} contains no data")

        # Try to parse first line to validate v1 format
        try:
            first_record = json.loads(non_empty_lines[0])
            if not isinstance(first_record, dict):
                raise ValueError(f"KPI file {file_path} first line is not a JSON object")

            # Parse all lines as v1 JSONL records
            v1_records = []
            for i, line in enumerate(non_empty_lines):
                try:
                    record = json.loads(line)
                    v1_records.append(record)
                except json.JSONDecodeError as line_err:
                    raise ValueError(
                        f"KPI file {file_path} line {i + 1} has invalid JSON: {line_err}"
                    ) from line_err

            # Convert v1 records to v2 hierarchical format
            logger.info(
                "Converting KPI file %s from v1 (JSONL) to v2 (hierarchical) format", file_path
            )

            # Transform to v2
            v2_data = _convert_v1_to_v2(v1_records)

            # Write v2 format to temporary file
            temp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
            try:
                json.dump(v2_data, temp_file, indent=2, ensure_ascii=False)
                temp_file.flush()
                temp_path = Path(temp_file.name)
                logger.info(
                    "Converted %s (%d records) to v2 format at %s",
                    file_path,
                    len(v1_records),
                    temp_path,
                )
                return temp_path, True
            except Exception as write_err:
                temp_file.close()
                Path(temp_file.name).unlink(missing_ok=True)
                raise ValueError(
                    f"Failed to write converted v2 format file: {write_err}"
                ) from write_err
            finally:
                temp_file.close()

        except Exception as v1_err:
            raise ValueError(
                f"KPI file {file_path} is not in valid v1 (JSONL) or v2 (hierarchical) format: {v1_err}"
            ) from v1_err

    except Exception as format_err:
        raise ValueError(
            f"Failed to process KPI file format for {file_path}: {format_err}"
        ) from format_err


def analyze_kpis(
    current_kpis_file: Path,
    historical_kpis_dir: Path,
    output_file: Path,
    plugin_module: str,
):
    """Analyze KPIs with automatic v1/v2 format conversion.

    This function handles:
    1. Format detection and conversion from v1 (JSONL) to v2 (hierarchical)
    2. Plugin loading
    3. Analysis execution
    4. Temporary file cleanup
    5. Result formatting

    Args:
        current_kpis_file: Path to current KPIs file (v1 or v2 format)
        historical_kpis_dir: Directory containing historical KPI files
        output_file: Path where analysis results will be written
        plugin_module: Plugin module name for KPI definitions and analysis rules

    Returns:
        Dictionary with status information:
        - success: bool - whether analysis completed successfully
        - output_file: str - path to analysis results (if successful)
        - error: str - error message (if failed)
    """

    try:
        return run_kpi_analysis(
            current_kpi_file=current_kpis_file,
            historical_data_dir=historical_kpis_dir,
            output_file=output_file,
            plugin_module=plugin_module,
        )

    except Exception as e:
        logger.exception("KPI analysis with format conversion failed")
        return {
            "status": "failed",
            "success": False,
            "error": str(e),
            "exit_code": 1,
            "completed_at": time.time(),
        }, {}
