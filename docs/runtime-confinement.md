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
directory and exist on disk. SELinux labels may use each of `user:`, `role:`,
`type:` and `level:` at most once. AppArmor profiles and SELinux labels must
exist on the host: CDS renders them but cannot load them, so a missing
profile is reported by Docker at `cds up`.

## Scope And Caveats

- **Compose only.** Confinement is not rendered into Helm charts. Rendering a
  profile that declares it with `--target=helm` emits warning `W127`, so a
  profile is never silently treated as confined. Kubernetes security contexts
  remain tracked under #65.
- **Module overrides can weaken the default.** A module instance may set a
  key to `unconfined` or `selinux: disable` even when the profile default is
  stricter. For production-class profiles
  `CDS-SEC-091` flags such overrides (see Production runtime policy).
- **Engine-specific mapping.** `apparmor: runtime/default` is rendered as
  Docker's `docker-default` profile. Other engines (for example Podman) use
  different default profile names; declare the host-loaded profile name
  explicitly there.
- **Path rules.** `file:` paths must be relative to the profile directory.
  Rooted (`/x`, `\x`), drive-qualified (`C:x`) and `..` paths are rejected on
  every platform, and are re-checked when `cds render` consumes a plan.
  Rendering fails with `E127` if the profile directory is unknown or the
  profile cannot be referenced relative to `docker-compose.yml` (for example
  on a different Windows drive).

## Production runtime policy

For production-class profiles `cds security` and `cds test` evaluate every
rendered service against one runtime policy:

- `CDS-SEC-090` reports one finding per service listing every violated
  setting: privileged mode, `cap_drop` without `ALL`, missing
  `no-new-privileges`, no explicit non-root `user`, a writable root
  filesystem, or `cap_add` of a kernel-escape capability (`ALL`,
  `SYS_ADMIN`, `SYS_MODULE`, `SYS_PTRACE`, `SYS_RAWIO`, `DAC_READ_SEARCH`).
- `CDS-SEC-091` flags `seccomp=unconfined`, `apparmor=unconfined` and
  `label=disable`, whether set by a module or via the confinement keys above.

CDS cannot see a non-root `USER` baked into an image, so a service that
relies on one must either declare `user:` explicitly or be covered by a
documented waiver. Waivers never cover privileged mode, dangerous
`cap_add` or confinement. Until
[#932](https://github.com/RonaldHensbergen/composable-data-stack/issues/932)
lands, the waiver applies to **every service in the profile**, not to a
specific module instance -- a waiver justified by one service's image
also suppresses the check for unrelated services, so keep the check list
as narrow as the reason actually justifies. An applied waiver emits
warning `W103` listing the suppressed checks, and a waiver on a
non-production profile emits `W104` because it has no effect there:

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

---

## Migration Guide

If your production profile fails with CDS-SEC-090/091 after upgrading:

1. **Check the findings**:
   ```bash
   cds security --profile my-profile
   ```
2. **Hardening options**:
   - Add `cap_drop: [ALL]` to your services.
   - Add `no-new-privileges: true` to `security_opt`.
   - Set `user: "1000:1000"` (or another non-root user).
   - Set `read_only: true` and use volumes for writable paths.
   - Remove `privileged: true`.
   - Remove `seccomp=unconfined`, `apparmor=unconfined`, or `label=disable` from `security_opt`.
3. **Temporary waivers**:
   If you cannot immediately harden your services, use a waiver:
   ```yaml
   spec:
     security:
       waivers:
         runtimePolicy:
           reason: "Legacy images cannot be modified immediately. Tracking in JIRA-123."
           checks: [nonRootUser, readOnlyRootFilesystem]
   ```
   Note: Privileged mode, dangerous `cap_add` (kernel-escape capabilities), and confinement **cannot be waived**.
