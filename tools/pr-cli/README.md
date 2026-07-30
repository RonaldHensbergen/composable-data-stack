# PR CLI Tools

This directory holds local helper scripts for creating pull requests against this
repository. The scripts themselves are git-ignored (they are developer conveniences
and not part of the CDS package), but this README is tracked so contributors know
what belongs here.

## Purpose

The scripts in this directory automate the repetitive parts of opening a PR:

- Pre-filling the PR body from `.github/pull_request_template.md`
- Setting the base branch and title from the current branch name and recent commits
- Optionally running the local check suite (`make check`) before pushing

## Typical Scripts

| Script | Description |
| --- | --- |
| `create-pr.sh` | Creates a PR using the `gh` CLI, pre-populated with the repository template |
| `create-pr.ps1` | PowerShell equivalent for Windows contributors |

These files are not committed. Each contributor keeps their own local copy and can
adapt them to their workflow.

## Requirements

- [GitHub CLI (`gh`)](https://cli.github.com/) authenticated to this repository
- For the shell script: Bash 4+ or Zsh
- For the PowerShell script: PowerShell 7+

## Getting Started

A minimal `create-pr.sh` looks like this:

```bash
#!/usr/bin/env bash
set -euo pipefail

BRANCH=$(git rev-parse --abbrev-ref HEAD)
TITLE=$(git log -1 --pretty=%s)

gh pr create \
  --base main \
  --head "$BRANCH" \
  --title "$TITLE" \
  --body-file .github/pull_request_template.md
```

Place it in this directory, make it executable (`chmod +x create-pr.sh`), and run
it from the repository root after pushing your branch.

## PR Checklist

Before opening a PR, work through the checklist in `.github/pull_request_template.md`
and the guidance in [CONTRIBUTING.md](../../CONTRIBUTING.md).
