# Composable Data Stack

A modular, contract-first approach to composing and validating data platform stacks.

Instead of a fixed demo stack, CDS lets you define reusable platform modules — orchestrators,
warehouses, BI tools, secrets providers — and wire them together through explicit contracts.
Swap Airflow for Dagster, Postgres for MariaDB, or Superset for Metabase, without hidden
coupling or full rewrites.

## Why CDS?

Most data platform setups force a choice between:

- a rigid, opinionated stack you cannot swap components in
- a fully custom setup with no shared contracts or reuse between tools

CDS gives you swappable, reusable modules wired together through explicit contracts, so you
can compose and evolve your stack without hidden coupling.

## Status

The MVP is **ready for testing**.

The `local-dagster-postgres-superset` profile is fully declarative and runnable. It covers:

- module manifest validation
- contract binding and resolution
- basic security checks against a profile
- profile composition and planning

Planned next:

- compose rendering
- secret resolution
- runtime generation
- stack bootstrap and smoke tests

> Feedback on the MVP is very welcome. Please open an issue if something breaks or feels wrong.

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

Then edit `.env` and set values for:

- `CDS_POSTGRES_PASSWORD`
- `CDS_SUPERSET_SECRET_KEY`
- `CDS_SUPERSET_ADMIN_PASSWORD`

### 5. Validate the example profile

```bash
make validate
```

Or directly:

```bash
cds validate local-dagster-postgres-superset
```

Expected output:

```text
Profile is valid.
```

## CLI reference

| Command | Description |
| ------- | ----------- |
| `cds validate <profile>` | Validate module manifests, contract bindings, and security checks for a profile |
| `cds plan <profile>` | Resolve dependencies and produce a composition plan *(planned)* |
| `cds render <profile>` | Render runtime assets from a resolved plan *(planned)* |
| `cds up <profile>` | Start services in dependency order *(planned)* |
| `cds test <profile>` | Run health checks and smoke tests *(planned)* |

### Environment variables

| Variable | Description |
| -------- | ----------- |
| `CDS_PROFILE_PATH` | Path to a `profiles/` directory or a specific `profile.yaml`. When set, commands accept a profile name instead of a full path. |
| `CDS_MODULE_PATH` | Path to a `modules/` directory. When set, module sources are loaded from this directory instead of the profile directory. |

**Linux / macOS:**

```bash
export CDS_PROFILE_PATH=/home/ronald/Projects/composable-data-stack/profiles
export CDS_MODULE_PATH=/home/ronald/Projects/composable-data-stack/modules
```

**Windows (PowerShell):**

```powershell
$env:CDS_PROFILE_PATH = 'C:\Projects\composable-data-stack\profiles'
$env:CDS_MODULE_PATH  = 'C:\Projects\composable-data-stack\modules'
```

## Core concepts

CDS is built around three primitives: **modules**, **profiles**, and **contracts**.

### Modules

Modules are reusable, self-contained platform components. Each module declares what it
provides, what it requires, and how it should be operated. Modules never depend on each
other directly — they interact only through contracts.

Available module categories:

| Category | Examples |
| -------- | ------- |
| Orchestration | `airflow`, `dagster` |
| Warehouse | `postgres`, `mariadb` |
| Transform | `dbt` |
| BI | `superset` |
| Quality | `great-expectations` |
| Secrets | `env`, `vault` |

Each module lives at `modules/<category>/<name>/` and may contain:

```text
modules/<category>/<name>/
├── module.yaml       # manifest: provides, requires, inputs, health checks
├── defaults.yaml     # default configuration values
├── compose.yaml      # runtime service definition
├── README.md         # module-level documentation
├── scripts/          # lifecycle and init scripts
└── tests/            # module-level smoke tests
```

### Profiles

A profile is a supported, runnable combination of modules. It defines which modules are
included, how their contracts are bound together, and what stage-specific values apply.

If a module combination is not represented as a profile, it should be treated as
experimental rather than supported.

Example profile names:

- `local-dagster-postgres-superset`
- `local-airflow-postgres-superset`
- `integration-airflow-postgres-dbt`
- `cloudstack-airflow-postgres-ha`

Each profile lives at `profiles/<profile-name>/` and may contain:

```text
profiles/<profile-name>/
├── profile.yaml      # module selection, bindings, and stage values
├── values.yaml       # profile-level configuration overrides
└── README.md         # profile-level documentation
```

### Contracts

Contracts are the interface layer between modules. A module declares what contracts it
provides and what contracts it requires. The profile wires them together through explicit
bindings.

Available contracts:

| Contract | Description |
| -------- | ----------- |
| `sql-database` | Relational database connection interface |
| `secrets-provider` | Secret resolution interface |
| `http-service` | HTTP endpoint exposure interface |
| `warehouse-query` | Query execution interface |
| `transformation-runner` | Transformation execution interface |

A module never depends on another module implicitly. If a module needs a database, it
declares a `sql-database` requirement. The profile decides which module satisfies it.

## Design principles

### Contract-first modules

Each module declares:

- what it **provides**
- what it **requires**
- configuration inputs
- health checks
- lifecycle hooks
- operational responsibilities

### Profile-driven composition

Profiles define supported combinations of modules. The profile is the unit of support —
not individual modules in isolation.

### Minimal hidden dependencies

Modules interact through declared contracts and profile bindings only. No undocumented
environment variables, cross-folder assumptions, or shared mutable state.

### One architecture, multiple stages

The same logical composition model applies across local development, CI environments, and
production deployment. Only runtime packaging and operational controls change between stages.

## Example profile

The current example profile is `local-dagster-postgres-superset`. It composes:

- **Postgres** for storage
- **Dagster** for orchestration
- **Superset** for BI

```bash
cds validate local-dagster-postgres-superset
```

## Bootstrap flow

The intended end-to-end workflow, once fully implemented:

1. Validate module manifests and profile definitions
2. Resolve dependencies, bindings, and security constraints
3. Generate a composition plan
4. Render runtime assets
5. Start services in dependency order
6. Run initialization and health checks
7. Produce an environment report

## Repository structure

```text
.
├── cli/              # Validation CLI and planning/rendering tooling
├── modules/          # Reusable, deployable building blocks
│   ├── bi/
│   ├── orchestration/
│   ├── secrets/
│   └── warehouse/
├── profiles/         # Supported runnable module combinations
├── docs/             # Architecture, contracts, modules, and operations
├── pyproject.toml
├── Makefile
└── .env.example
```

## Installation and packaging

Install from source for local development:

```bash
pip install -e .
```

Build a distributable wheel:

```bash
python3 -m pip install --upgrade build
python3 -m build
pip install dist/composable_data_stack-0.1.0-py3-none-any.whl
```

| Platform | Recommended distribution |
| -------- | ------------------------ |
| Linux | `pip install`, Homebrew/Linuxbrew tap, or `.deb`/`.rpm` package |
| macOS | Homebrew formula plus `pip install` |
| Windows | `pip install` for Python users; PyInstaller bundle or MSI for broader distribution |

See `docs/packaging.md` for full packaging and installer instructions.

## Development

```bash
make install       # install dependencies
make validate      # validate the default profile
make validate-profile P=profiles/local-dagster-postgres-superset/profile.yaml
make package       # build a distributable package
```

## Contributing

Contributions are welcome. Please read `CONTRIBUTING.md` before opening a pull request.

Good first areas to contribute:

- adding a new module under an existing category
- improving or adding profile-level documentation
- writing smoke tests for existing modules
- proposing new contract definitions

## License

See `LICENSE`.
