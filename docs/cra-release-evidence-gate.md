# CRA Release Evidence and Conformity Readiness Gate

Status: Living engineering record — proactive readiness, not a claim of CRA
scope or conformity
Owner: repository maintainer (Ronald Hensbergen)
Related: [`docs/cra-scope-decision.md`](cra-scope-decision.md),
[`docs/cra-risk-assessment.md`](cra-risk-assessment.md),
[`docs/cra-technical-documentation.md`](cra-technical-documentation.md),
[`docs/cra-incident-runbook.md`](cra-incident-runbook.md),
[`docs/security-support-policy.md`](security-support-policy.md),
[`RELEASE.md`](../RELEASE.md), milestone "Cyber Resilience Act readiness"
([#728](https://github.com/RonaldHensbergen/composable-data-stack/issues/728)-[#733](https://github.com/RonaldHensbergen/composable-data-stack/issues/733)),
covers issue
[#733](https://github.com/RonaldHensbergen/composable-data-stack/issues/733)
Last reviewed: 2026-10-07
Next scheduled review: 2027-10-07, or immediately upon a trigger in
[§8](#8-dependency-closure-and-review-cadence)

## Purpose and disclaimer

[`docs/cra-scope-decision.md`](cra-scope-decision.md) currently assesses CDS
as non-commercial free and open-source software, outside the Cyber
Resilience Act's (CRA, Regulation (EU) 2024/2847) economic-operator
obligations. Adding a CE mark or an EU declaration of conformity now would
be premature: scope, the responsible manufacturer, support commitments,
applicable harmonised standards, and the conformity-assessment route are
not yet settled.

This document is nonetheless built proactively, as a **living engineering
record** of:

- what evidence a future CRA conformity assessment would assemble, and
  where that evidence already exists today (§1);
- a documented, re-evaluable review of which CRA product category CDS
  would fall into if it ever becomes in-scope (§2, §3);
- draft-only templates for the user information and declarations a
  conformity assessment would eventually produce (§5);
- and a small, fail-closed, **disabled-by-default** mechanical gate that
  makes it structurally hard to ever attach a real conformity or
  CE-marking claim to a release without deliberately completing the
  evidence it checks for (§6).

**Nothing in this document, its templates, or its tooling is itself a
claim of CRA conformity, an EU declaration of conformity, or CE marking.**
No such claim exists for CDS today, and none may be added to this
document, the templates in §5, release artifacts, or any other project
material without a completed conformity-assessment process following a
future in-scope determination.

## 1. Release evidence manifest

Every CDS release already produces most of the evidence a conformity
assessment would need — it is simply scattered across existing documents
and CI artifacts rather than assembled into one place. The CRA release-
evidence manifest is a JSON document that references (never duplicates)
that evidence, built by
[`scripts/build_cra_release_evidence_manifest.py`](../scripts/build_cra_release_evidence_manifest.py)
and checked by
[`scripts/check_cra_release_gate.py`](../scripts/check_cra_release_gate.py)
(§6). Entries that point at repository files also carry a `url`
permalink pinned to the release commit (`blob/<sha>/<path>`), so the
evidence resolves to the exact state that was released. It references:

| Manifest field | Evidence it points to |
| --- | --- |
| `evidence.scopeDecision.ref` | [`docs/cra-scope-decision.md`](cra-scope-decision.md) |
| `evidence.riskAssessment.ref` | [`docs/cra-risk-assessment.md`](cra-risk-assessment.md) |
| `evidence.technicalDocumentation.ref` | [`docs/cra-technical-documentation.md`](cra-technical-documentation.md) |
| `evidence.sbom.ref` | `cli-sbom.cyclonedx.json` (per-release, see [`RELEASE.md`](../RELEASE.md#release-evidence)) |
| `evidence.releaseInventory.ref` | `release-inventory.json` (per-release, see [`RELEASE.md`](../RELEASE.md#release-evidence)) |
| `evidence.testResults.ref` | the CI run(s) for the release commit (full unit-test suite, Bandit, `pip-audit`; `.github/workflows/ci.yml`) |
| `evidence.vulnerabilityStatus.ref` | [`docs/image-scanning.md`](image-scanning.md) (daily/on-change rescans, not a static per-release snapshot) |
| `evidence.supportInformation.ref` | [`docs/security-support-policy.md`](security-support-policy.md) |
| `evidence.userInstructions.ref` | [`README.md`](../README.md) |
| `evidence.signaturesProvenance.ref` | [`docs/image-signing.md`](image-signing.md) (OIDC/PEP 740 for the CLI, cosign/SBOM/SLSA for images) |
| `classification` | §2 below |
| `conformityAssessmentRoute` | §3 below |
| `declarationOfConformity` | §4 below; `not-applicable` by default |
| `approvals` | empty by default; see §6 |
| `conformityClaimEnabled` | `false` by default; see §6 |

## 2. Product classification review

Annex III (important products) and Annex IV (critical products) list
specific product categories that, if met, would move CDS out of the
"default" category and require a different conformity-assessment route
(§3). This is a point-in-time review against the current product; see
[§8](#8-dependency-closure-and-review-cadence) for when it must be redone.

| Annex III/IV category | Does CDS implement this functionality? |
| --- | --- |
| Identity/access/privileged-access management software | No — CDS can *deploy* an identity module (e.g. Keycloak), but does not itself implement IAM; the deployed module, not CDS, would carry that classification if ever assessed separately. |
| Standalone/embedded browsers | No. |
| Password managers | No. |
| Malware search/removal/quarantine software | No. |
| VPN products | No — CDS can render a profile that includes a VPN/ingress module, but does not implement VPN functionality itself. |
| Network management systems, firewalls, intrusion detection/prevention | No — `rule-set.json`'s `network-exposure` category *flags* rendered network configuration; it is a static analysis of generated Compose/Helm output, not a runtime network-management or intrusion-detection system. |
| SIEM systems | No — the local audit trail ([`docs/audit-log.md`](audit-log.md)) is an append-only local log of CDS's own invocations, not a security information/event management product. |
| Boot managers, public key infrastructure/certificate issuance software | No. |
| Physical/virtual network interfaces, operating systems, routers/modems/switches | No — CDS targets these as deployment outputs (e.g. a rendered Compose network), it does not implement them. |
| Microprocessors/microcontrollers/ASICs/FPGAs with security functionality | No — CDS is a software-only CLI tool with no hardware component. |
| Smart-home devices, connected toys, wearables | No — out of CDS's product domain entirely. |
| Hypervisors, container runtime systems (Annex III Class II) | No — CDS orchestrates `docker compose`/Helm but does not implement a hypervisor or container runtime itself; `docker compose`/Kubernetes are third-party dependencies, not part of the CDS product. |
| Hardware security modules, secure elements, smart meter gateways (Annex IV) | No — no hardware component exists. |

**Classification: default category.** No Annex III or Annex IV category
describes CDS's own functionality as reviewed above. This conclusion is
consistent with
[`docs/cra-scope-decision.md`](cra-scope-decision.md)'s existing fact that
CDS's "intended use" is "a general-purpose compiler/CLI ... not marketed as
a security-management or infrastructure-control product."

**Re-evaluation conditions:** this classification must be redone if any of
the following occurs, in addition to the general triggers in
[§8](#8-dependency-closure-and-review-cadence):

- CDS gains a built-in identity/access-management, VPN, firewall,
  intrusion-detection, or SIEM *implementation* of its own (as distinct
  from rendering/deploying a third-party module that provides one).
- CDS begins shipping or bundling a hardware component.
- CDS's `rule-set.json` security engine evolves from static
  configuration analysis into a runtime network-management or
  intrusion-detection/prevention function.

## 3. Conformity assessment route

Given the "default category" classification in §2, the applicable route if
CDS ever becomes CRA in-scope is **Annex VIII Part I — conformity
assessment based on internal control (Module A)**:

- The manufacturer alone assesses conformity; no notified body
  involvement is required.
- The manufacturer must draw up technical documentation per Annex VII
  (see [`docs/cra-technical-documentation.md`](cra-technical-documentation.md),
  already maintained as a living index against this exact requirement).
- The manufacturer must take all necessary measures in the product's
  design, development, production, and vulnerability-handling processes
  to meet Annex I's essential requirements (see
  [`docs/cra-risk-assessment.md`](cra-risk-assessment.md), which already
  tracks this item by item).
- An EU declaration of conformity must be drawn up (Annex V; §5 below) and
  the CE marking affixed, only once conformity is actually established.

**Escalation conditions requiring a different route** (a notified body or
third-party assessment under a different Annex VIII part): the product
classification in §2 changes to "important" (Annex III) or "critical"
(Annex IV). Which route then applies depends on the specific category
(Annex III Class I vs. Class II, or Annex IV) and on the conditions in
Article 32 of the CRA (for example, whether harmonised standards have been
applied, and the provisions for free and open-source software whose
technical documentation is made public). This document does not
pre-decide that outcome; it must be re-assessed against Article 32 and the
specific category triggering the change.

## 4. CE marking and declaration of conformity

Software cannot carry a physical CE mark. A digital/electronic CE marking
would accompany the product's packaging/distribution metadata (e.g. PyPI
project metadata, a GitHub Release asset, or equivalent) only once all of
the following are true:

1. The classification in §2 is current and accurate.
2. A conformity assessment under the route in §3 has actually been
   performed and passed.
3. A real, signed EU declaration of conformity (Annex V) — or, where
   permitted alongside the full declaration, a simplified declaration
   (Annex VI) — has been drawn up, following the format in
   [`docs/cra-annex-v-eu-declaration-of-conformity-template.md`](cra-annex-v-eu-declaration-of-conformity-template.md)
   and
   [`docs/cra-annex-vi-simplified-declaration-template.md`](cra-annex-vi-simplified-declaration-template.md).
4. User information per Annex II has been assembled, following
   [`docs/cra-annex-ii-user-information-template.md`](cra-annex-ii-user-information-template.md).

None of these four conditions is true today. No CE marking, digital or
otherwise, is used anywhere in CDS's distribution.

## 5. Templates

These templates exist so that, if CDS ever becomes CRA in-scope, drafting
the real documents is an assembly exercise against existing evidence, not a
from-scratch effort. **Every template is prominently marked DRAFT /
NOT VALID and must stay that way** until a real conformity-assessment
process removes that marking:

- [`docs/cra-annex-ii-user-information-template.md`](cra-annex-ii-user-information-template.md)
  — Annex II information and instructions to the user.
- [`docs/cra-annex-v-eu-declaration-of-conformity-template.md`](cra-annex-v-eu-declaration-of-conformity-template.md)
  — Annex V EU declaration of conformity.
- [`docs/cra-annex-vi-simplified-declaration-template.md`](cra-annex-vi-simplified-declaration-template.md)
  — Annex VI simplified EU declaration of conformity.

## 6. Release gate

[`scripts/build_cra_release_evidence_manifest.py`](../scripts/build_cra_release_evidence_manifest.py)
assembles the manifest described in §1 for a given release, pointing at
the evidence that already exists. It always sets `conformityClaimEnabled`
to `false` and leaves `approvals` empty — no automation in this
repository ever changes either field.

[`scripts/check_cra_release_gate.py`](../scripts/check_cra_release_gate.py)
reads a manifest and enforces exactly one rule, mechanically:

- If `conformityClaimEnabled` is `false` or absent (the default for every
  manifest this repository generates), the gate is a **no-op**: it prints
  that no conformity claim is made and exits `0`. It can never fail a
  build or block a release in this state.
- If `conformityClaimEnabled` is `true` — which requires a human to
  deliberately hand-edit a specific manifest file after a real
  conformity-assessment process — the gate **fails closed**: it requires
  every evidence reference in §1's table to be present and non-empty, the
  classification (§2) and conformity-assessment route (§3) to be filled
  in, the EU declaration of conformity status to be exactly `"approved"`
  with a reference, and at least one named approval
  (`name`/`role`/ISO `date`). Repository-file references must resolve to an
  existing file inside the repository, and neither they nor the declaration
  reference may still carry a `DRAFT — NOT VALID` marking. Missing any of
  these fails the check (exit `1`) and lists exactly what's missing. An
  unreadable, invalid, or non-object manifest also fails with a clear
  error rather than a traceback.

This mechanism satisfies the acceptance criterion that the gate "remains
disabled for legal claims by default and cannot accidentally emit a CE
mark or declaration": there is no code path, flag, or CI condition in this
repository that sets `conformityClaimEnabled` to `true` automatically.

`tests/test_build_cra_release_evidence_manifest.py` and
`tests/test_check_cra_release_gate.py` cover both scripts, including that
an incomplete-but-enabled manifest fails with a specific, itemized list of
missing fields.

## 7. Retention

Per Article 13(13), the EU declaration of conformity and the technical
documentation must be kept at the disposal of market surveillance
authorities for **at least 10 years after the product with digital
elements was placed on the market, or for the support period (whichever is
longer)**. Per Article 13(18), the Annex II information and instructions to
the user are subject to the same retention period and must remain
accessible to users and authorities (online, if provided online).

This document, [`docs/cra-technical-documentation.md`](cra-technical-documentation.md),
and the templates in §5 are stored in git and retained the same way as
every other repository document: permanently, versioned alongside the code
they describe. A generated release-evidence manifest (§1) is attached as a
GitHub Release asset alongside the SBOM and release inventory (see
[`RELEASE.md`](../RELEASE.md#release-evidence)) — GitHub Releases are the
durable retention mechanism already established by
[`docs/security-support-policy.md`](security-support-policy.md#5-durable-retention)
for exactly this reason: tagged refs and release pages do not expire,
unlike short-retention CI artifacts.

## 8. Dependency closure and review cadence

Per this issue's acceptance criteria, #733 cannot close until all of the
following are complete (as of this writing, all are):

| Issue | Topic | Status |
| --- | --- | --- |
| [#728](https://github.com/RonaldHensbergen/composable-data-stack/issues/728) | Scope decision | Resolved by #738 |
| [#729](https://github.com/RonaldHensbergen/composable-data-stack/issues/729) | Vulnerability/incident reporting runbook | Resolved by #755 |
| [#730](https://github.com/RonaldHensbergen/composable-data-stack/issues/730) | Product SBOM | Resolved by #740 |
| [#731](https://github.com/RonaldHensbergen/composable-data-stack/issues/731) | Support/update policy | Resolved by #742 |
| [#732](https://github.com/RonaldHensbergen/composable-data-stack/issues/732) | Risk assessment/technical documentation | Resolved by #842 |

This document (and the rest of #733's scope) must also be revisited
immediately upon:

- Any trigger in
  [`docs/cra-scope-decision.md`](cra-scope-decision.md#mandatory-reassessment-triggers).
- A change to the classification in [§2](#2-product-classification-review).
- A change to any of the evidence documents referenced in §1's table in a
  way that changes what they cover (e.g. a new document replacing
  `docs/security-support-policy.md`).
- At least annually.

## Ownership and review cadence

- **Owner:** repository maintainer (Ronald Hensbergen).
- **Cadence:** at least annually, and immediately upon any trigger in
  [§8](#8-dependency-closure-and-review-cadence).
- **Next scheduled review:** 2027-10-07, or sooner if a trigger occurs
  first.

## References

- Regulation (EU) 2024/2847, Articles 13(12)-(20), 28-32; Annexes II, V,
  VI, VII, and VIII.
- <https://digital-strategy.ec.europa.eu/en/policies/cra-conformity-assessment>
- [`docs/cra-scope-decision.md`](cra-scope-decision.md),
  [`docs/cra-risk-assessment.md`](cra-risk-assessment.md),
  [`docs/cra-technical-documentation.md`](cra-technical-documentation.md),
  [`docs/security-support-policy.md`](security-support-policy.md),
  [`RELEASE.md`](../RELEASE.md).
