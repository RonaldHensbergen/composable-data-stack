# Packaging and installer guidance

This document describes the recommended packaging approach for Linux, macOS, and Windows.

## Current status

The repository is already set up as a Python package via `pyproject.toml` and exposes the CLI entrypoint:

- `cds = "cli.main:main"`

That means the easiest install path is a Python wheel.

## Environment variables

The CLI supports two optional variables:

- `CDS_PROFILE_PATH`
  - Path to a `profiles/` directory, or to a specific `profile.yaml` file.
  - When set, `cds` can use a profile name instead of a full path.

- `CDS_MODULE_PATH`
  - Path to a `modules/` directory.
  - When set, module sources are resolved against this directory instead of the profile directory.

### Example usage

Linux / macOS:

```bash
export CDS_PROFILE_PATH=/home/ronald/Projects/composable-data-stack/profiles
export CDS_MODULE_PATH=/home/ronald/Projects/composable-data-stack/modules
```

Windows PowerShell:

```powershell
$env:CDS_PROFILE_PATH = 'C:\Projects\composable-data-stack\profiles'
$env:CDS_MODULE_PATH = 'C:\Projects\composable-data-stack\modules'
```

## Packaging options

### 1. Python wheel (recommended)

This is the simplest and most portable option.

Build the wheel:

```bash
make package
```

Install it locally:

```bash
python3 -m pip install dist/composable_data_stack-0.1.0-py3-none-any.whl
```

Advantages:

- Cross-platform
- Minimal work
- Works well for developers

### 2. Linux installers

#### Option A: Homebrew/Linuxbrew tap

Create a Homebrew formula that installs the wheel or sources and links the `cds` executable.

#### Option B: native `.deb` / `.rpm`

Use `fpm`, `cargo-deb`, or native packaging tools:

- package the wheel and entrypoint into `/usr/local/bin/cds`
- include `CDS_PROFILE_PATH` and `CDS_MODULE_PATH` guidance in package docs

### 3. macOS installers

#### Option A: Homebrew formula

The natural path on macOS is a Homebrew formula.

#### Option B: `.pkg` or `.dmg`

Use `pkgbuild` + `productbuild` to create a `.pkg`, or `create-dmg` for a `.dmg`.

### 4. Windows installers

#### Option A: PyInstaller bundle

Build a single executable with PyInstaller. This removes the Python dependency from end users.

#### Option B: MSI / EXE installer

Wrap the bundled executable in an MSI using WiX Toolset or another Windows installer tool.

## Recommended rollout

1. Publish a Python wheel first.
2. Add shell/snippet docs for `CDS_PROFILE_PATH` and `CDS_MODULE_PATH`.
3. Add Homebrew/Linuxbrew support for Linux/macOS.
4. Add a PyInstaller Windows build if you need native packaging.

## Example installer-friendly workflow

1. `python3 -m build`
2. `pip install dist/*.whl`
3. Set env vars:
   - `CDS_PROFILE_PATH`
   - `CDS_MODULE_PATH`
4. Run:
   - `cds list profiles`
   - `cds list modules`
   - `cds validate local-dagster-postgres-superset`

## Notes for installer authors

- Make sure the CLI script `cds` is installed into the user PATH.
- Document `CDS_PROFILE_PATH` and `CDS_MODULE_PATH` as the default profile/module roots.
- Prefer using the Python wheel for the core install, then wrap that with native packaging if needed.
