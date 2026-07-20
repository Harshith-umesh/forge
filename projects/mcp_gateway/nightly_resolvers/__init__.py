"""MCP Gateway nightly resolvers.

Auto-discovers ImageReceiver and NightlyVerifier subclasses in this package.
To add a new receiver or verifier, just create a new file with a class that:
  - Inherits from ImageReceiver or NightlyVerifier
  - Defines a NAME class attribute (used for selection)

No modifications to this file are needed.
"""

from __future__ import annotations

import importlib
import os
import pkgutil

from projects.core.nightly.base_receiver import ImageReceiver
from projects.core.nightly.base_verifier import NightlyVerifier

_receivers: dict[str, type[ImageReceiver]] = {}
_verifiers: dict[str, type[NightlyVerifier]] = {}


def _discover():
    """Import all modules in this package and register subclasses."""
    if _receivers or _verifiers:
        return

    for _finder, module_name, _ in pkgutil.iter_modules(__path__):
        module = importlib.import_module(f"{__name__}.{module_name}")
        for attr in vars(module).values():
            if not isinstance(attr, type):
                continue
            if issubclass(attr, ImageReceiver) and attr is not ImageReceiver:
                name = getattr(attr, "NAME", None)
                if name:
                    _receivers[name] = attr
            if issubclass(attr, NightlyVerifier) and attr is not NightlyVerifier:
                name = getattr(attr, "NAME", None)
                if name:
                    _verifiers[name] = attr


def get_receiver() -> ImageReceiver:
    """Return the image receiver based on NIGHTLY_SOURCE env var."""
    _discover()
    source = os.environ.get("NIGHTLY_SOURCE", "ghcr")

    cls = _receivers.get(source)
    if cls is None:
        raise ValueError(
            f"Unknown source '{source}' for mcp_gateway. Available: {list(_receivers.keys())}"
        )
    return cls()


def get_verifier() -> NightlyVerifier:
    """Return the nightly verifier (first discovered NightlyVerifier subclass)."""
    _discover()

    if not _verifiers:
        raise RuntimeError("No NightlyVerifier subclass found in resolvers package")

    cls = next(iter(_verifiers.values()))
    return cls()
