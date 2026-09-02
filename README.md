# 🚀 Composable Data Stack (CDS)

> **Terraform for data platforms.**
> Build, validate, secure, and evolve data stacks using modular components and explicit contracts.

![Composable Data Stack logo](assets/branding/logo.svg)

---

## 🧠 What Is CDS (In 1 Minute)

Composable Data Stack (CDS) is a framework for defining and assembling data platforms from reusable modules such as orchestrators, warehouses, BI tools, and secrets providers.

## 🤝 Get Involved

- **Star and follow** on GitHub: [RonaldHensbergen/composable-data-stack](https://github.com/RonaldHensbergen/composable-data-stack)
- **Contribute**: open a discussion, file an issue, or send a PR to help shape CDS
- **Proof it**: if you run it in a real workflow, share your feedback — good or bad

> **Note:** Development helper tools are located in the `tools/` directory (git-ignored). See `tools/pr-cli/README.md` for PR creation scripts.

---

## ⚡ Why CDS

Instead of hardcoding integrations or relying on fragile pipelines, CDS introduces:

- 🔧 **Modules**: reusable components (Dagster, Postgres, Superset)
- 🔗 **Contracts**: explicit interfaces between components
- 🧩 **Profiles**: fully composed, runnable stacks

Think of it as Infrastructure as Code, but for data platforms.

Modern data platforms force a trade-off:

|Approach|Problem|
|---|---|
|Monolithic stack|Rigid, hard to evolve|
|Custom pipelines|Flexible but fragile and inconsistent|

CDS gives you the best of both:

- composability without chaos
- flexibility with guarantees
- modularity with structure
- no vendor lock-in by design

---

## 🎯 When To Use CDS

Use CDS if you:

- want to swap tools (Airflow ↔ Dagster, Superset ↔ Metabase)
- need reproducible environments across dev, CI, and prod
- are building a platform for multiple teams
- want contract-driven integration instead of implicit coupling

CDS may be overkill if:

- you only run a single-tool stack
- you do not need interchangeable components

---

## 🏗️ Example

The `local-dagster-postgres-superset` profile defines:

- Dagster -> orchestration
- Postgres -> storage
- Superset -> BI

### What CDS Does

1. Validates module definitions
2. Resolves contract bindings
3. Checks compatibility and security constraints
4. Produces a fully wired stack definition

`cds plan` resolves the full dependency graph before runtime configuration is generated, ensuring all module interactions are valid and predictable.

You can replace components without changing system behavior:

```text
Dagster -> Airflow
Superset -> Metabase
Postgres -> MariaDB
```

---

## 🗺️ Architecture Overview

CDS wires modules through **contracts**, not direct dependencies. This section has two levels: a high-level picture of what gets wired together ([Overview](#overview)), and a detailed look at what happens when you run a CDS command ([Internal Flow](#internal-flow)).

### Overview

Below, `local-dagster-postgres-superset` wires Dagster to Postgres to Superset through contracts:

```mermaid
flowchart TD
    Dagster[Dagster]
    Postgres[(Postgres)]
    Superset[Superset]

    Dagster -->|transformation-runner| Postgres
    Postgres -->|warehouse-query| Superset

    classDef tool stroke:#818cf8,fill:#eef2ff
    classDef database stroke:#2dd4bf,fill:#f0fdfa
    classDef viz stroke:#a78bfa,fill:#f5f3ff

    class Dagster tool
    class Postgres database
    class Superset viz
```

### Internal Flow

CDS splits into two phases: **compile-time**, where `cds` itself validates, resolves, and renders a plain `docker-compose.yaml`; and **runtime**, where the real `docker compose` binary builds and starts containers from that file. CDS never runs containers itself.

`cds test` runs the full compile-time pipeline in order — **validate → security → plan → render**. A `validate` (or `plan`) failure skips all downstream stages, but a **security** failure does not skip `plan`/`render`: those stages still run so `cds test` reports every stage's status in one pass, while the overall command still exits non-zero if any stage failed. `cds up` runs the same pipeline **minus security** (`validate → plan → render`), then hands off to `docker compose build`/`docker compose up`. See the [CLI table](#️-cli) below for exactly what each command runs.

- **Validate** checks profile shape, module configs, dependencies, secret refs, contract bindings, and outputs.
- **Security** (`cds test` only) runs rule-based checks against modules and resolved secrets; a failing check is reported but does not prevent `plan`/`render` from also running.
- **Plan** resolves contract bindings and substitutes secrets and defaults.
- **Render** generates the final `docker-compose.yaml`, with secret values as `${CDS_VAR}` placeholders; never the raw value.
- **Runtime** (`cds up` only): `docker compose build` (skippable with `--no-build`), then `docker compose up --detach`, then a live `cds state` view that polls until the stack settles (skippable with `--detach`). Build/up/log output is persisted to a log file (`.cds/logs/` by default). Docker Compose, not CDS, resolves `${CDS_VAR}` placeholders from a `.env` file (see `cds init`) and starts the containers.

Passing `validate → security → plan → render` proves the profile *compiles*
correctly — it is not proof that the resulting stack *runs* correctly. For the
broader success criteria used to certify a profile (booting the rendered
stack, service health, an end-to-end pipeline run, and downstream data
consumption in Superset), see
[docs/profile-testing/test-plan.md](docs/profile-testing/test-plan.md) and
[docs/profile-testing/failure-path-and-ci.md](docs/profile-testing/failure-path-and-ci.md).

```mermaid
flowchart TD
    subgraph compile["Compile-time (cds)"]
        direction TB
        Profile[/profile.yaml/]
        Validate[Validate]
        Security["Security checks<br/>(cds test only)"]
        Plan[Plan]
        Render[Render]
        Compose[/docker-compose.yaml/]
        Stop1((stops here))

        Profile --> Validate
        Validate -->|structural + contract checks| Security
        Security -->|rule-based checks| Plan
        Plan -->|resolve + substitute| Render
        Render --> Compose

        Validate -.->|E020, E041, E042, E081| Stop1
    end

    subgraph runtime["Runtime (docker compose, cds up only)"]
        direction TB
        Build["docker compose build<br/>(skip with --no-build)"]
        Up["docker compose up"]
        Env[(".env file")]
        Containers["running containers,<br/>real secret values injected"]

        Build --> Up
        Env -.->|resolves CDS_VAR| Up
        Up --> Containers
    end

    Compose --> Build

    classDef stage stroke:#818cf8,fill:#eef2ff
    classDef artifact stroke:#2dd4bf,fill:#f0fdfa
    classDef stop stroke:#f87171,fill:#fef2f2,stroke-dasharray: 3 3
    classDef runtimeNode stroke:#a78bfa,fill:#f5f3ff

    class Validate,Security,Plan,Render stage
    class Profile,Compose artifact
    class Stop1 stop
    class Build,Up,Env,Containers runtimeNode
```

This mirrors the [`cds` command table](#️-cli) below: `validate`, `plan`, and `render` are each callable on their own; `security` only runs as part of `cds test`, not `cds up`. Module and contract definitions follow the [Contract-First](#contract-first) design principle, so most of what "Validate" and "Plan" check comes directly from `module.yaml` and `profile.yaml`.

**See also:** [Security](#-security) for what the security stage checks, [Troubleshooting](#️-troubleshooting) for what each error code means and how to fix it, and [docs/architecture.md § Secrets and contract resolution](docs/architecture.md#secrets-and-contract-resolution) for a deeper look at how `secrets.*` and `contractRef` values actually resolve.

## 🔐 Security

CDS includes built-in security validation to prevent unsafe configurations before a stack is deployed.

The `cds security` checks analyze profiles and modules for common risks such as:

- weak or default passwords
- missing secret configurations
- insecure service exposure
- unsafe defaults in module configuration
- incomplete contract bindings that may leak data

Security checks run as part of validation and can be extended with custom rules.

### Example

```bash
cds security local-dagster-postgres-superset
```

---

## 📦 What You Get

When you run CDS:

- validated module graph
- resolved contract bindings
- dependency-aware execution plan
- generated Docker Compose configuration
- reproducible stack definition

This allows you to go from a declarative profile to a runnable local data stack.

---

## ✅ Prerequisites

CDS has separate compile-time and runtime requirements.

### Validate, Plan, And Render

The commands `cds validate`, `cds security`, `cds plan`, and `cds render`
do not require Docker. Install:

- Python 3.14 or newer, including `pip` and `venv`
- Git when cloning and installing CDS from source
- Internet access for the initial source and Python dependency downloads

### Build And Run A Stack

The `cds up` command and generated local profiles additionally require:

- Docker Engine (Linux) or Docker Desktop (macOS and Windows)
- Docker Compose v2, invoked as `docker compose`
- A running Docker daemon accessible to the current user
- Internet access for the initial container image pulls and builds, unless all
  required artifacts are already cached
- Free host ports required by the selected profile
- Write access to the checkout for `.env`, `docker-compose.yml`, and workdir
  data

Docker Desktop on Windows must use the WSL 2 backend. See the
[support policy](docs/support-policy.md) for supported operating systems and
runtime versions. For platform-specific setup from an empty machine, follow
the [complete installation guide](docs/installation.md).

For the complete example stack, allocate at least 8 GB of memory to Docker and
keep at least 10 GB of disk space free for images, build layers, volumes, and
logs. Larger workloads require additional resources.

### Preflight Check

Linux/macOS:

```bash
python3 --version
git --version
docker --version
docker compose version
docker info >/dev/null
```

Windows PowerShell:

```powershell
py --version
git --version
docker --version
docker compose version
docker info | Out-Null
```

Python must report version 3.14 or newer, Docker Compose must report v2, and
`docker info` must complete without a daemon or permission error. Git is
optional when CDS is installed from a package rather than source.

Tools such as `make`, Node.js, and `pre-commit` are contributor tooling and are
not required to run CDS profiles.

## 🚀 Quickstart

### CLI package preview

The Python distribution contains the `cds` command and its built-in security
rules. After production PyPI publishing is enabled, install the isolated CLI
with:

```bash
pipx install composable-data-stack
```

The package does not yet download profiles, modules, image build contexts, or
runtime workdirs. Continue with a repository checkout for the reference stack,
or configure external project assets with `CDS_PROFILE_PATH` and
`CDS_MODULE_PATH`. See [Packaging](docs/packaging.md) for the current TestPyPI
flow.

### 1. Clone

```bash
git clone https://github.com/RonaldHensbergen/composable-data-stack.git
cd composable-data-stack
```

### 2. Setup Environment

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Windows CMD:

```bat
py -m venv .venv
.venv\Scripts\activate.bat
python -m pip install -e .
```

If PowerShell blocks the activation script, run
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in the same
terminal session and activate the environment again.

### 3. Configure Environment

```bash
cds init local-dagster-postgres-superset
```

Set:

```text
CDS_ANALYTICS_DB_NAME
CDS_ANALYTICS_DB_USER
CDS_DAGSTER_DB_NAME
CDS_DAGSTER_DB_USER
CDS_SUPERSET_DB_NAME
CDS_SUPERSET_DB_USER
CDS_POSTGRES_SUPERUSER_PASSWORD
CDS_ANALYTICS_DB_PASSWORD
CDS_DAGSTER_DB_PASSWORD
CDS_SUPERSET_DB_PASSWORD
CDS_SUPERSET_SECRET_KEY
CDS_SUPERSET_ADMIN_PASSWORD
```

### 4. Validate A Stack

```bash
cds validate local-dagster-postgres-superset
```

Expected output:

```text
Profile is valid.
```

### 5. Check Runtime Prerequisites

```bash
cds preflight local-dagster-postgres-superset
```

This checks the runtime CLI and daemon, Compose support, required environment
values, and declared host ports without starting services. `cds init` only
creates configuration, while `cds validate` checks profile structure.

### 6. Run Security Checks

```bash
cds security local-dagster-postgres-superset
```

### 7. Generate A Plan

```bash
cds plan local-dagster-postgres-superset
```

This resolves:

- module dependencies
- contract bindings
- execution order

### 8. Render The Stack

```bash
cds render local-dagster-postgres-superset
```

By default, this writes `docker-compose.yml` to the project root.

Use a custom location when needed:

```bash
cds render local-dagster-postgres-superset --output build/docker-compose.yml
```

This generates:

- docker-compose.yml
- service definitions
- fully wired module configuration

### 9. Run The Stack

```bash
cds up local-dagster-postgres-superset
```

This runs `validate` → `plan` → `render` → `docker compose build` → `docker compose up` in one step.

`docker compose build` output is streamed to the terminal (grouped by image) and also persisted to a log file (default: `.cds/logs/up-<profile>-<timestamp>.log`; override with `--log-file`). `docker compose up` always runs detached: in the default mode its output goes only to the log file while the terminal shows the live state view; with `--detach` it is streamed to the terminal like the build output. Once the stack starts, `cds up` polls `docker compose ps` and redraws the same grouped/colored view as `cds state` until every service is healthy/running/exited or a timeout is hit (default 180s; override with `--timeout`; disable colored labels with `--no-color`). Container logs from that point on go only to the log file, not the terminal, since the terminal is showing the live state view. `cds up` exits `0` once the stack settles, or `1` on timeout or an unhealthy service.

Add `--detach` (or `-d`) to skip the live state view and return as soon as the stack starts, e.g. for scripting or CI:

```bash
cds up local-dagster-postgres-superset --detach
```

Use `--no-build` to skip the build step when images are already available:

```bash
cds up local-dagster-postgres-superset --no-build
```

### 9. Persistent Incoming Data Folder For Dagster

The Dagster module mounts a host directory into the containers so incoming files survive reboots.

- Host path: `workdirs/shared-data/incoming`
- Container path: `/app/data/cds/incoming`

Dagster includes a sensor that detects new files in `/app/data/cds/incoming` and runs a pickup job.
Picked files are moved to:

- Host path: `workdirs/shared-data/processed`
- Container path: `/app/data/cds/processed`

Create the directories once if they do not exist:

```bash
mkdir -p workdirs/shared-data/incoming workdirs/shared-data/processed
```

---

## 🧩 Core Concepts

### Modules

Reusable building blocks:

- orchestration (Dagster, Airflow)
- warehouse (Postgres, MariaDB)
- BI (Superset, Metabase)
- secrets (env, vault)

Structure:

```text
modules/<category>/<name>/
├── module.yaml
├── defaults.yaml
├── compose.yaml
├── scripts/
└── tests/
```

### Contracts

Contracts define how modules interact.

Examples:

|Contract|Purpose|
|---|---|
|sql-database|database interface|
|http-service|service exposure|
|secrets-provider|secret resolution|

Example binding:

```text
dagster.database -> postgres.sql-database
superset.database -> postgres.sql-database
```

No implicit dependencies. Everything is explicit.

### Profiles

Profiles define supported stacks:

```text
local-dagster-postgres-superset
local-airflow-postgres-superset
integration-airflow-postgres-dbt
```

Structure:

```text
profiles/[profile]/
├── profile.yaml
├── values.yaml
└── README.md
```

### Environment Overlays

A profile can be promoted from local development through staging to
production without duplicating its configuration. Add an `environments/`
directory next to `profile.yaml` containing one YAML file per environment;
each file is merged over the base profile when `--environment <name>` is
passed to a profile-consuming command:

```text
profiles/[profile]/
├── profile.yaml
└── environments/
    ├── dev.yaml
    └── prod.yaml
```

An overlay only needs to declare the values that differ from the base
profile. Module entries are merged by their stable `id` (not array
position), so an overlay can override just one field of one module:

```yaml
# profiles/[profile]/environments/prod.yaml
metadata:
  environment: production   # promotes the profile's security classification

spec:
  modules:
    - id: postgres
      config:
        storage:
          size: 20Gi
```

```bash
cds validate my-profile --environment prod
cds plan my-profile --environment prod
cds test my-profile --environment prod
cds up my-profile --environment prod
```

`local-dagster-postgres-superset` ships a working `dev`/`prod` example under
`profiles/local-dagster-postgres-superset/environments/` you can inspect or
copy.

Setting `metadata.environment` in an overlay also changes which security
policy `cds security`/`cds test` applies (see [Security](#-security)) —
promoting to `production` enables stricter checks, so a profile that passes
locally may report new findings once resolved with `--environment prod`.
That's expected: it surfaces settings that are fine for local development but
unsafe to carry into production.

Compare what an overlay actually changes with `cds diff`, without ever
printing a secret value (profiles only ever hold secret *references*, never
resolved values):

```bash
cds diff my-profile --from dev --to prod
```

```text
Differences from 'dev' to 'prod':

  ~ metadata.environment: "development" -> "production"
  ~ spec.modules[postgres].config.storage.size: "2Gi" -> "20Gi"
```

Persist an environment default for a project with `cds config set environment
prod`; commands without `--environment` then use that overlay. An explicit
`--environment` always takes precedence.

---

### Profile Composition (`extends`)

A profile can also factor out shared configuration into one or more parent
profiles instead of duplicating it, using a top-level `extends` field:

```yaml
# profiles/analytics-prod/profile.yaml
apiVersion: cds/v1alpha1
kind: Profile
metadata:
  name: analytics-prod
  environment: production
extends:
  - analytics-base   # a bare name resolves to profiles/analytics-base/profile.yaml
spec:
  modules:
    - id: postgres
      config:
        storage:
          size: 20Gi
```

`extends` accepts a non-empty list of parent references, each either a bare
profile name (resolved under the profiles root) or a path relative to the
child profile's directory (e.g. `../shared/profile.yaml`). Parents are
resolved and merged left-to-right — later parents win over earlier ones —
and then the child profile's own document is merged on top of all parents.
Composition uses the exact same deep-merge/module-merge-by-id engine as
environment overlays: mappings merge recursively, `spec.modules` entries
merge by stable `id`, and any other array is replaced wholesale rather than
concatenated. Parent profiles may themselves use `extends` (chains are
resolved transitively).

`extends` is not a CLI flag — it's read directly from `profile.yaml`, so
every profile-consuming command resolves it automatically, with no new
syntax to learn:

```bash
cds validate analytics-prod
cds plan analytics-prod
cds up analytics-prod
```

A profile can extend more than one parent, which is merged in the order
listed (later entries win over earlier ones):

```yaml
extends:
  - networking-base
  - observability-base
```

`extends` and `--environment` compose together: parents are merged first,
then the child, then the selected environment overlay is applied on top of
that fully-composed result — so a shared base profile and environment
promotion can both be used without duplicating configuration in either
dimension.

A malformed `extends` chain fails validation/planning before anything else
runs, with a dedicated diagnostic code:

|Code|Meaning|
|---|---|
|E110|`extends` is missing, not a list, empty, or contains a non-string/empty entry|
|E111|A parent reference resolves outside the profiles root|
|E112|A referenced parent profile does not exist|
|E113|A cycle was detected in the `extends` chain|

---

## ⚙️ CLI

|Command|Description|
|---|---|
|cds get \<profile\> [--remote \<owner/repo\>] [--ref \<ref\>] [--local \<dir\>] [--into \<dir\>]|Fetch a profile plus its dependent module/runtime assets from GitHub into a local CDS layout|
|cds list profiles\|modules\|images [--remote \<owner/repo\>] [--ref \<ref\>] [--local \<dir\>]|List available profiles, module sources, or module images and check for newer versions; add `--remote`/`--local` to inspect another repository before fetching from it|
|cds init [profile]|Generate a project `.env` template from profile secret definitions|
|cds validate [profile]|Validate modules and contracts|
|cds preflight [profile]|Check runtime tools, required environment values, and host ports without starting services|
|cds plan [profile]|Resolve dependencies and generate an execution plan|
|cds render [profile]|Generate Docker Compose configuration from a resolved plan|
|cds up [profile]|Validate, plan, render, build, and start services with docker compose; logs output to a file and shows a live `cds state` view until the stack settles (use `--no-build` to skip build, `--detach` to skip the live view, `--log-file`/`--timeout`/`--no-color` to override defaults)|
|cds state [profile]|Show running service status grouped by health (use `--no-color` to disable colored labels)|
|cds test [profile]|One-shot smoke validation: validate, security, plan, and render|
|cds security [profile]|Run rule-based security validation on a profile|
|cds diff [profile] --from \<env\> --to \<env\>|Show effective configuration differences between two environment overlays, secrets never included|
|cds use [profile] [--clear]|Save (show/clear) a default profile so it doesn't have to be passed to other commands|
|cds config get\|set\|unset\|list|Manage persisted project defaults in `.cds/config.json`|
|cds completion \<bash\|zsh\|powershell\>|Print shell setup instructions for tab-completion|

`init`, `validate`, `preflight`, `plan`, `render`, `up`, `test`, and `security`
all accept `--environment <name>` (or `-e <name>`) to merge
`environments/<name>.yaml` over the base profile before resolving; see
[Environment Overlays](#environment-overlays).

`cds get` copies the selected `profiles/<name>/` tree, every referenced module
directory, and any local build-context assets referenced by those modules'
Dockerfiles. By design it downloads from GitHub rather than a local checkout:
by default it fetches this project's upstream repository at the `main` branch,
downloading a tarball via the GitHub API (no `git` binary required). Use
`--remote <owner/repo>` (or a `github.com/...` URL) to fetch a fork, and
`--ref <branch|tag|sha>` to select a specific revision. Pass `--local <dir>`
to use an existing local directory instead of downloading (mutually exclusive
with `--remote`/`--ref`) for offline/dev workflows. Use `--into` to choose a
destination root, `--dry-run` to inspect the copy plan first, and `--force` to
replace conflicting local files. Successful fetches record tracking metadata
in `.cds/get-manifest.json` for future update workflows.

`cds list profiles`, `cds list modules`, and `cds list images` accept the same
`--remote`/`--ref`/`--local` source-repository selection as `cds get`, so you
can discover what's available in another repository (a fork, or an existing
local checkout) before running `cds get` against it. Without these flags,
`cds list` inspects the local project as before.

### Project defaults

`cds config` manages the gitignored `.cds/config.json` file (or the path in
`CDS_CONFIG_PATH`). Supported settings are `profile`, `environment`, and
`security.strict`:

```bash
cds config set profile my-profile
cds config set environment prod
cds config set security.strict true
cds config list
```

`profile` is stored as its resolved path, and `environment` is validated
against that profile's `environments/` directory. `security.strict true`
applies the existing production security rules even if the profile declares a
local environment. `cds use` remains a shortcut for setting, showing, or
clearing `profile`.

CLI flags take precedence over these defaults. In particular,
`--environment` overrides `config environment`; `CDS_PROFILE_PATH` continues
to override the saved profile.

`[profile]` accepts:

| Form | Example |
| ---- | ------- |
| Profile name | `local-dagster-postgres-superset` |
| Path to a `profile.yaml` file | `profiles/local-dagster-postgres-superset/profile.yaml` |
| Path to a profiles root directory | `profiles/` |

When `[profile]` is omitted, resolution falls back in order to: `CDS_PROFILE_PATH` if set (accepts the same three forms), then the default profile saved via `cds config set profile` (or `cds use <profile>`), then the single profile under `profiles/` if there is exactly one. An explicitly-set env var takes precedence over the persisted project default, matching common CLI convention (env vars are per-invocation and reflect the current session more reliably than a saved, gitignored default that's easy to forget about).

To view the full list of options for any command, use the `--help` flag:

```bash
cds --help
cds validate --help
cds plan --help
```

---

## 🪟 Windows Task Runner

Windows contributors without `make` can use `Makefile.ps1`, a PowerShell equivalent covering the core developer tasks:

```powershell
# Install in editable mode
.\Makefile.ps1 install

# Validate the default profile
.\Makefile.ps1 validate

# Validate a specific profile
.\Makefile.ps1 validate-profile -P profiles/local-dagster-postgres-superset/profile.yaml

# Build distribution packages
.\Makefile.ps1 package

# List available targets
.\Makefile.ps1 help
```

This does not replace the Linux/macOS `Makefile`, both exist side by side. Windows users can still install `make` via WSL or Chocolatey if they prefer the original workflow. `lint` and `docker-build` are not ported here. Run `yamllint .` and `npx markdownlint-cli` directly, or use `pre-commit` if it is set up in this repo. Docker Desktop's `docker build` works the same on Windows as it does elsewhere.

---

## 🛠️ Troubleshooting

Common errors from `cds validate`, `cds plan`, and `cds render`, and how to fix them.

| Error | Cause | Fix |
| --- | --- | --- |
| `[E020] ... YAML file not found: <path>` | The profile identifier or file path passed to `cds validate`, `cds plan`, or `cds render <profile>` doesn't resolve to an existing YAML file. | Run `cds list profiles` to see valid identifiers. Set `CDS_PROFILE_PATH` to a profile name, a `profile.yaml` file path, or a profiles root directory. |
| `[E081] ... Required secret "CDS_X_PASSWORD" not found in environment` | A secret marked `required: true` in the profile's `spec.secrets.values` is missing from the shell environment or the `.env` file in the current working directory. | Run `cds init <profile>` to generate `.env` in the project root, set the missing `CDS_*` variable, or export it directly before running the command. |
| `[E041] ... Contract ref "x.y" points to unknown module "x"` | A `consumes` binding's `contractRef` refers to a module ID that isn't defined in the profile. | Check `spec.modules` for the correct module `id`, and confirm the contract ref follows `<module-id>.<contract-name>`. |
| `[E041] ... but it does not provide "<contract-name>"` | The referenced module exists, but its `spec.provides` list doesn't expose that contract name. | Check the producing module's `module.yaml` for the contracts it actually provides, and fix the consumer's `contractRef` to match. |
| `[E042] ... Contract kind mismatch` | The consumer expects one contract kind (e.g. `sql-database`) but the producer exposes a different kind. | Point the binding at a module that provides the expected contract kind, or update the consumer's expected kind if the mismatch is intentional. |
| `[E103] ... config.image.tag is required ... when config.image.source is "registry"` | A module instance sets `config.image.source: registry` without also setting `config.image.tag`. | Set `config.image.tag` to a version, or switch back to `config.image.source: build`. |
| `[W097] ... config.image.tag is "latest" with config.image.source "registry"` | `config.image.tag: latest` under `source: registry` still validates, but drifts silently between deploys instead of pinning a reproducible version. | Pin an explicit tag from `publish-images.yml`'s output (or `tests/fixtures/signed-images.json`) instead of `latest`. |

All diagnostics print with their error code and YAML path (e.g. `spec.modules[1].config`), so search the profile file for that path to find the exact line to fix.

---

## 🔄 Workflow

```text
1. cds validate -> check module definitions
2. cds security -> detect unsafe configurations
3. cds plan -> resolve dependencies and bindings
4. cds render -> generate Docker Compose stack
5. cds up -> start services
6. cds test -> one-shot validate + security + plan + render smoke check
```

---

## 📂 Repository Structure

```text
.
├── cli/
├── modules/
│   ├── bi/
│   ├── orchestration/
│   ├── secrets/
│   └── warehouse/
├── profiles/
├── docs/
├── pyproject.toml
└── Makefile
```

---

## 🧱 Design Principles

### Contract-First

Modules declare:

- what they provide
- what they require
- configuration inputs
- health checks
- lifecycle hooks

### Profile-Driven

Profiles define supported stacks.
The profile is the unit of support, not individual modules.

### Zero Hidden Coupling

- no implicit environment variables
- no cross-module assumptions
- no shared mutable state

All interactions happen through explicit contracts.

### Security By Default

CDS validates configurations before runtime, ensuring that:

- weak credentials are detected early
- secrets are properly configured
- services are not unintentionally exposed

Security is part of platform composition, not an afterthought.

### One Model, Multiple Environments

The same composition model applies across:

- local development
- CI environments
- production

Only runtime packaging differs.

---

## 📊 Comparison

|Capability|Monolith|Custom pipelines|CDS|
|---|---|---|---|
|Swap components|❌|⚠️|✅|
|Reuse modules|❌|❌|✅|
|Explicit contracts|❌|❌|✅|
|Reproducibility|⚠️|⚠️|✅|
|Security validation|❌|❌|✅|
|Vendor lock-in|✅|⚠️|❌|

---

## 📌 Status

MVP ready:

- module validation
- contract resolution
- security checks
- profile composition
- Docker Compose rendering

Next:

- runtime orchestration
- Kubernetes support
- advanced secret providers
- stack bootstrap and health checks

See [docs/roadmap.md](docs/roadmap.md) for milestones and detailed status.
See [docs/support-policy.md](docs/support-policy.md) for OS support policy and platform-specific limitations.

---

## 🤝 Contributing

Contributions are welcome.

Please read these first:

- [CONTRIBUTING.md](CONTRIBUTING.md)
- [docs/maintainer-merge-policy.md](docs/maintainer-merge-policy.md)
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- [SECURITY.md](SECURITY.md)
- [SUPPORT.md](SUPPORT.md)
- [CHANGELOG.md](CHANGELOG.md)
- [RELEASE.md](RELEASE.md)

Good first contributions:

- adding new modules
- improving profile examples
- extending contract definitions
- adding validation or security rules

---

## 📖 Documentation

- [Quickstart](README.md#-quickstart) — get running in 5 minutes
- [From Docker Compose to CDS Profile](docs/from-docker-to-cds-profile.md) — complete transformation guide
- [Architecture](docs/architecture.md) — design and core concepts
- [Modules](docs/modules.md) — how to structure reusable components
- [Roadmap](docs/roadmap.md) — planned features and milestones

---

## 📜 License

See `LICENSE`.
