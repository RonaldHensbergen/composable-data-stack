"""
Local, append-only audit trail of rendered/applied stacks (#737).

This is distinct from `cli/getter.py`'s `.cds/get-manifest.json`, which only
tracks file provenance for `cds get`. This module instead records *what was
run* for a profile over time: timestamp, profile identity, resolved module
versions/image references, the command invoked, and its outcome. Users who
are themselves subject to incident-reporting obligations (e.g.
NIS2/Cyberbeveiligingswet-style regimes) can use this to reconstruct what
was running and when, without relying on ad hoc memory or external
infrastructure logs.

The log is strictly local evidence for the user's own purposes: nothing
here is sent to CDS or any third party. Only secret *aliases* are ever
recorded (e.g. "CDS_DB_PASSWORD"), the same `${CDS_*}` placeholder
convention used by the planner/renderer (see `cli/secrets.py`) -- never
secret values.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_LOG_FILE = Path(".cds") / "audit-log.jsonl"

# Set to disable the audit log without touching project config, e.g. for a
# single invocation or a CI job that should not write local evidence files.
_ENV_DISABLE = "CDS_AUDIT_LOG_DISABLE"
_TRUTHY = {"1", "true", "yes", "on"}


def audit_log_path(project_root: Path | None = None) -> Path:
    """Return the path audit entries are appended to for `project_root`.

    No `cli.main` call site currently passes an explicit `project_root`, so
    this defaults to `Path.cwd()` rather than the `resolve_project_root
    (profile_path)` convention used elsewhere (e.g. for rendered Compose
    output). That means audit entries land in whatever directory the user
    happened to invoke `cds` from, not necessarily the profile's own
    project root (e.g. when `CDS_PROFILE_PATH` points at a separate
    profiles checkout). Tracked by #822.
    """
    root = (project_root or Path.cwd()).expanduser().resolve()
    return root / _LOG_FILE


def is_enabled(*, config_disabled: bool = False) -> bool:
    """Whether audit logging is active.

    Disabled if the project config has `audit.enabled` set to `false`
    (`config_disabled=True`), or if `CDS_AUDIT_LOG_DISABLE` is truthy.
    """
    if config_disabled:
        return False
    return os.environ.get(_ENV_DISABLE, "").strip().lower() not in _TRUTHY


def _module_summary(plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Resolved module id/source/version and image reference fields.

    Only reference-shaped image fields (repository/tag/digest/variant) are
    included -- never the full module config, which could carry
    `secrets.<alias>` references or other user-provided values.
    """
    if not isinstance(plan, dict):
        return []

    summary: list[dict[str, Any]] = []
    for module in plan.get("modules", []):
        if not isinstance(module, dict):
            continue
        entry: dict[str, Any] = {
            "id": module.get("id"),
            "source": module.get("source"),
            "version": module.get("version"),
        }
        config = module.get("config")
        image_config = config.get("image") if isinstance(config, dict) else None
        if isinstance(image_config, dict):
            image_ref = {
                key: image_config[key]
                for key in ("repository", "tag", "digest", "variant")
                if key in image_config
            }
            if image_ref:
                entry["image"] = image_ref
        summary.append(entry)
    return summary


def _secret_aliases(plan: dict[str, Any] | None) -> list[str]:
    """Secret alias names referenced by the plan, never their values.

    `plan["secrets"]` already maps alias/env names to `CDS_*` env var
    *names* (see `cli/secrets.py:load_profile_secrets`) -- no secret value
    ever passes through the planner, so this is safe to record verbatim.
    """
    if not isinstance(plan, dict):
        return []
    secrets = plan.get("secrets")
    if not isinstance(secrets, dict):
        return []
    return sorted(secrets.keys())


def record_entry(
    *,
    command: str,
    profile: str,
    outcome: str,
    environment: str | None = None,
    plan: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
    project_root: Path | None = None,
    config_disabled: bool = False,
) -> Path | None:
    """Append one JSON Lines entry describing a render/apply attempt.

    Returns the log path written to, or `None` if audit logging is
    disabled. Never raises: a failure to write the audit log must not
    block the primary command it is observing.
    """
    if not is_enabled(config_disabled=config_disabled):
        return None

    log_path = audit_log_path(project_root)
    entry: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "command": command,
        "profile": profile,
        "environment": environment,
        "outcome": outcome,
        "modules": _module_summary(plan),
        "secretAliases": _secret_aliases(plan),
    }
    if details:
        entry["details"] = details

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if log_path.is_symlink():
            # Never follow/overwrite-through a symlink planted at the log
            # path (same guard as the get-manifest writer in getter.py).
            log_path.unlink()
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
    except OSError as exc:
        print(f"WARNING Could not write audit log entry to {log_path}: {exc}", file=sys.stderr)
        return None

    return log_path
