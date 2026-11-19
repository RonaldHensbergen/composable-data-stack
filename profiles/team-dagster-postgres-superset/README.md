# Small-Team Dagster + Postgres + Superset

`profiles/team-dagster-postgres-superset` composes the stable
[`local-dagster-postgres-superset`](../local-dagster-postgres-superset)
stack via `extends` and shapes it for a small team on a single shared
Docker host:

- **Traefik edge** (`modules/integration/traefik`): one TLS entrypoint on
  ports 80/443 that routes the Dagster and Superset UIs by hostname
  (hand-authored file-provider routes in
  [`traefik/dynamic/team-dagster-postgres-superset.yml`](../../traefik/dynamic/team-dagster-postgres-superset.yml);
  plain HTTP is redirected to HTTPS. The Traefik dashboard stays on the
  host loopback (`127.0.0.1:8080`).
- **Authenticated cache** (`keydb`): the password comes from a *required*
  secret (`CDS_CACHE_PASSWORD`), so the shared cache rejects
  unauthenticated clients even in development (issue #206). Superset's
  binding picks the password up automatically in its Redis URL.
- **`prod` overlay** (`environments/prod.yaml`): promotes the stack to
  the production security policy — explicit `scram-sha-256` host
  authentication for Postgres, encrypted and restore-verified backup
  policies per workload class (including `identity-metadata`), the
  documented runtime-policy waiver for the Dagster/Superset images, and
  an **identity provider** (issue #1273): Keycloak
  (`modules/identity/keycloak`) with a dedicated database on the postgres
  instance (`postgres.identity-database`), fronted by the edge at
  `https://identity.cds.local` through a contract-generated route
  (`traefik/dynamic/cds-routes-traefik.yml`, written at render time), with
  Superset's `identityProvider` bound to `keycloak.oidc-provider` so the
  BI admin UI delegates authentication to Keycloak's `cds` realm.

## Quick start (shared host)

```bash
cd <project root>            # where modules/, profiles/, workdirs/ live
mkdir -p traefik/dynamic certs/traefik
cds init team-dagster-postgres-superset --environment prod
# edit .env: replace every change-me with a real, unique value
cds render team-dagster-postgres-superset --environment prod
docker compose up -d
```

`cds init --environment prod` matters: the overlay declares extra
required secrets (`CDS_BACKUP_ENCRYPTION_KEY` plus the identity keys
`CDS_IDENTITY_DB_PASSWORD`, `CDS_KEYCLOAK_ADMIN_PASSWORD`, and
`CDS_SUPERSET_OIDC_CLIENT_SECRET`) that a base-only `.env` lacks, so
`cds render --environment prod` fails closed until they exist.

Each team member reaches the stack through the edge (add these hostnames
to your hosts file or team DNS, pointing at the shared host):

- Dagster: `https://dagster.cds.local`
- Superset: `https://superset.cds.local`
- Keycloak (sign-in / admin console): `https://identity.cds.local`
- Traefik dashboard: `http://127.0.0.1:8080` on the host itself

`identity.cds.local` must resolve to the shared host from both browsers
and the service containers, because Keycloak's `publicUrl` is the OIDC
issuer every participant dials. The route itself is generated from the
`keycloak.http-service` contract at render time, so the backend address
is never hardcoded; the Dagster/Superset routes stay in the hand-authored
file noted above.

Edit the hostnames in `traefik/dynamic/team-dagster-postgres-superset.yml`
to taste. Until a certificate is placed under `certs/traefik/`, Traefik
serves its own self-signed certificate; for a real internal certificate,
point `certs/traefik/` at your CA-issued files (or generate local ones
with mkcert) and load them in the dynamic config.

## How a team works on the stack

- **Code is the collaboration plane.** Profiles,
  `workdirs/dagster/definitions.py` (bind-mounted into dagster user-code),
  dbt projects, and the init scripts live in git. Everyone commits; the
  operator pulls on the shared host and re-runs `cds render` + `docker
  compose up -d`. Pipelines are code, not UI artifacts.
- **Superset signs in through Keycloak (prod overlay, issue #1273).** The
  `prod` overlay provisions a `superset` OIDC client in Keycloak's `cds`
  realm (via `oidcClients`, with the client secret from
  `CDS_SUPERSET_OIDC_CLIENT_SECRET`) and binds Superset's
  `identityProvider` to `keycloak.oidc-provider`, so the Superset UI uses
  OAuth against `https://identity.cds.local/realms/cds`. Manage users and
  roles in the Keycloak admin console (`admin` /
  `CDS_KEYCLOAK_ADMIN_PASSWORD`); the local `CDS_SUPERSET_ADMIN_PASSWORD`
  bootstrap account still exists for break-glass. The base (local)
  profile runs without an identity module and Superset keeps its local
  login.
- **Dagster has no native OIDC.** The Dagster webserver has no
  `oidc-provider` consume, so anyone who can reach port 443 can still view
  and trigger runs; the security scan accepts this profile because the
  identity module's presence covers CDS-SEC-081 profile-wide, but that is
  a detection accommodation, not authentication. Protect the dagster
  router with a proxy-level middleware: add a `basicAuth` middleware to the
  dagster router in the dynamic config (generate entries with
  `htpasswd -nB <user>`; do not commit the hashes), restrict port 443 to
  the team network (VPN, firewall allowlist), or put an
  OIDC-forward-auth middleware in front of it.
- **Ops:** one `.env` (written `0600` by `cds init`) on the host; state
  lives in named Docker volumes (`cds state` to inspect). The prod
  overlay's pgBackRest sidecar backs up the postgres volume — including
  the Keycloak identity database — into the encrypted repository volume
  per the declared workload-class policies (see
  `docs/backup-restore.md`).

## Deliberately not included

- **Identity in the base profile:** the local/learning profile deliberately
  runs without Keycloak; SSO is a `prod`-overlay feature (issue #1273).
- **Vault:** env-file secrets with `required: true` (missing value fails
  closed) are the supported simple path for a single Docker host; the
  Vault dev container in
  [`local-dagster-postgres-superset-vault`](../local-dagster-postgres-superset-vault)
  is a bootstrap example, not a hardened secrets backend.
- **dbt:** a workflow choice rather than infrastructure; compose it the
  same way [`local-dagster-postgres-superset-dbt`](../local-dagster-postgres-superset-dbt)
  does when your team transforms with dbt.
