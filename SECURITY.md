# Security Policy

See [`docs/cra-scope-decision.md`](docs/cra-scope-decision.md) for the
project's current Cyber Resilience Act scope and accountable-role
determination, and the conditions under which it must be reassessed.

## Supported Versions

This project currently supports the latest code on the main branch.

## Reporting A Vulnerability

Please do not open public issues for security vulnerabilities.

Use one of these private, monitored channels:

1. GitHub Security Advisories for this repository (primary; checked at
   least once every 24 hours)
2. Private contact to maintainers (if provided in project profile), used
   as a fallback if GitHub Security Advisories is not reachable

The intake owner for both channels is the repository maintainer. A report
is treated as received from the moment it is read, which is also when
response-target and, where applicable, incident-runbook clocks start.

When reporting, include:

- A clear description of the issue
- Affected files or modules
- Reproduction steps or proof of concept
- Impact assessment
- Suggested remediation (if known)

## Response Targets

- Initial acknowledgement: within 72 hours
- Triage decision: within 7 days
- Fix timeline: based on severity and exploitability

## Severity Triage And Embargo

Every report is triaged against the decision tree in
[`docs/cra-incident-runbook.md`](docs/cra-incident-runbook.md#decision-tree):
an actively exploited vulnerability, a severe security incident, an
ordinary vulnerability, or a false positive. Actively exploited
vulnerabilities and severe incidents follow that runbook's 24-hour,
72-hour, 14-day, and one-month deadlines in addition to the response
targets above; the 72-hour items are the same clock, not additive. All
other reports follow only the response targets above.

Reports are kept under embargo (no public issue, PR, commit message, or
advisory) until a fix is available or, for upstream-sourced reports, until
the upstream project's own disclosure timeline permits, whichever the
runbook's [third-party coordination](docs/cra-incident-runbook.md#third-party-and-upstream-coordination)
section requires. Active exploitation can shorten the embargo to allow
earlier user notification of mitigations, even before a full fix ships.

## Advisory Publication And User Notification

Once a fix is available, or immediately if active exploitation requires
earlier mitigation guidance, affected users are notified through a GitHub
Security Advisory, a `CHANGELOG.md` entry, and/or a GitHub Release note,
per [`docs/cra-incident-runbook.md`](docs/cra-incident-runbook.md#user-communication-and-advisory-publication).
The advisory is made public no later than the runbook's final-report
deadlines, referencing a CVE identifier where one applies.

## Incident Runbook

For actively exploited vulnerabilities and severe security incidents (as
opposed to ordinary vulnerability reports handled entirely through the
targets above), see
[`docs/cra-incident-runbook.md`](docs/cra-incident-runbook.md) for the full
decision tree, timeline, evidence-preservation, ENISA Single Reporting
Platform, and third-party-coordination procedures.

## Secret Handling Expectations

- Do not commit real credentials to the repository.
- Generated runtime artifacts should use environment variable placeholders for secrets.
- If secret leakage is suspected, rotate affected credentials immediately.
