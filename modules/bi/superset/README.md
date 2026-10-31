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
- OIDC login is optional and requires a separately provisioned client in the
  bound identity provider's realm.

## Web serving modes (issue #216)

The module serves HTTP in two modes, selected with `config.webServer.mode`
(see [`docs/serving-modes.md`](../../docs/serving-modes.md)):

- `production` (default): gunicorn with `webServer.workers` workers
  (default 4), `webServer.timeoutSeconds` per-request timeout (default 60),
  gthread worker class, and graceful shutdown on SIGTERM. The app is
  preloaded once in the gunicorn master before workers fork, so a web
  process that starts before `superset-init` (out-of-order start, restart
  against an empty database) cannot race schema creation between workers.
  The `superset` service passes `CDS_WEBSERVER_MODE`/`CDS_WEBSERVER_WORKERS`/`CDS_WEBSERVER_TIMEOUT`
  to the image entrypoint.
- `development`: Superset's built-in development server (`superset run`),
  for local debugging only; validation rejects it in production profiles
  (E140).

Health semantics are identical in both modes: `/health` on container port
8088, as rendered by the module and baked into the image `HEALTHCHECK`.

## Upstream documentation

- [Apache Superset documentation](https://superset.apache.org/docs/intro)

## Configuration notes

- `secretKeyFrom` and `adminUser.passwordFrom` reference `secrets.*` values resolved from the profile
- The optional `cacheService` binding wires Redis-backed caching into Superset when connected to a `cache-service` contract

## OIDC login

Set `identityProvider.contractRef` to a provider's `oidc-provider` contract,
provide the registered `clientId`, and reference the client secret with
`clientSecretFrom: secrets.<alias>`. The `issuerUrl` is taken from the bound
contract; Superset uses its OpenID Connect discovery document and requests
the `openid`, `email`, and `profile` scopes. Without this configuration,
Superset retains its built-in database authentication. OIDC is enabled only
when the issuer, client ID, and secret are all present; partial runtime
configuration fails during Superset startup.

Newly authenticated users are registered with the least-privileged `Gamma`
role. Register the exact Superset callback URL
`<public-superset-url>/oauth-authorized/keycloak` in the identity-provider
client, enable Standard Flow, and ensure the issuer URL is reachable and
consistent for both browsers and Superset containers. Client secrets remain
runtime `${CDS_*}` placeholders in rendered Compose output.
