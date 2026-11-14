# Repository review 2026-11-13

Repo-wide review of `main` (`7ae1c36`) done in seven rounds from different
angles: core CLI correctness, security posture, tests and coverage, docs and
conventions, CI/scripts hygiene, module/profile consistency, and
production-readiness completeness. Static review plus targeted runtime
proofs (`scripts/check_profile_matrix.py`, the full unittest suite, and
`cds maturity` on the shipped profiles). No code was changed by this review.

Companion document: [okdp-stackable-uwv-checkin.md](okdp-stackable-uwv-checkin.md)
— the external landscape check-in of the same date (OKDP, Stackable, UWV's
Open Data Analytics Platform) and the lessons drawn from it; its follow-ups
are issues #1281–#1285.

## Executive summary

The repository is in good shape. The compile path is disciplined (no bare
excepts, no `eval`/`exec`, no unsafe `yaml.load`, no `shell=True`; user-facing
failures are structured `Diagnostic`s with stable codes), secrets never reach
plans or rendered output, path handling is confined with
`resolve()` + `is_relative_to()` plus symlink-parent checks, images are
digest-pinned and run as non-root, the Helm renderer emits default-deny
NetworkPolicies, and every workflow is least-privilege with SHA-pinned actions.

Findings, ranked:

| # | Severity | Finding |
| --- | --- | --- |
| F1 | major (local dev experience, not CI) | Unit tests that invoke `main()` in-process are not hermetic against a repo-root `.cds/config.json` / `.env`: with the local `"target": "helm"` saved config, a full suite run from the repo root reports 28 failures. CI is green (clean checkout), but local runs mislead. |
| F2 | minor | No single exhaustive reference of all 119 `E`/`W` diagnostic codes; the README table is "common errors" only. |
| F3 | minor | Four contract kinds used by modules are not documented in `shared/contracts/`: `secrets-provider`, `scheduler`, `metadata-db-client`, `object-store`. |
| F4 | info (completeness) | Production readiness: all observability/lineage/ingestion/gitops modules are still in `modules-experimental/`, so no shipped profile can reach the Observed maturity tier; the best shipped profile (`production-kubernetes-example` with the `prod` overlay) reaches Basic only. There is also no production *compose* profile, only a production-style Helm one. |

## Round 1: core CLI correctness (`cli/`)

Verified clean:

- No bare `except:`, no `eval(`/`exec(`, no `yaml.load` without a safe loader,
  no `shell=True` anywhere in `cli/`, `scripts/`, `tools/`.
- All 11 `except Exception` sites either wrap the failure in a `Diagnostic`
  with a stable code (`cli/main.py:1615`, `cli/main.py:3816`,
  `cli/main.py:4157`, `cli/main.py:4214`, `cli/security.py:1510`) or are
  deliberately fail-open side channels that only log (`cli/main.py:1720`,
  `cli/main.py:1752` redaction must never break the command).
- 119 unique `E`/`W` codes in `cli/`; the 13 codes with more than one message
  literal are code *families* reused for related failures (by design per
  AGENTS.md), not collisions.
- Path confinement: `cli/loader.py` resolves module/profile sources and
  rejects anything outside the allowed roots (`resolve()` +
  `relative_to()`); `cli/confinement.py:137` re-checks the resolved target;
  `cli/security_common.py:ensure_no_symlinked_parent` closes the symlink
  TOCTOU variant for fetched assets (`cli/getter.py`).
- Secrets: planner/renderer only ever emit `${CDS_*}` placeholders;
  `cli/secret_leak.py` scans rendered Compose, generated files and CLI output
  for literal secret values (CDS-SEC-006/030/071); `load_env_file()` refuses
  to take trust settings (`CDS_GET_ALLOWED_SOURCES`) from `.env`.
- Generated files are written atomically (`cli/utils.py:_atomic_write`);
  schema/config nesting is depth-capped and surfaced as a diagnostic
  (`MaxNestingDepthExceeded`, `cli/planner.py:627`).

Finding F1 (details in Round 3): the in-process test contamination described
below.

## Round 2: security posture

Verified clean:

- `cli/resources/rule-set.json`: 35 rules, unique `CDS-SEC-*` ids; the
  code-enforced (`scope: ["none"]`) rules are documented in
  `docs/security-rules.md`, which explicitly defers to the `$comment` fields
  for the declarative rules — by design, not a doc gap.
- All `images/**/Dockerfile*` base images are pinned by
  `tag@sha256:...` and every image ends on a non-root `USER`.
- Helm target: one default-deny NetworkPolicy per module with egress derived
  from contract bindings (`cli/k8s_renderer.py:2847`); `waitFor` gate hosts
  and ports are validated against that policy (E152/E163); unsafe certificate
  alias mounts fail closed (E165/E166).
- `cds get` restricts remotes via an allowlist, validates commit pins, checks
  for symlinked parents, and only talks to the fixed HTTPS GitHub API hosts
  (annotated `# nosec B310`).
- Workflows: every file in `.github/workflows/` has a `permissions:` block and
  all third-party actions are pinned to 40-char SHAs.
- Prompts: `tests/test_prompts_lint.py` (62 tests) pins the known bypass
  classes (quoted values, `--flag=value`, command substitution, `--jq`,
  `gh api`, computed output flags, missing field lists).
- No live credentials in tracked files (only redaction-test fixtures);
  `.env` is untracked and mode 600; `certs/traefik/` contains only a README.

## Round 3: tests and coverage

- 140 test modules, 2688 tests; coverage gate `fail_under = 80` scoped to
  `cli/` (`pyproject.toml`); golden SHA-256 hashes guard the three rendered
  Helm charts (`tests/golden/k8s-chart*.sha256`).
- `scripts/check_profile_matrix.py` run locally: all 12 shipped profiles (and
  environment overlays) validate, plan and render on both targets — green.
- Full suite run locally: `Ran 2688 tests ... FAILED (failures=28,
  skipped=25)`. The 25 skips are environment-gated (Docker smoke, integration
  URIs, SQLAlchemy) and documented in `local-test-skips.md`.

F1 — the 28 failures are a test-hermeticity problem, not a code regression:

- They concentrate in `tests/test_main.py` (`up`, `state`, `test`, `render`
  paths), `tests/test_environment_cli.py` and `tests/test_secret_output_scanning.py`.
- Cause: those tests call `main()` in-process with the repo root as CWD, so
  `load_saved_target()` picks up the local `.cds/config.json`
  (`"target": "helm"`) and the repo-root `.env` is loaded by `load_env_file()`.
  The `state` test, for example, receives `kubectl ... get` instead of
  `docker compose ... ps`.
- Proof: re-running the same three modules from a clean CWD passes (the only
  2 failures there are artifacts of the changed CWD itself, e.g. the default
  output path); neutralizing `cli.main._read_config` from the repo root also
  removes the target-related failures. CI is unaffected because checkouts
  contain no `.cds/config.json`.
- Recommendation: make the `main()`-invoking tests hermetic (run them with a
  temp CWD or patch `cli.main._read_config`/`load_env_file`), and refresh
  `local-test-skips.md`, which was generated on an old PR branch and does not
  mention this failure class.

## Round 4: docs and conventions

Verified clean:

- `CHANGELOG.md` `[Unreleased]` is actively maintained with issue-referenced
  entries in the house style.
- Every CDS document (`module.yaml`, `profile.yaml`, `shared/contracts/*.yaml`)
  carries a `yaml-language-server` schema comment; only non-CDS app config
  files (e.g. `collector.yaml`, grafana provisioning) omit it, correctly.
- Zero broken relative Markdown links across `docs/`, `README.md`,
  `CONTRIBUTING.md`, `AGENTS.md` (checked programmatically).
- The README troubleshooting table renders as one contiguous table (the #1247
  fix is in place).

F2 — minor: the README table is explicitly "Common errors"; 55 of the 119
codes used in `cli/` have no row anywhere in the README, and
`docs/cli-reference.md` does not enumerate them either. A generated
"all diagnostic codes" table (e.g. a `scripts/` generator like
`check_contract_coverage.py`) would close the gap.

## Round 5: CI, workflows and helper scripts

- 22 workflows cover lint, tests + coverage, profile matrix, SonarCloud,
  image scan/sign/publish, k8s e2e, mutation tests, contract coverage, roadmap
  freshness, trivyignore audit, branch hygiene, agent-approval policy
  (`scripts/check_agent_policy.py`), auto-merge and release flows.
- All `tools/` helper scripts named in AGENTS.md exist and match their
  documented behavior; Makefile targets referenced by AGENTS.md are all
  present.

## Round 6: module and profile consistency

- 21 modules (11 in `modules/`, 10 in `modules-experimental/`), 16 shared
  contract kinds, 12 profiles. Checked programmatically:
  - every `provides`/`consumes` `contract.kind` matches a known kind except
    the four in F3 below;
  - every profile `contractRef` resolves to an existing module id and a
    contract that module actually provides;
  - every profile module `source` exists on disk;
  - compose service names are unique within each module and across modules;
  - interpolation vocabulary is consistent with AGENTS.md:
    `${config.*}`, `${bindings.*}`, `${service.host}`, `${service.dns}`,
    `${service.services.<name>}` and `${ifNonempty:*}` are all handled by
    planner + renderer; `${k8s.*}` and `${item.*}` by `cli/k8s_renderer.py`;
    bare `${CDS_*}`/`${VAR}` pass through to Compose runtime resolution
    intentionally; leftover `${config|bindings|service}.*` expressions are
    treated as rendering bugs (`cli/renderer.py:1518`).
- `productionSuitable` defaults to `true` in the schema; every unsuitable
  module (all of `modules-experimental/`, plus vault and garage) explicitly
  sets `false`. Consistent.

F3 — minor: `secrets-provider` (vault), `scheduler`, `metadata-db-client` and
`object-store` (airflow) are used as contract kinds but have no definition
file in `shared/contracts/` (the schema allows any `^[a-z0-9-]+$` kind).
Either document them there or mark them explicitly as module-internal kinds.

## Round 7: production-readiness completeness of the whole stack

Capability coverage of the production tree (`modules/`):

| Capability | Module | Status |
| --- | --- | --- |
| Warehouse | `postgres`, `duckdb` | production |
| Orchestration | `dagster` | production; `airflow` experimental |
| Transformation | `dbt` | production; `dlt` ingestion experimental |
| BI | `superset` | production |
| Cache | `keydb` | production |
| Identity | `keycloak` | production (compose + helm) |
| Secrets | `vault` | `productionSuitable: false` |
| Storage | `garage` | `productionSuitable: false` |
| Backup | `restic`, postgres pgBackRest sidecar (helm, #1266) | production; `object-storage` target experimental |
| Reverse proxy / mesh | `traefik`, `istio` | production |
| Observability | loki, otel-collector, grafana, marquez | all experimental |
| GitOps | `fluxcd` | experimental |
| Warehouse operator | `postgres-operator` | experimental (k8s) |

Maturity tiers (`cds maturity`, run on the shipped profiles):

- `production-kubernetes-example` + `prod` overlay: highest tier **Basic**
  (database, cache, backup, mesh bound; no identity/TLS in that profile).
- `local-haven-reference`: database, identity (keycloak), TLS (traefik) and
  backup bound, but no cache, so "Not yet Basic" in the `local` environment.
- No shipped profile reaches **Automated**, because the **Observed** tier
  requires a log/metrics/trace sink and all of those modules are still
  experimental. F4: promoting the observability stack (or at least one
  metrics/log sink) is the single biggest lever for production completeness;
  after that, identity+TLS in the production example would unlock Automated
  and Secured. There is also no production *compose* profile — production
  posture is only shipped as the Helm example (enforced by the E133-E139
  production checks for `metadata.environment: production`).

## What was executed as proof

- `python scripts/check_profile_matrix.py --quiet` — all shipped profiles
  validate and render (exit 0).
- Full suite via `python scripts/run_tests_with_deprecation_gate.py` —
  2688 tests; 28 failures fully explained by F1 (local `.cds/config.json`),
  25 environment-gated skips.
- `cds maturity` on `production-kubernetes-example` (prod), `local-haven-reference`,
  `team-dagster-postgres-superset`, `local-dagster-postgres-superset-vault`.
- Static sweeps listed per round above (grep-based scans plus small Python
  consistency checks over all `module.yaml`/`profile.yaml` files).

Docker-dependent runtime proofs were not run (no Docker daemon in this
environment); those tests skip locally and run in CI.

## Suggested follow-ups (filed as issues on 2026-11-13)

1. F1 → [#1277](https://github.com/RonaldHensbergen/composable-data-stack/issues/1277)
   — make `main()`-invoking tests hermetic against project-local
   `.cds/config.json` / `.env`; refresh `local-test-skips.md`.
2. F2 → [#1278](https://github.com/RonaldHensbergen/composable-data-stack/issues/1278)
   — generated exhaustive diagnostic-code table (`docs/diagnostic-codes.md`).
3. F3 → [#1279](https://github.com/RonaldHensbergen/composable-data-stack/issues/1279)
   — document or mark internal the four ad-hoc contract kinds.
4. F4 → [#1280](https://github.com/RonaldHensbergen/composable-data-stack/issues/1280)
   — observability promotion to `modules/` is the gate for the
   Observed/Secured maturity tiers; consider adding identity + TLS to the
   production example profile to demonstrate Automated.
