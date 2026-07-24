# Installation

This guide installs CDS and its local container runtime on a clean Linux,
macOS, or Windows machine.

## Choose What You Need

CDS compile-time commands and runtime commands have different requirements:

| Use case | Required software |
|---|---|
| Validate, secure, plan, or render profiles | Python 3.14+, `pip`, and `venv` |
| Clone and update the source repository | Git |
| Build and run local profiles | Docker Engine/Desktop and Docker Compose v2 |

Docker is not required for `cds validate`, `cds security`, `cds plan`, or
`cds render`.

## Resource Guidance

For the complete example stack:

- allocate at least 8 GB of memory to Docker
- keep at least 10 GB of disk space free
- ensure the profile's host ports are available

Images, build layers, persistent volumes, and logs consume additional disk
space over time. Production-sized workloads require more memory and storage.

## 1. Install Platform Prerequisites

### Linux

1. Install Git using the package manager for your distribution.
2. Install Python 3.14 or newer from your distribution, from
   [python.org](https://www.python.org/downloads/), or with a Python version
   manager.
3. Install Docker Engine by following Docker's
   [distribution-specific instructions](https://docs.docker.com/engine/install/).
4. Install the Docker Compose v2 plugin if it was not included with Docker
   Engine.
5. Start the Docker daemon and configure access for the account that will run
   CDS.

Docker's
[Linux post-installation guide](https://docs.docker.com/engine/install/linux-postinstall/)
documents daemon startup and non-root access. Membership in the `docker` group
effectively grants root-level control of the host; apply it only to trusted
accounts.

Verify the installation:

```bash
python3 --version
git --version
docker --version
docker compose version
docker info >/dev/null
```

### macOS

1. Install Git, either through the Xcode Command Line Tools or another trusted
   package source.
2. Install Python 3.14 or newer from
   [python.org](https://www.python.org/downloads/macos/) or a trusted package
   manager.
3. Install
   [Docker Desktop for Mac](https://docs.docker.com/desktop/setup/install/mac-install/).
4. Start Docker Desktop and wait until the Docker engine reports that it is
   running.
5. Allocate at least 8 GB of memory to Docker Desktop for the complete example
   stack.

Verify the installation:

```bash
python3 --version
git --version
docker --version
docker compose version
docker info >/dev/null
```

### Windows

1. Enable hardware virtualization in the system firmware when it is not
   already enabled.
2. Install WSL 2 by following Microsoft's
   [WSL installation guide](https://learn.microsoft.com/windows/wsl/install).
3. Install Git for Windows.
4. Install Python 3.14 or newer from
   [python.org](https://www.python.org/downloads/windows/). Enable the installer
   option that makes the Python launcher available.
5. Install
   [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/).
6. Select the WSL 2 backend in Docker Desktop and start the Docker engine.
7. Allocate at least 8 GB of memory to Docker Desktop for the complete example
   stack.

Verify the installation in PowerShell:

```powershell
py --version
git --version
docker --version
docker compose version
docker info | Out-Null
```

## 2. Check Versions

The version commands must show:

- Python 3.14 or newer
- Docker Compose v2, invoked as `docker compose`
- a Docker client and server that can communicate successfully

The legacy `docker-compose` v1 command is not supported. If `docker info`
reports a daemon or permission error, resolve that before running `cds up`.

## 3. Download CDS

Clone the repository and enter its root directory:

```bash
git clone https://github.com/RonaldHensbergen/composable-data-stack.git
cd composable-data-stack
```

The source checkout currently contains the reference profiles, modules, image
definitions, and workdirs needed by the example stacks. Run CDS commands from
the repository root unless you provide explicit paths.

## 4. Create A Python Environment

### Linux And macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

If `python3` is not Python 3.14 or newer, invoke the installed version
explicitly, for example `python3.14 -m venv .venv`.

### Windows PowerShell

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

If PowerShell blocks activation for the current session:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### Windows Command Prompt

```bat
py -3.14 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -e .
```

Verify the CLI:

```bash
cds --help
cds list profiles
```

## 5. Initialize Configuration

Generate the environment file required by a profile:

```bash
cds init local-dagster-postgres-superset
```

Open `.env` and replace every generated placeholder with an appropriate
value. Do not commit `.env` or share its secret values.

To use the profile with the local secrets service:

```bash
cds init local-dagster-postgres-superset-vault
```

Each profile defines its own required variables; `cds init` is the
authoritative source for the selected profile.

## 6. Validate Before Startup

Run the complete compile-time pipeline:

```bash
cds test local-dagster-postgres-superset
```

This validates the profile, applies security rules, resolves contracts, builds
the plan, and verifies that Docker Compose can be rendered. It does not start
containers.

You can also run each stage separately:

```bash
cds validate local-dagster-postgres-superset
cds security local-dagster-postgres-superset
cds plan local-dagster-postgres-superset
cds render local-dagster-postgres-superset
```

## 7. Start The Stack

Build images and start the services:

```bash
cds up local-dagster-postgres-superset --detach
```

The first build and image pull can take several minutes. Inspect service
status with:

```bash
docker compose ps
```

Use `--no-build` only when the required local images already exist:

```bash
cds up local-dagster-postgres-superset --detach --no-build
```

## 8. Stop Or Remove The Stack

Stop and remove containers while retaining named volumes:

```bash
docker compose down
```

Removing named volumes also removes persistent local data:

```bash
docker compose down --volumes
```

Use the `--volumes` option only when that data is no longer needed.

## Updating CDS

From a clean source checkout:

```bash
git pull --ff-only
python -m pip install -e .
cds test local-dagster-postgres-superset
cds up local-dagster-postgres-superset --detach
```

Review release notes and rendered changes before updating production-like
deployments.

## Removing CDS

1. Run `docker compose down` for active stacks.
2. Deactivate the virtual environment with `deactivate`.
3. Remove the `.venv` directory when the Python environment is no longer
   needed.
4. Remove the source checkout only after preserving any required workdir or
   volume data.
5. Uninstall Docker separately only when no other workloads depend on it.

## Common Installation Problems

### Python Is Too Old

Create the virtual environment with a Python 3.14 executable. Installing a
newer Python does not automatically replace an existing `.venv`; recreate it.

### `cds` Is Not Found

Activate `.venv` and rerun:

```bash
python -m pip install -e .
```

### Docker Daemon Is Unavailable

Start Docker Engine or Docker Desktop, then rerun `docker info`. On Linux,
confirm that the current account has permission to access the Docker socket.

### Docker Compose Is Missing

Install the Compose v2 plugin or update Docker Desktop. Confirm that
`docker compose version` works; the hyphenated v1 command is not sufficient.

### A Host Port Is Already In Use

Stop the conflicting application or configure a different host port in the
profile. The Docker error identifies the port that could not be bound.

### Image Pulls Or Builds Fail

Confirm internet access, available disk space, registry availability, and any
required proxy configuration. Retry after `docker info` succeeds.

### Windows Files Are Not Shared With Docker

Keep the repository in a location accessible to Docker Desktop and enable WSL
integration for the distribution used to run CDS.

## Contributor-Only Tools

Running profiles does not require `make`, Node.js, markdownlint, or
`pre-commit`. Contributors should follow
[CONTRIBUTING.md](../CONTRIBUTING.md) for those additional tools.
