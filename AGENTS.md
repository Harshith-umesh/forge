# FORGE CI Framework — Agent Instructions

## Project Overview

FORGE is a CI/CD testing framework that orchestrates Kubernetes workloads on OpenShift clusters.
It uses a Python DSL to define tasks, apply manifests, and collect artifacts for debugging.

Key paths:
- `projects/` — individual project test harnesses
- `projects/NAME/orchestration` — upper layer of the test harness (config, prepare, test, cleanup)
- `projects/NAME/toolbox` — lower layer of the test harness (toolbox of scripts altering the state of the cluster, using the DSL language)
- `projects/NAME/test` — unit testing of the test harness
- `projects/core/dsl/` — shared DSL (task execution, k8s utilities, shell helpers)
- `fournos/gitops/` — Tekton pipelines and GitOps base workflows
- `docs/` — project documentation

## Security: Secret Handling (CRITICAL)

**NEVER write secrets or sensitive data to artifact directories, logs, or files.**

### Rules

1. **Never persist secrets to `ARTIFACT_DIR`.**
   The `oc_apply(path, manifest)` helper writes the manifest YAML to disk before applying it.
   If the manifest contains sensitive data (passwords, tokens, pull-secrets, certificates, API keys),
   it MUST NOT be written to any path under `env.ARTIFACT_DIR`.

2. **Use `oc_apply_secret()` or `oc("apply", "-f", "-", input_text=...)` for secrets.**
   Apply secrets via stdin or a dedicated helper that does not persist the payload.
   Never use the standard `oc_apply()` for Secret-kind manifests.

3. **Never log secret values.**
   Do not print, log, or include in error messages: passwords, tokens, bearer credentials,
   TLS keys, pull-secret `.dockerconfigjson`, or any `data:`/`stringData:` field from a Secret.

4. **Check the `kind:` field.**
   Before calling `oc_apply(artifact_path, manifest)`, verify that `manifest.get("kind") != "Secret"`.
   If it is a Secret, use a non-persisting apply path.

5. **Artifact directories are public.**
   Treat everything under `ARTIFACT_DIR` as if it will be published on the internet.
   Only write non-sensitive operational data (CRs, logs, pod descriptions, events).
   Pod/Deployment and other resources are safe to store, as they must not contain secrets. See point 9.
   Kubernetes Secret objects should never be stored in the artifacts. Kubernetes Secret descriptions can be safely stored.

7. **Use the vault module.**
    Use the project `projects.core.library.vault` module to access the secrets, stored in files. Consider all the content of these files as secret.
    
8. **Never pass secrets via environment variables**
    For transparency and post-mortem troubleshooting, the environment variables are saved to disk at multiple locations. Do not use environment variables to pass a secret between two components. There might be few exceptions to this rule (MLFlow, AWS), but they must be handled with care.

9. **Never pass secrets via Pod environment variables**
   The Pod definition will be stored in public artifacts. Do no use plain text Pod environment variables to pass secrets to the Pod. Instead, use Secret resources and Pod environment variables that reference this secret.

### Safe Patterns

```python
# GOOD: Apply a Secret without persisting to disk
def apply_secret(manifest: dict) -> None:
    """Apply a Secret manifest via stdin — nothing written to disk."""
    import yaml
    from projects.core.dsl.utils.k8s import oc

    oc("apply", "-f", "-", input_text=yaml.safe_dump(manifest, sort_keys=False))


# GOOD: Guard before using oc_apply
def safe_oc_apply(artifact_path, manifest):
    if manifest.get("kind") == "Secret":
        raise ValueError(
            f"Refusing to write Secret '{manifest['metadata']['name']}' to artifacts. "
            "Use apply_secret() instead."
        )
    oc_apply(artifact_path, manifest)
```

### Forbidden Patterns

```python
# BAD: Writing a pull-secret to artifacts
src_dir = env.ARTIFACT_DIR / "src"
oc_apply(src_dir / "image-pull-secret.yaml", secret_manifest)  # LEAKS CREDENTIALS

# BAD: Logging secret content
logger.info(f"Applying secret: {manifest}")  # LEAKS if manifest has sensitive data

# BAD: Including tokens in error messages
raise RuntimeError(f"Failed to auth with token {token}")  # LEAKS TOKEN
```
