# SonarCloud Quality Gate: PR Checks vs. `main`

This documents a scope discrepancy discovered while fixing a string of
"Security Rating on New Code" gate failures (#700, #701, #702, #703):
a PR's SonarCloud check can pass with 0 open issues, and the same issue
can still fail `main`'s quality gate right after merging.

## Root cause

SonarCloud runs two different analyses against this repository, scoped
differently:

- **PR analysis** (the `sonarcloud` / "SonarCloud Code Analysis" check on
  a pull request) only evaluates issues on lines the PR's diff actually
  touches.
- **`main`'s quality gate** evaluates the project's **New Code Period**
  instead, which is a SonarCloud project setting (see
  SonarCloud → Project Settings → New Code) and can span more history/
  more lines than any single PR's diff.

A taint-analysis finding whose **source** line wasn't part of a given
PR's diff, but still falls inside the New Code Period, can therefore
pass that PR's check yet still fail on `main` once merged. This is why
fixing "the one flagged line" sometimes isn't enough: the true
source→sink path can start further back in the same function, in a
caller, or in a different file entirely.

## What to do when fixing a quality-gate finding

1. **Trace the whole source→sink pattern, not just the flagged line.**
   For a taint issue (e.g. CWE-88 command injection, CWE-22 path
   traversal), find every place an untrusted value (CLI args, profile
   YAML, environment variables) reaches the same kind of sink
   (`subprocess`, filesystem paths, etc.) across the codebase in one
   pass, instead of only the specific line SonarCloud reported. This
   reduces the number of merge → fail → fix round trips described
   above.
2. **Don't rely solely on the PR-scoped check as proof `main` will stay
   green.** If you have (or can request) a SonarCloud token, you can
   query the full-project issue list before merging:

   ```bash
   curl -s -u "$SONAR_TOKEN:" \
     "https://sonarcloud.io/api/issues/search?componentKeys=RonaldHensbergen_composable-data-stack&resolved=false&types=VULNERABILITY"
   ```

   This surfaces issues in the New Code Period that a single PR's diff
   might not include, before they show up as a post-merge surprise.
3. **Requesting a token**: this repository does not currently provision
   a `SONAR_TOKEN` for contributors or automation beyond the CI
   workflow's own secret. If you need one (read-only scope is enough for
   the query above), open an issue or ask the maintainer directly; do
   not request elevated/write scopes for anything beyond CI's existing
   scan step.
4. **New Code Period**: adjusting this setting (e.g. to "Previous
   version" or a short fixed number of days, so old/untouched lines stop
   resurfacing as "new" after unrelated merges) requires SonarCloud
   project-admin access and is a maintainer decision, not something a
   contributor's PR can change. If repeated merge→fail cycles keep
   happening, raise it as a discussion point with the maintainer rather
   than assuming a single PR fix will make `main` stay green.
