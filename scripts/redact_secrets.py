#!/usr/bin/env python3
"""Deterministic pre-inference redaction for untrusted text sent to an
external model (actions/ai-inference).

Reads a JSON file of GitHub issues (as produced by `gh issue list --json`)
and writes the same structure with secret-looking values in string fields
replaced by [REDACTED]. Used by biweekly-issue-audit.yml on open-issues.json
before it is passed as `file_input` to a prompt, so user-controlled issue
text crosses the trust boundary only after scrubbing. Deny-by-default for
recognizable secret patterns; never touches structure, only string values.

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
    # Key/value style secrets in text.
    re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd|authorization|auth[_-]?header)\b(\s*[:=]\s*)[^\s,;\"']+"),
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


def redact_text(text: str) -> str:
    """Strip control characters, neutralize injection-like content, and
    replace secret-looking substrings."""
    out = CONTROL_CHARS.sub("", text)
    for pattern in INJECTION_PATTERNS:
        out = pattern.sub(REDACTED, out)
    for pattern in PATTERNS:
        if pattern.groups >= 2:  # key/value pattern: keep the key name
            out = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", out)
        else:
            out = pattern.sub(REDACTED, out)
    return out


def redact_obj(obj: object) -> object:
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, list):
        return [redact_obj(item) for item in obj]
    if isinstance(obj, dict):
        return {key: redact_obj(value) for key, value in obj.items()}
    return obj


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    src = Path(args[0])
    data = json.loads(src.read_text(encoding="utf-8"))
    redacted = redact_obj(data)
    out = json.dumps(redacted, indent=2, ensure_ascii=False)
    if len(args) > 1:
        Path(args[1]).write_text(out + "\n", encoding="utf-8")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
