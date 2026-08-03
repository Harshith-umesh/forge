"""Abstract interface for image receivers.

Projects implement this to define how the latest available version
is fetched from a container registry (GHCR, Quay, etc.).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class ImageReceiver(ABC):
    """Fetches the latest available version from a container registry."""

    @abstractmethod
    def get_latest_version(self) -> str:
        """Query the registry and return the latest version string.

        Raises on failure (network, auth, no tags found, etc.).
        """
        ...
