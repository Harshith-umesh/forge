"""GuideLLM post-processing with the llm-d dashboard CSV schema."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from projects.caliper.engine.model import (
    ParseResult,
    PostProcessingPlugin,
    TestBaseNode,
    UnifiedRunModel,
)
from projects.guidellm.postprocess.guidellm.ai_eval import GuideLLMAIEvaluator
from projects.guidellm.postprocess.guidellm.dashboard import (
    compute_dashboard_kpis,
    dashboard_kpi_catalog,
    deployment_metadata_from_profile,
    enrich_guidellm_parse_result,
    export_dashboard_kpis_to_csv,
    normalize_product_version,
    validate_dashboard_fieldnames,
)
from projects.guidellm.postprocess.guidellm.parsing import GuideLLMKpiHandler, GuideLLMParser
from projects.llm_d.orchestration.render_inference_service import _build_vllm_args
from projects.llm_d.orchestration.runtime_config import deep_merge

FIELDNAMES = [
    "run",
    "accelerator",
    "model",
    "version",
    "prompt toks",
    "output toks",
    "TP",
    "DP",
    "EP",
    "replicas",
    "prefill_pod_count",
    "decode_pod_count",
    "router_config",
    "measured concurrency",
    "intended concurrency",
    "measured rps",
    "output_tok/sec",
    "total_tok/sec",
    "prompt_token_count_mean",
    "prompt_token_count_p99",
    "output_token_count_mean",
    "output_token_count_p99",
    "ttft_median",
    "ttft_p95",
    "ttft_p1",
    "ttft_p999",
    "tpot_median",
    "tpot_p95",
    "tpot_p99",
    "tpot_p999",
    "tpot_p1",
    "itl_median",
    "itl_p95",
    "itl_p999",
    "itl_p1",
    "request_latency_median",
    "request_latency_min",
    "request_latency_max",
    "successful_requests",
    "errored_requests",
    "uuid",
    "ttft_mean",
    "ttft_p99",
    "itl_mean",
    "itl_p99",
    "runtime_args",
    "guidellm_start_time_ms",
    "guidellm_end_time_ms",
    "image_tag",
    "guidellm_version",
    "notes",
]
validate_dashboard_fieldnames(FIELDNAMES)


class LlmDGuideLLMPlugin(PostProcessingPlugin):
    """Keep generic GuideLLM outputs and add the llm-d dashboard projection."""

    def __init__(self) -> None:
        self.parser = GuideLLMParser()
        self.kpi_handler = GuideLLMKpiHandler()
        self.ai_evaluator = GuideLLMAIEvaluator()

    def parse(self, nodes: list[TestBaseNode]) -> ParseResult:
        parsed = enrich_guidellm_parse_result(self.parser.parse(nodes), nodes)
        nodes_by_path = {str(node.test_path): node for node in nodes}
        records = []
        for record in parsed.records:
            node = nodes_by_path.get(record.test_base_path)
            deployment_metadata = _extract_deployment_metadata(node) if node else {}
            test_labels = node.test_labels.get("labels", {}) if node else {}
            hf_model_id = test_labels.get("model_name")
            if hf_model_id:
                record.metrics["hf_model_id"] = hf_model_id
            for key, value in deployment_metadata.items():
                record.metrics.setdefault(key, value)
            records.append(record)
        return ParseResult(records=records, warnings=parsed.warnings)

    def kpi_catalog(self) -> list[dict[str, Any]]:
        return self.kpi_handler.get_catalog() + dashboard_kpi_catalog(prefix="llmd")

    def compute_kpis(self, model: UnifiedRunModel) -> list[dict[str, Any]]:
        return self.kpi_handler.compute_kpis(model) + compute_dashboard_kpis(model, prefix="llmd")

    def export_kpis_to_csv(
        self,
        kpi_records: list[dict[str, Any]],
        output_path: Path,
        include_header_comments: bool = True,
    ) -> str:
        def metadata_row(labels: dict[str, Any]) -> dict[str, Any]:
            accelerator = labels.get("gpu_type") or labels.get("accelerator", "")
            model = labels.get("hf_model_id") or labels.get("model_name", "")
            run_model = model.replace("/", "-")
            tp = labels.get("tensor_parallel_size") or labels.get("TP", "")
            replicas = labels.get("replicas", "")
            version = normalize_product_version(
                labels.get("product_version") or labels.get("version", "")
            )
            deployment_profile = labels.get("deployment_profile", "")
            if version and deployment_profile:
                version = f"{version}-{deployment_profile}"
            return {
                "run": "-".join(str(value) for value in (accelerator, run_model, tp) if value),
                "accelerator": accelerator,
                "model": model,
                "version": version,
                "prompt toks": labels.get("prompt_toks", ""),
                "output toks": labels.get("output_toks", ""),
                "TP": tp,
                "DP": labels.get("DP") or labels.get("data_parallel_size") or 0,
                "EP": labels.get("EP") or labels.get("expert_parallel_size") or 0,
                "replicas": replicas,
                "prefill_pod_count": labels.get("prefill_pod_count", 0),
                "decode_pod_count": labels.get("decode_pod_count", 0),
                "router_config": labels.get("router_config", ""),
                "uuid": labels.get("run_uuid", ""),
                "runtime_args": labels.get("runtime_args", ""),
                "guidellm_start_time_ms": labels.get("guidellm_start_time_ms", ""),
                "guidellm_end_time_ms": labels.get("guidellm_end_time_ms", ""),
                "image_tag": labels.get("image_tag", ""),
                "guidellm_version": labels.get("guidellm_version", ""),
                "notes": labels.get("notes", ""),
            }

        return export_dashboard_kpis_to_csv(
            kpi_records,
            output_path,
            prefix="llmd",
            fieldnames=FIELDNAMES,
            metadata_row=metadata_row,
        )

    def build_ai_data_payload(self, model: UnifiedRunModel) -> dict[str, Any]:
        return self.ai_evaluator.build_payload(model, self)


def get_plugin() -> PostProcessingPlugin:
    return LlmDGuideLLMPlugin()


def _extract_deployment_metadata(node: TestBaseNode) -> dict[str, Any]:
    """Recover llm-d deployment metadata when only config.yaml was exported."""
    config_path = next((path for path in node.artifact_paths if path.name == "config.yaml"), None)
    if config_path is None:
        return {}
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(config, dict):
        return {}

    runtime = config.get("runtime", {})
    deployments = config.get("deployments", {})
    profile_name = runtime.get("deployment_profile")
    defaults = deployments.get("defaults", {})
    profile_override = deployments.get("profiles", {}).get(profile_name, {})
    profile = deep_merge(defaults, profile_override)

    metadata = {
        "model_name": runtime.get("model_name"),
        "hf_model_id": runtime.get("model_name"),
        "replicas": profile.get("replicas"),
        "tensor_parallel_size": profile.get("tensor_parallelism"),
    }
    configured_labels = config.get("cpt", {}).get("kpi", {}).get("labels", {})
    metadata["gpu_type"] = configured_labels.get("gpu_type") or _extract_accelerator(node)
    metadata["product_version"] = configured_labels.get("product_version")
    metadata.update(deployment_metadata_from_profile(profile, profile_name=profile_name))
    vllm_args = profile.get("vllm_extra", {}).get("args", {})
    if vllm_args:
        metadata["runtime_args"] = " ".join(_build_vllm_args(vllm_args))
    serving_image = profile.get("serving_image")
    if not serving_image:
        serving_image = _extract_serving_image(node)
    if serving_image:
        metadata["image_tag"] = serving_image
    return {key: value for key, value in metadata.items() if value not in (None, "")}


def _extract_serving_image(node: TestBaseNode) -> str | None:
    deployment_path = next(
        (
            path
            for path in node.artifact_paths
            if path.name
            in {
                "llminferenceservice.deployments.json",
                "llminferenceservice.deployments.yaml",
            }
        ),
        None,
    )
    if deployment_path is None:
        return None
    try:
        deployments = yaml.safe_load(deployment_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    for deployment in deployments.get("items", []) if isinstance(deployments, dict) else []:
        containers = (
            deployment.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        )
        for container in containers:
            if container.get("name") == "main" and container.get("image"):
                return str(container["image"])
    return None


def _extract_accelerator(node: TestBaseNode) -> str | None:
    """Infer the GPU family from captured serving-pod placement."""
    pods_path = next(
        (path for path in node.artifact_paths if path.name == "llminferenceservice.pods.yaml"),
        None,
    )
    if pods_path is None:
        return None
    try:
        pods = yaml.safe_load(pods_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    for pod in pods.get("items", []) if isinstance(pods, dict) else []:
        node_name = str(pod.get("spec", {}).get("nodeName", ""))
        match = re.search(r"(?:^|-)gpu-([a-z]+\d+[a-z0-9]*)(?:-|$)", node_name, re.IGNORECASE)
        if match:
            return match.group(1).upper()
    return None
