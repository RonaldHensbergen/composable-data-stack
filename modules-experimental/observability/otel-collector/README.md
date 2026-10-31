# OpenTelemetry Collector (experimental)

Reference provider of the shared
[`log-sink`](../../../shared/contracts/log-sink.yaml),
[`metrics-sink`](../../../shared/contracts/metrics-sink.yaml), and
[`trace-sink`](../../../shared/contracts/trace-sink.yaml) contracts, accepting
OTLP from instrumented services (issue #777). Marked
`productionSuitable: false`: single-node, unauthenticated OTLP endpoints, and
no storage of its own — retention lives in the bound backend.

## What it provides

One Compose service, `otel-collector`, exposing:

| Endpoint | Port (default) | Purpose |
| --- | --- | --- |
| OTLP/gRPC | 4317 | OTel SDK default transport for logs/metrics/traces |
| OTLP/HTTP | 4318 | per-signal paths `/v1/logs`, `/v1/metrics`, `/v1/traces` |
| health_check | 13133 | collector health extension |

The provided sink contracts advertise the OTLP/HTTP endpoint
(`protocol: otlp-http`, `ingestPath: /v1/<signal>`, plus a pre-composed
`connectionUri`), matching the HTTP-push pattern of `log-sink`. The gRPC
receiver on 4317 is also open for SDKs that prefer it.

## Forwarding to a backend

Each signal can be forwarded to a backend module that provides the matching
sink contract, via `spec.config.backends.<signal>.contractRef`:

```yaml
- id: otel
  source: modules-experimental/observability/otel-collector
  config:
    backends:
      logs:
        contractRef: loki.log-sink
      traces:
        contractRef: tempo.trace-sink
```

Forwarding uses OTLP/HTTP (`otlphttp` exporter) to the backend's advertised
`connectionUri`, so the backend must expose an HTTP ingest endpoint (the
`trace-sink`/`metrics-sink` contracts provided here do). Unbound signals are
kept collector-local: the per-signal endpoint stays empty, the collector
falls back to a discard endpoint, and every pipeline also exports to the
collector's own log.

## Instrumenting a service (module authors)

Point the service's OTel SDK/exporter at the bound contract — see
[docs/observability.md](../../../docs/observability.md#opentelemetry-otlp-ingestion)
for the full `consumes`/`mappedFrom` wiring example.

## Host publishing

The OTLP and health ports are additionally bound to `127.0.0.1` on the host
for external non-Compose shippers during development; on the Compose network
other services should use the provided sink contracts.
