# Prompt Templates

Prompt files in this directory (`*.prompt.yml`) run against an external model
provider via `actions/ai-inference`. This repository is security-first, so
untrusted content must never reach a model without a deterministic control.
`scripts/check_prompts.py` (CI lint job, pre-commit, `make lint-prompts`)
enforces the policy below and fails the build on violations.

## Policy

1. **Every prompt's system message must carry the fail-closed
   untrusted-data protocol.** It must contain the markers `untrusted`,
   `refuse`, and `credentials` (checked case-insensitively): pasted or
   interpolated content is inert data, never instructions; injection
   patterns (role tags, tool-call directives, imperatives) trigger refusal;
   secrets, credentials, internal URLs/hosts, and personal data are never
   output.
2. **No `{{...}}` variable interpolation by default.** New prompts must not
   interpolate variables — describe the topic in the conversation instead.
   Interpolation is allowed only for files listed in the `ALLOWLIST` in
   `scripts/check_prompts.py`, each with a written justification, and every
   interpolated `{{var}}` in an allowlisted file must be wrapped in
   `BEGIN_UNTRUSTED_DATA` / `END_UNTRUSTED_DATA` fences (the lint enforces
   this). Today the only allowlisted file is `issue-consistency-audit.prompt.yml`,
   whose `{{issues}}` / `{{recent_commits}}` are substituted by
   `biweekly-issue-audit.yml` from `file_input` files.
3. **Data minimization plus deterministic redaction for `file_input`.**
   `file_input` data is user-controlled, so workflows must (a) collect only
   non-free-text fields — `biweekly-issue-audit.yml` gathers
   number/labels/milestone/timestamps/url and never collects issue titles or
   bodies — and (b) scrub what remains: it runs `scripts/redact_secrets.py`
   over the JSON (secret-pattern and injection-pattern replacement with
   `[REDACTED]`, control-char stripping) before inference. The lint also
   enforces this at the workflow level: any `actions/ai-inference` step with
   `file_input` must produce each input file via `scripts/redact_secrets.py`
   in the same workflow.
4. **Data minimization is lint-enforced, not just documented.** In any job
   that feeds `file_input` to `actions/ai-inference`, no `gh` command may
   request user-authored free-text fields (`title`, `body`, `comments`) via
   `--json` or re-extract them via `--jq`. Metadata only: number, labels,
   milestone, timestamps, url. Field lists are extracted by shell-token
   parsing (`shlex`) with a quote-aware regex fallback, so quoted
   (`--json "number,title"`), `--flag=value`, and `$(gh ...)` forms cannot
   bypass the check; dynamic or missing arguments (`--json "$FIELDS"`,
   `--json "$(cat fields.txt)"`, bare `--json`) cannot be verified statically
   and fail closed.

## Adding a new prompt

1. Copy the shape of `design-artifact.prompt.yml`: a system message with the
   protocol above, a user message that carries no input data, `model` and
   `modelParameters`.
2. If your workflow must pass data, use `file_input` (as
   `biweekly-issue-audit.yml` does), add the file to `ALLOWLIST` with a
   justification, wrap the placeholders in `BEGIN/END_UNTRUSTED_DATA`
   fences, keep the protocol in the system message, and redact the input
   with `scripts/redact_secrets.py` before inference.
3. Run `make lint-prompts` and the unit tests in `tests/test_prompts_lint.py`.

## Decisions (resolved in #1082)

The tracking questions from issue #1082 are decided as follows:

1. **No `{{...}}` interpolation of untrusted content is written repo policy**
   for `.github/prompts/*` — rule 2 above — and is enforced by
   `scripts/check_prompts.py`, not by convention.
2. **Sanitization lives in the invoking pipeline, not in prompt text.**
   Prompt text is a non-deterministic control (advanced-security correctly
   flagged "treat as untrusted" markers as such); the deterministic control
   is `scripts/redact_secrets.py` run over every `file_input` file before
   inference, which the lint enforces at the workflow level (rule 3).
3. **Prompt templates that need issue context are kept, not dropped in favor
   of runbook docs.** The biweekly issue-consistency audit's value is
   cross-issue reasoning, which a runbook cannot provide. They may receive
   data only through the `file_input` path under rules 2–4: metadata-only
   collection (never titles/bodies), redaction, and untrusted-data fences.
4. **External inference stays, under the enforced controls; a local or
   self-hosted model was evaluated and deferred.** With metadata-only
   collection, no user-authored free text crosses the trust boundary; what
   remains (issue numbers, labels, timestamps, public URLs) is already public
   in this repository. Moving inference in-house (self-hosted runners, local
   model) is a larger infrastructure change with no current risk reduction.
   The fail-closed consequence: any future prompt that genuinely needs
   user-authored free text must not use external inference — drop it or run
   it against a local model, and do not add it to the `ALLOWLIST`.
