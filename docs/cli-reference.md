# CLI reference

Full reference for every `cds` subcommand. For a 5-minute walkthrough of the
common ones (`validate`, `plan`, `render`, `up`), see the
[Quickstart](../README.md#-quickstart) instead.

|Command|Description|
|---|---|
|cds get \<profile\> [--remote \<owner/repo\>] [--ref \<ref\>] [--commit \<sha\>] [--local \<dir\>] [--into \<dir\>]|Fetch a profile plus its dependent module/runtime assets from GitHub into a local CDS layout|
|cds list profiles\|modules\|images [--remote \<owner/repo\>] [--ref \<ref\>] [--local \<dir\>]|List available profiles, module sources, or module images and check for newer versions; add `--remote`/`--local` to inspect another repository before fetching from it|
|cds init [profile]|Generate a project `.env` template from profile secret definitions|
|cds validate [profile]|Validate modules and contracts; use `--target helm` for Kubernetes checks|
|cds preflight [profile]|Check runtime tools, required environment values, and host ports without starting services|
|cds plan [profile]|Resolve dependencies and generate an execution plan|
|cds render [profile]|Generate Docker Compose or a Helm chart from a resolved plan|
|cds up [profile]|Validate, plan, render, and start the Compose or Helm target; use `--target helm` for Kubernetes|
|cds down [profile]|Stop Compose or uninstall a Helm release; Helm PVCs are retained by default|
|cds state [profile]|Show Compose services or Kubernetes workloads grouped by health|
|cds test [profile]|One-shot smoke validation: validate, security, plan, and render|
|cds security [profile]|Run rule-based security validation on a profile|
|cds report [profile] [--json] [--output \<path\>]|Export a compliance/evidence report for a rendered stack (modules, image signature/SBOM/provenance evidence, contract topology, secret-leak check); see [compliance-report.md](compliance-report.md)|
|cds diff [profile] --from \<env\> --to \<env\>|Show effective configuration differences between two environment overlays, secrets never included|
|cds generate-profile \<input\|-\> [--name \<name\>] [--force]|Persist a JSON/YAML profile document (file path or stdin) to `profiles/<name>/profile.yaml`|
|cds compose-profile [profile] --add-module \<source\> [--bind \<name\>=\<moduleId.contract\>] [--set \<path\>=\<value\>] [--secret \<alias\>=\<ENV_VAR\>] [--write\|--output \<path\>]|Merge a new module instance into an existing profile, auto-resolving unambiguous contract bindings/dependencies and reporting ambiguous or missing ones|
|cds use [profile] [--clear]|Save (show/clear) a default profile so it doesn't have to be passed to other commands|
|cds config get\|set\|unset\|list|Manage persisted project defaults in `.cds/config.json`|
|cds completion \<bash\|zsh\|powershell\>|Print shell setup instructions for tab-completion|

`init`, `validate`, `preflight`, `plan`, `render`, `up`, `test`, `security`,
and `report` all accept `--environment <name>` (or `-e <name>`) to merge
`environments/<name>.yaml` over the base profile before resolving; see
[Environment Overlays](../README.md#environment-overlays).

## `cds get`

Copies the selected `profiles/<name>/` tree, every referenced module
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
in `.cds/get-manifest.json` (including the resolved commit SHA) for future
update workflows.

Source trust is opt-in. Set `cds config set get.allowedSources
"owner,owner/repo"` (or the `CDS_GET_ALLOWED_SOURCES` environment variable,
which takes precedence when non-empty) to restrict `cds get` and `cds list --remote` to the
listed owners or exact repositories; anything else fails before downloading.
The check applies to the requested `owner/repo`, not to any repository GitHub
redirects it to, and `--local` sources are not subject to it.
The upstream default remote is only allowed if it is on the list. Pass
`--commit <sha>` (12-40 hex characters; a full SHA is recommended) to pin a fetch: it fails closed, without
writing anything, if `--ref` resolves to a different commit. `--commit` cannot
be combined with `--local`.

## `cds list`

`cds list profiles`, `cds list modules`, and `cds list images` accept the same
`--remote`/`--ref`/`--local` source-repository selection as `cds get`, so you
can discover what's available in another repository (a fork, or an existing
local checkout) before running `cds get` against it. Without these flags,
`cds list` inspects the local project as before.

## `cds compose-profile`

Merges a new module instance into an existing profile instead of hand-editing
its `profile.yaml`: it resolves each of the new module's `consumes` entries
against contracts already `provides`d by the profile's existing modules,
auto-binding when exactly one candidate matches a consume entry's contract
kind (and adding the matching `dependsOn` automatically), or reporting an
error naming the candidates when a consume entry has zero or multiple matches
so you can disambiguate with `--bind <name>=<moduleId>.<providedContract>`.
Use `--set <config.path>=<value>` for any other config field, and
`--secret <alias>=<ENV_VAR>` for every `secrets.<alias>` reference the new
module's config ends up using that isn't already defined on the profile. By
default the merged profile is printed to stdout; pass `--write` to persist it
back to the resolved `profile.yaml`, or `--output <path>` to write it
elsewhere (e.g. before handing it to `cds generate-profile`).

`--add-module <source>` must be written the same way as the `source:` values
already in the target `profile.yaml` -- relative to the profile's own
directory, not the repository root (e.g. `../../modules/identity/keycloak`
for a profile under `profiles/<name>/`) -- unless `CDS_MODULE_PATH` is set, in
which case it's relative to that root instead, matching `cds validate`.

## Project defaults (`cds config`)

`cds config` manages the gitignored `.cds/config.json` file (or the path in
`CDS_CONFIG_PATH`). Supported settings are `profile`, `environment`,
`security.strict`, and `target`:

```bash
cds config set profile my-profile
cds config set environment prod
cds config set security.strict true
cds config set target helm
cds config list
```

`profile` is stored as its resolved path, and `environment` is validated
against that profile's `environments/` directory. `security.strict true`
applies the existing production security rules even if the profile declares a
local environment. `target` (`compose` or `helm`) is the default runtime
target used by `validate`, `render`, `up`, `down`, `test`, `state`, and
`security` when their own `--target` flag is omitted -- handy when working
against a Helm/Kubernetes profile for an extended session so `--target helm`
doesn't need repeating on every command. `cds use` remains a shortcut for
setting, showing, or clearing `profile`.

CLI flags take precedence over these defaults. In particular,
`--environment` overrides `config environment`; `CDS_PROFILE_PATH` continues
to override the saved profile.

## Resolving `[profile]`

`[profile]` accepts:

| Form | Example |
| ---- | ------- |
| Profile name | `local-dagster-postgres-superset` |
| Path to a `profile.yaml` file | `profiles/local-dagster-postgres-superset/profile.yaml` |
| Path to a profiles root directory | `profiles/` |

When `[profile]` is omitted, resolution falls back in order to:
`CDS_PROFILE_PATH` if set (accepts the same three forms), then the default
profile saved via `cds config set profile` (or `cds use <profile>`), then the
single profile under `profiles/` if there is exactly one. An explicitly-set
env var takes precedence over the persisted project default, matching common
CLI convention (env vars are per-invocation and reflect the current session
more reliably than a saved, gitignored default that's easy to forget about).

## Per-command help

To view the full list of options for any command, use the `--help` flag:

```bash
cds --help
cds validate --help
cds plan --help
```
