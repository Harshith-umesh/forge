# Configuration Framework Changelog

## 2026-08-17 - Cluster-based Preset Application System

### Added

#### Cluster-based Preset Application System

- **New Feature**: `apply_presets_from_cluster_config()` mechanism in `projects/core/library/config.py`
  - Automatically applies cluster-specific presets based on cluster name
  - Loads cluster name from `ci_job.cluster` configuration field
  - Falls back to `forge-config` ConfigMap when `ci_job.cluster` is not set
  - Applies preset named `cluster_{cluster_name}`

#### Key Components

1. **Primary cluster resolution**: Reads from `ci_job.cluster` config field
2. **Fallback cluster resolution**: Queries `oc get cm forge-config` and extracts `cluster` field from ConfigMap data
3. **Preset application**: Applies preset named `cluster_{cluster_name}` using existing preset system
4. **Audit tracking**: Inherits existing preset tracking in `presets_applied.txt`

#### Implementation Details

- **Method**: `Config.apply_presets_from_cluster_config()`
  - Robust error handling for missing configuration sections
  - Graceful handling when cluster-specific preset doesn't exist (debug logging)
  - Leverages existing `apply_preset()` method for consistency

- **Helper Method**: `Config._get_cluster_from_configmap()`
  - Executes `oc get cm forge-config -o yaml` 
  - Parses YAML response and extracts `data.cluster` field
  - 30-second timeout with comprehensive exception handling
  - Returns `None` on any failure (missing ConfigMap, parsing errors, timeouts)

#### Integration

- Integrated into configuration initialization sequence in `config.init()`
- Executes after project args presets but before final config overrides
- Maintains existing configuration precedence hierarchy
- Uses existing preset infrastructure for tracking and logging

#### Usage Example

```yaml
# In presets.d/clusters.yaml
__multiple: true

cluster_eks:
  runtime.namespace: kpouget-dev
  platform.cluster.namespace.labels: {}
  runtime.kserve.wait_readiness: false
  runtime.kueue.enabled: true

cluster_hera:
  rhaiis.cluster_tag: "hera2"
  rhaiis.deploy.image_pull_secrets: ["npalaska-image-pull"]
  benchmarks.guidellm.fs_group: 0
```

```yaml
# Configuration that triggers preset application
ci_job:
  cluster: eks  # or loaded from forge-config ConfigMap
```

With the above configuration, the system will automatically apply the `cluster_eks` preset.

#### ConfigMap Creation

```bash
# Create forge-config ConfigMap for fallback cluster detection
oc create configmap forge-config --from-literal=cluster=eks
```

### Technical Details

- **File Modified**: `projects/core/library/config.py`
- **Lines Added**: ~75 lines (simplified from previous approach)
- **Dependencies**: Uses existing `yaml`, `subprocess`, `logging` modules
- **Backward Compatibility**: Fully backward compatible - skips cluster preset when not available
- **Integration**: Leverages existing preset system for consistency and maintainability

### Design Benefits

- **Simplified Architecture**: Uses existing preset system instead of new configuration section
- **Consistent Behavior**: Same logging, tracking, and error handling as other presets
- **Maintainable**: Cluster configurations defined as standard presets in `presets.d/` files
- **Flexible**: Supports preset inheritance using `extends` keyword
- **Discoverable**: Cluster presets visible alongside other presets in preset files

This enhancement enables automatic environment-specific configuration through the established preset system, improving deployment experience across different cluster environments while maintaining architectural consistency.

## 2026-07-16 - Variables Override Helper

### New Features
- **Variables Override Helper**: Added `write_variables_override()` function for programmatic configuration override generation
  - **Behavior**: Creates variables_override.yaml files without requiring full project config initialization
  - **Usage**: Supports both preset configuration (`project.args`) and additional variable overrides
  - **Error Handling**: Validates `env.ARTIFACT_DIR` initialization with clear error messages

### Files Modified
- `projects/core/library/config.py` - Added `write_variables_override()` helper function

### Benefits
- **Early Configuration**: Enables configuration override before project initialization
- **Flexible Override Structure**: Supports both preset lists and arbitrary configuration variables
- **Robust Error Handling**: Fails fast with clear messages when environment not properly initialized
