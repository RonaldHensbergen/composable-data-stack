# Best-Practice Repository Layout: Platform Assets vs Project Code

Where should modules and profiles live relative to the Dagster pipelines,
dbt/dlt projects, and database scripts you build on top of the stack? This
doc gives the recommended split and shows the supported setups, from a single
checkout for local evaluation to fully separate repos for real projects.

## TL;DR

- **Modules and profiles are platform assets.** Keep them in the CDS
  repository (or a pinned fork of it). They carry contracts, config schemas,
  security rules, and release gates, and they are validated by this repo's
  CI.
- **Pipelines, transformation code, and your database scripts are project
  assets.** Keep them in your own project repository and mount them into the
  stack through profile `hostPath` entries. The `workdirs/` directory in a
  CDS checkout exists for the example stacks and local evaluation, not as a
  permanent home for your code.
- CDS supports both layouts: profiles and modules resolve from a repository
  checkout or from external roots via `CDS_PROFILE_PATH` / `CDS_MODULE_PATH`,
  and project code is bind-mounted into containers, never imported by the
  platform.

## What belongs where

| Asset | Layer | Lives in | Examples |
| --- | --- | --- | --- |
| `module.yaml` (+ module-local `config/`, `scripts/`) | platform | `modules/` / `modules-experimental/` in the CDS repo, or another root via `CDS_MODULE_PATH` | `modules/warehouse/postgres` |
| `profile.yaml` + `environments/*.yaml` overlays | platform composition | `profiles/` in the CDS repo, or your own profiles root via `CDS_PROFILE_PATH` | `profiles/local-dagster-postgres-superset-dbt` |
| Service bootstrap scripts (create databases/users for platform services) | platform, profile-adjacent | next to the profile (referenced by a module's `initDbScript`) | `profiles/local-dagster-postgres-superset-dbt/init-db.sh` |
| Dagster definitions, dbt project, dlt pipelines, DuckDB scripts | project workload | your project repo, mounted via `hostPath` | `workdirs/dagster/definitions.py`, `workdirs/dbt/` in the checkout |
| Schema and data transformations (dbt models, seeds, your migrations) | project | your project repo | `workdirs/dbt/models/` |
| Secrets (passwords, tokens) | never in any repo | environment / secret manager; the profile only maps an alias to an env var name | `secrets.analytics_db_password` → `CDS_ANALYTICS_DB_PASSWORD` |

Note the distinction between the two kinds of "database script": a script
that creates the platform's own databases and users (referenced by a
module's `initDbScript`) belongs with the profile; schema and data
transformations (dbt models, seeds, migrations you own) belong with the
project.

## Why the split

1. **Different release cadence and review ownership.** Modules go through
   contract-schema validation, E-code diagnostics, golden tests, security
   rules, and the release gates in [RELEASE.md](../RELEASE.md). Your pipelines
   and models change at a different pace and under different reviewers;
   co-locating them muddies both.
2. **A clear trust boundary.** Module `source:` paths must resolve under a
   `modules/` or `modules-experimental/` root — absolute paths and traversal
   are rejected with E022 (`cli/loader.py`; risk PLR-04 in
   [cra-risk-assessment.md](cra-risk-assessment.md)). Everything under a
   modules root is reviewed platform code; keeping project code out of those
   directories keeps that guarantee meaningful.
3. **Secrets hygiene.** Profiles are safe to commit because they only map
   secret aliases to environment-variable names — values are resolved by
   Docker Compose at container start and CDS never sees them. Database
   scripts, however, tend to accumulate credentials; keep those in your
   project repo with a gitignored `.env`, or better, in a secret manager.
4. **Packaging model.** The pip/pipx-installed `cds` CLI ships the engine and
   bundled rules only — profiles, modules, image build contexts, and workdirs
   come from a repository checkout or external paths
   ([installation.md](installation.md), [packaging.md](packaging.md)). The
   tool is designed for exactly this split.

## The seams: how the two sides connect

- **Module wiring** — a profile references modules with a relative `source:`
  (e.g. `../../modules/warehouse/postgres`), resolved under an allowed
  modules root; with `CDS_MODULE_PATH` set, `source:` is relative to that
  root instead (see [cli-reference.md](cli-reference.md)).
- **Code mounting** — workload code enters containers through bind mounts
  configured in the profile, e.g. the dagster module's
  `definitionsFile.hostPath` / `sharedData.hostPath`. Relative `hostPath`
  values resolve against the project root — the nearest ancestor of the
  profile's directory containing a `.cds`, `pyproject.toml`, or `.git`
  marker, falling back to the current directory — which is also where the
  rendered `docker-compose.yml` is written and from which Compose resolves
  relative bind sources (`cli/security_common.py:resolve_project_root`). The
  profile never reads your code; the container does.
- **Runtime wiring** — services find each other through contracts
  (`contractRef: postgres.sql-database`), never through shared files.
- **Environment variation** — `environments/<name>.yaml` overlays are
  deep-merged over the base profile with `--environment <name>`, so they can
  also override module config such as a `hostPath` per environment;
  `cds config set environment <name>` persists that choice. Note that
  `cds config` only accepts CLI-level keys (`profile`, `environment`,
  `security.strict`, `image.source`, `target`, `audit.enabled`,
  `get.allowedSources`) — it cannot set `hostPath` directly, and profile
  config strings can instead be parameterized with `${CDS_*}` env
  placeholders resolved from `.env`. `cds use <profile>` persists a
  per-project default profile in the gitignored `.cds/config.json`.

## Recommended setups

### A. Single checkout — local evaluation and small teams

Clone the CDS repo, put your code under `workdirs/`, and run `cds` from the
repo root:

```bash
git clone https://github.com/RonaldHensbergen/composable-data-stack.git
cd composable-data-stack
# edit workdirs/dagster/definitions.py, workdirs/dbt/, ...
cds up local-dagster-postgres-superset-dbt
```

This is what the shipped example profiles assume. It is fine while the
project *is* the example stack. Two watch-outs: do not let project code grow
into `modules/` or `profiles/` (see the trust boundary above), and when you
remove the checkout, preserve your workdirs first — see
[installation.md](installation.md) ("Removing CDS").

### B. Separate project repo — recommended beyond local development

Keep the platform in a CDS checkout (or fork) and your code in your own repo:

```text
my-data-project/
├── profiles/
│   └── local/
│       ├── profile.yaml        # adapted copy: hostPath points at ../workdirs/...
│       └── init-db.sh
├── workdirs/
│   ├── dagster/definitions.py
│   ├── dbt/
│   └── dlt/
└── .env                        # gitignored; provides the CDS_* secret values
```

Name the two paths explicitly as variables so every command shows what it
refers to:

```bash
# The platform checkout: provides modules/ and modules-experimental/.
export CDS_CHECKOUT=/path/to/composable-data-stack

# What CDS_PROFILE_PATH refers to: one of three forms — a profile name, a
# profiles root directory, or a single profile.yaml file. Here: this
# project's own profile file.
export CDS_PROFILE_PATH=$PWD/profiles/local/profile.yaml

# Module `source:` values resolve relative to this root, so keep the
# `modules/...` prefix in the profile (source: modules/warehouse/postgres).
export CDS_MODULE_PATH=$CDS_CHECKOUT

cd my-data-project
cds validate
cds up
```

(With `CDS_MODULE_PATH` pointing directly at a `modules/` directory instead,
drop the `modules/` prefix from each `source:`; pointing at the checkout root
as above covers both `modules/` and `modules-experimental/`. See
[installers.md](installers.md) for the full `CDS_PROFILE_PATH` forms.)

Relative `hostPath` entries in the profile resolve against that same project
root — the `my-data-project/` checkout itself, found via its `.git` marker —
not against the directory you happen to run `cds` from, so
`hostPath: ./workdirs/...` works from anywhere as long as the profile lives
inside the project.

### C. Pinned fork or vendored ref — platform as a dependency

For teams that customize modules: fork CDS, keep the fork as the platform
repo, and reference it from your project repo as in Option B. Upgrades become
an explicit, reviewable act — bump the path or ref, run `cds validate`,
review the render diff — the same way you treat any dependency (Renovate can
watch the ref).

## Rules of thumb

- Profiles and modules: platform repo. Pipelines, dbt/dlt code, your
  database scripts: project repo.
- Never commit secret values — not in profiles (aliases only) and not in
  database scripts.
- Keep your profile inside your project repo so the marker-based project
  root — and with it relative `hostPath` mounts — resolves consistently,
  regardless of where you run `cds` from.
- If you outgrow the checkout, the migration path is: move `workdirs/` and
  your profile into your own repo, set `CDS_PROFILE_PATH` / `CDS_MODULE_PATH`,
  and keep the CDS checkout purely as the module source.

## Related documentation

- [Installation](installation.md) — checkout vs external roots
- [Installers and environment variables](installers.md) — the three
  `CDS_PROFILE_PATH` forms, `CDS_MODULE_PATH`, `cds use`
- [CLI reference](cli-reference.md) — `[profile]` resolution order and module
  `source:` rules
- [Modules](modules.md) — the module contract and directory structure
- [Architecture](architecture.md) — layers, contracts, profile-driven support
- [Packaging](packaging.md) — what the pip-installed CLI does and does not
  bundle
- [From Docker Compose to CDS Profile](from-docker-to-cds-profile.md) —
  migrating an existing compose setup
