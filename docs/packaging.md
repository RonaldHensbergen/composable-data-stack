# Packaging and installer guidance

This document describes the recommended packaging approach for Linux, macOS, and Windows.

## Current status

The repository is already set up as a Python package via `pyproject.toml` and exposes the CLI entrypoint:

- `cds = "cli.main:main"`

The wheel contains the CLI and its built-in security rules. Profiles, modules,
Docker image build contexts, and runtime workdirs remain project content and
are not installed into the Python environment. Until registry-backed
`cds pull` support exists, use a repository checkout or provide external roots
through `CDS_PROFILE_PATH` and `CDS_MODULE_PATH`.

The wheel includes both renderers. Docker Compose remains the default target.
Users of `--target helm` must install Helm and kubectl separately and must supply
their own Kubernetes context. The package does not embed cluster credentials or
secret values.

## Environment variables

The CLI supports two optional variables:

- `CDS_PROFILE_PATH`
  - Accepts any of the following forms:
    - A bare **profile name** (e.g. `local-dagster-postgres-superset`) — resolved against the default `profiles/` directory.
    - A **profiles root directory** (e.g. `/path/to/profiles`) — profile names are looked up as subdirectories.
    - A **specific `profile.yaml` file path** — used directly without any further resolution.
  - When set, running `cds validate` (or `plan`, `render`) without an explicit profile argument uses this value.

- `CDS_MODULE_PATH`
  - Path to a `modules/` directory.
  - When set, module sources are resolved against this directory instead of the profile directory.

### Example usage

Linux / macOS:

```bash
# Profiles root directory — use any profile by name
export CDS_PROFILE_PATH=/home/ronald/Projects/composable-data-stack/profiles

# Or a specific profile by name — cds validate works without further args
export CDS_PROFILE_PATH=local-dagster-postgres-superset

# Or a direct profile file path
export CDS_PROFILE_PATH=/home/ronald/Projects/composable-data-stack/profiles/local-dagster-postgres-superset/profile.yaml

export CDS_MODULE_PATH=/home/ronald/Projects/composable-data-stack/modules
```

Windows PowerShell:

```powershell
# Profiles root directory
$env:CDS_PROFILE_PATH = 'C:\Projects\composable-data-stack\profiles'

# Or a specific profile name
$env:CDS_PROFILE_PATH = 'local-dagster-postgres-superset'

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
python3 -m pip install dist/composable_data_stack-*-py3-none-any.whl
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
   - `cds render local-dagster-postgres-superset --target helm`

## TestPyPI publishing

`.github/workflows/testpypi.yml` builds, checks, installs, and exercises the
wheel before publishing the same distributions to TestPyPI. It uses trusted
publishing and does not require a stored API token.

Both `testpypi.yml` and `pypi.yml` reuse `build-python-package.yml`, which
smoke-tests the built wheel two ways before it's ever uploaded:

1. Installs the wheel into an isolated venv and runs `cds --help`, `cds
   validate`, and a rule-set sanity check.
2. Runs the full end-user flow with no source checkout on
   `CDS_PROFILE_PATH`/`CDS_MODULE_PATH`: `cds get` a profile into an empty
   directory, `cds init` it, then `cds up` the real docker compose stack and
   confirm every service reports healthy. This is what catches packaging bugs
   that only affect the `cds get`-fetched layout (missing bundled assets,
   project-root resolution regressions, etc.) rather than a plain `pip
   install .` from the repo checkout.

Repository setup:

1. Create a GitHub environment named `testpypi`. Add required reviewers if
   publication should require approval.
2. On TestPyPI, create a pending trusted publisher for:
   - owner: `RonaldHensbergen`
   - repository: `composable-data-stack`
   - workflow: `testpypi.yml`
   - environment: `testpypi`
3. Ensure the version in `pyproject.toml` has never been uploaded to TestPyPI.
   Published files and versions are immutable.
4. Run **Publish CLI to TestPyPI** through the Actions workflow-dispatch UI.

To verify a TestPyPI artifact while resolving dependencies from PyPI, download
the wheel without dependencies and install that local artifact:

```bash
python -m pip download \
  --no-deps \
  --index-url https://test.pypi.org/simple/ \
  composable-data-stack
pipx install ./composable_data_stack-*.whl
cds --help
```

Publishing to production PyPI uses a separate workflow, environment, and
trusted publisher, described next. Do not reuse the TestPyPI workflow or
repository URL for production.

## PyPI publishing (production)

`.github/workflows/pypi.yml` reuses the same build/check/wheel-smoke-test
job as TestPyPI (factored out into
`.github/workflows/build-python-package.yml` as a reusable `workflow_call`
so a fix to one flow can't accidentally be forgotten in the other), then
publishes to production PyPI using trusted publishing (no stored API
token). It runs on `v*.*.*` tag pushes (the same tags
`.github/workflows/release.yml` reacts to) or via manual workflow-dispatch.
Every run — tag push or manual dispatch — checks `project.version` in
`pyproject.toml` against `scripts/check_release_version.py`'s
`--block-prerelease` guard: a tag push checks the pushed tag against
`pyproject.toml`; a manual dispatch has no tag ref to check, so it instead
checks `pyproject.toml`'s own declared version against itself, which still
enforces the pre-release block below. Always trigger a manual publish from
the commit you intend to release, since dispatch never verifies the working
tree against a tag.

`v*.*.*` also matches pre-release tags (for example `v0.4.0b1`), but
`check_release_version.py` is invoked here with `--block-prerelease`, so a
tag whose version is a pre-release (alpha/beta/rc/dev, per PEP 440) fails
the version check and the workflow stops before `build`/`publish` ever run.
Bump `project.version` in `pyproject.toml` to a final version before tagging
a production release. (`release.yml`'s own version check keeps allowing
pre-release tags — it only opens a draft GitHub release, not a PyPI publish —
so this restriction is specific to `pypi.yml`.)

**A pushed tag publishes to production PyPI immediately unless you gate it
with required reviewers — do this before the first release.** Unlike
`release.yml` (which opens a *draft* GitHub release for review before anyone
hits publish), this workflow has no separate review step of its own, and a
published version can never be replaced or re-uploaded. GitHub auto-creates
the `pypi` environment the first time the workflow references it, and a
freshly auto-created environment has **no required reviewers** by default —
you must add them explicitly (step 1 below) or every tag push publishes
unattended.

Repository setup:

1. **Create a GitHub environment named `pypi` and add required reviewers.**
   This is not optional here: without it, any push of a matching tag
   publishes to PyPI with no human in the loop, and a bad publish can never
   be undone. Settings → Environments → New environment → `pypi` →
   "Required reviewers".
2. On PyPI, create a pending trusted publisher for:
   - owner: `RonaldHensbergen`
   - repository: `composable-data-stack`
   - workflow: `pypi.yml`
   - environment: `pypi`
3. Bump `project.version` in `pyproject.toml` to a final (non-pre-release)
   version, merge that change to `main`, then push a matching `vX.Y.Z` tag
   (or run **Publish CLI to PyPI** via workflow-dispatch for a manual
   publish from that same commit). Published files and versions are
   immutable; a version can never be re-uploaded, so a mistaken publish
   requires a new version bump.
4. Verify the release:

   ```bash
   pipx install composable-data-stack
   cds --help
   ```

## Notes for installer authors

- Make sure the CLI script `cds` is installed into the user PATH.
- Document `CDS_PROFILE_PATH` and `CDS_MODULE_PATH` as the default profile/module roots.
- Prefer using the Python wheel for the core install, then wrap that with native packaging if needed.
