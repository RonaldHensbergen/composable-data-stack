# Maintainer Merge Policy

`main` is protected: PRs need an approving review and green CI before they merge. This document covers the normal review path, and how the maintainer's own PRs merge instead.

---

## Normal Review Path

This is how every PR merges, including the maintainer's own work when a second reviewer is available:

1. Open a PR against `main` from a short-lived branch (see [CONTRIBUTING.md](../CONTRIBUTING.md#branch-and-pr-flow)).
2. [`.github/CODEOWNERS`](../.github/CODEOWNERS) automatically requests review from the relevant code owner(s).
3. Required checks (`lint`, `test`, `docker`, defined in [.github/workflows/ci.yml](../.github/workflows/ci.yml)) must pass.
4. Once approved and green, the PR merges through the standard "Merge pull request" button.

---

## Why Maintainer PRs Are Different

GitHub does not let a PR author approve their own pull request. `.github/CODEOWNERS` currently lists a single owner, @RonaldHensbergen, for the whole repository. No second code owner exists, so his own PRs can never get the required approval through the normal path.

That's a structural gap in a solo-maintainer repo with required-review branch protection, not a bug in CODEOWNERS or in GitHub.

---

## Admin Merge Override

To merge in that situation, @RonaldHensbergen uses GitHub's admin override for branch protection: the repository-admin option to merge without waiting for the review requirement to be met.

| | Normal path | Admin override |
| --- | --- | --- |
| Used for | Any PR with an available reviewer | Only the maintainer's own PRs, when no second reviewer is available |
| Review | 1 approval, requested automatically | Skipped, since self-approval isn't possible on GitHub |
| CI | Must be green | Must be green (policy, not GitHub-enforced for this path) |
| Who can trigger it | Anyone with write access, after approval | Repo admin only |

The override is used only when all of the following hold:

- The PR is authored by the repo admin/maintainer. It is never used to merge a PR from an external contributor. Those always wait for an approval.
- Required CI checks are green before the merge happens. GitHub's admin override waives every branch-protection rule at once, not just the review requirement, so nothing stops a merge with red CI except discipline.
- The PR has had a self-review pass against the same [Pull Request Checklist](../CONTRIBUTING.md#pull-request-checklist) contributors are asked to satisfy.

---

## Limitations

- This does not add a second reviewer. External contributions still wait on a real review; the maintainer's own PRs still rely on self-review discipline rather than an independent check.
- GitHub's admin override does not have a review-only mode. Using it waives every required check, not just review. Keeping CI green for these merges is enforced by policy, not by GitHub.
- If the project gains a second maintainer, this override should be retired in favor of real cross-review, or branch protection should enable "Do not allow bypassing the above settings" so the exception can't be used at all.

---

## Reporting A Concern

If a PR looks like it merged via the admin override and it wasn't the maintainer's own PR, open an issue. That would mean this policy wasn't followed.
