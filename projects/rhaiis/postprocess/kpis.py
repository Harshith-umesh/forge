from __future__ import annotations

from typing import Any

from projects.caliper.engine.model import UnifiedRunModel
from projects.guidellm.postprocess.guidellm.dashboard import (
    compute_dashboard_kpis,
    dashboard_kpi_catalog,
)


class RhaiisKpiHandler:
    @staticmethod
    def get_catalog() -> list[dict[str, Any]]:
        return dashboard_kpi_catalog(prefix="rhaiis")

    @staticmethod
    def compute_kpis(model: UnifiedRunModel) -> list[dict[str, Any]]:
        return compute_dashboard_kpis(model, prefix="rhaiis")
