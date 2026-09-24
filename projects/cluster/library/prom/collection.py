from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import yaml

from projects.cluster.library.prom import metrics as prom_metrics
from projects.cluster.toolbox.capture_prometheus.main import run as _capture_prometheus
from projects.cluster.toolbox.capture_prometheus_metrics.main import (
    run as _capture_prometheus_metrics,
)
from projects.cluster.toolbox.enable_user_workload_monitoring.main import (
    run as _enable_user_workload_monitoring,
)
from projects.core.dsl.utils.k8s import oc
from projects.core.library import config

logger = logging.getLogger(__name__)

UWM_NAMESPACE = "openshift-user-workload-monitoring"
UWM_POD = "prometheus-user-workload-0"
MONITORING_NAMESPACE = "openshift-monitoring"
CONFIGMAP_NAME = "cluster-monitoring-config"


def capture_prometheus_metrics(start_time: datetime, end_time: datetime) -> None:
    if not config.project.get_config("prom.capture.metrics.enabled", False):
        logger.info("Prometheus metrics capture not enabled, skipping.")
        return

    groups = config.project.get_config("prom.capture.metrics.groups", {})
    if not groups:
        logger.warning("No metrics groups configured, skipping capture.")
        return

    yaml_files = sorted(prom_metrics.BUNDLED_DIR.glob("*.yaml"))
    all_defs = prom_metrics.load_definitions(*yaml_files)

    for group_name, group_cfg in groups.items():
        source = group_cfg.get("source", "platform")
        step = group_cfg.get("step_seconds", 15)
        categories = group_cfg.get("categories")
        params = group_cfg.get("params", {})

        defs = prom_metrics.select(all_defs, source=source, categories=categories)
        if not defs:
            logger.warning("Group %s: no metrics matched the filters, skipping.", group_name)
            continue

        queries = prom_metrics.resolve(defs, params)
        logger.info("Group %s: capturing %d metrics queries", group_name, len(queries))

        tmp_dir = Path("/tmp/prom_metrics_capture")
        input_path = prom_metrics.write_capture_input(queries, tmp_dir / f"{group_name}.yaml")

        _capture_prometheus_metrics(
            str(input_path),
            start_time,
            end_time,
            step_seconds=step,
            artifact_dirname_suffix=group_name,
        )


def is_user_workload_monitoring_enabled() -> bool:
    result = oc(
        "-n",
        MONITORING_NAMESPACE,
        "get",
        "configmap",
        CONFIGMAP_NAME,
        "-o",
        "jsonpath={.data.config\\.yaml}",
        check=False,
    )

    if not result.success:
        return False

    monitoring_config = yaml.safe_load(result.stdout)

    if not isinstance(monitoring_config, dict):
        return False

    return bool(monitoring_config.get("enableUserWorkload", False))


def validate_user_workload_monitoring() -> None:
    if not config.project.get_config("prom.capture.user_workload.fail_if_not_enabled"):
        return

    if not is_user_workload_monitoring_enabled():
        raise RuntimeError(
            "User workload monitoring is not enabled on the cluster, "
            "but prom.capture.user_workload.fail_if_not_enabled is set"
        )


def prepare_user_workload_monitoring(*, during: str) -> None:
    if not config.project.get_config(f"prom.prepare.user_workload.during_{during}"):
        return

    logger.info("Enabling user workload monitoring on the cluster (during %s)", during)
    _enable_user_workload_monitoring()


def capture_prometheus(start_time: datetime, end_time: datetime) -> None:
    if not config.project.get_config("prom.capture.enabled"):
        logger.info("Prometheus metrics capture not enabled.")
        return

    capture_prometheus_metrics(start_time, end_time)

    if config.project.get_config("prom.capture.system_metrics.enabled"):
        logger.info("Capturing Prometheus system metrics")
        _capture_prometheus(
            start_time,
            end_time,
            artifact_dirname_suffix="system",
        )
    else:
        logger.info("Prometheus system metrics capture is not enabled, skipping.")

    if not config.project.get_config("prom.capture.user_workload.enabled"):
        return

    if not is_user_workload_monitoring_enabled():
        logger.warning(
            "User workload monitoring is not enabled on the cluster, skipping UWM capture"
        )
        return

    logger.info("Capturing user-workload monitoring Prometheus metrics")
    _capture_prometheus(
        start_time,
        end_time,
        namespace=UWM_NAMESPACE,
        pod_name=UWM_POD,
        artifact_dirname_suffix="uwm",
    )
