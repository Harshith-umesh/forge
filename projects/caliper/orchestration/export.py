"""
Config-driven Caliper artifact export for FORGE orchestration projects (e.g. skeleton).

Validates :class:`~projects.caliper.orchestration.export_config.CaliperOrchestrationExportConfig`
and calls :func:`projects.caliper.engine.file_export.run_artifacts_export` /
:func:`projects.caliper.engine.file_export.run_multi_run_artifacts_export`.
"""

from __future__ import annotations

import copy
import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from projects.caliper.engine.file_export.artifacts_export_run import (
    discover_run_dirs,
    run_artifacts_export,
    run_multi_run_artifacts_export,
)
from projects.caliper.engine.file_export.mlflow_config import load_mlflow_config_yaml
from projects.caliper.orchestration.export_config import (
    CaliperOrchestrationExportConfig,
)
from projects.core.library import env
from projects.core.library import vault as vault_lib

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Run naming helpers
# ---------------------------------------------------------------------------

_TIMESTAMP_RE = re.compile(r"(\d{8}-\d{6})")
_DIR_INDEX_RE = re.compile(r"^(\d+__)")


def _extract_timestamp_from_fjob() -> str:
    """Extract YYYYMMDD-HHMMSS timestamp from FJOB_NAME env var."""
    fjob = os.environ.get("FJOB_NAME", "")
    m = _TIMESTAMP_RE.search(fjob)
    return m.group(1) if m else ""


def _read_test_labels(run_dir: Path) -> dict[str, str]:
    """Read labels from __test_labels__.yaml in a run directory."""
    marker = run_dir / "__test_labels__.yaml"
    if not marker.is_file():
        return {}
    try:
        data = yaml.safe_load(marker.read_text(encoding="utf-8"))
        labels = data.get("labels", {}) if isinstance(data, dict) else {}
        return labels if isinstance(labels, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


def _format_run_name(template: str, labels: dict[str, str], *, prefix: str, timestamp: str) -> str:
    """Format a run name template using labels plus computed fields (prefix, timestamp)."""
    fields = {**labels, "prefix": prefix, "timestamp": timestamp}
    try:
        return template.format_map(fields)
    except (KeyError, ValueError) as e:
        logger.warning("Run naming template error (%s), falling back to raw template", e)
        return template


def format_child_run_name(
    run_dir: Path,
    *,
    template: str,
    prefix: str,
    timestamp: str,
) -> str:
    """Build a child run name: preserve the NNN__ index prefix, apply the template for the rest."""
    dir_name = run_dir.name
    m = _DIR_INDEX_RE.match(dir_name)
    num_prefix = m.group(1) if m else ""
    labels = _read_test_labels(run_dir)
    formatted = _format_run_name(template, labels, prefix=prefix, timestamp=timestamp)
    return f"{num_prefix}{formatted}"


def resolve_run_names(
    run_dirs: list[Path],
    mlflow_config_data: dict[str, Any] | None,
    *,
    fallback_run_name: str | None = None,
) -> dict[str, str | None | dict[Path, str]]:
    """Resolve parent and child run names from ``run_naming`` config + test labels.

    Returns a dict with keys:
        parent_run_name: str | None
        single_run_name: str | None
        child_run_names: dict[Path, str] (mapping run_dir -> formatted name)
    """
    naming_cfg = (mlflow_config_data or {}).get("run_naming")
    if not naming_cfg or not isinstance(naming_cfg, dict):
        return {
            "parent_run_name": fallback_run_name,
            "single_run_name": None,
            "child_run_names": {},
        }

    prefix = naming_cfg.get("prefix", "")
    timestamp = _extract_timestamp_from_fjob()
    single_tpl = naming_cfg.get("single_run")
    parent_tpl = naming_cfg.get("parent_run")
    child_tpl = naming_cfg.get("child_run")

    result: dict[str, Any] = {
        "parent_run_name": fallback_run_name,
        "single_run_name": None,
        "child_run_names": {},
    }

    if len(run_dirs) == 1 and single_tpl:
        labels = _read_test_labels(run_dirs[0])
        result["single_run_name"] = _format_run_name(
            single_tpl, labels, prefix=prefix, timestamp=timestamp
        )

    if len(run_dirs) > 1 and parent_tpl:
        labels = _read_test_labels(run_dirs[0])
        result["parent_run_name"] = _format_run_name(
            parent_tpl, labels, prefix=prefix, timestamp=timestamp
        )

    if child_tpl:
        child_names: dict[Path, str] = {}
        for rd in run_dirs:
            child_names[rd] = format_child_run_name(
                rd, template=child_tpl, prefix=prefix, timestamp=timestamp
            )
        result["child_run_names"] = child_names

    return result


def run_from_orchestration_config(
    caliper_cfg: dict[str, Any] | None,
) -> int:
    """
    Run Caliper file export from orchestration config.

    Pass:

    * ``caliper.export`` from :func:`get_config` (inner mapping only), or
    * The full ``caliper`` object with an ``export`` key.

    Backends are selected only via flags such as ``backend.mlflow.enabled`` (not a
    free-form backend name list).

    If ``backend.mlflow.secrets`` uses the ``vault: { name, key }`` form, the process must
    have called :func:`projects.core.library.vault.init` with that vault name (as in the
    top-level ``vaults:`` list in project config) so :func:`vault.get_vault_content_path`
    can return the secrets file path.
    """

    try:
        export_cfg = CaliperOrchestrationExportConfig.model_validate(caliper_cfg["export"])
    except (ValidationError, ValueError) as e:
        logger.error("Invalid caliper export config: %s", e)
        raise

    raw_from = export_cfg.from_path
    if raw_from is None or (isinstance(raw_from, str) and not raw_from.strip()):
        raise ValueError("caliper.export.from is not set")
    from_path = Path(raw_from)
    if not from_path.exists():
        raise FileNotFoundError(f"caliper.export.from does not exist: {from_path}")

    backends = export_cfg.backend_list
    mlflow_backend_cfg = export_cfg.backend.mlflow

    status_yaml = env.ARTIFACT_DIR / "status.yaml"

    if "mlflow" not in backends:
        raise ValueError(
            f"only 'mlflow' backend export is supported at the moment (got '{' '.join(backends)}')."
        )

    vault_name = export_cfg.backend.mlflow.secrets.vault.name
    vault_mlflow_secret = export_cfg.backend.mlflow.secrets.vault.mlflow_secret
    mlflow_secrets_path = vault_lib.get_vault_content_path(vault_name, vault_mlflow_secret)

    if mlflow_secrets_path is None:
        raise ValueError(f"Vault {vault_name}/{vault_mlflow_secret} missing :/")
    elif not mlflow_secrets_path.exists():
        raise FileNotFoundError(f"Vault {vault_name}/{vault_mlflow_secret} file missing :/")

    raw_cfg = mlflow_backend_cfg.config
    mlflow_config_data: dict[str, Any] | None = None
    if raw_cfg is None:
        pass
    elif isinstance(raw_cfg, dict):
        mlflow_config_data = copy.deepcopy(raw_cfg)
    else:
        mlflow_config_data = load_mlflow_config_yaml(Path(raw_cfg).expanduser().resolve())

    run_dirs = discover_run_dirs(from_path)

    # Resume a pre-created MLflow run if the test step left a marker or test labels
    mlflow_run_id = export_cfg.mlflow_run_id or _discover_precreated_mlflow_run_id(from_path)

    # Resolve descriptive run names from labels + run_naming config
    naming = resolve_run_names(
        run_dirs, mlflow_config_data, fallback_run_name=export_cfg.mlflow_run_name
    )

    if len(run_dirs) > 1:
        if export_cfg.dry_run:
            logger.info(
                "dry-run: would export %d run dirs from %s (skipping)", len(run_dirs), from_path
            )
            ret = 0
        else:
            ret = run_multi_run_artifacts_export(
                from_path=from_path,
                run_dirs=run_dirs,
                backend=backends,
                mlflow_experiment=export_cfg.mlflow_experiment,
                mlflow_run_name=naming.get("parent_run_name"),
                mlflow_secrets_path=mlflow_secrets_path,
                mlflow_config_data=mlflow_config_data,
                mlflow_run_id=mlflow_run_id,
                child_run_names=naming.get("child_run_names") or {},
                verbose=export_cfg.verbose,
                status_yaml_path=status_yaml,
                upload_workers=export_cfg.upload_workers,
            )
    else:
        effective_name = (
            naming.get("single_run_name") if len(run_dirs) == 1 else None
        ) or export_cfg.mlflow_run_name

        mlflow_kwargs: dict[str, Any] = {
            "mlflow_experiment": export_cfg.mlflow_experiment,
            "mlflow_run_id": mlflow_run_id,
            "mlflow_run_name": effective_name,
            "mlflow_secrets_path": mlflow_secrets_path,
        }
        if mlflow_config_data is not None:
            mlflow_kwargs["mlflow_config_data"] = mlflow_config_data

        ret = run_artifacts_export(
            from_path=from_path,
            status_yaml_path=status_yaml,
            dry_run=export_cfg.dry_run,
            verbose=export_cfg.verbose,
            upload_workers=export_cfg.upload_workers,
            backend=backends,
            **mlflow_kwargs,
        )

    if ret != 0:
        raise RuntimeError(f"Caliper export failed (ret code = {ret})")

    with open(status_yaml) as f:
        return yaml.safe_load(f.read())


MLFLOW_PRECREATED_RUN_MARKER = "__mlflow_precreated_run__.yaml"
"""Marker file written by the test step to communicate the pre-created MLflow
run ID and experiment ID to the export step.

The test step pre-creates an MLflow run (immediately ended) so that the
``run_id`` and ``experiment_id`` are known *before* CSV generation.  These IDs
are embedded in test labels and flow into the dashboard CSV.  The export step
later discovers the marker via :func:`_discover_precreated_mlflow_run_id` and
resumes the same run (``mlflow.start_run(run_id=...)``) to upload artifacts,
avoiding a duplicate run.

The marker is a YAML file with keys ``run_id`` and ``experiment_id``, written
to ``ARTIFACT_DIR`` by :func:`precreate_mlflow_run`.
"""


def precreate_mlflow_run(
    *,
    secrets_path: Path,
    experiment: str | None = None,
    workspace: str | None = None,
) -> dict[str, str]:
    """Pre-create an MLflow run and write the marker file to ``ARTIFACT_DIR``.

    The run is created and immediately ended (status FINISHED).  The export step
    will resume it via ``mlflow.start_run(run_id=...)`` to upload artifacts.

    The caller (test harness) is responsible for reading config and vault paths;
    this function does not access the project config directly.

    Returns a dict with ``run_id`` and ``experiment_id``.
    """
    import mlflow

    from projects.caliper.public.file_export import (
        load_mlflow_secrets_yaml,
        mlflow_connection_env,
    )

    secrets_data = load_mlflow_secrets_yaml(secrets_path)
    tracking_uri = secrets_data.get("tracking_uri", "")

    prev_workspace = os.environ.get("MLFLOW_WORKSPACE")
    prev_tracking_uri = mlflow.get_tracking_uri()
    try:
        with mlflow_connection_env(secrets_data):
            if tracking_uri:
                mlflow.set_tracking_uri(tracking_uri)
            if workspace:
                os.environ["MLFLOW_WORKSPACE"] = workspace
            if experiment:
                mlflow.set_experiment(experiment)

            run_name = os.environ.get("FJOB_NAME")
            with mlflow.start_run(run_name=run_name):
                active = mlflow.active_run()
                run_id = active.info.run_id
                experiment_id = str(active.info.experiment_id)
    finally:
        if prev_workspace is not None:
            os.environ["MLFLOW_WORKSPACE"] = prev_workspace
        else:
            os.environ.pop("MLFLOW_WORKSPACE", None)
        mlflow.set_tracking_uri(prev_tracking_uri)

    meta = {"run_id": run_id, "experiment_id": experiment_id}

    marker_path = env.ARTIFACT_DIR / MLFLOW_PRECREATED_RUN_MARKER
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(yaml.safe_dump(meta, sort_keys=False), encoding="utf-8")
    logger.info(
        "Pre-created MLflow run %s (experiment=%s), marker: %s",
        run_id,
        experiment_id,
        marker_path,
    )

    return meta


def _read_mlflow_ids_from_marker() -> tuple[str, str]:
    """Read run_id and experiment_id from the pre-created marker file on disk."""
    artifact_dir = Path(env.ARTIFACT_DIR) if env.ARTIFACT_DIR else None
    if not artifact_dir:
        logger.warning("ARTIFACT_DIR not set, cannot read MLflow marker")
        return "", ""
    for marker in sorted(artifact_dir.rglob(MLFLOW_PRECREATED_RUN_MARKER)):
        data = yaml.safe_load(marker.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            logger.warning(
                "MLflow marker %s has unexpected type %s, skipping", marker, type(data).__name__
            )
            continue
        if data.get("run_id"):
            return data["run_id"], data.get("experiment_id", "")
    logger.warning("No valid MLflow marker found under %s", artifact_dir)
    return "", ""


def build_mlflow_run_url(
    *,
    secrets_path: Path,
    workspace: str | None = None,
) -> str:
    """Construct the MLflow run URL from vault secrets and the marker file.

    The caller (test harness) is responsible for resolving ``secrets_path``
    and ``workspace``; this function does not access the project config.
    """
    from urllib.parse import quote

    from projects.caliper.public.file_export import (
        assert_tracking_uri_has_no_userinfo,
        load_mlflow_secrets_yaml,
    )

    run_id, experiment_id = _read_mlflow_ids_from_marker()
    if not run_id or not experiment_id:
        logger.warning("Cannot build MLflow URL: run_id or experiment_id missing from marker")
        return ""

    if not secrets_path.exists():
        logger.warning("Cannot build MLflow URL: secrets file %s not found", secrets_path)
        return ""

    secrets_data = load_mlflow_secrets_yaml(secrets_path)
    tracking_uri = secrets_data.get("tracking_uri", "").rstrip("/")
    if not tracking_uri.startswith(("http://", "https://")):
        logger.warning("Cannot build MLflow URL: tracking_uri has unsupported scheme")
        return ""
    assert_tracking_uri_has_no_userinfo(tracking_uri)

    qs = f"?workspace={quote(workspace, safe='')}" if workspace else ""
    return f"{tracking_uri}/#/experiments/{experiment_id}/runs/{run_id}/artifacts{qs}"


def _discover_precreated_mlflow_run_id(from_path: Path) -> str | None:
    """Find a pre-created MLflow run_id from the marker file."""
    for marker in sorted(from_path.rglob(MLFLOW_PRECREATED_RUN_MARKER)):
        try:
            data = yaml.safe_load(marker.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                logger.warning(
                    "MLflow marker %s has unexpected type %s, skipping",
                    marker,
                    type(data).__name__,
                )
                continue
            run_id = data.get("run_id")
            if run_id:
                logger.info("Found pre-created MLflow run_id: %s (from marker %s)", run_id, marker)
                return run_id
        except (OSError, yaml.YAMLError) as e:
            logger.warning("Failed to read MLflow pre-created run marker %s: %s", marker, e)

    return None
