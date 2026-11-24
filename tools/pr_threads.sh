#!/usr/bin/env bash
# pr_threads.sh <pr-number> [--include-bodies] — compact list of review threads (id, state, location, optional first comment).
# Thin wrapper: argument parsing and the gh call stay in bash; the output
# formatter is pr_threads_fmt.py (no inline python3 -c quoting boundary).
set -euo pipefail

include_bodies=0
if [[ $# -lt 1 ]]; then
  echo "usage: pr_threads.sh <pr-number> [--include-bodies]" >&2
  exit 1
fi

pr="$1"
shift

if [[ $# -gt 0 ]]; then
  if [[ "$1" == "--include-bodies" ]]; then
    include_bodies=1
    shift
  else
    echo "usage: pr_threads.sh <pr-number> [--include-bodies]" >&2
    exit 1
  fi
fi

if [[ $# -gt 0 ]]; then
  echo "usage: pr_threads.sh <pr-number> [--include-bodies]" >&2
  exit 1
fi

# Only request untrusted comment bodies when explicitly asked for
# (--include-bodies); the default query does not fetch them at all.
if [[ "$include_bodies" == "1" ]]; then
  query='query($pr:Int!){ repository(owner:"RonaldHensbergen", name:"composable-data-stack"){ pullRequest(number:$pr){ number title reviewThreads(first:100){ nodes { id isResolved path line comments(first:3){ nodes { body author{login} } } } } } } }'
else
  query='query($pr:Int!){ repository(owner:"RonaldHensbergen", name:"composable-data-stack"){ pullRequest(number:$pr){ number title reviewThreads(first:100){ nodes { id isResolved path line } } } } }'
fi

# The output formatter lives in pr_threads_fmt.py next to this script: a real
# Python file instead of an inline `python3 -c "..."` string, so there is no
# shell-quoting boundary left to break (the inline version broke twice).
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
gh api graphql -f query="$query" -F pr="$pr" \
| INCLUDE_BODIES="$include_bodies" python3 "$script_dir/pr_threads_fmt.py"
