# CI on Codeberg (migration from GitHub Actions)

The repository migrated from GitHub to Codeberg
(<https://codeberg.org/composable-data-stack/composable-data-stack>). The
GitHub Actions under `.github/workflows/` were migrated to the two CI systems
Codeberg offers:

| GitHub workflow | New location | System | Why |
| --- | --- | --- | --- |
| `ci.yml` | `.woodpecker/ci.yml` (+ `ci-runtime.yml`) | Woodpecker | needs service containers (postgres/mysql) and >10 min; linux/amd64 only, so the macos/windows test legs were dropped |
| `docker-smoke-test.yml` | `.woodpecker/docker-smoke-test.yml` | Woodpecker, `docker=true` agent | needs a Docker daemon |
| `k8s-e2e.yml` | `.woodpecker/k8s-e2e.yml` | Woodpecker, `docker=true` agent | k3d needs a Docker daemon |
| `mvp-proof.yml` | `.woodpecker/mvp-proof.yml` + `mvp-proof-runtime.yml` | Woodpecker (runtime half on `docker=true` agent) | runtime proof needs a daemon |
| `build-python-package.yml` | inlined into `.woodpecker/release.yml`, `pypi.yml`, `testpypi.yml`; docker smoke → `.woodpecker/package-smoke.yml` | Woodpecker | Woodpecker has no reusable workflows / cross-pipeline artifacts |
| `publish-images.yml` | `.woodpecker/publish-images.yml` + `publish-images-update-fixture.yml` | Woodpecker | daemonless buildah + trivy gate + cosign |
| `image-security-scan.yml` | `.woodpecker/image-security-scan.yml` | Woodpecker | trivy (daemonless); cron scans published digests |
| `trivyignore-audit.yml` | `.woodpecker/trivyignore-audit.yml` | Woodpecker | trivy remote scans of 14 fixture images |
| `mutation-tests.yml` | `.woodpecker/mutation-tests.yml` | Woodpecker | weekly full matrix exceeds hosted Forgejo runner caps |
| `release.yml` / `pypi.yml` / `testpypi.yml` | `.woodpecker/release.yml` / `pypi.yml` / `testpypi.yml` | Woodpecker | tag-triggered; Forgejo release via REST, PyPI via token |
| `auto-merge.yml` | `.forgejo/workflows/auto-merge.yml` | Forgejo Actions, `codeberg-tiny` | `workflow_run` unsupported → `pull_request` + 15-min cron sweep; merge only when head status is green |
| `new-contributor-check.yml` | `.forgejo/workflows/new-contributor-check.yml` | Forgejo Actions, `codeberg-tiny` | `pull_request_target` supported; `author_association` derived via API |
| `branch-hygiene.yml` | `.forgejo/workflows/branch-hygiene.yml` | Forgejo Actions, `codeberg-tiny` | schedule + fj/REST helpers |
| `contract-coverage.yml` | `.forgejo/workflows/contract-coverage.yml` | Forgejo Actions, `codeberg-tiny` | pure python + fj/REST helpers |
| `roadmap-freshness.yml` | `.forgejo/workflows/roadmap-freshness.yml` | Forgejo Actions, `codeberg-small` | many API calls; script reads the Forgejo REST API when `FORGEJO_API_URL` is set |
| `issue-project-order.yml` | `.forgejo/workflows/issue-project-order.yml` | Forgejo Actions, `codeberg-tiny` | Projects v2 → label-backed (see below) |
| `release-tag-reminder.yml` | `.forgejo/workflows/release-tag-reminder.yml` | Forgejo Actions, `codeberg-medium` | release-event poll capped at 8 min to fit the 10-min runner cap |
| `version-bump.yml` | `.forgejo/workflows/version-bump.yml` | Forgejo Actions, `codeberg-small` | git-auto-semver → inline semver calc; create-pull-request → git push + `fj pr create` |
| `biweekly-issue-audit.yml` | `.forgejo/paused-workflows/` | **paused** | uses `actions/ai-inference` (GitHub Models) — no Codeberg equivalent yet |
| `practices-audit.yml` | `.forgejo/paused-workflows/` | **paused** | same reason |

`.github/workflows/` is **kept for now**: Forgejo only reads
`.forgejo/workflows/` and Woodpecker only reads `.woodpecker/*.yml`, so the
old GitHub files are inert on Codeberg, but several tests and scripts still
reference them (follow-up item 7 below). Remove `.github/workflows/` once those
are ported.

## CLI access: `fj` (forgejo-cli) + REST helpers

Forgejo has no `gh` CLI and no GraphQL API, so CI uses the native
[**fj**](https://codeberg.org/forgejo-contrib/forgejo-cli) CLI
(`forgejo-cli` releases, pinned `v0.7.0`) plus a small REST helper library:

- **Forgejo Actions**: the composite action `.forgejo/actions/setup-fj`
  installs the pinned fj binary, writes its `keys.json` into the workspace
  (`.ci-fj/`, gitignored; picked up via `XDG_DATA_HOME`) with the automatic
  `${{ github.token }}`, and exports `FORGEJO_API_URL` / `FORGEJO_REPOSITORY` /
  `FORGEJO_TOKEN` / `FJ_FALLBACK_HOST` for later steps.
- **Woodpecker**: one `setup-fj` step per pipeline runs
  `bash .forgejo/lib/setup-fj.sh`, which does the same with the
  `codeberg_token` secret.
- **REST helpers**: `.forgejo/lib/api.sh` (source it in a step) provides
  `fj_get/fj_post/fj_patch`, `ensure_label`, `find_open_issue`,
  `search_open_issues`, `create_issue`, `head_status`, `open_labeled_prs`,
  `merge_pr_if_green`, `release_is_draft`, and the label-backed project
  helpers below.

Division of labor: `fj` covers the human-shaped commands —
`fj issue create|edit|comment|close|search|view`,
`fj pr create|edit|comment|merge|search|view`,
`fj release create|edit|view|asset create` — invoked with `-R origin`
(issue/pr) or `-r "$FORGEJO_REPOSITORY"` (release). Everything `fj` cannot
do — JSON queries, label management, commit-status checks, the label-backed
project — goes through the Forgejo REST API (`$FORGEJO_API_URL`) via the
helpers in `.forgejo/lib/api.sh`, since fj has no `--json` output mode.

Python scripts (`scripts/check_agent_policy.py`,
`scripts/check_branch_hygiene.py`, `scripts/check_roadmap_freshness.py`)
shell out to `gh` on GitHub; on Codeberg they read the Forgejo REST API via
`scripts/forgejo_rest.py` when `FORGEJO_API_URL` is set (the repo is public,
so the read-only checks work anonymously without a token).

### Roadmap "project" on Codeberg

GitHub Projects v2 does not exist on Forgejo and fj has no `project`
commands. The roadmap project is backed by repository labels —
`roadmap-project` (membership), `order:<n>` (Order field), `status:<value>`
(Status field) — manipulated by the `project_items_json` / `project_add` /
`project_set_field` helpers in `.forgejo/lib/api.sh`.
`scripts/assign_project_order.py` works unmodified against the synthesized
`items.json`. **Follow-up:** `tools/next_issue.py` / `tools/issue_context.py`
still read the GitHub project via `gh` — port them to the labels (or to the
Forgejo projects API) in a follow-up.

## Runners

- **Forgejo Actions** (hosted, open alpha): labels `codeberg-tiny` (1 CPU/2 GB/2 min),
  `codeberg-small` (2/4/5 min), `codeberg-medium` (4/8/10 min), plus `-lazy`
  variants. No Docker daemon. Repo units: enable **Actions** in repo Settings → Units.
- **Woodpecker CI** at <https://ci.codeberg.org>: request access via the
  Codeberg-e.V. requests form, then enable the repo at
  <https://ci.codeberg.org/repos/add>. Pipelines needing a Docker daemon
  (`docker-smoke-test`, `k8s-e2e`, `mvp-proof-runtime`, `package-smoke`) carry
  `labels: {docker: "true"}` + a host socket `volumes:` entry — add a
  self-hosted agent with `WOODPECKER_AGENT_LABELS=docker=true` and enable the
  repo's **Trusted → Volumes** setting (see <https://docs.codeberg.org/ci/agents/>).

## Secrets / variables to configure

Forgejo repo secrets (Settings → Actions → Secrets) — actually **none** needed:
the light workflows use the automatic `${{ github.token }}`.

Woodpecker repo secrets (ci.codeberg.org → repo → Settings → Secrets):

| Secret | Used by | Notes |
| --- | --- | --- |
| `codeberg_token` | `fj` login + REST helpers in Woodpecker pipelines (issue filing, releases, PRs) + registry login for `codeberg.org` | Codeberg access token with `read:user`, `read:issue,write:issue`, `read:repository,write:repository` **plus `read:package,write:package`** (needed to push images to the Codeberg package registry) |
| ~~`sonar_token`~~ | — | intentionally **not** set: the sonarcloud step was removed from `ci.yml` (SonarCloud binds GitHub/GitLab ALMs only, and a missing secret would fail every push pipeline). Replaced by the OSS code-analysis stack in #1362 |
| `dockerhub_username` / `dockerhub_token` | `publish-images.yml` | Docker Hub **access token** (Account Settings → Security), not the account password; replaces `RONALDSOEVEREIN_LOGIN` |
| `cosign_private_key` / `cosign_password` / `cosign_public_key` | `publish-images.yml` | `cosign generate-key-pair`; replaces keyless OIDC signing |
| `pypi_api_token` / `testpypi_api_token` | `pypi.yml` / `testpypi.yml` | replaces OIDC trusted publishing |

Woodpecker cron jobs to create in repo settings (each maps to a
`when: {event: cron, cron: <name>}`):

| Cron name | Schedule (UTC) | Pipeline |
| --- | --- | --- |
| `publish-images-weekly` | `17 4 * * 1` | publish-images(+update-fixture) |
| `image-security-scan-daily` | `23 3 * * *` | image-security-scan |
| `trivyignore-audit-daily` | `47 3 * * *` | trivyignore-audit |
| `mutation-tests-weekly` | `5 6 * * 4` | mutation-tests |
| `mvp-proof-weekly` | `17 3 * * 1` | mvp-proof(+runtime) |
| `k8s-e2e-weekly` | `43 3 * * 1` | k8s-e2e |

Forgejo Actions `schedule:` crons run directly (no registration needed):
auto-merge sweep `*/15 * * * *`, branch-hygiene `0 8 * * *`,
contract-coverage `41 6 * * 3`, roadmap-freshness `23 7 * * 2`,
release-tag-reminder `43 8 * * *`, version-bump `0 9 * * 1`.

## Deliberate behavior changes / follow-ups

1. **Keyless cosign → key-based.** GitHub OIDC (`token.actions.githubusercontent.com`)
   does not exist on Codeberg. `tests/fixtures/signed-images.json` `trustRoot`
   (oidcIssuer/certificateIdentityRegexp, `keyless: true`) and the tests that
   assert it (`tests/test_validate_cli_sbom.py`, `tests/test_publish_images_workflow.py`)
   must be updated to the key-based trust root.
2. **PyPI attestations dropped** (they required OIDC); uploads use API tokens.
3. **auto-merge**: Forgejo has no auto-merge queue and no `workflow_run` event;
   the workflow now merges directly when the labeled non-fork PR's head commit
   status is `success` (checked via the commit-status API), on PR events and a
   15-minute cron sweep.
4. **new-contributor-check**: `author_association` is not in Forgejo webhook
   payloads; first-time = not a collaborator and no previously merged PR.
5. **macos/windows CI legs dropped** (Codeberg Woodpecker is linux/amd64 only).
6. **Artifacts**: Forgejo Actions keeps `actions/upload-artifact`; Woodpecker
   has none — reports/SBOMs stay in the workspace and are echoed to logs.
7. **Scripts and tests still referencing GitHub** (out of scope for the
   workflow migration, and the reason `.github/workflows/` is kept): the tests
   `tests/test_publish_images_workflow.py`, `tests/test_profile_matrix.py`,
   `tests/test_mutation_tests_workflow.py`, `tests/test_cds_workflow.py` and
   `tests/test_agent_policy.py` parse the old workflow files;
   `scripts/check_prompts.py` and `scripts/check_agent_policy.py`
   scan/protect `.github/workflows/` — repoint to `.forgejo/workflows/` and
   `.woodpecker/`; `scripts/check_agent_policy.py` reads PR labels via
   `scripts/forgejo_rest.py` when `FORGEJO_API_URL` is set (wired in
   `.woodpecker/ci.yml`); `tools/*.py|sh`
   (`next_issue.py`, `issue_context.py`, `start_pr.sh`, …) still use `gh` +
   GitHub Projects; `scripts/build_cra_release_evidence_manifest.py` and
   `scripts/render_support_notice.py` hardcode github.com URLs;
   `docs/*.md` reference workflow paths.
8. **Dependabot** (`.github/dependabot.yml`) does not run on Codeberg; the repo
   already has `renovate.json` — run Renovate on Codeberg (e.g. via a
   Woodpecker cron pipeline or a Forgejo app).
9. **Branch protection / required checks** must be reconfigured on Codeberg to
   the Woodpecker check names (ci.codeberg.org reports commit statuses).
10. The two AI-audit workflows are paused (see `.forgejo/paused-workflows/`).
11. Woodpecker 3 runs step commands with the image's `/bin/sh` (dash on the
    Debian-based images), not bash: step scripts are POSIX-sh only (no `<<<`
    herestrings, arrays, `${!var}` indirection, `/dev/tcp`, or `source`), and
    envsubst parses every string in the pipeline config, so shell
    metacharacters must not appear literally in comments. `ci.yml` also sets a
    full clone (`clone.git.settings.depth: 0`, `partial: false`) because
    Woodpecker's default depth-1 partial clone breaks
    `check_agent_policy.py`'s `git log origin/main..HEAD`.
12. The three Docker-needing pipelines (`docker-smoke-test`, `k8s-e2e`,
    `mvp-proof-runtime`) exclude `pull_request` until the self-hosted
    `docker=true` agent and the Trusted > Volumes setting exist: without the
    agent they would pend forever and keep every PR head status non-terminal.
    Re-add `pull_request` to their `when` blocks once the agent is registered.
