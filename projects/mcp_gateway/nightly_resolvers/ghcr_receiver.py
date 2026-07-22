"""GHCR image receiver for mcp_gateway.

Resolves the latest commit SHA that has a published image on ghcr.io.
Checks recent commits from the configured repo and finds the first
one with a corresponding container image tag (sha-<commit>).

No authentication needed for public packages (anonymous token).

Configuration (in config.yaml under nightly.sources.ghcr):
  repo:  GitHub repo in "owner/name" format (e.g. "Kuadrant/mcp-gateway")
  image: GHCR image path without registry (e.g. "kuadrant/mcp-gateway")
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from projects.core.nightly.base_receiver import ImageReceiver

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 10


class GHCRReceiver(ImageReceiver):
    """Find the latest commit with a published ghcr.io image."""

    NAME = "ghcr"

    def __init__(self):
        from projects.core.library import config

        source_cfg = config.project.get_config("nightly.sources.ghcr")
        self.repo = source_cfg["repo"]
        self.image = source_cfg["image"]

        self._commits_url = f"https://api.github.com/repos/{self.repo}/commits?per_page=20"
        self._token_url = (
            f"https://ghcr.io/token?service=ghcr.io&scope=repository:{self.image}:pull"
        )
        self._manifest_url = f"https://ghcr.io/v2/{self.image}/manifests/"

    def get_latest_version(self) -> str:
        last_error = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return self._fetch_version()
            except urllib.error.URLError as e:
                last_error = e
                if attempt < MAX_ATTEMPTS:
                    logger.warning(
                        "GHCR attempt %d/%d failed: %s. Retrying in %ds...",
                        attempt,
                        MAX_ATTEMPTS,
                        e,
                        RETRY_DELAY_SECONDS,
                    )
                    time.sleep(RETRY_DELAY_SECONDS)
        raise RuntimeError(f"GHCR receiver failed after {MAX_ATTEMPTS} attempts") from last_error

    def _fetch_version(self) -> str:
        token = self._get_ghcr_token()
        shas = self._get_recent_commits()

        for sha in shas:
            if self._image_exists(sha, token):
                return sha

        raise RuntimeError(
            f"No commit found with a published image on ghcr.io "
            f"(checked {len(shas)} recent commits)"
        )

    def _get_ghcr_token(self) -> str:
        with urllib.request.urlopen(self._token_url, timeout=30) as resp:
            data = json.loads(resp.read())
        return data["token"]

    def _get_recent_commits(self) -> list[str]:
        req = urllib.request.Request(self._commits_url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            commits = json.loads(resp.read())
        return [c["sha"] for c in commits]

    def _image_exists(self, sha: str, token: str) -> bool:
        tag = f"sha-{sha}"
        req = urllib.request.Request(
            self._manifest_url + tag,
            headers={
                "Accept": "application/vnd.oci.image.index.v1+json, "
                "application/vnd.docker.distribution.manifest.v2+json",
                "Authorization": f"Bearer {token}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30):
                return True
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False
            raise
