# From Docker Compose to CDS Profile: Complete Transformation Guide

This guide walks through transforming a plain `docker-compose.yaml` into a Composable Data Stack (CDS) profile with corresponding module definitions. It covers the complete journey from monolithic Docker Compose files to modular, reusable CDS components.

## Overview

### Key Concepts

- **docker-compose.yml**: The source configuration that defines all services, their build contexts, environment variables, ports, volumes, and dependencies.
- **Module**: A reusable, self-contained component (e.g., PostgreSQL, Dagster) that abstracts docker-compose services and defines configuration schema, contracts, and implementation details.
- **Profile**: A composition of multiple module instances that describes a complete deployment stack, including module configurations, secrets management, and output contracts.
- **Contract**: An interface defining connection points between modules (e.g., SQL database contract specifies host, port, database, credentials).

---

## Starting Point: A Typical docker-compose.yaml

```yaml
name: local-dagster-postgres-superset
services:
  postgres-postgres:
    image: postgres:16
    ports:
      - 5432:5432
    environment:
      POSTGRES_DB: analytics
      POSTGRES_USER: analytics
      POSTGRES_PASSWORD: ${CDS_POSTGRES_PASSWORD}
    volumes:
      - postgres-postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U analytics -d analytics"]
      interval: 10s
      timeout: 5s
      retries: 10

  dagster-user-code:
    build:
      context: .
      dockerfile: images/dagster/Dockerfile-user-code
    image: local/dagster-user-code:custom
    restart: always
    hostname: dagster-user-code
    environment:
      DAGSTER_HOME: /opt/dagster/dagster_home
      DAGSTER_POSTGRES_HOST: postgres
      DAGSTER_POSTGRES_PORT: 5432
      DAGSTER_POSTGRES_DB: analytics
      DAGSTER_POSTGRES_USER: analytics
      DAGSTER_POSTGRES_PASSWORD: ${CDS_POSTGRES_PASSWORD}

  dagster-dagster-webserver:
    image: local/dagster:custom
    restart: always
    ports:
      - 3000:3000
    depends_on:
      dagster-user-code:
        condition: service_healthy
    command:
      - bash
      - -c
      - dagster-webserver -h 0.0.0.0 -p 3000 -w workspace.yaml
    environment:
      DAGSTER_HOME: /opt/dagster/dagster_home
      DAGSTER_POSTGRES_HOST: postgres
      DAGSTER_POSTGRES_PORT: 5432
      DAGSTER_POSTGRES_DB: analytics
      DAGSTER_POSTGRES_USER: analytics
      DAGSTER_POSTGRES_PASSWORD: ${CDS_POSTGRES_PASSWORD}

  dagster-dagster-daemon:
    image: local/dagster:custom
    restart: on-failure
    depends_on:
      dagster-user-code:
        condition: service_healthy
    command:
      - bash
      - -c
      - dagster-daemon run
    environment:
      DAGSTER_HOME: /opt/dagster/dagster_home
      DAGSTER_POSTGRES_HOST: postgres
      DAGSTER_POSTGRES_PORT: 5432
      DAGSTER_POSTGRES_DB: analytics
      DAGSTER_POSTGRES_USER: analytics
      DAGSTER_POSTGRES_PASSWORD: ${CDS_POSTGRES_PASSWORD}

  superset-superset:
    image: apache/superset:6.1.0
    ports:
      - 8088:8088
    environment:
      SUPERSET_SECRET_KEY: ${CDS_SUPERSET_SECRET_KEY}
      SUPERSET_ADMIN_USERNAME: admin
      SUPERSET_ADMIN_PASSWORD: ${CDS_SUPERSET_ADMIN_PASSWORD}
      SUPERSET_ADMIN_EMAIL: admin@example.local
      SUPERSET_DATABASE_URI: postgresql://analytics:${CDS_POSTGRES_PASSWORD}@postgres:5432/analytics

volumes:
  postgres-postgres-data:
```

---

## Step 1: Identify Services and Group Them by Capability

Read each service and decide which platform capability it represents. Group services by their logical function.

### Service Inventory

| Docker Compose Service | Capability | CDS Module | Purpose |
|---|---|---|---|
| postgres-postgres | SQL database | warehouse/postgres | Data warehouse and storage |
| dagster-user-code | Job definitions | orchestration/dagster | Orchestration user code repository |
| dagster-dagster-webserver | Orchestration UI | orchestration/dagster | Orchestration webserver (UI + API) |
| dagster-dagster-daemon | Job scheduler | orchestration/dagster | Background scheduler and executor |
| superset-superset | Business intelligence | bi/superset | BI and visualization |

**Key insight**: Three docker-compose services (dagster-user-code, dagster-webserver, dagster-daemon) combine into one logical module because they work together as an orchestration system.

### Concept Mapping Summary

| Docker Compose Concept | CDS Concept | Purpose |
|---|---|---|
| A service block | Module `implementation.compose.services` entry | Defines how the module's services run |
| `ports:` host-side value | `config.<portName>` in module configSchema | Externally configurable port |
| `ports:` container-side value | `runtime.service.ports[].containerPort` | Internal container listening port |
| `environment:` non-secret values | `configSchema` properties | Typed, configurable parameters |
| `environment:` secret values (`${ENV_VAR}`) | `spec.secrets.values.<name>` | Secret references (env variable name) |
| Hard-coded connection string | `provides` contract + `consumes` contract + `contractRef` | Loose coupling via contracts |
| `depends_on:` between different services | `spec.modules[].dependsOn` in profile | Inter-module dependencies |
| `depends_on:` within same service group | `implementation.compose.services[].depends_on` in module | Intra-module service orchestration |
| `volumes:` | `implementation.compose.volumes` with `enabledFrom` | Persistent storage with toggles |
| `healthcheck:` | `implementation.compose.healthcheck` with `conditionallyEnabledFrom` | Health monitoring |
| Multi-service compose project | Profile (`kind: Profile`) | Top-level composition file |
| Compose project name / network | `spec.runtime.namespace` | Resource naming prefix |
| Which services are active | `spec.modules[].enabled: true/false` | Enable/disable modules |

---

## Step 2: Create Module Definitions

For each capability group, create a `module.yaml` file at `modules/<layer>/<name>/module.yaml`.

A module wraps the compose fragment and replaces hard-coded values with typed configuration properties.

### Module Structure

```yaml
apiVersion: cds/v1alpha1
kind: Module

metadata:
  name: {module-name}
  category: {layer}
  version: "0.1.0"
  displayName: {Human-Readable Name}
  description: >
    Purpose and configuration guidance.

spec:
  runtime:
    type: container
    service:
      name: {service-name}
      ports:
        - name: {port-name}
          containerPort: {container-port}
          protocol: TCP

  configSchema:
    type: object
    additionalProperties: false
    required: [required-keys]
    properties:
      {config-key}:
        type: {json-schema-type}
        description: Description
        default: {default-value}

  consumes:
    - name: {contract-name}
      contract:
        kind: {contract-kind}
      required: true
      mappedFrom: "spec.config.{path}"

  provides:
    - name: {output-contract-name}
      contract:
        kind: {contract-kind}
        spec:
          {field}: "{value}"

  implementation:
    kind: docker-compose
    compose:
      services:
        {service-name}:
          image: {image}
          environment:
            VAR: "${config.key}"
          # ... other config
```

### Field Origin Reference

| Schema Field | Origin in docker-compose |
|---|---|
| `metadata.name` | Choose a short name matching the service (`postgres`, `dagster`, `superset`) |
| `metadata.category` | The capability layer (`warehouse`, `orchestration`, `bi`) |
| `metadata.version` | Start with `"0.1.0"`; this is the module's semver, separate from image tags |
| `metadata.displayName` | Human-readable label for UI tooling |
| `metadata.description` | Purpose and configuration guidance |
| `spec.runtime.type` | Always `container` for containerized services |
| `spec.runtime.service.name` | The docker-compose service key (e.g., `postgres`) |
| `spec.runtime.service.ports[].containerPort` | Right-hand side of compose `ports` mapping (`"5432:5432"` → `5432`) |
| `spec.runtime.service.ports[].protocol` | `TCP` unless the service uses UDP |
| `spec.configSchema` | Extract values that vary between environments (passwords, db names, ports) into JSON Schema properties |
| `spec.consumes` | Contracts this module requires from other modules |
| `spec.provides` | Contracts this module exposes to other modules |
| `spec.implementation.kind` | `docker-compose` |
| `spec.implementation.compose` | The original compose fragment with hard-coded values replaced by `${config.*}` and `${bindings.*}` templates |

---

## Example 1: PostgreSQL Module

**File**: `modules/warehouse/postgres/module.yaml`

**Original compose fragment**:
```yaml
postgres-postgres:
  image: postgres:16
  ports:
    - 5432:5432
  environment:
    POSTGRES_DB: analytics
    POSTGRES_USER: analytics
    POSTGRES_PASSWORD: ${CDS_POSTGRES_PASSWORD}
  volumes:
    - postgres-postgres-data:/var/lib/postgresql/data
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U analytics -d analytics"]
    interval: 10s
    timeout: 5s
    retries: 10
```

**Transformation**:
- `image: postgres:16` → stays as-is
- `POSTGRES_DB: analytics` → becomes `${config.database}` (value moves to profile)
- `POSTGRES_PASSWORD: ${CDS_POSTGRES_PASSWORD}` → becomes `${config.passwordFrom}` (secret reference)
- `ports: [5432:5432]` → host port becomes `${config.port}`; container port declared in `runtime.service.ports`
- `volumes: postgres-postgres-data:…` → captured in `implementation.compose.volumes`
- `healthcheck:` → captured with `conditionallyEnabledFrom` gate

**Resulting module.yaml**:
```yaml
apiVersion: cds/v1alpha1
kind: Module

metadata:
  name: postgres
  category: warehouse
  version: "0.1.0"
  displayName: PostgreSQL
  description: >
    PostgreSQL database module providing sql-database contract.

spec:
  runtime:
    type: container
    service:
      name: postgres
      ports:
        - name: postgres
          containerPort: 5432
          protocol: TCP

  configSchema:
    type: object
    additionalProperties: false
    required:
      - database
      - username
      - passwordFrom
      - port
    properties:
      database:
        type: string
        minLength: 1
        description: Name of the database to create
        
      username:
        type: string
        minLength: 1
        description: Database user for authentication

      passwordFrom:
        type: string
        pattern: "^secrets\\.[a-zA-Z0-9_-]+$"
        description: Reference to secret containing database password

      port:
        type: integer
        minimum: 1
        maximum: 65535
        default: 5432
        description: Port the database listens on

      storage:
        type: object
        properties:
          enabled:
            type: boolean
            default: true
          size:
            type: string
            default: 5Gi

      healthcheck:
        type: object
        properties:
          enabled:
            type: boolean
            default: true

  provides:
    - name: sql-database
      contract:
        kind: sql-database
        spec:
          host: postgres
          port: "5432"
          database: analytics
          username: analytics
          password: "${CDS_POSTGRES_PASSWORD}"
          connectionUri: "postgresql://analytics:${CDS_POSTGRES_PASSWORD}@postgres:5432/analytics"

  implementation:
    kind: docker-compose
    compose:
      services:
        postgres:
          image: postgres:16
          hostname: postgres
          ports:
            - "${config.port}:5432"
          environment:
            POSTGRES_DB: "${config.database}"
            POSTGRES_USER: "${config.username}"
            POSTGRES_PASSWORD: "${config.passwordFrom}"
          volumes:
            - postgres-postgres-data:/var/lib/postgresql/data
          healthcheck:
            conditionallyEnabledFrom: spec.config.healthcheck.enabled
            test:
              - CMD-SHELL
              - pg_isready -U "${config.username}" -d "${config.database}"
            interval: 10s
            timeout: 5s
            retries: 10

      volumes:
        postgres-postgres-data:
```

---

## Example 2: Dagster Module (Multi-Service)

**File**: `modules/orchestration/dagster/module.yaml`

**Original compose fragments**:
```yaml
dagster-user-code:
  image: local/dagster-user-code:custom
  environment:
    DAGSTER_POSTGRES_HOST: postgres
    # ... other postgres vars

dagster-dagster-webserver:
  image: local/dagster:custom
  ports:
    - 3000:3000
  depends_on:
    dagster-user-code:
      condition: service_healthy

dagster-dagster-daemon:
  image: local/dagster:custom
  depends_on:
    dagster-user-code:
      condition: service_healthy
```

**Key transformation**:
- Three services become one module because they work together as an orchestration system
- Connection strings become **consumed contracts** instead of hard-coded environment variables
- Internal dependencies (webserver depends on user-code) stay within the module's `implementation.compose.services`
- External dependencies (on postgres) become `spec.consumes` contracts

**Resulting module.yaml** (excerpt):
```yaml
apiVersion: cds/v1alpha1
kind: Module

metadata:
  name: dagster
  category: orchestration
  version: "0.1.0"
  displayName: Dagster
  description: >
    Dagster orchestration service with webserver and daemon.

spec:
  runtime:
    type: container
    service:
      name: dagster
      ports:
        - name: http
          containerPort: 3000
          protocol: TCP

  configSchema:
    type: object
    additionalProperties: false
    required:
      - webPort
      - homeDir
      - storage
    properties:
      webPort:
        type: integer
        minimum: 1
        maximum: 65535
        default: 3000

      homeDir:
        type: string
        default: /opt/dagster/dagster_home

      daemon:
        type: object
        properties:
          enabled:
            type: boolean
            default: true

      storage:
        type: object
        required:
          - runStorage
          - eventLogStorage
          - scheduleStorage
        properties:
          runStorage:
            type: object
            properties:
              contractRef:
                type: string
                pattern: "^[a-z0-9-]+\\.[a-z0-9-]+$"
          eventLogStorage:
            type: object
            properties:
              contractRef:
                type: string
                pattern: "^[a-z0-9-]+\\.[a-z0-9-]+$"
          scheduleStorage:
            type: object
            properties:
              contractRef:
                type: string
                pattern: "^[a-z0-9-]+\\.[a-z0-9-]+$"

  consumes:
    - name: run-storage
      contract:
        kind: sql-database
      required: true
      mappedFrom: "spec.config.storage.runStorage"

    - name: event-log-storage
      contract:
        kind: sql-database
      required: true
      mappedFrom: "spec.config.storage.eventLogStorage"

    - name: schedule-storage
      contract:
        kind: sql-database
      required: true
      mappedFrom: "spec.config.storage.scheduleStorage"

  provides:
    - name: http-service
      contract:
        kind: http-service
        spec:
          protocol: http
          host: dagster
          port: "3000"

  implementation:
    kind: docker-compose
    compose:
      services:
        user-code:
          image: local/dagster-user-code:custom
          environment:
            DAGSTER_HOME: "${config.homeDir}"
            DAGSTER_POSTGRES_HOST: "${bindings.run-storage.host}"
            DAGSTER_POSTGRES_PORT: "${bindings.run-storage.port}"
            DAGSTER_POSTGRES_DB: "${bindings.run-storage.database}"
            DAGSTER_POSTGRES_USER: "${bindings.run-storage.username}"
            DAGSTER_POSTGRES_PASSWORD: "${bindings.run-storage.password}"

        dagster-webserver:
          image: local/dagster:custom
          ports:
            - "${config.webPort}:3000"
          depends_on:
            user-code:
              condition: service_healthy
          environment:
            DAGSTER_HOME: "${config.homeDir}"
            DAGSTER_POSTGRES_HOST: "${bindings.run-storage.host}"
            # ... other postgres bindings

        dagster-daemon:
          enabledFrom: spec.config.daemon.enabled
          image: local/dagster:custom
          depends_on:
            user-code:
              condition: service_healthy
          environment:
            DAGSTER_HOME: "${config.homeDir}"
            DAGSTER_POSTGRES_HOST: "${bindings.run-storage.host}"
            # ... other postgres bindings
```

**Important**: The `depends_on: user-code` for webserver and daemon is an **intra-module dependency** (service to service within the same module). It stays in the module implementation. This is different from the profile-level `dependsOn: [postgres]` which is an **inter-module dependency**.

---

## Step 3: Define Shared Contracts

Contracts live at `shared/contracts/<kind>.yaml`. They describe the interface between a providing module and consuming modules.

**Example**: `shared/contracts/sql-database.yaml`

```yaml
apiVersion: cds/v1alpha1
kind: Contract

metadata:
  name: sql-database
  category: shared
  version: "0.1.0"
  description: >
    Shared contract describing values a SQL database provider exposes.

spec:
  fields:
    host:
      type: string
      required: true
      description: Hostname or service name clients use to connect.

    port:
      type: integer
      required: true
      description: TCP port where the database listens.

    database:
      type: string
      required: true
      description: Database name to connect to.

    username:
      type: string
      required: true
      description: Username for authentication.

    password:
      type: string
      required: true
      description: Secret reference or runtime placeholder for password.

    connectionUri:
      type: string
      required: true
      description: Fully composed SQLAlchemy-style connection URI.

  examples:
    - host: postgres
      port: 5432
      database: analytics
      username: analytics
      password: ${CDS_POSTGRES_PASSWORD}
      connectionUri: postgresql://analytics:${CDS_POSTGRES_PASSWORD}@postgres:5432/analytics
```

---

## Step 4: Identify and Manage Secrets

In the original compose file, secrets came from shell environment variables:
- `${CDS_POSTGRES_PASSWORD}`
- `${CDS_SUPERSET_SECRET_KEY}`
- `${CDS_SUPERSET_ADMIN_PASSWORD}`

Each becomes an entry in the profile's `spec.secrets.values` section.

| Secret Value Field | Purpose | Origin |
|---|---|---|
| key (e.g., `postgres_password`) | Logical name within CDS | Assigned for clarity |
| `env` | Actual environment variable name | From original docker-compose |
| `required` | Whether stack refuses to start without it | True for critical secrets |

**Important**: Inside module configs, `passwordFrom: secrets.postgres_password` is a pointer to the secret name, not the env-var name. The profile maps this to the actual environment variable.

---

## Step 5: Write the Profile

The profile (`profiles/<name>/profile.yaml`) wires everything together, specifying:
- Which modules to instantiate
- How to configure each module
- Module dependencies
- Secret management
- Output contracts

### Profile Structure

```yaml
apiVersion: cds/v1alpha1
kind: Profile

metadata:
  name: {profile-name}
  displayName: {Human-Readable Name}
  description: >
    Description of this deployment stack.

spec:
  runtime:
    type: docker-compose
    namespace: {resource-prefix}

  modules:
    - id: {module-instance-id}
      source: {path-to-module}
      version: "{module-version}"
      enabled: true
      dependsOn:
        - {other-module-id}
      config:
        {configuration-key}: {value}

  secrets:
    provider:
      type: env
    values:
      {secret-name}:
        env: {ENVIRONMENT_VARIABLE_NAME}
        required: true

  outputs:
    contracts:
      {output-name}:
        from: {module-id}.{contract-name}
```

### Field Origin Reference

| Profile Schema Field | What it Replaces in docker-compose |
|---|---|
| `metadata.name` | Compose project name |
| `spec.runtime.type` | Render target (`docker-compose`) |
| `spec.runtime.namespace` | Compose network/resource namespace |
| `spec.modules[].id` | Logical instance name; used in `dependsOn` and `contractRef` |
| `spec.modules[].source` | Path to module directory |
| `spec.modules[].version` | Must match `metadata.version` in module |
| `spec.modules[].enabled` | Replaces commenting services in/out |
| `spec.modules[].dependsOn` | Replaces `depends_on:` between services |
| `spec.modules[].config.*` | All hard-coded values from compose `environment:`, `ports:`, `volumes:` |
| `spec.secrets` | All `${ENV_VAR}` references from compose, centralized |
| `spec.outputs.contracts` | Exports contracts for external consumption |

### Wiring Modules Together: Contract References

Instead of repeating connection strings:

```yaml
# original compose (repeated 3 times!)
environment:
  DAGSTER_RUN_STORAGE_POSTGRES_URL: postgresql://analytics:${CDS_POSTGRES_PASSWORD}@postgres:5432/analytics
  DAGSTER_EVENT_LOG_STORAGE_POSTGRES_URL: postgresql://analytics:${CDS_POSTGRES_PASSWORD}@postgres:5432/analytics
  DAGSTER_SCHEDULE_STORAGE_POSTGRES_URL: postgresql://analytics:${CDS_POSTGRES_PASSWORD}@postgres:5432/analytics
```

The profile simply wires the contract:

```yaml
config:
  storage:
    runStorage:
      contractRef: postgres.sql-database
    eventLogStorage:
      contractRef: postgres.sql-database
    scheduleStorage:
      contractRef: postgres.sql-database
```

The `contractRef` pattern is `<module-id>.<contract-name>`. The CLI resolves this to all fields advertised by the providing module.

### Example Profile

**File**: `profiles/local-dagster-postgres-superset/profile.yaml`

```yaml
apiVersion: cds/v1alpha1
kind: Profile

metadata:
  name: local-dagster-postgres-superset
  displayName: Local Dagster + Postgres + Superset
  description: >
    Minimal local development stack for orchestration, warehouse, and BI.

spec:
  runtime:
    type: docker-compose
    namespace: cds-local

  modules:
    # PostgreSQL storage backend
    - id: postgres
      source: ../../modules/warehouse/postgres
      version: "0.1.0"
      enabled: true
      config:
        database: analytics
        username: analytics
        passwordFrom: secrets.postgres_password
        port: 5432
        storage:
          enabled: true
          size: 5Gi
        healthcheck:
          enabled: true

    # Dagster orchestration
    - id: dagster
      source: ../../modules/orchestration/dagster
      version: "0.1.0"
      enabled: true
      dependsOn:
        - postgres  # ← Dagster module depends on Postgres module
      config:
        webPort: 3000
        homeDir: /opt/dagster/dagster_home
        daemon:
          enabled: true
        storage:
          runStorage:
            contractRef: postgres.sql-database
          eventLogStorage:
            contractRef: postgres.sql-database
          scheduleStorage:
            contractRef: postgres.sql-database

    # Superset BI
    - id: superset
      source: ../../modules/bi/superset
      version: "0.1.0"
      enabled: true
      dependsOn:
        - postgres
      config:
        webPort: 8088
        secretKeyFrom: secrets.superset_secret_key
        adminUser:
          username: admin
          passwordFrom: secrets.superset_admin_password
          email: admin@example.local
        metadataDatabase:
          contractRef: postgres.sql-database

  secrets:
    provider:
      type: env
    values:
      postgres_password:
        env: CDS_POSTGRES_PASSWORD
        required: true
      superset_secret_key:
        env: CDS_SUPERSET_SECRET_KEY
        required: true
      superset_admin_password:
        env: CDS_SUPERSET_ADMIN_PASSWORD
        required: true

  outputs:
    contracts:
      warehouse:
        from: postgres.sql-database
      orchestrationApi:
        from: dagster.http-service
      biApi:
        from: superset.http-service
```

---

## Understanding Dependencies: Inter-Module vs Intra-Module

This is a critical distinction when working with CDS modules.

### Inter-Module Dependencies (Profile Level)

**Location**: Defined in `profile.yaml` using `modules[].dependsOn`

**Purpose**: Specifies that one MODULE depends on another MODULE being healthy before starting

**Example**:
```yaml
modules:
  - id: dagster
    dependsOn:
      - postgres  # ← dagster module depends on postgres module
```

**Why**: The dagster module needs postgres to be running because its services use environment variables that connect to postgres (DAGSTER_POSTGRES_HOST, etc.).

**Docker-Compose Origin**: Represents implicit dependencies through shared environment variables or database connections between different service groups.

### Intra-Module Dependencies (Module Implementation Level)

**Location**: Defined in `module.yaml` under `spec.implementation.compose.services[service].depends_on`

**Purpose**: Specifies that one SERVICE within a MULTI-SERVICE MODULE depends on another service in the same module being healthy before starting

**Example from dagster module**:
```yaml
implementation:
  compose:
    services:
      user-code:
        # ... first service (no dependencies)

      dagster-webserver:
        depends_on:
          user-code:            # ← webserver depends on user-code
            condition: service_healthy

      dagster-daemon:
        depends_on:
          user-code:            # ← daemon depends on user-code
            condition: service_healthy
```

**Why**: The Dagster webserver and daemon both require the user-code service to be running and healthy. The user-code service defines the Dagster assets and jobs.

**Docker-Compose Origin**: Maps directly from the original docker-compose `depends_on` clauses within service groups.

### Quick Reference: Where to Define Dependencies

| Dependency Type | Location | Used For | Example |
|---|---|---|---|
| **Inter-Module** | `profile.yaml` `modules[].dependsOn[]` | Module depends on another module | `dagster` depends on `postgres` |
| **Intra-Module** | `module.yaml` `implementation.compose.services[].depends_on` | Service depends on another service within same module | `dagster-webserver` depends on `dagster-user-code` |

### Complete Example: Dagster Module with Both Types of Dependencies

```yaml
# In profiles/local-dagster-postgres-superset/profile.yaml (INTER-MODULE)
modules:
  - id: postgres
    # ... postgres module config
  
  - id: dagster
    dependsOn:
      - postgres        # ← INTER-MODULE: dagster module depends on postgres module


# In modules/orchestration/dagster/module.yaml (INTRA-MODULE)
spec:
  implementation:
    kind: docker-compose
    compose:
      services:
        user-code:
          # ... service definition (no dependencies)

        dagster-webserver:
          depends_on:
            user-code:                # ← INTRA-MODULE: webserver depends on user-code service
              condition: service_healthy

        dagster-daemon:
          depends_on:
            user-code:                # ← INTRA-MODULE: daemon depends on user-code service
              condition: service_healthy
```

**How This Works**:
1. Profile says: `dagster` module depends on `postgres` module → postgres starts first (inter-module)
2. Dagster module starts with 3 services
3. Module's implementation defines: webserver and daemon wait for user-code to be healthy (intra-module)
4. Docker Compose starts user-code first, then starts webserver and daemon

**Final Startup Sequence**:
```
1. postgres-postgres (no dependencies)
2. dagster-user-code (waits for postgres via implicit env var dependency)
3. dagster-dagster-webserver (waits for dagster-user-code to be healthy)
4. dagster-dagster-daemon (waits for dagster-user-code to be healthy)
5. superset-superset (waits for postgres)
```

---

## Rendering the Profile

Once modules and profile are defined, use the CDS CLI to render a docker-compose.yml:

```bash
# Set required secrets
export CDS_POSTGRES_PASSWORD=your_password
export CDS_SUPERSET_SECRET_KEY=your_secret
export CDS_SUPERSET_ADMIN_PASSWORD=admin_password

# Render the profile
cds render local-dagster-postgres-superset

# Or output to a specific file
cds render local-dagster-postgres-superset --output build/docker-compose.yml
```

The renderer:
1. Validates all modules and contracts
2. Resolves dependencies
3. Substitutes configuration values
4. Resolves contract bindings
5. Generates a complete, valid docker-compose.yml

---

## Key Patterns and Best Practices

### Module Boundaries

A module should:
- Represent a single logical component or tool (e.g., "orchestration", "warehouse", "BI")
- Contain all services required for that component to function
- Have a clear configuration interface
- Define what it consumes (external contracts) and provides (output contracts)

### Configuration vs. Secrets

- **Configuration** (`configSchema`): Values that vary between deployments but are not sensitive (database names, port numbers, feature flags)
- **Secrets** (`spec.secrets`): Sensitive values that should never be committed (passwords, API keys, secret keys)

Configuration is baked into the rendered compose file or environment variables. Secrets are referenced as Docker Compose runtime placeholders (`${ENV_VAR}`).

### Contract Design

Contracts should:
- Be minimal and focused (e.g., `sql-database` exposes connection details; `http-service` exposes protocol, host, port)
- Be reusable across many modules
- Hide implementation details
- Document all required fields

### Reusability

Modules become more valuable as:
- Configuration options grow (more flexibility)
- Contracts are well-defined (clearer interfaces)
- Implementation details are factored out
- Common patterns emerge across deployments

---

## Summary: From Compose to CDS

| Stage | Deliverable | Purpose |
|---|---|---|
| 1. Analysis | Service inventory | Map services to capabilities |
| 2. Modules | `modules/*/module.yaml` | Define reusable components |
| 3. Contracts | `shared/contracts/*.yaml` | Define module interfaces |
| 4. Profile | `profiles/*/profile.yaml` | Compose modules into a stack |
| 5. Render | `docker-compose.yml` | Generate deployment configuration |

The result is a maintainable, reusable, and composable infrastructure-as-code that separates concerns and promotes modularity.
