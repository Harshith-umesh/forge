---
name: forge-review
description: >
  Pre-commit review that diffs local changes against the remote branch and checks
  for FORGE AGENTS.md rule violations. Use before committing, when the user says
  "review my changes", "check before commit", "pre-commit review", or "forge review".
---

# FORGE Pre-Commit Review

Review local code changes against the FORGE project rules defined in `AGENTS.md` before committing.

## Trigger

Run this review **before every commit**. If the user asks to commit, run this review first and present findings before proceeding.

## Procedure

### Step 1: Gather the diff

Run the diff gathering script:

```bash
./scripts/gather-diff.sh
```

The script outputs structured sections:
- `=== DIFF_META ===` — branch name, remote, timestamp
- `=== STATUS ===` — `git status --short`
- `=== DIFF_TYPE: staged|unstaged|branch|none ===` — the actual diff, prioritizing staged > unstaged > branch
- `=== UNSTAGED_WARNING ===` — warns if unstaged changes exist alongside staged (won't be committed)
- `=== UNTRACKED ===` — new files not yet tracked by git

For any files listed under `UNTRACKED`, read their full content — they won't appear in the diff output.

### Step 2: Load the rules

Read the `AGENTS.md` file from the repository root. This contains all project-wide rules that code must follow. Parse each `##` section as a distinct rule category.

### Step 3: Analyze each rule against the diff

For every added or modified line in the diff (`+` lines), check against each rule category:

#### Secret Handling (CRITICAL — blocks commit)
- `oc_apply()` called with a path under `ARTIFACT_DIR` on a Secret-kind manifest
- Secret data logged via `logger.info/debug/warning(f"...{secret}...")`
- Tokens, passwords, credentials in error messages or `raise` statements
- Environment variables used to pass secrets between components
- Pod env vars with inline secret values instead of Secret references
- Any manifest with `kind: Secret` written to an artifact path

#### Error Handling
- Bare `except Exception` that only logs a warning and continues
- Functions returning empty string `""` or `None` on failure instead of raising — **except** inside
  `@retry`-decorated tasks, where returning a falsy value (`False`, `None`, `""`, `[]`) is the
  intended mechanism to trigger a retry. Only flag falsy returns as error swallowing when the
  function does NOT have a `@retry` decorator above `@task`.
- `logger.warning(...)` as the sole response to a failure condition
- Missing `ci.add_notification_file()` for operational failures that need visibility

#### Layer Isolation
- Any file under `projects/*/toolbox/` importing from `projects/*/orchestration/`
- Toolbox code reading config via `config.project.get_config()` directly instead of receiving values through `run()` parameters
- Manifest/template files placed outside their toolbox command directory
- **Caliper orchestration vs engine**: The `projects/caliper/engine/` layer contains pure
  data-processing logic (parsing artifacts, building the unified model, computing KPIs,
  rendering visualizations). The `projects/caliper/orchestration/` layer drives the engine
  via CLI subprocess calls and handles CI concerns (step sequencing, status files, logging,
  notifications, final status computation). Orchestration code must not duplicate or inline
  engine logic — e.g. directly traversing artifact trees, computing KPI values, rendering
  plots, or manipulating the unified model. If new data-processing capability is needed,
  add it to the engine and expose it through the CLI; orchestration only invokes it.

#### No time.sleep()
- `time.sleep(N)` used to wait for cluster state (pod ready, operator deployed, resource status)
- Any hardcoded delay where `@retry` polling should be used instead
- When flagging `time.sleep()`, check whether the containing task already has `@retry`. If not,
  suggest adding `@retry(attempts=N, delay=M, backoff=1.0)` above `@task` and replacing the
  sleep with a polling check that returns falsy to trigger retry

#### Config Type Coercion
- `int()`, `float()`, `bool()`, `str()` wrapping `config.project.get_config()` return values
- Custom bool/int coercion helpers for YAML config values

#### Framework Duplication
- Re-implementing preset handling that `config.init()` already does
- Manual vault initialization that duplicates `vault.init()`
- Custom `/test` or `/pipeline` directive parsing that framework handles

#### Environment Variable Hygiene
- `os.environ[KEY] = value` without a corresponding `finally` cleanup
- Temporary env var mutation without saving and restoring the original value

### Step 4: Report findings

Format each finding as:

```
<file>:<line>: <severity> <rule>: <problem>. <fix>.
```

Severity levels:
- `🔴 BLOCK` — Secret handling violations. **Must fix before commit.**
- `🟡 WARN` — Error swallowing, layer violations, sleep usage. Should fix.
- `🔵 NOTE` — Type coercion, env hygiene, framework duplication. Improve if easy.

#### Output structure

```
## FORGE Pre-Commit Review

### Diff summary
<N files changed, A insertions, D deletions>

### Findings
<findings grouped by severity, BLOCK first>

### Verdict
✅ PASS — no rule violations found, safe to commit.
⚠️  WARN — <N> warnings found. Review recommended before commit.
🚫 BLOCK — <N> critical violations. Must fix before commit.
```

If verdict is PASS, proceed with the commit. If BLOCK, do not commit — show findings and offer to fix. If WARN, show findings and ask the user whether to proceed or fix first.

### Step 5: Offer fixes

For each finding, offer a concrete fix. If the user agrees, apply fixes and re-run the review to confirm the violations are resolved before committing.

## Scope

Only review files under `projects/`. Ignore:
- `docs/` (documentation only)
- `fournos/gitops/` (Tekton pipelines, different patterns)
- Test files (`test_*.py`, `conftest.py`) — unless they contain secret handling code
- Configuration files (`*.yaml`, `*.json`) — unless they contain inline secrets
