# Open Milestone Issue Sequencing

Proposed ordering for every **open** issue in the four GitHub milestones, by
importance and dependency order, not by issue number. It complements
[cra-and-nl-law-sequencing.md](cra-and-nl-law-sequencing.md), which tracks
the (now almost fully resolved) CRA and Dutch-law chains.

Snapshot taken 2026-10-08. The ordering is a planning proposal derived from
issue titles, labels and the existing docs, not from a line-by-line read of
every issue body; confirm each dependency in the issue before starting work.

Milestones: **2** Production ready, **4** Profile Retrieval & Update,
**5** Cyber Resilience Act readiness, **6** Dutch Public-Sector Data &
Security Alignment.

## Sequence

|Order|Milestone|Issue|Description|Depends on / rationale|
|---|---|---|---|---|
|1|5 - CRA|#849|`cds get` trusted-source allowlist and commit/digest pinning|Tracked CRA gap (PLR-06); self-contained CLI change|
|2|5 - CRA|#841|Notify users when a newer CDS CLI release is available|Tracked CRA gap (PLR-10); self-contained CLI change|
|3|5 - CRA|#625|Lock pip dependencies to verified hashes across all Dockerfiles|Tracked CRA gap (PLR-08); independent image work|
|4|5 - CRA|#356|Implement or remove secret-scanning rules CDS-SEC-006/030/071|Rule-set completeness; independent of other work|
|5|5 - CRA|#357|Implement or remove CDS-SEC-032 secret file permission checks|Same rule-set area as #356; do together|
|6|2 - Prod|#211|Docker host and daemon hardening baseline|High priority; no dependencies; feeds #64 and #72|
|7|2 - Prod|#207|Runtime confinement and rootless deployment policies|High priority; builds on #211|
|8|2 - Prod|#577|TLS requirement fields on HTTP, database and cache contracts|First step of the TLS chain (contract vocabulary)|
|9|2 - Prod|#578|Certificate reference mapping in profile/module validation|Needs the fields from #577|
|10|2 - Prod|#579|Shared TLS-capable reverse-proxy contract|Needs #577; pairs with #578|
|11|2 - Prod|#580|Enforce TLS contract requirements and diagnostics in validator/planner|Needs #577–#579 so there is something to enforce|
|12|2 - Prod|#205|TLS and certificate-management contracts|Umbrella for #577–#580; close once they land|
|13|2 - Prod|#206|Enforce database and cache authentication policies|High priority; independent of the TLS chain|
|14|2 - Prod|#216|Replace development web entrypoints with production process managers|Independent; needed before #64|
|15|2 - Prod|#219|Shared Python runtime API for named database connections|Independent; lower urgency|
|16|2 - Prod|#64|Production-ready profiles with hardened runtime images|Needs #206, #207, #211, #216 and the TLS chain|
|17|2 - Prod|#72|Document and test the production hardening checklist|Last of the hardening set; verifies #64 and #211|
|18|4 - Retrieval|#346|Tracking metadata for fetched CDS configuration assets|Do after #849 so provenance and trust fields are designed once|
|19|4 - Retrieval|#348|`cds update` to refresh tracked profiles, modules and contracts|Needs #346 metadata|
|20|2 - Prod|#73|Kubernetes runtime support in profile and module schemas|Start of the Kubernetes chain|
|21|2 - Prod|#74|Core Kubernetes renderer and CLI runtime switch|Needs #73|
|22|2 - Prod|#75|K8s secrets, configmaps, network policies and stateful resources|Needs #74|
|23|2 - Prod|#76|Kubernetes renderer test suite and kind-based CI validation|Needs #74; extend as #75 lands|
|24|2 - Prod|#77|Kubernetes docs, migration guide and example profiles|Needs #73–#76|
|25|2 - Prod|#65|Kubernetes Support Implementation Plan|Tracking/umbrella for #73–#77; close with #77|
|26|2 - Prod|#767|GitOps rendering/module support (FluxCD reference)|Needs the K8s renderer (#74)|
|27|2 - Prod|#768|Kubernetes-native database operator pattern (CloudNativePG)|Needs #75; low priority|
|28|6 - NL|#210|Backup, restore and disaster-recovery contracts|High priority; contract must exist before the provider issues|
|29|6 - NL|#665|Backup/DR capability for the Postgres warehouse module|Needs #210|
|30|6 - NL|#668|Generic file/volume backup capability (Dagster IO manager, DuckDB, dlt)|Needs #210|
|31|6 - NL|#669|Backup/replication for the object-storage module's bucket contents|Needs #210|
|32|2 - Prod|#777|Trace-sink contract and OpenTelemetry Collector reference module|Independent; optional backend for lineage work|
|33|6 - NL|#662|Log-sink provider module (e.g. Loki/Vector)|Independent; pairs with #777 for observability|
|34|2 - Prod|#776|Service-mesh contract and Istio reference module (mutual TLS)|Needs the TLS chain (#205) and the K8s renderer; low priority|
|35|2 - Prod|#778|Zero-Trust Architecture maturity tier|Consolidates #205, #776 and network policies (#75); do last of that set|
|36|2 - Prod|#769|`cds maturity`: read-only profile maturity report|Most useful once #778 defines the tiers|
|37|6 - NL|#843|Assess whether the lineage-sink contract meets FDS provenance basisafspraken|Documentation only; low priority|
|38|6 - NL|#844|Let a profile declare participation as a federated data-sharing data provider|Low priority; informed by #843|
|39|6 - NL|#845|Standard vocabulary for dataset classification in the open-data-provider contract|Long-term; informed by #843|
|40|6 - NL|#680|Keycloak module: realm configuration support|Independent; low priority|
|41|6 - NL|#681|Keycloak module: `oidc-provider` shared contract|Independent; low priority, easier after #680|

## Notes

- **Fixed chains:** TLS (#577 → #578/#579 → #580 → #205), Kubernetes
  (#73 → #74 → #75/#76 → #77 → #65), retrieval (#346 → #348) and backup
  (#210 → #665/#668/#669). Items inside a chain should not be reordered;
  chains can be interleaved freely.
- **Flexible parts:** orders 1–5 are independent of each other and of the
  rest. The hardening items #206, #207, #211 and #216 can be reordered
  among themselves.
- **Quick wins first:** the CRA gaps (orders 1–5) come first because they
  are small, tracked in `docs/cra-risk-assessment.md`, and keep the CRA
  milestone closable.
- **Milestone placement:** #210 and #662/#665/#668/#669 sit in milestone 6
  but are platform work (backup, logging), not Dutch-law-specific; the
  ordering reflects their technical dependencies, not their milestone.
- Already closed issues (for example #576, #229, #230, #212, #208, #209,
  #70, #71, #347, #280, #174, #175 and all of milestone 5's earlier chain)
  are omitted. The completed CRA/NL chain is in
  [cra-and-nl-law-sequencing.md](cra-and-nl-law-sequencing.md).
