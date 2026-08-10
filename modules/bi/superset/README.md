# Superset

Apache Superset BI service with a metadata database binding.

## Purpose

Provides the Superset web UI for exploring and visualizing data from a
connected analytics database. Profiles that need BI capabilities include
this module and wire a `sql-database` binding to `metadataDatabase`.

## Known limitations

- `metadataDatabase` is mandatory, the module does not start without a `sql-database` binding
- The `superset-init` container bootstraps the metadata database and the admin user on first start and then exits, the main container waits for it via `service_completed_successfully`
- The healthcheck has a 180 second start period, first startup takes a while before the web UI reports healthy
- The image is built locally from `images/superset/base/Dockerfile`, startup includes a Docker build step

## Upstream documentation

- [Apache Superset documentation](https://superset.apache.org/docs/intro)

## Configuration notes

- `secretKeyFrom` and `adminUser.passwordFrom` reference `secrets.*` values resolved from the profile
- The optional `cacheService` binding wires Redis-backed caching into Superset when connected to a `cache-service` contract
