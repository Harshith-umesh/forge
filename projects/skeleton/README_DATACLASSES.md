# Skeleton KPI Implementation with Dataclasses

The skeleton plugin implements a complete KPI mechanism using Python dataclasses for structured data handling. This provides type safety, better serialization, and improved maintainability.

## Features

### 1. Dataclass-based Architecture

All KPI-related data structures use Python `@dataclass` with `dataclasses.asdict()` for serialization:

```python
from dataclasses import asdict, dataclass, field
from typing import Any
```

### 2. Core Dataclasses

#### SkeletonKpiRecord
Structured KPI record with full type safety:

```python
@dataclass
class SkeletonKpiRecord:
    schema_version: str
    kpi_id: str
    value: float | list[Any]
    unit: str
    run_id: str
    timestamp: str
    labels: dict[str, Any]
    metadata: dict[str, Any]
    source: dict[str, Any]
    x_unit: str | None = None  # For 2D KPIs
    x_help: str | None = None
    y_unit: str | None = None
    y_help: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
```

#### SkeletonKpiCatalogEntry
KPI catalog metadata with validation:

```python
@dataclass
class SkeletonKpiCatalogEntry:
    kpi_id: str
    name: str
    unit: str
    higher_is_better: bool
    is_2d: bool
    help: str = ""
    x_unit: str = ""
    x_help: str = ""
    y_unit: str = ""
    y_help: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
```

#### SkeletonRegressionReport
Comprehensive regression analysis results:

```python
@dataclass
class SkeletonRegressionReport:
    status: str  # "success", "no_regression", "regression_detected", "error"
    total_kpis: int
    regression_count: int
    analysis_timestamp: str
    baseline_version: str | None
    current_version: str | None
    findings: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def has_regressions(self) -> bool:
        return self.regression_count > 0

    def is_successful(self) -> bool:
        return self.status in ("success", "no_regression")
```

### 3. KPI Functions

- **`kpi_skeleton_throughput_rps`**: Measures requests per second (higher is better)
- **`kpi_skeleton_latency_ms`**: Measures response latency in milliseconds (lower is better)

### 4. Date-based Versioning

The plugin automatically extracts dates and stores them as the `version` label from:

1. **Test Labels**: `version` or `date` fields in test metadata
2. **Directory Paths**: Date patterns (YYYY-MM-DD) in test paths  
3. **Fallback**: Current date if no date found

## Usage Examples

### Creating KPI Records

```python
from projects.skeleton.postprocess.default.parsing.kpis import SkeletonKpiRecord

# Create structured KPI record
kpi_record = SkeletonKpiRecord(
    schema_version="1",
    kpi_id="kpi_skeleton_throughput_rps",
    value=1000.0,
    unit="req/s",
    run_id="/artifacts/2024-03-15/test1",
    timestamp="2024-03-15T10:30:00Z",
    labels={
        "scenario": "test1",
        "version": "2024-03-15",
        "higher_is_better": True,
    },
    metadata={"test_config": {}, "environment": "test"},
    source={"test_base_path": "/artifacts/2024-03-15/test1", "plugin_module": "skeleton"},
)

# Convert to dictionary for JSON serialization
kpi_dict = kpi_record.to_dict()
```

### Generating KPI Catalog

```python
from projects.skeleton.postprocess.default.parsing.kpis import SkeletonKpiHandler

# Get structured catalog
catalog = SkeletonKpiHandler.get_catalog()

# Example catalog entry:
# {
#     "kpi_id": "kpi_skeleton_throughput_rps",
#     "name": "Throughput",
#     "unit": "req/s",
#     "higher_is_better": true,
#     "is_2d": false,
#     "help": "Number of requests processed per second"
# }
```

### Regression Analysis

```python
# Sample data
baseline_kpis = [
    {
        "kpi_id": "kpi_skeleton_throughput_rps",
        "value": 1000.0,
        "unit": "req/s",
        "labels": {"version": "2024-03-15", "higher_is_better": True},
    }
]

current_kpis = [
    {
        "kpi_id": "kpi_skeleton_throughput_rps",
        "value": 800.0,  # 20% decrease - regression!
        "unit": "req/s",
        "labels": {"version": "2024-03-20", "higher_is_better": True},
    }
]

# Create regression report
report = SkeletonKpiHandler.create_regression_report(
    baseline_kpis=baseline_kpis,
    current_kpis=current_kpis,
    threshold=0.1,  # 10% threshold
)

# Access structured results
print(f"Status: {report.status}")  # "regression_detected"
print(f"Regressions: {report.regression_count}")  # 1
print(f"Has regressions: {report.has_regressions()}")  # True

# Detailed findings
for finding in report.findings:
    print(
        f"{finding.kpi_id}: {finding.baseline_value} → {finding.current_value} ({finding.change_percent:.1f}%)"
    )
```

### Test Structure with Dates

```
artifacts/
  2024-03-15/
    test1/
      metrics.json: {"throughput": 1000, "latency_ms": 50}
  2024-03-20/
    test1/
      metrics.json: {"throughput": 800, "latency_ms": 75}
```

### Generated KPI Structure

```json
{
  "schema_version": "1",
  "kpi_id": "kpi_skeleton_throughput_rps",
  "value": 1000.0,
  "unit": "req/s",
  "run_id": "/artifacts/2024-03-15/test1",
  "timestamp": "2024-03-15T10:30:00Z",
  "labels": {
    "scenario": "test1",
    "version": "2024-03-15",
    "higher_is_better": true
  },
  "metadata": {
    "test_config": {},
    "environment": "test"
  },
  "source": {
    "test_base_path": "/artifacts/2024-03-15/test1",
    "plugin_module": "projects.skeleton.postprocess.default.plugin"
  }
}
```

### Regression Report Structure

```json
{
  "status": "regression_detected",
  "total_kpis": 1,
  "regression_count": 1,
  "analysis_timestamp": "2024-08-29T10:30:00Z",
  "baseline_version": "2024-03-15",
  "current_version": "2024-03-20",
  "findings": [
    {
      "kpi_id": "kpi_skeleton_throughput_rps",
      "baseline_value": 1000.0,
      "current_value": 800.0,
      "relative_change": -0.2,
      "change_percent": -20.0,
      "is_regression": true,
      "higher_is_better": true,
      "unit": "req/s"
    }
  ],
  "summary": {
    "threshold_percent": 10.0,
    "total_comparisons": 1,
    "regressions": [...],
    "improvements": []
  }
}
```

## Analysis Configuration

```python
analysis_config = AnalysisConfig(
    comparison_labels=["version"],  # Compare across different versions (dates in YYYY-MM-DD format)
    ignored_labels=["higher_is_better"],  # Ignore KPI metadata labels
    regression_config={
        "SCALAR_RELATIVE_CHANGE": {
            "max_relative_regression": 0.1,  # 10% threshold
            "min_baseline_points": 1,  # Minimum baseline points needed
        },
    },
)
```

## Benefits

1. **Type Safety**: Full type checking with mypy/IDE support
2. **Structured Data**: Clear data models with validation  
3. **Easy Serialization**: `asdict()` provides clean JSON serialization
4. **Maintainability**: Self-documenting data structures
5. **Extensibility**: Easy to add new fields without breaking compatibility
6. **Testing**: Clear interfaces for unit testing
7. **IDE Support**: Full autocompletion and refactoring support

## Testing

Run the test script to verify the implementation:

```bash
python projects/skeleton/test_dataclasses_kpi.py
```

This tests:
- KPI record creation and serialization
- KPI catalog generation with dataclasses
- Regression analysis with structured reports  
- JSON serialization compatibility
- All dataclass functionality

The implementation provides a robust foundation for KPI analysis with proper data structures, type safety, and comprehensive regression testing capabilities.
