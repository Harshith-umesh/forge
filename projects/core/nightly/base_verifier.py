"""Abstract interface for nightly verifiers.

Projects implement this to define how run history is checked
(S3, MLflow, etc.) to determine if a version was already tested.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 10


class NightlyVerifier(ABC):
    """Checks historical run data to determine if a version was already tested."""

    @abstractmethod
    def _fetch_last_version(self) -> str:
        """Return the version string of the last successful run.

        Returns empty string if no previous run exists.
        """
        ...

    def get_last_tested_version(self) -> str:
        """Call _fetch_last_version with retry on transient failures."""
        last_error = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return self._fetch_last_version()
            except Exception as e:
                last_error = e
                if attempt < MAX_ATTEMPTS:
                    logger.warning(
                        "Verifier attempt %d/%d failed: %s. Retrying in %ds...",
                        attempt, MAX_ATTEMPTS, e, RETRY_DELAY_SECONDS,
                    )
                    time.sleep(RETRY_DELAY_SECONDS)
        raise RuntimeError(
            f"Verifier failed after {MAX_ATTEMPTS} attempts"
        ) from last_error
