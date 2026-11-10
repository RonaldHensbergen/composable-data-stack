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
- [Operator-managed modules](#operator-managed-modules)
- [Service mesh (mTLS between module instances)](#service-mesh-mtls-between-module-instances)
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

Without `--timeout`, the settle budget defaults to 180s plus 120s per local
image build (issue #1116): on a cold k3d cluster each built image adds build,
`k3d image import`, and kubelet image-load time before its pods can become
Ready, so a first run waits long enough instead of reporting a false failure.
With `--no-build` (or no local images) the default stays at 180s. When the
budget does expire, the failure message distinguishes pods that are still
initializing from pods that are actually unhealthy, and hints at re-running
`cds up` or passing `--timeout`.

`cds up --target helm` is self-contained, like the Compose target (#690). Before
`helm upgrade --install` runs, it:

1. checks that the cluster named by `--kube-context` is reachable. If it is
   not, the command fails with a legible error — unless `--create-cluster` is
   given and the context is a k3d one (`k3d-<name>`), in which case `cds`
   creates that cluster with `k3d cluster create <name> --wait` and waits for
   the node to be Ready;
2. builds every local image the rendered chart references (the compose
   services with a `build:` block, exactly the set that gets
   `imagePullPolicy: Never`), streaming `docker build` output to the log file;
3. imports those images into the cluster with `k3d image import` when the
   context is a k3d one. On a non-k3d cluster there is no supported way to
   load local images, so the command fails closed and tells you to switch the
   profile to `--image-source registry` instead.

`--no-build` skips steps 2 and 3; the images must then already be present in
the cluster. Registry-sourced images (`--image-source registry`) are pulled by
the cluster itself and need no build or import step.

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

Since #690, `cds up --target helm` itself builds the local images, ensures the
cluster, and imports the images, so `make k3d-build`/`make k3d-up` are no longer
strict prerequisites of `make k3d-install` — the wrapper scripts remain as thin,
worktree-scoped conveniences (branch-derived cluster name, kubeconfig, and port
block) that call `cds up --target helm` with the right `--kube-context`/
`--namespace`/`--release`. A cluster created by `--create-cluster` uses k3d's
default ports; use the harness when you want the deterministic per-branch host
port mappings.

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
into k3d by `cds up --target helm` itself (step 3 above; `make k3d-up` does the same for the
harness flow). This deliberately avoids the common combination of
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

Installing `chart/` with plain `helm` — without the `cds` CLI — is covered in
[Underlying commands](underlying-commands.md).

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
| Single-file host bind (`fromBind`) | ConfigMap projected at the bind target via `subPath`, read-only |
| Directory host bind | `emptyDir` (host content is not carried over) |

Dagster's Compose-only Unix socket does not cross Pod boundaries. The
Kubernetes implementation uses Dagster gRPC over a ClusterIP Service, as
recorded in ADR 0002.

### Sample data on the Helm target

The dagster module's `sampleData.enabled` (default `false`) ships the bundled
`offers-1000.csv` fixture into the dagster pods on the Helm target as the
`sample-data` ConfigMap, projected at `sampleData.containerPath` (default
`/app/data/cds/incoming/offers-1000.csv`) — the same path the Compose target
serves through its `sharedData` host bind, so both targets see the identical
file (issue #1117). The ConfigMap is rendered from `sampleData.hostPath`
(default `./workdirs/shared-data/incoming/offers-1000.csv`) at render time and
must stay under the 1 MiB ConfigMap limit (E075). When enabled, the user-code
container also gets `CDS_SAMPLE_DATA_ENABLED=true` and
`CDS_OFFERS_FIXTURE_PATH`, and the `load_offers_1000` job runs on a schedule
(idempotent: exactly 1,000 rows in `offers_1000` after every run). When the
parameter is absent or `false`, neither the ConfigMap nor its mounts are
rendered and behavior is unchanged.

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
- Reverse-proxy `config.routes` entries derive the same pair of edges
  (#1033, #883): each route binds an `http-service` provide through its
  `contractRef` (`<module-id>.<contract-name>`), and that target module
  opens egress on the proxy's pods and matching ingress on the target's
  pods, scoped to the route's backend port (the target's http-service
  contract port, mapped through the target's Service `targetPort` like a
  binding's port). Targets no route names stay unreachable from the proxy.
  A route that cannot yield a port-scoped edge (malformed or unknown
  contractRef, no matching http-service provide, no port evidence) adds no
  edge and is reported as `W129`; the `networkPolicy.egress` passthrough
  remains the escape hatch for anything routes cannot express.
- Within a module, pods may talk freely on any port: a module is the unit
  of composition and declares its own internal wiring (which is why the
  Superset init job and the webserver share one policy).
- Egress to the cluster DNS service is the only other unconditional rule. The
  built-in selector allows UDP and TCP port 53 to pods in the `kube-system`
  namespace labeled `k8s-app: kube-dns`. This matches common kubeadm, k3s/k3d,
  EKS, and GKE CoreDNS installations; the selector is fixed and does not
  discover DNS pods with different labels, such as OpenShift's `dns-default`.
  Under the default-deny policy, workloads on those clusters will not resolve
  service names until an allow rule matches their DNS pods. Add a rule under
  each affected module's `implementation.kubernetes.networkPolicy.egress`
  passthrough, using the cluster's actual DNS namespace and pod labels and
  allowing both UDP and TCP port 53. This rule is additive: it does not replace
  the built-in `kube-dns` selector.
- Egress to anything outside the plan -- an external database, an HTTPS API,
  a package index, an object store reached by address -- is denied. That is
  the security baseline: the only way to open external egress is a declared
  `implementation.kubernetes.egress` entry (see below, issue #1023). A DNS
  name cannot be declared: a NetworkPolicy can only express IP ranges, so
  resolve the name and declare the resulting IP/CIDR.
- `waitFor` gates are validated against this baseline at render time: a gate
  whose resolved host is neither a same-module Service emitted by this render
  (a workload disabled via `enabledFrom` gets no Service), a Service of a
  consumed provider module (the binding-derived egress edge is
  provider-module-wide, #1125), nor a reverse-proxy route target, and whose
  host:port no custom `networkPolicy.egress` ipBlock rule covers, would only
  hang until its timeout, so the render fails closed with E152 naming the
  gate and the
  unreachable host (#1031, #1033), and whose host:port no declared
  `implementation.kubernetes.egress` entry covers (#1023). A DNS-name gate
  host cannot be matched against ipBlock CIDRs at render time (no DNS
  resolution in the renderer): when the module declares a custom
  `networkPolicy.egress` passthrough rule that could cover the resolved
  address on the gate's port (an ipBlock peer and the gate's port open), the
  gate renders with a W133 warning instead of failing closed, while a gate
  naming another module's in-cluster Service still fails closed with E152
  (#1126).
- `waitFor` entry shapes are re-checked at render time too: a plan loaded from
  a file bypasses the module schema, so a malformed entry -- a non-list
  block, a non-mapping entry, a missing or non-string `host`, a missing
  `port`, a non-integer `timeoutSeconds` -- fails closed with E162 naming the
  entry path instead of crashing the renderer with a `KeyError`/`TypeError`
  (#1130).
- A gate on a consumed provider module's Service is also checked in the port
  dimension (#1124): the binding-derived egress rule is port-scoped, so the
  gate's Service-facing port is mapped through the provider Service's
  `port_map`
  (exactly as the contract port is mapped for the rule itself, keeping a
  `port`/`targetPort` remap correct) and must be covered by a binding to
  that host. An uncovered port fails closed with E163 naming the gate, the
  host, and the port. Same-module gates need no port check: same-module
  egress is unscoped.
- Provided contracts get the port-dimension cross-check too (issue #1245):
  when a provided contract's advertised host matches a rendered Service of
  the module (the E150 check), its numeric `port` — and for the sink kinds
  the port embedded in `connectionUri` — must equal one of that Service's
  declared ports (its `port`/`targetPort` entries, or
  `runtime.service.ports[].containerPort` when the plan carries the runtime
  block). A mismatch fails closed with E164 instead of shipping an endpoint
  consumers dial into a connection refused. Declared (not rendered)
  `implementation.kubernetes.services` entries contribute their own
  `port`/`targetPort`; an operator-created Service that declares no ports
  (e.g. CloudNativePG's `<cluster>-rw`) is left unchecked.
- NodePort and LoadBalancer services are intended entry points: ingress is
  opened to all sources on exactly their exposed ports, and CDS-K8S-007
  flags that exposure.
- The ServiceAccount mounts no API token by default; RBAC is opt-in via
  `implementation.kubernetes.serviceAccount.rbac` (malformed rules fail
  closed with E148), and wildcard grants are flagged by CDS-K8S-006.

### Declaring external egress

Contract bindings and reverse-proxy routes are the only ways to open
cross-module traffic, and they cannot name anything outside the plan. For an
external destination, declare it under `implementation.kubernetes.egress`
(issue #1023):

```yaml
implementation:
  kubernetes:
    egress:
      - host: 203.0.113.10          # IP literal -> ipBlock 203.0.113.10/32
        port: 5432                  # external PostgreSQL
      - cidr: 10.20.0.0/16          # a range, with optional carve-outs
        except: [10.20.0.13/32]     # stays denied
        ports: [443, 8443]          # HTTPS endpoints
        protocol: TCP               # default; UDP/SCTP also possible
```

Each entry renders as one ipBlock egress rule on the module's NetworkPolicy,
scoped to exactly the declared destination, `except` CIDRs, and ports; the
policy itself stays release-scoped (`{{ .Release.Name }}-...`). To open a
destination for one workload only, put the same list under that workload
(`implementation.kubernetes.workloads.<name>.egress`): it renders as an
additional workload-scoped NetworkPolicy whose podSelector also matches the
workload's `app.kubernetes.io/name` label, so the module's other pods keep
default-deny egress. Values support `${config.*}` substitution, so a
profile can supply the destination through module config. Malformed entries
(a DNS-name `host`, a `cidr` with host bits set, an `except` outside the
`cidr`, a bad port or protocol, an unresolved expression) fail closed with
E153. Without `port`/`ports` the rule covers all ports to that destination
only. The security scan records every declared entry as a CDS-K8S-009
finding whose severity scales with breadth: `0.0.0.0/0` on all ports is high,
an unscoped destination or a missing port list is medium, and a
destination+port-scoped entry is low -- the audit trail of an intentional
opening in `cds security --target helm`.

### When the baseline denies something you need

For anything the structured `egress` block cannot express -- cross-namespace
clients such as an ingress controller fronting a ClusterIP service, or
selector-based peers -- declare raw rules via
`implementation.kubernetes.networkPolicy.ingress`/`egress`: they are rendered
verbatim after the binding-derived, route-derived, and declared-egress rules,
and malformed shapes fail closed with E149. An ipBlock egress rule here also
lifts the E152 `waitFor` gate check for an external host it covers; a DNS-name
gate host cannot be matched against the CIDR at render time, so with such a
rule declared (ipBlock peer, gate port open) the gate renders with a W133
warning instead of failing closed -- gates on other modules' Services still
fail closed (#1126). The security scan records every
external-egress rule as a low CDS-K8S-008 finding, and every ingress rule
without a source restriction (no `from`, an empty `from` list, or an ipBlock
peer) as a low CDS-K8S-010 finding, so intentional openings stay visible in
`cds security --target helm`.

For example, a DNS provider with different labels needs a peer selector and
both DNS transports; replace the placeholder namespace and label with values
from the cluster:

```yaml
implementation:
  kubernetes:
    networkPolicy:
      egress:
        - to:
            - namespaceSelector:
                matchLabels:
                  kubernetes.io/metadata.name: <dns-namespace>
              podSelector:
                matchLabels:
                  <dns-pod-label>: <dns-pod-value>
          ports:
            - protocol: UDP
              port: 53
            - protocol: TCP
              port: 53
```

Because CDS generates one policy per module, add this passthrough to every
module whose workloads need to resolve names.

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
A manifest may also declare `forEach: <config path>` (issue #1170): when the
expression resolves to a list, the renderer emits one copy per element with
the element bound as `item` (`${item.name}` etc.), suffixing each copy's
template file name with its resolved `metadata.name`; duplicate names fail
closed with E156.
Because a GitOps module has no workloads of its own, it gets no ServiceAccount
or NetworkPolicy, and rendering a profile that contains it with
`--target compose` fails closed with E070.

Non-goals: CDS does not install or run the GitOps controllers themselves —
the cluster needs a FluxCD (or ArgoCD) installation with its CRDs present
before the rendered chart is applied — and CDS does not commit to the GitOps
repository; rendering produces local files the operator commits. ArgoCD can be
added later as a second provider of the same `gitops-target` contract.

## Operator-managed modules

A module whose endpoint is created by an operator (not by CDS-rendered pods)
declares that endpoint instead of a workload, e.g. the experimental
[`modules-experimental/warehouse/postgres-operator`](../modules-experimental/warehouse/postgres-operator/)
(issue #768), which renders a CloudNativePG `Cluster` CR. Two schema blocks
support this pattern:

- `implementation.kubernetes.services` declares Services the module's
  manifests cause to exist in the release namespace — CloudNativePG creates
  `<cluster>-rw`/`-ro`/`-r` for every Cluster. The names are *declared, not
  rendered*: they join the renderer's service registry, so contract-host
  validation (E084/E150), release-scoping of consumer binding hosts, and
  `waitFor` gates treat them exactly like rendered workload Services. Key
  `rw` on module instance `warehouse` is `warehouse-rw`, release-scoped to
  `{{ .Release.Name }}-warehouse-rw` — matching the operator-created Service
  for a Cluster named `{{ .Release.Name }}-warehouse`.
- `implementation.kubernetes.manifests` carries the CR itself (see
  [GitOps handoff](#gitops-handoff)). A manifest value that is exactly one
  secret reference (`secrets.*`, resolving to `${CDS_*}`) is rewritten to a
  Helm `required` lookup of `.Values.secrets`, so CRs like a `Cluster`'s
  `bootstrap.initdb.secret` can consume the app password without the chart
  ever storing it; any other leftover `${CDS_*}` still fails closed with
  E078.

Like the GitOps module, an operator-managed module runs no pods of its own, so
it gets no ServiceAccount or NetworkPolicy, and `--target compose` fails
closed with E070. CDS renders the CR but does not install the operator
itself — the module declares it under `implementation.kubernetes.operators`
(`name`, `crd`, `install`, optional `docs`), and CDS surfaces the
prerequisite at every step (issue #768):

- `cds render` / `cds test` / `cds up --target helm` print a **W130** warning
  naming the operator, its CRD, and the install command; the chart's
  `templates/NOTES.txt` repeats it after `helm install`.
- `cds up --target helm` checks the cluster first (`kubectl get crd <crd>`)
  and fails closed with **E155** while the CRD is absent, rather than letting
  `helm` fail with "no matches for kind". If the check itself cannot run
  (RBAC/connectivity), it warns and continues.

Installing CloudNativePG on the cluster (official chart; installs controller,
webhooks, RBAC, and CRDs):

```bash
helm repo add cnpg https://cloudnative-pg.github.io/charts
helm repo update
helm upgrade --install cnpg \
  --namespace cnpg-system \
  --create-namespace \
  cnpg/cloudnative-pg
kubectl get crd clusters.postgresql.cnpg.io                          # CRD registered
kubectl -n cnpg-system rollout status deployment/cnpg-cloudnative-pg  # ready
```

cert-manager is not required for a basic install (the operator manages its
own webhook/cluster CA by default). See the module README for the full flow
and the demo profile
[`profiles/local-kubernetes-postgres-operator`](../profiles/local-kubernetes-postgres-operator/).

## Service mesh (mTLS between module instances)

For profiles that need mutual TLS between module instances — rather than only
TLS at the ingress/reverse proxy — the vendor-neutral
[`service-mesh` contract](../shared/contracts/service-mesh.yaml) describes
the mesh (enforcement mode, enrolled named services, optional per-service
authorization policy), and the production-suitable reference provider
[`modules/security/istio`](../modules/security/istio/) (issue #776, promoted
by #1171) renders the Istio security resources on the Helm target:

- a namespace-wide `PeerAuthentication` with `mtls.mode` from
  `config.mtlsMode` (`STRICT` by default), and
- when `config.authorization.enabled` is true, one `AuthorizationPolicy`
  (`action: ALLOW`) **per enrolled service**, fanned out over
  `config.services` with the `forEach` manifest marker (issue #1170): each
  policy carries `spec.selector.matchLabels: {cds.dev/module: <service>}` and
  the rules from `config.authorization.rules`, so the allow rules apply
  per service rather than namespace-wide.

A profile enrolls an existing module simply by naming its instance id (and
port) in the istio module's `config.services` — no consumer-side changes to
that module: the mesh policy selects its pods by the `cds.dev/module` label.
Each enrolled name is validated at plan time against the profile's module
instance ids: an unknown id (a typo, or a disabled module) fails closed with
E159, because the per-service `AuthorizationPolicy` would otherwise select
zero pods — a silent no-op. The module advertises the resolved mesh on the
`service-mesh` contract, which `cds maturity` counts as the service-mesh leg
of the **Secured** tier.

Helm rendering also warns with W132 when a known enrollment names a module
that produces no workload pods, including a manifest-only module or one whose
workloads are all gated off by `enabledFrom`. If authorization is enabled, the
per-service policy would select zero pods; enroll a module with an enabled
workload or remove the enrollment.

Like operator-managed modules, the istio module runs no pods of its own (no
ServiceAccount/NetworkPolicy), declares the Istio control plane under
`implementation.kubernetes.operators` (W130 on render/test, E155 on
`cds up --target helm` while `peerauthentications.security.istio.io` is
absent), and fails closed with E070 on `--target compose`. The mesh adds peer
identity and L7 policy on top of CDS's default-deny NetworkPolicy — it does
not open L3/L4 paths by itself, so enrolled modules still need their
contract-derived (or passthrough) NetworkPolicy edges. The module is
included in the `production-kubernetes-example` and
`local-kubernetes-example` profiles. The Istio control plane remains an
out-of-band prerequisite: render/test report W130, and `cds up --target helm`
fails closed with E155 while the `PeerAuthentication` CRD is absent. See the
module README for the istiod install and namespace sidecar-injection label.

## Troubleshooting

- If Pods remain Pending, inspect node taints. Docker disk pressure requires
  freeing Docker VM space, then restarting only this worktree's k3d server node.
- If a context is missing, export the `KUBECONFIG` printed by `make k3d-up`.
- If an imported image cannot start, confirm its workload uses
  `imagePullPolicy: Never` and rerun `cds up --target helm` (or `make k3d-up`)
  after the image build.
- If the cluster is not reachable, create it (`k3d cluster create <name>`) or
  pass `--create-cluster` with a `k3d-<name>` `--kube-context`; `cds up --target
  helm` checks reachability before installing and fails with this guidance.
- If Helm reports missing secret data, rerun `cds init`, fill every required
  `CDS_*` value, and keep `.env` untracked.
- If every Service name lookup times out under the default-deny NetworkPolicy,
  the cluster's DNS pods are probably not labeled `k8s-app: kube-dns` in
  `kube-system` (for example OpenShift's `dns-default`): add the passthrough
  egress rule from
  [Network isolation and least privilege](#network-isolation-and-least-privilege)
  to every module that needs to resolve names (#1032).
