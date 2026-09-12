# Keycloak

Keycloak identity/SSO provider running in development mode, backed by a
consumed sql-database contract for its own metadata store.

## Purpose

Provides a local Keycloak container for profiles that need centralized
authentication/SSO across multiple services. Binds to any module that
provides a `sql-database` contract (e.g. `modules/warehouse/postgres`) for
its own metadata storage, instead of hardcoding a database service name.

## Known limitations

- Runs with the `start-dev` command, which relaxes hostname/TLS
  requirements not suitable for production
- Declares `productionSuitable: false`, do not use in production
- No `identity-broker` contract exists yet for other modules to consume
  Keycloak-issued tokens/SSO directly; this module currently only exposes
  itself as an `http-service` for a future reverse-proxy/ingress module
- No realm configuration: the module relies on Keycloak's implicit default
  `master` realm; there is no way to declare a dedicated application realm
  yet (tracked in #680)
- No `oidc-provider` shared contract yet for other modules to `consume`
  Keycloak for authentication delegation (per #370's original design);
  Keycloak can be composed into a profile today but nothing can bind to it
  as an identity provider yet (tracked in #681)
- `KC_DB` is exposed as `metadataDatabase.driver` (default `postgres`) so
  the vendor value can be set explicitly if a non-Postgres `sql-database`
  provider is ever bound here; there is still no automatic validation that
  the chosen driver actually matches the bound provider's wire protocol
  (tracked in #682)

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
- The healthcheck probes `/health/ready` on the management port (9000)
  over a raw TCP connection, since the upstream image ships without
  `curl`/`wget`
