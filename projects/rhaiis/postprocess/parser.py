from __future__ import annotations

import json
import logging
import re
from typing import Any

from projects.caliper.engine.model import (
    ParseResult,
    TestBaseNode,
    UnifiedResultRecord,
)
from projects.guidellm.postprocess.guidellm.parsing.parsers import (
    GuideLLMParser,
)

logger = logging.getLogger(__name__)


class RhaiisParser:
    """Extends GuideLLMParser with additional metrics for model-furnace parity."""

    def __init__(self) -> None:
        self._base_parser = GuideLLMParser()

    def parse(self, nodes: list[TestBaseNode]) -> ParseResult:
        base_result = self._base_parser.parse(nodes)

        enriched_records = []
        for record in base_result.records:
            if record.metrics.get("no_benchmarks_found"):
                enriched_records.append(record)
                continue

            node = _find_node_for_record(record, nodes)
            if node:
                extra_metrics, extra_curves = _extract_extra_metrics(node)
                merged_metrics = {**record.metrics, **extra_metrics}
                if extra_curves:
                    existing_curves = merged_metrics.get("performance_curves", {})
                    existing_curves.update(extra_curves)
                    merged_metrics["performance_curves"] = existing_curves
                enriched_records.append(
                    UnifiedResultRecord(
                        test_base_path=record.test_base_path,
                        distinguishing_labels=record.distinguishing_labels,
                        metrics=merged_metrics,
                        run_identity=record.run_identity,
                        parse_notes=record.parse_notes,
                    )
                )
            else:
                enriched_records.append(record)

        return ParseResult(records=enriched_records, warnings=base_result.warnings)


def _find_node_for_record(
    record: UnifiedResultRecord,
    nodes: list[TestBaseNode],
) -> TestBaseNode | None:
    for node in nodes:
        if str(node.test_path) == record.test_base_path:
            return node
    return None


def _bench_sort_key(bench: dict) -> float:
    """Sort key matching GuideLLMParser: requests_per_second.successful.mean."""
    return float(
        bench.get("metrics", {})
        .get("requests_per_second", {})
        .get("successful", {})
        .get("mean", 0)
    )


def _extract_extra_metrics(node: TestBaseNode) -> tuple[dict[str, Any], dict[str, list]]:
    """Return (scalar_metrics, extra_curves) extracted from raw benchmarks.json files."""
    extra: dict[str, Any] = {}
    benchmarks_files = sorted(
        [p for p in node.artifact_paths if p.name == "benchmarks.json"
         or (p.name.startswith("benchmarks-rate-") and p.suffix == ".json")],
        key=lambda p: p.name,
    )
    if not benchmarks_files:
        return extra, {}

    all_benchmarks: list[dict] = []
    report_metadata: dict[str, Any] = {}
    report_args: dict[str, Any] = {}

    for bf in benchmarks_files:
        try:
            data = json.loads(bf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        all_benchmarks.extend(data.get("benchmarks", []))
        if not report_metadata:
            report_metadata = data.get("metadata", {})
        if not report_args:
            report_args = data.get("args", {})

    if not all_benchmarks:
        return extra, {}

    # Sort by request_rate to match GuideLLMParser curve ordering
    all_benchmarks.sort(key=_bench_sort_key)

    # Report-level metadata
    extra["guidellm_version"] = report_metadata.get("guidellm_version", "")

    data_str = ""
    if isinstance(report_args, dict):
        data_list = report_args.get("data", [])
        if data_list:
            data_str = data_list[0] if isinstance(data_list, list) else str(data_list)
    tokens = dict(re.findall(r"(\w+)=([\d.]+)", data_str))
    extra["prompt_toks"] = int(float(tokens["prompt_tokens"])) if "prompt_tokens" in tokens else ""
    extra["output_toks"] = int(float(tokens["output_tokens"])) if "output_tokens" in tokens else ""

    start_times = []
    end_times = []
    for bench in all_benchmarks:
        sched = bench.get("scheduler_metrics", {})
        if "start_time" in sched:
            start_times.append(sched["start_time"])
        if "end_time" in sched:
            end_times.append(sched["end_time"])
    extra["guidellm_start_time_ms"] = int(min(start_times) * 1000) if start_times else ""
    extra["guidellm_end_time_ms"] = int(max(end_times) * 1000) if end_times else ""

    # Scalar extras from first benchmark (backward compat)
    bench0 = all_benchmarks[0]
    m0 = bench0.get("metrics", {})

    def _percentile0(metric_name: str, pct: str, default: float = 0.0) -> float:
        return float(
            m0.get(metric_name, {}).get("successful", {}).get("percentiles", {}).get(pct, default)
        )

    def _stat0(metric_name: str, stat: str, default: float = 0.0) -> float:
        return float(m0.get(metric_name, {}).get("successful", {}).get(stat, default))

    extra["ttft_p99"] = _percentile0("time_to_first_token_ms", "p99") / 1000.0
    extra["tpot_p99"] = _percentile0("time_per_output_token_ms", "p99") / 1000.0
    extra["itl_p99"] = _percentile0("inter_token_latency_ms", "p99") / 1000.0

    request_totals0 = m0.get("request_totals", {})
    extra["completed_requests"] = int(request_totals0.get("successful", 0))
    extra["failed_requests"] = int(request_totals0.get("errored", 0))
    extra["prompt_token_count_mean"] = _stat0("prompt_token_count", "mean")

    concurrency = _stat0("request_concurrency", "mean")
    if concurrency > 0:
        extra["request_concurrency"] = concurrency

    # Per-benchmark extra curves — indices align with GuideLLM's sorted curves
    extra_curves: dict[str, list] = {
        "ttft_p1": [],
        "ttft_p999": [],
        "ttft_mean": [],
        "tpot_p1": [],
        "tpot_p999": [],
        "itl_p1": [],
        "itl_p999": [],
        "itl_mean": [],
        "request_latency_min": [],
        "request_latency_max": [],
        "measured_rps": [],
        "prompt_token_count_mean": [],
        "prompt_token_count_p99": [],
        "output_token_count_mean": [],
        "output_token_count_p99": [],
        "output_tok_per_sec": [],
        "total_tok_per_sec": [],
        "successful_requests": [],
        "errored_requests": [],
        "intended_concurrency": [],
    }

    for bench in all_benchmarks:
        metrics = bench.get("metrics", {})
        strategy = bench.get("config", {}).get("strategy", {})

        def _pct(metric_name: str, pct: str, _m: dict = metrics):
            return _m.get(metric_name, {}).get("successful", {}).get("percentiles", {}).get(pct)

        def _pct_s(metric_name: str, pct: str):
            v = _pct(metric_name, pct)
            return v / 1000.0 if v is not None else None

        def _stat(metric_name: str, stat: str, _m: dict = metrics):
            return _m.get(metric_name, {}).get("successful", {}).get(stat)

        def _stat_s(metric_name: str, stat: str):
            v = _stat(metric_name, stat)
            return v / 1000.0 if v is not None else None

        def _total_stat(metric_name: str, stat: str, _m: dict = metrics):
            return _m.get(metric_name, {}).get("total", {}).get(stat)

        request_totals = metrics.get("request_totals", {})

        extra_curves["ttft_p1"].append(_pct_s("time_to_first_token_ms", "p01"))
        extra_curves["ttft_p999"].append(_pct_s("time_to_first_token_ms", "p999"))
        extra_curves["ttft_mean"].append(_stat_s("time_to_first_token_ms", "mean"))
        extra_curves["tpot_p1"].append(_pct_s("time_per_output_token_ms", "p01"))
        extra_curves["tpot_p999"].append(_pct_s("time_per_output_token_ms", "p999"))
        extra_curves["itl_p1"].append(_pct_s("inter_token_latency_ms", "p01"))
        extra_curves["itl_p999"].append(_pct_s("inter_token_latency_ms", "p999"))
        extra_curves["itl_mean"].append(_stat_s("inter_token_latency_ms", "mean"))
        extra_curves["request_latency_min"].append(_stat("request_latency", "min"))
        extra_curves["request_latency_max"].append(_stat("request_latency", "max"))
        extra_curves["measured_rps"].append(_stat("requests_per_second", "mean"))
        extra_curves["prompt_token_count_mean"].append(_stat("prompt_token_count", "mean"))
        extra_curves["prompt_token_count_p99"].append(_pct("prompt_token_count", "p99"))
        extra_curves["output_token_count_mean"].append(_stat("output_token_count", "mean"))
        extra_curves["output_token_count_p99"].append(_pct("output_token_count", "p99"))
        extra_curves["output_tok_per_sec"].append(_total_stat("output_tokens_per_second", "mean"))
        extra_curves["total_tok_per_sec"].append(_total_stat("tokens_per_second", "mean"))
        extra_curves["successful_requests"].append(request_totals.get("successful", 0))
        extra_curves["errored_requests"].append(request_totals.get("errored", 0))
        extra_curves["intended_concurrency"].append(strategy.get("streams"))

    return extra, extra_curves
