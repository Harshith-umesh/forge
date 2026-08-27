# CPT Config Structure — How to Write a Forge Launcher for the CPT Matrix

This document describes the configuration file layout used by the rhaiis project
to define CPT (Continuous Performance Testing) pipelines. Understanding this
structure is a prerequisite for building a Forge launcher that expands a CPT
definition into individual FournosJob submissions.

## Directory Layout

```
projects/rhaiis/orchestration/
├── config.yaml              # Top-level defaults (vaults, benchmark config, caliper settings)
├── config.d/
│   ├── rhaiis.yaml          # Engine/deploy/runtime defaults (images, ports, GPU types, ...)
│   ├── models.yaml          # Model registry (hf_model_id, vllm_args per model key)
│   └── workloads.yaml       # Workload profiles (data shape, rates, durations)
├── presets.d/
│   ├── presets.yaml         # Named presets: accelerator, engine, model aliases, workload aliases
│   ├── clusters.yaml        # Cluster-specific overrides (image pull secrets, fs_group, ...)
│   └── benchmarks.yaml      # Benchmark preset (enables profiler, dashboard, notifications)
└── cpt.d/
    └── cpt.yaml             # CPT pipeline definitions (the matrix)
```

## Config Resolution Order

When a job is launched, configuration is layered bottom-up:

1. **config.yaml** — base defaults (vault list, benchmark tool config, caliper/export settings)
2. **config.d/rhaiis.yaml** — engine images, deploy settings, accelerator env vars, S3, profiler
3. **config.d/models.yaml** — looked up by `tests.rhaiis.model_key`; provides `hf_model_id` and engine args
4. **config.d/workloads.yaml** — looked up by `tests.rhaiis.workload_key`; provides `data`, `rates`, `max_seconds`
5. **presets.d/presets.yaml** — named groups of config overrides, selected by CLI args (e.g. `nvidia`, `vllm`, `llama-70b`, `profile1`)
6. **presets.d/clusters.yaml** — cluster-specific overrides, selected by cluster name
7. **cpt.d/cpt.yaml** — pipeline-level globals and per-model overrides (highest priority)

Later layers override earlier ones. `configOverrides` in the FournosJob spec override everything.

## Model Registry (`config.d/models.yaml`)

Each key is a **model_key** referenced by presets and CPT pipelines:

```yaml
llama-3-3-70b-fp8:
  name: Llama-3.3-70B-Instruct-FP8
  hf_model_id: RedHatAI/Llama-3.3-70B-Instruct-FP8-dynamic
  vllm_args:
    tensor-parallel-size: 4
    kv-cache-dtype: fp8
```

- `hf_model_id` — HuggingFace model path (or custom registry path)
- `vllm_args` — engine-specific arguments; `tensor-parallel-size` determines GPU count

## Workload Profiles (`config.d/workloads.yaml`)

Each key is a **workload_key**:

```yaml
profile1:
  data: "prompt_tokens=1000,output_tokens=1000"
  rates: [1, 50, 100, 200, 300]
  max_seconds: 450
```

- `data` — GuideLLM data specification (token shapes, distributions)
- `rates` — concurrency levels to sweep
- `max_seconds` — benchmark timeout per rate

## Presets (`presets.d/presets.yaml`)

A preset file with `__multiple: true` allows combining multiple presets in a
single invocation. Each preset is a flat map of dotted config keys → values:

```yaml
__multiple: true

nvidia:
  rhaiis.accelerator: nvidia

vllm:
  rhaiis.engine: vllm

llama-70b:
  tests.rhaiis.model_key: llama-3-3-70b-fp8

profile1:
  tests.rhaiis.workload_key: profile1
```

When the launcher is called with `args: [ci-test, nvidia, vllm, hera, llama-70b]`,
each arg is looked up as a preset and the resulting overrides are merged left-to-right.

## CPT Pipeline Definitions (`cpt.d/cpt.yaml`)

This is the core file for defining test matrices.

### Top-Level Marker

```yaml
__cpt: true
```

Signals that this file contains CPT pipeline definitions (not regular presets).

### Pipeline Structure

```yaml
<pipeline-name>:
  # ── Meta fields (prefixed with __) ──
  __description: "Human-readable description"
  __engine: vllm | sglang | trtllm
  __accelerator: nvidia | amd        # optional, defaults to nvidia
  __models:
    <preset-name>/tp<N>:             # model alias from presets.yaml + GPU count
    <preset-name>/tp<N>:
      <dotted.config.key>: value     # per-model overrides (optional)
  __workloads:
    - <workload-preset-name>         # from presets.yaml (maps to workload_key)
    - ...

  # ── Global overrides (applied to every job in this pipeline) ──
  rhaiis.profiler.enabled: true
  tests.rhaiis.run_benchmark: true
  caliper.postprocess.csv_dashboard.enabled: true
  tests.rhaiis.slack_notify_always: true
  rhaiis.agent_analysis.enabled: false
```

### Model Entries

Model keys under `__models` use the format `<preset>/tp<N>`:

- `<preset>` — an alias from `presets.d/presets.yaml` (e.g. `llama-70b` → `tests.rhaiis.model_key: llama-3-3-70b-fp8`)
- `tp<N>` — the tensor-parallel size (GPU count) for this job

If the value is `null` (or omitted), only the pipeline-level globals apply.
If the value is a map, those entries are additional config overrides for that
specific model (e.g. to override `tensor-parallel-size`):

```yaml
__models:
  llama-70b/tp4:           # uses model's default tp=4, no extra overrides
  llama-70b/tp2:           # override tp to 2 for this specific entry
    rhaiis.engines.vllm.args.tensor-parallel-size: 2
```

### Workload Entries

```yaml
__workloads:
  - ci-quick
  - profile1
  - profile2
```

Each entry is a preset name from `presets.d/presets.yaml` that sets
`tests.rhaiis.workload_key`.

### Matrix Expansion

A CPT pipeline generates **|models| × |workloads|** jobs. For example,
`cpt-vllm-release` with 9 models × 5 workloads = 45 individual benchmark jobs.

## How the Fournos UI Currently Submits CPT Jobs

The UI plugin (`fournos-ui/app/projects/rhaiis.py`, see
[fournos PR #117](https://github.com/openshift-psap/fournos/pull/117)) implements
the CPT flow as follows:

### Job Granularity: One Job Per Model (not per model×workload)

The UI creates **one FournosJob per model entry** in `__models`. All workloads
are passed as a list in `tests.rhaiis.workload_keys` so the single job iterates
over them internally during its test phase.

This means a pipeline with 9 models × 5 workloads = **9 jobs** (not 45).

### Submit Flow (`POST /api/submit-cpt`)

The UI sends a JSON payload:
```json
{
  "models": [{"name": "llama-70b/tp4", "preset": "llama-70b", "tp": 4, "overrides": {}}],
  "workloads": ["ci-quick", "profile1", "profile2", "profile3", "profile4"],
  "accelerator": "nvidia",
  "engine": "vllm",
  "cluster": "hera",
  "pipeline": "forge-full",
  "owner": "fournos-dashboard",
  "priority": "manual",
  "version_label": "vLLM-0.24.0-CPT",
  "overrides": {"rhaiis.profiler.enabled": true, ...}
}
```

For each model entry, the backend:

1. **Resolves the model preset** → looks up `model_entries[preset].overrides["tests.rhaiis.model_key"]`
2. **Determines GPU count** — from the `tp` field (parsed from `/tp<N>` suffix), falling back to `models.yaml[model_key].vllm_args.tensor-parallel-size`
3. **Determines GPU type** — from `clusters.yaml` or defaults (`h200` for hera/zeus)
4. **Builds `configOverrides`** — merges: pipeline-level overrides → per-model overrides → `tests.rhaiis.workload_keys: [all workloads]`
5. **Builds the FournosJob spec**:
   ```yaml
   spec:
     cluster: hera
     displayName: rhaiis-cpt-llama-70b-hera
     hardware:
       gpuType: h200
       gpuCount: 4
     executionEngine:
       forge:
         project: rhaiis
         args: [nvidia, vllm, hera, llama-70b]
         configOverrides:
           tests.rhaiis.workload_keys: [ci-quick, profile1, profile2, profile3, profile4]
           tests.rhaiis.version: "vLLM-0.24.0-CPT"
           rhaiis.profiler.enabled: true
           ...per-model overrides...
     secretRefs: [psap-forge-dashboard-s3, psap-forge-notifications]
   ```
6. **Submits** via `k8s_client.create_fournos_job(body)`

### Key Differences from a Hypothetical CLI Launcher

| Aspect | UI (current) | CLI launcher (future) |
|--------|-------------|----------------------|
| Job granularity | 1 job per model (workloads as list) | Could be 1 per model×workload |
| Config source | Fetches from GitHub API at runtime | Reads local files |
| Args format | `[accelerator, engine, cluster, model]` | Same |
| Workload selection | `tests.rhaiis.workload_keys: [...]` | Same, or `workload_key` singular |

## How a Forge CLI Launcher Should Work

A CLI launcher should mirror what the UI does:

1. **Parse `cpt.d/cpt.yaml`** — iterate over each pipeline definition.
2. **For each model in `__models`**:
   a. Parse `<preset>/tp<N>` → extract preset name and TP size
   b. Resolve preset → `tests.rhaiis.model_key` from `presets.d/presets.yaml`
   c. Look up `models.yaml[model_key]` → get `hf_model_id`, validate TP
   d. Merge: pipeline globals → per-model overrides → `tests.rhaiis.workload_keys: __workloads`
3. **Determine hardware**:
   - `gpuCount` = TP size (from `/tp<N>`)
   - `gpuType` = from `clusters.yaml` or `rhaiis.gpu_types[accelerator]`
4. **Build the FournosJob** — set:
   - `spec.executionEngine.forge.args`: `[<accelerator>, <engine>, <cluster>, <model-preset>]`
   - `spec.executionEngine.forge.configOverrides`: merged config overrides
   - `spec.hardware`: `{gpuCount, gpuType}`
   - `spec.cluster`: target cluster
   - `spec.secretRefs`: from `config.yaml.vaults`
5. **Submit** — create the FournosJob CR.

### Example: Expanding one model entry

Given:
```yaml
cpt-vllm-release:
  __engine: vllm
  __models:
    llama-70b/tp2:
      rhaiis.engines.vllm.args.tensor-parallel-size: 2
  __workloads:
    - profile1
    - profile2
  rhaiis.profiler.enabled: true
  tests.rhaiis.run_benchmark: true
```

Produces **one** FournosJob with:
- `args: [nvidia, vllm, hera, llama-70b]`
- `configOverrides`:
  ```yaml
  tests.rhaiis.workload_keys: [profile1, profile2]
  rhaiis.engines.vllm.args.tensor-parallel-size: 2
  rhaiis.profiler.enabled: true
  tests.rhaiis.run_benchmark: true
  ```
- `hardware: {gpuCount: 2, gpuType: h200}`

