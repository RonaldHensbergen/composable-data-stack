# Loki log aggregation (experimental)

Local and development log aggregation using Vector as an HTTP ingestion
gateway and Grafana Loki as the log store. Provides the shared `log-sink`
contract for modules and tools that ship raw records or structured runtime
events.

## Purpose

Use this module when a profile needs a queryable log store without adding a
metrics or dashboard stack. Vector accepts JSON records at the contract's
`/logs` endpoint, classifies records containing the required structured-event
fields, and forwards them to Loki with a retention-tier label. Loki applies
the configured retention period to each tier.

## Known limitations

- The endpoint accepts unauthenticated HTTP and is intended only for trusted
  local/development profiles. Its host port is bound to `127.0.0.1`; services
  on the shared Compose network can also reach it.
- The module does not mount the Docker socket or automatically tail container
  output. Configure a shipper or application to POST JSON records to the
  `log-sink` contract endpoint.
- Input is JSON objects, one per request. Structured records must include
  string-valued `service`, `profile`, `environment`, `timestamp`, and
  `severity` fields; other records receive the shorter raw-log retention.
  The gateway classifies records but does not validate the full
  `structured-event` schema.
- This is a single-node filesystem-backed Loki instance without redundancy,
  authentication, TLS, or a query UI. Pair it with a secured reverse proxy
  and a separately configured dashboard for deployments beyond local use.

## Upstream documentation

- [Grafana Loki documentation](https://grafana.com/docs/loki/latest/)
- [Vector documentation](https://vector.dev/docs/)

## Configuration notes

`rawRetentionDays` defaults to 7 and `structuredRetentionDays` defaults to
90. Loki enforces the raw period by default and overrides it for the
`tier="structured"` stream. Logs are persisted in the `loki-data` named
volume; back it up and test restores independently. `ingestPort` changes only
the host loopback publish port; the `log-sink` contract always advertises the
Vector listener's in-network port, `8686`.
