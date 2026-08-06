from __future__ import annotations

import json
from pathlib import Path

from projects.caliper.engine.model import TestBaseNode
from projects.guidellm.postprocess.guidellm.parsing.parsers import GuideLLMParser


def _write_benchmark_file(path: Path, streams: int) -> None:
    payload = {
        "args": {"rate": streams},
        "metadata": {"label": f"rate-{streams}"},
        "benchmarks": [
            {
                "config": {"strategy": {"type_": "concurrent", "streams": streams}},
                "scheduler": {"state": {"start_time": 0, "end_time": 10}},
                "metrics": {
                    "requests_per_second": {"successful": {"mean": 1.5}},
                    "input_tokens_per_second": {"successful": {"mean": 3.0}},
                    "output_tokens_per_second": {"successful": {"mean": 4.0}},
                    "request_latency": {
                        "successful": {"median": 100.0, "percentiles": {"p95": 120.0}}
                    },
                    "time_to_first_token_ms": {
                        "successful": {
                            "median": 10.0,
                            "percentiles": {
                                "p10": 8.0,
                                "p25": 9.0,
                                "p50": 10.0,
                                "p75": 11.0,
                                "p90": 12.0,
                                "p95": 13.0,
                            },
                        }
                    },
                    "inter_token_latency_ms": {
                        "successful": {
                            "median": 5.0,
                            "percentiles": {
                                "p10": 4.0,
                                "p25": 4.5,
                                "p50": 5.0,
                                "p75": 5.5,
                                "p90": 6.0,
                                "p95": 6.5,
                            },
                        }
                    },
                    "time_per_output_token_ms": {
                        "successful": {"median": 7.0, "percentiles": {"p95": 8.0}}
                    },
                },
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_parser_accepts_rate_split_benchmark_files(tmp_path: Path) -> None:
    test_dir = tmp_path / "run"
    test_dir.mkdir()
    file_a = test_dir / "benchmarks-rate-32.json"
    file_b = test_dir / "benchmarks-rate-64.json"
    _write_benchmark_file(file_a, 32)
    _write_benchmark_file(file_b, 64)

    parser = GuideLLMParser()
    node = TestBaseNode(
        directory=test_dir,
        test_labels={"labels": {"guidellm_loadshape": "multi-turn"}},
        artifact_paths=[file_b, file_a],
    )

    result = parser.parse([node])

    assert result.warnings == []
    assert len(result.records) == 1

    # Check that both rate configurations are present in the performance curves
    record = result.records[0]
    curves = record.metrics["performance_curves"]
    assert curves["intended_concurrency"] == [32, 64]
    assert record.run_identity == {"guidellm": True}
