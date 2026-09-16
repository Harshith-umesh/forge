# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is FORGE

FORGE (Framework for Orchestrating Runtime Gen-AI Experiments) is a CI-first testing framework for benchmarking AI/ML workloads on OpenShift clusters. It deploys inference runtimes (vLLM, SGLang, TensorRT-LLM) via KServe, runs GuideLLM benchmarks, and feeds results through Caliper post-processing into dashboards (S3 CSV), MLflow, and OpenSearch.

FORGE works with [Fournos](https://github.com/openshift-psap/fournos) for job orchestration — Fournos submits and manages the K8s jobs, FORGE provides the test logic.

## Build & Development

```bash
pip install -e .                    # core only
pip install -e '.[dev]'             # dev (ruff, pytest, pre-commit)
pip install -e '.[caliper]'         # caliper backends (opensearch, boto3, mlflow)
pip install -e '.[testing]'         # test dependencies
```

### Lint & Format

```bash
ruff check projects/                # lint (pre-commit runs this)
ruff format projects/               # format
ruff check --fix projects/          # auto-fix lint issues
```

Ruff config: `pyproject.toml` — line-length 100, target py312. `projects/matrix_benchmarking` and `projects/llm_d_legacy` are excluded from linting.

### Tests

```bash
pytest                                        # all tests (paths in pyproject.toml)
pytest projects/core/tests/                   # core tests only
pytest projects/core/tests/test_dsl_toolbox.py  # single file
pytest -m "not slow"                          # skip slow tests
pytest -m integration                         # integration tests only
```

Test paths configured in `pyproject.toml`: `projects/core/tests`, `projects/llm_d/tests`, `projects/caliper/tests`.

### Container Dev Environment

```bash
./bin/forge_launcher build      # build container image
./bin/forge_launcher recreate   # create/recreate dev container
./bin/forge_launcher enter      # enter container shell
```

## Architecture

Each project under `projects/` follows a two-layer pattern:

- **`orchestration/`** — upper layer: CI entrypoints (`ci.py`), CLI (`cli.py`), config (`config.yaml`), and test orchestration. Uses Click for CLI, the core config system for YAML-based configuration.
- **`toolbox/`** — lower layer: individual actions that modify cluster state (deploy, wait, cleanup). Each action is a module under `toolbox/` with a `main.py`.

### Core Framework (`projects/core/`)

- **`dsl/`** — Domain Specific Language for K8s task execution, shell helpers, template rendering
- **`ci_entrypoint/`** — `run_ci`/`run_cli` entrypoints that dispatch `<project> <operation>` to `projects/<project>/orchestration/<operation>.py`
- **`notifications/`** — GitHub + Slack notification system
- **`library/`** — shared utilities: config management, vault secrets, CI helpers, caliper export
- **`nightly/`** — nightly image-version-check logic

### Key Projects

- **`rhaiis/`** — RHAIIS inference benchmarking pipeline (the primary active project). Deploys KServe InferenceServices, runs GuideLLM benchmarks, does regression analysis.
- **`caliper/`** — Post-processing engine: parses artifacts, generates KPIs, exports to MLflow/S3/OpenSearch, produces CSV dashboards.
- **`mcp_gateway/`** — MCP Gateway testing with its own Slack notification provider.
- **`fournos_launcher/`** — Integration with Fournos job orchestration.

### Configuration System

YAML-based hierarchical config (`projects/core/library/config.py`). Each project has:
- `config.yaml` — default config
- `config.d/*.yaml` — additional config files (models, workloads, etc.)
- `presets.d/*.yaml` — named presets that override config values

Access: `config.project.get_config("dotted.key.path", default)`. Config can be overridden via PR directives or environment variables.

### Notification System

Two notification paths:

1. **Provider-based** (`projects/core/notifications/provider.py`): Subclass `SlackNotificationProvider`, implement `get_channel_id()` and `format_message()`. Wire up via `notifications.slack.provider_module` in config.yaml. Called automatically by `caliper_export_entrypoint`. See `mcp_gateway/orchestration/notifications.py` for a complete example.

2. **Direct** (`projects/rhaiis/postprocess/regression.py`): `send_regression_notification()` and `send_failure_notification()` send directly via `_send_via_topsail_bot()`. Used by RHAIIS for regression alerts and pipeline failure alerts.

Both use `topsail-bot.slack-token` from the `psap-forge-notifications` vault.

### Vault System

Secrets are managed via vaults (`projects/core/library/vault.py`). Vault definitions live in `vaults/`. Access secrets with `vault.get_vault_content_path(vault_name, secret_file)`. **Never write secrets to ARTIFACT_DIR** — see AGENTS.md for detailed rules.

### RHAIIS Pipeline Flow

1. `ci.py prepare` → prepare cluster
2. `ci.py test` → `test_rhaiis.test()` → `test_phase.run()`:
   - Deploy KServe InferenceService (ServingRuntime + ISVC manifests)
   - Wait for readiness
   - Optional warmup or profiler step
   - Run GuideLLM benchmarks per workload key
   - Create test labels (`__test_labels__.yaml`)
   - Caliper post-processing: parse → KPIs → CSV → S3 sync
   - Regression analysis against previous version
3. `ci.py post_cleanup` → cleanup + pipeline failure notification check
4. `ci.py caliper-export` → export artifacts to MLflow/S3

### Notification Context

`slack_user` for RHAIIS notifications comes from `tests.rhaiis.slack_user` in config. It flows to:
- `send_failure_notification()` in `projects/rhaiis/postprocess/regression.py`
- `send_regression_notification()` in `projects/rhaiis/postprocess/regression.py`
- `send_pipeline_failure_alert()` / `send_pipeline_warning()` in `projects/rhaiis/orchestration/notifications.py`

The `user_line` (Triggered by) is only included when `slack_user` is non-empty. The success notification flows through `caliper_export_entrypoint` → `send_notification()` → per-project `SlackNotificationProvider` (if configured).

## CI Entrypoints

```bash
bin/run_ci <project> <operation>    # CI mode (e.g., bin/run_ci rhaiis test)
bin/run_cli <project> <operation>   # CLI mode (e.g., bin/run_cli rhaiis prepare)
```

Both dispatch to `projects/<project>/orchestration/<operation>.py` (CI) or `projects/<project>/orchestration/cli.py <operation>` (CLI).
