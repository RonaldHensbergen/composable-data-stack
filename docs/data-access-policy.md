# Data-access policy contracts (policy-as-code at the query layer)

CDS contracts have always been *deployment-level*: they describe wiring,
ports, and credentials. This page documents the first *data-level*
authorization contract, `data-access-policy`
(`shared/contracts/data-access-policy.yaml`, issue #1284), following up on
the lesson from UWV's OpenDataAnalyticsPlatform and Stackable in
[docs/okdp-stackable-uwv-checkin.md](okdp-stackable-uwv-checkin.md) section 7:
data-level access control (row filters, column masking, purpose binding)
enforced by policy-as-code, version-controlled and compile-time checked like
everything else in CDS.

The contract is **provider-neutral**: it names no policy engine, language, or
storage format. Providers advertise their own decision API shape through
`decisionPath`/`decisionUri`; `modules-experimental/security/opa` is the
reference implementation (OPA with Rego policies stored as versioned files in
the profile/repo).

## The contract

A module that can answer data-access decisions (a query engine, a catalog, a
policy engine) provides a `data-access-policy` contract:

| Field | Required | Meaning |
| --- | --- | --- |
| `host` | yes | In-network DNS name of the decision endpoint |
| `port` | yes | In-network container port (never the host publish, #1067) |
| `scheme` | no | `http` (default when absent) or `https` |
| `decisionPath` | yes | Base path for decision queries, engine-specific (e.g. `/v1/data`) |
| `decisionUri` | yes | Fully composed `<scheme>://<host>:<port><decisionPath>` |
| `healthPath` | no | Health endpoint path (default `/health`) |
| `policyVersion` | no | Identifier of the loaded policy set (VCS revision, bundle id) for audit |

Like the other endpoint contracts, the provided contract's `host`/`port`
(and the host/port embedded in `decisionUri`) are cross-checked against the
render at render time (E150/E164), so a policy endpoint that resolves to
nothing fails the render instead of failing at query time.

## Declaring data as governed

A data provider opts its contract into governance by setting `governed: true`
in the provide entry's contract spec — either a literal boolean or a pure
`${config.<path>}` reference resolved against the producer instance's profile
config (falling back to the `configSchema` default when the profile leaves the
field unset). `modules/warehouse/postgres` advertises
`governed: ${config.governed}` (default `false`) on all of its `sql-database`
provides, so a profile opts in per instance:

```yaml
- id: postgres
  source: ../../modules/warehouse/postgres
  config:
    governed: true   # every sql-database contract of this instance is governed
```

`governed` is a reserved field documented on the `sql-database` shared
contract; any data-carrying contract kind can adopt the same convention.

## Binding a policy for a governed contract (`policyFrom`)

A consumer entry that binds a governed contract must also bind a
`data-access-policy` contract through the consume entry's optional
`policyFrom` key — a `spec.config.<path>` reference to a
`{contractRef: <module-id>.<provide-name>}` binding in the consumer's own
config (same vocabulary as `mappedFrom`):

```yaml
# modules/bi/superset/module.yaml (excerpt)
consumes:
  - name: analytics-database
    contract:
      kind: sql-database
    required: true
    mappedFrom: spec.config.analyticsDatabase
    policyFrom: spec.config.analyticsDataPolicy   # required when governed
```

```yaml
# profile.yaml (excerpt)
- id: superset
  config:
    analyticsDatabase:
      contractRef: postgres.sql-database
    analyticsDataPolicy:
      contractRef: policy.data-access-policy
```

The planner resolves the policy binding into a `<consume-name>-policy` entry
next to the main binding, so templates can use
`${bindings.analytics-database-policy.decisionUri}` (superset renders it as
`CDS_ANALYTICS_DB_POLICY_DECISION_URI`).

## Fail-closed validation (E167)

`cds validate` / `cds test` fail closed with **E167** when a consume entry
binds a governed contract but declares no `policyFrom`, or its `policyFrom`
resolves to no binding. A `policyFrom` that resolves to a contract of a
different kind is E042; a malformed or unknown ref is E041 — the same codes
the main binding uses. Ungoverned contracts need no policy binding at all, so
existing profiles stay valid unchanged.

## Reference implementation: `modules-experimental/security/opa`

The experimental `opa` module runs a policy engine serving the
`data-access-policy` contract. Its `config.policiesDir` (default `policies/`,
resolved relative to the profile directory, like postgres's `initDbScript`)
holds the version-controlled policy files, bind-mounted read-only at
`/policies` and loaded by `run --server /policies`. The image is
digest-pinned, the container runs non-root with a read-only root filesystem,
and only `127.0.0.1:<apiPort>:8181` is published to the host.

CDS validates the *wiring*, not the policy content: policy syntax errors
surface when the engine loads the files at container start. Run the engine's
own `check` on the policies directory while authoring.

## Example

`profiles/local-postgres-opa-superset/` wires the full flow: postgres with
`governed: true`, the `opa` module serving `policies/analytics.rego`, and
superset binding `policy.data-access-policy` for both database consume
entries. Removing a policy binding from that profile makes `cds validate`
fail with E167:

```console
$ cds validate local-postgres-opa-superset
[E167] spec.modules[3].config
Consume entry "analytics-database" in module "superset" declares policyFrom
"spec.config.analyticsDataPolicy", but it resolves to no policy binding, while
the bound contract "postgres.sql-database" is governed ...
```

## Scope and limitations

- The contract carries *where to ask*, not *what the answer is*: row filters,
  column masking, and purpose binding are expressed in the provider's policy
  files and evaluated by its engine at query time.
- Enforcement happens at the query engine, not in CDS: CDS guarantees that a
  governed contract is always consumed together with a policy binding
  (compile-time), it does not evaluate queries.
- The reference module is experimental and single-node with an unauthenticated
  in-network decision API — keep it on the Compose network and do not publish
  it beyond `127.0.0.1` on the host.
