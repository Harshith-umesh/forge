from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from projects.caliper.engine.model import UnifiedRunModel


class RhaiisKpiHandler:
    @staticmethod
    def get_catalog() -> list[dict[str, Any]]:
        return [
            {"kpi_id": "rhaiis_output_tok_per_sec", "name": "Output Token Throughput", "unit": "tokens/s", "higher_is_better": True},
            {"kpi_id": "rhaiis_total_tok_per_sec", "name": "Total Token Throughput", "unit": "tokens/s", "higher_is_better": True},
            {"kpi_id": "rhaiis_measured_rps", "name": "Measured Request Rate", "unit": "req/s", "higher_is_better": True},
            {"kpi_id": "rhaiis_measured_concurrency", "name": "Measured Concurrency", "unit": "count", "higher_is_better": None},
            {"kpi_id": "rhaiis_completed_requests", "name": "Completed Requests", "unit": "count", "higher_is_better": True},
            {"kpi_id": "rhaiis_failed_requests", "name": "Failed Requests", "unit": "count", "higher_is_better": False},
            {"kpi_id": "rhaiis_ttft_median", "name": "TTFT (Median)", "unit": "s", "higher_is_better": False},
            {"kpi_id": "rhaiis_ttft_p95", "name": "TTFT (P95)", "unit": "s", "higher_is_better": False},
            {"kpi_id": "rhaiis_ttft_p99", "name": "TTFT (P99)", "unit": "s", "higher_is_better": False},
            {"kpi_id": "rhaiis_tpot_median", "name": "TPOT (Median)", "unit": "s", "higher_is_better": False},
            {"kpi_id": "rhaiis_tpot_p95", "name": "TPOT (P95)", "unit": "s", "higher_is_better": False},
            {"kpi_id": "rhaiis_tpot_p99", "name": "TPOT (P99)", "unit": "s", "higher_is_better": False},
            {"kpi_id": "rhaiis_itl_median", "name": "ITL (Median)", "unit": "s", "higher_is_better": False},
            {"kpi_id": "rhaiis_itl_p95", "name": "ITL (P95)", "unit": "s", "higher_is_better": False},
            {"kpi_id": "rhaiis_itl_p99", "name": "ITL (P99)", "unit": "s", "higher_is_better": False},
            {"kpi_id": "rhaiis_request_latency_median", "name": "Request Latency (Median)", "unit": "s", "higher_is_better": False},
            {"kpi_id": "rhaiis_request_latency_p95", "name": "Request Latency (P95)", "unit": "s", "higher_is_better": False},
            {"kpi_id": "rhaiis_prompt_token_count_mean", "name": "Prompt Token Count (Mean)", "unit": "tokens", "higher_is_better": None},
            {"kpi_id": "rhaiis_duration", "name": "Benchmark Duration", "unit": "s", "higher_is_better": None},
        ]

    @staticmethod
    def compute_kpis(model: UnifiedRunModel) -> list[dict[str, Any]]:
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        out: list[dict[str, Any]] = []

        # Mapping from performance_curves keys to KPI IDs
        curve_kpi_mappings = [
            ("rhaiis_output_tok_per_sec", "output_tokens_per_second", "tokens/s", True),
            ("rhaiis_total_tok_per_sec", "tokens_per_second", "tokens/s", True),
            ("rhaiis_measured_concurrency", "request_concurrency", "count", None),
            ("rhaiis_completed_requests", "completed_requests", "count", True),
            ("rhaiis_failed_requests", "failed_requests", "count", False),
            ("rhaiis_ttft_median", "ttft_median", "s", False),
            ("rhaiis_ttft_p95", "ttft_p95", "s", False),
            ("rhaiis_ttft_p99", "ttft_p99", "s", False),
            ("rhaiis_tpot_median", "tpot_median", "s", False),
            ("rhaiis_tpot_p95", "tpot_p95", "s", False),
            ("rhaiis_tpot_p99", "tpot_p99", "s", False),
            ("rhaiis_itl_median", "itl_median", "s", False),
            ("rhaiis_itl_p95", "itl_p95", "s", False),
            ("rhaiis_itl_p99", "itl_p99", "s", False),
            ("rhaiis_request_latency_median", "request_latency_median", "s", False),
            ("rhaiis_request_latency_p95", "request_latency_p95", "s", False),
        ]

        for r in model.unified_result_records:
            if not r.run_identity.get("guidellm"):
                continue
            if r.metrics.get("no_benchmarks_found"):
                continue

            base_labels = {**r.distinguishing_labels}
            curves = r.metrics.get("performance_curves", {})
            request_rates = r.metrics.get("request_rate", [])

            if not curves or not request_rates:
                # Fallback: emit scalar KPIs from top-level metrics
                _emit_scalar_kpis(out, r, base_labels, ts, model.plugin_module)
                continue

            # Emit per-rate-point KPIs from performance curves
            for idx, rate in enumerate(request_rates):
                rate_labels = {
                    **base_labels,
                    "rate_index": str(idx),
                    "intended_concurrency": str(rate),
                }

                for kpi_id, curve_key, unit, higher_is_better in curve_kpi_mappings:
                    curve_values = curves.get(curve_key, [])
                    if idx >= len(curve_values):
                        continue
                    raw_value = curve_values[idx]
                    try:
                        value = float(raw_value) if raw_value is not None else None
                    except (TypeError, ValueError):
                        value = None
                    if value is None:
                        continue

                    labels = {**rate_labels}
                    if higher_is_better is not None:
                        labels["higher_is_better"] = higher_is_better

                    out.append({
                        "schema_version": "1",
                        "kpi_id": kpi_id,
                        "value": value,
                        "unit": unit,
                        "run_path": r.test_base_path,
                        "timestamp": ts,
                        "labels": labels,
                        "source": {
                            "test_base_path": r.test_base_path,
                            "plugin_module": model.plugin_module,
                        },
                    })

            # Also emit scalar KPIs that don't come from curves
            for kpi_id, metric_key, unit, higher_is_better in [
                ("rhaiis_prompt_token_count_mean", "prompt_token_count_mean", "tokens", None),
                ("rhaiis_duration", "duration", "s", None),
                ("rhaiis_measured_rps", "request_rate", "req/s", True),
            ]:
                raw_value = r.metrics.get(metric_key)
                # request_rate is a list; skip it for scalar emission
                if isinstance(raw_value, list):
                    continue
                try:
                    value = float(raw_value) if raw_value is not None else None
                except (TypeError, ValueError):
                    value = None
                if value is None:
                    continue

                labels = {**base_labels}
                if higher_is_better is not None:
                    labels["higher_is_better"] = higher_is_better

                out.append({
                    "schema_version": "1",
                    "kpi_id": kpi_id,
                    "value": value,
                    "unit": unit,
                    "run_path": r.test_base_path,
                    "timestamp": ts,
                    "labels": labels,
                    "source": {
                        "test_base_path": r.test_base_path,
                        "plugin_module": model.plugin_module,
                    },
                })

        return out


def _emit_scalar_kpis(
    out: list[dict[str, Any]],
    r,
    base_labels: dict[str, Any],
    ts: str,
    plugin_module: str,
) -> None:
    """Fallback: emit KPIs from top-level scalar metrics when no curves are available."""
    scalar_mappings = [
        ("rhaiis_output_tok_per_sec", "output_tokens_per_second", "tokens/s", True),
        ("rhaiis_total_tok_per_sec", "tokens_per_second", "tokens/s", True),
        ("rhaiis_measured_concurrency", "request_concurrency", "count", None),
        ("rhaiis_completed_requests", "completed_requests", "count", True),
        ("rhaiis_failed_requests", "failed_requests", "count", False),
        ("rhaiis_ttft_median", "ttft_median", "s", False),
        ("rhaiis_ttft_p95", "ttft_p95", "s", False),
        ("rhaiis_ttft_p99", "ttft_p99", "s", False),
        ("rhaiis_tpot_median", "tpot_median", "s", False),
        ("rhaiis_tpot_p95", "tpot_p95", "s", False),
        ("rhaiis_tpot_p99", "tpot_p99", "s", False),
        ("rhaiis_itl_median", "itl_median", "s", False),
        ("rhaiis_itl_p95", "itl_p95", "s", False),
        ("rhaiis_itl_p99", "itl_p99", "s", False),
        ("rhaiis_request_latency_median", "request_latency_median", "s", False),
        ("rhaiis_request_latency_p95", "request_latency_p95", "s", False),
        ("rhaiis_prompt_token_count_mean", "prompt_token_count_mean", "tokens", None),
        ("rhaiis_duration", "duration", "s", None),
    ]
    for kpi_id, metric_key, unit, higher_is_better in scalar_mappings:
        raw_value = r.metrics.get(metric_key)
        if isinstance(raw_value, list):
            continue
        try:
            value = float(raw_value) if raw_value is not None else None
        except (TypeError, ValueError):
            value = None
        if value is None:
            continue

        labels = {**base_labels}
        if higher_is_better is not None:
            labels["higher_is_better"] = higher_is_better

        out.append({
            "schema_version": "1",
            "kpi_id": kpi_id,
            "value": value,
            "unit": unit,
            "run_path": r.test_base_path,
            "timestamp": ts,
            "labels": labels,
            "source": {
                "test_base_path": r.test_base_path,
                "plugin_module": plugin_module,
            },
        })
