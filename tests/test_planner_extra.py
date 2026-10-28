import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from cli import planner
from cli.constants import MAX_NESTING_DEPTH, MaxNestingDepthExceeded
from cli.diagnostics import Diagnostic


def _module(name="mod", config_schema=None, provides=None, consumes=None):
    return {
        "apiVersion": "cds/v1alpha1",
        "kind": "Module",
        "metadata": {"name": name},
        "spec": {
            "configSchema": config_schema
            or {"type": "object", "additionalProperties": False},
            "provides": provides or [],
            "consumes": consumes or [],
            "implementation": {"kind": "docker-compose", "compose": {"services": {}}},
        },
    }


def _profile(modules, **spec):
    return {
        "apiVersion": "cds/v1alpha1",
        "kind": "Profile",
        "metadata": {"name": "test"},
        "spec": {"modules": modules, **spec},
    }


def _write_module(root: Path, name: str, module_def: dict) -> None:
    module_dir = root / "modules" / name
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "module.yaml").write_text(yaml.safe_dump(module_def), encoding="utf-8")


def _nested(depth, leaf):
    obj = leaf
    for _ in range(depth):
        obj = {"a": obj}
    return obj


def _nested_schema(depth):
    schema = {"type": "string"}
    for _ in range(depth):
        schema = {"type": "object", "properties": {"a": schema}}
    return schema


class BuildPlanEdgeCaseTest(unittest.TestCase):
    def test_build_plan_returns_none_when_profile_cannot_be_resolved(self):
        diag = Diagnostic(level="error", code="E001", message="boom", path="x")
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_file = Path(tmpdir) / "profile.yaml"
            with patch("cli.overlay.resolve_extends", return_value=(None, {}, [diag])):
                plan, diags = planner.build_plan(str(profile_file))

        self.assertIsNone(plan)
        self.assertEqual([d.code for d in diags], ["E001"])

    def test_build_plan_with_environment_uses_resolve_profile(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_file = Path(tmpdir) / "profile.yaml"
            with patch(
                "cli.overlay.resolve_profile", return_value=(_profile([]), {"k": "v"}, [])
            ) as resolver:
                plan, diags = planner.build_plan(str(profile_file), environment="dev")

        resolver.assert_called_once()
        self.assertEqual(diags, [])
        self.assertEqual(plan["environment"], "dev")
        self.assertEqual(plan["provenance"], {"k": "v"})

    def test_build_plan_from_profile_rejects_non_list_modules(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            plan, diags = planner.build_plan_from_profile(_profile("nope"), Path(tmpdir))

        self.assertIsNone(plan)
        self.assertEqual([(d.code, d.path) for d in diags], [("E010", "spec.modules")])

    def test_build_plan_from_profile_rejects_non_object_module_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            plan, diags = planner.build_plan_from_profile(_profile(["x"]), Path(tmpdir))

        self.assertIsNotNone(plan)
        self.assertEqual([(d.code, d.path) for d in diags], [("E010", "spec.modules[0]")])

    def test_build_plan_from_profile_skips_disabled_and_reports_missing_id_and_source(self):
        profile = _profile([
            {"id": "off", "source": "./modules/off", "enabled": False},
            {"source": "./modules/x"},
            {"id": "nosource"},
        ])
        with tempfile.TemporaryDirectory() as tmpdir:
            plan, diags = planner.build_plan_from_profile(profile, Path(tmpdir))

        self.assertEqual(plan["modules"], [])
        self.assertEqual(
            [(d.code, d.path) for d in diags],
            [("E010", "spec.modules[1].id"), ("E010", "spec.modules[2].source")],
        )
        self.assertEqual(plan["sourceProfile"], "<in-memory profile>")

    def test_build_plan_from_profile_skips_module_whose_yaml_cannot_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module_dir = root / "modules" / "bad"
            module_dir.mkdir(parents=True)
            (module_dir / "module.yaml").write_text("a: [unclosed", encoding="utf-8")

            plan, diags = planner.build_plan_from_profile(
                _profile([{"id": "bad", "source": "./modules/bad"}]), root
            )

        self.assertEqual(plan["modules"], [])
        self.assertTrue(any(d.level == "error" for d in diags))

    def test_build_plan_from_profile_reports_unresolvable_module_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            plan, diags = planner.build_plan_from_profile(
                _profile([{"id": "m", "source": "./modules/missing"}]), Path(tmpdir)
            )

        self.assertEqual(plan["modules"], [])
        self.assertTrue(diags)

    def test_build_plan_from_profile_reports_e094_for_deeply_nested_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_module(root, "deep", _module(config_schema=_nested_schema(MAX_NESTING_DEPTH + 5)))
            config = _nested(MAX_NESTING_DEPTH + 5, "v")

            plan, diags = planner.build_plan_from_profile(
                _profile([{"id": "deep", "source": "./modules/deep", "config": config}]), root
            )

        self.assertEqual(plan["modules"], [])
        self.assertEqual([(d.code, d.path) for d in diags], [("E094", "spec.modules[0].config")])

    def test_build_plan_from_profile_reports_e094_for_deeply_nested_provided_contract(self):
        deep_contract = {"kind": "x", "spec": _nested(MAX_NESTING_DEPTH + 5, "v")}
        module = _module(provides=[{"name": "deep", "contract": deep_contract}])
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_module(root, "prov", module)

            plan, diags = planner.build_plan_from_profile(
                _profile([{"id": "prov", "source": "./modules/prov"}]), root
            )

        self.assertEqual([(d.code, d.path) for d in diags], [("E094", "module:prov.provides")])
        self.assertEqual(plan["modules"][0]["provides"], {})

    def test_build_plan_from_profile_records_environment_provenance_and_label(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            plan, _ = planner.build_plan_from_profile(
                _profile([]),
                Path(tmpdir),
                environment="dev",
                provenance={"a": "b"},
                source_label="label",
            )

        self.assertEqual(plan["environment"], "dev")
        self.assertEqual(plan["provenance"], {"a": "b"})
        self.assertEqual(plan["sourceProfile"], "label")

    def test_plan_generated_profile_returns_none_when_resolution_fails(self):
        diag = Diagnostic(level="error", code="E001", message="boom", path="x")
        with patch("cli.overlay.resolve_extends_from_profile", return_value=(None, {}, [diag])):
            plan, diags = planner.plan_generated_profile(_profile([]), Path("."))

        self.assertIsNone(plan)
        self.assertEqual([d.code for d in diags], ["E001"])

    def test_plan_generated_profile_with_environment_builds_plan(self):
        with patch(
            "cli.overlay.resolve_profile_from_profile",
            return_value=(_profile([]), {"k": "v"}, []),
        ) as resolver:
            plan, diags = planner.plan_generated_profile(
                _profile([]), Path("."), environment="dev", source_label="gen"
            )

        resolver.assert_called_once()
        self.assertEqual(diags, [])
        self.assertEqual(plan["environment"], "dev")
        self.assertEqual(plan["provenance"], {"k": "v"})
        self.assertEqual(plan["sourceProfile"], "gen")


class ConfigEnvSubstitutionTest(unittest.TestCase):
    def test_substitutes_nested_values_and_reports_missing_vars(self):
        diags: list[Diagnostic] = []
        result = planner._substitute_config_env_vars(
            {"a": "x-${CDS_A}", "b": ["${CDS_B}", 3], "c": {"d": "${CDS_MISSING}"}},
            {"CDS_A": "1", "CDS_B": "2"},
            "cfg",
            diags,
        )

        self.assertEqual(result, {"a": "x-1", "b": ["2", 3], "c": {"d": ""}})
        self.assertEqual([(d.code, d.path) for d in diags], [("E083", "cfg.c.d")])


class ImageOverrideTest(unittest.TestCase):
    @staticmethod
    def _module_def(tag_default="1.0"):
        return {
            "spec": {
                "configSchema": {
                    "properties": {
                        "image": {
                            "properties": {
                                "source": {},
                                "variant": {},
                                "tag": {"default": tag_default},
                            }
                        }
                    }
                }
            }
        }

    def test_supports_image_variant(self):
        self.assertTrue(planner._supports_image_variant(self._module_def()))
        self.assertFalse(planner._supports_image_variant({}))

    def test_override_noop_without_source_property_or_value(self):
        config = {"image": {"tag": "1.0"}}
        planner._apply_image_source_override(config, {}, "registry")
        planner._apply_image_source_override(config, self._module_def(), None)

        self.assertEqual(config, {"image": {"tag": "1.0"}})

    def test_override_build_sets_source_only(self):
        config = {"image": {"tag": "1.0", "variant": "hardened"}}
        planner._apply_image_source_override(config, self._module_def(), "build")

        self.assertEqual(config["image"]["source"], "build")
        self.assertEqual(config["image"]["tag"], "1.0")

    def test_override_registry_prefixes_default_tag_with_variant(self):
        config = {"image": {"tag": "1.0", "variant": "hardened"}}
        planner._apply_image_source_override(config, self._module_def(), "registry")

        self.assertEqual(config["image"]["tag"], "hardened-1.0")

    def test_override_registry_keeps_pinned_or_base_tags(self):
        pinned = {"image": {"tag": "9.9", "variant": "hardened"}}
        planner._apply_image_source_override(pinned, self._module_def(), "registry")
        base = {"image": {"tag": "1.0", "variant": "base"}}
        planner._apply_image_source_override(base, self._module_def(), "registry")
        no_default = {"image": {"variant": "hardened"}}
        planner._apply_image_source_override(no_default, self._module_def(tag_default=""), "registry")

        self.assertEqual(pinned["image"]["tag"], "9.9")
        self.assertEqual(base["image"]["tag"], "1.0")
        self.assertNotIn("tag", no_default["image"])

    def test_build_plan_from_profile_applies_hardened_flag(self):
        module = _module(
            config_schema={
                "type": "object",
                "properties": {
                    "image": {
                        "type": "object",
                        "properties": {
                            "variant": {"type": "string", "default": "base"},
                            "source": {"type": "string", "default": "build"},
                            "tag": {"type": "string", "default": "1.0"},
                        },
                    }
                },
            }
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_module(root, "img", module)
            plan, diags = planner.build_plan_from_profile(
                _profile([{"id": "img", "source": "./modules/img"}]),
                root,
                hardened=True,
                image_source="registry",
            )

        self.assertEqual(diags, [])
        image = plan["modules"][0]["config"]["image"]
        self.assertEqual(image["variant"], "hardened")
        self.assertEqual(image["source"], "registry")
        self.assertEqual(image["tag"], "hardened-1.0")


class ApplyDefaultsTest(unittest.TestCase):
    def test_applies_object_array_and_nested_defaults(self):
        schema = {
            "type": "object",
            "properties": {
                "hc": {
                    "type": "object",
                    "default": {},
                    "properties": {"enabled": {"type": "boolean", "default": True}},
                },
                "nested": {
                    "type": "object",
                    "properties": {"x": {"type": "integer", "default": 1}},
                },
                "empty": {"type": "object", "properties": {"y": {"type": "string"}}},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"k": {"type": "string", "default": "v"}},
                    },
                },
                "given": {"type": "array", "items": {"type": "object", "properties": {"k": {"default": "v"}}}},
                "scalar": {"type": "string", "default": "s"},
            },
        }
        result = planner.apply_defaults({"given": [{}, {"k": "z"}]}, schema)

        self.assertEqual(result["hc"], {"enabled": True})
        self.assertEqual(result["nested"], {"x": 1})
        self.assertNotIn("empty", result)
        self.assertNotIn("items", result)
        self.assertEqual(result["given"], [{"k": "v"}, {"k": "z"}])
        self.assertEqual(result["scalar"], "s")

    def test_non_matching_types_are_returned_unchanged(self):
        self.assertEqual(planner._apply_schema_defaults("s", {"type": "object"}), "s")
        self.assertEqual(planner._apply_schema_defaults("s", {"type": "array"}), "s")
        self.assertEqual(planner._apply_schema_defaults(None, {"type": "array"}), [])
        self.assertEqual(planner._apply_schema_defaults(None, {"type": "object"}), {})
        self.assertEqual(planner._apply_schema_defaults(None, {"default": 5}), 5)

    def test_raises_when_schema_nesting_too_deep(self):
        with self.assertRaises(MaxNestingDepthExceeded):
            planner.apply_defaults(_nested(MAX_NESTING_DEPTH + 5, "v"), _nested_schema(MAX_NESTING_DEPTH + 5))


class ConsumedContractTest(unittest.TestCase):
    @staticmethod
    def _inst(config, consumes):
        return {"id": "c", "config": config, "module": {"spec": {"consumes": consumes}}}

    def _resolve(self, config, consumes, modules=None, contracts=None):
        diags: list[Diagnostic] = []
        resolved = planner.resolve_consumed_contracts(
            self._inst(config, consumes), modules or {}, contracts or {}, diags
        )
        return resolved, diags

    def test_skips_incomplete_consume_entries(self):
        resolved, diags = self._resolve({}, [{"name": "a"}, {"mappedFrom": "spec.config.x"}])

        self.assertEqual((resolved, diags), ({}, []))

    def test_malformed_required_if_reports_e021_and_falls_back_to_required(self):
        consume = {
            "name": "db",
            "mappedFrom": "spec.config.db",
            "required": False,
            "requiredIf": "!!!",
        }
        with patch("cli.planner.evaluate_required_if", side_effect=planner.RequiredIfSyntaxError("bad")):
            resolved, diags = self._resolve({}, [consume])

        self.assertEqual(resolved, {})
        self.assertEqual([d.code for d in diags], ["E021"])

    def test_required_if_makes_optional_consume_required(self):
        consume = {
            "name": "db",
            "mappedFrom": "spec.config.db",
            "required": False,
            "requiredIf": "x",
        }
        with patch("cli.planner.evaluate_required_if", return_value=True):
            _, diags = self._resolve({}, [consume])

        self.assertEqual([d.code for d in diags], ["E041"])

    def test_optional_missing_path_and_empty_binding_are_skipped(self):
        consume = {"name": "db", "mappedFrom": "spec.config.db", "required": False}
        self.assertEqual(self._resolve({}, [consume]), ({}, []))
        self.assertEqual(self._resolve({"db": {}}, [consume]), ({}, []))

    def test_required_missing_path_reports_e041(self):
        consume = {"name": "db", "mappedFrom": "spec.config.db"}
        _, diags = self._resolve({}, [consume])

        self.assertEqual([d.code for d in diags], ["E041"])
        self.assertIn("could not be resolved", diags[0].message)

    def test_binding_without_contract_ref_reports_e041(self):
        consume = {"name": "db", "mappedFrom": "spec.config.db"}
        _, diags = self._resolve({"db": {"other": 1}}, [consume])

        self.assertIn('must contain "contractRef"', diags[0].message)

    def test_invalid_contract_ref_reports_e041(self):
        consume = {"name": "db", "mappedFrom": "spec.config.db"}
        _, diags = self._resolve({"db": {"contractRef": "nodot"}}, [consume])

        self.assertIn("Invalid contract ref", diags[0].message)

    def test_unknown_producer_reports_e041(self):
        consume = {"name": "db", "mappedFrom": "spec.config.db"}
        _, diags = self._resolve({"db": {"contractRef": "p.sql"}}, [consume])

        self.assertIn("Unknown producer", diags[0].message)

    def test_producer_without_provided_contract_reports_e041(self):
        consume = {"name": "db", "mappedFrom": "spec.config.db"}
        _, diags = self._resolve(
            {"db": {"contractRef": "p.sql"}}, [consume], modules={"p": {}}, contracts={"p": {}}
        )

        self.assertIn('does not provide "sql"', diags[0].message)

    def test_resolves_matching_contract_with_deep_copy(self):
        consume = {"name": "db", "mappedFrom": "spec.config.db"}
        contracts = {"p": {"sql": {"kind": "sql"}}}
        resolved, diags = self._resolve(
            {"db": {"contractRef": "p.sql"}}, [consume], modules={"p": {}}, contracts=contracts
        )

        self.assertEqual(diags, [])
        self.assertEqual(resolved["db"], {"contractRef": "p.sql", "contract": {"kind": "sql"}})
        self.assertIsNot(resolved["db"]["contract"], contracts["p"]["sql"])


class SecretRefAndOutputsTest(unittest.TestCase):
    def test_resolve_secret_refs_reports_missing_secret_with_path(self):
        diags: list[Diagnostic] = []
        obj = {"a": ["secrets.MISSING", "plain"], "b": "secrets.present"}
        result = planner.resolve_secret_refs(obj, {"present": "CDS_P"}, "cfg", diags)

        self.assertEqual(result, obj)
        self.assertEqual([(d.code, d.path) for d in diags], [("E081", "cfg.a[0]")])

    def test_resolve_outputs_handles_valid_invalid_and_unresolved_refs(self):
        diags: list[Diagnostic] = []
        outputs = {
            "contracts": {
                "ok": {"from": "p.sql"},
                "badref": {"from": "nodot"},
                "missing": {"from": "p.other"},
                "notstr": {"from": 5},
            }
        }
        resolved = planner.resolve_outputs(outputs, {"p": {"sql": {"kind": "sql"}}}, diags)

        self.assertEqual(resolved, {"contracts": {"ok": {"from": "p.sql", "contract": {"kind": "sql"}}}})
        self.assertEqual(
            [(d.code, d.path) for d in diags],
            [
                ("E060", "spec.outputs.contracts.badref.from"),
                ("E060", "spec.outputs.contracts.missing.from"),
            ],
        )


class SubstitutionTest(unittest.TestCase):
    def test_substitute_values_raises_when_too_deep(self):
        with self.assertRaises(MaxNestingDepthExceeded):
            planner.substitute_values(_nested(MAX_NESTING_DEPTH + 5, "v"), {})

    def test_substitute_values_handles_containers_and_scalars(self):
        ctx = {"config": {"n": 3, "s": "x"}}
        result = planner.substitute_values({"a": ["${config.s}", 1, "${config.n}"]}, ctx)

        self.assertEqual(result, {"a": ["x", 1, 3]})

    def test_substitute_string_variants(self):
        ctx = {"config": {"n": 3, "s": "x", "pw": "secrets.db"}, "secrets": {"db": "CDS_DB"}}

        self.assertEqual(planner.substitute_string("plain", ctx), "plain")
        self.assertEqual(planner.substitute_string("${config.n}", ctx), 3)
        self.assertEqual(planner.substitute_string("a-${config.s}-b", ctx), "a-x-b")
        self.assertEqual(planner.substitute_string("$${config.s}", ctx), "${config.s}")
        self.assertEqual(planner.substitute_string("${config.nope}", ctx), "${config.nope}")
        self.assertEqual(planner.substitute_string("${config.pw}", ctx), "${CDS_DB}")
        self.assertEqual(planner.substitute_string("${secrets.db}", ctx), "${CDS_DB}")
        self.assertEqual(planner.substitute_string("${secrets.unmapped}", ctx), "${unmapped}")

    def test_resolve_expr_if_nonempty(self):
        ctx = {"config": {"v": "x", "e": " ", "pw": "secrets.db"}, "secrets": {"db": "CDS_DB"}}

        self.assertEqual(planner.resolve_expr("ifNonempty:config.v,-,+", ctx), "-x+")
        self.assertEqual(planner.resolve_expr("ifNonempty:config.e,-,+", ctx), "")
        self.assertEqual(planner.resolve_expr("ifNonempty:config.none,-,+", ctx), "")
        self.assertIsNone(planner.resolve_expr("ifNonempty:config.v", ctx))
        self.assertIsNone(planner.resolve_expr("config.v.deeper", ctx))
        self.assertEqual(planner.resolve_expr("config.pw", ctx), "${CDS_DB}")

    def test_resolve_provided_contracts_substitutes_and_skips_unnamed(self):
        inst = {
            "id": "svc",
            "config": {"port": 5432},
            "module": {
                "spec": {
                    "provides": [
                        {"name": "sql", "contract": {"host": "${service.host}", "port": "${config.port}"}},
                        {"contract": {"ignored": True}},
                    ]
                }
            },
        }

        self.assertEqual(
            planner.resolve_provided_contracts(inst),
            {"sql": {"host": "svc", "port": 5432}},
        )


class BackupSourceBindingTest(unittest.TestCase):
    """E146: a volume-kind backup-source binding must name a Compose volume
    the producer actually declares (issue #668); anything else would make
    Docker Compose create an empty implicit volume the backup protects
    nothing with."""

    @staticmethod
    def _consumed_backup_source(contract_ref, volume_name):
        return {
            "contractRef": contract_ref,
            "contract": {
                "kind": "backup-source",
                "spec": {"kind": "volume", "volumeName": volume_name},
            },
        }

    @staticmethod
    def _module(module_id, volumes, consumes=None):
        return {
            "id": module_id,
            "consumes": consumes or {},
            "implementation": {
                "kind": "docker-compose",
                "compose": {"services": {}, "volumes": {name: {} for name in volumes}},
            },
        }

    def _diags(self, modules):
        diagnostics: list[Diagnostic] = []
        planner.validate_backup_source_bindings(modules, diagnostics)
        return [(d.code, d.path) for d in diagnostics]

    def test_known_producer_volume_is_clean(self):
        modules = [
            self._module("dlt", ["dlt-state"]),
            self._module(
                "restic",
                ["restic-repo"],
                {"source-a": self._consumed_backup_source("dlt.backup-source", "dlt-dlt-state")},
            ),
        ]
        self.assertEqual(self._diags(modules), [])

    def test_unknown_producer_volume_reports_e146(self):
        modules = [
            self._module("dlt", ["dlt-state"]),
            self._module(
                "restic",
                ["restic-repo"],
                {"source-a": self._consumed_backup_source("dlt.backup-source", "dlt-typo")},
            ),
        ]
        self.assertEqual(
            self._diags(modules),
            [("E146", "module:restic.consumes.source-a")],
        )

    def test_bind_kind_source_is_not_volume_checked(self):
        consumed = {
            "contractRef": "duckdb.backup-source",
            "contract": {
                "kind": "backup-source",
                "spec": {"kind": "bind", "hostPath": "./data/duckdb"},
            },
        }
        modules = [
            self._module("duckdb", []),
            self._module("restic", [], {"source-a": consumed}),
        ]
        self.assertEqual(self._diags(modules), [])

    def test_non_backup_source_contracts_are_ignored(self):
        consumed = {
            "contractRef": "pg.sql-database",
            "contract": {"kind": "sql-database", "spec": {}},
        }
        modules = [self._module("pg", ["postgres-data"]), self._module("dag", [], {"db": consumed})]
        self.assertEqual(self._diags(modules), [])


if __name__ == "__main__":
    unittest.main()
