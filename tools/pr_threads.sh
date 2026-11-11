#!/usr/bin/env bash
# pr_threads.sh <pr-number> [--include-bodies] — compact list of review threads (id, state, location, optional first comment).
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

gh api graphql -f query='query($pr:Int!){ repository(owner:"RonaldHensbergen", name:"composable-data-stack"){ pullRequest(number:$pr){ number title reviewThreads(first:100){ nodes { id isResolved path line comments(first:3){ nodes { body author{login} } } } } } } }' -F pr="$pr" \
| INCLUDE_BODIES="$include_bodies" python3 -c "import json,sys,os,re
def neutralize_prompt_injection(text):
    patterns = [
        r'(?i)\bignore\s+(all\s+)?(previous|prior|above)\s+instructions?\b',
        r'(?i)\bsystem\s+prompt\b',
        r'(?i)\bdeveloper\s+message\b',
        r'(?i)\byou\s+are\s+chatgpt\b',
        r'(?i)\btool\s*call\b',
        r'(?i)\bfunction\s*call\b',
        r'(?i)\bexecute\s+(a\s+)?command\b',
        r'(?i)\bdo\s+not\s+follow\s+the\s+above\b',
        r'(?i)\binstead,\s*you\s+must\b'
    ]
    for p in patterns:
        text = re.sub(p, '[REDACTED-INSTRUCTION]', text)
    return text

def redact_secrets(text):
    patterns = [
        r'gh[pousr]_[A-Za-z0-9_]{20,}',                                  # GitHub tokens
        r'AKIA[0-9A-Z]{16}',                                              # AWS access key id
        r'xox[baprs]-[A-Za-z0-9-]{10,}',                                  # Slack tokens
        r'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}', # JWT
        r'-----BEGIN [A-Z ]*PRIVATE KEY-----',                            # PEM private key marker
        r'(?i)\b(bearer)\s+[A-Za-z0-9._-]{12,}\b',                        # Bearer tokens
        r'(?i)\b(password|passwd|pwd|secret|api[_-]?key|token)\b\s*[:=]\s*\S+',  # key=value secrets
        r'\b[A-Za-z0-9+/=_-]{32,}\b'                                      # generic long token-like strings
    ]
    for p in patterns:
        text = re.sub(p, '[REDACTED-SECRET]', text)
    return text

def sanitize(text):
    text=re.sub(r'[\x00-\x1f\x7f]', ' ', text)
    text=' '.join(text.split())
    text=neutralize_prompt_injection(text)
    text=redact_secrets(text)
    return text[:120]

include_bodies=os.environ.get('INCLUDE_BODIES') == '1'
d=json.load(sys.stdin)['data']['repository']['pullRequest']
print(f\"PR #{d['number']}: {sanitize(d.get('title') or '')}\")
ts=d['reviewThreads']['nodes']
print(f'{len(ts)} threads')
for t in ts:
    cs=t['comments']['nodes']
    who=cs[0]['author']['login'] if cs and cs[0].get('author') else '?'
    base=f\"- {'RESOLVED' if t['isResolved'] else 'OPEN    '} {t['id']} {t.get('path')}:{t.get('line')} @{who}\"
    if include_bodies:
        raw_len=len(cs[0].get('body') or '') if cs else 0
        print(f\"{base}: [UNTRUSTED_COMMENT_REDACTED] body_present={'yes' if raw_len > 0 else 'no'} body_len={raw_len}\")
    else:
        print(base)"
