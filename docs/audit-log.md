# Local audit trail of rendered/applied stacks

`validate`, `render`, `up`, and `test` append a structured, local record of
what was rendered or applied for a profile over time: module/image
versions, the command invoked, and its outcome. It covers issue #737.

> **Disclaimer:** This is user-side evidence for your own incident-reporting
> or change-tracking obligations (e.g. 24-hour/72-hour windows under
> NIS2/Cyberbeveiligingswet-style regimes) — **not** CDS telemetry. Nothing
> in the log is sent to CDS or any third party, and CDS never reads it back.
> It is not a legal compliance certification.

This is distinct from `cds get`'s `.cds/get-manifest.json`, which only
tracks file provenance for fetched profile assets, not deployment history.

## Where it's written

By default, entries are appended to `.cds/audit-log.jsonl` under the
current working directory as
[JSON Lines](https://jsonlines.org/) (one JSON object per line), so the
file can be tailed, `grep`ped, or parsed line-by-line without loading the
whole history into memory.

## What's recorded

Each entry is a JSON object with:

| Field | Content |
| --- | --- |
| `timestamp` | UTC ISO 8601 timestamp of the command invocation. |
| `command` | Which command produced the entry: `validate`, `render`, `up`, or `test`. |
| `profile` | The profile argument as passed on the command line. |
| `environment` | The `--environment` overlay used, if any. |
| `outcome` | `success`, `failure`, or `interrupted` (`cds up` stopped with Ctrl-C while the stack keeps running in the background). |
| `modules` | Resolved module `id`, `source`, `version`, and (if present) image reference fields (`repository`/`tag`/`digest`/`variant`) for every module instance in the plan. |
| `secretAliases` | The *names* of secret aliases/env vars the plan referenced (e.g. `CDS_DB_PASSWORD`) — never secret values. |
| `details` | Command-specific extra context (e.g. render target/output path, `cds up`'s Helm release/namespace, `cds test`'s per-stage pass/fail). |

**No secret values ever appear in the log.** `secretAliases` only ever
contains alias/env-var *names*, the same `${CDS_*}` placeholder convention
the planner and renderer already use (see `cli/secrets.py`) — the actual
secret value never passes through the planner in the first place, so there
is nothing to redact.

## Disabling the audit log

Two ways to turn it off:

- For a single invocation or environment (e.g. CI), set
  `CDS_AUDIT_LOG_DISABLE=1` (or `true`/`yes`/`on`).
- For a project, persist the setting with:

  ```sh
  cds config set audit.enabled false
  ```

  Re-enable it with `cds config unset audit.enabled` or
  `cds config set audit.enabled true`.

## Retention and rotation

CDS only appends; it never rotates, truncates, or deletes
`.cds/audit-log.jsonl`. Retention and rotation are left to you, based on
your own regulatory retention requirements — for example:

- Rotate with standard tools (`logrotate`, a scheduled copy-and-truncate
  script, or committing periodic snapshots to a private evidence store).
- Keep `.cds/audit-log.jsonl` out of version control alongside other
  generated artifacts (see `.gitignore`) if you don't want deployment
  history in your repository history, and instead back it up separately if
  you need durable evidence.
