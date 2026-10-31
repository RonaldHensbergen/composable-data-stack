# FluxCD GitOps bootstrap (experimental)

This module is the reference provider of the shared
[`gitops-target` contract](../../../shared/contracts/gitops-target.yaml)
(issue #767). It renders the FluxCD bootstrap resources — a `GitRepository`
plus either a `Kustomization` or a `HelmRelease` — that point a pull-based
GitOps controller at a CDS Helm chart committed to a Git repository. It is
experimental, Kubernetes-only (`implementation.targets: [kubernetes]`), and
marked `productionSuitable: false`.

## What CDS does and does not do

CDS renders the bootstrap manifests into the Helm chart
(`implementation.kubernetes.manifests`), so they are committed and reviewed
like everything else the chart produces. CDS does **not** install or run the
FluxCD controllers themselves: the cluster needs a FluxCD installation
(`flux bootstrap` or a Helm install of the Flux controllers) whose CRDs
(`source.toolkit.fluxcd.io`, `kustomize.toolkit.fluxcd.io`,
`helm.toolkit.fluxcd.io`) exist before the chart is applied. Applying the
chart without the CRDs fails in the cluster, not in `cds render`.

Because the module declares no workloads, it contributes no pods, no
ServiceAccount, and no NetworkPolicy — only the Flux CR templates
(`templates/<module-id>-manifest-*.yaml`). Rendering a profile that contains
this module with `--target compose` fails closed with `E070`, since a GitOps
bootstrap has no Compose meaning.

## Configuration

| Field | Required | Description |
| --- | --- | --- |
| `repositoryUrl` | Yes | Git URL of the GitOps source-of-truth repository, e.g. `https://github.com/org/cds-gitops.git` |
| `path` | Yes | Path in the repository the controller applies: the committed `cds render --target helm` output directory (`rendered` mode) or the chart directory (`chart` mode) |
| `branch` | No | Tracked branch/ref; defaults to `main` |
| `mode` | No | `rendered` (Kustomization applies rendered manifests) or `chart` (HelmRelease installs the chart); defaults to `rendered` |
| `syncInterval` | No | Reconciliation interval of the Flux CRs, Go duration format; defaults to `1m0s` |
| `prune` | No | Kustomization prunes resources removed from the repo (`rendered` mode); defaults to `true` |
| `targetNamespace` | No | Namespace the controller deploys the stack into; defaults to `cds-local` |
| `releaseName` | No | Helm release name for the `HelmRelease` (`chart` mode); defaults to `cds` |
| `chartPath` | No | Chart location within the Git source (`chart` mode); defaults to `./chart` |
| `fluxNamespace` | No | Namespace the rendered Flux CRs live in (the namespace the controllers watch); defaults to `flux-system` |

Example profile fragment:

```yaml
- id: gitops
  source: modules-experimental/gitops/fluxcd
  version: "0.1.0"
  enabled: true
  config:
    repositoryUrl: https://github.com/example/cds-gitops.git
    branch: main
    mode: rendered
    path: clusters/local/rendered
    targetNamespace: cds-local
```

## The two handoff models

`cds render --target helm` output is the source-of-truth artifact the GitOps
controller consumes. Two layouts are supported via `config.mode`:

- **`rendered` (default, rendered-chart-as-artifact):** commit the rendered
  chart directory (the `cds render --target helm --output ...` output) under
  `path` in the GitOps repository. The rendered `Kustomization` applies those
  manifests verbatim into `targetNamespace`. Secrets stay out of the commit:
  the chart references them via `secretKeyRef`/`$(CDS_*)` expansions, and the
  values are supplied at apply time (Flux `Kustomize` post-build variable
  substitution or an external secret store), never written by CDS.
- **`chart` (chart-as-source):** commit the chart directory itself under
  `path` and set `chartPath` to its location. The rendered `HelmRelease`
  installs it as release `releaseName` into `targetNamespace`, so Helm values,
  hooks, and `helm upgrade` semantics apply; Flux reconciles the release on
  every `syncInterval`.

In both modes a `GitRepository` CR names `repositoryUrl` + `branch` as the
source, and every rendered CR is named `{{ .Release.Name }}-...` and placed in
`fluxNamespace`.

## Non-goals

- CDS does not run, install, or upgrade FluxCD (or ArgoCD): no controllers are
  deployed by this module. ArgoCD can later be added as a second provider of
  the same vendor-neutral `gitops-target` contract; it is not a parallel
  module today.
- CDS does not manage the GitOps repository's contents (no committing, no
  PR automation); rendering produces local files that the operator commits.
- Raw manifests never carry secret values: a leftover `${CDS_*}` reference in
  a chart file fails the render with `E078`, keeping secret material out of the
  committed chart.
