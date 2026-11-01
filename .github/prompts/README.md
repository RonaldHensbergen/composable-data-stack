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
   the fields the task needs — `biweekly-issue-audit.yml` gathers
   number/title/labels/milestone/timestamps/url and never collects issue
   bodies — and (b) scrub what remains: it runs `scripts/redact_secrets.py`
   over the JSON (secret-pattern replacement with `[REDACTED]`, control-char
   stripping) before inference. External inference is acceptable only with
   both steps; the question of moving to a local/self-hosted model entirely
   is tracked in issue #1082.

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

## Open policy question

Whether prompts that handle repository/issue content should run against a
local or self-hosted model instead of an external provider (so no data
crosses the trust boundary at all) is tracked in issue #1082.
