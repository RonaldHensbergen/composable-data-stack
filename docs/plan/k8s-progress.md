# Kubernetes render target: progress write-up

**Branch:** `feat/k8s` · **Epic:** `composable-data-stack-73y` · **Date:** 2026-09-03

**Status: implementation complete and verified locally.** All six Kubernetes
workloads are healthy, and the E2E suite proves the PostgreSQL, Dagster, and
Superset boundaries on an isolated k3s release.

## Table of contents

- [Goal](#goal)
- [Approach and why](#approach-and-why)
- [What was built](#what-was-built)
- [Where it stands](#where-it-stands)
- [Bugs this surfaced in the existing repo](#bugs-this-surfaced-in-the-existing-repo)
- [A correction to an earlier claim](#a-correction-to-an-earlier-claim)
- [How to run it](#how-to-run-it)
- [Verification completed](#verification-completed)

## Goal

CDS renders `docker-compose.yml` and nothing else. The goal is a second render
target that produces a Helm chart deployable to Kubernetes, proven on a local
k3s cluster, with the example profile `local-dagster-postgres-superset`
(Postgres + Dagster + KeyDB + Superset, seven compose services across four
modules).

## Approach and why

Two ADRs record the decisions.

**[ADR 0001](../adr/0001-kubernetes-render-target.md) — render from the plan, not
from the Compose file.** `cli/k8s_renderer.py` is a sibling of `render_compose`:
both consume the same resolved plan, and neither reads the other's output.

The alternative, converting the generated `docker-compose.yml` with kompose or
Katenary, was rejected. Those tools lose exactly what CDS already knows and the
Compose file has already discarded: healthchecks do not become probes, no
resource requests or limits are emitted, `depends_on` and Compose networks have
no translation, and Compose secrets map only loosely onto Kubernetes Secrets.
Score (`score-compose` / `score-k8s`) is the closest prior art and works the way
this does: one spec, two sibling renderers.

The responsibility split that makes it work:

```text
  module.yaml
  ├── implementation.compose      → describes the CONTAINER
  │     image, command, env, healthcheck, tmpfs, read_only,
  │     cap_drop, security_opt, user
  │     ... translated mechanically by the renderer
  │
  └── implementation.kubernetes   → describes the ORCHESTRATION SHAPE
        which services share a Pod, Deployment vs StatefulSet,
        which service is an init container, PVC vs emptyDir,
        which bind mount becomes a ConfigMap, resources,
        service exposure, waitFor gates
        ... the decisions Compose cannot express and a converter can only guess
```

**[ADR 0002](../adr/0002-dagster-grpc-over-tcp.md) — Dagster's code server uses
gRPC over TCP on Kubernetes.** On Compose the three Dagster processes share a
Unix socket through a named volume. Pods cannot share a socket. Kubernetes could
reproduce it only by co-locating all three containers in one Pod, which couples
three independently restartable processes and still leaves the webserver racing
the socket at startup. Instead the code server listens on TCP 4000 and is reached
through a Service, which is what Dagster's own Helm chart does. The Compose
implementation is unchanged.

Chart shape: **CDS resolves, Helm deploys.** Config is resolved once into
`values.yaml`; templates read `.Values.*` only, so CDS placeholders and Helm
placeholders never coexist in one file.

## What was built

| Area | Files |
| --- | --- |
| Decisions | `docs/adr/0001-kubernetes-render-target.md`, `docs/adr/0002-dagster-grpc-over-tcp.md` (new `docs/adr/`) |
| Schema | `cli/resources/module.schema.json` — 10 new `$defs` |
| Renderer | `cli/k8s_renderer.py` (new, ~900 lines) |
| CLI | `cli/main.py` — `cds render --target=compose\|helm`, `--force` |
| Modules | `kubernetes` blocks in postgres, keydb, dagster, superset |
| Local cluster | `scripts/k8s/{k3d-env,k3d-up,k3d-down,build-images,install}.sh` |

### Schema

`spec.implementation` gains an optional `targets` list (defaulting to `[kind]`,
so every existing module is untouched) and a `kubernetes` sibling of `compose`.
The block declares workloads, init containers, service exposure, volume
classification, ConfigMaps (from a Compose bind path or inline content),
resources, `waitFor` gates replacing `depends_on` conditions, pod and container
security contexts, and per-container overrides.

**Compose output is byte-identical after all of this** (verified by diffing the
rendered `docker-compose.yml` before and after), and both profiles still pass
`cds validate`.

### Renderer

Translations implemented and visible in the rendered chart:

- Compose service → container; `entrypoint` → `command`, `command` → `args`
- `healthcheck` → readiness/liveness probes, `start_period` → a `startupProbe`
  (not folded into `initialDelaySeconds`, which means something different)
- `read_only` → `readOnlyRootFilesystem`; `cap_drop: [ALL]` → `capabilities.drop`;
  `no-new-privileges` → `allowPrivilegeEscalation: false`
- `tmpfs` → `emptyDir` with `medium: Memory`
- named volumes → PVC (`volumeClaimTemplates` on a StatefulSet) or `emptyDir`,
  honouring `enabledFrom`; `storage.size: 5Gi` resolves through `sizeFrom`
- bind-mounted files → ConfigMap projected with `subPath`, inheriting the Compose
  bind target as its mount path; a >1MiB file is refused rather than truncated
- `depends_on` conditions → `waitFor` init containers doing a bounded TCP wait
- resource requests/limits, which Compose has no equivalent for at all

Two guards run on every render: no unresolved `${config.*}` / `${bindings.*}` /
`${k8s.*}` may survive into the chart (`E071`), and no chart file may contain a
Compose-style `${CDS_*}` that Kubernetes would pass through literally, nor expand
a `$(CDS_*)` that no `secretKeyRef` supplies (`E078`/`E079`).

### Secrets

Secret values never reach the chart. Each `${CDS_*}` reference becomes a
Kubernetes `$(VAR)` expansion, and every referenced key is emitted first as its
own `secretKeyRef` entry — necessary because Kubernetes expands `$(VAR)` only
against entries earlier in the same `env` list and never against `envFrom`, and
because the values are often embedded in mixed strings such as
`postgresql://$(CDS_SUPERSET_DB_USER):$(CDS_SUPERSET_DB_PASSWORD)@postgres:5432/...`.

`templates/secret.yaml` is emitted as raw template text (a Helm `range` over
`.Values.secrets`) rather than a dumped mapping, and `scripts/k8s/install.sh`
supplies the values through a mode-0600 file outside the chart, deleted on exit.

### Service naming

Contracts bind to bare hostnames (`host: postgres`), and that hostname is what
every consumer's environment already carries. Services are therefore named after
the Compose service rather than prefixed with the Helm release, so the same
hostname resolves on both targets. Rewriting hosts inside env values was
rejected: `POSTGRES_DB: postgres` is a database name, not a host, and no textual
rule separates them safely. The cost is that one namespace holds one release of a
profile.

## Where it stands

Cluster `cds-feat-k8s` (k3d, per-worktree, deterministic ports derived from the
branch name). `cds render --target=helm` produces a chart that passes `helm lint`
and `helm template`, and `helm upgrade --install` succeeds.

```text
cds-postgres-0                     1/1  Running             StatefulSet + 5Gi PVC, init-db.sh via ConfigMap
cds-keydb-...                      1/1  Running             probe fixed (see below)
cds-dagster-user-code-...          1/1  Running             gRPC over TCP 4000, per ADR 0002
cds-dagster-webserver-...          1/1  Running             HTTP boundary verified
cds-dagster-daemon-...             1/1  Running             zero restarts
cds-superset-...                   1/1  Running             login page verified
```

Objects rendered: 1 StatefulSet, 5 Deployments, 5 Services, 3 ConfigMaps, 1 Secret.

### Resolved problem 1: Dagster webserver and daemon

```text
cp: cannot create regular file '/opt/dagster/dagster_home/workspace.yaml': Permission denied
```

`images/dagster/entrypoint.sh` copies `workspace.yaml` into `$DAGSTER_HOME`, which
is a memory-backed `emptyDir` (translated from the Compose `tmpfs`). The pod
carries `fsGroup: 999`, and the directory is `drwxrwsrwt 0 999`.

The image entrypoint copied a mode `0444` ConfigMap file into persistent
`emptyDir` state. The first start worked, but every container restart tried to
overwrite that read-only destination and failed. The entrypoint now copies to a
writable temporary file, sets mode `0644`, and atomically renames it. A regression
test executes the install twice against a read-only source.

### Resolved problem 2: Superset

The Kubernetes renderer did not apply profile `config.initDbEnv` to the Postgres
container. Its init script therefore attempted to create empty role and database
names. The renderer now uses the same resolved init environment merge as Compose.
A clean cluster creates all expected databases, and Superset serves its login page.

### Housekeeping

The final proof uses a dedicated release and namespace, removes only suite-owned
resources, and leaves the operator's six-pod stack untouched.

## Bugs this surfaced in the existing repo

Both break `docker compose up` identically. Neither was caused by this work.

**1. KeyDB healthcheck references a binary that does not exist.** The module's
healthcheck runs `redis-cli ping`, but `eqalpha/keydb:6.3.4` ships only
`keydb-cli` — `/usr/local/bin` contains no `redis-cli`. The probe could never
have passed on either target. Fixed in `modules/cache/keydb/module.yaml`
(`redis-cli` → `keydb-cli`), which fixes Compose too.

**2. `cryptography>=48` makes Superset unimportable on Apple Silicon**
(`composable-data-stack-bcu`, P1). `images/superset/requirements.txt` pins
`cryptography>=48.0.1`, which resolves to 50.0.1 and makes `import superset` die
with SIGILL. Bisected in-container:

| version | result |
| --- | --- |
| 46.0.5 (unmodified `apache/superset:6.1.0` base) | OK |
| 48.0.1 | Illegal instruction |
| 49.0.0 | Illegal instruction |
| 50.0.1 (what the pin resolves to) | Illegal instruction |

The unmodified base image works, so our requirements upgrade introduces it. The
container is natively `aarch64` with no emulation; the guest advertises `sve2`,
`bf16` and `sme`, consistent with the Rust extension emitting instructions the
Apple virtualisation guest does not implement.

The `>=48.0.1` pin looks security-motivated, so lowering it is a maintainer's
call. The repo pin is untouched; `scripts/k8s/build-images.sh` passes
`cryptography==46.0.5` through the Dockerfile's existing `IMAGE_PACKAGES` hook for
local builds only, overridable with `CDS_SUPERSET_PACKAGES`.

## A correction to an earlier claim

Earlier in this work I stated that `cli/planner.py:237` carries resolved plaintext
secrets, and that resolving config into `values.yaml` would therefore write
passwords into the chart. **That was wrong.** `cli/secrets.py::load_profile_secrets`
returns only environment variable *names* — its own comment says "Keep only env
variable names in the returned mapping; never include values" — so CDS never holds
a secret value at all, and the property I flagged as needing a carve-out already
held by design.

The consequence was a bad guard: the first version of the leak check compared
chart content against those names and produced 61 false positives on the first
run (`superset` is both a database name and a module id). It was replaced with a
check that asserts something real: the secret plumbing is complete and
self-consistent. The value-leak class is covered by a real-profile sentinel test
that scans every rendered chart artifact.

## How to run it

```bash
python3.14 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env          # set real values

make k3d-build                      # local/dagster:custom, local/superset:custom
make k3d-up                         # per-branch cluster, imports both images
make k3d-install                    # cds up --target helm with bounded rollouts
make k3d-e2e                        # isolated real-service proof
make k3d-down                       # deletes only this branch's cluster
```

Render without a cluster:

```bash
cds render profiles/local-dagster-postgres-superset/profile.yaml --target=helm -o ./chart
# Supply every required secrets.KEY with --set-string or a private values file.
```

Cluster names and ports derive from the branch name, so worktrees never collide,
and `KUBECONFIG` is per-worktree. No script calls `kubectl config use-context`,
which mutates the shared config and races siblings. Nothing deletes a sibling's
cluster.

The UIs are ClusterIP services, so reaching them needs a port-forward:

```bash
source scripts/k8s/k3d-env.sh
kubectl --context "$CDS_CONTEXT" -n cds-local port-forward svc/dagster-webserver 3000:3000
kubectl --context "$CDS_CONTEXT" -n cds-local port-forward svc/superset          8088:8088
```

Both endpoints are verified by `scripts/k8s/e2e.sh`.

## Verification completed

- 648 unit and integration tests pass, with 8 environment-dependent skips.
- Helm lint and template pass with install-time secret placeholders.
- The missing-secret guard fails Helm rendering before workload creation.
- The isolated k3s E2E summary is `pass=6 fail=0`.
- `cds state --target helm` reports all six operator workloads as healthy.

The Apple Silicon Superset cryptography incompatibility remains tracked outside
this epic as `composable-data-stack-bcu`; the local image build has an explicit,
overridable compatibility pin.
