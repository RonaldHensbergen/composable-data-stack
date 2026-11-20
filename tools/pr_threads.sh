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

# Only request untrusted comment bodies when explicitly asked for
# (--include-bodies); the default query does not fetch them at all.
if [[ "$include_bodies" == "1" ]]; then
  query='query($pr:Int!){ repository(owner:"RonaldHensbergen", name:"composable-data-stack"){ pullRequest(number:$pr){ number title reviewThreads(first:100){ nodes { id isResolved path line comments(first:3){ nodes { body author{login} } } } } } } }'
else
  query='query($pr:Int!){ repository(owner:"RonaldHensbergen", name:"composable-data-stack"){ pullRequest(number:$pr){ number title reviewThreads(first:100){ nodes { id isResolved path line } } } } }'
fi

gh api graphql -f query="$query" -F pr="$pr" \
| INCLUDE_BODIES="$include_bodies" python3 -c "import json,sys,os,re
def neutralize_prompt_injection(text):
    # Word separators may be spaces, hyphens or underscores: a crafted diff
    # path like "ignore-all-previous-instructions.md" must be caught too.
    patterns = [
        r'(?i)(?<![A-Za-z0-9])ignore[\s_-]+(?:all[\s_-]+)?(?:previous|prior|above)[\s_-]+instructions?(?![A-Za-z0-9])',
        r'(?i)(?<![A-Za-z0-9])system[\s_-]+prompt(?![A-Za-z0-9])',
        r'(?i)(?<![A-Za-z0-9])developer[\s_-]+message(?![A-Za-z0-9])',
        r'(?i)(?<![A-Za-z0-9])you[\s_-]+are[\s_-]+chatgpt(?![A-Za-z0-9])',
        r'(?i)(?<![A-Za-z0-9])tool[\s_-]*call(?![A-Za-z0-9])',
        r'(?i)(?<![A-Za-z0-9])function[\s_-]*call(?![A-Za-z0-9])',
        r'(?i)(?<![A-Za-z0-9])execute[\s_-]+(?:a[\s_-]+)?command(?![A-Za-z0-9])',
        r'(?i)(?<![A-Za-z0-9])do[\s_-]+not[\s_-]+follow[\s_-]+the[\s_-]+above(?![A-Za-z0-9])',
        r'(?i)(?<![A-Za-z0-9])instead,[\s_-]*you[\s_-]+must(?![A-Za-z0-9])'
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

def sanitize(text, limit=120):
    text=re.sub(r'[\x00-\x1f\x7f]', ' ', text)
    text=' '.join(text.split())
    text=neutralize_prompt_injection(text)
    text=redact_secrets(text)
    return text[:limit]

include_bodies=os.environ.get('INCLUDE_BODIES') == '1'
d=json.load(sys.stdin)['data']['repository']['pullRequest']
print(f\"PR #{d['number']}: {sanitize(d.get('title') or '')}\")
ts=d['reviewThreads']['nodes']
print(f'{len(ts)} threads')
for t in ts:
    cs=(t.get('comments') or {}).get('nodes') or []
    who=cs[0]['author']['login'] if cs and cs[0].get('author') else '?'
    # Every field that comes from GitHub (thread id, diff path, author login)
    # is untrusted: route the whole line through sanitize() so a crafted path
    # or login cannot carry instruction-like text into the reader's context
    # (#1338, residual of the #1254 advanced-security review).
    base=f\"- {'RESOLVED' if t['isResolved'] else 'OPEN    '} {t['id']} {t.get('path')}:{t.get('line')} @{who}\"
    if include_bodies:
        raw_len=len(cs[0].get('body') or '') if cs else 0
        print(f\"{sanitize(base, 300)}: [UNTRUSTED_COMMENT_REDACTED] body_present={'yes' if raw_len > 0 else 'no'} body_len={raw_len}\")
    else:
        print(sanitize(base, 300))"
