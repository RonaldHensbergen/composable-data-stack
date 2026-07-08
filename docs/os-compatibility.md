# OS Compatibility Analysis (Historical)

> **This is a point-in-time investigation, not a live status document.**
> For current OS support status, requirements, and known limitations, see
> [docs/support-policy.md](support-policy.md). This document is kept as a
> record of the analysis that identified the work needed for Windows/macOS
> support and explains the reasoning behind a few platform-specific design
> decisions.

## Executive Summary

This analysis identified OS-specific code in the Composable Data Stack (CDS) repository and the changes needed for full cross-platform support across Linux, macOS, and Windows. All identified work has since been completed. See [docs/support-policy.md](support-policy.md) for the current state.

---

## Identified OS-Specific Areas

### 1. Docker Container Paths

**Location**: Module configurations
**Example**: `modules/orchestration/dagster/module.yaml` uses `homeDir: /opt/dagster/dagster_home`

These paths run *inside* Docker containers (always Linux-based), not on the host OS, so they are intentionally Linux-specific regardless of the host platform. No change was needed here.

### 2. Shell Commands in Docker

**Location**: Docker Compose service definitions (e.g. `command: [bash, -c, ...]`)

These commands run inside Linux containers, so using `bash` is correct and intentional regardless of the host OS. No change was needed here.

### 3. Path Handling in the Python CLI

**Location**: `cli/*.py`

The CLI uses `pathlib.Path` throughout, which provides a good cross-platform foundation. One real bug was found here: `cli/renderer.py` computed relative paths via a `Path.relative_to()` → `os.path.relpath()` fallback, but `os.path.relpath()` raises `ValueError` on Windows when the two paths are on different drives (e.g. `C:\` vs `D:\`), something that has no equivalent failure mode on Linux/macOS, so it went undetected until Windows CI coverage was added. This affected two separate functions, `_resolve_context_path` and `_rewrite_local_path`, not one. The fix adds a nested `try/except` that falls back to an absolute path when `relpath` itself fails:

```python
try:
    rel = Path(chosen).relative_to(compose_dir)
except ValueError:
    try:
        rel = Path(os.path.relpath(chosen, compose_dir))
    except ValueError:
        # On Windows, relpath raises when chosen and compose_dir are on
        # different drives, no relative path can express that. Fall
        # back to the absolute path.
        return Path(chosen).as_posix()
return rel.as_posix()
```

### 4. Makefile

**Location**: `Makefile`

Uses Bash/POSIX shell syntax, which doesn't run on Windows without WSL/Git Bash/Make for Windows. `Makefile.ps1` now provides a PowerShell equivalent for the core targets (`install`, `validate`,
`validate-profile`, `package`), see the README's Windows Task Runner section.

### 5. Documentation Examples

**Location**: `README.md`, `CONTRIBUTING.md`

Originally only showed Linux/macOS setup examples (e.g. `source .venv/bin/activate`). Windows PowerShell and CMD equivalents have since been added to both files.

---

## What Was Already Cross-Platform

- `pathlib.Path` used throughout for path handling, home directory expansion, and existence checks
- `os.getenv()` for environment variables
- Docker Compose itself (via Docker Desktop on macOS/Windows)
- Container internals and port mappings, identical across host OSes

---

## Outcome

Everything identified in this analysis has been implemented and merged:

- The `os.path.relpath` cross-drive bug is fixed in both affected functions, with regression tests
- `Makefile.ps1` exists for Windows contributors
- `README.md` and `CONTRIBUTING.md` have Windows PowerShell/CMD examples
- `.gitattributes` normalizes line endings, with CRLF exceptions for `.ps1`/`.bat`/`.cmd` scripts
- CI runs the test suite on Linux, macOS, and Windows on every push and PR

For current OS support status, required tooling, and known limitations,
see [docs/support-policy.md](support-policy.md).
