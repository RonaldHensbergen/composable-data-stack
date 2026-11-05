# Grafana dashboards (experimental)

Grafana dashboarding/observability UI, providing an `http-service` contract for
the web UI and optionally provisioning datasources from bound contracts.
Closes #369.

## Purpose

Use this module when a profile needs a first-class way to visualize metrics,
logs, and warehouse data — the same role Superset plays for BI. The module
provides `http-service` so a reverse proxy can route to it, consumes an
optional `sql-database` binding to provision a Postgres "Warehouse"
datasource, and consumes an optional `metrics-sink` binding to provision a
Prometheus "Metrics" datasource (only for providers advertising
`protocol: prometheus`; push-style OTLP endpoints cannot be queried by
Grafana directly).

## Known limitations

- Single-node, no HA; the admin UI is authenticated by the initial admin
  account only (no OIDC wiring yet — wire the `oidc-provider` contract when
  needed). `productionSuitable: false`.
- The web UI is published on the host loopback only (`127.0.0.1:<webPort>`);
  front it with the reverse-proxy module for anything beyond local access.
- Datasources are provisioned from the mounted files under
  `provisioning/datasources/`, gated per binding with `enabledFrom` equality
  checks (`engine == postgres`, `protocol == prometheus`), so an unbound or
  wrong-protocol contract never leaves a half-configured datasource.
  Dashboards are not provisioned from files yet; use the Grafana UI/API.
- Compose render target only — no `kubernetes:` implementation block yet
  (same as the other experimental `observability/` modules).
- If the Docker runtime fails to exec the pinned image under
  `no-new-privileges` (the class of failure tracked by #1066 for the
  vector/loki images), remove that one `security_opt` line locally; the
  capability drop and read-only root filesystem stay enabled.

## Upstream documentation

- [Grafana documentation](https://grafana.com/docs/grafana/latest/)
- [Provisioning datasources](https://grafana.com/docs/grafana/latest/administration/provisioning/#datasources)
  (provisioning files expand `${ENV_VAR}` from the container environment)

## Configuration notes

- `adminUser.passwordFrom` is a `secrets.<alias>` reference; the value never
  appears in rendered output, only the `${CDS_*}` runtime placeholder.
- The provisioning files read `CDS_GF_DS_WAREHOUSE_*` / `CDS_GF_DS_METRICS_URL`
  environment variables, which the compose service fills from the bound
  contracts via `${ifNonempty:bindings....,,}` — empty when unbound.
- Analytics/reporting and update checks are disabled in the container
  environment: the module declares no external egress, so Grafana must not
  phone home.
