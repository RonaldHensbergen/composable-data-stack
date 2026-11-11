#!/usr/bin/env bash
# start_pr.sh <issue-nr> <type> <slug> [--approve-remote] [label ...]
#   e.g. start_pr.sh 1234 fix my-thing --approve-remote bug
# One action for: throwaway worktree + branch <type>/<issue>-<slug> from main
# + push + open PR. Work happens in /tmp/<branch>-wt afterwards.
set -euo pipefail
n=${1:?usage: start_pr.sh <issue-nr> <type> <slug> [--approve-remote] [label ...]}
type=${2:?missing type}
slug=${3:?missing slug}
shift 3
approve_remote=0
labels=()
for arg in "$@"; do
  case "$arg" in
    --approve-remote) approve_remote=1 ;;
    *) labels+=("$arg") ;;
  esac
done
branch="${type}/${n}-${slug}"
REPO=RonaldHensbergen/composable-data-stack
# Main checkout = the repository this script lives in (tools/..).
MAIN="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
wt="/tmp/${branch//\//-}-wt"
git -C "$MAIN" worktree add -b "$branch" "$wt" main
cd "$wt"
echo "About to push and open PR with:"
echo "  repo:   $REPO"
echo "  base:   main"
echo "  branch: $branch"
echo "  issue:  #$n"
echo "  wt:     $wt"
if [[ "$approve_remote" -ne 1 ]]; then
  echo "Refusing remote side effects without explicit approval."
  echo "Re-run with --approve-remote to allow git push and gh pr create."
  exit 1
fi
read -r -p "Proceed with remote push and PR creation? [y/N] " confirm
case "$confirm" in
  y|Y|yes|YES) ;;
  *) echo "Aborted."; exit 1 ;;
esac
git push -u origin "$branch" --quiet
args=(--repo "$REPO" --base main --head "$branch" --title "$branch" --body "Closes #$n.")
for label in "${labels[@]}"; do args+=(--label "$label"); done
gh pr create "${args[@]}" --json number,url
