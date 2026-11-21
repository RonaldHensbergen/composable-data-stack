# Project Scope

One page, approved together with [MISSION.md](MISSION.md). The **six-point
action plan for 17 November – 31 December 2026 is the leading plan** for this
period; this document defines the scope that plan works within — and what is
deliberately out of scope.

## The Leading Plan (17 Nov – 31 Dec 2026)

1. **Mission, scope and decision-making** — `MISSION.md` + this scope
2. **MVP and acceptance criteria** — defined from existing implementation,
   including the Stackable/OKDP landscape comparison
3. **Repository migration (20 Nov – 4 Dec) — the plan** — project
   collaboration moves to **Codeberg**: git history and feasible issues
   migrate, CI dependencies are reviewed, documentation and contributor links
   updated, backups and maintainer access defined; GitHub stays read-only as
   a signpost. This follows from CDS's sovereignty principles: hosting under
   the project's own control, not on a platform it does not control.
4. **Two independent reviews** — functional fit against the MVP, and code
   quality / security / tests, plus the infrastructure-backend assessment
   (Podman/Quadlet, Docker Swarm, Incus)
5. **Prioritize, stabilize, release gate** — feature freeze only once the
   agreed MVP criteria pass, not on a calendar date
6. **Community and pilot** — contribution and governance basics, and a narrow,
   measurable pilot

## In Scope Now

- The CDS compiler/CLI pipeline — load -> validate -> security -> plan ->
  render — and lifecycle delegation (`cds up` / `down` / `state`) for the
  Docker Compose target (stable) and the Helm/Kubernetes target (experimental)
- Modules and shared contracts (`modules/`, `modules-experimental/`,
  `shared/contracts/`), profiles (`profiles/`), the security rule set,
  tests, docs, and release engineering
- The Dutch public-sector alignment (FDS/Common Ground/Haven, NIS2, CRA
  records) as the beachhead for the target launching customer — Dutch local
  governments, targeted but not yet agreed by any government — layered on
  top, never limiting CDS to those frameworks (see
  [MISSION.md](MISSION.md#beyond-haven-nis2-and-cra))

## Out Of Scope For Now

- New deployment backends as commitments — Podman/Quadlet, Docker Swarm,
  Incus, Nomad and friends are research candidates under the *Platform-neutral
  deployment targets* milestone: evaluated, not promised
- Automated contract generation from external specs — an exploratory post-MVP
  idea, not a current promise
- CDS running containers itself — CDS renders standard artifacts; Docker
  Compose or Helm operate the stack
- Any claim of production readiness, security, or sovereignty beyond what
  tests and pilot evidence actually show

## Existing Capabilities vs. Claims Still Requiring Evidence

- **Existing and tested** (details in [docs/roadmap.md](docs/roadmap.md)):
  `cds validate` / `plan` / `render` / `security` / `up` / `down` / `test`,
  the Compose target, the Dagster / Postgres / Superset / KeyDB / Vault / dbt
  / DuckDB / Istio / Traefik modules, and signed images with SBOM and
  provenance attestations
- **Requiring evidence** — tracked as issues, never asserted in docs:
  behavioral and data interchangeability behind a contract, operational
  parity across backends, sovereignty demonstrated in a real pilot

## Maintainer Responsibilities And Decision Process

- **Maintainer and code owner:** Ronald Hensbergen (code owner via
  `.github/CODEOWNERS` today, the Codeberg equivalent after the cutover).
  Owns final merge and release decisions, security triage, and roadmap
  ordering; roles are revisited as the community grows.
- **Decisions:** labeled issues ordered by the roadmap project; PRs merge on
  review plus green CI into protected `main`
  ([docs/maintainer-merge-policy.md](docs/maintainer-merge-policy.md));
  releases follow the weekly train
  ([docs/release-strategy.md](docs/release-strategy.md)). Until the Codeberg
  cutover (plan point 3) completes, this flow runs on GitHub; afterwards the
  same flow runs on Codeberg.
- **Agents** follow [AGENTS.md](AGENTS.md), including the agent approval
  policy: merges, protected-path changes, and secrets always require a human.
