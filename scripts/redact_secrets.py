#!/usr/bin/env python3
"""Deterministic pre-inference redaction for untrusted text sent to an
external model (actions/ai-inference).

Reads a JSON file of GitHub issues (as produced by `gh issue list --json`)
and writes the same structure with secret-looking values in string fields
replaced by [REDACTED]. Plain-text input (e.g. `git log` output) is redacted
line by line. Used by biweekly-issue-audit.yml on every file passed as
`file_input` to a prompt, so user-controlled text crosses the trust boundary
only after scrubbing. Deny-by-default for recognizable secret patterns;
never touches JSON structure, only string values.

Usage: python scripts/redact_secrets.py input.json [output.json]
(default output: stdout). Stdlib only.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REDACTED = "[REDACTED]"

PATTERNS: list[re.Pattern[str]] = [
    # Well-known token formats.
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    # Key/value style secrets in text, including quoted values
    # (password="x", token: 'y'). Matched via _redact_kv below.
    re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password|passwd|authorization|auth[_-]?header)\b"
        r"(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;\"']+)"
    ),
    # Bearer/basic credentials in free text.
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"),
]

CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Instruction-like content that must never reach the model as text records.
INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)\b(ignore|disregard|forget)\s+(all\s+|any\s+|the\s+)?(previous|prior|above)\s+instructions?\b[^\n]*"),
    re.compile(r"(?i)^\s*(system|assistant|user|developer)\s*:\s*", re.MULTILINE),
    re.compile(r"(?i)\b(system\s+prompt|tool[_-]?call|function[_-]?call)\b[^\n]*"),
    re.compile(r"(?i)</?\s*(instruction|prompt|system)\s*>"),
]


def _redact_kv(match: re.Match[str]) -> str:
    """Keep the key name, replace the value (quotes included) with [REDACTED]."""
    return f"{match.group(1)}{match.group(2)}{REDACTED}"


def redact_text(text: str, hits: list[str] | None = None) -> str:
    """Strip control characters, neutralize injection-like content, and
    replace secret-looking substrings. If `hits` is a list, every matched
    injection pattern is recorded there (for --strict fail-closed signaling)."""
    out = CONTROL_CHARS.sub("", text)
    for pattern in INJECTION_PATTERNS:
        if pattern.search(out):
            if hits is not None:
                hits.append(pattern.pattern)
            out = pattern.sub(REDACTED, out)
    for pattern in PATTERNS:
        if pattern.groups >= 3:  # key/value pattern: keep the key name
            out = pattern.sub(_redact_kv, out)
        else:
            out = pattern.sub(REDACTED, out)
    return out


def redact_obj(obj: object, hits: list[str] | None = None) -> object:
    if isinstance(obj, str):
        return redact_text(obj, hits)
    if isinstance(obj, list):
        return [redact_obj(item, hits) for item in obj]
    if isinstance(obj, dict):
        return {key: redact_obj(value, hits) for key, value in obj.items()}
    return obj


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    strict = "--strict" in args
    args = [a for a in args if a != "--strict"]
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    hits: list[str] = []
    src = Path(args[0])
    raw = src.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
        redacted = redact_obj(data, hits)
        out = json.dumps(redacted, indent=2, ensure_ascii=False)
    except json.JSONDecodeError:
        out = "\n".join(redact_text(line, hits) for line in raw.splitlines())
    if len(args) > 1:
        Path(args[1]).write_text(out + "\n", encoding="utf-8")
    else:
        print(out)
    if strict and hits:
        print(f"strict mode: {len(hits)} injection-like pattern(s) detected and redacted", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
