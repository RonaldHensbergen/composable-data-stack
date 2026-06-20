# Security Policy

## Supported Versions

This project currently supports the latest code on the main branch.

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
