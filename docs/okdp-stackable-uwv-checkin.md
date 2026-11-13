# Check-in: OKDP, Stackable and UWV's Open Data Analytics Platform

Version: 0.1
Status: Draft — landscape check-in, not a plan. Candidate follow-ups are listed
in section 7 and are not filed as issues yet.
Date: 2026-11-13
Related: [docs/architecture.md](architecture.md),
[docs/haven-parity-plan.md](haven-parity-plan.md),
[docs/common-ground-alignment.md](common-ground-alignment.md),
[docs/kubernetes.md](kubernetes.md), [docs/roadmap.md](roadmap.md)

## 1. Scope and method

This document checks in what three external open-source data platforms do and
how they position themselves, and derives lessons for CDS. Reviewed on
2026-11-13 from public sources (listed in section 8):

- **OKDP** — Open Kubernetes Data Platform, a French community initiative.
- **Stackable** — Stackable GmbH (Germany), the company behind the Stackable
  Data Platform (SDP).
- **UWV OpenDataAnalyticsPlatform** — a Dutch public-sector reference
  implementation built on Stackable.

All three are Kubernetes-native. CDS is not (yet): it compiles profiles to
Docker Compose today and has a Kubernetes/Helm rendering path in progress
(see [docs/kubernetes.md](kubernetes.md) and
[docs/haven-parity-plan.md](haven-parity-plan.md)). That difference is itself one
of the findings below.

## 2. The three platforms at a glance

| Dimension | OKDP | Stackable | UWV OpenDataAnalyticsPlatform |
| --- | --- | --- | --- |
| Who is behind it | Community under the TOSIT association; initiated by DGFiP (French tax administration), Orange and others; members include BPCE, Société Générale | Stackable GmbH, a German company; open-source distribution with commercial support | UWV (Dutch Employee Insurance Agency), published on the Dutch government code platform code.overheid.nl |
| License | Apache-2.0, 100% open source | Open source core (operators, CLI), company-backed with support/SLA editions | Open source; repo carries an explicit disclaimer that it is a fictional/illustrative reference, not a UWV product |
| Runtime | Kubernetes (any cluster; Kind sandbox in one command) | Kubernetes incl. OpenShift; any cloud or on-prem | Kubernetes; three deploy modes: k3d (laptop), AKS (production), StackIT SKE |
| Packaging model | Each component as a standalone Helm chart (helm-handbook), plus an optional Control Plane UI with GitOps (ArgoCD) or direct deploy | One Kubernetes operator per product, driven by product CRDs on a common framework; installed via `stackablectl` or Helm; released together on a predictable schedule | Layered GitOps repo: `stackablectl` release of operators, then per-component platform manifests with per-environment overlays, driven by `make` targets |
| Components | Apache Spark, Hive Metastore, Trino, Superset, Airflow, JupyterHub, Onyxia, Spark History Server, … | Operators for Kafka, Spark, Trino, Superset, Airflow, Druid, HDFS, Hive, HBase, NiFi, OpenSearch, ZooKeeper, OPA, plus commons/listener/secret operators | SeaweedFS/MinIO (S3), Delta Lake, Hive Metastore, Spark streaming, Trino + OPA, dbt-trino, Airflow 3 + cosmos, Superset, JupyterHub, OpenMetadata, Keycloak, portal shell, Vector + Prometheus |
| Positioning | "Modular, sovereign, community-driven"; full control of the tech lifecycle, no vendor lock-in; deploy all or part | "Curated selection of the best open source data apps from a single source"; daily interoperability testing, signed images, one point of contact; explicitly against assembling Helm charts yourself | Compliance-first reference platform: NORA, AVG/GDPR, BIO/BIO2, NIS2, AI Act; OPA/Rego policy-as-code on Trino (purpose binding, row filters, column masking) |
| Control plane | Optional Control Plane UI + REST API for deploy/configure/monitor across clusters; components usable without it | `stackablectl` CLI + Stackable Hub (CRD reference); no mandatory UI | Makefile-driven lifecycle (`doctor`, `deploy`, `seed`, `test`, `clean`) + a portal that embeds all service UIs under one URL |

## 3. OKDP — community platform, Helm-first

OKDP (okdp.io, github.com/OKDP) describes itself as "a free, open-source and
cloud-native data platform built on Kubernetes. Modular, sovereign, and
community-driven." The architecture has two layers:

1. **A reference ecosystem of data & AI modules**, each distributed as a
   standalone Helm chart, installable independently of anything else and
   combinable with existing GitOps tooling (ArgoCD, Flux).
2. **An optional Control Plane** (UI + REST API) to deploy, configure and
   monitor all components across one or more clusters. The control plane is
   explicitly optional — the modules work without it.

Key traits: sovereignty and vendor-independence as the headline promise
("maîtriser le cycle de vie des technologies, sans aucune dépendance avec les
éditeurs"), governance through TOSIT — an association that hosts
administration- and company-initiated open-source projects and is open to all
participants. Roadmap is public, with planned AI/MLOps additions (Kubeflow,
MLflow, LLM serving).

## 4. Stackable — company-backed operator platform

Stackable (stackable.tech, github.com/stackabletech) is "a company based in
Germany building an open-source distribution for modern data platforms on top
of Kubernetes." Its model:

- **One operator per product**, each with a product-specific custom resource
  (e.g. `TrinoCluster`, `SparkCluster`). All operators are built on a common
  framework, so every product CR "looks and behaves the same": authentication
  (LDAP/OIDC, OPA authorization), storage, monitoring, logging are configured
  uniformly across products.
- **Cross-cutting operators**: commons (shared CRDs), listener (ingress/exposure
  via `ListenerClass`/`Listener`), secret (SecretClass with backends such as
  Vault, cert provisioning).
- **Release train**: products are released together, tested daily for
  interoperability across versions and Kubernetes installations, images are
  signed, and fixes/upgrades arrive as one platform on a predictable schedule.
  The Stackable Hub hosts the CRD reference.
- **CLI**: `stackablectl` knows the platform releases and can install a set of
  operators, run demos, and inspect the cluster.
- **Commercial model**: open-source core; the company sells support/SLA
  (Enterprise), which is the classic "open core with a services company"
  positioning. Their own docs frame the alternative as assembling Helm charts
  per product and owning configuration, security and upgrades yourself.

## 5. UWV OpenDataAnalyticsPlatform — Stackable as the substrate of a governed platform

The UWV repo (code.overheid.nl/UWV/OpenDataAnalyticsPlatform, "An Open Source
Data and Analytics Platform based on Stackable") is the most instructive of the
three for CDS because it shows **what you build on top of an operator platform**:

- A **lakehouse + analytics stack**: S3 object storage (SeaweedFS on k3d,
  MinIO on AKS/StackIT), Delta Lake tables, Hive Metastore catalog, Spark
  Structured Streaming ingestion, Trino as query engine, dbt-trino, Airflow 3,
  Superset, JupyterHub, OpenMetadata for catalog/governance/lineage.
- **Compliance as a first-class feature**: NORA, AVG, BIO/BIO2, NIS2 and the
  AI Act are named in the README; authorization is policy-as-code (OPA + Rego
  policies for purpose binding/doelbinding, row filters, column masking),
  with an OpenMetadata access-bridge that turns approval workflows into
  Keycloak roles and OPA grants. All sample data is synthetic and watermarked.
- **Three environment modes with one command surface**: `make deploy
  MODE={k3d|aks|stackit}` — same platform, different cluster/storage/ingress,
  selected by values overlays (`values-k3d.yaml`, `values-aks.yaml`, …).
- **Pragmatic modularity**: the Stackable release file deliberately comments
  out NiFi/Kafka/ZooKeeper operators because the use case does not need them —
  "deploy only what you need" is an operational decision, not just a marketing
  line.
- Stackable operators used: airflow, commons, hive, listener, opa, secret,
  spark-k8s, superset, trino.

## 6. How the three compare

| Question | OKDP | Stackable | UWV platform |
| --- | --- | --- | --- |
| What is the unit of composition? | Helm chart per component | CR per product instance, on shared CRDs | Per-component manifests + env overlays, on Stackable operators |
| Who owns day-2 operations? | The adopter (charts are stateless) | The operators (continuous reconciliation, lifecycle) | Operators + GitOps + make targets |
| How is cross-component uniformity achieved? | Conventions in the helm-handbook | Common operator framework + commons/listener/secret CRDs | Inherited from Stackable + own portal/OPA layer |
| Security model | OIDC-secured complete stack option; per-chart | Uniform LDAP/OIDC/OPA across products, SecretClass | Keycloak OIDC + OPA/Rego at the query engine, policy-as-code |
| Governance | Association (TOSIT) with administrations and companies | Single company | Single organization (UWV), published openly |
| Entry point | `kind` sandbox, one command | `stackablectl` demo/stack | `make doctor` / `make deploy MODE=k3d` |

The common pattern: **all three sell "modular + sovereign + no lock-in", but
differ in the abstraction they put between the user and Kubernetes** — Helm
charts (OKDP), typed CRDs with operators (Stackable), and a governed
application layer on top of operators (UWV). CDS's profiles/modules/contracts
model is the Compose-side analogue of the same idea one abstraction level
down.

## 7. What CDS can learn from them

Mapped to CDS concepts, ordered by how concrete the lesson is:

1. **Compile-time validation is a differentiator — keep and advertise it.**
   Helm (OKDP) and CRDs (Stackable/UWV) only fail at apply/reconcile time.
   CDS's `load → validate → plan → render` pipeline with structured
   `Diagnostic`s and stable E-/W-codes catches contract mismatches, secret
   leaks and topology errors *before* anything is deployed. That is ahead of
   all three; it should be a headline feature, especially for the k8s target.
2. **Uniform cross-cutting concerns via shared contracts.** Stackable's
   commons/listener/secret CRDs are exactly what `shared/contracts/` is for
   Compose: one vocabulary for auth, storage, exposure, observability that every
   module speaks. Lesson: keep investing in shared contracts (and the
   contract-coverage docs) so every new module inherits security/observability
   instead of re-inventing it — the UWV portal/OPA layer shows the same idea
   at application level.
3. **Per-module standalone artifacts.** OKDP ships each component as an
   installable chart; CDS modules are already standalone `module.yaml` files,
   but only render inside a profile. Lesson: make "render a single module
   standalone" a first-class, tested path (the k8s target moves toward this).
4. **Environment selection as data, not code.** UWV's
   `MODE={k3d|aks|stackit}` with values overlays is the k8s mirror of CDS
   profiles per target (`local-*`, `production-*`, `*-kubernetes-example`).
   Lesson: formalize profile overlays/inheritance so a profile can be
   "base + environment patch", and keep the `make`-style preflight
   (`doctor`/`validate`) before any deploy.
5. **GitOps-native output layout.** OKDP and UWV both assume ArgoCD/Flux
   consuming the manifests. Lesson: define a canonical rendered-manifest
   layout (app-of-apps friendly) for the Helm/k8s target so `cds render`
   output drops straight into GitOps, like Stackable's `stack.yaml` entry point.
6. **Release train + compatibility matrix.** Stackable releases all products
   together with daily interoperability tests. CDS analogue: golden tests and
   contract tests per profile already exist; a versioned "platform release"
   that pins tested module versions (and signed images) would close the gap —
   see [docs/image-signing.md](image-signing.md).
7. **Policy-as-code for data access, not just deployment.** UWV puts OPA/Rego
   on Trino (row filters, column masking, purpose binding); Stackable ships an
   OPA operator. CDS contracts are deployment-level. Lesson: a future
   Trino/OPA module (or a `policy` contract) could carry data-level
   authorization as code, matching the NIS2/CRA docs the repo already maintains.
8. **Compliance-first positioning is a real market.** UWV's README leads with
   NORA/AVG/NIS2/AI Act, not with features. CDS already has CRA/NIS2/threat-model
   docs; lesson: surface them in the README/docs landing path as a positioning
   asset for Dutch public-sector adopters, alongside Haven/Common Ground
   alignment.
9. **A thin optional portal/control layer.** OKDP's Control Plane is optional;
   UWV embeds all UIs in one portal. Lesson: if CDS adds a UI, keep it optional
   and compose-compatible — modules must remain usable without it (same rule
   as OKDP).
10. **Pragmatic "lite" profiles.** UWV disables NiFi/Kafka/ZooKeeper on a
    laptop to save resources. Lesson: ship explicit lite/minimal profiles so
    `cds up` on a developer machine stays light; document what each profile
    deliberately omits.
11. **Neutral governance if the ecosystem grows.** TOSIT shows the endgame for
    community platforms (administrations + companies in one association).
    Not needed now, but keep contribution lanes and licensing (Apache-2.0)
    foundation-friendly.
12. **Secrets parity.** Stackable's SecretClass never materializes secrets into
    manifests; CDS's rule "never resolve secret values into plans or rendered
    Compose, only `${CDS_*}` placeholders" is the same principle. Lesson:
    document this parity explicitly when targeting k8s (External Secrets /
    SecretClass mapping), it is a strong security story against both.

What CDS should **not** copy: requiring a Kubernetes cluster for the default
workflow. OKDP/Stackable/UWV all assume k8s; CDS's Compose target is what makes
it usable on a laptop with only Docker. The k8s/Helm target should be an
additional rendering target, not a replacement — consistent with
[docs/haven-parity-plan.md](haven-parity-plan.md).

### Follow-ups (filed 2026-11-13)

- #1281 — Render a single module standalone for the Helm target (no full
  profile required).
- #1282 — Canonical app-of-apps/GitOps-friendly layout for rendered k8s
  manifests + docs page.
- #1283 — "Platform release" artifact: pinned, tested module-version set +
  signing evidence, surfaced per release.
- #1284 — Provider-neutral data-access policy contract (policy-as-code at the
  query layer).
- #1285 — README/docs landing: compliance positioning (CRA/NIS2/Haven/Common
  Ground) for public-sector adopters.

Not filed: profile overlay/inheritance — already exists today
(`cli/overlay.py`, `profiles/<name>/environments/<env>.yaml`; consolidation
tracked by #382), and `cds diff` over environment overlays is on the roadmap.
The lesson from UWV is therefore "use and document the existing mechanism",
not "build a new one".

## 8. Sources (accessed 2026-11-13)

- OKDP: [okdp.io](https://okdp.io/en/) · [github.com/OKDP/OKDP](https://github.com/OKDP/OKDP) ·
  [helm-handbook](https://github.com/OKDP/helm-handbook) · [TOSIT](https://tosit.fr/) ·
  [Adaltas introduction](https://www.adaltas.com/en/2026/06/29/okdp-data-platform-introduction/)
- Stackable: [stackable.tech](https://stackable.tech/en/) ·
  [Kubernetes/OpenShift](https://stackable.tech/en/kubernetes/) ·
  [concepts overview](https://docs.stackable.tech/home/stable/concepts/overview/) ·
  [Stackable Hub CRDs](https://hub.stackable.tech/crds) ·
  [crds.stackable.tech](https://crds.stackable.tech/) ·
  [github.com/stackabletech](https://github.com/stackabletech) (operators, stackablectl)
- UWV: [OpenDataAnalyticsPlatform on code.overheid.nl](https://code.overheid.nl/UWV/OpenDataAnalyticsPlatform)
  (README and per-component docs, incl. ADRs such as 0006-delta and 0011-seaweedfs)
