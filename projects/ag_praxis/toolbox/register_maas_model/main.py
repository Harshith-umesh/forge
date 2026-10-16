#!/usr/bin/env python3

from __future__ import annotations

import logging

import yaml

from projects.core.dsl import always, entrypoint, execute_tasks, retry, task
from projects.core.dsl.template import render_template
from projects.core.dsl.utils.k8s import best_effort_oc, oc, oc_apply

logger = logging.getLogger("DSL")

_VALID_MODEL_REF_KINDS = ("LLMInferenceService", "ExternalModel")


@entrypoint
def run(
    *,
    model_namespace: str,
    tenant_namespace: str,
    llmisvc_name: str,
    model_ref_name: str = "",
    model_ref_kind: str = "LLMInferenceService",
    external_endpoint: str = "",
    external_target_model: str = "",
    external_service_name: str = "",
    external_port: int = 8080,
    gateway_name: str = "",
    gateway_namespace: str = "",
    owner_deployment_name: str = "",
):
    """Register a model with MaaS by creating MaaSModelRef, MaaSAuthPolicy, and MaaSSubscription.

    For ExternalModel kind, also creates a credential secret, ExternalModel CR, and HTTPRoute.

    Args:
        model_namespace: Namespace where the LLMInferenceService or ExternalModel lives.
        tenant_namespace: Namespace for MaaSAuthPolicy and MaaSSubscription.
        llmisvc_name: Name of the LLMInferenceService to reference (or ExternalModel name).
        model_ref_name: Name for the MaaSModelRef resource. Defaults to llmisvc_name.
        model_ref_kind: Kind of resource the MaaSModelRef references ("LLMInferenceService" or "ExternalModel").
        external_endpoint: Service endpoint for ExternalModel (e.g. "praxis.ns.svc.cluster.local").
        external_target_model: Target model name for ExternalModel.
        external_service_name: K8s Service name for the HTTPRoute backend (e.g. "praxis").
        external_port: Port for the HTTPRoute backend.
        gateway_name: MaaS gateway name (required for ExternalModel HTTPRoute).
        gateway_namespace: MaaS gateway namespace (required for ExternalModel HTTPRoute).
        owner_deployment_name: Name of the Deployment that owns the created resources (for garbage collection).
    """
    if model_ref_kind not in _VALID_MODEL_REF_KINDS:
        raise ValueError(
            f"Invalid model_ref_kind: {model_ref_kind!r}. Valid: {_VALID_MODEL_REF_KINDS}"
        )
    if model_ref_kind == "ExternalModel" and not external_endpoint:
        raise ValueError("external_endpoint is required when model_ref_kind is ExternalModel")
    if model_ref_kind == "ExternalModel" and not external_service_name:
        raise ValueError("external_service_name is required when model_ref_kind is ExternalModel")
    if model_ref_kind == "ExternalModel" and not (gateway_name and gateway_namespace):
        raise ValueError(
            "gateway_name and gateway_namespace are required when model_ref_kind is ExternalModel"
        )

    return execute_tasks(locals())


@task
def resolve_names(args, ctx):
    """Resolve default names and fetch the LLMInferenceService UID (if applicable)."""
    ctx.model_ref_name = args.model_ref_name or args.llmisvc_name
    ctx.is_external = args.model_ref_kind == "ExternalModel"
    ctx.created_model_ref = False
    ctx.created_auth_policy = False
    ctx.created_subscription = False
    ctx.created_external_model = False
    ctx.created_httproute = False

    if ctx.is_external:
        ctx.llmisvc_uid = None
    else:
        ctx.llmisvc_uid = oc(
            "get",
            "llminferenceservice",
            args.llmisvc_name,
            "-n",
            args.model_namespace,
            "-o",
            "jsonpath={.metadata.uid}",
            log_stdout=True,
        ).stdout.strip()

    ctx.owner_ref = None
    if args.owner_deployment_name:
        owner_uid = oc(
            "get",
            "deployment",
            args.owner_deployment_name,
            "-n",
            args.model_namespace,
            "-o",
            "jsonpath={.metadata.uid}",
            log_stdout=True,
        ).stdout.strip()
        ctx.owner_ref = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "name": args.owner_deployment_name,
            "uid": owner_uid,
        }

    return f"Model ref name: {ctx.model_ref_name}, kind: {args.model_ref_kind}"


@task
def delete_previous(args, ctx):
    """Delete any existing MaaS resources before re-creating them."""
    best_effort_oc(
        "delete",
        "maassubscription",
        f"{args.model_namespace}-{ctx.model_ref_name}-subscription",
        "-n",
        args.tenant_namespace,
    )
    best_effort_oc(
        "delete",
        "maasauthpolicy",
        f"{args.model_namespace}-{ctx.model_ref_name}-access",
        "-n",
        args.tenant_namespace,
    )
    best_effort_oc("delete", "maasmodelref", ctx.model_ref_name, "-n", args.model_namespace)

    if ctx.is_external:
        best_effort_oc(
            "delete",
            "externalmodel.maas.opendatahub.io",
            ctx.model_ref_name,
            "-n",
            args.model_namespace,
        )
        best_effort_oc(
            "delete", "secret", f"{ctx.model_ref_name}-creds", "-n", args.model_namespace
        )

    return "Deleted previous MaaS resources"


def _template_context(args, ctx):
    return {
        "model_namespace": args.model_namespace,
        "tenant_namespace": args.tenant_namespace,
        "llmisvc_name": args.llmisvc_name,
        "model_ref_name": ctx.model_ref_name,
        "model_ref_kind": args.model_ref_kind,
        "is_external": ctx.is_external,
        "llmisvc_uid": ctx.llmisvc_uid,
        "external_endpoint": args.external_endpoint,
        "external_target_model": args.external_target_model,
        "external_service_name": args.external_service_name,
        "external_port": args.external_port,
        "gateway_name": args.gateway_name,
        "gateway_namespace": args.gateway_namespace,
        "owner_ref": ctx.owner_ref,
    }


@task
def apply_external_model(args, ctx):
    """Create credential secret and ExternalModel CR (only for ExternalModel kind)."""
    if not ctx.is_external:
        return "Skipped (not ExternalModel)"

    import base64

    secret_name = f"{ctx.model_ref_name}-creds"
    dummy_key = base64.b64encode(f"dummy-key-{ctx.model_ref_name}".encode()).decode()
    secret_metadata = {
        "name": secret_name,
        "namespace": args.model_namespace,
        "labels": {
            "inference.networking.k8s.io/bbr-managed": "true",
            "app.kubernetes.io/managed-by": "forge",
        },
    }
    if ctx.owner_ref:
        secret_metadata["ownerReferences"] = [ctx.owner_ref]

    secret_manifest = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": secret_metadata,
        "data": {
            "api-key": dummy_key,
        },
    }
    oc("apply", "-f", "-", input_text=yaml.safe_dump(secret_manifest, sort_keys=False))

    src_dir = args.artifact_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    tctx = _template_context(args, ctx)
    manifest = yaml.safe_load(render_template("external_model.yaml.j2", context=tctx))
    oc_apply(src_dir / "external-model.yaml", manifest)
    ctx.created_external_model = True

    return f"Applied ExternalModel {ctx.model_ref_name} → {args.external_endpoint}"


@task
def apply_httproute(args, ctx):
    """Skip — the MaaS operator creates the HTTPRoute automatically from the MaaSModelRef."""
    return "Skipped (MaaS operator manages HTTPRoute)"


@task
def apply_maas_model_ref(args, ctx):
    """Create the MaaSModelRef in the model namespace."""
    src_dir = args.artifact_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    tctx = _template_context(args, ctx)
    manifest = yaml.safe_load(render_template("maas_model_ref.yaml.j2", context=tctx))
    oc_apply(src_dir / "maas-model-ref.yaml", manifest)
    ctx.created_model_ref = True

    return f"Applied MaaSModelRef {ctx.model_ref_name} (kind={args.model_ref_kind})"


@task
def apply_maas_auth_policy(args, ctx):
    """Create the MaaSAuthPolicy in the tenant namespace."""
    src_dir = args.artifact_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    tctx = _template_context(args, ctx)
    manifest = yaml.safe_load(render_template("maas_auth_policy.yaml.j2", context=tctx))
    oc_apply(src_dir / "maas-auth-policy.yaml", manifest)
    ctx.created_auth_policy = True

    return f"Applied MaaSAuthPolicy {ctx.model_ref_name}-access"


@task
def apply_maas_subscription(args, ctx):
    """Create the MaaSSubscription in the tenant namespace."""
    src_dir = args.artifact_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    tctx = _template_context(args, ctx)
    manifest = yaml.safe_load(render_template("maas_subscription.yaml.j2", context=tctx))
    oc_apply(src_dir / "maas-subscription.yaml", manifest)
    ctx.created_subscription = True

    return f"Applied MaaSSubscription {ctx.model_ref_name}-subscription"


@retry(attempts=30, delay=4, backoff=1.0)
@task
def wait_maas_model_ref_ready(args, ctx):
    """Wait for the MaaSModelRef to reach Ready phase."""
    result = oc(
        "get",
        "maasmodelref",
        ctx.model_ref_name,
        "-n",
        args.model_namespace,
        "-o",
        "jsonpath={.status.phase}",
        log_stdout=True,
    )
    phase = result.stdout.strip()
    if phase != "Ready":
        return False, f"MaaSModelRef phase={phase!r}, waiting for Ready"

    return f"MaaSModelRef {ctx.model_ref_name} is Ready"


@always
@task
def capture_maas_resources(args, ctx):
    """Capture the YAML of created MaaS resources for post-mortem analysis."""
    captured = []

    artifacts_dir = args.artifact_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    if ctx.created_external_model:
        oc(
            "get",
            "externalmodel.maas.opendatahub.io",
            ctx.model_ref_name,
            "-n",
            args.model_namespace,
            "-o",
            "yaml",
            check=False,
            log_stdout=False,
            stdout_dest=artifacts_dir / "externalmodel.yaml",
        )
        captured.append("ExternalModel")

    if ctx.created_model_ref:
        oc(
            "get",
            "maasmodelref",
            ctx.model_ref_name,
            "-n",
            args.model_namespace,
            "-o",
            "yaml",
            check=False,
            log_stdout=False,
            stdout_dest=artifacts_dir / "maasmodelref.yaml",
        )
        captured.append("MaaSModelRef")

    if ctx.created_auth_policy:
        oc(
            "get",
            "maasauthpolicy",
            f"{args.model_namespace}-{ctx.model_ref_name}-access",
            "-n",
            args.tenant_namespace,
            "-o",
            "yaml",
            check=False,
            log_stdout=False,
            stdout_dest=artifacts_dir / "maasauthpolicy.yaml",
        )
        captured.append("MaaSAuthPolicy")

    if ctx.created_subscription:
        oc(
            "get",
            "maassubscription",
            f"{args.model_namespace}-{ctx.model_ref_name}-subscription",
            "-n",
            args.tenant_namespace,
            "-o",
            "yaml",
            check=False,
            log_stdout=False,
            stdout_dest=artifacts_dir / "maassubscription.yaml",
        )
        captured.append("MaaSSubscription")

    if not captured:
        return "No MaaS resources were created, nothing to capture"

    return f"Captured {', '.join(captured)} for {ctx.model_ref_name}"


if __name__ == "__main__":
    run.main()
