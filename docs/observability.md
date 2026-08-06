# Observability: Tiered Log Retention and Structured Runtime Events

Version: 0.1
Status: Draft — architecture guidance for issue #174. Extends as modules add
`log-sink` providers.
Related: [#174](https://github.com/RonaldHensbergen/composable-data-stack/issues/174)
(this document), [#219](https://github.com/RonaldHensbergen/composable-data-stack/issues/219)
(shared `cds-runtime` named-connection API), [#167](https://github.com/RonaldHensbergen/composable-data-stack/issues/167)
(backend-portable event persistence).

## 1. Problem

Troubleshooting a CDS-rendered stack today relies on ad hoc `docker compose
logs`, service-specific UIs (Dagster's UI, Superset's UI, etc.), and manual
datastore checks. There is no retention policy, no cross-service correlation,
and no separation between high-value operational events and noisy raw
container output. This document defines the architecture CDS profiles should
follow to fix that, without mandating a specific observability vendor.

## 2. Ownership boundary

This document — and the contract/schema it defines — owns:

- the structured event schema (what fields a high-value event contains),
- the `log-sink` contract (how a module exposes a place to ship events to),
- retention-tier guidance (raw vs. structured),
- redaction guidance and operator incident-query examples.

It explicitly does **not** own:

- named database connection resolution — that is #219's `cds-runtime`
  package; any module persisting structured events to a SQL/NoSQL backend
  must resolve that connection through `cds-runtime`, not a new adapter.
- event-domain persistence schema/migrations — that is #167's job for the
  shared event-persistence helper used by orchestration/user-code layers.

Concretely: this document tells you *what* a structured event looks like and
*where* it can be shipped; #219/#167 own *how* a module talks to a database
to store one.

## 3. Design: two retention tiers

| Tier | Contents | Retention | Where it lives |
| --- | --- | --- | --- |
| **Raw** | Unmodified container stdout/stderr | Short (days) | Docker's own logging driver / local disk, or forwarded as-is to a collector |
| **Structured** | High-value events: run status, resource name, record counts, failures, retries, health transitions | Longer (weeks-months) | Shipped as JSON matching [`schemas/structured-event.schema.json`](../schemas/structured-event.schema.json) to a `log-sink` (or a database resolved via `cds-runtime`, per #219/#167) |

Raw logs answer "what happened, verbatim, right now." Structured events
answer "what happened, to what, with what outcome, and how does it relate to
other events" — and are worth keeping around much longer because they are
cheap (small, normalized) and high-signal. Rendered Compose services should
apply a bounded raw-log driver (e.g. `json-file` with `max-size`/`max-file`,
or an external tier via `syslog`/`fluentd`/`awslogs` drivers) rather than
unbounded log growth; the exact driver is a per-runtime rendering concern and
out of scope here.

## 4. Structured event schema

Structured events are JSON documents validated against
[`schemas/structured-event.schema.json`](../schemas/structured-event.schema.json).
Required fields:

- `service` — the emitting module instance id (`spec.modules[].id`)
- `profile` — the profile's `metadata.name`
- `environment` — the profile's `metadata.environment` (matches
  `cli/security.py`'s profile classification — `local`, `development`,
  `staging`, or `production`)
- `timestamp` — RFC 3339 / ISO 8601, UTC recommended
- `severity` — normalized to `debug` / `info` / `warning` / `error` /
  `critical`, independent of any module-native log-level naming

Optional correlation/workload fields:

- `correlationId` — ties related events together across services (e.g. a run
  id or request id)
- `workloadId` — the specific job/run/asset/query the event describes
- `resource` — the table, asset, pipeline, or endpoint the event is about
- `recordCount`, `retryCount` — counters, when applicable
- `message` — human-readable summary (see redaction guidance below)

Modules that want to emit structured events construct a document matching
this shape and either write it via a `cds-runtime` database connection
(#219/#167) or forward it to a `log-sink` (below). This repository does not
ship an enforcement point that validates every emitted event against the
schema; it is a contract for module authors and downstream tooling to build
against.

## 5. The `log-sink` contract

[`shared/contracts/log-sink.yaml`](../shared/contracts/log-sink.yaml) defines
a vendor-neutral contract (`kind: log-sink`) that a centralized log collector
module can provide: `host`, `port`, `protocol`, an optional `ingestPath`, and
optional `rawRetentionDays`/`structuredRetentionDays`. Any module that ships
logs can `consume` this contract the same way modules already consume
`sql-database` or `cache-service` — by declaring a `consumes` entry and
letting the profile bind it with `contractRef: <module-id>.log-sink`.

This keeps CDS's existing vendor-neutral pattern: nothing in the CLI or in
other modules hardcodes a specific logging product. A `log-sink` provider
could be backed by Loki, Fluent Bit's HTTP input, an OpenSearch ingest
pipeline, or a simple internal collector — the contract only commits to the
wire-level fields needed to reach it.

## 6. Profile-level opt-in without naming a module

A profile can opt into log shipping and declare retention tiers without
picking a provider:

```yaml
# profiles/<name>/profile.yaml
spec:
  observability:
    logShipping:
      enabled: true
      retention:
        rawDays: 7
        structuredDays: 90
```

This is intentionally valid on its own — `sink` is optional. It signals
operator intent ("this profile should ship structured events somewhere") that
tooling, CI, or an operator runbook can act on, without the profile author
needing to already know which module will collect them.

Once a specific collector module is added to the profile and provides
`log-sink`, the profile can pin to it explicitly:

```yaml
spec:
  observability:
    logShipping:
      enabled: true
      retention:
        rawDays: 7
        structuredDays: 90
      sink:
        contractRef: log-collector.log-sink
```

`cli/validator.py`'s `validate_observability_config` enforces:

- `spec.observability` and `spec.observability.logShipping` are objects when
  present,
- `logShipping.enabled` is a boolean,
- `retention.rawDays`/`retention.structuredDays` are positive integers, and
  `structuredDays >= rawDays` (the structured tier is never shorter-lived
  than the raw tier it summarizes),
- when `sink.contractRef` is set, it resolves to a module in the profile that
  provides a `log-sink` contract (the same `<module-id>.<contract-name>`
  resolution already used by `spec.outputs.contracts`).

## 7. Redaction guidance

- Never put secret values, connection URIs with embedded credentials, or raw
  `${CDS_*}` placeholder resolutions into `message` or any structured event
  field. This mirrors the existing rule that plans/rendered Compose never
  resolve secret values (see `docs/threat-model.md` Boundary D).
  Structured events are the same trust boundary as rendered artifacts — they
  routinely leave the runtime environment (log shipping, dashboards,
  incident tickets) and must be treated as such.
- Prefer emitting the *name* of a secret alias, resource, or connection
  (e.g. `"connection": "analytics"`) rather than any resolved value.
  Consumers can be pointed at the alias in ops docs rather than have it
  embedded in every event.
- If a module needs to redact user-supplied content before emission (e.g.
  SQL error messages that may echo query text), do so before constructing
  the event, not as a downstream log-shipping concern.

## 8. Recommended pipelines (non-mandatory)

None of these are required or shipped by CDS; they are reference patterns for
a `log-sink` provider module:

- **Fluent Bit → Loki/OpenSearch**: Fluent Bit tails container stdout for the
  raw tier and forwards `log-sink`-shaped JSON separately for the structured
  tier, using Loki's or OpenSearch's own retention policy per index/stream.
- **Vector → any HTTP sink**: similar shape, using Vector's own
  transform/route pipeline to split raw vs. structured streams.
- **Direct HTTP push**: a module emits structured events directly to the
  `log-sink` contract's `host`/`port`/`ingestPath` without an intermediate
  agent, for simple profiles.

## 9. Operator incident queries (examples)

Once structured events are flowing to a queryable sink, these are the
baseline questions the correlation fields above are meant to answer:

- "What happened to `correlationId=<run-id>` across every service?" — filter
  by `correlationId`, sort by `timestamp`.
- "Which resources failed in `profile=<name>` in the last hour?" — filter by
  `profile`, `severity in (error, critical)`, `timestamp` window.
- "Is `service=<module-id>` retrying excessively?" — filter by `service`,
  aggregate `retryCount` over time.
- "What's the health-transition history for `resource=<name>`?" — filter by
  `resource`, sort by `timestamp`, read `severity`/`message` sequence.

## 10. Out of scope

- Mandating one observability vendor or shipping a bundled logging stack in
  every profile.
- Persisting every raw log indefinitely — the raw tier is intentionally
  short-retention.
- A second database/credential/adapter API for event storage — reuse
  #219/#167.
