# CRA Cybersecurity Risk Assessment and Annex I Traceability

Status: Living engineering record — proactive readiness, not a claim of CRA
scope or conformity
Owner: repository maintainer (Ronald Hensbergen)
Related: [`docs/cra-scope-decision.md`](cra-scope-decision.md),
[`docs/threat-model.md`](threat-model.md),
[`docs/cra-incident-runbook.md`](cra-incident-runbook.md),
[`docs/security-support-policy.md`](security-support-policy.md),
[`docs/cra-technical-documentation.md`](cra-technical-documentation.md),
milestone "Cyber Resilience Act readiness"
([#728](https://github.com/RonaldHensbergen/composable-data-stack/issues/728)-[#733](https://github.com/RonaldHensbergen/composable-data-stack/issues/733)),
covers issue
[#732](https://github.com/RonaldHensbergen/composable-data-stack/issues/732)
Last reviewed: 2026-10-07
Next scheduled review: 2027-10-07, or immediately upon a trigger in
[§4](#4-review-triggers)

## Purpose and disclaimer

[`docs/cra-scope-decision.md`](cra-scope-decision.md) currently assesses CDS
as non-commercial free and open-source software, outside the Cyber
Resilience Act's (CRA, Regulation (EU) 2024/2847) economic-operator
obligations. This document is nonetheless built proactively, as a **living
engineering record** of how CDS's design, development, release, and
maintenance already address — or have a tracked gap or a justified
non-applicability against — Annex I's essential cybersecurity requirements
and Article 13(2)-(3)'s cybersecurity risk assessment. It does not itself
establish or waive any legal obligation, and **it is not a claim of CRA
conformity, an EU declaration of conformity, or a substitute for a future
conformity-assessment procedure** should scope ever change (see
[`docs/cra-scope-decision.md`](cra-scope-decision.md)).

This document complements, and deliberately does not restate, two existing
records:

- [`docs/threat-model.md`](threat-model.md) assesses risks to a
  **CDS-rendered production deployment** (the stack a user builds with
  CDS) — secrets, network exposure, container/host runtime, supply chain,
  backup/recovery.
- This document assesses risks to **the CDS product itself** — the CLI
  distribution and its development/release lifecycle, and the
  CDS-published runtime images as a related but distinct artifact family
  (Article 13(2), Annex I Part I, point 1).

Per the scope note in [`docs/cra-scope-decision.md`](cra-scope-decision.md),
every control described below is read as "what CDS's engineering practice
already does, and where it currently falls short" — not as evidence that
CDS is an in-scope CRA manufacturer today.

## 1. Product-lifecycle risk register

Scope: risks arising from the CDS CLI's own design, development, build, and
release process — distinct from §5 of
[`docs/threat-model.md`](threat-model.md), which covers a rendered
deployment's runtime risks. Each entry follows the acceptance-criteria
schema: asset, threat, likelihood, impact, treatment, residual risk, owner,
evidence, review date.

| ID | Asset | Threat | Likelihood | Impact | Treatment | Residual risk | Owner | Evidence | Review date |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PLR-01 | Profile/module YAML parser (`cli/loader.py`) | Untrusted YAML (a profile or module shared by a third party) exploits an unsafe loader to execute arbitrary Python objects on load | Low | High | All YAML parsing uses `yaml.safe_load` exclusively (`cli/loader.py:28`, `cli/image_verification.py:114`, `cli/main.py:245,1877,1939,2286`, `cli/preflight.py:291`, `cli/security.py:1019`); profile/module shape is further constrained by closed JSON Schema validation (`cli/resources/*.json`) before any field is used | Low — no `yaml.load`/`FullLoader` call exists in `cli/`; schema validation rejects unrecognized fields | Maintainer | `cli/loader.py`, `cli/resources/*.json`, grep audit 2026-10-07 (`yaml.safe_load` only, no unsafe loader calls) | 2027-10-07 |
| PLR-02 | Template interpolation engine (`cli/renderer.py`) | A module/profile author embeds an expression that escapes the intended `${config.*}`/`${bindings.*}`/`${service.host}` vocabulary to read arbitrary host state or inject unintended Compose fields | Low | Medium | Renderer only recognizes the closed interpolation vocabulary documented in `AGENT.md`; no `eval`/`exec`/shell-style expression evaluation exists in the renderer | Low | Maintainer | `cli/renderer.py`, `AGENT.md` ("Preserve the interpolation vocabulary...") | 2027-10-07 |
| PLR-03 | Generated Compose/Helm artifacts and bind-mount sources | A crafted profile/module causes the renderer to write outside the chosen output directory (directory-traversal in generated file paths) | Low | High | `cli/renderer.py`'s `_path_is_within_root` containment check gates rewritten bind-mount/build-context sources against `project_root`/`compose_dir` (`cli/renderer.py:650,842,903`); exercised by the tabletop scenario in [`docs/cra-incident-runbook.md`](cra-incident-runbook.md#scenario-exercised-2026-09-22) | Low — containment check is unconditional wherever `project_root` is known | Maintainer | `cli/renderer.py:903` (`_path_is_within_root`), `cra-incident-runbook.md` tabletop scenario | 2027-10-07 |
| PLR-04 | Module/profile source resolution (`cli/loader.py`) | A module `source:` path traverses outside `modules/`/`modules-experimental/`, loading an attacker-controlled module definition | Low | High | `cli/loader.py` resolves and validates every module source against an allowed root, rejecting absolute paths and traversal outside `modules/`/`modules-experimental/` (`cli/loader.py:87-131`); `CDS_MODULE_PATH`/`CDS_PROFILE_PATH` are the only supported external-root overrides and are explicit, operator-controlled environment variables | Low | Maintainer | `cli/loader.py:87-131`, `AGENT.md` ("Module sources must be relative...") | 2027-10-07 |
| PLR-05 | Subprocess invocations (`docker compose`, `cosign`, `trivy`, `kind`/k3d) | Command injection via unsanitized arguments passed to `subprocess.run`/`Popen` | Low | High | Every subprocess call in `cli/` builds a fixed argument list (never `shell=True`), reviewed and annotated `# nosec B603` after Bandit review; Bandit (`bandit -r cli/ shared/ images/ workdirs/`) runs on every CI run (`.github/workflows/ci.yml`) | Low | Maintainer | `cli/image_verification.py:261`, `cli/k8s_runner.py:280`, `cli/preflight.py:109`, `cli/up_runner.py:79,144,164`, `.github/workflows/ci.yml` (bandit step) | 2027-10-07 |
| PLR-06 | `cds get` archive/repository retrieval | Fetching a profile/module bundle from an untrusted URL extracts an archive entry (zip-slip) outside the destination root, or follows a redirect to an unintended host | Medium | Medium | `cli/getter.py` resolves the destination root and every extracted/copied asset path against it (`cli/getter.py:76,232,255,259,283,296,345`); `.cds/get-manifest.json` records file provenance for fetched assets | Medium — no explicit allowlist of permitted source registries/hosts for `cds get` is documented; tracked as a gap below | Maintainer | `cli/getter.py` path-containment checks, `docs/architecture.md` (`cds get`'s manifest) | 2027-10-07 |
| PLR-07 | Generated artifacts (rendered Compose/Helm, `.env`, `docker-compose.yml`) | A secret value is resolved into a generated artifact instead of a `${CDS_*}` placeholder, leaking it into a file a user might commit or share | Low | High | Planner/renderer only ever emit `${CDS_*}` placeholders for secrets (core design invariant, `AGENT.md`); `cds report`'s `secretLeakCheck` independently re-scans rendered output for literal secret values and fails with `E119` if one is found | Low | Maintainer | `docs/compliance-report.md` (`secretLeakCheck`), `AGENT.md` ("Never resolve secret values into plans...") | 2027-10-07 |
| PLR-08 | Third-party/dependency components (Python packages, OS packages in runtime images, upstream base images) | A compromised or vulnerable upstream dependency is integrated without detection, propagating the compromise to CDS's own distribution | Medium | High | `pip-audit` (CLI/runtime Python dependencies) and Bandit SAST run in CI (`.github/workflows/ci.yml`); runtime images are scanned by trivy at PR-time and daily (`docs/image-scanning.md`); `.trivyignore` exceptions are time-boxed and CI-enforced (`tests/test_trivyignore.py`) | Medium — pip dependency *hash* pinning (not just CVE scanning) is not yet enforced for all Dockerfiles; **tracked gap, [#625](https://github.com/RonaldHensbergen/composable-data-stack/issues/625)** | Maintainer | `.github/workflows/ci.yml` (bandit/pip-audit steps), `docs/image-scanning.md`, `tests/test_trivyignore.py` | 2027-10-07 |
| PLR-09 | Release infrastructure (GitHub Actions, PyPI, GHCR, Docker Hub publishing credentials) | A compromised CI job or leaked credential publishes a tampered CLI package or image under the project's identity | Low | Critical | PyPI publishing uses OIDC trusted publishing with no stored long-lived token (`id-token: write`, `.github/workflows/pypi.yml`) and PEP 740 attestations (`attestations: true`); GHCR publishing uses the ephemeral, auto-rotated `GITHUB_TOKEN`; image signing uses keyless cosign scoped to `publish-images.yml@refs/heads/main` (`docs/image-signing.md`) | Medium — Docker Hub publishing still uses a long-lived stored PAT (`secrets.RONALDSOEVEREIN_LOGIN`), and the project has a single-maintainer governance model with no credential-rotation SLA documented (same structural fact already recorded in `docs/cra-scope-decision.md`'s governance section) | Maintainer | `.github/workflows/pypi.yml`, `.github/workflows/publish-images.yml`, `docs/image-signing.md`, `docs/cra-scope-decision.md` (governance) | 2027-10-07 |
| PLR-10 | Update delivery / version discovery for the CDS CLI itself | A user keeps running an outdated, vulnerable CDS CLI version because nothing in the tool itself surfaces that a newer release exists | High | Medium | Users discover updates via GitHub Releases, `CHANGELOG.md`, and GitHub Security Advisories (`docs/security-support-policy.md` §3 "Discovery"); no in-CLI check/notification exists yet | High — no automated or opt-in notification mechanism inside `cds` itself; distinct from [#386](https://github.com/RonaldHensbergen/composable-data-stack/issues/386), which checks *rendered-stack* image/package staleness, not the CDS CLI's own version. **Tracked gap, [#841](https://github.com/RonaldHensbergen/composable-data-stack/issues/841)** | Maintainer | `docs/security-support-policy.md` §3 | 2027-10-07 |

## 2. Annex I Part I traceability — properties of products with digital elements

Disposition values: **Implemented** (evidence exists and is considered
adequate), **Tracked gap** (a real shortfall with an owning GitHub issue),
**Non-applicable (justified)** (the requirement does not meaningfully apply
to a CLI compiler/image-publishing product, with a stated reason).

| Annex I Part I item | Requirement (summarized) | CDS control / evidence | Owner | Related issue(s) | Disposition |
| --- | --- | --- | --- | --- | --- |
| 1 | Appropriate level of cybersecurity based on risk | §1 above (product-lifecycle risk register) plus [`docs/threat-model.md`](threat-model.md) (rendered-deployment risks) and `cli/resources/rule-set.json` (security rule engine) | Maintainer | — | Implemented |
| 2(a) | Made available without known exploitable vulnerabilities | Bandit SAST + `pip-audit` on every CI run (`.github/workflows/ci.yml`); runtime images gated by a PR-time trivy scan that fails on HIGH/CRITICAL findings (`docs/image-scanning.md`) | Maintainer | — | Implemented |
| 2(b) | Secure-by-default configuration, with reset capability | `cli/validator.py` and `rule-set.json`'s `configuration-management` category flag insecure fallback defaults and empty/weak secrets before a profile can render; `cds render`/`cds up` are idempotent and stateless on the CDS side (no CDS-held device state to "reset" — any persistent state belongs to the rendered stack, covered by `docs/threat-model.md`) | Maintainer | — | Implemented |
| 2(c) | Vulnerabilities addressed through security updates, incl. automatic option and user notification | `docs/security-support-policy.md` (delivery targets, emergency-release path); daily rescans + weekly rebuild/republish for images (`docs/image-scanning.md`). No in-CLI update notification exists (CDS is a `pip`/`pipx`-installed developer tool with no background agent to auto-update) | Maintainer | [#386](https://github.com/RonaldHensbergen/composable-data-stack/issues/386) (related: rendered-stack staleness, not CDS's own version), [#841](https://github.com/RonaldHensbergen/composable-data-stack/issues/841) (owning issue for this gap) | Tracked gap — see PLR-10, [#841](https://github.com/RonaldHensbergen/composable-data-stack/issues/841) |
| 2(d) | Protection from unauthorized access, with reporting | Release-infrastructure access uses OIDC/ephemeral tokens where available (PLR-09); rendered-stack authentication enforcement is `rule-set.json`'s `access-control` category, with known coverage gaps | Maintainer | [#206](https://github.com/RonaldHensbergen/composable-data-stack/issues/206) (auth policy enforcement), [#356](https://github.com/RonaldHensbergen/composable-data-stack/issues/356)/[#357](https://github.com/RonaldHensbergen/composable-data-stack/issues/357) (secret-handling rule completeness) | Tracked gap |
| 2(e) | Confidentiality of stored/transmitted data | Secrets are never resolved into plans/rendered output — only `${CDS_*}` placeholders (PLR-07); encryption-in-transit for *rendered deployments* (TLS) is not yet enforced | Maintainer | [#205](https://github.com/RonaldHensbergen/composable-data-stack/issues/205) (TLS/certificate-management contracts) | Tracked gap (CLI-side handling implemented; deployment-side TLS open) |
| 2(f) | Integrity of stored/transmitted data, commands, configuration; report corruption | Runtime images: cosign signing + CycloneDX SBOM + SLSA provenance attestation (`docs/image-signing.md`); CLI package: PyPI publish uses PEP 740 Sigstore attestations (`attestations: true`, `.github/workflows/pypi.yml`); golden-hash tests (`tests/golden/`) catch unintended render drift | Maintainer | — | Implemented |
| 2(g) | Data minimisation | CDS sends no telemetry; the local audit trail (`docs/audit-log.md`) is explicitly local-only ("Nothing in the log is sent to CDS or any third party") | Maintainer | — | Implemented |
| 2(h) | Availability of essential/basic functions, incl. resilience against DoS | CDS itself is a one-shot compiler with no persistent service to keep available; resilience of a *rendered* stack's stateful services (backup/restore) is not yet covered by any module contract | Maintainer | [#210](https://github.com/RonaldHensbergen/composable-data-stack/issues/210) (backup/restore/DR contracts) | Tracked gap (deployment-side only; CDS itself is Non-applicable here) |
| 2(i) | Minimise negative impact on availability of other devices/networks | `rule-set.json`'s `network-exposure` category flags overly broad network bindings (`0.0.0.0`, externally published databases) in rendered output; CDS itself has no network listener | Maintainer | — | Implemented (rendered-stack scope); Non-applicable (CDS process itself) |
| 2(j) | Limit attack surfaces, incl. external interfaces | `yaml.safe_load` exclusively (PLR-01); closed JSON Schema validation of every profile/module field; module-source path containment (PLR-04); closed template-interpolation vocabulary (PLR-02) | Maintainer | — | Implemented |
| 2(k) | Reduce incident impact via exploitation-mitigation techniques | Subprocess calls use fixed argument lists, Bandit-reviewed (PLR-05); generated-artifact path containment (`_path_is_within_root`, PLR-03) — the exact scenario exercised by the `cra-incident-runbook.md` tabletop | Maintainer | — | Implemented |
| 2(l) | Record/monitor internal security-relevant activity, with opt-out | `validate`/`render`/`up`/`test` append a local, structured audit entry per invocation (`docs/audit-log.md`); no opt-out flag exists yet — the log is always written when those commands run | Maintainer | — | Tracked gap (no opt-out) — no existing issue; candidate for a future `--no-audit-log` flag, not yet filed given low severity (local-only, no PII/telemetry risk) |
| 2(m) | Secure, permanent data removal; secure data portability | The audit log is a plain local file (`.cds/audit-log.jsonl`) the user can delete directly; CDS holds no remote or CDS-controlled copy of any user data to "remove" | Maintainer | — | Non-applicable (justified) — CDS has no remote/managed data store; all state is local, user-owned files |

## 3. Annex I Part II traceability — vulnerability handling requirements

| Annex I Part II item | Requirement (summarized) | CDS control / evidence | Owner | Related issue(s) | Disposition |
| --- | --- | --- | --- | --- | --- |
| II.1 | Identify/document components, incl. an SBOM | CycloneDX SBOM generated and attested for every published image (`docs/image-signing.md`); `docs/compliance-report.md` (`cds report`) links per-rendered-stack image/SBOM evidence; product-level release inventory covers CLI artifacts | Maintainer | [#730](https://github.com/RonaldHensbergen/composable-data-stack/issues/730) (closed — SBOM/release inventory delivered) | Implemented |
| II.2 | Address and remediate vulnerabilities without delay, incl. separate security/functionality updates | `docs/security-support-policy.md` §3 (severity-scaled delivery targets, emergency-release path independent of the weekly train) | Maintainer | [#731](https://github.com/RonaldHensbergen/composable-data-stack/issues/731) (closed) | Implemented |
| II.3 | Apply effective, regular security tests and reviews | CI runs the full unit-test suite, Bandit SAST, `pip-audit`, and image trivy scans on every change (`.github/workflows/ci.yml`, `docs/image-scanning.md`); `docs/threat-model.md` §7 defines an (as-yet-unexecuted) penetration-test plan | Maintainer | [#64](https://github.com/RonaldHensbergen/composable-data-stack/issues/64)/[#72](https://github.com/RonaldHensbergen/composable-data-stack/issues/72) (production-hardening baseline and its verification, prerequisite for executing the pen-test plan) | Tracked gap (automated testing implemented; the dedicated penetration test in `threat-model.md` §7 has not yet run) |
| II.4 | Publicly disclose fixed-vulnerability information | `SECURITY.md` coordinated vulnerability disclosure policy; `docs/cra-incident-runbook.md` (advisory publication timing); `CHANGELOG.md`/GitHub Releases for every fix | Maintainer | — | Implemented |
| II.5 | Coordinated vulnerability disclosure policy | `SECURITY.md` (CVD policy, monitored contact); operationalized by `docs/cra-incident-runbook.md` | Maintainer | — | Implemented |
| II.6 | Facilitate sharing of vulnerability info, incl. a contact address | GitHub Security Advisories is the documented, monitored primary channel (`SECURITY.md`, `docs/cra-incident-runbook.md` §"Monitored contact and intake") | Maintainer | — | Implemented |
| II.7 | Secure update-distribution mechanisms | CLI: signed PyPI releases via OIDC trusted publishing + PEP 740 attestations; images: cosign-signed, SBOM/provenance-attested pulls, verified by `cli/image_verification.py` | Maintainer | — | Implemented |
| II.8 | Security updates disseminated without delay, free of charge, with advisory information | `docs/security-support-policy.md` (free, no paid tier — consistent with `docs/cra-scope-decision.md`'s facts); `docs/cra-incident-runbook.md` §"User communication and advisory publication" | Maintainer | — | Implemented |

## 4. Review triggers

This document, and the matrices above, must be revisited immediately upon
any of the following, in addition to the annual cadence in
[§6](#6-ownership-and-review-cadence):

- A material change to the CDS product itself: a new compiler stage, a new
  secrets-handling mechanism, or a change to the module/profile schema that
  affects untrusted-input handling (§1).
- A new render target (alongside Docker Compose and Kubernetes/Helm),
  since it introduces a new generated-artifact surface that §1/§2 must be
  re-evaluated against.
- New network behavior in the CDS CLI itself (e.g., any outbound telemetry,
  update-check network call, or new default-on registry/API call) — today
  CDS makes no such calls except explicit, user-invoked ones (`cds get`,
  image pulls, `cds security --verify-images`).
- A new package format or distribution channel (e.g., a native installer,
  an additional package registry) beyond PyPI/GHCR/Docker Hub.
- Any security incident or actively exploited vulnerability handled under
  [`docs/cra-incident-runbook.md`](cra-incident-runbook.md), once resolved
  — to confirm whether this document's risk register or disposition table
  needs a new or updated entry.
- Any reassessment trigger in
  [`docs/cra-scope-decision.md`](cra-scope-decision.md#mandatory-reassessment-triggers).

## 5. New gaps identified while building this matrix

Building the traceability matrix in §2/§3 surfaced two shortfalls. Per this
document's acceptance criteria ("every requirement has implemented
evidence, a tracked gap, or a justified non-applicability"), each must have
an owning issue rather than being left undispositioned:

- **CDS CLI self-update notification** (Annex I Part I, item 2(c); PLR-10)
  — distinct from [#386](https://github.com/RonaldHensbergen/composable-data-stack/issues/386),
  which addresses rendered-stack image/package staleness, not the CDS CLI's
  own version. No pre-existing issue owned this gap, so
  [#841](https://github.com/RonaldHensbergen/composable-data-stack/issues/841)
  was filed to track it.
- Pip dependency hash-locking for runtime images (Annex I Part I, item
  2(a)/PLR-08) was already tracked under
  [#625](https://github.com/RonaldHensbergen/composable-data-stack/issues/625)
  and did not need a new issue.

## 6. Ownership and review cadence

- **Owner:** repository maintainer (Ronald Hensbergen).
- **Cadence:** at least annually, and immediately upon any trigger in
  [§4](#4-review-triggers).
- **Next scheduled review:** 2027-10-07, or sooner if a trigger occurs
  first.
- Updating a disposition in §2/§3 (e.g., from "Tracked gap" to
  "Implemented" once an owning issue closes) does not require a full
  document re-review; the annual/triggered review re-validates the whole
  matrix.

## References

- Regulation (EU) 2024/2847, Article 13(1)-(4), Annex I (Parts I and II).
- [`docs/cra-scope-decision.md`](cra-scope-decision.md),
  [`docs/threat-model.md`](threat-model.md),
  [`docs/cra-incident-runbook.md`](cra-incident-runbook.md),
  [`docs/security-support-policy.md`](security-support-policy.md),
  [`docs/image-scanning.md`](image-scanning.md),
  [`docs/image-signing.md`](image-signing.md),
  [`docs/compliance-report.md`](compliance-report.md),
  [`docs/audit-log.md`](audit-log.md),
  [`docs/security-compliance-categories.md`](security-compliance-categories.md).
