# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog.

## [Unreleased]

### Added

- Profile, module, and shared-contract JSON schemas are now loaded and enforced at runtime: profile shape validation is backed by `cli/resources/profile.schema.json` (E010), loaded module definitions by `cli/resources/module.schema.json` (E021), and standalone contract files in `shared/contracts/` can be checked against `cli/resources/contract.schema.json` via `cli.validator.validate_contract_file()` (#413).

### Changed

- Schema-backed validation is stricter than the previous hand-written checks. Profiles must now carry `metadata.environment`, `spec.runtime`, and per-module `version`/`enabled`; loaded modules must satisfy `module.schema.json`; profiles that previously validated may now fail (#413).

### Removed

- Deleted the unused rule-set entries CDS-SEC-050/051/052/053/054 from `cli/resources/rule-set.json` (#354). Image policy enforcement lives solely in `cli/image_verification.py` (findings are still reported as CDS-SEC-050/051/052), and CDS-SEC-053 had no enforcement anywhere.
- Disabled the CDS-SEC-006 and CDS-SEC-032 rule-set entries (`enabled: false`) so no `scope: ["none"]` rule appears active; #356 and #357 track the remaining work on those rules.

### Fixed

- `cds security --verify-images` no longer plans and renders the profile a second time for image verification; it reuses the compose the security scan already rendered (#336).
- `cds-dagster` and `cds-dbt` now pin the rebuilt `python:3.14-slim` base digest (`a7fb1e63...`) and add `.trivyignore` exceptions (exp 2026-11-15) for CVE-2026-53615 (`util-linux` libblkid, #455, #457) and the four sqlparse advisories from 2026-08-17 (CVE-2026-54284/59893/59894/71491). Neither fix is shippable yet: `2.41.5-0+deb13u1` has no base digest carrying it, and dbt-core 1.12.2 pins `sqlparse<0.6.0`. Expired exceptions fail the daily scan again.
- Corrected stale documentation that referenced the deleted CDS-SEC-050/051/052/053/054 rule-set entries: `docs/image-signing.md`, `docs/threat-model.md`, `docs/vm-postgres-odbc-access.md`, and the `cli/image_verification.py` module docstring.
- Added a regression guard for #354: the image-policy finding IDs CDS-SEC-050/051/052 must still be emitted by `cli/image_verification.py` for a non-compliant Compose fixture, and none of the deleted IDs may reappear in the rule set.
- Added a regression guard for #355: no `scope: ["none"]` security rule may be enabled in the bundled rule set.
- Added a regression guard for #397: a `CDS_DB_PASSWORD` reference with a fallback value outside CDS-SEC-040's literal list is still caught by preflight insecure-default detection.
- `image-security-scan.yml`'s scheduled scan step never passed a `trivyignores` input to `trivy-action`, and `publish-images.yml`'s pre-push gate passed it under the wrong input name (`ignorefile` instead of `trivyignores`); both silently ignored `.trivyignore`, so the approved CVE-2026-53612/53613/53614 exceptions added in #484 never took effect and the daily scan kept refiling duplicate issues (#481, #482, #483, #485, #486, #487).

## [0.4.0] - 2026-08-11

### Added

- Provider-neutral observability architecture foundations for #174: `docs/observability.md`, the shared `log-sink` contract, the structured-event schema, and optional profile `spec.observability.logShipping` validation.
- `publish-images.yml` now runs a trivy HIGH/CRITICAL vulnerability gate on
  the locally built images before pushing or signing, so a CVE disclosed
  after the PR-time scan blocks publication to GitHub Container Registry and
  Docker Hub (#274).

## [0.4.0-beta-1] - 2026-07-31

### Added

- `ruff` (`select = ["UP"]`) now lints for pyupgrade/deprecation issues in CI and pre-commit, alongside a dedicated test-suite deprecation gate (`scripts/run_tests_with_deprecation_gate.py`) that promotes `DeprecationWarning`s raised from repo-owned `cli`/`test_*` modules to errors while leaving third-party dependency warnings alone.
- `renovate.json`'s custom managers now validate with `renovate-config-validator --strict` in CI, and migrate `fileMatch`/`matchPackagePatterns` to the current `managerFilePatterns`/`matchPackageNames` syntax.
- `cli/planner.py`'s `build_plan()` now reports a diagnostic instead of crashing when `spec.modules` is a non-list scalar.

### Fixed

- CDS-SEC-031 no longer flags the conventional, `.gitignore`-covered project-root `.env` file as a security finding; it still flags nested/non-root `.env` files.
- Removed the redundant `wheel` entry from `build-system.requires` (modern `setuptools` builds wheels natively via PEP 517/660).
- Corrected stale documentation: `README.md`'s description of `cds test`'s security-stage gating, `docs/support-policy.md`'s minimum Python version (3.14+, not 3.11+), and the bug report issue template's Python version placeholder.

## [0.3.0-beta-1] - 2026-07-27

### Added

- `cds up` now supports `--no-build` to skip Docker Compose image builds when images are already available.
- Python distributions now include the CLI security rules and complete PyPI metadata.
- CI builds and smoke-tests the wheel outside the source tree, and maintainers can publish validated artifacts to TestPyPI through trusted publishing.
- `cds --version`/`-v` now reports the installed CLI version.
- The release workflow now verifies that a pushed `vX.Y.Z` tag matches the version declared in `pyproject.toml` before publishing a GitHub release, failing fast on drift.

### Changed

- `cds up` now runs `docker compose build` before `docker compose up` by default.
- Security validation loads its default rules from package resources instead of requiring a repository-root `security/` directory.
- CI now measures test coverage on the Ubuntu leg of the test matrix and fails the build if `cli/` coverage drops below 65%.
- Superset initialization now synchronizes roles and permissions after migrations and admin provisioning, preventing authenticated API requests from failing with `403` responses.
- Dagster services now communicate with the user-code gRPC server through a shared Unix-domain socket volume instead of an internal TCP port.

### Security

- The Superset image now upgrades its inherited `uv` and `uvx` binaries to `uv 0.11.26`, which embeds the patched `quinn-proto 0.11.15`.
- `images/dagster/Dockerfile` now pins its base image to a digest (not just the `python:3.14-slim` tag), runs as a non-root `dagster` user, contains only required application files, installs PostgreSQL support only for PostgreSQL builds, and no longer installs packages at startup. Dagster services now drop all Linux capabilities, prevent privilege escalation, use read-only root filesystems, and no longer expose the unused Docker socket.
- The Superset image now pins `apache/superset:6.1.0` to a digest and installs its entrypoint with immutable permissions. Superset services also drop all Linux capabilities, prevent privilege escalation, and use a read-only root filesystem with restricted temporary filesystems.
- PostgreSQL, KeyDB, and Vault images are now digest-pinned and run as their upstream non-root users with read-only roots, no Linux capabilities, no privilege escalation, bounded process counts, restricted temporary filesystems, and host ports bound to loopback only.
- Dagster now uses its writable application home for user state, disables telemetry, and performs lightweight bounded TCP health probes so hardened services start reliably without accumulating stuck healthcheck processes.
- Dagster user-code definitions can now be supplied through a configurable read-only bind mount, allowing code overrides before container startup while retaining an immutable root filesystem.
- Module `source:` paths are now required to resolve inside an allowed `modules/`- or `modules-experimental/`-rooted directory before the module file is read, for both `cds validate`/`cds plan`/`cds render` (`cli/loader.py`'s `resolve_module_file`) and the `CDS_MODULE_PATH` override path. Fixes [GHSA-jgg5-4wcm-fvxq](https://github.com/RonaldHensbergen/composable-data-stack/security/advisories/GHSA-jgg5-4wcm-fvxq): a profile's `source:` field could previously traverse outside the intended module tree (e.g. `source: "../../../../../../tmp/outside_zone"`) and have its content read and embedded into the rendered `docker-compose.yaml`. Out-of-bounds sources now fail with `E022`.

## [0.1.1] - 2026-06-21

### Added

- Default render output path to project-root docker-compose.yml when no output is provided.
- Open-source project governance and support docs.
- Troubleshooting guidance in the README for common CLI validation, secret, and contract-binding errors.
- Added `docs/os-compatibility.md` with OS compatibility analysis and recommendations.
- Improved the bug report template with severity and minimal repro fields.

### Changed

- Compose rendering now preserves secrets as runtime environment placeholders instead of embedding resolved values.
- Plan secret mapping now stores env variable names rather than secret values.
- Renderer build-context path rewriting now preserves portable relative paths for nested compose output directories.

### Tests

- Added renderer regression coverage to ensure generated Docker Compose output never includes raw secret values.

### Security

- Added explicit security reporting process and secret-handling guidance.
