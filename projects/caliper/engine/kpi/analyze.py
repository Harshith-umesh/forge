"""Generic KPI regression analysis against historical baselines."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
    baseline_values: dict[str, float] = field(default_factory=dict)


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
    for field_name in ["comparison_keys", "ignored_keys", "sorting_keys"]:
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

    # Validate max_relative_regression is numeric
    if not isinstance(config.max_relative_regression, (int, float)):
        raise ValueError(
            f"Plugin module '{plugin_module}' analysis config field 'max_relative_regression' "
            f"must be numeric, got {type(config.max_relative_regression).__name__}: {config.max_relative_regression}"
        )

    # Validate min_baseline_points is at least 1
    if not isinstance(config.min_baseline_points, int) or config.min_baseline_points < 1:
        raise ValueError(
            f"Plugin module '{plugin_module}' analysis config field 'min_baseline_points' "
            f"must be an integer >= 1, got {type(config.min_baseline_points).__name__}: {config.min_baseline_points}"
        )


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

    # Build baseline values mapping by comparison flags
    baseline_values_by_comparison = {}
    for baseline in baselines:
        if baseline.get("value") is not None:
            baseline_value = float(baseline["value"])
            baseline_labels = baseline.get("labels", {})

            # Create comparison flag from comparison_keys
            comparison_parts = []
            for key in config.comparison_keys:
                if key in baseline_labels:
                    comparison_parts.append(f"{key}={baseline_labels[key]}")

            comparison_flag = ", ".join(comparison_parts) if comparison_parts else "default"
            baseline_values_by_comparison[comparison_flag] = baseline_value

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
        baseline_values=baseline_values_by_comparison,
    )


def _sort_results(results: list[KpiTestResult], sorting_keys: list[str]) -> list[KpiTestResult]:
    """Sort results by sorting keys extracted from labels, then by kpi_id."""

    def sort_key(r: KpiTestResult):
        label_key = tuple(str(r.labels.get(k, "")) for k in sorting_keys)
        return (*label_key, r.kpi_id)

    return sorted(results, key=sort_key)


def _build_report(
    results: list[KpiTestResult],
    config: AnalysisConfig,
    current_source: str,
    baseline_sources: list[str],
    skipped: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the final report structure."""
    regressions = [r for r in results if r.regression]

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
                "baseline_values": r.baseline_values,
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
            }

        if not historical_data_dir.exists():
            logger.error("Historical data directory not found: %s", historical_data_dir)
            return {
                "status": "failed",
                "success": False,
                "error": f"Historical data directory not found: {historical_data_dir}",
                "exit_code": 1,
                "completed_at": time.time(),
            }

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
            }

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
            return {
                "status": "failed",
                "success": False,
                "error": "Current KPI file must be schema_version 2 (hierarchical)",
                "exit_code": 1,
                "completed_at": time.time(),
            }

        current_records = _extract_kpi_records_from_hierarchical(current_data)
        if not current_records:
            logger.warning("No KPI records found in current file")
            return {
                "status": "failed",
                "success": False,
                "error": "No KPI records found in current file",
                "exit_code": 1,
                "completed_at": time.time(),
            }

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
            }

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
        report = _build_report(
            results=results,
            config=config,
            current_source=str(current_kpi_file),
            baseline_sources=baseline_sources,
            skipped=skipped,
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
        if overall_verdict == "REGRESSION_DETECTED":
            return {
                "status": "regression_detected",
                "success": False,
                "regressions_detected": True,  # For orchestration compatibility
                "output_file": str(output_file),
                "exit_code": 3,
                "completed_at": time.time(),
            }
        else:  # "PASS"
            return {
                "status": "success",
                "success": True,
                "output_file": str(output_file),
                "exit_code": 0,
                "completed_at": time.time(),
            }

    except Exception as e:
        logger.exception("KPI analysis failed")
        return {
            "status": "failed",
            "success": False,
            "error": f"KPI analysis failed: {e}",
            "exit_code": 1,
            "completed_at": time.time(),
        }


def _write_no_baseline_report(
    output_file: Path, current_kpi_file: Path, plugin_module: str
) -> None:
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

    result = run_kpi_analysis(
        current_kpi_file=Path(current_path),
        historical_data_dir=historical_dir,
        output_file=Path(output_path),
        plugin_module=plugin_module,
    )

    # Extract just the relevant fields for this legacy interface
    return {
        "status": result.get("status"),
        "message": result.get("message"),
        "completed_at": result.get("completed_at"),
    }


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

        # Determine if higher is better from labels or default to false
        higher_is_better = labels.get("higher_is_better", False)
        metric_entry["higher_is_better"] = higher_is_better

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
) -> dict[str, Any]:
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
        # Use directory-based analysis (supports multiple baseline files)
        result = run_kpi_analysis(
            current_kpi_file=current_kpis_file,
            historical_data_dir=historical_kpis_dir,
            output_file=output_file,
            plugin_module=plugin_module,
        )

        # Return result directly since run_kpi_analysis now returns final status values
        return result

    except Exception as e:
        logger.exception("KPI analysis with format conversion failed")
        return {
            "status": "failed",
            "success": False,
            "error": str(e),
            "exit_code": 1,
            "completed_at": time.time(),
        }
