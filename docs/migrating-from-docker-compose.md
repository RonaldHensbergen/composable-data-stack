## Migrating from Docker Compose to a CDS profile

This guide walks step by step through transforming a plain `docker-compose.yaml` into a
Composable Data Stack (CDS) profile and the modules it references.

The example used throughout is a three-service stack: **Postgres**, **Dagster**, and
**Apache Superset**.

---

### Starting point: a typical `docker-compose.yaml`

```yaml
services:
  postgres:
    image: postgres:16
    ports:
      - "5432:5432"
    environment:
      POSTGRES_DB: analytics
      POSTGRES_USER: analytics
      POSTGRES_PASSWORD: ${CDS_POSTGRES_PASSWORD}
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U analytics -d analytics"]
      interval: 10s
      timeout: 5s
      retries: 10

  dagster-webserver:
    image: local/dagster:custom
    command: ["bash", "-c", "dagster-webserver -h 0.0.0.0 -p 3000"]
    ports:
      - "3000:3000"
    environment:
      DAGSTER_HOME: /opt/dagster/dagster_home
      DAGSTER_RUN_STORAGE_POSTGRES_URL: postgresql://analytics:${CDS_POSTGRES_PASSWORD}@postgres:5432/analytics
      DAGSTER_EVENT_LOG_STORAGE_POSTGRES_URL: postgresql://analytics:${CDS_POSTGRES_PASSWORD}@postgres:5432/analytics
      DAGSTER_SCHEDULE_STORAGE_POSTGRES_URL: postgresql://analytics:${CDS_POSTGRES_PASSWORD}@postgres:5432/analytics

  dagster-daemon:
    image: local/dagster:custom
    command: ["bash", "-c", "dagster-daemon run"]
    environment:
      DAGSTER_HOME: /opt/dagster/dagster_home
      DAGSTER_RUN_STORAGE_POSTGRES_URL: postgresql://analytics:${CDS_POSTGRES_PASSWORD}@postgres:5432/analytics
      DAGSTER_EVENT_LOG_STORAGE_POSTGRES_URL: postgresql://analytics:${CDS_POSTGRES_PASSWORD}@postgres:5432/analytics
      DAGSTER_SCHEDULE_STORAGE_POSTGRES_URL: postgresql://analytics:${CDS_POSTGRES_PASSWORD}@postgres:5432/analytics

  superset:
    image: apache/superset:6.1.0
    ports:
      - "8088:8088"
    environment:
      SUPERSET_SECRET_KEY: ${CDS_SUPERSET_SECRET_KEY}
      SUPERSET_ADMIN_USERNAME: admin
      SUPERSET_ADMIN_PASSWORD: ${CDS_SUPERSET_ADMIN_PASSWORD}
      SUPERSET_ADMIN_EMAIL: admin@example.local
      SUPERSET_DATABASE_URI: postgresql://analytics:${CDS_POSTGRES_PASSWORD}@postgres:5432/analytics

volumes:
  postgres-data:
```

---

### Step 1 — Identify services and group them by capability

Read each service in the compose file and decide which platform capability it represents.

| Docker Compose service | Capability | CDS layer |
| --- | --- | --- |
| `postgres` | SQL database | `warehouse` |
| `dagster-webserver`, `dagster-daemon` | Workflow orchestration | `orchestration` |
| `superset` | Business intelligence | `bi` |

Each capability group becomes one **module**.

---

### Step 2 — Create a module for each capability group

A module lives at `modules/<layer>/<name>/module.yaml`.
It wraps the compose fragment for that capability and replaces hard-coded values with
typed configuration properties.

#### `module.yaml` field origins

| Schema field | Where it comes from in docker-compose |
| --- | --- |
| `metadata.name` | Choose a short name matching the service (`postgres`, `dagster`, `superset`) |
| `metadata.category` | The capability layer (`warehouse`, `orchestration`, `bi`) |
| `metadata.version` | Assign `"0.1.0"` to start; this is the module's own semver, separate from the image tag |
| `metadata.displayName` | A human-readable label for UI tooling |
| `metadata.description` | Free text description |
| `spec.runtime.type` | Always `container` for a containerised service |
| `spec.runtime.service.name` | The Docker Compose service key (e.g., `postgres`) |
| `spec.runtime.service.ports[].containerPort` | The right-hand side of the compose `ports` mapping (`"5432:5432"` → `5432`) |
| `spec.runtime.service.ports[].protocol` | `TCP` unless the service uses UDP |
| `spec.configSchema` | Extract every value that varies between environments (passwords, db names, ports) into JSON Schema properties |
| `spec.consumes` | Contracts this module needs from another module (e.g., Dagster needs the Postgres connection URI) |
| `spec.provides` | Contracts this module exposes so other modules can connect to it |
| `spec.implementation.kind` | `docker-compose` |
| `spec.implementation.compose` | The original compose fragment for this service, with hard-coded values replaced by `${config.*}` and `${bindings.*}` template expressions |

#### Example — Postgres module

File: `modules/warehouse/postgres/module.yaml`

The original compose fragment had:

- `image: postgres:16` → stays as-is in `implementation.compose`
- `POSTGRES_DB: analytics` → becomes `${config.database}` (the value `analytics` moves to the profile under `config.database`)
- `POSTGRES_PASSWORD: ${CDS_POSTGRES_PASSWORD}` → becomes `${config.passwordFrom}` (the secret reference moves to the profile as `config.passwordFrom: secrets.postgres_password`)
- `ports: ["5432:5432"]` → the host port becomes `${config.port}`; the container port is declared in `runtime.service.ports`
- `volumes: postgres-data:…` → captured in `implementation.compose.volumes` with an `enabledFrom` gate
- `healthcheck:` → captured in `implementation.compose` with a `conditionallyEnabledFrom` gate

The module then declares a **contract it provides**:

```yaml
provides:
  - name: sql-database
    contract:
      kind: sql-database
      spec:
        host: ${service.host}
        port: ${config.port}
        database: ${config.database}
        username: ${config.username}
        password: ${config.passwordFrom}
        connectionUri: postgresql://${config.username}:${config.passwordFrom}@${service.host}:${config.port}/${config.database}
```

This replaces the hard-coded connection strings that were duplicated across all three
services in the original compose file.

#### Example — Dagster module

File: `modules/orchestration/dagster/module.yaml`

Dagster had three environment variables pointing to Postgres:

- `DAGSTER_RUN_STORAGE_POSTGRES_URL`
- `DAGSTER_EVENT_LOG_STORAGE_POSTGRES_URL`
- `DAGSTER_SCHEDULE_STORAGE_POSTGRES_URL`

These become **consumed contracts** in the module:

```yaml
consumes:
  - name: run-storage
    contract: {kind: sql-database}
    required: true
    mappedFrom: spec.config.storage.runStorage
  - name: event-log-storage
    contract: {kind: sql-database}
    required: true
    mappedFrom: spec.config.storage.eventLogStorage
  - name: schedule-storage
    contract: {kind: sql-database}
    required: true
    mappedFrom: spec.config.storage.scheduleStorage
```

At render time `${bindings.run-storage.connectionUri}` replaces the hard-coded URL in
the compose implementation.

The two compose services (`dagster-webserver`, `dagster-daemon`) are both captured inside
`implementation.compose.services`. The daemon can be toggled with
`enabledFrom: spec.config.daemon.enabled`.

#### Example — Superset module

File: `modules/bi/superset/module.yaml`

- `SUPERSET_DATABASE_URI` → `${bindings.metadata-database.connectionUri}` (consumed `sql-database` contract)
- `SUPERSET_SECRET_KEY: ${CDS_SUPERSET_SECRET_KEY}` → `${config.secretKeyFrom}`, where the profile sets `secretKeyFrom: secrets.superset_secret_key`
- `SUPERSET_ADMIN_PASSWORD: ${CDS_SUPERSET_ADMIN_PASSWORD}` → `${config.adminUser.passwordFrom}`

---

### Step 3 — Define shared contracts

A contract lives at `shared/contracts/<kind>.yaml`.
It describes the **interface** between a providing module and consuming modules.

#### `contract.yaml` field origins

| Schema field | What it captures |
| --- | --- |
| `metadata.name` | The contract kind identifier (e.g., `sql-database`) |
| `metadata.version` | Semver for the contract itself |
| `spec.fields.<name>.type` | The data type of each field the provider exposes |
| `spec.fields.<name>.required` | Whether consumers must expect this field |
| `spec.fields.<name>.description` | Human-readable purpose of the field |
| `spec.examples` | One or more concrete example values |

The six fields of `sql-database` — `host`, `port`, `database`, `username`, `password`,
`connectionUri` — correspond exactly to the pieces of the hard-coded connection string
from the original compose file.

A contract only needs to be defined once and is then reused by any module that provides
or consumes that capability.

---

### Step 4 — Identify secrets

In the original compose file all secrets came from shell environment variables:

- `${CDS_POSTGRES_PASSWORD}`
- `${CDS_SUPERSET_SECRET_KEY}`
- `${CDS_SUPERSET_ADMIN_PASSWORD}`

Each one becomes an entry under `spec.secrets.values` in the profile.
`secrets.provider.type: env` means the CLI reads them from the shell environment.

| `spec.secrets.values` field | Origin in docker-compose |
| --- | --- |
| key (e.g., `postgres_password`) | A logical name you assign inside CDS |
| `env` | The original env-var name (`CDS_POSTGRES_PASSWORD`) |
| `required` | Whether the stack must refuse to start without it |

Inside module configs, `passwordFrom: secrets.postgres_password` is a pointer to this
secret, not the raw env-var name.

---

### Step 5 — Write the profile

The profile (`profiles/<name>/profile.yaml`) wires everything together.

#### `profile.yaml` field origins

| Profile schema field | What it replaces in docker-compose |
| --- | --- |
| `metadata.name` | The project name you'd put in `COMPOSE_PROJECT_NAME` |
| `spec.runtime.type` | `docker-compose` — the render target |
| `spec.runtime.namespace` | Replaces the compose project/network namespace |
| `spec.modules[].id` | Logical name for this module instance; used in `dependsOn` and `contractRef` |
| `spec.modules[].source` | Path to the module directory |
| `spec.modules[].version` | Must match `metadata.version` in the module |
| `spec.modules[].enabled` | Replaces commenting a service in or out |
| `spec.modules[].dependsOn` | Replaces `depends_on:` in compose |
| `spec.modules[].config.*` | Every hard-coded value previously in compose `environment:`, `ports:`, or `volumes:` |
| `spec.secrets` | All `${ENV_VAR}` references from the compose file, centralised |
| `spec.outputs.contracts` | Documents which contracts are exported to the outside world |

#### Wiring modules together

Instead of repeating the connection string three times:

```yaml
# original compose
environment:
  DAGSTER_RUN_STORAGE_POSTGRES_URL: postgresql://analytics:${CDS_POSTGRES_PASSWORD}@postgres:5432/analytics
```

the profile simply writes:

```yaml
config:
  storage:
    runStorage:
      contractRef: postgres.sql-database
```

The `contractRef` pattern is `<module-id>.<contract-name>`. The CLI resolves this to all
the fields advertised by the Postgres module in its `provides` section.

---

### Concept mapping summary

| Docker Compose concept | CDS concept |
| --- | --- |
| A service block | Module `implementation.compose.services` entry |
| `ports:` host-side value | `config.<portName>` in module configSchema |
| `ports:` container-side value | `runtime.service.ports[].containerPort` |
| `environment:` non-secret values | `configSchema` properties |
| `environment:` secret values (`${ENV_VAR}`) | `spec.secrets.values.<name>` with `env:` pointing to the var |
| Hard-coded connection string | `provides` contract + `consumes` contract + `contractRef` binding |
| `depends_on:` | `spec.modules[].dependsOn` in the profile |
| `volumes:` | `implementation.compose.volumes` with optional `enabledFrom` toggle |
| `healthcheck:` | `implementation.compose.services.<name>.healthcheck` with optional `conditionallyEnabledFrom` |
| Multi-service compose project | Profile (`kind: Profile`) |
| Compose project name / network | `spec.runtime.namespace` |
| Which services are active | `spec.modules[].enabled: true/false` |
