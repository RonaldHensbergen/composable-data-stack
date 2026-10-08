# Cyberbeveiligingswet / NIS2 Scope and Downstream-Compliance Role

Status: Scoping / gap-analysis record — not legal advice, not a compliance
or conformity claim
Related: [milestone "Dutch Public-Sector Data & Security
Alignment"](https://github.com/RonaldHensbergen/composable-data-stack/milestone/6)
(#771); distinct from the CRA product-security milestone
([#728](https://github.com/RonaldHensbergen/composable-data-stack/issues/728)-[#733](https://github.com/RonaldHensbergen/composable-data-stack/issues/733),
see [`docs/cra-scope-decision.md`](cra-scope-decision.md))

## Purpose and disclaimer

The Dutch Cyberbeveiligingswet (the pending national implementation of
Directive (EU) 2022/2555, "NIS2") is a distinct legal regime from the EU
Cyber Resilience Act ("CRA") already tracked under milestone 5. It is easy
to conflate the two because both are EU cybersecurity legislation with
overlapping vocabulary (risk management, incident reporting, supply chain
security). This document draws the line between them and records, per
NIS2 Article 21(2) measure category, whether CDS already provides
supporting evidence a Cyberbeveiligingswet-regulated organisation could
use, or has an open gap.

**This document makes no compliance or conformity claim.** It does not
state that any organisation using CDS satisfies Cyberbeveiligingswet/NIS2
obligations, and it must not be cited as evidence of conformity. It is a
scoping and gap-analysis aid only, in the same spirit as
[`docs/cra-scope-decision.md`](cra-scope-decision.md) and
[`docs/security-compliance-categories.md`](security-compliance-categories.md).
Consult qualified legal counsel and your own compliance function to
determine which obligations apply to your organisation.

## Who is actually regulated

- **The CRA (Regulation (EU) 2024/2847)** regulates *manufacturers,
  importers, and distributors* of products with digital elements — i.e.
  potentially CDS itself, as assessed in
  [`docs/cra-scope-decision.md`](cra-scope-decision.md) (currently: outside
  scope, as non-commercial FOSS under Article 2(12)).
- **NIS2 (Directive (EU) 2022/2555)** and its Dutch implementation, the
  Cyberbeveiligingswet, regulate *"essential"* and *"important"
  entities* — organisations operating in specified sectors (including many
  Dutch municipalities, provinces, water boards, and other
  uitvoeringsorganisaties) that must apply operational cybersecurity
  risk-management measures and report significant incidents to a national
  CSIRT within statutory timelines (an early warning within 24 hours, an
  incident notification within 72 hours, per Article 23).

**CDS itself is not an "essential" or "important" entity under NIS2 or the
Cyberbeveiligingswet.** CDS is a software supplier: a compiler/CLI that
validates and composes declarative data-platform profiles into Docker
Compose/Kubernetes artifacts (see
[`docs/cra-scope-decision.md`](cra-scope-decision.md)'s "Intended use"
fact). The entities potentially in scope are **CDS's users** —
municipalities and other public-sector organisations that *operate*
profiles built with CDS. NIS2 obligations attach to how those
organisations run their infrastructure, staff, and incident processes, not
to the tool used to compose it.

CDS's relevant role is therefore indirect: providing features and
documentation that help a regulated operator meet its own Article 21(2)
obligations, without CDS itself carrying any of those obligations, and
without CDS being able to guarantee the operator satisfies them.

## Article 21(2) measure categories and current CDS support

Directive (EU) 2022/2555, Article 21(2), requires essential/important
entities to apply an all-hazards approach covering at least the following
ten measure categories. For each, this table records whether a CDS
feature already produces supporting evidence an operator could cite, and
what — if anything — remains an open gap.

| # | Article 21(2) measure | CDS support today | Gap / follow-up |
| --- | --- | --- | --- |
| (a) | Policies on risk analysis and information system security | `cds security`/`cds test` findings, organized by `complianceCategory` (see [`docs/security-compliance-categories.md`](security-compliance-categories.md)), give an operator machine-checked input to their own risk analysis. CDS's own product-level risk analysis is in [`docs/threat-model.md`](threat-model.md). `cds security --report nis2` (#774) groups those findings directly by this table's measure letters. | The operator's formal risk-analysis policy itself is out of CDS's scope — it is an organisational artifact, not something a compiler can produce. |
| (b) | Incident handling | CDS's own vulnerability-handling process is documented in `SECURITY.md` and [`docs/security-support-policy.md`](security-support-policy.md). The CRA incident/vulnerability runbook (#729) documents CDS-side handling. | An operator's own incident-handling procedure (who is notified, how a CSIRT report is drafted) is out of CDS's scope; [`docs/audit-log.md`](audit-log.md)'s local audit trail of rendered/applied stacks (#737, closed) gives an operator evidence to *support* that procedure, not the procedure itself. |
| (c) | Business continuity (backup management, disaster recovery, crisis management) | `CDS-SEC-080` (#774, see [`docs/security-rules.md`](security-rules.md)) flags a profile with a durable data store (`sql-database`/`file-database`) that has no `backup-target` contract consumer bound anywhere. | No module in this repository provides a `backup-target` contract yet (tracked by #210, #665, #668, #669), so `CDS-SEC-080` fires on essentially every profile with a data store today — it documents the gap rather than closing it. Operating an actual backup/restore process remains the operator's responsibility. |
| (d) | Supply chain security (direct suppliers/service providers) | Product SBOM and release artifact inventory (#730, closed), the `patching` rule-set category (tag/digest pinning, cosign signature/build-provenance verification — see [`docs/image-signing.md`](image-signing.md)), and the stale pinned-digest warning (#736, closed) give an operator supply-chain evidence for the images a profile pins. | None identified; #736 closes the "known-outdated pinned digest" gap. |
| (e) | Security in acquisition/development/maintenance, incl. vulnerability handling and disclosure | `SECURITY.md`'s vulnerability-reporting channel and response targets, plus [`docs/security-support-policy.md`](security-support-policy.md)'s support-period/patch-delivery targets, document CDS's own secure-maintenance practice. The `cds security`/`cds test` rule-set is itself part of "security in acquisition" review for profiles built on CDS. | None identified beyond continuing #729. |
| (f) | Policies/procedures to assess the effectiveness of risk-management measures | `cds security --group-by-category`/`cds test --group-by-category` give a repeatable, versionable check an operator can re-run to assess whether their profile's measures still pass. | [`docs/audit-log.md`](audit-log.md)'s audit trail (#737, closed) adds a historical record an operator can use to demonstrate effectiveness over time. |
| (g) | Basic cyber hygiene practices and cybersecurity training | Out of scope for a compiler/CLI — this is an operator organisational practice, not a rendered-artifact property. | No CDS issue expected; contributor-facing hygiene (e.g. `CONTRIBUTING.md`, `SECURITY.md`) covers CDS's own project practice only. |
| (h) | Policies/procedures on cryptography and encryption | The `encryption-in-transit` and `secrets-management` rule-set categories (see [`docs/security-compliance-categories.md`](security-compliance-categories.md)) check that production endpoints use TLS and that secret material isn't mishandled. Image signing (cosign) in [`docs/image-signing.md`](image-signing.md) covers cryptographic supply-chain integrity. | None identified. |
| (i) | Human resources security, access control policies, asset management | The `access-control` rule-set category (default/missing credentials, weak/reused secrets) supports access-control policy checks. `CDS-SEC-081` (#774, see [`docs/security-rules.md`](security-rules.md)) flags an admin-facing service (e.g. Superset, Dagster's webserver) when no identity/auth module is present in the profile at all. The SBOM/release inventory (#730) supports asset management for the images a profile depends on. | Human-resources security itself is out of CDS's scope (organisational, not a rendered-artifact property). `CDS-SEC-081` is a coarse profile-wide presence check, not per-service wiring confirmation (no `identity-broker` contract exists yet, tracked by #370). |
| (j) | MFA/continuous authentication, secured communications | The `identity/keycloak` module (`modules/identity/keycloak`) can provide MFA-capable authentication to profiles that include it; this is opt-in per profile, not a default. `CDS-SEC-081` (#774) now flags when an admin-facing service is present without it. Secured voice/video/text/emergency communications are entirely out of CDS's scope. | No CDS-wide gap beyond `CDS-SEC-081`'s coarseness (above): MFA support exists as an available module, not a default. Cross-reference #772 (Common Ground/Haven reference profile), which may adopt it by default in a reference profile. |

## Cross-reference to the CRA milestone

This document does not duplicate the CRA milestone's work. In particular:

- CDS's own accountable role and scope determination under the CRA is
  recorded in [`docs/cra-scope-decision.md`](cra-scope-decision.md), not
  here.
- CDS's own product SBOM, support-period policy, and incident runbook
  (#730, #731, #729) are CRA manufacturer-facing obligations if/when CDS
  becomes in-scope; they are cited above only as evidence an *operator*
  can reuse, not restated as CDS's own NIS2 obligations (CDS has none).
- #774 (extending `cds security`'s rule-set with NIS2-aligned checks) is
  the follow-up implementation issue for the gaps identified in rows (a)
  and (f) above; it is gated on this scoping document per the "Dutch
  Public-Sector Data & Security Alignment" milestone sequencing (see
  [`docs/plan/cra-and-nl-law-sequencing.md`](plan/cra-and-nl-law-sequencing.md)).

## Ownership and review cadence

- **Owner:** repository maintainer (Ronald Hensbergen).
- **Review trigger:** whenever the Cyberbeveiligingswet's implementing
  text is finalized or materially amended, or whenever the CRA scope
  decision above changes (since the two determinations must stay
  distinguishable).

## References

- Directive (EU) 2022/2555 (NIS2), Article 21(2) (risk-management measure
  categories) and Article 23 (incident-reporting timelines).
- <https://eur-lex.europa.eu/eli/dir/2022/2555/oj/eng>
- <https://www.digitaleoverheid.nl/overzicht-van-alle-onderwerpen/data/interbestuurlijke-datastrategie/>
- [`docs/cra-scope-decision.md`](cra-scope-decision.md) — CDS's own CRA
  scope determination (distinct regime, distinct role).
- [`docs/security-compliance-categories.md`](security-compliance-categories.md) —
  the `cds security`/`cds test` compliance-category taxonomy referenced
  throughout the table above.
