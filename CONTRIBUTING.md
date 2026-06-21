# Contributing
Thanks for your interest in contributing to Composable Data Stack (CDS).

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

## Writing Regression Tests
When fixing bugs, add a regression test to prevent the same issue from regressing in future releases. This section defines the repository standard for regression test structure, file layout, naming, and local execution.

### Test Directory Layout
All test folders mirror the source structure under the root `tests/` directory:
- CLI logic: `tests/cli/`
- Module components: `tests/modules/<category>/` (bi, orchestration, warehouse, secrets)
- Profile validation: `tests/profiles/`

Place your regression test inside the subfolder matching the module you modified.

### Core Regression Test Requirements
When fixing a bug, write a regression test to prevent the issue from recurring.
Regression tests must live under the `tests/` directory and fully reproduce the original failing state prior to your fix.
Use `unittest.mock` to isolate test logic and eliminate external runtime dependencies for faster, reliable test runs.

### Naming Standards
1. Test file: `test_<target_module>.py`
2. Regression test function: `test_regression_<short_bug_description>`
   Example: `test_regression_unsafe_default_postgres_password`

### Minimal Regression Test Template
Follow the standard Arrange-Act-Assert pattern consistent with all existing repository test suites:
```python
def test_regression_security_default_password():
    # Arrange: Input configuration that reproduces the original bug
    raw_profile = {"modules": ["dagster", "postgres"], "credentials": {"password": "default"}}

    # Act: Execute the broken validation/security logic
    result = validate_security_constraints(raw_profile)

    # Assert: Confirm the fixed behavior blocks the bug scenario
    assert result.has_critical_risk is True
    assert "weak default password" in result.risk_messages
```

### Local Test Execution Workflow
Run the full test suite before submitting your PR to avoid breaking existing functionality:
```bash
# Full repository test suite (official standard command)
python -m unittest discover -s tests -p "*.py"

# Target a single module test folder for faster iterative development
python -m unittest discover -s tests/modules/warehouse -p "*.py"
```

### Regression Test Acceptance Rules
All regression tests must satisfy these requirements before PR merge:
1. The test reproduces the exact failure scenario described in the linked GitHub issue
2. File path and test function strictly follow the naming standards above
3. Full local test suite executes without failures or warnings
4. The related issue number is referenced in your PR description

## Pull Request Checklist
- [ ] Tests added or updated for changed behavior
- [ ] All local unit tests pass
- [ ] Documentation updated (if user-facing behavior changes)
- [ ] No plaintext secrets or auto-generated artifacts committed
- [ ] PR description clearly outlines user-facing impact of changes

## Need Help?
Open a GitHub issue with full context: reproduction steps, error logs, and environment details.