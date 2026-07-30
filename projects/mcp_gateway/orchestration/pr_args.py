#!/usr/bin/env python3
"""
MCP Gateway Project PR Arguments Parser

Parses mcp_gateway-specific directives from PR trigger comments.

Supported syntax::

    /test fournos mcp_gateway smoke
    /test fournos mcp_gateway smoke collect_cluster_info
    /version 0.7.0
    /pipeline nightly ghcr
    /pipeline nightly quay
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _parse_test_line(line: str) -> list[str] | None:
    """Return trailing mcp_gateway test args for a matching /test fournos line."""
    if not line.startswith("/test "):
        return None

    parts = line[6:].strip().split()
    if len(parts) < 2:
        return None

    test_name, project_name, *args = parts
    if test_name != "fournos" or project_name != "mcp_gateway":
        return None

    return args


def parse_project_directives(comment_text: str) -> tuple[dict[str, Any], list[str]]:
    """
    Parse mcp_gateway-specific directives from PR trigger comments.

    Args:
        comment_text: Text from PR trigger comment

    Returns:
        Tuple of (configuration overrides dict, list of parsed directive lines)
    """
    config_overrides: dict[str, Any] = {}
    parsed_directives: list[str] = []

    for raw_line in comment_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        args = _parse_test_line(line)
        if args is not None:
            if args:
                parsed_directives.append(line)
                logger.info("Parsed mcp_gateway presets: %s", args)
            continue

        # Parse /version directive
        if line == "/version" or line.startswith("/version "):
            version = line.removeprefix("/version").strip()
            if not version:
                raise ValueError(
                    "Invalid /version directive: version cannot be empty. Example: /version 0.7.0"
                )
            parts = version.split()
            if len(parts) > 1:
                raise ValueError(
                    f"Invalid /version directive: expected a single version, "
                    f"got '{version}'. Example: /version 0.7.0"
                )
            config_overrides["infrastructure.mcp_gateway_version"] = parts[0]
            parsed_directives.append(line)
            logger.info("Parsed mcp_gateway version: %s", parts[0])

        # Parse /pipeline nightly <source> — extract source from pipeline directive
        # The directive line itself is already captured by the FOURNOS /pipeline handler,
        # so only extract the config overrides here to avoid duplicating it in notifications.
        if line.startswith("/pipeline nightly "):
            parts = line.split()
            if len(parts) == 3:
                source = parts[2]
                config_overrides["nightly.source"] = source
                config_overrides["fournos.job.pipeline_name"] = "nightly"
                config_overrides["caliper.export.backend.mlflow.config.experiment"] = (
                    "nightly-pipeline-experiment"
                )
                config_overrides["caliper.export.backend.mlflow.config.run_naming.prefix"] = (
                    "mcp-gw-nightly"
                )
                config_overrides["caliper.export.backend.mlflow.config.run_naming.single_run"] = (
                    "{prefix}-{timestamp}-{outcome}"
                )
                logger.info("Parsed nightly source from pipeline directive: %s", source)

    return config_overrides, parsed_directives
