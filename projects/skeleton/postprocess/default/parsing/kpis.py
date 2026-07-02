"""KPI definitions and computation for Skeleton Caliper plugin."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from projects.caliper.engine.model import UnifiedRunModel


class SkeletonKpiHandler:
    """Handles KPI catalog and computation for skeleton project."""

    @staticmethod
    def get_catalog() -> list[dict[str, Any]]:
        """
        Return the KPI catalog for skeleton metrics.

        Returns:
            List of KPI definitions
        """
        return [
            {
                "kpi_id": "skeleton_throughput_rps",
                "name": "Skeleton throughput",
                "unit": "req/s",
                "higher_is_better": True,
            },
            {
                "kpi_id": "skeleton_latency_ms",
                "name": "Skeleton latency",
                "unit": "ms",
                "higher_is_better": False,
            },
        ]

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

        for r in model.unified_result_records:
            tp_raw = r.metrics.get("throughput")
            lat_raw = r.metrics.get("latency_ms")

            # Convert throughput to float
            try:
                tp = float(tp_raw) if tp_raw is not None else None
            except (TypeError, ValueError):
                tp = None

            # Convert latency to float
            try:
                lat = float(lat_raw) if lat_raw is not None else None
            except (TypeError, ValueError):
                lat = None

            base_labels = {**r.distinguishing_labels}

            # Add throughput KPI
            out.append(
                {
                    "schema_version": "1",
                    "kpi_id": "skeleton_throughput_rps",
                    "value": tp,
                    "unit": "req/s",
                    "run_id": r.test_base_path,
                    "timestamp": ts,
                    "labels": {**base_labels, "higher_is_better": True},
                    "source": {
                        "test_base_path": r.test_base_path,
                        "plugin_module": model.plugin_module,
                    },
                }
            )

            # Add latency KPI
            out.append(
                {
                    "schema_version": "1",
                    "kpi_id": "skeleton_latency_ms",
                    "value": lat,
                    "unit": "ms",
                    "run_id": r.test_base_path,
                    "timestamp": ts,
                    "labels": {**base_labels, "higher_is_better": False},
                    "source": {
                        "test_base_path": r.test_base_path,
                        "plugin_module": model.plugin_module,
                    },
                }
            )

        return out
