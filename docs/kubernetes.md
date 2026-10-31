# Kubernetes Target

CDS can render the same resolved plan as either Docker Compose or a Helm chart.
The Helm target is intended for local and development Kubernetes clusters,
including k3s through k3d.

## Contents

- [Prerequisites](#prerequisites)
- [Validate and render](#validate-and-render)
- [Supply secrets at install time](#supply-secrets-at-install-time)
- [Run and inspect the stack](#run-and-inspect-the-stack)
- [Stop the stack](#stop-the-stack)
- [Use the isolated k3d harness](#use-the-isolated-k3d-harness)
- [Access local services](#access-local-services)
- [Migrating from Docker Compose](#migrating-from-docker-compose)
- [Translation boundaries](#translation-boundaries)
- [Network isolation and least privilege](#network-isolation-and-least-privilege)
- [GitOps handoff](#gitops-handoff)
- [Troubleshooting](#troubleshooting)

## Prerequisites

Install the standard CDS requirements plus:

- Docker Engine or Docker Desktop
- k3d 5 or newer for the local k3s harness
- kubectl
- Helm 4 or a compatible Helm 3 release

The complete example stack needs at least 8 GB of Docker memory and 10 GB of
free disk space. Commands must run with the worktree-specific `KUBECONFIG` when
using the included harness. The scripts export it automatically.

## Validate and render

Target-aware validation catches modules or contract bindings that Kubernetes
cannot express:

```bash
cds validate local-dagster-postgres-superset --target helm
cds security local-dagster-postgres-superset --target helm
cds test local-dagster-postgres-superset --target helm
cds render local-dagster-postgres-superset --target helm
```

Working against the Helm target for an extended session? `cds config set target helm`
persists it as the default for `validate`/`render`/`up`/`down`/`test`/`state`/`security`,
so `--target helm` doesn't need repeating on every command (an explicit `--target`
flag still overrides it). `cds config unset target` (or `cds config set target compose`)
reverts to the Compose default.

The default output directory is `chart/`. Use `--output` to choose another
directory. CDS replaces a chart it generated previously as one atomic directory
update. It refuses to replace unrelated content unless `--force` is present.

The generated chart contains `Chart.yaml`, `values.yaml`, `.helmignore`, notes,
and templates for workloads, Services, ConfigMaps, PVC templates, and a Secret.
Rendering is deterministic and contains no secret values. Workloads and
Services are prefixed by the Helm release name, so separate releases can share
a namespace without claiming each other's internal DNS names.

## Supply secrets at install time

The plan validates every `secrets.*` reference, while the chart stores only the
corresponding `CDS_*` key names. `cds up --target helm` reads those values from
the process environment or `.env`, writes a mode `0600` temporary values file,
passes it to Helm, and deletes it immediately after the command finishes.

For a manual Helm install, create an untracked values file:

```yaml
secrets:
  CDS_POSTGRES_SUPERUSER_PASSWORD: replace-me
  CDS_SUPERSET_SECRET_KEY: replace-me
```

Then install with `helm upgrade --install --values secrets.local.yaml`. Never
commit that file. The generated Secret template is also the extension point for
a future External Secrets Operator or Vault CSI integration.

## Run and inspect the stack

Deploy directly from the CLI:

```bash
export KUBECONFIG="$PWD/.k3d/cds-feat-k8s.kubeconfig"
cds up local-dagster-postgres-superset \
  --target helm \
  --kube-context k3d-cds-feat-k8s \
  --namespace cds-local \
  --release cds \
  --timeout 300
```

The timeout bounds both Helm's wait and every explicit workload rollout check.
Output is written to `.cds/logs/`. `--detach` submits the release without
waiting for rollouts.

Use the provider-neutral health view:

```bash
cds state local-dagster-postgres-superset \
  --target helm \
  --kube-context k3d-cds-feat-k8s \
  --namespace cds-local \
  --release cds
```

## Stop the stack

Uninstall the Helm release while retaining database PVCs:

```bash
cds down local-dagster-postgres-superset \
  --target helm \
  --kube-context k3d-cds-feat-k8s \
  --namespace cds-local \
  --release cds
```

PVC retention is the safe default. Add `--delete-pvcs` only when the persistent
data may be destroyed.

## Use the isolated k3d harness

The harness derives a cluster name, context, kubeconfig, and port block from the
current branch. It never changes the shared kubectl context and never deletes a
sibling worktree's cluster.

```bash
make k3d-build
make k3d-up
make k3d-install
CDS_E2E_KEEP_CLUSTER=1 make k3d-e2e
```

Run `make k3d-down` when finished. The E2E suite uses a dedicated namespace and
release. It removes only resources it created and deletes the cluster only when
the suite created that cluster. Setting `CDS_E2E_KEEP_CLUSTER=1` leaves its
resources running for inspection.

The `Kubernetes E2E` workflow (`.github/workflows/k8s-e2e.yml`) runs exactly
this harness (`make k3d-build` then `make k3d-e2e`) in CI on pull requests and
pushes that touch the chart, CLI, images, harness scripts, the Makefile's k3d
targets, or `pyproject.toml`, so the documented developer flow and the CI check
cannot drift apart. On failure it uploads pod descriptions, events, and logs as
the `k8s-e2e-logs` artifact.

Local Dagster, Superset, and PostgreSQL images use `imagePullPolicy: Never` and are imported
into k3d. This deliberately avoids the common combination of
`imagePullPolicy: Always`, a nonexistent `localhost:5000` registry, and macOS
AirPlay's host-port conflict. If a future module requires a registry, expose it
on a host port other than 5000 and configure a k3s registry mirror before the
node starts.

## Access local services

The reusable chart keeps its Services private with `ClusterIP`. The local k3d
install wrapper attaches Dagster and Superset to the NodePorts already published
by the branch-scoped cluster. `make k3d-install` performs this step automatically
and prints both URLs.

To expose an existing installation or print its URLs again, run:

```bash
make k3d-expose
```

Ports are deterministic per branch to keep worktrees isolated (e.g. Dagster at
`http://127.0.0.1:38142` and Superset at `http://127.0.0.1:38143` for a branch
that happens to hash to that pair). Run `scripts/k8s/k3d-env.sh` to print the
ports for the current branch.

Set `CDS_EXPOSE_LOCALHOST=0` when installing a second release into the same
cluster. NodePorts are cluster-global, so only one release can own the local
host mappings. The isolated E2E release disables them and uses bounded temporary
forwards for its own boundary checks.

## Migrating from Docker Compose

You do not write a separate profile for Kubernetes: the render target is a
CLI flag, so the same profile renders to either runtime. Pick a profile,
validate and render it for Helm, and supply secrets at install time:

```bash
cds validate my-profile --target helm
cds render my-profile --target helm --output chart/
cds up my-profile --target helm        # or install chart/ with helm directly
```

Ready-made example profiles (issue #77), both render successfully with
`--target helm`:

- `profiles/local-kubernetes-example` — local development stack on the k3d
  harness: `cds render local-kubernetes-example --target helm`.
- `profiles/production-kubernetes-example` — production-style stack: the base
  profile locks the cache down with a required secret, and the `prod`
  environment overlay (`environments/prod.yaml`) promotes it to the
  production security policy: `cds render production-kubernetes-example
  --environment prod --target helm`.

What carries over and what does not is summarized in
[Translation boundaries](#translation-boundaries): compose services become
workloads plus Services, healthchecks become probes, `depends_on` becomes
bounded init-container wait gates, and named volumes become StatefulSet
`volumeClaimTemplates`. Host port bindings do not translate — expose UIs
through the workload's Service (e.g. the `kubernetesService` NodePort config
used by the example profiles) or port-forward. Modules that build locally
(`image.source: build`) must be built and imported into the cluster first
(`make k3d-build`). One capability is compose-only for now: the Postgres
pgBackRest backup sidecar is not rendered for Helm yet (E142), so the
production-style example declares its backup policies without enabling the
sidecar.

## Translation boundaries

The Kubernetes block declares orchestration details explicitly. CDS does not
guess at semantics that differ between runtimes.

| Compose behavior | Kubernetes target |
| --- | --- |
| Named persistent volume | StatefulSet `volumeClaimTemplates` |
| Ephemeral named volume | `emptyDir` |
| `depends_on` health gate | Bounded init-container `waitFor` check |
| Compose healthcheck | Readiness, liveness, and startup probes |
| Host port binding | Not translated; use a Service or port-forward |
| Compose network | Not translated; Kubernetes DNS and Services provide reachability |
| Network policy | Emitted per module: default-deny ingress/egress with contract-derived allow rules (see [Network isolation](#network-isolation-and-least-privilege)) |
| `pids_limit` | Not translated by the current schema |
| Local `build:` | Build first, then import into k3d |

Dagster's Compose-only Unix socket does not cross Pod boundaries. The
Kubernetes implementation uses Dagster gRPC over a ClusterIP Service, as
recorded in ADR 0002.

## Network isolation and least privilege

Every module in a rendered chart ships a per-module ServiceAccount and a
NetworkPolicy (issue #75):

- The NetworkPolicy is default-deny for ingress and egress. Allow rules are
  derived from the plan's contract bindings: a binding from consumer to
  provider opens egress on the consumer's pods and matching ingress on the
  provider's pods, scoped to the binding's port. The contract's port is
  Service-facing, so it is mapped through the provider's Service
  `targetPort`: a `port: X, targetPort: Y` remap keeps the rule correct. A
  binding without a numeric port falls back to the provider's advertised
  service ports; with no port evidence at all the rule stays module-scoped
  and the render reports `W128` asking for the port to be declared.
- Within a module, pods may talk freely on any port: a module is the unit
  of composition and declares its own internal wiring (which is why the
  Superset init job and the webserver share one policy).
- Egress to the cluster DNS service (kube-system `kube-dns`, UDP+TCP 53) is
  the only other unconditional rule.
- NodePort and LoadBalancer services are intended entry points: ingress is
  opened to all sources on exactly their exposed ports, and CDS-K8S-007
  flags that exposure.
- The ServiceAccount mounts no API token by default; RBAC is opt-in via
  `implementation.kubernetes.serviceAccount.rbac` (malformed rules fail
  closed with E148), and wildcard grants are flagged by CDS-K8S-006.

### When the baseline denies something you need

Bindings cannot name external endpoints or cross-namespace clients: a job
reading an external API or database, or an ingress controller fronting a
ClusterIP service, would be silently denied by the default-deny baseline.
Declare those rules via `implementation.kubernetes.networkPolicy.ingress`/
`egress` -- they are rendered verbatim after the binding-derived rules, and
malformed shapes fail closed with E149. The security scan records every
external-egress rule as a low CDS-K8S-008 finding, so intentional openings
stay visible in `cds security --target helm`.

## GitOps handoff

`cds render --target helm` output is designed to become the source-of-truth
artifact a pull-based GitOps controller (FluxCD, ArgoCD) reconciles against,
instead of requiring `cds up --target helm` to be run by hand against a live
cluster (issue #767). The vendor-neutral
[`gitops-target` contract](../shared/contracts/gitops-target.yaml) describes
that source of truth — `repositoryUrl`, `branch`, `path`, `syncInterval`,
`targetNamespace` — and the experimental reference provider
[`modules-experimental/gitops/fluxcd`](../modules-experimental/gitops/fluxcd/)
renders the Flux bootstrap resources: a `GitRepository` plus a `Kustomization`
or `HelmRelease` (selected by the module's `config.mode`).

Two handoff layouts are supported:

- **Rendered-chart-as-artifact (default):** commit the `cds render --target
  helm` output directory under `path` in the GitOps repository; the rendered
  `Kustomization` applies those manifests into `targetNamespace`. The chart
  contains no secret values (they stay `$(CDS_*)` expansions backed by
  `secretKeyRef`), so the commit is safe to publish.
- **Chart-as-source:** commit the chart directory itself and point the
  rendered `HelmRelease` at it (`config.chartPath`); Flux installs the release
  and reconciles it on every `syncInterval`.

The module declares raw Kubernetes manifests via
`implementation.kubernetes.manifests`, a new module-schema block the Helm
renderer emits verbatim as extra chart templates (substituted like any other
module value, named `{{ .Release.Name }}-...`, and shape-checked with E151).
Because a GitOps module has no workloads of its own, it gets no ServiceAccount
or NetworkPolicy, and rendering a profile that contains it with
`--target compose` fails closed with E070.

Non-goals: CDS does not install or run the GitOps controllers themselves —
the cluster needs a FluxCD (or ArgoCD) installation with its CRDs present
before the rendered chart is applied — and CDS does not commit to the GitOps
repository; rendering produces local files the operator commits. ArgoCD can be
added later as a second provider of the same `gitops-target` contract.

## Troubleshooting

- If Pods remain Pending, inspect node taints. Docker disk pressure requires
  freeing Docker VM space, then restarting only this worktree's k3d server node.
- If a context is missing, export the `KUBECONFIG` printed by `make k3d-up`.
- If an imported image cannot start, confirm its workload uses
  `imagePullPolicy: Never` and rerun `make k3d-up` after the image build.
- If Helm reports missing secret data, rerun `cds init`, fill every required
  `CDS_*` value, and keep `.env` untracked.
