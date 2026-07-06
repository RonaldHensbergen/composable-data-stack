# OS Support Policy

This document defines the supported host operating systems for running and contributing to Composable Data Stack (CDS), and known platform-specific limitations.

---

## Supported Platforms

| Platform | Status | Notes |
| --- | --- | --- |
| Linux (Ubuntu 22.04+) | ✅ Primary | CI-tested on every push and PR |
| macOS (12+) | ✅ CI-tested | Requires Docker Desktop for local development |
| Windows 10/11 | ✅ CI-tested | Requires Docker Desktop with WSL 2 for local development |

**Primary** means the platform is the reference environment; other platforms are tested against it.

**CI-tested** means the test suite runs on this platform on every push and PR (see CI Coverage below).

---

## Required Tooling

| Tool | Version | Notes |
| --- | --- | --- |
| Python | 3.11+ | Required for the CLI |
| Docker | Latest stable | Linux: native daemon; macOS/Windows: Docker Desktop |
| Docker Compose | v2+ | Bundled with Docker Desktop |
| Make | Any | Linux/macOS. Windows contributors can use `Makefile.ps1` instead (see below) |

---

## Known Limitations

### Windows

- `make` is not available by default. Use `Makefile.ps1` (PowerShell equivalent covering `install`, `validate`, `validate-profile`, and `package`; see the README's Windows Task Runner section), or install `make` via WSL or Chocolatey if you prefer the original workflow.
- Docker Desktop with the WSL 2 backend is required.
- Contributed files are normalized to LF via `.gitattributes`, except `.ps1`, `.bat`, and `.cmd` scripts, which stay CRLF.

### macOS

- Docker Desktop is required (there is no native Docker daemon on macOS).
- CLI commands and Makefile targets work the same as on Linux.

### All Platforms

- Container internals are always Linux-based. Paths such as `/opt/dagster/dagster_home` are intentionally Linux-specific and run inside Docker, not on the host OS.

---

## CI Coverage

The test suite runs on Linux, macOS, and Windows on every push and PR. Linting and Docker image builds run on Linux only, linting checks file content rather than OS-dependent code paths, and Docker Desktop's licensing and slower/less reliable support on hosted macOS/Windows runners make those jobs impractical to matrix.

---

## Reporting Platform Issues

If you hit a platform-specific issue, open a GitHub issue and include:

- Host OS and version
- Docker version (`docker --version`)
- Python version (`python --version`)
- Full error output
