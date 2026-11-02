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
   [docs/haven-parity-plan.md](../haven-parity-plan.md), and every gap becomes a
   tracked issue.
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

## 2. Prerequisites: open issues that gate parts of the MVP

These issues are open as of 2026-11-02. A prerequisite blocks only the gate
listed; where it does, run the reduced scope and record the gap in Gate 4
instead of stalling the cycle.

| Issue | Blocks | What "resolved" means for this MVP |
| --- | --- | --- |
| [#690](https://github.com/RonaldHensbergen/composable-data-stack/issues/690) | Gate 0/1 (no-clone path) | `cds up --target helm` self-contained: builds or pulls images and imports them into the cluster. Implemented in PR [#1105](https://github.com/RonaldHensbergen/composable-data-stack/pull/1105) (`feat/690-helm-up-self-contained`); the maintainer merges it when the MVP cycle needs it. Until merged, the fresh-directory path uses `image.source: registry` + `image.tag` (published signed images), or a repo clone with `make k3d-build`. |
| [#1075](https://github.com/RonaldHensbergen/composable-data-stack/issues/1075) (+ Keycloak/Traefik have no `kubernetes:` block in their `module.yaml`) | Gate 3 | A k8s-capable `oidc-provider` and reverse proxy: `modules/identity/keycloak` and `modules/integration/traefik` render Compose-only today, so `local-haven-reference` cannot run on Helm yet. MVP scope: measure and file, run the Haven shape on Compose. |
| [#1033](https://github.com/RonaldHensbergen/composable-data-stack/issues/1033) | Gates 2/3 | Reverse-proxy route targets derived as NetworkPolicy edges; until then an ingress fronting a ClusterIP service needs hand-declared `networkPolicy.ingress` rules. |
| [#1023](https://github.com/RonaldHensbergen/composable-data-stack/issues/1023) | Gates 1/2 | External egress for Helm workloads under the default-deny baseline. |
| [#1031](https://github.com/RonaldHensbergen/composable-data-stack/issues/1031) | Gate 1 | `waitFor` init-gates validated against the default-deny NetworkPolicy baseline. |
| [#812](https://github.com/RonaldHensbergen/composable-data-stack/issues/812) | Gate 1 (quality of life) | Persisted `--kube-context`/`--namespace`/`--release` defaults, so the MVP doesn't repeat flags. |
| [#974](https://github.com/RonaldHensbergen/composable-data-stack/issues/974), [#682](https://github.com/RonaldHensbergen/composable-data-stack/issues/682) | Gates 2/3 | Keycloak production mode default (`E140` absent-mode gap) and the hardcoded `KC_DB: postgres` binding; both gate a production-suitable identity module. |
| [#883](https://github.com/RonaldHensbergen/composable-data-stack/issues/883) | Gate 3 | Provider-neutral HTTP routing contract, so reverse-proxy wiring is resolved from the contract graph instead of hand-authored. |
| [#768](https://github.com/RonaldHensbergen/composable-data-stack/issues/768) | Gate 3 (stretch) | CloudNativePG-shaped database operator; MVP can proceed with StatefulSet+PVC Postgres and file the gap. |
| [#776](https://github.com/RonaldHensbergen/composable-data-stack/issues/776), [#1068](https://github.com/RonaldHensbergen/composable-data-stack/issues/1068) | Post-MVP Haven parity | Service mesh (mTLS) and workload identity; out of MVP scope, tracked. |
| [#369](https://github.com/RonaldHensbergen/composable-data-stack/issues/369), [#976](https://github.com/RonaldHensbergen/composable-data-stack/issues/976), [#1065](https://github.com/RonaldHensbergen/composable-data-stack/issues/1065), [#1066](https://github.com/RonaldHensbergen/composable-data-stack/issues/1066), [#1076](https://github.com/RonaldHensbergen/composable-data-stack/issues/1076) | Gate 3 (observability row) | Grafana metrics module, Marquez promotion, and the experimental Loki/otel-collector gaps; the MVP can wire `loki`/`otel-collector` experimentally and file the rest. |
| Backup providers [#665](https://github.com/RonaldHensbergen/composable-data-stack/issues/665)/[#668](https://github.com/RonaldHensbergen/composable-data-stack/issues/668)/[#669](https://github.com/RonaldHensbergen/composable-data-stack/issues/669); `E142` (pgBackRest sidecar not rendered for Helm) | Gate 2 (backup row) | The `backup-target` contract exists (#210, closed) but no provider module is implemented; the prod overlay declares policies without an enabled sidecar. MVP verifies the policy validation, not a real backup run. |

Already landed — do not re-file: the Helm render target with NetworkPolicy
generation (#608, #75), kind-based CI validation (#76), Kubernetes docs and
example profiles (#77), the production hardening checklist (#72), FluxCD
GitOps rendering (#767), the `backup-target` contract (#210), the Loki
log-sink provider (#662), the `trace-sink`/`metrics-sink` contracts and
otel-collector module (#777), the Keycloak module with the shared
`oidc-provider` contract and Superset consumer (#370, #1064), and the
`cds maturity` tier report (#769, #1059).

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
`k3d version`, `kubectl version --client`, `helm version`) in the results log —
reproducibility from the version set is part of the evidence.

## 4. Gate overview

| Gate | Goal | Exit criteria | Estimated effort |
| --- | --- | --- | --- |
| [0 — Bootstrap](#5-gate-0--bootstrap-from-a-fresh-directory-docker-only) | Fresh dir + Docker → CLI, tooling, profiles, cluster | `cds --version`, `kubectl get nodes` green, profiles fetched, Compose smoke run passes | 4–6 h |
| [1 — Kubernetes MVP](#6-gate-1--kubernetes-mvp-on-a-local-cluster) | Full Helm lifecycle on the local cluster | `cds up/state/down --target helm` green, all workloads healthy, boundary checks pass, evidence recorded | 6–8 h |
| [2 — Production-ready](#7-gate-2--production-ready-posture-on-kubernetes) | Prod overlay passes fail-closed gates on Helm | `validate`/`security`/`preflight` diff-clean, checklist mapped, maturity tier recorded | 6–8 h |
| [3 — Haven parity](#8-gate-3--haven-parity) | Measure Haven+ capabilities, run what exists | Maturity report + gap matrix, Haven shape run on Compose, gaps filed as issues | 4–6 h |
| [4 — Evidence and issues](#9-gate-4--evidence-and-issue-triage) | Everything reproducible, every gap tracked | Results log complete, one issue per gap, docs/changelog updated | 3–4 h |
| [5 — v1.0.0 release](#10-gate-5--v100-release) | Cut and publish v1.0.0 | Tag pushed, release published with evidence assets, announcement done | 4–6 h |

Total: roughly 4–5 working days of ≤2 h tasks. The primary testing command per
gate and the signoff table are in [section 13](#13-testing-commands-and-signoff).

## 5. Gate 0 — Bootstrap from a fresh directory (Docker only)

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
- [ ] **G0.5 Decide the image supply path** (~1–2 h). Path A requires every
  module in the profile to use `image.source: registry` with a pinned
  `image.tag` (published, signed, scanned images — see
  [docs/image-signing.md](../image-signing.md)); set that in the fetched
  profile/module config. Path B: clone the repo at the fetched commit SHA and
  run `make k3d-build`, then import images into the cluster
  (`k3d image import ...`). #690 (PR #1105) makes this step self-contained.
  Exit: the profile's images are resolvable by the cluster (`imagePullPolicy`
  correct for the path chosen).
- [ ] **G0.6 Compose smoke run first** (~1 h). `cds init` the simplest fetched
  profile, fill `.env` (mode 0600), `cds up` (Compose default), `cds state`,
  `cds down`. This proves the CLI + secrets flow before Kubernetes is
  introduced. Exit: all services healthy, clean down.

Gate 0 exit: G0.1–G0.6 checked, versions recorded in the results log.

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
  `CDS_E2E_KEEP_CLUSTER=1 make k3d-e2e`. Exit: pass = all, fail = 0, or each
  failure filed as an issue.
- [ ] **G1.6 Teardown** (~0.5 h). `cds down ... --target helm` (PVC retention is
  the default; verify with `kubectl get pvc`), confirm the namespace is clean
  of release resources, `k3d cluster delete cds-mvp` when the gate is done.
  Exit: no leftover workloads; PVCs retained unless `--delete-pvcs`.
- [ ] **G1.7 Record evidence** (~0.5 h). Save command outputs, `cds state`
  output, pod descriptions (`kubectl describe pod`), events, and timings into
  the results log / evidence directory.

Gate 1 exit: the full Helm lifecycle green on a cluster created from a fresh
directory, with boundary checks passing and evidence saved.

## 7. Gate 2 — Production-ready posture on Kubernetes

Run against `production-kubernetes-example` with `--environment prod` (the
overlay that promotes the profile to the production security policy).

- [ ] **G2.1 Production render and policy validation** (~1 h).
  `cds validate production-kubernetes-example --environment prod --target helm`
  and `cds render ... --environment prod --target helm`. Confirm the
  production markers: `E139` (no trust auth), `E140` (no development web
  serving), SCRAM host auth, declared `spec.backup` policies — noting the
  documented `E142` caveat that the pgBackRest sidecar is not rendered for
  Helm yet. Exit: validation passes with the expected diagnostics.
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
  isolation, ServiceAccount/RBAC opt-in, PVC retention, etc.). File issues for
  controls with no Helm equivalent (backup provider row, see section 2).
  Exit: a filled-in mapping table in the results log.
- [ ] **G2.6 Maturity tier** (~0.5 h).
  `cds maturity production-kubernetes-example --environment prod --format json`.
  MVP target: at least **Automated** (identity, TLS/reverse proxy, backup
  provider declared); **Secured** is the stretch, blocked on the issues in
  section 2. Exit: tier recorded with the contracts that justify it.
- [ ] **G2.7 Live production run** (~2 h). `cds up production-kubernetes-example
  --environment prod --target helm ...`, `cds state ... --target helm` until
  healthy, verify the exposure surface (only the intended ingress/NodePort
  answers; backends ClusterIP-only), then `cds down`. Exit: healthy run,
  exposure as expected, clean down.

Gate 2 exit: fail-closed gates diff-clean, checklist mapped, maturity tier and
evidence recorded, live prod-profile run green.

## 8. Gate 3 — Haven parity

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
- [ ] **G3.3 Compose a Haven-shaped Helm profile from what exists** (~2 h). Add
  the experimental modules that do render to Helm — `fluxcd` (GitOps, #767),
  `loki`/`otel-collector` (observability, #662/#777) — to a copy of the
  Kubernetes example profile; render with `--target helm` and record what
  fails. Confirm and cross-link the known Helm gaps: Keycloak and Traefik have
  no `kubernetes:` implementation (#1075, #1033, #883). Exit: a rendered (or
  fail-closed with a filed issue) Haven-shaped chart plus the module
  capability list.
- [ ] **G3.4 Sync the parity plan** (~1 h). Update section 4 of
  [docs/haven-parity-plan.md](../haven-parity-plan.md) if the measured status
  drifted from the documented status. Exit: plan matches reality.

Gate 3 exit: maturity tier + gap matrix recorded, Haven shape demonstrated on
Compose, Helm-capable subset rendered or gaps filed.

## 9. Gate 4 — Evidence and issue triage

- [ ] **G4.1 Collect the evidence bundle** (~1 h). One directory with: rendered
  chart (or its hash), `helm lint`/`template` output, `cds state` output,
  `cds security` and `cds preflight` reports, `cds maturity --format json`,
  `cds report --hardened` file, k3d/kubectl/helm/cds/docker versions, timings
  per task. Commit it under `docs/evidence/` only if it contains no secrets
  (it must not); otherwise keep it local and link from the results log.
- [ ] **G4.2 File one issue per gap** (~1–2 h). Every failure, unexpected
  diagnostic, missing Helm implementation, or checklist row without a
  Kubernetes equivalent becomes an issue with labels and an effort estimate;
  issues arising from a milestone-tracked effort inherit that milestone. Do
  not leave gaps as notes only.
- [ ] **G4.3 Update docs and changelog** (~1 h). Anything that surprised during
  the gates goes into [docs/kubernetes.md](../kubernetes.md) troubleshooting
  or the relevant profile README; user-facing fixes get `CHANGELOG.md`
  `[Unreleased]` entries per repo convention.

Gate 4 exit: results log complete, issue list filed, docs/changelog in sync.

## 10. Gate 5 — v1.0.0 release

- [ ] **G5.1 Confirm the 1.0.0 criteria** (~1 h). Proposed definition for this
  project: main green in CI including the `k8s-e2e` workflow; Gates 1–3 of this
  runbook passed with evidence; no open high-severity blockers; changelog
  complete. Decision to make explicitly: does `--target helm` ship as stable in
  1.0.0 (recommended if Gates 1–2 passed) or stay experimental (then 1.0.0
  announces the Compose target as stable and Helm as the flagship experimental
  target)? Record the decision; file the tracking issue for 1.0.0 criteria.
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
  or file an issue and mark the gate partial — do not silently grow a task.
- A command that fails for a reason outside the runbook: stop, record the exact
  output in the results log, file an issue in Gate 4, and continue with the
  next task. Never "fix" the environment ad hoc in a way that can't be
  reproduced from the log.
- Secrets only through `cds init` / `.env` (mode 0600, untracked); nothing
  secret-shaped goes into the evidence bundle, the results log, or issues.
- Run every command from the fresh directory (Path A) unless a task explicitly
  says to use a clone (Path B); record which path each task used.
- Keep `main` releasable: any repo change made during the cycle goes through a
  reviewed PR with a `CHANGELOG.md` entry, per
  [CONTRIBUTING.md](../../CONTRIBUTING.md).

## 12. Definition of done for the MVP cycle

- [ ] Gate 0–4 exit criteria all met, results log filled in.
- [ ] Every gap found during the cycle is a tracked issue (section 2 table plus
  Gate 4 additions), each with an owner decision: fix before 1.0.0, or
  explicitly deferred with a milestone.
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

| Gate | Primary testing command | Expected result |
| --- | --- | --- |
| 0 — Bootstrap | `cds test local-kubernetes-example` (Compose default) after `cds init` | exit 0: validate, security, plan, render all green on Compose |
| 1 — Kubernetes MVP | `cds test local-kubernetes-example --target helm`, then `cds up/state/down ... --target helm` plus the boundary checks of [scripts/k8s/e2e.sh](../../scripts/k8s/e2e.sh) (`make k3d-e2e` from a clone) | exit 0; chart passes `helm lint`/`template`; all workloads healthy; E2E pass = all, fail = 0 |
| 2 — Production-ready | `cds test production-kubernetes-example --environment prod --target helm`, plus `cds preflight ... --environment prod` and `cds security ... --environment prod --target helm` | exit 0, or findings exactly matching the documented snapshot; no preflight `[FAIL]` |
| 3 — Haven parity | `cds maturity local-haven-reference --format json`, plus a `--target helm` render of the Haven-shaped profile from G3.3 | tier reported with justifying contracts; gap matrix complete; render succeeds or fails closed with a filed issue |
| 4 — Evidence and issues | (process gate — no command) | results recorded, one issue per gap, docs/changelog updated |
| 5 — v1.0.0 release | `python -m unittest discover -s tests -p "*.py"` + `CDS_RUN_DOCKER_SMOKE=1 python3 -m unittest tests.test_compose_runtime_smoke -v` + `make k3d-build && make k3d-e2e` (from a clone) | all green, per [docs/release-strategy.md](../release-strategy.md#release-gates) |

Signoff table — one row per gate, filled in while running. A gate is signed off
only when its exit criteria (sections 5–10) are met and every failure is either
fixed or filed as an issue (last column).

| Gate | Testing command run | Path (A/B) | Result (pass/fail/blocked) | Tested by | Date | Signed off by (name, date) | Evidence link | Issues filed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 — Bootstrap | | | | | | | | |
| 1 — Kubernetes MVP | | | | | | | | |
| 2 — Production-ready | | | | | | | | |
| 3 — Haven parity | | | | | | | | |
| 4 — Evidence and issues | | | | | | | | |
| 5 — v1.0.0 release | | | | | | | | |

Per-task detail (which of G0.1–G5.5 ran, timings, exact outputs) goes in the
evidence bundle linked from the gate's row, not in this table.

Environment record (fill in once, reference everywhere):

| Tool | Version | Notes |
| --- | --- | --- |
| Docker Engine | | memory/disk allocated |
| `cds` CLI | | install path (pipx/pip), fetched commit SHA |
| k3d / kubectl / Helm | | |
| Cluster | `k3d-cds-mvp`, 1 server + 1 agent | |
