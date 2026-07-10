"""Generate consolidated_dashboard.csv from PSAP JSON files.

Matches the CSV schema from model-furnace's visualization/parse_furnace_results.py.
"""

from __future__ import annotations

import csv
import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

FIELDNAMES = [
    "run", "accelerator", "model", "version", "prompt toks", "output toks", "TP",
    "measured concurrency", "intended concurrency", "measured rps",
    "output_tok/sec", "total_tok/sec", "prompt_token_count_mean",
    "prompt_token_count_p99", "output_token_count_mean",
    "output_token_count_p99", "ttft_median", "ttft_p95", "ttft_p1", "ttft_p999",
    "tpot_median", "tpot_p95", "tpot_p99", "tpot_p999", "tpot_p1",
    "itl_median", "itl_p95", "itl_p999", "itl_p1",
    "request_latency_median", "request_latency_min", "request_latency_max",
    "successful_requests", "errored_requests", "uuid",
    "ttft_mean", "ttft_p99", "itl_mean", "itl_p99", "runtime_args",
    "guidellm_start_time_ms", "guidellm_end_time_ms", "image_tag", "guidellm_version",
]


def find_psap_files(artifact_dir: Path) -> list[Path]:
    """Glob for PSAP_perf*.json files in artifact_dir (recursive)."""
    return sorted(artifact_dir.rglob("PSAP_perf*.json"))


def generate_dashboard_csv(psap_json_path: Path, version: str, output_path: Path) -> Path:
    """Read a PSAP JSON file and produce a dashboard CSV.

    Args:
        psap_json_path: Path to the PSAP payload JSON file.
        version: Version string for the CSV rows.
        output_path: Where to write the CSV.

    Returns:
        Path to the generated CSV file.
    """
    with open(psap_json_path, encoding="utf-8") as f:
        payload = json.load(f)

    model = payload.get("model", "")
    accelerator = payload.get("accelerator_type", "")
    tp = payload.get("accelerator_count", 1)
    image_tag = payload.get("container_image_tag", "")
    runtime_args = json.dumps(payload.get("inference_server_args", {}))
    guidellm_start_ms = payload.get("guidellm_start_time_ms")
    guidellm_end_ms = payload.get("guidellm_end_time_ms")

    report = payload.get("report", {})
    guidellm_version = report.get("metadata", {}).get("guidellm_version", "")
    benchmarks = report.get("benchmarks", [])

    data_str = ""
    args = report.get("args", {})
    if isinstance(args, dict):
        data_list = args.get("data", [])
        if data_list:
            data_str = data_list[0] if isinstance(data_list, list) else str(data_list)

    tokens = dict(re.findall(r"(\w+)=([\d.]+)", data_str))
    prompt_toks = tokens.get("prompt_tokens", "")
    output_toks = tokens.get("output_tokens", "")

    rows = []
    for bench in benchmarks:
        metrics = bench.get("metrics", {})
        row = _extract_row(
            metrics=metrics,
            model=model,
            accelerator=accelerator,
            version=version,
            tp=tp,
            prompt_toks=prompt_toks,
            output_toks=output_toks,
            image_tag=image_tag,
            runtime_args=runtime_args,
            guidellm_start_ms=guidellm_start_ms,
            guidellm_end_ms=guidellm_end_ms,
            guidellm_version=guidellm_version,
        )
        rows.append(row)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    logger.info("Generated dashboard CSV with %d rows: %s", len(rows), output_path)
    return output_path


def _extract_row(
    *,
    metrics: dict,
    model: str,
    accelerator: str,
    version: str,
    tp: int,
    prompt_toks: str,
    output_toks: str,
    image_tag: str,
    runtime_args: str,
    guidellm_start_ms,
    guidellm_end_ms,
    guidellm_version: str,
) -> dict:
    def _pct(metric_name: str, pct: str) -> float:
        return float(
            metrics.get(metric_name, {})
            .get("successful", {})
            .get("percentiles", {})
            .get(pct, 0)
        )

    def _stat(metric_name: str, stat: str) -> float:
        return float(metrics.get(metric_name, {}).get("successful", {}).get(stat, 0))

    request_totals = metrics.get("request_totals", {})
    successful = int(request_totals.get("successful", 0))
    errored = int(request_totals.get("errored", 0))

    concurrency_mean = _stat("request_concurrency", "mean")
    intended_concurrency = _stat("request_concurrency", "max")

    output_tok_sec = _stat("output_token_throughput", "mean")
    total_tok_sec = _stat("total_token_throughput", "mean")
    measured_rps = _stat("request_rate", "mean")

    return {
        "run": "",
        "accelerator": accelerator,
        "model": model,
        "version": version,
        "prompt toks": prompt_toks,
        "output toks": output_toks,
        "TP": tp,
        "measured concurrency": concurrency_mean,
        "intended concurrency": intended_concurrency,
        "measured rps": measured_rps,
        "output_tok/sec": output_tok_sec,
        "total_tok/sec": total_tok_sec,
        "prompt_token_count_mean": _stat("prompt_token_count", "mean"),
        "prompt_token_count_p99": _pct("prompt_token_count", "p99"),
        "output_token_count_mean": _stat("output_token_count", "mean"),
        "output_token_count_p99": _pct("output_token_count", "p99"),
        "ttft_median": _pct("time_to_first_token_ms", "p50") / 1000.0,
        "ttft_p95": _pct("time_to_first_token_ms", "p95") / 1000.0,
        "ttft_p1": _pct("time_to_first_token_ms", "p1") / 1000.0,
        "ttft_p999": _pct("time_to_first_token_ms", "p999") / 1000.0,
        "tpot_median": _pct("time_per_output_token_ms", "p50") / 1000.0,
        "tpot_p95": _pct("time_per_output_token_ms", "p95") / 1000.0,
        "tpot_p99": _pct("time_per_output_token_ms", "p99") / 1000.0,
        "tpot_p999": _pct("time_per_output_token_ms", "p999") / 1000.0,
        "tpot_p1": _pct("time_per_output_token_ms", "p1") / 1000.0,
        "itl_median": _pct("inter_token_latency_ms", "p50") / 1000.0,
        "itl_p95": _pct("inter_token_latency_ms", "p95") / 1000.0,
        "itl_p999": _pct("inter_token_latency_ms", "p999") / 1000.0,
        "itl_p1": _pct("inter_token_latency_ms", "p1") / 1000.0,
        "request_latency_median": _pct("request_latency_ms", "p50") / 1000.0,
        "request_latency_min": _stat("request_latency_ms", "min") / 1000.0,
        "request_latency_max": _stat("request_latency_ms", "max") / 1000.0,
        "successful_requests": successful,
        "errored_requests": errored,
        "uuid": "",
        "ttft_mean": _stat("time_to_first_token_ms", "mean") / 1000.0,
        "ttft_p99": _pct("time_to_first_token_ms", "p99") / 1000.0,
        "itl_mean": _stat("inter_token_latency_ms", "mean") / 1000.0,
        "itl_p99": _pct("inter_token_latency_ms", "p99") / 1000.0,
        "runtime_args": runtime_args,
        "guidellm_start_time_ms": guidellm_start_ms or "",
        "guidellm_end_time_ms": guidellm_end_ms or "",
        "image_tag": image_tag,
        "guidellm_version": guidellm_version,
    }
