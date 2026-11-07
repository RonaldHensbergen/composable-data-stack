"""
Helm-target coverage for Superset's `identityProvider` wiring (issue #1075).

PR #1064 implemented the wiring for both render targets, but the only
`oidc-provider` provider in the repo (modules/identity/keycloak) is
compose-only, so no profile could exercise OIDC on `--target helm` end to
end. These tests close that gap with a minimal kubernetes-capable fixture
provider (tests/fixtures/helm-oidc/modules/identity/oidc-stub) and assert
the helm contract of the wiring:

- the issuer URL reaches the superset container as a literal env value,
- the client secret appears ONLY as a `$(CDS_*)` expansion backed by a
  `secretKeyRef` entry, and as a `required` key in templates/secret.yaml,
- the E078/E079 secret-plumbing checks stay green and no resolved secret
  value reaches any chart file.

The rendered chart is additionally pinned by golden hashes
(tests/golden/k8s-chart-oidc.sha256), mirroring the k8s-chart.sha256 golden
for profiles/local-dagster-postgres-superset.
"""

import hashlib
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from cli.k8s_renderer import render_helm
from cli.planner import build_plan
from cli.validator import validate_profile

_REPO_ROOT = Path(__file__).resolve().parent.parent

# The fixture lives in the repo so it resolves through CDS_MODULE_PATH like
# every real module source below.
_OIDC_STUB_SOURCE = "tests/fixtures/helm-oidc/modules/identity/oidc-stub"

_ISSUER_URL = "https://identity.example.test/realms/analytics"

_SECRET_VALUES = {
    "CDS_ANALYTICS_DB_PASSWORD": "sentinel-analytics-password",
    "CDS_POSTGRES_SUPERUSER_PASSWORD": "sentinel-postgres-password",
    "CDS_SUPERSET_DB_PASSWORD": "sentinel-superset-password",
    "CDS_SUPERSET_SECRET_KEY": "sentinel-superset-secret-key",
    "CDS_SUPERSET_ADMIN_PASSWORD": "sentinel-admin-password",
    "CDS_SUPERSET_OIDC_CREDENTIAL": "sentinel-oidc-client-secret",
}

_SECRET_ALIASES = {
    "analytics_db_password": "CDS_ANALYTICS_DB_PASSWORD",
    "postgres_superuser_password": "CDS_POSTGRES_SUPERUSER_PASSWORD",
    "superset_db_password": "CDS_SUPERSET_DB_PASSWORD",
    "superset_secret_key": "CDS_SUPERSET_SECRET_KEY",
    "superset_admin_password": "CDS_SUPERSET_ADMIN_PASSWORD",
    "superset_oidc_credential": "CDS_SUPERSET_OIDC_CREDENTIAL",
}


class SupersetOidcHelmTest(unittest.TestCase):
    def _write_profile(self, profile_dir: Path, *, oidc: bool) -> Path:
        superset_config: dict = {
            "webPort": 8088,
            "secretKeyFrom": "secrets.superset_secret_key",
            "adminUser": {
                "username": "admin",
                "passwordFrom": "secrets.superset_admin_password",
                "email": "admin@example.local",
            },
            "metadataDatabase": {
                "contractRef": "postgres.superset-database",
            },
            "analyticsDatabase": {
                "contractRef": "postgres.sql-database",
            },
            "cacheService": {
                "contractRef": "keydb.cache-service",
            },
        }
        if oidc:
            superset_config["identityProvider"] = {
                "contractRef": "oidc.oidc-provider",
                "clientId": "superset",
                "clientSecretFrom": "secrets.superset_oidc_credential",
            }

        modules = [
            {
                "id": "postgres",
                "source": "modules/warehouse/postgres",
                "version": "0.1.0",
                "enabled": True,
                "config": {
                    "database": "analytics",
                    "username": "analytics",
                    "passwordFrom": "secrets.analytics_db_password",
                    "superuserPasswordFrom": "secrets.postgres_superuser_password",
                    "port": 5432,
                    "supersetDatabase": {
                        "name": "superset",
                        "username": "superset",
                        "passwordFrom": "secrets.superset_db_password",
                    },
                },
            },
            {
                "id": "keydb",
                "source": "modules/cache/keydb",
                "version": "0.1.0",
                "enabled": True,
                "config": {"port": 6379},
            },
            {
                "id": "oidc",
                "source": _OIDC_STUB_SOURCE,
                "version": "0.1.0",
                "enabled": True,
                "config": {
                    "issuerUrl": _ISSUER_URL,
                    "realm": "analytics",
                },
            },
            {
                "id": "superset",
                "source": "modules/bi/superset",
                "version": "0.1.0",
                "enabled": True,
                "dependsOn": ["postgres", "keydb", "oidc"],
                "config": superset_config,
            },
        ]
        profile = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Profile",
            "metadata": {"name": "superset-oidc-helm", "environment": "local"},
            "spec": {
                "runtime": {"type": "docker-compose"},
                "modules": modules,
                "secrets": {
                    "provider": {"type": "env"},
                    "values": {
                        name: {"env": env, "required": True}
                        for name, env in _SECRET_ALIASES.items()
                    },
                },
            },
        }
        profile_file = profile_dir / "profile.yaml"
        profile_file.write_text(yaml.safe_dump(profile), encoding="utf-8")
        # A project-root marker (see cli/security_common.find_project_root_dir):
        # without it the helm renderer fails closed (E085) on ConfigMap bind
        # sources such as the postgres init-db script.
        (profile_dir / "pyproject.toml").write_text(
            '[project]\nname = "superset-oidc-helm-test"\n', encoding="utf-8"
        )
        # The postgres module's init-db ConfigMap binds this file relative to
        # the profile/project root on the helm target.
        (profile_dir / "init-db.sql").write_text(
            "-- oidc-helm fixture stub\n", encoding="utf-8"
        )
        return profile_file

    def _render_profile(self, profile_dir: Path, *, oidc: bool):
        profile_file = self._write_profile(profile_dir, oidc=oidc)
        env_file = profile_dir / ".env"
        env_file.write_text(
            "\n".join(f"{key}={value}" for key, value in _SECRET_VALUES.items())
            + "\n",
            encoding="utf-8",
        )
        with mock.patch.dict(
            "os.environ", {"CDS_MODULE_PATH": str(_REPO_ROOT)}, clear=False
        ):
            diagnostics = validate_profile(str(profile_file))
            self.assertEqual(
                [d for d in diagnostics if d.level == "error"],
                [],
                msg=f"unexpected validation errors: {diagnostics}",
            )
            plan, plan_diags = build_plan(str(profile_file), env_file=str(env_file))
            self.assertIsNotNone(plan)
            self.assertEqual(
                [d for d in plan_diags if d.level == "error"],
                [],
                msg=f"unexpected plan errors: {plan_diags}",
            )
            files, render_diags = render_helm(plan, env_file=str(env_file))
        errors = [d for d in render_diags if d.level == "error"]
        self.assertEqual(
            errors, [], msg=f"unexpected helm render errors: {render_diags}"
        )
        self.assertTrue(files, "helm render produced no chart files")
        return files, render_diags

    def _render(self, *, oidc: bool = True):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        return self._render_profile(Path(tempdir.name), oidc=oidc)

    def _superset_deployment(self, files: dict[str, str]) -> str:
        return files["templates/superset-superset-deployment.yaml"]

    def _env_value(self, deployment: str, name: str) -> str:
        match = re.search(
            rf"- name: {re.escape(name)}\n\s+value: (.*)", deployment
        )
        self.assertIsNotNone(match, f"env entry {name} missing from superset deployment")
        return match.group(1).strip().strip("'\"")

    def test_issuer_url_and_client_id_render_as_literal_env(self):
        files, _ = self._render()
        deployment = self._superset_deployment(files)
        self.assertEqual(self._env_value(deployment, "CDS_OIDC_ISSUER_URL"), _ISSUER_URL)
        self.assertEqual(self._env_value(deployment, "CDS_OIDC_CLIENT_ID"), "superset")

    def test_client_secret_is_only_a_secret_key_ref_expansion(self):
        files, _ = self._render()
        deployment = self._superset_deployment(files)
        self.assertEqual(
            self._env_value(deployment, "CDS_OIDC_CLIENT_SECRET"),
            "$(CDS_SUPERSET_OIDC_CREDENTIAL)",
        )
        # The referenced key is supplied by a secretKeyRef header entry, not a
        # literal value.
        header = re.search(
            r"- name: CDS_SUPERSET_OIDC_CREDENTIAL\n"
            r"\s+valueFrom:\n"
            r"\s+secretKeyRef:\n"
            r"\s+name: '\{\{ \.Release\.Name \}\}-secrets'\n"
            r"\s+key: CDS_SUPERSET_OIDC_CREDENTIAL",
            deployment,
        )
        self.assertIsNotNone(
            header, "no secretKeyRef entry for CDS_SUPERSET_OIDC_CREDENTIAL"
        )

    def test_secret_manifest_marks_oidc_credential_required(self):
        files, _ = self._render()
        secret = files["templates/secret.yaml"]
        self.assertIn("CDS_SUPERSET_OIDC_CREDENTIAL", secret)
        self.assertIn(
            'required "secrets.CDS_SUPERSET_OIDC_CREDENTIAL is required"', secret
        )

    def test_secret_plumbing_checks_stay_green_and_no_values_leak(self):
        files, render_diags = self._render()
        codes = {d.code for d in render_diags}
        self.assertNotIn("E078", codes)
        self.assertNotIn("E079", codes)
        rendered = "\n".join(files.values())
        # Compose-style references must be fully translated on the helm target.
        self.assertNotIn("${CDS_", rendered)
        for value in _SECRET_VALUES.values():
            self.assertNotIn(value, rendered)

    def test_chart_is_deterministic_and_matches_golden_hashes(self):
        first, _ = self._render()
        second, _ = self._render()
        self.assertEqual(first, second)

        golden_path = _REPO_ROOT / "tests" / "golden" / "k8s-chart-oidc.sha256"
        expected = {}
        for line in golden_path.read_text(encoding="utf-8").splitlines():
            digest, relative_path = line.split("  ", 1)
            expected[relative_path] = digest
        actual = {
            relative_path: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for relative_path, content in sorted(first.items())
        }
        self.assertEqual(actual, expected)

    def test_without_identity_provider_oidc_env_is_empty_on_helm(self):
        files, _ = self._render(oidc=False)
        deployment = self._superset_deployment(files)
        for name in (
            "CDS_OIDC_ISSUER_URL",
            "CDS_OIDC_CLIENT_ID",
            "CDS_OIDC_CLIENT_SECRET",
        ):
            self.assertEqual(self._env_value(deployment, name), "")
        secret = files["templates/secret.yaml"]
        self.assertNotIn("CDS_SUPERSET_OIDC_CREDENTIAL", secret)

    def test_compose_only_provider_still_fails_closed_on_helm_target(self):
        # The fixture exists because the only real oidc-provider (keycloak) is
        # compose-only: binding it on the helm target must keep failing closed
        # with E072 until keycloak gains a kubernetes block.
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        profile_dir = Path(tempdir.name)
        profile_file = self._write_profile(profile_dir, oidc=True)
        profile = yaml.safe_load(profile_file.read_text(encoding="utf-8"))
        modules_by_id = {m["id"]: m for m in profile["spec"]["modules"]}
        oidc_module = modules_by_id["oidc"]
        oidc_module["source"] = "modules/identity/keycloak"
        oidc_module["config"] = {
            "metadataDatabase": {"contractRef": "postgres.sql-database"},
            "adminUser": {
                "username": "admin",
                "passwordFrom": "secrets.keycloak_admin_password",
            },
            "realm": "analytics",
            "publicUrl": "https://identity.example.test",
        }
        # The superset binding stays "oidc.oidc-provider"; only the provider's
        # module behind the "oidc" instance id changed to keycloak.
        self.assertEqual(
            modules_by_id["superset"]["config"]["identityProvider"]["contractRef"],
            "oidc.oidc-provider",
        )
        profile["spec"]["secrets"]["values"]["keycloak_admin_password"] = {
            "env": "CDS_KEYCLOAK_ADMIN_PASSWORD",
            "required": True,
        }
        profile_file.write_text(yaml.safe_dump(profile), encoding="utf-8")
        env_file = profile_dir / ".env"
        env_file.write_text(
            "\n".join(f"{key}={value}" for key, value in _SECRET_VALUES.items())
            + "\nCDS_KEYCLOAK_ADMIN_PASSWORD=sentinel-keycloak-admin\n",
            encoding="utf-8",
        )
        with mock.patch.dict(
            "os.environ", {"CDS_MODULE_PATH": str(_REPO_ROOT)}, clear=False
        ):
            plan, plan_diags = build_plan(str(profile_file), env_file=str(env_file))
            self.assertIsNotNone(plan)
            self.assertEqual(
                [d for d in plan_diags if d.level == "error"], [],
                msg=f"unexpected plan errors: {plan_diags}",
            )
            files, render_diags = render_helm(plan, env_file=str(env_file))
        self.assertEqual(files, {})
        self.assertIn(
            "E072",
            {d.code for d in render_diags if d.level == "error"},
            msg=f"expected E072 for compose-only keycloak, got: {render_diags}",
        )


if __name__ == "__main__":
    unittest.main()
