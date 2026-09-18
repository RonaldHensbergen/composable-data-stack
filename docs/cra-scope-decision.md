# CDS Cyber Resilience Act (CRA) Scope Decision

Status: Draft decision record — readiness assessment, not legal advice
Decision date: 2026-09-18
Reviewer: Ronald Hensbergen (repository owner and sole maintainer)
Next scheduled review: 2027-09-18, or immediately upon any reassessment
trigger listed below, whichever comes first
Related: milestone "Cyber Resilience Act readiness"
([#728](https://github.com/RonaldHensbergen/composable-data-stack/issues/728)-[#733](https://github.com/RonaldHensbergen/composable-data-stack/issues/733))

## Purpose and disclaimer

This record documents, at a point in time, whether the CDS project's current
distribution and governance model places it inside the economic-operator
obligations of Regulation (EU) 2024/2847 (the Cyber Resilience Act, "CRA").
It is compliance readiness, not legal advice, and it does not itself
establish or waive any legal obligation. It states facts and assumptions
separately, records open questions, and defines the conditions under which
this assessment must be redone.

**This record does not claim, and must not be cited as evidence of, CRA
conformity, an EU declaration of conformity, or CE marking.** No such claim
exists for CDS today, and none may be added to this document, to release
artifacts, or to any other project material without a completed
conformity-assessment process following a future in-scope determination
(see issue #733).

## Facts as of the decision date

- **License and price:** CDS is distributed under the Apache License 2.0
  (`LICENSE`). There is no purchase price, paid tier, or paid support
  contract.
- **Distribution channels:** source and releases on GitHub
  (`RonaldHensbergen/composable-data-stack`), the Python package on PyPI
  (`.github/workflows/pypi.yml`, `.github/workflows/testpypi.yml`), and
  runtime container images published to GHCR
  (`ghcr.io/ronaldhensbergen/cds-*`) and Docker Hub
  (`docker.io/ronaldsoeverein/*`), as described in `docs/image-signing.md`.
- **Governance:** a single natural person (Ronald Hensbergen) is the
  repository owner, PyPI project owner, and controller of the GHCR/Docker
  Hub publishing credentials. There is no separate legal entity, foundation,
  or open-source software steward distinct from that individual.
- **Monetisation:** none identified at this date — no paid distribution, no
  disclosed commercial sponsorship, and no known bundling or incorporation
  agreement with a third party.
- **Intended use:** a general-purpose compiler/CLI that validates and
  composes declarative data-platform profiles into Docker Compose (and
  Kubernetes) artifacts. It is not marketed as a security-management or
  infrastructure-control product.
- **Maturity:** pre-1.0 (`pyproject.toml` version `0.9.1`,
  `Development Status :: 3 - Alpha`).

## Assumptions

- The project's distribution remains free of charge and free of a
  commercial sponsor for the period this decision covers.
- No third party currently has a commercial redistribution agreement with
  the maintainer that would make the maintainer a manufacturer on another
  undertaking's behalf.
- Publishing through GitHub, PyPI, GHCR, and Docker Hub is, on its own,
  distribution infrastructure rather than evidence of commercial activity
  (consistent with CRA recital guidance, but not independently confirmed by
  legal counsel).

## Analysis against CRA roles

- **Manufacturer (Article 3(13)):** a natural or legal person who develops,
  manufactures, or has a product with digital elements developed or
  manufactured, and markets it under its own name or trademark. An
  individual maintainer can be a manufacturer if a product is in scope.
- **Open-source software steward (Article 3(14)):** must be a *legal
  person* that establishes a sustained engagement to support the
  development of specific software intended for commercial activities.
  There is no such legal person for CDS today.
- **Non-commercial FOSS (Article 2(12), recitals 18-20):** software
  developed or supplied outside the course of a commercial activity is
  outside the CRA's economic-operator obligations. Whether that applies
  turns on the facts above, not on the mere existence of GitHub/PyPI/GHCR
  distribution.

## Current scope determination

As of the decision date above, and based on the facts and assumptions
recorded here, CDS is assessed as **non-commercial free and open-source
software under Article 2(12)**, and therefore currently **outside the
CRA's economic-operator obligations** (manufacturer, importer, or
distributor duties, including conformity assessment and CE marking). No
manufacturer or open-source-software-steward role currently applies to the
maintainer or the project.

This determination does not exempt the project from the readiness work
already tracked under the "Cyber Resilience Act readiness" milestone
(#728-#733); that work is undertaken proactively so the project is prepared
if scope changes, not because scope has been established today.

## Mandatory reassessment triggers

This decision must be revisited, and this document updated with a new
decision date and reviewer sign-off, immediately if any of the following
occurs:

- Introduction of a paid distribution channel, paid tier, or paid support
  offering.
- Commercial sponsorship of the project becoming known to the maintainer.
- Bundling or incorporation of CDS into a commercial offering, by the
  maintainer or, to the maintainer's knowledge, by a third party with the
  maintainer's involvement.
- Transfer of release ownership (GitHub, PyPI, GHCR, or Docker Hub
  credentials) to another individual or entity.
- Formal establishment of a legal entity or open-source software steward
  for the project.
- A material change to intended use, in particular any security-management
  or infrastructure-control functionality.

## Unresolved legal questions

- Whether unrelated third-party commercial redistribution or bundling,
  outside the maintainer's knowledge or control, could affect this
  determination.
- How a CRA support-period determination (Article 13(8)-(9)) would be made
  if scope changes.
- Which obligations would apply, and under what timeline, once a
  reassessment trigger occurs; this requires qualified legal advice at that
  time and is not resolved by this document.

## Ownership and review cadence

- **Owner:** repository maintainer (Ronald Hensbergen).
- **Cadence:** at least annually, and immediately upon any trigger listed
  above.
- **Next scheduled review:** 2027-09-18, or sooner if a trigger occurs
  first.

## References

- Regulation (EU) 2024/2847, Articles 2(12), 3(13), 3(14), 3(22), 3(23),
  and 24; recitals 18-20.
- <https://eur-lex.europa.eu/eli/reg/2024/2847/2024-11-20/eng>
- <https://digital-strategy.ec.europa.eu/en/policies/cra-open-source>
- <https://digital-strategy.ec.europa.eu/en/policies/cra-manufacturers>
