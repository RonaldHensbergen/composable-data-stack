# Security Policy

See [`docs/cra-scope-decision.md`](docs/cra-scope-decision.md) for the
project's current Cyber Resilience Act scope and accountable-role
determination, and the conditions under which it must be reassessed.

## Supported Versions

Only the most recently published tagged release (`vX.Y.Z`) is supported.
An untagged commit on `main` is never itself a supported release. See
[`docs/security-support-policy.md`](docs/security-support-policy.md) for
the full support-period determination, how long a release stays supported
after being superseded, severity-based fix delivery targets, the emergency
release path, and where security update history is retained.

## Reporting A Vulnerability

Please do not open public issues for security vulnerabilities.

Use one of these private channels:

1. GitHub Security Advisories for this repository
2. Private contact to maintainers (if provided in project profile)

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

## Secret Handling Expectations

- Do not commit real credentials to the repository.
- Generated runtime artifacts should use environment variable placeholders for secrets.
- If secret leakage is suspected, rotate affected credentials immediately.
