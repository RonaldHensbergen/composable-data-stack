#!/usr/bin/env python3
"""Lint .github/prompts/*.prompt.yml against the prompt trust-boundary policy.

This repo is security-first: prompt templates run against an external model,
so untrusted content must never enter a prompt without a deterministic
control. The policy enforced here (see .github/prompts/README.md):

1. Every prompt's system message must contain the fail-closed untrusted-data
   protocol. Required markers (case-insensitive): "untrusted", "refuse",
   "credentials". The protocol makes pasted/interpolated content inert data,
   forbids following instructions inside it, and forbids outputting secrets.
2. `{{...}}` variable interpolation is denied by default. A prompt file may
   interpolate only if it is listed in ALLOWLIST below with a justification;
   the allowlist exists for workflows that pass data via `file_input` (the
   substitution happens in the ai-inference action, not in free chat).

Runs in CI (lint job), pre-commit, and `make lint-prompts`. Exit 1 on any
violation; the script never modifies files.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = REPO_ROOT / ".github" / "prompts"

# file name -> justification. Keep this list minimal and review every entry.
ALLOWLIST: dict[str, str] = {
    "issue-consistency-audit.prompt.yml": (
        "interpolates file_input data (open-issues JSON, recent commits) supplied "
        "by biweekly-issue-audit.yml via actions/ai-inference; system prompt must "
        "carry the untrusted-data protocol. Residual disclosure risk of issue "
        "content reaching the external model is tracked in issue #1082."
    ),
}

REQUIRED_MARKERS = ["untrusted", "refuse", "credentials"]
VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def lint_file(path: Path) -> list[str]:
    """Return a list of policy violations for one prompt file."""
    errors: list[str] = []
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return [f"{path.name}: invalid YAML: {exc}"]
    if not isinstance(doc, dict) or not isinstance(doc.get("messages"), list):
        return [f"{path.name}: must be a mapping with a 'messages' list"]

    system_contents = [
        str(m.get("content", ""))
        for m in doc["messages"]
        if isinstance(m, dict) and m.get("role") == "system"
    ]
    if not system_contents:
        errors.append(f"{path.name}: missing system message")
    else:
        system_text = "\n".join(system_contents).lower()
        for marker in REQUIRED_MARKERS:
            if marker not in system_text:
                errors.append(
                    f"{path.name}: system message lacks required untrusted-data "
                    f"protocol marker {marker!r} (see .github/prompts/README.md)"
                )

    all_text = "\n".join(
        str(m.get("content", "")) for m in doc["messages"] if isinstance(m, dict)
    )
    variables = sorted(set(VAR_RE.findall(all_text)))
    if variables and path.name not in ALLOWLIST:
        errors.append(
            f"{path.name}: interpolates variable(s) {variables} but is not in the "
            f"ALLOWLIST in scripts/check_prompts.py — add it there with a "
            f"justification, or remove the interpolation"
        )
    return errors


def lint_all(prompts_dir: Path = PROMPTS_DIR) -> list[str]:
    errors: list[str] = []
    files = sorted(prompts_dir.glob("*.prompt.yml"))
    if not files:
        errors.append(f"no *.prompt.yml files found in {prompts_dir}")
    for path in files:
        errors.extend(lint_file(path))
    return errors


def main() -> int:
    errors = lint_all()
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"prompt lint OK ({len(list(PROMPTS_DIR.glob('*.prompt.yml')))} prompt files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
