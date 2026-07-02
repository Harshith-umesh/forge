"""KPI definitions and computation for GuideLLM Caliper plugin."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import Any

from projects.caliper.engine.kpi import (
    Format,
    HigherBetter,
    KPIMetadata,
    LowerBetter,
    TwoDimensional,
    build_catalog_from_functions,
    create_label_extractor,
    get_kpi_functions,
    is_2d_kpi,
)
from projects.caliper.engine.model import UnifiedRunModel


# Throughput KPIs - for individual benchmark records
@HigherBetter()
@Format("{:.2f}")
@KPIMetadata(help="Maximum achieved request rate", unit="req/s")
def guidellm_max_request_rate(unified_record) -> float:
    """Maximum Request Rate KPI."""
    value = unified_record.metrics.get("request_rate")
    if value is None:
        raise ValueError("request_rate metric not found")
    return float(value)


@HigherBetter()
@Format("{:.2f}")
@KPIMetadata(help="Maximum achieved output token throughput", unit="tokens/s")
def guidellm_max_output_tokens_per_second(unified_record) -> float:
    """Maximum Output Token Throughput KPI."""
    value = unified_record.metrics.get("output_tokens_per_second")
    if value is None:
        raise ValueError("output_tokens_per_second metric not found")
    return float(value)


@HigherBetter()
@Format("{:.2f}")
@KPIMetadata(help="Maximum achieved total token throughput", unit="tokens/s")
def guidellm_max_tokens_per_second(unified_record) -> float:
    """Maximum Total Token Throughput KPI."""
    value = unified_record.metrics.get("tokens_per_second")
    if value is None:
        raise ValueError("tokens_per_second metric not found")
    return float(value)


@HigherBetter()
@Format("{:.1f}")
@KPIMetadata(help="Request concurrency level", unit="connections")
def guidellm_request_concurrency(unified_record) -> float:
    """Request Concurrency KPI."""
    value = unified_record.metrics.get("request_concurrency")
    if value is None:
        raise ValueError("request_concurrency metric not found")
    return float(value)


# Token Count Statistics KPIs - static values
@LowerBetter()
@Format("{:.1f}")
@KPIMetadata(help="Average input tokens per request", unit="tokens")
def guidellm_input_tokens_per_request(unified_record) -> float:
    """Input Tokens Per Request KPI."""
    value = unified_record.metrics.get("input_tokens_per_request")
    if value is None:
        raise ValueError("input_tokens_per_request metric not found")
    return float(value)


@LowerBetter()
@Format("{:.1f}")
@KPIMetadata(help="Average output tokens per request", unit="tokens")
def guidellm_output_tokens_per_request(unified_record) -> float:
    """Output Tokens Per Request KPI."""
    value = unified_record.metrics.get("output_tokens_per_request")
    if value is None:
        raise ValueError("output_tokens_per_request metric not found")
    return float(value)


# Time to First Token (TTFT) KPIs - from individual records
@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="Time to first token median", unit="s")
def guidellm_best_ttft_median(unified_record) -> float:
    """Time to First Token Median KPI."""
    value = unified_record.metrics.get("ttft_median")
    if value is None:
        raise ValueError("ttft_median metric not found")
    return float(value)


@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="Time to first token P95", unit="s")
def guidellm_best_ttft_p95(unified_record) -> float:
    """Time to First Token P95 KPI."""
    value = unified_record.metrics.get("ttft_p95")
    if value is None:
        raise ValueError("ttft_p95 metric not found")
    return float(value)


@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="Time to first token P99", unit="s")
def guidellm_best_ttft_p99(unified_record) -> float:
    """Time to First Token P99 KPI."""
    value = unified_record.metrics.get("ttft_p99")
    if value is None:
        raise ValueError("ttft_p99 metric not found")
    return float(value)


# Time Per Output Token (TPOT) KPIs - from individual records
@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="Time per output token median", unit="s")
def guidellm_best_tpot_median(unified_record) -> float:
    """Time Per Output Token Median KPI."""
    value = unified_record.metrics.get("tpot_median")
    if value is None:
        raise ValueError("tpot_median metric not found")
    return float(value)


@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="Time per output token P95", unit="s")
def guidellm_best_tpot_p95(unified_record) -> float:
    """Time Per Output Token P95 KPI."""
    value = unified_record.metrics.get("tpot_p95")
    if value is None:
        raise ValueError("tpot_p95 metric not found")
    return float(value)


@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="Time per output token P99", unit="s")
def guidellm_best_tpot_p99(unified_record) -> float:
    """Time Per Output Token P99 KPI."""
    value = unified_record.metrics.get("tpot_p99")
    if value is None:
        raise ValueError("tpot_p99 metric not found")
    return float(value)


# Inter-Token Latency (ITL) KPIs - from individual records
@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="Inter-token latency median", unit="s")
def guidellm_best_itl_median(unified_record) -> float:
    """Inter Token Latency Median KPI."""
    value = unified_record.metrics.get("itl_median")
    if value is None:
        raise ValueError("itl_median metric not found")
    return float(value)


@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="Inter-token latency P95", unit="s")
def guidellm_best_itl_p95(unified_record) -> float:
    """Inter Token Latency P95 KPI."""
    value = unified_record.metrics.get("itl_p95")
    if value is None:
        raise ValueError("itl_p95 metric not found")
    return float(value)


@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="Inter-token latency P99", unit="s")
def guidellm_best_itl_p99(unified_record) -> float:
    """Inter Token Latency P99 KPI."""
    value = unified_record.metrics.get("itl_p99")
    if value is None:
        raise ValueError("itl_p99 metric not found")
    return float(value)


# Request Latency KPIs - from individual records
@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="End-to-end request latency median", unit="s")
def guidellm_best_request_latency_median(unified_record) -> float:
    """Request Latency Median KPI."""
    value = unified_record.metrics.get("request_latency_median")
    if value is None:
        raise ValueError("request_latency_median metric not found")
    return float(value)


@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="End-to-end request latency P95", unit="s")
def guidellm_best_request_latency_p95(unified_record) -> float:
    """Request Latency P95 KPI."""
    value = unified_record.metrics.get("request_latency_p95")
    if value is None:
        raise ValueError("request_latency_p95 metric not found")
    return float(value)


# Success/Failure KPIs - from individual records
@HigherBetter()
@Format("{:,.0f}")
@KPIMetadata(help="Completed requests", unit="requests")
def guidellm_total_completed_requests(unified_record) -> float:
    """Completed Requests KPI."""
    value = unified_record.metrics.get("completed_requests")
    if value is None:
        raise ValueError("completed_requests metric not found")
    return float(value)


@LowerBetter()
@Format("{:,.0f}")
@KPIMetadata(help="Failed requests", unit="requests")
def guidellm_total_failed_requests(unified_record) -> float:
    """Failed Requests KPI."""
    value = unified_record.metrics.get("failed_requests")
    if value is None:
        raise ValueError("failed_requests metric not found")
    return float(value)


# 2D KPIs that aggregate data from multiple individual records
@HigherBetter()
@TwoDimensional(
    x_unit="req/s",
    x_help="Request rate",
    y_unit="tokens/s",
    y_help="Achieved throughput",
    x_format="{:.1f}",
    y_format="{:.1f}",
)
@KPIMetadata(help="Throughput achieved at different request rates", unit="tokens/s")
def guidellm_throughput_curve(unified_records) -> list[tuple[float, float]]:
    """Throughput vs Request Rate Curve KPI."""
    # Handle both single record and list of records
    if not isinstance(unified_records, list):
        unified_records = [unified_records]

    curve_points = []
    for record in unified_records:
        request_rate = record.metrics.get("request_rate", 0)
        tokens_per_sec = record.metrics.get("tokens_per_second", 0)

        if request_rate > 0 and tokens_per_sec > 0:
            curve_points.append((float(request_rate), float(tokens_per_sec)))

    return curve_points


@LowerBetter()
@TwoDimensional(
    x_unit="req/s",
    x_help="Request rate",
    y_unit="s",
    y_help="P95 latency",
    x_format="{:.1f}",
    y_format="{:.4f}",
)
@KPIMetadata(help="P95 latency at different request rates", unit="s")
def guidellm_latency_vs_load(unified_records) -> list[tuple[float, float]]:
    """P95 Latency vs Load Curve KPI."""
    # Handle both single record and list of records
    if not isinstance(unified_records, list):
        unified_records = [unified_records]

    curve_points = []
    for record in unified_records:
        request_rate = record.metrics.get("request_rate", 0)
        p95_latency = record.metrics.get("request_latency_p95", 0)

        if request_rate > 0 and p95_latency > 0:
            curve_points.append((float(request_rate), float(p95_latency)))

    return curve_points


@LowerBetter()
@TwoDimensional(
    x_unit="req/s",
    x_help="Request rate",
    y_unit="s",
    y_help="TTFT P95",
    x_format="{:.1f}",
    y_format="{:.4f}",
)
@KPIMetadata(help="Time to first token P95 at different request rates", unit="s")
def guidellm_ttft_vs_load(unified_records) -> list[tuple[float, float]]:
    """TTFT P95 vs Load Curve KPI."""
    # Handle both single record and list of records
    if not isinstance(unified_records, list):
        unified_records = [unified_records]

    curve_points = []
    for record in unified_records:
        request_rate = record.metrics.get("request_rate", 0)
        ttft_p95 = record.metrics.get("ttft_p95", 0)

        if request_rate > 0 and ttft_p95 > 0:
            curve_points.append((float(request_rate), float(ttft_p95)))

    return curve_points


class GuideLLMKpiHandler:
    """Handles KPI catalog and computation for GuideLLM benchmarks."""

    # Define label extractor for all GuideLLM test conditions
    LABEL_EXTRACTOR = create_label_extractor(
        {
            "strategy": "metrics.strategy",
            "duration": "metrics.duration",
        }
    )

    # Metadata fields to include in KPI records but not as labels
    @staticmethod
    def extract_metadata(record) -> dict[str, Any]:
        """Extract metadata fields for KPI records."""
        config = record.metrics.get("configuration", {})
        return {
            "configuration": config,
            "run_path": record.test_base_path,
        }

    @staticmethod
    def get_catalog() -> list[dict[str, Any]]:
        """
        Return the KPI catalog for GuideLLM metrics.

        Returns:
            List of KPI definitions
        """
        current_module = inspect.getmodule(GuideLLMKpiHandler)
        return build_catalog_from_functions(current_module)

    @staticmethod
    def compute_kpis(model: UnifiedRunModel) -> list[dict[str, Any]]:
        """
        Compute KPI values from the unified model.

        Args:
            model: Unified model containing parsed test results

        Returns:
            List of KPI records
        """
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        out: list[dict[str, Any]] = []
        current_module = inspect.getmodule(GuideLLMKpiHandler)
        kpi_functions = get_kpi_functions(current_module)

        # Filter valid records
        valid_records = [
            r
            for r in model.unified_result_records
            if r.run_identity.get("guidellm") and not r.metrics.get("no_benchmarks_found")
        ]

        if not valid_records:
            return out

        # Group records by test path for 2D KPIs (same test, different rates)
        from collections import defaultdict

        records_by_test = defaultdict(list)
        for r in valid_records:
            records_by_test[r.test_base_path].append(r)

        # Generate scalar KPIs for each record
        for r in valid_records:
            base_labels = {**r.distinguishing_labels}
            test_condition_labels = GuideLLMKpiHandler.LABEL_EXTRACTOR.extract(r)
            metadata_fields = GuideLLMKpiHandler.extract_metadata(r)

            # Compute scalar KPIs only
            for kpi_id, kpi_func in kpi_functions.items():
                # Skip 2D KPIs for individual records - they'll be handled separately
                if is_2d_kpi(kpi_func):
                    continue

                try:
                    value = kpi_func(r)
                except (TypeError, ValueError, KeyError):
                    value = None  # None for missing/failed scalar KPIs

                # Skip KPIs with null values
                if value is None:
                    continue

                # Merge base labels, test condition labels, and system labels
                all_labels = {
                    **base_labels,
                    **test_condition_labels,
                    "higher_is_better": kpi_func._kpi_higher_is_better,
                }

                kpi_record = {
                    "schema_version": "1",
                    "kpi_id": kpi_id,
                    "value": value,
                    "unit": kpi_func._kpi_unit,
                    "run_id": r.test_base_path,
                    "timestamp": ts,
                    "labels": all_labels,
                    "metadata": metadata_fields,
                    "source": {
                        "test_base_path": r.test_base_path,
                        "plugin_module": model.plugin_module,
                    },
                    "is_2d": False,
                }

                out.append(kpi_record)

        # Generate 2D curve KPIs once per test (aggregated across rates)
        for test_path, test_records in records_by_test.items():
            if len(test_records) < 1:
                continue  # Need at least one record

            # Use first record for labels and metadata
            representative_record = test_records[0]
            base_labels = {**representative_record.distinguishing_labels}
            test_condition_labels = GuideLLMKpiHandler.LABEL_EXTRACTOR.extract(
                representative_record
            )
            metadata_fields = GuideLLMKpiHandler.extract_metadata(representative_record)

            # Generate 2D KPIs with aggregated data
            for kpi_id, kpi_func in kpi_functions.items():
                if not is_2d_kpi(kpi_func):
                    continue

                try:
                    # Pass all records for this test to the 2D KPI function
                    if len(test_records) == 1:
                        # If only one record, pass it directly (for backward compatibility)
                        value = kpi_func(test_records[0])
                    else:
                        # Multiple records, pass as list for aggregation
                        value = kpi_func(test_records)
                except (TypeError, ValueError, KeyError):
                    value = []  # Empty list for failed 2D KPIs

                # Skip 2D KPIs with empty or null values
                if not value or value is None:
                    continue

                # Remove rate-specific labels for aggregated KPIs
                aggregated_labels = {
                    k: v
                    for k, v in base_labels.items()
                    if k not in ["concurrency", "rate", "max_concurrency"]
                }
                all_labels = {
                    **aggregated_labels,
                    **test_condition_labels,
                    "higher_is_better": kpi_func._kpi_higher_is_better,
                }

                kpi_record = {
                    "schema_version": "1",
                    "kpi_id": kpi_id,
                    "value": value,
                    "unit": kpi_func._kpi_unit,
                    "run_id": test_path,
                    "timestamp": ts,
                    "labels": all_labels,
                    "metadata": metadata_fields,
                    "source": {
                        "test_base_path": test_path,
                        "plugin_module": model.plugin_module,
                    },
                    "is_2d": True,
                    "x_unit": kpi_func._kpi_x_unit,
                    "x_help": kpi_func._kpi_x_help,
                    "y_unit": getattr(kpi_func, "_kpi_y_unit", None) or kpi_func._kpi_unit,
                    "y_help": getattr(kpi_func, "_kpi_y_help", None) or kpi_func._kpi_help,
                }

                out.append(kpi_record)

        return out
