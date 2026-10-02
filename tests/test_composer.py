"""Tests for cli.composer.compose_profile() (#807, the compose-time
follow-up to #349's cds generate-profile)."""

import tempfile
import unittest
from pathlib import Path
from textwrap import dedent

from cli.composer import compose_profile


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(content), encoding="utf-8")


class ComposeProfileTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.modules_root = self.root / "modules"
        self.profile_dir = self.root / "profiles" / "demo"

        _write(
            self.modules_root / "warehouse" / "postgres" / "module.yaml",
            """
            apiVersion: cds/v1alpha1
            kind: Module
            metadata:
              name: postgres
              category: warehouse
              version: "0.1.0"
            spec:
              runtime:
                type: container
                service:
                  name: postgres
              configSchema:
                type: object
              provides:
                - name: sql-database
                  contract:
                    kind: sql-database
                - name: dagster-database
                  contract:
                    kind: sql-database
              implementation:
                kind: docker-compose
                compose:
                  services: {}
            """,
        )

        _write(
            self.modules_root / "warehouse" / "sqlite" / "module.yaml",
            """
            apiVersion: cds/v1alpha1
            kind: Module
            metadata:
              name: sqlite
              category: warehouse
              version: "0.1.0"
            spec:
              runtime:
                type: container
                service:
                  name: sqlite
              configSchema:
                type: object
              provides:
                - name: sql-database
                  contract:
                    kind: sql-database
              implementation:
                kind: docker-compose
                compose:
                  services: {}
            """,
        )

        _write(
            self.modules_root / "cache" / "keydb" / "module.yaml",
            """
            apiVersion: cds/v1alpha1
            kind: Module
            metadata:
              name: keydb
              category: cache
              version: "0.1.0"
            spec:
              runtime:
                type: container
                service:
                  name: keydb
              configSchema:
                type: object
              provides:
                - name: cache-service
                  contract:
                    kind: cache-service
              implementation:
                kind: docker-compose
                compose:
                  services: {}
            """,
        )

        _write(
            self.modules_root / "identity" / "keycloak" / "module.yaml",
            """
            apiVersion: cds/v1alpha1
            kind: Module
            metadata:
              name: keycloak
              category: identity
              version: "0.1.0"
            spec:
              runtime:
                type: container
                service:
                  name: keycloak
              configSchema:
                type: object
              consumes:
                - name: metadata-database
                  contract:
                    kind: sql-database
                  required: true
                  mappedFrom: spec.config.metadataDatabase
              provides:
                - name: http-service
                  contract:
                    kind: http-service
              implementation:
                kind: docker-compose
                compose:
                  services: {}
            """,
        )

    def _profile(self, modules=None, secrets=None):
        return {
            "apiVersion": "cds/v1alpha1",
            "kind": "Profile",
            "metadata": {"name": "demo", "environment": "local"},
            "spec": {
                "runtime": {"type": "docker-compose"},
                "modules": modules if modules is not None else [],
                "secrets": {"provider": {"type": "env"}, "values": secrets or {}},
            },
        }

    def test_auto_resolves_unambiguous_binding_and_dependency(self):
        profile = self._profile(
            modules=[
                {
                    "id": "sqlite",
                    "source": "warehouse/sqlite",
                    "enabled": True,
                    "config": {},
                }
            ]
        )

        merged, diagnostics = compose_profile(
            profile,
            self.profile_dir,
            "identity/keycloak",
            secret_env_names={"keycloak_admin_password": "CDS_KEYCLOAK_ADMIN_PASSWORD"},
            modules_root=self.modules_root,
        )

        self.assertEqual(diagnostics, [])
        self.assertIsNotNone(merged)
        new_instance = merged["spec"]["modules"][-1]
        self.assertEqual(new_instance["id"], "keycloak")
        self.assertEqual(new_instance["dependsOn"], ["sqlite"])
        self.assertEqual(
            new_instance["config"]["metadataDatabase"]["contractRef"], "sqlite.sql-database"
        )
        # Original profile must be untouched.
        self.assertEqual(profile["spec"]["modules"], [profile["spec"]["modules"][0]])

    def test_ambiguous_contract_kind_requires_explicit_bind(self):
        profile = self._profile(
            modules=[
                {
                    "id": "postgres",
                    "source": "warehouse/postgres",
                    "enabled": True,
                    "config": {},
                }
            ]
        )
        # postgres provides two sql-database contracts, so auto-resolution
        # must fail without an explicit --bind-equivalent.
        merged, diagnostics = compose_profile(
            profile,
            self.profile_dir,
            "identity/keycloak",
            modules_root=self.modules_root,
        )

        self.assertIsNone(merged)
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0].code, "E125")

    def test_explicit_bind_resolves_ambiguity(self):
        profile = self._profile(
            modules=[
                {
                    "id": "postgres",
                    "source": "warehouse/postgres",
                    "enabled": True,
                    "config": {},
                }
            ]
        )

        merged, diagnostics = compose_profile(
            profile,
            self.profile_dir,
            "identity/keycloak",
            bindings={"metadata-database": "postgres.dagster-database"},
            secret_env_names={"keycloak_admin_password": "CDS_KEYCLOAK_ADMIN_PASSWORD"},
            modules_root=self.modules_root,
        )

        self.assertEqual(diagnostics, [])
        new_instance = merged["spec"]["modules"][-1]
        self.assertEqual(
            new_instance["config"]["metadataDatabase"]["contractRef"], "postgres.dagster-database"
        )

    def test_bind_with_wrong_contract_kind_is_rejected(self):
        profile = self._profile(
            modules=[
                {
                    "id": "postgres",
                    "source": "warehouse/postgres",
                    "enabled": True,
                    "config": {},
                },
                {
                    "id": "keydb",
                    "source": "cache/keydb",
                    "enabled": True,
                    "config": {},
                },
            ]
        )

        merged, diagnostics = compose_profile(
            profile,
            self.profile_dir,
            "identity/keycloak",
            bindings={"metadata-database": "keydb.cache-service"},
            modules_root=self.modules_root,
        )

        self.assertIsNone(merged)
        self.assertEqual(diagnostics[0].code, "E124")

    def test_missing_required_binding_with_no_candidates_errors(self):
        profile = self._profile(
            modules=[
                {
                    "id": "keydb",
                    "source": "cache/keydb",
                    "enabled": True,
                    "config": {},
                }
            ]
        )

        merged, diagnostics = compose_profile(
            profile,
            self.profile_dir,
            "identity/keycloak",
            modules_root=self.modules_root,
        )

        self.assertIsNone(merged)
        self.assertEqual(diagnostics[0].code, "E125")

    def test_unresolved_secret_alias_errors(self):
        profile = self._profile(
            modules=[
                {
                    "id": "postgres",
                    "source": "warehouse/postgres",
                    "enabled": True,
                    "config": {},
                }
            ]
        )

        merged, diagnostics = compose_profile(
            profile,
            self.profile_dir,
            "identity/keycloak",
            bindings={"metadata-database": "postgres.sql-database"},
            config_settings=[("adminUser.passwordFrom", "secrets.keycloak_admin_password")],
            modules_root=self.modules_root,
        )

        self.assertIsNone(merged)
        self.assertEqual(diagnostics[0].code, "E126")

    def test_existing_secret_alias_is_reused_without_new_env_mapping(self):
        profile = self._profile(
            modules=[
                {
                    "id": "postgres",
                    "source": "warehouse/postgres",
                    "enabled": True,
                    "config": {},
                }
            ],
            secrets={"keycloak_admin_password": {"env": "CDS_EXISTING", "required": True}},
        )

        merged, diagnostics = compose_profile(
            profile,
            self.profile_dir,
            "identity/keycloak",
            bindings={"metadata-database": "postgres.sql-database"},
            config_settings=[("adminUser.passwordFrom", "secrets.keycloak_admin_password")],
            modules_root=self.modules_root,
        )

        self.assertEqual(diagnostics, [])
        self.assertEqual(
            merged["spec"]["secrets"]["values"]["keycloak_admin_password"],
            {"env": "CDS_EXISTING", "required": True},
        )

    def test_duplicate_instance_id_is_rejected(self):
        profile = self._profile(
            modules=[
                {
                    "id": "keycloak",
                    "source": "identity/keycloak",
                    "enabled": True,
                    "config": {"metadataDatabase": {"contractRef": "postgres.sql-database"}},
                }
            ]
        )

        merged, diagnostics = compose_profile(
            profile,
            self.profile_dir,
            "identity/keycloak",
            modules_root=self.modules_root,
        )

        self.assertIsNone(merged)
        self.assertEqual(diagnostics[0].code, "E123")

    def test_config_settings_merge_with_resolved_bindings(self):
        profile = self._profile(
            modules=[
                {
                    "id": "postgres",
                    "source": "warehouse/postgres",
                    "enabled": True,
                    "config": {},
                }
            ]
        )

        merged, diagnostics = compose_profile(
            profile,
            self.profile_dir,
            "identity/keycloak",
            instance_id="keycloak",
            bindings={"metadata-database": "postgres.sql-database"},
            config_settings=[("metadataDatabase.driver", "postgres"), ("httpPort", 8081)],
            modules_root=self.modules_root,
        )

        self.assertEqual(diagnostics, [])
        config = merged["spec"]["modules"][-1]["config"]
        self.assertEqual(config["httpPort"], 8081)
        self.assertEqual(config["metadataDatabase"]["driver"], "postgres")
        self.assertEqual(config["metadataDatabase"]["contractRef"], "postgres.sql-database")

    def test_explicit_depends_on_is_preserved_alongside_resolved_dependency(self):
        profile = self._profile(
            modules=[
                {
                    "id": "postgres",
                    "source": "warehouse/postgres",
                    "enabled": True,
                    "config": {},
                },
                {
                    "id": "keydb",
                    "source": "cache/keydb",
                    "enabled": True,
                    "config": {},
                },
            ]
        )

        merged, diagnostics = compose_profile(
            profile,
            self.profile_dir,
            "identity/keycloak",
            bindings={"metadata-database": "postgres.sql-database"},
            depends_on=["keydb"],
            modules_root=self.modules_root,
        )

        self.assertEqual(diagnostics, [])
        self.assertEqual(sorted(merged["spec"]["modules"][-1]["dependsOn"]), ["keydb", "postgres"])

    def test_disabled_module_is_not_considered_a_provider(self):
        profile = self._profile(
            modules=[
                {
                    "id": "postgres",
                    "source": "warehouse/postgres",
                    "enabled": False,
                    "config": {},
                }
            ]
        )

        merged, diagnostics = compose_profile(
            profile,
            self.profile_dir,
            "identity/keycloak",
            modules_root=self.modules_root,
        )

        self.assertIsNone(merged)
        self.assertEqual(diagnostics[0].code, "E125")

    def test_new_instance_can_be_added_disabled(self):
        profile = self._profile(
            modules=[
                {
                    "id": "postgres",
                    "source": "warehouse/postgres",
                    "enabled": True,
                    "config": {},
                }
            ]
        )

        merged, diagnostics = compose_profile(
            profile,
            self.profile_dir,
            "identity/keycloak",
            bindings={"metadata-database": "postgres.sql-database"},
            enabled=False,
            modules_root=self.modules_root,
        )

        self.assertEqual(diagnostics, [])
        self.assertFalse(merged["spec"]["modules"][-1]["enabled"])


if __name__ == "__main__":
    unittest.main()
