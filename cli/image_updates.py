# cli/image_updates.py
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .loader import load_yaml_file

DOCKER_HUB_API = "https://hub.docker.com/v2/repositories"
SEMVER_PATTERN = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+].*)?$")


def collect_module_images(module_root: Path) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    for module_file in sorted(module_root.rglob("module.yaml")):
        module_def, diags = load_yaml_file(module_file)
        if module_def is None:
            continue

        module_name = str(module_file.parent.relative_to(module_root))
        compose = module_def.get("spec", {}).get("implementation", {}).get("compose", {})
        module_images = find_images_in_compose(compose)

        for service_name, image in module_images:
            images.append(
                {
                    "module": module_name,
                    "service": service_name,
                    "image": image,
                }
            )

    return images


def find_images_in_compose(compose: Any, service_name: str | None = None) -> list[tuple[str, str]]:
    images: list[tuple[str, str]] = []

    if isinstance(compose, dict):
        if "image" in compose and isinstance(compose["image"], str):
            images.append((service_name or "<root>", compose["image"]))

        for key, value in compose.items():
            if key == "services" and isinstance(value, dict):
                for svc_name, svc_def in value.items():
                    images.extend(find_images_in_compose(svc_def, service_name=svc_name))
            else:
                images.extend(find_images_in_compose(value, service_name=service_name))

    elif isinstance(compose, list):
        for item in compose:
            images.extend(find_images_in_compose(item, service_name=service_name))

    return images


def parse_image_reference(image: str) -> dict[str, str | None]:
    ref = image.split("@", 1)[0]
    tag = "latest"
    if ":" in ref and "/" in ref and ref.rsplit(":", 1)[1].find("/") == -1:
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


def fetch_dockerhub_tags(namespace: str, repository: str, page_size: int = 100) -> list[str] | None:
    tags: list[str] = []
    url = f"{DOCKER_HUB_API}/{namespace}/{repository}/tags?page_size={page_size}"

    while url:
        try:
            req = Request(url, headers={"User-Agent": "cds-image-check/1.0"})
            with urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
        except (HTTPError, URLError, ValueError):
            return None

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


def check_image_update(image: str) -> dict[str, Any]:
    info = parse_image_reference(image)
    if is_local_image(image):
        return {"image": image, "status": "local", "latest": None}

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
