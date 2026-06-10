# Composable Data Stack

Composable Data Stack is a modular approach to defining and running data platform building blocks such as orchestration, storage, and BI through reusable module contracts and profiles.

## Quickstart

### 1. Clone the repository

```bash
git clone https://github.com/RonaldHensbergen/composable-data-stack.git
cd composable-data-stack
```

### 2. Create and activate a virtual environment
```bash

python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install the CLI
```bash
make install
```

Or manually:
```bash
pip install -e .
```

### 4. Create your local environment file
```bash
cp .env.example .env
```

Then edit `.env` and set real values for:

- `CDS_POSTGRES_PASSWORD`
- `CDS_SUPERSET_SECRET_KEY`
- `CDS_SUPERSET_ADMIN_PASSWORD`

### 5. Validate the example profile
```bash
make validate
```

Or directly:
```bash
cds validate profiles/local-dagster-postgres-superset/profile.yaml
```

## Current example profile

The main example profile is:
```text
profiles/local-dagster-postgres-superset/profile.yaml
```

It composes:

-    Postgres for storage
-    Dagster for orchestration
-    Superset for BI

## Project status

This repository currently includes:

-    profile/module YAML modeling
-    contract-based module wiring
-    a validation CLI
-    example modules for Postgres, Dagster, and Superset

Planned next steps:

-    compose rendering
-    secret resolution
-    runtime generation
-    stack bootstrap and smoke tests


## Core concepts
This repository is designed around **modules**, **profiles**, and **contracts**.

## Modules

Modules are reusable platform components such as orchestrators, warehouses, BI tools, secrets providers, and ingress.

Examples include:

-    `modules/orchestration/airflow`
-    `modules/orchestration/dagster`
-    `modules/warehouse/postgres`
-    `modules/warehouse/mariadb`
-    `modules/transform/dbt`
-    `modules/bi/superset`
-    `modules/quality/great-expectations`
-    `modules/secrets/env`
-    `modules/secrets/vault`

A module should eventually contain:
```text
modules/<category>/<name>/
├── module.yaml
├── defaults.yaml
├── compose.yaml
├── README.md
├── scripts/
└── tests/
```

## Profiles

Profiles are supported runnable compositions of modules, with explicit bindings and minimal hidden dependencies.

A profile should define:

-    selected module instances
-    bindings between provided and required contracts
-    stage-specific values
-    supported runtime expectations

Example profile names:

-    `local-airflow-postgres-superset`
-    `local-dagster-postgres-superset`
-    `integration-airflow-postgres-dbt`
-    `cloudstack-airflow-postgres-ha`

A profile should eventually contain:
```text
profiles/<profile-name>/
├── profile.yaml
├── values.yaml
└── README.md
```
## Contracts

Contracts provide the interface layer between modules.

Examples:

-    `sql-database`
-    `secrets-provider`
-    `http-service`
-    `warehouse-query`
-    `transformation-runner`

A module should never depend on another module implicitly. Instead, it should require a contract and receive it through profile bindings.

## Goals

- Build a composable data platform instead of a fixed demo stack
- Support interchangeable modules, such as:

  -  Airflow or Dagster
  -  Postgres, MariaDB, or Spark-oriented backends
  -  Superset, Metabase, and other BI options
  -  dbt, Great Expectations, Soda, Vault, and more

- Keep module contracts explicit
- Make profiles the unit of support
- Avoid implicit coupling and hidden dependencies
- Evolve from:

  -  local development
  -  reproducible integration environments
  -  production-ready deployments

## Design principles
### Contract-first modules

Each module should declare:

-    what it **provides**
-    what it **requires**
-    configuration inputs
-    health checks
-    lifecycle hooks
-    operational responsibilities

### Profile-driven composition

Profiles define supported combinations of modules.

If a combination is not represented as a profile, it should be treated as experimental rather than supported.

### Minimal hidden dependencies

Modules should interact through declared contracts and bindings, not through undocumented environment variables, cross-folder assumptions, or shared mutable state.
### One architecture, multiple stages

The platform should preserve the same logical composition model across:

-    local development
-    CI or integration environments
-   production deployment targets

Only the runtime packaging and operational controls should change.

## Development
### Install dependencies
```bash
make install
```

### Validate the default profile
```bash
make validate
```
### Validate a specific profile
```bash
make validate-profile P=profiles/local-dagster-postgres-superset/profile.yaml
```
## Repository structure
```text
.
├── cli/
├── modules/
│   ├── bi/
│   ├── orchestration/
│   ├── secrets/
│   └── warehouse/
├── profiles/
├── pyproject.toml
├── Makefile
├── .env.example
└── README.md
```
### Key directories
| Path |	Purpose |
| ---- | ------- |
| `cli/` |	Validation CLI and future planning/rendering tooling |
| `modules/` |	Deployable, reusable building blocks |
| `profiles/`	| Supported runnable combinations of modules | 
| `docs/` |	Architecture, contracts, modules, operations, and profiles |

### Planned bootstrap flow

The intended workflow is:

1.    Validate module manifests
1.    Validate profile definitions
1.    Resolve dependencies and bindings
1.    Generate a composition plan
1.    Render runtime assets
1.    Start services in dependency order
1.    Run initialization and health checks
1.    Produce an environment report

### Example future commands:
```bash
cds validate profiles/local-dagster-postgres-superset/profile.yaml
cds plan profiles/local-dagster-postgres-superset/profile.yaml
cds render profiles/local-dagster-postgres-superset/profile.yaml
cds up profiles/local-dagster-postgres-superset/profile.yaml
cds test profiles/local-dagster-postgres-superset/profile.yaml
```

### Initial implementation priorities

The near-term focus is:

1.    Normalize the repository around modules/ and profiles/
1.    Define shared module and profile schemas
1.    Establish initial contract definitions
1.    Make one profile fully declarative and runnable
1.    Build validation and planning tooling
1.    Add health checks and smoke tests

### MVP target

The initial MVP centers on a single supported local profile:
```text
local-dagster-postgres-superset
```
This is intended to validate:

-    module manifests
-    contract binding rules
-    profile composition
-    local rendering and bootstrap flow
-    health and smoke test behavior

### Non-goals for the initial phase

The following are intentionally deferred:

-    full production HA deployment logic
-    advanced CloudStack packaging
-    full upgrade orchestration
-    backup scheduling
-    multi-node Spark orchestration
-    complex secrets management automation

### Documentation

Current and planned documentation lives under `docs/`.

## License

See `LICENSE`.
