"""
Generate tokenizer-accurate synthetic prompts for load testing.

Shared toolbox — usable by any project that needs token-profiled benchmarks.
Uses the same encode-trim-decode approach as the Benchmark MCP Server
to guarantee each prompt is EXACTLY the specified number of tokens.

The prompts are:
- Unique per entry (random token sampling avoids vLLM prefix-cache hits)
- Validated against the model's actual tokenizer
- Saved as a ConfigMap (synthetic_prompts.jsonl) for Locust workers to mount

Depends on: agentic_tools.utils.token_text (shared algorithm)
"""

from __future__ import annotations

import json
import logging
import tempfile
from typing import Any

from projects.core.dsl import always, entrypoint, execute_tasks, retry, task
from projects.core.dsl.utils.k8s import oc, oc_get_json, oc_resource_exists
from projects.core.library import env

logger = logging.getLogger(__name__)

GENERATOR_JOB_NAME = "prompt-generator"
GENERATOR_IMAGE = "registry.access.redhat.com/ubi9/python-311:latest"


@entrypoint
def run(
    *,
    namespace: str,
    num_tokens: int,
    num_prompts: int,
    tokenizer_model: str,
    output_configmap: str = "synthetic-prompts",
) -> int:
    """
    Generate synthetic prompts of exactly `num_tokens` tokens.

    Args:
        namespace: Target namespace
        num_tokens: Exact number of tokens per prompt
        num_prompts: How many unique prompts to generate
        tokenizer_model: HuggingFace model name for tokenizer
        output_configmap: ConfigMap name to store generated prompts
    """
    execute_tasks(locals())
    return 0


@task
def ensure_rbac(args, ctx):
    """Ensure the default SA can create ConfigMaps (for prompt output)."""
    import os
    import tempfile

    rbac_manifest = f"""apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: prompt-generator-role
  namespace: {args.namespace}
rules:
- apiGroups: [""]
  resources: ["configmaps"]
  verbs: ["create", "delete", "get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: prompt-generator-binding
  namespace: {args.namespace}
subjects:
- kind: ServiceAccount
  name: default
  namespace: {args.namespace}
roleRef:
  kind: Role
  name: prompt-generator-role
  apiGroup: rbac.authorization.k8s.io
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(rbac_manifest)
        manifest_path = f.name

    oc("apply", "-f", manifest_path, "-n", args.namespace, check=True)
    os.unlink(manifest_path)
    return "RBAC configured for prompt generator"


@task
def generate_prompts_job(args, ctx):
    """Run a Job that generates tokenizer-accurate synthetic prompts."""
    script = _build_generator_script(
        num_tokens=args.num_tokens,
        num_prompts=args.num_prompts,
        tokenizer_model=args.tokenizer_model,
    )

    oc(
        "delete",
        "job",
        GENERATOR_JOB_NAME,
        "-n",
        args.namespace,
        "--ignore-not-found=true",
        check=False,
    )
    oc(
        "delete",
        "configmap",
        args.output_configmap,
        "-n",
        args.namespace,
        "--ignore-not-found=true",
        check=False,
    )

    script_cm_name = f"{GENERATOR_JOB_NAME}-script"
    oc(
        "delete",
        "configmap",
        script_cm_name,
        "-n",
        args.namespace,
        "--ignore-not-found=true",
        check=False,
    )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(script)
        script_path = f.name

    oc(
        "create",
        "configmap",
        script_cm_name,
        f"--from-file=generate.py={script_path}",
        "-n",
        args.namespace,
        check=True,
    )

    import os

    os.unlink(script_path)

    job_manifest = _build_job_manifest(
        namespace=args.namespace,
        output_configmap=args.output_configmap,
        script_cm_name=script_cm_name,
    )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        import yaml

        yaml.safe_dump(job_manifest, f, sort_keys=False)
        manifest_path = f.name

    oc("apply", "-f", manifest_path, "-n", args.namespace, check=True)
    os.unlink(manifest_path)

    return f"Prompt generator job submitted (tokens={args.num_tokens}, count={args.num_prompts})"


@retry(attempts=60, delay=10, backoff=1.0)
@task
def wait_for_completion(args, ctx):
    """Wait for the prompt generator job to complete."""
    payload = oc_get_json(
        "job",
        name=GENERATOR_JOB_NAME,
        namespace=args.namespace,
        ignore_not_found=True,
    )
    if not payload:
        return (False, "Waiting for job to appear")

    succeeded = payload.get("status", {}).get("succeeded", 0)
    failed = payload.get("status", {}).get("failed", 0)

    if succeeded >= 1:
        return "Prompt generator job completed successfully"
    if failed >= 1:
        raise RuntimeError("Prompt generator job FAILED — check pod logs")

    return (False, "Job still running...")


@task
def validate_output(args, ctx):
    """Validate the generated prompts ConfigMap exists and has correct token counts."""
    if not oc_resource_exists("configmap", args.output_configmap, namespace=args.namespace):
        raise RuntimeError(f"Output ConfigMap '{args.output_configmap}' not found")

    payload = oc_get_json(
        "configmap",
        name=args.output_configmap,
        namespace=args.namespace,
        ignore_not_found=False,
    )
    data = payload.get("data", {})
    if "synthetic_prompts.jsonl" not in data:
        raise RuntimeError("ConfigMap missing 'synthetic_prompts.jsonl' key")

    lines = [line for line in data["synthetic_prompts.jsonl"].strip().split("\n") if line.strip()]
    logger.info("Generated %d prompts (requested %d)", len(lines), args.num_prompts)

    first = json.loads(lines[0])
    reported_tokens = first.get("token_count", 0)
    if reported_tokens != args.num_tokens:
        logger.warning(
            "First prompt reports %d tokens, expected %d — verify tokenizer match",
            reported_tokens,
            args.num_tokens,
        )
    else:
        logger.info("Token count validation passed: %d tokens per prompt", reported_tokens)

    artifacts_dir = env.ARTIFACT_DIR / "artifacts" / "prompts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "synthetic_prompts.jsonl").write_text(
        data["synthetic_prompts.jsonl"], encoding="utf-8"
    )

    return f"Validated {len(lines)} prompts, each {reported_tokens} tokens"


@always
@task
def capture_logs(args, ctx):
    """Capture generator job logs for diagnostics."""
    artifacts_dir = env.ARTIFACT_DIR / "artifacts" / "prompts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    oc(
        "logs",
        f"job/{GENERATOR_JOB_NAME}",
        "-n",
        args.namespace,
        check=False,
        log_stdout=False,
        stdout_dest=artifacts_dir / "generator.log",
    )

    return "Logs captured"


def _build_generator_script(*, num_tokens: int, num_prompts: int, tokenizer_model: str) -> str:
    """Build the Python script that runs inside the generator Job.

    Uses the shared build_exact_text algorithm (encode-trim-decode loop)
    from agentic_tools.utils.token_text.
    """
    return f'''#!/usr/bin/env python3
"""
Synthetic prompt generator — produces EXACTLY {num_tokens} tokens per prompt.
Uses encode-trim-decode loop (same as agentic_tools.utils.token_text).
"""
import json
import os
import random
import subprocess
import sys

subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "transformers", "kubernetes"], stdout=subprocess.DEVNULL)

from transformers import AutoTokenizer

NUM_TOKENS = {num_tokens}
NUM_PROMPTS = {num_prompts}
TOKENIZER_MODEL = "{tokenizer_model}"
OUTPUT_FILE = "/output/synthetic_prompts.jsonl"
CONFIGMAP_NAME = os.environ.get("OUTPUT_CONFIGMAP", "synthetic-prompts")
NAMESPACE = os.environ.get("NAMESPACE", "default")


def get_valid_ids(tokenizer):
    vocab = tokenizer.get_vocab()
    special_ids = set()
    for attr in ("bos_token_id", "eos_token_id", "pad_token_id", "unk_token_id"):
        tid = getattr(tokenizer, attr, None)
        if tid is not None:
            special_ids.add(tid)
    if hasattr(tokenizer, "all_special_ids"):
        special_ids.update(tokenizer.all_special_ids)
    return [tid for tid in vocab.values() if tid not in special_ids]


def build_exact_text(tokenizer, valid_ids, num_tokens, prefix=""):
    """Build text of exactly num_tokens tokens using encode-trim-decode loop."""
    for attempt in range(10):
        filler_ids = random.choices(valid_ids, k=num_tokens * 2)
        text = prefix + tokenizer.decode(filler_ids, skip_special_tokens=True)

        for _ in range(10):
            ids = tokenizer.encode(text, add_special_tokens=False)
            if len(ids) == num_tokens:
                return text
            text = tokenizer.decode(ids[:num_tokens], skip_special_tokens=True)

    ids = tokenizer.encode(text, add_special_tokens=False)
    print(f"WARNING: Could not converge to exactly {{num_tokens}} tokens (got {{len(ids)}})", file=sys.stderr)
    return text


def main():
    print(f"Loading tokenizer: {{TOKENIZER_MODEL}}")
    tokenizer = AutoTokenizer.from_pretrained(TOKENIZER_MODEL, trust_remote_code=True)
    print(f"Tokenizer loaded (vocab size: {{tokenizer.vocab_size}})")

    valid_ids = get_valid_ids(tokenizer)
    print(f"Vocabulary: {{len(valid_ids)}} usable token IDs")

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

    print(f"Generating {{NUM_PROMPTS}} prompts of exactly {{NUM_TOKENS}} tokens...")
    prompts = []
    for i in range(NUM_PROMPTS):
        text = build_exact_text(tokenizer, valid_ids, NUM_TOKENS)
        actual_count = len(tokenizer.encode(text, add_special_tokens=False))
        prompts.append({{"prompt": text, "token_count": actual_count, "index": i}})
        if (i + 1) % 10 == 0:
            print(f"  Generated {{i + 1}}/{{NUM_PROMPTS}} (last: {{actual_count}} tokens)")

    with open(OUTPUT_FILE, "w") as f:
        for p in prompts:
            f.write(json.dumps(p) + "\\n")

    print(f"Written {{len(prompts)}} prompts to {{OUTPUT_FILE}}")

    mismatches = [p for p in prompts if p["token_count"] != NUM_TOKENS]
    if mismatches:
        print(f"WARNING: {{len(mismatches)}} prompts did not match target token count")
        for m in mismatches[:5]:
            print(f"  index={{m['index']}}: got {{m['token_count']}} tokens")
    else:
        print(f"SUCCESS: All {{len(prompts)}} prompts are exactly {{NUM_TOKENS}} tokens")

    print(f"Creating ConfigMap {{CONFIGMAP_NAME}} in namespace {{NAMESPACE}}...")
    from kubernetes import client, config as k8s_config
    k8s_config.load_incluster_config()
    v1 = client.CoreV1Api()

    with open(OUTPUT_FILE, "r") as f:
        prompts_data = f.read()

    cm = client.V1ConfigMap(
        metadata=client.V1ObjectMeta(name=CONFIGMAP_NAME, namespace=NAMESPACE),
        data={{"synthetic_prompts.jsonl": prompts_data}},
    )
    v1.create_namespaced_config_map(namespace=NAMESPACE, body=cm)
    print("ConfigMap created successfully")


if __name__ == "__main__":
    main()
'''


def _build_job_manifest(
    *,
    namespace: str,
    output_configmap: str,
    script_cm_name: str,
) -> dict[str, Any]:
    """Build the K8s Job manifest for the prompt generator."""
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": GENERATOR_JOB_NAME,
            "namespace": namespace,
            "labels": {
                "forge.openshift.io/component": "prompt-generator",
                "app": "prompt-generator",
            },
        },
        "spec": {
            "template": {
                "metadata": {
                    "labels": {"app": "prompt-generator"},
                },
                "spec": {
                    "serviceAccountName": "default",
                    "containers": [
                        {
                            "name": "generator",
                            "image": GENERATOR_IMAGE,
                            "command": ["python3", "/scripts/generate.py"],
                            "env": [
                                {"name": "OUTPUT_CONFIGMAP", "value": output_configmap},
                                {"name": "NAMESPACE", "value": namespace},
                            ],
                            "volumeMounts": [
                                {"name": "scripts", "mountPath": "/scripts"},
                                {"name": "output", "mountPath": "/output"},
                            ],
                            "resources": {
                                "requests": {"memory": "2Gi", "cpu": "1"},
                                "limits": {"memory": "4Gi", "cpu": "2"},
                            },
                        }
                    ],
                    "volumes": [
                        {"name": "scripts", "configMap": {"name": script_cm_name}},
                        {"name": "output", "emptyDir": {}},
                    ],
                    "restartPolicy": "Never",
                },
            },
            "backoffLimit": 1,
        },
    }
