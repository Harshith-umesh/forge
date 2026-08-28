# Caliper Status Models

This document describes the unified status model system for Caliper commands, providing type-safe dataclass models for status objects returned by various Caliper operations.

## Overview

Previously, Caliper commands returned untyped dictionaries with inconsistent field names and structures. The new status model system provides:

- **Type Safety**: Dataclass models with proper typing for all status fields
- **Consistency**: Unified field names and structures across all commands  
- **IDE Support**: Full autocompletion and type checking
- **Backward Compatibility**: Easy conversion to legacy dict format when needed

## Public API

All status models are exposed in the `projects.caliper.public` module:

```python
from projects.caliper.public import (
    StatusLevel,
    KpiAnalysisStatus,
    ParseStatus,
    VisualizeStatus,
    create_success_status,
    create_failure_status,
)
```

## Status Levels

All commands use the unified `StatusLevel` enum:

```python
class StatusLevel(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    WARNING = "warning"
    DISABLED = "disabled"
    REGRESSION_DETECTED = "regression_detected"
```

## Available Status Models

### Base Status
All status objects inherit from `BaseStatus`:

```python
@dataclass
class BaseStatus:
    status: StatusLevel
    completed_at: float
    log_file: Optional[str] = None
    error: Optional[str] = None
    message: Optional[str] = None
```

### Command-Specific Models

#### ParseStatus
For `caliper parse` command:
```python
@dataclass
class ParseStatus(BaseStatus):
    success: bool = False
    exit_code: int = 0
    detail: Optional[str] = None
    plugin_module: Optional[str] = None
    record_count: Optional[int] = None
    parse_cache_ref: Optional[str] = None
```

#### KpiAnalysisStatus
For `caliper kpi analyse-kpis` command:
```python
@dataclass
class KpiAnalysisStatus(BaseStatus):
    success: bool = False
    exit_code: int = 0
    output_file: Optional[str] = None
    regressions_detected: bool = False
    regression_count: Optional[int] = None
    total_kpis: Optional[int] = None
    baseline_files_count: Optional[int] = None
```

#### VisualizeStatus
For `caliper visualize` command:
```python
@dataclass
class VisualizeStatus(BaseStatus):
    success: bool = False
    exit_code: int = 0
    detail: Optional[str] = None
    plugin_module: Optional[str] = None
    output_files: list[str] = field(default_factory=list)
    output_dir: Optional[str] = None
    generated_files: int = 0
```

*Additional models available for S3Import, S3Export, KpiGenerate, AiDataExport, and CsvExport...*

## Helper Functions

### Creating Status Objects

```python
# Success status
status = create_success_status(
    KpiAnalysisStatus, output_file="analysis_report.yaml", total_kpis=10, regression_count=0
)

# Failure status
status = create_failure_status(KpiAnalysisStatus, error="No baseline data found", exit_code=2)

# Disabled status
status = create_disabled_status(ParseStatus, reason="parse disabled in configuration")
```

## Usage Patterns

### Engine Side (Producing Status)

```python
from projects.caliper.public import KpiAnalysisStatus, create_success_status

def run_kpi_analysis_typed(...) -> Tuple[KpiAnalysisStatus, dict]:
    try:
        # ... perform analysis ...
        
        if analysis_successful:
            return create_success_status(
                KpiAnalysisStatus,
                output_file=str(output_file),
                total_kpis=len(kpis),
                regression_count=0,
            ), report
        
        elif regressions_found:
            return KpiAnalysisStatus(
                status=StatusLevel.REGRESSION_DETECTED,
                success=False,
                exit_code=3,
                regressions_detected=True,
                regression_count=len(regressions),
                completed_at=time.time(),
            ), report
            
    except Exception as e:
        return create_failure_status(
            KpiAnalysisStatus,
            error=str(e),
            exit_code=1,
        ), None
```

### Orchestration Side (Consuming Status)

```python
from projects.caliper.public import StatusLevel, KpiAnalysisStatus


def handle_kpi_analysis_result(status: KpiAnalysisStatus):
    # Type-safe access with IDE support
    match status.status:
        case StatusLevel.SUCCESS:
            print(f"✅ Analysis completed: {status.total_kpis} KPIs")

        case StatusLevel.REGRESSION_DETECTED:
            print(f"🚨 Regressions found: {status.regression_count}")

        case StatusLevel.FAILED:
            print(f"❌ Analysis failed: {status.error}")

        case StatusLevel.WARNING:
            print(f"⚠️ Warning: {status.message}")
```

### Legacy Compatibility

Convert typed status to legacy dict when needed:

```python
def convert_to_legacy_dict(status: BaseStatus) -> dict[str, Any]:
    result = {
        "status": status.status.value,
        "completed_at": status.completed_at,
    }

    # Add optional fields
    if status.error:
        result["error"] = status.error
    if hasattr(status, "success"):
        result["success"] = status.success
    if hasattr(status, "exit_code"):
        result["exit_code"] = status.exit_code

    return result
```

## Migration Guide

### For Engine Developers

1. **Import status models**:
   ```python
   from projects.caliper.public import KpiAnalysisStatus, create_success_status
   ```

2. **Update function signatures**:
   ```python
   # Before
   def run_analysis(...) -> dict[str, Any]:
   
   # After  
   def run_analysis(...) -> KpiAnalysisStatus:
   ```

3. **Replace dict returns with typed status**:
   ```python
   # Before
   return {
       "status": "success",
       "exit_code": 0,
       "output_file": str(output_file),
   }

   # After
   return create_success_status(KpiAnalysisStatus, output_file=str(output_file))
   ```

### For Orchestration Developers

1. **Update imports**:
   ```python
   from projects.caliper.public import StatusLevel, KpiAnalysisStatus
   ```

2. **Use typed status objects**:
   ```python
   # Before
   if result_dict["status"] == "success":
   
   # After
   if status.status == StatusLevel.SUCCESS:
   ```

3. **Convert to legacy format when needed**:
   ```python
   legacy_dict = convert_to_legacy_dict(status)
   ```

## Benefits

1. **Type Safety**: Catch errors at development time instead of runtime
2. **IDE Support**: Full autocompletion and refactoring support
3. **Documentation**: Self-documenting code with clear field types
4. **Consistency**: Unified interface across all Caliper commands
5. **Maintainability**: Easier to evolve and extend status objects
6. **Pattern Matching**: Modern Python pattern matching with enums

## Testing

The status models include comprehensive test coverage:

```bash
python -c "from projects.caliper.public import *; print('✅ Import successful')"
```

See the examples in `analyze_refactored.py` and `typed_status_example.py` for complete usage patterns.
