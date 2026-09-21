from __future__ import annotations

import logging
from datetime import datetime

import yaml

from projects.cluster.toolbox.capture_prometheus.main import run as _capture_prometheus
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
