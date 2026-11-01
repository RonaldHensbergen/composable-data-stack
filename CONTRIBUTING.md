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

## How We Work

This repo adopts the working style of [Impatient Programming](https://impatientprogramming.org/) — XP rewritten for teams building with coding agents. The practices below are the ones that live in this repository; the PR and issue templates encode them.

- **Get your story straight before you commit to building.** Every feature request carries success criteria ("how will we know this worked?") and starts with the One Question: *what do we still need to learn before we can build this?*
- **Ship in small, frequent PRs from short-lived branches.** Long-lived branches go stale in hours when agents write code all day; rebase on `main` and keep the PR focused.
- **Every PR breathes in and out.** The agent may sprawl while exploring, but the exhale happens in the same PR: fold what you learned back into the existing code. No new technical debt ships.
- **Label throughput honestly.** `fix:` means a defect in code that is live in production. Polishing work that was never shipped is `refine:` — git-cliff groups it under "Refinements" in the changelog so dashboards don't read refinement as collapsing quality.
- **The Contract is written down and executable.** The JSON Schemas in `cli/resources/` plus each `module.yaml`'s `contracts:` blocks are the Contract. `tests/test_module_contract_schema.py` enforces it on every module and profile in CI, and [docs/contract-coverage.md](docs/contract-coverage.md) publishes how much of the system the Contract covers with executable tests (`make contract-coverage`).
- **Every test must earn its place.** Run the kill matrix (`make test-mutation`, mutation testing via mutmut) and keep a test only if it catches a mutant no other test catches. CI runs the matrix on changed files for every PR and publishes the full report weekly (`.github/workflows/mutation-tests.yml`).
- **Pin behavior before you change it.** When touching existing code, add a golden or characterization test that pins the current behavior first, then change the code.
- **Bug fixes get a regression test.** See "Writing Regression Tests" below.
- **Spikes are cheap — run them, then delete them.** Throwaway prototypes that answer one question stay out of the repo; only the distilled result lands in a PR. When a design needs talking through first, produce a design artifact (options with trade-offs, a rejected comp, one recommendation) before writing code — see `.github/prompts/design-artifact.prompt.yml`.
- **Green merges itself where it is safe.** Label a PR `automerge` and, once CI is green, `.github/workflows/auto-merge.yml` merges it automatically. Human review still guards everything else — see [docs/maintainer-merge-policy.md](docs/maintainer-merge-policy.md).
- **Work in the open, with your agents.** Agent instructions live in `AGENT.md` and `.github/copilot-instructions.md`; keep agent-produced changes reviewable like any other PR.
- **The practices are audited, not just written down.** Contract coverage and the kill matrix run on schedules (`contract-coverage.yml`, `mutation-tests.yml`), and a biweekly audit (`make practices-audit`, filed by `practices-audit.yml`) rolls up coverage, the `fix:`/`refine:` commit mix, `automerge` usage, and the latest kill-matrix run into one issue. The audit also watches the source method itself: a pinned sha256 of <https://impatientprogramming.org/rules/> lives in `scripts/imp-rules.sha256`, and when the live rules page drifts from it the audit files a tracking issue to re-read the rules and re-assess this adoption — after handling it, re-pin with `make practices-audit-pin`.
- **Branches are audited for staleness daily.** `make branch-hygiene` (local; also covers local branches and worktrees) or the daily `branch-hygiene.yml` run lists branches whose work is fully merged into `main` — safe to delete. The workflow files a `branch-hygiene` report issue when stale branches exist and closes it when clean.

## Development Setup

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
```

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
```

Windows CMD:

```bat
py -m venv .venv
.venv\Scripts\activate.bat
python -m pip install -e ".[test]"
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
python -m unittest discover -s tests -p "test_*.py" -v
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

This repo uses [pre-commit](https://pre-commit.com/) to catch formatting and lint issues before you open a PR. Most hooks mirror the checks that run in CI (`markdownlint`, `yamllint`, and Ruff's pyupgrade-based deprecation check); Renovate config validation runs via `make lint` and CI, while `flake8` remains additional Python quality coverage that CI does not yet run.

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

1. Create a feature branch from main. Keep it short-lived: rebase on `main` early and often.
2. Keep changes focused and small.
3. Add or update tests when behavior changes.
4. Open a PR with a clear summary and test evidence.

`main` requires an approving review and green CI before merge. See [docs/maintainer-merge-policy.md](docs/maintainer-merge-policy.md) for how that applies to the maintainer's own PRs, since a solo maintainer can't approve their own review.

If your PR touches a SonarCloud-flagged security/vulnerability finding, read [docs/sonarcloud-quality-gate.md](docs/sonarcloud-quality-gate.md) first: a PR's SonarCloud check passing does not guarantee `main`'s quality gate stays green afterward, because the two are scoped differently.

## Commit Message Guidance

Use imperative style, keep scope clear, and prefix conventional types so git-cliff groups the changelog correctly: `feat:`, `fix:`, `refine:`, `refactor:`, `perf:`, `docs:`, `test:`, `chore:`.

`fix:` is reserved for defects in code that is live in production. Refinement of work that has not shipped yet is `refine:`, not `fix:` — label honestly so throughput metrics stay truthful (see "How We Work").

Examples:

- `feat: add default render output path`
- `fix: resolve secret placeholder rendering`
- `refine: simplify planner regression test setup`
- `docs: update planner regression tests`

## Coding Guidelines

- Preserve existing style and APIs unless change is intentional.
- Avoid embedding secret values in generated output.
- Prefer explicit contracts over implicit cross-module assumptions.
- Keep docs in sync with behavior changes.

## Testing Expectations

- New features should include tests.
- Bug fixes should include a regression test when possible.
- Every test must earn its place: run the kill matrix with `make test-mutation` (mutation testing via mutmut) and keep a test only if it catches a mutant that no other test catches. CI publishes the matrix for changed files on PRs and the full report weekly.
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
