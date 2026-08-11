# Dagster

Dagster orchestration service aligned with the Dagster OSS Docker Compose
pattern (webserver + daemon), with pluggable storage bindings.

## Purpose

Provides a code server, webserver, and daemon for profiles that need
orchestration. The webserver and daemon share the user-code gRPC socket
and the configured storage bindings.

## Known limitations

- `runStorage`, `eventLogStorage`, and `scheduleStorage` are mandatory bindings in the config schema
- `definitionsFile.hostPath` is bind-mounted read-only into the user-code container, the host file must exist before `cds up`
- `sharedData.hostPath` is bind-mounted into the containers, the host directory must exist before `cds up`
- The task daemon runs only when `daemon.enabled` is true, schedules and sensors need it

## Upstream documentation

- [Dagster documentation](https://docs.dagster.io/)

## Configuration notes

- `storage.backend` selects the database driver for the local image build and must match the database behind the storage bindings
- The webserver and daemon wait for the user-code healthcheck over a Unix socket, start period is 60 seconds
