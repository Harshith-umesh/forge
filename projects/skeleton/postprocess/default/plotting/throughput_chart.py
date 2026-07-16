"""Throughput chart plot for Skeleton Caliper plugin."""

from __future__ import annotations

import logging
from pathlib import Path

import plotly.graph_objects as go

from projects.caliper.engine.model import UnifiedRunModel

logger = logging.getLogger(__name__)


class ThroughputChartPlot:
    """Generates a Plotly bar chart of throughput by scenario."""

    @staticmethod
    def generate(model: UnifiedRunModel, output_dir: Path) -> str:
        """
        Generate a throughput chart HTML report.

        Args:
            model: Unified model containing parsed test results
            output_dir: Directory to write the output file

        Returns:
            Path to the generated HTML file
        """
        logger.info("Generating throughput chart HTML report")
        logger.info(
            f"Processing {len(model.unified_result_records)} result records for throughput data"
        )

        xs: list[str] = []
        ys: list[float] = []

        for i, r in enumerate(model.unified_result_records):
            label = str(r.distinguishing_labels.get("scenario") or r.test_base_path)
            raw = r.metrics.get("throughput", 0)

            try:
                y = float(raw)
            except (TypeError, ValueError):
                logger.debug(
                    f"Record {i}: Could not convert throughput '{raw}' to float, using 0.0"
                )
                y = 0.0

            logger.debug(f"Record {i}: label='{label}', throughput={y}")
            xs.append(label)
            ys.append(y)

        logger.info(f"Collected throughput data for {len(xs)} scenarios")
        logger.info(f"Throughput range: {min(ys) if ys else 0} - {max(ys) if ys else 0}")

        logger.info("Creating Plotly bar chart")
        fig = go.Figure(data=[go.Bar(x=xs, y=ys)])
        fig.update_layout(
            title="Throughput by scenario",
            xaxis_title="Scenario",
            yaxis_title="Throughput",
        )

        from projects.caliper.postprocess.helpers.visualization_utils import write_full_page_html

        output_file = output_dir / "throughput_chart.html"
        logger.info(f"Writing throughput chart to: {output_file}")
        success = write_full_page_html(fig, str(output_file), "Throughput Chart")
        if success:
            file_size = output_file.stat().st_size
            logger.info(f"Throughput chart HTML written successfully ({file_size} bytes)")
            return str(output_file)
        else:
            logger.error(f"Failed to write throughput chart to {output_file}")
            return None
