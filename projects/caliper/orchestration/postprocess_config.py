"""
Pydantic models for Caliper parse / visualize / KPI steps driven from ``caliper.postprocess``.
"""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CaliperOrchestrationParseSection(BaseModel):
    """``caliper.postprocess.parse``."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    no_cache: bool = False


class CaliperOrchestrationVisualizeSection(BaseModel):
    """``caliper.postprocess.visualize`` — same semantics as ``caliper visualize``."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    output_dir: str | None = Field(
        default=None,
        description=("Directory for HTML/plots. Must be an absolute path."),
    )
    reports: str | None = Field(
        default=None,
        description="Comma-separated report ids or list of report ids (alternative to report_group).",
    )
    report_group: str | None = Field(
        default=None,
        description="Group id from visualize-groups.yaml under the artifact tree.",
    )
    visualize_config: str | None = Field(
        default=None,
        description="Path to visualize-groups YAML; default search under artifact tree.",
    )
    include_labels: list[str] = Field(default_factory=list)
    exclude_labels: list[str] = Field(default_factory=list)

    @field_validator("reports", mode="before")
    @classmethod
    def _convert_reports_list(cls, v):
        """Convert list of reports to comma-separated string."""
        if isinstance(v, list):
            return ",".join(str(item) for item in v)
        return v


class CaliperOrchestrationKpiGenerateSection(BaseModel):
    """Emit KPI JSONL via plugin ``compute_kpis``."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    output: str | None = Field(
        default="kpis.jsonl",
        description="Filename or path; relative paths resolve under the post-processing artifact dir.",
    )


class CaliperOrchestrationKpiExportSection(BaseModel):
    """Push KPI rows to OpenSearch (requires env/client setup)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False


class CaliperOrchestrationKpiCsvExportSection(BaseModel):
    """Export KPI data to CSV format."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    output: str | None = Field(
        default="kpis.csv",
        description="CSV filename or path; relative paths resolve under the post-processing artifact dir.",
    )
    include_header_comments: bool = Field(
        default=True,
        description="Whether to include descriptive header comments in the CSV file.",
    )


class CaliperOrchestrationKpiSection(BaseModel):
    """``caliper.postprocess.kpi``."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    generate: CaliperOrchestrationKpiGenerateSection = Field(
        default_factory=CaliperOrchestrationKpiGenerateSection
    )
    export: CaliperOrchestrationKpiExportSection = Field(
        default_factory=CaliperOrchestrationKpiExportSection
    )
    csv_export: CaliperOrchestrationKpiCsvExportSection = Field(
        default_factory=CaliperOrchestrationKpiCsvExportSection
    )


class CaliperOrchestrationAnalyzeSection(BaseModel):
    """``caliper.postprocess.analyze`` — regression vs baseline KPI JSONL."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    baseline: str | None = Field(
        default=None,
        description="Baseline KPI JSONL path (relative → artifact tree root unless absolute).",
    )
    output: str | None = Field(
        default="kpi_analyze.json",
        description="Written under post-processing artifact dir when relative.",
    )

    @model_validator(mode="after")
    def _baseline_when_enabled(self) -> Self:
        if self.enabled and not (self.baseline and str(self.baseline).strip()):
            raise ValueError(
                "caliper.postprocess.analyze.enabled requires non-empty baseline path."
            )
        return self


class CaliperOrchestrationPostprocessConfig(BaseModel):
    """``caliper.postprocess`` — parse, visualize, optional KPI + regression."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = Field(True, description="Master switch for the whole post-processing pipeline.")

    artifacts_dir: str | None = Field(
        default=None,
        description=(
            "Root of the Caliper artifact tree; when null, callers typically use "
            "ARTIFACT_BASE_DIR or override via CLI."
        ),
    )
    plugin_module: str | None = Field(
        default=None,
        description="Plugin import path; overrides manifest plugin_module when set.",
    )
    postprocess_config: str | None = Field(
        default=None,
        description="Explicit path to caliper.yaml manifest.",
    )
    parse: CaliperOrchestrationParseSection = Field(
        default_factory=CaliperOrchestrationParseSection
    )
    visualize: CaliperOrchestrationVisualizeSection = Field(
        default_factory=CaliperOrchestrationVisualizeSection
    )
    kpi: CaliperOrchestrationKpiSection = Field(default_factory=CaliperOrchestrationKpiSection)
    analyze: CaliperOrchestrationAnalyzeSection = Field(
        default_factory=CaliperOrchestrationAnalyzeSection
    )

    @model_validator(mode="after")
    def _visualize_needs_selector(self) -> Self:
        if not self.visualize.enabled:
            return self
        if not (self.visualize.reports or self.visualize.report_group):
            raise ValueError(
                "caliper.postprocess.visualize.enabled requires "
                "`reports` (comma-separated) or `report_group`."
            )
        return self
