# Your First Contribution

A walkthrough for going from zero to a merged PR in this repo. If you've never contributed to an open-source project before, start here.

## 1. Setup

Fork the repo, then clone **your fork** (not `RonaldHensbergen/composable-data-stack` directly, you won't have push access to that one):

```bash
git clone https://github.com/<your-username>/composable-data-stack.git
cd composable-data-stack
```

Create a virtual environment and install the project in editable mode:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

> **Windows users:** the activate path is different. Use
> `source .venv/Scripts/activate` (Git Bash) instead of `.venv/bin/activate`.
> If you get `ModuleNotFoundError: No module named 'yaml'` after running tests,
> it almost always means the venv wasn't activated, or wasn't activated
> *before* `pip install -e .` — dependencies installed into the wrong Python.

`pip install -e .` registers the `cds` command on your PATH (see
`pyproject.toml` → `[project.scripts]`). Confirm it worked:

```bash
cds --help
```

Then confirm the test suite runs:

```bash
python -m unittest discover -s tests -p "*.py"
```

You should see all tests pass. If a test fails on something unrelated to your change (for example, a path-separator assertion that only breaks on Windows), that's a pre-existing issue, not something you broke, note it in your PR's Validation section instead of trying to fix it as a drive-by change.

## 2. Choosing An Issue

Filter the [issue tracker](https://github.com/RonaldHensbergen/composable-data-stack/issues) by the `good first issue` label first. Those are scoped to be self-contained, no deep familiarity with the planner, renderer, or contract resolution internals required.

Before picking one, check:

- **Is it already assigned, or does an open PR reference it?** Check the
  issue's sidebar and the [PR list](https://github.com/RonaldHensbergen/composable-data-stack/pulls).
- **Does the Acceptance Criteria section give you something concrete to
  verify?** If you can't tell when you're "done," ask in the issue before
  starting.
- **Does it touch code that depends on Docker, or a service you can't run
  locally?** (Postgres, Dagster, Superset, Vault.) If you can't actually run
  and verify the thing you're changing, say so in the issue before claiming
  it — some issues need that runtime to validate properly.

Docs-only issues (`area:docs`) are the lowest-risk way to make a first PR: no test suite to satisfy, low chance of merge conflicts, and the acceptance criteria is usually "is the information accurate and current."

## 3. Coding And Testing Flow

```bash
git checkout -b <type>/<short-description>
# e.g. docs/troubleshooting-section, fix/secret-leak-in-render
```

Make your change. Keep the diff focused on the issue. If you spot an unrelated bug while you're in there, file a separate issue instead of fixing it in the same PR. A reviewer evaluating "did this fix the thing the issue asked for" gets harder to satisfy the bigger the diff gets.

Before opening the PR:

```bash
python -m unittest discover -s tests -p "*.py"
```

If your change is code (not docs), also run the actual CLI workflow against a profile, this is the same sequence described in the README's Quickstart, so it's the most realistic smoke test:

```bash
cds validate local-dagster-postgres-superset
cds security local-dagster-postgres-superset
cds plan local-dagster-postgres-superset
cds render local-dagster-postgres-superset
```

If you cite specific CLI output, error codes, or messages in docs (like a troubleshooting table), don't guess the wording, grep the actual source (`cli/*.py`) or trigger the error live and copy the real output. Diagnostics in this codebase follow a consistent format, check `cli/diagnostics.py` for the exact pattern rather than assuming, since the wording changes between versions.

When editing README.md or files under `docs/`, double-check you haven't copy-pasted anything from GitHub's *rendered* web page instead of the raw file, the web view adds clickable anchor links next to headings (`[#-some-heading](#-some-heading)`) that don't exist in the actual markdown
source. If you see one of those in your diff, delete it.

## 4. Opening The PR

Push your branch and open a PR against `main`:

```bash
git push origin <your-branch-name>
```

Fill out the PR template honestly:

- **Summary** — what changed and why, in your own words, not a copy of the
  issue.
- **Validation** — the actual commands you ran and their actual output. If
  something failed and it's not related to your change, say so explicitly
  rather than omitting it.
- **Checklist** — only check boxes that are actually true. Leave "Tests
  added or updated" unchecked with a one-line reason if your change is
  docs-only.

Add `Closes #<issue-number>` so the PR auto-links to and closes the issue on
merge.

## 5. After You Open It

As a first-time contributor, expect two things before CI runs:

- **"Review required"** — at least one approving review from someone with
  write access (the maintainer) is required before merge. This is normal,
  not a sign something's wrong with your PR.
- **"Workflow awaiting approval"** — GitHub doesn't auto-run Actions for
  first-time contributors from a fork, as a security measure. The
  maintainer has to manually approve the workflow run before `CI/test`
  executes. There's nothing you need to do here except wait.

This is a small, solo-maintained project, so response times won't be instant. Give it a few days before following up with a polite comment on the PR.