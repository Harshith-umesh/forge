# Benchmark Isolation: Per-Workload Deployment

## Why each benchmark needs a fresh deployment

The llm_d test harness deploys an LLMInferenceService (LLMISVC) for each
benchmark workload rather than sharing a single deployment across workloads.
Three factors drive this design:

### 1. `--max-model-len` affects performance

The vLLM serving engine's `--max-model-len` parameter controls the maximum
sequence length and directly impacts memory allocation, scheduling behavior,
and throughput. Different workloads have different token distributions:

- `short` uses `prompt_tokens=256, output_tokens=128`
- `heavy-heterogeneous` uses prompts up to 30,000 tokens

Running `heavy-heterogeneous` against a server configured with
`--max-model-len=4096` would truncate or reject requests, while running
`short` against `--max-model-len=32768` wastes GPU memory on unused KV-cache
blocks. Workloads can provide `deployment_overrides` to set the right
`--max-model-len` for their token distribution.

### 2. KV-cache pollution across workloads

When multiple benchmarks share a deployment, residual KV-cache entries from
the first workload can affect the performance measurements of subsequent
workloads. The current mitigation relies on guidellm's prefixed prompts to
generate unique cache keys per benchmark. This works for standard load
generation but is not a guarantee of isolation.

### 3. Future trace-replay workloads

Trace-replay workloads (replaying production request traces) will require
starting the vLLM server with `VLLM_DEV_MODE=1` so that the KV-cache can
be explicitly cleared between runs. This is a deployment-time environment
variable, not a runtime toggle, reinforcing the need for per-workload
deployment isolation.

## Architecture: 3D RunSpec matrix

The test matrix is a Cartesian product of three dimensions:

```
model_name x deployment_profile x benchmark_key
```

Each combination (a `RunSpec`) gets:
1. Its own Kubernetes namespace
2. A fresh LLMISVC deployment with the profile's vLLM args
3. A smoke test to verify the deployment is healthy
4. A single benchmark run
5. Full teardown (LLMISVC, benchmark job, PVC, namespace resources)

### Example

```
/var runtime.model_name: Qwen/Qwen3-0.6B
/var runtime.deployment_profile: [approximate-prefix-cache, distributed-default]
/var runtime.benchmark_key: [short, multi-turn]
```

Produces 1 x 2 x 2 = 4 isolated test runs, each with its own namespace:

| RunSpec | Model | Profile | Benchmark |
|---------|-------|---------|-----------|
| 1 | Qwen3-0.6B | approximate-prefix-cache | short |
| 2 | Qwen3-0.6B | approximate-prefix-cache | multi-turn |
| 3 | Qwen3-0.6B | distributed-default | short |
| 4 | Qwen3-0.6B | distributed-default | multi-turn |

## Backward compatibility

| Scenario | `benchmark_key` value | Matrix | Behavior |
|----------|-----------------------|--------|----------|
| Smoke-only | `null` | M x P | No benchmark, just smoke test |
| Single benchmark | `"short"` | M x P x 1 | One benchmark per deploy (same as before) |
| Multiple benchmarks | `["a", "b"]` | M x P x 2 | Each gets its own deployment |

When `benchmark_key` is null or a single scalar, the matrix degrades to the
previous 2D behavior with no overhead.

## Per-workload deployment overrides

Workloads can override deployment profile settings via `deployment_overrides`
in `config.d/workloads.yaml`. This dict is deep-merged on top of the resolved
deployment profile when building the LLMISVC manifest.

```yaml
benchmarks:
  heavy-heterogeneous:
    args:
      backend_type: openai_http
      rate_type: concurrent
      rate: [300, 200, 100, 50, 1]
      data: prompt_tokens=8000,...
    deployment_overrides:
      vllm_args:
        - --max-model-len=32768

  trace-replay:
    args:
      backend_type: openai_http
      ...
    deployment_overrides:
      env:
        - name: VLLM_DEV_MODE
          value: "1"
```
