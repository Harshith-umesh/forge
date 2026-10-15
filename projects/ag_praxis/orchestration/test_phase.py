import logging
from datetime import UTC

from projects.ag_praxis.toolbox.deploy_praxis import main as deploy_praxis_command
from projects.core.library import config, env, run
from projects.core.library.postprocess import (
    create_test_metadata,
    run_and_postprocess,
    update_test_labels_with_status,
    update_test_labels_with_timing,
)

logger = logging.getLogger(__name__)

# Temporary: switch between "llmisvc" (KServe LLMInferenceService) and "k8s" (plain Deployment+Service)
SIM_DEPLOY_MODE = "llmisvc"


def _sim_served_model_names():
    """Return the list of served model names from config."""
    sim_cfg = config.project.get_config("platform.sim")
    return sim_cfg.get("served_model_names", [sim_cfg["model_name"]])


def test_maas():
    from projects.ag_praxis.toolbox.maas_sim_test import main as maas_sim_test_command

    maas_sim_test_command.run(action="status")
    maas_sim_test_command.run(action="cleanup")
    maas_sim_test_command.run(action="deploy")
    maas_sim_test_command.run(action="status")
    maas_sim_test_command.run(action="test")
    maas_sim_test_command.run(action="cleanup")
    maas_sim_test_command.run(action="status")


def test():
    """Main test function that wraps do_test() with outcome postprocessing."""
    return run_and_postprocess(do_test_praxis)


def _resolve_benchmark_config(benchmark_key: str) -> dict:
    """Resolve a benchmark config by key, merging workload defaults."""
    import copy

    workloads = copy.deepcopy(config.project.get_config("workloads"))
    benchmark = copy.deepcopy(workloads.get("benchmarks", {}).get(benchmark_key))
    if benchmark is None:
        raise ValueError(f"Unknown benchmark key: {benchmark_key}")

    for key in (
        "job_name",
        "image",
        "pvc_size",
        "pvc_storage_class",
        "timeout_seconds",
        "fs_group",
        "use_pvc",
        "replica_count",
    ):
        if key in workloads and key not in benchmark:
            benchmark[key] = workloads[key]

    workload_args = workloads.get("args", {})
    if workload_args:
        benchmark_args = benchmark.get("args", {})
        merged = dict(workload_args)
        merged.update(benchmark_args)
        benchmark["args"] = merged

    return benchmark


def _execute_benchmark(
    test_namespace, endpoint_url, benchmark, artifact_suffix, api_key_file_path=None
):
    """Run a resolved benchmark config."""
    from projects.guidellm.library import benchconf as benchconf_lib
    from projects.guidellm.toolbox.run_guidellm_benchmark import build_guidellm_args
    from projects.guidellm.toolbox.run_guidellm_benchmark import (
        main as run_guidellm_benchmark_command,
    )

    config_path = None
    benchconf_ref = benchmark.get("benchconf")
    if benchconf_ref and benchconf_lib._is_enabled():
        benchconf_lib.maybe_install_custom_version()
        config_path = benchconf_lib.resolve_config_path(benchconf_ref)
        benchconf_lib.save_version()

    guidellm_args = build_guidellm_args(benchmark)

    run_guidellm_benchmark_command.run(
        endpoint_url=endpoint_url,
        name=benchmark.get("job_name"),
        namespace=test_namespace,
        image=benchmark.get("image"),
        timeout=benchmark.get("timeout_seconds"),
        pvc_size=benchmark.get("pvc_size"),
        pvc_storage_class=benchmark.get("pvc_storage_class"),
        guidellm_args=guidellm_args,
        config_path=config_path,
        fs_group=benchmark.get("fs_group"),
        use_pvc=benchmark.get("use_pvc"),
        replica_count=benchmark.get("replica_count", 1),
        artifact_dirname_suffix=artifact_suffix,
        api_key_file_path=api_key_file_path,
        validate_backend=config.project.get_config("workloads.validate_backend"),
    )


def _run_guidellm_benchmark(test_namespace, endpoint_url, benchmark_key):
    """Resolve a named benchmark and run it."""
    benchmark = _resolve_benchmark_config(benchmark_key)
    _execute_benchmark(test_namespace, endpoint_url, benchmark, benchmark_key)


def _build_benchmark_config(payload_name, sweep_name):
    """Build a benchmark config from payload and sweep dimensions."""
    import copy

    workloads = copy.deepcopy(config.project.get_config("workloads"))

    payloads = workloads.get("payloads", {})
    payload = payloads.get(payload_name)
    if payload is None:
        raise ValueError(f"Unknown payload: {payload_name!r}. Valid: {list(payloads)}")

    sweeps = workloads.get("sweeps", {})
    sweep = sweeps.get(sweep_name)
    if sweep is None:
        raise ValueError(f"Unknown sweep: {sweep_name!r}. Valid: {list(sweeps)}")

    duration = workloads.get("duration", 180)

    benchmark = {}
    for key in (
        "job_name",
        "image",
        "pvc_size",
        "pvc_storage_class",
        "timeout_seconds",
        "fs_group",
        "use_pvc",
        "replica_count",
    ):
        if key in workloads:
            benchmark[key] = workloads[key]

    workload_args = dict(workloads.get("args", {}))
    workload_args.update(
        {
            "profile": "kind=concurrent,streams={rate}",
            "data": f"kind=synthetic_text,prompt_tokens={payload['prompt_tokens']},output_tokens={payload['output_tokens']}",
            "constraint": f"kind=max_duration,seconds={duration}",
        }
    )
    benchmark["args"] = workload_args
    benchmark["rate"] = sweep

    return benchmark


def _write_benchmark_dry_run(endpoint_url):
    """Write the benchmark configuration that would be started to a YAML file."""
    import yaml

    from projects.guidellm.toolbox.run_guidellm_benchmark import build_guidellm_args

    payload_name = config.project.get_config("test.payload")
    sweep_name = config.project.get_config("test.sweep")
    benchmark = _build_benchmark_config(payload_name, sweep_name)

    dry_run = {
        "payload": payload_name,
        "sweep": sweep_name,
        "endpoint_url": endpoint_url,
        "guidellm_args": build_guidellm_args(benchmark),
        "benchmark": benchmark,
    }

    if config.project.get_config("test.run_warmup"):
        warmup_key = config.project.get_config("workloads.warmup_benchmark_key")
        if warmup_key:
            warmup = _resolve_benchmark_config(warmup_key)
            dry_run["warmup"] = {
                "benchmark_key": warmup_key,
                "guidellm_args": build_guidellm_args(warmup),
                "benchmark": warmup,
            }

    dest = env.ARTIFACT_DIR / "benchmark_dry_run.yaml"
    dest.write_text(yaml.safe_dump(dry_run, sort_keys=False))
    logger.info("Benchmark dry-run config written to %s", dest)


def _run_guidellm(test_namespace: str, endpoint_url: str, api_key_file_path=None):
    """Run the warmup (if enabled) then the main GuideLLM benchmark."""
    if config.project.get_config("test.run_warmup"):
        warmup_key = config.project.get_config("workloads.warmup_benchmark_key")
        if warmup_key:
            logger.info("Running warmup benchmark: %s", warmup_key)
            with env.NextArtifactDir("warmup"):
                _run_guidellm_benchmark(test_namespace, endpoint_url, warmup_key)

    payload_name = config.project.get_config("test.payload")
    sweep_name = config.project.get_config("test.sweep")
    logger.info("Running benchmark: payload=%s sweep=%s", payload_name, sweep_name)
    benchmark = _build_benchmark_config(payload_name, sweep_name)

    rates = benchmark.pop("rate")
    replica_count = benchmark.get("replica_count", 1)

    for rate in rates:
        per_replica_rate = round(rate / replica_count)
        run_benchmark = dict(benchmark)
        run_benchmark["rate"] = per_replica_rate
        suffix = f"{payload_name}_{sweep_name}_rate{rate}"
        logger.info(
            "Running rate=%s (per_replica=%s, replicas=%s)", rate, per_replica_rate, replica_count
        )
        _execute_benchmark(
            test_namespace,
            endpoint_url,
            run_benchmark,
            suffix,
            api_key_file_path=api_key_file_path,
        )


def _deploy_sim_llmisvc(
    test_namespace,
    *,
    name=None,
    model_name=None,
    served_model_names=None,
    gateway_name="",
    gateway_namespace="",
    maas_gateway_label="",
    skip_router=True,
):
    """Deploy a sim model (LLMInferenceService or plain K8s Deployment depending on SIM_DEPLOY_MODE)."""
    sim_cfg = config.project.get_config("platform.sim")
    deploy_name = name or sim_cfg["llmisvc_name"]
    deploy_model_name = model_name or sim_cfg["model_name"]

    if SIM_DEPLOY_MODE == "k8s":
        if not skip_router:
            raise ValueError("K8s deploy mode does not support router (skip_router=False)")

        from projects.kserve.toolbox.deploy_k8s_vllm_sim import (
            main as deploy_k8s_vllm_sim_command,
        )

        endpoint = deploy_k8s_vllm_sim_command.run(
            namespace=test_namespace,
            name=deploy_name,
            model_name=deploy_model_name,
            served_model_names=served_model_names,
            image=sim_cfg["image"],
            time_to_first_token=sim_cfg["time_to_first_token"],
            inter_token_latency=sim_cfg["inter_token_latency"],
            mode=sim_cfg["mode"],
            max_num_seqs=sim_cfg["max_num_seqs"],
            artifact_dirname_suffix=deploy_name,
        )
        return endpoint

    from projects.kserve.toolbox.deploy_sim_llmisvc import main as deploy_sim_llmisvc_command

    endpoint = deploy_sim_llmisvc_command.run(
        namespace=test_namespace,
        name=deploy_name,
        model_name=deploy_model_name,
        served_model_names=served_model_names,
        image=sim_cfg["image"],
        gateway_name=gateway_name,
        gateway_namespace=gateway_namespace,
        gateway_status_address_name=None,
        skip_router=skip_router,
        deploy_monitor=False,
        maas_gateway_label=maas_gateway_label,
        time_to_first_token=sim_cfg["time_to_first_token"],
        inter_token_latency=sim_cfg["inter_token_latency"],
        mode=sim_cfg["mode"],
        max_num_seqs=sim_cfg["max_num_seqs"],
        artifact_dirname_suffix=deploy_name,
    )
    return endpoint.replace("https://", "http://", 1)


def _deploy_all_sims(test_namespace, **kwargs):
    """Deploy a single sim serving multiple model names. Returns list of (model_name, endpoint)."""
    served_model_names = _sim_served_model_names()

    endpoint = _deploy_sim_llmisvc(
        test_namespace,
        served_model_names=served_model_names,
        **kwargs,
    )

    sims = []
    for model_name in served_model_names:
        sims.append((model_name, endpoint))
        _smoke_test(test_namespace, endpoint, f"sim-{model_name}", served_model_name=model_name)

    return sims


def _build_gateway_endpoint(gateway_cfg, test_namespace):
    """Construct the gateway internal URL."""
    return (
        f"http://{gateway_cfg['name']}-{gateway_cfg['gateway_class_name']}"
        f".{gateway_cfg['namespace']}.svc.cluster.local"
        f"/{test_namespace}/{config.project.get_config('platform.sim.llmisvc_name')}"
    )


def _smoke_test(test_namespace, endpoint_url, suffix, served_model_name, api_key_file_path=None):
    from projects.guidellm.toolbox.run_smoke_request import main as run_smoke_request_command

    smoke_cfg = config.project.get_config("workloads.smoke_request")
    kwargs = {}
    if api_key_file_path:
        kwargs["api_key_file_path"] = api_key_file_path

    run_smoke_request_command.run(
        namespace=test_namespace,
        endpoint_url=endpoint_url,
        served_model_name=served_model_name,
        prompt=smoke_cfg["prompt"],
        max_tokens=smoke_cfg["max_tokens"],
        temperature=smoke_cfg["temperature"],
        artifact_dirname_suffix=suffix,
        **kwargs,
    )


def _ensure_gateway(gateway_cfg):
    from projects.llm_d.toolbox.ensure_gateway import main as ensure_gateway_command

    ensure_gateway_command.run(
        config_dir="",
        namespace=gateway_cfg["namespace"],
        name=gateway_cfg["name"],
        gateway_class_name=gateway_cfg["gateway_class_name"],
        status_address_name=gateway_cfg["status_address_name"],
        create_if_missing=gateway_cfg["create_if_missing"],
    )


def _deploy_praxis(test_namespace, model_endpoint):
    praxis_cfg = config.project.get_config("platform.praxis")
    deploy_praxis_command.run(
        namespace=test_namespace,
        model_endpoint=model_endpoint,
        praxis_version=praxis_cfg["version"],
        replicas=praxis_cfg["replicas"],
    )


def _prep_direct(test_namespace, gateway_cfg):
    sims = _deploy_all_sims(test_namespace)
    _model_name, sim_endpoint = sims[0]
    return sim_endpoint


DIRECT_GATEWAY_HTTPROUTE_NAME = "direct-gateway-route"


def _prep_direct_gateway(test_namespace, gateway_cfg):
    from projects.ag_praxis.toolbox.add_gateway_route import main as add_gateway_route_command

    sims = _deploy_all_sims(test_namespace)
    model_name, sim_endpoint = sims[0]
    llmisvc_name = config.project.get_config("platform.sim.llmisvc_name")

    logger.info("[direct-gateway] model direct endpoint: %s", sim_endpoint)

    _ensure_gateway(gateway_cfg)
    gateway_endpoint = add_gateway_route_command.run(
        namespace=test_namespace,
        name=DIRECT_GATEWAY_HTTPROUTE_NAME,
        gateway_name=gateway_cfg["name"],
        gateway_namespace=gateway_cfg["namespace"],
        backend_service=llmisvc_name
        if SIM_DEPLOY_MODE == "k8s"
        else f"{llmisvc_name}-kserve-workload-svc",
        backend_port=8000,
    )
    logger.info("[direct-gateway] gateway endpoint: %s", gateway_endpoint)
    _smoke_test(test_namespace, gateway_endpoint, "gateway", model_name)
    return gateway_endpoint


def _prep_praxis(test_namespace, gateway_cfg):
    sims = _deploy_all_sims(test_namespace)
    model_name, sim_endpoint = sims[0]
    logger.info("[praxis] model direct endpoint: %s", sim_endpoint)

    _deploy_praxis(test_namespace, sim_endpoint)
    praxis_url = f"http://praxis.{test_namespace}.svc:8080"
    logger.info("[praxis] praxis endpoint: %s", praxis_url)
    _smoke_test(test_namespace, praxis_url, "praxis", model_name)
    return praxis_url


PRAXIS_HTTPROUTE_NAME = "praxis-gateway-route"


def _add_gateway_route(test_namespace, gateway_cfg):
    from projects.ag_praxis.toolbox.add_gateway_route import main as add_gateway_route_command

    return add_gateway_route_command.run(
        namespace=test_namespace,
        name=PRAXIS_HTTPROUTE_NAME,
        gateway_name=gateway_cfg["name"],
        gateway_namespace=gateway_cfg["namespace"],
        backend_service="praxis",
        backend_port=8080,
    )


def _prep_praxis_gateway(test_namespace, gateway_cfg):
    sims = _deploy_all_sims(test_namespace)
    model_name, sim_endpoint = sims[0]
    logger.info("[praxis-gateway] model direct endpoint: %s", sim_endpoint)

    _deploy_praxis(test_namespace, sim_endpoint)
    praxis_url = f"http://praxis.{test_namespace}.svc:8080"
    logger.info("[praxis-gateway] praxis endpoint: %s", praxis_url)
    _smoke_test(test_namespace, praxis_url, "praxis", model_name)

    _ensure_gateway(gateway_cfg)
    gateway_endpoint = _add_gateway_route(test_namespace, gateway_cfg)
    logger.info("[praxis-gateway] gateway endpoint: %s", gateway_endpoint)
    _smoke_test(test_namespace, gateway_endpoint, "gateway", model_name)
    return gateway_endpoint


def _cleanup_llmisvc(test_namespace):
    from projects.core.dsl.utils.k8s import best_effort_oc

    sim_name = config.project.get_config("platform.sim.llmisvc_name")
    if SIM_DEPLOY_MODE == "k8s":
        best_effort_oc("delete", "deployment", sim_name, "-n", test_namespace)
        best_effort_oc("delete", "service", sim_name, "-n", test_namespace)
    else:
        best_effort_oc("delete", "llminferenceservice", sim_name, "-n", test_namespace)


def _cleanup_praxis(test_namespace):
    from projects.ag_praxis.toolbox.teardown_praxis import main as teardown_praxis_command

    teardown_praxis_command.run(namespace=test_namespace)


def _cleanup_direct(test_namespace):
    _cleanup_llmisvc(test_namespace)


def _cleanup_direct_gateway(test_namespace):
    from projects.core.dsl.utils.k8s import best_effort_oc

    best_effort_oc("delete", "httproute", DIRECT_GATEWAY_HTTPROUTE_NAME, "-n", test_namespace)
    _cleanup_llmisvc(test_namespace)


def _cleanup_praxis_flavor(test_namespace):
    _cleanup_praxis(test_namespace)
    _cleanup_llmisvc(test_namespace)


def _cleanup_praxis_gateway(test_namespace):
    from projects.core.dsl.utils.k8s import best_effort_oc

    best_effort_oc("delete", "httproute", PRAXIS_HTTPROUTE_NAME, "-n", test_namespace)
    _cleanup_praxis(test_namespace)
    _cleanup_llmisvc(test_namespace)


def _llmisvc_names_sim():
    if SIM_DEPLOY_MODE == "k8s":
        return []
    return [config.project.get_config("platform.sim.llmisvc_name")]


def _llmisvc_names_maas():
    if SIM_DEPLOY_MODE == "k8s":
        return []
    return [config.project.get_config("platform.maas.llmisvc_name")]


def _endpoint_direct(test_namespace, gateway_cfg):
    sim_name = config.project.get_config("platform.sim.llmisvc_name")
    if SIM_DEPLOY_MODE == "k8s":
        return f"http://{sim_name}.{test_namespace}.svc:8000"
    svc_name = f"{sim_name}-kserve-workload-svc"
    return f"http://{svc_name}.{test_namespace}.svc.cluster.local"


def _endpoint_gateway(test_namespace, gateway_cfg):
    return (
        f"http://{gateway_cfg['name']}-{gateway_cfg['gateway_class_name']}"
        f".{gateway_cfg['namespace']}.svc.cluster.local"
    )


def _endpoint_praxis(test_namespace, gateway_cfg):
    return f"http://praxis.{test_namespace}.svc:8080"


def _resolve_maas_external_url():
    """Resolve the external MaaS gateway URL from the cluster ingress domain."""
    from projects.core.dsl.utils.k8s import oc

    result = oc(
        "get",
        "ingresses.config.openshift.io",
        "cluster",
        "-o",
        "jsonpath={.spec.domain}",
        log_stdout=True,
    )
    cluster_domain = result.stdout.strip()
    if not cluster_domain:
        raise RuntimeError("Failed to resolve cluster ingress domain")

    return f"https://maas.{cluster_domain}"


def _create_maas_api_key(namespace, maas_gateway_url):
    """Create an ephemeral MaaS API key and write it to a temp file.

    Returns the path to the file containing the API key (outside ARTIFACT_DIR).
    """
    import json
    import tempfile

    from projects.core.dsl.utils.k8s import oc

    token_result = oc(
        "create",
        "token",
        "default",
        "-n",
        namespace,
        handled_secretly=True,
    )
    sa_token = token_result.stdout.strip()

    import shlex

    payload = json.dumps({"name": "forge-test-key", "expiresIn": "1h", "ephemeral": True})
    curl_cmd = (
        f"curl -sS -k -X POST {shlex.quote(maas_gateway_url + '/maas-api/v1/api-keys')}"
        f" -H {shlex.quote('Authorization: Bearer ' + sa_token)}"
        f" -H 'Content-Type: application/json'"
        f" -d {shlex.quote(payload)}"
    )
    result = run.run(curl_cmd, capture_stdout=True, timeout=30, handled_securely=True)

    response = json.loads(result.stdout)
    api_key = response.get("key")
    if not api_key:
        raise RuntimeError(f"Failed to create MaaS API key: {response}")

    token_file = tempfile.NamedTemporaryFile(
        prefix="maas-api-key-", suffix=".key", delete=False, mode="w"
    )
    token_file.write(api_key)
    token_file.close()

    logger.info("Created ephemeral MaaS API key: %s...", api_key[:10])
    return token_file.name


def _create_maas_gateway_token(namespace, gateway_name):
    """Create a SA token for MaaS gateway auth and write it to a temp file.

    Returns the path to the file containing the token (outside ARTIFACT_DIR).
    """
    import tempfile

    from projects.core.dsl.utils.k8s import oc

    token_result = oc(
        "create",
        "token",
        "default",
        "--audience=https://kubernetes.default.svc",
        "--duration=2h",
        "-n",
        namespace,
        handled_secretly=True,
    )

    token = token_result.stdout.strip()
    if not token:
        raise RuntimeError("Failed to create SA token for gateway auth")

    token_file = tempfile.NamedTemporaryFile(
        prefix="maas-gateway-token-", suffix=".key", delete=False, mode="w"
    )
    token_file.write(token)
    token_file.close()

    logger.info("Created gateway SA token for audience %s-sa", gateway_name)
    return token_file.name


def _prep_maas(test_namespace, gateway_cfg):
    """Deploy sim LLMInferenceService + MaaS registration, benchmark through the gateway."""
    maas_cfg = config.project.get_config("platform.maas")

    maas_gw = maas_cfg["gateway"]
    _verify_gateway_allows_namespace(test_namespace, maas_gw["name"], maas_gw["namespace"])

    _deploy_maas_sim(test_namespace, maas_cfg)

    maas_sim_name = maas_cfg["llmisvc_name"]
    llm_endpoint = (
        f"http://{maas_sim_name}-kserve-workload-svc.{test_namespace}.svc.cluster.local:8000"
    )
    maas_model_name = config.project.get_config("platform.sim.model_name")
    logger.info("[maas] LLM direct endpoint: %s", llm_endpoint)
    _smoke_test(test_namespace, llm_endpoint, "sim-maas", maas_model_name)

    _register_maas_model(test_namespace, maas_cfg)

    maas_gateway_url = _resolve_maas_external_url()
    token_file_path = _create_maas_gateway_token(test_namespace, maas_gw["name"])

    model_path = f"/{test_namespace}/{maas_sim_name}"
    gateway_endpoint = f"{maas_gateway_url}{model_path}"
    logger.info("[maas] gateway endpoint: %s", gateway_endpoint)
    _smoke_test(
        test_namespace,
        gateway_endpoint,
        "maas-gateway",
        maas_model_name,
        api_key_file_path=token_file_path,
    )

    return gateway_endpoint, token_file_path


def _cleanup_maas(test_namespace):
    maas_cfg = config.project.get_config("platform.maas")
    _cleanup_maas_sim(test_namespace, maas_cfg)


def _endpoint_maas(test_namespace, gateway_cfg):
    maas_cfg = config.project.get_config("platform.maas")
    maas_gateway_url = _resolve_maas_external_url()
    model_path = f"/{test_namespace}/{maas_cfg['llmisvc_name']}"
    return f"{maas_gateway_url}{model_path}"


def _deploy_maas_sim(test_namespace, maas_cfg):
    """Deploy a sim for MaaS, with router in llmisvc mode, without in k8s mode."""
    maas_gw = maas_cfg["gateway"]
    _deploy_sim_llmisvc(
        test_namespace,
        name=maas_cfg["llmisvc_name"],
        gateway_name=maas_gw["name"],
        gateway_namespace=maas_gw["namespace"],
        maas_gateway_label=maas_cfg["maas_gateway_label"],
        skip_router=False,
    )


def _register_maas_model(test_namespace, maas_cfg):
    """Register the model with MaaS (after HTTPRoute exists)."""
    from projects.ag_praxis.toolbox.register_maas_model import main as register_maas_model_command

    register_maas_model_command.run(
        model_namespace=test_namespace,
        tenant_namespace=maas_cfg["tenant_namespace"],
        llmisvc_name=maas_cfg["llmisvc_name"],
    )


def _cleanup_maas_sim(test_namespace, maas_cfg):
    """Unregister and delete the MaaS sim LLMInferenceService."""
    from projects.ag_praxis.toolbox.unregister_maas_model import (
        main as unregister_maas_model_command,
    )
    from projects.core.dsl.utils.k8s import best_effort_oc

    unregister_maas_model_command.run(
        model_namespace=test_namespace,
        tenant_namespace=maas_cfg["tenant_namespace"],
        llmisvc_name=maas_cfg["llmisvc_name"],
    )
    maas_sim_name = maas_cfg["llmisvc_name"]
    if SIM_DEPLOY_MODE == "k8s":
        best_effort_oc("delete", "deployment", maas_sim_name, "-n", test_namespace)
        best_effort_oc("delete", "service", maas_sim_name, "-n", test_namespace)
    else:
        best_effort_oc("delete", "llminferenceservice", maas_sim_name, "-n", test_namespace)


def _patch_httproute_to_praxis(test_namespace, llmisvc_name):
    """Patch the kserve-created HTTPRoute to route traffic to Praxis instead of model backend."""
    import json

    from projects.core.dsl.utils.k8s import oc

    httproute_name = f"{llmisvc_name}-kserve-route"

    route_json = json.loads(
        oc(
            "get",
            "httproute",
            httproute_name,
            "-n",
            test_namespace,
            "-o",
            "json",
            log_stdout=False,
        ).stdout
    )

    patches = []
    for i, rule in enumerate(route_json.get("spec", {}).get("rules", [])):
        for j, _ in enumerate(rule.get("backendRefs", [])):
            patches.append(
                {
                    "op": "replace",
                    "path": f"/spec/rules/{i}/backendRefs/{j}/name",
                    "value": "praxis",
                }
            )
            patches.append(
                {"op": "replace", "path": f"/spec/rules/{i}/backendRefs/{j}/port", "value": 8080}
            )

    oc(
        "patch",
        "httproute",
        httproute_name,
        "-n",
        test_namespace,
        "--type=json",
        f"-p={json.dumps(patches)}",
    )
    logger.info("Patched HTTPRoute %s to route to praxis:8080", httproute_name)


def _verify_gateway_allows_namespace(test_namespace, gateway_name, gateway_namespace):
    """Verify that the MaaS gateway allows routes from the test namespace."""
    from projects.core.dsl.utils.k8s import oc_get_json

    gateway = oc_get_json("gateway", name=gateway_name, namespace=gateway_namespace)
    listeners = gateway.get("spec", {}).get("listeners", [])
    if not listeners:
        raise RuntimeError(f"Gateway {gateway_name} has no listeners")

    allowed = listeners[0].get("allowedRoutes", {}).get("namespaces", {})
    if allowed.get("from") == "All":
        return

    selector = allowed.get("selector", {})
    for expr in selector.get("matchExpressions", []):
        if (
            expr.get("key") == "kubernetes.io/metadata.name"
            and expr.get("operator") == "In"
            and test_namespace in expr.get("values", [])
        ):
            return

    raise RuntimeError(
        f"Gateway {gateway_name} in {gateway_namespace} does not allow routes "
        f"from namespace {test_namespace}. Run the prepare phase first."
    )


def _prep_maas_praxis(test_namespace, gateway_cfg):
    maas_cfg = config.project.get_config("platform.maas")

    maas_gw = maas_cfg["gateway"]
    _verify_gateway_allows_namespace(test_namespace, maas_gw["name"], maas_gw["namespace"])

    _deploy_maas_sim(test_namespace, maas_cfg)

    maas_sim_name = maas_cfg["llmisvc_name"]
    if SIM_DEPLOY_MODE == "k8s":
        llm_endpoint = f"http://{maas_sim_name}.{test_namespace}.svc:8000"
    else:
        llm_endpoint = (
            f"http://{maas_sim_name}-kserve-workload-svc.{test_namespace}.svc.cluster.local:8000"
        )
    logger.info("[maas-praxis] LLM direct endpoint: %s", llm_endpoint)
    maas_model_name = config.project.get_config("platform.sim.model_name")
    _smoke_test(test_namespace, llm_endpoint, "sim-maas", maas_model_name)

    _deploy_praxis(test_namespace, llm_endpoint)
    praxis_url = f"http://praxis.{test_namespace}.svc:8080"
    logger.info("[maas-praxis] praxis endpoint: %s", praxis_url)
    _smoke_test(test_namespace, praxis_url, "praxis", maas_model_name)

    _register_maas_model(test_namespace, maas_cfg)
    _patch_httproute_to_praxis(test_namespace, maas_sim_name)

    maas_gateway_url = _resolve_maas_external_url()
    api_key_file_path = _create_maas_api_key(test_namespace, maas_gateway_url)

    model_path = f"/{test_namespace}/{maas_sim_name}"
    gateway_endpoint = f"{maas_gateway_url}{model_path}"
    logger.info("[maas-praxis] gateway endpoint: %s", gateway_endpoint)
    _smoke_test(
        test_namespace,
        gateway_endpoint,
        "maas-gateway",
        maas_model_name,
        api_key_file_path=api_key_file_path,
    )

    return gateway_endpoint, api_key_file_path


def _cleanup_maas_praxis(test_namespace):
    maas_cfg = config.project.get_config("platform.maas")
    _cleanup_maas_sim(test_namespace, maas_cfg)
    _cleanup_praxis(test_namespace)


def _endpoint_maas_praxis(test_namespace, gateway_cfg):
    maas_cfg = config.project.get_config("platform.maas")
    maas_gateway_url = _resolve_maas_external_url()
    model_path = f"/{test_namespace}/{maas_cfg['llmisvc_name']}"
    return f"{maas_gateway_url}{model_path}"


FLAVORS = {
    "direct": (_prep_direct, _cleanup_direct, _endpoint_direct, _llmisvc_names_sim),
    "direct-gateway": (
        _prep_direct_gateway,
        _cleanup_direct_gateway,
        _endpoint_gateway,
        _llmisvc_names_sim,
    ),
    "praxis": (_prep_praxis, _cleanup_praxis_flavor, _endpoint_praxis, _llmisvc_names_sim),
    "praxis-gateway": (
        _prep_praxis_gateway,
        _cleanup_praxis_gateway,
        _endpoint_gateway,
        _llmisvc_names_sim,
    ),
    "maas": (
        _prep_maas,
        _cleanup_maas,
        _endpoint_maas,
        _llmisvc_names_maas,
    ),
    "maas-praxis": (
        _prep_maas_praxis,
        _cleanup_maas_praxis,
        _endpoint_maas_praxis,
        _llmisvc_names_maas,
    ),
}


def _validate_benchmark_rates():
    """Validate that all sweep rates divide evenly by replica_count."""
    benchmark = _build_benchmark_config(
        config.project.get_config("test.payload"),
        config.project.get_config("test.sweep"),
    )
    replica_count = benchmark.get("replica_count", 1)
    for rate in benchmark.get("rate", []):
        per_replica_rate = rate / replica_count
        if abs(per_replica_rate - round(per_replica_rate)) > 0.01:
            raise ValueError(
                f"rate={rate} does not divide evenly by replica_count={replica_count} "
                f"(got {per_replica_rate})"
            )


def do_test_praxis():
    logger.info("=== AG Praxis Project Test Phase ===")
    from projects.cluster.library.prom.collection import (
        prepare_user_workload_monitoring,
        validate_user_workload_monitoring,
    )
    from projects.core.orchestration.utils.k8s import ensure_namespace

    if config.project.get_config("test.run_benchmark"):
        _validate_benchmark_rates()

    validate_user_workload_monitoring()
    prepare_user_workload_monitoring(during="test")

    test_namespace = config.project.get_config("test.namespace")
    ensure_namespace(test_namespace)

    sim_cfg = config.project.get_config("platform.sim")
    gateway_cfg = sim_cfg["gateway"]

    _run_flavor_loop(test_namespace, gateway_cfg)


def _run_flavor_finalizers(
    flavor,
    test_namespace,
    cleanup_fn,
    llmisvc_name_fn,
    primary_exc,
    benchmark_times,
):
    import sys

    from projects.cluster.library.prom.collection import capture_prometheus

    finalizer_exc = None

    def _run_finalizer(description, callback, **kwargs):
        nonlocal finalizer_exc
        try:
            callback(**kwargs)
        except Exception:
            if primary_exc is None:
                logger.exception("Finalizer failed: %s", description)
                finalizer_exc = finalizer_exc or sys.exc_info()
            else:
                logger.exception("Ignoring %s failure after primary test failure", description)

    if SIM_DEPLOY_MODE == "k8s":
        from projects.kserve.toolbox.capture_sim_state import (
            main as capture_sim_state_command,
        )

        sim_name = config.project.get_config("platform.sim.llmisvc_name")

        def _capture_sim_state():
            capture_sim_state_command.run(
                namespace=test_namespace,
                name=sim_name,
            )

        _run_finalizer(f"capturing sim state for {sim_name}", _capture_sim_state)
    else:
        from projects.kserve.toolbox.capture_llmisvc_state import (
            main as capture_llmisvc_state_command,
        )

        llmisvc_names = llmisvc_name_fn()
        for llmisvc_name in llmisvc_names:

            def _capture_llmisvc_state(name=llmisvc_name):
                capture_llmisvc_state_command.run(
                    name,
                    namespace=test_namespace,
                )

            _run_finalizer(
                f"capturing LLMInferenceService state for {llmisvc_name}", _capture_llmisvc_state
            )

    if benchmark_times:
        start_time, end_time = benchmark_times

        def _capture_prom():
            with env.NextArtifactDir("prometheus_metrics"):
                capture_prometheus(start_time, end_time)

        _run_finalizer("capturing Prometheus metrics", _capture_prom)

    if not config.project.get_config("test.skip_cleanup"):
        _run_finalizer("cleaning up flavor resources", cleanup_fn, test_namespace=test_namespace)

    return finalizer_exc


def _run_flavor_loop(test_namespace, gateway_cfg):
    import sys
    from datetime import datetime

    flavors = config.project.get_config("test.flavors")
    continue_on_failure = config.project.get_config("test.continue_on_flavor_failure")
    failed_flavors = []

    for flavor in flavors:
        with env.NextArtifactDir(flavor):
            test_dir = env.ARTIFACT_DIR
            create_test_metadata(test_dir, {"flavor": flavor})

            flavor_fns = FLAVORS.get(flavor)
            if flavor_fns is None:
                raise ValueError(f"Unknown test flavor: {flavor!r}. Valid: {list(FLAVORS)}")

            prep_fn, cleanup_fn, endpoint_fn, llmisvc_name_fn = flavor_fns
            logger.info("--- Running test flavor: %s ---", flavor)
            primary_exc = None
            benchmark_times = None
            try:
                update_test_labels_with_timing(test_dir, "test", "start")

                if not config.project.get_config("test.skip_prepare"):
                    prep_result = prep_fn(test_namespace, gateway_cfg)
                else:
                    prep_result = endpoint_fn(test_namespace, gateway_cfg)

                if isinstance(prep_result, tuple):
                    benchmark_endpoint, api_key_file_path = prep_result
                else:
                    benchmark_endpoint, api_key_file_path = prep_result, None

                logger.info("[%s] benchmark endpoint: %s", flavor, benchmark_endpoint)

                if config.project.get_config("test.run_benchmark"):
                    update_test_labels_with_timing(test_dir, "benchmark", "start")
                    benchmark_start = datetime.now(UTC)
                    try:
                        _run_guidellm(
                            test_namespace, benchmark_endpoint, api_key_file_path=api_key_file_path
                        )
                        benchmark_times = (benchmark_start, datetime.now(UTC))
                    except Exception:
                        benchmark_times = (benchmark_start, datetime.now(UTC))
                        raise
                    update_test_labels_with_timing(test_dir, "benchmark", "end")
                else:
                    _write_benchmark_dry_run(benchmark_endpoint)
            except Exception as e:
                primary_exc = sys.exc_info()
                logger.exception("Flavor %s failed", flavor)
                update_test_labels_with_status(test_dir, False, f"Flavor {flavor} failed: {e}")
            finally:
                update_test_labels_with_timing(test_dir, "test", "end")

                finalizer_exc = _run_flavor_finalizers(
                    flavor,
                    test_namespace,
                    cleanup_fn,
                    llmisvc_name_fn,
                    primary_exc,
                    benchmark_times,
                )

            if primary_exc is not None:
                if not continue_on_failure:
                    raise primary_exc[1].with_traceback(primary_exc[2])
                failed_flavors.append((flavor, primary_exc[1]))
                continue

            if finalizer_exc is not None:
                update_test_labels_with_status(
                    test_dir,
                    False,
                    f"Flavor {flavor} finalizers failed: {finalizer_exc[1]}",
                )
                if not continue_on_failure:
                    raise finalizer_exc[1].with_traceback(finalizer_exc[2])
                failed_flavors.append((flavor, finalizer_exc[1]))
                continue

            update_test_labels_with_status(
                test_dir, True, f"Flavor {flavor} completed successfully"
            )

    if failed_flavors:
        details = "; ".join(f"{name}: {err}" for name, err in failed_flavors)
        raise RuntimeError(f"Test flavors failed: {details}")


def fournos_resolve_hardware_request(hardware_spec: dict):
    """
    Resolve hardware requirements for FournosJob based on AG Praxis project configuration.

    This is a stub implementation. Update spec.hardware based on project configuration.

    Args:
        hardware_spec: The current spec.hardware dict from the FournosJob. This object should be updated.

    """
    logger.info("Hardware resolution: stub implementation - no changes made")

    # Stub implementation - could be extended to:
    # - Read hardware config from project configuration
    # - Set hardware requirements based on workload needs
    # - Handle different hardware profiles (GPU, CPU, memory requirements)
    # - Example: return {"gpu": {"type": "nvidia-tesla-v100", "count": 1}, "memory": "32Gi"}

    return hardware_spec
