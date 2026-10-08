# CRA and Related-Law Issue Sequencing

Combined ordering for all issues in milestone 5 (**Cyber Resilience Act
readiness**) and milestone 6 (**Dutch Public-Sector Data & Security
Alignment**), by importance/dependency order, not by issue number.

|Order|Milestone|Issue|Effort/Description|Status|
|---|---|---|---|---|
|1|5 - CRA|#728|~~Documentation only~~|Resolved by #738|
|2|6 - NL|#771|~~Cyberbeveiligingswet/NIS2 scope — the national-law counterpart to #728; states CDS is a software supplier, not a regulated entity, and cross-references milestone 5 so the two aren't conflated. Gates #774.~~|Resolved by #780|
|3|5 - CRA|#731|~~Support period/update policy - reconciles docs that already exist, zero dependencies.~~|Resolved by #742|
|4|5 - CRA|#735|~~Rule-set → compliance category mapping/additive schema field, zero dependencies, feeds #734.~~|Resolved by #756|
|5|5 - CRA|#730|~~Product SBOM + release inventory, needs new CI work; do before #734 since the report command should link real SBOM evidence, not placeholders.~~|Resolved by #740|
|6|5 - CRA|#729|~~Vulnerability/incident runbook, mostly docs + a tabletop test, no dependencies.~~|Resolved by #755|
|7|5 - CRA|#734|~~Exportable compliance/evidence report, consumes #730's SBOM links and #735's categories, so it's genuinely more useful once both exist.~~|Resolved by #766|
|8|6 - NL|#774|~~NIS2-aligned `cds security` reporting — extends the rule-set with Article 21 operator-readiness checks now that #771's gap analysis has landed.~~|Resolved by #804|
|9|6 - NL|#736|~~Stale pinned-digest warning, independent; natural to do alongside #734 since it touches similar digest/registry knowledge from #730. Cited directly by #771's Article 21 supply-chain-security gap.~~|Resolved by #758|
|10|6 - NL|#737|~~Local audit trail, independent; complements #734's evidence report and directly supports the 24h/72h incident-reporting evidence needs described in #771.~~|Resolved by #810|
|11|6 - NL|#773|~~FDS data-provider basisafspraken mapping — documentation/gap-analysis only, no dependency on the CRA chain; may spin off a follow-up contract issue once the gap analysis lands.~~|Resolved by #826|
|12|6 - NL|#779|~~OpenLineage data-lineage contract + Dagster integration — coordinates with #773's gap analysis (may satisfy part of FDS's data-provider metadata basisafspraken); optionally reuses the OTel-collector work (#777, milestone 2) as its backend.~~|Resolved by #831|
|13|6 - NL|#772|~~Common Ground/Haven reference profile and mapping doc — independent, reuses existing/in-flight modules (identity, TLS); lowest urgency of the NL set, no legal deadline behind it.~~|Resolved by #830|
|14|5 - CRA|#732|~~CRA risk assessment & technical documentation, deliberately last-but-one: it's a traceability matrix that's supposed to reference existing controls/evidence rather than restate them, so it's far more tractable once #729–731 and #737 already exist to cite~~|Resolved by #842|
|15|5 - CRA|#733|CRA release evidence gate, hard-blocked: its own acceptance criteria say it can't close until #728 (done), #729, #730, #731, and #732 are all complete, so it must be last regardless of effort.|Resolved by #847|

## Notes

- **Flexible parts:** #729/#730/#731 can be reordered among themselves (no
  dependencies between them); #736/#737/#772/#773 aren't part of the CRA
  milestone chain at all and could be interleaved anywhere once their own
  prerequisites (#730/#735/#771, where applicable) land — they're only
  sequenced here to keep the evidence-building and NL-alignment themes each
  grouped together.
- **Fixed parts:** #733 must be last, and #732 should come after the
  control-building issues it's meant to summarize. #771 should land before
  #774, since #774's scope is literally gated on #771's gap analysis.
- Milestone 5 (CRA) is *product*-security scope: CDS's own obligations as a
  manufacturer under the EU Cyber Resilience Act. Milestone 6 (Dutch
  Public-Sector Alignment) is broader: it covers Cyberbeveiligingswet/NIS2
  *operator*-level obligations that fall on CDS's users, plus the
  unrelated Federatief Datastelsel/Common Ground data- and
  platform-alignment work. #736/#737 sit in milestone 6 because their own
  issue text ties them to NIS2/Cyberbeveiligingswet-style operator
  obligations, not because they relate to FDS/Common Ground.
- Out of scope for this table (tracked instead in
  [docs/haven-parity-plan.md](../haven-parity-plan.md) and
  [docs/roadmap.md](../roadmap.md)'s Near-Term section, milestone 2
  "Production ready"): the service-mesh contract (#776), the consolidated
  Zero-Trust Architecture framing (#778), and the OpenTelemetry
  trace-sink/collector work (#777) — these are platform-architecture gaps,
  not CRA- or Dutch-law-specific, even though #777 is a dependency option
  for #779 above.
