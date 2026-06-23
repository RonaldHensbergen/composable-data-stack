# Contributing

Thanks for your interest in contributing to Composable Data Stack (CDS).

New here? Read [docs/good-first-issues.md](docs/good-first-issues.md) first. It walks through setup, picking an issue, and the PR flow end-to-end.

## Ways To Contribute

- Report bugs
- Suggest features
- Improve docs and examples
- Add modules and contracts
- Improve validation, planner, renderer, or security checks

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```
## Line Endings
This repository enforces LF line endings for all source, config, and documentation files via 
`.gitattributes`. Windows-specific scripts (`.bat`, `.cmd`, `.ps1`) are intentionally set to CRLF.If you contribute from Windows, ensure your editor respects `.gitattributes`.

## Run Checks Locally

```bash
python -m unittest discover -s tests -p "*.py"
```

Optional smoke path:

```bash
python3 -m cli.main validate local-dagster-postgres-superset
python3 -m cli.main plan local-dagster-postgres-superset
python3 -m cli.main render local-dagster-postgres-superset
```

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
