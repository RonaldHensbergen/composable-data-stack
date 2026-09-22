# CRA Vulnerability and Incident Reporting Runbook

Status: Operational runbook — proactive readiness, not a claim of CRA scope
Owner: repository maintainer (Ronald Hensbergen)
Related: [`docs/cra-scope-decision.md`](cra-scope-decision.md),
[`SECURITY.md`](../SECURITY.md), [`RELEASE.md`](../RELEASE.md), milestone
"Cyber Resilience Act readiness"
([#728](https://github.com/RonaldHensbergen/composable-data-stack/issues/728)-[#733](https://github.com/RonaldHensbergen/composable-data-stack/issues/733))

## Purpose

[`docs/cra-scope-decision.md`](cra-scope-decision.md) currently assesses CDS
as non-commercial free and open-source software, outside the Cyber
Resilience Act's (CRA) economic-operator obligations. This runbook is built
proactively, so the project already has an operational path if that scope
determination changes, and because it is good practice independent of legal
status. It does not itself establish or waive any CRA obligation.

This runbook operationalizes [`SECURITY.md`](../SECURITY.md)'s coordinated
vulnerability disclosure (CVD) policy for the two triggers that carry CRA
Article 14 reporting obligations for an in-scope manufacturer: an **actively
exploited vulnerability** in CDS, and a **severe security incident**
affecting CDS's confidentiality, integrity, or availability, or that of its
supply chain. Ordinary dependency findings and false positives are handled
through the routine channels described below, not through this runbook.

## Roles

CDS currently has a single maintainer. Until the project has additional
maintainers or a formal legal entity, one person holds every role below:

| Role | Responsibility | Current holder |
| --- | --- | --- |
| Intake owner | Monitors the reporting channel, acknowledges reports, starts the clock | Ronald Hensbergen |
| Triage lead | Runs the decision tree, sets severity, decides embargo | Ronald Hensbergen |
| Communications lead | Drafts advisories, user notifications, and third-party maintainer notices | Ronald Hensbergen |
| Reporting lead | Prepares ENISA Single Reporting Platform (SRP) submissions and CSIRT contact | Ronald Hensbergen |

A single-person rotation means there is no handoff between roles, but it
also means a missed out-of-hours notification has no backup. See
[Out-of-hours path](#out-of-hours-path).

## Monitored contact and intake

The monitored contact is the same as [`SECURITY.md`](../SECURITY.md)'s
reporting channel:

1. **GitHub Security Advisories** for this repository (primary; creates a
   private draft advisory and notifies the maintainer by GitHub
   notification and email).
2. **Private contact to maintainers**, if listed in the project profile, as
   a fallback if GitHub Security Advisories is unavailable to the reporter.

The intake owner checks both channels at least once every 24 hours,
including weekends, for the duration any CRA-relevant milestone issue
remains open, and treats a report as "aware" (starting the clock in
[Timeline](#timeline-and-deadlines)) from the moment it is read, not from
when it was sent.

## Decision tree

On every report or internally discovered issue, the triage lead classifies
it before doing anything else:

1. **Is there credible evidence of active exploitation** (e.g., a public
   proof-of-concept in use, exploitation reported by a user, telemetry, or a
   trusted third party)? -> **Actively exploited vulnerability.** Follow the
   full timeline below.
2. If not actively exploited, **did it cause a severe impact** on
   confidentiality, integrity, or availability of CDS-produced artifacts or
   the infrastructure used to build/publish them (e.g., compromised signing
   key, published image containing injected malicious code, leaked
   credentials with confirmed misuse)? -> **Severe security incident.**
   Follow the full timeline below.
3. If neither 1 nor 2, but it is a legitimate vulnerability without evidence
   of exploitation (e.g., a dependency CVE, a validator bypass with no known
   exploitation) -> **Ordinary vulnerability.** Handle through
   [`SECURITY.md`](../SECURITY.md)'s standard response targets and, for
   runtime images, [`docs/image-scanning.md`](image-scanning.md). No ENISA
   SRP submission and no runbook clock apply.
4. If investigation shows the report does not reproduce, is out of scope,
   or is a duplicate -> **False positive/non-issue.** Close with an
   explanation to the reporter; no further action.

Reclassify if new evidence emerges (e.g., an "ordinary vulnerability" later
found to be under active exploitation moves to step 1 and the clock starts
from the moment that evidence is confirmed).

## Timeline and deadlines

These deadlines apply only to actively exploited vulnerabilities and severe
incidents (decision tree steps 1-2). The clock starts when the intake owner
becomes aware, per [Monitored contact and intake](#monitored-contact-and-intake).

| Deadline | Action |
| --- | --- |
| Within 24 hours | Submit an early warning: what is known, whether exploitation is suspected or confirmed, and initial severity. Internal record kept regardless of CRA-SRP applicability (see [ENISA reporting](#enisa-reporting-and-what-stays-internal)); preserve evidence per [Evidence preservation](#evidence-preservation). |
| Within 72 hours | Submit a vulnerability/incident notification: confirmed severity and impact, indicators of compromise or exploitation known so far, and, where already possible, mitigating measures. This is also the point at which affected-user communication is drafted if impact is confirmed (see [User communication](#user-communication-and-advisory-publication)). |
| Within 14 days after a corrective or mitigating measure becomes available | Submit the final vulnerability report: root cause, the fix or mitigation, and confirmation it is available to users. |
| Within one month of the incident notification | Submit the final incident report for severe security incidents: root cause, impact, and mitigation measures taken. |

The 72-hour deadline in this table does not change
[`SECURITY.md`](../SECURITY.md)'s 72-hour **initial acknowledgement**
target for ordinary reports; the two are the same clock only when a report
turns out to be an actively exploited vulnerability or severe incident, in
which case the acknowledgement and the 72-hour notification happen
together.

## Out-of-hours path

There is no on-call rotation beyond the single maintainer. If the intake
owner is unavailable (travel, illness) for more than 24 hours during an
active CRA-relevant event:

- The most recent commit access holder able to reach GitHub Security
  Advisories acts as a temporary intake owner if one is designated in
  advance; today, none is, and this gap is a known limitation of the
  single-maintainer governance model recorded in
  [`docs/cra-scope-decision.md`](cra-scope-decision.md).
- On return, the intake owner reviews any missed reports as if just
  received, notes the actual detection delay in the evidence log, and
  proceeds with the timeline from the point of awareness, not from the
  original report time.

## Evidence preservation

For every actively exploited vulnerability or severe incident, the triage
lead preserves, in a private location (a draft GitHub Security Advisory or
an access-restricted issue, never a public issue or PR):

- The original report or detection signal (message, telemetry, advisory
  text) with received/observed timestamps.
- Affected versions, modules, or images, and the commit/tag/digest in
  scope.
- Reproduction steps or proof of concept, if provided.
- Any indicators of exploitation (log excerpts, reporter-provided evidence,
  upstream advisory references).
- A timeline of maintainer actions and decisions (classification,
  escalation, fix, notification), each with a timestamp.
- The eventual fix commit/PR and release version.

Evidence is retained indefinitely by default (private GitHub Security
Advisory or access-restricted issue), and at minimum for as long as the
affected release remains supported. A durable, version-specific retention
period is defined by the support policy tracked under
[#731](https://github.com/RonaldHensbergen/composable-data-stack/issues/731);
this runbook will adopt that period once it exists, rather than restate one
prematurely.

## ENISA reporting and what stays internal

For an in-scope manufacturer, Article 14 reports for actively exploited
vulnerabilities and severe incidents go to the
[ENISA Single Reporting Platform (SRP)](https://www.enisa.europa.eu/topics/product-security/single-reporting-platform-srp),
which also notifies the relevant national Computer Security Incident
Response Team (CSIRT). Because
[`docs/cra-scope-decision.md`](cra-scope-decision.md) currently assesses CDS
as outside CRA's economic-operator obligations, no ENISA SRP submission is
made today. If that determination changes, the reporting lead submits:

- **To ENISA SRP:** the 24-hour early warning, the 72-hour notification, the
  14-day final vulnerability report, and the one-month final incident
  report described in [Timeline and deadlines](#timeline-and-deadlines).
- **Kept internal only** (not submitted to ENISA SRP): the full evidence
  log described above, internal severity scoring rationale, draft
  communications before publication, and any information whose disclosure
  would itself create additional risk (e.g., unpatched exploit details)
  before a fix is available.

Regardless of CRA-SRP applicability, the internal evidence log and the
24-hour/72-hour actions in the table above are followed every time, so the
project is ready to submit immediately if scope changes mid-incident.

## Third-party and upstream coordination

If a vulnerability is found in a component CDS integrates (a base image, a
Python dependency, a module's upstream project) rather than in CDS's own
code:

1. Notify the upstream maintainer or project's security contact using their
   published disclosure process, before any public CDS-side advisory, and
   respect their embargo timeline if one is set.
2. Do not disclose upstream-reported details publicly until either the
   upstream project has published a fix/advisory, or an agreed embargo
   period elapses, whichever is first, unless active exploitation makes
   immediate user notification necessary regardless of upstream's
   timeline (see [User communication](#user-communication-and-advisory-publication)).
3. Share reproduction details or fixes with upstream maintainers over a
   private channel (GitHub Security Advisory collaboration, encrypted
   email, or the upstream project's own private intake), never in a public
   issue, PR, or commit message.
4. Track the upstream fix in the CDS evidence log and reference it from the
   CDS advisory once public.

## User communication and advisory publication

- **Without undue delay** once impact on users is confirmed (typically at
  or before the 72-hour notification point), publish or send a
  user-facing notice describing: what is affected, mitigation or
  workaround steps available now, and where the fix will land.
  Communication channels are the same as existing release communication:
  a GitHub Security Advisory, a `CHANGELOG.md` entry, and/or a GitHub
  Release note.
- Mitigation guidance must not require disclosing exploit details that
  would help an unrelated attacker before a fix ships.
- After remediation, publish the public advisory (GitHub Security
  Advisory, made public) and add a `CHANGELOG.md`/release-notes entry
  following [`RELEASE.md`](../RELEASE.md)'s Release Notes Template,
  referencing the advisory and, for a CVE, the CVE identifier.
- Advisory publication timing follows the same 14-day (vulnerability) or
  one-month (incident) final-report deadlines in
  [Timeline and deadlines](#timeline-and-deadlines): the advisory should be
  public no later than the corresponding final report, unless publishing
  earlier would still leave users exposed to an unfixed, actively exploited
  issue.

## Tabletop exercise

The runbook is exercised with a tabletop scenario at least once per year,
or whenever it changes materially, and the result is retained (dated, with
the scenario and outcome) alongside this document's revision history or in
a linked private note if it contains sensitive detail.

### Scenario (exercised 2026-09-22)

**Scenario:** A researcher privately reports, via GitHub Security
Advisories, that a publicly available exploit chain lets an attacker who
can submit a crafted profile to `cds render` escape the intended output
directory and overwrite arbitrary files on the host, and that the exploit
is already circulating on a public forum.

**Walkthrough outcome:**

- **Trigger:** decision tree step 1 (actively exploited vulnerability) —
  public exploit availability plus forum activity is credible evidence of
  active exploitation.
- **Owner:** the maintainer, in the intake-owner role, per
  [Roles](#roles).
- **Deadline:** 24-hour early warning recorded internally; 72-hour
  notification with confirmed severity/impact and, if available, initial
  mitigation guidance; 14-day final report once a fix or documented
  mitigation ships.
- **Evidence:** the original report, the forum post reference, the
  affected `cds render` code path/commit, and a timeline entry for each
  step, stored in a private draft GitHub Security Advisory.
- **Communication channel:** user-facing mitigation guidance (e.g., "do not
  render untrusted profiles until patched") published via a GitHub
  Security Advisory update; a `CHANGELOG.md` entry and public advisory
  after the fix ships, cross-referencing the CVE/advisory ID.

The exercise confirmed the maintainer can identify the correct trigger,
owner, deadlines, evidence to preserve, and communication channel from this
document alone, without additional lookups. No gaps were found; the
single-maintainer out-of-hours limitation noted above remains open and is
tracked as an accepted risk pending additional maintainers.

## Cross-references

- [`SECURITY.md`](../SECURITY.md) — coordinated vulnerability disclosure
  policy, monitored contact, and response targets for all reports.
- [`RELEASE.md`](../RELEASE.md) — release process; a security fix released
  under this runbook still follows the Release Checklist and Release Notes
  Template.
- [`docs/cra-scope-decision.md`](cra-scope-decision.md) — current CRA scope
  determination and reassessment triggers.
- [`docs/image-scanning.md`](image-scanning.md) — routine (non-incident)
  vulnerability scanning and remediation SLA for runtime images.
- [`docs/support-policy.md`](support-policy.md) and
  [`SUPPORT.md`](../SUPPORT.md) — supported versions and evidence retention
  period.
