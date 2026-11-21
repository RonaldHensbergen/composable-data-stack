# Mission

Composable Data Stack (CDS) is an open, Apache-2.0-licensed framework for
assembling and operating modular data platforms from interoperable,
open-source components. Its central ambition is to make **data sovereignty
practical**: organizations should retain meaningful control over their data,
infrastructure, software choices, and ability to change providers.

CDS does not reinvent databases, orchestrators, or BI tools. It brings
existing technologies together through declarative **profiles**, reusable
**modules**, and explicit **contracts**, then validates, plans, and renders
the stack to a runtime artifact (Docker Compose today; Helm for Kubernetes as
an experimental target). CDS is a compiler and CLI for data platforms — it
renders configuration and delegates lifecycle to the selected runtime; it is
not a long-running service and does not run containers itself. The one-page
project scope lives in [SCOPE.md](SCOPE.md).

## Who CDS Is For

- **Dutch local governments — the target launching-customer segment.** CDS is
  first built and validated against Common Ground/Haven platform conventions,
  FDS/IBDS data-sharing agreements, and NIS2/Cyberbeveiligingswet operator
  obligations (see [docs/roadmap.md](docs/roadmap.md) and the *Dutch
  Public-Sector Data & Security Alignment* milestone). This is targeted, not
  agreed: no government has committed yet, and pilot conversations are part
  of the leading plan (see [SCOPE.md](SCOPE.md)).
- Other organizations that need control over sensitive or strategically
  important data
- Smaller teams that need reproducible infrastructure across dev, CI, and
  prod
- Research organizations and independent service providers

These remain audiences to validate, not confirmed market segments — and the
target segment is a starting point, not a limit; see
[Beyond Haven, NIS2 and CRA](#beyond-haven-nis2-and-cra).

## Beyond Haven, NIS2 and CRA

The Dutch public-sector alignment is a beachhead, not a boundary. CDS itself
is jurisdiction-agnostic: profiles, modules, and contracts carry no
hardcoded legal assumptions, so the same stack can run under different laws
and frameworks — other EU rules such as GDPR, national or sector-specific
regimes, or none of these at all.

Compliance mappings are layered on top as documentation and checkable
evidence, never baked into the core. Haven/Common Ground alignment and NIS2
operator-readiness checks live in the `cds security` rule set and docs, and
[docs/cra-scope-decision.md](docs/cra-scope-decision.md) shows the pattern:
CDS documents its own product-security posture without claiming CRA scope or
conformity. A new jurisdiction is added the same way — map its obligations,
encode what can be machine-checked in `cds security`, and document what
remains an operator responsibility.

## What Data Sovereignty Means Here

1. **Data control** — the organization determines storage location,
   processing, access, and sharing conditions.
2. **Technology independence** — components and providers can be selected or
   replaced where contracts and migration requirements permit.
3. **Infrastructure choice** — the platform deploys on infrastructure the
   operator selects, within supported backends.
4. **Transparency and auditability** — architecture, dependencies,
   configurations, and operational assumptions can be inspected.
5. **Continuity and portability** — a data environment can be reproduced and
   moved, with documented handling of data, secrets, storage, and backups.
6. **No lock-in — not even to CDS itself** — components are replaceable via
   contracts, and once rendered the stack runs on standard artifacts without
   CDS (see [No Lock-In](#no-lock-in)).

These are architectural objectives, not finished product claims. They must
not be presented as fully demonstrated capabilities until supported by tests
and pilot evidence; open source alone does not guarantee sovereignty. Where
CDS cannot yet prove something, it says so in the docs and tracks the gap as
an issue.

## What CDS Is (and Is Not)

| It **is** | It is **not** |
| --- | --- |
| A YAML-driven CLI (`cds`) | A cloud provisioner or Terraform replacement |
| A validator + planner + renderer | An orchestrator or runtime agent |
| A generator of Compose and Helm artifacts | A service that stays running |
| A contract-checking compiler for data platforms | A re-implementation of the tools it composes |
| A runtime requirement after rendering | A lock-in — the rendered artifacts run standalone (see below) |

## No Lock-In

CDS compiles; it does not own your runtime. The rendered artifacts are
standard and standalone: a `docker-compose.yml` that runs with plain
`docker compose`, or a Helm chart that installs with plain `helm`. After
rendering, CDS is **not required** to keep using or operating the stack — the
artifacts have no runtime dependency on CDS, so you can stop using CDS
entirely and keep running them as-is.

If you do keep using CDS, [`cds update`](docs/cli-reference.md) still helps
keep the profile current: it refreshes profiles, modules, contracts, and
runtime assets previously fetched with `cds get` from their recorded source,
preserving your local edits.

## Security Is a Top Priority

Security is a first-class, built-in concern in CDS — not an add-on:

- `cds security` runs a data-driven rule set (`cli/resources/rule-set.json`)
  against every profile before deployment and fails closed on unsafe
  configurations: weak or default credentials, missing secrets, wrongly
  exposed services, unsafe module defaults, and contract bindings that can
  leak data.
- Secrets are never resolved into plans or rendered Compose/Helm output:
  profiles only map aliases to `CDS_*` environment names, and the runtime
  resolves the values at container start.
- Validation failures are structured diagnostics with stable E-/W- codes and
  precise data paths, so problems surface at compile time instead of at
  runtime.
- Published images are signed and carry SBOM and SLSA provenance
  attestations, and `cds preflight` checks the Docker host against a
  hardening baseline before anything starts.
- Vulnerability reporting and the security support window are defined in
  [SECURITY.md](SECURITY.md) and
  [docs/security-support-policy.md](docs/security-support-policy.md); CRA
  and NIS2 readiness work is tracked in [docs/roadmap.md](docs/roadmap.md).

As with sovereignty, security claims must be backed by tests and evidence —
the rule set, golden tests, and CI scans are the proof, and known gaps are
tracked as issues, not hidden.

## Non-Goals

- CDS is not Kubernetes-only: Docker Compose is the stable single-host path;
  the Helm target is experimental. Further backends (Podman/Quadlet, Docker
  Swarm, and others) are researched under the *Platform-neutral deployment
  targets* milestone, not committed features.
- CDS does not claim every tool is interchangeable. Contractual compatibility
  is validated at compile time; behavioral and data compatibility still
  require integration tests.
- Automated contract generation from external specs is an exploratory idea,
  not a current promise.

## How Decisions Are Made

- Work is prioritized by the roadmap: labeled issues in the tracker, ordered
  by the *Composable Data Stack Roadmap* project; releases follow the weekly
  train in [docs/release-strategy.md](docs/release-strategy.md) — a release
  is cut only when `main` is green and the changelog is ready.
- Changes ship as small PRs from short-lived branches with green CI; `main`
  is protected and merges follow [docs/maintainer-merge-policy.md](docs/maintainer-merge-policy.md).
  See [CONTRIBUTING.md](CONTRIBUTING.md) for the full working style.
- **Repository transition (the plan):** per the leading plan, project
  collaboration — source history, issues, PRs, CI — migrates to **Codeberg**
  (planned 20 November – 4 December 2026; see [SCOPE.md](SCOPE.md)). GitHub
  then stays read-only as a signpost. This is consistent with the sovereignty
  principles above: hosting and collaboration under the project's own
  control, not on a platform it does not control. Until the cutover
  completes, the flow above runs on GitHub as it does today; afterwards the
  same flow runs on Codeberg.
- The maintainer is Ronald Hensbergen (see `.github/CODEOWNERS`); roles and
  authority are revisited as the community grows.
- Agent contributors follow the canonical instructions in
  [AGENTS.md](AGENTS.md), including the agent approval policy: merges,
  protected-path changes, and secrets handling always require a human.

## Current State

CDS is at v0.15.x, pre-1.0, actively developed. Stable today: `cds
validate` / `plan` / `render` / `security` / `up` / `down` / `test`, the
Compose render target, and the Dagster, Postgres, Superset, KeyDB, Vault,
dbt, DuckDB, Istio, and Traefik modules. Experimental: the Helm/Kubernetes
target, Airflow, dlt, Keycloak, Marquez, and Loki. The authoritative,
always-current picture — including the *Production ready*, *Dutch
Public-Sector Data & Security Alignment*, and *Platform-neutral deployment
targets* milestones — lives in [docs/roadmap.md](docs/roadmap.md) and
[CHANGELOG.md](CHANGELOG.md), not in this file.

## Community And Governance

- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — how we treat each other
- [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/good-first-issues.md](docs/good-first-issues.md) — how to contribute
- [SECURITY.md](SECURITY.md) and [docs/security-support-policy.md](docs/security-support-policy.md) — how to report vulnerabilities and which versions receive fixes
- [SUPPORT.md](SUPPORT.md) — how to get help
- [LICENSE](LICENSE) — Apache-2.0

## Where To Start

- New users: [README.md](README.md), then [docs/how-it-works.md](docs/how-it-works.md)
- Architecture: [docs/architecture.md](docs/architecture.md)
- Roadmap and milestones: [docs/roadmap.md](docs/roadmap.md)
- Kubernetes target: [docs/kubernetes.md](docs/kubernetes.md)
- Haven+ parity plan: [docs/haven-parity-plan.md](docs/haven-parity-plan.md)
