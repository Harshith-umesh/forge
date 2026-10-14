#!/usr/bin/env python3

from __future__ import annotations

import logging

from projects.core.dsl import entrypoint, execute_tasks, task
from projects.core.dsl.utils.k8s import best_effort_oc

logger = logging.getLogger("DSL")


@entrypoint
def run(
    *,
    model_namespace: str,
    tenant_namespace: str,
    llmisvc_name: str,
    model_ref_name: str = "",
):
    """Unregister a model from MaaS by deleting MaaSSubscription, MaaSAuthPolicy, and MaaSModelRef.

    Args:
        model_namespace: Namespace where the MaaSModelRef lives.
        tenant_namespace: Namespace where MaaSAuthPolicy and MaaSSubscription live.
        llmisvc_name: Name of the LLMInferenceService that was referenced.
        model_ref_name: Name of the MaaSModelRef resource. Defaults to llmisvc_name.
    """
    return execute_tasks(locals())


@task
def resolve_names(args, ctx):
    """Resolve default names from parameters."""
    ctx.model_ref_name = args.model_ref_name or args.llmisvc_name
    return f"Model ref name: {ctx.model_ref_name}"


@task
def delete_maas_subscription(args, ctx):
    """Delete the MaaSSubscription."""
    best_effort_oc(
        "delete",
        "maassubscription",
        f"{ctx.model_ref_name}-subscription",
        "-n",
        args.tenant_namespace,
        "--ignore-not-found",
    )
    return f"Deleted MaaSSubscription {ctx.model_ref_name}-subscription"


@task
def delete_maas_auth_policy(args, ctx):
    """Delete the MaaSAuthPolicy."""
    best_effort_oc(
        "delete",
        "maasauthpolicy",
        f"{ctx.model_ref_name}-access",
        "-n",
        args.tenant_namespace,
        "--ignore-not-found",
    )
    return f"Deleted MaaSAuthPolicy {ctx.model_ref_name}-access"


@task
def delete_maas_model_ref(args, ctx):
    """Delete the MaaSModelRef."""
    best_effort_oc(
        "delete",
        "maasmodelref",
        ctx.model_ref_name,
        "-n",
        args.model_namespace,
        "--ignore-not-found",
    )
    return f"Deleted MaaSModelRef {ctx.model_ref_name}"


if __name__ == "__main__":
    run.main()
