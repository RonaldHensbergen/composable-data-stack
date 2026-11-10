#!/usr/bin/env bash
# ci_status.sh <branch|pr-number> — compact CI status in one call.
set -euo pipefail
ref=${1:?usage: ci_status.sh <branch|pr-number>}
REPO=RonaldHensbergen/composable-data-stack
if [[ "$ref" =~ ^[0-9]+$ ]]; then
  if out=$(gh pr checks "$ref" --repo "$REPO" --json name,state,bucket 2>/dev/null); then
    printf '%s' "$out" | python3 -c "import json,sys
for c in json.load(sys.stdin):
    print(f\"{c.get('bucket') or ''}: {c.get('name')} -> {c.get('state')}\")"
  else
    gh pr checks "$ref" --repo "$REPO"
  fi
else
  gh run list --repo "$REPO" --branch "$ref" --limit 15 \
    --json workflowName,status,conclusion,url \
  | python3 -c "import json,sys
for r in json.load(sys.stdin):
    print(f\"{r['workflowName']}: {r['status']}/{r.get('conclusion') or '-'}  {r['url']}\")"
fi
