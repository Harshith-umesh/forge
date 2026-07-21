from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from projects.core.dsl import shell
from projects.core.dsl.utils import slugify_identifier
from projects.core.library import config, env
from projects.core.library.postprocess import run_and_postprocess, write_test_labels
from projects.core.library.run import SignalInterrupt
from projects.core.orchestration.utils.k8s import ensure_namespace
from projects.guidellm.toolbox.run_guidellm_benchmark import build_guidellm_args
from projects.guidellm.toolbox.run_guidellm_benchmark import main as run_guidellm_benchmark_command
from projects.guidellm.toolbox.run_smoke_request import main as run_smoke_request_command
from projects.kserve.toolbox.capture_llmisvc_state import main as capture_llmisvc_state
from projects.kserve.toolbox.deploy_llmisvc import main as deploy_llmisvc
from projects.kserve.toolbox.wait_kserve_ready import main as wait_kserve_ready
from projects.llm_d.orchestration import runtime_config
from projects.llm_d.orchestration.prepare_phase import prepare_model_cache
from projects.llm_d.orchestration.render_inference_service import (
    render_inference_service_from_parts,
)
from projects.llm_d.orchestration.utils import write_yaml
from projects.llm_d.toolbox.cleanup_test_resources import main as cleanup_test_resources_command

logger = logging.getLogger(__name__)


def create_test_labels() -> None:
    """Create __test_labels__.yaml with model name and guidellm configuration."""

    model_name = runtime_config.get_model_name()
    deployment_profile = runtime_config.get_deployment_profile_name()
    benchmark_keys = runtime_config.get_benchmark_keys()

    labels = {
        "model_name": model_name,
        "deployment_profile": deployment_profile,
    }

    if benchmark_keys:
        labels["guidellm_loadshape"] = benchmark_keys[0]

    write_test_labels(env.ARTIFACT_DIR, labels)
    logger.info("Created test labels: %s", labels)


def run() -> int:
    """Main test function that wraps do_test() with outcome postprocessing."""

    dry_run = config.project.get_config("runtime.kserve.dry_run", False)
    if dry_run:
        ret = do_test()
        logger.info("Kserve dry-run mode enabled - Skipping caliper post-processing")
        return ret

    return run_and_postprocess(do_test)


def run_finalizers(
    endpoint_url: str | None,
    primary_exc: tuple[type[BaseException], BaseException, Any] | None,
    finalizer_exc: tuple[type[BaseException], BaseException, Any] | None,
) -> tuple[type[BaseException], BaseException, Any] | None:
    def _run_finalizer(
        description: str,
        callback,
        **kwargs,
    ):
        try:
            callback(**kwargs)
        except Exception:
            if primary_exc is None:
                logger.exception("Finalizer failed: %s", description)
                return finalizer_exc or sys.exc_info()
            logger.exception("Ignoring %s failure after primary test failure", description)
        return finalizer_exc

    namespace = runtime_config.get_namespace()
    platform = runtime_config.get_platform_config()
    capture_namespace_events = platform["artifacts"]["capture_namespace_events"]

    finalizer_exc = _run_finalizer(
        "capture inference-service state",
        capture_inference_service_state,
    )
    finalizer_exc = _run_finalizer(
        "write endpoint URL",
        write_endpoint_url,
        artifact_dir=env.ARTIFACT_DIR,
        endpoint_url=endpoint_url,
    )
    finalizer_exc = _run_finalizer(
        "capture namespace events",
        capture_namespace_events_after_test,
        artifact_dir=env.ARTIFACT_DIR,
        namespace=namespace,
        capture_namespace_events=capture_namespace_events,
    )
    finalizer_exc = _run_finalizer(
        "cleanup runtime resources",
        cleanup_test_resources,
    )

    return primary_exc, finalizer_exc


def do_test() -> int:
    # Load minimal config needed for orchestration flow

    namespace = runtime_config.get_namespace()
    dry_run = config.project.get_config("runtime.kserve.dry_run", False)

    if not dry_run:
        # Ensure namespace exists before starting any deployments
        ensure_namespace(
            namespace, labels=config.project.get_config("platform.cluster.namespace.labels")
        )

    endpoint_url: str | None = None
    primary_exc: tuple[type[BaseException], BaseException, Any] | None = None
    finalizer_exc: tuple[type[BaseException], BaseException, Any] | None = None

    with env.NextArtifactDir("llmd_test"):
        try:
            # Create test labels with actual model and profile information
            create_test_labels()

            endpoint_url = deploy_inference_service()

            if dry_run:
                logging.warning("Running in dry-run mode, skipping the rest of the test steps")
                return 0

            if not endpoint_url:
                raise ValueError("Failed to extract the endpoint_url from the LLMISVC deployment")
            run_smoke_request(endpoint_url=endpoint_url)

            run_guidellm_benchmark(endpoint_url=endpoint_url)
        except Exception:
            primary_exc = sys.exc_info()
        except SignalInterrupt:
            primary_exc = sys.exc_info()
        finally:
            do_finalizers = config.project.get_config("runtime.run_test_finalizers")
            if primary_exc and isinstance(primary_exc[1], SignalInterrupt):
                logging.warning("Caught a SignalInterrupt, skipping the finalizers")
                do_finalizers = False

            if dry_run:
                do_finalizers = False

            if do_finalizers:
                primary_exc, finalizer_exc = run_finalizers(
                    endpoint_url, primary_exc, finalizer_exc
                )

    if primary_exc is not None:
        raise primary_exc[1].with_traceback(primary_exc[2])

    if finalizer_exc is not None:
        raise finalizer_exc[1].with_traceback(finalizer_exc[2])

    return 0


def deploy_inference_service() -> str:
    """Deploy LLMInferenceService and return endpoint URL.

    Returns:
        Gateway endpoint URL for the deployed service
    """
    logger.info("Starting LLMInferenceService deployment")

    # Load config where it's consumed

    namespace = runtime_config.get_namespace()
    platform = runtime_config.get_platform_config()
    gateway = platform["gateway"]

    # Check if dry-run mode is enabled early
    dry_run = config.project.get_config("runtime.kserve.dry_run", False)

    # Step 1: Ensure model cache is ready (skip in dry-run)
    if not dry_run:
        _prepare_model_cache()
    else:
        logger.info("Skipping model cache preparation - dry-run mode enabled")

    # Step 2: Wait for the serving control plane to settle before creating the service.
    if not dry_run:
        rhoai_namespace = platform["rhoai"]["namespace"]
        wait_kserve_ready.run(namespace=rhoai_namespace)

    # Step 3: Build and write inference service manifest
    manifest_path = _build_inference_service_manifest()

    # Step 4: Deploy the service and wait for endpoint
    logger.info("Deploying LLMInferenceService from manifest: %s", manifest_path)

    endpoint_url = deploy_llmisvc.run(
        namespace=namespace,
        inference_service_manifest_path=str(manifest_path),
        gateway_status_address_name=gateway["status_address_name"],
        dry_run=dry_run,
    )

    if dry_run:
        llmisvc_manifest_path = endpoint_url
        logger.info("Dry-run completed: LLMInferenceService manifest prepared:")
        logger.info(llmisvc_manifest_path)
        return llmisvc_manifest_path

    logger.info("LLMInferenceService deployed successfully, endpoint: %s", endpoint_url)
    return endpoint_url


def _prepare_model_cache() -> None:
    """Ensure model cache PVC is ready for deployment."""

    model_name = runtime_config.get_model_name()
    logger.info("Preparing model cache for model: %s", model_name)

    # Use the same prepare_model_cache function as the prepare phase
    # This includes vault token handling and PVC existence checks
    prepare_model_cache()


def _build_inference_service_manifest() -> Path:
    """Build and write the LLMInferenceService manifest."""

    config_dir = runtime_config.get_config_dir()
    namespace = runtime_config.get_namespace()
    platform = runtime_config.get_platform_config()
    inference_service = platform["inference_service"]
    model_name = runtime_config.get_model_name()
    model_slug = runtime_config.get_model_slug(model_name)
    deployment_profile = runtime_config.get_deployment_profile()
    model_cache = runtime_config.get_model_cache_config()

    benchmark_overrides = runtime_config.get_benchmark_deployment_overrides()
    if benchmark_overrides:
        deployment_profile = runtime_config.deep_merge(deployment_profile, benchmark_overrides)

    # Build the InferenceService manifest
    deployment_profile_name = runtime_config.get_deployment_profile_name()
    manifest = render_inference_service_from_parts(
        config_dir=config_dir,
        namespace=namespace,
        inference_service=inference_service,
        model_name=model_name,
        model_slug=model_slug,
        deployment_profile=deployment_profile,
        model_cache=model_cache,
        deployment_profile_name=deployment_profile_name,
    )

    # Write the manifest to artifacts
    artifacts_dir = env.ARTIFACT_DIR / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = artifacts_dir / "llminferenceservice.yaml"
    write_yaml(manifest_path, manifest)

    logger.info("Built LLMInferenceService manifest: %s", manifest_path)
    return manifest_path


def run_smoke_request(*, endpoint_url: str) -> dict[str, object]:
    # Load config where it's consumed

    namespace = runtime_config.get_namespace()
    platform = runtime_config.get_platform_config()
    smoke = platform["smoke"]
    smoke_request = runtime_config.get_smoke_request()

    return run_smoke_request_command.run(
        namespace=namespace,
        endpoint_url=endpoint_url,
        pod_name=smoke["pod_name"],
        client_image=smoke["client_image"],
        endpoint_path=smoke["endpoint_path"],
        request_timeout_seconds=smoke["request_timeout_seconds"],
        served_model_name=runtime_config.get_served_model_name(),
        prompt=smoke_request["prompt"],
        max_tokens=smoke_request["max_tokens"],
        temperature=smoke_request["temperature"],
    )


def run_guidellm_benchmark(*, endpoint_url: str) -> None:
    namespace = runtime_config.get_namespace()
    benchmark = runtime_config.get_benchmark_config()

    if benchmark is None:
        return

    benchmark_key = runtime_config.get_benchmark_keys()[0]
    guidellm_args = build_guidellm_args(benchmark)
    if not any(arg.startswith("--processor=") for arg in guidellm_args):
        guidellm_args.append(f"--processor={runtime_config.get_model_name()}")
    artifact_name = f"benchmark_{slugify_identifier(benchmark_key, max_length=48)}"
    with env.NextArtifactDir(artifact_name):
        run_guidellm_benchmark_command.run(
            endpoint_url=endpoint_url,
            name=benchmark.get("job_name"),
            namespace=namespace,
            image=benchmark.get("image"),
            timeout=benchmark.get("timeout_seconds"),
            pvc_size=benchmark.get("pvc_size"),
            guidellm_args=guidellm_args,
        )


def capture_inference_service_state() -> None:
    # Load config where it's consumed

    namespace = runtime_config.get_namespace()
    platform = runtime_config.get_platform_config()
    inference_service = platform["inference_service"]

    capture_llmisvc_state.run(
        llmisvc_name=inference_service["name"],
        namespace=namespace,
    )


def write_endpoint_url(*, artifact_dir: Path, endpoint_url: str | None) -> None:
    if not endpoint_url:
        return

    endpoint_file = artifact_dir / "artifacts" / "endpoint.url"
    endpoint_file.parent.mkdir(parents=True, exist_ok=True)
    endpoint_file.write_text(f"{endpoint_url}\n", encoding="utf-8")


def cleanup_test_resources() -> None:
    """Cleanup test resources using the toolbox script"""

    # Skip cleanup when in dry-run mode
    dry_run = config.project.get_config("runtime.kserve.dry_run", False)
    if dry_run:
        logger.info("Skipping cleanup_test_resources - dry-run mode enabled")
        return

    namespace = runtime_config.get_namespace()
    platform = runtime_config.get_platform_config()
    inference_service = platform["inference_service"]
    smoke = platform["smoke"]

    cleanup_test_resources_command.run(
        namespace=namespace,
        inference_service_name=inference_service["name"],
        smoke_pod_name=smoke["pod_name"],
        benchmark_job_name=runtime_config.get_benchmark_job_name(),
    )


def capture_namespace_events_after_test(
    *,
    artifact_dir: Path,
    namespace: str,
    capture_namespace_events: bool,
) -> None:
    if not capture_namespace_events:
        return

    shell.run(
        f"oc get events -n {namespace} --sort-by=.metadata.creationTimestamp",
        check=False,
        stdout_dest=artifact_dir / "artifacts" / "namespace.events.txt",
    )
