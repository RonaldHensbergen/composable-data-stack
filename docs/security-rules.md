# Code-enforced security rules

Most `cli/resources/rule-set.json` rules are evaluated by the declarative
match engine in `cli/security.py` purely from `scope`/`match` conditions.
A small number of rules need Plan/topology-level reasoning the declarative
engine can't express (e.g. "is any contract of a given kind consumed
anywhere in the profile?"), and are instead implemented directly in Python.
These rules use `scope: ["none"]` (so the declarative engine never attempts
to evaluate them -- a scope-none rule can never match) with `enabled: false`
and `codeEnforced: true` as the real on/off toggle. See each rule's
`$comment` in `rule-set.json` for the authoritative detail; this document
summarizes them for readers who aren't grepping the JSON directly.

| Rule | Title | Function |
| --- | --- | --- |
| `CDS-SEC-074` | Production profile exposes plaintext HTTP endpoint without TLS reverse-proxy | `_check_production_plaintext_exposure` |
| `CDS-SEC-080` | Stateful module has no backup/restore target bound | `_check_backup_target_binding` |
| `CDS-SEC-081` | Admin-facing service with no identity/auth module in profile | `_check_admin_service_identity_binding` |
| `CDS-SEC-090` | Service violates the production runtime policy | `_check_runtime_policy` |
| `CDS-SEC-091` | Runtime confinement (seccomp/AppArmor/SELinux) disabled | `_check_runtime_policy` |
| `CDS-SEC-006` | Resolved secret present in rendered Compose output | `_check_rendered_secret_values` |
| `CDS-SEC-030` | Generated file contains resolved secrets | `cli.secret_leak.check_generated_text` (called by `render_compose`/`render_helm`) |
| `CDS-SEC-071` | CLI plan or validate output prints secrets | `cli.secret_leak.RedactingStream` (installed by `cli/main.py`) |

Detailed sections below cover `CDS-SEC-080` and `CDS-SEC-081` (introduced by
[#774](https://github.com/RonaldHensbergen/composable-data-stack/issues/774));
the production runtime policy rules `CDS-SEC-090`/`091` are documented in
[docs/runtime-confinement.md](runtime-confinement.md#production-runtime-policy);
see `rule-set.json`'s `$comment` field on each rule (including the other
`scope: ["none"]` rules not listed above) for the full rationale of every
code-enforced rule.

## CDS-SEC-080: Stateful module has no backup/restore target bound

Flags a profile where at least one module provides a `sql-database` or
`file-database` contract (durable state) but no module in the profile
consumes a `backup-target` contract anywhere.

**Known limitation:** no module in this repository provides a
`backup-target` contract yet (backup/restore support is tracked by
[#210](https://github.com/RonaldHensbergen/composable-data-stack/issues/210),
[#665](https://github.com/RonaldHensbergen/composable-data-stack/issues/665),
[#668](https://github.com/RonaldHensbergen/composable-data-stack/issues/668),
[#669](https://github.com/RonaldHensbergen/composable-data-stack/issues/669),
all not yet started). This rule is therefore expected to fire on
essentially every profile with a durable data store today. That is treated
as honest, accurate signal about a real, currently-unaddressed NIS2
Article 21(2)(c) business-continuity gap, not a bug -- the finding will stop
firing for a given profile once a backup-capable module exists and is wired
into it.

## CDS-SEC-081: Admin-facing service with no identity/auth module in profile

Flags a profile whose rendered Compose includes an admin-ui-type service
(the same heuristic `CDS-SEC-020`/`023` use: a service name containing
`superset`, `dagster-webserver`, or `ui`) when no module in the profile is
sourced from `modules/identity` (or `modules-experimental/identity`), e.g.
`modules/identity/keycloak`.

**Known limitation:** this is a coarse, profile-wide presence check, not a
per-service wiring check. No `identity-broker` contract exists yet (tracked
by [#370](https://github.com/RonaldHensbergen/composable-data-stack/issues/370))
to confirm the identity module actually fronts/protects that specific
admin-facing service -- the rule only confirms that *some* identity/auth
module is present somewhere in the profile.

## CDS-SEC-006 / CDS-SEC-030 / CDS-SEC-071: resolved secrets in output

CDS only ever renders `${CDS_*}` placeholders, so the literal value of a
known secret appearing in output means it was pasted into profile/module
config instead of referenced as `secrets.<alias>`. These three rules look
for those literal values (declared under `spec.secrets.values`, or any
secret-named `CDS_*` variable, of at least 8 characters) on three surfaces:

- `CDS-SEC-006`: the rendered Compose model, reported as a finding by
  `cds security`/`cds test`.
- `CDS-SEC-030`: generated files. `cds render`/`cds up` refuse to write
  `docker-compose.yml` or any Helm chart file containing a secret and fail
  with error `E096`.
- `CDS-SEC-071`: `cds plan`, `validate`, `test` and `security` output has
  secret values replaced with `***REDACTED***`, followed by warning `W102`
  on stderr. `--reveal-secrets` (where offered) disables this.

**Known limitations:** shorter values and values CDS cannot see (not in
`.env`/the environment) are not detected, and neither are transformed
copies such as base64-encoded values. In CLI output, multi-line secrets
are redacted line by line (lines of at least 8 characters).

## CDS-SEC-032: `.env` file readable by group or others

Data-driven (`file-permissions` scope), not listed in the table above.
Flags a `.env` with any group/other permission bit set (POSIX only;
skipped on Windows). Notes:

- Enabled for every profile class, so a default-umask `0644` `.env` is
  flagged; run `chmod 600 .env` (files written by `cds init` are already
  `0600`). Intentionally group-readable setups (e.g. `0640`) are flagged too.
- Filesystems that do not store POSIX modes (WSL `/mnt/c`, some container
  or network mounts) report `0777` and will always trigger the rule; keep
  the `.env` on a native Linux filesystem.

## Reporting

Both rules are tagged with a `complianceCategory`
(`business-continuity` and `access-control` respectively, see
[`docs/security-compliance-categories.md`](security-compliance-categories.md))
and participate in `cds security --group-by-category` and the new
`cds security --report nis2` mode (see
[`docs/nis2-cyberbeveiligingswet-scope.md`](nis2-cyberbeveiligingswet-scope.md)).

---

## Warnings

| Code | Description                                                                                         | Severity |
|------|-----------------------------------------------------------------------------------------------------|----------|
| W096 | Rendered-compose security checks were skipped because the profile could not be planned or rendered. | Warning  |
| W098 | Plaintext endpoint exposure waiver suppressed violations for production profile.                    | Warning  |
| W099 | Plaintext endpoint exposure waiver is set but has no effect (non-production profile).               | Warning  |
| W103 | Runtime policy waiver suppressed violations for production profile.                                 | Warning  |
| W104 | Runtime policy waiver is set but has no effect (non-production profile).                            | Warning  |
