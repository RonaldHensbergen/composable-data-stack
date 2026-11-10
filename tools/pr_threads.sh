#!/usr/bin/env bash
# pr_threads.sh <pr-number> — compact list of review threads (id, state, location, first comment).
set -euo pipefail
pr=${1:?usage: pr_threads.sh <pr-number>}
gh api graphql -f query='query($pr:Int!){ repository(owner:"RonaldHensbergen", name:"composable-data-stack"){ pullRequest(number:$pr){ number title reviewThreads(first:100){ nodes { id isResolved path line comments(first:3){ nodes { body author{login} } } } } } } }' -F pr="$pr" \
| python3 -c "import json,sys
d=json.load(sys.stdin)['data']['repository']['pullRequest']
print(f\"PR #{d['number']}: {d['title']}\")
ts=d['reviewThreads']['nodes']
print(f'{len(ts)} threads')
for t in ts:
    cs=t['comments']['nodes']
    first=' '.join(cs[0]['body'].split())[:120] if cs else ''
    who=cs[0]['author']['login'] if cs and cs[0].get('author') else '?'
    print(f\"- {'RESOLVED' if t['isResolved'] else 'OPEN    '} {t['id']} {t.get('path')}:{t.get('line')} @{who}: {first}\")"
