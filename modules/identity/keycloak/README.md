# Keycloak

Keycloak identity/SSO provider running in development mode, backed by a
consumed sql-database contract for its own metadata store.

## Purpose

Provides a local Keycloak container for profiles that need centralized
authentication/SSO across multiple services. Binds to any module that
provides a `sql-database` contract (e.g. `modules/warehouse/postgres`) for
its own metadata storage, instead of hardcoding a database service name.

## Known limitations

- Runs in development mode (`start-dev`) by default, which relaxes
  hostname/TLS requirements not suitable for production;
  `config.webServer.mode: production` switches to Keycloak production
  mode (`kc.sh start --http-enabled=true`) — the default stays
  `development` until the module is production-suitable
  (see [`docs/serving-modes.md`](../../docs/serving-modes.md), issue #216)
- Declares `productionSuitable: false`, do not use in production
- The default realm is Keycloak's built-in `master`; set `realm` to a
  dedicated application realm to have Keycloak import it on startup.
  Existing realms are not overwritten. Configure clients and users through
  the Keycloak admin console or a separate realm provisioning workflow.
- `publicUrl` is the canonical OIDC issuer base URL. It must resolve to the
  same Keycloak issuer from the user's browser and from consuming service
  containers. Set it to the TLS reverse-proxy URL in deployed profiles;
  `localhost` is only suitable when all OIDC participants share that host
  network namespace.
- `KC_DB` is exposed as `metadataDatabase.driver` (default `postgres`) so
  the vendor value can be set explicitly if a non-Postgres `sql-database`
  provider is ever bound here; there is still no automatic validation that
  the chosen driver actually matches the bound provider's wire protocol
  (tracked in #682)
- `httpPort` is bound to `127.0.0.1`; use a TLS-terminating
  reverse-proxy/ingress module for browser access beyond the local host.

## Upstream documentation

- [Keycloak server documentation](https://www.keycloak.org/documentation)
- [Running Keycloak in a container](https://www.keycloak.org/server/containers)

## Configuration notes

- `metadataDatabase.contractRef` must point at a module instance providing
  `sql-database` (e.g. `postgres.identity-database`); the module reads
  `host`/`port`/`database`/`username`/`password` from that binding via
  `KC_DB_URL_HOST`/`KC_DB_URL_PORT`/`KC_DB_URL_DATABASE`/`KC_DB_USERNAME`/`KC_DB_PASSWORD`
- `metadataDatabase.driver` selects the `KC_DB` vendor value
  (`postgres`/`mysql`/`mariadb`/`mssql`/`oracle`); defaults to `postgres`,
  matching the only `sql-database` provider in this repository today
- `adminUser.passwordFrom` must reference a profile secret; there is no
  default admin password
- `realm` selects the issuer realm. Non-master realms are imported on
  startup; the built-in `master` realm is not re-created.
- `publicUrl` has no trailing slash; the `oidc-provider` contract advertises
  `${publicUrl}/realms/${realm}` as the issuer.
- The healthcheck probes `/health/ready` on the management port (9000)
  over a raw TCP connection, since the upstream image ships without
  `curl`/`wget`

## OIDC clients

The module provides both `http-service` and `oidc-provider` contracts.
Consumers bind to `<keycloak-module-id>.oidc-provider`. Client credentials
belong to each consumer, not the identity provider. Create an OpenID Connect
client in the configured realm, enable Standard Flow, and register the
consumer's exact callback URL. Keep the client secret in the profile's secret
values and refer to it from the consumer config; it remains a `${CDS_*}`
placeholder in generated plans and Compose files.
