# Web serving modes: local development vs production

How CDS modules serve HTTP, and how the serving mode differs between a
local-development stack and a production-oriented one (issue #216). The
[production hardening checklist](production-hardening-checklist.md) maps
the serving-mode controls to the operator verification procedure.

## The `webServer.mode` convention

Modules that expose an HTTP service implement the same configuration
convention: `config.webServer` with a `mode` of either `production` or
`development`. The mode is a provider-neutral capability description: the
module maps it to its own implementation (a process manager, a hardened
server mode, or a built-in development server). Profiles and environment
overlays select the mode without binding CDS to one specific server.

| Module | `production` (what runs) | `development` (what runs) | Default |
| --- | --- | --- | --- |
| `bi/superset` | gunicorn with a preloaded app (loaded once in the master, then forked), `webServer.workers` workers (default 4), `webServer.timeoutSeconds` per-request timeout (default 60), gthread worker class, SIGTERM drains in-flight requests | Superset's built-in development server (`superset run`) | `production` |
| `identity/keycloak` | `kc.sh start --http-enabled=true --hostname-strict=false` (production mode, plain HTTP kept on the localhost-only bind for the reverse proxy in front; hostname-strict stays relaxed until KC hostname/proxy-header configuration lands, #579/#581) | Keycloak development mode (`start-dev`), which relaxes hostname/TLS checks | `development` (module is `productionSuitable: false`) |

The defaults are deliberate:

- `bi/superset` defaults to `production` so every profile gets a
  production-grade web server (workers, timeouts, graceful shutdown) out of
  the box; the Flask development server is an explicit opt-in for local
  debugging.
- `identity/keycloak` defaults to `development` while it is marked
  `productionSuitable: false` (realm import and the `oidc-provider`
  contract are available, but `KC_DB`/driver checks and the remaining
  production hardening are still open — #682). Its production mode is
  available, but profiles remain subject to the existing production
  security gates.

## Health and readiness

Serving modes do not change health semantics. Superset keeps serving
`/health` on its container port in both modes (the rendered healthcheck and
the image `HEALTHCHECK` are unchanged); Keycloak keeps its management
interface health endpoint (`KC_HEALTH_ENABLED: true`) in both modes. Switching
modes therefore never invalidates a rendered readiness check.

## Validation and security rules

- **E140**: a `metadata.environment: production` profile that sets a
  module's `config.webServer.mode` to `development` fails validation:
  development web servers are prohibited in production profiles. An absent
  `webServer.mode` passes because validation runs before configSchema
  defaults apply — the module default decides, and production-suitable
  modules default to `production`.
- **CDS-SEC-073**: `cds security` flags any staging/production profile using
  a module marked `productionSuitable: false` (currently keycloak and vault),
  independently of the serving mode.

## Choosing a mode in a profile

```yaml
spec:
  modules:
    - id: superset
      config:
        webServer:
          mode: development   # local debugging: Flask development server
          # mode: production  # default: gunicorn with workers/timeout
          # workers: 4
          # timeoutSeconds: 60
```

Environment overlays (e.g. `environments/prod.yaml`) can pin the mode per
environment; production overlays cannot pin it to `development` (E140).

## Operator guidance

- The development servers print request logs to stdout, serve one request
  per process, auto-reload on some code changes, and give no worker
  isolation or timeouts. Use them for interactive debugging only.
- The production serving mode is the baseline for the hardened profile work
  (issue #64, #72): workers recycle on timeout, SIGTERM drains in-flight
  requests before exit, and access/error logs are written to stdout for
  container log collection.
- Behind a TLS-terminating reverse proxy (traefik), Keycloak's production
  mode with `--http-enabled=true` on the localhost bind matches the current
  topology; revisit proxy/hostname headers together with the reverse-proxy
  contract work (#579/#581) when fronting non-localhost services.
