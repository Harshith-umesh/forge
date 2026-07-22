from __future__ import annotations

import json
import logging
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
                extra = _extract_extra_metrics(node)
                merged_metrics = {**record.metrics, **extra}
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


def _extract_extra_metrics(node: TestBaseNode) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    benchmarks_files = [p for p in node.artifact_paths if p.name == "benchmarks.json"]
    if not benchmarks_files:
        return extra

    try:
        data = json.loads(benchmarks_files[0].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return extra

    benchmarks = data.get("benchmarks", [])
    if not benchmarks:
        return extra

    bench = benchmarks[0]
    metrics = bench.get("metrics", {})

    def _percentile(metric_name: str, pct: str, default: float = 0.0) -> float:
        return float(
            metrics.get(metric_name, {})
            .get("successful", {})
            .get("percentiles", {})
            .get(pct, default)
        )

    def _stat(metric_name: str, stat: str, default: float = 0.0) -> float:
        return float(metrics.get(metric_name, {}).get("successful", {}).get(stat, default))

    extra["ttft_p99"] = _percentile("time_to_first_token_ms", "p99") / 1000.0
    extra["tpot_p99"] = _percentile("time_per_output_token_ms", "p99") / 1000.0
    extra["itl_p99"] = _percentile("inter_token_latency_ms", "p99") / 1000.0

    request_totals = metrics.get("request_totals", {})
    extra["completed_requests"] = int(request_totals.get("successful", 0))
    extra["failed_requests"] = int(request_totals.get("errored", 0))

    extra["prompt_token_count_mean"] = _stat("prompt_token_count", "mean")

    concurrency = _stat("request_concurrency", "mean")
    if concurrency > 0:
        extra["request_concurrency"] = concurrency

    return extra


