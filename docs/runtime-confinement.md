# Runtime Confinement

CDS can render seccomp, AppArmor and SELinux settings into every Docker
Compose service's `security_opt`. Declare a profile-wide default and,
optionally, override individual keys per module instance:

```yaml
spec:
  security:
    runtime:
      confinement:
        seccomp: file:seccomp/cds.json   # relative to the profile directory
        apparmor: runtime/default
        selinux: [type:container_t]
  modules:
    - id: db
      # ...
      security:
        confinement:
          apparmor: cds-postgres         # profile already loaded on the host
```

## Keys

| Key | Values | Rendered as |
| --- | --- | --- |
| `seccomp` | `default` (engine default; clears a module-set value), `unconfined`, `file:<relative path>` | `seccomp=unconfined` / `seccomp:<path relative to docker-compose.yml>` |
| `apparmor` | `runtime/default`, `unconfined`, or a host-loaded profile name | `apparmor=docker-default` / `apparmor=<name>` |
| `selinux` | `disable` or a list of `user:`, `role:`, `type:`, `level:` labels | `label=disable` / `label=<label>` |

A key set on a module instance replaces the profile default for that key;
other keys keep the profile default. The declared value also replaces a
matching entry a module hard-codes in its own `security_opt`; unrelated
options such as `no-new-privileges:true` are preserved.

## Validation

Invalid values fail `cds validate` with error `E127` and the exact data
path. `file:` seccomp profiles must be relative, stay inside the profile
directory and exist on disk. AppArmor profiles and SELinux labels must
exist on the host: CDS renders them but cannot load them, so a missing
profile is reported by Docker at `cds up`.

## Scope And Caveats

- **Compose only.** Confinement is not rendered into Helm charts. Rendering a
  profile that declares it with `--target=helm` emits warning `W127`, so a
  profile is never silently treated as confined. Kubernetes security contexts
  remain tracked under #65.
- **Module overrides can weaken the default.** A module instance may set a
  key to `unconfined` or `selinux: disable` even when the profile default is
  stricter. This page only covers the mechanism; production policy rules for
  flagging such overrides follow in a later part of #207.
- **Engine-specific mapping.** `apparmor: runtime/default` is rendered as
  Docker's `docker-default` profile. Other engines (for example Podman) use
  different default profile names; declare the host-loaded profile name
  explicitly there.
- **Path rules.** `file:` paths must be relative to the profile directory.
  Rooted (`/x`, `\x`), drive-qualified (`C:x`) and `..` paths are rejected on
  every platform, and are re-checked when `cds render` consumes a plan.
