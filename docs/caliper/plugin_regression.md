# KPI Regression Analysis Configuration

This document explains how to configure KPI regression analysis in Caliper plugins. Regression analysis compares current KPI values against historical baselines to detect performance regressions.

## Overview

KPI regression analysis helps you:
- **Detect Performance Regressions**: Automatically flag when KPIs deteriorate compared to historical baselines
- **Track Performance Trends**: Compare current results against previous versions or configurations
- **Set Quality Gates**: Define acceptable thresholds for performance changes
- **Generate Reports**: Produce detailed analysis reports with regression verdicts

## Analysis Configuration

### AnalysisConfig Fields

Every plugin **must** provide an analysis configuration that specifies how regression testing should be performed:

```python
@dataclass
class AnalysisConfig:
    """Configuration for KPI regression analysis."""
    
    comparison_keys: list[str] = field(default_factory=list)
    ignored_keys: list[str] = field(default_factory=list)
    sorting_keys: list[str] = field(default_factory=list)
    max_relative_regression: float = 0.1
    min_baseline_points: int = 1
```

#### Field Descriptions

**`comparison_keys`** *(required)*
- Label keys that define what we compare against
- Records must differ on at least one comparison key to be distinct baselines
- Example: `["version"]` means we test the current version against other versions
- Example: `["model", "version"]` means we compare across different model versions

**`ignored_keys`** *(optional)*
- Label keys excluded when matching current to baseline records
- Allows testing across environmental differences
- Example: `["os", "hostname"]` means we match across different operating systems and machines
- Example: `["timestamp", "build_id"]` means we ignore timing and build-specific labels

**`sorting_keys`** *(optional)*
- Label keys used to order entries in the output report
- Helps organize results in a logical sequence
- Example: `["platform", "model"]` sorts results by platform, then model

**`max_relative_regression`** *(default: 0.1)*
- Fraction threshold for flagging regression (0.1 = 10%)
- Regressions exceeding this threshold are marked as failures
- Example: 0.05 for 5% threshold, 0.2 for 20% threshold

**`min_baseline_points`** *(default: 1)*
- Minimum number of baseline data points required to run a test
- KPIs with fewer baselines are skipped
- Higher values require more historical data for reliable comparison

## Providing Configuration in Plugins

Plugins must provide analysis configuration using one of two methods:

### Method 1: Static Configuration Attribute

Provide a static `analysis_config` attribute in your plugin module:

```python
# In your plugin module (e.g., projects/myproject/postprocess/myplugin/__init__.py)

from projects.caliper.engine.kpi.analyze import AnalysisConfig

# Static configuration
analysis_config = AnalysisConfig(
    comparison_keys=["version"],
    ignored_keys=["os", "hostname"],
    sorting_keys=["platform", "model"],
    max_relative_regression=0.05,  # 5% threshold
    min_baseline_points=3,
)
```

### Method 2: Dynamic Configuration Function

Provide a `get_analysis_config()` function for dynamic configuration:

```python
# In your plugin module
from projects.caliper.engine.kpi.analyze import AnalysisConfig


def get_analysis_config() -> AnalysisConfig:
    """Return analysis configuration based on environment or settings."""

    # Example: Different thresholds based on environment
    import os

    if os.environ.get("CI_ENVIRONMENT") == "production":
        regression_threshold = 0.02  # Stricter in production
    else:
        regression_threshold = 0.1  # More lenient in development

    return AnalysisConfig(
        comparison_keys=["version", "model"],
        ignored_keys=["hostname", "timestamp"],
        sorting_keys=["version"],
        max_relative_regression=regression_threshold,
        min_baseline_points=2,
    )
```

### Method 3: Dictionary Configuration

You can also provide configuration as a dictionary (automatically converted to AnalysisConfig):

```python
# Dictionary format (converted automatically)
analysis_config = {
    "comparison_keys": ["version"],
    "ignored_keys": ["os"],
    "sorting_keys": ["platform"],
    "max_relative_regression": 0.1,
    "min_baseline_points": 1,
}
```

## Configuration Examples

### Example 1: Version-Based Comparison

Test current version against previous versions, ignoring environmental differences:

```python
analysis_config = AnalysisConfig(
    comparison_keys=["version"],  # Compare against different versions
    ignored_keys=["os", "hostname"],  # Ignore environment differences
    sorting_keys=["platform"],  # Sort results by platform
    max_relative_regression=0.1,  # 10% regression threshold
    min_baseline_points=2,  # Require at least 2 baseline points
)
```

### Example 2: Model Performance Comparison

Compare different model configurations:

```python
analysis_config = AnalysisConfig(
    comparison_keys=["model", "version"],  # Compare across model versions
    ignored_keys=["timestamp", "run_id"],  # Ignore timing/execution metadata
    sorting_keys=["model", "version"],  # Sort by model, then version
    max_relative_regression=0.05,  # Stricter 5% threshold
    min_baseline_points=3,  # Need more baselines for reliability
)
```

### Example 3: Multi-Platform Analysis

Test across different platforms while tracking version changes:

```python
analysis_config = AnalysisConfig(
    comparison_keys=["version"],  # Compare versions
    ignored_keys=["hostname"],  # Ignore specific machines
    sorting_keys=["platform", "version"],  # Group by platform, sort by version
    max_relative_regression=0.15,  # 15% threshold (platforms vary more)
    min_baseline_points=1,  # Accept single baseline per platform
)
```

### Example 4: Environment-Sensitive Configuration

Different thresholds for different environments:

```python
def get_analysis_config() -> AnalysisConfig:
    import os

    env = os.environ.get("TEST_ENVIRONMENT", "dev")

    if env == "production":
        return AnalysisConfig(
            comparison_keys=["version"],
            ignored_keys=["hostname"],
            max_relative_regression=0.02,  # Very strict for production
            min_baseline_points=5,  # Need lots of baselines
        )
    elif env == "staging":
        return AnalysisConfig(
            comparison_keys=["version", "branch"],
            ignored_keys=["hostname", "timestamp"],
            max_relative_regression=0.05,  # Moderate threshold
            min_baseline_points=2,
        )
    else:  # development
        return AnalysisConfig(
            comparison_keys=["version"],
            ignored_keys=["hostname", "timestamp", "developer"],
            max_relative_regression=0.2,  # Lenient for development
            min_baseline_points=1,
        )
```

## Understanding Regression Logic

### How Matching Works

1. **Baseline Selection**: Records with different `comparison_keys` become potential baselines
2. **Label Matching**: Current and baseline records must match on all labels except:
   - Labels in `ignored_keys` (explicitly ignored)
   - Labels in `comparison_keys` (expected to differ)
3. **Threshold Comparison**: If relative change exceeds `max_relative_regression`:
   - For "higher is better" KPIs: negative change > threshold = regression
   - For "lower is better" KPIs: positive change > threshold = regression

### Example Matching Scenario

Given this configuration:
```python
analysis_config = AnalysisConfig(
    comparison_keys=["version"],
    ignored_keys=["hostname"],
    max_relative_regression=0.1,
)
```

**Current Record**:
```json
{
  "kpi_id": "throughput",
  "value": 90.0,
  "labels": {"version": "v2.0", "platform": "GPU", "hostname": "worker-1"}
}
```

**Potential Baselines**:
```json
[
  {
    "kpi_id": "throughput", 
    "value": 100.0,
    "labels": {"version": "v1.9", "platform": "GPU", "hostname": "worker-2"}
  },
  {
    "kpi_id": "throughput",
    "value": 85.0, 
    "labels": {"version": "v1.8", "platform": "GPU", "hostname": "worker-1"}
  }
]
```

**Analysis**:
- Both baselines match: same `platform`, different `version`, `hostname` ignored
- Baseline mean: (100.0 + 85.0) / 2 = 92.5
- Relative change: (90.0 - 92.5) / 92.5 = -0.027 (-2.7%)
- Result: No regression (2.7% < 10% threshold)

## Error Handling

If your plugin is missing analysis configuration, Caliper will fail with detailed error messages:

```
Analysis configuration error: Plugin module 'myproject.postprocess.myplugin' is missing required analysis configuration. Plugin must provide either 'analysis_config' attribute or 'get_analysis_config()' function.
```

Common configuration errors:
- **Missing configuration**: Plugin has no `analysis_config` or `get_analysis_config()`
- **Invalid format**: Configuration is not a dict or AnalysisConfig instance
- **Function errors**: `get_analysis_config()` raises an exception
- **Import errors**: Plugin module cannot be imported

## Best Practices

### 1. Choose Appropriate Comparison Keys
- Use version-related labels for tracking releases: `["version", "build"]`
- Include model/configuration changes: `["model", "parameters"]`
- Avoid high-cardinality labels that create too many baseline groups

### 2. Set Realistic Thresholds
- Start with conservative thresholds (10-20%) and adjust based on your system's variability
- Consider different thresholds for different KPI types (latency vs throughput)
- Use stricter thresholds in production environments

### 3. Ignore Environmental Noise
- Always ignore machine-specific labels: `["hostname", "worker_id"]`
- Ignore timing metadata: `["timestamp", "run_id", "job_id"]`
- Consider ignoring OS/infrastructure differences if testing across platforms

### 4. Organize Results Clearly
- Use logical sorting keys that help humans understand results
- Group related configurations together: `["platform", "model", "version"]`
- Put most important differentiators first

### 5. Require Sufficient Baselines
- Set `min_baseline_points` to 2-3 for better statistical reliability
- Consider higher values for critical production metrics
- Balance reliability with the need to run analysis on limited historical data

## Integration with Orchestration

The analysis configuration works with the orchestration layer:

```yaml
# In your caliper postprocess config
caliper:
  postprocess:
    analyze:
      enabled: true
      fail_on_regression: false  # Don't fail tests on regression by default
      current_kpis: "kpis.json"
      historical_kpis: "historical_data/"
      output: "kpi_analysis.json"
```

When `analyze.enabled: true`, Caliper will:
1. Load your plugin's analysis configuration
2. Compare current KPIs against historical baselines
3. Generate a JSON analysis report
4. Optionally fail the test if regressions are detected (when `fail_on_regression: true`)
