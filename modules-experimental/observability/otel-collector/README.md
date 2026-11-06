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
| health_check | 13133 | collector health extension (probed via the host-published port — the pinned image is distroless, so no container-side healthcheck can run, issue #1065) |

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
        contractRef: logs.log-sink            # the loki module's OTLP endpoint
      traces:
        contractRef: otel-backend.trace-sink   # another OTLP/HTTP backend
```

Forwarding uses OTLP/HTTP (`otlphttp` exporter) to the backend's advertised
`connectionUri` + `ingestPath` — the exporter treats a per-signal endpoint as
the complete URL and does not append `/v1/<signal> itself, so the ingest path
must be included. The backend must therefore expose an OTLP/HTTP ingest
endpoint: all three sink contracts declare `connectionUri` (the `log-sink`
contract gained the field in #1076), and both shipped providers advertise an
OTLP endpoint — this module's own `/v1/<signal>` paths, and the `loki` module's
native Loki endpoint `/otlp/v1/logs` — so **all three signals, including
logs, forward end-to-end** (#1076). A sink that only speaks plain JSON is not
OTLP-capable and stays collector-local. Unbound signals are kept
collector-local too: the per-signal endpoint stays empty, the exporter falls
back to its base endpoint — the collector's own health_check extension, which
answers the export POST with 200 on any path and drops the batch silently
instead of the retry storm a dead port (`127.0.0.1:1`) would cause — and every
pipeline also exports to the collector's own log (the `debug` exporter; the
`logging` exporter was removed in the pinned collector v0.164.0).

## Instrumenting a service (module authors)

Point the service's OTel SDK/exporter at the bound contract — see
[docs/observability.md](../../../docs/observability.md#opentelemetry-otlp-ingestion)
for the full `consumes`/`mappedFrom` wiring example.

## Host publishing

The OTLP and health ports are additionally bound to `127.0.0.1` on the host
for external non-Compose shippers during development; on the Compose network
other services should use the provided sink contracts.
