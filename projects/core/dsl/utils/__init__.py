"""
Utility modules for DSL framework
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml


def write_json(path: Path, payload: Any) -> None:
    """Write JSON data to a file with proper formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{json.dumps(payload, indent=2, sort_keys=True)}\n", encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    """Write text content to a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_yaml(path: Path, payload: Any) -> None:
    """Write YAML data to a file with proper formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def slugify_identifier(value: str, *, max_length: int = 63) -> str:
    """Convert a string to a valid Kubernetes identifier with optional max length."""
    slug = re.sub(r"[^a-z0-9-]+", "-", value.lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug[:max_length].rstrip("-") or "item"


def truncate_k8s_name(value: str, *, max_length: int = 63) -> str:
    """Truncate a string to Kubernetes name length limits.

    When truncation occurs, a short hash suffix is appended to avoid
    collisions between names that share a long common prefix.
    """
    if len(value) <= max_length:
        return value
    hash_suffix = hashlib.sha256(value.encode()).hexdigest()[:6]
    prefix = value[: max_length - len(hash_suffix) - 1].rstrip("-")
    return f"{prefix}-{hash_suffix}"
