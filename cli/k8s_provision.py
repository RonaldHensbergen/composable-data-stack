# cli/k8s_provision.py
"""Cluster and image provisioning for the Helm render target (issue #690).

`cds up --target compose` owns the whole lifecycle: it builds the profile's
local images and starts everything in one command. This module gives the Helm
target the same self-contained `up`, in three steps that run before
`helm_up()`:

1. `collect_local_builds()` / `build_local_images()` — the rendered chart
   references locally built images with `imagePullPolicy: Never`, so the
   images have to exist in the Docker daemon. The build set is derived from
   the plan exactly the way `cli/k8s_renderer.py` derives the containers it
   translates (production-gated compose services with a `build:` block), so
   what `cds up` builds is always what the chart references.
2. `ensure_k8s_cluster()` — verifies the target cluster is reachable, and can
   create it when the kube context names a k3d cluster (`k3d-<name>`).
3. `import_local_images()` — loads the built images into a k3d cluster with
   `k3d image import`, the cluster-side half of `imagePullPolicy: Never`.

Every subprocess invocation goes through `up_runner._validate_command()`
(allowlisted executable, no shell, no null bytes), and image references,
build-arg names, Dockerfile paths, and cluster names are validated against
allowlists before they reach a command line (CWE-88 argument injection).
"""

from __future__ import annotations

import re
import subprocess  # nosec B404
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from .k8s_runner import _validate_k8s_name, _validate_kube_context, _validate_timeout
from .renderer import (
    _build_context,
    _choose_best_context_candidate,
    _context_bases,
    _is_disabled,
    _looks_remote_context,
    _resolve_profile_dir,
    _resolve_project_root,
    _substitute_values,
    apply_production_gates,
)
from .up_runner import _validate_command, run_streamed

# Docker image reference: registry/repo:tag or repo@sha256:... form. The
# allowlist rejects anything that could be parsed as a flag (leading `-`)
# or smuggle extra arguments (whitespace/control chars).
_IMAGE_REF_RE = re.compile(r"[A-Za-z0-9@][A-Za-z0-9_./:@-]*")

# Docker build-arg names are environment-variable-shaped.
_BUILD_ARG_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# A Dockerfile path passed to `docker build -f` must not look like a flag.
_DOCKERFILE_RE = re.compile(r"[A-Za-z0-9_./@][A-Za-z0-9_./@-]*")

# A `${CDS_*}` placeholder left by renderer substitution for a secret
# reference. The direct `docker build` path refuses these in build fields
# (see _reject_secret_placeholders): a resolved build arg would expose the
# secret in the process argv.
_CDS_PLACEHOLDER_RE = re.compile(r"\$\{(CDS_[A-Z0-9_]+)\}")

_HEALTHZ_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class LocalImageBuild:
    """One locally built image the rendered chart references."""

    image: str
    context: str
    dockerfile: str | None = None
    args: dict[str, str] = field(default_factory=dict)


def _validate_image_ref(image: Any) -> str:
    if not isinstance(image, str):
        raise ValueError("service image is not a string")
    match = _IMAGE_REF_RE.fullmatch(image)
    if match is None:
        raise ValueError(f"invalid image reference: {image!r}")
    return match.group(0)


def _validate_dockerfile(dockerfile: Any) -> str:
    if not isinstance(dockerfile, str):
        raise ValueError("build.dockerfile is not a string")
    match = _DOCKERFILE_RE.fullmatch(dockerfile)
    if match is None:
        raise ValueError(f"invalid build.dockerfile: {dockerfile!r}")
    return match.group(0)


def _validate_build_args(args: Any) -> dict[str, str]:
    if not isinstance(args, dict):
        raise ValueError("build.args is not a mapping")
    validated: dict[str, str] = {}
    for key, value in args.items():
        if not isinstance(key, str) or _BUILD_ARG_NAME_RE.fullmatch(key) is None:
            raise ValueError(f"invalid build arg name: {key!r}")
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError(f"invalid build arg value for {key!r}")
        validated[key] = value
    return validated


def collect_local_builds(plan: dict[str, Any]) -> list[LocalImageBuild]:
    """
    Collect the locally built images the Helm chart for `plan` references.

    Mirrors `cli/k8s_renderer.render_helm()`: each module's compose services
    are production-gated (the Helm target always renders production variants),
    interpolated with the module's build context, and every service that still
    carries a `build:` block is exactly the set `_translate_container()` marks
    `imagePullPolicy: Never`. Services sharing an image (e.g. dagster's three
    containers) collapse into one build. Build contexts are resolved to
    absolute paths the same way `renderer._resolve_context_path()` resolves
    them, so `docker build` runs against the same directory compose would use.
    """
    secrets = plan.get("secrets", {})
    profile_dir = _resolve_profile_dir(plan)
    project_root = _resolve_project_root(profile_dir)
    compose_dir = profile_dir if profile_dir is not None else Path.cwd().resolve()

    builds: dict[str, LocalImageBuild] = {}
    for module in plan.get("modules", []):
        if not isinstance(module, dict):
            continue
        implementation = module.get("implementation") or {}
        if not isinstance(implementation, dict):
            continue
        compose_impl = implementation.get("compose") or {}
        raw_services = (
            compose_impl.get("services", {}) if isinstance(compose_impl, dict) else {}
        )
        # True: the Helm target renders production variants unconditionally
        # (see render_helm), so the build set must match those variants.
        services = apply_production_gates(deepcopy(raw_services), True)
        context = _build_context(module, secrets)
        for service_def in services.values():
            if not isinstance(service_def, dict):
                continue
            if _is_disabled(service_def.get("enabledFrom"), context):
                continue
            spec = _substitute_values(deepcopy(service_def), context)
            build = spec.get("build")
            if build is None:
                continue
            image = _validate_image_ref(spec.get("image"))
            if isinstance(build, str):
                build_context, dockerfile, args = build, None, {}
            elif isinstance(build, dict):
                raw_context = build.get("context", ".")
                build_context = raw_context if isinstance(raw_context, str) else "."
                raw_dockerfile = build.get("dockerfile")
                dockerfile = (
                    _validate_dockerfile(raw_dockerfile)
                    if raw_dockerfile is not None
                    else None
                )
                args = _validate_build_args(build.get("args", {}))
            else:
                raise ValueError(f"unsupported build block for image {image!r}")
            resolved = _resolve_build_context(
                build_context,
                dockerfile,
                module,
                profile_dir,
                project_root,
                compose_dir,
            )
            existing = builds.get(image)
            if existing is None:
                builds[image] = LocalImageBuild(
                    image=image, context=resolved, dockerfile=dockerfile, args=args
                )
            elif (existing.context, existing.dockerfile, existing.args) != (
                resolved,
                dockerfile,
                args,
            ):
                raise ValueError(
                    f"image {image!r} is built with conflicting build blocks; "
                    "give each service its own image tag"
                )
    return [builds[image] for image in sorted(builds)]


def _resolve_build_context(
    context: str,
    dockerfile: str | None,
    module: dict[str, Any],
    profile_dir: Path | None,
    project_root: Path | None,
    compose_dir: Path,
) -> str:
    """Resolve a compose `build.context` to a path `docker build` can use.

    Absolute paths and remote (git/URL) contexts pass through unchanged,
    matching `renderer._resolve_context_path()`; relative contexts are
    resolved against the same base list the compose renderer searches.
    """
    if Path(context).is_absolute() or _looks_remote_context(context):
        return context
    candidates = [
        (base / context).resolve()
        for base in _context_bases(module, profile_dir, project_root, compose_dir)
    ]
    if not candidates:
        return context
    chosen = _choose_best_context_candidate(candidates, dockerfile)
    return str(chosen)


def _reject_secret_placeholders(build: LocalImageBuild) -> None:
    """
    Fail closed when a build field still contains a `${CDS_*}` placeholder.

    On the Compose target, `docker compose build` expands secret placeholders
    server-side through the Docker API. The Helm path calls `docker build`
    directly, and a resolved `--build-arg KEY=VALUE` (or `-t`) would put the
    secret value in the process argv, where it is readable via `ps` by any
    local user while the build runs. No module uses secrets in build fields
    today; if one ever needs a build-time secret, that must go through
    BuildKit `--secret` mounts, not build args. Refusing here keeps the
    secret out of argv and fails with a legible message instead.
    """
    fields = [("image", build.image), ("build.dockerfile", build.dockerfile or "")]
    fields.extend(
        (f"build.args.{key}", value) for key, value in (build.args or {}).items()
    )
    for field_name, value in fields:
        if isinstance(value, str) and _CDS_PLACEHOLDER_RE.search(value):
            raise RuntimeError(
                f"{field_name} of image {build.image!r} contains a ${{CDS_*}} secret placeholder. "
                "The Helm target builds with a direct `docker build` call, where a resolved "
                "build arg would expose the secret in the process argv; use BuildKit "
                "--secret mounts or --image-source registry instead."
            )


def build_local_images(
    builds: list[LocalImageBuild],
    log_file: IO[str],
    *,
    use_color: bool = True,
) -> int:
    """
    Build each collected image with `docker build`, streaming output to
    `log_file` (and the terminal) exactly like the compose target's build
    step. `${CDS_*}` secret placeholders in build fields are refused up front
    (see _reject_secret_placeholders). Returns the first non-zero exit code,
    or 0 when every build succeeded.
    """
    commands: list[list[str]] = []
    for build in builds:
        _reject_secret_placeholders(build)
        command = ["docker", "build", "-t", build.image]
        if build.dockerfile:
            command.extend(["-f", build.dockerfile])
        for key, value in sorted((build.args or {}).items()):
            command.extend(["--build-arg", f"{key}={value}"])
        command.append(build.context)
        commands.append(command)

    for command in commands:
        print(f"Running: {' '.join(command)}")
        result = run_streamed(command, log_file, use_color=use_color)
        if result != 0:
            return result
    return 0


def k3d_cluster_name(kube_context: str | None) -> str | None:
    """
    Derive the k3d cluster name from a kube context: k3d names every cluster's
    context `k3d-<cluster>`, so `k3d-cds-local` maps to cluster `cds-local`.
    Returns None for non-k3d contexts.
    """
    if kube_context is None:
        return None
    prefix = "k3d-"
    if not kube_context.startswith(prefix) or len(kube_context) == len(prefix):
        return None
    # Cluster names must satisfy the same DNS-1123 rule as the context suffix;
    # validating here keeps a malformed context from reaching `k3d` as an
    # argument (CWE-88).
    return _validate_k8s_name(kube_context[len(prefix) :], "k3d cluster name")


def current_kube_context() -> str | None:
    """The active kube context when the caller passed no --kube-context."""
    try:
        result = subprocess.run(  # nosec B603  # noqa: S603
            _validate_command(["kubectl", "config", "current-context"]),
            capture_output=True,
            text=True,
            timeout=_HEALTHZ_TIMEOUT_SECONDS,
            shell=False,
        )
    except FileNotFoundError, subprocess.TimeoutExpired, OSError:
        return None
    if result.returncode != 0:
        return None
    context = result.stdout.strip()
    return context or None


def cluster_is_reachable(kube_context: str | None = None) -> bool:
    """
    True when the API server of `kube_context` (default: the current context)
    answers `kubectl get --raw /healthz`. A missing kubectl binary or a
    timeout counts as unreachable rather than crashing the caller.
    """
    kube_context = _validate_kube_context(kube_context)
    command = ["kubectl"]
    if kube_context:
        command.extend(["--context", kube_context])
    command.extend(["get", "--raw", "/healthz"])
    try:
        result = subprocess.run(  # nosec B603  # noqa: S603
            _validate_command(command),
            capture_output=True,
            text=True,
            timeout=_HEALTHZ_TIMEOUT_SECONDS,
            shell=False,
        )
    except FileNotFoundError, subprocess.TimeoutExpired, OSError:
        return False
    return result.returncode == 0


def ensure_k8s_cluster(
    kube_context: str | None = None,
    *,
    create: bool = False,
    log_file: IO[str] | None = None,
    timeout: float = 180.0,
    use_color: bool = True,
) -> int:
    """
    Ensure the target cluster is reachable before `helm upgrade --install`.

    Without `create`, an unreachable cluster is a hard error with a legible
    message (the cluster is external state `cds` must not guess at). With
    `create=True` and a k3d context (`k3d-<name>`), a missing cluster is
    created with `k3d cluster create <name> --wait` — traefik disabled, the
    same as the scripts/k8s harness — and the node is waited on. Returns 0
    when the cluster answers afterwards, non-zero otherwise.
    """
    kube_context = _validate_kube_context(kube_context)
    # Validate up front, before any subprocess runs: the timeout is user input
    # (--timeout) that flows into a `--timeout=<n>s` kubectl argument, so it
    # goes through the same _validate_timeout sanitizer helm_up/helm_down use
    # (freshly constructed int, rejecting non-finite/non-numeric values) to
    # keep a non-numeric value off the command line (CWE-88; also keeps
    # SonarCloud's S8705 taint flow from args.timeout to the sink closed).
    timeout = _validate_timeout(timeout)
    label = kube_context or "current context"
    if cluster_is_reachable(kube_context):
        if log_file is not None:
            log_file.write(f"cluster reachable: {label}\n")
            log_file.flush()
        return 0

    cluster_name = k3d_cluster_name(kube_context)
    if not create:
        raise RuntimeError(
            f"cluster for {label} is not reachable (kubectl get --raw /healthz failed). "
            "Create it first (e.g. `k3d cluster create <name>`), or pass --create-cluster "
            "with a k3d-<name> --kube-context to let cds create it."
        )
    if cluster_name is None:
        raise RuntimeError(
            f"--create-cluster needs a k3d kube context of the form k3d-<name>, got {label!r}"
        )
    if log_file is None:
        raise RuntimeError(
            "ensure_k8s_cluster requires a log_file when creating a cluster"
        )

    context_name = f"k3d-{cluster_name}"
    print(
        f"Cluster {cluster_name} not reachable; creating it with k3d (context {context_name})"
    )
    # Traefik is disabled to match the scripts/k8s harness: the chart keeps its
    # Services ClusterIP/NodePort and does not need the bundled ingress.
    create_command = [
        "k3d",
        "cluster",
        "create",
        cluster_name,
        "--wait",
        "--k3s-arg",
        "--disable=traefik@server:0",
    ]
    result = run_streamed(create_command, log_file, use_color=use_color)
    if result != 0:
        return result

    wait_seconds = timeout  # already validated to a fresh int above
    wait_command = [
        "kubectl",
        "--context",
        context_name,
        "wait",
        "--for=condition=Ready",
        "node",
        "--all",
        f"--timeout={wait_seconds}s",
    ]
    result = run_streamed(wait_command, log_file, use_color=use_color)
    if result != 0:
        return result
    if not cluster_is_reachable(context_name):
        raise RuntimeError(
            f"cluster {cluster_name} was created but is still not reachable"
        )
    return 0


def import_local_images(
    images: list[str],
    kube_context: str | None = None,
    log_file: IO[str] | None = None,
    *,
    use_color: bool = True,
) -> int:
    """
    Load locally built `images` into the target cluster with
    `k3d image import`. The rendered chart sets `imagePullPolicy: Never` for
    these images, so on a non-k3d cluster there is no supported way to make
    them available and this fails closed with a clear message instead of
    letting the deploy die in ImagePullBackOff. An empty `images` list is a
    no-op (registry-sourced images are pulled by the cluster itself).
    """
    if not images:
        return 0
    validated = [_validate_image_ref(image) for image in images]
    kube_context = _validate_kube_context(kube_context)
    context_label = kube_context or current_kube_context()
    cluster_name = k3d_cluster_name(context_label)
    if cluster_name is None:
        raise RuntimeError(
            f"cannot import local images ({', '.join(validated)}) into a non-k3d cluster "
            f"(context {context_label!r}): the chart pulls them with imagePullPolicy: Never. "
            "Use a k3d context (k3d-<name>), or switch the profile to --image-source registry "
            "so the cluster pulls published images instead."
        )
    if log_file is None:
        raise RuntimeError("import_local_images requires a log_file")
    command = ["k3d", "image", "import", *validated, "--cluster", cluster_name]
    print(f"Running: {' '.join(command)}")
    return run_streamed(command, log_file, use_color=use_color)


__all__ = [
    "LocalImageBuild",
    "build_local_images",
    "cluster_is_reachable",
    "collect_local_builds",
    "current_kube_context",
    "ensure_k8s_cluster",
    "import_local_images",
    "k3d_cluster_name",
]
