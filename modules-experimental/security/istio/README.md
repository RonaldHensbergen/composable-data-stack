# Istio service mesh (experimental)

This module is the reference provider of the shared
[`service-mesh` contract](../../../shared/contracts/service-mesh.yaml)
(issue #776, `docs/haven-parity-plan.md` workstream 3.4). It renders the
Istio security resources that enforce mutual TLS between the profile's named
module services on the Helm render target, plus an optional per-service
`AuthorizationPolicy`. It is experimental, Kubernetes-only
(`implementation.targets: [kubernetes]`), and marked
`productionSuitable: false`. It is not wired into any stable profile.

## What CDS does and does not do

CDS renders the Istio CRs into the Helm chart
(`implementation.kubernetes.manifests`), so they are committed and reviewed
like everything else the chart produces:

- a namespace-wide `PeerAuthentication` (`security.istio.io/v1`) with
  `mtls.mode` from `config.mtlsMode` (`STRICT` by default), enforcing mTLS
  for every workload in the release namespace;
- when `config.authorization.enabled` is true, **one `AuthorizationPolicy`
  per enrolled service** (`action: ALLOW`), fanned out over `config.services`
  via the renderer's `forEach` manifest marker (issue #1170): each policy is
  named `mesh-allow-<service>` and carries
  `spec.selector.matchLabels: {cds.dev/module: <service>}`, so it applies
  only to that service's pods, with `spec.rules` coming verbatim from
  `config.authorization.rules` — the per-service allow rules on top of mTLS
  (e.g. only `warehouse`'s pods may be called by `orchestration`'s service
  account). When disabled (the default) no `AuthorizationPolicy` is rendered,
  because an ALLOW policy with an empty rules list would deny everything it
  selects; mTLS alone then governs between enrolled services.

CDS does **not** install or run the Istio control plane: the cluster needs an
Istio installation (istio-base + istiod) whose CRDs
(`security.istio.io`) exist before the chart is applied. The module declares
that prerequisite under `implementation.kubernetes.operators`, so
`cds render` / `cds test` / `cds up --target helm` warn with **W130** naming
the install command, the chart's `NOTES.txt` repeats it, and
`cds up --target helm` fails closed with **E155** while
`kubectl get crd peerauthentications.security.istio.io` is absent.

Because the module declares no workloads, it contributes no pods, no
ServiceAccount, and no NetworkPolicy — only the Istio CR templates
(`templates/<module-id>-manifest-*.yaml`). Rendering a profile that contains
this module with `--target compose` fails closed with `E070`, since a service
mesh has no Compose meaning.

Two boundaries worth knowing:

- **The mesh does not open network paths.** CDS's per-module default-deny
  NetworkPolicy still governs L3/L4 reachability between module instances;
  the mesh adds peer identity (mTLS) and optional L7 authorization on top.
  Enrolled modules that need to reach each other still need their
  contract-derived (or passthrough) NetworkPolicy edges.
- **Enrollment needs no consumer-side changes.** A profile enrolls an
  existing module by naming its instance id in `config.services`; the mesh
  policy selects the module's pods by the `cds.dev/module` label CDS renders
  on every workload. The enrolled modules themselves are untouched.

## Configuration

| Field | Required | Description |
| --- | --- | --- |
| `services` | Yes | Named services enrolled in the mesh: a list of `{name, port}` where `name` is a module instance id of the profile (e.g. `warehouse`, `orchestration`) and `port` the TCP port peers call it on |
| `mtlsMode` | No | `STRICT` (default) or `PERMISSIVE`; rendered into the namespace-wide `PeerAuthentication` |
| `authorization.enabled` | No | Render the per-service mesh `AuthorizationPolicy` copies; defaults to `false` |
| `authorization.rules` | No | Istio `AuthorizationPolicy` rules (`from`/`to`/`operation`), rendered verbatim into each per-service policy's `spec.rules`; defaults to `[]` |

Example profile fragment — two existing modules enrolled with no changes to
those modules:

```yaml
- id: warehouse
  source: modules/warehouse/postgres
  version: "0.1.0"
  enabled: true
  config: { ... }

- id: orchestration
  source: modules/orchestration/dagster
  version: "0.1.0"
  enabled: true
  config: { ... }

- id: mesh
  source: modules-experimental/security/istio
  version: "0.1.0"
  enabled: true
  config:
    mtlsMode: STRICT
    services:
      - name: warehouse
        port: 5432
      - name: orchestration
        port: 3000
```

The module advertises the resolved mesh on the `service-mesh` contract
(`mtlsMode`, `services`, `authorizationPolicy`), which `cds maturity` counts
as the service-mesh leg of the **Secured** tier.

## Installing Istio on the cluster

```bash
helm repo add istio https://istio-release.storage.googleapis.com/charts
helm repo update
helm upgrade --install istio-base \
  --namespace istio-system --create-namespace \
  istio/istio-base
helm upgrade --install istiod --namespace istio-system istio/istiod
kubectl get crd peerauthentications.security.istio.io   # CRD registered
```

For the mesh to actually intercept traffic, the release namespace also needs
sidecar injection (`kubectl label namespace <release-ns>
istio-injection=enabled`); the rendered `PeerAuthentication` then applies
to the injected workloads. See
[Istio's installation docs](https://istio.io/latest/docs/setup/install/helm/).
