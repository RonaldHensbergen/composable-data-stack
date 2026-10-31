## Module Contract

A module is a self-contained building block of the platform, such as an orchestrator, warehouse, transformation engine, BI tool, validation tool, identity provider, or secrets provider.

Each module should be independently understandable, minimally reusable, and composable into one or more stack profiles.

**Getting started?** See [docs/from-docker-to-cds-profile.md](from-docker-to-cds-profile.md) for a complete walkthrough on creating modules from existing docker-compose services.

## Goals

Each module should:

- have a clearly defined responsibility
- expose a predictable interface to other modules
- be runnable through Docker Compose
- document its required configuration
- avoid hidden dependencies where possible

## Required structure

A module is defined by a single `module.yaml` file. Everything the validator, planner, and renderer need (runtime service definition, configuration schema, contracts, and the Docker Compose implementation) lives inline in that one file.

```text
modules/<category>/<name>/
└── module.yaml
```

Optional, module-local supporting files:

```text
modules/<category>/<name>/
├── module.yaml
├── README.md         # optional — human-readable notes
├── config/           # optional — static configuration files
└── scripts/          # optional — bootstrap, init, or helper scripts
```

## Module files

| File/Directory | Required | Purpose |
| --- | --- | --- |
| `module.yaml` | yes | Source of truth: metadata, runtime service, config schema, contracts, and the inline Compose implementation |
| `README.md` | no | Optional human-readable notes; not read by the validator |
| `config/` | no | Static configuration files |
| `scripts/` | no | Bootstrap, init, or helper scripts |
| `data/` | no | Local development data or seeds, if intentionally included |

There is no separate `compose.yml` or `.env.example` file. The Docker Compose definition lives inline at `spec.implementation.compose`, and configuration values are declared and validated through `spec.configSchema` rather than an example env file. See [Configuration](#configuration) below.

A representative stable module (`modules/warehouse/postgres/`) and a representative experimental module (`modules-experimental/orchestration/airflow/`) both follow this same single-file structure; "experimental" is a directory convention (`modules-experimental/`), not a different schema.

## Responsibilities

A module should own:

- its service definition and Compose implementation
- its module-specific configuration schema
- its container image definition, if needed
- its own setup notes and runtime assumptions

A module should not own:

- top-level profile orchestration
- global bootstrap logic
- unrelated shared utilities
- configuration for other modules

## Implementation

Each module declares its Docker Compose implementation inline, under `spec.implementation`:

```yaml
spec:
  implementation:
    kind: docker-compose
    compose:
      services:
        postgres:
          image: postgres:18@sha256:...
          # ...
      volumes:
        postgres-data:
          enabledFrom: spec.config.storage.enabled
```

`kind` is required; `docker-compose` is the only implementation kind in use today. `compose` is a normal Docker Compose service/volume/network definition, with two CDS-specific extensions:

- template placeholders, resolved at render time from four namespaces:
  - `${config.<field>}` reads the module configuration validated by `spec.configSchema`
  - `${service.host}` resolves to the module instance `id` declared in the profile, not to a service name under `spec.implementation.compose.services`; modules with multiple Compose services receive the same module `id` for this placeholder
  - `${service.dns}` resolves to the Compose DNS name the module's serving service answers to: the renderer's compose service name (`<id>` when the service already carries the id prefix, `<id>-<service>` otherwise) when the module has a single Compose service or a namesake service matching the module name (e.g. `postgres` alongside its `postgres-backup`/`postgres-restore` job containers), so contract endpoints resolve even when the instance id differs from the service name (issue #1010); other modules with multiple Compose services receive the instance `id` and name their serving service explicitly via the contract's `serviceName`
  - `${service.services.<name>}` resolves to the Compose DNS name of one specific service as written under `spec.implementation.compose.services`, mapped through the same service-naming rule (`<id>-<service>` unless already prefixed), so multi-service modules can point an endpoint at a particular service (e.g. a `lineage-sink` provider's API service) for any instance `id` (issue #1029)
  - `${bindings.<contract>.<field>}` reads a field from a contract declared in `spec.consumes` and resolved by the profile
  - `${secrets.<alias>}` resolves a profile secret alias to a Docker Compose runtime placeholder such as `${CDS_DB_PASSWORD}`; CDS never embeds the secret value
  - `${certificates.<alias>.cert}` / `${certificates.<alias>.key}` (issue #578) resolve one half of a profile certificate alias declared under `spec.certificates.values` to its `${CDS_*}` runtime placeholder. CDS never generates, reads, or embeds certificate or private key material: the alias only names the environment variables the external secret/cert provider supplies, so an undeclared alias, a bare `certificates.<alias>` reference, or an unknown trailing part fails validation with `E129` instead of silently not substituting
- `enabledFrom` / `conditionallyEnabledFrom: <json-path>`, which include or drop a volume, service, or healthcheck based on a boolean resolved from the profile's config (see `postgres`'s `storage.enabled` and `healthcheck.enabled` above). The marker's shape is enforced: it must be a **non-empty string** on services, volume definitions, volume-mount entries, and healthchecks — anything else (e.g. the boolean `enabledFrom: true`, unlike `production: true`) crashes the renderer and now fails validation with `E145`; on entries of any other service list field the marker is not implemented, so any `enabledFrom` key there fails closed as well. In the `kubernetes` implementation block the same rules apply (`production` gates on volume and configMap entries, `enabledFrom` on volume entries and workloads; markers elsewhere in the block fail closed). Both render targets re-check marker shapes when rendering a stored plan (plans can be loaded from files).

When authoring `spec.implementation.compose`:

- define only the services that belong to that module
- use stable, descriptive service names
- include health checks where practical, guarded with `enabledFrom` if optional
- attach services to the shared profile network
- expose only the ports needed for local use, bound to `127.0.0.1` unless the profile requires otherwise
- use named volumes for persistent state where appropriate

Prefer:

- explicit environment variables
- explicit dependencies
- small, focused service definitions

Avoid:

- hidden reliance on undeclared services
- hardcoded paths outside the repo unless clearly documented
- broad coupling to one specific profile

## Interpolation placeholders

CDS resolves module placeholders while rendering a profile. Each placeholder has a
specific source and availability:

| Placeholder | Source | Available when |
| --- | --- | --- |
| `${config.<field>}` | The module configuration, after schema validation | The module is being rendered |
| `${bindings.<contract>.<field>}` | A consumed contract resolved by the profile | The contract is declared in `spec.consumes` and has been bound |
| `${service.host}` | The module instance `id` from the profile | The module is being rendered |
| `${service.dns}` | The Compose DNS name of the module's serving service (single-service modules or modules with a namesake compose service: the renderer's compose service name; other multi-service modules: the instance `id`) | The module is being rendered |
| `${service.services.<name>}` | The Compose DNS name of the compose service `<name>` as written in the module, mapped through the renderer's service-naming rule (issue #1029) | The module is being rendered |
| `${secrets.<alias>}` | A profile secret alias | The alias is declared in `spec.secrets` |
| `${certificates.<alias>.cert}` / `${certificates.<alias>.key}` | One half of a profile certificate alias | The alias is declared in `spec.certificates` |
| `${ifNonempty:<path>,<prefix>,<suffix>}` | Conditional interpolation of a placeholder path | The module is being rendered |

For example, a cache module can publish a contract and consume it from another
module without hard-coding either module's host or port:

```yaml
# modules/cache/module.yaml
spec:
  provides:
    - name: cache-service
      contract:
        kind: cache-service
        spec:
          host: ${service.host}
          port: ${config.port}
  implementation:
    kind: docker-compose
    compose:
      services:
        cache:
          ports:
            - "127.0.0.1:${config.port}:6379"

# modules/worker/module.yaml
spec:
  consumes:
    - name: cache
      contract: cache-service
  implementation:
    kind: docker-compose
    compose:
      services:
        worker:
          environment:
            CACHE_HOST: ${bindings.cache.host}
            CACHE_PORT: ${bindings.cache.port}
```

With a profile instance such as `id: cache` and `port: 6380`, the first module
publishes `host: cache` and `port: 6380`; the worker receives those values when
its `cache` binding is resolved. `${secrets.<alias>}` is different: it remains
a Docker Compose runtime variable (for example `${CDS_DB_PASSWORD}`), so CDS
never embeds the secret value in generated output. See the
[profile guide](from-docker-to-cds-profile.md) for a complete stack example.

### TLS requirements (`tls`)

Modules that interact with an HTTP, database, or cache endpoint can declare a
TLS requirement on a `consumes` entry, provider-neutral and opt-in
(issue #577):

```yaml
spec:
  consumes:
    - name: metadata-database
      contract:
        kind: sql-database
      required: true
      mappedFrom: spec.config.metadataDatabase
      tls: required
```

The provider opts in from its provide entry's `contract.spec`:

```yaml
spec:
  provides:
    - name: sql-database
      contract:
        kind: sql-database
        spec:
          host: ${service.host}
          port: ${config.port}
          tls: true
```

Semantics and validation paths:

- `tls: required` is the only requirement value today; any other value fails
  module schema validation (`E021`) so a typo cannot silently disable the
  check. Omitting the key places no TLS requirement, so existing profiles and
  `module.yaml` documents stay valid unless they explicitly opt in.
- The provider's advertisement is a boolean literal (`true`/`false`) or a pure
  `${config.<path>}` reference that resolves against the producer instance's
  profile config. Because validation runs before the planner applies
  `configSchema` defaults, a config-driven advertisement only satisfies a
  requirement when the profile sets the referenced value explicitly.
- A requirement is only satisfied by `tls: true`; an absent field, `false`,
  or an unresolvable config reference fails closed with `E128` at `cds
  validate` (see [Troubleshooting](../README.md#️-troubleshooting)).
- Like every other contract-spec field, `tls` flows into the consumer's
  resolved binding (`${bindings.<name>.tls}`) during `cds plan`, so
  implementation templates can act on it.

The field is optional (`required: false`) in the shared contract documents
(`shared/contracts/http-service.yaml`, `sql-database.yaml`,
`cache-service.yaml`, `reverse-proxy.yaml`); `file-database` carries no TLS
field because it describes a bind-mounted file, not a network endpoint.

### Reverse-proxy dynamic routes

A module providing the `reverse-proxy` contract (e.g. Traefik) exposes public
routes through its `config.routes` entries instead of Docker label
autodiscovery: each entry declares a public `host` and a `target` module
id (or `<module-id>.<contract-name>` when the target provides several
`http-service` contracts). CDS resolves the target's `http-service`
contract, cross-checks the backend address against the compose services the
render actually produced, and writes deterministic file-provider
configuration next to the rendered compose file; invalid routing inputs
fail with `E136` instead of silently generating broken config.

The same fail-closed cross-check applies to plain provided contracts
(issues #1010, #1029): every `http-service`, `sql-database`,
`cache-service`, `object-storage`, `lineage-sink`, and `log-sink`
contract's advertised network host (the `host` field, the `endpoint`'s
hostname part, or -- for the sink kinds -- the `connectionUri`'s
hostname part; a scheme-less `host:port` value contributes its host
part, issue #1036) must match a compose service the render actually
produced for the providing module, and contracts that advertise a
`serviceName` have that name mapped through the renderer's
service-naming rule first. Mismatches fail with `E150` instead of
shipping a contract endpoint nothing answers. `${service.dns}`
resolves through the same naming rule during planning, so modules with
a single or namesake compose service advertise a resolvable host for
any instance id, and multi-service modules can build a specific
service's endpoint from `${service.services.<name>}`. On the `helm` target
the same cross-check runs against the Kubernetes Services the render
produces for the module (workloads with a `service` block, plus the
governing Service of every StatefulSet), so a provided contract whose
host matches no rendered Service also fails with `E150` there
(issue #1035).

A route's optional `certificate` names a profile certificate alias
(`spec.certificates.values`, issue #578). The alias is only a name: CDS
never generates, reads, or embeds certificate material. For the alias to
work, the external secret/cert provider must place the files as
`<alias>.crt` and `<alias>.key` inside the proxy's certificate mount
(`./certs/traefik/` for the Traefik module, mounted read-only at
`/etc/traefik/certs/`); the generated configuration wires those paths as
the router's certificate files. The alias is restricted to letters, digits
and hyphens because it interpolates into those file paths.

### Authentication advertisements (`authentication`)

Relational database and cache providers advertise how (and whether) they
authenticate clients from their provide entry's `contract.spec`
(issue #206), mirroring the TLS advertisement:

```yaml
# modules/warehouse/postgres/module.yaml
spec:
  provides:
    - name: sql-database
      contract:
        kind: sql-database
        spec:
          host: ${service.host}
          port: ${config.port}
          authentication: ${config.authentication.hostAuth}   # e.g. scram-sha-256

# modules/cache/keydb/module.yaml
spec:
  provides:
    - name: cache-service
      contract:
        kind: cache-service
        spec:
          authentication: ${ifNonempty:config.passwordFrom,required,}
```

For a `sql-database` provider the advertisement is the host (TCP)
authentication method (e.g. `scram-sha-256`, `md5`, `trust`); for a
`cache-service` provider it is `required` when the service rejects
unauthenticated clients and empty when it does not.

Production profiles (`metadata.environment: production`) fail validation
without them:

- a `sql-database` provider without an advertisement, or advertising
  `trust`, fails with `E139`; `md5` validates but warns (`W105`) because it
  is a deprecated password method;
- a `cache-service` provider that does not advertise `required` fails with
  `E137`;
- every credential-named config field of those providers (e.g.
  `passwordFrom`, `superuserPasswordFrom`) must be a `secrets.<alias>`
  reference whose alias is declared `required: true`, rejecting plaintext
  literals, empty values, and `${CDS_*}` profile inputs with `E138`.

Like the TLS advertisement, a `${config.<path>}` reference only satisfies
the policy when the profile sets the value explicitly (validation runs
before `configSchema` defaults apply), so production profiles declare
their authentication method explicitly. Non-production profiles are
unaffected: a development cache may stay unauthenticated.

Connection contracts carry credentials safely across consumers: the
planner turns `secrets.<alias>` references into Docker Compose runtime
placeholders (`${CDS_CACHE_PASSWORD}`) inside the resolved contract, so a
binding's `connectionUri` (e.g. `redis://:${CDS_CACHE_PASSWORD}@keydb:6379`)
flows into orchestration and BI consumers without CDS ever embedding a
resolved secret value.

### Conditional interpolation (`ifNonempty`)

For configuration values that require delimiters or prefixes only when set (such as optional passwords in connection URIs), CDS supports conditional interpolation via `${ifNonempty:<path>,<prefix>,<suffix>}`:

- **Syntax**: `${ifNonempty:<path>,<prefix>,<suffix>}` where `<path>` is any valid placeholder expression (e.g. `config.passwordFrom` or `bindings.db.password`), `<prefix>` is prepended to the resolved value, and `<suffix>` is appended.
- **Semantics**: Evaluates `<path>`. If the resolved value is `None`, empty, or whitespace-only after stripping, it emits an empty string `""`. Otherwise, it returns `prefix + value + suffix`.
- **Example**: In `modules/cache/keydb/module.yaml`, constructing an authenticated Redis connection URI:

```yaml
connectionUri: "redis://${ifNonempty:config.passwordFrom,:,@}${service.host}:${config.port}"
```

If `config.passwordFrom` resolves to a secret placeholder such as
`${CDS_CACHE_PASSWORD}`, it renders
`redis://:${CDS_CACHE_PASSWORD}@keydb:6379`. If `config.passwordFrom` is
unset, both prefix and suffix are skipped, rendering
`redis://keydb:6379`.

### Production and learning variants (`production`)

Modules mark the dividing line between production-worthy pieces and
learning/mockup-oriented pieces with the `production` tag, evaluated
against the profile's `metadata.environment` (`production` vs anything
else). The learning variant must stay especially easy to install — the
pinned stock image and no local builds — while production profiles gain
the hardened pieces.

Two forms of the tag, distinguished by type:

- **`production: true`** on a service, volume, or volume-mount entry keeps
  the entry only in production profiles; **`production: false`** keeps it
  only outside them. Entries carrying both `production` and `enabledFrom`
  must match both gates. The marker's shape is enforced: on services and
  volume definitions it must be a boolean or a mapping; on volume-mount
  entries it must be a boolean (or absent) — this includes mount entries
  nested inside a service's `production: { ... }` mapping. On entries of
  any other service list field (`ports`, `labels`, ...) no marker is
  implemented, so any `production` key there fails closed as well. Every
  violation fails validation with `E144` instead of silently leaking
  into the rendered output, and both render targets re-check marker
  shapes when rendering a stored plan (plans can be loaded from files).
- **`production: { ... }`** (mapping form) on a service marks the enclosed
  fields as production-only overrides of the service's base fields. A
  service is a single YAML key, so its learning and production variants
  cannot be two separate services; in a production profile the mapping is
  merged over the base service — mappings such as `environment` merge
  recursively, anything else (`image`, `command`, `volumes`) replaces the
  base value — and in other profiles it is ignored entirely. The base
  service stays the easy-to-install learning variant.

```yaml
services:
  postgres:
    image: postgres:18@sha256:...      # learning: pull only, no build
    ...
    production:                        # production pieces
      image: local/postgres:custom
      build: { context: ../../../, dockerfile: images/postgres/Dockerfile }
      command: [postgres, -c, archive_mode=on, ...]
      environment: { ... }              # merged over the base environment
  postgres-backup:
    production: true                    # production-only service
    enabledFrom: config.backup.enabled
    ...
```

The compose renderer drops mismatched entries before rendering; the
Kubernetes (helm) target is treated as production-only for now and always
renders the production variants. When a config option only works in
production (for example the postgres backup capability, whose sidecar is
`production: true`), validation fails closed instead of silently rendering
less than requested (`E143` for `config.backup.enabled` outside a
production profile).

## Custom images

When a module requires a custom Docker image, the build context belongs in the `images/` directory at the repository root, not inside the module directory. Reference it from `spec.implementation.compose.services.<name>`, the same place any other Compose service field goes:

```text
images/<module-name>/
├── Dockerfile
└── requirements.txt      # or other build context files
```

```yaml
spec:
  implementation:
    compose:
      services:
        <service-name>:
          build:
            context: ../../../
            dockerfile: images/<module-name>/Dockerfile
          image: local/<module-name>:custom
```

`context` is relative to the module's own directory, so it points at the repository root (`../../../`) with `dockerfile` given as a root-relative path, not a path already inside `images/<module-name>/`. The `image` field assigns a local tag so Docker Compose can reference the built image consistently across services in the same module.

### Example: Dagster

The Dagster module uses a custom image defined in `images/dagster/`. Shared
build support files (config generation, entrypoint, healthcheck, workspace,
requirements) live directly under `images/dagster/`, while each image variant
has its own Dockerfile under a `base/` or `hardened/` subfolder so the two
variants share everything except the Dockerfile itself:

```text
images/dagster/
├── base/
│   └── Dockerfile        # Debian/python:3.14-slim (default)
├── hardened/
│   └── Dockerfile        # Alpine-based, minimal attack surface
├── entrypoint.sh
├── generate_config.py
├── healthcheck.py
├── requirements.txt
└── workspace.yaml
```

Referenced in `modules/orchestration/dagster/module.yaml`, where
`config.image.variant` (`base` or `hardened`) selects which Dockerfile is
built:

```yaml
spec:
  implementation:
    compose:
      services:
        dagster-webserver:
          build:
            context: ../../../
            dockerfile: images/dagster/${config.image.variant}/Dockerfile
          image: local/dagster:custom
```

Modules that use a standard upstream image without customization do not need an entry in `images/` and should reference the image directly in the service's `image` field.

## Configuration

Each module declares the configuration it accepts as a JSON Schema under `spec.configSchema`. This is the validated source of truth for module config; there is no `.env.example` file.

```yaml
spec:
  configSchema:
    type: object
    additionalProperties: false
    required:
      - database
      - username
      - passwordFrom
      - port
    properties:
      database:
        type: string
        minLength: 1
      passwordFrom:
        type: string
        pattern: "^secrets\\.[a-zA-Z0-9_-]+$"
      port:
        type: integer
        minimum: 1
        maximum: 65535
        default: 5432
```

Guidelines:

- set `additionalProperties: false` and list every accepted field explicitly
- give safe local-development `default`s where possible
- keep secret-bearing fields separate from plain config (see [Secrets](#secrets) below) and give them a `passwordFrom`/`tokenFrom`-style name so their purpose is obvious from the schema alone
- add a `description` to non-obvious properties; it is the primary documentation a consumer of the module sees, since `README.md` is optional

A profile supplies concrete values for these fields, and `cds init <profile>` generates a project-root `.env` template from the resolved config across all of a profile's modules. See [docs/installation.md](installation.md) for the end-to-end flow.

## Networking

Modules should assume that profiles provide a shared Docker network.

Modules may:
- attach services to the shared profile network
- expose ports for local access

Modules should not:
- require undocumented external networks
- create isolated network behavior unless there is a strong reason

## Storage

Modules that persist data should use named Docker volumes.

Examples:

- database data directories
- application metadata
- generated documentation
- local cache/state that should survive restarts

Avoid committing runtime-generated data to the repository unless it is intentionally part of an example.

## Secrets

Modules must not require committed secrets.

Secrets are declared in `spec.configSchema` as string fields matching the `^secrets\.[a-zA-Z0-9_-]+$` pattern (see `passwordFrom`, `tokenFrom` above). CDS resolves these references to `${CDS_VAR}` placeholders using environment variables or local `.env` files excluded from version control (generated via `cds init`).

A profile can also compose a secrets module such as Vault. That module runs as an ordinary service and handles its own runtime integration; it is not a separate secret-loading backend used by the CDS renderer.

Do not give secret-bearing fields a `default` in `configSchema`, leaving them `required` with no default forces every consumer to supply a real value.

If a module depends on a secrets provider, that dependency must be documented in `metadata.description` and, if present, the module's `README.md`, and in any profile that uses it.

## Health and readiness

Modules with long-running services should define health checks where meaningful, typically guarded with `enabledFrom`/`conditionallyEnabledFrom` so they can be disabled per-profile.

Examples:

- database readiness checks
- HTTP health endpoints
- worker ping checks
- broker readiness checks

Health checks should reflect actual readiness, not just whether the process has started.

## Documentation

`README.md` is optional and, when present, is for human readers only; it is not read by the validator. The authoritative, machine-checked documentation of a module is `module.yaml` itself:

- `metadata.description` — what the module does
- `spec.configSchema` property `description`s — what each config field means
- `spec.provides` / `spec.consumes` — what contracts the module offers or needs

If you do add a `README.md`, follow the format guide in [docs/module-readme-template.md](module-readme-template.md). `modules/secrets/vault/README.md` is a completed example. Keep it to context that doesn't belong in YAML: rationale, known limitations, links to upstream docs.

## Dependency rules

Modules may depend on other modules, but dependencies must be explicit through `spec.provides` / `spec.consumes` contracts (see [docs/architecture.md](architecture.md#secrets-and-contract-resolution)), not through hardcoded service names or hidden assumptions.

Examples:

- an orchestration module may depend on a database and a queue
- a BI module may depend on a metadata database and a broker
- a transformation module may depend on a warehouse module

Profiles are responsible for composing modules together. Modules should avoid hiding cross-module assumptions whenever possible.

## Profiles vs modules

Modules are reusable building blocks.

Profiles are runnable stack combinations built from modules.

Examples of profiles:

- Dagster + Postgres + KeyDB + Superset
- Dagster + Postgres + KeyDB + Superset + Vault

A profile is responsible for:

- selecting modules
- wiring them together
- defining shared environment and network behavior
- documenting startup order and operational flow

## Naming conventions

Use names based on role and implementation.

Examples:

- modules/orchestration/dagster
- modules/warehouse/postgres
- modules/bi/superset
- modules/secrets/vault
- modules/cache/keydb
- modules/identity/keycloak

Avoid vague names such as:

- db
- assets

## Definition of done

A module is considered complete when:

- it contains a valid `module.yaml` that passes `cds validate` against `cli/resources/module.schema.json`
- its `spec.configSchema` fully describes every accepted config field, with `additionalProperties: false`
- its dependencies are declared through `spec.provides`/`spec.consumes`
- it can be included in at least one profile
- its services start successfully in that profile

`README.md` is encouraged for anything not obvious from `module.yaml`, but is not required for a module to be considered complete.
