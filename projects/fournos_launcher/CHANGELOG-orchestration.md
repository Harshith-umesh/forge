# Fournos Launcher Orchestration Changelog

## 2026-07-13 - Clusterless Mode and New Directives

### New PR Directives
- **Clusterless Directive**: Added `/clusterless` directive to enable clusterless mode execution
  - **Effect**: Sets `fournos.job.exclusive=false` and `fournos.job.clusterless=true`
  - **Validation**: Prevents conflicting usage with `/exclusive true`
- **Fournos Environment Directive**: Added `/fournos` directive for namespace targeting
  - **Format**: `/fournos wip` or `/fournos staging`
  - **Effect**: Sets `fournos.namespace` to `psap-automation-{environment}`
  - **Validation**: Restricts to `wip` and `staging` environments only

### Enhanced Validation
- **Mutual Exclusivity**: Added comprehensive validation to prevent `clusterless=true` and `exclusive=true` conflicts
  - **PR Parser Level**: Catches directive conflicts during comment parsing
  - **CLI Level**: Validates configuration before command execution
  - **Submit Level**: Final validation before job submission
- **Optional Cluster**: Made `/cluster` directive optional when `/clusterless` is used
  - **Smart Validation**: Requires cluster specification only when not in clusterless mode
  - **Clear Error Messages**: Improved error messaging to explain clusterless option

### Files Modified
- `pr_args.py` - Added `/clusterless` and `/fournos` directive handlers with validation
- `cli.py` - Enhanced cluster requirement logic and added conflict validation
- `submit.py` - Added clusterless/exclusive mutual exclusivity checks

### Benefits
- **Flexible Execution**: Enables both cluster-targeted and clusterless job execution modes
- **Environment Targeting**: Simplified namespace selection for different deployment environments
- **Robust Validation**: Prevents configuration conflicts with clear, actionable error messages
- **Backward Compatibility**: Maintains existing directive functionality while adding new capabilities

## 2026-06-30 - Notification System Integration

### Job Completion Notifications
- **GitHub Notification Integration**: Added automated notification system for job submission completion
  - **Success/Failure Status**: Clear visual indicators with green/red flags for job outcomes
  - **Runtime Tracking**: Displays total execution time in notification messages
  - **Structured Format**: Collapsible notification format with test results, logs, and configuration details
  - **MLflow Integration**: Automatic extraction and linking of MLflow test results when available

### API Improvements
- **Link Generation Update**: Migrated from `get_ci_link` to `get_ocpci_link` for OCPCI result linking
  - **Consistent Naming**: Standardized API naming across notification systems
  - **Enhanced Compatibility**: Better integration with core notification framework

### Files Modified
- `projects/fournos_launcher/orchestration/submit.py` - Added notification system integration and runtime tracking

### Benefits
- **Immediate Feedback**: Users receive instant notification when job submissions complete
- **Simple Context**: Notifications include test results, logs, and execution time for complete visibility
- **Consistent Experience**: Unified notification format across all FORGE orchestration systems

## 2026-06-24 - Job Management & Notification Improvements

### Enhanced Notifications
- **Custom Notifications**: Enhanced notifications include MLflow URLs and log file links
- **Better Content**: Improved notification content with direct links to job outputs

### Files Modified
- Orchestration `submit` - notification generation with MLflow/log links

### Benefits
- Improved notification content with direct links
- Easier access to job outputs and logs
