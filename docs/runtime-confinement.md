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

Kubernetes security contexts remain tracked under #65; this page covers
the Compose target only.
