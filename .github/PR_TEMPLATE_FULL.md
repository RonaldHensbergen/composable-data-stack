# PR Template: Full Documentation

This template provides a comprehensive structure for creating well-documented pull requests.

## Title Format

`[type]: brief description`

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`, `ci`

Example: `feat: add profile secret resolution and renderer fixes`

---

## PR Body Structure

### 1. Overview

Brief summary of what this PR accomplishes and why it's needed.

**Example:**
> This PR implements profile-based secret resolution and aligns module templates with the planner/renderer interpolation behavior.

### 2. Changes

List the primary changes with inline comments and representative code snippets.

**Format:**

```
1) `file/path.py` — brief description

- What changed: Detailed explanation of the change
- Why: Rationale for the change

Key snippet:
```py
# Representative code showing the change
```
```

### 3. Files Modified

List all files changed, organized by category:

**New Files:**
- `path/to/new/file.py` - Description

**Modified Files:**
- `path/to/modified/file.py` - Description

**Deleted Files:**
- `path/to/deleted/file.py` - Description

### 4. Testing

Describe testing approach and results:

- [ ] Unit tests added/updated
- [ ] Integration tests pass
- [ ] Manual testing completed
- [ ] Edge cases covered

**Test Coverage:**
```
# Command to run tests
python -m unittest discover -s tests

# Expected output or coverage metrics
```

### 5. Breaking Changes

List any breaking changes and migration steps:

**Breaking:**
- Description of breaking change
- Migration path: How to update existing code

**Non-breaking:**
- Backward compatible changes

### 6. Documentation

- [ ] README updated
- [ ] API documentation updated
- [ ] Inline code comments added
- [ ] Examples updated

### 7. Checklist

- [ ] Code follows project style guidelines
- [ ] All tests pass
- [ ] No new warnings introduced
- [ ] Documentation is complete
- [ ] Commit messages are clear
- [ ] Branch is up to date with base

### 8. Related Issues

Closes #issue_number
Relates to #issue_number

### 9. Screenshots/Examples

If applicable, include:
- Before/after screenshots
- Example usage
- Output samples

### 10. Deployment Notes

Any special considerations for deployment:
- Environment variables needed
- Database migrations required
- Configuration changes
- Dependencies to install

---

## Example PR Body

See `.github/PR_BODY_WITH_COMMENTS.md` for a complete example following this template.

## Usage

When creating a PR manually:

```bash
# Using GitHub CLI
gh pr create --title "feat: your feature" \
  --body-file .github/PR_TEMPLATE_FULL.md \
  --draft --base main --head your-branch
```

When using the automated workflow:

```bash
# Trigger via GitHub Actions UI
# Go to Actions → Create Documented PR → Run workflow
# Fill in: branch name, title, base branch, draft status
```
