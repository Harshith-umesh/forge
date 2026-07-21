# Fournos Launcher Toolbox Changelog

## 2026-07-13 - Clusterless Mode Support

### submit_and_wait

#### Clusterless Execution Support
- **Clusterless Parameter**: Added `clusterless: bool = False` parameter to entrypoint function
- **Template Updates**: Enhanced job.yaml.j2 template to conditionally render `clusterless: true` or `cluster: <name>` fields
- **Validation Logic**: Added validation to ensure clusterless and exclusive modes are mutually exclusive
- **Optional Cluster**: Made `cluster_name` parameter optional when `clusterless=True`

#### Files Modified
- `main.py` - Added clusterless parameter support and validation logic
- `templates/job.yaml.j2` - Updated FournosJob template with conditional cluster/clusterless field rendering

#### Benefits
- Enables clusterless job execution for improved resource flexibility
- Maintains backward compatibility with existing cluster-based workflows
- Provides clear validation for conflicting configuration options

## 2026-06-24 - Job Management Improvements

### submit_and_wait

#### Enhanced Job Storage
- **Job Storage**: Now saves Fournos jobs in well-known location for better tracking
- **Clean Annotations**: Removed unnecessary annotations from operations

#### Files Modified
- Toolbox `submit_and_wait` operations - job storage and annotations

#### Benefits
- Better job artifact management
- Cleaner job metadata
- Improved job tracking capabilities
