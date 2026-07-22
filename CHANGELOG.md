# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog.

## [Unreleased]

### Added

- `cds up` now supports `--no-build` to skip Docker Compose image builds when images are already available.

### Changed

- `cds up` now runs `docker compose build` before `docker compose up` by default.
- CI now measures test coverage on the Ubuntu leg of the test matrix and fails the build if `cli/` coverage drops below 65%.
- Superset initialization now synchronizes roles and permissions after migrations and admin provisioning, preventing authenticated API requests from failing with `403` responses.

### Security

- `images/dagster/Dockerfile` now pins its base image to a digest (not just the `python:3.14-slim` tag), runs as a non-root `dagster` user, contains only required application files, installs PostgreSQL support only for PostgreSQL builds, and no longer installs packages at startup. Dagster services now drop all Linux capabilities, prevent privilege escalation, use read-only root filesystems, and no longer expose the unused Docker socket.
- The Superset image now pins `apache/superset:6.1.0` to a digest and installs its entrypoint with immutable permissions. Superset services also drop all Linux capabilities, prevent privilege escalation, and use a read-only root filesystem with restricted temporary filesystems.
- PostgreSQL, KeyDB, and Vault images are now digest-pinned and run as their upstream non-root users with read-only roots, no Linux capabilities, no privilege escalation, bounded process counts, restricted temporary filesystems, and host ports bound to loopback only.
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
