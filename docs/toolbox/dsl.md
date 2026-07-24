# Toolbox DSL

The FORGE toolbox uses a small Python **domain-specific language** (DSL) so each command is a standalone script: a **public entrypoint** (plain function with typed parameters), a **linear list of tasks** (`@task` functions), and **verbose logging** under an artifact directory for post-mortem review.

## Command entrypoint and CLI

The public API for orchestration (or humans) is a single function, for example `run(...)`, documented with an `Args:` section in its docstring.

- **From Python**: call that function; it typically ends with `return execute_tasks(locals())` so every `@task` defined in the same module runs in registration order.
- **From the shell**: use `projects.core.dsl.toolbox.create_toolbox_main(run)` so `argparse` options are generated from the function signature (`toolbox.run_toolbox_command`).

Tasks are **not** the entrypoint; they are internal steps registered at import time.

## Tasks (`@task`)

Each task is a function `(args, ctx) -> value` (or return `None`).

- **`args`**: read-only view of the command parameters plus `artifact_dir` (see `projects.core.dsl.context.ReadOnlyArgs`).
- **`ctx`**: mutable per-task context that is merged back into a shared namespace after each task so later steps can read values previous tasks set on `ctx`.

Registration order in the file is execution order (see `ScriptManager`).

```python
@task
def ensure_project(args, ctx):
    """Describe the step; shown in logs."""
    ctx.project_ready = True
    return "ok"
```

## Conditional tasks (`@when`)

`@when` takes a **zero-argument** callable (usually `lambda:` …) evaluated when the task is reached. If it returns a falsy value, the task is skipped (logged as skipped).

**Decorator order:** write `@when(...)` on the line **above** `@task` (same as `@retry`). Python applies the bottom decorator first, so `@task` registers the step, then `@when` attaches the condition and updates the script registry entry so `execute_tasks` sees it.

```python
@when(lambda: some_other_task.status.return_value is True)
@task
def follow_up(args, ctx): ...
```

Because the condition is called with **no arguments**, anything dynamic must come from a **closure**, module-level state, or another task’s `.status.return_value` (see `TaskResult` in `script_manager.py`).

## Retries (`@retry`)

**Order:** `@retry(...)` above `@task` above `def` (same pattern as waiting for OpenShift resources).

By default, retries apply when the task **returns a falsy** value (`False`, `None`, `[]`, …). Each attempt runs the full `@task` wrapper (logging, result capture). Delays use `time.sleep`.

Parameters:

| Parameter | Meaning |
|-----------|---------|
| `attempts` | Maximum attempts |
| `delay` | Initial sleep in seconds before the next attempt |
| `backoff` | Multiplier for the delay after each retry |
| `retry_on_exceptions` | If `True`, **also** retry when the task raises (never retries on `KeyboardInterrupt`) |

```python
@retry(attempts=60, delay=30, backoff=1.0)
@task
def wait_until_ready(args, ctx):
    ...
    return False  # try again after delay
```

```python
@retry(attempts=5, delay=2, backoff=1.5, retry_on_exceptions=True)
@task
def call_flaky_api(args, ctx): ...
```

If all attempts fail, `RetryFailure` is raised (wrapped in `TaskExecutionError` during `execute_tasks`, with the underlying `RetryFailure` available as `TaskExecutionError.__cause__` when that applies).

## Always tasks (`@always`)

Mark cleanup or artifact steps that must run **after a failure** in the main sequence. `@always` may appear **above or below** `@task` on the same function (see `always()` in `task.py`).

If a normal task raises, remaining non-`@always` tasks are **skipped** (each pending non-always task is logged as skipped; its body is not run). Pending `@always` tasks still run in file order. The original error is re-raised after always-tasks finish (unless an always-task fails and becomes the primary error when there was none).

Place `@always` tasks **after** the main pipeline so they behave as teardown (see toolbox scripts under `projects/*/toolbox/`).

## Execution driver (`execute_tasks`)

`execute_tasks(locals())` (or a filtered dict of parameters):

- Opens a nested artifact directory (`env.NextArtifactDir`).
- Writes metadata (`_meta/metadata.yaml`, `_meta/restart.sh`) and `task.log`.
- Runs tasks from the **calling file** only (`ScriptManager` path must match `Path(__file__).relative_to(FORGE_HOME)` vs `os.path.relpath` at task definition — run commands from the repository root as the toolbox does).

Interrupts (`KeyboardInterrupt`, `SignalInterrupt`) stop execution and still emit completion banners where implemented (not covered by `test_dsl_toolbox.py`; see `runtime.py`).

### Trace and artifacts (post-mortem)

Each run is intended to be reviewable without re-executing the command:

| Output | Role |
|--------|------|
| `task.log` | Full DSL log stream for the run |
| `_meta/metadata.yaml` | Timestamp, command file, artifact dir, serialized arguments |
| `_meta/restart.sh` | Replay helper with the same CLI-style flags |
| Console / DSL logger | Step headers, skip lines (`==> SKIPPING TASK: …` when pending steps are skipped after a failure), retry banners |

Keep secrets out of entrypoint parameters where possible so they appear safely in metadata (follow project norms for redaction if you add any).

### Standalone parameters (entrypoint contract)

Declare orchestration and operator inputs on the **public entrypoint** (typed parameters and an `Args:` section in the docstring). Prefer those parameters (and values derived in tasks) over ad hoc reads of undeclared environment variables, so the command stays **standalone** and reviewable—except where FORGE already documents global conventions (for example `FORGE_HOME`, artifact layout).

## Related modules

- `projects.core.dsl.task` — `@task`, `@when`, `@retry`, `@always`, `RetryFailure`
- `projects.core.dsl.runtime` — `execute_tasks`, `TaskExecutionError`
- `projects.core.dsl.toolbox` — CLI wrapper
- `projects.core.dsl.shell`, `template`, … — helpers used inside tasks

## Tests

`projects/core/tests/test_dsl_toolbox.py` exercises the behaviors below. Run: `python -m pytest projects/core/tests/test_dsl_toolbox.py -v`.

| Area | What is asserted |
|------|------------------|
| Task order | `first` / `second` run in **source definition order** when all succeed. |
| Failure → skip | After a task raises, **later non-`@always`** tasks do not run: **`task.log`** has `SKIPPING TASK: …` and the “not @always” line; unique return markers for pending steps **do not** appear in the log. |
| Failure → `@always` | After a task raises, a **later** `@always` task **still runs** (event order); **`task.log`** contains that task’s return value; the failure re-raised is **`TaskExecutionError`** with the original **`RuntimeError`** as **`__cause__`**. |
| `@when` | If the predicate is falsy, the task body does not run. |
| `@retry` (falsy → success) | Falsy return values are retried until a truthy result. |
| `@retry` (falsy exhausted) | If every attempt returns falsy, **`RetryFailure`** is raised and wrapped so **`TaskExecutionError.__cause__`** is **`RetryFailure`**. |
| `@retry` (exceptions) | With `retry_on_exceptions=True`, exceptions are retried until success; if every attempt raises, **`TaskExecutionError.__cause__`** is **`RetryFailure`**. |
| Decorator stack | `@retry` / `@when` **without** `@task` raise **`TypeError` at definition time** with the “Put `@task` BELOW …” message. |
| Success return | `execute_tasks` returns **`shared_context`** with task attributes and **`artifact_dir`** set. |

Not in that file: interrupt handling (`KeyboardInterrupt` / `SignalInterrupt`), and CLI wiring (`create_toolbox_main` / `run_toolbox_command`)—those are documented above but not exercised by these unit tests.
