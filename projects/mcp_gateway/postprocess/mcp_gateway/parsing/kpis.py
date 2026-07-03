"""KPI definitions and computation for MCP Gateway Caliper plugin."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from projects.caliper.engine.model import UnifiedRunModel


class MCPGatewayKpiHandler:
    """Handles KPI catalog and computation for MCP Gateway benchmarks."""

    @staticmethod
    def get_catalog() -> list[dict[str, Any]]:
        return [
            {
                "kpi_id": "mcp_gw_requests_per_second",
                "name": "Request Rate",
                "unit": "req/s",
                "higher_is_better": True,
            },
            {
                "kpi_id": "mcp_gw_avg_response_time_ms",
                "name": "Average Response Time",
                "unit": "ms",
                "higher_is_better": False,
            },
            {
                "kpi_id": "mcp_gw_p50_ms",
                "name": "P50 Latency",
                "unit": "ms",
                "higher_is_better": False,
            },
            {
                "kpi_id": "mcp_gw_p95_ms",
                "name": "P95 Latency",
                "unit": "ms",
                "higher_is_better": False,
            },
            {
                "kpi_id": "mcp_gw_p99_ms",
                "name": "P99 Latency",
                "unit": "ms",
                "higher_is_better": False,
            },
            {
                "kpi_id": "mcp_gw_failure_rate",
                "name": "Failure Rate",
                "unit": "ratio",
                "higher_is_better": False,
            },
        ]

    @staticmethod
    def compute_kpis(model: UnifiedRunModel) -> list[dict[str, Any]]:
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        out: list[dict[str, Any]] = []

        kpi_mappings = [
            ("mcp_gw_requests_per_second", "requests_per_second", "req/s", True),
            ("mcp_gw_avg_response_time_ms", "avg_response_time_ms", "ms", False),
            ("mcp_gw_p50_ms", "p50_ms", "ms", False),
            ("mcp_gw_p95_ms", "p95_ms", "ms", False),
            ("mcp_gw_p99_ms", "p99_ms", "ms", False),
            ("mcp_gw_failure_rate", "failure_rate", "ratio", False),
        ]

        for r in model.unified_result_records:
            if not r.run_identity.get("mcp_gateway"):
                continue
            if r.metrics.get("no_stats_csv_found"):
                continue

            base_labels = {**r.distinguishing_labels}

            for kpi_id, metric_key, unit, higher_is_better in kpi_mappings:
                raw_value = r.metrics.get(metric_key)
                try:
                    value = float(raw_value) if raw_value is not None else None
                except (TypeError, ValueError):
                    value = None

                # Skip KPIs with null values
                if value is None:
                    continue

                out.append(
                    {
                        "schema_version": "1",
                        "kpi_id": kpi_id,
                        "value": value,
                        "unit": unit,
                        "run_id": r.test_base_path,
                        "timestamp": ts,
                        "labels": {**base_labels, "higher_is_better": higher_is_better},
                        "source": {
                            "test_base_path": r.test_base_path,
                            "plugin_module": model.plugin_module,
                        },
                    }
                )

        return out
