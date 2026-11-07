# Keycloak

Keycloak identity/SSO provider with production web serving by default,
backed by a consumed sql-database contract for its own metadata store.

## Purpose

Provides a local Keycloak container for profiles that need centralized
authentication/SSO across multiple services. Binds to any module that
provides a `sql-database` contract (e.g. `modules/warehouse/postgres`) for
its own metadata storage, instead of hardcoding a database service name.

## Known limitations

- `config.webServer.mode: development` runs Keycloak in development mode
  (`start-dev`), which relaxes hostname/TLS requirements; the default is
  `production` (`kc.sh start --http-enabled=true`), and production profiles
  reject an explicit `development` mode (E140), so a production profile can
  never silently run `start-dev` when the mode is left unset
  (see [`docs/serving-modes.md`](../../docs/serving-modes.md), issues #216,
  #974)
- The default realm is Keycloak's built-in `master`; set `realm` to a
  dedicated application realm to have Keycloak import it on startup.
  Existing realms are not overwritten. OIDC clients for consumers are
  provisioned declaratively via `config.oidcClients` (issue #1074);
  users are still managed through the Keycloak admin console or a separate
  realm provisioning workflow.
- `publicUrl` is the canonical OIDC issuer base URL. It must resolve to the
  same Keycloak issuer from the user's browser and from consuming service
  containers. Set it to the TLS reverse-proxy URL in deployed profiles;
  `localhost` is only suitable when all OIDC participants share that host
  network namespace.
- `KC_DB` is exposed as `metadataDatabase.driver` (default `postgres`) so
  the vendor value can be set explicitly if a non-Postgres `sql-database`
  provider is ever bound here; the `metadata-database` consume entry
  declares `engineFrom: spec.config.metadataDatabase.driver`, so validation
  fails closed with E154 when the bound provider's contract does not
  advertise a matching `engine` (issue #682)
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
  matching the only `sql-database` provider in this repository today. The
  bound provider's `sql-database` contract must advertise a matching
  `engine` (see `engineFrom` on the consume entry), otherwise `cds validate`
  fails with E154 (issue #682)
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
Consumers bind to `<keycloak-module-id>.oidc-provider` and reference the
provisioned client through their own config (e.g. Superset's
`identityProvider.clientId`/`clientSecretFrom`).

Declare clients on the provider side with `config.oidcClients` (issue
#1074): each entry takes a `clientId`, the exact `redirectUris` to register
(e.g. `<public-superset-url>/oauth-authorized/keycloak`), optional
`webOrigins`, `standardFlowEnabled` (default true), realm `roles` created
and assigned to the client, and an optional `secretFrom` secret alias. CDS
renders the realm-import JSON from this list and injects it as the
`CDS_OIDC_REALM_IMPORT` environment variable, which the entrypoint writes
to `/opt/keycloak/data/import` on startup (non-master realms only). Client
secrets stay profile secret aliases: they render as `${CDS_*}` runtime
placeholders in the generated Compose file and are interpolated by Compose
at `up` time, so a resolved secret value never lands in plans or rendered
output (CDS-SEC-030/E078). A client without `secretFrom` is created as a
public client. Malformed entries fail rendering with E160.
