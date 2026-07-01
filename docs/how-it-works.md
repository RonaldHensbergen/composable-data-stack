# How CDS Works

> A visual, end-to-end walkthrough of the Composable Data Stack (CDS) engine:
> what it is, what technology it uses, what it actually produces, and how each
> CLI command flows through the code.

## Table of Contents

- [TL;DR](#tldr)
- [What CDS Is (and Is Not)](#what-cds-is-and-is-not)
- [Technology and Frameworks](#technology-and-frameworks)
- [What Resources CDS Creates](#what-resources-cds-creates)
- [C4 Diagrams](#c4-diagrams)
  - [Level 1: System Context](#level-1-system-context)
  - [Level 2: Containers](#level-2-containers)
  - [Level 3: Components (the CLI internals)](#level-3-components-the-cli-internals)
- [The Domain Model](#the-domain-model)
- [Sequence Diagrams](#sequence-diagrams)
  - [End-to-End: From Profile to Running Stack](#end-to-end-from-profile-to-running-stack)
  - [`cds validate`](#cds-validate)
  - [`cds security`](#cds-security)
  - [`cds plan`](#cds-plan)
  - [`cds render`](#cds-render)
- [Anatomy of the Generated `docker-compose.yml`](#anatomy-of-the-generated-docker-composeyml)
- [Key Source Files](#key-source-files)

---

## TL;DR

CDS is a **Python command-line tool** that reads declarative **YAML** (profiles +
modules), validates them against **JSON Schemas** and a security rule set, resolves
the wiring between components ("contracts"), and **generates a `docker-compose.yml`**
you can `docker compose up`.

It is best understood as a **compiler / code generator for data platforms**:

```text
  YAML profile + YAML modules  ──▶  [ CDS CLI ]  ──▶  docker-compose.yml  ──▶  docker compose up
     (declarative source)          (validate,        (generated artifact)     (real containers)
                                    plan, render)
```

There is **no SDK, no cloud API, no Terraform provider, and no long-running
service.** CDS itself provisions nothing. It emits a Compose file; Docker does the
actual running. Think "Terraform `plan`/`render`" for a local data stack, where the
"apply" step is just `docker compose up`.

---

## What CDS Is (and Is Not)

| It **is** | It is **not** |
| --- | --- |
| A YAML-driven CLI (`cds`) | A cloud provisioner / Terraform replacement (yet) |
| A validator + planner + renderer | An orchestrator or runtime agent |
| A generator of `docker-compose.yml` | A collection of hand-written compose scripts |
| A contract-checking "compiler" | A service that stays running |
| Pure Python, two dependencies | A heavyweight framework with a plugin runtime |

The MVP shipped today covers **validate → security → plan → render**. Commands like
`cds up` and `cds test` are declared in the CLI table but are **planned**, not yet
implemented. The engine stops at generating the Compose file.

---

## Technology and Frameworks

CDS is deliberately minimal. From `pyproject.toml`:

```toml
[project]
name = "composable-data-stack"
requires-python = ">=3.11"
dependencies = [
  "PyYAML>=6.0",
  "jsonschema>=4.22.0",
]

[project.scripts]
cds = "cli.main:main"
```

| Concern | Technology | Notes |
| --- | --- | --- |
| CLI framework | **`argparse`** (stdlib) | Subcommands: `validate`, `plan`, `render`, `list`, `security`. Optional `argcomplete` for shell completion. |
| Config format | **YAML** via **PyYAML** | Everything (profiles, modules, defaults) is YAML. |
| Validation | **`jsonschema`** (Draft) | `schemas/profile.schema.json`, `schemas/module.schema.json`, plus a hand-rolled contract/graph checker. |
| Security rules | Custom engine over **JSON** | `security/rule-set.json` validated by `security/rule-schema.json`. |
| Output target | **Docker Compose** | The only implementation `kind` supported today is `docker-compose`. |
| Packaging | **setuptools** | Installs the `cds` console script. |

**No SDK.** The "interface" is the `cds` CLI plus the YAML schemas. Modules do not
import a Python API; they are pure YAML definitions the CLI reads.

The technologies you see mentioned (Dagster, Postgres, Superset, Airflow, dbt,
Vault, Superset, etc.) are **not dependencies of CDS**. They are the *tools the
generated stack runs* as Docker images. CDS only knows their Compose fragments and
contracts, declared as YAML modules under `modules/`.

---

## What Resources CDS Creates

This is the question that most often trips people up. CDS does **not** stand up
infrastructure. Running the pipeline produces exactly one durable artifact:

```text
  cds render local-dagster-postgres-superset
        │
        ▼
  ./docker-compose.yml        ← the ONLY thing CDS writes to disk
```

That generated file contains:

| Compose key | Where it comes from |
| --- | --- |
| `name:` | `profile.metadata.name` (the Compose project name) |
| `services:` | Each enabled module's `spec.implementation.compose.services`, **prefixed by module id** (e.g. `postgres-postgres`, `dagster-web`, `superset-app`) |
| `volumes:` | Each module's declared volumes, prefixed by module id (omitted if none) |
| Health checks | Rendered from each service's `healthcheck` block (conditionally enabled per config) |
| Substituted values | `{{ config.* }}` and `secrets.*` placeholders resolved against the plan's config + secrets |

Intermediate (optional) artifacts:

- **A plan** — `cds plan` emits a JSON document (`apiVersion: cds/v1alpha1`) to stdout
  or a file. This is the fully resolved, validated composition graph. `cds render`
  can consume a saved plan file directly instead of re-reading the profile.

So the honest answer to *"does it just create a bunch of composed-up scripts?"* is:
**it generates a single, deterministic Compose file from many small, contract-checked
YAML modules.** The value is in everything that happens *before* the file is written:
schema validation, contract resolution, dependency-cycle detection, and security
checks.

---

## C4 Diagrams

> Rendered as Mermaid `graph TB` (the C4 *levels* are expressed as labelled
> boundaries/subgraphs, which standard Mermaid renders reliably).

### Level 1: System Context

```mermaid
graph TB
    DEV["👤 **Platform / Data Engineer**<br/>Wants a reproducible data stack"]

    subgraph SYS["🧩 System: Composable Data Stack (CDS)"]
        CDS["**cds CLI**<br/>Validate, plan, render<br/>data platform profiles"]
    end

    subgraph INPUTS["📄 Declarative Source (in the repo)"]
        PROFILES["**Profiles**<br/>profiles/*/profile.yaml"]
        MODULES["**Modules**<br/>modules/**/module.yaml"]
    end

    subgraph RUNTIME["🐳 Runtime (external)"]
        DOCKER["**Docker Compose**<br/>Runs the generated stack"]
    end

    DEV -->|"runs cds validate / plan / render"| CDS
    PROFILES -->|"read"| CDS
    MODULES -->|"read"| CDS
    CDS -->|"writes docker-compose.yml"| DOCKER
    DEV -->|"docker compose up"| DOCKER
```

### Level 2: Containers

> "Container" here is the C4 sense (a runnable/deployable unit), not a Docker
> container. CDS is a single Python package; the interesting structure is inside it.

```mermaid
graph TB
    DEV["👤 **Engineer**"]

    subgraph PKG["🐍 CDS Python Package (cli/)"]
        MAIN["**main.py**<br/>argparse entrypoint<br/>command dispatch + path resolution"]
        VALIDATOR["**validator.py**<br/>Schema + contract + graph checks"]
        SECURITY["**security.py**<br/>Rule engine"]
        PLANNER["**planner.py**<br/>Resolve config, contracts, secrets"]
        RENDERER["**renderer.py**<br/>Emit docker-compose.yml"]
    end

    subgraph DATA["📄 On-disk YAML/JSON"]
        PROFILE["profiles/*/profile.yaml"]
        MODULE["modules/**/module.yaml"]
        SCHEMAS["schemas/*.schema.json"]
        RULES["security/rule-set.json"]
        ENV["🔑 .env / shell env<br/>CDS_* secrets"]
    end

    OUT["📦 **docker-compose.yml**<br/>generated artifact"]

    DEV -->|"cds <command>"| MAIN
    MAIN --> VALIDATOR
    MAIN --> SECURITY
    MAIN --> PLANNER
    MAIN --> RENDERER

    PROFILE -->|read| VALIDATOR
    MODULE -->|read| VALIDATOR
    SCHEMAS -->|validate against| VALIDATOR
    RULES -->|load rules| SECURITY
    ENV -->|resolve secrets| PLANNER

    VALIDATOR -->|"ok?"| PLANNER
    PLANNER -->|"plan (cds/v1alpha1)"| RENDERER
    RENDERER --> OUT
```

### Level 3: Components (the CLI internals)

```mermaid
graph TB
    subgraph CLI["🧩 Component: cli/ package"]
        MAIN["**main.py**<br/>dispatch + resolve_profile_path"]

        subgraph VAL["Validation"]
            VALIDATOR["**validator.py**<br/>shape, configs, deps, contracts, outputs"]
            GRAPH["**graph.py**<br/>dependency cycle detection"]
        end

        subgraph PLAN["Planning"]
            PLANNER["**planner.py**<br/>apply_defaults, resolve contracts"]
            RESOLVER["**resolver.py**<br/>parse contract refs, secret refs"]
            SECRETS["**secrets.py**<br/>load CDS_* from env/.env"]
        end

        subgraph REND["Rendering"]
            RENDERER["**renderer.py**<br/>services/volumes + value substitution"]
        end

        SEC["**security.py**<br/>rule-set.json engine"]
        LOADER["**loader.py**<br/>load_yaml_file"]
        DIAG["**diagnostics.py**<br/>Diagnostic(code, level, path, msg)"]
        IMG["**image_updates.py**<br/>registry version checks (cds list images)"]
    end

    MAIN --> VALIDATOR
    MAIN --> SEC
    MAIN --> PLANNER
    MAIN --> RENDERER
    VALIDATOR --> GRAPH
    VALIDATOR --> LOADER
    PLANNER --> RESOLVER
    PLANNER --> SECRETS
    PLANNER --> LOADER
    VALIDATOR -.emits.-> DIAG
    PLANNER -.emits.-> DIAG
    RENDERER -.emits.-> DIAG
    SEC -.emits.-> DIAG
```

---

## The Domain Model

Three concepts drive everything:

```text
  ┌──────────────┐        includes         ┌──────────────┐
  │   Profile    │ ──────────────────────▶ │    Module    │
  │ (a runnable  │   (by source + version) │ (one reusable│
  │  composition)│                         │  capability) │
  └──────┬───────┘                         └──────┬───────┘
         │                                        │
         │ wires modules via                      │ declares
         ▼                                        ▼
  ┌──────────────┐   provides / consumes   ┌──────────────┐
  │  Contracts   │ ◀─────────────────────▶ │  Compose     │
  │ sql-database │   (explicit interfaces) │  fragment    │
  │ http-service │                         │ (services,   │
  │ secrets-...  │                         │  volumes)    │
  └──────────────┘                         └──────────────┘
```

- A **Module** (`modules/<category>/<name>/module.yaml`) declares its `configSchema`,
  the contracts it `provides`, and its `implementation.compose` (the Docker Compose
  fragment). Categories today: `orchestration/` (Dagster), `warehouse/` (Postgres),
  `bi/` (Superset), `secrets/` (Vault).
- A **Profile** (`profiles/<name>/profile.yaml`) lists module instances with their
  `config`, `dependsOn` edges, `secrets` bindings, and `outputs`. Bindings use
  `contractRef` like `postgres.sql-database`.
- **Contracts** are the typed interfaces (`kind: sql-database`, `http-service`, …). A
  consumer's expected kind must match the producer's provided kind, or validation
  fails with `E042`.

---

## Sequence Diagrams

### End-to-End: From Profile to Running Stack

The happy path an engineer follows in the Quickstart.

```mermaid
sequenceDiagram
    actor Dev as Engineer
    participant CLI as cds (main.py)
    participant Val as validator.py
    participant Sec as security.py
    participant Plan as planner.py
    participant Rend as renderer.py
    participant FS as Filesystem
    participant Docker as docker compose

    Dev->>CLI: cds validate <profile>
    CLI->>Val: validate_profile(path)
    Val-->>CLI: [] (no errors)
    CLI-->>Dev: Profile is valid.

    Dev->>CLI: cds security <profile>
    CLI->>Sec: run_security_validation(profile, rules)
    Sec-->>CLI: findings []
    CLI-->>Dev: No security findings.

    Dev->>CLI: cds plan <profile>
    CLI->>Val: validate_profile(path)
    CLI->>Plan: build_plan(path)
    Plan-->>CLI: plan (cds/v1alpha1)
    CLI-->>Dev: plan JSON

    Dev->>CLI: cds render <profile>
    CLI->>Val: validate_profile(path)
    CLI->>Plan: build_plan(path)
    CLI->>Rend: render_compose(plan, output_path)
    Rend->>FS: write docker-compose.yml
    Rend-->>CLI: compose yaml
    CLI-->>Dev: Rendered compose file written

    Dev->>Docker: docker compose up
    Docker-->>Dev: Dagster + Postgres + Superset running
```

### `cds validate`

Validation is the gate every other command runs first. It never touches secrets
values or generates output — it only reports `Diagnostic`s.

```mermaid
sequenceDiagram
    participant CLI as main.py
    participant Val as validator.py
    participant Load as loader.py
    participant Schema as jsonschema
    participant Graph as graph.py

    CLI->>Val: validate_profile(profile_path)
    Val->>Load: load_yaml_file(profile.yaml)
    Load-->>Val: profile dict
    Val->>Schema: validate_profile_shape (profile.schema.json)
    Val->>Load: load each module.yaml (load_module_instances)
    Load-->>Val: module instances
    Val->>Schema: validate_module_configs (per-module configSchema)
    Val->>Graph: validate_dependency_graph(ids, dependsOn)
    Graph-->>Val: cycle? -> diagnostics
    Val->>Val: validate_secret_refs (secrets.* exist)
    Val->>Val: validate_contract_bindings (provides/consumes kind match)
    Val->>Val: validate_outputs
    Val-->>CLI: list[Diagnostic]
    Note over CLI: exit 1 if any level == "error"
```

### `cds security`

```mermaid
sequenceDiagram
    participant CLI as main.py
    participant Val as validator.py
    participant Sec as security.py
    participant Rules as security/rule-set.json

    CLI->>Val: validate_profile(path)
    Note over CLI: abort if validation has errors
    CLI->>Sec: run_security_validation(profile, rule_schema, rule_set)
    Sec->>Rules: load + validate rules (rule-schema.json)
    Sec->>Sec: evaluate rules vs profile<br/>(weak passwords, missing secrets,<br/>exposed services, unsafe defaults)
    Sec-->>CLI: findings[] + diagnostics
    Note over CLI: exit 1 if any finding severity == "high"
    CLI-->>CLI: print [SEVERITY] rule_id message + fixes
```

### `cds plan`

`build_plan` is where the composition is actually resolved: defaults applied,
contracts wired, secrets located (but **not** inlined — references stay as
`secrets.*` strings).

```mermaid
sequenceDiagram
    participant CLI as main.py
    participant Plan as planner.py
    participant Load as loader.py
    participant Secrets as secrets.py
    participant Res as resolver.py

    CLI->>Plan: build_plan(profile_path)
    Plan->>Load: load profile + each module.yaml
    Plan->>Secrets: load_profile_secrets(spec.secrets, env)
    Secrets-->>Plan: {name: value} from CDS_* env/.env
    loop each module instance
        Plan->>Plan: apply_defaults(config, configSchema)
        Plan->>Res: resolve_secret_refs (verify secrets.* exist)
    end
    Plan->>Res: resolve_provided_contracts (per module)
    Plan->>Res: resolve_consumed_contracts (match refs to producers)
    Note over Plan: emits E041/E042 on unknown module or kind mismatch
    Plan->>Plan: resolve_outputs (map profile outputs to contracts)
    Plan-->>CLI: plan { apiVersion: cds/v1alpha1, metadata,<br/>modules[], secrets, outputs }
```

### `cds render`

Render turns a validated plan into the Compose file. It accepts either a profile
(re-runs validate + plan) **or** a previously saved plan JSON file.

```mermaid
sequenceDiagram
    participant CLI as main.py
    participant Plan as planner.py
    participant Rend as renderer.py
    participant FS as Filesystem

    alt input is a saved plan file (apiVersion cds/v1alpha1)
        CLI->>CLI: load plan JSON directly
    else input is a profile
        CLI->>Plan: validate + build_plan(profile)
        Plan-->>CLI: plan
    end

    CLI->>Rend: render_compose(plan, output_path)
    loop each module (implementation.kind == docker-compose)
        Rend->>Rend: read compose.services / compose.volumes
        Rend->>Rend: substitute {{config.*}} and secrets.* via context
        Rend->>Rend: apply conditional healthcheck
        Rend->>Rend: prefix names -> "<module-id>-<service>"
    end
    Rend->>FS: write docker-compose.yml (yaml.safe_dump)
    Rend-->>CLI: (compose_yaml, diagnostics)
    CLI-->>CLI: "Rendered compose file written to <path>"
```

---

## Anatomy of the Generated `docker-compose.yml`

For the `local-dagster-postgres-superset` profile, render produces roughly:

```yaml
name: local-dagster-postgres-superset   # from profile.metadata.name
services:
  postgres-postgres:      # <module-id>-<service-name>
    image: postgres:16
    ports: ["5432:5432"]
    environment:
      POSTGRES_DB: analytics            # from config.database
      POSTGRES_PASSWORD: ${CDS_POSTGRES_PASSWORD}   # from secrets.postgres_password
    healthcheck: { ... }                # conditionally enabled (pg_isready)
  dagster-webserver:                    # Dagster fans out into 3 services
    build: ../images/dagster            # custom image (images/dagster/Dockerfile)
    ports: ["3000:3000"]
    depends_on: { postgres-postgres: { condition: service_healthy } }
  dagster-daemon:                       # conditionally enabled (config.daemon.enabled)
    build: ../images/dagster
  dagster-user-code:                    # user workspace (images/dagster/Dockerfile.user-code)
    build: ../images/dagster
  superset-app:
    image: apache/superset:6.1.0
    ports: ["8088:8088"]
volumes:
  postgres-data: {}                     # <module-id>-<volume-name>
```

A few things worth noting:

- **Namespacing:** every service and volume name is prefixed with its module id
  (`postgres-postgres`, `dagster-webserver`, …), so two modules can each declare a
  `web` service without colliding.
- **Fan-out:** a single module can emit multiple services. The Dagster module renders
  a webserver, an optional daemon, and a user-code service from its compose fragment.
- **Custom images:** modules may build from `images/<name>/` (e.g. the Dagster images)
  rather than pulling a public tag.
- **Substitution:** `{{ config.* }}` and `secrets.*` placeholders are resolved from
  the plan; `${CDS_*}` env references are left intact for Docker to resolve at
  `up` time.

> Note: CDS emits `name`, `services`, and (when present) `volumes`. It does **not**
> write a top-level `networks:` block — services share Docker Compose's default
> project network.

---

## Key Source Files

| File | Responsibility |
| --- | --- |
| `cli/main.py` | `argparse` entrypoint, command dispatch, profile-path + project-root resolution |
| `cli/validator.py` | Profile/module shape, config schemas, dependencies, contract bindings, outputs |
| `cli/graph.py` | Dependency-graph cycle detection |
| `cli/security.py` | Loads and evaluates `security/rule-set.json` findings |
| `cli/planner.py` | `build_plan` — apply defaults, resolve contracts + secrets, emit plan |
| `cli/resolver.py` | Parse `contractRef` and `secrets.*` references |
| `cli/secrets.py` | Load `CDS_*` secrets from environment / `.env` |
| `cli/renderer.py` | `render_compose` — plan → `docker-compose.yml` |
| `cli/loader.py` | YAML loading with diagnostics |
| `cli/diagnostics.py` | `Diagnostic` (error code, level, YAML path, message) |
| `cli/image_updates.py` | `cds list images` — check module images against registries |
| `schemas/*.schema.json` | JSON Schemas for profiles and modules |
| `security/rule-set.json` | Security rules (with `rule-schema.json` meta-schema) |
| `profiles/*/profile.yaml` | Runnable compositions |
| `modules/**/module.yaml` | Reusable capability definitions (config schema, contracts, compose fragment) |

---

*This document reflects the MVP engine: `validate → security → plan → render`.
`cds up` and `cds test` are on the roadmap but not yet implemented. See
[architecture.md](architecture.md) for the longer-term production-platform vision and
[roadmap.md](roadmap.md) for milestones.*
