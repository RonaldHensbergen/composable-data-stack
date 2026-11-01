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
3. **Deterministic pre-inference redaction for `file_input`.** Data passed
   via `file_input` is user-controlled (issue titles/bodies). Workflows must
   scrub it before inference — `biweekly-issue-audit.yml` runs
   `scripts/redact_secrets.py` over the issues JSON (secret-pattern
   replacement with `[REDACTED]`, control-char stripping) so only redacted
   content crosses the trust boundary. External inference is acceptable only
   with this sanitization/minimization step; the question of moving to a
   local/self-hosted model entirely is tracked in issue #1082.

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
