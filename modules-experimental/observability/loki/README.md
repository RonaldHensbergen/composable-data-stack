# Loki log aggregation (experimental)

Local and development log aggregation using Grafana Loki as the log store,
with Vector as a host-loopback plain-JSON ingestion gateway. Provides the
shared `log-sink` contract over **Loki's native OTLP/HTTP endpoint**
(`POST /otlp/v1/logs` on the `loki-storage` service, port 3100, served by
default since Loki 3.0) for modules and tools that ship OTLP logs — including
the `otel-collector` module, whose `backends.logs` forwards to this endpoint
via the contract's `connectionUri` + `ingestPath` (issue #1076).

## Purpose

Use this module when a profile needs a queryable log store without adding a
metrics or dashboard stack. The provided `log-sink` contract advertises
`protocol: otlp-http`, `ingestPath: /otlp/v1/logs`, and
`connectionUri: http://<loki-storage>:3100`, so any OTLP/HTTP shipper (an OTel
SDK pointed at the binding, or an `otel-collector` forwarding pipeline) can
push logs end-to-end.

## Endpoints

| Endpoint | Where | Purpose |
| --- | --- | --- |
| `POST /otlp/v1/logs` (OTLP/HTTP) | `loki-storage:3100`, in-network via the `log-sink` contract | contract-advertised ingest for OTLP shippers |
| `POST /logs` (one JSON object per request) | Vector gateway, published as `127.0.0.1:<ingestPort>` | host-loopback convenience for local dev tools |

Both paths land in the same Loki store with separate retention tiers: records
carrying the `tier="structured"` label get `structuredRetentionDays`,
everything else gets `rawRetentionDays`. On the JSON path the Vector gateway
classifies records containing the required string-valued `service`,
`profile`, `environment`, `timestamp`, and `severity` fields as structured.
On the OTLP path, Loki promotes the `service.name`,
`deployment.environment.name`, and `tier` resource attributes to index
labels (see `loki.yaml`); set `tier="structured"` as a resource attribute to
land in the structured tier. All other OTLP attributes are kept as structured
metadata on each log line.

## Known limitations

- The endpoints accept unauthenticated traffic and are intended only for
  trusted local/development profiles. Only the Vector gateway's port is
  published on the host, bound to `127.0.0.1`; services on the shared Compose
  network reach the OTLP endpoint through the `log-sink` contract.
- The module does not mount the Docker socket or automatically tail container
  output. Configure a shipper or application to ship records to the
  `log-sink` contract endpoint.
- The JSON gateway classifies records but does not validate the full
  `structured-event` schema.
- This is a single-node filesystem-backed Loki instance without redundancy,
  authentication, TLS, or a query UI. Pair it with a secured reverse proxy
  and a separately configured dashboard for deployments beyond local use.
- On the snap-confined Docker runtime used in some agent sandboxes, both
  services fail to start under `security_opt: no-new-privileges:true` with
  `exec ...: operation not permitted` for any image; that is a runtime
  artifact, not an image defect — verify on a non-snap Docker host before
  changing this module's hardening (see
  [docs/runtime-confinement.md](../../../docs/runtime-confinement.md#scope-and-caveats),
  issue #1155).

## Upstream documentation

- [Grafana Loki documentation](https://grafana.com/docs/loki/latest/)
- [Vector documentation](https://vector.dev/docs/)

## Configuration notes

`rawRetentionDays` defaults to 7 and `structuredRetentionDays` defaults to
90. Loki enforces the raw period by default and overrides it for the
`tier="structured"` stream. Logs are persisted in the `loki-data` named
volume; back it up and test restores independently. `ingestPort` changes only
the host loopback publish port of the Vector JSON gateway; the `log-sink`
contract always advertises the in-network OTLP listener on the `loki-storage`
service (`otlp-http`, port `3100`, `/otlp/v1/logs`).
