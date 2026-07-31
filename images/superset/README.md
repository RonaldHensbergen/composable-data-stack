# CDS Superset Image

Apache Superset image built for the [Composable Data Stack (CDS)](https://github.com/RonaldHensbergen/composable-data-stack) `bi/superset` module.

This image is not meant to be run standalone — it is built and wired by CDS profiles via `cds render` / `cds up`, which generate the matching `docker-compose.yml` and Superset configuration for your stack.

## Tags

- `latest` — most recent build from `main`
- `<superset-version>-<yyyymmdd>` — e.g. `6.1.0-20260730`, pinned to the base `apache/superset` version and build date

## What's inside

- Based on the official `apache/superset` image (version pinned in the Dockerfile)
- `uv` for fast, reproducible Python dependency installs
- Extra/updated Python packages layered on top of the base image via `requirements.txt` (crypto, JWT, Arrow/Parquet, packaging fixes, etc.)
- Optional additional packages installable at build time via the `IMAGE_PACKAGES` build arg (e.g. database drivers)
- Custom `superset_config.py` and entrypoint/init scripts wired in by the CDS renderer
- Unused system packaging tools (`pip`, `setuptools`, `wheel`) removed from the image to reduce scanner noise, since the app runs from its own `/app/.venv`

## Ports

- `8088` — Superset web UI (HTTP)

## Source

Dockerfile and supporting files: [`images/superset`](https://github.com/RonaldHensbergen/composable-data-stack/tree/main/images/superset)
Module definition: [`modules/bi/superset/module.yaml`](https://github.com/RonaldHensbergen/composable-data-stack/tree/main/modules/bi/superset)
