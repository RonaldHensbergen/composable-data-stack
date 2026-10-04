# Federatief Datastelsel (FDS) Data-Provider Alignment

Status: Scoping / gap-analysis record — not legal advice, not a compliance
or conformity claim
Related: [milestone "Dutch Public-Sector Data & Security
Alignment"](https://github.com/RonaldHensbergen/composable-data-stack/milestone/6)
(#773); sibling scoping docs:
[`docs/nis2-cyberbeveiligingswet-scope.md`](nis2-cyberbeveiligingswet-scope.md),
[`docs/cra-scope-decision.md`](cra-scope-decision.md)

## Purpose and disclaimer

The Interbestuurlijke Datastrategie (IBDS)'s Federatief Datastelsel (FDS) is
an *afsprakenstelsel* (agreement framework) for Dutch organisations with a
public task, standardising how data is described and disclosed across
domains. The Overheidsbreed Beleidsoverleg Digitale Overheid (OBDO) formally
adopted the FDS Afsprakenstelsel's **data-provider (data-aanbieder)**
basisafspraken in February 2026. Data-consumer (afnemer) basisafspraken are
expected later in 2026 and are **not yet published** at the time of writing
— they are explicitly out of scope for this document and should be revisited
once available.

**This document makes no compliance or conformity claim.** It does not state
that any organisation using CDS, or CDS itself, satisfies FDS obligations,
and it must not be cited as evidence of conformity. It is a scoping and
gap-analysis aid only, in the same spirit as
[`docs/nis2-cyberbeveiligingswet-scope.md`](nis2-cyberbeveiligingswet-scope.md)
and [`docs/cra-scope-decision.md`](cra-scope-decision.md). Consult your own
legal/compliance function and the authoritative Afsprakenstelsel FDS text
before claiming FDS participation.

**A note on sources.** The authoritative basisafspraken text lives in the
Afsprakenstelsel FDS wiki on
[realisatieibds.nl](https://realisatieibds.nl/groups/view/0056c9ef-5c2e-44f9-a998-e735f1e9ccaa/federatief-datastelsel/wiki/view/d0d0c6b6-b1bd-41a4-962a-b1e628c1ee82/afsprakenstelsel-fds),
which requires a registered account to browse in full. This mapping is based
on the publicly available description of the Afsprakenstelsel's structure
and scope from [digitaleoverheid.nl's IBDS
overview](https://www.digitaleoverheid.nl/overzicht-van-alle-onderwerpen/data/interbestuurlijke-datastrategie/)
and [OBDO's formal-adoption
announcement](https://www.digitaleoverheid.nl/nieuws/obdo-stelt-afsprakenstelsel-federatief-datastelsel-vast/).
Readers implementing against FDS should verify specific basisafspraak
wording and numbering directly against the realisatieibds.nl wiki rather
than relying solely on this document.

## Who is actually a "data-aanbieder" here

CDS itself is a compiler/CLI — it renders a `profile.yaml` into a runnable
stack, it does not operate one. The organisation that deploys a CDS-rendered
stack and exposes a dataset or data service to other public-task
organisations is the one that would actually participate in the FDS as a
data-aanbieder, in the same way
[`docs/nis2-cyberbeveiligingswet-scope.md`](nis2-cyberbeveiligingswet-scope.md)
distinguishes CDS-the-product from an operator's own regulatory role. This
document therefore maps what evidence/mechanism CDS's compile-time contract
model (`shared/contracts/*.yaml`, module `provides`/`consumes`, profile
`contractRef` binding) could already give such an operator, and where it
falls short.

## The Afsprakenstelsel FDS's four agreement domains

Per the public OBDO/IBDS description, the Afsprakenstelsel FDS's
data-provider basisafspraken span four domains: **technical**, **semantic**,
**legal**, and **organisational** agreements, together forming a shared
trust framework for uniform, domain-crossing data description and
disclosure without centralising the data itself.

## Mapping against CDS's existing contract model

| FDS domain | What it asks of a data-aanbieder (per the public summary) | CDS support today | Gap |
| --- | --- | --- | --- |
| Technical | Disclose data via dataservices conforming to FDS's technical interoperability standards. | `shared/contracts/*.yaml` (e.g. `sql-database`, `cache-service`, `file-database`, `log-sink`) already define machine-checkable, versioned field-level interface contracts between modules, resolved at plan time via `provides.contracts`/`consumes`/profile `contractRef` binding (see `cli/validator.py`/`cli/planner.py`). This is the same category of mechanism FDS asks for — uniform, checkable service interfaces — just scoped today to inter-module wiring inside one profile, not to an external FDS dataservice endpoint. | No existing contract expresses an *externally disclosed* dataservice endpoint shape (e.g. an OGC API/REST dataservice per FDS's technical standards) as opposed to CDS's current internal service-to-service contracts. |
| Semantic | Describe datasets and dataservices uniformly so they are findable across domains (standardised metadata vocabulary). | `module.yaml`'s `metadata.description`/`metadata.displayName` give human-readable prose for the *module catalog*, not a structured, profile-emitted dataset description. Nothing in the contract model captures dataset-level semantic metadata (title, theme/classification, standard identifier). | **Gap.** No shared contract captures FDS/DCAT-style dataset metadata (title, description, theme/classification, standard identifier scheme) in a profile-portable, machine-checkable way, distinct from existing service-connection contracts. |
| Legal | Make access conditions (and, implicitly, licensing) for the disclosed data clear and consistent with privacy/security law. | The existing security rule-set (`cds security`/`cds test`), the NIS2/CRA scoping docs, and the secrets model (`secrets.<alias>` → `${CDS_*}`, never resolved into plans/rendered output) already give a baseline security/privacy evidence story an operator can point to. | **Gap.** No structured "access conditions"/license field exists anywhere in the contract or module config model; these terms, if declared at all today, would live outside CDS entirely (e.g. in an operator's own documentation). |
| Organisational | Be accountable and controllable/auditable for the publication process and the data's quality. | `cds report` (#734, `docs/compliance-report.md`) aggregates module/image/topology/secret-leak evidence for a rendered stack, and the new local audit trail (#737, `docs/audit-log.md`) records a durable history of `validate`/`render`/`up`/`test` invocations. Both give an operator auditable evidence of what was rendered and deployed, and when. | Partially met. Neither mechanism lets a profile *declare* that it participates in the FDS as a specific data-aanbieder, or links its evidence to an FDS registration — there is no "this is an FDS-disclosed dataset" marker today. |

## Conclusion

The **technical** and **organisational** domains are reasonably well served
by CDS's existing contract/report/audit mechanisms, even though none of them
were designed with FDS specifically in mind. The **semantic** and **legal**
domains have a concrete gap: CDS has no shared contract that expresses
FDS-required *dataset-level* provider metadata (description, classification,
access conditions/license) — only service-connection contracts
(`sql-database`, `cache-service`, etc.) exist today, and those describe how
to *connect* to a backing service, not how to *describe and disclose* a
dataset to the outside world.

Per this issue's acceptance criteria, that gap is **not** solved inline
here. It is filed as a separate, scoped follow-up:

- **#825** — add a shared `open-data-provider` (or `data-catalog`) contract
  expressing FDS-required provider metadata (dataset description,
  classification, access conditions) in a profile-portable way.

## Deliberately out of scope

- **Data-consumer (afnemer) basisafspraken** — not yet published; revisit
  this document once they are.
- **Any FDS conformity/registration claim** — participating in the FDS as a
  registered data-aanbieder is an organisational/legal step outside CDS's
  scope as a compiler; this document only maps evidence CDS could help
  produce.

## References

- <https://federatief.datastelsel.nl/>
- <https://www.digitaleoverheid.nl/overzicht-van-alle-onderwerpen/data/interbestuurlijke-datastrategie/>
- <https://www.digitaleoverheid.nl/nieuws/obdo-stelt-afsprakenstelsel-federatief-datastelsel-vast/>
- <https://realisatieibds.nl/groups/view/0056c9ef-5c2e-44f9-a998-e735f1e9ccaa/federatief-datastelsel/wiki/view/d0d0c6b6-b1bd-41a4-962a-b1e628c1ee82/afsprakenstelsel-fds>
  (registration required for full basisafspraken text)
