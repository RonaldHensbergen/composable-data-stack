|Order|Issue|Effort/Description|Status|
|---|---|---|---|
|[x]|#728|<span style="color: #444;">~~Documentation only~~</span>|Resolved by #738|
|[x]|#731|<span style="color: #444;">~~Support period/update policy - reconciles docs that already exist, zero dependencies.~~</span>|Resolved by #742|
|[x]|#735|<span style="color: #444;">~~Rule-set → compliance category mapping/additive schema field, zero dependencies, feeds #734.~~</span>|Resolved by #756|
|[x]|#730|<span style="color: #444;">~~Product SBOM + release inventory, needs new CI work; do before #734 since the report command should link real SBOM evidence, not placeholders.~~</span>|Resolved by #740|
|[5]|#729|**Vulnerability/incident runbook, mostly docs + a tabletop test, no dependencies.**|Ready with #755|
|[6]|#734|**Exportable compliance/evidence report, consumes #730's SBOM links and #735's categories, so it's genuinely more useful once both exist.**|Ready with #766|
|[7]|#736|<span style="color: lightblue;">**Stale pinned-digest warning, independent; natural to do alongside #734 since it touches similar digest/registry knowledge from #730.**</span>|Ready with #756|
|[8]|#737|<span style="color: lightblue;">Local audit trail, independent; complements #734's evidence report, so sequencing it right after keeps the "evidence" theme coherent.</22span>|No CRA|
|[9]|#732|<span style="color: #bdf;">CRA risk assessment & technical documentation, deliberately last-but-one: it's a traceability matrix that's supposed to reference existing controls/evidence rather than restate them, so it's far more tractable once #729–731 and #737 already exist to cite.</span>|CRA|
|[10]|#733|<span style="color: #bdf;">CRA release evidence gate, hard-blocked: its own acceptance criteria say it can't close until #728 (done), #729, #730, #731, and #732 are all complete, so it must be last regardless of effort.</span>|CRA|

Flexible parts: #729/#730/#731 can be reordered among themselves (no dependencies between them); #736/#737 aren't part of the CRA milestone chain at all and could be interleaved anywhere once #730/#735 land - they're only sequenced here to keep evidence-building work grouped. Fixed part: #733 must be last, and #732 should come after the control-building issues it's meant to summarize.
