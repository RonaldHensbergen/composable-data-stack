# cli/image_digest_check.py
"""
Pinned image digest staleness check (issue #736).

Modules pin third-party base images by digest (e.g.
`postgres:18@sha256:...`) so deploys are reproducible. When the upstream
registry republishes the same tag with a new digest (a patched rebuild),
that pin silently goes stale until someone notices out-of-band. This module
adds an opt-in, network-gated check that compares a module's pinned digest
against the digest the registry currently serves for that tag, and reports
a stable warning diagnostic (W100) when they differ.

The check is off by default so `cds validate`/`cds up` stay usable offline
and in CI without network access; it is enabled explicitly via
`--check-image-digests` or the CDS_CHECK_IMAGE_DIGESTS=1 environment
variable. Any lookup failure (offline, auth failure, unknown tag, ...) is
reported as "skipped", never as an error, per the issue's acceptance
criteria.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .image_updates import find_images_in_compose, parse_image_reference

ENV_VAR = "CDS_CHECK_IMAGE_DIGESTS"
_TIMEOUT = 10
_MANIFEST_ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)
_USER_AGENT = "cds-digest-check/1.0"

_PINNED_DIGEST_PATTERN = re.compile(r"^([^@]+)@(sha256:[a-f0-9]{64})$")
_AUTH_PARAM_PATTERN = re.compile(r'(\w+)="([^"]*)"')


def is_enabled(explicit: bool | None = None) -> bool:
    """
    Resolve whether the staleness check should run: an explicit CLI flag
    wins, otherwise CDS_CHECK_IMAGE_DIGESTS=1/true/yes/on opts in. Off by
    default so validate/up/render stay network-free.
    """
    if explicit:
        return True
    return os.getenv(ENV_VAR, "").strip().lower() in ("1", "true", "yes", "on")


def parse_pinned_reference(image: str) -> tuple[str, str] | None:
    """Split "repo:tag@sha256:..." into ("repo:tag", "sha256:...")."""
    match = _PINNED_DIGEST_PATTERN.match(image)
    if not match:
        return None
    return match.group(1), match.group(2)


def _registry_base_url(registry: str) -> str:
    if registry == "docker.io":
        return "https://registry-1.docker.io"
    return f"https://{registry}"


def _fetch_bearer_token(www_authenticate: str) -> str | None:
    """Follow the OAuth2 token challenge described by a 401 response's
    WWW-Authenticate header (realm/service/scope), per the Docker Registry
    v2 auth spec."""
    if not www_authenticate.lower().startswith("bearer"):
        return None
    params = dict(_AUTH_PARAM_PATTERN.findall(www_authenticate))
    realm = params.get("realm")
    if not realm:
        return None
    query = "&".join(f"{key}={value}" for key, value in params.items() if key != "realm")
    url = f"{realm}?{query}" if query else realm
    if not url.startswith(("http://", "https://")):
        return None
    try:
        req = Request(url, headers={"User-Agent": _USER_AGENT})  # nosec B310  # noqa: S310
        # This check is opt-in and only ever targets registries already
        # referenced by a module's own pinned image (not arbitrary
        # user/CLI input). The scheme is restricted to http(s) above, but
        # the realm host itself is not otherwise restricted: a
        # compromised/malicious registry could point "realm" at an
        # internal address. The request carries no credentials and only
        # a bearer token string is read back from the (JSON-parsed)
        # response, bounding the impact to an anonymous GET against a
        # registry-controlled URL rather than to full SSRF.
        with urlopen(req, timeout=_TIMEOUT) as response:  # nosec B310  # noqa: S310
            data = json.loads(response.read().decode())
    except (HTTPError, URLError, ValueError):
        return None
    token = data.get("token") or data.get("access_token")
    return token if isinstance(token, str) else None


def fetch_registry_digest(registry: str, repository: str, tag: str) -> str | None:
    """
    Return the manifest digest the registry currently serves for
    repository:tag, or None if it cannot be determined (offline, auth
    failure, unknown tag, unsupported registry response, ...).
    """
    base = _registry_base_url(registry)
    url = f"{base}/v2/{repository}/manifests/{tag}"
    headers = {"Accept": _MANIFEST_ACCEPT, "User-Agent": _USER_AGENT}

    for attempt in range(2):
        try:
            req = Request(url, headers=headers, method="HEAD")  # nosec B310  # noqa: S310
            # url is built from a schema-validated registry/repository/tag
            # triple, always http(s).
            with urlopen(req, timeout=_TIMEOUT) as response:  # nosec B310  # noqa: S310
                digest = response.headers.get("Docker-Content-Digest")
                return digest if isinstance(digest, str) and digest else None
        except HTTPError as exc:
            if exc.code == 401 and attempt == 0:
                www_auth = exc.headers.get("WWW-Authenticate", "") if exc.headers else ""
                token = _fetch_bearer_token(www_auth)
                if token:
                    headers["Authorization"] = "Bearer " + token
                    continue
            return None
        except URLError:
            return None
    return None


def check_digest_staleness(image: str) -> dict[str, Any]:
    """
    Compare a pinned "repo:tag@sha256:..." reference against the digest the
    registry currently publishes for that same tag.

    Returns a dict with "image", "status" (one of "not-pinned",
    "lookup-failed", "up-to-date", "stale"), and "latest_digest".
    """
    parsed = parse_pinned_reference(image)
    if parsed is None:
        return {"image": image, "status": "not-pinned", "latest_digest": None}

    tag_ref, pinned_digest = parsed
    info = parse_image_reference(tag_ref)
    repository = info["repository"]
    if info["namespace"]:
        repository = f"{info['namespace']}/{repository}"
    if not repository or not info["registry"]:
        return {"image": image, "status": "lookup-failed", "latest_digest": None}

    latest_digest = fetch_registry_digest(info["registry"], repository, info["tag"])
    if latest_digest is None:
        return {"image": image, "status": "lookup-failed", "latest_digest": None}
    if latest_digest == pinned_digest:
        return {"image": image, "status": "up-to-date", "latest_digest": latest_digest}
    return {"image": image, "status": "stale", "latest_digest": latest_digest}


def collect_pinned_images_for_module(module_def: dict[str, Any]) -> list[dict[str, str]]:
    """Return {service, image} entries for a module's digest-pinned images."""
    compose = module_def.get("spec", {}).get("implementation", {}).get("compose", {})
    entries: list[dict[str, str]] = []
    for service_name, image, _dockerfile in find_images_in_compose(compose):
        if parse_pinned_reference(image) is not None:
            entries.append({"service": service_name, "image": image})
    return entries
