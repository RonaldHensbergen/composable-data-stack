# Installer guidance for Linux, macOS, and Windows

This document describes a recommended installer approach for each platform.

## 1. Build the Python package

The repo is already configured as a Python package via `pyproject.toml`.

Use the included Make target:

```bash
make package
```

This builds distributables in `dist/`.

## 2. Linux

### Option A: Python wheel

Install with pip:

```bash
python3 -m pip install dist/composable_data_stack-0.1.0-py3-none-any.whl
```

### Option B: Homebrew/Linuxbrew formula

Create a formula that installs the wheel and links the `cds` executable.

### Option C: native package

Use a packager like `fpm` to create a `.deb` or `.rpm`:

```bash
fpm -s python -t deb dist/composable_data_stack-0.1.0-py3-none-any.whl
```

Then install with:

```bash
sudo dpkg -i package.deb
```

## 3. macOS

### Option A: Python wheel

Install with pip:

```bash
python3 -m pip install dist/composable_data_stack-0.1.0-py3-none-any.whl
```

### Option B: Homebrew formula

Publish a Homebrew formula that installs the package and links `cds`.

### Option C: native installer

Create a `.pkg` or `.dmg` if you need a native macOS installer.

## 4. Windows

### Option A: Python wheel

Install with pip in a Python 3 environment:

```powershell
python -m pip install dist\composable_data_stack-0.1.0-py3-none-any.whl
```

### Option B: PyInstaller bundle

If you want an executable without requiring Python, use PyInstaller:

```bash
pip install pyinstaller
pyinstaller --onefile --name cds cli/main.py
```

Then distribute `dist/cds.exe`.

### Option C: MSI/EXE installer

Wrap the PyInstaller executable with WiX Toolset or another installer authoring tool.

## 5. Environment variables

Set these so the CLI can resolve profiles and modules without full paths.

### `CDS_PROFILE_PATH`

Accepts any of these forms:

| Form | Example | Behaviour |
| ---- | ------- | --------- |
| Profile name | `local-dagster-postgres-superset` | Looked up as a subdirectory under the default `profiles/` directory |
| Profiles root directory | `/path/to/profiles` | Profile names passed to commands are resolved as subdirectories of this root |
| Specific profile file | `/path/to/profiles/local/profile.yaml` | Used directly; no further resolution needed |

When `CDS_PROFILE_PATH` is set, running `cds validate` (or `plan`, `render`) without an explicit profile argument resolves the profile automatically.

### `CDS_MODULE_PATH`

Path to a `modules/` directory. Module sources are resolved relative to this root instead of the profile directory.

The CLI also supports optional shell completion when `argcomplete` is installed.

### Install completion support

```bash
python3 -m pip install argcomplete
```

### Enable completion

Bash:

```bash
eval "$(register-python-argcomplete cds)"
```

Zsh:

```bash
autoload -U bashcompinit
bashcompinit
eval "$(register-python-argcomplete cds)"
```

### Linux / macOS

```bash
# Profiles root directory
export CDS_PROFILE_PATH=/path/to/profiles

# Or a bare profile name (resolved against profiles/ in the working directory)
export CDS_PROFILE_PATH=local-dagster-postgres-superset

# Or a direct profile file
export CDS_PROFILE_PATH=/path/to/profiles/local-dagster-postgres-superset/profile.yaml

export CDS_MODULE_PATH=/path/to/modules
```

### Windows PowerShell

```powershell
# Profiles root directory
$env:CDS_PROFILE_PATH = 'C:\path\to\profiles'

# Or a bare profile name
$env:CDS_PROFILE_PATH = 'local-dagster-postgres-superset'

$env:CDS_MODULE_PATH = 'C:\path\to\modules'
```

## 6. Using the CLI with defaults

Once installed, commands can use shorthand profile names when the env vars are set:

```bash
cds list profiles
cds list modules
cds validate local-dagster-postgres-superset
cds plan local-dagster-postgres-superset --json
cds render local-dagster-postgres-superset
```
