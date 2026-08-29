"""Core KPI dataclasses for Caliper engine.

These dataclasses are used by all plugins to ensure consistency and type safety
across the Caliper ecosystem. Plugins should import and use these directly,
not inherit from them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class OverallStatus(StrEnum):
    """Overall analysis status."""

    PASS = "PASS"
    REGRESSION_DETECTED = "REGRESSION_DETECTED"
    NO_BASELINE = "NO_BASELINE"
    NO_TEST_PERFORMED = "NO_TEST_PERFORMED"


@dataclass
class SourceInfo:
    """Source tracking information for KPI records."""

    test_base_path: str
    plugin_module: str
    additional_info: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceInfo:
        """Create SourceInfo from dictionary data."""
        return cls(**data)


@dataclass
class KpiRecord:
    """Standard KPI record structure used by all Caliper plugins.

    This is the canonical format for KPI data across the Caliper ecosystem.
    All plugins must use this exact structure for KPI records.
    """

    # Core identification
    schema_version: str
    kpi_id: str

    # Value and measurement
    value: int | float  # Only numeric types allowed
    unit: str

    # Context and tracking
    run_id: str
    timestamp: str
    labels: dict[str, Any]  # Keys must be strings, values can be anything

    # Metadata and source tracking
    metadata: dict[str, Any] = field(default_factory=dict)
    source: SourceInfo | None = None

    # Optional 2D KPI fields (for time series, histograms, etc.)
    x_unit: str | None = None
    x_help: str | None = None
    y_unit: str | None = None
    y_help: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KpiRecord:
        """Create KpiRecord from dictionary data."""
        return cls(**data)


@dataclass
class KpiCatalogEntry:
    """KPI catalog metadata entry used by all Caliper plugins.

    Describes available KPIs and their characteristics for analysis
    and visualization systems.
    """

    # Core identification
    kpi_id: str
    name: str

    # Measurement characteristics
    unit: str
    higher_is_better: bool
    is_2d: bool

    # Documentation and help
    help: str = ""
    description: str = ""

    # Optional 2D metadata
    x_unit: str = ""
    x_help: str = ""
    y_unit: str = ""
    y_help: str = ""

    # Categorization
    category: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KpiCatalogEntry:
        """Create KpiCatalogEntry from dictionary data."""
        return cls(**data)


@dataclass
class TestSummary:
    """Summary of KPI test results."""

    total_kpis: int
    pass_count: int = 0
    regression_count: int = 0
    skipped_count: int = 0
    improvement_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class ConfigSummary:
    """Summary of analysis configuration."""

    comparison_labels: list[str] = field(default_factory=list)
    ignored_labels: list[str] = field(default_factory=list)
    sorting_labels: list[str] = field(default_factory=list)
    regression_config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class BaselineSummary:
    """Summary of baseline data sources."""

    relevant_sources: list[dict[str, Any]] = field(default_factory=list)
    irrelevant_sources: list[dict[str, Any]] = field(default_factory=list)
    baseline_source_count: int = 0
    baseline_skipped: dict[str, int] = field(default_factory=dict)
    current_source: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class AnalysisSummary:
    """Comprehensive analysis summary with structured components."""

    tested: TestSummary
    config: ConfigSummary
    baseline_info: BaselineSummary
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class RegressionFinding:
    """Individual regression analysis finding."""

    kpi_id: str
    baseline_value: int | float
    current_value: int | float
    relative_change: float
    change_percent: float
    is_regression: bool
    higher_is_better: bool
    unit: str

    # Optional context
    baseline_labels: dict[str, Any] = field(default_factory=dict)
    current_labels: dict[str, Any] = field(default_factory=dict)
    threshold_used: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class ReportMetadata:
    """Structured metadata for regression analysis reports."""

    total_tested: int = 0
    total_skipped: int = 0
    analysis_duration_ms: int = 0
    plugin_module: str = ""
    caliper_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class RegressionReport:
    """Comprehensive regression analysis report used by all Caliper plugins.

    Standard structure for regression analysis results across all plugins.
    """

    # Required fields first (no defaults)
    status: OverallStatus
    total_kpis: int
    regression_count: int
    analysis_timestamp: str

    # Optional fields with defaults
    improvement_count: int = 0
    baseline_version: str | None = None
    current_version: str | None = None
    findings: list[RegressionFinding] = field(default_factory=list)
    threshold_percent: float = 10.0
    comparison_labels: list[str] = field(default_factory=list)
    summary: AnalysisSummary | None = None
    metadata: ReportMetadata | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RegressionReport:
        """Create RegressionReport from dictionary data."""
        # Convert findings list back to RegressionFinding objects
        findings_data = data.get("findings", [])
        findings = [
            RegressionFinding.from_dict(f) if isinstance(f, dict) else f for f in findings_data
        ]

        # Update data with converted findings
        data_copy = data.copy()
        data_copy["findings"] = findings

        return cls(**data_copy)

    def has_regressions(self) -> bool:
        """Check if any regressions were detected."""
        return self.regression_count > 0

    def is_successful(self) -> bool:
        """Check if analysis completed successfully."""
        return self.status in ("success", "no_regression")

    def get_regressions(self) -> list[RegressionFinding]:
        """Get only the regression findings."""
        return [f for f in self.findings if f.is_regression]

    def get_improvements(self) -> list[RegressionFinding]:
        """Get only the improvement findings."""
        return [
            f
            for f in self.findings
            if not f.is_regression
            and (
                (f.higher_is_better and f.relative_change > 0)
                or (not f.higher_is_better and f.relative_change < 0)
            )
        ]


# Convenience type aliases for plugin developers
KPI = KpiRecord  # Shorter alias
Catalog = KpiCatalogEntry  # Shorter alias
Report = RegressionReport  # Shorter alias

# Make OverallStatus available at module level
__all__ = [
    "OverallStatus",
    "SourceInfo",
    "KpiRecord",
    "KPI",
    "KpiCatalogEntry",
    "Catalog",
    "RegressionReport",
    "Report",
    "RegressionFinding",
    "TestSummary",
    "ConfigSummary",
    "BaselineSummary",
    "AnalysisSummary",
    "ReportMetadata",
]
