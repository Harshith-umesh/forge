from __future__ import annotations

import base64
import logging
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from projects.cluster.toolbox.cluster_deploy_operator import main as cluster_deploy_operator
from projects.cluster.toolbox.deploy_custom_catalog import main as deploy_custom_catalog
from projects.cluster.toolbox.wait_for_crds import main as wait_for_crds_command
from projects.core.dsl.utils.k8s import oc, oc_get_json
from projects.core.library import vault

logger = logging.getLogger(__name__)

RHOAI_PULL_SECRET_NAMESPACE = "openshift-config"
RHOAI_PULL_SECRET_NAME = "pull-secret"
RHOAI_REGISTRY = "quay.io/rhoai"


class _RhoaiCustomCatalogPullSecretVaultConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    content: str


class _RhoaiCustomCatalogPullSecretConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    vault: _RhoaiCustomCatalogPullSecretVaultConfig


class _RhoaiCustomCatalogPullSecretInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pull_secret: _RhoaiCustomCatalogPullSecretConfig


class RhoaiCustomCatalogConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    name: str
    namespace: str
    image: str | None = None
    display_name: str | None = None
    publisher: str | None = None
    pull_secret: _RhoaiCustomCatalogPullSecretConfig


class RhoaiOperatorConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    custom_catalog: RhoaiCustomCatalogConfig
    namespace: str
    datasciencecluster_name: str
    components: list[str] = Field(default_factory=list)
    required_crds_before_dsc: list[str] = Field(default_factory=list)
    required_crds_after_dsc: list[str] = Field(default_factory=list)


def custom_catalog_pull_secret_path(custom_catalog: dict[str, Any]) -> Path:
    catalog = _RhoaiCustomCatalogPullSecretInput.model_validate(custom_catalog)
    secret_path = vault.get_vault_content_path(
        catalog.pull_secret.vault.name,
        catalog.pull_secret.vault.content,
    )
    if secret_path is None:
        raise RuntimeError(
            "RHOAI pull secret content "
            f"'{catalog.pull_secret.vault.content}' was not found in vault "
            f"'{catalog.pull_secret.vault.name}'"
        )
    return secret_path


def operator_spec_by_package(platform: dict[str, Any], package: str) -> dict[str, Any]:
    operators = platform["operators"]
    if isinstance(operators, dict):
        if package in operators:
            return {"package": package, **operators[package]}
        raise KeyError(f"Unknown operator package: {package}")

    for operator_spec in operators:
        if operator_spec["package"] == package:
            return operator_spec
    raise KeyError(f"Unknown operator package: {package}")


def ensure_operator_subscription(operator_spec: dict[str, str]) -> dict[str, object]:
    return cluster_deploy_operator.run(
        package_name=operator_spec["package"],
        target_namespace=operator_spec["namespace"],
        source_name=operator_spec["source"],
        channel=operator_spec["channel"],
        source_namespace=operator_spec.get("source_namespace", "openshift-marketplace"),
        display_name=operator_spec.get("display_name", operator_spec["package"]),
        artifact_dirname_suffix=f"_{operator_spec['package']}",
    )


def deploy_rhoai_custom_catalog(*, custom_catalog: RhoaiCustomCatalogConfig) -> int:
    if not custom_catalog.enabled:
        logger.info("RHOAI custom catalog disabled; using default catalog source")
        return 0

    if not custom_catalog.image:
        raise RuntimeError("RHOAI custom catalog is enabled but no image was configured")

    return deploy_custom_catalog.run(
        catalog_source_name=custom_catalog.name,
        catalog_namespace=custom_catalog.namespace,
        catalog_image=custom_catalog.image,
        display_name=custom_catalog.display_name or custom_catalog.name,
        publisher=custom_catalog.publisher or "",
    )


def rhoai_operator_spec(
    *,
    custom_catalog: RhoaiCustomCatalogConfig,
    operator_spec: dict[str, str],
) -> dict[str, str]:
    if not custom_catalog.enabled:
        return operator_spec

    updated_spec = dict(operator_spec)
    updated_spec["source"] = custom_catalog.name
    updated_spec["source_namespace"] = custom_catalog.namespace
    return updated_spec


def prepare_rhcl_operator(platform: dict[str, Any]) -> None:
    operator_spec = operator_spec_by_package(platform, "rhcl-operator")
    ensure_operator_subscription(operator_spec)


def _decode_pull_secret(secret_data: dict[str, Any]) -> str:
    encoded = secret_data.get("data", {}).get(".dockerconfigjson")
    if not encoded:
        raise RuntimeError("openshift-config/pull-secret is missing .dockerconfigjson data")

    return base64.b64decode(encoded).decode("utf-8")


def wait_for_rhoai_pull_secret_ready(
    *, timeout_seconds: int = 600, poll_interval_seconds: int = 15
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: str | None = None

    while time.monotonic() < deadline:
        try:
            secret = oc_get_json(
                "secret",
                name=RHOAI_PULL_SECRET_NAME,
                namespace=RHOAI_PULL_SECRET_NAMESPACE,
            )
            decoded = _decode_pull_secret(secret)
            if RHOAI_REGISTRY not in decoded:
                raise RuntimeError(f"{RHOAI_REGISTRY} not yet present in pull secret")

            mcp_status = oc(
                "get",
                "mcp",
                "-o",
                r'jsonpath={.items[*].status.conditions[?(@.type=="Updated")].status}',
                check=False,
                log_stdout=False,
                log_stderr=False,
            ).stdout.strip()
            if not mcp_status or "False" in mcp_status:
                raise RuntimeError("machine config pools are still updating")

            logger.info(
                "RHOAI pull secret now contains %s and machine config pools are updated",
                RHOAI_REGISTRY,
            )
            return
        except RuntimeError as exc:
            last_error = str(exc)
            logger.info("Waiting for RHOAI pull secret propagation: %s", last_error)
            time.sleep(poll_interval_seconds)

    raise RuntimeError(
        f"Timed out waiting for {RHOAI_REGISTRY} pull secret propagation: {last_error}"
    )


def prepare_rhoai_pull_secret(custom_catalog: RhoaiCustomCatalogConfig) -> None:
    pull_secret_path = vault.get_vault_content_path(
        custom_catalog.pull_secret.vault.name,
        custom_catalog.pull_secret.vault.content,
    )
    if pull_secret_path is None:
        raise RuntimeError(
            "RHOAI pull secret content "
            f"'{custom_catalog.pull_secret.vault.content}' was not found in vault "
            f"'{custom_catalog.pull_secret.vault.name}'"
        )
    auth_basic = pull_secret_path.read_text(encoding="utf-8").strip()
    if not auth_basic:
        raise RuntimeError(f"RHOAI pull secret file is empty: {pull_secret_path}")

    current_secret = oc_get_json(
        "secret",
        name=RHOAI_PULL_SECRET_NAME,
        namespace=RHOAI_PULL_SECRET_NAMESPACE,
    )
    decoded_secret = _decode_pull_secret(current_secret)

    if RHOAI_REGISTRY in decoded_secret:
        logger.info("RHOAI registry %s already present in cluster pull secret", RHOAI_REGISTRY)
        return

    logger.info("Adding %s to cluster pull secret from %s", RHOAI_REGISTRY, pull_secret_path)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as temp_file:
        temp_file.write(decoded_secret)
        temp_path = Path(temp_file.name)

    try:
        oc(
            "registry",
            "login",
            f"--registry={RHOAI_REGISTRY}",
            f"--auth-basic={auth_basic}",
            "--to",
            str(temp_path),
        )
        oc(
            "set",
            "data",
            f"secret/{RHOAI_PULL_SECRET_NAME}",
            "-n",
            RHOAI_PULL_SECRET_NAMESPACE,
            f"--from-file=.dockerconfigjson={temp_path}",
        )
    finally:
        temp_path.unlink(missing_ok=True)

    wait_for_rhoai_pull_secret_ready()


def ensure_required_crds_before_dsc(rhoai: RhoaiOperatorConfig) -> None:
    wait_for_crds_command.run(
        crd_names=rhoai.required_crds_before_dsc,
        display_name="RHOAI pre-DSC CRDs",
    )


def prepare_rhoai_operator(
    *,
    platform: dict[str, Any],
    rhoai: dict[str, Any],
    icsp_applier: Callable[[], None],
) -> None:
    rhoai_config = RhoaiOperatorConfig.model_validate(rhoai)

    prepare_rhcl_operator(platform)
    if rhoai_config.custom_catalog.enabled:
        prepare_rhoai_pull_secret(rhoai_config.custom_catalog)
        icsp_applier()
    deploy_rhoai_custom_catalog(custom_catalog=rhoai_config.custom_catalog)
    operator_spec = operator_spec_by_package(platform, "rhods-operator")
    operator_spec = rhoai_operator_spec(
        custom_catalog=rhoai_config.custom_catalog,
        operator_spec=operator_spec,
    )
    ensure_operator_subscription(operator_spec)
    ensure_required_crds_before_dsc(rhoai_config)
