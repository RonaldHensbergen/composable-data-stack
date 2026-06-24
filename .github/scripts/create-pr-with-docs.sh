#!/bin/bash
set -e

# Script to create a GitHub PR with full documentation
# Usage: create-pr-with-docs.sh <title> <head-branch> <base-branch> <draft>

TITLE="$1"
HEAD_BRANCH="$2"
BASE_BRANCH="${3:-main}"
DRAFT="${4:-true}"

PR_BODY_FILE=".github/pr-docs/PR_BODY.md"

echo "Creating PR with documentation..."
echo "  Title: $TITLE"
echo "  Head: $HEAD_BRANCH"
echo "  Base: $BASE_BRANCH"
echo "  Draft: $DRAFT"

# Check if gh CLI is available
if ! command -v gh &> /dev/null; then
    echo "Error: GitHub CLI (gh) is not installed"
    echo "Install it from: https://cli.github.com/"
    exit 1
fi

# Check if PR body file exists
if [ ! -f "$PR_BODY_FILE" ]; then
    echo "Error: PR body file not found at $PR_BODY_FILE"
    exit 1
fi

# Build gh pr create command
GH_CMD="gh pr create --title \"$TITLE\" --body-file \"$PR_BODY_FILE\" --base \"$BASE_BRANCH\" --head \"$HEAD_BRANCH\""

if [ "$DRAFT" = "true" ]; then
    GH_CMD="$GH_CMD --draft"
fi

# Create the PR
echo "Executing: $GH_CMD"
eval $GH_CMD

PR_URL=$(gh pr view "$HEAD_BRANCH" --json url -q .url)
echo ""
echo "✓ Pull request created successfully!"
echo "  URL: $PR_URL"
echo ""
echo "PR documentation saved at: $PR_BODY_FILE"
