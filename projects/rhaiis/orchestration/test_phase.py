from __future__ import annotations

import logging
import uuid as _uuid_mod

from projects.core.library import env
from projects.core.library.postprocess import run_and_postprocess, write_test_labels
from projects.rhaiis.orchestration import runtime_config

logger = logging.getLogger(__name__)

_K8S_NAME_MAX = 63


def _guidellm_job_name(prefix: str, workload_key: str, deployment_name: str) -> str:
    """Build a K8s-safe job name: {prefix}-{workload_key}-{model}, trimming model to fit."""
    base = f"{prefix}-{workload_key}-"
    available = _K8S_NAME_MAX - len(base)
    model = deployment_name[:available] if available > 0 else ""
    return f"{base}{model}".rstrip("-")


def run(
    *,
    model_key: str,
    workload_keys: list[str],
    namespace: str,
    deployment_name: str | None = None,
) -> int:
    return run_and_postprocess(
        do_test,
        model_key=model_key,
        workload_keys=workload_keys,
        namespace=namespace,
        deployment_name=deployment_name,
    )


def do_test(
    *,
    model_key: str,
    workload_keys: list[str],
    namespace: str,
    deployment_name: str | None = None,
) -> int:
    with env.NextArtifactDir("testing"):
        return _run_test(
            model_key=model_key,
            workload_keys=workload_keys,
            namespace=namespace,
            deployment_name=deployment_name,
        )


def _run_test(
    *,
    model_key: str,
    workload_keys: list[str],
    namespace: str,
    deployment_name: str | None = None,
) -> int:
    model_cfg = runtime_config.get_model(model_key)
    accelerator = runtime_config.get_accelerator()
    gpu_type = runtime_config.get_gpu_type(accelerator) or accelerator
    from projects.core.library import config as _cfg
    cluster_tag = _cfg.project.get_config("rhaiis.cluster_tag", "")
    accelerator_key = f"{gpu_type}_{cluster_tag}".upper() if cluster_tag else gpu_type.upper()
    deploy_cfg = runtime_config.get_deploy_config()
    benchmark_cfg = runtime_config.get_benchmark_config()

    if not deployment_name:
        deployment_name = runtime_config.derive_deployment_name(model_cfg["hf_model_id"])

    vllm_image = runtime_config.get_vllm_image(accelerator)
    vllm_defaults = runtime_config.get_vllm_defaults()
    first_workload = runtime_config.get_workload(workload_keys[0])
    vllm_args = runtime_config.merge_vllm_args(vllm_defaults, model_cfg, first_workload)
    env_vars = runtime_config.merge_env_vars(accelerator, model_cfg)

    _create_test_labels(model_key, workload_keys[0], accelerator, vllm_args)

    from projects.guidellm.toolbox.run_guidellm_benchmark.main import (
        wait_guidellm_benchmark_task,
    )
    from projects.rhaiis.toolbox.deploy_kserve_isvc.main import run as deploy_kserve_isvc
    from projects.rhaiis.toolbox.wait_isvc_ready.main import run as wait_isvc_ready

    from projects.core.library import config as _cfg
    run_uuid = _cfg.project.get_config("tests.rhaiis.run_uuid", "") or str(_uuid_mod.uuid4())
    logger.info("Run UUID for this job: %s", run_uuid)

    import os, subprocess
    fjob_name = os.environ.get("FJOB_NAME", "")
    fjob_ns = os.environ.get("FOURNOS_WORKLOAD_NAMESPACE", "psap-automation")
    if fjob_name:
        try:
            mgmt_env = {k: v for k, v in os.environ.items() if k != "KUBECONFIG"}
            subprocess.run(
                ["oc", "annotate", "fournosjob", fjob_name, "-n", fjob_ns,
                 f"rhaiis.run-uuid={run_uuid}", "--overwrite"],
                check=False, capture_output=True, timeout=10, env=mgmt_env,
            )
            logger.info("Annotated FournosJob %s with run-uuid=%s", fjob_name, run_uuid)
        except Exception:
            logger.debug("Failed to annotate FournosJob with UUID", exc_info=True)

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
        warmup_enabled = config.project.get_config("tests.rhaiis.warmup", True)

        logger.info(
            "Running %d workload(s): %s", len(workload_keys), workload_keys,
        )

        # Phase 1: warmup or profiler for ALL workloads first
        for wl_key in workload_keys:
            workload = runtime_config.get_workload(wl_key)
            if profiler_enabled:
                logger.info("Running profiler for workload=%s", wl_key)
                _run_profiler_step(
                    deployment_name=deployment_name,
                    namespace=namespace,
                    endpoint_url=endpoint_url,
                    benchmark_cfg=benchmark_cfg,
                    model_cfg=model_cfg,
                    workload=workload,
                    workload_key=wl_key,
                    benchmark_timeout=benchmark_timeout,
                )
            elif warmup_enabled:
                logger.info("Running warmup for workload=%s", wl_key)
                _run_warmup_step(
                    deployment_name=deployment_name,
                    namespace=namespace,
                    endpoint_url=endpoint_url,
                    benchmark_cfg=benchmark_cfg,
                    model_cfg=model_cfg,
                    workload=workload,
                    workload_key=wl_key,
                    benchmark_timeout=benchmark_timeout,
                )

        if profiler_enabled:
            try:
                _upload_profiler_traces(model_cfg, gpu_type, vllm_args, profiler_cfg)
            except Exception:
                logger.warning("Profiler trace upload failed; continuing", exc_info=True)

        # Phase 2: benchmark + post-processing for ALL workloads
        for wl_key in workload_keys:
            _run_workload_benchmark(
                model_key=model_key,
                workload_key=wl_key,
                model_cfg=model_cfg,
                accelerator=accelerator,
                accelerator_key=accelerator_key,
                gpu_type=gpu_type,
                vllm_image=vllm_image,
                vllm_args=vllm_args,
                benchmark_cfg=benchmark_cfg,
                deployment_name=deployment_name,
                namespace=namespace,
                endpoint_url=endpoint_url,
                benchmark_timeout=benchmark_timeout,
                run_uuid=run_uuid,
            )

        try:
            first_workload = runtime_config.get_workload(workload_keys[0])
            first_rates = first_workload.get("rates", [1])
            first_max_seconds = first_workload.get("max_seconds", 180)
            _set_mlflow_metadata(
                model_key,
                ",".join(workload_keys),
                model_cfg,
                accelerator,
                vllm_image,
                vllm_args,
                benchmark_cfg,
                first_rates,
                first_max_seconds,
                namespace,
                deployment_name,
            )
        except Exception:
            logger.warning("Setting MLflow metadata failed; continuing", exc_info=True)
    finally:
        _capture_and_cleanup(deployment_name, namespace)

    try:
        _upload_predictor_log(run_uuid)
    except Exception:
        logger.warning("Predictor log upload failed; continuing", exc_info=True)

    return 0


def _run_workload_benchmark(
    *,
    model_key: str,
    workload_key: str,
    model_cfg: dict,
    accelerator: str,
    accelerator_key: str,
    gpu_type: str,
    vllm_image: str,
    vllm_args: dict,
    benchmark_cfg: dict,
    deployment_name: str,
    namespace: str,
    endpoint_url: str,
    benchmark_timeout: int,
    run_uuid: str,
) -> None:
    """Run benchmark and post-processing for a single workload."""
    logger.info("=== Benchmark %s (UUID: %s) ===", workload_key, run_uuid)

    workload = runtime_config.get_workload(workload_key)
    rates = workload.get("rates", [1])
    max_seconds = workload.get("max_seconds", 180)

    from projects.core.library import config
    from projects.guidellm.toolbox.run_guidellm_benchmark.main import (
        run as run_guidellm_benchmark,
    )

    run_benchmark = config.project.get_config("tests.rhaiis.run_benchmark", True)

    main_benchmark_dir = None
    if not run_benchmark:
        logger.info("run_benchmark=false, skipping main benchmark")
        try:
            _run_standalone_analysis(model_cfg, accelerator_key, vllm_args, run_uuid=run_uuid)
        except Exception:
            logger.warning("Standalone analysis failed; continuing", exc_info=True)
    else:
        logger.info("Running benchmark at rates=%s for workload=%s", rates, workload_key)

        benchmark_image = benchmark_cfg.get("image", "ghcr.io/vllm-project/guidellm:v0.6.0")

        guidellm_args = runtime_config.build_guidellm_args(
            benchmark_cfg=benchmark_cfg,
            model_id=model_cfg["hf_model_id"],
            data=workload["data"],
            rates=rates,
            max_seconds=max_seconds,
        )

        pre_index = env.next_artifact_index()
        run_guidellm_benchmark(
            endpoint_url=f"{endpoint_url}/v1",
            name=_guidellm_job_name("guidellm-bench", workload_key, deployment_name),
            namespace=namespace,
            image=benchmark_image,
            timeout=benchmark_timeout,
            pvc_size=benchmark_cfg.get("pvc_size", "5Gi"),
            guidellm_args=guidellm_args,
        )
        from pathlib import Path
        candidates = sorted(Path(env.ARTIFACT_DIR).glob(f"{pre_index:03d}__*"))
        if candidates:
            main_benchmark_dir = candidates[0]

    if main_benchmark_dir:
        try:
            _generate_psap_payload(
                model_cfg, accelerator_key, vllm_image, vllm_args, workload_key,
                run_uuid=run_uuid, benchmark_dir=main_benchmark_dir,
            )
        except Exception:
            logger.warning("PSAP payload generation failed; continuing", exc_info=True)

        try:
            _generate_and_sync_dashboard_csv(model_cfg, accelerator_key, workload_key, vllm_args, run_uuid=run_uuid)
        except Exception:
            logger.warning("Dashboard CSV generation/sync failed; continuing", exc_info=True)


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
    *,
    run_uuid: str = "",
    benchmark_dir: Path | None = None,
) -> None:
    from pathlib import Path

    from projects.rhaiis.postprocess.parser import generate_psap_payload, write_psap_payload

    if benchmark_dir:
        benchmarks_json = benchmark_dir / "artifacts" / "results" / "benchmarks.json"
    else:
        matches = list(
            Path(env.ARTIFACT_DIR).glob("*__run_guidellm_benchmark/artifacts/results/benchmarks.json")
        )
        benchmarks_json = matches[-1] if matches else None

    if not benchmarks_json or not benchmarks_json.exists():
        logger.warning(
            "benchmarks.json not found, skipping PSAP payload"
        )
        return

    payload = generate_psap_payload(
        benchmarks_json_path=benchmarks_json,
        model_id=model_cfg["hf_model_id"],
        vllm_image=vllm_image,
        vllm_args=vllm_args,
        accelerator=accelerator,
        workload_key=workload_key,
        run_uuid=run_uuid,
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
    *,
    run_uuid: str = "",
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
        csv_path = generate_dashboard_csv(psap_file, version=version, output_path=output_path, run_uuid=run_uuid)
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

    _run_regression_check(csv_path, compare_version, version, model_cfg, accelerator, run_uuid=run_uuid)


def _run_standalone_analysis(
    model_cfg: dict,
    accelerator: str,
    vllm_args: dict,
    *,
    run_uuid: str = "",
) -> None:
    """Run regression check + agent analysis using existing S3 data (no new benchmark)."""
    import tempfile
    from pathlib import Path

    from projects.caliper.cli.s3_export import create_s3_client, get_aws_credentials
    from projects.core.library import config

    agent_cfg = config.project.get_config("rhaiis.agent_analysis", {})
    if not agent_cfg.get("enabled", False):
        logger.info("Standalone analysis skipped: agent_analysis not enabled")
        return

    version = config.project.get_config("tests.rhaiis.version", "")
    compare_version = config.project.get_config("tests.rhaiis.compare_version", "")
    if not version or not compare_version:
        logger.info("Standalone analysis skipped: version or compare_version not configured")
        return

    csv_dashboard_cfg = config.project.get_config("caliper.postprocess.csv_dashboard", {})
    s3_bucket = csv_dashboard_cfg.get("s3_bucket", "psap-dashboard-data")
    s3_key = csv_dashboard_cfg.get("s3_key", "staging/rhaiis-dashboard/consolidated_dashboard.csv")
    vault_name = csv_dashboard_cfg.get("vault", "psap-forge-dashboard-s3")

    credentials_path = get_aws_credentials(vault_name, "aws.credentials")
    if not credentials_path:
        logger.warning("AWS credentials not available, skipping standalone analysis")
        return

    consolidated_path = None
    current_csv_path = None
    try:
        import pandas as pd

        s3 = create_s3_client(credentials_path)
        with tempfile.NamedTemporaryFile(mode="w+b", suffix=".csv", delete=False) as tmp:
            consolidated_path = tmp.name
        s3.download_file(s3_bucket, s3_key, consolidated_path)

        df = pd.read_csv(consolidated_path, on_bad_lines="warn")
        for col in df.columns:
            if df[col].dtype == object:
                df[col] = df[col].str.strip()

        model_id = model_cfg.get("hf_model_id", "")
        tp = str(vllm_args.get("tensor-parallel-size", 1))

        current_rows = df[
            (df["version"] == version)
            & (df["model"] == model_id)
            & (df["accelerator"] == accelerator)
            & (df["TP"].astype(str) == tp)
        ]

        if current_rows.empty:
            logger.warning(
                "No data found in S3 for version=%s, model=%s, accelerator=%s, TP=%s",
                version, model_id, accelerator, tp,
            )
            return

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
            current_csv_path = tmp.name
            current_rows.to_csv(tmp, index=False)

        logger.info("Standalone analysis: found %d rows for version=%s", len(current_rows), version)
        _run_regression_check(
            current_csv_path, compare_version, version, model_cfg, accelerator, run_uuid=run_uuid,
        )
    except Exception:
        logger.warning("Standalone analysis failed", exc_info=True)
    finally:
        import os
        if consolidated_path and os.path.exists(consolidated_path):
            os.unlink(consolidated_path)
        if current_csv_path and os.path.exists(current_csv_path):
            os.unlink(current_csv_path)


def _run_regression_check(
    csv_path,
    compare_version: str,
    current_version: str,
    model_cfg: dict,
    accelerator: str,
    *,
    run_uuid: str = "",
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
            report_url = ""
            agent_cfg = config.project.get_config("rhaiis.agent_analysis", {})
            if agent_cfg.get("enabled", False):
                report_url = _run_agent_analysis(
                    analysis, model_cfg, accelerator,
                    current_version, compare_version, run_uuid,
                    severity_threshold=agent_cfg.get("severity_threshold", 10),
                )

            from projects.core.notifications.send import send_regression_notification

            send_regression_notification(
                analysis,
                model=model_cfg.get("hf_model_id", ""),
                accelerator=accelerator,
                job_id=run_uuid,
                slack_user=config.project.get_config("tests.rhaiis.slack_user", ""),
                webhook_vault="psap-forge-rhaiis-slack",
                notification_vault="psap-forge-notifications",
                report_url=report_url,
            )
    except Exception:
        logger.warning("Regression analysis failed; continuing", exc_info=True)
    finally:
        import os
        if consolidated_path and os.path.exists(consolidated_path):
            os.unlink(consolidated_path)


def _run_agent_analysis(
    analysis: dict,
    model_cfg: dict,
    accelerator: str,
    current_version: str,
    compare_version: str,
    run_uuid: str,
    *,
    severity_threshold: int = 10,
) -> str:
    """Request AI agent analysis for severe regressions. Returns report URL or empty string."""
    from pathlib import Path

    from projects.core.library import config
    from projects.rhaiis.postprocess.agent import (
        AGENT_SEVERITY_THRESHOLD,
        build_pr_followup_prompt,
        check_agent_connectivity,
        markdown_to_html,
        request_agent_analysis,
        send_followup,
    )

    agent_cfg = config.project.get_config("rhaiis.agent_analysis", {})
    agent_url = agent_cfg.get("url", "")
    if not agent_url:
        logger.warning("Agent analysis enabled but no URL configured (rhaiis.agent_analysis.url)")
        return ""

    threshold = severity_threshold or AGENT_SEVERITY_THRESHOLD
    severe = [r for r in analysis.get("regressions", []) if abs(r["pct_diff"]) > threshold]
    if not severe:
        logger.info("No severe regressions (>%d%%), skipping agent analysis", threshold)
        return ""

    ok, detail = check_agent_connectivity(agent_url)
    if not ok:
        logger.warning("Agent not reachable, skipping analysis: %s", detail)
        return ""

    tp = str(model_cfg.get("vllm_args", {}).get("tensor-parallel-size", 1))
    model = model_cfg.get("hf_model_id", "")
    improvements = analysis.get("improvements", [])

    agent_response = request_agent_analysis(
        model=model,
        accelerator=accelerator,
        current_version=current_version,
        compare_version=compare_version,
        tp=tp,
        severe_regressions=severe,
        job_id=run_uuid,
        improvements=improvements if improvements else None,
        agent_url=agent_url,
    )
    if not agent_response:
        return ""

    pr_prompt = build_pr_followup_prompt(current_version, compare_version)
    pr_analysis = send_followup(message=pr_prompt, job_id=run_uuid, agent_url=agent_url)
    if pr_analysis:
        agent_response = f"{agent_response}\n\n---\n\n## Related Pull Requests\n\n{pr_analysis}"

    html_content = markdown_to_html(
        agent_response, run_uuid, model, current_version, compare_version,
    )
    html_path = Path(env.ARTIFACT_DIR) / f"agent_analysis_{run_uuid}.html"
    html_path.write_text(html_content, encoding="utf-8")
    logger.info("Agent analysis saved to %s", html_path)

    try:
        from projects.caliper.cli.s3_export import create_s3_client, get_aws_credentials

        csv_dashboard_cfg = config.project.get_config("caliper.postprocess.csv_dashboard", {})
        vault_name = csv_dashboard_cfg.get("vault", "psap-forge-dashboard-s3")
        credentials_path = get_aws_credentials(vault_name, "aws.credentials")
        if credentials_path:
            s3 = create_s3_client(credentials_path)
            s3_bucket = "psap-dashboard-data"
            s3_key = f"reports/rhaiis/{run_uuid}_analysis.html"
            s3.upload_file(
                str(html_path), s3_bucket, s3_key,
                ExtraArgs={"ContentType": "text/html"},
            )
            report_url = s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": s3_bucket, "Key": s3_key},
                ExpiresIn=2592000,
            )
            logger.info("Agent analysis uploaded to S3, presigned URL generated")
            return report_url
    except Exception:
        logger.warning("Failed to upload agent analysis to S3; continuing", exc_info=True)

    return ""


def _upload_predictor_log(run_uuid: str) -> None:
    """Upload the captured predictor pod log to S3 as ``logs/{run_uuid}.log``."""
    from pathlib import Path

    from projects.core.library import config
    from projects.rhaiis.postprocess.s3_dashboard import upload_predictor_log_to_s3

    matches = sorted(Path(env.ARTIFACT_DIR).glob("*__capture_isvc_state/artifacts/inferenceservice.pods.logs"))
    log_path = matches[-1] if matches else None
    if not log_path or not log_path.exists():
        logger.info("No predictor pod log found under %s, skipping upload", env.ARTIFACT_DIR)
        return

    csv_dashboard_cfg = config.project.get_config("caliper.postprocess.csv_dashboard", {})
    vault_name = csv_dashboard_cfg.get("vault", "psap-forge-dashboard-s3")

    result = upload_predictor_log_to_s3(
        log_path,
        run_uuid=run_uuid,
        vault_name=vault_name,
        dry_run=config.project.get_config("caliper.export.dry_run", False),
    )
    logger.info("Predictor log upload result: %s", result)


def _run_warmup_step(
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
    """Run a short warmup benchmark to prime KV cache and CUDA kernels."""
    from projects.guidellm.toolbox.run_guidellm_benchmark.main import (
        run as run_guidellm_benchmark,
    )

    from projects.core.library import config

    warmup_cfg = config.project.get_config("rhaiis.warmup", {})
    warmup_rate = warmup_cfg.get("rate", 200)
    warmup_max_seconds = warmup_cfg.get("max_seconds", 60)

    guidellm_args = runtime_config.build_guidellm_args(
        benchmark_cfg=benchmark_cfg,
        model_id=model_cfg["hf_model_id"],
        data=workload["data"],
        rates=[warmup_rate],
        max_seconds=warmup_max_seconds,
    )

    logger.info("Running warmup (concurrency=%d, duration=%ds)", warmup_rate, warmup_max_seconds)
    try:
        run_guidellm_benchmark(
            endpoint_url=f"{endpoint_url}/v1",
            name=_guidellm_job_name("guidellm-warmup", workload_key, deployment_name),
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
        labels = [_derive_profiler_label(workload)]
        logger.info("Auto-generated profiler label from workload: %s", labels[0])

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
                name=_guidellm_job_name("guidellm-profiler", workload_key, deployment_name),
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


def _derive_profiler_label(workload: dict) -> str:
    """Auto-generate a profiler label like 'isl1000_osl1000' from the workload data string."""
    data = workload.get("data", "")
    params = dict(item.split("=", 1) for item in data.split(",") if "=" in item)
    isl = params.get("prompt_tokens", "0")
    osl = params.get("output_tokens", "0")
    return f"isl{isl}_osl{osl}"


def _infer_profile_labels_from_traces(trace_files: list) -> list[str]:
    """Extract unique profile labels from trace filenames.

    Filenames follow the pattern: trace_rank{R}_pid{P}_run{LABEL}_range{S}-{E}.json
    The {LABEL} portion is the gate value used during profiling (e.g. 'isl1000_osl1000').
    """
    import re

    pattern = re.compile(r"_run(.+?)_range\d+-\d+")
    labels: list[str] = []
    seen: set[str] = set()
    for f in trace_files:
        m = pattern.search(f.name)
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            labels.append(m.group(1))
    return labels


def _upload_profiler_traces(
    model_cfg: dict,
    accelerator: str,
    vllm_args: dict,
    profiler_cfg: dict,
) -> None:
    from pathlib import Path

    from projects.core.library import config
    from projects.rhaiis.postprocess.s3_dashboard import upload_profiler_traces_to_s3

    trace_files = sorted(Path(env.ARTIFACT_DIR).glob("*__copy_profiler_traces/artifacts/traces/trace_*"))
    if not trace_files:
        logger.info("No profiler traces to upload")
        return

    traces_dir = trace_files[0].parent
    if len(set(f.parent for f in trace_files)) > 1:
        traces_dir = Path(env.ARTIFACT_DIR) / "artifacts" / "traces_combined"
        traces_dir.mkdir(parents=True, exist_ok=True)
        for f in trace_files:
            import shutil
            shutil.copy2(f, traces_dir / f.name)
    logger.info("Found %d profiler trace files in %s", len(trace_files), traces_dir)

    version = config.project.get_config("tests.rhaiis.version", "")
    if not version:
        logger.info("No version configured, skipping profiler trace upload")
        return

    profile_labels = profiler_cfg.get("labels", [])
    if not profile_labels:
        profile_labels = _infer_profile_labels_from_traces(trace_files)
        logger.info("Inferred profile labels from trace filenames: %s", profile_labels)

    result = upload_profiler_traces_to_s3(
        traces_dir,
        model_name=model_cfg.get("hf_model_id", ""),
        accelerator=accelerator,
        tp_size=int(vllm_args.get("tensor-parallel-size", 1)),
        version=version,
        profile_labels=profile_labels,
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
