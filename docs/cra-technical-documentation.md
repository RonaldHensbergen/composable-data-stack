# CRA Annex VII Technical-Documentation Index

Status: Living engineering record — proactive readiness, not a claim of CRA
scope or conformity
Owner: repository maintainer (Ronald Hensbergen)
Related: [`docs/cra-scope-decision.md`](cra-scope-decision.md),
[`docs/cra-risk-assessment.md`](cra-risk-assessment.md),
[`docs/threat-model.md`](threat-model.md),
[`docs/cra-incident-runbook.md`](cra-incident-runbook.md),
[`docs/security-support-policy.md`](security-support-policy.md),
milestone "Cyber Resilience Act readiness"
([#728](https://github.com/RonaldHensbergen/composable-data-stack/issues/728)-[#733](https://github.com/RonaldHensbergen/composable-data-stack/issues/733)),
covers issue
[#732](https://github.com/RonaldHensbergen/composable-data-stack/issues/732)
Last reviewed: 2026-10-07
Next scheduled review: 2027-10-07, or immediately upon a trigger in
[Review triggers](#review-triggers)

## Purpose and disclaimer

[`docs/cra-scope-decision.md`](cra-scope-decision.md) currently assesses CDS
as non-commercial free and open-source software, outside the Cyber
Resilience Act's (CRA, Regulation (EU) 2024/2847) economic-operator
obligations. This document is nonetheless built proactively: it is an
**index**, not a new standalone dossier. For each of Annex VII's nine
required technical-documentation points it names the existing CDS artifact
(or artifacts) that would serve as that evidence, so that producing a
genuine Annex VII technical-documentation file — if CDS's scope
determination ever changes — is an assembly exercise, not a from-scratch
effort. It does not itself establish or waive any legal obligation, and it
is not a claim of CRA conformity or a substitute for a future formal
technical-documentation file or EU declaration of conformity.

CDS is distributed as two distinct kinds of artifact, each covered below
where the evidence differs:

- **The `cds` CLI** — published to PyPI (`pip`/`pipx install cds`), source
  in this repository.
- **CDS-published container images** — `cds-dagster`, `cds-superset`, and
  other images published to GHCR and Docker Hub by
  [`.github/workflows/publish-images.yml`](../.github/workflows/publish-images.yml).

## Annex VII index

| # | Annex VII requirement | CDS artifact(s) | Notes |
| --- | --- | --- | --- |
| 1 | General description of the product, including its intended purpose, versions, and information needed to identify the product and its essential-requirements compliance | [`docs/architecture.md`](architecture.md) (intended purpose, architectural layers, responsibility boundaries); [`README.md`](../README.md) (what CDS is/isn't); [`CHANGELOG.md`](../CHANGELOG.md) and [GitHub Releases](https://github.com/RonaldHensbergen/composable-data-stack/releases) (versions); [`docs/cli-reference.md`](cli-reference.md) (CLI usage/identification) | Covers the CLI. CDS-published images are identified by immutable digest and tag per [`docs/image-signing.md`](image-signing.md#registry-and-naming) |
| 2 | Description of the design, development, and production of the product and vulnerability-handling processes, incl. SBOM, CVD policy, contact address, and technical solutions for secure distribution of updates | [`docs/architecture.md`](architecture.md) (design); [`CONTRIBUTING.md`](../CONTRIBUTING.md) and `.github/workflows/ci.yml` (development/production — CI, lint, tests, Bandit, pip-audit); [`SECURITY.md`](../SECURITY.md) (CVD policy and contact); [`docs/compliance-report.md`](compliance-report.md) (SBOM/evidence export via `cds report`); [`docs/image-signing.md`](image-signing.md) and `.github/workflows/pypi.yml` (OIDC trusted publishing, PEP 740 attestations) / `.github/workflows/publish-images.yml` (cosign keyless signing) for secure update distribution | Image SBOM is produced per [`docs/image-scanning.md`](image-scanning.md#pipeline); CLI dependency manifest is `pyproject.toml` plus `pip-audit`-scanned lockfile artifacts in CI |
| 3 | Cybersecurity risk assessment referred to in Article 13(2) | [`docs/cra-risk-assessment.md`](cra-risk-assessment.md) (product-lifecycle risk register and Annex I traceability matrix); [`docs/threat-model.md`](threat-model.md) (deployed-stack threat model — complementary, different scope) | Primary artifact for this point |
| 4 | Information needed to determine the support period under Article 13(8) | [`docs/security-support-policy.md`](security-support-policy.md) (§2 "How the support duration was determined") | — |
| 5 | List of harmonised standards, common specifications, or cybersecurity certification schemes applied, or a description of the solutions adopted to meet the essential requirements if not fully applied | None formally adopted or certified against at this time | No harmonised standard/common specification/certification scheme is currently claimed; Annex I conformity is instead evidenced item-by-item in [`docs/cra-risk-assessment.md`](cra-risk-assessment.md) §2/§3 |
| 6 | Reports of the tests carried out to verify the product and vulnerability-handling conformity | CI test runs (`.github/workflows/ci.yml`: unit tests, Bandit, pip-audit); [`docs/image-scanning.md`](image-scanning.md) (scan results, remediation SLA); [`docs/cra-incident-runbook.md`](cra-incident-runbook.md) (tabletop-exercise record) | Individual CI run logs are retained per GitHub Actions' standard log-retention window, not indefinitely; see [`docs/security-support-policy.md`](security-support-policy.md) §5 for the durable-retention artifacts (release notes, advisories, signed-image provenance) that substitute for raw logs beyond that window |
| 7 | Copy of the EU declaration of conformity referred to in Article 28 | Not applicable | CDS is currently assessed as outside CRA economic-operator obligations per [`docs/cra-scope-decision.md`](cra-scope-decision.md); no EU declaration of conformity exists or is claimed |
| 8 | Software bill of materials, at the request of a market surveillance authority, per Article 13(24) | [`docs/compliance-report.md`](compliance-report.md) (`cds report --format json`, machine-readable evidence export); image SBOM per [`docs/image-scanning.md`](image-scanning.md#pipeline) | SBOM generation already exists for both artifact kinds; this point is "if applicable" under Annex VII and would be satisfied on request |
| 9 | Other information, reports, or certificates relevant for conformity assessment | [`docs/security-compliance-categories.md`](security-compliance-categories.md) (how CDS's rule-based security checks map to compliance frameworks); [`docs/audit-log.md`](audit-log.md) (audit trail of plan/render decisions); [`docs/nis2-cyberbeveiligingswet-scope.md`](nis2-cyberbeveiligingswet-scope.md) (related NL/NIS2 scope note) | — |

## Review triggers

Review and update this index when any of the following occurs, consistent
with [`docs/cra-risk-assessment.md`](cra-risk-assessment.md#4-review-triggers):

- CDS's CRA scope determination in [`docs/cra-scope-decision.md`](cra-scope-decision.md)
  changes (e.g., a commercial offering or distribution model emerges).
- A new document is created, or an existing one materially restructured,
  that would change which artifact answers one of the nine Annex VII
  points above.
- The CLI's or a published image's signing/attestation/SBOM mechanism
  changes (new workflow, new tool, new registry).
- At least annually, alongside the review of
  [`docs/cra-risk-assessment.md`](cra-risk-assessment.md).

## References

- Regulation (EU) 2024/2847, Annex VII.
- [`docs/cra-scope-decision.md`](cra-scope-decision.md),
  [`docs/cra-risk-assessment.md`](cra-risk-assessment.md),
  [`docs/threat-model.md`](threat-model.md),
  [`docs/architecture.md`](architecture.md),
  [`docs/compliance-report.md`](compliance-report.md),
  [`docs/image-signing.md`](image-signing.md),
  [`docs/image-scanning.md`](image-scanning.md),
  [`docs/security-support-policy.md`](security-support-policy.md),
  [`docs/security-compliance-categories.md`](security-compliance-categories.md),
  [`docs/audit-log.md`](audit-log.md).
