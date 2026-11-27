#!/usr/bin/env python3
"""Minimal Forgejo REST client shared by the CI scripts in this repo.

On GitHub the scripts shell out to the `gh` CLI; on Codeberg (Forgejo) there
is no GitHub API, so when FORGEJO_API_URL is set they use this module instead
(see docs/ci-codeberg.md). Reads:

- FORGEJO_API_URL      e.g. https://codeberg.org/api/v1 (set => Forgejo mode)
- FORGEJO_REPOSITORY   owner/repo (falls back to GITHUB_REPOSITORY)
- FORGEJO_TOKEN        access token; optional — the repo is public, so
                       anonymous reads work for the read-only checks
"""

from __future__ import annotations

import json
import os
import urllib.request

DEFAULT_API_URL = "https://codeberg.org/api/v1"


def enabled() -> bool:
    """True when running against Forgejo (Codeberg CI) rather than GitHub."""
    return bool(os.environ.get("FORGEJO_API_URL"))


def api_url() -> str:
    return os.environ.get("FORGEJO_API_URL", DEFAULT_API_URL).rstrip("/")


def repo() -> str:
    return os.environ.get("FORGEJO_REPOSITORY") or os.environ.get(
        "GITHUB_REPOSITORY", ""
    )


def get(path: str) -> dict | list:
    """GET <api_url>/<path> and return the decoded JSON (dict or list)."""
    req = urllib.request.Request(  # noqa: S310 - fixed https host from FORGEJO_API_URL
        f"{api_url()}/{path}", headers={"Accept": "application/json"}
    )
    token = os.environ.get("FORGEJO_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        req.add_header("Authorization", f"token {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))
