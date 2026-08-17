# Composable Data Stack repository instructions

## Development commands

- Use Python 3.14 or newer. Create a virtual environment and install the CLI with
  `python3 -m venv .venv && source .venv/bin/activate && python -m pip install -e .`.
  Install `.[dev]` when running coverage, Bandit, or `pip-audit`.
- Run the local lint and test suite with `make check`. Its components are
  `make lint` and `python -m unittest discover -s tests -p "test_*.py" -v`
  with `PYTHONWARNINGS=error::DeprecationWarning:cli,error::DeprecationWarning:test_.*`
  so repo-owned deprecations fail the suite without inheriting third-party noise.
- Run one test module with `python -m unittest tests.test_validator`, one test
  class with `python -m unittest tests.test_validator.ValidatorRegressionTest`,
  or one test method with
  `python -m unittest tests.test_validator.ValidatorRegressionTest.test_validate_profile_rejects_module_source_traversal_outside_modules_tree`.
- Match CI's coverage gate with
  `coverage run -m unittest discover -s tests -p "test_*.py" -v && coverage report -m`;
  coverage is scoped to `cli/` and must remain at least 65%.
- Lint Markdown with
  `npx --yes markdownlint-cli@0.49.0 "**/*.md" ".github/**/*.md"`, YAML with
  `yamllint .`, Python deprecations with `ruff check .` (configured for Ruff's
  `UP` pyupgrade rules only), and Renovate config with
  `npx --yes --package renovate -- renovate-config-validator --strict renovate.json`.
  `pre-commit run --all-files` additionally runs the repository's selected
  Flake8 checks and file hygiene hooks.
- Validate a profile with `cds validate <profile-name-or-path>` or
  `make validate-profile P=profiles/.../profile.yaml`. Exercise the compile
  pipeline with `cds test <profile>`; use an explicit temporary `--output` when
  invoking `cds render` during development to avoid writing the generated
  `docker-compose.yml` into the repository root.
- Build the Python distribution with `make package` and all Dockerfiles with
  `make docker-build`. Docker builds use the repository root as context for the
  Dagster and Superset images.
- On Windows, `Makefile.ps1` provides install, profile-validation, and package
  targets; use the direct Python commands or pre-commit for tests and linting.

## Architecture

- CDS is a compiler and CLI for declarative data stacks. Profiles under
  `profiles/` select module instances and topology; reusable `module.yaml`
  definitions under `modules/` own configuration schemas, provided/consumed
  contracts, and Docker Compose implementation templates; `shared/contracts/`
  documents the interfaces between modules. The JSON schemas in `cli/resources/`
  describe these YAML document formats.
- The compile-time path is `load -> validate -> plan -> render`.
  `cli/validator.py` checks profile/module shape, module roots, dependency
  graphs, secrets, contract compatibility, and outputs. `cli/planner.py` applies
  schema defaults and resolves contracts into a `cds/v1alpha1` Plan.
  `cli/renderer.py` expands template expressions and emits one Compose model.
  `cli/main.py` orchestrates these stages and owns CLI/path behavior.
- `cds test` runs validate, rule-based security checks, plan, and render.
  `cds up` deliberately omits the security stage, renders
  `docker-compose.yml`, then delegates build/start to the real
  `docker compose` executable. CDS itself does not run containers.
- Security validation is data-driven by `cli/resources/rule-schema.json` and
  `cli/resources/rule-set.json`; both files are bundled into the Python
  distribution. Runtime/image support code lives under `images/`
  and `workdirs/`; those files are consumed by rendered module services rather
  than imported by the CLI package.

## Repository-specific conventions

- Treat `module.yaml` as the source of truth for a module. It uses
  `apiVersion: cds/v1alpha1`, `kind: Module`, a closed JSON Schema-style
  `spec.configSchema`, contract declarations, and an inline
  `spec.implementation.kind: docker-compose` template. Add schema defaults in
  `configSchema`; the planner recursively materializes them.
- Keep modules isolated. A module must not hardcode another module's service
  name. Providers expose named contracts, consumers declare `consumes` entries
  with `mappedFrom`, and profiles bind them with
  `contractRef: <module-id>.<provided-contract>`. `dependsOn` controls
  cross-module Compose startup ordering; it does not replace contract binding.
- Module sources must be relative and resolve beneath `modules/` or
  `modules-experimental/`; absolute paths and traversal outside those roots are
  rejected. `CDS_MODULE_PATH` and `CDS_PROFILE_PATH` support external roots in
  CLI and test scenarios.
- Preserve the interpolation vocabulary across planning and rendering:
  `${config.*}` reads normalized module config, `${bindings.*}` reads consumed
  contract fields, and `${service.host}` resolves to the profile's module
  instance ID. Renderer-local service dependencies, volumes, build contexts,
  and service names are rewritten relative to the module/profile and
  namespaced by module ID.
- Never resolve secret values into plans or rendered Compose. Profiles map
  aliases under `spec.secrets.values` to `CDS_*` environment names; module
  config refers to `secrets.<alias>`, and planner/renderer output only
  `${CDS_*}` placeholders for Docker Compose to resolve at runtime. Keep `.env`
  files and generated Compose artifacts uncommitted.
- YAML documents include a `yaml-language-server` schema comment pointing to
  the matching repository schema. Module directories are named by
  capability/implementation, such as `modules/warehouse/postgres`; module
  service names must exist and be unique.
- User-facing validation failures are structured `Diagnostic` values with a
  stable error/warning code, message, and precise data path. Extend the existing
  code families rather than returning ad hoc strings, and stop later compile
  stages when error diagnostics exist.
- Tests use the standard-library `unittest` runner, `unittest.mock`, and
  temporary directories. Keep unit tests independent of Docker and external
  services; the subprocess workflow tests exercise the installed `cds` entry
  point and the Docker-specific workflows cover runtime smoke behavior.
- Keep LF endings for source, YAML, and documentation; PowerShell/batch files
  are the intentional CRLF exceptions defined by `.gitattributes`.
