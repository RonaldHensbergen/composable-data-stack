"""
Committed keycloak -> postgres binding test.

Proves, via the real plan/render pipeline (not an uncommitted scratch
profile), that keycloak's `metadataDatabase.contractRef` binding to a
postgres module's `sql-database` contract resolves all five
`KC_DB_URL_*`/`KC_DB_USERNAME`/`KC_DB_PASSWORD` environment variables through
to the rendered `keycloak` service -- i.e. none of them are left as an
unresolved `${bindings...}` expression -- while `KC_BOOTSTRAP_ADMIN_PASSWORD`
(sourced from `config.adminUser.passwordFrom`, not a consumed binding) stays
an unresolved `${CDS_*}` placeholder for Docker Compose to fill in at
runtime, per repo convention: secrets never resolve into plans or rendered
Compose. Same bar as the dlt<->postgres wiring test
(test_dlt_postgres_wiring.py).

Also proves the issue #682 engine guard end to end: keycloak's
metadata-database consume entry declares engineFrom, so binding it to a
sql-database provider that advertises a non-Postgres engine fails
`cds validate` with E154 (both when the profile leaves
metadataDatabase.driver at its postgres default and when it sets one
explicitly), while matching the advertised engine passes.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from cli.planner import build_plan
from cli.renderer import render_compose
from cli.validator import validate_profile

_REPO_ROOT = Path(__file__).resolve().parent.parent


class KeycloakPostgresWiringTest(unittest.TestCase):
    """Wires the real keycloak and postgres modules together and renders them."""

    def _write_profile(
        self,
        profile_dir: Path,
        http_port: int | None = None,
        environment: str = "local",
        keycloak_web_server_mode: str | None = None,
    ) -> Path:
        keycloak_config: dict = {
            "metadataDatabase": {
                "contractRef": "postgres.sql-database",
            },
            "adminUser": {
                "username": "admin",
                "passwordFrom": "secrets.keycloak_admin_password",
            },
        }
        if http_port is not None:
            keycloak_config["httpPort"] = http_port
        if keycloak_web_server_mode is not None:
            keycloak_config["webServer"] = {"mode": keycloak_web_server_mode}
        postgres_config: dict = {
            "database": "identity",
            "username": "identity",
            "passwordFrom": "secrets.identity_db_password",
            "superuserPasswordFrom": "secrets.postgres_superuser_password",
            "port": 5432,
        }
        if environment == "production":
            # E139 requires the host-auth advertisement to be set explicitly
            # in production profiles (configSchema defaults do not count), so
            # pin it here to keep the production regression tests focused on
            # the web-serving-mode rule.
            postgres_config["authentication"] = {"hostAuth": "scram-sha-256"}
        secrets_values: dict = {
            "identity_db_password": {
                "env": "CDS_IDENTITY_DB_PASSWORD",
                "required": True,
            },
            "postgres_superuser_password": {
                "env": "CDS_POSTGRES_SUPERUSER_PASSWORD",
                "required": True,
            },
            "keycloak_admin_password": {
                "env": "CDS_KEYCLOAK_ADMIN_PASSWORD",
                "required": True,
            },
        }
        backup_block: dict | None = None
        if environment == "production":
            # E133/E134/E135: production profiles must declare an encrypted,
            # restore-verified backup policy per workload class
            # (configuration, identity-metadata, warehouse for this module
            # set), pinned to postgres's built-in backup-target contract.
            secrets_values["backup_encryption_key"] = {
                "env": "CDS_BACKUP_ENCRYPTION_KEY",
                "required": True,
            }
            backup_block = {
                "target": {"contractRef": "postgres.backup-target"},
                "encryption": {"keyFrom": "secrets.backup_encryption_key"},
                "policies": [
                    {
                        "workloadClass": workload_class,
                        "retentionDays": 30,
                        "rpoMinutes": 60,
                        "rtoMinutes": 240,
                        "restoreVerification": {"frequencyDays": 30},
                    }
                    for workload_class in (
                        "configuration",
                        "identity-metadata",
                        "warehouse",
                    )
                ],
            }
        profile = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Profile",
            "metadata": {
                "name": "keycloak-postgres-wiring",
                "environment": environment,
            },
            "spec": {
                "runtime": {"type": "docker-compose"},
                "modules": [
                    {
                        "id": "postgres",
                        "source": "modules/warehouse/postgres",
                        "version": "0.1.0",
                        "enabled": True,
                        "config": postgres_config,
                    },
                    {
                        "id": "keycloak",
                        "source": "modules/identity/keycloak",
                        "version": "0.1.0",
                        "enabled": True,
                        "dependsOn": ["postgres"],
                        "config": keycloak_config,
                    },
                ],
                "secrets": {
                    "provider": {"type": "env"},
                    "values": secrets_values,
                },
            },
        }
        if backup_block is not None:
            profile["spec"]["backup"] = backup_block

        profile_file = profile_dir / "profile.yaml"
        profile_file.write_text(yaml.safe_dump(profile), encoding="utf-8")
        return profile_file

    def test_all_five_kc_db_vars_resolve_and_admin_password_stays_a_placeholder(self):
        with tempfile.TemporaryDirectory() as root:
            profile_dir = Path(root)
            profile_file = self._write_profile(profile_dir)

            env_file = profile_dir / ".env"
            env_file.write_text(
                "CDS_IDENTITY_DB_PASSWORD=identity_testpass\n"
                "CDS_POSTGRES_SUPERUSER_PASSWORD=superuser_testpass\n"
                "CDS_KEYCLOAK_ADMIN_PASSWORD=admin_testpass\n",
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

                output, render_diags = render_compose(plan, env_file=str(env_file))
                self.assertEqual(
                    [d for d in render_diags if d.level == "error"],
                    [],
                    msg=f"unexpected render errors: {render_diags}",
                )

                compose = yaml.safe_load(output)
                keycloak_env = compose["services"]["keycloak"]["environment"]

                # All five consumed sql-database binding fields must be
                # fully resolved to postgres's rendered values -- not left
                # as raw "${bindings.metadata-database.*}" expressions.
                for key in (
                    "KC_DB_URL_HOST",
                    "KC_DB_URL_PORT",
                    "KC_DB_URL_DATABASE",
                    "KC_DB_USERNAME",
                    "KC_DB_PASSWORD",
                ):
                    self.assertIn(key, keycloak_env)
                    self.assertNotIn("${bindings.", str(keycloak_env[key]))

                self.assertEqual(keycloak_env["KC_DB_URL_HOST"], "postgres")
                self.assertEqual(keycloak_env["KC_DB_URL_PORT"], 5432)
                self.assertEqual(keycloak_env["KC_DB_URL_DATABASE"], "identity")
                self.assertEqual(keycloak_env["KC_DB_USERNAME"], "identity")
                self.assertEqual(
                    keycloak_env["KC_DB_PASSWORD"], "${CDS_IDENTITY_DB_PASSWORD}"
                )

                # KC_DB is derived from config.metadataDatabase.driver (not
                # hardcoded) and defaults to "postgres" when the profile
                # does not set it explicitly.
                self.assertEqual(keycloak_env["KC_DB"], "postgres")

                # The admin bootstrap password is not a consumed binding --
                # it comes straight from config.adminUser.passwordFrom -- but
                # must still stay an unresolved runtime placeholder, never a
                # plaintext secret, per repo-wide secrets convention.
                self.assertEqual(
                    keycloak_env["KC_BOOTSTRAP_ADMIN_PASSWORD"],
                    "${CDS_KEYCLOAK_ADMIN_PASSWORD}",
                )
                self.assertEqual(keycloak_env["KC_BOOTSTRAP_ADMIN_USERNAME"], "admin")

    def test_entrypoint_reads_container_env_via_compose_escape(self):
        # The serving-mode switch lives in an inline entrypoint script, so the
        # mode placeholder must reach the container as a literal
        # ${CDS_WEBSERVER_MODE:-production} that /bin/bash resolves against
        # the service's own environment entry. If the $$ escape were lost,
        # Docker Compose would interpolate the placeholder itself at up time
        # from the *host* environment (defaulting it to "production" there)
        # and the rendered CDS_WEBSERVER_MODE environment entry would never
        # be consulted -- a profile's explicit `development` override in a
        # local profile would silently run production mode on every `cds up`.
        with tempfile.TemporaryDirectory() as root:
            profile_dir = Path(root)
            profile_file = self._write_profile(profile_dir)

            env_file = profile_dir / ".env"
            env_file.write_text(
                "CDS_IDENTITY_DB_PASSWORD=identity_testpass\n"
                "CDS_POSTGRES_SUPERUSER_PASSWORD=superuser_testpass\n"
                "CDS_KEYCLOAK_ADMIN_PASSWORD=admin_testpass\n",
                encoding="utf-8",
            )

            with mock.patch.dict(
                "os.environ", {"CDS_MODULE_PATH": str(_REPO_ROOT)}, clear=False
            ):
                plan, _ = build_plan(str(profile_file), env_file=str(env_file))
                self.assertIsNotNone(plan)
                output, render_diags = render_compose(plan, env_file=str(env_file))
                self.assertEqual(
                    [d for d in render_diags if d.level == "error"],
                    [],
                    msg=f"unexpected render errors: {render_diags}",
                )

                compose = yaml.safe_load(output)
                service = compose["services"]["keycloak"]
                script = "\n".join(str(line) for line in service["entrypoint"])
                # Every mode placeholder must carry the $$ escape; a bare
                # unescaped ${...} would let Compose hijack the mode switch.
                bare = script.replace("$${CDS_WEBSERVER_MODE:-production}", "")
                self.assertNotIn("${CDS_WEBSERVER_MODE", bare)
                self.assertIn("$${CDS_WEBSERVER_MODE:-production}", script)

                # The rendered mode the script will read at container runtime
                # is the module's configSchema default (production, #974).
                self.assertEqual(
                    service["environment"]["CDS_WEBSERVER_MODE"], "production"
                )

                # Production mode must boot without a configured hostname
                # (plain-HTTP-behind-proxy topology, docs/serving-modes.md).
                self.assertIn("--hostname-strict=false", script)

    def _write_env(self, profile_dir: Path, production: bool = False) -> Path:
        env_file = profile_dir / ".env"
        lines = [
            "CDS_IDENTITY_DB_PASSWORD=identity_testpass",
            "CDS_POSTGRES_SUPERUSER_PASSWORD=superuser_testpass",
            "CDS_KEYCLOAK_ADMIN_PASSWORD=admin_testpass",
        ]
        if production:
            lines.append("CDS_BACKUP_ENCRYPTION_KEY=backup-encryption-test-key")
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return env_file

    def test_production_profile_without_explicit_mode_passes_e140_and_renders_production(
        self,
    ):
        # E140 absent-mode regression (issue #974): E140 only rejects an
        # *explicit* webServer.mode "development" in a production profile,
        # because validation runs before configSchema defaults apply -- an
        # absent mode is decided by the module default at plan time. This
        # test pins both halves of that contract for the real keycloak
        # module: the configSchema default is "production" (and the module
        # is production-suitable), and a production profile with keycloak
        # and no explicit mode validates without E140 and renders
        # CDS_WEBSERVER_MODE=production, so it can never silently run
        # `start-dev` at runtime.
        module_def = yaml.safe_load(
            (
                _REPO_ROOT / "modules" / "identity" / "keycloak" / "module.yaml"
            ).read_text(encoding="utf-8")
        )
        self.assertNotEqual(
            module_def["metadata"].get("productionSuitable", True),
            False,
            msg="keycloak must be production-suitable (productionSuitable: true)",
        )
        mode_schema = module_def["spec"]["configSchema"]["properties"]["webServer"][
            "properties"
        ]["mode"]
        self.assertEqual(
            mode_schema.get("default"),
            "production",
            msg="keycloak's webServer.mode configSchema default must be production (#974)",
        )

        with tempfile.TemporaryDirectory() as root:
            profile_dir = Path(root)
            profile_file = self._write_profile(profile_dir, environment="production")
            env_file = self._write_env(profile_dir, production=True)

            with mock.patch.dict(
                "os.environ", {"CDS_MODULE_PATH": str(_REPO_ROOT)}, clear=False
            ):
                diagnostics = validate_profile(str(profile_file))
                self.assertEqual(
                    [d for d in diagnostics if d.level == "error"],
                    [],
                    msg=f"production profile with keycloak and no explicit mode must validate: {diagnostics}",
                )
                self.assertNotIn(
                    "E140",
                    [d.code for d in diagnostics],
                    msg=f"unexpected E140: {diagnostics}",
                )

                plan, plan_diags = build_plan(str(profile_file), env_file=str(env_file))
                self.assertIsNotNone(plan)
                self.assertEqual(
                    [d for d in plan_diags if d.level == "error"],
                    [],
                    msg=f"unexpected plan errors: {plan_diags}",
                )

                output, render_diags = render_compose(plan, env_file=str(env_file))
                self.assertEqual(
                    [d for d in render_diags if d.level == "error"],
                    [],
                    msg=f"unexpected render errors: {render_diags}",
                )

                compose = yaml.safe_load(output)
                keycloak_env = compose["services"]["keycloak"]["environment"]
                self.assertEqual(keycloak_env["CDS_WEBSERVER_MODE"], "production")

    def test_production_profile_with_explicit_development_mode_still_fails_e140(self):
        # The other half of the E140 contract for keycloak: an explicit
        # webServer.mode "development" in a production profile is still
        # rejected, so the rule does not depend on the default flip.
        with tempfile.TemporaryDirectory() as root:
            profile_dir = Path(root)
            profile_file = self._write_profile(
                profile_dir,
                environment="production",
                keycloak_web_server_mode="development",
            )

            with mock.patch.dict(
                "os.environ", {"CDS_MODULE_PATH": str(_REPO_ROOT)}, clear=False
            ):
                diagnostics = validate_profile(str(profile_file))
                e140 = [d for d in diagnostics if d.code == "E140"]
                self.assertEqual(len(e140), 1)
                self.assertEqual(e140[0].level, "error")
                self.assertEqual(e140[0].path, "spec.modules[1].config.webServer.mode")

    def test_host_port_binds_localhost_and_honors_config(self):
        with tempfile.TemporaryDirectory() as root:
            profile_dir = Path(root)
            profile_file = self._write_profile(profile_dir, http_port=8180)

            env_file = profile_dir / ".env"
            env_file.write_text(
                "CDS_IDENTITY_DB_PASSWORD=identity_testpass\n"
                "CDS_POSTGRES_SUPERUSER_PASSWORD=superuser_testpass\n"
                "CDS_KEYCLOAK_ADMIN_PASSWORD=admin_testpass\n",
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

                output, render_diags = render_compose(plan, env_file=str(env_file))
                self.assertEqual(
                    [d for d in render_diags if d.level == "error"],
                    [],
                    msg=f"unexpected render errors: {render_diags}",
                )

                compose = yaml.safe_load(output)
                self.assertEqual(
                    compose["services"]["keycloak"]["ports"],
                    ["127.0.0.1:8180:8080"],
                )


class KeycloakEngineGuardTest(unittest.TestCase):
    """
    Issue #682 end-to-end guard: keycloak's metadata-database consume entry
    declares `engineFrom: spec.config.metadataDatabase.driver`, so a bound
    sql-database provider must advertise a matching `engine` in its contract
    spec. A non-Postgres provider must fail `cds validate` with E154 -- with
    the default driver (postgres) as well as with an explicitly configured
    one -- and a matching engine must pass.
    """

    _FAKE_MYSQL_MODULE = {
        "apiVersion": "cds/v1alpha1",
        "kind": "Module",
        "metadata": {"name": "fake-mysql", "category": "warehouse", "version": "0.1.0"},
        "spec": {
            "runtime": {
                "type": "container",
                "service": {
                    "name": "fake-mysql",
                    "ports": [
                        {"name": "mysql", "containerPort": 3306, "protocol": "TCP"}
                    ],
                },
            },
            "configSchema": {"type": "object", "additionalProperties": False},
            "provides": [
                {
                    "name": "sql-database",
                    "contract": {
                        "kind": "sql-database",
                        "spec": {
                            "host": "fake-mysql",
                            "port": 3306,
                            "database": "identity",
                            "username": "identity",
                            "password": "secrets.identity_db_password",
                            "engine": "mysql",
                        },
                    },
                }
            ],
            "implementation": {"kind": "docker-compose", "compose": {"services": {}}},
        },
    }

    def _write_fake_provider_profile(
        self, root: Path, driver: str | None
    ) -> tuple[Path, Path]:
        # A scratch modules root holding a copy of the real keycloak module
        # plus a fake sql-database provider advertising engine "mysql"; the
        # profile binds keycloak to the fake provider.
        modules_root = root / "modules"
        shutil.copytree(
            _REPO_ROOT / "modules" / "identity" / "keycloak",
            modules_root / "identity" / "keycloak",
        )
        fake_dir = modules_root / "warehouse" / "fake-mysql"
        fake_dir.mkdir(parents=True)
        (fake_dir / "module.yaml").write_text(
            yaml.safe_dump(self._FAKE_MYSQL_MODULE), encoding="utf-8"
        )

        metadata_database: dict = {"contractRef": "fake-mysql.sql-database"}
        if driver is not None:
            metadata_database["driver"] = driver
        profile = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Profile",
            "metadata": {"name": "keycloak-engine-guard", "environment": "local"},
            "spec": {
                "runtime": {"type": "docker-compose"},
                "modules": [
                    {
                        "id": "fake-mysql",
                        "source": "modules/warehouse/fake-mysql",
                        "version": "0.1.0",
                        "enabled": True,
                        "config": {},
                    },
                    {
                        "id": "keycloak",
                        "source": "modules/identity/keycloak",
                        "version": "0.1.0",
                        "dependsOn": ["fake-mysql"],
                        "enabled": True,
                        "config": {
                            "metadataDatabase": metadata_database,
                            "adminUser": {
                                "username": "admin",
                                "passwordFrom": "secrets.keycloak_admin_password",
                            },
                        },
                    },
                ],
                "secrets": {
                    "provider": {"type": "env"},
                    "values": {
                        "identity_db_password": {
                            "env": "CDS_IDENTITY_DB_PASSWORD",
                            "required": True,
                        },
                        "keycloak_admin_password": {
                            "env": "CDS_KEYCLOAK_ADMIN_PASSWORD",
                            "required": True,
                        },
                    },
                },
            },
        }
        profile_dir = root / "profiles" / "local"
        profile_dir.mkdir(parents=True)
        profile_file = profile_dir / "profile.yaml"
        profile_file.write_text(yaml.safe_dump(profile), encoding="utf-8")
        return profile_file, modules_root

    def _validate(self, driver: str | None) -> list:
        with tempfile.TemporaryDirectory() as root:
            profile_file, modules_root = self._write_fake_provider_profile(
                Path(root), driver
            )
            # CDS_MODULE_PATH is the root that contains the "modules/" tree,
            # mirroring how the repo-root test wires "modules/..." sources.
            with mock.patch.dict(
                "os.environ", {"CDS_MODULE_PATH": str(modules_root.parent)}, clear=False
            ):
                return validate_profile(str(profile_file))

    def test_non_postgres_provider_with_default_driver_fails_e154(self):
        # The profile does not set metadataDatabase.driver, so the schema
        # default "postgres" applies -- the guard must still catch the
        # mismatch against the provider's advertised "mysql" engine.
        diagnostics = self._validate(driver=None)
        errors = [d for d in diagnostics if d.level == "error"]
        e154 = [d for d in errors if d.code == "E154"]
        self.assertEqual(len(e154), 1, msg=f"diagnostics: {diagnostics}")
        self.assertIn('"postgres"', e154[0].message)
        self.assertIn('"mysql"', e154[0].message)
        self.assertIn("fake-mysql.sql-database", e154[0].message)

    def test_non_postgres_provider_with_explicit_driver_fails_e154(self):
        diagnostics = self._validate(driver="mariadb")
        e154 = [d for d in diagnostics if d.level == "error" and d.code == "E154"]
        self.assertEqual(len(e154), 1, msg=f"diagnostics: {diagnostics}")
        self.assertIn('"mariadb"', e154[0].message)
        self.assertIn('"mysql"', e154[0].message)

    def test_matching_explicit_driver_passes_validation(self):
        diagnostics = self._validate(driver="mysql")
        self.assertEqual(
            [d for d in diagnostics if d.level == "error"],
            [],
            msg=f"unexpected validation errors: {diagnostics}",
        )


if __name__ == "__main__":
    unittest.main()
