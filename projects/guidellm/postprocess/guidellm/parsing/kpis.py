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


# Throughput KPIs
@HigherBetter()
@Format("{:.2f}")
@KPIMetadata(help="Measured requests processed per second", unit="req/s")
def guidellm_measured_rps(unified_record) -> float:
    """Measured Request Rate KPI."""
    return float(unified_record.metrics.get("measured_rps", 0))


@HigherBetter()
@Format("{:.2f}")
@KPIMetadata(help="Output tokens processed per second", unit="tokens/s")
def guidellm_output_tok_per_sec(unified_record) -> float:
    """Output Token Throughput KPI."""
    return float(unified_record.metrics.get("output_tok/sec", 0))


@HigherBetter()
@Format("{:.2f}")
@KPIMetadata(help="Total tokens processed per second", unit="tokens/s")
def guidellm_total_tok_per_sec(unified_record) -> float:
    """Total Token Throughput KPI."""
    return float(unified_record.metrics.get("total_tok/sec", 0))


@HigherBetter()
@Format("{:.1f}")
@KPIMetadata(help="Measured concurrent connections", unit="connections")
def guidellm_measured_concurrency(unified_record) -> float:
    """Measured Concurrency KPI."""
    return float(unified_record.metrics.get("measured_concurrency", 0))


# Token Count Statistics KPIs
@LowerBetter()
@Format("{:.1f}")
@KPIMetadata(help="Average prompt token count", unit="tokens")
def guidellm_prompt_token_count_mean(unified_record) -> float:
    """Prompt Token Count Mean KPI."""
    return float(unified_record.metrics.get("prompt_token_count_mean", 0))


@LowerBetter()
@Format("{:.1f}")
@KPIMetadata(help="99th percentile prompt token count", unit="tokens")
def guidellm_prompt_token_count_p99(unified_record) -> float:
    """Prompt Token Count P99 KPI."""
    return float(unified_record.metrics.get("prompt_token_count_p99", 0))


@HigherBetter()
@Format("{:.1f}")
@KPIMetadata(help="Average output token count", unit="tokens")
def guidellm_output_token_count_mean(unified_record) -> float:
    """Output Token Count Mean KPI."""
    return float(unified_record.metrics.get("output_token_count_mean", 0))


@HigherBetter()
@Format("{:.1f}")
@KPIMetadata(help="99th percentile output token count", unit="tokens")
def guidellm_output_token_count_p99(unified_record) -> float:
    """Output Token Count P99 KPI."""
    return float(unified_record.metrics.get("output_token_count_p99", 0))


# Time to First Token (TTFT) KPIs
@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="Time to first token (median)", unit="s")
def guidellm_ttft_median(unified_record) -> float:
    """Time to First Token Median KPI."""
    return float(unified_record.metrics.get("ttft_median", 0))


@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="Time to first token (mean)", unit="s")
def guidellm_ttft_mean(unified_record) -> float:
    """Time to First Token Mean KPI."""
    return float(unified_record.metrics.get("ttft_mean", 0))


@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="Time to first token (1st percentile)", unit="s")
def guidellm_ttft_p1(unified_record) -> float:
    """Time to First Token P1 KPI."""
    return float(unified_record.metrics.get("ttft_p1", 0))


@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="Time to first token (95th percentile)", unit="s")
def guidellm_ttft_p95(unified_record) -> float:
    """Time to First Token P95 KPI."""
    return float(unified_record.metrics.get("ttft_p95", 0))


@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="Time to first token (99th percentile)", unit="s")
def guidellm_ttft_p99(unified_record) -> float:
    """Time to First Token P99 KPI."""
    return float(unified_record.metrics.get("ttft_p99", 0))


@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="Time to first token (99.9th percentile)", unit="s")
def guidellm_ttft_p999(unified_record) -> float:
    """Time to First Token P999 KPI."""
    return float(unified_record.metrics.get("ttft_p999", 0))


# Time Per Output Token (TPOT) KPIs
@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="Time per output token (median)", unit="s")
def guidellm_tpot_median(unified_record) -> float:
    """Time Per Output Token Median KPI."""
    return float(unified_record.metrics.get("tpot_median", 0))


@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="Time per output token (1st percentile)", unit="s")
def guidellm_tpot_p1(unified_record) -> float:
    """Time Per Output Token P1 KPI."""
    return float(unified_record.metrics.get("tpot_p1", 0))


@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="Time per output token (95th percentile)", unit="s")
def guidellm_tpot_p95(unified_record) -> float:
    """Time Per Output Token P95 KPI."""
    return float(unified_record.metrics.get("tpot_p95", 0))


@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="Time per output token (99th percentile)", unit="s")
def guidellm_tpot_p99(unified_record) -> float:
    """Time Per Output Token P99 KPI."""
    return float(unified_record.metrics.get("tpot_p99", 0))


@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="Time per output token (99.9th percentile)", unit="s")
def guidellm_tpot_p999(unified_record) -> float:
    """Time Per Output Token P999 KPI."""
    return float(unified_record.metrics.get("tpot_p999", 0))


# Inter-Token Latency (ITL) KPIs
@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="Inter-token latency (median)", unit="s")
def guidellm_itl_median(unified_record) -> float:
    """Inter Token Latency Median KPI."""
    return float(unified_record.metrics.get("itl_median", 0))


@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="Inter-token latency (mean)", unit="s")
def guidellm_itl_mean(unified_record) -> float:
    """Inter Token Latency Mean KPI."""
    return float(unified_record.metrics.get("itl_mean", 0))


@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="Inter-token latency (1st percentile)", unit="s")
def guidellm_itl_p1(unified_record) -> float:
    """Inter Token Latency P1 KPI."""
    return float(unified_record.metrics.get("itl_p1", 0))


@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="Inter-token latency (95th percentile)", unit="s")
def guidellm_itl_p95(unified_record) -> float:
    """Inter Token Latency P95 KPI."""
    return float(unified_record.metrics.get("itl_p95", 0))


@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="Inter-token latency (99th percentile)", unit="s")
def guidellm_itl_p99(unified_record) -> float:
    """Inter Token Latency P99 KPI."""
    return float(unified_record.metrics.get("itl_p99", 0))


@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="Inter-token latency (99.9th percentile)", unit="s")
def guidellm_itl_p999(unified_record) -> float:
    """Inter Token Latency P999 KPI."""
    return float(unified_record.metrics.get("itl_p999", 0))


# Request Latency KPIs
@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="End-to-end request latency (median)", unit="s")
def guidellm_request_latency_median(unified_record) -> float:
    """Request Latency Median KPI."""
    return float(unified_record.metrics.get("request_latency_median", 0))


@LowerBetter()
@Format("{:.4f}")
@KPIMetadata(help="End-to-end request latency (minimum)", unit="s")
def guidellm_request_latency_min(unified_record) -> float:
    """Request Latency Min KPI."""
    return float(unified_record.metrics.get("request_latency_min", 0))


@HigherBetter()
@Format("{:.4f}")
@KPIMetadata(help="End-to-end request latency (maximum)", unit="s")
def guidellm_request_latency_max(unified_record) -> float:
    """Request Latency Max KPI."""
    return float(unified_record.metrics.get("request_latency_max", 0))


# Success/Failure KPIs
@HigherBetter()
@Format("{:,.0f}")
@KPIMetadata(help="Number of successful requests", unit="requests")
def guidellm_successful_requests(unified_record) -> float:
    """Successful Requests KPI."""
    return float(unified_record.metrics.get("successful_requests", 0))


@LowerBetter()
@Format("{:,.0f}")
@KPIMetadata(help="Number of errored requests", unit="requests")
def guidellm_errored_requests(unified_record) -> float:
    """Errored Requests KPI."""
    return float(unified_record.metrics.get("errored_requests", 0))


# Example 2D KPIs
@HigherBetter()
@TwoDimensional(
    x_unit="req/s",
    x_help="Target request rate",
    y_unit="req/s",
    y_help="Achieved throughput",
    x_format="{:.1f}",
    y_format="{:.1f}",
)
@KPIMetadata(help="Throughput achieved at different target request rates", unit="req/s")
def guidellm_throughput_curve(unified_record) -> list[tuple[float, float]]:
    """Throughput vs Request Rate Curve KPI."""
    # Extract real curve data from load testing at different RPS levels
    curve_data = unified_record.metrics.get("throughput_curve", [])
    if not curve_data:
        # Return empty list when no real curve data exists
        return []

    return [(float(x), float(y)) for x, y in curve_data]


@LowerBetter()
@TwoDimensional(
    x_unit="req/s",
    x_help="Request rate",
    y_unit="s",
    y_help="Latency percentile",
    x_format="{:.1f}",
    y_format="{:.4f}",
)
@KPIMetadata(help="P95 latency at different request rates", unit="s")
def guidellm_latency_vs_load(unified_record) -> list[tuple[float, float]]:
    """P95 Latency vs Load Curve KPI."""
    # Extract real curve data showing how latency degrades with load
    latency_curve = unified_record.metrics.get("latency_curve", [])
    if not latency_curve:
        # Return empty list when no real curve data exists
        return []

    return [(float(x), float(y)) for x, y in latency_curve]


class GuideLLMKpiHandler:
    """Handles KPI catalog and computation for GuideLLM benchmarks."""

    # Define label extractor for all GuideLLM test conditions
    LABEL_EXTRACTOR = create_label_extractor(
        lambda record: {
            # Test identification
            "run": record.metrics.get("run", "unknown"),
            # Model configuration
            "model": record.metrics.get("model", "unknown"),
            "version": record.metrics.get("version", "unknown"),
            "image_tag": record.metrics.get("image_tag", "unknown"),
            "guidellm_version": record.metrics.get("guidellm_version", "unknown"),
            # Hardware setup
            "accelerator": record.metrics.get("accelerator", "unknown"),
            # Workload configuration
            "prompt_toks": str(record.metrics.get("prompt toks", 0)),
            "output_toks": str(record.metrics.get("output toks", 0)),
            "intended_concurrency": str(record.metrics.get("intended concurrency", 1)),
            # Parallelism settings
            "TP": str(record.metrics.get("TP", 1)),
            "DP": str(record.metrics.get("DP", 1)),
            "EP": str(record.metrics.get("EP", 1)),
            "replicas": str(record.metrics.get("replicas", 1)),
            # Infrastructure setup
            "prefill_pod_count": str(record.metrics.get("prefill_pod_count", 0)),
            "decode_pod_count": str(record.metrics.get("decode_pod_count", 0)),
            "router_config": record.metrics.get("router_config", "unknown"),
            # Runtime configuration
            "runtime_args": record.metrics.get("runtime_args", "unknown"),
            # Performance tier (derived)
            "performance_tier": (
                "high"
                if record.metrics.get("total_tok/sec", 0) > 1000
                else "medium"
                if record.metrics.get("total_tok/sec", 0) > 100
                else "low"
            ),
        }
    )

    # Metadata fields to include in KPI records but not as labels
    @staticmethod
    def extract_metadata(record) -> dict[str, Any]:
        """Extract metadata fields for KPI records."""
        return {
            "uuid": record.metrics.get("uuid"),
            "notes": record.metrics.get("notes"),
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

        for r in model.unified_result_records:
            # Skip records without GuideLLM data
            if not r.run_identity.get("guidellm"):
                continue

            # Skip if no benchmarks found
            if r.metrics.get("no_benchmarks_found"):
                continue

            base_labels = {**r.distinguishing_labels}

            # Extract test condition labels (same for all KPIs in this test)
            test_condition_labels = GuideLLMKpiHandler.LABEL_EXTRACTOR.extract(r)

            # Extract metadata fields
            metadata_fields = GuideLLMKpiHandler.extract_metadata(r)

            # Compute each KPI using the decorated functions
            for kpi_id, kpi_func in kpi_functions.items():
                try:
                    value = kpi_func(r)
                except (TypeError, ValueError, KeyError):
                    if is_2d_kpi(kpi_func):
                        value = []  # Empty list for failed 2D KPIs
                    else:
                        value = 0.0  # Zero for failed scalar KPIs

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
                }

                # Add 2D-specific metadata
                if is_2d_kpi(kpi_func):
                    kpi_record["is_2d"] = True
                    kpi_record["x_unit"] = kpi_func._kpi_x_unit
                    kpi_record["x_help"] = kpi_func._kpi_x_help
                    kpi_record["y_unit"] = (
                        getattr(kpi_func, "_kpi_y_unit", None) or kpi_func._kpi_unit
                    )
                    kpi_record["y_help"] = (
                        getattr(kpi_func, "_kpi_y_help", None) or kpi_func._kpi_help
                    )
                else:
                    kpi_record["is_2d"] = False

                out.append(kpi_record)

        return out
