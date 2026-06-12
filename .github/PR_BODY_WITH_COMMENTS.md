# PR: binded-secrets — profile secret resolution and renderer fixes

This PR implements profile-based secret resolution and aligns module templates with the planner/renderer interpolation behavior.

Below are the primary changes with inline comments and representative code snippets from the current branch.

1) `cli/secrets.py` — central secret loading and profile secret resolution

- What changed: Added `load_profile_secrets()` which loads `.env`/environment secrets and resolves `spec.secrets.values` entries into a dictionary keyed by the secret name used in profiles.

Key snippet:

```py
def load_profile_secrets(spec_secrets: dict[str, Any] | None, env_file: Path | None = None) -> tuple[dict[str, str], list[Diagnostic]]:
    # loads .env and environment (CDS_ prefixed vars)
    # then iterates spec.secrets.values and resolves each defined secret env name
    secrets, secret_diags = load_secrets_from_env(env_file)
    for secret_name, secret_def in values.items():
        env_name = secret_def.get("env")
        secret_value, err = resolve_secret(env_name, secrets, required)
        if secret_value is not None:
            secrets[secret_name] = secret_value
    return secrets, diagnostics
```

- Why: Planner expects profile-level `spec.secrets.values` definitions (mapping to env var names) and resolves them once into the plan so templates and contract interpolation can use `config.*` or `secrets.*` consistently.

2) `cli/planner.py` — integrate profile secrets and fix contract resolution

- What changed: `build_plan()` now calls `load_profile_secrets()` to populate `secrets` early. Module instance configs are normalized and secret refs are resolved with `resolve_secret_refs()`. Contracts and outputs are substituted with `substitute_values()` using `config`, `service`, and `secrets` in the context.

Representative snippet:

```py
spec = profile.get("spec", {})
secrets, secret_diags = load_profile_secrets(spec.get("secrets"), env_file)

normalized_config = apply_defaults(...)
normalized_config = resolve_secret_refs(normalized_config, secrets, f"spec.modules[{i}].config", diagnostics)
normalized_config = substitute_values(normalized_config, {"secrets": secrets})
```

- Why: Ensures that `config.*` fields that point to secrets (e.g. `passwordFrom: secrets.postgres_password`) are replaced with actual secret values from the profile `.env` or environment before contracts are resolved.

3) `cli/renderer.py` — render-time secret interpolation

- What changed: `render_compose()` loads `.env` via `load_secrets_from_env()` and `substitute_string()` supports resolving `${secrets.KEY}` expressions from the loaded environment.

Representative snippet:

```py
secrets, secret_diags = load_secrets_from_env(env_file)
compose_impl = implementation.get("compose", {})
rendered_services = render_services(module, services, secrets)
```

- Why: When rendering a `Plan` directly into docker-compose YAML, secrets defined in the environment or an `.env` file must be interpolated.

4) Module templates — use `config.*` for resolved secrets

- Files modified: `modules/warehouse/postgres/module.yaml`, `modules/bi/superset/module.yaml`
- What changed: Module templates now refer to `${config.passwordFrom}` and `${config.secretKeyFrom}` (or nested `adminUser.passwordFrom`) instead of `${secrets.*}` so that planner substitutions produce actual values.

Example from `modules/warehouse/postgres/module.yaml`:

```yaml
password: ${config.passwordFrom}
connectionUri: postgresql://${config.username}:${config.passwordFrom}@${service.host}:${config.port}/${config.database}
environment:
  POSTGRES_PASSWORD: "${config.passwordFrom}"
```

Comment: The `configSchema` continues to constrain `passwordFrom` to match the pattern `^secrets\.[a-zA-Z0-9_-]+$` (i.e. it is still declared as referencing a `secrets.<name>` key in the profile), but the module's template reads from `config.*` so the planner's resolution step can substitute the value.

5) Tests

- Added tests covering:
  - Planner regression and profile secret ref resolution (`tests/test_planner.py`)
  - Renderer secret interpolation from `.env` (`tests/test_renderer.py`)
  - Smoke tests for example profile plan/render (`tests/test_smoke_example_profile.py`, `tests/test_render_example_profile.py`)
  - Lint test preventing committed `secrets.` references in module templates (`tests/test_modules_no_committed_secrets.py`)

6) CI

- Added `.github/workflows/ci.yml` to run `python -m unittest discover -s tests -p "*.py"` on pushes and pull requests to `main`.

How to create the PR on GitHub

- The branch `binded-secrets-ready` is pushed to origin. I can create the actual GitHub pull request, but creating PRs via the API requires authentication (`GITHUB_TOKEN`) or the GitHub CLI `gh` to be installed on this machine.

- To create the PR now, run one of these locally or provide a `GITHUB_TOKEN` for me to use:

1) Using GitHub web UI (quick):

Open:
https://github.com/RonaldHensbergen/composable-data-stack/pull/new/binded-secrets-ready

2) Using GitHub CLI:

```bash
gh pr create --title "binded-secrets: profile secret resolution and renderer fixes" \
  --body-file .github/PR_BODY_WITH_COMMENTS.md --draft --base main --head binded-secrets-ready
```

3) Using the GitHub API (requires `GITHUB_TOKEN`):

```bash
BODY=$(jq -Rs . < .github/PR_BODY_WITH_COMMENTS.md)
curl -H "Authorization: token $GITHUB_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"title\":\"binded-secrets: profile secret resolution and renderer fixes\",\"head\":\"binded-secrets-ready\",\"base\":\"main\",\"body\":$BODY,\"draft\":true}" \
  https://api.github.com/repos/RonaldHensbergen/composable-data-stack/pulls
```

Commit and push this PR body file so it's available on GitHub and can be used as the PR description.
