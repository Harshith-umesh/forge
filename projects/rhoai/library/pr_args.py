#!/usr/bin/env python3
"""
RHOAI PR directive handlers.

Provides directive handlers for RHOAI-specific PR trigger directives.
These handlers are consumed by project-level pr_args modules (e.g., llm_d).

Supported syntax::

    /rhoai.rc-image quay.io/rhoai/rhoai-fbc-fragment@sha256:abc123 [CHANNEL]
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def handle_rhoai_rc_image_directive(line: str) -> dict[str, Any]:
    """
    Handle /rhoai.rc-image directive for setting RHOAI RC catalog image.

    Format: /rhoai.rc-image IMAGE [CHANNEL]

    CHANNEL is optional and defaults to "beta" if not specified.

    Args:
        line: The directive line

    Returns:
        Dictionary with custom catalog and operator channel configuration

    Raises:
        ValueError: If image is empty
    """
    parts = line.removeprefix("/rhoai.rc-image").strip().split()

    if not parts or not parts[0]:
        raise ValueError(
            "Invalid /rhoai.rc-image directive: image cannot be empty. "
            "Example: /rhoai.rc-image quay.io/rhoai/rhoai-fbc-fragment@sha256:abc123 [beta]"
        )

    image = parts[0]
    channel = parts[1] if len(parts) > 1 else "beta"

    return {
        "platform.rhoai.custom_catalog.enabled": True,
        "platform.rhoai.custom_catalog.image": image,
        "platform.operators.rhods-operator.channel": channel,
    }


def get_rhoai_directive_handlers() -> dict[str, callable]:
    """
    Get a mapping of RHOAI directive prefixes to their handler functions.

    Returns:
        Dictionary mapping directive prefixes to handler functions
    """
    return {
        "/rhoai.rc-image": handle_rhoai_rc_image_directive,
    }


def get_supported_rhoai_directives() -> dict[str, str]:
    """
    Get a dictionary of supported RHOAI directives and their descriptions.

    Returns:
        Dictionary mapping directive names to detailed descriptions
    """
    return {
        "/rhoai.rc-image": """Set RHOAI RC catalog image for testing release candidates.
                      Format: /rhoai.rc-image IMAGE [CHANNEL]
                      CHANNEL is optional and defaults to beta if not specified.
                      Example: /rhoai.rc-image quay.io/rhoai/rhoai-fbc-fragment@sha256:abc123 stable
                      Effect: Enables custom catalog with the given image and sets operator channel.""",
    }
