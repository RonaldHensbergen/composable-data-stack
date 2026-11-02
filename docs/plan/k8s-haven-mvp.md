# Kubernetes MVP Runbook: From a Fresh Directory to v1.0.0, with Haven Parity

Version: 0.1
Status: Draft — execution runbook, drafted 2026-11-02. Fill in the signoff
table (section 13) while running it; promote to a committed plan once a cycle
has completed.
Related: [docs/kubernetes.md](../kubernetes.md),
[docs/haven-parity-plan.md](../haven-parity-plan.md),
[docs/production-hardening-checklist.md](../production-hardening-checklist.md),
[docs/release-strategy.md](../release-strategy.md), [RELEASE.md](../../RELEASE.md),
[docs/roadmap.md](../roadmap.md)

## 1. What this MVP proves

One cycle, starting from a fresh directory on a machine with only Docker
installed, proves four claims and ends in a release:

1. **Kubernetes works end to end.** An operator installs the `cds` CLI, stands
   up a local Kubernetes cluster, and runs a CDS profile with `--target helm`
   through the full lifecycle (`up` → `state` → `down`) with recorded evidence.
2. **The deployment is production-ready.** The production-style profile passes
   the fail-closed gates (`validate`, `security`, `preflight`) under the `prod`
   overlay and maps onto the 13-control
   [production hardening checklist](../production-hardening-checklist.md), with
   deviations filed as issues rather than silently waived.
3. **Haven parity is measured, not asserted.** `cds maturity` reports the
   profile's Basic/Automated/Observed/Secured/Resilient tier, the Haven+ platform
   capabilities (GitOps, observability, security, database operator, backup,
   networking) are checked one by one against
   [docs/haven-parity-plan.md](../haven-parity-plan.md). Every blocking gap is
   mandatory in-cycle work (section 2): the cycle pauses at the owning gate
   until the issue is closed, rather than shipping a reduced scope.
4. **The cycle ends in a v1.0.0 release**, cut per
   [RELEASE.md](../../RELEASE.md) and the release gates in
   [docs/release-strategy.md](../release-strategy.md).

### Non-goals

- No claim of certified Haven+ compliance — Haven parity here means "the
  reference architecture's baseline capabilities are either present in CDS or
  tracked as issues", per the non-goals of
  [docs/haven-parity-plan.md](../haven-parity-plan.md#6-non-goals).
- No multi-node, multi-cloud, or bare-metal clusters; one local k3d cluster is
  the MVP target.
- No `CDSProfile` CRD/operator (explicitly deferred in
  [ADR 0001](../adr/0001-kubernetes-render-target.md)).
- The Docker Compose path is not modified; Kubernetes remains a second render
  target.

## 2. Blocking issues — mandatory work in this cycle

These issues are open as of 2026-11-02. Each one is **required work, not a
choice**: it is assigned to the gate that resolves it, split into ≤2 h subtasks
under its tracking issue, and that gate does not sign off until the issue is
closed. The target is the most production-worthy outcome, so the cycle pauses
at a blocking gate instead of shipping a reduced scope. Only the explicitly
post-MVP items at the end of this section may still be open after Gate 5.

| Issue | Resolved in | What "done" means (gate exit requires closure) |
| --- | --- | --- |
| [#690](https://github.com/RonaldHensbergen/composable-data-stack/issues/690) | Gate 0, task G0.0 | PR [#1105](https://github.com/RonaldHensbergen/composable-data-stack/pull/1105) (`feat/690-helm-up-self-contained`) merged to main: `cds up --target helm` builds/imports images and ensures a reachable cluster by itself. The maintainer merges it before Gate 1 starts; the MVP does not run on a workaround. |
| [#1031](https://github.com/RonaldHensbergen/composable-data-stack/issues/1031) | Gate 1, task G1.8 | `waitFor` init-gates are validated against the default-deny NetworkPolicy baseline; a conflicting gate fails the render closed. |
| [#1023](https://github.com/RonaldHensbergen/composable-data-stack/issues/1023) | Gate 1, task G1.9 | External egress for Helm workloads is declarable and rendered under the default-deny baseline, surfaced as `CDS-K8S-008` findings. |
| [#1033](https://github.com/RonaldHensbergen/composable-data-stack/issues/1033) | Gates 2/3, tasks G2.8 and G3.4 | Reverse-proxy route targets are derived as NetworkPolicy edges, so an ingress fronting a ClusterIP service needs no hand-declared `networkPolicy.ingress` rules. |
| [#665](https://github.com/RonaldHensbergen/composable-data-stack/issues/665)/[#668](https://github.com/RonaldHensbergen/composable-data-stack/issues/668)/[#669](https://github.com/RonaldHensbergen/composable-data-stack/issues/669); `E142` | Gate 2, task G2.9 | A `backup-target` provider renders and runs on Helm (pgBackRest sidecar or a restic-shaped module), and a backup **plus a restore verification** run against the prod profile. Policy-only declaration is not accepted. |
| [#1075](https://github.com/RonaldHensbergen/composable-data-stack/issues/1075) (+ Keycloak/Traefik have no `kubernetes:` block in their `module.yaml`) | Gate 3, task G3.3 | `modules/identity/keycloak` and `modules/integration/traefik` have `kubernetes:` implementations, and `local-haven-reference` renders and runs on `--target helm`. |
| [#682](https://github.com/RonaldHensbergen/composable-data-stack/issues/682), [#974](https://github.com/RonaldHensbergen/composable-data-stack/issues/974) | Gate 3, task G3.3 | Keycloak consumes a generic `sql-database` binding (no hardcoded `KC_DB: postgres`) and defaults to production web serving under the prod overlay (`E140` enforced). |
| [#883](https://github.com/RonaldHensbergen/composable-data-stack/issues/883) | Gate 3, task G3.4 | A provider-neutral HTTP routing contract exists and Traefik wiring is resolved from the contract graph, not hand-authored. |
| [#768](https://github.com/RonaldHensbergen/composable-data-stack/issues/768) | Gate 3, task G3.5 | A CloudNativePG-shaped operator module provides `sql-database` on Helm as the production warehouse; StatefulSet+PVC Postgres stays the dev path. |
| [#369](https://github.com/RonaldHensbergen/composable-data-stack/issues/369), [#1066](https://github.com/RonaldHensbergen/composable-data-stack/issues/1066), [#1076](https://github.com/RonaldHensbergen/composable-data-stack/issues/1076) | Gate 3, task G3.6 | Observability providers run live on Helm: the Grafana metrics module is delivered (#369), Loki/otel-collector are wired into the profile, and the no-new-privileges exec failure (#1066) and the OTLP log-sink gap (#1076) are fixed — the **Observed** tier is reached with providers running, not merely declared. |
| [#1065](https://github.com/RonaldHensbergen/composable-data-stack/issues/1065) | Gate 4, task G4.2 | Docker smoke-test CI covers the experimental modules this MVP runs (loki, otel-collector). |

Already landed — do not re-file: the Helm render target with NetworkPolicy
generation (#608, #75), kind-based CI validation (#76), Kubernetes docs and
example profiles (#77), the production hardening checklist (#72), FluxCD
GitOps rendering (#767), the `backup-target` contract (#210), the Loki
log-sink provider (#662), the `trace-sink`/`metrics-sink` contracts and
otel-collector module (#777), the Keycloak module with the shared
`oidc-provider` contract and Superset consumer (#370, #1064), and the
`cds maturity` tier report (#769, #1059).

Post-MVP, tracked but **not** blocking this cycle (each keeps its issue and
milestone): [#776](https://github.com/RonaldHensbergen/composable-data-stack/issues/776)
service mesh and
[#1068](https://github.com/RonaldHensbergen/composable-data-stack/issues/1068)
workload identity — the **Secured** tier is reachable via TLS + identity broker
without a mesh;
[#812](https://github.com/RonaldHensbergen/composable-data-stack/issues/812)
persisted Helm context — quality of life, not a correctness gap;
[#976](https://github.com/RonaldHensbergen/composable-data-stack/issues/976)
Marquez promotion — lineage UI, not a Haven+ baseline capability.

There is currently no tracking issue for "1.0.0 release criteria"; Gate 5
section 10.1 defines them. File an issue for that when this runbook is first
executed.

## 3. Starting point and execution paths

**Starting point:** a fresh directory on a machine with Docker Engine (with the
Compose v2 plugin) installed and nothing else — no repository clone, no Python,
no kubectl, no Helm, no k3d. At least 8 GB of Docker memory and 10 GB of free
disk (see [docs/kubernetes.md](../kubernetes.md#prerequisites)).

Two execution paths; the MVP uses Path A and falls back to Path B where noted:

- **Path A — no clone (the actual MVP scenario).** Install the CLI from PyPI,
  fetch profiles with `cds get --remote`, select published registry images,
  create the cluster with the standalone `k3d` CLI.
- **Path B — repository clone.** `git clone` the repo and use the branch-scoped
  harness (`make k3d-build` / `k3d-up` / `k3d-install` / `k3d-e2e` /
  `k3d-down`, see [docs/kubernetes.md](../kubernetes.md#use-the-isolated-k3d-harness)),
  which builds local images and imports them into k3d.

Record exact versions of everything (`docker version`, `cds --version`,
`k3d version`, `kubectl version --client`, `helm version`) in the environment
record (section 13) — reproducibility from the version set is part of the
evidence.

## 4. Gate overview

| Gate | Goal | Exit criteria | Estimated effort |
| --- | --- | --- | --- |
| [0 — Bootstrap](#5-gate-0--bootstrap-from-a-fresh-directory-docker-only) | Fresh dir + Docker → CLI, tooling, profiles, cluster, #690 merged | `cds --version`, `kubectl get nodes` green, profiles fetched on a main SHA including PR #1105, Compose smoke run passes | 4–6 h |
| [1 — Kubernetes MVP](#6-gate-1--kubernetes-mvp-on-a-local-cluster) | Full Helm lifecycle on the local cluster | `cds up/state/down --target helm` green, all workloads healthy, boundary checks pass, #1031 and #1023 closed, evidence recorded | 8–10 h |
| [2 — Production-ready](#7-gate-2--production-ready-posture-on-kubernetes) | Prod overlay passes fail-closed gates on Helm, with real backup | `validate`/`security`/`preflight` diff-clean, checklist mapped with every control working on Helm, backup + restore verified (#665/#668/#669, E142), #1033 closed, maturity tier **Secured** recorded | 10–12 h |
| [3 — Haven parity](#8-gate-3--haven-parity) | Haven-shaped profile runs on Helm | Identity + TLS ingress + operator-managed DB + backup + live observability + GitOps on `--target helm` (#1075, #682, #974, #883, #768, #369, #1066, #1076 closed), maturity tier **Secured**, gap matrix recorded | 5–8 working days |
| [4 — Evidence and issues](#9-gate-4--evidence-and-issue-triage) | Everything reproducible, every remaining gap tracked | Signoff table complete, one issue per non-blocking gap (#1065 included), docs/changelog updated | 3–4 h |
| [5 — v1.0.0 release](#10-gate-5--v100-release) | Cut and publish v1.0.0 | All section-2 blocking issues closed, tag pushed, release published with evidence assets, announcement done | 4–6 h |

Total: roughly 3–4 weeks of ≤2 h tasks — Gate 3 is the heavy gate, because the
Keycloak/Traefik Helm implementations and the database operator are feature
work, split into ≤2 h subtasks under their tracking issues. The primary testing
command per gate and the signoff table are in
[section 13](#13-testing-commands-and-signoff).

## 5. Gate 0 — Bootstrap from a fresh directory (Docker only)

- [ ] **G0.0 Merge #690 (PR #1105)** (maintainer action, ~0.5 h to verify).
  Before Gate 1 starts, `feat/690-helm-up-self-contained` is merged to main so
  `cds up --target helm` is self-contained (builds/imports images, ensures the
  cluster). Then re-fetch the profiles so `.cds/get-manifest.json` records a
  main commit SHA that includes the merge. Exit: fetched SHA contains #1105.
- [ ] **G0.1 Install the CDS CLI** (~0.5 h). `pipx install composable-data-stack`
  (or `pip install --user`); verify `cds --version` and `cds --help` work outside
  any source checkout. Exit: CLI runs from the fresh directory.
- [ ] **G0.2 Install cluster tooling** (~0.5 h). Install `k3d` (v5+), `kubectl`,
  and Helm 3/4 per [docs/kubernetes.md](../kubernetes.md#prerequisites). Exit:
  all three binaries on `PATH`.
- [ ] **G0.3 Fetch the profiles without cloning** (~1 h).
  `cds get local-kubernetes-example --remote RonaldHensbergen/composable-data-stack --into ~/cds-mvp`
  and likewise `production-kubernetes-example` and `local-haven-reference`;
  verify with `cds list profiles --local ~/cds-mvp`. Inspect
  `.cds/get-manifest.json` (records the resolved commit SHA and file hashes).
  Exit: all three profiles validate as fetched assets.
- [ ] **G0.4 Create the local cluster** (~0.5 h).
  `k3d cluster create cds-mvp --agents 1 --port "38080:80@loadbalancer"` (any
  free host port block; avoid 5000, see
  [docs/kubernetes.md](../kubernetes.md#use-the-isolated-k3d-harness));
  `kubectl get nodes`. Exit: one Ready node, kubeconfig written by k3d.
- [ ] **G0.5 Decide the image supply path** (~1 h). With #690 merged (G0.0),
  `cds up --target helm` builds and imports local images itself on Path B.
  Path A (no clone) instead pulls the published, signed, scanned images — one
  command, persisted for the fresh directory:

  ```bash
  cd ~/cds-mvp
  cds config set image.source registry   # project default for render/up/test/report
  cds config set target helm             # optional: make helm the default target
  ```

  or per command: `cds up ... --target helm --image-source registry`. Pin a
  digest via the profile's module config where available (`config.image.tag` /
  `config.image.registry`; `dockerhub` default rewrites to
  `docker.io/ronaldsoeverein/<module>:<tag>`, `ghcr` to
  `ghcr.io/ronaldhensbergen/cds-<module>:sha-<sha>` — GHCR has no
  version-derived tags; see [docs/image-signing.md](../image-signing.md)). Add
  `--hardened` for the hardened variant. Note: only the dagster and superset
  modules support `registry` source today; postgres and keydb use public
  upstream images that the cluster pulls directly. Exit: the profile's images
  are resolvable by the cluster — registry pulls need no import step, unlike
  locally built images (`imagePullPolicy: Never` + `k3d image import`).
- [ ] **G0.6 Compose smoke run first** (~1 h). `cds init` the simplest fetched
  profile, fill `.env` (mode 0600), `cds up` (Compose default), `cds state`,
  `cds down`. This proves the CLI + secrets flow before Kubernetes is
  introduced. Exit: all services healthy, clean down.

Gate 0 exit: G0.0–G0.6 checked, #1105 merged and reflected in the fetched
commit SHA, versions recorded in the signoff table.

## 6. Gate 1 — Kubernetes MVP on a local cluster

Run against `local-kubernetes-example` (the `local-dagster-postgres-superset`
stack on Helm) in namespace `cds-mvp`, release `cds`, context `k3d-cds-mvp`.

- [ ] **G1.1 Target-aware static gates** (~1 h).
  `cds validate local-kubernetes-example --target helm`,
  `cds security ... --target helm`, `cds test ... --target helm`,
  `cds maturity ...`. Exit: all pass (or findings match the documented set).
- [ ] **G1.2 Render and inspect the chart** (~1 h).
  `cds render local-kubernetes-example --target helm --output chart/`, then
  `helm lint chart/`, `helm template chart/`. Inspect the rendered
  NetworkPolicies, ServiceAccounts (no token mount by default), probes, PVC
  templates per [docs/kubernetes.md](../kubernetes.md#network-isolation-and-least-privilege).
  Exit: lint/template clean; no secret values in the chart (the sentinel
  property documented in [docs/plan/k8s-progress.md](k8s-progress.md)).
- [ ] **G1.3 Bring the stack up** (~2 h including troubleshooting).
  `cds init`, fill secrets, then
  `cds up local-kubernetes-example --target helm --kube-context k3d-cds-mvp --namespace cds-mvp --release cds --timeout 300`.
  Exit: release installed, every workload Rollout-complete.
- [ ] **G1.4 Health and access** (~1 h).
  `cds state local-kubernetes-example --target helm ...` shows all workloads
  healthy; reach Dagster and Superset via port-forward (or NodePort if the
  profile's `kubernetesService` config is used) and verify the HTTP boundary.
  Exit: both UIs answer.
- [ ] **G1.5 Boundary checks** (~2 h). The checks `make k3d-e2e` runs
  ([scripts/k8s/e2e.sh](../../scripts/k8s/e2e.sh)): Postgres reachable from the
  Dagster pod only through the contract-derived NetworkPolicy edge, Dagster
  webserver gRPC path (ADR 0002), Superset login page. From a fresh directory
  run these as explicit `kubectl exec` checks; from a clone use
  `CDS_E2E_KEEP_CLUSTER=1 make k3d-e2e`. Exit: pass = all, fail = 0; every
  failure is blocking work resolved before signoff (section 2), not a filed
  note.
- [ ] **G1.6 Teardown** (~0.5 h). `cds down ... --target helm` (PVC retention is
  the default; verify with `kubectl get pvc`), confirm the namespace is clean
  of release resources, `k3d cluster delete cds-mvp` when the gate is done.
  Exit: no leftover workloads; PVCs retained unless `--delete-pvcs`.
- [ ] **G1.7 Record evidence** (~0.5 h). Save command outputs, `cds state`
  output, pod descriptions (`kubectl describe pod`), events, and timings into
  the evidence directory linked from the signoff table.
- [ ] **G1.8 Resolve #1031** (≤2 h subtasks under the issue). Validate
  `waitFor` init-gates against the default-deny NetworkPolicy baseline; a
  conflicting gate fails the render closed. Tests included. Exit: issue
  closed, `cds render local-kubernetes-example --target helm` clean.
- [ ] **G1.9 Resolve #1023** (≤2 h subtasks under the issue). Make external
  egress declarable and rendered under the default-deny baseline, surfaced as
  `CDS-K8S-008` findings. Exit: issue closed; every external egress rule in
  the example chart is explicit.

Gate 1 exit: the full Helm lifecycle green on a cluster created from a fresh
directory, boundary checks passing, #1031 and #1023 closed, evidence saved.

## 7. Gate 2 — Production-ready posture on Kubernetes

Run against `production-kubernetes-example` with `--environment prod` (the
overlay that promotes the profile to the production security policy).

- [ ] **G2.1 Production render and policy validation** (~1 h).
  `cds validate production-kubernetes-example --environment prod --target helm`
  and `cds render ... --environment prod --target helm`. Confirm the
  production markers: `E139` (no trust auth), `E140` (no development web
  serving), SCRAM host auth, declared `spec.backup` policies — the Helm backup
  provider itself is made real in G2.9, not just declared. Exit: validation
  passes with the expected diagnostics.
- [ ] **G2.2 Preflight and host baseline** (~1 h).
  `cds preflight production-kubernetes-example --environment prod`; triage the
  `HOST-001..006` advisories against
  [docs/docker-host-hardening.md](../docker-host-hardening.md) and record which
  are operator-accepted. Exit: no `[FAIL]` lines; warnings dispositioned.
- [ ] **G2.3 Security scan, diff-based** (~1 h).
  `cds security production-kubernetes-example --environment prod --target helm`.
  The command is fail-closed; compare findings against the documented snapshot
  in [docs/production-hardening-checklist.md](../production-hardening-checklist.md#4-expected-findings-snapshot-2026-10-30)
  plus the Helm-target rules (`CDS-K8S-*`, e.g. external exposure CDS-K8S-007,
  wildcard RBAC CDS-K8S-006, external egress CDS-K8S-008). Any new finding is a
  regression: fix or file. Exit: findings match the documented set.
- [ ] **G2.4 Secrets and evidence report** (~1 h). Confirm `.env` is mode 0600,
  the rendered chart contains only `$(CDS_*)` expansions backed by
  `secretKeyRef`, and run
  `cds report production-kubernetes-example --environment prod --hardened --output <evidence-file>`.
  Exit: report exported, secret-leak check `status=pass`.
- [ ] **G2.5 Map the hardening checklist onto Helm** (~2 h). Walk the 13
  controls of
  [docs/production-hardening-checklist.md](../production-hardening-checklist.md#2-the-checklist)
  and state the Kubernetes equivalent of each (pod security context and
  `cli/k8s_security.py` rules for control 6, NetworkPolicy for network
  isolation, ServiceAccount/RBAC opt-in, PVC retention, etc.). Every control
  must have a working Helm equivalent — the backup row is closed by G2.9, not
  filed. Exit: a filled-in mapping table in the evidence bundle.
- [ ] **G2.6 Maturity tier** (~0.5 h).
  `cds maturity production-kubernetes-example --environment prod --format json`.
  MVP target: **Secured** — reachable via identity + TLS/reverse proxy + a
  working backup provider now that section 2's issues are mandatory in-cycle.
  Exit: tier recorded with the contracts that justify it.
- [ ] **G2.7 Live production run** (~2 h). `cds up production-kubernetes-example
  --environment prod --target helm ...`, `cds state ... --target helm` until
  healthy, verify the exposure surface (only the intended ingress/NodePort
  answers; backends ClusterIP-only — enabled by G2.8), then `cds down`. Exit:
  healthy run, exposure as expected, clean down.
- [ ] **G2.8 Resolve #1033** (≤2 h subtasks under the issue). Derive
  reverse-proxy route targets as NetworkPolicy edges, so an ingress fronting a
  ClusterIP service renders correct policies without hand-declared rules.
  Exit: issue closed; exposure check in G2.7 passes on contract-derived
  rules alone.
- [ ] **G2.9 Backup on Helm — #665/#668/#669 and E142** (≤2 h subtasks under
  the issues). Render the pgBackRest sidecar (or a restic-shaped
  `backup-target` provider) for the Helm target; run a backup of the prod
  profile's Postgres and a **restore verification** into a scratch namespace.
  Exit: issues closed, `E142` gone, restore evidence in the bundle.

Gate 2 exit: fail-closed gates diff-clean, checklist mapped with every control
working on Helm, backup + restore verified on Helm, #1033 closed, maturity tier
**Secured** and evidence recorded, live prod-profile run green.

## 8. Gate 3 — Haven parity

The "Haven-shaped profile" of G3.3–G3.6 is the full Haven profile defined in
[section 13](#full-haven-profile-gate-3--the-v20-suite): the v1.0.0 stack
(Postgres, Dagster, Superset, KeyDB) plus dbt and the Haven+ platform baseline
(Keycloak, Traefik, restic, garage, vault, FluxCD, Loki/otel-collector), with
the operator-managed database on Helm. It is the ~v2.0 suite; v1.0.0 ships
with the Gate-2 profile only.

- [ ] **G3.1 Run the Haven reference shape on Compose** (~2 h). From the fresh
  directory, `cds get local-haven-reference`, `cds init`, `cds up` (Compose),
  verify Keycloak behind Traefik TLS (`https://.../realms/cds`), `cds state`,
  `cds down`. Exit: identity + TLS ingress + database shape working.
- [ ] **G3.2 Measure maturity against Haven+ capabilities** (~1 h).
  `cds maturity local-haven-reference --format json`, then build the gap matrix:
  for each Haven+ baseline capability (GitOps, metrics/logs/traces,
  cert-manager, service mesh, identity, database operator, backup, network
  policy) mark present / experimental / missing with the tracking issue.
  Source of truth: [docs/haven-parity-plan.md](../haven-parity-plan.md) sections
  2 and 4. Exit: the matrix is written down.
- [ ] **G3.3 Make the Haven modules Helm-capable — #1075, #682, #974**
  (feature work; ≤2 h subtasks under the issues). Add `kubernetes:`
  implementations to `modules/identity/keycloak` and
  `modules/integration/traefik`; replace Keycloak's hardcoded `KC_DB: postgres`
  with the consumed `sql-database` binding; make Keycloak default to production
  web serving under the prod overlay (`E140`). Then render
  `local-haven-reference --target helm` and run it on the k3d cluster, with
  Keycloak reachable behind TLS ingress. Exit: issues closed; the Haven
  reference profile runs on Helm.
- [ ] **G3.4 Routing contract + ingress edges — #883, #1033** (≤2 h subtasks
  under the issues). Introduce the provider-neutral HTTP routing contract and
  resolve Traefik's dynamic configuration from the contract graph instead of
  hand-authored files; emit route targets as NetworkPolicy edges. Exit: issues
  closed; no hand-authored routing anywhere in the profile.
- [ ] **G3.5 Database operator on Helm — #768** (feature work; ≤2 h subtasks
  under the issue). Deliver the CloudNativePG-shaped module providing
  `sql-database` on Helm as the production warehouse option, alongside the
  StatefulSet+PVC Postgres dev path. Exit: issue closed; the prod profile's
  warehouse runs operator-managed on the k3d cluster.
- [ ] **G3.6 Observability live on Helm — #369, #1066, #1076** (≤2 h subtasks
  under the issues). Deliver the Grafana metrics module (#369), wire
  `loki`/`otel-collector` (#662/#777) into the Haven-shaped profile on Helm,
  and fix the pinned-image exec failure under no-new-privileges (#1066) and the
  OTLP log-sink gap (#1076). Add the `fluxcd` module (#767) so the rendered
  chart syncs via GitOps. Exit: issues closed; `cds maturity` reports at least
  **Observed** with providers actually running on the cluster.
- [ ] **G3.7 Sync the parity plan** (~1 h). Update section 4 of
  [docs/haven-parity-plan.md](../haven-parity-plan.md) to match the measured
  status. Exit: plan matches reality.

Gate 3 exit: the Haven-shaped profile — identity, TLS ingress, operator-managed
database, backup, live observability, GitOps — runs on `--target helm` on the
k3d cluster; maturity tier **Secured** recorded with the gap matrix; all
section-2 Gate-3 issues closed.

## 9. Gate 4 — Evidence and issue triage

- [ ] **G4.1 Collect the evidence bundle** (~1 h). One directory with: rendered
  chart (or its hash), `helm lint`/`template` output, `cds state` output,
  `cds security` and `cds preflight` reports, `cds maturity --format json`,
  `cds report --hardened` file, k3d/kubectl/helm/cds/docker versions, timings
  per task. Commit it under `docs/evidence/` only if it contains no secrets
  (it must not); otherwise keep it local and link from the signoff table.
- [ ] **G4.2 File one issue per remaining gap, close #1065** (~1–2 h). Any
  failure or gap that is *not* in section 2's blocking list becomes an issue
  with labels and an effort estimate; issues arising from a milestone-tracked
  effort inherit that milestone. Extend Docker smoke-test CI to the
  experimental modules this MVP runs (#1065). Blocking issues from section 2
  must already be closed by their owning gate — if one is still open, Gate 4
  does not sign off. Do not leave gaps as notes only.
- [ ] **G4.3 Update docs and changelog** (~1 h). Anything that surprised during
  the gates goes into [docs/kubernetes.md](../kubernetes.md) troubleshooting
  or the relevant profile README; user-facing fixes get `CHANGELOG.md`
  `[Unreleased]` entries per repo convention.

Gate 4 exit: signoff table complete, remaining non-blocking gaps filed as
issues, docs/changelog in sync.

## 10. Gate 5 — v1.0.0 release

- [ ] **G5.1 Confirm the 1.0.0 criteria** (~1 h). Definition for this project:
  main green in CI including the `k8s-e2e` workflow; Gates 1–3 of this runbook
  signed off with evidence (which requires the section-2 blocking issues to be
  closed); no open high-severity blockers; changelog complete. With those met,
  `--target helm` ships as **stable** in 1.0.0 — record the decision and update
  the roadmap component lists; file the tracking issue for 1.0.0 criteria.
- [ ] **G5.2 Bump the version** (~1 h). Normally the weekly
  `version-bump.yml` workflow opens the `chore(release): bump version` PR; for
  1.0.0 set `version = "1.0.0"` in `pyproject.toml` and date a
  `## [1.0.0]` section in `CHANGELOG.md` from `[Unreleased]` (see
  [RELEASE.md](../../RELEASE.md) step 3).
- [ ] **G5.3 Run the release gates locally** (~2 h) per
  [docs/release-strategy.md](../release-strategy.md#release-gates):
  `python -m unittest discover -s tests -p "*.py"`,
  `CDS_RUN_DOCKER_SMOKE=1 python3 -m unittest tests.test_compose_runtime_smoke -v`,
  and the k3d E2E (`make k3d-build && make k3d-e2e`) from a clone. Exit: all
  green; a skipped smoke test is an environment limitation, not validation.
- [ ] **G5.4 Tag and publish** (~1 h). Merge the bump, `git tag v1.0.0`,
  `git push origin main --tags`; verify `.github/workflows/release.yml` creates
  the GitHub release, publishes it (not draft), and attaches the
  `cli-sbom.cyclonedx.json` and `release-inventory.json` evidence assets
  ([RELEASE.md](../../RELEASE.md) steps 6–8). If PyPI publishing changes are in
  scope, run the TestPyPI validation first.
- [ ] **G5.5 Announce and reset** (~1 h). Release notes from the changelog,
  announcement per the release checklist, roadmap updated (move completed MVP
  items to Completed, restate Near-Term), `[Unreleased]` reopened.

Gate 5 exit: `v1.0.0` tag pushed, GitHub release published with evidence
assets, announcement done.

## 11. Working rules for the whole cycle

- Every task is sized to finish within 2 hours. If a task runs over, split it
  into ≤2 h subtasks under its tracking issue — do not silently grow a task.
  A gate never signs off over an open blocking issue (section 2): the cycle
  pauses at that gate until the issue is closed.
- A command that fails for a reason outside the runbook: stop, record the exact
  output in the evidence bundle, and treat the cause as blocking work in the
  owning gate (section 2) — no gate signs off over an unresolved failure.
  Never "fix" the environment ad hoc in a way that can't be reproduced from
  the evidence.
- Secrets only through `cds init` / `.env` (mode 0600, untracked); nothing
  secret-shaped goes into the evidence bundle, the signoff table, or issues.
- Run every command from the fresh directory (Path A) unless a task explicitly
  says to use a clone (Path B); record which path each task used.
- Keep `main` releasable: any repo change made during the cycle goes through a
  reviewed PR with a `CHANGELOG.md` entry, per
  [CONTRIBUTING.md](../../CONTRIBUTING.md).

## 12. Definition of done for the MVP cycle

- [ ] Gate 0–4 exit criteria all met, signoff table filled in.
- [ ] Every blocking issue in section 2 is closed before Gate 5 starts; only
  the explicitly post-MVP list (service mesh #776, workload identity #1068,
  persisted Helm context #812, Marquez promotion #976) may remain open, each
  with its milestone.
- [ ] `v1.0.0` tagged and published with evidence assets (Gate 5).
- [ ] The 1.0.0 stability decision (Helm stable vs experimental) is recorded
  in the roadmap and, if Helm ships stable, reflected in
  [docs/roadmap.md](../roadmap.md) component lists.

## 13. Testing commands and signoff

Each gate has one primary testing command, run from the fresh directory
(Path A) unless the row says clone (Path B). `cds test` runs
validate → security → plan → render in sequence, so on a given target it is
the single command that exercises the whole static pipeline; the live checks
(`up`/`state`/boundary/E2E) run on top of it.

### Full production suite in one block (Gate 2 — the v1.0.0 suite)

The v1.0.0 production suite is the `production-kubernetes-example` profile —
which via `extends` pulls in the complete
`local-dagster-postgres-superset` stack (Postgres, Dagster, Superset, KeyDB)
— with the `prod` environment overlay
(`profiles/production-kubernetes-example/environments/prod.yaml`) merged on
top via `--environment prod`. One `--environment` overlay applies per
invocation; use `cds diff <profile> --from dev --to prod` on a profile that
has both to see exactly what an overlay changes. Copy-paste block:

```bash
# 1. Generate .env with every required secret — the overlay adds
#    CDS_BACKUP_ENCRYPTION_KEY and the profile requires CDS_CACHE_PASSWORD —
#    then replace every change-me with a real, unique value:
cds init production-kubernetes-example --environment prod

# 2. Full static production suite in one command: validate -> security ->
#    plan -> render, prod overlay applied, Helm target. Drop `--target helm`
#    for the Compose/Docker target:
cds test production-kubernetes-example --environment prod --target helm

# 3. Live full run on the cluster (G2.7), then tear down:
cds up production-kubernetes-example --environment prod --target helm
cds state production-kubernetes-example --environment prod --target helm
cds down production-kubernetes-example --environment prod --target helm

# 4. The surrounding fail-closed gates, all with the same overlay:
cds preflight production-kubernetes-example --environment prod
cds security production-kubernetes-example --environment prod --target helm
cds maturity production-kubernetes-example --environment prod --format json
cds report production-kubernetes-example --environment prod --hardened --output <evidence-file>
```

Expected: step 2 exits 0 with all four stages `[PASS]` once real secrets are
set (weak or reused placeholder values fail the security stage closed via
CDS-SEC-012/013); the remaining findings are the documented snapshot
(CDS-SEC-080/081 et al., see
[docs/production-hardening-checklist.md](../production-hardening-checklist.md)).
Step 3 needs the cluster from Gate 1 (Path A) or `make k3d-up` (Path B).

### Full Haven profile (Gate 3 — the ~v2.0 suite)

`production-kubernetes-example` is the v1.0.0 suite. The full Haven profile —
working name `production-haven`, created by G3.3–G3.6 — is the ~v2.0 target:
the v1.0.0 stack plus the Haven+ platform baseline, so the profile provides
every platform contract, not just the workload. The absolutely necessary
module set, mapped to the Haven+ baseline of
[docs/haven-parity-plan.md](../haven-parity-plan.md) sections 2 and 7:

| Module | Contract(s) | Haven+ capability it covers | Status / tracked gap |
| --- | --- | --- | --- |
| `warehouse/postgres` | `sql-database` (+ per-app databases), built-in `backup-target` | warehouse; database operator on Helm via CloudNativePG-shaped module | stable; operator = #768 (G3.5) |
| `orchestration/dagster` | `http-service`, `backup-source` | orchestration workload | stable; has `kubernetes:` block |
| `bi/superset` | `http-service`, consumes `oidc-provider` | BI workload | stable; has `kubernetes:` block |
| `cache/keydb` | `cache-service` | cache | stable; has `kubernetes:` block |
| `transformation/dbt` | consumes `sql-database`, `docs-service` | transformation | stable; no `kubernetes:` block yet |
| `identity/keycloak` | `oidc-provider` | identity/SSO (Haven+: Keycloak) | `productionSuitable: false`, no `kubernetes:` block — #1075, #682/#974 (G3.3) |
| `integration/traefik` | `reverse-proxy` with TLS | TLS ingress edge (stands in for cert-manager+ingress at MVP scope) | no `kubernetes:` block — #1075, #883, #1033 (G3.3/G3.4) |
| `backup/restic` | provides `backup-target`, consumes `backup-source` | backup (Haven+: Velero); Postgres sidecar is #665 | production-only piece (E143); no `kubernetes:` block — G2.9 |
| `storage/garage` | `object-storage` (S3) | restic's S3 repository target / landing zone (#669) | `productionSuitable: false` (single-node) — deviation for the local MVP cluster only |
| `secrets/vault` | `secrets-provider` | secrets sync (Haven+: Sealed/External Secrets) | `productionSuitable: false` currently |
| `gitops/fluxcd` (experimental) | `gitops-target` | GitOps (Haven+: FluxCD) | has `kubernetes:` block; experimental — #767 |
| `observability/loki` + `observability/otel-collector` (experimental) | `log-sink`, `metrics-sink`, `trace-sink` | observability: logs + OTLP pipeline (Haven+: Loki/Alloy; Grafana metrics backend = #369) | experimental; no `kubernetes:` blocks — G3.6 (#369, #1066, #1076) |

Deliberately **not** in the full profile (post-MVP, per section 2): service
mesh/Istio (#776), workload identity (#1068), a standalone cert-manager
(#205/#577/#580 — Traefik TLS covers the edge), and Marquez lineage (#976).

Once the profile exists, the same command shape runs the whole ~v2.0 suite:

```bash
cds init production-haven --environment prod
cds test production-haven --environment prod --target helm   # drop --target helm for Compose
cds up production-haven --environment prod --target helm
cds state production-haven --environment prod --target helm
cds down production-haven --environment prod --target helm
cds maturity production-haven --environment prod --format json
```

Deviations are recorded here as issues, not silently waived: under the `prod`
overlay, every `productionSuitable: false` module above raises a **high**
CDS-SEC-073 finding (each is filed against its own module issue); backup
pieces are production-only, so the suite fails closed with E143 unless
`--environment prod` is passed; Keycloak must not run `webServer.mode:
development` under prod (E140, #974); and modules without a `kubernetes:`
block fail the `--target helm` render closed with E072 until their Gate-3
issues land — on the Compose target the suite renders and runs today.

| Gate | Primary testing command | Expected result |
| --- | --- | --- |
| 0 — Bootstrap | `cds test local-kubernetes-example` (Compose default) after `cds init` | exit 0: validate, security, plan, render all green on Compose |
| 1 — Kubernetes MVP | `cds test local-kubernetes-example --target helm`, then `cds up/state/down ... --target helm` plus the boundary checks of [scripts/k8s/e2e.sh](../../scripts/k8s/e2e.sh) (`make k3d-e2e` from a clone) | exit 0; chart passes `helm lint`/`template`; all workloads healthy; E2E pass = all, fail = 0 |
| 2 — Production-ready | `cds test production-kubernetes-example --environment prod --target helm`, plus `cds preflight ... --environment prod` and `cds security ... --environment prod --target helm` | exit 0, or findings exactly matching the documented snapshot; no preflight `[FAIL]` |
| 3 — Haven parity | `cds maturity local-haven-reference --format json --target helm`, plus a live `--target helm` run of the Haven-shaped profile from G3.3–G3.6 | Haven-shaped profile running on Helm; tier **Secured** reported with justifying contracts; gap matrix complete |
| 4 — Evidence and issues | (process gate — no command) | results recorded, one issue per gap, docs/changelog updated |
| 5 — v1.0.0 release | `python -m unittest discover -s tests -p "*.py"` + `CDS_RUN_DOCKER_SMOKE=1 python3 -m unittest tests.test_compose_runtime_smoke -v` + `make k3d-build && make k3d-e2e` (from a clone) | all green, per [docs/release-strategy.md](../release-strategy.md#release-gates) |

Signoff table — one row per gate, filled in while running. A gate is signed off
only when its exit criteria (sections 5–10) are met, every blocking issue
(section 2) assigned to it is closed, and every remaining non-blocking gap is
filed as an issue (last column).

| Gate | Testing command run | Path (A/B) | Result (pass/fail/blocked) | Tested by | Date | Signed off by (name, date) | Evidence link | Issues filed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 — Bootstrap | | | | | | | | |
| 1 — Kubernetes MVP | | | | | | | | |
| 2 — Production-ready | | | | | | | | |
| 3 — Haven parity | | | | | | | | |
| 4 — Evidence and issues | | | | | | | | |
| 5 — v1.0.0 release | | | | | | | | |

Per-task detail (which of G0.0–G5.5 ran, timings, exact outputs) goes in the
evidence bundle linked from the gate's row, not in this table.

Environment record (fill in once, reference everywhere):

| Tool | Version | Notes |
| --- | --- | --- |
| Docker Engine | | memory/disk allocated |
| `cds` CLI | | install path (pipx/pip), fetched commit SHA |
| k3d / kubectl / Helm | | |
| Cluster | `k3d-cds-mvp`, 1 server + 1 agent | |
