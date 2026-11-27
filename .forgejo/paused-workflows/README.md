# Paused workflows (not run on Codeberg)

These two workflows from the GitHub era are **paused** during the Codeberg
migration: their core step uses `actions/ai-inference` (GitHub Models), which
has no equivalent on Forgejo Actions, and no LLM API secret is configured in
the Codeberg repository yet. They live here (Forgejo only reads
`.forgejo/workflows/`, not subdirectories) so they are kept in-repo but
inactive. Re-enable by moving them back into `.forgejo/workflows/` once the
LLM step is replaced (e.g. a generic LLM API call via a repo secret — see
docs/ci-codeberg.md).

- `biweekly-issue-audit.yml` — Biweekly Issue Consistency Audit (AI-assisted)
- `practices-audit.yml` — Biweekly Practices Audit (uses the API for PR/run
  stats; portable via `fj`/`.forgejo/lib/api.sh` once re-enabled)
