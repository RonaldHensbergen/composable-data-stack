# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog.

## [Unreleased]

### Added

- Added a CRA release-evidence manifest and fail-closed conformity
  readiness gate: `scripts/build_cra_release_evidence_manifest.py`
  references (not duplicates) existing scope-decision, risk-assessment,
  technical-documentation, SBOM, test-result, vulnerability-status,
  support, user-instruction, and signature/provenance evidence into one
  JSON document, always disabled (`conformityClaimEnabled: false`, no
  automation can flip it), attached as a permanent GitHub Release asset
  alongside the existing SBOM/release-inventory evidence.
  `scripts/check_cra_release_gate.py` is a no-op unless that flag is
  explicitly set, in which case it fails closed on any missing evidence.
  Added `docs/cra-release-evidence-gate.md` (product classification
  review against CRA Annex III/IV, the Annex VIII Part I
  conformity-assessment route, and CE-marking/declaration explanation)
  and draft-only, clearly-marked-invalid Annex II/V/VI templates
  (`docs/cra-annex-ii-user-information-template.md`,
  `docs/cra-annex-v-eu-declaration-of-conformity-template.md`,
  `docs/cra-annex-vi-simplified-declaration-template.md`). None of this
  asserts CRA conformity or CE marking for CDS today (#733).

- Added a shared `lineage-sink` contract (`shared/contracts/lineage-sink.yaml`)
  for vendor-neutral OpenLineage-compatible data-lineage backends, mirroring
  the existing `log-sink` pattern. The `dagster` module gained an optional
  `consumes` entry (`spec.config.lineageSink`) wiring `OPENLINEAGE_URL`,
  `OPENLINEAGE_ENDPOINT`, `OPENLINEAGE_API_KEY`, and `OPENLINEAGE_NAMESPACE`
  into all three Dagster services when bound, with no change for existing
  profiles that leave it unbound. Added a new experimental reference
  provider module, `modules-experimental/observability/marquez`, and a demo
  profile, `profiles/local-dagster-postgres-marquez`, verifying the wiring
  end-to-end against a live Marquez backend. See `docs/observability.md`
  section 11 (#779).

- Added `docs/cra-risk-assessment.md` (a product-lifecycle risk register
  plus a full Annex I Part I/Part II traceability matrix mapping CDS's
  design, release, and maintenance controls to the Cyber Resilience Act's
  essential requirements) and `docs/cra-technical-documentation.md` (an
  Annex VII technical-documentation index pointing each required point at
  the existing CDS artifact that would serve as evidence). Added
  `tests/test_cra_traceability.py`, a CI check that every Annex I item
  appears exactly once with a valid, non-empty disposition (implemented
  evidence, a tracked gap, or a justified non-applicability) (#732).

- Added `docs/fds-alignment.md`, mapping CDS's `provides`/`consumes`
  contract model against the Federatief Datastelsel (FDS) Afsprakenstelsel's
  data-provider basisafspraken (technical, semantic, legal, organisational
  agreement domains). Concludes the technical/organisational domains are
  reasonably served by existing contracts and the compliance report/audit
  trail. See `docs/roadmap.md` (#773).

- Added a shared `open-data-provider` contract
  (`shared/contracts/open-data-provider.yaml`), closing the semantic/legal
  gap identified by `docs/fds-alignment.md`: it expresses FDS-required
  dataset provider metadata (`datasetId`, `title`, `description`,
  `classification`, `accessConditions`, `licence`) in a profile-portable,
  machine-checkable way, distinct from existing service-connection
  contracts (`sql-database`, `cache-service`). The `postgres` module
  demonstrates providing it via a new, opt-in `config.dataProvider` block
  (empty/unset by default, so existing profiles are unaffected) (#825).

- Published `docs/common-ground-alignment.md`, mapping CDS's
  module/contract/profile model to Common Ground/Haven reference
  architecture concepts, and added the `local-haven-reference` profile
  combining the `postgres`, `keycloak`, and `traefik` modules in a
  Haven-aligned identity/TLS-ingress/database shape; listed under
  Experimental Components in `docs/roadmap.md` until Keycloak gains realm
  configuration (#680) and an `oidc-provider` contract (#681) (#772).

- Added a local, append-only audit trail of rendered/applied stacks:
  `validate`, `render`, `up`, and `test` now append a JSON Lines entry to
  `.cds/audit-log.jsonl` recording the timestamp, command, profile,
  environment, resolved module versions/image references, and
  success/failure/interrupted outcome. Only secret *alias* names are ever
  recorded (never values), following the existing `${CDS_*}` placeholder
  convention. Disable it per-invocation with `CDS_AUDIT_LOG_DISABLE=1` or
  per-project with `cds config set audit.enabled false`. Distinct from
  `cds get`'s `.cds/get-manifest.json`, which only tracks file provenance.
  See `docs/audit-log.md` (#737).

- Added `docs/cli-reference.md`, a standalone full command/flag reference
  for every `cds` subcommand (including `cds report`, which had been
  missing from the README's command table since its addition in #734),
  moved out of `README.md`'s `## ⚙️ CLI` section to keep the README
  focused on onboarding/quickstart content. The README section is now a
  short pointer to the new doc.

- Added a `cds compose-profile` command that merges a new module instance
  into an existing profile instead of requiring a hand-edited
  `profile.yaml`: it resolves the new module's `consumes` entries against
  contracts already `provides`d by the profile's existing modules,
  auto-binding (and adding the matching `dependsOn`) when exactly one
  candidate matches a consume entry's contract kind, and reporting an
  error naming the candidates when a consume entry has zero or multiple
  matches so the ambiguity can be resolved with `--bind
  <name>=<moduleId>.<providedContract>`. `--set <config.path>=<value>`
  sets any other config field, and `--secret <alias>=<ENV_VAR>` defines a
  `secrets.<alias>` reference the new module's config ends up using.
  Prints the merged profile to stdout by default; `--write` persists it
  back to the resolved `profile.yaml`, and `--output <path>` writes it
  elsewhere (e.g. before handing it to `cds generate-profile`).
  `--add-module <source>` is written the same way as existing
  `spec.modules[].source` entries in the target `profile.yaml` (relative
  to the profile's own directory, not the repository root, unless
  `CDS_MODULE_PATH` is set). This is the follow-up to #349 noted in
  `docs/roadmap.md`'s Near-Term section ("beyond `cds generate-profile`")
  (#807).

### Fixed

- Fixed the `marquez-web` healthcheck in the experimental
  `modules-experimental/observability/marquez` module always reporting
  `unhealthy` under `cds up`: it shelled out to `bash -c '... /dev/tcp ...'`,
  but the `marquez-web` image is BusyBox/Alpine-based and has no `bash`
  binary. Switched to BusyBox-compatible `wget --spider` (#831).

- `cli/renderer.py`'s short-form string volume rewriting (`"./a/b:/target"`)
  now re-adds a `./` prefix when it rewrites a relative bind-mount source to
  a multi-segment path (e.g. a project-root-relative fallback). Compose's
  short volume syntax only treats a source starting with `.`, `/`, or `~`
  as a bind mount; a bare rewritten path like `traefik/dynamic` was
  otherwise parsed as a named volume reference, breaking `docker compose
  config`/`cds up` for the `traefik` module's dynamic-config and
  certificate bind mounts, surfaced by the new `local-haven-reference`
  profile being the first to exercise them (#772).

- `local-haven-reference`'s `init-db.sh` wrapped its `GRANT ALL
  PRIVILEGES ON DATABASE ... TO ...` statement in a `DO $do$ ... $do$;`
  block, but `psql`'s `:'var'` variable interpolation does not apply
  inside dollar-quoted string literals, so the literal `:'identity_db'`
  text was sent to Postgres and broke container startup
  (`ERROR: syntax error at or near ":"`, `postgres` exiting on first
  boot). Replaced it with the `SELECT format(...) \gexec` pattern used by
  every other profile's `init-db.sh` (#772).

- `cds compose-profile --write` now refuses to run on a profile that
  declares its own top-level `extends:` entry: resolving `extends`
  flattens the parent/child chain, so writing the merged result back to
  `profile.yaml` would silently discard the `extends` reference and
  duplicate the parent's modules on disk. The command now errors and
  points at `--output <path>` instead, which still works since it writes
  the merged document elsewhere rather than overwriting the source (#808).

## [0.11.0] - 2026-10-04

### Added

- Extended the security rule-set with two NIS2/Cyberbeveiligingswet
  operator-readiness checks and a dedicated report mode: `CDS-SEC-080`
  flags a profile with a durable data store (`sql-database`/
  `file-database` provider) that has no `backup-target` contract consumer
  bound anywhere (a known limitation until a backup-capable module
  exists, tracked by #210/#665/#668/#669); `CDS-SEC-081` flags an
  admin-facing service (e.g. Superset, Dagster's webserver) when no
  identity/auth module (e.g. `modules/identity/keycloak`) is present in
  the profile at all. Both are code-enforced (`scope: ["none"]`,
  `codeEnforced: true`), tagged with a new `business-continuity`/
  `access-control` compliance category, and participate in
  `cds security --report nis2`, a new report mode that groups findings by
  NIS2 Article 21(2) measure letter (a)-(j) instead of by raw compliance
  category. See `docs/security-rules.md` and the updated
  `docs/nis2-cyberbeveiligingswet-scope.md` gap table (#774).

- Added `docs/nis2-cyberbeveiligingswet-scope.md`, recording that CDS
  itself is not an "essential"/"important" entity under NIS2/the
  Cyberbeveiligingswet — that role falls on operators of profiles built
  with CDS — and mapping each NIS2 Article 21(2) risk-management measure
  category to existing CDS evidence (SBOM, rule-set compliance categories,
  image signing) or an open gap, cross-referenced against the CRA scope
  decision so the two regimes aren't conflated (#771).

- Added a `cds report` command that exports a compliance/evidence report
  for a rendered stack: resolved module list (id/source/version/
  dependsOn), linked signature/SBOM/provenance evidence per image (looked
  up from the signed-images fixture, degrading gracefully with a `W101`
  warning for locally-built or unlinkable images instead of failing), the
  contract/topology graph, and a secret-leak check (`E119`) confirming no
  declared secret value renders literally into Compose output instead of
  a `${CDS_*}` placeholder. Supports human-readable text (default) or
  `--json`, and `--output`/`-o` to save to a file; diagnostics are always
  printed to stderr so `--json` output stays parseable. See
  `docs/compliance-report.md`, which also carries the required disclaimer
  that this is readiness evidence, not a legal compliance/conformity
  certification (#734).

- Added an opt-in pinned image digest staleness check: `cds validate`/`cds
  up --check-image-digests` (or `CDS_CHECK_IMAGE_DIGESTS=1`) compares each
  module's digest-pinned image (e.g. `postgres:18@sha256:...`) against the
  digest the registry currently publishes for that same tag and emits a
  stable `W100` warning when they differ. Off by default so `validate`/
  `render`/`up` stay network-free, and any lookup failure (offline, auth,
  unsupported registry) is silently skipped rather than failing the
  command (#736).

- Added true in-memory/dict-based profile planning entry points, closing the remaining gap in #349: `cli.overlay.resolve_extends_from_profile()`/`resolve_profile_from_profile()` and `cli.planner.build_plan_from_profile()`/`plan_generated_profile()` let a runtime-generated profile be validated and planned directly from a dict -- same `extends`/environment-overlay semantics and module `source:` resolution as the disk-based `resolve_extends()`/`resolve_profile()`/`build_plan()`, anchored to a directory that does not need to contain a `profile.yaml` of its own -- without ever writing it to disk first (#679).

- Added a persisted `target` project default via `cds config set target
  compose|helm`: `validate`/`render`/`up`/`down`/`test`/`state`/`security`
  now fall back to the configured value (and then to `compose`) whenever
  their `--target` flag is omitted, so the Helm target no longer needs to
  be repeated on every invocation. An explicit `--target` flag still
  takes precedence, and `cds config unset target` reverts to the
  built-in `compose` default (#811).

### Removed

- Removed the stale `docs/plan/CRA_and_other_laws.md`, a duplicate of
  `docs/plan/cra-and-nl-law-sequencing.md` left behind when two in-flight
  PRs independently modified the pre-rename and post-rename filenames;
  the surviving sequencing doc's statuses are refreshed to reflect
  #729/#734/#736/#771 now being resolved and #774 no longer blocked.

### Fixed

- Bumped the `urllib3` transitive dependency pinned in `uv.lock` from
  `2.7.0` to `2.8.0`, resolving three Dependabot-reported advisories: an
  HTTPS proxy TLS configuration bypass, an unbounded chunk-size-line
  memory buffer in `HTTPResponse.stream()`/`read_chunked()`, and a
  chunked-deflate decompression infinite loop.
- Bumped `dagster-postgres` from `0.29.24` to `0.29.25` in
  `images/dagster/requirements-postgres.txt` to match the Renovate-bumped
  `dagster`/`dagster-graphql`/`dagster-webserver` `1.13.25` release train;
  the mismatched pin made `pip-audit`'s dependency resolution fail with
  `ResolutionImpossible` (#799).
- Reverted the `sqlalchemy` upper-bound pin in
  `images/dagster/requirements-postgres.txt` from `<2.2` back to `<2.1`
  after a Renovate bump to `<2.2` allowed SQLAlchemy 2.1.x to resolve,
  which defaults bare `postgresql://` URLs to the psycopg3 driver instead
  of psycopg2 and crashed `dagster-webserver`/`dagster-daemon` at startup
  with `ModuleNotFoundError: No module named 'psycopg'` (#781).

- Hardened `ifNonempty:` interpolation in `cli/planner.py` and
  `cli/renderer.py` against malformed expressions, so a wrong comma count
  now leaves the placeholder unresolved for the existing `E071` check
  instead of crashing with `ValueError`, without changing any well-formed
  expression (#557).
  
- Required `cds generate-profile` names to be a single relative path
  segment, so multi-segment names like `teams/checkout` now fail closed with
  `E115` instead of producing a profile invisible to `cds list profiles`,
  without changing any accepted name (#678).
  
- Rejected `cds get --local` combined with an explicit `--ref`, which was
  previously accepted silently while ignoring `--ref`, so the combination
  now fails closed with a `GetError` matching the documented mutual
  exclusivity, without changing `--local` with the default ref (#506).
  
- Wrapped malformed compose YAML parse errors in
  `scripts/compose_to_module.py` in `ScaffoldError`, so a bad
  `docker-compose.yml` input now fails with a clean `error:` message naming
  the file and the parser error instead of an unhandled `yaml.YAMLError`
  traceback, without changing any accepted input (#686).

- Fixed `cds init` printing the literal string "None" in its summary
  message (e.g. "Initialized environment for None.") when the profile is
  resolved via `CDS_PROFILE_PATH` or the saved `cds use`/`cds config`
  default rather than passed as a CLI argument; it now reports the
  actually-resolved profile name, matching the `cds preflight` hint on the
  following line.

- Fixed the vault profile's `init-db.sh` bootstrap, which failed on a
  fresh database: passed the four missing `psql` variables, made all
  three roles config-driven (including the previously uncreated Dagster
  role), dropped the redundant hardcoded `analytics` role, and replaced
  the `DO $do$` block (where `psql` interpolation does not apply) with
  `SELECT format(...) \gexec` grants, without changing any database,
  role, or privilege that gets created (#836).

### Changed

- Added a Renovate `packageRule` disabling further updates to the
  `sqlalchemy` pin in `images/dagster/requirements-postgres.txt` so it
  cannot be widened past `<2.1` again until `dagster-postgres` supports
  the psycopg3 driver (#781).

- Included the discovered top-level entries in the `cds get` unexpected
  tarball-layout error, so a malformed GitHub archive now reports what was
  found instead of only the expected shape, without changing any accepted
  archive (#507).

## [0.10.0] - 2026-09-27

### Added

- Added [`docs/cra-incident-runbook.md`](docs/cra-incident-runbook.md), an
  operational runbook for actively exploited vulnerabilities and severe
  security incidents covering the decision tree, 24-hour/72-hour/14-day/
  one-month deadlines, evidence preservation, the ENISA Single Reporting
  Platform split between internal and submitted evidence, third-party
  upstream coordination, user notification, and a tabletop exercise result.
  Extended `SECURITY.md`'s coordinated vulnerability disclosure policy with
  monitored-contact/intake ownership, severity triage and embargo handling,
  and advisory publication, and cross-linked `RELEASE.md`'s checklist to
  the runbook's final-report deadlines (#729).

- Added an informational `complianceCategory` field to every rule in
  `cli/resources/rule-set.json` (validated by a new closed enum in
  `cli/resources/rule-schema.json`), mapping each security finding onto a
  compliance control category (e.g. `access-control`, `secrets-management`,
  `network-exposure`, `encryption-in-transit`, `configuration-management`,
  `system-hardening`, `logging-monitoring`, `patching`) so findings can be
  organized against a user's own risk assessment (e.g. NIS2/
  Cyberbeveiligingswet). `cds security` and `cds test` gained repeatable
  `--category` filtering and a `--group-by-category` flag; this is
  additive metadata only and does not change which rules run or their
  pass/fail outcome. See `docs/security-compliance-categories.md` for the
  category set, rationale, and an explicit disclaimer that this is a
  readiness aid, not a compliance certification (#735). Findings from
  `--target=helm` Kubernetes checks and `--verify-images` image
  verification, which aren't declared in `rule-set.json`, are tagged with
  `system-hardening`/`configuration-management` and `patching` respectively
  so they participate in `--category`/`--group-by-category` like any other
  finding (#735).
- Added an authoritative security support period and update policy
  (`docs/security-support-policy.md`): only the latest tagged release is
  supported, support ends 14 days after being superseded, and
  Critical/High severity fixes get an emergency out-of-band release.
  Every GitHub release now carries a machine-generated `## Support`
  section (`scripts/render_support_notice.py`), verified before
  publishing by `scripts/check_release_notes_support.py`. `SECURITY.md`,
  `SUPPORT.md`, `RELEASE.md`, `docs/release-strategy.md`,
  `docs/support-policy.md`, and `docs/cra-scope-decision.md` now cross-link
  to it instead of separately describing (or omitting) support terms.
  `SECURITY.md` now also defines the Critical/High/Medium/Low severity
  scale referenced by the delivery targets (#731).
- Added a `cds security` rule (`CDS-SEC-074`) that flags production profiles
  exposing a plaintext HTTP endpoint (a module providing an `http-service`
  contract with `protocol: http`) that is host-published on a non-loopback
  address without a wired TLS reverse-proxy in front of it. Fronting by a
  `reverse-proxy` contract with `protocol: https` does not suppress the
  finding if the backend independently publishes its own port, since that
  remains directly reachable, bypassing the proxy. Profiles that intentionally
  accept plaintext exposure can set
  `spec.security.waivers.plaintextEndpointExposure.reason` (a required,
  non-blank string) to downgrade the finding to a `W098` warning instead
  (#576).

### Fixed

- Pinned `sqlalchemy<2.1` in `images/dagster/requirements-postgres.txt`.
  SQLAlchemy 2.1 changed the default DBAPI for bare `postgresql://` URLs
  from `psycopg2` to `psycopg` (v3), which isn't installed alongside the
  Postgres storage adapter's `psycopg2-binary` dependency, so
  `dagster-webserver`/`dagster-daemon` crashed at startup with
  `ModuleNotFoundError: No module named 'psycopg'` once pip resolved the
  newest SQLAlchemy transitively (#754).
- Retried the `publish-dockerhub` job's post-publish `cosign verify`/
  `cosign verify-attestation` self-checks with backoff. Docker Hub's
  OCI referrers/attestations endpoint is only eventually consistent, so a
  verify call made immediately after `cosign attest` could transiently see
  only a subset of the just-pushed attestations and fail the whole
  `Publish Signed Images` job even though signing/attesting succeeded
  (#760).

## [0.9.1] - 2026-09-18

### Fixed

- Remediated CVE-2026-89161 and other fixed Debian package vulnerabilities in
  the Dagster base image by applying security upgrades during the runtime
  build. Scheduled image scans and signed-image fixture refreshes now track
  base and hardened variants independently instead of scanning the same
  digest twice (#718, #719).
- Replaced the SHA-1 branch-to-port hash used by the k3d local-dev harness
  (`scripts/k8s/`) with SHA-256 (#698).
- Hardened `helm`/`kubectl` invocations against CLI-supplied argument and
  path issues flagged by SonarCloud: resolved CWE-88/CWE-22 risks in
  CLI-supplied args and paths (#700), satisfied taint tracking for k8s
  name/context validation (#701), resolved `--chart-dir` to an absolute path
  before use in the `helm` command (#702), and validated the
  `helm`/`kubectl` `--timeout` value before building the command argument
  (#705).
- Clarified `SECURITY.md`'s "Supported Versions" statement to mention the
  14-day Critical/High-severity grace period for the immediately prior
  release, matching what `docs/security-support-policy.md` §4 already
  specifies, instead of the unqualified "only the latest release is
  supported" claim (#731).

## [0.9.0] - 2026-09-12

### Removed

- Removed `jinja2` from the CLI package's runtime dependencies; it was only ever imported by `images/dagster/generate_config.py`, a Docker build-time script, and is now installed explicitly in `images/dagster/requirements.txt` instead. Added a new `test` extra (and updated `Makefile`, `CONTRIBUTING.md`, and CI) since the test suite still exercises `generate_config.py` directly (#470).

### Added

- Added a compatibility registry (`cli/resources/compatibility-registry.json` + `compatibility-registry.schema.json`) strengthening contract compatibility validation beyond plain `kind` matching: `validate_contract_bindings` now looks up each consumer/provider pairing (by `<category>/<name>` module identity) and reports a new `E043` error if the pairing is explicitly recorded as `unsupported`, even when the contract `kind` matches structurally. A pairing with no registry entry is unaffected (only structural `kind` matching applies); entries recorded as `tested` document known-good combinations, seeded here with the three pairings `profiles/local-dagster-postgres-superset/profile.yaml` already exercises in CI (#350).
- Added a new `identity` module category and its first module, Keycloak (`modules/identity/keycloak/`): an identity/SSO provider running in development mode (`start-dev`), consuming a `sql-database` contract for its own metadata store and providing an `http-service` contract. Declares `productionSuitable: false`; realm configuration and an `oidc-provider` contract for other modules to consume are tracked as follow-ups (#680, #681) (#370).
- Added a Kubernetes runtime target: `cds render`/`cds validate`/`cds security`/`cds test`/`cds up`/`cds down`/`cds state` now accept `--target helm` alongside the existing Docker Compose target. `cli/k8s_renderer.py` renders the resolved plan as a Helm chart (Secrets, ConfigMaps, Deployments/StatefulSets and PVCs, release-scoped Services), `cli/k8s_security.py` runs Kubernetes-specific security checks (effective per-container root/non-root posture), and `cli/k8s_runner.py` provides bounded `helm upgrade --install` plus `kubectl wait`/`rollout status` lifecycle operations. Includes a sibling-safe, per-worktree k3d local-dev harness (`scripts/k8s/`, `make k3d-*`) with an isolated k3s CI proof workflow, a TenderNed procurement-data Superset/Dagster analytics demo wired through the new target, and `docs/kubernetes.md` (#608).
- Added `scripts/ai_profile_review.py`, an optional AI-assisted guardrail/simplification review for CDS profiles: it runs `cds validate`/`cds plan` and then asks an LLM to flag repository-convention violations and simplification opportunities not already covered by schema/contract validation, seeing only profile YAML and the resolved plan (secrets are always placeholders, never resolved values). Supports `--json` and `--dry-run`, and a vendored single-seam LLM client (`scripts/_vendor/llm/`) with three explicit providers (`copilot_cli`, `azure_openai`, `ollama`) and no silent fallback (#652).
- Added `scripts/compose_to_module.py`, a scaffolding tool that automates the mechanical parts of `docs/from-docker-to-cds-profile.md`: it converts an existing `docker-compose.yml` into a starter `module.yaml`, lifting ports/environment into a `configSchema`, replacing literal values with `${config.*}` placeholders, and detecting secret references, hardcoded connection strings, and dependencies on other compose services (flagging well-known infra images for binding to an existing shared contract and provider module instead of being scaffolded anew) (#670).
- Added `config.image.registry` (`dockerhub` default | `ghcr`) to the `orchestration/dagster` and `bi/superset` module schemas, so `config.image.source: registry` can pull the same signed image `publish-images.yml` already publishes to GHCR, not just Docker Hub; existing profiles are unaffected since the default keeps them pointed at Docker Hub (#613).
- Promoted the dbt transformation module from `modules-experimental/` to
  `modules/transformation/`, with production-suitable hardening and
  PostgreSQL/DuckDB warehouse support (#594).
- Added regression tests for planner default materialization in nested `configSchema` structures: array-item object defaults filled in per-item without overwriting explicitly provided sibling properties, and partially provided nested objects preserving explicit falsy values (`False`/`0`) while still materializing omitted siblings (#459).
- Added CLI-level test coverage asserting `cds validate` reports precise diagnostic codes and data paths for common validation failures: a module entry missing a required field (`E010`) and a consume binding with an unresolvable `contractRef` (`E041`) (#460).
- Added `cli.loader.save_generated_profile()` and `cli.main.generate_profile()` so a runtime/programmatically composed profile can be persisted at its normal `profiles/<name>/profile.yaml` location (honoring `CDS_PROFILE_PATH`), then handed to the existing `validate_profile()`/`build_plan()` entry points completely unchanged -- same relative module-source resolution and `extends`/environment-overlay semantics as any hand-authored profile. Refuses to write outside the profiles root or silently overwrite an existing profile without `force=True`. Exposed as a new `cds generate-profile <file>` CLI command (reads JSON/YAML from a file path or `-` for stdin, with `--name`/`--force` options) (#349).
- Added `test_fetch_profile_rejects_dockerfile_copy_traversal_escaping_source_repo`, a regression test proving a Dockerfile `COPY`/`ADD` source containing `..` that `Path.glob()` matches outside the source repository is rejected as a stable `GetError` (via the existing `_add_copy_action` guard from #454), not an unhandled `ValueError` (#475).
- Pinned `build`, `twine`, and `yamllint`'s CI-installed versions, and the `renovate` npm package version used by `renovate-config-validator`, matching this repo's existing exact-pin convention for CI-only tooling (e.g. `ruff==0.16.6`), addressing SonarCloud's `githubactions:S8544` findings triaged in #622. Added matching Renovate custom managers so these pins stay up to date automatically.

### Fixed

- Set the Docker Hub short description for every published image (`dagster`, `superset`, `dbt`, `dlt`) via `peter-evans/dockerhub-description`'s `short-description` input, instead of relying on it being set manually per repository. `dbt` and `dlt` were missing it entirely since their Docker Hub repositories were auto-created by CI without ever going through that manual step.
- Fixed a quadratic (super-linear) regex backtracking hazard in `cli/preflight.py`'s `_ENV_REFERENCE` pattern, used to scan rendered Compose YAML for `${VAR...}` references: an unterminated reference could make the identifier and suffix capture groups' overlapping character classes retry every possible split point. Required the suffix group to start with one of its actual delimiters (`:`, `?`, `-`), making the two groups' character classes disjoint, flagged by SonarCloud as `python:S8786`.
- `images/superset/init.sh` now uses `[[ ... ]]` instead of `[ ... ]` for its conditional tests, addressing SonarCloud's `shelldre:S7688` findings triaged in #622.

### Changed

- Raised the `coverage`-enforced `cli/` coverage gate from 65% to 80%, matching actual measured coverage and the industry norm for a security-focused tool (`pyproject.toml`'s `[tool.coverage.report]` `fail_under`) (#471).

## [0.8.0] - 2026-09-04

### Added

- `cds list profiles`/`cds list modules`/`cds list images` now accept `--remote <owner/repo>`, `--ref <ref>`, and `--local <dir>`, reusing `cds get`'s own source-repository resolution so users can discover what's available in a remote repository or existing local checkout before running `cds get` against it (#500).

### Fixed

- `cds preflight`'s image supply-chain check now honors a saved `security.strict` project default, forcing the production image policy (registry allowlist, digest pins, signature/provenance verification) regardless of the profile's inferred environment class, matching the existing behavior of `cds security --verify-images` (#546).

## [0.7.0] - 2026-08-28

### Added

- Added a `cds config` subcommand family (`get`/`set`/`unset`/`list`) to manage persisted project-level defaults in `.cds/config.json` (or `CDS_CONFIG_PATH`), generalizing the existing single-purpose `cds use` command. Supported keys are `profile`, `environment`, and `security.strict`; a saved `environment` default is applied whenever `--environment` is omitted on any command that already accepts it, with an explicit `--environment` flag always taking precedence (#383, #537).
- Added the core rendering mechanism for `image.source: build|registry`: `modules/orchestration/dagster/module.yaml` gained `image.source` (default `build`) and `image.tag` config fields, and `cli/renderer.py` can now conditionally swap a service's `build:` key for a registry `image:` reference derived from the naming scheme already used by `publish-images.yml`, composing with the existing `image.variant` (`--hardened`) field (#532, #536).
- `publish-images.yml`'s Docker Hub publish job now signs, SBOM-attests, and SLSA-provenance-attests pushed images with the same keyless OIDC identity as the existing GHCR job, closing the supply-chain-guarantee gap between the two registries; `docs/image-signing.md` documents the shared trust identity (#275, #539).

## [0.6.0] - 2026-08-25

### Added

- Added a `--hardened` CLI flag to `cds up`, `cds render`, and `cds test`, which overrides `config.image.variant` to `hardened` for any module whose configSchema exposes an `image.variant` property (currently only `modules/orchestration/dagster`) before planning, so users no longer need to hand-edit their profile YAML to select the Alpine-hardened Dagster image build (#373).

### Changed

- Expanded the `ruff` lint scope in `pyproject.toml` from pyupgrade-only (`UP`) to also include pyflakes, bugbear, bandit, and isort (`F`, `B`, `S`, `I`), and fixed or annotated (`# noqa`) every finding surfaced by the wider scope across `cli/`, `tests/`, and `workdirs/`. This also uncovered and fixed a dormant bug in `tests/test_module_isolation.py`: `setUpClass` read a module file handle after it had already been closed, so `cls.modules` was always empty and `test_no_cross_module_service_references` silently never executed its assertions (#497, #498, #499).

### Fixed

- `cli/renderer.py` no longer allows a module template's pure `${config.*}`/`${bindings.*}` substitution to splice a profile-supplied dict/list verbatim into compose-dangerous service fields (`command`, `entrypoint`, `environment`, `volumes`, `cap_add`, `security_opt`, `ports`, and similar). This closes a compose-injection path where an untrusted profile could smuggle arbitrary command args, environment variables, or host bind mounts through module config; such templates now fail rendering with a new `E072` diagnostic.
- `cds get` no longer writes fetched files or the tracking manifest through a pre-planted symlink at the destination path. `_find_conflicts` now treats any symlink destination (including a dangling one, which `Path.exists()` reports as absent) as a conflict, and the copy/manifest-write steps unlink any symlink at the destination before writing, so a symlink can no longer be used to redirect fetched content onto an arbitrary path outside the destination tree (#474).

## [0.5.2] - 2026-08-25

### Changed

- `find_project_root()`/`resolve_project_root()` in `cli/main.py` now check for a `.cds` directory (CDS's own state marker, created by `cds get`/`cds use`) at each ancestor level, alongside the existing `pyproject.toml`/`.git` markers. A `.cds`-marked working directory is recognized as the project root immediately, instead of being shadowed by an unrelated ancestor repository (e.g. a dotfiles repo at `$HOME`) further up the tree (#512).
- `build-python-package.yml` (reused by `testpypi.yml`/`pypi.yml`) now also runs a full-stack install smoke test: it installs the built wheel with no source checkout on `CDS_PROFILE_PATH`/`CDS_MODULE_PATH`, fetches a profile via `cds get --local`, initializes it with `cds init`, and brings the full docker compose stack up, confirming every service reports healthy before the package is published (#512).

### Fixed

- `cds get` no longer discards a malformed or unreadable `.cds/get-manifest.json` tracking manifest silently. Invalid JSON or a non-object root is backed up alongside the original file and reported with a `WARNING` naming the reason and the backup path; a manifest that cannot even be read is reported without attempting a doomed backup copy (#495).

## [0.5.1] - 2026-08-24

### Changed

- `cds get` now downloads a profile and its module/runtime assets from GitHub by default (via the tarball API) instead of copying from a local checkout. Use `--local <dir>` to opt back into the previous local-directory behavior; `--remote <owner/repo>` and `--ref <branch|tag|sha>` select a specific fork/revision to download (#493).

## [0.5.0] - 2026-08-24

### Added

- Profile, module, and shared-contract JSON schemas are now loaded and enforced at runtime: profile shape validation is backed by `cli/resources/profile.schema.json` (E010), loaded module definitions by `cli/resources/module.schema.json` (E021), and standalone contract files in `shared/contracts/` can be checked against `cli/resources/contract.schema.json` via `cli.validator.validate_contract_file()` (#413).

### Changed

- Schema-backed validation is stricter than the previous hand-written checks. Profiles must now carry `metadata.environment`, `spec.runtime`, and per-module `version`/`enabled`; loaded modules must satisfy `module.schema.json`; profiles that previously validated may now fail (#413).

### Removed

- Deleted the unused rule-set entries CDS-SEC-050/051/052/053/054 from `cli/resources/rule-set.json` (#354). Image policy enforcement lives solely in `cli/image_verification.py` (findings are still reported as CDS-SEC-050/051/052), and CDS-SEC-053 had no enforcement anywhere.
- Disabled the CDS-SEC-006 and CDS-SEC-032 rule-set entries (`enabled: false`) so no `scope: ["none"]` rule appears active; #356 and #357 track the remaining work on those rules.

### Fixed

- `cds security --verify-images` no longer plans and renders the profile a second time for image verification; it reuses the compose the security scan already rendered (#336).
- `cds-dagster` and `cds-dbt` now pin the rebuilt `python:3.14-slim` base digest (`a7fb1e63...`) and add `.trivyignore` exceptions (exp 2026-11-15) for CVE-2026-53615 (`util-linux` libblkid, #455, #457) and the four sqlparse advisories from 2026-08-17 (CVE-2026-54284/59893/59894/71491). Neither fix is shippable yet: `2.41.5-0+deb13u1` has no base digest carrying it, and dbt-core 1.12.2 pins `sqlparse<0.6.0`. Expired exceptions fail the daily scan again.
- Corrected stale documentation that referenced the deleted CDS-SEC-050/051/052/053/054 rule-set entries: `docs/image-signing.md`, `docs/threat-model.md`, `docs/vm-postgres-odbc-access.md`, and the `cli/image_verification.py` module docstring.
- Added a regression guard for #354: the image-policy finding IDs CDS-SEC-050/051/052 must still be emitted by `cli/image_verification.py` for a non-compliant Compose fixture, and none of the deleted IDs may reappear in the rule set.
- Added a regression guard for #355: no `scope: ["none"]` security rule may be enabled in the bundled rule set.
- Added a regression guard for #397: a `CDS_DB_PASSWORD` reference with a fallback value outside CDS-SEC-040's literal list is still caught by preflight insecure-default detection.
- `image-security-scan.yml`'s scheduled scan step never passed a `trivyignores` input to `trivy-action`, and `publish-images.yml`'s pre-push gate passed it under the wrong input name (`ignorefile` instead of `trivyignores`); both silently ignored `.trivyignore`, so the approved CVE-2026-53612/53613/53614 exceptions added in #484 never took effect and the daily scan kept refiling duplicate issues (#481, #482, #483, #485, #486, #487).

## [0.4.0] - 2026-08-11

### Added

- Provider-neutral observability architecture foundations for #174: `docs/observability.md`, the shared `log-sink` contract, the structured-event schema, and optional profile `spec.observability.logShipping` validation.
- `publish-images.yml` now runs a trivy HIGH/CRITICAL vulnerability gate on
  the locally built images before pushing or signing, so a CVE disclosed
  after the PR-time scan blocks publication to GitHub Container Registry and
  Docker Hub (#274).

## [0.4.0-beta-1] - 2026-07-31

### Added

- `ruff` (`select = ["UP"]`) now lints for pyupgrade/deprecation issues in CI and pre-commit, alongside a dedicated test-suite deprecation gate (`scripts/run_tests_with_deprecation_gate.py`) that promotes `DeprecationWarning`s raised from repo-owned `cli`/`test_*` modules to errors while leaving third-party dependency warnings alone.
- `renovate.json`'s custom managers now validate with `renovate-config-validator --strict` in CI, and migrate `fileMatch`/`matchPackagePatterns` to the current `managerFilePatterns`/`matchPackageNames` syntax.
- `cli/planner.py`'s `build_plan()` now reports a diagnostic instead of crashing when `spec.modules` is a non-list scalar.

### Fixed

- CDS-SEC-031 no longer flags the conventional, `.gitignore`-covered project-root `.env` file as a security finding; it still flags nested/non-root `.env` files.
- Removed the redundant `wheel` entry from `build-system.requires` (modern `setuptools` builds wheels natively via PEP 517/660).
- Corrected stale documentation: `README.md`'s description of `cds test`'s security-stage gating, `docs/support-policy.md`'s minimum Python version (3.14+, not 3.11+), and the bug report issue template's Python version placeholder.

## [0.3.0-beta-1] - 2026-07-27

### Added

- `cds up` now supports `--no-build` to skip Docker Compose image builds when images are already available.
- Python distributions now include the CLI security rules and complete PyPI metadata.
- CI builds and smoke-tests the wheel outside the source tree, and maintainers can publish validated artifacts to TestPyPI through trusted publishing.
- `cds --version`/`-v` now reports the installed CLI version.
- The release workflow now verifies that a pushed `vX.Y.Z` tag matches the version declared in `pyproject.toml` before publishing a GitHub release, failing fast on drift.

### Changed

- `cds up` now runs `docker compose build` before `docker compose up` by default.
- Security validation loads its default rules from package resources instead of requiring a repository-root `security/` directory.
- CI now measures test coverage on the Ubuntu leg of the test matrix and fails the build if `cli/` coverage drops below 65%.
- Superset initialization now synchronizes roles and permissions after migrations and admin provisioning, preventing authenticated API requests from failing with `403` responses.
- Dagster services now communicate with the user-code gRPC server through a shared Unix-domain socket volume instead of an internal TCP port.

### Security

- The Superset image now upgrades its inherited `uv` and `uvx` binaries to `uv 0.11.26`, which embeds the patched `quinn-proto 0.11.15`.
- `images/dagster/Dockerfile` now pins its base image to a digest (not just the `python:3.14-slim` tag), runs as a non-root `dagster` user, contains only required application files, installs PostgreSQL support only for PostgreSQL builds, and no longer installs packages at startup. Dagster services now drop all Linux capabilities, prevent privilege escalation, use read-only root filesystems, and no longer expose the unused Docker socket.
- The Superset image now pins `apache/superset:6.1.0` to a digest and installs its entrypoint with immutable permissions. Superset services also drop all Linux capabilities, prevent privilege escalation, and use a read-only root filesystem with restricted temporary filesystems.
- PostgreSQL, KeyDB, and Vault images are now digest-pinned and run as their upstream non-root users with read-only roots, no Linux capabilities, no privilege escalation, bounded process counts, restricted temporary filesystems, and host ports bound to loopback only.
- Dagster now uses its writable application home for user state, disables telemetry, and performs lightweight bounded TCP health probes so hardened services start reliably without accumulating stuck healthcheck processes.
- Dagster user-code definitions can now be supplied through a configurable read-only bind mount, allowing code overrides before container startup while retaining an immutable root filesystem.
- Module `source:` paths are now required to resolve inside an allowed `modules/`- or `modules-experimental/`-rooted directory before the module file is read, for both `cds validate`/`cds plan`/`cds render` (`cli/loader.py`'s `resolve_module_file`) and the `CDS_MODULE_PATH` override path. Fixes [GHSA-jgg5-4wcm-fvxq](https://github.com/RonaldHensbergen/composable-data-stack/security/advisories/GHSA-jgg5-4wcm-fvxq): a profile's `source:` field could previously traverse outside the intended module tree (e.g. `source: "../../../../../../tmp/outside_zone"`) and have its content read and embedded into the rendered `docker-compose.yaml`. Out-of-bounds sources now fail with `E022`.

## [0.1.1] - 2026-06-21

### Added

- Default render output path to project-root docker-compose.yml when no output is provided.
- Open-source project governance and support docs.
- Troubleshooting guidance in the README for common CLI validation, secret, and contract-binding errors.
- Added `docs/os-compatibility.md` with OS compatibility analysis and recommendations.
- Improved the bug report template with severity and minimal repro fields.

### Changed

- Compose rendering now preserves secrets as runtime environment placeholders instead of embedding resolved values.
- Plan secret mapping now stores env variable names rather than secret values.
- Renderer build-context path rewriting now preserves portable relative paths for nested compose output directories.

### Tests

- Added renderer regression coverage to ensure generated Docker Compose output never includes raw secret values.

### Security

- Added explicit security reporting process and secret-handling guidance.
