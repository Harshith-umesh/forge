from __future__ import annotations

import logging

from projects.core.library import env
from projects.core.library.postprocess import run_and_postprocess, write_test_labels
from projects.rhaiis.orchestration import runtime_config

logger = logging.getLogger(__name__)


def run(
    *,
    model_key: str,
    workload_key: str,
    namespace: str,
    deployment_name: str | None = None,
) -> int:
    return run_and_postprocess(
        do_test,
        model_key=model_key,
        workload_key=workload_key,
        namespace=namespace,
        deployment_name=deployment_name,
    )


def do_test(
    *,
    model_key: str,
    workload_key: str,
    namespace: str,
    deployment_name: str | None = None,
) -> int:
    with env.NextArtifactDir("testing"):
        return _run_test(
            model_key=model_key,
            workload_key=workload_key,
            namespace=namespace,
            deployment_name=deployment_name,
        )


def _run_test(
    *,
    model_key: str,
    workload_key: str,
    namespace: str,
    deployment_name: str | None = None,
) -> int:
    model_cfg = runtime_config.get_model(model_key)
    workload = runtime_config.get_workload(workload_key)
    accelerator = runtime_config.get_accelerator()
    deploy_cfg = runtime_config.get_deploy_config()
    benchmark_cfg = runtime_config.get_benchmark_config()

    if not deployment_name:
        deployment_name = runtime_config.derive_deployment_name(model_cfg["hf_model_id"])

    vllm_image = runtime_config.get_vllm_image(accelerator)
    vllm_defaults = runtime_config.get_vllm_defaults()
    vllm_args = runtime_config.merge_vllm_args(vllm_defaults, model_cfg, workload)
    env_vars = runtime_config.merge_env_vars(accelerator, model_cfg)

    rates = workload.get("rates", [1])
    max_seconds = workload.get("max_seconds", 180)

    _create_test_labels(model_key, workload_key, accelerator, vllm_args)

    logger.info(
        "Testing model=%s workload=%s accelerator=%s", model_cfg["name"], workload_key, accelerator
    )

    from projects.guidellm.toolbox.run_guidellm_benchmark.main import (
        run as run_guidellm_benchmark,
    )
    from projects.guidellm.toolbox.run_guidellm_benchmark.main import (
        wait_guidellm_benchmark_task,
    )
    from projects.rhaiis.toolbox.deploy_kserve_isvc.main import run as deploy_kserve_isvc
    from projects.rhaiis.toolbox.wait_isvc_ready.main import run as wait_isvc_ready

    benchmark_timeout = benchmark_cfg.get("timeout", 14400)
    wait_guidellm_benchmark_task._retry_config["attempts"] = max(1, benchmark_timeout // 10)

    try:
        logger.info("Deploying %s to %s/%s", model_cfg["hf_model_id"], namespace, deployment_name)
        deploy_kserve_isvc(
            deployment_name=deployment_name,
            namespace=namespace,
            model_id=model_cfg["hf_model_id"],
            vllm_image=vllm_image,
            accelerator=accelerator,
            vllm_args=vllm_args,
            env_vars=env_vars,
            replicas=deploy_cfg.get("replicas", 1),
            cpu_request=deploy_cfg.get("cpu_request", "4"),
            memory_request=deploy_cfg.get("memory_request", "16Gi"),
            storage_source=deploy_cfg.get("storage_source", "hf"),
            storage_pvc=deploy_cfg.get("storage_pvc", ""),
            image_pull_secret=deploy_cfg.get("image_pull_secret", ""),
            service_account_name=deploy_cfg.get("service_account_name", ""),
        )

        logger.info("Waiting for InferenceService to be ready")
        wait_isvc_ready(
            name=deployment_name,
            namespace=namespace,
            timeout_seconds=deploy_cfg.get("ready_timeout", 3600),
            health_check_timeout=deploy_cfg.get("health_check_timeout", 120),
        )

        endpoint_url = f"http://{deployment_name}-predictor.{namespace}.svc.cluster.local:8080"

        from projects.core.library import config

        profiler_cfg = runtime_config.get_profiler_config()
        profiler_enabled = profiler_cfg.get("enabled", False)
        run_benchmark = config.project.get_config("tests.rhaiis.run_benchmark", True)
        warmup_enabled = config.project.get_config("tests.rhaiis.warmup", True)

        if profiler_enabled:
            logger.info("Skipping warmup -- profiler step provides its own warmup")
            _run_profiler_step(
                deployment_name=deployment_name,
                namespace=namespace,
                endpoint_url=endpoint_url,
                benchmark_cfg=benchmark_cfg,
                model_cfg=model_cfg,
                workload=workload,
                workload_key=workload_key,
                benchmark_timeout=benchmark_timeout,
            )
        elif warmup_enabled:
            _run_warmup_step(
                deployment_name=deployment_name,
                namespace=namespace,
                endpoint_url=endpoint_url,
                benchmark_cfg=benchmark_cfg,
                model_cfg=model_cfg,
                workload=workload,
                benchmark_timeout=benchmark_timeout,
            )

        if not run_benchmark:
            logger.info("run_benchmark=false, skipping main benchmark")
        else:
            logger.info("Running benchmark at rates=%s", rates)

            benchmark_image = benchmark_cfg.get("image", "ghcr.io/vllm-project/guidellm:v0.6.0")

            guidellm_args = runtime_config.build_guidellm_args(
                benchmark_cfg=benchmark_cfg,
                model_id=model_cfg["hf_model_id"],
                data=workload["data"],
                rates=rates,
                max_seconds=max_seconds,
            )

            run_guidellm_benchmark(
                endpoint_url=f"{endpoint_url}/v1",
                name=f"guidellm-{deployment_name}",
                namespace=namespace,
                image=benchmark_image,
                timeout=benchmark_timeout,
                pvc_size=benchmark_cfg.get("pvc_size", "5Gi"),
                guidellm_args=guidellm_args,
            )
    finally:
        _capture_and_cleanup(deployment_name, namespace)

    try:
        _generate_psap_payload(model_cfg, accelerator, vllm_image, vllm_args, workload_key)
    except Exception:
        logger.warning("PSAP payload generation failed; continuing", exc_info=True)

    try:
        _generate_and_sync_dashboard_csv(model_cfg, accelerator, workload_key, vllm_args)
    except Exception:
        logger.warning("Dashboard CSV generation/sync failed; continuing", exc_info=True)

    profiler_cfg = runtime_config.get_profiler_config()
    if profiler_cfg.get("enabled", False):
        try:
            _upload_profiler_traces(model_cfg, accelerator, vllm_args, profiler_cfg)
        except Exception:
            logger.warning("Profiler trace upload failed; continuing", exc_info=True)

    try:
        _set_mlflow_metadata(
            model_key,
            workload_key,
            model_cfg,
            accelerator,
            vllm_image,
            vllm_args,
            benchmark_cfg,
            rates,
            max_seconds,
            namespace,
            deployment_name,
        )
    except Exception:
        logger.warning("Setting MLflow metadata failed; continuing", exc_info=True)

    return 0


def _create_test_labels(
    model_key: str, workload_key: str, accelerator: str, vllm_args: dict
) -> None:
    labels = {
        "model_key": model_key,
        "workload_key": workload_key,
        "accelerator": accelerator,
        "tensor_parallel_size": str(vllm_args.get("tensor-parallel-size", 1)),
    }
    write_test_labels(env.ARTIFACT_DIR, labels)
    logger.info("Created test labels: %s", labels)


def _set_mlflow_metadata(
    model_key: str,
    workload_key: str,
    model_cfg: dict,
    accelerator: str,
    vllm_image: str,
    vllm_args: dict,
    benchmark_cfg: dict,
    rates: list[int],
    max_seconds: int,
    namespace: str,
    deployment_name: str,
) -> None:
    from projects.core.library import config

    image_name, image_tag = runtime_config.split_image_tag(vllm_image)
    guidellm_image = benchmark_cfg.get("image", "ghcr.io/vllm-project/guidellm:v0.6.0")
    benchmark_args = benchmark_cfg.get("args", {})

    tags = {
        "project": "rhaiis",
        "model_key": model_key,
        "hf_model_id": model_cfg["hf_model_id"],
        "accelerator": accelerator,
        "tensor_parallel_size": str(vllm_args.get("tensor-parallel-size", 1)),
        "vllm_image": vllm_image,
        "vllm_version": image_tag,
        "workload_key": workload_key,
        "rates": ",".join(str(r) for r in rates),
        "max_seconds": str(max_seconds),
        "guidellm_image": guidellm_image,
        "namespace": namespace,
        "deployment_name": deployment_name,
    }
    for key, value in benchmark_args.items():
        tags[f"guidellm_{key}"] = str(value)

    config.project.set_config("caliper.export.backend.mlflow.config.tags", tags)
    logger.info("Set MLflow tags: %s", list(tags.keys()))


def _generate_psap_payload(
    model_cfg: dict,
    accelerator: str,
    vllm_image: str,
    vllm_args: dict,
    workload_key: str,
) -> None:
    from pathlib import Path

    from projects.rhaiis.postprocess.parser import generate_psap_payload, write_psap_payload

    matches = list(
        Path(env.ARTIFACT_DIR).glob("*__run_guidellm_benchmark/artifacts/results/benchmarks.json")
    )
    if not matches:
        logger.warning(
            "benchmarks.json not found under %s, skipping PSAP payload", env.ARTIFACT_DIR
        )
        return
    benchmarks_json = matches[0]

    payload = generate_psap_payload(
        benchmarks_json_path=benchmarks_json,
        model_id=model_cfg["hf_model_id"],
        vllm_image=vllm_image,
        vllm_args=vllm_args,
        accelerator=accelerator,
        workload_key=workload_key,
    )
    output_dir = Path(env.ARTIFACT_DIR) / "artifacts" / "results"
    write_psap_payload(
        payload=payload,
        output_dir=output_dir,
        accelerator=accelerator,
        model_id=model_cfg["hf_model_id"],
        workload_key=workload_key,
    )


def _generate_and_sync_dashboard_csv(
    model_cfg: dict,
    accelerator: str,
    workload_key: str,
    vllm_args: dict,
) -> None:
    from pathlib import Path

    from projects.core.library import config
    from projects.rhaiis.postprocess.csv_export import find_psap_files, generate_dashboard_csv

    psap_files = find_psap_files(Path(env.ARTIFACT_DIR))
    if not psap_files:
        logger.warning("No PSAP JSON files found, skipping dashboard CSV")
        return

    version = config.project.get_config("tests.rhaiis.version", "")
    if not version:
        logger.info("No version configured, skipping dashboard CSV generation")
        return

    csv_path = None
    for psap_file in psap_files:
        output_path = psap_file.parent / f"dashboard_{psap_file.stem}.csv"
        csv_path = generate_dashboard_csv(psap_file, version=version, output_path=output_path)
        logger.info("Generated dashboard CSV: %s", csv_path)

    if not csv_path:
        return

    csv_dashboard_cfg = config.project.get_config("caliper.postprocess.csv_dashboard", {})
    if not csv_dashboard_cfg or not csv_dashboard_cfg.get("enabled", False):
        logger.info("Dashboard CSV S3 sync not enabled")
        return

    from projects.rhaiis.postprocess.s3_dashboard import sync_csv_to_s3

    sync_result = sync_csv_to_s3(
        csv_path,
        s3_bucket=csv_dashboard_cfg.get("s3_bucket", "psap-dashboard-data"),
        s3_key=csv_dashboard_cfg.get("s3_key", "staging/rhaiis-dashboard/consolidated_dashboard.csv"),
        vault_name=csv_dashboard_cfg.get("vault", "psap-forge-dashboard-s3"),
        dry_run=config.project.get_config("caliper.export.dry_run", False),
    )
    logger.info("Dashboard CSV sync result: %s", sync_result)

    compare_version = config.project.get_config("tests.rhaiis.compare_version", "")
    if not compare_version:
        return

    _run_regression_check(csv_path, compare_version, version, model_cfg, accelerator)


def _run_regression_check(
    csv_path,
    compare_version: str,
    current_version: str,
    model_cfg: dict,
    accelerator: str,
) -> None:
    import tempfile
    from pathlib import Path

    from projects.caliper.cli.s3_export import create_s3_client, get_aws_credentials
    from projects.caliper.engine.kpi.analyze import run_regression_analysis
    from projects.core.library import config

    csv_dashboard_cfg = config.project.get_config("caliper.postprocess.csv_dashboard", {})
    s3_bucket = csv_dashboard_cfg.get("s3_bucket", "psap-dashboard-data")
    s3_key = csv_dashboard_cfg.get("s3_key", "staging/rhaiis-dashboard/consolidated_dashboard.csv")
    vault_name = csv_dashboard_cfg.get("vault", "psap-forge-dashboard-s3")

    credentials_path = get_aws_credentials(vault_name, "aws.credentials")
    if not credentials_path:
        logger.warning("AWS credentials not available, skipping regression analysis")
        return

    consolidated_path = None
    try:
        s3 = create_s3_client(credentials_path)
        with tempfile.NamedTemporaryFile(mode="w+b", suffix=".csv", delete=False) as tmp:
            consolidated_path = tmp.name
        s3.download_file(s3_bucket, s3_key, consolidated_path)
    except Exception as e:
        logger.warning("Could not download consolidated CSV for regression check: %s", e)
        return

    try:
        output_file = Path(env.ARTIFACT_DIR) / "regression_analysis.json"
        analysis = run_regression_analysis(
            current_csv_path=Path(str(csv_path)),
            consolidated_csv_path=Path(consolidated_path),
            compare_version=compare_version,
            current_version=current_version,
            output_file=output_file,
        )

        if analysis.get("regression_count", 0) > 0 or analysis.get("improvement_count", 0) > 0:
            from projects.core.notifications.send import send_regression_notification

            send_regression_notification(
                analysis,
                model=model_cfg.get("hf_model_id", ""),
                accelerator=accelerator,
                job_id=env.ARTIFACT_DIR.rsplit("/", 1)[-1] if env.ARTIFACT_DIR else "",
                slack_user=config.project.get_config("tests.rhaiis.slack_user", ""),
                webhook_vault="psap-forge-rhaiis-slack",
                notification_vault="psap-forge-notifications",
            )
    except Exception:
        logger.warning("Regression analysis failed; continuing", exc_info=True)
    finally:
        import os
        if consolidated_path and os.path.exists(consolidated_path):
            os.unlink(consolidated_path)


def _run_warmup_step(
    *,
    deployment_name: str,
    namespace: str,
    endpoint_url: str,
    benchmark_cfg: dict,
    model_cfg: dict,
    workload: dict,
    benchmark_timeout: int,
) -> None:
    """Run a short warmup benchmark to prime KV cache and CUDA kernels."""
    from projects.guidellm.toolbox.run_guidellm_benchmark.main import (
        run as run_guidellm_benchmark,
    )

    warmup_rates = [200]
    warmup_max_seconds = 60

    guidellm_args = runtime_config.build_guidellm_args(
        benchmark_cfg=benchmark_cfg,
        model_id=model_cfg["hf_model_id"],
        data=workload["data"],
        rates=warmup_rates,
        max_seconds=warmup_max_seconds,
    )

    logger.info("Running warmup (concurrency=200, duration=60s)")
    try:
        run_guidellm_benchmark(
            endpoint_url=f"{endpoint_url}/v1",
            name=f"guidellm-warmup-{deployment_name}",
            namespace=namespace,
            image=benchmark_cfg.get("image", "ghcr.io/vllm-project/guidellm:v0.6.0"),
            timeout=benchmark_timeout,
            pvc_size=benchmark_cfg.get("pvc_size", "5Gi"),
            guidellm_args=guidellm_args,
        )
        logger.info("Warmup completed")
    except Exception:
        logger.warning("Warmup failed; continuing with benchmark", exc_info=True)


def _run_profiler_step(
    *,
    deployment_name: str,
    namespace: str,
    endpoint_url: str,
    benchmark_cfg: dict,
    model_cfg: dict,
    workload: dict,
    workload_key: str,
    benchmark_timeout: int,
) -> None:
    """Run profiler-gated benchmarks: verify prereqs → enable gate → benchmark → disable gate → copy traces."""
    from projects.guidellm.toolbox.run_guidellm_benchmark.main import (
        run as run_guidellm_benchmark,
    )
    from projects.rhaiis.toolbox.copy_profiler_traces.main import run as copy_profiler_traces
    from projects.rhaiis.toolbox.enable_profiler_gate.main import run as enable_profiler_gate
    from projects.rhaiis.toolbox.verify_profiler_prereqs.main import run as verify_profiler_prereqs

    logger.info("Verifying profiler prerequisites")
    verify_profiler_prereqs(namespace=namespace)

    profiler_cfg = runtime_config.get_profiler_config()
    labels = profiler_cfg.get("labels", [])
    if not labels:
        logger.info("No profiler labels configured, skipping profiler step")
        return

    profiler_max_seconds = profiler_cfg.get("max_seconds", 60)

    for label in labels:
        logger.info("Profiling label=%s", label)

        gate_value = label if isinstance(label, str) else str(label)
        enable_profiler_gate(
            name=deployment_name,
            namespace=namespace,
            gate_value=gate_value,
        )

        profiler_rates = profiler_cfg.get("rates", [1])
        guidellm_args = runtime_config.build_guidellm_args(
            benchmark_cfg=benchmark_cfg,
            model_id=model_cfg["hf_model_id"],
            data=workload["data"],
            rates=profiler_rates,
            max_seconds=profiler_max_seconds,
        )

        try:
            run_guidellm_benchmark(
                endpoint_url=f"{endpoint_url}/v1",
                name=f"guidellm-profiler-{deployment_name}-{gate_value}",
                namespace=namespace,
                image=benchmark_cfg.get("image", "ghcr.io/vllm-project/guidellm:v0.6.0"),
                timeout=benchmark_timeout,
                pvc_size=benchmark_cfg.get("pvc_size", "5Gi"),
                guidellm_args=guidellm_args,
            )
        finally:
            enable_profiler_gate(
                name=deployment_name,
                namespace=namespace,
                disable=True,
            )

    logger.info("Copying profiler traces from pod")
    try:
        copy_profiler_traces(name=deployment_name, namespace=namespace)
    except Exception:
        logger.warning("Failed to copy profiler traces", exc_info=True)


def _upload_profiler_traces(
    model_cfg: dict,
    accelerator: str,
    vllm_args: dict,
    profiler_cfg: dict,
) -> None:
    from pathlib import Path

    from projects.core.library import config
    from projects.rhaiis.postprocess.s3_dashboard import upload_profiler_traces_to_s3

    traces_dir = Path(env.ARTIFACT_DIR) / "artifacts" / "traces"
    if not traces_dir.exists() or not any(traces_dir.iterdir()):
        logger.info("No profiler traces to upload")
        return

    version = config.project.get_config("tests.rhaiis.version", "")
    if not version:
        logger.info("No version configured, skipping profiler trace upload")
        return

    result = upload_profiler_traces_to_s3(
        traces_dir,
        model_name=model_cfg.get("hf_model_id", ""),
        accelerator=accelerator,
        tp_size=int(vllm_args.get("tensor-parallel-size", 1)),
        version=version,
        profile_labels=profiler_cfg.get("labels", []),
        s3_bucket=profiler_cfg.get("s3_bucket", "psap-dashboard-data"),
        s3_prefix=profiler_cfg.get("s3_prefix", "pytorch-profiles/rhaiis"),
        vault_name=profiler_cfg.get("vault", "psap-forge-dashboard-s3"),
        dry_run=config.project.get_config("caliper.export.dry_run", False),
    )
    logger.info("Profiler trace upload result: %s", result)


def _capture_and_cleanup(deployment_name: str, namespace: str) -> None:
    from projects.rhaiis.toolbox.capture_isvc_state.main import run as capture_isvc_state

    logger.info("Capturing state")
    try:
        capture_isvc_state(name=deployment_name, namespace=namespace)
    except Exception:
        logger.warning("Capture failed, continuing with cleanup", exc_info=True)

    from projects.rhaiis.toolbox.cleanup_isvc.main import run as cleanup_isvc

    logger.info("Cleaning up")
    try:
        cleanup_isvc(name=deployment_name, namespace=namespace)
    except Exception:
        logger.warning("Cleanup failed", exc_info=True)
