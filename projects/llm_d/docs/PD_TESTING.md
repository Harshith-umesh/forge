# P/D (Prefill/Decode) Testing in llm-d

This document describes the P/D testing capabilities in the llm-d project, which allows testing various Prefill/Decode configurations with flexible pod counts, tensor parallelism, and scheduler configurations using profile name-based parameter extraction.

## Overview

P/D testing enables you to:
- Configure the number of prefill and decode pods via profile names
- Set different tensor parallelism for prefill vs decode pods
- Test various scheduler configurations  
- Run matrix tests across multiple P/D configurations
- Use template-based profiles with automatic parameter extraction

## P/D Configuration Architecture

### Profile Name-Based Configuration

P/D deployments use profile names to encode configuration parameters:

**Pattern**: `pd-d.x{decode_pods}-p.tp{prefill_tp}-d.tp{decode_tp}-p.x{prefill_pods}`

**Examples**:
- `pd-d.x2-p.tp1-d.tp4-p.x8` - 2 decode pods (4 GPUs each), 8 prefill pods (1 GPU each)
- `pd-d.x1-p.tp2-d.tp8-p.x4` - 1 decode pod (8 GPUs), 4 prefill pods (2 GPUs each)

### Global P/D Configuration

P/D-specific settings are centralized in `deployments.yaml`:

```yaml
pd:
  vllm_extra:
    args:
      - --block-size 128
      - --kv-transfer-config '{"kv_connector":"NixlConnector", "kv_role":"kv_both"}'
    env:
      - name: VLLM_NIXL_SIDE_CHANNEL_HOST
        valueFrom:
          fieldRef:
            apiVersion: v1
            fieldPath: status.podIP
  resources:
    rdma/ib: '1'
```

### Template-Based Profiles

Template profiles use `FROM_NAME` placeholders for automatic parameter extraction:

```yaml
template__pd:
  scheduler_manifest: {}  # simplified scheduler
  tensor_parallelism: FROM_NAME  # extracted from profile name
  pd_config:
    prefill_pods: FROM_NAME      # extracted from p.x{N} pattern
    decode_pods: FROM_NAME       # extracted from d.x{N} pattern  
    scheduler_config: "high-throughput"
```

## P/D Profile Examples

### Small Scale P/D
```yaml
# Profile: pd-d.x1-p.tp1-d.tp2-p.x2
# 1 decode pod with 2 GPUs, 2 prefill pods with 1 GPU each
deployment_profile: pd-d.x1-p.tp1-d.tp2-p.x2
```

### Medium Scale P/D  
```yaml
# Profile: pd-d.x2-p.tp2-d.tp4-p.x4
# 2 decode pods with 4 GPUs each, 4 prefill pods with 2 GPUs each
deployment_profile: pd-d.x2-p.tp2-d.tp4-p.x4
```

### Large Scale P/D
```yaml
# Profile: pd-d.x4-p.tp4-d.tp8-p.x8
# 4 decode pods with 8 GPUs each, 8 prefill pods with 4 GPUs each  
deployment_profile: pd-d.x4-p.tp4-d.tp8-p.x8
```

## P/D Testing with Presets

P/D testing integrates with the existing preset system. Use presets that include P/D profile configurations:

### Custom P/D Testing
```bash
llm_d ci --preset your-pd-preset
```

Where your preset contains P/D profile configurations in `presets.yaml`:

```yaml
your-pd-preset:
  runtime:
    model_name: Qwen/Qwen3-0.6B
    deployment_profile: pd-d.x2-p.tp1-d.tp4-p.x8
    benchmark_key: short
```

## Custom P/D Testing via PR Comments

You can override P/D configurations using PR comment directives:

### Test Specific P/D Configurations
```
/var runtime.deployment_profile: [pd-d.x1-p.tp1-d.tp2-p.x2, pd-d.x2-p.tp2-d.tp4-p.x4]
/var runtime.model_name: meta-llama/Llama-3.1-8B-Instruct
/var runtime.benchmark_key: short
```

### Test Different P/D Scales
```
/var runtime.deployment_profile: [pd-d.x1-p.tp1-d.tp4-p.x1, pd-d.x4-p.tp4-d.tp8-p.x8]
/var runtime.model_name: Qwen/Qwen3-0.6B
/var runtime.benchmark_key: [short, concurrent-1k-1k]
```

### Mix P/D and Traditional Profiles
```
/var runtime.deployment_profile: [pd-d.x2-p.tp1-d.tp4-p.x8, approximate-prefix-cache, precise-prefix-cache]
/var runtime.model_name: Qwen/Qwen3-0.6B
```

## Parameter Extraction Rules

The system extracts parameters from P/D profile names using these patterns:

### Tensor Parallelism
- **Main container (decode)**: `d.tp{N}` or `tp{N}` → N GPUs per decode pod
- **Prefill container**: `p.tp{N}` → N GPUs per prefill pod

### Pod Replicas
- **Main container (decode)**: `d.x{N}` or `x{N}` → N decode pods
- **Prefill container**: `p.x{N}` → N prefill pods

### Examples
- `pd-d.x2-p.tp1-d.tp4-p.x8`: 2 decode pods (4 GPUs each), 8 prefill pods (1 GPU each)
- `pd-tp8-x1-p.tp2-p.x4`: 1 decode pod (8 GPUs), 4 prefill pods (2 GPUs each)

## Runtime Configuration Functions

New runtime configuration functions are available for P/D testing:

```python
from projects.llm_d.orchestration import runtime_config

# Get P/D configuration
pd_config = runtime_config.get_pd_config()
# Returns: {'prefill_pods': 2, 'decode_pods': 4, 'scheduler_config': 'high-throughput'}

# Get individual P/D parameters
prefill_pods = runtime_config.get_prefill_pod_count()  # Returns: 2
decode_pods = runtime_config.get_decode_pod_count()    # Returns: 4
scheduler_config = runtime_config.get_scheduler_config()  # Returns: 'high-throughput'

# Check if current deployment is P/D
is_pd = runtime_config.is_pd_deployment()  # Returns: True for P/D profiles
```

## Test Output and Artifacts

P/D tests generate the same artifacts as standard llm-d tests, with additional P/D-specific metadata:

### Test Labels
```yaml
model_name: meta-llama/Llama-3.1-8B-Instruct
deployment_profile: pd-d.x2-p.tp1-d.tp4-p.x8
guidellm_loadshape: short
pd_prefill_pods: 8       # Extracted from p.x8
pd_decode_pods: 2        # Extracted from d.x2  
pd_prefill_tensor_parallelism: 1   # Extracted from p.tp1
pd_decode_tensor_parallelism: 4    # Extracted from d.tp4
pd_scheduler_config: high-throughput
```

### Artifact Directory Structure
```
ARTIFACT_DIR/
├── llmd_test/
│   ├── __test_labels__.yaml
│   ├── artifacts/
│   │   ├── llminferenceservice.yaml
│   │   ├── endpoint.url
│   │   └── results/
│   │       └── benchmark_short/
├── prepare_llmd_run_pd_d_x2_p_tp1_d_tp4_p_x8_meta_llama_instruct/
└── ...
```

### LLMISVC Manifest Annotations
P/D deployments include profile metadata in annotations:

```yaml
apiVersion: serving.kserve.io/v1alpha1
kind: LLMInferenceService
metadata:
  name: llm-d-pd-d.x2-p.tp1-d.tp4-p.x8
  namespace: forge-llm-d
  annotations:
    forge.openshift.io/deployment-profile: pd-d.x2-p.tp1-d.tp4-p.x8
spec:
  replicas: 2  # decode pods
  prefill:
    replicas: 8  # prefill pods
    template:
      containers:
      - name: main
        resources:
          requests:
            nvidia.com/gpu: '1'  # prefill tensor parallelism
            rdma/ib: '1'         # P/D resources
          limits:
            nvidia.com/gpu: '1'
            rdma/ib: '1'
  template:  # decode template
    containers:
    - name: main  
      resources:
        requests:
          nvidia.com/gpu: '4'  # decode tensor parallelism
          rdma/ib: '1'         # P/D resources
        limits:
          nvidia.com/gpu: '4'
          rdma/ib: '1'
```

## Implementation Details

### Template Profile Configuration
P/D template profiles are defined in `orchestration/config.d/deployments.yaml`:

```yaml
template__pd:
  scheduler_manifest: {}  # simplified scheduler
  tensor_parallelism: FROM_NAME
  pd_config:
    prefill_pods: FROM_NAME
    decode_pods: FROM_NAME  
    scheduler_config: "high-throughput"
```

### Global P/D Configuration
P/D-specific VLLM args, environment variables, and resources:

```yaml
pd:
  vllm_extra:
    args:
      - --block-size 128
      - --kv-transfer-config '{"kv_connector":"NixlConnector", "kv_role":"kv_both"}'
    env:
      - name: VLLM_NIXL_SIDE_CHANNEL_HOST
        valueFrom:
          fieldRef:
            apiVersion: v1
            fieldPath: status.podIP
  resources:
    rdma/ib: '1'
```

### Parameter Extraction
The `_extract_value_from_profile_name()` function extracts values using regex patterns:

```python
# For tensor_parallelism (main container)
match = re.search(r"d\.tp(\d+)", profile_name)  # d.tp4 → 4
if not match:
    match = re.search(r"(?<!p\.)tp(\d+)", profile_name)  # tp4 (but not p.tp4) → 4

# For prefill_tensor_parallelism
match = re.search(r"p\.tp(\d+)", profile_name)  # p.tp1 → 1

# For decode_pods (main container replicas)  
match = re.search(r"d\.x(\d+)", profile_name)   # d.x2 → 2
if not match:
    match = re.search(r"(?<!p\.)x(\d+)", profile_name)  # x2 (but not p.x2) → 2

# For prefill_pods
match = re.search(r"p\.x(\d+)", profile_name)   # p.x8 → 8
```

### VLLM Configuration
P/D deployments combine profile VLLM args with P/D-specific args:

- **Base args**: From `deployments.defaults.vllm_extra_args` + profile `vllm_extra_args`
- **P/D args**: From `deployments.pd.vllm_extra.args`
- **Tensor parallelism**: Automatically added based on pod type (prefill vs decode)

### Matrix Expansion
P/D testing leverages the existing llm-d matrix expansion system. When you specify multiple deployment profiles, the system automatically generates RunSpecs for each combination:

```
Model × P/D Profile × Benchmark = RunSpec
```

Each RunSpec gets its own namespace and artifact directory for isolation.

## Best Practices

1. **Start Small**: Begin with `pd-smoke` for initial validation
2. **Use Matrix Testing**: Use `pd-matrix` for comprehensive validation across configurations
3. **Scale Gradually**: Progress from `pd-small` → `pd-medium` → `pd-large` as needed
4. **Monitor Resources**: P/D large configurations require significant GPU resources
5. **Custom Configs**: Use PR directives for ad-hoc testing of specific combinations

## Troubleshooting

### Resource Constraints
If tests fail due to insufficient resources:
- Start with `pd-small` profile
- Ensure your cluster has enough GPU nodes
- Check pod scheduling events in test artifacts

### Configuration Errors
- Validate P/D profiles exist: Check `deployments.yaml`
- Verify scheduler manifest: Check `pd-scheduler.yaml` template
- Review test labels: Check `__test_labels__.yaml` for P/D metadata

### Matrix Expansion Issues
- Confirm preset configuration: Review `presets.yaml`
- Check RunSpec generation: Look for multiple prepare directories
- Validate combinations: Ensure expected number of test runs

## Examples

### Single P/D Configuration
```bash
# Create a preset with P/D configuration
cat > my-pd-preset.yaml << EOF
runtime:
  model_name: Qwen/Qwen3-0.6B
  deployment_profile: pd-d.x1-p.tp1-d.tp2-p.x2
  benchmark_key: short
EOF

llm_d ci --preset my-pd-preset
```

### Matrix P/D Testing
```bash
# Use PR comment for matrix testing:
# /var runtime.deployment_profile: [pd-d.x1-p.tp1-d.tp2-p.x2, pd-d.x2-p.tp2-d.tp4-p.x4]
# /var runtime.model_name: [Qwen/Qwen3-0.6B, meta-llama/Llama-3.1-8B-Instruct]
# /var runtime.benchmark_key: short

llm_d ci  # Will use PR directive overrides
```

### Custom P/D with Different GPU Allocation
```bash
# Test asymmetric GPU allocation: many small prefill pods, few large decode pods
# Profile: pd-d.x1-p.tp1-d.tp8-p.x16
# 1 decode pod with 8 GPUs, 16 prefill pods with 1 GPU each

cat > asymmetric-pd.yaml << EOF  
runtime:
  model_name: meta-llama/Llama-3.1-8B-Instruct
  deployment_profile: pd-d.x1-p.tp1-d.tp8-p.x16
  benchmark_key: concurrent-1k-1k
EOF

llm_d ci --preset asymmetric-pd
```

### Validating P/D Profile Names
```bash
# Test profile name parsing
python3 -c "
from projects.llm_d.orchestration.runtime_config import _extract_value_from_profile_name

profile = 'pd-d.x2-p.tp1-d.tp4-p.x8'
print(f'Decode pods: {_extract_value_from_profile_name(profile, \"decode_pods\")}')
print(f'Prefill pods: {_extract_value_from_profile_name(profile, \"prefill_pods\")}') 
print(f'Decode TP: {_extract_value_from_profile_name(profile, \"tensor_parallelism\")}')
print(f'Prefill TP: {_extract_value_from_profile_name(profile, \"prefill_tensor_parallelism\")}')
"
```

This P/D testing system provides flexible, template-based configuration with automatic parameter extraction from profile names, enabling sophisticated testing of Prefill/Decode configurations at scale.
