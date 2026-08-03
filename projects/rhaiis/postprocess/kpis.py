from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from projects.caliper.engine.model import UnifiedRunModel

# All per-rate-point metrics we emit as KPIs.
# (kpi_id, performance_curves key, unit, higher_is_better)
_CURVE_KPI_MAPPINGS: list[tuple[str, str, str, bool | None]] = [
    # Throughput
    ("rhaiis_output_tok_per_sec", "output_tok_per_sec", "tokens/s", True),
    ("rhaiis_total_tok_per_sec", "total_tok_per_sec", "tokens/s", True),
    # Concurrency / rate
    ("rhaiis_measured_concurrency", "request_concurrency", "count", None),
    ("rhaiis_measured_rps", "measured_rps", "req/s", True),
    ("rhaiis_intended_concurrency", "intended_concurrency", "count", None),
    # Request counts
    ("rhaiis_completed_requests", "successful_requests", "count", True),
    ("rhaiis_failed_requests", "errored_requests", "count", False),
    # TTFT
    ("rhaiis_ttft_median", "ttft_median", "s", False),
    ("rhaiis_ttft_p95", "ttft_p95", "s", False),
    ("rhaiis_ttft_p99", "ttft_p99", "s", False),
    ("rhaiis_ttft_p1", "ttft_p1", "s", False),
    ("rhaiis_ttft_p999", "ttft_p999", "s", False),
    ("rhaiis_ttft_mean", "ttft_mean", "s", False),
    # TPOT
    ("rhaiis_tpot_median", "tpot_median", "s", False),
    ("rhaiis_tpot_p95", "tpot_p95", "s", False),
    ("rhaiis_tpot_p99", "tpot_p99", "s", False),
    ("rhaiis_tpot_p1", "tpot_p1", "s", False),
    ("rhaiis_tpot_p999", "tpot_p999", "s", False),
    # ITL
    ("rhaiis_itl_median", "itl_median", "s", False),
    ("rhaiis_itl_p95", "itl_p95", "s", False),
    ("rhaiis_itl_p99", "itl_p99", "s", False),
    ("rhaiis_itl_p1", "itl_p1", "s", False),
    ("rhaiis_itl_p999", "itl_p999", "s", False),
    ("rhaiis_itl_mean", "itl_mean", "s", False),
    # Request latency
    ("rhaiis_request_latency_median", "request_latency_median", "s", False),
    ("rhaiis_request_latency_min", "request_latency_min", "s", False),
    ("rhaiis_request_latency_max", "request_latency_max", "s", False),
    # Token counts
    ("rhaiis_prompt_token_count_mean", "prompt_token_count_mean", "tokens", None),
    ("rhaiis_prompt_token_count_p99", "prompt_token_count_p99", "tokens", None),
    ("rhaiis_output_token_count_mean", "output_token_count_mean", "tokens", None),
    ("rhaiis_output_token_count_p99", "output_token_count_p99", "tokens", None),
]


class RhaiisKpiHandler:
    @staticmethod
    def get_catalog() -> list[dict[str, Any]]:
        return [
            {"kpi_id": kpi_id, "name": kpi_id, "unit": unit, "higher_is_better": hib}
            for kpi_id, _, unit, hib in _CURVE_KPI_MAPPINGS
        ]

    @staticmethod
    def compute_kpis(model: UnifiedRunModel) -> list[dict[str, Any]]:
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        out: list[dict[str, Any]] = []

        for r in model.unified_result_records:
            if not r.run_identity.get("guidellm"):
                continue
            if r.metrics.get("no_benchmarks_found"):
                continue

            base_labels = {**r.distinguishing_labels}
            curves = r.metrics.get("performance_curves", {})
            request_rates = r.metrics.get("request_rate", [])

            # Report-level metadata — same for every rate point in this record
            meta_labels = {
                "guidellm_version": str(r.metrics.get("guidellm_version", "")),
                "prompt_toks": str(r.metrics.get("prompt_toks", "")),
                "output_toks": str(r.metrics.get("output_toks", "")),
                "guidellm_start_time_ms": str(r.metrics.get("guidellm_start_time_ms", "")),
                "guidellm_end_time_ms": str(r.metrics.get("guidellm_end_time_ms", "")),
            }

            if not curves or not request_rates:
                continue

            n_rates = len(request_rates)

            for idx in range(n_rates):
                rate_labels = {
                    **base_labels,
                    **meta_labels,
                    "rate_index": str(idx),
                }

                for kpi_id, curve_key, unit, higher_is_better in _CURVE_KPI_MAPPINGS:
                    curve_values = curves.get(curve_key, [])
                    if idx >= len(curve_values):
                        continue
                    raw_value = curve_values[idx]
                    if raw_value is None:
                        continue
                    try:
                        value = float(raw_value)
                    except (TypeError, ValueError):
                        continue

                    labels = {**rate_labels}
                    if higher_is_better is not None:
                        labels["higher_is_better"] = higher_is_better

                    out.append(
                        {
                            "schema_version": "1",
                            "kpi_id": kpi_id,
                            "value": value,
                            "unit": unit,
                            "run_id": r.test_base_path,
                            "run_path": r.test_base_path,
                            "timestamp": ts,
                            "labels": labels,
                            "source": {
                                "test_base_path": r.test_base_path,
                                "plugin_module": model.plugin_module,
                            },
                        }
                    )

        return out
