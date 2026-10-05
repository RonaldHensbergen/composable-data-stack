# Local Haven/Common Ground Reference Stack

Reference profile combining existing CDS modules in a shape aligned with
Common Ground's [Haven](https://haven.commonground.nl/) reference
architecture: an identity provider (Keycloak), a TLS-terminating reverse
proxy (Traefik), and a `sql-database` provider (Postgres). See
[docs/common-ground-alignment.md](../../docs/common-ground-alignment.md)
for the concept mapping between CDS and Haven/Common Ground principles.

## Status

Experimental (tracked in [docs/roadmap.md](../../docs/roadmap.md) under
Experimental Components) until the Keycloak module gains realm
configuration (#680) and an `oidc-provider` contract (#681). No new
Common Ground-specific modules were introduced for this profile; it only
wires together modules CDS already has.

## Purpose

- `postgres` provides the `sql-database` contract, holding Keycloak's own
  metadata store (an `identity` database distinct from any
  application-data database another profile might add).
- `keycloak` consumes that `sql-database` contract and runs in development
  mode (`start-dev`), providing an `http-service` contract on port 8080.
- `traefik` fronts the stack with TLS 1.2+ enforcement and a file-provider
  dynamic configuration ([`traefik/dynamic/keycloak.yml`](../../traefik/dynamic/keycloak.yml))
  routing its `websecure` entrypoint to the Keycloak container over the
  profile's Docker network. CDS has no automated contract-based wiring
  between `reverse-proxy` and `http-service` contracts yet, so this routing
  is hand-authored rather than resolved from the profile's contract graph.

## Prerequisites

- Python 3.14+ with the CDS CLI installed, see [docs/installation.md](../../docs/installation.md)
- Docker Engine with the Compose v2 plugin
- The `init-db.sh` script in this directory, it is bind-mounted into
  postgres and executed on first boot
- `traefik/dynamic/` and `certs/traefik/` at the project root, bind-mounted
  into the `traefik` container (see [modules/integration/traefik](../../modules/integration/traefik));
  `certs/traefik/` may stay empty, Traefik falls back to its own
  auto-generated self-signed certificate

## Required secrets

Secrets come from environment variables declared in `profile.yaml`:

| Secret alias | Environment variable |
| --- | --- |
| `postgres_superuser_password` | `CDS_POSTGRES_SUPERUSER_PASSWORD` |
| `identity_db_password` | `CDS_IDENTITY_DB_PASSWORD` |
| `keycloak_admin_password` | `CDS_KEYCLOAK_ADMIN_PASSWORD` |

Non-secret config values also come from env: `CDS_IDENTITY_DB_NAME`,
`CDS_IDENTITY_DB_USER`. Run `cds init local-haven-reference` to generate
the `.env` file from the profile secret definitions (default
`<project-root>/.env`).

## Startup order

- `postgres` starts first, it has no dependencies
- `keycloak` depends on `postgres`
- `traefik` depends on `keycloak`

Start the stack with `cds up local-haven-reference` (add `-d` to detach).
Reach Keycloak directly at `http://localhost:8081`, or through Traefik's
TLS entrypoint at `https://localhost` (self-signed certificate, expect a
browser warning). Traefik's own dashboard is on `http://127.0.0.1:8080`.

## Teardown

- Stop the stack with `docker compose down` in the directory where the
  compose file was rendered, the default is `<project-root>/docker-compose.yml`
- Add `-v` to also delete the named volume (`postgres-data`) and rebuild
  the database from scratch

## Operational notes

- Keycloak's admin console and `KC_HEALTH_ENABLED` health endpoint are
  published directly on the host (`httpPort: 8081`) in this profile for
  local development; front it exclusively through Traefik before exposing
  it beyond a trusted network (see `modules/identity/keycloak/module.yaml`).
- This profile has no `environments/` overlays yet.
