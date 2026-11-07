# Local Loki + OTel Collector (experimental observability)

Minimal local stack bundling the two experimental observability modules:
[`loki`](../../modules-experimental/observability/loki/README.md) (Vector HTTP
ingest gateway + Grafana Loki store, provides `log-sink`) and
[`otel-collector`](../../modules-experimental/observability/otel-collector/README.md)
(OTLP receiver, provides `log-sink` / `metrics-sink` / `trace-sink`). It exists
so the Docker smoke-test CI job (`.github/workflows/docker-smoke-test.yml`,
issue #1065) can prove these modules not only render green but actually ingest
and store events at runtime.

## Purpose

- `loki` accepts one JSON object per `POST` to `http://127.0.0.1:8686/logs`.
  Records with string `service`, `profile`, `environment`, `timestamp`, and
  `severity` fields land in the `tier="structured"` stream (90-day retention);
  anything else lands in `tier="raw"` (7-day retention). Query the store
  through the in-network Loki API on `loki-storage:3100`, e.g.
  `GET /loki/api/v1/query_range?query={tier="structured"}&start=<ns>&end=<ns>`
  (stream selectors need the range endpoint).
- `otel-collector` receives OTLP on gRPC `127.0.0.1:4317` and HTTP
  `127.0.0.1:4318` (`/v1/logs`, `/v1/metrics`, `/v1/traces`) and exposes a
  health endpoint on `127.0.0.1:13133`. With no `backends.*` binding the
  signals stay collector-local and are printed to the collector's own log.

## Prerequisites

- Python 3.14+ with the CDS CLI installed, see [docs/installation.md](../../docs/installation.md)
- Docker Engine with the Compose v2 plugin

The profile needs no secrets: both modules are unauthenticated and loopback-only
by design (experimental, local-only posture).

## Startup

```bash
cds up local-loki-otel-collector
```

## Teardown

```bash
cds down local-loki-otel-collector
# or, in the directory where the compose file was rendered:
docker compose down -v --remove-orphans
```

## Operational notes

- Both modules are experimental (`productionSuitable: false`,
  `modules-experimental/observability/`) and render for the docker-compose
  target only; all host ports are published on `127.0.0.1` only
- The CI smoke test for this profile lives in
  `tests/test_observability_runtime_smoke.py` and runs under
  `CDS_RUN_DOCKER_SMOKE=1` (see `local-test-skips.md`)
