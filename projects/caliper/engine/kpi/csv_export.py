"""Export KPIs to CSV format using plugin interface."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def export_kpis_to_csv(
    *,
    plugin: object,
    kpi_records: list[dict[str, Any]],
    output_path: Path,
    include_header_comments: bool = True,
) -> str:
    """
    Export KPI records to CSV format using the plugin's CSV export method.

    Args:
        plugin: PostProcessingPlugin instance with export_kpis_to_csv method
        kpi_records: List of KPI record dictionaries
        output_path: Path where to write the CSV file
        include_header_comments: Whether to include descriptive header comments

    Returns:
        Path to the generated CSV file as string

    Raises:
        AttributeError: If plugin doesn't have export_kpis_to_csv method
    """
    if not hasattr(plugin, "export_kpis_to_csv"):
        raise AttributeError(
            f"Plugin {plugin.__class__.__name__} does not implement export_kpis_to_csv method"
        )

    # Delegate to plugin-specific CSV export implementation
    result_path = plugin.export_kpis_to_csv(
        kpi_records=kpi_records,
        output_path=output_path,
        include_header_comments=include_header_comments,
    )

    return result_path
