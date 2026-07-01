# Contributing

Thanks for your interest in contributing to Composable Data Stack (CDS).

New here? Read [docs/good-first-issues.md](docs/good-first-issues.md) first. It walks through setup, picking an issue, and the PR flow end-to-end.

## Ways To Contribute

- Report bugs
- Suggest features
- Improve docs and examples
- Add modules and contracts
- Improve validation, planner, renderer, or security checks

## Understanding Modules and Profiles

New to CDS? Start with [docs/from-docker-to-cds-profile.md](docs/from-docker-to-cds-profile.md) for a complete guide on transforming Docker Compose configurations into reusable CDS modules and profiles. It covers the entire process from analysis through rendering, with practical examples.

## Development Setup

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Windows CMD:

```bat
py -m venv .venv
.venv\Scripts\activate.bat
python -m pip install -e .
```

If PowerShell blocks the activation script, run
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in the same
terminal session and activate the environment again.

## Line Endings
This repository enforces LF line endings for all source, config, and documentation files via
`.gitattributes`. Windows-specific scripts (`.bat`, `.cmd`, `.ps1`) are intentionally set to CRLF.

If you contribute from Windows, ensure your editor respects `.gitattributes`.

## Run Checks Locally

Use the combined local self-test target first:

```bash
make check
```

If you want to run the pieces individually:

```bash
python -m unittest discover -s tests -p "*.py"
```

To verify that all Dockerfiles build:

```bash
make docker-build
```

Optional smoke path:

```bash
python3 -m cli.main validate local-dagster-postgres-superset
python3 -m cli.main plan local-dagster-postgres-superset
python3 -m cli.main render local-dagster-postgres-superset
```
## Pre-commit Hooks

This repo uses [pre-commit](https://pre-commit.com/) to catch formatting and lint issues before you open a PR. Most hooks mirror the checks that run in CI (`markdownlint`, `yamllint`); `flake8` is additional Python quality coverage that CI does not yet run.

Install once per clone:

```bash
pip install pre-commit
pre-commit install
```

Run against all files (recommended before your first PR):

```bash
pre-commit run --all-files
```

The `markdownlint` hook runs via `npx`, so Node.js must be available on your machine. Every other hook is installed and managed by `pre-commit` itself, no other manual setup needed.

Hooks run automatically on `git commit` after `pre-commit install`. If a hook modifies files (for example, trimming trailing whitespace), re-stage and commit again.

Hooks included:

- `trailing-whitespace`, `end-of-file-fixer` — basic hygiene
- `check-yaml`, `check-merge-conflict`, `check-added-large-files` — safety checks
- `flake8` — catches Python syntax errors and undefined names (`E9`, `F63`,
  `F7`, `F82`)
- `yamllint` — same config (`.yamllint.yml`) used in CI
- `markdownlint` — same version and config used in CI

## Branch And PR Flow

1. Create a feature branch from main.
2. Keep changes focused and small.
3. Add or update tests when behavior changes.
4. Open a PR with a clear summary and test evidence.

## Commit Message Guidance

Use imperative style and keep scope clear.

Examples:

- Add default render output path
- Fix secret placeholder rendering
- Update planner regression tests

## Coding Guidelines

- Preserve existing style and APIs unless change is intentional.
- Avoid embedding secret values in generated output.
- Prefer explicit contracts over implicit cross-module assumptions.
- Keep docs in sync with behavior changes.

## Testing Expectations

- New features should include tests.
- Bug fixes should include a regression test when possible.
- PRs should pass CI before merge.

### Writing Regression Tests

When fixing a bug, please write a regression test to prevent the issue from returning. Regression tests should be placed in the `tests/` directory and should clearly reproduce the original failing condition before the fix is applied. Use `unittest.mock` where appropriate to isolate your tests and ensure they run quickly without external dependencies.

## Pull Request Checklist

- [ ] Tests added or updated
- [ ] Tests pass locally
- [ ] Docs updated (if needed)
- [ ] No secrets or generated artifacts committed
- [ ] PR description explains user-facing impact

## Need Help?

Open a GitHub issue with details, logs, and reproduction steps.
