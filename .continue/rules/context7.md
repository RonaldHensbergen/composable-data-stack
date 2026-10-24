---
name: Context7 documentation lookup
description: Use when a task involves an external library, framework or CLI tool (e.g. Docker Compose, Dagster, Superset, JSON Schema, Python packages).
alwaysApply: false
---

# Context7 usage

- Use the Context7 MCP to fetch current documentation before writing or
  changing code that relies on an external library, framework or CLI tool.
- Resolve the library ID first, then request only the topic you need to keep
  context small.
- Not needed for this repository's own code (`cli/`, `modules/`, `docs/`);
  read those files directly.
- If Context7 has no match, fall back to Fetch or Tavily.
