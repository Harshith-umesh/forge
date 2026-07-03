"""Regression analysis vs baseline KPI set."""

from __future__ import annotations

from typing import Any

from projects.caliper.engine.kpi.import_export import load_kpis_json
from projects.caliper.engine.kpi.rules import DEFAULT_RULE
from projects.caliper.engine.model import RegressionFinding


def _direction_worse(
    current: float,
    baseline: float,
    *,
    higher_is_better: bool,
) -> bool:
    if higher_is_better:
        return current < baseline
    return current > baseline


def run_analyze(
    *,
    current_path: Any,
    baseline_path: Any,
    output_path: Any,
) -> list[RegressionFinding]:
    current = load_kpis_json(current_path)
    baseline = load_kpis_json(baseline_path)
    base_by_id = {b["kpi_id"]: b for b in baseline}
    findings: list[RegressionFinding] = []
    rule = DEFAULT_RULE
    for c in current:
        kid = c["kpi_id"]
        if kid not in base_by_id:
            continue
        b = base_by_id[kid]
        try:
            cv = float(c["value"])
            bv = float(b["value"])
        except (TypeError, ValueError):
            continue
        higher = str(c.get("labels", {}).get("higher_is_better", "true")).lower() in (
            "1",
            "true",
            "yes",
        )
        worse = _direction_worse(cv, bv, higher_is_better=higher)
        rel = abs(cv - bv) / (abs(bv) + 1e-9)
        status = "ok"
        if worse and rel > rule.max_relative_regression:
            status = "regression"
        elif not worse and rel > rule.max_relative_regression:
            status = "improvement"
        findings.append(
            RegressionFinding(
                kpi_id=kid,
                current_value=cv,
                baseline_value=bv,
                direction="higher_better" if higher else "lower_better",
                status=status,
            )
        )
    import json  # noqa: PLC0415

    out = {
        "findings": [
            {
                "kpi_id": f.kpi_id,
                "current_value": f.current_value,
                "baseline_value": f.baseline_value,
                "direction": f.direction,
                "status": f.status,
            }
            for f in findings
        ]
    }
    output_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return findings
