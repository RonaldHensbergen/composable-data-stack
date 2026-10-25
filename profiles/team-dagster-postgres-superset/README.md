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
  policies per workload class, and the documented runtime-policy waiver
  for the Dagster/Superset images.

## Quick start (shared host)

```bash
cd <project root>            # where modules/, profiles/, workdirs/ live
mkdir -p traefik/dynamic certs/traefik
cds init team-dagster-postgres-superset --environment prod
# edit .env: replace every change-me with a real, unique value
cds render team-dagster-postgres-superset --environment prod
docker compose up -d
```

`cds init --environment prod` matters: the overlay declares an extra
required secret (`CDS_BACKUP_ENCRYPTION_KEY`) that a base-only `.env`
lacks, so `cds render --environment prod` fails closed until it exists.

Each team member reaches the stack through the edge:

- Dagster: `https://dagster.cds.local` (hosts-file entry pointing at the
  shared host, or your local DNS)
- Superset: `https://superset.cds.local`
- Traefik dashboard: `http://127.0.0.1:8080` on the host itself

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
- **Superset is multi-user:** the bootstrap admin account is provisioned
  from `CDS_SUPERSET_ADMIN_PASSWORD`; that admin creates individual
  accounts and roles in Settings → Users. Access control is per-user, not
  SSO.
- **Dagster has no authentication.** Anyone who can reach port 443 can
  view and trigger runs. Until proxy-level SSO exists (below), protect
  the edge: add a `basicAuth` middleware to the dagster router in the
  dynamic config (generate entries with `htpasswd -nB <user>`; do not
  commit the hashes) or restrict port 443 to the team network (VPN,
  firewall allowlist).
- **Ops:** one `.env` (written `0600` by `cds init`) on the host; state
  lives in named Docker volumes (`cds state` to inspect). Back up the
  postgres volume until a backup-target provider module exists (see
  `docs/backup-restore.md`), e.g. a nightly
  `docker compose exec postgres pg_dumpall`.

## Deliberately not included

- **Keycloak/SSO:** the keycloak module has no realm configuration yet
  (#680) and no `oidc-provider` contract (#681), and neither the superset
  nor the dagster module exposes an OAuth/OIDC config surface. Running an
  IdP nothing authenticates against adds cost without SSO. The realistic
  path is Traefik `forwardAuth` against Keycloak for Dagster (which will
  never have native OIDC) plus a Superset OAuth config surface.
- **Vault:** env-file secrets with `required: true` (missing value fails
  closed) are the supported simple path for a single Docker host; the
  Vault dev container in
  [`local-dagster-postgres-superset-vault`](../local-dagster-postgres-superset-vault)
  is a bootstrap example, not a hardened secrets backend.
- **dbt:** a workflow choice rather than infrastructure; compose it the
  same way [`local-dagster-postgres-superset-dbt`](../local-dagster-postgres-superset-dbt)
  does when your team transforms with dbt.
