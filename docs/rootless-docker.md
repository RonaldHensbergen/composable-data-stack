# Rootless Docker

Rootless Docker runs the Docker daemon and containers inside an
unprivileged user namespace, so a container breakout yields an
unprivileged host user instead of host root. It is the recommended
deployment mode for production hosts (threat T16 in
[`threat-model.md`](threat-model.md)).

## Check your host

`cds preflight` reports the daemon mode as `runtime.rootless`:

- `PASS` — the daemon runs rootless.
- `WARN` on production-class profiles — the daemon is rootful.
- `WARN` — the mode could not be determined (`docker info` failed).

The same information is available with
`docker info --format '{{json .SecurityOptions}}'`, which contains
`name=rootless` for a rootless daemon.

## Compatibility expectations

CDS-rendered stacks are designed for hardened, non-root containers
(`cap_drop: [ALL]`, `no-new-privileges`, explicit non-root `user`,
`read_only: true`), so they do not need extra privileges under rootless
Docker. Expect these rootless limits:

- **Ports below 1024** cannot be published unless
  `net.ipv4.ip_unprivileged_port_start` is lowered; use the module's
  `httpPort`-style config to publish high ports.
- **Bind mounts and volumes** are owned by your mapped sub-UIDs. A
  container `user: "1000:0"` appears on the host as a sub-UID, so prepare
  host directories with `podman unshare`/`chown` equivalents or prefer named
  volumes.
- **Resource limits** (`cpus`, `mem_limit`) need cgroup v2 controller
  delegation to your user.
- **The Docker socket** (`/var/run/docker.sock`) is per-user
  (`$XDG_RUNTIME_DIR/docker.sock`); CDS never mounts it, and `CDS-SEC-063`
  rejects modules that do.
- **Privileged mode and added capabilities** only apply inside the user
  namespace; `CDS-SEC-061` still rejects them.

## Setup

Follow the upstream guide: <https://docs.docker.com/engine/security/rootless/>.
After installation, point the Docker CLI at the rootless daemon
(`docker context use rootless`) and run `cds preflight <profile>`.

## Kubernetes

Pod security contexts, `runAsNonRoot` and user-namespace equivalents for
Kubernetes remain tracked under #65.
