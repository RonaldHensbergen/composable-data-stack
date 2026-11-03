# Underlying Commands: Running the Stack Without `cds`

The `cds` CLI is a convenience wrapper. Everything it does at runtime is a
standard, CLI-independent artifact or command:

- `cds render` writes a plain **Docker Compose file** (`<project-root>/docker-compose.yml`)
  or a plain **Helm chart directory** (`<project-root>/chart/`).
- `cds up` / `cds state` / `cds down` are plain `docker compose` / `helm` /
  `kubectl` / `k3d` invocations against those artifacts.

So the rendered stack is **not locked into the CLI**: after `cds init` +
`cds render`, you can drive it with `docker compose`, `helm`, and `kubectl`
directly — in CI, in GitOps pipelines, or after migrating away from `cds`
entirely.

```bash
# Compose target, standalone (from the project root):
docker compose -f docker-compose.yml up -d

# Helm target, standalone:
helm upgrade --install <release> chart/ --namespace <ns> --create-namespace \
  --values secrets.local.yaml --timeout 300s
```

This document maps every lifecycle-relevant `cds` command to the exact
underlying command(s), verified line-by-line against the CLI source. Code
references point at `cli/` files; line numbers refer to the commit at which
this doc was written — the function names are the stable anchor.

## Contents

- [The rendered artifacts are standard](#the-rendered-artifacts-are-standard)
- [Command mapping at a glance](#command-mapping-at-a-glance)
- [`cds init` and the `.env` file](#cds-init-and-the-env-file)
- [`cds render`](#cds-render)
- [`cds up` (Compose target)](#cds-up-compose-target)
- [`cds up --target helm` (Kubernetes target)](#cds-up---target-helm-kubernetes-target)
- [`cds state`](#cds-state)
- [`cds down`](#cds-down)
- [What `cds` adds on top](#what-cds-adds-on-top)

## The rendered artifacts are standard

| Artifact | Written by | Standard? |
| --- | --- | --- |
| `<project-root>/docker-compose.yml` | `cds render` / `cds up` (compose) | Yes — a plain Compose file. `docker compose -f docker-compose.yml <cmd>` works as-is. |
| `<project-root>/traefik/dynamic/cds-routes-<module>.yml` | same render, next to the compose file (`cli/renderer.py:250`, `:255`, `:697`) | Yes — Traefik file-provider documents, mounted by the compose file. |
| `<project-root>/chart/` (`Chart.yaml`, `values.yaml`, `templates/`, ...) | `cds render --target helm` / `cds up --target helm` | Yes — a plain Helm chart. `helm upgrade --install <release> chart/ ...` works as-is. Contains no secret values, only `CDS_*` key names. |
| `<project-root>/.env` | `cds init` | Yes — a standard Compose env file. Compose picks it up automatically when the compose file lives at the project root. |

## Command mapping at a glance

| `cds` command | Underlying command(s) | Code reference |
| --- | --- | --- |
| `cds render <profile>` | writes `<project-root>/docker-compose.yml` (compose) or `<project-root>/chart/` (helm) | `cli/main.py:2279`, `:2320`, `:2384`; `cli/renderer.py:44`; `cli/k8s_renderer.py:88` |
| `cds up <profile>` (compose) | `docker compose -f docker-compose.yml build` (skipped with `--no-build`), then `docker compose -f docker-compose.yml up --detach`; log tail `docker compose -f docker-compose.yml logs -f --no-color` into `.cds/logs/up-<profile>-<ts>.log`; settle poll `docker compose -f docker-compose.yml ps -a --format json` | `cli/main.py:2430`, `:2574`, `:2585`, `:2613`; `cli/up_runner.py:161`, `:370` |
| `cds up <profile> --target helm` | optional `k3d cluster create <name> --wait` (`--create-cluster`); `docker build -t <image> ...` for local images; `k3d image import <images> --cluster <name>`; then `helm [--kube-context X] upgrade --install <release> chart/ --namespace <ns> --create-namespace --values <secrets.yaml> --timeout <N>s` | `cli/main.py:2467`; `cli/k8s_provision.py:296`, `:426`, `:490`; `cli/k8s_runner.py:131` |
| `cds state <profile>` | compose: `docker compose -f docker-compose.yml ps -a --format json`; helm: `kubectl --namespace <ns> get deployment,statefulset,job -l app.kubernetes.io/instance=<release> -o json` | `cli/main.py:3075`, `:3105`, `:3087`; `cli/k8s_runner.py:272`; `cli/state.py` |
| `cds down <profile>` | compose: `docker compose -f docker-compose.yml down`; helm: `helm uninstall <release> --namespace <ns> --ignore-not-found --timeout <N>s` (+ `kubectl delete pvc ... --ignore-not-found` with `--delete-pvcs`) | `cli/main.py:2812`, `:2836`; `cli/k8s_runner.py:244`, `:258` |

## `cds init` and the `.env` file

`cds init <profile>` generates `<project-root>/.env` from the profile's secret
definitions (`cli/main.py:3122`, `:3135`; helpers at `cli/main.py:925`,
`:995`). Fill in the values, then the file is a standard Compose env file:

```bash
# Standalone equivalent after editing .env — Compose reads <project-root>/.env
# automatically because the compose file lives there:
cd <project-root>
docker compose -f docker-compose.yml up -d
```

Secret resolution prefers a profile-local `.env`
(`profiles/<profile>/.env`) and falls back to the project-root `.env`
(`resolve_env_file_path`, `cli/main.py:750`). When running compose by hand,
the project-root `.env` is what Compose itself reads — no `cds` needed.

## `cds render`

```bash
cds render <profile>                  # -> <project-root>/docker-compose.yml
cds render <profile> --target helm    # -> <project-root>/chart/
```

Underneath (`cli/main.py:2279`):

- Compose target: the resolved plan is rendered and written atomically to
  `<project-root>/docker-compose.yml` (default output; `--output` overrides) —
  `cli/main.py:2384` and `render_compose()` at `cli/renderer.py:44` (file write
  at `cli/renderer.py:243-248`). Rendering the Compose target also writes Traefik
  file-provider route documents to `traefik/dynamic/cds-routes-<module>.yml`
  next to the compose file (`cli/renderer.py:250`, `:255`).
- Helm target: the chart directory `<project-root>/chart/` is (re)generated —
  `cli/main.py:2320`, `:2384` via `_render_helm_chart()` (`cli/main.py:1323`)
  and `render_helm()` (`cli/k8s_renderer.py:88`). `--force` allows replacing a
  non-empty directory that doesn't look CDS-generated.

Both outputs are standard artifacts: `docker compose -f docker-compose.yml ...`
and `helm ... chart/ ...` consume them directly.

## `cds up` (Compose target)

```bash
cds up <profile>            # optionally --no-build, --detach, --timeout N, --log-file PATH
```

Underneath (`cli/main.py:2430`):

1. Re-renders `<project-root>/docker-compose.yml` (`cli/main.py:2574-2576`).
2. Unless `--no-build`: builds local images —
   ```bash
   docker compose -f docker-compose.yml build
   ```
   (`build_cmd` at `cli/main.py:2613`).
3. Starts the stack, always detached —
   ```bash
   docker compose -f docker-compose.yml up --detach
   ```
   (`up_cmd` at `cli/main.py:2585`; in the default non-`--detach` mode it runs
   in the background while the live state view polls, `cli/main.py:2658`).
4. Streams build/up output to a log file, default
   `.cds/logs/up-<profile>-<UTC timestamp>.log` (relative to the current
   working directory; `default_log_path()` at `cli/up_runner.py:50`, resolved
   at `cli/main.py:2602-2606`; `--log-file` overrides). Once `up` finishes, a
   log tail is appended to the same file —
   ```bash
   docker compose -f docker-compose.yml logs -f --no-color
   ```
   (`cli/up_runner.py:154`, `:161`; started at `cli/main.py:2663`).
5. Polls readiness every 2 s until every service is in a terminal health
   bucket or `--timeout` (default 180 s) elapses —
   ```bash
   docker compose -f docker-compose.yml ps -a --format json
   ```
   (`poll_state_until_settled()` at `cli/up_runner.py:320`, `ps_cmd` at
   `cli/up_runner.py:370`; grouping in `cli/state.py`).

Copy-pasteable standalone equivalent (from the project root, after
`cds init` + filling in `.env`, and `cds render <profile>`):

```bash
docker compose -f docker-compose.yml build      # optional; --no-build skips it
docker compose -f docker-compose.yml up --detach
docker compose -f docker-compose.yml ps -a --format json   # state, machine-readable
docker compose -f docker-compose.yml logs -f --no-color    # follow logs
```

## `cds up --target helm` (Kubernetes target)

```bash
cds up <profile> --target helm \
  [--kube-context k3d-<name>] [--create-cluster] [--no-build] \
  [--namespace <ns>] [--release <name>] [--timeout N]
```

Underneath (`cli/main.py:2467`):

0. Re-renders the chart into `<project-root>/chart/` (or `--chart-dir`)
   (`cli/main.py:2468-2472`).
1. Ensures the cluster is reachable — `kubectl [--context X] get --raw /healthz`
   (`cluster_is_reachable()`, `cli/k8s_provision.py:347`; called via
   `ensure_k8s_cluster()` at `cli/k8s_provision.py:371`, `cli/main.py:2495`).
   With `--create-cluster` and a k3d context (`k3d-<name>`), a missing cluster
   is created first:
   ```bash
   k3d cluster create <name> --wait --k3s-arg --disable=traefik@server:0
   kubectl --context k3d-<name> wait --for=condition=Ready node --all --timeout=<N>s
   ```
   (`cli/k8s_provision.py:426-433`, `:440-448`).
2. Unless `--no-build`: builds every local image the chart references
   (the services with a `build:` block — the same set that gets
   `imagePullPolicy: Never`):
   ```bash
   docker build -t <image> [-f <dockerfile>] [--build-arg K=V ...] <context>
   ```
   (`build_local_images()`, `cli/k8s_provision.py:279`, command at `:296`;
   called at `cli/main.py:2512`).
3. Imports those images into a k3d cluster:
   ```bash
   k3d image import <image...> --cluster <name>
   ```
   (`import_local_images()`, `cli/k8s_provision.py:460`, command at `:490`;
   called at `cli/main.py:2521`). On a non-k3d cluster there is no supported
   way to load local images — use registry-sourced images instead
   (`--image-source registry`).
4. Installs/upgrades the release:
   ```bash
   helm [--kube-context X] upgrade --install <release> chart/ \
     --namespace <ns> --create-namespace \
     --values <secrets.yaml> --timeout <N>s
   ```
   (`helm_up()`, `cli/k8s_runner.py:90`, command at `:131-143`; called at
   `cli/main.py:2537`).
   - Release default: the plan's `metadata.name`, else the profile directory
     name. Namespace default: `runtime.namespace`, else `cds-local`
     (`_helm_identity()`, `cli/main.py:229`; `--release`/`--namespace` override).
   - `<secrets.yaml>` is a temporary values file `cds` writes itself: it reads
     the value of every `CDS_*` environment variable the plan references (from
     the process environment or `.env`), writes them as
     `secrets: {CDS_FOO: value, ...}` to a mode-`0600` temp file, passes it via
     `--values`, and deletes it immediately after the command finishes
     (`_secret_values()` / `_write_secret_values()`, `cli/k8s_runner.py:303`,
     `:317`; unlink at `:146-148`). To replicate by hand, create your own
     untracked values file with the same shape:
     ```yaml
     secrets:
       CDS_POSTGRES_SUPERUSER_PASSWORD: replace-me
       CDS_SUPERSET_SECRET_KEY: replace-me
     ```
     and pass it with `--values secrets.local.yaml` (see
     [Supply secrets at install time](kubernetes.md#supply-secrets-at-install-time)).
     Never commit that file.
5. Polls readiness with the exact same query as `cds state --target helm`
   (below) until all workloads settle or `--timeout` elapses
   (`poll_k8s_state_until_settled()`, `cli/k8s_runner.py:166`).

Copy-pasteable standalone equivalent:

```bash
k3d cluster create cds-local --wait --k3s-arg --disable=traefik@server:0   # once
kubectl --context k3d-cds-local wait --for=condition=Ready node --all --timeout=180s
docker build -t <image> <context>                                          # per local image
k3d image import <image...> --cluster cds-local
helm --kube-context k3d-cds-local upgrade --install <release> chart/ \
  --namespace cds-local --create-namespace \
  --values secrets.local.yaml --timeout 300s
```

## `cds state`

```bash
cds state <profile>                 # compose target (default)
cds state <profile> --target helm [--namespace <ns>] [--release <name>] [--kube-context X]
```

Underneath (`cli/main.py:3075`):

- Compose target: `cli/main.py:3100-3107` runs
  ```bash
  docker compose -f <project-root>/docker-compose.yml ps -a --format json
  ```
  and groups the output by health (`parse_compose_ps_json`,
  `group_services_by_health`, `format_state_output` in `cli/state.py:13`,
  `:120`, `:153`).
- Helm target: `cli/main.py:3084-3090` calls `get_k8s_state()`
  (`cli/k8s_runner.py:296`), which runs
  ```bash
  kubectl [--context X] --namespace <ns> get deployment,statefulset,job \
    -l app.kubernetes.io/instance=<release> -o json
  ```
  (`get_k8s_workloads()`, `cli/k8s_runner.py:266`, command at `:272-280`) and
  groups the result with the same `cli/state.py` helpers.

## `cds down`

```bash
cds down <profile>                 # compose target (default)
cds down <profile> --target helm [--namespace <ns>] [--release <name>] \
  [--kube-context X] [--delete-pvcs] [--timeout N]
```

Underneath (`cli/main.py:2812`):

- Compose target:
  ```bash
  docker compose -f <project-root>/docker-compose.yml down
  ```
  (`cli/main.py:2836-2841`).
- Helm target: `helm_down()` (`cli/k8s_runner.py:225`, called at
  `cli/main.py:2827`) runs
  ```bash
  helm [--kube-context X] uninstall <release> --namespace <ns> \
    --ignore-not-found --timeout <N>s
  ```
  (command at `cli/k8s_runner.py:244-253`). With `--delete-pvcs`, it then
  deletes the release's PersistentVolumeClaims (derived from StatefulSet
  `volumeClaimTemplates`, `_stateful_pvc_names()` at `cli/k8s_runner.py:334`):
  ```bash
  kubectl [--context X] --namespace <ns> delete pvc <name...> --ignore-not-found
  ```
  (`cli/k8s_runner.py:258-263`). PVCs are retained by default.
- Lifecycle output goes to `.cds/logs/down-<profile>-<ts>.log`
  (`cli/main.py:2821` + `default_log_path(..., prefix="down")`,
  `cli/up_runner.py:50`).

## What `cds` adds on top

Running the underlying commands directly means giving up the wrapper's extras,
not losing functionality of the stack itself:

- **Validation and planning** (`cds validate`, `cds plan`, `cds preflight`,
  `cds security`): profile/contract checks before anything is rendered or
  started. Without `cds` you render first and let Docker/Helm surface errors.
- **Rendering**: `docker-compose.yml` / `chart/` must be produced once by
  `cds render` (or kept from a previous run) and re-rendered by hand whenever
  the profile changes.
- **Secrets handling**: `cds init` scaffolds `.env`; for Helm, `cds` builds the
  temporary `--values` file from `CDS_*` env vars (see above for the manual
  equivalent).
- **Live state view and settle polling** during `cds up`: the compose/helm
  equivalents are the `ps`/`kubectl get` commands listed above, run in a loop.
- **Audit log**: `cds` records each lifecycle action under `.cds/`; plain
  `docker compose` / `helm` invocations do not.
