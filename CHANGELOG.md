# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog.

## [Unreleased]

### Added

- New best-practice doc [docs/repo-layout.md](docs/repo-layout.md): the
  recommended repository layout separating platform assets (modules,
  profiles) from project assets (Dagster pipelines, dbt/dlt code, database
  scripts), with the supported setups from a single checkout to a separate
  project repo wired through `CDS_PROFILE_PATH` / `CDS_MODULE_PATH` and
  `hostPath` mounts.
- Recommended standard vocabulary for the `open-data-provider` contract's
  `classification` field (issue #845): CDS now recommends the DCAT-AP-NL data
  themes — the EU data-theme authority list
  (`http://publications.europa.eu/resource/authority/data-theme`: AGRI, ECON,
  EDUC, ENER, ENVI, GEOJ, GOVE, HEAH, INTR, JUST, REGI, SOCI, TRAN), given as
  the bare code or the full authority URI. `cli/planner.py`'s new
  `validate_open_data_classification` warns W131 at plan time when a provided
  `open-data-provider` contract carries any other non-empty classification,
  without breaking existing free-text profiles (warning only, still renders).
  Documented in `shared/contracts/open-data-provider.yaml`, the postgres
  module's `config.dataProvider.classification`, `docs/fds-alignment.md`, and
  the README troubleshooting table (W131).
- Declarative OIDC client provisioning for the keycloak module (issue
  #1074): `config.oidcClients` accepts a list of clients (`clientId`,
  `redirectUris`, optional `webOrigins`/`standardFlowEnabled`/`roles`/
  `secretFrom`), validated by the module config schema. The Compose renderer
  builds the realm-import JSON from `config.realm` + `config.oidcClients` and
  injects it as the `CDS_OIDC_REALM_IMPORT` environment variable, which the
  keycloak entrypoint writes to `/opt/keycloak/data/import` on startup of a
  non-master realm. Client secrets stay profile secret aliases rendered as
  `${CDS_*}` runtime placeholders — never embedded in rendered output
  (CDS-SEC-030/E078 keep passing) — and malformed entries (plan-file
  bypass) fail closed with E160. Consumers such as Superset reference the
  provisioned client via their existing `identityProvider`
  `clientId`/`clientSecretFrom` config. Documented in the keycloak and
  Superset READMEs and the local-haven-reference README.
- CI now exercises the full shipped profile matrix, not just
  `local-dagster-postgres-superset` (issue #1208). Cheap tier, every PR and
  no Docker: the new `profile-matrix` job in `.github/workflows/ci.yml` runs
  `scripts/check_profile_matrix.py` (`make profile-matrix`), which runs the
  same code paths as `cds validate` / `cds render` over every
  `profiles/*/profile.yaml` and every `profiles/*/environments/*.yaml`
  overlay — schema + planner validation, then a compose render, plus a Helm
  render where the target is defined (a module without a
  `implementation.kubernetes` block, E072, or a compose-only capability
  like backup, E142, means "not defined" and is skipped; any other error
  fails). Guards keep the matrix from shrinking silently: every combo must
  define at least one target, the Kubernetes example profiles must render
  to Helm, and the superset/vault variants must render to compose. The same
  check runs in the unit-test job via `tests/test_profile_matrix.py` on all
  CI operating systems. Smoke tier: the docker-smoke-test workflow gains a
  `tests/test_compose_profile_lifecycle_smoke.py` step running the full
  `render -> build -> up --wait -> cds state -> cds down` lifecycle per
  compose profile (default `CDS_SMOKE_LIFECYCLE_PROFILES`:
  `local-dagster-postgres-superset,local-dagster-postgres-superset-vault`).
  The k3d e2e on the Kubernetes example profiles remains the helm-tier
  runtime gate.
- Plan-time validation of `service-mesh` enrollments (issue #1179): every
  `config.services[].name` of a module providing the `service-mesh` contract
  must name a module instance id of the profile, checked against the planned
  module ids in `cli/planner.py` (`validate_service_mesh_enrollments`). An
  unknown id -- a typo such as `warehose` for `warehouse`, or a disabled
  module -- fails closed with E159 naming the enrollment and listing the
  planned ids, because the rendered per-service `AuthorizationPolicy` would
  otherwise select zero pods via the `cds.dev/module` label (a silent no-op
  allow policy). Documented in the README troubleshooting table (E159),
  `modules/security/istio/README.md`, and `docs/kubernetes.md`.
- Helm rendering now warns with W132 when a known service-mesh enrollment
  names a module that produces no workload pods, including manifest-only
  modules and modules whose workloads are all disabled by `enabledFrom`
  (issue #1198). The check uses the workloads actually rendered for the Helm
  chart and points to the enrollment; documented in the README troubleshooting
  table, `modules/security/istio/README.md`, and `docs/kubernetes.md`.
- Promoted the Istio service-mesh provider to `modules/security/istio/` with
  `productionSuitable: true`, and enrolled Postgres, Dagster, and Superset in
  the production-style and local Kubernetes example profiles. The k3d E2E
  harness now installs the out-of-band Istio control plane and verifies the
  rendered `PeerAuthentication` resource; the per-service
  `AuthorizationPolicy` selectors from #1170 remain explicitly scoped to each
  enrolled service. (#1171)
- Optional, provider-neutral `spec.dataSharing` profile block (issue #844,
  closing the Organisational row of `docs/fds-alignment.md`): a profile can
  declare the datasets it discloses as a data provider (`datasets[].datasetId`,
  optionally tied to a module instance via `datasets[].module`) and link the
  declaration to an external registration (`registration.system` +
  `registration.reference`, both maintained outside CDS). Shape is validated
  by `cli/resources/profile.schema.json` (E010); cross-reference rules are
  E157 (duplicate `datasetId`) and E158 (`module` naming a non-existent
  enabled instance) in `cli/validator.py`. The declaration is carried into
  the plan and surfaced in `cds report` under `dataSharing` (enriched with
  whether the named module advertises an `open-data-provider` contract).
  CDS only records and reports the declaration — it never performs, verifies,
  or claims any registration. Existing profiles without the block are
  unaffected. Documented in `docs/fds-alignment.md`,
  `docs/compliance-report.md`, `docs/cli-reference.md`, and the README
  troubleshooting table (E157/E158).
- Unscoped custom ingress allow rules are recorded by the Kubernetes
  security scan (issue #1030): a `networkPolicy.ingress` passthrough rule
  without a source restriction (no `from` key, an empty `from` list, or an
  `ipBlock` peer) admits sources no contract binding can name -- all sources
  when `from` is absent, equally or more exposed than the
  NodePort/LoadBalancer service CDS-K8S-007 flags -- so `cds security` now
  reports every such rule as a low CDS-K8S-010 finding
  (configuration-management), mirroring the CDS-K8S-008 egress shape.
  Selector-scoped (`namespaceSelector`/`podSelector`) ingress rules stay
  clean. (The issue text says CDS-K8S-009, but that code had already been
  taken on `main` by declared-external-egress breadth, so the new rule is
  CDS-K8S-010.)
- New `service-mesh` shared contract (`shared/contracts/service-mesh.yaml`)
  and experimental reference provider `modules-experimental/security/istio`
  (issue #776, Haven parity workstream 3.4): vendor-neutral contract for
  mutual TLS and traffic policy between a profile's named module services
  (`mtlsMode`, enrolled `services`, optional per-service
  `authorizationPolicy`), validated by `cli/validator.py` like every shared
  contract. The Istio module is gated to `--target helm` only
  (`targets: [kubernetes]`; `--target compose` fails closed with E070) and
  renders a namespace-wide `PeerAuthentication` (STRICT mTLS by default)
  plus an optional `AuthorizationPolicy` (`config.authorization`) via
  `implementation.kubernetes.manifests`. A profile enrolls an existing
  module by naming its instance id in `config.services` — no consumer-side
  changes; the module advertises the resolved mesh on the contract, which
  `cds maturity` already counts as the service-mesh leg of the Secured tier.
  The Istio control plane is declared under
  `implementation.kubernetes.operators`, so render/test warn with W130 and
  `cds up --target helm` fails closed with E155 while its CRD is absent.
  Not wired into any stable profile. Documented in
  `modules-experimental/security/istio/README.md`, `docs/kubernetes.md`
  ("Service mesh"), `docs/zero-trust-architecture.md`, `docs/roadmap.md`,
  and `docs/haven-parity-plan.md` (workstream 3.4 / section 4 status table).
- Per-service `AuthorizationPolicy` selectors in the Istio reference module,
  via a new `forEach` marker on raw `implementation.kubernetes.manifests`
  entries (issue #1170): when the marker's config path expression resolves
  to a list (e.g. `config.services`), the Helm renderer emits one manifest
  copy per element with the element bound as `item` (`${item.name}` etc.),
  suffixing each copy's template file name with its resolved
  `metadata.name`. Misuse fails closed: a non-string marker is E145, an
  expression that resolves to nothing or to a non-list, and duplicate
  resolved names, are E156; a leftover `${item.*}` is E071. The istio module
  now renders `config.authorization` as one `AuthorizationPolicy` per
  enrolled service (named `mesh-allow-<service>`, selected onto that
  service's pods via `spec.selector.matchLabels` on `cds.dev/module`)
  instead of a single namespace-wide policy. Documented in
  `modules-experimental/security/istio/README.md`, `docs/kubernetes.md`,
  `docs/modules.md`, the `service-mesh` contract, and the README
  troubleshooting table (E156).

- New experimental module `modules-experimental/warehouse/postgres-operator`
  (issue #768): a Kubernetes-native, operator-managed PostgreSQL for the
  `--target helm` render target, backed by CloudNativePG. It provides the
  same `sql-database` contract as `modules/warehouse/postgres` (which stays
  untouched as the Compose/local-dev path), so consumers bind with no
  consumer-side changes. The module renders a CloudNativePG `Cluster` CR
  (instances, digest-pinned `imageName`, PVC size, `initdb` bootstrap,
  `pg_hba` from `config.authentication.hostAuth`, and `inheritedMetadata`
  labels so binding-derived NetworkPolicy edges select the operator pods)
  plus the app-credentials `Secret` the bootstrap reads. The advertised
  endpoint is the operator-created `<cluster>-rw` Service, declared via the
  new `implementation.kubernetes.services` block. Demoed by the new
  `profiles/local-kubernetes-postgres-operator` profile (Superset + KeyDB
  binding via the unchanged `sql-database` contract);
  `cds test local-kubernetes-postgres-operator --target helm` passes and
  `--target compose` fails closed with E070. Documented in
  `docs/modules.md` ("Choosing a warehouse module"), `docs/kubernetes.md`
  ("Operator-managed modules"), `docs/roadmap.md`, and
  `docs/haven-parity-plan.md` section 4 (workstream 3.6 now implemented,
  experimental).

- The Helm renderer learned two module-schema blocks for operator-managed
  modules (issue #768): `implementation.kubernetes.services` declares
  Services that a module's manifests (or the operator they drive) create
  without CDS rendering them — the names join the renderer's service
  registry, so E084/E150 contract-host checks, release-scoping of consumer
  binding hosts, `${k8s.service.*}`, and `waitFor` gates treat them like
  rendered workload Services; and a raw `kubernetes.manifests` value that is
  exactly one secret reference (`secrets.*` → `${CDS_*}`) is rewritten to a
  Helm `required` lookup of `.Values.secrets` (the key is registered in the
  chart's secrets Secret), so CRs can consume secrets without the committed
  chart ever carrying them — any other leftover `${CDS_*}` still fails
  closed with E078.

- Out-of-band operator prerequisites are now surfaced in-product (issue
  #768): a module can declare `implementation.kubernetes.operators` entries
  (`name`, `crd`, `install`, optional `docs`) — used by the postgres-operator
  module for CloudNativePG. `cds render`/`cds test`/`cds up --target helm`
  warn with **W130** naming the install command, the chart's `NOTES.txt`
  repeats it, and `cds up --target helm` fails closed with **E155** when
  `kubectl get crd <crd>` confirms the operator's CRD is absent from the
  cluster (instead of a bare helm "no matches for kind" failure); when the
  check itself cannot run it warns and continues. New codes W130/E155 are in
  the README troubleshooting table.
- Provider-neutral HTTP routing contract wiring for reverse proxies (issue
  #883): a reverse-proxy module (e.g. `modules/integration/traefik`) now
  declares a list-valued `consumes` entry (`mappedFrom: spec.config.routes`)
  of kind `http-service`, and each `config.routes` entry binds the routed
  service with `contractRef: <module-id>.<contract-name>` instead of a
  `target` module id. The planner resolves every route's binding into the
  plan's `consumes` map, `cds validate` checks each ref like any other
  binding (E041/E042 at a per-route path), and the compose renderer
  generates the Traefik file-provider document
  (`traefik/dynamic/cds-routes-<module>.yml`) from those resolved bindings
  -- the backend host/port/serviceName come from the bound contract, so no
  service name is hardcoded and no hand-written routing file is needed.
  Route entries compose a public rule from an optional `host` and an
  optional `pathPrefix` (at least one required; the bound `http-service`
  contract may advertise a default `pathPrefix`), rendered as `Host(...)`,
  `PathPrefix(...)`, or both joined with `&&`; invalid rules, duplicate
  rules, unknown entrypoints, and undeclared certificate aliases still fail
  closed with E136, and the helm target derives the same route targets as
  NetworkPolicy edges from the `contractRef` (W129).
  `shared/contracts/http-service.yaml` documents the consume-side routing
  flow and the new optional `pathPrefix` field;
  `shared/contracts/reverse-proxy.yaml` documents that providers route
  bound `http-service` contracts. `profiles/local-haven-reference` now
  routes its TLS entrypoint to Keycloak via
  `contractRef: keycloak.http-service` and the static
  `traefik/dynamic/keycloak.yml` is deleted; `modules/identity/keycloak`
  advertises the in-network container port (`config.containerPort`, default
  8080) as the http-service backend port instead of the localhost host
  publish, so a generated route dials the port the container actually
  listens on. Validator/planner/renderer/k8s tests and docs updated.
- New experimental `grafana` observability module
  (`modules-experimental/observability/grafana/`, issue #369): a Grafana
  dashboarding UI providing the `http-service` contract (health path
  `/api/health`), with optional `sql-database` and `metrics-sink` bindings
  provisioned as datasources via Grafana declarative provisioning
  (`provisioning/datasources/*.yaml`, gated per binding with `enabledFrom`
  equality checks and fed from `${ifNonempty:bindings....,,}` env vars). The
  warehouse binding is engine-guarded (`engineFrom`, E154 for non-Postgres
  providers, issue #682); the metrics datasource is only provisioned for
  `protocol: prometheus` providers. Admin credentials come from
  `secrets.<alias>` only, the UI is published on the host loopback only, and
  analytics/update checks are disabled (no external egress declared). Comes
  with the `local-postgres-grafana` demo profile, a module README, and
  `tests/test_grafana_module.py` covering schema, contract wiring, render
  gating, the E154 guard, and the container-hardening baseline.
- Two new Gate 3 tasks in the Kubernetes/Haven MVP runbook
  (`docs/plan/k8s-haven-mvp.md`): G3.8 proves the GitOps handoff end to end —
  render the Haven-shaped profile, commit the rendered chart to a
  `gitops-target` repository, and verify Flux reconciles and corrects drift
  (issue #1136) — and G3.9 proves workload SQL/py code runs from a Git
  checkout via the bind mounts (`config.project.hostPath` for dbt,
  `definitionsFile.hostPath`/`sharedData.hostPath` for Dagster, issue #1137).
  The executable companion `docs/plan/k8s-haven-gate-checklist.md` is now
  committed alongside, with both tasks as rows; Gate 3 exit criteria updated.
- Enforced database-engine match for `sql-database` bindings (issue #682): a
  `consumes` entry can now declare `engineFrom: spec.config.<path>` naming a
  string field in the consumer's own config whose value is the engine it
  expects the bound provider to speak (e.g. keycloak's
  `metadataDatabase.driver`, which feeds `KC_DB`). At `cds validate` the
  referenced value — from the profile config when set, otherwise from the
  `configSchema` default at that path, so a defaulted driver is enforced too —
  is compared case-insensitively with the provider contract spec's advertised
  `engine` (a literal, or a pure `${config.<path>}` reference against the
  producer's config). A mismatch, or a provider advertising no `engine` at
  all, fails closed with the new `E154`; an unresolvable `engineFrom` path
  fails with `E041`. `modules/warehouse/postgres` now advertises
  `engine: postgres` on all four `sql-database` provides, and
  `modules/identity/keycloak` declares
  `engineFrom: spec.config.metadataDatabase.driver` on its `metadata-database`
  consume entry, so binding Keycloak to a non-Postgres `sql-database` provider
  now fails validation instead of silently misconfiguring `KC_DB`. The
  `engine` field is added to `shared/contracts/sql-database.yaml`, the
  `engineFrom` field to `cli/resources/module.schema.json`, and the README
  troubleshooting table, `docs/modules.md`, and `docs/architecture.md` are
  updated; unit tests in `tests/test_validator.py` and end-to-end guard tests
  in `tests/test_keycloak_postgres_wiring.py` cover match, mismatch, missing
  advertisement, config-reference, schema-default, and backward-compat cases.

- Declared external egress for helm-target workloads under the generated
  default-deny NetworkPolicies (issue #1023): modules can now declare
  egress to destinations outside the plan via
  `implementation.kubernetes.egress` -- a list of entries with `host` (IP
  literal, rendered as a /32 or /128 ipBlock) or `cidr`, optional `except`
  CIDRs, `port`/`ports`, and `protocol` (default TCP) -- or scoped to one
  workload via `implementation.kubernetes.workloads.<name>.egress`, which
  renders an additional workload-scoped NetworkPolicy selecting that
  workload's `app.kubernetes.io/name`. Entries render as ipBlock rules on
  the module's release-scoped NetworkPolicy allowing exactly the declared
  destination/ports; undeclared external egress remains denied (the
  security baseline, now stated explicitly in `docs/kubernetes.md`).
  Values support `${config.*}` substitution so a profile can supply the
  destination through module config. Malformed entries (DNS-name host,
  CIDR with host bits, `except` outside the CIDR, bad port/protocol,
  unresolved expression) fail closed with the new E153 diagnostic, and the
  k8s security scan records every declared entry as a CDS-K8S-009 finding
  whose severity scales with breadth (0.0.0.0/0 on all ports = high).
  The E152 wait-gate check now also accepts a gate whose host:port a
  declared entry (or a raw `networkPolicy.egress` ipBlock rule) covers,
  and its message points at `implementation.kubernetes.egress`. Module
  schema (`cli/resources/module.schema.json`), renderer, scan, README
  troubleshooting table, and tests included.
- New `sampleData` config block on the dagster module (issue #1117):
  `sampleData.enabled` (default `false`) ships the bundled
  `offers-1000.csv` sample fixture into the dagster containers at the same
  container path (`sampleData.containerPath`, default
  `/app/data/cds/incoming/offers-1000.csv`) on both render targets — on
  Compose through the existing `sharedData` bind plus a read-only file bind,
  on Helm through a new `sample-data` ConfigMap projected from
  `sampleData.hostPath` — and schedules the `load_offers_1000` job
  automatically via `CDS_SAMPLE_DATA_ENABLED` (the job is idempotent and
  leaves exactly 1,000 rows in `offers_1000`). The helm renderer now honors
  `enabledFrom` on `implementation.kubernetes.configMaps` entries and on
  individual compose volume-mount entries, so the ConfigMap and its mounts
  disappear when the parameter is off; rendering stays fail-closed
  (`cds test` green on both targets with the parameter on and off).

- Added `docs/plan/k8s-haven-mvp.md` (#1104): an execution runbook for an MVP
  cycle that starts from a fresh directory with only Docker installed and walks
  six gates — bootstrap, Kubernetes MVP on a local k3d cluster (`--target
  helm`), production-ready posture under the `prod` overlay, Haven-parity
  measurement via `cds maturity`, evidence/issue triage, and the v1.0.0
  release — each split into tasks of at most 2 hours. Section 2 lists the open
  prerequisite issues per gate and the already-landed work not to re-file.
  Section 13 now spells out the full production suite as a copy-paste command
  block — `cds init` / `cds test` / `cds up` / `state` / `down` on
  `production-kubernetes-example --environment prod`, with and without
  `--target helm`, plus the surrounding `preflight` / `security` / `maturity`
  / `report` gates (#1104). That profile is labeled the v1.0.0 suite; a second
  block defines the full Haven profile (working name `production-haven`, the
  ~v2.0 Gate-3 target): the v1.0.0 stack plus dbt, Keycloak, Traefik, restic,
  garage, vault, FluxCD and Loki/otel-collector, with each module's
  `productionSuitable: false` / missing-`kubernetes:`-block deviation recorded
  against its tracking issue (#1104).

- Added `docs/underlying-commands.md` (#1119): maps every lifecycle-relevant
  `cds` command (`render`, `up`, `state`, `down` — Compose and Helm targets)
  to the exact underlying `docker compose` / `helm` / `kubectl` / `k3d`
  commands, with code references verified against the CLI source, so teams can
  run the stack without the `cds` CLI. States explicitly that the rendered
  `docker-compose.yml` and `chart/` are standard artifacts (`docker compose -f
  docker-compose.yml up -d` and `helm upgrade --install <release> chart/ ...`
  work standalone) and that the project-root `.env` is a standard Compose env
  file. Linked from `docs/cli-reference.md`, the README (quick-start and
  documentation index), and `docs/kubernetes.md`.

- `cds render --target helm` now validates every `waitFor` gate host against
  the default-deny NetworkPolicy baseline (issue #1031): a gate whose resolved
  host is neither a same-module Service emitted by this render (a workload
  disabled via `enabledFrom` gets no Service) nor the endpoint of a provider
  the module consumes (no binding-derived egress edge exists for it), and
  whose host:port no custom `networkPolicy.egress` ipBlock rule covers, would
  only hang until its timeout, so the render fails closed with the new E152
  diagnostic naming the gate workload and the unreachable host. Gates
  resolving to `${bindings.*.host}` of a consumed provider, to a same-module
  `${k8s.service.*}` Service, or to an ipBlock-passthrough-covered external
  address render unchanged; tests cover the allowed cases plus the
  fail-closed ones (cross-module Service without a binding, external literal
  host, disabled same-module Service target, address/port outside the
  passthrough CIDR). Documented in `docs/kubernetes.md` and the README
  troubleshooting table.

- `cds render --target helm` now derives NetworkPolicy edges from
  reverse-proxy `config.routes` (issue #1033): each route's target module
  opens a port-scoped egress rule on the proxy's pods and a matching ingress
  rule on the target's pods, scoped to the route's backend port (the
  target's http-service contract port, mapped through the target's Service
  `targetPort` like a binding's port), so a proxy needs no hand-written
  `networkPolicy` passthrough rules for its routed targets. Targets no route
  names stay unreachable from the proxy. A route that cannot yield a
  port-scoped edge (unknown target module, no matching http-service provide,
  no usable port evidence -- absent or outside 1-65535 with no advertised
  service ports to fall back to) adds no edge -- default-deny holds -- and is
  reported with the new W129 warning; the passthrough remains the escape
  hatch.
  `waitFor` gates on a route target's Service are accepted by the E152
  baseline check. Tests cover both acceptance criteria plus the port remap,
  the W129 cases, and the gate check. Documented in `docs/kubernetes.md` and
  the README troubleshooting table.

- Helm-target test coverage for Superset's `identityProvider` binding (issue
  #1075): the only `oidc-provider` provider in the repo (keycloak) is
  compose-only, so the new fixture module
  `tests/fixtures/helm-oidc/modules/identity/oidc-stub` provides a minimal
  kubernetes-capable `oidc-provider` and `tests/test_superset_oidc_helm.py`
  renders a profile binding it on `--target helm` end to end — issuer URL
  env present as a literal value, client secret only as a `$(CDS_*)`
  expansion backed by a `secretKeyRef` entry and a `required` key in
  `templates/secret.yaml`, E078/E079 secret-plumbing checks green, no secret
  values in any chart file. The rendered chart is pinned by golden hashes in
  `tests/golden/k8s-chart-oidc.sha256`. A guard test also pins that binding
  the compose-only keycloak on the helm target still fails closed with E072.
- The experimental `dlt` ingestion module can now load into an embedded
  `file-database` warehouse in addition to a `sql-database` one: a new
  `config.destinationType` (`postgres` default, or `duckdb`) selects which
  consume entry is required (`destinationDatabase` vs the new
  `destinationWarehouseFile`, enforced at compile time with E041), the
  consumed contract's shared `hostDirectory` is bind-mounted at
  `/usr/app/duckdb_data` when duckdb is selected, and the database file path
  is passed to dlt's duckdb destination via `DESTINATION__DUCKDB__CREDENTIALS`
  (the `images/dlt` image now also pins the `duckdb` client library). A new
  `profiles/local-duckdb-dlt-dbt` demo profile composes duckdb + dlt + dbt
  end to end against the shared file (#593).

- Documented the snap-confined Docker runtime artifact (issue #1155, loose end
  of #1066/PR #1154): on the snap-confined Docker runtime every container
  fails to start under `--security-opt no-new-privileges:true` with
  `exec ...: operation not permitted` for any image, which is a runtime
  artifact rather than image file capabilities — `docs/runtime-confinement.md`
  gains a "Scope And Caveats" note advising to verify on a non-snap Docker
  host before changing module hardening, the smoke-test guide
  (`docs/profile-testing/failure-path-and-ci.md`) classifies the failure as a
  test-infrastructure artifact, and the loki module README's known-limitations
  links to the note.
- Docker smoke-test CI now covers the experimental observability modules
  (issue #1065): a new `Docker Runtime Smoke Test` step runs
  `tests/test_observability_runtime_smoke.py`, which renders the new
  `profiles/local-loki-otel-collector` profile (loki + otel-collector),
  brings it up on its own compose project, asserts every container reaches a
  running state (otel-collector additionally healthy via its healthcheck),
  POSTs a raw and a structured JSON record to the loki `/logs` ingest
  endpoint and an OTLP/HTTP log record to the collector's `/v1/logs`,
  queries both records back from the Loki store under their retention tier,
  and confirms the collector received the OTLP record — so "renders green but
  fails at runtime" in experimental modules is caught in CI. The render-level
  contract coverage for the profile runs everywhere in
  `tests/test_local_loki_otel_collector_profile.py`.
- Every `WARN` and `FAIL` line in `cds preflight` output now ends with a
  `(see <url>)` link to the repo doc that governs the check
  (`PreflightCheck.reference`): the Docker host hardening baseline for
  `host.*` warnings, `docs/rootless-docker.md` for `host.rootless`,
  `docs/installation.md` for runtime and environment findings,
  `docs/from-docker-to-cds-profile.md` for host-port findings, and
  `docs/image-signing.md#verification-policy` for `images.*` failures.

### Changed

- Deduplicated the Kubernetes workload substitution used for NetworkPolicy
  surfaces (issue #1034): `cli/k8s_renderer.py` no longer substitutes each
  module's Kubernetes block twice per render. `_render_module` now collects
  each enabled workload's Service type, ports, and port -> targetPort map
  while the workloads are already being substituted in its single rendering
  pass, and hands them to the NetworkPolicy generator; the separate
  `_workload_surfaces` pass is gone. Pure refactor — no behavior change,
  golden chart hashes unchanged.
- Added Gate 4.5 (stability and Beta-readiness) to the Kubernetes MVP runbook
  (`docs/plan/k8s-haven-mvp.md`) and gate checklist
  (`docs/plan/k8s-haven-gate-checklist.md`): issues #1206 (versioned profile
  schema + breaking-change/deprecation policy), #1207 (documented CLI
  stability guarantees and E/W error-code lifecycle), and #1208
  (profile-matrix E2E in CI) are now blocking issues for the v1.0.0 release
  gate, with the `Development Status` classifier decision (Alpha → Beta)
  recorded as task G4.7.
- CI now guards `uv.lock` freshness (issue #1197): the `lint` job in
  `.github/workflows/ci.yml` runs `uv lock --check` right after setting up
  Python, so a `pyproject.toml` change that is not followed by `uv lock`
  fails the build instead of silently drifting (previously only caught when
  someone ran `uv lock --check` locally, e.g. the ruff 0.17.1 -> 0.17.3 drift
  from #1056).
- The `log-sink` contract and the observability modules can now forward the
  logs signal end-to-end over OTLP/HTTP (issue #1076, loose end from #777/#1063):
  `shared/contracts/log-sink.yaml` gains an optional `connectionUri` field
  (mirroring `trace-sink`/`metrics-sink`) with OTLP-shaped examples, and the
  experimental `loki` module now provides its `log-sink` contract over Loki's
  native OTLP/HTTP endpoint (`protocol: otlp-http`, `ingestPath: /otlp/v1/logs`,
  `connectionUri: http://<loki-storage>:3100`, served by the `loki-storage`
  service on port 3100) instead of the plain-JSON Vector gateway, which
  remains as the host-loopback dev endpoint (`/logs`). Loki promotes the
  `service.name`, `deployment.environment.name`, and `tier` resource
  attributes to index labels so OTLP shippers can still reach the
  `tier="structured"` retention stream. The `otel-collector` module's
  `backends.logs` binding therefore resolves a real forwarding URL (e.g.
  `http://logs-loki-storage:3100/otlp/v1/logs`) instead of silently staying
  collector-local, and its unbound-signal discard now points at the
  collector's own health_check extension (which answers the export POST with
  200 and drops the batch silently — no retry storm) instead of
  `http://127.0.0.1:1`. Verified at runtime against the pinned images: a log posted to
  the collector's `/v1/logs` with `backends.logs` bound to a `loki` instance
  lands in Loki (`service_name`/`tier` labels applied), and an unbound signal
  produces no export-retry errors. `docs/observability.md` (sections 5 and
  12), both module READMEs, and `tests/test_otel_collector_module.py` /
  `tests/test_loki_module.py` (including an end-to-end
  `backends.logs -> loki.log-sink` plan/render test) are updated.

- `cds get` (and `cds list --remote`) now default `--ref` to the release tag
  matching the installed CLI version (e.g. `v0.15.0` for cds 0.15.0) instead
  of `main`, so a fetched profile and its modules are validated by the same
  bundled schema as the running `cds` and version-skew failures like
  `E021 ... 'enabledFrom' was unexpected` cannot occur by default. When the
  version is unknown, is a dev/local build, or the tag does not exist
  upstream (e.g. a fork without tags), the default falls back to `main` with
  a note on stderr. An explicit `--ref <branch|tag|sha>` is unchanged and
  still fails closed on 404, but a version tag newer than the installed cds
  version (e.g. `--ref v0.16.0` on cds 0.15.0) now prints a warning on
  `cds get` and `cds update`, since assets at a newer release may use schema
  features the running CLI does not understand. A bare version
  (`--ref 0.15.0`) is accepted as shorthand for its `v`-prefixed release tag
  (`v0.15.0`), retrying on HTTP 404. The resolved revision is
  recorded in `.cds/get-manifest.json`, so `cds update` keeps the layout on
  that tag; re-run `cds get --ref main` to move to `main`.

- The keycloak module (`modules/identity/keycloak`) is now production-suitable
  and defaults `config.webServer.mode` to `production` in the same change
  (issue #974): the `productionSuitable: false` marker is dropped and the
  configSchema default flips from `development` to `production`, so a
  production profile using keycloak without an explicit mode passes `cds
  validate` (E140 only rejects an *explicit* `development`) and plans/renders
  `CDS_WEBSERVER_MODE=production` instead of silently running `start-dev` at
  runtime. `development` remains an explicit opt-in for local profiles and is
  still rejected in production profiles by E140. `docs/serving-modes.md`
  (keycloak row default column), the module README, and the haven/roadmap docs
  are updated to match; `tests/test_keycloak_postgres_wiring.py` gains an E140
  regression test covering a production profile with keycloak and no explicit
  mode (validates clean, renders production mode) plus the explicit-`development`
  rejection case.

- The DuckDB warehouse module is promoted out of `modules-experimental/` to
  `modules/warehouse/duckdb/` and is now production-suitable: the
  `productionSuitable: false` marker and the `(experimental)` displayName
  suffix are dropped once the `file-database` contract shape is exercised by
  real consumers (dlt and dbt wiring plus the `local-duckdb-dlt-dbt` demo
  profile, #593). `docs/architecture.md` and `docs/roadmap.md` list the module
  under stable components; `tests/test_duckdb_hardening.py` now asserts the
  production-suitable metadata instead of the experimental one.

### Fixed

- `cds get` no longer follows a symlinked `.cds` tracking directory, update
  notifications parse bounded PEP 440 versions, and image digest pins use
  deterministic validation instead of potentially super-linear regular
  expressions (issue #1199).

- `cds down` now writes its lifecycle log to `.cds/logs/down-<profile>-<ts>.log`
  instead of `.cds/logs/up-down-<profile>-<ts>.log`: `default_log_path()`
  takes a `prefix` parameter (`"up"` for `cds up`, `"down"` for `cds down`)
  instead of unconditionally prefixing `up-` (#1123).

- The `Mutation Tests (Kill Matrix)` workflow no longer fails on time-capped
  PR runs (issue #1175): a `timeout`-killed matrix can leave an empty or
  truncated `mutants/*.meta` cache file, and `mutmut export-cicd-stats`
  crashed on it with a `JSONDecodeError` (`load()` only tolerates a missing
  file), while a run capped before any result was recorded left
  `mutmut badge` failing on the missing `mutants/mutmut-cicd-stats.json`.
  The export step now drops incomplete meta cache files before exporting and
  writes an all-zero stats placeholder when no results exist, so the badge,
  job summary, and artifact still publish the partial matrix.
  `tests/test_mutation_tests_workflow.py` reproduces the crash against the
  previous workflow and pins the tolerant export.
- The mutmut-side fix for that torn-cache crash is upstreamed (issue #1181):
  boxed/mutmut#630 (merged 2026-11-07) makes `SourceFileMutationData.load()`
  catch `json.JSONDecodeError` alongside `FileNotFoundError`, so any
  interrupted `mutmut run` — not just the kill-matrix workflow — recovers
  cleanly on the next command. The workflow-side sanitizer in
  `.github/workflows/mutation-tests.yml` was dropped against the merged fix
  (see #1193 below); the pin flips to a versioned release once one > 3.8.1
  ships on PyPI.
- The kill-matrix workflow no longer sanitizes the `mutants/*.meta` cache
  itself (issue #1193): with boxed/mutmut#630 merged, mutmut's `load()`
  treats a torn cache from a time-capped run like a missing one, so
  `mutmut export-cicd-stats`/`badge` tolerate it directly. The export step in
  `.github/workflows/mutation-tests.yml` drops the `rglob("*.meta")` sanitizer
  block, and the ad hoc mutmut pin in that workflow and the `Makefile`
  `test-mutation` target moves from `mutmut==3.8.1` to the
  `boxed/mutmut@6837b3c3ab0cfa4f5488f68adbd1bb26f68b8eb8` commit — flip it to
  `mutmut==<release>` once a release > 3.8.1 is on PyPI. The all-zero stats
  placeholder stays: it guards the separate zero-result case where
  `mutmut badge` exits 2 on a missing `mutants/mutmut-cicd-stats.json`.
  `tests/test_mutation_tests_workflow.py` now pins "mutmut tolerates the torn
  file" instead of "the sanitizer drops it", and keeps the placeholder/badge
  pins.
- The local audit log (`.cds/audit-log.jsonl`, #737/#810) is now anchored to
  the profile's own project root — `resolve_project_root(profile_path)`,
  the same resolution that decides where `docker-compose.yml` is rendered —
  instead of the current working directory (issue #822). Entries for a
  profile resolved via `CDS_PROFILE_PATH` from an unrelated directory (e.g.
  a centrally shared profiles checkout) now consistently land in one
  `.cds/audit-log.jsonl` next to that profile's project, rather than being
  scattered across whatever directory `cds` was invoked from. When the cwd
  already is the project root, the location is unchanged.
  `docs/audit-log.md` documents the resolved location, and
  `tests/test_main.py::AuditLogCLIIntegrationTest` gains #822 regression
  tests (validate/render from a foreign cwd) alongside the common-case test.
- `modules-experimental/observability/otel-collector/collector.yaml` now uses
  the canonical `otlp_http/<signal>` exporter names instead of the deprecated
  `otlphttp` alias, which logged a deprecation warning on every startup of the
  pinned 0.164.0 image, and pins the `health_check` extension to
  `0.0.0.0:${env:HEALTH_PORT}` with a comment explaining why: the image's
  default is `localhost:13133`, which left the host-published
  `127.0.0.1:<healthPort>:13133` mapping unanswerable from the host (issue
  #1167; the container-side compose healthcheck half was already closed by
  #1065/#1165). `tests/test_otel_collector_module.py` pins both properties of
  the shipped collector config. Verified at runtime against the pinned
  image: zero deprecation warnings, `otelcol-contrib validate` exits 0,
  OTLP/HTTP ingest answers 200, and the host-published health port answers
  `{"status":"Server available"}` with HTTP 200.
- Bumped the digest-pinned base of the hardened dagster image to remediate
  CVE-2026-85091 (HIGH, heap buffer overflow in zlib's `gz_vacate()`) reported
  by the Trivy image scan against the alpine 3.24.2 layer:
  `python:3.14-alpine` `sha256:f6a589...` -> `sha256:a18b20ad...` (alpine
  3.24.4, zlib 1.3.2-r1) in both stages of `images/dagster/hardened/Dockerfile`.
- `cds test --target helm` (and `cds security`) no longer fail the security
  stage with W096 when the profile's Compose render fails *only* because a
  module has no compose implementation (E070 — e.g. a Helm-only module such
  as the CloudNativePG operator module, issue #768). That skip is expected
  and already surfaced by the render stage itself; any other render error
  still raises W096 and fails the stage, so the silent-skip guard
  (GHSA-mx5p-cv63-6829) is unchanged for real breakage.
- `modules-experimental/observability/loki` re-enables
  `security_opt: no-new-privileges:true` on both the `loki` (Vector) and
  `loki-storage` (Loki) services (#1066). The omission was based on an
  `exec ...: operation not permitted` failure under no_new_privs, but that
  failure is an artifact of the snap-confined Docker runtime used in the
  sandbox (it rejects every binary under `--security-opt
  no-new-privileges:true`, including a plain Alpine `/bin/sh`), not of the
  images: registry layer inspection shows no `security.capability` xattr on
  either pinned binary, and both `timberio/vector:0.51.0-alpine` and
  `grafana/loki:3.6.1` exec cleanly under `PR_SET_NO_NEW_PRIVS` on a
  standard runc host. `tests/test_loki_module.py` now asserts the flag on
  the module definition and on the rendered Compose output.
- The `Mutation Tests (Kill Matrix)` workflow no longer fails on PRs when a
  hosted runner receives a shutdown signal ~4 minutes into the job (observed
  on PR #1143 and on the weekly `main` run, both killed before the previous
  240s cap could fire): the PR time cap is lowered to 180s so `timeout` fires
  first and the step exits 124, which the workflow already accepts as a
  partial matrix. Weekly schedule runs stay uncapped and produce the full
  matrix.
- `cds render/up --target helm` no longer fails with E085 ("bind source
  outside the project root") on projects scaffolded by `cds get`: the
  renderer's project-root detection now treats a `.cds/` directory as a
  project marker (first priority, same as `cds use`/`cds config`), so
  relative ConfigMap `fromBind` sources like `./workdirs/dagster/definitions.py`
  resolve against the project root again. Previously such projects — which
  have `.cds/` but no `.git`/`pyproject.toml` — made every relative bind fail
  closed (#1113).

- The prompt trust-boundary lint (`scripts/check_prompts.py`, `make
  lint-prompts`/CI/pre-commit) no longer has a non-gh hole in
  `actions/ai-inference` `file_input` jobs: previously only `gh` collection
  commands were fail-closed (rule 4), while `curl`/`wget`/python HTTP calls
  could still write issue/PR/comment free text into an input file — and
  `scripts/redact_secrets.py` strips secret/injection patterns, not titles
  or bodies. New rule 5 fail-closes both directions: (a) every write
  (shell redirect, `tee`, python `open`/`write_text`, `cp`/`mv`/`install`)
  to a file later used as `file_input` must come from a rule-4-compliant
  literal-metadata `gh` command or from the new
  `FILE_INPUT_PRODUCERS_ALLOWLIST` (keyed by workflow + canonical path with
  a required producer pattern, a producer validator, and justification; the
  only entry is `biweekly-issue-audit.yml`'s `recent-commits.txt` from
  `git log`, where the validator accepts only metadata-only `--format`
  specifiers — hashes/dates/timestamps — so `--format='%h %s'`, author
  fields, and `%(...)` pretty-format atoms (`%(trailers:...)`, `%w(...)`,
  `%C(...)`) fail closed, as does a bare `git log`; python writes with
  non-literal target paths (`open(var, 'w')`) fail closed as unverifiable,
  redirect detection covers the noclobber-override forms (`>|`, `&>|`,
  `2>|`), output-path flags are treated as writes (`curl -o`, `wget -O`,
  `--output=`, attached `-oFILE`, combined clusters like `curl -soFILE` /
  `wget -qOFILE`, `dd of=`, `--dest`), and a producer line must be a
  single simple command (no `;`/`&&`/`||`/backticks/`$(...)`, no process
  substitution `>(...)`/`<(...)`, pipes only into `tee`) so chained or hidden
  writers cannot ride on an allowlisted/gh/redact line — the
  `scripts/redact_secrets.py` exemption applies only to a single simple
  command; opaque runner invocations (`python3 script.py`, heredoc python,
  `bash`/`node`/etc.) fail closed in feeding jobs except
  `scripts/redact_secrets.py`; write targets via script-local literal
  variables are resolved and checked (assignments carrying shell separators
  are not trusted), directory destinations of `cp`/`mv`/`install`
  (`-t DIR`, trailing `/`) are derived as `DIR/basename(src)`, while
  unresolved shell expansion in a write target fails closed (runner vars
  like `$GITHUB_OUTPUT` exempt), and
  (b) any non-gh
  HTTP fetcher targeting a
  GitHub free-text endpoint (issue/PR/comment/review/discussion resources
  or the `graphql` endpoint) fails closed even when captured via `$(...)`,
  and (c) deny by default for `uses:` steps in feeding jobs: only
  `FEEDING_JOB_USES_ALLOWLIST` actions (checkout, setup-*, cache,
  upload/download-artifact, ai-inference) are allowed; every other action
  (third-party, composite, local, docker://, github-script) fails closed.
  Tests in `tests/test_prompts_lint.py`; policy documented in
  `.github/prompts/README.md` (#1090; residual opaque-`run:`-script vectors
  tracked in #1120).
- Bumped the digest-pinned base images of the five published runtime images
  to remediate CVE-2026-109518 (HIGH, on-path attack in apt/libapt-pkg on
  Debian) reported by the daily Trivy scan (#1144-#1148): `python:3.14-slim`
  to `sha256:ac25e5af...` (Debian trixie base now baking apt 3.0.4) in the
  dlt, dbt, and dagster base Dockerfiles; `postgres:18` to
  `sha256:e923a22a...` (apt 3.0.4) in `images/postgres/Dockerfile` plus the
  matching pin in `modules/warehouse/postgres/module.yaml` and the
  `tests/test_image_digest_check.py` example; and
  `apache/superset:6.1.0-py311` to `sha256:3f63de20...` in
  `images/superset/base/Dockerfile` (that base still ships apt 2.6.1, but
  the image's build-time `apt-get upgrade` now pulls the fixed apt 2.6.2
  from bookworm-security). Merging this PR triggers `publish-images.yml`,
  whose pre-push Trivy HIGH/CRITICAL gate re-verifies all five rebuilt
  images before they are signed and republished.
- Two "renders green but fails at runtime" bugs in the experimental
  observability modules, caught by building the #1065 docker smoke test
  (both fixed here so the new CI job is green):
  - `modules-experimental/observability/loki` now points the Vector gateway's
    `LOKI_ENDPOINT` at `${service.services.loki-storage}` instead of
    `${service.host}-loki-storage`. The old template only resolved when the
    module instance id differed from `loki`; with the conventional id `loki`
    the rendered storage service is `loki-storage` while the endpoint named
    the non-existent `loki-loki-storage`, so ingestion failed at runtime.
    `tests/test_loki_module.py` pins the rendered endpoint for the namesake
    id.
  - `modules-experimental/observability/otel-collector` no longer configures
    the removed `logging` exporter (the pinned
    `otel/opentelemetry-collector-contrib:0.164.0` fails config validation
    with it — the collector exited 1 on startup) and no longer renders a
    container-side healthcheck (that image is distroless — no shell, wget, or
    curl — so the `CMD-SHELL wget` probe could never pass and would keep the
    container permanently unhealthy). Pipelines now use the `debug` exporter,
    and health is probed via the host-published `healthPort`
    (`health_check` on 13133). `tests/test_otel_collector_module.py` asserts
    the healthcheck is absent.
- The `cds-restic` image's compiled restic binary is rebuilt with the fixed
  Go module versions to clear CVE-2026-78661 (HIGH, DoS via crafted HTML
  input in `golang.org/x/net/html`, reported by the Trivy image scan against
  the published `cds-restic` digest, issue #1185): the `go get` pins in
  `images/restic/Dockerfile` move `golang.org/x/net` v0.60.0 -> v0.62.0 (the
  fixed version), with the minimum versions that release requires,
  `golang.org/x/crypto` v0.57.0 -> v0.58.0 and `golang.org/x/text` v0.42.0
  -> v0.43.0. restic 0.19.1 is still the latest upstream release, so the
  image keeps compiling the pinned release tag from source; Renovate's
  regex manager in `renovate.json` keeps tracking the pins.
- `uv.lock` is regenerated so the locked ruff matches the `ruff==0.17.3` pin
  in `pyproject.toml` (issue #1194): the lock still recorded 0.17.1 from
  before the #1056 bump, so `uv lock --check` failed on main and any plain
  `uv run`/`uv sync` silently rewrote the lock as a side effect. The lock now
  records ruff 0.17.3 (specifier `==0.17.3`) and `uv lock --check` passes.
- Lint drift cleanup (issue #1177): `yamllint .` no longer warns about the
  multi-line `enum` flow sequence in the `kubernetesVolume.medium` property of
  `cli/resources/module.schema.json` (now single-line, matching the other
  inline enums), and `ruff format` (pinned `==0.17.3` in `pyproject.toml`) has
  been applied repo-wide, so `ruff format --check .` is clean alongside the
  already-gated `ruff check .`. Two suppression comments that the
  reformatting detached from their flagged line were moved back:
  `# noqa: S104` in `tests/test_security.py` (onto the `"0.0.0.0"` literal)
  and `# NOSONAR` in `cli/renderer.py` (onto the `http://` backend URL, so
  the S5332 false positive stays suppressed). The reformatting also
  re-wrapped the line SonarCloud anchors its pre-existing S8707 finding on
  in `cli/main.py`, which re-attributed that false positive (operator's own
  CLI path argument) to this PR; the line now carries an explicit `# NOSONAR`
  with the justification, so the PR quality gate stays green.
- LLM seam hardening (github-advanced-security findings on this PR):
  `scripts/_vendor/llm/client.py::complete()` now dispatches on an explicit
  provider allowlist and raises `ValueError` for unknown providers instead of
  falling through to Azure OpenAI (a misconfigured `LLM_PROVIDER` can no
  longer off-box the prompt by accident), and wraps the caller's user prompt
  in the repo's `BEGIN_UNTRUSTED_DATA` / `END_UNTRUSTED_DATA` fences with a
  data-not-instructions preamble before dispatching to any provider, so
  untrusted profile YAML/plan JSON embedded in `scripts/ai_profile_review.py`
  prompts is structurally isolated (system prompt unchanged). The prompt is
  additionally run through a deterministic sanitizer before fencing (control
  chars stripped; fence markers, instruction-like patterns, and
  secret-looking values replaced with `[REDACTED]`, mirroring
  `scripts/redact_secrets.py`), and fence idempotence is strictly structural
  (exact preamble+begin+payload+end shape), so embedded markers can neither
  forge nor break the fences. As a deterministic trust-boundary gate,
  `complete()` now refuses to send any prompt to the remote `azure_openai`
  backend unless `LLM_ALLOW_REMOTE_PROMPTS=1` is explicitly set
  (default-deny; local providers unaffected). `parse_status()` in
  `scripts/ai_profile_review.py` is now strict/structural: exactly one
  `STATUS:` line, required to be the last non-empty line of the model report,
  so an injected or echoed status line cannot forge a clean verdict.

## [0.15.0] - 2026-11-02

### Added

- `cds up --target helm` is now self-contained, matching the Compose target's
  one-command lifecycle (#690). Before `helm upgrade --install` runs, it checks
  that the `--kube-context` cluster is reachable (with `--create-cluster` and a
  `k3d-<name>` context it creates the missing k3d cluster and waits for the
  node), builds every local image the rendered chart references with
  `imagePullPolicy: Never` (the compose services' `build:` blocks), and imports
  them into a k3d cluster with `k3d image import`. On a non-k3d cluster with
  local images the command fails closed and points at `--image-source registry`.
  `${CDS_*}` secret placeholders in build fields are refused on this path
  (a resolved `docker build --build-arg` would expose the value in the process
  argv) instead of being expanded, and build-arg values are redacted
  (`--build-arg KEY=<redacted>`) in the echoed `Running: ...` command line so
  sensitive values are not written to stdout/CI logs while the executed
  command is unchanged. `--no-build` skips the build and import
  steps. New `cli/k8s_provision.py`;
  `docs/kubernetes.md` updated; tests in `tests/test_k8s_provision.py`.

- The `cds` CLI now honors the `NO_COLOR` environment variable (#1094): when
  present and non-empty (<https://no-color.org/>), colors and the ANSI
  in-place redraw of the live state view are disabled for every command, as
  if `--no-color` had been passed. `--no-color` still works, and
  `cds up --target helm` now forwards its color setting to the Helm apply
  output as well. `ACCESSIBILITY.md` and `docs/cli-reference.md` updated.

- Added `ACCESSIBILITY.md` (#1092): the project's accessibility commitment,
  scope (the `cds` CLI and repository docs versus third-party module web UIs),
  the WCAG 2.2 AA target for CDS-hosted web content, supported environments,
  known limitations, and how to report a barrier. Linked from `README.md` and
  `LEGAL.md`.

- Agent approval policy (#1084), enforced in CI: `AGENT.md` now has an
  "Agent approval policy" section (autonomous vs human-approval actions),
  and the CI lint job runs `scripts/check_agent_policy.py` — agent-authored
  commits (Mistral Vibe footer) touching protected paths (workflows, prompts,
  security tooling, `AGENT.md`, certs, dependency/policy config) fail unless
  the PR carries the `agent-approved` label. Tests in
  `tests/test_agent_policy.py`.

- Follow-ups from the 2026-10 EAISI masterclass on AI-assisted coding (Kapitan): CONTRIBUTING.md "How We Work" now states the spec-first rule (the design/spec artifact is the source of truth humans edit; the executable Contract is what machines check) and the one-canonical-instruction-file rule (`AGENT.md` canonical, tool-specific paths symlinked locally). Runtime agent-security policy (Agent Security Kernel) is tracked as #1084.

- Adopted working practices from [Impatient Programming](https://impatientprogramming.org/)
  where they can be enforced by the repository itself (see CONTRIBUTING.md
  "How We Work"):
  - The Contract is now executable in CI: `tests/test_module_contract_schema.py`
    validates every `module.yaml` under `modules/` and `modules-experimental/`
    against `cli/resources/module.schema.json`, and every profile through the
    real validator pipeline (extends resolution included).
  - Contract coverage is published: `scripts/check_contract_coverage.py`
    (make target `make contract-coverage`, CI workflow `contract-coverage.yml`)
    regenerates `docs/contract-coverage.md`, and CI fails when the committed
    report is stale. Current coverage: 30 of 37 modules/profiles/shared
    contracts (modules 15/17, profiles 3/8, shared contracts 12/12).
  - A kill matrix runs mutation testing (mutmut) over the contract/security
    core of the CLI (`make test-mutation`, workflow `mutation-tests.yml`), so
    every test must prove it catches something no other test catches.
  - Issue and PR templates now encode the IMP practices: feature requests
    require success criteria and the One Question; PRs require the Contract
    to be kept current, existing behavior pinned before changes, breathe-out
    in the same PR, tests that earn their place, regression tests for fixes,
    and short-lived branches. `refine:` commits are grouped under
    "Refinements" in the changelog (cliff.toml) so non-defect polishing is
    labeled honestly.
  - Label-gated auto-merge: a PR labeled `automerge` with a green CI head
    pipeline is switched to auto-merge by `auto-merge.yml`; all other PRs
    keep the human review path.
  - New `.github/prompts/design-artifact.prompt.yml` produces a written
    design artifact (options, rejected comp, recommendation, success
    criteria) before implementation.
  - Added a biweekly practices audit (`scripts/check_practices_audit.py`,
    `make practices-audit`, workflow `practices-audit.yml`) that files a
    roll-up issue with contract coverage, the `fix:`/`refine:` commit mix,
    `automerge` usage, and the latest kill-matrix run. The audit also pins a
    sha256 of <https://impatientprogramming.org/rules/> in
    `scripts/imp-rules.sha256`; when the live rules page changes,
    `practices-audit.yml` files a tracking issue to re-read the rules and
    re-assess the adoption (`make practices-audit-pin` re-pins the hash).
  - Added a daily branch-hygiene audit (`scripts/check_branch_hygiene.py`,
    `make branch-hygiene`, workflow `branch-hygiene.yml`) that classifies
    branches as merged-into-main (ancestor or patch-equivalent via
    `git cherry`), active-PR, or open-work, and files a `branch-hygiene`
    report issue listing stale branches that are safe to delete.
  - Added a prompt trust-boundary lint (`scripts/check_prompts.py`, wired
    into CI, pre-commit, and `make lint-prompts`; policy in
    `.github/prompts/README.md`, tests in `tests/test_prompts_lint.py`):
    every prompt's system message must carry the fail-closed untrusted-data
    protocol (markers `untrusted`/`refuse`/`credentials`), and `{{...}}`
    interpolation is denied unless the file is in the script's justified
    ALLOWLIST. `issue-consistency-audit.prompt.yml` was hardened with the
    full protocol and `BEGIN/END_UNTRUSTED_DATA` fences (now lint-enforced
    for allowlisted files); the design-artifact prompt carries no input at all.
  - Added deterministic pre-inference redaction: `scripts/redact_secrets.py`
    (tests in `tests/test_redact_secrets.py`) scrubs secret patterns from
    user-controlled issue JSON before it is sent to the external model via
    `file_input` in `biweekly-issue-audit.yml`.
  - `auto-merge.yml` now only considers non-fork PRs (`isCrossRepository ==
    false`) at the green head SHA; `scripts/check_practices_audit.py`
    sanitizes untrusted PR titles before persisting them into report issues.
  - The prompt lint also covers workflows: any `actions/ai-inference` step
    with `file_input` must produce each input file via `scripts/redact_secrets.py`
    in the same workflow, and `redact_secrets.py` now neutralizes
    instruction-like content (role tags, tool directives, "ignore previous
    instructions") in addition to secrets, for JSON and plain text.
- Added the shared `oidc-provider` contract, configurable Keycloak realm
  import and issuer advertisement, and optional Superset OIDC login using
  provider discovery and profile-backed client secrets (#1055, #680, #681).
- Added an experimental Loki log-aggregation module implementing the shared
  `log-sink` contract, with Vector HTTP ingestion, structured-event
  classification, and configurable raw/structured retention tiers (#662).
- Added optional module-instance scoping for production runtime-policy waivers; unknown module IDs are rejected (#932).
- Applied the production/learning split to Superset and Dagster (issue
  #959): learning Superset profiles pull the pinned upstream image without
  local builds, production profiles retain the hardened local build, and
  Dagster now defaults to its hardened variant in production while
  preserving explicit variant overrides and variant-prefixed registry tags.
- Added GitOps rendering support (issue #767): a new vendor-neutral
  `gitops-target` contract (`shared/contracts/gitops-target.yaml`: repository
  URL, branch, path, sync interval, target namespace), validated by
  `cli/validator.py` like the other shared contracts, and an experimental
  FluxCD reference module (`modules-experimental/gitops/fluxcd/`) that
  provides the contract and renders a Flux `GitRepository` +
  `Kustomization`/`HelmRelease` bootstrap manifest set over the
  `--target helm` chart output. To support it, the module schema gains an
  `implementation.kubernetes.manifests` block whose raw manifests the Helm
  renderer emits as extra chart templates (release-scoped names, standard
  module labels, `enabledFrom` gating, shape-checked with the new E151
  diagnostic); manifest-only modules get no ServiceAccount/NetworkPolicy, and
  the GitOps handoff (rendered-chart-as-artifact vs. chart-as-source, CDS does
  not run the controllers) is documented in `docs/kubernetes.md` and the
  module README.
- Added `cds maturity` to report Basic/Automated tiers and recognized platform contract providers in text or JSON without requiring runtime secret values (#769).
- Added the `trace-sink` and `metrics-sink` shared contracts (`shared/contracts/trace-sink.yaml`, `shared/contracts/metrics-sink.yaml`) shaped so an OpenTelemetry Collector OTLP receiver conforms without hardcoding OTel, plus an experimental `otel-collector` reference module (`modules-experimental/observability/otel-collector/`, multi-arch digest-pinned contrib image, hardened container) that provides `log-sink`/`metrics-sink`/`trace-sink` over OTLP/HTTP (gRPC 4317 also open) and can forward each signal to a bound OTLP/HTTP backend via `spec.config.backends.<signal>.contractRef` (`connectionUri` + `ingestPath`, since the collector's per-signal exporter endpoints are complete URLs); the E150 provided-contract host check now also covers `metrics-sink`/`trace-sink` providers, and `docs/observability.md` documents OTLP instrumentation and binding (#777).
- Extended `cds maturity` with Observed, Secured, and Resilient tiers;
  observability now advances a profile from Automated to Observed, and
  Resilient requires backup coverage for every stateful module (#1059).
- Documented how CDS security capabilities map to NIST SP 800-207, clarified
  the contract-based meaning and limits of the Secured maturity tier, and
  filed the separate workload-identity gap (#778, #1068).
- Published the Kubernetes target docs and example profiles (issue #77):
  `docs/kubernetes.md` gains a "Migrating from Docker Compose" guide (same
  profile, `--target helm` render flag, what translates and what does not),
  and two ready-made example profiles that render successfully to Helm —
  `profiles/local-kubernetes-example` (local k3d stack) and
  `profiles/production-kubernetes-example` with a `prod` environment overlay
  promoting it to the production security policy. README links the guide
  and the examples.
- **Breaking (for existing `--target helm` deployments):** Added
  NetworkPolicy and ServiceAccount generation to the `helm` render target,
  completing the Kubernetes production resources (issue #75): every
  module's chart now ships a default-deny NetworkPolicy whose allow
  rules are derived from the plan's contract bindings (consumer-to-
  provider egress and matching provider ingress on the contract port,
  same-module traffic, and cluster DNS egress; NodePort/LoadBalancer
  services open ingress on exactly their exposed ports), and every
  workload runs under a dedicated per-module ServiceAccount with opt-in
  least-privilege RBAC via the new
  `implementation.kubernetes.serviceAccount.rbac` rules — no rules means
  no Role, no RoleBinding, and no mounted API token (malformed rules fail
  closed with E148). Custom allow rules for destinations no binding can
  name — an external API or database a job reads from, a cross-namespace
  ingress controller — are opt-in via
  `implementation.kubernetes.networkPolicy.ingress`/`egress`, rendered
  verbatim after the binding-derived rules (malformed shapes fail closed
  with E149), and the contract's Service-facing port is mapped through the
  provider's Service `targetPort` so a port remap cannot silently break a
  rule. Extended the Kubernetes security scan with CDS-K8S-006 (wildcard
  RBAC grants), CDS-K8S-007 (NodePort/LoadBalancer ingress exposure), and
  CDS-K8S-008 (external-egress passthrough rules), and added a W128
  warning when a binding carries no port evidence and its egress rule
  cannot be port-scoped; documented the network-isolation baseline in
  `docs/kubernetes.md`. Charts rendered before this change will deny
  external and cross-namespace traffic on their next render until the new
  `networkPolicy` rules are declared (see the "When the baseline denies
  something you need" section of `docs/kubernetes.md`).
- Added a `Kubernetes E2E` GitHub Actions workflow that runs the existing
  k3d harness (`make k3d-build` and `make k3d-e2e`) against the example
  profile on pull requests and pushes touching the chart, CLI, images, harness
  scripts, the Makefile's k3d targets, or `pyproject.toml`: it installs the rendered Helm chart into a throwaway
  cluster, waits for every workload to become Ready, checks PostgreSQL,
  Dagster, and Superset, uploads pod diagnostics on failure, and always
  deletes the cluster. k3d is pinned and checksum-verified, and the
  harness now also builds and imports the PostgreSQL module's local image
  (previously the pod failed with `ErrImageNeverPull`) (#76).

- Added a single-node Garage S3-compatible object-storage module and
  provider-neutral `object-storage` contract, with persistent data and
  metadata volumes and profile-secret-backed credentials. The module is
  explicitly unsuitable for production because it has no redundancy
  (#663).
- Added an experimental scheduled object-storage backup module that writes
  immutable, timestamped S3-compatible snapshots to a separately configured
  destination (#669). Every successful pass writes a
  `.cds-snapshot-complete` marker into its prefix, so a prefix left behind
  by a failed pass is recognizable as partial; failed passes retry with a
  fresh timestamp after a configurable delay (`retryDelaySeconds`,
  300 seconds by default) instead of crash-looping. Like the
  other backup modules it is production-only
  (E143), and its explicit `productionSuitable: false` marking (plaintext
  snapshots, operator-managed restore) requires a scoped CDS-SEC-073
  waiver in production. Every pass is a full copy of the current bucket
  contents, so destination storage grows linearly with the number of
  retained snapshots, and a destination configured as literally the bound
  source endpoint and bucket fails planning with a new E147 diagnostic
  instead of booting a service that would copy the bucket onto itself. A
  Docker-gated runtime proof runs in the Docker
  Runtime Smoke Test workflow: it snapshots a seeded bucket to a second
  Garage instance, overwrites the source object, and recovers it
  byte-for-byte from the completed, marker-verified timestamped prefix.

- Added typed provenance records to the `cds get` tracking manifest
  for fetched profiles, modules, contracts, and runtime assets, including
  source/local paths, dependency relationships, and per-file SHA-256 hashes
  for detecting local edits. Existing profile entries remain available in
  the version 2 manifest (#346).
- Added `cds update` to refresh tracked profiles, modules, contracts, and
  runtime assets while reporting and preserving locally modified files;
  local source paths are recorded absolutely for subsequent updates (#348).

- Added a generic file/volume backup capability (issue #668): the new
  `backup/restic` module snapshots modules whose durable
  state is plain files or directories into an encrypted restic
  repository, complementing the Postgres-specific pgBackRest capability
  (#665). Modules opt in by providing the new `backup-source` contract
  (a named Compose volume or a host directory); the shipped providers
  are the orchestration module's IO-manager storage volume, the
  file-based warehouse's host directory, and the ingestion module's
  pipeline-state volume. A profile binds up to three sources to the
  restic module's source slots; its `restic-backup` sidecar mounts each
  source read-only and takes scheduled, encrypted, integrity-checked
  backups with retention, into a local repository volume by default or
  any S3-compatible endpoint via restic's native S3 backend. The module
  provides the vendor-neutral `backup-target` contract for
  `spec.backup.target.contractRef`, is a production-only piece like the
  Postgres capability (E143 outside production), and ships a gated
  one-shot `restic-restore` service plus a Docker-gated
  clean-environment restore-verification runtime test. The renderer now
  supports cross-module named-volume mounts (a source mounted by its
  fully rendered `<module-id>-<volume>` Compose name is no longer
  re-namespaced), and plan validation fails loudly when a bound
  backup-source advertises a volume the producer does not declare
  (E146), instead of silently protecting an empty implicit volume.

- `.trivyignore` exceptions now also accept GHSA ids for scan findings
  reported without a CVE alias (GitHub security advisories): trivy's
  native ignore format supports any advisory id, and without this the
  restic image's grpc finding (GHSA-hrxh-6v49-42gf, no CVE alias) could
  not be excepted at all. `tests/test_trivyignore.py` and
  `docs/image-scanning.md` document the extended format.

- Migrated the Python workdirs to cds-runtime and demonstrated the
  workload API across two runtimes (issue #987): the dagster workdir
  resolves its connections through `cds_runtime.connections`
  (adopting the module's `CDS_ANALYTICS_DB_*` environment block via the
  prefix, legacy `_NAME`/`_USER` spellings included) and its
  dagster-specific event persistence is ported onto cds-runtime inside
  the workdir; the shared example workload `workdirs/db_roundtrip.py`
  runs unchanged as a dagster asset (canonical `analytics` connection)
  and as the dlt pipeline's pre-load check (canonical `destination`
  connection), with both runtime images baking `runtime/cds_runtime` in
  at `/app/runtime`. The legacy `shared/python/db/connection.py`
  helper is deprecated (kept in the images for externally supplied
  definitions files); the dagster and dlt modules gained additive
  canonical `CDS_DB_*` environment blocks.

- Added a `cds-runtime` job to CI (issue #985): the opt-in backend
  integration tests in `tests/test_cds_runtime_backends.py` now run
  against real PostgreSQL and MySQL service containers on every push,
  and the built `cds-runtime` wheel is installed into a fresh virtualenv
  and smoke-tested (`py.typed`, metadata, engine-backed roundtrip),
  guarding the package's packaging and data path on every change.

- Added the production hardening checklist and operator verification
  procedure (issue #72): `docs/production-hardening-checklist.md` maps every
  hardening control to CDS enforcement or operator responsibility, and
  defines a reproducible command sequence (`cds validate`, `cds init`,
  `cds preflight`, `cds security`, `cds render`, `cds report --hardened`,
  `cds up`/`cds state`) with documented expected output against the
  reference production profile (`team-dagster-postgres-superset` with the
  `prod` overlay), including the current known-findings snapshot and its
  per-finding classifications.

- Added a production web-serving mode for HTTP modules (issue #216): a
  provider-neutral `config.webServer` convention with `mode`
  (`production`/`development`). `bi/superset` now serves through gunicorn
  by default (worker and timeout settings via `config.webServer.workers`
  and `config.webServer.timeoutSeconds`) instead of Superset's development
  server, and `identity/keycloak` can switch between development mode
  (`start-dev`, still its default while the module is not
  production-suitable) and Keycloak production mode. Production profiles
  that explicitly select `webServer.mode: development` fail validation
  with `E140`; see `docs/serving-modes.md`. The gunicorn configuration
  preloads the app in the master process before forking workers, so
  workers cannot race each other creating the metadata schema when the
  web process starts before the init container.

- Added `cds-runtime`, an installable Python package giving CDS workloads
  one stable API for named database connections (issue #219):
  `connections.database(name)` resolves a connection from the profile's
  bound `sql-database` contracts through a fixed environment-variable
  convention (`CDS_DB_<NAME>_CONNECTION_URI`, or `CDS_DB_<NAME>_BACKEND`
  plus host/port/database/username/password fields, or a SQLite path),
  so application code no longer duplicates URI construction, SQLAlchemy
  setup, and cleanup in per-tool helper files. The core package is
  dependency-free (SQLAlchemy and the database drivers are lazily
  imported optional extras); imports are side-effect free; the API
  covers parameterized reads and writes (`fetch_all`/`fetch_one`/
  `execute`), bulk inserts with validated identifiers and bound
  parameters (`insert_rows`), explicit transactions, and deterministic
  engine cleanup (`close`, `close_all`, context managers); rows are
  plain dicts; PostgreSQL, MySQL, and SQLite are supported where
  compatible drivers are installed; errors are typed
  (`UnknownConnectionError`, `MissingConnectionSettingsError`,
  `DriverUnavailableError`, `ConnectionSetupError`,
  `QueryExecutionError`) and never carry credentials or unredacted
  connection URIs (query and engine-creation failures redact both the
  `_PASSWORD` field and a password embedded in `CONNECTION_URI`, plus
  any URI-shaped substring the driver echoes);
  field-based config validates `_PORT` as a port number (1-65535),
  accepts an empty `_PASSWORD` for password-less authentication, and
  resolves the legacy `_NAME`/`_USER` spellings (as shipped by the
  dagster module's environment block) as aliases for
  `_DATABASE`/`_USERNAME`; field-assembled postgres URLs pin the
  psycopg2 driver (`postgresql+psycopg2://`) so engine creation stays
  independent of SQLAlchemy's default-driver changes;
  `insert_rows` is one atomic transaction and
  quotes identifiers through the active dialect (backticks on MySQL,
  double quotes elsewhere); the
  dagster adapter rejects non-PostgreSQL URLs up front; the wheel ships
  `py.typed` and the Apache-2.0 license text; secrets flow exclusively
  through the configured secret provider's runtime placeholders, and
  platform metadata connections stay separate from workload contracts by
  name. A Dagster adapter (`postgres_url`) bridges named connections into
  Dagster's PostgreSQL storage configuration. Documented in
  `docs/runtime-api.md`, including migration from the legacy
  `shared/python/db/connection.py` helper.

- Added an automated restore verification for the built-in backup
  capability (issue #210): the Docker-gated runtime proof
  `tests/test_postgres_backup_restore_runtime.py` renders the shipped
  production overlay, writes marker data, seeds a clean Compose project
  with only the encrypted backup repository volume, proves a restore with
  the wrong key fails, restores the newest restore point, and verifies
  the data survived byte-for-byte within the profile's
  `rpoMinutes`/`rtoMinutes`. It runs in the Docker Runtime Smoke Test
  workflow or locally with
  `CDS_RUN_DOCKER_SMOKE=1 python -m unittest
  tests.test_postgres_backup_restore_runtime -v`; see
  `docs/backup-restore.md` section 5.

- Resolved the prompt trust-boundary tracking issue #1082: the open policy
  questions are now decided and documented in `.github/prompts/README.md`
  ("Decisions" section) — no `{{...}}` interpolation of untrusted content is
  enforced repo policy, sanitization lives deterministically in the invoking
  pipeline (`scripts/redact_secrets.py`, lint-enforced for every
  `actions/ai-inference` `file_input`), prompts needing issue context are kept
  under the `file_input` + metadata-only + redaction path rather than dropped,
  and external inference stays under those controls with local/self-hosted
  models evaluated and deferred. New deterministic control in
  `scripts/check_prompts.py` (`make lint-prompts`, CI, pre-commit): in any
  job that feeds `file_input` to `actions/ai-inference`, a `gh` command may
  no longer collect user-authored free-text fields (`title`, `body`,
  `comments`) — not via `--json`/`--field`, not via `gh api` (REST or
  graphql; always fails closed), and not via `--jq` (computed access like
  `.["tit"+"le"]` is unverifiable, so any `--jq` fails closed). Field lists
  are parsed as shell tokens (`shlex`) with a quote-aware regex fallback, so
  quoted (`--json "number,title"`), `--flag=value`, and `$(gh ...)` forms
  cannot bypass the lint, and dynamic or missing arguments (`--json "$FIELDS"`,
  `--json "$(cat fields.txt)"`, bare `--json`) fail closed as unverifiable.
  Also fail closed: `--template` on any gh command, and gh collection
  commands without `--json` (default tabular output includes titles)
  (github-advanced-security reviews on #1087 plus self-review). Tests in
  `tests/test_prompts_lint.py`; the agent-side protocol is recorded in
  `AGENT.md` "Agent-side untrusted-content protocol".

- Naming tests for the four remaining uncovered profiles
  (`tests/test_local_dagster_postgres_superset_dbt_profile.py`,
  `tests/test_local_haven_reference_profile.py`,
  `tests/test_local_kubernetes_example_profile.py`,
  `tests/test_production_kubernetes_example_profile.py`): each validates
  the profile with `cli.validator.validate_profile` and plans/renders it
  through the real pipeline (docker-compose or Helm, base and prod
  environment where applicable) (#1079). Profile contract coverage in
  `docs/contract-coverage.md` is now 8/8.

- Naming tests for the last two uncovered experimental modules
  (`tests/test_marquez_module.py`, `tests/test_airflow_module.py`): each loads
  its `module.yaml`, validates it against `cli/resources/module.schema.json`,
  and pins its provided contract(s) (#1080). Module contract coverage in
  `docs/contract-coverage.md` is now 18/18.

### Fixed

- Fixed the `publish-dockerhub` "Determine version" step for the restic image:
  it read the version with `cut -d= -f3`, but `ARG RESTIC_VERSION=0.19.1` has a
  single `=`, so the field was empty and every publish run failed with "Failed
  to determine base version for 'restic'" — skipping `update-fixture` and
  leaving stale digests in `tests/fixtures/signed-images.json`, which the daily
  scan kept re-flagging as vuln-scan issues (#1100, #1101). The field is now
  `-f2-`, with a regression test that executes the version step for every
  image (`tests/test_publish_images_workflow.py`).
- Production profiles no longer host-publish the Dagster webserver and
  Superset UI on all interfaces: both modules' compose services now carry a
  `production: { ports: [] }` mapping, so the TLS reverse proxy is the sole
  host-published entry point and `CDS-SEC-074` no longer fires on the
  `team-dagster-postgres-superset --environment prod` profile (#981).
  Learning/dev profiles keep their direct host publishes for local access.
- Fixed the experimental Loki `log-sink` contract to advertise its in-network
  listener port independently of the host-published port (#662).
- Fixed Compose parsing and runtime shell-variable interpolation in the
  learning Superset initialization command (#959, #1071).
- Switched learning Superset services to the pinned upstream development
  image, which includes the PostgreSQL and Redis drivers required to boot
  without a local build (#959, #1071).
- Fetched build assets (`cds get`/`cds update`) now also include builds
  nested in a service's `production:` mapping, so fetched profiles can
  still build the hardened production images (#959, #1071).
- Learning Superset services bind-mount `images/superset/superset_config.py`
  over the stock upstream image's pythonpath entry, so the secret key,
  session-cookie hardening, and optional OIDC login (#1064) apply without a
  local build; production keeps the image-baked copy (#959, #1071).

- Fixed the Windows CI test job (`test (windows-latest, 3.14)`) failing on
  PR #1057: `tests/test_maturity.py` asserted the literal POSIX string
  `env_file="/profiles/minimal/.env"`, but `str(Path(...))` yields
  backslashes on Windows; the assertion now uses
  `str(Path("/profiles/minimal/.env"))` so it matches on every platform.

- Fixed the experimental `marquez` module advertising unresolvable
  endpoints (issue #1029): it is a multi-service module (`marquez-api`,
  `marquez-web`), so the instance id is not a DNS name on the Compose
  network, yet its `lineage-sink` contract's `host`/`connectionUri` and
  the web UI's `MARQUEZ_HOST` all substituted `${service.host}` —
  consumers such as Dagster (`OPENLINEAGE_URL`) and the marquez web UI
  dialed `http://marquez:5000`, which nothing answers. Added the
  `${service.services.<name>}` placeholder (resolved during both
  planning and rendering through the renderer's service-naming rule,
  `_compose_service_name(id, service)`), pointed the marquez contract
  and `MARQUEZ_HOST` at `${service.services.marquez-api}`, and
  advertised `serviceName: marquez-api` on the provide entry. The E150
  provided-contract host cross-check now also covers the `lineage-sink`
  and `log-sink` kinds (both `host` and the `connectionUri` hostname,
  plus a `serviceName` advertisement mapped through the naming rule) on
  both render targets, so this mismatch class fails loudly at render
  time instead of silently shipping a broken endpoint; the `serviceName`
  field is now declared on both sink contracts.

- Cleared the 34 `.trivyignore` exceptions listed by the trivyignore audit
  (#1001) by fixing the findings at the source in the rebuilt images instead
  of ignoring them: the `postgres` image now pins the current `postgres:18`
  digest (also in `modules/warehouse/postgres/module.yaml`,
  `docker-compose.yml`, and `.cds/haven-dev-noinit.yml`) and replaces the
  base image's prebuilt `gosu` binary — which no `postgres:18` tag rebuilds
  with a fixed Go toolchain — with a build from the pinned gosu 1.20 tag
  compiled with Go 1.27.3; the `restic` image now compiles restic 0.19.1 (the
  latest upstream release) from source with Go 1.27.3 and the fixed module
  versions (`go-jose/v4` 4.1.6, `klauspost/compress` 1.18.7, `x/crypto`
  0.57.0, `x/net` 0.60.0, `x/text` 0.42.0, `grpc` 1.83.2) on a pinned Alpine
  base instead of using the vulnerable prebuilt `restic/restic` image; both
  source tarballs are verified against pinned sha256 hashes in the builder
  stages. Both rebuilt images scan clean of HIGH/CRITICAL findings with
  Trivy v0.75.0.

- Fixed `cds security` false positives on the reference production profile
  (#979): the secret-value rules CDS-SEC-001/003/033 no longer flag values
  that are entirely a renderer reference (`config.passwordFrom`,
  `${secrets.x}`, ...), which the declarative engine sees unresolved in the
  planned config but the renderer resolves to `${CDS_*}` placeholders at
  render time (new opt-in `ignoreRendererReferences` rule flag, rule set
  1.0.1); the CDS-SEC-013 secret-reuse check skips such references as well;
  the waiver-reason entropy exemption now also matches the flattened
  `security.waivers.*.reason` path (spec-level keys flatten without the
  `spec.` prefix); and CDS-SEC-031 matches the env file path relative to the
  project root (the same marker walk `resolve_env_file_path` uses, now shared
  via `cli/security_common.py`), so a bare project-root `.env` never fires
  when the CLI is invoked from another directory (e.g. with
  `CDS_PROFILE_PATH` set).

- Fixed the `helm` render target lacking the provide-side contract-host
  cross-check that the compose target got for issue #1010: the Kubernetes
  renderer only validated the consume side (E084), so a provided
  `http-service`, `sql-database`, `cache-service`, or `object-storage`
  contract advertising a host no rendered Kubernetes Service matches did
  not fail closed. `render_helm` now cross-checks every provided
  contract's advertised network host (the `host` field, the `endpoint`'s
  hostname part, or a `serviceName` resolved to the serving workload's
  Service name) against the Services the render produces for that module
  and fails with `E150` on mismatch (issue #1035).
- The `Kubernetes E2E` workflow now pins its k3d cluster name
  (`cds-k8s-e2e-ci`) instead of deriving `cds-head` from the detached-HEAD
  checkout, so CI logs and failure diagnostics are attributable at a glance
  (#1040).
- Fixed provided contract endpoints silently breaking when a module
  instance id differs from the module's compose service name (issue
  #1010, found in the review of #1006): contract resolution substituted
  `${service.host}` with the instance id, while the renderer names each
  compose service `<id>`/`<id>-<service>`, so profiles instantiating,
  e.g., the S3-compatible object-storage module as
  `id: object-storage` produced a contract endpoint no sibling container
  could resolve. The interpolation vocabulary now separates the two
  roles: `${service.host}` keeps resolving to the instance id (name
  prefixes such as backup volume names), while the new
  `${service.dns}` resolves through the same service-naming rule the
  renderer uses -- the compose name of the module's single service, or
  of its namesake service when auxiliary services exist (e.g.
  `postgres` next to its backup job containers); the explicit
  `serviceName` mechanism keeps covering other multi-service modules. The single-service modules advertising
  network endpoints (KeyDB-compatible cache, identity provider, reverse
  proxy, object storage, the Postgres warehouse, and the Superset BI
  app) now build them from `${service.dns}`, and the
  render cross-checks every provided `http-service`, `sql-database`,
  `cache-service`, and `object-storage` contract's advertised network
  host against the compose services it actually produced, failing with
  `E150` instead of shipping a broken endpoint.

- Fixed the restic file backup module's restore path for multiple bound
  sources (issue #1014, found in the post-merge review of #1011): the
  scheduler took one separate restic snapshot per source, but the restore
  service runs `restic restore latest`, which resolves to the single
  newest snapshot — so with two or more bound sources the default
  restore silently restored only the slot whose snapshot happened to be
  newest while still exiting 0. Each backup pass now takes one restic
  snapshot covering every mounted source, making the newest restore
  point complete and consistent across sources; the Docker-gated runtime
  proof now binds a second, volume-kind source (the dlt module's state
  volume) and verifies both markers byte-for-byte after the
  clean-environment restore. Also corrected the backup-module E143
  message's stray quote and the scheduler's restart-behavior comment
  (issue #1016).

- Fixed `make lint-renovate` (and therefore `make check`) failing: the
  Makefile ran the Renovate config validator with an unpinned `npx
  --package renovate`, which resolves to npm's stale `latest` (39.264.1,
  an older major than the repo's 44.x pins) and rejects the current
  `renovate.json`. The Makefile now uses the same exact pin as CI
  (`renovate@44.140.0`), the self-pin Renovate custom manager now also
  tracks the pins in `Makefile` and `AGENT.md` (so all three stay in sync
  with CI's), and AGENT.md documents the same pinned command (issue
  #1024).

- Fixed PostgreSQL dialect detection in the dagster workdir's event
  persistence: a `postgresql+psycopg` (v3) connection URI fell back to
  the sqlite/JSON DDL branch and would have failed against a live
  PostgreSQL server. The scheme is now matched by prefix, covering every
  `postgresql+<driver>` spelling (review follow-up on #996).
- Hardened cds-runtime's thread model and measured its read path
  (issue #986): the connection registry and the lazy engine creation
  are now lock-guarded (double-checked), so concurrent first calls share
  one connection and one engine per connection — the create-then-store
  window could leak engines on free-threaded builds (none observed
  under CPython's GIL in a 16-thread thundering herd). The per-query
  transaction overhead was benchmarked against a live PostgreSQL 16
  (`scripts/bench_cds_runtime.py`, medians of 200 iterations: a
  `fetch_all` over 1-100 rows takes ~0.1-0.6ms per call, of which
  ~0.1-0.4ms is the BEGIN/COMMIT round trip): immaterial, so reads keep
  their consistent snapshot transaction instead of a read-only
  `connect()` path. Findings and methodology documented in
  `docs/runtime-api.md` (Limitations).
- Fixed the fresh CVE findings the trivy DB update of 2026-10-27 raised
  for the superset image scan: dnspython 2.7.0 (bundled with the
  apache/superset base image) is vulnerable to CVE-2026-114605 (DoS via
  excessive DNS compression pointer traversal) — the superset
  requirements now pin `dnspython>=2.9.0`, installed over the base
  image's copy by the existing `--upgrade` mechanism. The same scan run
  flagged CVE-2026-111497 (brotli) and CVE-2026-84782 (openssl) in the
  Debian base packages; both are resolved by the image build's own
  `apt-get upgrade` (fixed debs `1.0.9-2+deb12u1` and
  `3.0.22-1~deb12u2`), so no ignore entries are needed: a clean rebuild
  scans green (verified locally with trivy 0.75.0 against the
  rebuilt image).
- Fixed the `Trivyignore Audit` workflow falsely reporting every
  `.trivyignore` exception as resolved (#983, also #963): Trivy
  automatically loads `.trivyignore` from the working directory, so the
  audit scan had filtered out the exceptions it was meant to check. The
  scan now explicitly disables that default ignore file and keeps unfixed
  findings visible, since they remain present in the image and Trivy's
  fixed-version data can fluctuate. An exception may be removed only when
  its vulnerability no longer appears in any published image.

- Fixed a template-authoring footgun in the `production` tag introduced
  with #665: the `production: { ... }` mapping form on a volume-mount
  entry was silently ignored and leaked its key into the rendered Compose
  output (and mounted unconditionally on the helm target). Validation now
  fails closed with `E144` for every marker shape or position the
  renderer cannot honor: mount entries in a service's base `volumes`
  list and nested inside its `production` mapping (boolean only), and
  non-boolean/non-mapping markers on services and volume definitions; on
  entries of any other service list field (`ports`, `labels`, ...) no
  marker is implemented, so any marker there is rejected as well. Both
  render targets re-check marker shapes when rendering a stored plan,
  since plans can be loaded from files (#958 post-merge review, #988).

- Hardened the `enabledFrom` / `conditionallyEnabledFrom` guards the same
  way (#988): the renderer resolves these markers as string config-path
  expressions, so any other shape — e.g. the boolean `enabledFrom: true`,
  a plausible mistake given `production: true` is a boolean gate —
  crashed the renderer with an untyped `TypeError`/`AttributeError`, and
  on entries of service list fields where no marker is implemented the
  key leaked into the rendered output. Validation now fails closed with
  `E145`: a non-empty string is required on services, volume definitions,
  volume-mount entries, and healthchecks (including inside a service's
  `production` mapping), and any `enabledFrom` on other entries is
  rejected. Both render targets re-check marker shapes when rendering a
  stored plan, converting the former render-time crash into a typed
  validation error. The marker-shape scans also cover the
  `implementation.kubernetes` block, where the same classes applied:
  an unguarded `enabledFrom` on a workload crashed the helm renderer,
  bad shapes on volume specs silently misbehaved, and unsupported
  marker positions (`production` on workloads and configMap entries,
  `enabledFrom` on configMap entries) were silently ignored.

- Fixed the E150 provided-contract host check silently skipping
  scheme-less `host:port` endpoints (issue #1036): the check only
  validated values matching the DNS-label pattern, so an endpoint like
  `garage:3900` (no scheme, but with a port) never got cross-checked
  against the rendered services, while a full URL (`http://garage:3900`)
  did. The host part of a scheme-less `host:port` value is now extracted
  and cross-checked for every advertised field (`host`, `endpoint`,
  `connectionUri`) on both render targets; values with a non-numeric
  port or an IPv6 literal are still skipped as outside the cluster
  namespace.

- Raised the `pyasn1` floor in `images/superset/requirements.in` from
  `>=0.6.4` to `>=0.6.5` so recompiling the Superset image lock can never
  resolve back to the vulnerable 0.6.4 (CVE-2026-119194/95/96/97;
  Dependabot alerts #65-#68); the hash-locked `requirements.txt` already
  pins `pyasn1==0.6.5`.

- Documented the split bot ownership of pip locks (issue #1048):
  `.github/dependabot.yml` now states it manages only the root manifests
  (`pyproject.toml`/`uv.lock`), while the `images/*` pip-compile locks
  (including `images/dagster/`) are Renovate-managed — so Dependabot alerts on
  those manifests show "cannot update to the required version" by design and
  are remediated by Renovate PRs. `docs/image-scanning.md` carries the same
  note next to the lock-maintenance rules.

- The experimental airflow placeholder module now declares
  `productionSuitable: false` like every other `modules-experimental/`
  module: the schema default is `true` and CDS-SEC-073 only fires on an
  explicit `false`, so a staging/prod profile could previously compose it
  unflagged (#1080).

## [0.14.0] - 2026-10-26

### Added

- Added a production/learning dividing line for module templates: a
  `production: true|false` tag on services, volumes, and volume mounts
  keeps the entry only in (respectively: only outside) production
  profiles, and the `production: { ... }` mapping form merges
  production-only fields over a service's base definition. Learning
  profiles stay easy to install (pullable stock images, no local builds);
  the helm target is treated as production-only for now (#665).

- Added the built-in backup and disaster-recovery capability for the
  warehouse/postgres module (#665): a pgBackRest sidecar taking scheduled
  full and incremental backups with continuous WAL archiving
  (point-in-time recovery) into a client-side encrypted repository
  volume, `pgbackrest check` integrity verification after each backup,
  retention via `config.backup.retentionDays`, and a gated one-shot
  `postgres-restore` service; the module now provides a `backup-target`
  contract (`postgres.backup-target`) for `spec.backup.target.contractRef`.
  Validation fails closed with `E141` (backup enabled without an encryption
  key reference), `E142` (backup enabled on the not-yet-supported helm
  target), and `E143` (backup enabled outside a production profile); the
  shipped production overlays bind the new provider. See
  `docs/backup-restore.md` and `docs/modules.md`.

- Added reverse-proxy dynamic route generation from contract bindings
  (#581): a `reverse-proxy` provider's `config.routes` entries (public
  `host`, `target` module id) render deterministic Traefik file-provider
  configuration next to the compose file, resolving each target's
  `http-service` contract `host`/`port` instead of Docker label
  autodiscovery; multi-service providers advertise the serving compose
  service through a `serviceName` field (dagster, dbt), and the backend
  address is cross-checked against the services the render actually
  produced, failing with `E136` instead of emitting an address nothing
  answers; a route's `target` may name a specific contract as
  `<module-id>.<contract-name>` when the target exposes several
  http-service endpoints; invalid routing inputs (unknown target,
  unresolvable endpoint or a backend port outside 1-65535, an entrypoint
  the proxy does not declare in config.entryPoints, an undeclared
  certificate alias, or a host duplicated
  by an earlier route of the same proxy) also fail rendering
  with `E136`, the
  optional `certificate` alias wires the profile's certificate mapping
  into the router's certificate files (TLS-enabled routers only; the
  alias is always validated), and stale generated route files
  are removed when their routes (or proxy) disappear from the profile.
- Added TLS metadata to the shared reverse-proxy contract (#579): the
  protocol-version window (`tlsMinVersion`/`tlsMaxVersion`), SNI strictness
  (`tlsSniStrict`), and certificate-verification status (`certVerified`)
  fields, so downstream consumers can make provider-neutral TLS decisions;
  the Traefik provider exposes them from its existing TLS policy
  configuration.
- Added a vendor-neutral `backup-target` shared contract and an optional
  `spec.backup` profile block (per-workload-class retention, RPO/RTO,
  secret-referenced encryption, and restore-verification settings) with
  validation codes `E130`-`E135`, documented in `docs/backup-restore.md`
  (#210).
- Added a certificate reference mechanism parallel to secret aliases
  (issue #578): profiles declare `spec.certificates.values` aliases mapping
  to externally managed material via `certEnv`/`keyEnv` environment variable
  names, and module config references `certificates.<alias>.cert|.key`,
  which validate (`E129` for unknown aliases or malformed references) and
  render as `${CDS_*}` runtime placeholders. CDS never generates, reads,
  or embeds certificate or private key material.
- Added provider-neutral TLS requirements to contracts (issue #577): a
  `consumes` entry can declare `tls: required`, and providers advertise
  `tls: true` (boolean literal or `${config.<path>}` reference) in their
  provide entry's contract spec; a required binding whose provider does not
  advertise TLS fails validation with `E128`, while omitting the field keeps
  existing profiles valid. Extended the `sql-database`, `cache-service`, and
  new `http-service`/`reverse-proxy` shared contract documents with the
  optional `tls` field.
- Added a production-aware `host.rootless` result to `cds preflight` (a rootful
  daemon is advisory on local profiles and a warning on production-class
  profiles) and documented rootless Docker compatibility expectations in
  `docs/rootless-docker.md` (#207).
- Extended the `.trivyignore` audit to list still-reported exceptions that
  have a fixed package version available (#934).
- Added a daily `Trivyignore Audit` workflow and
  `scripts/audit_trivyignore.py` that report `.trivyignore` exceptions whose
  vulnerability no longer appears in the published images, and exceptions
  expiring within 14 days, in a single `trivyignore-audit` issue.
- Added declarative runtime confinement: `spec.security.runtime.confinement`
  (profile default) and `modules[].security.confinement` (per-module
  override) select seccomp, AppArmor and SELinux settings that `cds render`
  writes into each service's `security_opt`; invalid or unsupported values
  fail validation with error `E127`, and `--target=helm` warns (`W127`)
  that confinement is not rendered for Kubernetes (#207, #897, #894).
- Added code-enforced rules CDS-SEC-090 (production runtime policy:
  capabilities, privilege escalation, non-root user and read-only root
  filesystem evaluated together, with a reasoned
  `spec.security.waivers.runtimePolicy` waiver) and CDS-SEC-091 (disabled
  seccomp/AppArmor/SELinux confinement) for production profiles; services
  that grant a kernel-escape capability via `cap_add` (`ALL`, `SYS_ADMIN`,
  `SYS_MODULE`, `SYS_PTRACE`, `SYS_RAWIO`, `DAC_READ_SEARCH`) also violate
  CDS-SEC-090, and an applied or ineffective runtime-policy waiver emits
  warning `W103`/`W104` (#207, #895, #898).
- Added an optional `authentication` advertisement to the `sql-database`
  and `cache-service` shared contracts (issue #206): providers advertise
  their host authentication method (`scram-sha-256`, `md5`, `trust`) or
  whether the cache rejects unauthenticated clients (`required`), and
  production profiles fail validation when a relational provider has no
  advertisement or advertises `trust` (`E139`, with a `W105` warning for
  the legacy `md5` method), a cache accepts unauthenticated clients
  (`E137`), or a credential field is not a `secrets.<alias>` reference
  declared `required: true` (`E138`).
- Added the `team-dagster-postgres-superset` example profile: the stable
  Dagster + Postgres + Superset stack composed via `extends` and shaped
  for a small team on a single shared Docker host — a Traefik edge
  terminating TLS and routing the UIs by hostname (hand-authored
  file-provider routes, HTTP redirected to HTTPS), an authenticated cache
  (required `CDS_CACHE_PASSWORD` secret), and a `prod` overlay applying
  the production security policy (explicit `scram-sha-256` host
  authentication, encrypted restore-verified backup policies, runtime
  policy waiver); the profile README documents the multi-user access
  model and the SSO (Keycloak `forwardAuth`) follow-up path.

### Changed

- **Breaking:** profiles with `metadata.environment: production` must now
  declare `spec.backup` with encrypted, restore-verified policies for every
  workload class they run, or `cds validate` fails with `E133`-`E135`; the
  example `prod` overlay declares them (#210).
- **Breaking:** the postgres module configures host (TCP) authentication
  explicitly through initdb `--auth-host` with a new
  `config.authentication.hostAuth` knob (default `scram-sha-256`), and the
  keydb module takes its cache password through `config.passwordFrom` (a
  `secrets.<alias>` reference) and enforces it with `--requirepass` in
  both the Compose and Kubernetes renderings, so a configured password
  makes the cache reject unauthenticated clients; profiles setting the
  removed keydb `config.password` field now fail config validation, and
  the healthcheck verifies an authenticated `PONG` (#206, #291).
- Production-class profiles whose services do not meet the runtime policy,
  including the bundled production overlay, now fail `cds security` and
  `cds test` with HIGH findings until the services are hardened or waived
  (#895).

### Fixed

- Removed the eight resolved `.trivyignore` exceptions (util-linux and
  libbrotli1 CVEs) now that the published images no longer report them (#934).
- Bumped `filelock` to 4.2.4 in `uv.lock` to resolve the open Dependabot
  advisories (fixed in 4.2.1) (#925).
- Pinned `brotli>=1.2.1` in the Superset image lock to fix the critical
  out-of-bounds write (GHSA-9v8h-qqgm-4m35) that failed the image scan (#926).
- Added a time-boxed `.trivyignore` exception for the not-yet-published
  libbrotli1 fix (CVE-2026-111497) in the Superset base image (#927).
- Made `tests/test_dagster_entrypoint_backend_guard.py` OS-independent: the
  POSIX shell used to exercise the entrypoint guard is resolved from PATH or
  a Git for Windows install, and the tests skip with a clear message when no
  shell is available instead of failing with `FileNotFoundError` (#564).
- Fixed the dbt example profile failing `cds validate` with `E041`: its
  superset instance never bound the required `analytics-database`
  contract after it became mandatory, and added a regression test that
  validates every shipped profile and environment overlay (#940).
- Made `tests/test_dagster_entrypoint_backend_guard.py` OS-independent: the
  POSIX shell used to exercise the entrypoint guard is resolved from PATH or
  a Git for Windows install, and the tests skip with a clear message when no
  shell is available instead of failing with `FileNotFoundError` (#564).

## [0.13.0] - 2026-10-22

### Added

- Implemented CDS-SEC-032: `cds security`/`cds test` now flag a `.env`
  file readable or writable by group or others (new `file-permissions`
  rule scope and `permissions` condition; skipped on Windows), and
  `cds init` explicitly creates `.env` as owner-only (`0600`) (#357).
- Added a versioned Docker host and daemon hardening baseline
  (`docs/docker-host-hardening.md`) separating CDS-enforced controls from
  operator responsibilities, and advisory `host.*` checks in `cds preflight`
  for rootless mode, user namespaces, seccomp, logging, live restore, and
  daemon socket exposure (#211).

### Fixed

- Fixed a flaky Superset init failure (duplicate-key error while creating the
  metadata tables) when the web process starts concurrently, by retrying the
  idempotent init steps a bounded number of times, and added container-log
  capture to the Docker runtime smoke test on failure (#890).
- Fixed `cds get` crashing with an uncaught `NotImplementedError` on Windows
  when a Dockerfile `COPY`/`ADD` source starts with a rooted path such as
  `/abs`; such sources are now skipped like other absolute paths (#904).
- Bumped the Superset image's hash-locked `uv` to 0.13.4 to resolve
  PYSEC-2026-4321 flagged by `pip-audit` (#907).
- Moved the Superset image to the `apache/superset:6.1.0-py311` base and
  re-locked its dependencies for Python 3.11, which allows Pillow 13.0.0 and
  resolves the Pillow advisories flagged by `pip-audit` (#907).

## [0.12.0] - 2026-10-16

### Added

- Added code-enforced, literal-value secret scanning for the formerly
  deferred rules CDS-SEC-006 (rendered Compose model), CDS-SEC-030
  (generated files) and CDS-SEC-071 (CLI output): `cds render`/`cds up`
  now refuse to write `docker-compose.yml` or Helm chart files containing
  a resolved secret (error `E096`), `cds plan`/`validate`/`test`/`security`
  redact such values from output with a `W102` warning, and `cds security`
  reports CDS-SEC-006 findings. CDS-SEC-006 now covers only rendered
  Compose output; its file and CLI halves moved to CDS-SEC-030/071 (#356).
- Added opt-in source trust for `cds get`: a trusted-source allowlist
  (`cds config set get.allowedSources` or `CDS_GET_ALLOWED_SOURCES`,
  also enforced for `cds list --remote`) and `cds get --commit <sha>`
  pinning (12-40 hex characters) that fails closed on a mismatch, verifying
  via the GitHub API when an archive lacks the full SHA.
  `CDS_GET_ALLOWED_SOURCES=*` allows any source for a single run; the
  variable is never read from `.env`. The
  resolved commit SHA is now recorded in `.cds/get-manifest.json` (#849).

- Added an opt-in CDS CLI update notice: `cds check-update` queries the
  public PyPI index on demand, and `CDS_CHECK_UPDATES=1` prints a
  non-failing stderr notice (24h cache) when a newer release exists.
  Nothing is checked by default (#841).
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

- Added the Docker Hub-published images (`dagster`, `dagster` hardened,
  `superset`, `dbt`, `dlt` under `docker.io/ronaldsoeverein/`) to
  `tests/fixtures/signed-images.json` and made the `update-fixture` job
  refresh them after `publish-dockerhub` succeeds, so `cds report` finds
  signature/SBOM/provenance evidence for default `image.source: registry`
  pulls instead of always emitting W101 (#824).

- Tagged GitHub releases are now explicitly published after creation, even
  when the release action leaves them as drafts. Release notes, attached
  assets, and the separate PyPI publishing flow remain unchanged (#765).

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

- Validated `compose_to_module` `--name`/`--category` against the
  `^[a-z0-9-]+$` module-id pattern before writing anything to disk, and
  confined the derived default output path under `modules/`, so invalid
  names fail fast and traversal values can no longer escape the modules
  tree, without changing explicit `--output` handling (#685).

### Changed

- Hash-locked the Python dependencies of the `dlt`, `dagster`, `dbt`, and
  `superset` images: each `requirements*.in` lists the direct dependencies,
  `requirements*.txt` is compiled with `uv pip compile --generate-hashes`,
  and the Dockerfiles install with `--require-hashes`, so unverified
  transitive packages fail the build (SonarCloud python:S8544). The Dagster
  image now installs a single self-contained lock per database backend, and
  Renovate maintains the locks through its `pip-compile` manager. CI now
  audits each lock with `pip-audit --require-hashes` (including transitive
  dependencies) and fails when a lock is out of date with its `.in` file.
  Superset's `uv` is installed from a hash lock, and the dbt image builds its
  sdist-only dependency with hashed build requirements and
  `--no-build-isolation`. See
  `docs/image-scanning.md` (#625).

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

- Bound the Keycloak module's published host port to `127.0.0.1`, so the
  admin console is reachable from the local machine only, matching the
  localhost-only convention used by the Postgres module; Dagster and
  Superset are unchanged (#832).

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
