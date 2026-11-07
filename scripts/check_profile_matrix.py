#!/usr/bin/env python3
"""Validate and render every shipped profile on every CI run (issue #1208).

Cheap, Docker-free tier of the profile-matrix E2E: for every
``profiles/*/profile.yaml`` (and every environment overlay under
``profiles/*/environments/``) this script runs the same code paths the CLI
commands ``cds validate`` and ``cds render`` run:

1. ``validate_profile()`` -- schema + semantic validation,
2. ``build_plan()``       -- the planner pass ``cds render`` performs first,
3. ``render_compose()``   -- ``--target compose``, skipped only when a module
   is not compose-implemented (E070: the compose target is not defined for
   that profile, e.g. the Kubernetes example profiles),
4. ``render_helm()``      -- ``--target helm``, skipped only when a module
   lacks a Kubernetes implementation (E072) or the overlay enables a
   compose-only capability (E142, backup sidecar).

A target that is not defined for a profile is skipped; any other error fails
the run. Guards keep the matrix from silently shrinking: every profile/overlay
combo must define at least one target, the Kubernetes example profiles must
render to Helm, and the compose variants named in #1208 (superset, vault)
must render to compose.

Runnable locally (``make profile-matrix``) and in CI
(.github/workflows/ci.yml ``profile-matrix`` job, plus
tests/test_profile_matrix.py in the unit-test job). No Docker needed.

    python scripts/check_profile_matrix.py           # exit 1 on any failure
    python scripts/check_profile_matrix.py --quiet   # only print failures
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from cli.k8s_renderer import render_helm  # noqa: E402
from cli.planner import build_plan  # noqa: E402
from cli.renderer import render_compose  # noqa: E402
from cli.validator import validate_profile  # noqa: E402

PROFILES_ROOT = REPO_ROOT / "profiles"

# Diagnostic codes that mean "this render target is not defined for the
# profile/overlay combination" rather than a regression. In render_helm()
# E072 is emitted exactly for modules without an implementation.kubernetes
# block, and E142 exactly for config.backup.enabled (compose-only sidecar).
# In render_compose() E070 is emitted exactly for non-docker-compose module
# kinds. Any other error code fails the matrix.
COMPOSE_UNDEFINED_CODES = frozenset({"E070"})
HELM_UNDEFINED_CODES = frozenset({"E072", "E142"})

# Guards against silent shrinkage of the matrix: these (profile, environment)
# combinations must render successfully for the given target. The Kubernetes
# example profiles are the helm-tier gate's render-level counterpart (the k3d
# e2e in .github/workflows/k8s-e2e.yml is the runtime gate); the superset and
# vault variants are the compose profiles #1208 names for the smoke tier.
HELM_REQUIRED = {
    ("local-kubernetes-example", None),
    ("local-kubernetes-postgres-operator", None),
    ("production-kubernetes-example", None),
    ("production-kubernetes-example", "prod"),
}
COMPOSE_REQUIRED = {
    ("local-dagster-postgres-superset", None),
    ("local-dagster-postgres-superset-vault", None),
}

# CI-only sentinel values for the env vars the shipped profiles reference.
# Curated so overlays with password/policy rules still validate; any newly
# referenced CDS_* var gets a long generic sentinel automatically (see
# _sentinel_env) so a new profile cannot silently pass with a missing secret.
_SENTINEL_ENV = {
    "CDS_ANALYTICS_DB_NAME": "analytics",
    "CDS_ANALYTICS_DB_USER": "analytics",
    "CDS_ANALYTICS_DB_PASSWORD": "ci-matrix-analytics-password",
    "CDS_BACKUP_ENCRYPTION_KEY": "ci-matrix-backup-encryption-key",
    "CDS_CACHE_PASSWORD": "ci-matrix-cache-password",
    "CDS_DAGSTER_DB_NAME": "dagster",
    "CDS_DAGSTER_DB_USER": "dagster",
    "CDS_DAGSTER_DB_PASSWORD": "ci-matrix-dagster-password",
    "CDS_GRAFANA_ADMIN_PASSWORD": "ci-matrix-grafana-admin-password",
    "CDS_IDENTITY_DB_NAME": "identity",
    "CDS_IDENTITY_DB_USER": "identity",
    "CDS_IDENTITY_DB_PASSWORD": "ci-matrix-identity-password",
    "CDS_KEYCLOAK_ADMIN_PASSWORD": "ci-matrix-keycloak-admin-password",
    "CDS_MARQUEZ_DB_NAME": "marquez",
    "CDS_MARQUEZ_DB_USER": "marquez",
    "CDS_MARQUEZ_DB_PASSWORD": "ci-matrix-marquez-password",
    "CDS_POSTGRES_SUPERUSER_PASSWORD": "ci-matrix-postgres-superuser-password",
    "CDS_SUPERSET_ADMIN_PASSWORD": "ci-matrix-superset-admin-password",
    "CDS_SUPERSET_DB_NAME": "superset",
    "CDS_SUPERSET_DB_PASSWORD": "ci-matrix-superset-password",
    "CDS_SUPERSET_DB_USER": "superset",
    "CDS_SUPERSET_SECRET_KEY": "ci-matrix-superset-secret-key",
    "CDS_VAULT_TOKEN": "ci-matrix-vault-root-token",
}

_ENV_REF_PATTERNS = (
    re.compile(r"\$\{(CDS_[A-Z0-9_]+)\}"),
    re.compile(r"^\s*env:\s*(CDS_[A-Z0-9_]+)\s*$", re.MULTILINE),
)


def _referenced_env_names(profiles_root: Path) -> set[str]:
    """All CDS_* env var names referenced anywhere under profiles/ (yaml)."""
    names: set[str] = set()
    for path in sorted(profiles_root.rglob("*.yaml")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in _ENV_REF_PATTERNS:
            names.update(pattern.findall(text))
    return names


def _sentinel_env(profiles_root: Path) -> dict[str, str]:
    env = dict(_SENTINEL_ENV)
    for name in sorted(_referenced_env_names(profiles_root)):
        if name not in env:
            env[name] = f"ci-matrix-{name.lower().replace('_', '-')}-0123456789abcdef"
    return env


@dataclass
class ComboResult:
    profile: str
    environment: str | None
    validate_errors: list[str] = field(default_factory=list)
    plan_errors: list[str] = field(default_factory=list)
    compose: str = "not attempted"
    helm: str = "not attempted"

    @property
    def label(self) -> str:
        return (
            f"{self.profile}[{self.environment}]" if self.environment else self.profile
        )

    def failures(self) -> list[str]:
        out = [f"{self.label}: validate failed: {e}" for e in self.validate_errors]
        out += [f"{self.label}: plan failed: {e}" for e in self.plan_errors]
        if self.compose.startswith("FAILED"):
            out.append(f"{self.label}: compose render failed: {self.compose}")
        if self.helm.startswith("FAILED"):
            out.append(f"{self.label}: helm render failed: {self.helm}")
        return out


def _errors(diags) -> list[str]:
    return [f"{d.code} {d.path}: {d.message}" for d in diags if d.level == "error"]


def _classify(render_errors: list[str], undefined_codes: frozenset[str]) -> str:
    """Map render errors to 'rendered', 'undefined(<codes>)' or 'FAILED: ...'."""
    if not render_errors:
        return "rendered"
    codes = {e.split(" ", 1)[0] for e in render_errors}
    if codes <= undefined_codes:
        return f"undefined({','.join(sorted(codes))})"
    return "FAILED: " + "; ".join(render_errors[:5])


def _iter_combos(profiles_root: Path) -> list[tuple[Path, str | None]]:
    combos: list[tuple[Path, str | None]] = []
    for profile in sorted(profiles_root.glob("*/profile.yaml")):
        combos.append((profile, None))
        env_dir = profile.parent / "environments"
        if env_dir.is_dir():
            for overlay in sorted(env_dir.glob("*.yaml")):
                combos.append((profile, overlay.stem))
    return combos


def check_matrix(profiles_root: Path, workdir: Path, verbose: bool = True) -> list[str]:
    """Validate + plan + render every profile/overlay combo.

    Returns a list of human-readable failure strings (empty means the whole
    matrix is green). Renders write into ``workdir`` only.
    """
    profiles_root = profiles_root.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    env_file = workdir / ".env"
    env_file.write_text(
        "\n".join(f"{k}={v}" for k, v in sorted(_sentinel_env(profiles_root).items()))
        + "\n",
        encoding="utf-8",
    )

    results: list[ComboResult] = []
    failures: list[str] = []
    for profile, environment in _iter_combos(profiles_root):
        name = profile.parent.name
        label_env = environment
        result = ComboResult(profile=name, environment=label_env)

        validate_diags = validate_profile(str(profile), environment=environment)
        result.validate_errors = _errors(validate_diags)

        plan, plan_diags = build_plan(
            str(profile), env_file=str(env_file), environment=environment
        )
        result.plan_errors = _errors(plan_diags)
        if plan is None:
            result.plan_errors.append("build_plan returned no plan")

        if plan is not None and not result.validate_errors and not result.plan_errors:
            slug = f"{name}-{environment or 'base'}"
            compose_out = workdir / f"compose-{slug}.yml"
            _, compose_diags = render_compose(
                plan, output_path=str(compose_out), env_file=str(env_file)
            )
            compose_errors = _errors(compose_diags)
            result.compose = _classify(compose_errors, COMPOSE_UNDEFINED_CODES)
            if result.compose == "rendered":
                if not compose_out.exists():
                    result.compose = "FAILED: compose file was not written"
                else:
                    doc = yaml.safe_load(compose_out.read_text(encoding="utf-8")) or {}
                    if not doc.get("services"):
                        result.compose = "FAILED: rendered compose has no services"

            chart_dir = workdir / f"chart-{slug}"
            files, helm_diags = render_helm(
                plan, output_dir=str(chart_dir), env_file=str(env_file)
            )
            helm_errors = _errors(helm_diags)
            result.helm = _classify(helm_errors, HELM_UNDEFINED_CODES)
            if result.helm == "rendered":
                chart_yaml = chart_dir / "Chart.yaml"
                if not chart_yaml.exists() or not files:
                    result.helm = "FAILED: no chart files were written"

        results.append(result)
        failures.extend(result.failures())
        if verbose:
            print(
                f"{result.label}: validate={'ok' if not result.validate_errors else 'FAIL'} "
                f"plan={'ok' if not result.plan_errors else 'FAIL'} "
                f"compose={result.compose} helm={result.helm}"
            )

    # Guards: every combo defines at least one target, and the named
    # example profiles render for their expected target. The named-target
    # guards only apply to the shipped profiles root: they exist to catch
    # silent shrinkage of the shipped matrix, not to judge custom
    # --profiles-root runs.
    by_combo = {(r.profile, r.environment): r for r in results}
    for profile, environment in _iter_combos(profiles_root):
        r = by_combo[(profile.parent.name, environment)]
        if r.compose == "rendered" or r.helm == "rendered":
            continue
        if r.validate_errors or r.plan_errors:
            continue  # already reported above; renders were never attempted
        failures.append(
            f"{r.label}: neither compose nor helm is defined for this profile "
            f"(compose={r.compose}, helm={r.helm})"
        )
    if profiles_root == PROFILES_ROOT:
        for profile, environment in sorted(
            HELM_REQUIRED, key=lambda t: (t[0], t[1] or "")
        ):
            r = by_combo.get((profile, environment))
            if r is None:
                failures.append(f"{profile}[{environment}]: profile/overlay not found")
            elif r.helm != "rendered":
                failures.append(
                    f"{profile}[{environment or 'base'}]: expected a successful helm "
                    f"render, got helm={r.helm}"
                )
        for profile, environment in sorted(
            COMPOSE_REQUIRED, key=lambda t: (t[0], t[1] or "")
        ):
            r = by_combo.get((profile, environment))
            if r is None:
                failures.append(f"{profile}[{environment}]: profile/overlay not found")
            elif r.compose != "rendered":
                failures.append(
                    f"{profile}[{environment or 'base'}]: expected a successful compose "
                    f"render, got compose={r.compose}"
                )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--profiles-root",
        type=Path,
        default=PROFILES_ROOT,
        help="Directory containing <name>/profile.yaml shipped profiles.",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=None,
        help="Directory for rendered output and the generated .env "
        "(default: a fresh temporary directory).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print failures, not the per-combo summary.",
    )
    args = parser.parse_args()

    if args.workdir is not None:
        failures = check_matrix(
            args.profiles_root, args.workdir, verbose=not args.quiet
        )
    else:
        with tempfile.TemporaryDirectory(prefix="cds-profile-matrix-") as tmp:
            failures = check_matrix(
                args.profiles_root, Path(tmp), verbose=not args.quiet
            )

    if failures:
        print(f"\nprofile matrix: {len(failures)} failure(s):", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print("\nprofile matrix: all shipped profiles validate and render.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
