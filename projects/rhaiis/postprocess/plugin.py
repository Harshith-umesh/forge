from __future__ import annotations

import csv
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

from projects.caliper.engine.model import (
    ParseResult,
    PostProcessingPlugin,
    TestBaseNode,
    UnifiedRunModel,
)

from .kpis import RhaiisKpiHandler
from .parser import RhaiisParser

logger = logging.getLogger(__name__)

# CSV columns whose KPI values are in seconds but the dashboard expects milliseconds.
# GuideLLM parser converts `*_ms` metrics to seconds; the old CSV pipeline kept them in ms.
_SECONDS_TO_MS_COLUMNS = frozenset(
    {
        "ttft_median",
        "ttft_p95",
        "ttft_p99",
        "ttft_p1",
        "ttft_p999",
        "ttft_mean",
        "tpot_median",
        "tpot_p95",
        "tpot_p99",
        "tpot_p1",
        "tpot_p999",
        "itl_median",
        "itl_p95",
        "itl_p99",
        "itl_p1",
        "itl_p999",
        "itl_mean",
    }
)

# KPI ID → dashboard CSV column name mapping (complete)
_KPI_TO_CSV_COLUMN = {
    "rhaiis_output_tok_per_sec": "output_tok/sec",
    "rhaiis_total_tok_per_sec": "total_tok/sec",
    "rhaiis_measured_concurrency": "measured concurrency",
    "rhaiis_measured_rps": "measured rps",
    "rhaiis_intended_concurrency": "intended concurrency",
    "rhaiis_completed_requests": "successful_requests",
    "rhaiis_failed_requests": "errored_requests",
    "rhaiis_ttft_median": "ttft_median",
    "rhaiis_ttft_p95": "ttft_p95",
    "rhaiis_ttft_p99": "ttft_p99",
    "rhaiis_ttft_p1": "ttft_p1",
    "rhaiis_ttft_p999": "ttft_p999",
    "rhaiis_ttft_mean": "ttft_mean",
    "rhaiis_tpot_median": "tpot_median",
    "rhaiis_tpot_p95": "tpot_p95",
    "rhaiis_tpot_p99": "tpot_p99",
    "rhaiis_tpot_p1": "tpot_p1",
    "rhaiis_tpot_p999": "tpot_p999",
    "rhaiis_itl_median": "itl_median",
    "rhaiis_itl_p95": "itl_p95",
    "rhaiis_itl_p99": "itl_p99",
    "rhaiis_itl_p1": "itl_p1",
    "rhaiis_itl_p999": "itl_p999",
    "rhaiis_itl_mean": "itl_mean",
    "rhaiis_request_latency_median": "request_latency_median",
    "rhaiis_request_latency_min": "request_latency_min",
    "rhaiis_request_latency_max": "request_latency_max",
    "rhaiis_prompt_token_count_mean": "prompt_token_count_mean",
    "rhaiis_prompt_token_count_p99": "prompt_token_count_p99",
    "rhaiis_output_token_count_mean": "output_token_count_mean",
    "rhaiis_output_token_count_p99": "output_token_count_p99",
}


class RhaiisPlugin(PostProcessingPlugin):
    def __init__(self) -> None:
        self.parser = RhaiisParser()
        self.kpi_handler = RhaiisKpiHandler()

    def parse(self, nodes: list[TestBaseNode]) -> ParseResult:
        return self.parser.parse(nodes)

    def get_available_reports(self) -> dict[str, dict[str, str]]:
        return {}

    def get_available_reports_by_type(self) -> dict[str, dict[str, str]]:
        return {"reports": {}, "plots": {}}

    def get_reports_only(self) -> dict[str, str]:
        return {}

    def get_plots_only(self) -> dict[str, str]:
        return {}

    def visualize(
        self,
        model: UnifiedRunModel,
        output_dir: Path,
        report_ids: list[str] | None,
        group_id: str | None,
        visualize_config: dict[str, Any] | None,
    ) -> list[str]:
        return []

    def kpi_catalog(self) -> list[dict[str, Any]]:
        return self.kpi_handler.get_catalog()

    def compute_kpis(self, model: UnifiedRunModel) -> list[dict[str, Any]]:
        return self.kpi_handler.compute_kpis(model)

    def export_kpis_to_csv(
        self,
        kpi_records: list[dict[str, Any]],
        output_path: Path,
        include_header_comments: bool = True,
    ) -> str:
        """Export KPI records to dashboard-format CSV.

        Groups per-rate-point KPIs by (run_path, rate_index) and pivots them
        into rows matching the RHAIIS dashboard CSV schema with all fields
        populated.
        """
        from projects.rhaiis.postprocess.csv_export import FIELDNAMES

        # Group KPIs by (run_path, rate_index)
        groups: dict[tuple[str, str], dict[str, Any]] = defaultdict(dict)
        group_labels: dict[tuple[str, str], dict[str, Any]] = {}

        for kpi in kpi_records:
            labels = kpi.get("labels", {})
            run_path = kpi.get("run_path", "")
            rate_index = labels.get("rate_index", "0")
            key = (run_path, rate_index)

            csv_col = _KPI_TO_CSV_COLUMN.get(kpi.get("kpi_id", ""))
            if csv_col:
                groups[key][csv_col] = kpi.get("value")

            # Capture the richest set of labels for this group
            if key not in group_labels or len(labels) > len(group_labels[key]):
                group_labels[key] = labels

        # Build CSV rows
        rows = []
        for key in sorted(groups):
            metrics = groups[key]
            labels = group_labels.get(key, {})

            acc = labels.get("accelerator", "").upper()
            cluster_tag = labels.get("cluster_tag", "")
            model_id = labels.get("hf_model_id", "")
            tp = labels.get("tensor_parallel_size", "1")
            version = labels.get("version", "")

            run_name = (
                f"{acc}-{cluster_tag}-{model_id}-{tp}" if cluster_tag else f"{acc}-{model_id}-{tp}"
            )

            row: dict[str, Any] = dict.fromkeys(FIELDNAMES, "")
            # Populate all metric columns from KPI values
            row.update(metrics)
            # Convert latency columns from seconds back to ms for dashboard compat
            for col in _SECONDS_TO_MS_COLUMNS:
                val = row.get(col)
                if val not in ("", None):
                    try:
                        row[col] = float(val) * 1000.0
                    except (TypeError, ValueError):
                        pass
            # Populate metadata columns from labels
            row["run"] = run_name
            row["accelerator"] = acc
            row["model"] = model_id
            row["version"] = version
            row["TP"] = tp
            row["prompt toks"] = labels.get("prompt_toks", "")
            row["output toks"] = labels.get("output_toks", "")
            # intended concurrency is populated from the rhaiis_intended_concurrency KPI;
            # only fall back to labels if the KPI didn't set it
            if not row.get("intended concurrency"):
                row["intended concurrency"] = labels.get("intended_concurrency", "")
            row["image_tag"] = labels.get("image_tag", "")
            row["runtime_args"] = labels.get("runtime_args", "")
            row["uuid"] = labels.get("run_uuid", "")
            row["guidellm_start_time_ms"] = labels.get("guidellm_start_time_ms", "")
            row["guidellm_end_time_ms"] = labels.get("guidellm_end_time_ms", "")
            row["guidellm_version"] = labels.get("guidellm_version", "")
            rows.append(row)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

        logger.info("Exported %d dashboard CSV rows to %s", len(rows), output_path)
        return str(output_path)

    def build_ai_data_payload(self, model: UnifiedRunModel) -> dict[str, Any]:
        return {}


def get_plugin() -> PostProcessingPlugin:
    return RhaiisPlugin()
