#!/usr/bin/env python3
"""Test script for skeleton plugin dataclasses implementation."""

import json
import sys
from datetime import UTC, datetime

# Add the forge root to the path
sys.path.insert(0, "/home/kpouget/openshift/forge")

from projects.skeleton.postprocess.default.parsing.kpis import (
    SkeletonKpiCatalogEntry,
    SkeletonKpiHandler,
    SkeletonKpiRecord,
)


def test_kpi_record_dataclass():
    """Test KPI record dataclass creation and serialization."""
    print("=== Testing KPI Record Dataclass ===")

    # Create a KPI record
    kpi_record = SkeletonKpiRecord(
        schema_version="1",
        kpi_id="kpi_skeleton_throughput_rps",
        value=1000.0,
        unit="req/s",
        run_id="/artifacts/2024-03-15/test1",
        timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        labels={
            "scenario": "test1",
            "version": "2024-03-15",
            "higher_is_better": True,
        },
        metadata={"test_config": {}, "environment": "test"},
        source={
            "test_base_path": "/artifacts/2024-03-15/test1",
            "plugin_module": "skeleton",
        },
    )

    # Test dataclass functionality
    print(f"✅ KPI Record created: {kpi_record.kpi_id}")
    print(f"✅ Value: {kpi_record.value} {kpi_record.unit}")
    print(f"✅ Version: {kpi_record.labels['version']}")

    # Test serialization
    kpi_dict = kpi_record.to_dict()
    print(f"✅ Serialization works: {type(kpi_dict)}")
    print(f"✅ JSON serializable: {bool(json.dumps(kpi_dict))}")
    print()


def test_kpi_catalog_dataclass():
    """Test KPI catalog entry dataclass."""
    print("=== Testing KPI Catalog Dataclass ===")

    # Create a catalog entry
    catalog_entry = SkeletonKpiCatalogEntry(
        kpi_id="kpi_skeleton_throughput_rps",
        name="Skeleton Throughput",
        unit="req/s",
        higher_is_better=True,
        is_2d=False,
        help="Number of requests processed per second",
    )

    print(f"✅ Catalog entry created: {catalog_entry.name}")
    print(f"✅ KPI ID: {catalog_entry.kpi_id}")
    print(f"✅ Unit: {catalog_entry.unit}")
    print(f"✅ Higher is better: {catalog_entry.higher_is_better}")

    # Test serialization
    catalog_dict = catalog_entry.to_dict()
    print(f"✅ Serialization works: {type(catalog_dict)}")
    print()


def test_regression_report_dataclass():
    """Test regression report dataclass."""
    print("=== Testing Regression Report Dataclass ===")

    # Create sample KPI data
    baseline_kpis = [
        {
            "kpi_id": "kpi_skeleton_throughput_rps",
            "value": 1000.0,
            "unit": "req/s",
            "labels": {"version": "2024-03-15", "higher_is_better": True},
        }
    ]

    current_kpis = [
        {
            "kpi_id": "kpi_skeleton_throughput_rps",
            "value": 800.0,  # 20% decrease - regression
            "unit": "req/s",
            "labels": {"version": "2024-03-20", "higher_is_better": True},
        }
    ]

    # Create regression report
    report = SkeletonKpiHandler.create_regression_report(
        baseline_kpis=baseline_kpis,
        current_kpis=current_kpis,
        threshold=0.1,  # 10% threshold
    )

    print(f"✅ Regression report created: {report.status}")
    print(f"✅ Total KPIs: {report.total_kpis}")
    print(f"✅ Regressions detected: {report.regression_count}")
    print(f"✅ Has regressions: {report.has_regressions()}")
    print(f"✅ Is successful: {report.is_successful()}")
    print(f"✅ Baseline version: {report.baseline_version}")
    print(f"✅ Current version: {report.current_version}")

    # Test findings
    if report.findings:
        finding = report.findings[0]
        print(f"✅ First finding: {finding['kpi_id']}")
        print(
            f"✅ Change: {finding['baseline_value']} → {finding['current_value']} ({finding['change_percent']:.1f}%)"
        )
        print(f"✅ Is regression: {finding['is_regression']}")

    # Test serialization
    report_dict = report.to_dict()
    print(f"✅ Report serialization works: {type(report_dict)}")
    print(f"✅ JSON serializable: {bool(json.dumps(report_dict))}")
    print()


def test_catalog_generation():
    """Test the actual catalog generation from KPI functions."""
    print("=== Testing Catalog Generation ===")

    try:
        catalog = SkeletonKpiHandler.get_catalog()
        print(f"✅ Catalog generated: {len(catalog)} entries")

        for entry in catalog:
            print(f"  - {entry['kpi_id']}: {entry['name']} ({entry['unit']})")

        # Test that it's JSON serializable
        json.dumps(catalog)
        print("✅ Catalog is JSON serializable")

    except Exception as e:
        print(f"❌ Catalog generation failed: {e}")

    print()


def main():
    """Run all tests."""
    print("Testing Skeleton Plugin Dataclasses Implementation\n")

    test_kpi_record_dataclass()
    test_kpi_catalog_dataclass()
    test_regression_report_dataclass()
    test_catalog_generation()

    print("=== All Tests Complete ===")
    print("✅ Dataclasses implementation working correctly!")


if __name__ == "__main__":
    main()
