# CRA Annex II Information-and-Instructions-to-the-User Template

> **DRAFT TEMPLATE — NOT VALID.** This is not user documentation, not legal
> advice, and not evidence of CRA conformity. CDS is currently assessed as
> **outside** the Cyber Resilience Act's economic-operator obligations (see
> [`docs/cra-scope-decision.md`](cra-scope-decision.md)). This template
> exists so that, if that scope determination ever changes, drafting the
> real Annex II user information is an assembly exercise against existing
> CDS documentation, not a from-scratch effort. It must not be copied into
> `README.md` or any other user-facing document, distributed to users, or
> referenced as if it were a completed declaration, until a real
> conformity-assessment process (see
> [`docs/cra-release-evidence-gate.md`](cra-release-evidence-gate.md))
> has actually been completed and this notice has been removed by
> whoever completes that process.

Covers issue [#733](https://github.com/RonaldHensbergen/composable-data-stack/issues/733).
Structure follows Regulation (EU) 2024/2847, Annex II.

---

## 1. Manufacturer identification

- **Name / registered trade name or mark:** [fill in]
- **Postal address:** [fill in]
- **Email / other digital contact:** [fill in]
- **Website:** [fill in]

## 2. Single point of contact for vulnerability reporting

- **Contact:** see [`SECURITY.md`](../SECURITY.md) for the current
  coordinated vulnerability disclosure (CVD) intake channel.
- **CVD policy location:** [`SECURITY.md`](../SECURITY.md).

## 3. Product identification

- **Name / type:** `composable-data-stack` (CLI command: `cds`).
- **Additional identification:** PyPI package name, version (per
  `pyproject.toml`), and source commit — see
  `release-inventory.json` attached to each GitHub Release (see
  [`RELEASE.md`](../RELEASE.md#release-evidence)).

## 4. Intended purpose and security environment

- **Intended purpose:** see [`docs/architecture.md`](architecture.md)
  ("Intended outcomes") and [`README.md`](../README.md).
- **Security environment provided:** see
  [`docs/cra-risk-assessment.md`](cra-risk-assessment.md) §1-§3 for the
  product-lifecycle controls already in place, and
  [`docs/threat-model.md`](threat-model.md) for a rendered deployment's
  own security environment (a distinct, downstream concern from this
  product's own security properties).
- **Essential functionalities and security properties:** see
  [`docs/architecture.md`](architecture.md) and
  [`docs/cra-risk-assessment.md`](cra-risk-assessment.md) §2-§3 (Annex I
  traceability).

## 5. Known or foreseeable misuse risks

- See [`docs/cra-risk-assessment.md`](cra-risk-assessment.md) §1
  (product-lifecycle risk register) for risks arising from untrusted
  profile/module YAML, generated-artifact handling, and dependency
  compromise.

## 6. EU declaration of conformity

- **Status:** not applicable — no EU declaration of conformity exists
  (see [`docs/cra-scope-decision.md`](cra-scope-decision.md)).
- **Internet address (once applicable):** [fill in].

## 7. Technical security support

- **Type of support offered:** see
  [`docs/security-support-policy.md`](security-support-policy.md) §1-§3.
- **End date for vulnerability handling/updates:** see
  [`docs/security-support-policy.md`](security-support-policy.md) §2
  (currently rolling/superseded-based, not a fixed calendar date).

## 8. Detailed security instructions

- **Secure use from commissioning to end-of-life:** see
  [`docs/installation.md`](installation.md),
  [`docs/cli-reference.md`](cli-reference.md).
- **How changes affect data security:** see
  [`AGENT.md`](../AGENT.md) (`${CDS_*}` secret-placeholder invariant) and
  [`docs/compliance-report.md`](compliance-report.md) (`secretLeakCheck`).
- **Installing security-relevant updates:** see
  [`docs/security-support-policy.md`](security-support-policy.md) §3.
- **Secure decommissioning / removal of user data:** CDS holds no
  remote/managed user data; see
  [`docs/cra-risk-assessment.md`](cra-risk-assessment.md) (Annex I item
  2(m)) and [`docs/audit-log.md`](audit-log.md) for the local audit file a
  user can delete directly.
- **Disabling automatic security updates:** not applicable — CDS performs
  no automatic updates of itself (see
  [`docs/cra-risk-assessment.md`](cra-risk-assessment.md), Annex I item
  2(c), PLR-10, tracked by
  [#841](https://github.com/RonaldHensbergen/composable-data-stack/issues/841)).
- **Information for integrators:** see
  [`docs/modules.md`](modules.md),
  [`docs/architecture.md`](architecture.md) (responsibility boundaries).

## 9. Software bill of materials

- **Availability:** see
  [`RELEASE.md`](../RELEASE.md#release-evidence) (`cli-sbom.cyclonedx.json`
  attached to every GitHub Release) and
  [`docs/image-scanning.md`](image-scanning.md) (runtime-image SBOMs).

## 10. Retention

Per Article 13(18), this information and these instructions must be kept
at the disposal of users and market surveillance authorities for at least
10 years after the product is placed on the market or for the support
period, whichever is longer, and, where provided online, remain accessible
and available online for that period.

---

## References

- Regulation (EU) 2024/2847, Annex II, Article 13(18).
- [`docs/cra-release-evidence-gate.md`](cra-release-evidence-gate.md),
  [`docs/cra-scope-decision.md`](cra-scope-decision.md).
