# Accessibility

Composable Data Stack (CDS) is committed to being usable by as many people as
possible, including people with disabilities. This document describes our
accessibility commitment, the surfaces it covers, supported environments,
known limitations, and how to report a barrier.

## Our Commitment

CDS is primarily a command-line tool plus the documentation in this
repository. For the surfaces we own, we commit to:

- Plain, structured text output from the `cds` CLI. Color is only emitted when
  stdout is a terminal, so piped or redirected output is plain text that
  screen readers and braille displays can consume. The interactive `up` and
  `state` commands accept `--no-color` to disable colored labels even on a
  color-capable terminal, and their live state view redraws in place only on a
  terminal — when output is redirected, each update is printed as plain text.
- Machine-readable output where it exists (for example `cds plan --json`), so
  output can be processed by tooling instead of read visually.
- Documentation written in clear language, using proper Markdown headings,
  lists, and tables, so it renders accessibly on GitHub and in text form.

CDS does not currently host its own web application. The web UIs you reach
through a running profile — for example Apache Superset, Dagster, Airflow,
Keycloak, or Marquez, depending on the modules in your profile — are
third-party open-source products. Their accessibility is governed by the
upstream projects, not by CDS, and we cannot make conformance claims on their
behalf. Check the upstream project's own accessibility statement for the
module you use.

For CDS-owned content rendered on the web (this repository and its docs on
GitHub), we aim to conform to WCAG 2.2 Level AA. No formal third-party audit
has been commissioned yet; see Known Limitations below.

## Supported Environments

CDS runs on the host platforms and tooling listed in
[`docs/support-policy.md`](docs/support-policy.md): Linux (Ubuntu 22.04+,
primary), macOS (12+), and Windows 10/11 (via Docker Desktop with WSL 2), with
Python 3.14+ and Docker Compose v2+. The CLI accessibility behavior described
above applies equally on all supported platforms.

## Known Limitations

- The `NO_COLOR` environment-variable convention (<https://no-color.org/>) is
  not honored; use the `--no-color` flag on `cds up` / `cds state`, or pipe
  the output (color is terminal-only, so piped output is already plain).
- No formal accessibility audit (WCAG evaluation or VPAT) has been performed
  yet for the CLI or for this documentation.
- The accessibility of bundled third-party web UIs varies and is not audited
  by CDS; conformance is the upstream project's responsibility.
- Documentation is currently available in English only.

## Reporting A Barrier

If you encounter an accessibility barrier in CDS — in the CLI, this
documentation, or a rendered artifact — please
[open a GitHub issue](https://github.com/RonaldHensbergen/composable-data-stack/issues/new/choose)
and include:

- What you were trying to do, and where the barrier is (command, file, or URL)
- What happened versus what you expected
- Reproduction steps
- Your environment: OS, terminal, assistive technology (for example a screen
  reader), and versions

Maintainers aim to give an initial response within 72 hours, consistent with
[`SUPPORT.md`](SUPPORT.md). If your report contains sensitive information, use
the private channels described in [`SECURITY.md`](SECURITY.md) instead of a
public issue.

## Review

This statement is reviewed whenever the scope of CDS changes — for example if
CDS starts hosting its own web UI — and otherwise at least once per release
cycle. Changes are recorded in [`CHANGELOG.md`](CHANGELOG.md).
