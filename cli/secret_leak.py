"""Value-based detection of resolved secrets in generated or printed output.

Backs the code-enforced rules CDS-SEC-006 (rendered Compose model),
CDS-SEC-030 (generated files) and CDS-SEC-071 (CLI output). Rather than
guessing from entropy, these look for the literal secret values CDS knows
about (the `CDS_*` variables from `.env`/the environment), so a hit is a real
leak: CDS only ever renders `${CDS_*}` placeholders, never resolved values.
"""
from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any, TextIO

from .diagnostics import Diagnostic
from .secrets import load_secrets_from_env
from .security_common import SECRET_KEY_RE

# Shorter values (e.g. "admin") would match ordinary words and mangle output;
# weak credentials are reported separately by the CDS-SEC-010 family.
MIN_SECRET_LENGTH = 8

REDACTED = "***REDACTED***"

GENERATED_FILE_LEAK_CODE = "E096"
CLI_OUTPUT_REDACTED_CODE = "W102"

# CDS_* variables that configure the CLI itself and are never secret even if
# their names happen to contain a secret-like word.
_NON_SECRET_ENV_NAMES = frozenset({
    "CDS_CHECK_UPDATES",
    "CDS_CHECK_IMAGE_DIGESTS",
    "CDS_MODULE_PATH",
    "CDS_PROFILE_PATH",
    "CDS_CONFIG_PATH",
    "CDS_AUDIT_LOG_DISABLE",
})


def declared_secret_env_names(plan: dict[str, Any] | None) -> set[str]:
    """Env var names a plan maps its secret aliases to (never their values)."""
    if not isinstance(plan, dict):
        return set()
    secrets = plan.get("secrets", {})
    if not isinstance(secrets, dict):
        return set()
    return {
        value for alias, value in secrets.items()
        if isinstance(value, str) and alias != value
    }


def resolved_secret_values(
    declared_env_names: set[str] | frozenset[str] | None = None,
    env_file: str | None = None,
) -> dict[str, str]:
    """Return {env name: value} for every CDS_* variable that holds a secret.

    A variable counts as secret when the profile declares it under
    `spec.secrets.values` or its name looks secret-like. Unreadable env files
    simply yield no extra values; the process environment is still covered.
    """
    declared = declared_env_names or frozenset()
    values, _ = load_secrets_from_env(Path(env_file) if env_file else None)
    # load_secrets_from_env returns nothing when the env file is unreadable,
    # which would silently drop environment-provided secrets; fail closed.
    for name, value in os.environ.items():
        if name.startswith("CDS_"):
            values.setdefault(name, value)
    result: dict[str, str] = {}
    for name, value in values.items():
        if name in _NON_SECRET_ENV_NAMES or len(value) < MIN_SECRET_LENGTH:
            continue
        if name in declared or SECRET_KEY_RE.search(name):
            result[name] = value
    return result


def find_secret_leaks(text: str, secret_values: dict[str, str]) -> list[tuple[str, int]]:
    """Return (env name, 1-based line) for each literal secret occurrence."""
    leaks: list[tuple[str, int]] = []
    for name, value in secret_values.items():
        start = 0
        while (index := text.find(value, start)) != -1:
            leaks.append((name, text.count("\n", 0, index) + 1))
            start = index + len(value)
    return sorted(leaks, key=lambda leak: (leak[1], leak[0]))


def check_generated_text(
    text: str,
    label: str,
    secret_values: dict[str, str],
) -> list[Diagnostic]:
    """CDS-SEC-030: one error per distinct secret found in generated content."""
    diagnostics: list[Diagnostic] = []
    seen: set[str] = set()
    for name, line in find_secret_leaks(text, secret_values):
        if name in seen:
            continue
        seen.add(name)
        diagnostics.append(Diagnostic(
            level="error",
            code=GENERATED_FILE_LEAK_CODE,
            message=(
                f"[CDS-SEC-030] Generated content contains the resolved value of {name}; "
                "refusing to write it. Render secrets as ${CDS_*} placeholders instead of "
                "literal values (for example by moving the literal out of module/profile "
                "config and into spec.secrets.values)."
            ),
            path=f"{label}:{line}",
        ))
    return diagnostics


def redact_text(text: str, secret_values: dict[str, str]) -> tuple[str, set[str]]:
    """Replace literal secret values with a marker; return (text, names hit).

    Longer values are replaced first so a secret that contains another secret
    as a substring is still fully redacted.
    """
    hit: set[str] = set()
    for name, value in sorted(secret_values.items(), key=lambda item: -len(item[1])):
        if value in text:
            hit.add(name)
            text = text.replace(value, REDACTED)
    return text, hit


class RedactingStream(io.TextIOBase):
    """Text stream wrapper that redacts known secret values (CDS-SEC-071).

    Output is buffered per line so a value is still caught when a single
    `print()` reaches the stream in several `write()` calls.
    """

    def __init__(self, wrapped: TextIO, secret_values: dict[str, str], hits: set[str]):
        self._wrapped = wrapped
        self._secrets = secret_values
        self._hits = hits
        self._buffer = ""

    def writable(self) -> bool:
        return True

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return getattr(self._wrapped, "encoding", "utf-8")

    def isatty(self) -> bool:
        return self._wrapped.isatty()

    def fileno(self) -> int:
        return self._wrapped.fileno()

    def write(self, text: str) -> int:
        self._buffer += text
        head, newline, tail = self._buffer.rpartition("\n")
        if newline:
            self._emit(head + newline)
            self._buffer = tail
        return len(text)

    def flush(self) -> None:
        # A secret never spans a flush boundary in practice, and holding a
        # partial line back would break prompts.
        self._emit(self._buffer)
        self._buffer = ""
        self._wrapped.flush()

    def _emit(self, text: str) -> None:
        if not text:
            return
        redacted, hit = redact_text(text, self._secrets)
        self._hits.update(hit)
        self._wrapped.write(redacted)

