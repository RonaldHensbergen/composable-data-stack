#!/usr/bin/env bash
# start_pr.sh <issue-nr> <type> <slug> [label ...]
#   e.g. start_pr.sh 1234 fix my-thing bug
# One action for: throwaway worktree + branch <type>/<issue>-<slug> from main
# + push + open PR. Work happens in /tmp/<branch>-wt afterwards.
set -euo pipefail
n=${1:?usage: start_pr.sh <issue-nr> <type> <slug> [label ...]}
type=${2:?missing type}
slug=${3:?missing slug}
shift 3
branch="${type}/${n}-${slug}"
REPO=RonaldHensbergen/composable-data-stack
# Main checkout = the repository this script lives in (tools/..).
MAIN="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
wt="/tmp/${branch//\//-}-wt"
git -C "$MAIN" worktree add -b "$branch" "$wt" main
cd "$wt"
git push -u origin "$branch" --quiet
args=(--repo "$REPO" --base main --head "$branch" --title "$branch" --body "Closes #$n.")
for label in "$@"; do args+=(--label "$label"); done
gh pr create "${args[@]}" --json number,url
