# Open Policy Agent (experimental)

Reference implementation of the provider-neutral `data-access-policy`
contract (issue #1284): a policy engine that evaluates data-level access
policies — row filters, column masking, purpose binding — as
version-controlled policy files stored in the profile/repo, and advertises
the policy decision point through the shared
[`data-access-policy`](../../../shared/contracts/data-access-policy.yaml)
contract.

## Purpose

Use this module when a profile needs policy-as-code at the data layer, not
just at the deployment layer. A data provider (e.g. `warehouse/postgres`)
declares its contract `governed: true`; every consumer of that contract must
then bind a `data-access-policy` contract through its consume entry's
`policyFrom` (e.g. `analyticsDataPolicy: {contractRef: policy.data-access-policy}`),
and `cds validate`/`cds test` fail closed with E167 when that binding is
missing. See [docs/data-access-policy.md](../../../docs/data-access-policy.md)
for the full flow.

## Known limitations

- Experimental and single-node: no clustering, no persistent bundle storage,
  and the decision API is unauthenticated — keep it on the Compose network
  (the module publishes only `127.0.0.1:<apiPort>:8181` to the host).
- The contract is provider-neutral by design; this module is the OPA/Rego
  reference implementation behind it. Policies live in `config.policiesDir`
  (default `policies/`, relative to the profile directory), mounted
  read-only at `/policies`.
- `cds` validates the wiring, not the policy content: Rego syntax errors
  surface when the engine loads the files at container start, not at
  validate time. Run `opa check` locally on the policies directory while
  authoring.

## Upstream documentation

- [Open Policy Agent documentation](https://www.openpolicyagent.org/docs/latest/)
- [OPA REST API](https://www.openpolicyagent.org/docs/latest/rest-api/) —
  decisions at `POST /v1/data/<path>`, health at `/health`

## Configuration notes

- `policiesDir` resolves relative to the profile directory, like
  `warehouse/postgres`'s `initDbScript`; keep policy files next to the
  profile so they are reviewed and versioned with it.
- `policyVersion` (e.g. a git SHA) is advertised as `policyVersion` on the
  provided contract for audit and drift detection.
- Consumers reach the decision endpoint through
  `${bindings.<consume-name>-policy.decisionUri}` (e.g.
  `http://<instance>-opa:8181/v1/data`).
