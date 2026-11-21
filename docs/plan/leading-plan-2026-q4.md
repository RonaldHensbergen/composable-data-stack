# Leading Plan: The Six-Point Action Plan, 17 Nov – 31 Dec 2026

Version: 0.1
Status: Draft — detailed elaboration of the leading plan summarized in
`SCOPE.md` (PR [#1324](https://github.com/RonaldHensbergen/composable-data-stack/pull/1324)).
Dates marked *(proposed)* are proposals until confirmed by the maintainer.
Related: [docs/roadmap.md](../roadmap.md),
[docs/haven-parity-plan.md](../haven-parity-plan.md),
[docs/plan/k8s-haven-mvp.md](k8s-haven-mvp.md),
[docs/release-strategy.md](../release-strategy.md),
[docs/good-first-issues.md](../good-first-issues.md),
[AGENTS.md](../../AGENTS.md)

## 1. Purpose and ground rules

This document is the detailed plan for the six-point action plan covering
**17 November – 31 December 2026**, the leading plan for this period as
declared in `SCOPE.md` (PR [#1324](https://github.com/RonaldHensbergen/composable-data-stack/pull/1324)).
It elaborates each point with a window, deliverables, owner, dependencies,
exit criteria and tracking references.

Ground rules:

- **Issue-level sequencing stays in the roadmap project.** The `Order` field
  of the "Composable Data Stack Roadmap" project (project 1) sequences
  individual issues; this document deliberately does not duplicate
  `docs/plan/milestone-issue-sequencing.md`, which is not updated.
- **Evidence over claims.** No production-readiness, security or sovereignty
  claim beyond what tests and pilot evidence show; claims requiring evidence
  are tracked as issues, never asserted in docs (the `SCOPE.md` convention).
- **Dates marked *(proposed)*** are proposals; the maintainer confirms them
  via the roadmap project.
- **Agent approval policy applies throughout** ([AGENTS.md](../../AGENTS.md)):
  merges, protected-branch pushes, protected-path changes and secrets always
  require a human.

## 2. Timeline at a glance

| Week (2026) | Points in flight |
| --- | --- |
| 17–23 Nov | 1, 2 (start 17 Nov), 3 (starts 20 Nov) |
| 24–30 Nov | 2 (ends 27 Nov), 3, 4 (start, *proposed*) |
| 1–7 Dec | 3 (ends 4 Dec), 4, 5 preparation (freeze decision when checklist green) |
| 8–14 Dec | 4 (ends 11 Dec, *proposed*), 5 (rc1 once `MVP.md` §4 is green) |
| 15–21 Dec | 6 (start, *proposed*), 5 (rc → final work) |
| 22–28 Dec | 6 (pilot), buffer for slipped items |
| 29–31 Dec | close-out review against section 5 |

## 3. The six points

### Point 1 — Mission, scope and decision-making (17–22 Nov, *proposed*)

- **Deliverables:** root-level `MISSION.md` (mission, target audiences with
  Dutch local governments as the targeted launching-customer segment — none
  agreed yet — data-sovereignty principles, is/is-not scope, no-lock-in
  statement, decision process incl. the planned Codeberg transition) and
  `SCOPE.md` (this leading plan in summary, in/out of scope, maintainer
  responsibilities).
- **Owner:** maintainer. **Depends on:** nothing. **Tracking:** PR
  [#1324](https://github.com/RonaldHensbergen/composable-data-stack/pull/1324).
- **Exit criteria:** both files merged, all relative links resolve,
  markdownlint clean, CHANGELOG updated.

### Point 2 — MVP and acceptance criteria (17–27 Nov)

- **Deliverables:** root-level `MVP.md` — the minimum deployable use case
  defined from existing implementation, the feature inventory, the testable
  acceptance checklist (each criterion with an objective test and owner), a
  concise Stackable/OKDP landscape comparison (full check-in:
  [docs/okdp-stackable-uwv-checkin.md](../okdp-stackable-uwv-checkin.md),
  follow-ups [#1281](https://github.com/RonaldHensbergen/composable-data-stack/issues/1281)–[#1285](https://github.com/RonaldHensbergen/composable-data-stack/issues/1285)),
  and the explicit out-of-scope list.
- **Owner:** maintainer. **Depends on:** point 1 (scope frames the MVP).
  **Tracking:** PR
  [#1331](https://github.com/RonaldHensbergen/composable-data-stack/pull/1331).
- **Exit criteria:** `MVP.md` merged with its §4 acceptance checklist;
  review threads resolved.

### Point 3 — Repository migration to Codeberg (20 Nov – 4 Dec)

- **Goal:** project collaboration moves to **Codeberg**; GitHub stays
  read-only as a signpost. Follows from the sovereignty principles: hosting
  under the project's own control.
- **Deliverables:** git history and feasible issues migrated; CI dependencies
  reviewed; documentation and contributor links updated; backups and
  maintainer access defined.
- **Owner:** maintainer (human-only actions: org/repo settings, secrets,
  merges). **Depends on:** point 1 (decision process documented in
  `SCOPE.md`). **Tracking:** discussion
  [#138](https://github.com/RonaldHensbergen/composable-data-stack/discussions/138)
  ("Plan: phased move from GitHub to Codeberg"); file tracking issues in the
  roadmap project when execution starts (loose end).
- **Exit criteria:** issues, PRs and CI run on Codeberg; the GitHub repo is
  read-only with a signpost README; the same maintainer flow
  ([docs/maintainer-merge-policy.md](../maintainer-merge-policy.md)) runs on
  Codeberg.

### Point 4 — Two independent reviews (24 Nov – 11 Dec, *proposed*)

- **Review A — functional fit against the MVP:** does the shipped
  implementation actually meet `MVP.md` (PR
  [#1331](https://github.com/RonaldHensbergen/composable-data-stack/pull/1331))
  §2–§4, including the "renders green but fails at runtime" scenarios.
- **Review B — code quality, security and tests**, plus the
  **infrastructure-backend assessment**: Podman/Quadlet
  ([#1303](https://github.com/RonaldHensbergen/composable-data-stack/issues/1303)),
  Docker Swarm
  ([#1304](https://github.com/RonaldHensbergen/composable-data-stack/issues/1304)),
  Ansible
  ([#1305](https://github.com/RonaldHensbergen/composable-data-stack/issues/1305)),
  Nix/NixOS
  ([#1306](https://github.com/RonaldHensbergen/composable-data-stack/issues/1306))
  and Incus — research candidates under the *Platform-neutral deployment
  targets* milestone, evaluated not promised; structural follow-ups
  [#1308](https://github.com/RonaldHensbergen/composable-data-stack/issues/1308)
  (target abstraction + ranked target roadmap) and
  [#1302](https://github.com/RonaldHensbergen/composable-data-stack/issues/1302)
  (capability contracts + per-target capability matrix).
- **Deliverables:** two review reports; every finding filed as a labeled
  issue in the roadmap project.
- **Owner:** two independent reviewers (maintainer + one external reviewer).
  **Depends on:** point 2 (review A needs the MVP definition). **Exit
  criteria:** both reports posted, findings triaged into the project.

### Point 5 — Prioritize, stabilize, release gate (from checklist green, not a calendar date)

- **Goal:** feature freeze **only once the agreed MVP criteria pass**, then
  cut **v1.0.0-rc1** and work towards 1.0.0 from there.
- **Deliverables:** freeze declaration + prioritized backlog + release
  readiness criteria; `v1.0.0-rc1` tagged (GitHub release only, not
  production PyPI); final 1.0.0 additionally requires the runbook Gates 2–5
  criteria.
- **Owner:** maintainer (version bump, tag push and release are human-only
  actions). **Depends on:** point 2 (`MVP.md` §4 checklist) and point 4
  review A. **Tracking:**
  [#1332](https://github.com/RonaldHensbergen/composable-data-stack/issues/1332)
  (milestone *v1.0.0 — MVP delivery (rc1 → final)*), docs PR
  [#1349](https://github.com/RonaldHensbergen/composable-data-stack/pull/1349),
  release criteria
  [#1115](https://github.com/RonaldHensbergen/composable-data-stack/issues/1115)
  (runbook Gate 5 / G5.1).
- **Exit criteria:** rc1 tagged with `MVP.md` §4 green, announced with the
  explicit non-claims (no certified Haven+ compliance; Helm still
  experimental until Gate 4.5,
  [#1206](https://github.com/RonaldHensbergen/composable-data-stack/issues/1206)/[#1207](https://github.com/RonaldHensbergen/composable-data-stack/issues/1207)/[#1208](https://github.com/RonaldHensbergen/composable-data-stack/issues/1208)).

### Point 6 — Community and pilot (15–31 Dec, *proposed*)

- **Deliverables:** contribution and governance basics (see
  [docs/good-first-issues.md](../good-first-issues.md) for newcomer-suitable
  work), and a **narrow, measurable pilot** with Dutch local governments as
  the target launching-customer segment (targeted, no government agreed
  yet — `MISSION.md`, PR
  [#1324](https://github.com/RonaldHensbergen/composable-data-stack/pull/1324)).
  Pilot criteria are defined before the pilot starts and evidence is
  recorded, per the evidence-over-claims rule.
- **Owner:** maintainer + pilot participant. **Depends on:** point 5 (pilot
  runs against rc1, not an arbitrary main state). **Exit criteria:**
  governance basics published; pilot executed with measurable results filed
  as issues/docs.

## 4. Dependencies between points

```text
1 (mission/scope) → 2 (MVP) → 4A (functional review) → 5 (freeze + rc1) → 6 (pilot)
                              → 4B (quality/security/backend review)
3 (Codeberg migration) runs in parallel; interacts with 5 — the weekly
release train keeps running on GitHub until the cutover completes
(point 3 exit criteria), then on Codeberg.
```

## 5. Period exit criteria (31 Dec 2026)

- All six points done, or explicitly re-planned with reasons recorded in the
  roadmap project (a slip is a decision, not a silence).
- `MVP.md` acceptance checklist green and v1.0.0-rc1 tagged (point 5), or the
  blocking findings filed as labeled issues with the freeze re-decided.
- Codeberg cutover complete or re-planned (point 3).
- Pilot results recorded with evidence (point 6).

## 6. What this plan deliberately does not do

- No new deployment-backend commitments: Podman/Quadlet, Docker Swarm,
  Ansible, Nix/NixOS and Incus are **evaluated** in point 4, committed only
  with evidence and capacity (issues #1303–#1306, milestone *Platform-neutral
  deployment targets*).
- No automated contract generation (post-MVP exploratory idea).
- No certified Haven+ compliance claim — Haven parity means "baseline
  capabilities present or tracked as issues"
  ([docs/haven-parity-plan.md](../haven-parity-plan.md) non-goals).
- No re-sequencing of existing roadmap issues — that stays in the project
  `Order` field.
- Not folded into PR
  [#1331](https://github.com/RonaldHensbergen/composable-data-stack/pull/1331):
  item 2 stays a docs-only MVP deliverable; this plan is its own deliverable.
