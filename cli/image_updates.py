# cli/image_updates.py
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .loader import load_yaml_file

DOCKER_HUB_API = "https://hub.docker.com/v2/repositories"
SEMVER_PATTERN = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+].*)?$")


def _read_max_pages() -> int:
    raw = os.getenv("CDS_DOCKERHUB_MAX_PAGES", "3").strip()
    try:
        value = int(raw)
    except ValueError:
        return 3
    return value if value > 0 else 3

def collect_module_images(module_root: Path) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    for module_file in sorted(module_root.rglob("module.yaml")):
        module_def, diags = load_yaml_file(module_file)
        if module_def is None:
            continue

        module_name = str(module_file.parent.relative_to(module_root))
        compose = module_def.get("spec", {}).get("implementation", {}).get("compose", {})
        module_images = find_images_in_compose(compose, module_dir=module_file.parent)
        # ^^^ pass module_dir so build contexts can be resolved

        for service_name, image, dockerfile in module_images:
            entry = {"module": module_name, "service": service_name, "image": image}
            if dockerfile:
                entry["dockerfile"] = str(dockerfile)
            images.append(entry)

    return images

def find_images_in_compose(
    compose: Any,
    service_name: str | None = None,
    module_dir: Path | None = None,
) -> list[tuple[str, str, Path | None]]:
    images: list[tuple[str, str, Path | None]] = []

    if isinstance(compose, dict):
        if "image" in compose and isinstance(compose["image"], str):
            dockerfile: Path | None = None

            if "build" in compose and module_dir is not None:
                build = compose["build"]
                if isinstance(build, str):
                    # build: ./path  (shorthand)
                    context = module_dir / build
                    candidate = context / "Dockerfile"
                    dockerfile = candidate if candidate.is_file() else None
                elif isinstance(build, dict):
                    context_str = build.get("context", ".")
                    df_name = build.get("dockerfile", "Dockerfile")
                    context = module_dir / context_str
                    candidate = context / df_name
                    dockerfile = candidate if candidate.is_file() else None

            images.append((service_name or "<root>", compose["image"], dockerfile))

        for key, value in compose.items():
            if key == "services" and isinstance(value, dict):
                for svc_name, svc_def in value.items():
                    images.extend(
                        find_images_in_compose(svc_def, service_name=svc_name, module_dir=module_dir)
                    )
            elif key != "build":  # don't recurse into build blocks
                images.extend(
                    find_images_in_compose(value, service_name=service_name, module_dir=module_dir)
                )

    elif isinstance(compose, list):
        for item in compose:
            images.extend(find_images_in_compose(item, service_name=service_name, module_dir=module_dir))

    return images

_ARG_REF = re.compile(r"\$\{?\w+\}?")

def extract_base_image(dockerfile: Path, *, final_stage: bool = True) -> str | None:
    """
    Return the base image from a Dockerfile's FROM instruction.

    Args:
        dockerfile:  Path to the Dockerfile.
        final_stage: If True (default), return the last FROM line's image
                     (the runtime stage). If False, return the first.

    Returns:
        The image reference string, or None if no suitable FROM is found.
    """
    try:
        lines = dockerfile.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    from_images: list[str] = []

    for line in lines:
        stripped = line.strip()
        # Case-insensitive match; guard against lines with no tokens after FROM
        tokens = stripped.split()
        if not tokens or tokens[0].upper() != "FROM":
            continue
        if len(tokens) < 2:
            continue  # malformed: bare FROM with no image

        image = tokens[1]

        if image.upper() == "SCRATCH":
            continue  # scratch has no base to check

        if _ARG_REF.search(image):
            continue  # skip ARG-substituted references we can't resolve statically

        from_images.append(image)

    if not from_images:
        return None

    return from_images[-1] if final_stage else from_images[0]

def parse_image_reference(image: str) -> dict[str, str | None]:
    ref = image.split("@", 1)[0]
    tag = "latest"
    # Fix: only require ":" to be present, not necessarily "/"
    if ":" in ref:
        potential_tag = ref.rsplit(":", 1)[1]
        if "/" not in potential_tag:  # colon is a tag separator, not a port
            ref, tag = ref.rsplit(":", 1)

    parts = ref.split("/")
    if len(parts) == 1:
        registry = "docker.io"
        namespace = "library"
        repository = parts[0]
    elif len(parts) == 2 and "." not in parts[0] and ":" not in parts[0]:
        registry = "docker.io"
        namespace, repository = parts
    else:
        registry = parts[0]
        namespace = parts[1]
        repository = parts[2] if len(parts) >= 3 else ""

    return {
        "registry": registry,
        "namespace": namespace,
        "repository": repository,
        "tag": tag,
    }


def is_docker_hub_image(image: str) -> bool:
    info = parse_image_reference(image)
    return info["registry"] in {"docker.io", "registry-1.docker.io"}


def is_local_image(image: str) -> bool:
    return image.endswith(":custom") or parse_image_reference(image)["registry"] != "docker.io"


def normalize_semver(tag: str) -> str | None:
    clean = tag.split("@", 1)[0].split("-")[0].split("+")[0]
    match = SEMVER_PATTERN.match(clean)
    if not match:
        return None
    major, minor, patch = match.groups()
    components = [major or "0", minor or "0", patch or "0"]
    return ".".join(components)


def semver_key(tag: str) -> tuple[int, int, int] | None:
    normalized = normalize_semver(tag)
    if normalized is None:
        return None
    parts = normalized.split(".")
    return tuple(int(part) for part in parts)


def fetch_dockerhub_tags(namespace: str, repository: str, page_size: int = 100, max_pages: int | None = None) -> list[str] | None:
    tags: list[str] = []
    url = f"{DOCKER_HUB_API}/{namespace}/{repository}/tags?page_size={page_size}"
    pages_read = 0
    page_limit = _read_max_pages() if max_pages is None else max_pages

    while url and pages_read < page_limit:
        try:
            req = Request(url, headers={"User-Agent": "cds-image-check/1.0"})
            with urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
        except (HTTPError, URLError, ValueError):
            return None

        pages_read += 1

        for result in data.get("results", []):
            name = result.get("name")
            if isinstance(name, str):
                tags.append(name)

        url = data.get("next")
        if not url:
            break

    return tags


def find_newer_tag(current_tag: str, tags: list[str]) -> str | None:
    current_semver = semver_key(current_tag)
    if current_semver is None:
        return None

    current_parts = current_tag.split("-")[0].split("+")[0].split(".")
    current_len = len(current_parts)
    candidate_tags: dict[tuple[int, int, int], str] = {}

    for tag in tags:
        candidate_semver = semver_key(tag)
        if candidate_semver is None:
            continue

        if current_len == 1:
            if candidate_semver[0] != current_semver[0]:
                continue
        elif current_len == 2:
            if candidate_semver[:2] != current_semver[:2]:
                continue
        else:
            if candidate_semver[:2] != current_semver[:2]:
                continue

        candidate_tags[candidate_semver] = tag

    if not candidate_tags:
        return None

    latest = max(candidate_tags)
    if latest > current_semver:
        return candidate_tags[latest]
    return None

def check_image_update(image: str, dockerfile: Path | str | None = None) -> dict[str, Any]:
    info = parse_image_reference(image)
    if is_local_image(image):
        if dockerfile is None:
            return {"image": image, "status": "local", "latest": None}
        # Resolve FROM line and recurse on the base image
        base_image = extract_base_image(Path(dockerfile))
        if base_image is None:
            return {"image": image, "status": "local-no-base", "latest": None}
        result = check_image_update(base_image)
        return {**result, "image": image, "base_image": base_image}

    if not is_docker_hub_image(image):
        return {"image": image, "status": "unsupported-registry", "latest": None}

    namespace = info["namespace"]
    repository = info["repository"]
    if not repository:
        return {"image": image, "status": "invalid", "latest": None}

    tags = fetch_dockerhub_tags(namespace, repository)
    if tags is None:
        return {"image": image, "status": "lookup-failed", "latest": None}

    latest = find_newer_tag(info["tag"], tags)
    if latest:
        return {"image": image, "status": "update-available", "latest": latest}
    return {"image": image, "status": "up-to-date", "latest": None}
