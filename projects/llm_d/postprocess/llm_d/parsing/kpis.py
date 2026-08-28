"""KPI handlers for LLM_D."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from projects.caliper.engine.kpi import (
    build_catalog_from_functions,
    get_kpi_functions,
    is_2d_kpi,
)
from projects.caliper.engine.model import UnifiedRunModel


class GuideLLMKpiHandler:
    """Handles KPI catalog and computation for GuideLLM benchmarks."""

    # Define custom label extractor for GuideLLM with fallbacks for missing kpi_labels
    @staticmethod
    def _extract_labels(record) -> dict[str, Any]:
        """Extract labels from record with fallbacks for missing kpi_labels."""

        from projects.caliper.engine.kpi.decorators import _extract_value_by_path

        labels = {}

        # Extract artifact-based labels (graceful handling)
        artifact_fields = {
            "product_version": "metrics.product_version",
            "cluster": "metrics.cluster",
            "deployment_profile": "metrics.deployment_profile",
            "model_name": "metrics.model_name",
            "guidellm_loadshape": "metrics.benchmark_key",
        }

        for label_key, path in artifact_fields.items():
            value = _extract_value_by_path(record, path)
            if value is not None:
                labels[label_key] = str(value)

        labels |= record.metrics.get("kpi_labels", {}) or {}

        return labels

    LABEL_EXTRACTOR = type("TestLabelExtractor", (), {"extract": _extract_labels})()

    # Metadata fields to include in KPI records but not as labels
    @staticmethod
    def extract_metadata(record) -> dict[str, Any]:
        """Extract metadata fields for KPI records."""
        config = record.metrics.get("configuration", {})
        return {
            "configuration": config,
            "run_path": record.test_base_path,
        }

    @staticmethod
    def get_catalog() -> list[dict[str, Any]]:
        """
        Return the KPI catalog for GuideLLM metrics.

        Returns:
            List of KPI definitions
        """
        # Import the module containing the KPI functions
        from projects.guidellm.postprocess.guidellm.parsing import kpis as guidellm_kpis

        return build_catalog_from_functions(guidellm_kpis)

    @staticmethod
    def compute_kpis(
        model: UnifiedRunModel,
    ) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], dict[str, Any]]:
        """
        Compute KPI values from the unified model.

        Args:
            model: Unified model containing parsed test results

        Returns:
            List of KPI records, or tuple of (KPI records, status details) if warnings present
        """
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        out: list[dict[str, Any]] = []

        # Import the module containing the KPI functions
        from projects.guidellm.postprocess.guidellm.parsing import kpis as guidellm_kpis

        kpi_functions = get_kpi_functions(guidellm_kpis)

        # Filter valid records
        valid_records = [
            r
            for r in model.unified_result_records
            if r.run_identity.get("guidellm") and not r.metrics.get("no_benchmarks_found")
        ]

        if not valid_records:
            return out

        # Check for unknown gpu_type in records
        unknown_gpu_records = []
        for r in valid_records:
            test_condition_labels = GuideLLMKpiHandler.LABEL_EXTRACTOR.extract(r)
            gpu_type = test_condition_labels.get("gpu_type")
            if gpu_type and gpu_type.lower() == "unknown":
                unknown_gpu_records.append(r.test_base_path)

        # If unknown GPU types found, return empty KPIs with error status
        if unknown_gpu_records:
            error_msg = (
                f"Found gpu_type='unknown' in {len(unknown_gpu_records)} test paths: "
                f"{', '.join(unknown_gpu_records[:3])}{'...' if len(unknown_gpu_records) > 3 else ''}. "
                "Unknown GPU types prevent reliable KPI analysis. "
                "Please ensure GPU detection is working correctly or set gpu_type manually in test labels."
            )

            status_details = {
                "status": "failed",
                "success": False,
                "message": error_msg,
                "warnings": [],
            }

            return [], status_details

        # Group records by test path for 2D KPIs (same test, different rates)
        from collections import defaultdict

        records_by_test = defaultdict(list)
        for r in valid_records:
            records_by_test[r.test_base_path].append(r)

        # Generate scalar KPIs for each record
        for r in valid_records:
            test_condition_labels = GuideLLMKpiHandler.LABEL_EXTRACTOR.extract(r)
            metadata_fields = GuideLLMKpiHandler.extract_metadata(r)

            # Compute scalar KPIs only
            for kpi_id, kpi_func in kpi_functions.items():
                # Skip 2D KPIs for individual records - they'll be handled separately
                if is_2d_kpi(kpi_func):
                    continue

                try:
                    value = kpi_func(r)
                except (TypeError, ValueError, KeyError):
                    value = None  # None for missing/failed scalar KPIs

                # Skip KPIs with null values
                if value is None:
                    continue

                # Use only extracted KPI labels, not test labels
                all_labels = {
                    **test_condition_labels,
                }

                kpi_record = {
                    "schema_version": "1",
                    "kpi_id": kpi_id,
                    "value": value,
                    "unit": kpi_func._kpi_unit,
                    "run_id": r.test_base_path,
                    "timestamp": ts,
                    "labels": all_labels,
                    "metadata": metadata_fields,
                    "source": {
                        "test_base_path": r.test_base_path,
                        "plugin_module": model.plugin_module,
                    },
                    "is_2d": False,
                }

                out.append(kpi_record)

        # Generate 2D curve KPIs for records that have performance curves
        for r in valid_records:
            # Check if this record has performance curves (indicating it's aggregated data)
            curves = r.metrics.get("performance_curves", {})
            request_rates = r.metrics.get("request_rate", [])

            # Only generate 2D KPIs if we have performance curves with data
            if not curves or not request_rates:
                continue

            kpi_labels = GuideLLMKpiHandler.LABEL_EXTRACTOR.extract(r)
            metadata_fields = GuideLLMKpiHandler.extract_metadata(r)

            # Generate 2D KPIs from performance curves
            for kpi_id, kpi_func in kpi_functions.items():
                if not is_2d_kpi(kpi_func):
                    continue

                try:
                    # Pass the single record with performance curves to the 2D KPI function
                    value = kpi_func(r)
                except (TypeError, ValueError, KeyError):
                    value = []  # Empty list for failed 2D KPIs

                # Skip 2D KPIs with empty or null values
                if not value or value is None:
                    continue

                kpi_record = {
                    "schema_version": "1",
                    "kpi_id": kpi_id,
                    "value": value,
                    "unit": kpi_func._kpi_unit,
                    "run_id": r.test_base_path,
                    "timestamp": ts,
                    "labels": kpi_labels,
                    "metadata": metadata_fields,
                    "source": {
                        "test_base_path": r.test_base_path,
                        "plugin_module": model.plugin_module,
                    },
                    "is_2d": True,
                    "x_unit": kpi_func._kpi_x_unit,
                    "x_help": kpi_func._kpi_x_help,
                    "y_unit": getattr(kpi_func, "_kpi_y_unit", None) or kpi_func._kpi_unit,
                    "y_help": getattr(kpi_func, "_kpi_y_help", None) or kpi_func._kpi_help,
                }

                out.append(kpi_record)

        return out
