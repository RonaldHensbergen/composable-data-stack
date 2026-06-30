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

Each module should follow this structure:

```text
modules/<category>/<name>/
├── compose.yml
├── README.md
├── .env.example
├── config/
└── scripts/
```

## Minimal required files

Each module must include the following files:

| File | Required | Purpose |
| --- | --- | --- |
| `compose.yml` | yes | Defines the services, networks, volumes, and health checks for the module |
| `README.md` | yes | Explains what the module does, how it is configured, and how it is used |
| `.env.example` | yes | Documents the environment variables expected by the module |

Optional directories:

| Directory | Purpose |
| --- | --- | --- |
| `config/` | Static configuration files |
| `scripts/` | Bootstrap, init, or helper scripts |
| `data/` | Local development data or seeds, if intentionally included |

A minimal module layout looks like this:

```text
modules/<category>/<name>/
├── compose.yml
├── README.md
├── .env.example
├── config/           # optional
└── scripts/          # optional
```

## Responsibilities

A module should own:

- its service definitions
- its module-specific configuration
- its container image definition, if needed
- its own setup notes and runtime assumptions

A module should not own:

- top-level profile orchestration
- global bootstrap logic
- unrelated shared utilities
- configuration for other modules

## Compose requirements

Each module `compose.yml` should:

- define only the services that belong to that module
- use stable, descriptive service names
- include health checks where practical
- attach services to the shared profile network
- expose only the ports needed for local use
- use named volumes for persistent state where appropriate

Prefer:

- explicit environment variables
- explicit dependencies
    small, focused service definitions

Avoid:

- hidden reliance on undeclared services
- hardcoded paths outside the repo unless clearly documented
- broad coupling to one specific profile

## Custom images

When a module requires a custom Docker image, the build context belongs in the `images/` directory at the repository root, not inside the module directory.

Structure:

```text
images/<module-name>/
├── Dockerfile
└── requirements.txt      # or other build context files
```

Reference the image from `module.yaml` using a relative path:

```yaml
build:
  context: ../../../images/dagster
  dockerfile: Dockerfile
image: local/dagster:custom
```

The `image` field assigns a local tag so Docker Compose can reference the built image consistently across services in the same module.

### Example: Dagster

The Dagster module uses a custom image defined in `images/dagster/`:

```text
images/dagster/
├── Dockerfile
└── requirements.txt
```

Referenced in `modules/orchestration/dagster/module.yaml`:

```yaml
build:
  context: ../../../images/dagster
  dockerfile: Dockerfile
image: local/dagster:custom
```

Modules that use a standard upstream image without customization do not need an entry in `images/` and should reference the image directly in `module.yaml`.

## Environment variables

Each module must document its expected environment variables in .env.example.

Guidelines:

- include defaults where safe for local development
- clearly separate normal config from secrets
- do not commit real credentials
- keep module-local variables inside the module unless they are intentionally shared

Example:
```env
POSTGRES_USER=dev
POSTGRES_PASSWORD=dev
POSTGRES_DB=app
POSTGRES_PORT=5432
```

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

Secrets should be provided through one of these mechanisms:

- environment variables
- local `.env` files excluded from version control
- a dedicated secrets module such as Vault

### Secret Placeholders

When authoring a module that requires secrets, you should use standard placeholder behavior (e.g., `<SECRET_NAME_HERE>` or `${SECRET_NAME}`) within example configuration files or `.env.example`. Ensure that these placeholders are well-documented so users know exactly which values must be provided at runtime. Do not provide default values for sensitive fields.

If a module depends on a secrets provider, that dependency must be documented in the module README and in any profile that uses it.

## Health and readiness

Modules with long-running services should define health checks where meaningful.

Examples:

- database readiness checks
- HTTP health endpoints
- worker ping checks
- broker readiness checks

Health checks should reflect actual readiness, not just whether the process has started.

## Documentation requirements

Each module `README.md` should describe:

- the module’s purpose
- the services it runs
- required environment variables
- ports exposed
- volumes used
- dependencies on other modules
- known limitations or assumptions

## Dependency rules

Modules may depend on other modules, but dependencies must be explicit.

Examples:

- an orchestration module may depend on a database and a queue
- a BI module may depend on a metadata database and a broker
- a transformation module may depend on a warehouse module

Profiles are responsible for composing modules together. Modules should avoid hiding cross-module assumptions whenever possible.

## Profiles vs modules

Modules are reusable building blocks.

Profiles are runnable stack combinations built from modules.

Examples of profiles:

- Airflow + Postgres + dbt + Great Expectations + Superset
- Dagster + Postgres + dbt + Superset
- Airflow + Spark + dbt + Soda

A profile is responsible for:

- selecting modules
- wiring them together
- defining shared environment and network behavior
- documenting startup order and operational flow

## Naming conventions

Use names based on role and implementation.

Examples:

- modules/orchestration/airflow
- modules/warehouse/postgres
- modules/quality/great-expectations
- modules/bi/superset

Avoid vague names such as:

- db
- assets

## Definition of done

A module is considered complete when:

- it contains a valid compose.yml
- it contains a README.md
- it contains a .env.example
- its dependencies are documented
- it can be included in at least one profile
- its services start successfully in that profile
