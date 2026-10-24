---
name: GitHub MCP usage
alwaysApply: true
---

# Using the GitHub MCP server

- Default repository: owner `RonaldHensbergen`, repo `composable-data-stack`.
  Always pass these exact values; never guess other owners or repos.
- Use `list_*` tools for broad retrieval (all open PRs/issues) and `search_*`
  tools for targeted queries. Pass sort/order as separate parameters, not
  inside the query string.
- List open PRs with `list_pull_requests` (state `open`) or
  `search_pull_requests` with `repo:RonaldHensbergen/composable-data-stack is:open`.
  Report only what the tool returns; if empty, say so.
- The pinned server (v2.0.3, toolsets `repos,issues,pull_requests`) uses
  consolidated tool names; do not guess older ones like `get_issue`,
  `create_issue`, `get_pull_request` or `create_pull_request_review`:
  - Issues: `issue_read` (get/comments/labels via `method`), `issue_write`
    (create/update via `method`), `list_issues`, `search_issues`,
    `add_issue_comment`, `sub_issue_write`, `get_label`.
  - PRs: `pull_request_read` (get/diff/files/reviews/comments/check runs via
    `method`), `list_pull_requests`, `search_pull_requests`,
    `create_pull_request`, `update_pull_request`, `merge_pull_request`,
    `pull_request_review_write`, `add_comment_to_pending_review`,
    `add_reply_to_pull_request_comment`.
  - Repos: `get_file_contents`, `list_commits`, `get_commit`,
    `list_branches`, `create_branch`, `push_files`, `search_code`.
  - Not available (no `actions` toolset, no thread resolution, no Projects):
    workflow runs/logs and `resolveReviewThread` — use the `gh` CLI.
- Paginate in batches of 5-10 and use minimal output when full detail is not needed.
- Issues must be labeled (`bug`, `enhancement`, `documentation`, `question`,
  `refactor`, an `area:*` label, and `priority:*` when known). Never apply
  `good first issue`. Every new issue includes an effort estimate
  (e.g. "Estimated effort: 1-2 hours"). Avoid module names in titles/bodies
  when a provider-neutral description works.
- PRs do not get labels.
- PR reviews: use inline (line-level) comments whenever possible. After
  addressing review comments, reply on each and resolve the thread.
- "Can this PR be merged?" means a quality check (tests, lint, CI, review
  substance), not GitHub's mergeable status.
- Use new GitHub Projects (`gh project`), never classic Projects.
- Follow Conventional Commits for commit/PR titles and keep `CHANGELOG.md`
  `[Unreleased]` in sync (see AGENT.md).
- Prefer the GitHub MCP for reads/writes on GitHub; fall back to the `gh` CLI
  for Projects and GraphQL (e.g. `resolveReviewThread`).
- Never print or commit tokens. The token comes from the Continue secret
  `GITHUB_PERSONAL_ACCESS_TOKEN`.
