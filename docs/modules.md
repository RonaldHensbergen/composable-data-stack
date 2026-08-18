## Module Contract

A module is a self-contained building block of the platform, such as an orchestrator, warehouse, transformation engine, BI tool, validation tool, or secrets provider.

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
  - `${bindings.<contract>.<field>}` reads a field from a contract declared in `spec.consumes` and resolved by the profile
  - `${secrets.<alias>}` resolves a profile secret alias to a Docker Compose runtime placeholder such as `${CDS_DB_PASSWORD}`; CDS never embeds the secret value
- `enabledFrom` / `conditionallyEnabledFrom: <json-path>`, which include or drop a volume, service, or healthcheck based on a boolean resolved from the profile's config (see `postgres`'s `storage.enabled` and `healthcheck.enabled` above)

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

CDS resolves module placeholders while rendering a profile. Each namespace has a
specific source and availability:

| Placeholder | Source | Available when |
| --- | --- | --- |
| `${config.<field>}` | The module configuration, after schema validation | The module is being rendered |
| `${bindings.<contract>.<field>}` | A consumed contract resolved by the profile | The contract is declared in `spec.consumes` and has been bound |
| `${service.host}` | The module instance `id` from the profile | The module is being rendered |
| `${secrets.<alias>}` | A profile secret alias | The alias is declared in `spec.secrets` |

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
