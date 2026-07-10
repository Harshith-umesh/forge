# Rebuild Image Toolbox

Triggers a rebuild of an existing Shipwright Build by creating a BuildRun and waiting for completion.

## Usage

```bash
# Basic usage - rebuild a Build named "my-build" 
python3 main.py rebuild_image my-build

# Specify namespace and timeout
python3 main.py rebuild_image my-build \
  --namespace my-namespace \
  --timeout-minutes 45
```

## Parameters

- `build_name` (required): Name of the existing Shipwright Build to rebuild
- `--namespace` (optional): Kubernetes namespace (default: psap-automation-wip)  
- `--timeout-minutes` (optional): Maximum time to wait for build completion (default: 30)

## What it does

1. **Validates** the specified Build exists in the namespace
2. **Creates** a BuildRun with generateName to trigger rebuild
3. **Monitors** the BuildRun status every 30 seconds
4. **Captures** logs and artifacts on completion or failure
5. **Reports** success/failure with detailed error messages

## Artifacts

Generated in the artifacts directory:
- `{buildrun-name}-final-status.yaml` - Final BuildRun status
- `{buildrun-name}-build.log` - Build logs  
- `buildrun-describe.txt` - Detailed BuildRun description
- `{build-name}-build-definition.yaml` - Original Build definition

## Integration

Can be imported and used programmatically:

```python
from projects.cluster.toolbox.rebuild_image.main import run as rebuild_image

result = rebuild_image(
    build_name="my-build",
    namespace="my-namespace", 
    timeout_minutes=30
)
```

## Common Use Cases

- **FORGE image rebuilds**: Rebuild FORGE container after code changes
- **Dependency updates**: Trigger rebuilds when base images change
- **Testing**: Validate Build configurations work correctly
- **CI/CD integration**: Automated rebuilds in pipelines
