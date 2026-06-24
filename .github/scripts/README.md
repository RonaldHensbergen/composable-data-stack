# PR Creation Scripts

This directory contains scripts for creating well-documented pull requests.

## Scripts

### `local-create-pr.sh`

Create a documented PR from your local machine.

**Usage:**

```bash
# Basic usage (creates draft PR from current branch to main)
./.github/scripts/local-create-pr.sh --title "feat: add new feature"

# Specify base and head branches
./.github/scripts/local-create-pr.sh \
  --title "fix: resolve bug" \
  --base develop \
  --head feature-branch

# Create as ready PR (not draft)
./.github/scripts/local-create-pr.sh \
  --title "docs: update README" \
  --no-draft
```

**Options:**
- `--title TITLE` - PR title (required)
- `--base BRANCH` - Base branch (default: main)
- `--head BRANCH` - Head branch (default: current branch)
- `--no-draft` - Create as ready PR instead of draft
- `--help` - Show help message

**Requirements:**
- GitHub CLI (`gh`) installed and authenticated
- Git repository with commits to create PR from

### `create-pr-with-docs.sh`

Used by GitHub Actions workflow. Creates PR with pre-generated documentation.

**Usage:**

```bash
./.github/scripts/create-pr-with-docs.sh \
  "PR Title" \
  "head-branch" \
  "base-branch" \
  "true"
```

**Arguments:**
1. PR title
2. Head branch
3. Base branch
4. Draft status (true/false)

## GitHub Actions Workflow

### `create-documented-pr.yml`

Automated workflow to create PRs with full documentation.

**Trigger:**

1. Go to GitHub Actions tab
2. Select "Create Documented PR" workflow
3. Click "Run workflow"
4. Fill in the form:
   - Branch name
   - PR title
   - Base branch (optional, default: main)
   - Draft status (optional, default: true)

**What it does:**

1. Checks out the specified branch
2. Generates PR documentation including:
   - Commit history
   - Changed files
   - Testing checklist
3. Creates the PR using `gh` CLI
4. Saves documentation to `.github/pr-docs/PR_BODY.md`

## PR Documentation Template

See `.github/PR_TEMPLATE_FULL.md` for the complete template structure.

## Examples

### Example 1: Quick Draft PR

```bash
# From your feature branch
git checkout feature-branch
./.github/scripts/local-create-pr.sh --title "feat: add user authentication"
```

### Example 2: Ready PR with Custom Base

```bash
./.github/scripts/local-create-pr.sh \
  --title "fix: resolve memory leak" \
  --base develop \
  --no-draft
```

### Example 3: Using GitHub Actions

1. Push your branch: `git push origin feature-branch`
2. Go to Actions → Create Documented PR
3. Run workflow with:
   - Branch: `feature-branch`
   - Title: `feat: add user authentication`
   - Base: `main`
   - Draft: `true`

## Troubleshooting

### "gh: command not found"

Install GitHub CLI:
```bash
# macOS
brew install gh

# Linux
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null
sudo apt update
sudo apt install gh
```

Then authenticate:
```bash
gh auth login
```

### "Not in a git repository"

Make sure you're in the root of your git repository:
```bash
cd /path/to/your/repo
```

### Permission denied

Make scripts executable:
```bash
chmod +x .github/scripts/*.sh
```

## Best Practices

1. **Always review generated documentation** before finalizing the PR
2. **Add context** - The auto-generated body is a starting point; add details about why changes were made
3. **Link issues** - Reference related issues in the PR body
4. **Use draft PRs** for work in progress
5. **Request reviews** after creating the PR
6. **Keep commits clean** - Squash or rebase before creating PR if needed
