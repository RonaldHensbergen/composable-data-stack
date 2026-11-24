"""Output formatter for tools/pr_threads.sh (issue #1338, PR #1352).

Reads the GraphQL response of `gh api graphql` on stdin and prints a compact
review-thread list. Every field that comes from GitHub (PR title, thread id,
diff path, line number, author login) is untrusted: attacker-controlled
tokens are rendered fail-closed through strict_token()'s per-field
allowlist, and free-form text through sanitize() (pattern-based
neutralization plus secret redaction), so role markers, code fences and
other instruction-like payloads cannot survive as free-form output and reach
the reader's context.

Kept as a real Python module (instead of an inline `python3 -c "..."` string)
so there is no shell-quoting boundary to break — the earlier inline version
was broken twice by exactly that quoting class.
"""

import json
import os
import re
import sys

INJECTION_PATTERNS = [
    # Word separators may be spaces, hyphens or underscores: a crafted diff
    # path like "ignore-all-previous-instructions.md" must be caught too.
    r'(?i)(?<![A-Za-z0-9])ignore[\s_-]+(?:all[\s_-]+)?(?:previous|prior|above)[\s_-]+instructions?(?![A-Za-z0-9])',
    r'(?i)(?<![A-Za-z0-9])system[\s_-]+prompt(?![A-Za-z0-9])',
    r'(?i)(?<![A-Za-z0-9])developer[\s_-]+message(?![A-Za-z0-9])',
    r'(?i)(?<![A-Za-z0-9])you[\s_-]+are[\s_-]+chatgpt(?![A-Za-z0-9])',
    r'(?i)(?<![A-Za-z0-9])tool[\s_-]*call(?![A-Za-z0-9])',
    r'(?i)(?<![A-Za-z0-9])function[\s_-]*call(?![A-Za-z0-9])',
    r'(?i)(?<![A-Za-z0-9])execute[\s_-]+(?:a[\s_-]+)?command(?![A-Za-z0-9])',
    r'(?i)(?<![A-Za-z0-9])do[\s_-]+not[\s_-]+follow[\s_-]+the[\s_-]+above(?![A-Za-z0-9])',
    r'(?i)(?<![A-Za-z0-9])instead,[\s_-]*you[\s_-]+must(?![A-Za-z0-9])',
    # Role markers like system:/assistant:/user: (separator-tolerant).
    r'(?i)(?<![A-Za-z0-9])(system|assistant|user|developer|tool)[\s_-]*:',
]

SECRET_PATTERNS = [
    r'gh[pousr]_[A-Za-z0-9_]{20,}',                                   # GitHub tokens
    r'AKIA[0-9A-Z]{16}',                                              # AWS access key id
    r'xox[baprs]-[A-Za-z0-9-]{10,}',                                  # Slack tokens
    r'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}',  # JWT
    r'-----BEGIN [A-Z ]*PRIVATE KEY-----',                            # PEM private key marker
    r'(?i)\b(bearer)\s+[A-Za-z0-9._-]{12,}\b',                        # Bearer tokens
    r'(?i)\b(password|passwd|pwd|secret|api[_-]?key|token)\b\s*[:=]\s*\S+',  # key=value secrets
    r'\b[A-Za-z0-9+/=_-]{32,}\b',                                     # generic long token-like strings
]

# Per-field allowlists for attacker-controlled tokens: everything outside the
# set is replaced with '_' by strict_token().
LOGIN_CHARS = 'A-Za-z0-9-'
NODE_ID_CHARS = 'A-Za-z0-9_+/='
PATH_CHARS = r'A-Za-z0-9._\-/'
LINE_CHARS = '0-9'


def neutralize_prompt_injection(text):
    for pattern in INJECTION_PATTERNS:
        text = re.sub(pattern, '[REDACTED-INSTRUCTION]', text)
    return text


def redact_secrets(text):
    for pattern in SECRET_PATTERNS:
        text = re.sub(pattern, '[REDACTED-SECRET]', text)
    return text


def sanitize(text, limit=120):
    text = re.sub(r'[\x00-\x1f\x7f]', ' ', text)
    text = ' '.join(text.split())
    text = neutralize_prompt_injection(text)
    text = redact_secrets(text)
    return text[:limit]


def strict_token(text, allowed):
    """Fail-closed rendering of an attacker-controlled token.

    Keep only allowlisted characters and replace everything else with '_',
    so role markers (system:), code fences, separators and other
    instruction-like payloads cannot survive as free-form text. Empty input
    gets a deterministic placeholder.
    """
    s = '' if text is None else str(text)
    out = re.sub('[^' + allowed + ']', '_', s)
    return out or '?'


def main() -> int:
    include_bodies = os.environ.get('INCLUDE_BODIES') == '1'
    d = json.load(sys.stdin)['data']['repository']['pullRequest']
    print(f"PR #{d['number']}: {sanitize(d.get('title') or '')}")
    ts = d['reviewThreads']['nodes']
    print(f'{len(ts)} threads')
    for t in ts:
        cs = (t.get('comments') or {}).get('nodes') or []
        who_raw = cs[0]['author']['login'] if cs and cs[0].get('author') else ''
        # Every field that comes from GitHub (thread id, diff path, line
        # number, author login) is untrusted: render each through
        # strict_token()'s fail-closed allowlist before it is formatted into
        # the output line (#1338, residual of the #1254 advanced-security
        # review; hardened after the PR #1352 review).
        who = strict_token(who_raw, LOGIN_CHARS)
        thread_id = strict_token(t.get('id'), NODE_ID_CHARS)
        thread_path = strict_token(t.get('path'), PATH_CHARS)
        thread_line = strict_token(t.get('line'), LINE_CHARS)
        base = f"- {'RESOLVED' if t['isResolved'] else 'OPEN    '} {thread_id} {thread_path}:{thread_line} @{who}"
        if include_bodies:
            raw_len = len(cs[0].get('body') or '') if cs else 0
            print(f"{sanitize(base, 300)}: [UNTRUSTED_COMMENT_REDACTED] body_present={'yes' if raw_len > 0 else 'no'} body_len={raw_len}")
        else:
            print(sanitize(base, 300))
    return 0


if __name__ == '__main__':
    sys.exit(main())
