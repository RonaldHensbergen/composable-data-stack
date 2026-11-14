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
| `CDS-SEC-081` | Admin-facing service without identity-provider wiring | `_check_admin_service_identity_binding` |
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
`file-database` contract (durable state) but no backup target is bound
anywhere: neither a module in the profile consuming a `backup-target`
contract, nor a profile-level `spec.backup.target.contractRef` that
resolves to a `backup-target` contract (the planner carries `spec.backup`
through the plan since #1270, so the profile-level wiring counts as
coverage).

The two shipped providers (the postgres module's built-in pgBackRest
capability of issue #665, and the restic file backup module of issue #668)
are wired into a profile through `spec.backup.target.contractRef` or
`backup-source` bindings rather than a `backup-target` consume entry;
since issue #1270 the profile-level `spec.backup.target` wiring is
recognized, so the finding now fires only on profiles that have wired
neither provider. Treat it as honest signal about a real NIS2 Article
21(2)(c) business-continuity gap: the finding stops firing for a given
profile once a backup-capable module exists and is wired into it.

## CDS-SEC-081: Admin-facing service without identity-provider wiring

Flags each admin-ui-type service in the rendered Compose (the same heuristic
`CDS-SEC-020`/`023` use: a service name containing `superset`,
`dagster-webserver`, or `ui`) whose owning module is not wired to an identity
provider (issue #1271). The check is binding-aware per service: a service
counts as auth-delegated when its module consumes an `oidc-provider` (or the
reserved `identity-broker` kind,
[#370](https://github.com/RonaldHensbergen/composable-data-stack/issues/370))
contract -- the planner only emits that consume entry when the profile
actually bound it, e.g. Superset's `identityProvider` referencing
`keycloak.oidc-provider` (available since
[#1064](https://github.com/RonaldHensbergen/composable-data-stack/issues/1064)).
As an additional, coarser profile-wide signal, the presence of any module
sourced from `modules/identity` (or `modules-experimental/identity`), e.g.
`modules/identity/keycloak`, also suppresses the finding for every service.
One-shot init/migration/job containers are excluded: a compose service
whose name ends in `-init`, `-migration`, or `-job` (e.g. `superset-init`)
runs a bounded task and exits, so it is not an exposed admin entry point and
is not classified as admin-ui in the first place (issue
[#1275](https://github.com/RonaldHensbergen/composable-data-stack/issues/1275)).

**Known limitations:** the profile-wide identity-module presence signal can
still mask a service that is not itself wired to the identity module, and the
admin-ui detection is the name-based heuristic above rather than a declared
service property. A service the service->module map cannot attribute fails
closed (it is flagged).

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
