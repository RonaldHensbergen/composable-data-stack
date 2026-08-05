# LinkedIn Post Agent MVP

A lightweight first step toward a semi-automatic LinkedIn post workflow for `RonaldHensbergen/composable-data-stack`.

This step builds a **signal collector** that gathers:

- recent GitHub commits
- recently merged pull requests
- open issues
- your own notes
- your content themes and tone preferences

It saves everything into one file:

```text
data/cache/signals.json
```

---

## What you are building

A small Python script that:

1. reads repository activity from GitHub
2. reads local content notes
3. combines those inputs
4. writes a single JSON payload for later post generation

This is the **input layer** for the rest of the agent.

---

## Project layout

Create this structure:

```text
linkedin-agent/
├── data/
│   ├── cache/
│   └── inputs/
├── src/
└── .env
```

Add these files:

```text
data/inputs/notes.md
data/inputs/themes.json
src/collect_signals.py
```

---

## Prerequisites

- Python 3.9+
- pip
- optional: a GitHub token for higher API rate limits

Install dependencies:

```bash
pip install requests python-dotenv
```

---

## Configuration

Create a `.env` file in the project root:

```env
GITHUB_TOKEN=your_token_here
GITHUB_OWNER=RonaldHensbergen
GITHUB_REPO=composable-data-stack
```

If you do not set `GITHUB_TOKEN`, the script may still work, but GitHub rate limits will be lower.

---

## Input files

### `data/inputs/notes.md`

Use this file for raw ideas, observations, and possible post angles.

Example:

```md
- Many teams adopt multiple tools before defining interface contracts.
- Composability helps flexibility but increases integration responsibility.
- Need better explanation of how orchestration and governance fit together.
- Possible post idea: composability is not just tool choice, it is boundary design.
```

### `data/inputs/themes.json`

Use this file for content themes, tone, and constraints.

Example:

```json
{
  "themes": [
    "composable architecture",
    "interoperability",
    "data contracts",
    "orchestration",
    "governance",
    "tool tradeoffs"
  ],
  "tone": [
    "practical",
    "clear",
    "slightly opinionated",
    "non-hype"
  ],
  "avoid": [
    "buzzwords",
    "generic inspiration",
    "emoji-heavy writing"
  ]
}
```

---

## Collector script

Create `src/collect_signals.py`:

```python
import os
import json
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

OWNER = os.getenv("GITHUB_OWNER")
REPO = os.getenv("GITHUB_REPO")
TOKEN = os.getenv("GITHUB_TOKEN")

BASE_DIR = Path(__file__).resolve().parent.parent
INPUTS_DIR = BASE_DIR / "data" / "inputs"
CACHE_DIR = BASE_DIR / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {}
if TOKEN:
    HEADERS["Authorization"] = f"Bearer {TOKEN}"
HEADERS["Accept"] = "application/vnd.github+json"


def github_get(url, params=None):
    response = requests.get(url, headers=HEADERS, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def get_commits(limit=10):
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/commits"
    data = github_get(url, params={"per_page": limit})
    commits = []

    for item in data:
        commit = item.get("commit", {})
        commits.append({
            "sha": item.get("sha"),
            "message": commit.get("message"),
            "author": commit.get("author", {}).get("name"),
            "date": commit.get("author", {}).get("date"),
            "url": item.get("html_url")
        })

    return commits


def get_pull_requests(limit=10):
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/pulls"
    data = github_get(url, params={"state": "closed", "per_page": limit})
    prs = []

    for item in data:
        if item.get("merged_at"):
            prs.append({
                "number": item.get("number"),
                "title": item.get("title"),
                "author": item.get("user", {}).get("login"),
                "created_at": item.get("created_at"),
                "merged_at": item.get("merged_at"),
                "url": item.get("html_url")
            })

    return prs


def get_issues(limit=10):
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/issues"
    data = github_get(url, params={"state": "open", "per_page": limit})
    issues = []

    for item in data:
        if "pull_request" not in item:
            issues.append({
                "number": item.get("number"),
                "title": item.get("title"),
                "author": item.get("user", {}).get("login"),
                "created_at": item.get("created_at"),
                "url": item.get("html_url")
            })

    return issues


def read_notes():
    notes_file = INPUTS_DIR / "notes.md"
    if notes_file.exists():
        return notes_file.read_text(encoding="utf-8")
    return ""


def read_themes():
    themes_file = INPUTS_DIR / "themes.json"
    if themes_file.exists():
        return json.loads(themes_file.read_text(encoding="utf-8"))
    return {}


def main():
    signals = {
        "repo": f"{OWNER}/{REPO}",
        "commits": get_commits(),
        "merged_pull_requests": get_pull_requests(),
        "open_issues": get_issues(),
        "notes": read_notes(),
        "themes": read_themes()
    }

    output_file = CACHE_DIR / "signals.json"
    output_file.write_text(json.dumps(signals, indent=2), encoding="utf-8")
    print(f"Saved signals to {output_file}")


if __name__ == "__main__":
    main()
```

---

## Usage

Run from the project root:

```bash
python src/collect_signals.py
```

---

## Output

On success, the script creates:

```text
data/cache/signals.json
```

Expected contents:

| Key | Description |
|---|---|
| `repo` | repository name |
| `commits` | recent commit activity |
| `merged_pull_requests` | recently merged PRs |
| `open_issues` | open issues excluding PRs |
| `notes` | content from `notes.md` |
| `themes` | content from `themes.json` |

---

## Verification

Step 1 is complete if:

- the script runs without crashing
- `data/cache/signals.json` exists
- the JSON includes GitHub activity
- the JSON includes your notes and themes

---

## Troubleshooting

### 404 or repo not found

Check:

- `GITHUB_OWNER`
- `GITHUB_REPO`

### 401 or 403

Check:

- your token is valid
- `.env` is being loaded
- the token was copied correctly

### Empty PRs or issues

Possible causes:

- the repo has no recent matching activity
- API rate limiting
- incorrect repo settings

### Notes or themes missing

Make sure these files exist:

```text
data/inputs/notes.md
data/inputs/themes.json
```

---

## Scope

Do not add these yet:

- Docker
- local LLMs
- vector databases
- LinkedIn auto-posting
- multi-agent frameworks
- browser automation

Keep this step small and reliable.

---

## Next step

Once `signals.json` is working, build a second script that reads it and creates a **post brief** with:

- topic
- audience
- insight
- evidence
- CTA

That brief will later be used to generate LinkedIn post drafts.
