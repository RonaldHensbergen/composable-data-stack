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
   `file_input` to `actions/ai-inference`, no `gh` collection command
   (`issue/pr list|view`, `search issues|prs`, `gh api`) may collect
   user-authored free-text fields (`title`, `body`, `comments`) — not via
   `--json`/`--field`, not via free-text REST endpoints or `gh api graphql`,
   and not via `--jq` (computed access cannot be verified statically, so any
   `--jq` fails closed). Dynamic or missing arguments fail closed too.
   Collect literal metadata only (number, labels, milestone, timestamps, url).

Runs in CI (lint job), pre-commit, and `make lint-prompts`. Exit 1 on any
violation; the script never modifies files.
"""
from __future__ import annotations

import re
import shlex
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
# Detection: any gh collection-capable invocation — issue/pr list|view,
# search issues|prs, and gh api (REST or graphql; github-advanced-security
# review on PR #1087: gh api could fetch titles/bodies unnoticed) — including
# env-prefixed (GH_TOKEN=... gh ...) and $(gh ...) forms. Fail closed: a false
# positive here only fails the lint, never opens a hole.
GH_CMD_RE = re.compile(
    r"\bgh\s+(?:(?:issue|pr)\s+(?:list|view)|search\s+(?:issues|prs)|api\b)"
)
GH_API_RE = re.compile(r"\bgh\s+api\b")
GH_API_GRAPHQL_RE = re.compile(r"\bgh\s+api\s+graphql\b")
# gh api endpoints whose responses contain user-authored free text by default.
API_FREE_TEXT_RESOURCE_RE = re.compile(r"(?:^|/)(?:issues|pulls|comments|reviews|discussions)(?:/|$|\?)")
# Field-list extraction, quote-aware: --json=f, --json f, --json "f,g",
# --json 'f, g' (github-advanced-security review on PR #1087: a regex over
# raw text that does not account for quotes misses --json "number,title").
GH_JSON_RE = re.compile(r"--json[=\s]*[\"']?\s*([A-Za-z0-9_]+(?:\s*,\s*[A-Za-z0-9_]+)*)")
# Dynamic (non-literal) --json/--jq arguments — --json "$FIELDS",
# --json "$(cat fields.txt)", globs, or a missing value — cannot be verified
# statically, so they fail closed via a sentinel that downstream checks treat
# as disallowed (github-advanced-security review on PR #1087).
DYNAMIC_ARG = "<dynamic-argument>"
DYNAMIC_ARG_RE = re.compile(r"[$*?`]")
# A value token that is actually a shell operator means --json/--jq has no
# value at all — also fail closed.
SHELL_OPERATORS = frozenset({">", ">>", "<", "|", "||", "&&", ";", "&"})
GH_JSON_DYNAMIC_RE = re.compile(r"--json[=\s]+[\"']?\S*[$*?`]\S*|--json[=\s]*$")
GH_JQ_DYNAMIC_RE = re.compile(r"--jq[=\s]+[\"']?\S*[$*?`]\S*|--jq[=\s]*$")
# --jq is disallowed outright on gh collection commands that feed file_input:
# computed key access (e.g. .["tit"+"le"]) cannot be verified statically, so
# any jq expression — literal or dynamic — fails closed
# (github-advanced-security review on PR #1087).


def _is_gh_collection_cmd(line: str) -> bool:
    """True when the line invokes one of the gh commands whose --json output
    could be collected as ai-inference file_input."""
    return bool(GH_CMD_RE.search(line))


def _gh_json_fields(line: str) -> set[str]:
    """Every --json field name in the line. Primary path tokenizes with
    shlex and inspects --json / --json= tokens uniformly (quoted values are
    handled naturally); a quote-aware regex union covers forms shlex cannot
    see (e.g. inside $(...) substitution), and remains the fallback when the
    line is not valid shell at all. Never silently returns empty on a line
    that does carry --json."""
    fields: set[str] = set()
    for match in GH_JSON_RE.finditer(line):
        fields.update(f.strip() for f in match.group(1).split(",") if f.strip())
    if GH_JSON_DYNAMIC_RE.search(line):
        fields.add(DYNAMIC_ARG)
    try:
        tokens = shlex.split(line)
    except ValueError:
        return fields  # malformed shell: regex extraction above still applies
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--json":
            if (
                i + 1 >= len(tokens)
                or tokens[i + 1].startswith("-")
                or tokens[i + 1] in SHELL_OPERATORS
            ):
                fields.add(DYNAMIC_ARG)  # bare --json with no value: fail closed
            else:
                value = tokens[i + 1]
                if DYNAMIC_ARG_RE.search(value):
                    fields.add(DYNAMIC_ARG)
                else:
                    fields.update(f.strip() for f in value.split(",") if f.strip())
            i += 2
            continue
        if tok.startswith("--json="):
            value = tok[len("--json="):]
            if not value or DYNAMIC_ARG_RE.search(value):
                fields.add(DYNAMIC_ARG)
            else:
                fields.update(f.strip() for f in value.split(",") if f.strip())
        i += 1
    return fields


def _gh_jq_expressions(line: str) -> set[str]:
    """Every --jq expression in the line (--jq <e>, --jq=<e>, quoted), via
    shlex tokens plus a quote-aware regex union, mirroring _gh_json_fields."""
    exprs: set[str] = set()
    if GH_JQ_DYNAMIC_RE.search(line):
        exprs.add(DYNAMIC_ARG)
    try:
        tokens = shlex.split(line)
    except ValueError:
        tokens = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--jq":
            if (
                i + 1 >= len(tokens)
                or tokens[i + 1].startswith("-")
                or tokens[i + 1] in SHELL_OPERATORS
            ):
                exprs.add(DYNAMIC_ARG)  # bare --jq with no value: fail closed
            else:
                value = tokens[i + 1]
                exprs.add(DYNAMIC_ARG if DYNAMIC_ARG_RE.search(value) else value)
            i += 2
            continue
        if tok.startswith("--jq="):
            value = tok[len("--jq="):]
            exprs.add(DYNAMIC_ARG if not value or DYNAMIC_ARG_RE.search(value) else value)
        i += 1
    for match in re.finditer(r"--jq[=\s]+[\"']([^\"']+)[\"']|--jq[=\s]+(\S+)", line):
        exprs.add(match.group(1) or match.group(2) or "")
    return exprs


def _gh_flag_hits(line: str) -> list[str]:
    """--field/-F values and --template/-t on a matched gh command: computed
    output cannot be verified statically — fail closed on any presence."""
    hits: list[str] = []
    try:
        tokens = shlex.split(line)
    except ValueError:
        tokens = []
    if tokens:
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok in ("--field", "-F") and i + 1 < len(tokens):
                if tokens[i + 1].strip().strip("\"'").lower() in FREE_TEXT_FIELDS:
                    hits.append(f"--field {tokens[i + 1]}")
                i += 2
                continue
            if tok.startswith("--field=") and tok.split("=", 1)[1].strip().lower() in FREE_TEXT_FIELDS:
                hits.append(f"--field={tok.split('=', 1)[1]}")
            if tok in ("--template", "-t"):
                hits.append("--template (computed output cannot be verified statically)")
            i += 1
    else:
        # Unparseable line: conservative raw-text fallbacks.
        for match in re.finditer(r"--field[=\s]+[\"']?(\w+)", line):
            if match.group(1).lower() in FREE_TEXT_FIELDS:
                hits.append(f"--field {match.group(1)}")
        if re.search(r"--template(=|\s)", line):
            hits.append("--template (raw match)")
    return hits


def _gh_api_free_text_hits(line: str) -> list[str]:
    """gh api in an inference-feeding job always fails closed: REST responses
    can include titles/bodies/comments by default, `gh api graphql` can select
    arbitrary fields, and --field/--template output is computed — none of it
    can be verified statically (github-advanced-security review on PR #1087).
    Returns the reasons when the line invokes gh api, else an empty list."""
    if not GH_API_RE.search(line):
        return []
    hits = ["gh api is not allowed in jobs that feed ai-inference file_input"]
    if GH_API_GRAPHQL_RE.search(line):
        hits.append("gh api graphql query can select arbitrary fields incl. free text")
    try:
        tokens = shlex.split(line)
    except ValueError:
        tokens = []
    if tokens:
        for i, tok in enumerate(tokens):
            if tok == "api" and i + 1 < len(tokens):
                endpoint = tokens[i + 1]
                if not endpoint.startswith("-") and API_FREE_TEXT_RESOURCE_RE.search(endpoint):
                    hits.append(f"endpoint '{endpoint}' returns free text by default")
                break
    elif API_FREE_TEXT_RESOURCE_RE.search(line):
        hits.append("gh api endpoint with free-text resource (raw match)")
    hits.extend(_gh_flag_hits(line))
    return hits

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
            if not _is_gh_collection_cmd(line):
                continue
            fields = _gh_json_fields(line)
            free_text = sorted(fields & FREE_TEXT_FIELDS)
            dynamic_json = DYNAMIC_ARG in fields
            jq_exprs = _gh_jq_expressions(line)
            dynamic_jq = DYNAMIC_ARG in jq_exprs
            api_hits = _gh_api_free_text_hits(line)
            flag_hits = [] if api_hits else _gh_flag_hits(line)
            problems: list[str] = []
            if dynamic_json or dynamic_jq:
                problems.append(
                    "a dynamic or missing --json/--jq argument (shell expansion like "
                    "$VAR, $(...), backticks, or a glob) that cannot be verified statically"
                )
            if free_text:
                problems.append(f"free-text field(s) {free_text} via --json")
            if jq_exprs and not dynamic_jq:
                # Any jq expression fails closed: computed key access such as
                # .["tit"+"le"] cannot be verified statically (#1087 review).
                problems.append(
                    "a --jq expression (computed access cannot be verified statically, "
                    "so it can re-extract free-text fields)"
                )
            if api_hits:
                problems.append(f"gh api free-text exposure: {', '.join(api_hits)}")
            if not api_hits:
                # A matched gh collection command without any --json flag prints
                # the default table, which includes titles — fail closed unless
                # an explicit literal metadata field list is given.
                if not re.search(r"--json\b", line):
                    problems.append(
                        "no explicit --json metadata field list (default output "
                        "includes titles)"
                    )
                if flag_hits:
                    problems.append(", ".join(flag_hits))
            if problems:
                errors.append(
                    f"{path.name} (job {job_name}): gh command rejected for ai-inference "
                    f"file_input — {'; '.join(problems)} — user-authored text must never "
                    f"be collected; use a literal metadata-only field list (number, labels, "
                    f"milestone, timestamps, url) (.github/prompts/README.md, decided in #1082)"
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
