"""Tests for KPI regression analysis (analyze.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from projects.caliper.engine.kpi.analyze import (
    AnalysisConfig,
    Verdict,
    _build_baseline_index,
    _extract_kpi_records_from_hierarchical,
    _match_key,
    _run_regression_test,
    run_kpi_analysis,
)


def _make_hierarchical_kpi(
    tests: list[dict],
) -> dict:
    """Helper to build a schema_version=2 hierarchical KPI doc."""
    return {"schema_version": "2", "tests": tests}


def _make_test_entry(
    run_id: str,
    labels: dict,
    kpis: list[dict],
) -> dict:
    return {
        "run_id": run_id,
        "labels": labels,
        "metadata": {"timestamp": "2025-01-01T00:00:00Z"},
        "kpis": kpis,
    }


def _make_kpi(kpi_id: str, value, unit: str = "tokens/s", higher_is_better: bool = True) -> dict:
    return {"id": kpi_id, "value": value, "unit": unit, "higher_is_better": higher_is_better}


class TestMatchKey:
    def test_basic_match_key(self):
        labels = {"platform": "A100", "version": "1.0", "os": "linux"}
        key = _match_key(labels, ignored_labels=["os"], comparison_labels=["version"])
        assert ("os", "linux") not in key
        assert ("version", "1.0") not in key
        assert ("platform", "A100") in key

    def test_empty_config(self):
        labels = {"a": "1", "b": "2"}
        key = _match_key(labels, ignored_labels=[], comparison_labels=[])
        assert key == tuple(sorted([("a", "1"), ("b", "2")]))


class TestExtractRecords:
    def test_extracts_flat_records(self):
        data = _make_hierarchical_kpi(
            [
                _make_test_entry(
                    "run1",
                    {"platform": "A100"},
                    [
                        _make_kpi("throughput", 100.0),
                        _make_kpi("latency", 0.5, unit="s", higher_is_better=False),
                    ],
                ),
            ]
        )
        records = _extract_kpi_records_from_hierarchical(data)
        assert len(records) == 2
        assert records[0]["kpi_id"] == "throughput"
        assert records[0]["labels"] == {"platform": "A100"}
        assert records[1]["value"] == 0.5

    def test_empty_tests(self):
        data = _make_hierarchical_kpi([])
        assert _extract_kpi_records_from_hierarchical(data) == []


class TestBuildBaselineIndex:
    def test_indexes_by_kpi_and_match_key(self):
        data = _make_hierarchical_kpi(
            [
                _make_test_entry(
                    "run1",
                    {"platform": "A100", "version": "1.0"},
                    [
                        _make_kpi("throughput", 95.0),
                    ],
                ),
                _make_test_entry(
                    "run2",
                    {"platform": "A100", "version": "2.0"},
                    [
                        _make_kpi("throughput", 100.0),
                    ],
                ),
            ]
        )
        config = AnalysisConfig(comparison_labels=["version"])
        current_keys = {"platform": {"A100"}, "version": {"1.0", "2.0"}}
        baseline_data = {Path("/fake/kpis.json"): data}
        index = _build_baseline_index(baseline_data, config, current_keys)

        mk = _match_key({"platform": "A100"}, ignored_labels=[], comparison_labels=["version"])
        key = ("throughput", mk)
        assert key in index
        assert len(index[key]) == 2


class TestRegressionTest:
    def test_no_regression_higher_is_better(self):
        current = {"kpi_id": "throughput", "value": 100.0, "higher_is_better": True, "labels": {}}
        baselines = [{"value": 95.0, "labels": {}}, {"value": 100.0, "labels": {}}]
        config = AnalysisConfig(regression_config={"max_relative_regression": 0.1})
        result = _run_regression_test(current, baselines, config)
        assert result["verdict"] != Verdict.REGRESSION
        assert result["details"]["relative_change"] > 0

    def test_regression_higher_is_better(self):
        current = {"kpi_id": "throughput", "value": 80.0, "higher_is_better": True, "labels": {}}
        baselines = [{"value": 100.0, "labels": {}}, {"value": 100.0, "labels": {}}]
        config = AnalysisConfig(regression_config={"max_relative_regression": 0.1})
        result = _run_regression_test(current, baselines, config)
        assert result["verdict"] == Verdict.REGRESSION
        assert result["details"]["relative_change"] < -0.1

    def test_regression_lower_is_better(self):
        current = {"kpi_id": "latency", "value": 1.5, "higher_is_better": False, "labels": {}}
        baselines = [{"value": 1.0, "labels": {}}, {"value": 1.0, "labels": {}}]
        config = AnalysisConfig(regression_config={"max_relative_regression": 0.1})
        result = _run_regression_test(current, baselines, config)
        assert result["verdict"] == Verdict.REGRESSION
        assert result["details"]["relative_change"] > 0.1

    def test_no_regression_lower_is_better(self):
        current = {"kpi_id": "latency", "value": 0.9, "higher_is_better": False, "labels": {}}
        baselines = [{"value": 1.0, "labels": {}}]
        config = AnalysisConfig(regression_config={"max_relative_regression": 0.1})
        result = _run_regression_test(current, baselines, config)
        assert result["verdict"] != Verdict.REGRESSION


class TestEndToEnd:
    """Full end-to-end test of run_kpi_analysis."""

    def _write_hierarchical_kpi(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f)

    def test_pass_no_regression(self, tmp_path):
        current_dir = tmp_path / "current"
        historical_dir = tmp_path / "historical"
        output_file = tmp_path / "report.yaml"

        # Current KPIs: throughput=100
        current = _make_hierarchical_kpi(
            [
                _make_test_entry(
                    "current_run",
                    {"platform": "A100"},
                    [
                        _make_kpi("throughput", 100.0),
                    ],
                ),
            ]
        )
        self._write_hierarchical_kpi(current_dir / "kpis.json", current)

        # Historical: throughput=95, 98 (within 10% threshold)
        baseline1 = _make_hierarchical_kpi(
            [
                _make_test_entry(
                    "old_run_1",
                    {"platform": "A100"},
                    [
                        _make_kpi("throughput", 95.0),
                    ],
                ),
            ]
        )
        baseline2 = _make_hierarchical_kpi(
            [
                _make_test_entry(
                    "old_run_2",
                    {"platform": "A100"},
                    [
                        _make_kpi("throughput", 98.0),
                    ],
                ),
            ]
        )
        self._write_hierarchical_kpi(historical_dir / "run1" / "kpis.json", baseline1)
        self._write_hierarchical_kpi(historical_dir / "run2" / "kpis.json", baseline2)

        test_status, _ = run_kpi_analysis(
            current_kpi_file=current_dir / "kpis.json",
            historical_data_dir=historical_dir,
            output_file=output_file,
            plugin_module="projects.caliper.tests.stub_plugin",
        )

        assert test_status["exit_code"] == 0
        assert output_file.exists()

        with open(output_file) as f:
            report = yaml.safe_load(f)

        assert report["analysis"]["status"] == "PASS"
        assert report["overall"]["verdict"] == "PASS"
        assert report["overall"]["regression_count"] == 0
        assert report["tested"]["total_kpis"] == 1

    def test_regression_detected(self, tmp_path):
        current_dir = tmp_path / "current"
        historical_dir = tmp_path / "historical"
        output_file = tmp_path / "report.yaml"

        # Current: throughput dropped from 100 to 70 (30% regression)
        current = _make_hierarchical_kpi(
            [
                _make_test_entry(
                    "current_run",
                    {"platform": "A100"},
                    [
                        _make_kpi("throughput", 70.0),
                    ],
                ),
            ]
        )
        self._write_hierarchical_kpi(current_dir / "kpis.json", current)

        baseline = _make_hierarchical_kpi(
            [
                _make_test_entry(
                    "old_run",
                    {"platform": "A100"},
                    [
                        _make_kpi("throughput", 100.0),
                    ],
                ),
            ]
        )
        self._write_hierarchical_kpi(historical_dir / "run1" / "kpis.json", baseline)

        test_status, _ = run_kpi_analysis(
            current_kpi_file=current_dir / "kpis.json",
            historical_data_dir=historical_dir,
            output_file=output_file,
            plugin_module="projects.caliper.tests.stub_plugin",
        )

        assert test_status["exit_code"] == 3
        with open(output_file) as f:
            report = yaml.safe_load(f)

        assert report["analysis"]["status"] == "REGRESSION_DETECTED"
        assert report["overall"]["regression_count"] == 1
        assert report["results"][0]["verdict"] == "REGRESSION"
        assert report["results"][0]["details"]["relative_change"] == pytest.approx(-0.3)

    def test_no_historical_data(self, tmp_path):
        current_dir = tmp_path / "current"
        historical_dir = tmp_path / "historical"
        historical_dir.mkdir(parents=True)
        output_file = tmp_path / "report.yaml"

        current = _make_hierarchical_kpi(
            [
                _make_test_entry(
                    "run",
                    {"platform": "A100"},
                    [
                        _make_kpi("throughput", 100.0),
                    ],
                ),
            ]
        )
        self._write_hierarchical_kpi(current_dir / "kpis.json", current)

        test_status, _ = run_kpi_analysis(
            current_kpi_file=current_dir / "kpis.json",
            historical_data_dir=historical_dir,
            output_file=output_file,
            plugin_module="projects.caliper.tests.stub_plugin",
        )

        assert test_status["exit_code"] == 2
        with open(output_file) as f:
            report = yaml.safe_load(f)
        assert report["overall"]["verdict"] == "NO_BASELINE"

    def test_mixed_regression_and_pass(self, tmp_path):
        current_dir = tmp_path / "current"
        historical_dir = tmp_path / "historical"
        output_file = tmp_path / "report.yaml"

        current = _make_hierarchical_kpi(
            [
                _make_test_entry(
                    "run",
                    {"platform": "A100"},
                    [
                        _make_kpi("throughput", 100.0, higher_is_better=True),
                        _make_kpi("latency", 2.0, unit="s", higher_is_better=False),
                    ],
                ),
            ]
        )
        self._write_hierarchical_kpi(current_dir / "kpis.json", current)

        baseline = _make_hierarchical_kpi(
            [
                _make_test_entry(
                    "old",
                    {"platform": "A100"},
                    [
                        _make_kpi("throughput", 100.0, higher_is_better=True),
                        _make_kpi("latency", 1.0, unit="s", higher_is_better=False),
                    ],
                ),
            ]
        )
        self._write_hierarchical_kpi(historical_dir / "run1" / "kpis.json", baseline)

        test_status, _ = run_kpi_analysis(
            current_kpi_file=current_dir / "kpis.json",
            historical_data_dir=historical_dir,
            output_file=output_file,
            plugin_module="projects.caliper.tests.stub_plugin",
        )

        assert test_status["exit_code"] == 3
        with open(output_file) as f:
            report = yaml.safe_load(f)

        assert report["overall"]["regression_count"] == 1
        latency_result = next(r for r in report["results"] if r["kpi_id"] == "latency")
        assert latency_result["verdict"] == "REGRESSION"
        throughput_result = next(r for r in report["results"] if r["kpi_id"] == "throughput")
        assert throughput_result["verdict"] == "PASS"

    def test_skips_non_scalar_kpis(self, tmp_path):
        current_dir = tmp_path / "current"
        historical_dir = tmp_path / "historical"
        output_file = tmp_path / "report.yaml"

        current = _make_hierarchical_kpi(
            [
                _make_test_entry(
                    "run",
                    {"platform": "A100"},
                    [
                        _make_kpi("throughput", 100.0),
                        _make_kpi("curve_data", [1.0, 2.0, 3.0]),  # non-scalar
                    ],
                ),
            ]
        )
        self._write_hierarchical_kpi(current_dir / "kpis.json", current)

        baseline = _make_hierarchical_kpi(
            [
                _make_test_entry(
                    "old",
                    {"platform": "A100"},
                    [
                        _make_kpi("throughput", 95.0),
                    ],
                ),
            ]
        )
        self._write_hierarchical_kpi(historical_dir / "run1" / "kpis.json", baseline)

        test_status, _ = run_kpi_analysis(
            current_kpi_file=current_dir / "kpis.json",
            historical_data_dir=historical_dir,
            output_file=output_file,
            plugin_module="projects.caliper.tests.stub_plugin",
        )

        assert test_status["exit_code"] == 0
        with open(output_file) as f:
            report = yaml.safe_load(f)

        assert report["tested"]["skipped"] == 1
        assert report["tested"]["total_kpis"] == 2

    def test_comparison_keys_separate_baselines(self, tmp_path):
        """Records that differ on comparison_keys should still be matched."""
        current_dir = tmp_path / "current"
        historical_dir = tmp_path / "historical"
        output_file = tmp_path / "report.yaml"

        # Current: version=2.0 on platform=A100
        current = _make_hierarchical_kpi(
            [
                _make_test_entry(
                    "run",
                    {"platform": "A100", "version": "2.0"},
                    [
                        _make_kpi("throughput", 100.0),
                    ],
                ),
            ]
        )
        self._write_hierarchical_kpi(current_dir / "kpis.json", current)

        # Baseline: version=1.0 on platform=A100 (different comparison key value)
        baseline = _make_hierarchical_kpi(
            [
                _make_test_entry(
                    "old",
                    {"platform": "A100", "version": "1.0"},
                    [
                        _make_kpi("throughput", 95.0),
                    ],
                ),
            ]
        )
        self._write_hierarchical_kpi(historical_dir / "run1" / "kpis.json", baseline)

        # With version as comparison_key, both match on platform=A100
        # (plugin would expose this config; here we test the core logic directly)
        test_status, _ = run_kpi_analysis(
            current_kpi_file=current_dir / "kpis.json",
            historical_data_dir=historical_dir,
            output_file=output_file,
            plugin_module="projects.caliper.tests.stub_plugin",
        )

        # Without plugin config, default AnalysisConfig has no comparison_keys,
        # so version must also match → no baselines found → all KPIs skipped
        assert test_status["exit_code"] == 2
        with open(output_file) as f:
            report = yaml.safe_load(f)
        # The version label differs, so with empty comparison_keys they don't match → all skipped
        assert report["tested"]["total_kpis"] == 1
        assert report["tested"]["skipped"] == 1
        assert report["overall"]["verdict"] == "NO_TEST_PERFORMED"
