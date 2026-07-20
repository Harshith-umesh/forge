"""Abstract interface for nightly verifiers.

Projects implement this to define how run history is checked
(S3, MLflow, etc.) to determine if a version was already tested.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class NightlyVerifier(ABC):
    """Checks historical run data to determine if a version was already tested."""

    @abstractmethod
    def get_last_tested_version(self) -> str:
        """Return the version string of the last successful run.

        Returns empty string if no previous run exists.
        """
        ...
