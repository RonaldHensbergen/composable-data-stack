"""Declarative runtime confinement (seccomp, AppArmor, SELinux) for Compose services.

A profile declares a default under `spec.security.runtime.confinement`; a
module instance may override individual keys under `security.confinement`.
The effective value is rendered into each service's `security_opt`.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .diagnostics import Diagnostic

INVALID_CONFINEMENT_CODE = "E127"
CONFINEMENT_KEYS = ("seccomp", "apparmor", "selinux")

_APPARMOR_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_SELINUX_LABEL_RE = re.compile(r"^(user|role|type|level):[A-Za-z0-9_.,:-]+$")


def profile_confinement(profile_or_plan: dict[str, Any] | None) -> dict[str, Any]:
    """Return the profile-wide confinement block from a profile or plan."""
    if not isinstance(profile_or_plan, dict):
        return {}
    security = profile_or_plan.get("security")
    if security is None:
        security = (profile_or_plan.get("spec") or {}).get("security")
    runtime = (security or {}).get("runtime") if isinstance(security, dict) else None
    block = (runtime or {}).get("confinement") if isinstance(runtime, dict) else None
    return block if isinstance(block, dict) else {}


def plan_declares_confinement(plan: dict[str, Any] | None) -> bool:
    """True when the profile or any module instance sets confinement."""
    if profile_confinement(plan):
        return True
    return any(
        isinstance((module.get("security") or {}).get("confinement"), dict)
        and (module.get("security") or {}).get("confinement")
        for module in (plan or {}).get("modules") or []
        if isinstance(module, dict)
    )


def effective_confinement(
    defaults: dict[str, Any] | None, override: dict[str, Any] | None
) -> dict[str, Any]:
    """Profile defaults with the module instance's keys layered on top."""
    merged = {key: value for key, value in (defaults or {}).items() if key in CONFINEMENT_KEYS}
    for key, value in (override or {}).items():
        if key in CONFINEMENT_KEYS:
            merged[key] = value
    return merged


def validate_confinement(
    block: Any, path: str, base_dir: Path | None = None
) -> list[Diagnostic]:
    """Check one confinement block; `base_dir` anchors `file:` seccomp profiles."""
    if block is None:
        return []
    if not isinstance(block, dict):
        return [_error(f"{path} must be an object.", path)]
    diagnostics: list[Diagnostic] = []
    for key in block:
        if key not in CONFINEMENT_KEYS:
            diagnostics.append(_error(
                f"Unknown confinement key '{key}'; supported keys: {', '.join(CONFINEMENT_KEYS)}.",
                f"{path}.{key}",
            ))

    seccomp = block.get("seccomp")
    if seccomp is not None:
        diagnostics.extend(_validate_seccomp(seccomp, f"{path}.seccomp", base_dir))
    apparmor = block.get("apparmor")
    if apparmor is not None and not (
        isinstance(apparmor, str)
        and (apparmor in {"runtime/default", "unconfined"} or _APPARMOR_NAME_RE.match(apparmor))
    ):
        diagnostics.append(_error(
            "apparmor must be 'runtime/default', 'unconfined' or the name of a profile "
            "already loaded on the host (letters, digits, '.', '_', '-', '/').",
            f"{path}.apparmor",
        ))
    selinux = block.get("selinux")
    if selinux is not None:
        diagnostics.extend(_validate_selinux(selinux, f"{path}.selinux"))
    return diagnostics


def _validate_seccomp(value: Any, path: str, base_dir: Path | None) -> list[Diagnostic]:
    if value in {"default", "unconfined"}:
        return []
    if not isinstance(value, str) or not value.startswith("file:") or len(value) == 5:
        return [_error(
            "seccomp must be 'default', 'unconfined' or 'file:<path to a JSON profile "
            "relative to the profile directory>'.",
            path,
        )]
    relative = value[5:]
    if base_dir is None:
        return [_error(
            "seccomp file profiles need a known profile directory to resolve against.", path
        )]
    # Reject rooted and drive-qualified paths explicitly: they are not
    # "absolute" to pathlib on every platform (e.g. "/x" on Windows).
    if (
        Path(relative).is_absolute()
        or relative.startswith(("/", "\\"))
        or _DRIVE_RE.match(relative)
        or ".." in Path(relative).parts
    ):
        return [_error(
            "seccomp file profiles must be relative paths inside the profile directory.", path
        )]
    target = (base_dir / relative).resolve()
    if not target.is_relative_to(base_dir.resolve()):
        return [_error(
            "seccomp file profile resolves outside the profile directory.", path
        )]
    if not target.is_file():
        return [_error(f"seccomp profile '{relative}' does not exist.", path)]
    return []


def _validate_selinux(value: Any, path: str) -> list[Diagnostic]:
    if value == "disable":
        return []
    if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
        return [_error(
            "selinux must be 'disable' or a non-empty list of labels such as "
            "'type:container_t' or 'level:s0:c100,c200'.",
            path,
        )]
    bad = [label for label in value if not _SELINUX_LABEL_RE.match(label)]
    if bad:
        return [_error(
            f"Invalid SELinux label(s): {', '.join(bad)}; expected user:, role:, type: or level: values.",
            path,
        )]
    return []


def confinement_security_opts(
    confinement: dict[str, Any], compose_dir: Path, base_dir: Path | None
) -> list[str]:
    """Translate a validated confinement block into Compose `security_opt` entries.

    Raises ValueError when a seccomp file cannot be expressed relative to
    `compose_dir` (for example on a different Windows drive).
    """
    opts: list[str] = []
    seccomp = confinement.get("seccomp")
    if seccomp == "unconfined":
        opts.append("seccomp=unconfined")
    elif isinstance(seccomp, str) and seccomp.startswith("file:"):
        target = (base_dir or compose_dir) / seccomp[5:]
        opts.append(f"seccomp:{Path(os.path.relpath(target, compose_dir)).as_posix()}")
    apparmor = confinement.get("apparmor")
    if apparmor == "runtime/default":
        opts.append("apparmor=docker-default")
    elif isinstance(apparmor, str):
        opts.append(f"apparmor={apparmor}")
    selinux = confinement.get("selinux")
    if selinux == "disable":
        opts.append("label=disable")
    elif isinstance(selinux, list):
        opts.extend(f"label={label}" for label in selinux)
    return opts


def apply_confinement(
    service: dict[str, Any],
    confinement: dict[str, Any],
    compose_dir: Path,
    base_dir: Path | None,
    path: str = "security.confinement",
) -> list[Diagnostic]:
    """Set the declared confinement on a service, replacing module-set values per key."""
    if not confinement:
        return []
    try:
        new_opts = confinement_security_opts(confinement, compose_dir, base_dir)
    except ValueError:
        return [_error(
            "seccomp file profile cannot be referenced relative to the generated "
            "docker-compose.yml (different drive); render into the profile's drive.",
            f"{path}.seccomp",
        )]
    existing = [str(opt) for opt in service.get("security_opt") or []]
    replaced = {
        "seccomp": ("seccomp=", "seccomp:"),
        "apparmor": ("apparmor=", "apparmor:"),
        "selinux": ("label=", "label:"),
    }
    prefixes = tuple(p for key in confinement for p in replaced.get(key, ()))
    kept = [opt for opt in existing if not opt.startswith(prefixes)] if prefixes else existing
    # `default` seccomp means "engine default", so it only clears module-set values.
    merged = kept + new_opts
    if merged:
        service["security_opt"] = merged
    else:
        service.pop("security_opt", None)
    return []


def _error(message: str, path: str) -> Diagnostic:
    return Diagnostic(level="error", code=INVALID_CONFINEMENT_CODE, message=message, path=path)
