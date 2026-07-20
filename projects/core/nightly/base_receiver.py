"""Abstract interface for image receivers.

Projects implement this to define how the latest available version
is fetched from a container registry (GHCR, Quay, etc.).
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 10


class ImageReceiver(ABC):
    """Fetches the latest available version from a container registry."""

    @abstractmethod
    def _fetch_version(self) -> str:
        """Query the registry and return the latest version string.

        Raises on failure (network, auth, no tags found, etc.).
        """
        ...

    def get_latest_version(self) -> str:
        """Call _fetch_version with retry on transient failures."""
        last_error = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return self._fetch_version()
            except Exception as e:
                last_error = e
                if attempt < MAX_ATTEMPTS:
                    logger.warning(
                        "Receiver attempt %d/%d failed: %s. Retrying in %ds...",
                        attempt, MAX_ATTEMPTS, e, RETRY_DELAY_SECONDS,
                    )
                    time.sleep(RETRY_DELAY_SECONDS)
        raise RuntimeError(
            f"Receiver failed after {MAX_ATTEMPTS} attempts"
        ) from last_error
