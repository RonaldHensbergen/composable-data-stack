"""Additional tests for cli.composer covering error and edge-case paths."""

import tempfile
import unittest
from pathlib import Path
from textwrap import dedent

from cli.composer import _iter_secret_aliases, compose_profile


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(content), encoding="utf-8")


def _module(name, provides="", consumes="", config_schema="type: object", kind="Module", metadata=True):
    meta = f"metadata:\n  name: {name}\n  version: \"0.1.0\"\n" if metadata else ""
    return (
        "apiVersion: cds/v1alpha1\n"
        f"kind: {kind}\n"
        f"{meta}"
        "spec:\n"
        f"  configSchema:\n    {config_schema}\n"
        f"{provides}{consumes}"
    )


PROVIDES_SQL = "  provides:\n    - name: sql-database\n      contract:\n        kind: sql-database\n"


def _consumes(required="true", name="db", kind="sql-database", mapped="spec.config.db"):
    return (
        "  consumes:\n"
        f"    - name: {name}\n"
        f"      contract:\n        kind: {kind}\n"
        f"      required: {required}\n"
        f"      mappedFrom: {mapped}\n"
    )


class ComposeProfileExtraTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.modules_root = self.root / "modules"
        self.profile_dir = self.root / "profiles" / "demo"
        self.profile_dir.mkdir(parents=True)

    def _add(self, rel, content):
        (self.modules_root / rel).mkdir(parents=True, exist_ok=True)
        (self.modules_root / rel / "module.yaml").write_text(content, encoding="utf-8")

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

    def _compose(self, profile, source="app/consumer", **kwargs):
        return compose_profile(profile, self.profile_dir, source, modules_root=self.modules_root, **kwargs)

    def _codes(self, diagnostics):
        return [d.code for d in diagnostics]

    def _provider(self, instance_id="db1"):
        self._add("db/one", _module("one", provides=PROVIDES_SQL))
        return {"id": instance_id, "source": "db/one", "enabled": True, "config": {}}

    def test_invalid_profile_returns_e120(self):
        for bad in ("not a dict", {"spec": "nope"}, {}):
            merged, diagnostics = self._compose(bad)
            self.assertIsNone(merged)
            self.assertEqual(self._codes(diagnostics), ["E120"])

    def test_unresolvable_module_source_returns_none(self):
        merged, diagnostics = self._compose(self._profile(), source="/abs/module")
        self.assertIsNone(merged)
        self.assertEqual(self._codes(diagnostics), ["E022"])
        self.assertTrue(all(d.level == "error" for d in diagnostics))

    def test_unloadable_module_yaml_returns_none(self):
        self._add("app/consumer", "key: [unclosed\n")
        merged, diagnostics = self._compose(self._profile())
        self.assertIsNone(merged)
        self.assertTrue(diagnostics)

    def test_wrong_kind_returns_e121(self):
        self._add("app/consumer", _module("consumer", kind="Profile"))
        merged, diagnostics = self._compose(self._profile())
        self.assertIsNone(merged)
        self.assertEqual(self._codes(diagnostics), ["E121"])

    def test_missing_instance_id_returns_e122(self):
        self._add("app/consumer", _module("consumer", metadata=False))
        merged, diagnostics = self._compose(self._profile())
        self.assertIsNone(merged)
        self.assertEqual(self._codes(diagnostics), ["E122"])

    def test_duplicate_instance_id_returns_e123(self):
        self._add("app/consumer", _module("consumer"))
        profile = self._profile(modules=[{"id": "consumer", "source": "app/consumer", "enabled": False}])
        merged, diagnostics = self._compose(profile)
        self.assertIsNone(merged)
        self.assertEqual(self._codes(diagnostics), ["E123"])

    def test_existing_module_with_bad_source_reports_error(self):
        self._add("app/consumer", _module("consumer"))
        profile = self._profile(modules=[{"id": "ghost", "source": "/abs/module"}])
        merged, diagnostics = self._compose(profile)
        self.assertIsNone(merged)
        self.assertEqual(self._codes(diagnostics), ["E022"])

    def test_existing_module_with_invalid_yaml_reports_error(self):
        self._add("app/consumer", _module("consumer"))
        self._add("bad/mod", "key: [unclosed\n")
        profile = self._profile(modules=[{"id": "bad", "source": "bad/mod"}])
        merged, diagnostics = self._compose(profile)
        self.assertIsNone(merged)
        self.assertTrue(diagnostics)

    def test_existing_modules_with_odd_shapes_are_skipped(self):
        self._add("app/consumer", _module("consumer"))
        self._add(
            "odd/provider",
            _module(
                "odd",
                provides=(
                    "  provides:\n    - just-a-string\n"
                    "    - name: incomplete\n"
                    "    - name: sql-database\n      contract:\n        kind: sql-database\n"
                ),
            ),
        )
        profile = self._profile(
            modules=[
                "not-a-dict",
                {"source": "odd/provider"},
                {"id": "nosource"},
                {"id": "odd", "source": "odd/provider"},
            ]
        )
        profile["spec"]["modules"][1]["id"] = 5
        self._add("app/consumer", _module("consumer", consumes=_consumes()))
        merged, diagnostics = self._compose(profile)
        self.assertEqual(diagnostics, [])
        self.assertEqual(merged["spec"]["modules"][-1]["config"]["db"]["contractRef"], "odd.sql-database")

    def test_explicit_binding_success(self):
        self._add("app/consumer", _module("consumer", consumes=_consumes()))
        merged, diagnostics = self._compose(
            self._profile(modules=[self._provider()]), bindings={"db": "db1.sql-database"}
        )
        self.assertEqual(diagnostics, [])
        self.assertEqual(merged["spec"]["modules"][-1]["dependsOn"], ["db1"])

    def test_invalid_binding_ref_returns_e124(self):
        self._add("app/consumer", _module("consumer", consumes=_consumes()))
        merged, diagnostics = self._compose(
            self._profile(modules=[self._provider()]), bindings={"db": "nodot"}
        )
        self.assertIsNone(merged)
        self.assertEqual(self._codes(diagnostics), ["E124"])
        self.assertIn("Invalid contract ref", diagnostics[0].message)

    def test_unknown_binding_returns_e124(self):
        self._add("app/consumer", _module("consumer", consumes=_consumes()))
        merged, diagnostics = self._compose(
            self._profile(modules=[self._provider()]), bindings={"db": "other.sql-database"}
        )
        self.assertIsNone(merged)
        self.assertEqual(self._codes(diagnostics), ["E124"])
        self.assertIn("does not match", diagnostics[0].message)

    def test_binding_kind_mismatch_returns_e124(self):
        self._add("app/consumer", _module("consumer", consumes=_consumes(kind="cache-service")))
        merged, diagnostics = self._compose(
            self._profile(modules=[self._provider()]), bindings={"db": "db1.sql-database"}
        )
        self.assertIsNone(merged)
        self.assertEqual(self._codes(diagnostics), ["E124"])
        self.assertIn("requires", diagnostics[0].message)

    def test_required_consume_without_candidates_returns_e125(self):
        self._add("app/consumer", _module("consumer", consumes=_consumes()))
        merged, diagnostics = self._compose(self._profile())
        self.assertIsNone(merged)
        self.assertEqual(self._codes(diagnostics), ["E125"])

    def test_optional_consume_without_candidates_is_skipped(self):
        self._add("app/consumer", _module("consumer", consumes=_consumes(required="false")))
        merged, diagnostics = self._compose(self._profile())
        self.assertEqual(diagnostics, [])
        self.assertNotIn("dependsOn", merged["spec"]["modules"][-1])
        self.assertEqual(merged["spec"]["modules"][-1]["config"], {})

    def test_multiple_candidates_returns_e125(self):
        self._add("app/consumer", _module("consumer", consumes=_consumes()))
        self._add("db/two", _module("two", provides=PROVIDES_SQL))
        profile = self._profile(
            modules=[self._provider(), {"id": "db2", "source": "db/two", "enabled": True}]
        )
        merged, diagnostics = self._compose(profile)
        self.assertIsNone(merged)
        self.assertEqual(self._codes(diagnostics), ["E125"])
        self.assertIn("db1.sql-database, db2.sql-database", diagnostics[0].message)

    def test_disabled_provider_is_not_a_candidate(self):
        self._add("app/consumer", _module("consumer", consumes=_consumes()))
        provider = self._provider()
        provider["enabled"] = False
        merged, diagnostics = self._compose(self._profile(modules=[provider]))
        self.assertIsNone(merged)
        self.assertEqual(self._codes(diagnostics), ["E125"])

    def test_malformed_consume_entries_are_ignored(self):
        self._add(
            "app/consumer",
            _module(
                "consumer",
                consumes=(
                    "  consumes:\n    - just-a-string\n"
                    "    - name: nomap\n      contract:\n        kind: sql-database\n"
                ),
            ),
        )
        merged, diagnostics = self._compose(self._profile())
        self.assertEqual(diagnostics, [])
        self.assertIsNotNone(merged)

    def test_mapped_from_without_prefix_and_explicit_options(self):
        self._add("app/consumer", _module("consumer", consumes=_consumes(mapped="db.nested")))
        merged, diagnostics = self._compose(
            self._profile(modules=[self._provider()]),
            instance_id="web",
            version="2.0.0",
            enabled=False,
            depends_on=["extra"],
            config_settings=[("db.nested.driver", "pg")],
        )
        self.assertEqual(diagnostics, [])
        new = merged["spec"]["modules"][-1]
        self.assertEqual(new["id"], "web")
        self.assertEqual(new["version"], "2.0.0")
        self.assertFalse(new["enabled"])
        self.assertEqual(new["dependsOn"], ["db1", "extra"])
        self.assertEqual(new["config"]["db"]["nested"], {"driver": "pg", "contractRef": "db1.sql-database"})

    def test_missing_secret_alias_returns_e126(self):
        self._add("app/consumer", _module("consumer"))
        merged, diagnostics = self._compose(
            self._profile(), config_settings=[("tokens", ["secrets.api_token", {"k": "secrets.other"}])]
        )
        self.assertIsNone(merged)
        self.assertEqual(self._codes(diagnostics), ["E126", "E126"])

    def test_secret_alias_added_and_existing_preserved(self):
        self._add("app/consumer", _module("consumer"))
        profile = self._profile(secrets={"other": {"env": "CDS_OTHER", "required": True}})
        merged, diagnostics = self._compose(
            profile,
            config_settings=[("a", "secrets.api_token"), ("b", "secrets.other")],
            secret_env_names={"api_token": "CDS_API_TOKEN"},
        )
        self.assertEqual(diagnostics, [])
        values = merged["spec"]["secrets"]["values"]
        self.assertEqual(values["api_token"], {"env": "CDS_API_TOKEN", "required": True})
        self.assertEqual(values["other"]["env"], "CDS_OTHER")
        self.assertNotIn("api_token", profile["spec"]["secrets"]["values"])

    def test_secrets_block_created_when_absent(self):
        self._add("app/consumer", _module("consumer"))
        profile = self._profile()
        del profile["spec"]["secrets"]
        del profile["spec"]["modules"]
        merged, diagnostics = self._compose(
            profile, config_settings=[("a", "secrets.tok")], secret_env_names={"tok": "CDS_TOK"}
        )
        self.assertEqual(diagnostics, [])
        self.assertEqual(merged["spec"]["secrets"]["provider"], {"type": "env"})
        self.assertEqual(len(merged["spec"]["modules"]), 1)

    def test_iter_secret_aliases_walks_lists_and_dicts(self):
        value = {"a": ["secrets.x", {"b": "secrets.y"}], "c": "plain", "d": 3}
        self.assertEqual(sorted(_iter_secret_aliases(value)), ["x", "y"])


if __name__ == "__main__":
    unittest.main()
