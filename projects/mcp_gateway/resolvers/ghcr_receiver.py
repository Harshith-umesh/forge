"""GHCR image receiver for mcp_gateway.

Resolves the latest mcp-gateway commit SHA that has a published image
on ghcr.io. Checks recent commits from the repo and finds the first
one with a corresponding container image tag (sha-<commit>).

No authentication needed for public packages (anonymous token).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from projects.core.nightly.base_receiver import ImageReceiver

REPO = "Kuadrant/mcp-gateway"
IMAGE = "kuadrant/mcp-gateway"
COMMITS_URL = f"https://api.github.com/repos/{REPO}/commits?per_page=20"
TOKEN_URL = f"https://ghcr.io/token?service=ghcr.io&scope=repository:{IMAGE}:pull"
MANIFEST_URL = f"https://ghcr.io/v2/{IMAGE}/manifests/"


class GHCRReceiver(ImageReceiver):
    """Find the latest mcp-gateway commit with a published ghcr.io image."""

    NAME = "ghcr"

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
        with urllib.request.urlopen(TOKEN_URL, timeout=30) as resp:
            data = json.loads(resp.read())
        return data["token"]

    def _get_recent_commits(self) -> list[str]:
        req = urllib.request.Request(COMMITS_URL)
        with urllib.request.urlopen(req, timeout=30) as resp:
            commits = json.loads(resp.read())
        return [c["sha"] for c in commits]

    def _image_exists(self, sha: str, token: str) -> bool:
        tag = f"sha-{sha}"
        req = urllib.request.Request(
            MANIFEST_URL + tag,
            headers={
                "Accept": "application/vnd.oci.image.index.v1+json, "
                          "application/vnd.docker.distribution.manifest.v2+json",
                "Authorization": f"Bearer {token}",
            },
        )
        try:
            urllib.request.urlopen(req, timeout=30)
            return True
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False
            raise
