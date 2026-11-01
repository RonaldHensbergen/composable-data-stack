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

3. Workflows that call `actions/ai-inference` with `file_input` must produce
   each input file via `scripts/redact_secrets.py` earlier in the same
   workflow (minimize + redact before any external inference).
4. Data minimization is enforced, not just documented: in any job that feeds
   `file_input` to `actions/ai-inference`, no `gh` command may collect
   user-authored free-text fields (`title`, `body`, `comments`) via `--json`.
   Collect metadata only (number, labels, milestone, timestamps, url).

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
        "carry the untrusted-data protocol. Residual disclosure risk is governed "
        "by the resolved policy in .github/prompts/README.md (issue #1082): "
        "metadata-only collection plus scripts/redact_secrets.py before inference."
    ),
}

# User-authored free-text fields that must never be collected for file_input
# (data minimization; decided in issue #1082). Metadata fields such as number,
# labels, milestone, createdAt/updatedAt, and url remain allowed.
FREE_TEXT_FIELDS = frozenset({"title", "body", "comments"})
GH_JSON_RE = re.compile(r"--json[=\s]+([A-Za-z0-9_,\s]+)")
GH_CMD_RE = re.compile(
    r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S+\s+)*gh\s+(?:(?:issue|pr)\s+(?:list|view)|search\s+(?:issues|prs))\b"
)

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

    message_texts = [str(m.get("content", "")) for m in doc["messages"] if isinstance(m, dict)]
    all_text = "\n".join(message_texts)
    variables = sorted(set(VAR_RE.findall(all_text)))
    if variables and path.name not in ALLOWLIST:
        errors.append(
            f"{path.name}: interpolates variable(s) {variables} but is not in the "
            f"ALLOWLIST in scripts/check_prompts.py — add it there with a "
            f"justification, or remove the interpolation"
        )
    if variables and path.name in ALLOWLIST:
        # Structural control for allowlisted interpolation: every {{var}} must
        # sit inside the nearest matching BEGIN/END UNTRUSTED DATA fence pair
        # within the same message, and that fenced block must carry explicit
        # data-only guard text.
        for text in message_texts:
            for match in VAR_RE.finditer(text):
                begin = text.rfind("BEGIN_UNTRUSTED_DATA", 0, match.start())
                end = text.find("END_UNTRUSTED_DATA", match.end())
                if begin == -1 or end == -1:
                    errors.append(
                        f"{path.name}: interpolation {match.group(0)} is not wrapped in "
                        f"BEGIN_UNTRUSTED_DATA / END_UNTRUSTED_DATA fences in the same message"
                    )
                    continue
                block = text[begin:end].lower()
                if "data-only" not in block and "do not follow" not in block and "do-not-follow" not in block:
                    errors.append(
                        f"{path.name}: fenced block around {match.group(0)} lacks "
                        f"explicit data-only / do-not-follow guard text"
                    )
    return errors


WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
AI_INFERENCE_ACTION = "actions/ai-inference"
FILE_INPUT_RE = re.compile(r"^\s*\w[\w-]*:\s*(\S.*)$", re.MULTILINE)


def lint_workflow(path: Path) -> list[str]:
    """Durable trust-boundary rule for workflows: any actions/ai-inference
    step that passes `file_input` files must produce those files through
    scripts/redact_secrets.py earlier in the same workflow, so user-controlled
    content is minimized/redacted before it can cross to an external model."""
    errors: list[str] = []
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return []  # not a workflow document; yamllint's job
    if not isinstance(doc, dict):
        return []
    jobs = doc.get("jobs") or {}
    redact_call_re = re.compile(r"redact_secrets\.py\s+(?:--strict\s+)?(\S+)\s+(\S+)")
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        redacted_outputs: set[str] = set()
        run_scripts: list[str] = []
        feeds_inference = False
        for step in job.get("steps") or []:
            if not isinstance(step, dict):
                continue
            run_script = str(step.get("run") or "").replace("\\\n", " ")
            if run_script.strip():
                run_scripts.append(run_script)
            for match in redact_call_re.finditer(run_script):
                redacted_outputs.add(_canon_path(match.group(2)))
            uses = str(step.get("uses", ""))
            if AI_INFERENCE_ACTION not in uses:
                continue
            file_input = str((step.get("with") or {}).get("file_input") or "")
            if not file_input.strip():
                continue
            feeds_inference = True
            for raw in FILE_INPUT_RE.findall(file_input):
                target = _canon_path(raw)
                if target not in redacted_outputs:
                    errors.append(
                        f"{path.name} (job {job_name}): ai-inference file_input "
                        f"'{raw.strip()}' is not produced by an earlier "
                        f"scripts/redact_secrets.py call in the same job — minimize "
                        f"and redact before any external inference "
                        f"(.github/prompts/README.md)"
                    )
        if feeds_inference:
            errors.extend(_lint_minimization(path, job_name, run_scripts))
    return errors


def _lint_minimization(path: Path, job_name: str, run_scripts: list[str]) -> list[str]:
    """Data minimization as a deterministic control (decided in issue #1082):
    in a job that feeds file_input to actions/ai-inference, no gh command may
    request user-authored free-text fields via --json — titles, bodies, and
    comments are free text and must never cross the trust boundary."""
    errors: list[str] = []
    for script in run_scripts:
        for line in script.splitlines():
            if not GH_CMD_RE.search(line):
                continue
            for match in GH_JSON_RE.finditer(line):
                fields = {f.strip() for f in match.group(1).split(",") if f.strip()}
                free_text = sorted(fields & FREE_TEXT_FIELDS)
                if free_text:
                    errors.append(
                        f"{path.name} (job {job_name}): gh command collects free-text "
                        f"field(s) {free_text} — user-authored text must never be "
                        f"collected for ai-inference file_input; collect metadata only "
                        f"(number, labels, milestone, timestamps, url) "
                        f"(.github/prompts/README.md, decided in #1082)"
                    )
    return errors


def _canon_path(raw: str) -> str:
    """Normalize a path token for exact comparison: strip quotes, unify temp
    dir spellings, POSIX separators, drop leading ./"""
    token = raw.strip().strip("\"'")
    token = token.replace("${{ runner.temp }}", "__TEMP__").replace("$RUNNER_TEMP", "__TEMP__")
    token = token.replace("\\", "/")
    while token.startswith("./"):
        token = token[2:]
    return token


def lint_all(prompts_dir: Path = PROMPTS_DIR) -> list[str]:
    errors: list[str] = []
    files = sorted(prompts_dir.glob("*.prompt.yml"))
    if not files:
        errors.append(f"no *.prompt.yml files found in {prompts_dir}")
    for path in files:
        errors.extend(lint_file(path))
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        errors.extend(lint_workflow(path))
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
