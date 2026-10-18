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

## Production runtime policy

For production-class profiles `cds security` and `cds test` evaluate every
rendered service against one runtime policy:

- `CDS-SEC-090` reports one finding per service listing every violated
  setting: privileged mode, `cap_drop` without `ALL`, missing
  `no-new-privileges`, no explicit non-root `user`, or a writable root
  filesystem.
- `CDS-SEC-091` flags `seccomp=unconfined`, `apparmor=unconfined` and
  `label=disable`, whether set by a module or via the confinement keys above.

CDS cannot see a non-root `USER` baked into an image, so a service that
relies on one must either declare `user:` explicitly or be covered by a
documented waiver. Waivers never cover privileged mode or confinement:

```yaml
spec:
  security:
    waivers:
      runtimePolicy:
        reason: Images set a non-root USER in their Dockerfile.
        checks: [nonRootUser]   # capDrop, noNewPrivileges, nonRootUser, readOnlyRootFilesystem
```

Kubernetes security contexts remain tracked under #65; this page covers
the Compose target only.
