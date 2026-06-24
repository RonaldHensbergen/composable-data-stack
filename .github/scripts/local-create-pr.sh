#!/bin/bash
set -e

# Local script to create a documented PR
# Usage: ./local-create-pr.sh [options]

# Default values
DRAFT="true"
BASE_BRANCH="main"
HEAD_BRANCH=$(git branch --show-current)

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --title)
            TITLE="$2"
            shift 2
            ;;
        --base)
            BASE_BRANCH="$2"
            shift 2
            ;;
        --head)
            HEAD_BRANCH="$2"
            shift 2
            ;;
        --no-draft)
            DRAFT="false"
            shift
            ;;
        --help)
            echo "Usage: $0 --title 'PR Title' [options]"
            echo ""
            echo "Options:"
            echo "  --title TITLE      PR title (required)"
            echo "  --base BRANCH      Base branch (default: main)"
            echo "  --head BRANCH      Head branch (default: current branch)"
            echo "  --no-draft         Create as ready PR instead of draft"
            echo "  --help             Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Validate required arguments
if [ -z "$TITLE" ]; then
    echo "Error: --title is required"
    echo "Use --help for usage information"
    exit 1
fi

# Check if gh CLI is available
if ! command -v gh &> /dev/null; then
    echo "Error: GitHub CLI (gh) is not installed"
    echo "Install it from: https://cli.github.com/"
    exit 1
fi

# Check if we're in a git repository
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo "Error: Not in a git repository"
    exit 1
fi

echo "Generating PR documentation..."
echo "  Title: $TITLE"
echo "  Head: $HEAD_BRANCH"
echo "  Base: $BASE_BRANCH"
echo "  Draft: $DRAFT"
echo ""

# Create documentation directory
mkdir -p .github/pr-docs

# Get commit messages since base branch
echo "Fetching commits..."
git fetch origin "$BASE_BRANCH" 2>/dev/null || true
COMMITS=$(git log --pretty=format:"- %s" "origin/$BASE_BRANCH..$HEAD_BRANCH" 2>/dev/null || echo "- No commits found")

# Get changed files
echo "Analyzing changed files..."
CHANGED_FILES=$(git diff --name-only "origin/$BASE_BRANCH..$HEAD_BRANCH" 2>/dev/null || git diff --name-only --cached)

# Count changes
NUM_FILES=$(echo "$CHANGED_FILES" | wc -l)
NUM_COMMITS=$(echo "$COMMITS" | wc -l)

# Generate PR body
cat > .github/pr-docs/PR_BODY.md << EOF
# $TITLE

## Overview

This PR includes changes from branch \`$HEAD_BRANCH\` to be merged into \`$BASE_BRANCH\`.

**Summary:**
- $NUM_COMMITS commit(s)
- $NUM_FILES file(s) changed

## Changes

### Commits

$COMMITS

### Files Changed

\`\`\`
$CHANGED_FILES
\`\`\`

## Testing

- [ ] Unit tests pass
- [ ] Integration tests pass
- [ ] Manual testing completed

**Run tests:**
\`\`\`bash
python -m unittest discover -s tests
\`\`\`

## Checklist

- [ ] Code follows project style guidelines
- [ ] Documentation updated
- [ ] Tests added/updated
- [ ] No breaking changes (or documented)
- [ ] Commit messages are clear

## Related Issues

<!-- Link related issues here -->
<!-- Closes #123 -->

## Additional Notes

<!-- Add any additional context, screenshots, or notes here -->

EOF

echo "✓ PR documentation generated at .github/pr-docs/PR_BODY.md"
echo ""

# Create the PR
echo "Creating pull request..."

GH_CMD="gh pr create --title \"$TITLE\" --body-file .github/pr-docs/PR_BODY.md --base \"$BASE_BRANCH\" --head \"$HEAD_BRANCH\""

if [ "$DRAFT" = "true" ]; then
    GH_CMD="$GH_CMD --draft"
fi

eval $GH_CMD

PR_URL=$(gh pr view "$HEAD_BRANCH" --json url -q .url 2>/dev/null || echo "")

echo ""
echo "✓ Pull request created successfully!"
if [ -n "$PR_URL" ]; then
    echo "  URL: $PR_URL"
fi
echo ""
echo "PR documentation saved at: .github/pr-docs/PR_BODY.md"
echo ""
echo "Next steps:"
echo "  1. Review the PR on GitHub"
echo "  2. Add any additional context or screenshots"
echo "  3. Request reviewers"
echo "  4. Mark as ready for review when complete"
