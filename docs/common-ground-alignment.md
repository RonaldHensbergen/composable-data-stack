# Common Ground / Haven Alignment

Related: [docs/haven-parity-plan.md](haven-parity-plan.md) (the longer-term
plan to close platform-level gaps), [docs/roadmap.md](roadmap.md),
[profiles/local-haven-reference](../profiles/local-haven-reference) (the
reference profile this document accompanies).

## 1. What Common Ground and Haven are

[Common Ground](https://commonground.nl/) is the Dutch municipal
association's (VNG) vision for component-based government IT: autonomous,
API-first components that communicate over standardized interfaces instead
of monolithic, vendor-locked applications. [Haven](https://haven.commonground.nl/)
is the accompanying reference architecture for *where* those components
run — a platform-independent, Kubernetes-based hosting standard with a
certified reference implementation ("Haven+") covering GitOps,
observability, security, database operators, networking, and backup as
baseline platform capabilities rather than optional extras.

Both share a conviction that CDS also starts from: integrations should be
expressed as contracts between interchangeable, vendor-neutral components,
not as hardcoded dependencies on a specific product, and adoption should be
incremental rather than a big-bang platform migration.

## 2. Concept mapping

| Common Ground / Haven concept | CDS concept |
| --- | --- |
| Autonomous, API-first component | A `module.yaml` — a self-contained unit with its own `configSchema` and `implementation`, unaware of any other module's internals |
| Contract-based integration between components | `provides` / `consumes` entries on a module, resolved by `contractRef: <module-id>.<contract-name>` bindings in a profile ([cli/validator.py](../cli/validator.py), [cli/planner.py](../cli/planner.py)) |
| Vendor-neutral interface, swappable implementation | A shared contract kind (e.g. `sql-database`, `http-service`, `reverse-proxy`) that any conforming module can provide or consume, see [shared/contracts/](../shared/contracts/) |
| Incremental, non-big-bang adoption | Profile composition: start from one module, extend a profile with more modules/contracts over time ([`cds compose-profile`](cli-reference.md), profile layering) rather than redesigning the whole stack at once |
| Haven's platform baseline (GitOps, observability, security, networking, backup) | Tracked as an explicit, larger gap in [docs/haven-parity-plan.md](haven-parity-plan.md); CDS today covers the application layer (orchestration, warehouse, BI, cache, secrets, transformation, identity, integration) and is actively closing platform-layer gaps module by module |
| A Haven-style component stack (identity, ingress, database, observability) | [`profiles/local-haven-reference`](../profiles/local-haven-reference), combining the `keycloak` (identity), `traefik` (TLS-terminating ingress), and `postgres` (`sql-database`) modules; a `log-sink` provider (#662) is a planned addition once available |

## 3. What the reference profile demonstrates

[`profiles/local-haven-reference`](../profiles/local-haven-reference) wires
together modules CDS already has or has already scoped — no new
Common Ground-specific module was invented for this alignment:

- `postgres` provides the `sql-database` contract used as Keycloak's own
  metadata store.
- `keycloak` consumes that contract and provides an `http-service` contract,
  demonstrating an autonomous identity component wired purely through
  contracts, with no hardcoded reference to the `postgres` module's service
  name.
- `traefik` provides a `reverse-proxy` contract with TLS 1.2+ enforcement,
  acting as the stack's single ingress point.

This profile is intentionally minimal: it shows the *shape* of a
Haven-aligned component stack using CDS's existing contract model, not a
certified Haven+ deployment. The bigger platform-layer gaps (GitOps,
enforced network policy, backup/restore, full observability) are tracked
separately in [docs/haven-parity-plan.md](haven-parity-plan.md) and are out
of scope for this profile.

## 4. Known gaps

- CDS has no contract-based auto-wiring between a `reverse-proxy` provider
  and an `http-service` consumer yet; the reference profile's Traefik →
  Keycloak routing is hand-authored dynamic configuration
  ([`traefik/dynamic/keycloak.yml`](../traefik/dynamic/keycloak.yml)), not
  resolved from the profile's contract graph.
- The `keycloak` module has no realm configuration (#680) or
  `oidc-provider` contract for other modules to consume (#681); it is
  listed under Experimental Components in [docs/roadmap.md](roadmap.md)
  until those land.
- No `log-sink` provider module exists yet (#662); once one does, it is a
  natural addition to this reference profile for Haven-aligned centralized
  logging.

## 5. References

- [Haven](https://haven.commonground.nl/)
- [Common Ground](https://commonground.nl/)
- [docs/haven-parity-plan.md](haven-parity-plan.md) — the longer-term plan
  for closing Haven's platform-layer gaps in CDS
