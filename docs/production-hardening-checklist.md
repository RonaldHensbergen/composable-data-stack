# Production hardening checklist

Version: 0.1
Status: Draft: operator verification baseline for issue
[#72](https://github.com/RonaldHensbergen/composable-data-stack/issues/72).

## 1. Purpose and scope

This document is the operator-facing verification layer of the
production-ready baseline ([#64](https://github.com/RonaldHensbergen/composable-data-stack/issues/64)).
It answers two questions for anyone running a CDS profile in production:

1. **Which hardening controls exist, and who owns them** — CDS enforces some
   controls at compile time (validation, security rules, rendering), while
   others remain the operator's responsibility (host baseline, secret
   provisioning, live posture). Section 2 maps every control to its owner.
2. **How to verify a rendered production profile reproducibly** — a fixed
   command sequence with documented expected output, runnable against the
   reference production profile (`team-dagster-postgres-superset` with its
   `prod` overlay). Section 3 is the procedure; section 4 documents the
   expected findings; section 5 states the CI equivalence.

This document does not own neighboring concerns. It references them and
keeps the boundary:

| Concern | Owner |
| --- | --- |
| Docker host/daemon hardening baseline (HOST-001..006) | [docker-host-hardening.md](docker-host-hardening.md), [rootless-docker.md](rootless-docker.md) ([#211](https://github.com/RonaldHensbergen/composable-data-stack/issues/211)) |
| Security rule catalog and severities | [security-rules.md](security-rules.md) |
| Production vs development web serving | [serving-modes.md](serving-modes.md) ([#216](https://github.com/RonaldHensbergen/composable-data-stack/issues/216)) |
| Image vulnerability scanning and SLA | [image-scanning.md](image-scanning.md) |
| Image signing, SBOM, provenance evidence | [image-signing.md](image-signing.md), [compliance-report.md](compliance-report.md) |
| Backup, restore, disaster-recovery policy | [backup-restore.md](backup-restore.md) ([#210](https://github.com/RonaldHensbergen/composable-data-stack/issues/210)) |
| Runtime confinement and runtime policy | [runtime-confinement.md](runtime-confinement.md) |
| Profile/module schemas and validation codes | [modules.md](modules.md), README diagnostic tables |

## 2. The checklist

Each control lists what CDS enforces, what the operator must do, and the
command that verifies it. "Enforced by CDS" means the `cds` CLI fails closed
(nonzero exit) when the control is violated; an entry under "Operator
responsibility" means CDS can only warn or report, and the human running the
stack owns the outcome.

| # | Control | Enforced by CDS | Operator responsibility | Verify with |
| --- | --- | --- | --- | --- |
| 1 | Profile integrity: shape, module roots, contract compatibility, dependency graph | `cds validate` (error diagnostics stop later stages) | Keep profiles declarative; do not hand-edit rendered output | `cds validate <profile> --environment prod` |
| 2 | Production marker discipline: a `metadata.environment: production` profile must not opt services into development settings | `E139` (explicit modern DB host auth required), `E140` (explicit `webServer.mode: development` rejected) | Do not weaken defaults in production overlays | `cds validate <profile> --environment prod` |
| 3 | Secret hygiene: profiles stay secret-free; only `${CDS_*}` placeholders reach rendered output | Planner/renderer placeholder discipline; `CDS-SEC-001/003/004/012/030/033`; rendered-output leak scan | Provision strong, unique values via `.env` (created `0600`); never commit secret values | `cds init`, `cds security`, report secret-leak check |
| 4 | TLS termination at the edge: reverse-proxy contract fronting HTTP services | `CDS-SEC-074` flags host-published plaintext endpoints | Publish only the proxy; remove backend host publishes, or set `spec.security.waivers.plaintextEndpointExposure.reason` | `cds security` + rendered ports inspection (step 5) |
| 5 | Database and cache authentication | `E139` rejects trust authentication in production; authentication policy checks ([#206](https://github.com/RonaldHensbergen/composable-data-stack/issues/206)) | Provide per-service credentials through secrets, not literals | `cds validate`, `cds security` |
| 6 | Runtime confinement: non-root, capabilities, read-only filesystem, writable paths | Production runtime policy `CDS-SEC-090/091`; waivers must carry a documented reason | Use images that set a non-root `USER`; waive only single checks with justification | `cds security` (waivers surface as `W103`) |
| 7 | Production process management for web entrypoints | `E140`; the `webServer.mode` convention maps to production servers ([#216](https://github.com/RonaldHensbergen/composable-data-stack/issues/216)) | Choose production-capable modules when promoting a profile | `cds validate`, [serving-modes.md](serving-modes.md) |
| 8 | Host and daemon baseline (rootless, userns, seccomp, logging, live restore, socket) | `cds preflight` reports `HOST-001..006` as advisory checks | Harden the host per [docker-host-hardening.md](docker-host-hardening.md) | `cds preflight <profile> --environment prod` |
| 9 | Image supply chain: digest pinning, signature/SBOM/provenance evidence | `CDS-SEC-051` (critical services must be digest-pinned); `cds report` links evidence | Publish signed images and reference them by digest | `cds preflight`, `cds report --hardened` |
| 10 | Image vulnerability management | Publishing pipeline scans every image; exceptions audited (`.trivyignore` audit) | Rebuild or bump within the documented SLA; file exceptions explicitly | [image-scanning.md](image-scanning.md) |
| 11 | Backup and disaster recovery | `spec.backup` policy validation; `backup-target` contract ([#210](https://github.com/RonaldHensbergen/composable-data-stack/issues/210)) | Declare per-workload-class policies; run restore verification at the stated frequency | [backup-restore.md](backup-restore.md) |
| 12 | Admin-UI authentication | `CDS-SEC-081` flags admin-facing services without an identity module | Front admin UIs with an identity provider (identity module work: [#370](https://github.com/RonaldHensbergen/composable-data-stack/issues/370)) | `cds security` |
| 13 | Evidence and audit trail | `cds report` exports the readiness evidence snapshot | Retain the report with the deployment record | `cds report --hardened --output <file>` |

## 3. Operator verification procedure

The procedure is written against the shipped reference production profile:
`team-dagster-postgres-superset` with the `prod` overlay. Substitute your own
profile and overlay as needed; the command shapes are identical. Run every
command from the project root (the directory containing `profiles/`),
because secret loading and path-relative rules assume that layout.

### Step 1: validate the resolved profile

```bash
cds validate team-dagster-postgres-superset --environment prod
```

Expected: `Profile is valid.` (exit 0). Any `E`-code diagnostic is a hard
failure; fix the profile before continuing.

### Step 2: provision runtime secrets

```bash
cds init team-dagster-postgres-superset --environment prod
```

Expected: a project-root `.env` created with owner-only permissions,
containing every `CDS_*` key the resolved profile requires (14 keys for the
reference profile: 13 from the base profile, including `CDS_CACHE_PASSWORD`,
plus the one the prod overlay adds: `CDS_BACKUP_ENCRYPTION_KEY`). Replace
every placeholder with a strong, unique value; `cds preflight` and `cds
security` both fail closed on placeholder or weak values
(`CDS-SEC-004`/`012`). Rendering fails closed (`E081`) while a required
secret key is missing from `.env`; a render with placeholder values still
succeeds, so preflight and security are the gates for weak values.

### Step 3: check runtime prerequisites and host posture

```bash
cds preflight team-dagster-postgres-superset --environment prod
```

Expected output categories:

- `[PASS]` lines for the Docker CLI/Compose/daemon checks and each required
  port.
- `[WARN] host.*` lines for the baseline controls the host currently
  misses, from the `HOST-001..006` baseline (typically `HOST-001` rootless
  daemon, `HOST-002` user namespaces, `HOST-005` live restore; `HOST-004`
  when the daemon logging driver is disabled, and `HOST-006` when the
  daemon socket is exposed without TLS or is world-writable). These are
  operator responsibilities; see
  [docker-host-hardening.md](docker-host-hardening.md).
- `[WARN] environment.placeholders` while `.env` still contains generated
  placeholders.
- `[FAIL]` for port conflicts and for critical services whose images are not
  digest-pinned (`CDS-SEC-051`). Preflight exits nonzero if any check fails.

### Step 4: run the security rule set

```bash
cds security team-dagster-postgres-superset --environment prod
```

The command is fail-closed: it exits 1 when any finding is reported. As of
the snapshot in section 4 the reference profile reports a known set of
findings, so the operator's check is **diff-based**: the findings must match
the documented set in section 4, and any *new* finding is a regression.
`No security findings.` (exit 0) is the target state once the tracked items
land.

### Step 5: render and inspect the exposure surface

```bash
cds render team-dagster-postgres-superset --environment prod --output /tmp/compose.yaml
```

Expected: the render succeeds, the rendered file contains only `${CDS_*}`
secret placeholders, and the only service publishing on all interfaces is
the TLS reverse proxy. Inspection helper:

```bash
python3 - <<'EOF'
import yaml
compose = yaml.safe_load(open("/tmp/compose.yaml"))
for name, svc in sorted(compose["services"].items()):
    print(f"{name:28s} {svc.get('ports', [])}")
EOF
```

Database and cache services should publish loopback-only
(`127.0.0.1:...`); the proxy publishes the TLS entry points. The reference
profile currently still host-publishes the two admin backends — a true
`CDS-SEC-074` finding tracked in section 4.

### Step 6: export the evidence report

```bash
cds report team-dagster-postgres-superset --environment prod \
  --hardened --output /tmp/cds-report.txt
```

Expected: modules, images, contract topology, and the secret-leak check
(`status=pass`). Images without linked signature/SBOM/provenance evidence
produce `W101` warnings; see [image-signing.md](image-signing.md) for what
makes that evidence available.

### Step 7: verify live posture (host with Docker only)

```bash
cds up team-dagster-postgres-superset --environment prod
cds state team-dagster-postgres-superset
```

Expected: `cds state` shows every service healthy; the proxy's TLS entry
points answer (for example `curl -v https://<host>/`) and the backend ports
answer only through the proxy. `cds down` stops the stack.

## 4. Expected findings (snapshot 2026-10-26)

`cds security team-dagster-postgres-superset --environment prod` currently
exits 1 with the following findings. The set is stable; a deviation in either
direction is the operator's signal to stop and investigate.

| Finding | Count | Classification | Action / tracking |
| --- | --- | --- | --- |
| `CDS-SEC-001`/`CDS-SEC-033` on `initDbEnv.*`; `CDS-SEC-003` on `initDbEnv.*` and on the waiver reason | 11 | False positive: the flagged values are renderer references (for example `config.passwordFrom`) or free-text waiver reasons, not secrets; rendered output contains only `${CDS_*}` placeholders | [#979](https://github.com/RonaldHensbergen/composable-data-stack/issues/979) |
| `CDS-SEC-074` on `services.dagster-webserver.ports[0]` and `services.superset.ports[0]` | 2 | True positive: the admin backends still publish directly to the host, allowing a proxy bypass | Remove the backend host publishes (proxy becomes the sole entry point), or set `spec.security.waivers.plaintextEndpointExposure.reason` |
| `CDS-SEC-081` on the admin-facing services | 3 | Known gap: no identity module is bound | [#370](https://github.com/RonaldHensbergen/composable-data-stack/issues/370) |
| `CDS-SEC-080` on `spec.modules` | 1 | Known gap: the backup policy is declared at profile level (`spec.backup`), which the rule does not yet recognize as contract consumption | [backup-restore.md](backup-restore.md) follow-ups ([#210](https://github.com/RonaldHensbergen/composable-data-stack/issues/210)) |
| `CDS-SEC-013` on `backup.encryption.keyFrom` / `backup.encryptionKeyFrom` | 2 | Accepted: one backup encryption key is deliberately referenced by both the module capability and the profile policy | None (single purpose, single key) |
| `W103` on `spec.security.waivers.runtimePolicy` | 1 | Informational: the documented non-root waiver for images that set `USER` in their Dockerfiles | Keep the waiver reason accurate |

Additional `CDS-SEC-004` (placeholder/weak secret) or `CDS-SEC-012` (too
weak or predictable) findings mean the `.env` still holds generated
placeholder or weak values — fix the values, not the profile.

## 5. CI and local equivalence

CI and a local operator run the same code paths, so results are equivalent
by construction for everything below:

- `tests/test_shipped_profiles.py` runs the real validator against **every
  shipped profile and every environment overlay** on each push, so
  `cds validate` locally can never diverge from CI for a committed profile.
- `ci.yml` additionally runs `cds validate` + `cds security` on the stable
  profile and the `cds test` stage bundle (validate, security, plan, render),
  with the documented expected exit codes.
- The local procedure adds what CI intentionally cannot check: the host
  baseline (`cds preflight` `host.*` checks), live runtime posture
  (`cds up` / `cds state`), and the operator's actual secret values.

Run the section 3 commands from the same commit you intend to deploy;
profile, module, and rule definitions all live in the repository, so the
rendered result is reproducible from the commit hash.

## 6. Keeping this document honest

- The section 4 snapshot must be updated whenever a tracked finding resolves
  (a false-positive fix landing, the identity module binding, the backup
  contract consumption) or a new finding class appears. Each change gets a
  `CHANGELOG.md` entry referencing the tracking issue.
- New hardening controls get a row in section 2 as they land, not before:
  the checklist documents tested behavior, not planned behavior.
