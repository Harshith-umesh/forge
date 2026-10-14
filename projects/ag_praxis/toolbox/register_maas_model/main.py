#!/usr/bin/env python3

from __future__ import annotations

import logging

from projects.core.dsl import always, entrypoint, execute_tasks, retry, task
from projects.core.dsl.utils.k8s import best_effort_oc, oc, oc_apply

logger = logging.getLogger("DSL")


@entrypoint
def run(
    *,
    model_namespace: str,
    tenant_namespace: str,
    llmisvc_name: str,
    model_ref_name: str = "",
):
    """Register a model with MaaS by creating MaaSModelRef, MaaSAuthPolicy, and MaaSSubscription.

    Args:
        model_namespace: Namespace where the LLMInferenceService lives.
        tenant_namespace: Namespace for MaaSAuthPolicy and MaaSSubscription.
        llmisvc_name: Name of the LLMInferenceService to reference.
        model_ref_name: Name for the MaaSModelRef resource. Defaults to llmisvc_name.
    """
    return execute_tasks(locals())


def _llmisvc_owner_ref(name, uid):
    return {
        "apiVersion": "serving.kserve.io/v1alpha1",
        "kind": "LLMInferenceService",
        "name": name,
        "uid": uid,
    }


@task
def resolve_names(args, ctx):
    """Resolve default names and fetch the LLMInferenceService UID."""
    ctx.model_ref_name = args.model_ref_name or args.llmisvc_name
    ctx.created_model_ref = False
    ctx.created_auth_policy = False
    ctx.created_subscription = False

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

    return f"Model ref name: {ctx.model_ref_name}, LLMInferenceService UID: {ctx.llmisvc_uid}"


@task
def delete_previous(args, ctx):
    """Delete any existing MaaS resources before re-creating them."""
    best_effort_oc(
        "delete",
        "maassubscription",
        f"{ctx.model_ref_name}-subscription",
        "-n",
        args.tenant_namespace,
    )
    best_effort_oc(
        "delete", "maasauthpolicy", f"{ctx.model_ref_name}-access", "-n", args.tenant_namespace
    )
    best_effort_oc("delete", "maasmodelref", ctx.model_ref_name, "-n", args.model_namespace)
    return "Deleted previous MaaS resources"


@task
def apply_maas_model_ref(args, ctx):
    """Create the MaaSModelRef in the model namespace, owned by the LLMInferenceService."""
    src_dir = args.artifact_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "apiVersion": "maas.opendatahub.io/v1alpha1",
        "kind": "MaaSModelRef",
        "metadata": {
            "name": ctx.model_ref_name,
            "namespace": args.model_namespace,
            "ownerReferences": [_llmisvc_owner_ref(args.llmisvc_name, ctx.llmisvc_uid)],
        },
        "spec": {
            "modelRef": {
                "kind": "LLMInferenceService",
                "name": args.llmisvc_name,
            },
        },
    }

    oc_apply(src_dir / "maas-model-ref.yaml", manifest)
    ctx.created_model_ref = True
    return f"Applied MaaSModelRef {ctx.model_ref_name}"


@task
def apply_maas_auth_policy(args, ctx):
    """Create the MaaSAuthPolicy in the tenant namespace."""
    src_dir = args.artifact_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "apiVersion": "maas.opendatahub.io/v1alpha1",
        "kind": "MaaSAuthPolicy",
        "metadata": {
            "name": f"{ctx.model_ref_name}-access",
            "namespace": args.tenant_namespace,
            "ownerReferences": [_llmisvc_owner_ref(args.llmisvc_name, ctx.llmisvc_uid)],
        },
        "spec": {
            "modelRefs": [
                {
                    "name": ctx.model_ref_name,
                    "namespace": args.model_namespace,
                },
            ],
            "subjects": {
                "groups": [{"name": "system:authenticated"}],
                "users": [],
            },
        },
    }

    oc_apply(src_dir / "maas-auth-policy.yaml", manifest)
    ctx.created_auth_policy = True
    return f"Applied MaaSAuthPolicy {ctx.model_ref_name}-access"


@task
def apply_maas_subscription(args, ctx):
    """Create the MaaSSubscription in the tenant namespace."""
    src_dir = args.artifact_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "apiVersion": "maas.opendatahub.io/v1alpha1",
        "kind": "MaaSSubscription",
        "metadata": {
            "name": f"{ctx.model_ref_name}-subscription",
            "namespace": args.tenant_namespace,
            "ownerReferences": [_llmisvc_owner_ref(args.llmisvc_name, ctx.llmisvc_uid)],
        },
        "spec": {
            "owner": {
                "groups": [{"name": "system:authenticated"}],
                "users": [],
            },
            "modelRefs": [
                {
                    "name": ctx.model_ref_name,
                    "namespace": args.model_namespace,
                    "tokenRateLimits": [
                        {"limit": 100, "window": "1m"},
                    ],
                },
            ],
            "priority": 10,
        },
    }

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
            f"{ctx.model_ref_name}-access",
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
            f"{ctx.model_ref_name}-subscription",
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
