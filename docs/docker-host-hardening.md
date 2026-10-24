# Docker Host And Daemon Hardening Baseline

Baseline version: **1.0** (applies to CDS `docker-compose` runtimes).

CDS hardens the *rendered stack* (images, Compose services, secrets). It does
not secure the Docker daemon or the host beneath it. This baseline lists the
host controls an operator must provide, and states which are verified by
`cds preflight`. Threat assumptions come from the
[threat model](threat-model.md); the production checklist
cross-links this document rather than duplicating it.

## Responsibility Split

| Area | CDS enforces | Operator responsibility |
| --- | --- | --- |
| Service containers (capabilities, privileges, secrets, images) | Yes, via `cds security` rules and image verification | Review findings |
| Daemon and host configuration | No | Yes, everything below |
| Host diagnostics | Advisory `WARN` results in `cds preflight` | Act on warnings |

## Controls

| ID | Control | Guidance | Inspected by `cds preflight` |
| --- | --- | --- | --- |
| `HOST-001` | Rootless mode | Run the daemon rootless ([docs](https://docs.docker.com/engine/security/rootless/)) so a container escape does not yield host root. | `host.rootless` |
| `HOST-002` | User namespaces | If rootful, set `"userns-remap": "default"` in `daemon.json`. Rootless mode satisfies this. | `host.userns` |
| `HOST-003` | Default seccomp profile | Never run the daemon with `--seccomp-profile=unconfined`. | `host.seccomp` |
| `HOST-004` | Logging | Keep a logging driver enabled and configure rotation (`log-opts` `max-size`/`max-file`) or ship logs to a central collector. | `host.logging` (driver only; rotation is not inspectable) |
| `HOST-005` | Live restore | Set `"live-restore": true` so daemon updates do not stop workloads. | `host.live-restore` |
| `HOST-006` | Daemon socket exposure | Do not expose the daemon over unauthenticated TCP; never mount `docker.sock` into containers; keep the socket mode `660` or stricter. If remote access is required, use TLS client certificates or SSH. | `host.socket` |
| `HOST-007` | Filesystem permissions | Keep `/var/lib/docker` mode `710` owned by root, restrict `/etc/docker`, `daemon.json`, and the socket to root and the `docker` group, and keep the `docker` group membership minimal (membership is root-equivalent). | No (needs privileged host access) |
| `HOST-008` | Updates | Apply Docker Engine, containerd, runc, and kernel security updates promptly; subscribe to upstream advisories. | No |
| `HOST-009` | Auditing | Audit Docker daemon files and the socket with `auditd`. | No |
| `HOST-010` | CIS Docker Benchmark | Run the CIS Docker Benchmark (for example with `docker-bench-security`) on the host and track exceptions. | No |

## Diagnostics

`cds preflight` reads `docker info --format '{{json .}}'` and the
`DOCKER_HOST`/`DOCKER_TLS_VERIFY` environment, and `stat`s the daemon socket.
It needs no privileged containers and no extra permissions beyond access to
the daemon. Host findings are advisory (`PASS` or `WARN`) and never fail
preflight, because they describe the operator-owned host. They apply to
every profile class; production-class gating of the rootless result is
handled separately by the rootless preflight check.

Limits of the checks:

- Docker Desktop (macOS/Windows) runs the daemon in a managed VM, so host
  checks are skipped there.
- `host.socket` is only reported when the socket can be inspected: a TCP
  `DOCKER_HOST` (TLS is considered enabled when `DOCKER_TLS_VERIFY` is set to
  any non-empty value, as Docker itself does), an `ssh://` host, or a local
  Unix socket on POSIX. Other transports are omitted rather than reported as
  `PASS`. It is also omitted when the Docker CLI targets a named context
  (`DOCKER_CONTEXT` or `currentContext` in `config.json`), since the daemon
  may be remote. For a rootless daemon without `DOCKER_HOST`, the socket at
  `$XDG_RUNTIME_DIR/docker.sock` is inspected.

```text
[PASS] host.rootless: Docker daemon runs in rootless mode.
[WARN] host.live-restore: Live restore is not enabled; daemon restarts stop containers (HOST-005).
```

## Verification On A Reference Host

1. Provision a clean host with a supported Docker Engine and apply the
   controls above.
2. Run `cds preflight <profile>`; every `host.*` check should be `PASS`.
3. Run `docker-bench-security` and record accepted exceptions.
4. Re-run after each Docker or kernel upgrade.

The automated checks are unit-tested against recorded `docker info` output;
a clean-host verification run remains an operator step because CDS CI does
not run a rootless or user-namespace-remapped daemon.
