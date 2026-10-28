import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from cli import validator
from cli.validator import (
    CompatibilityRegistryError,
    _load_compatibility_registry,
    _module_key,
    has_errors,
    load_module_instances,
    validate_contract_bindings,
    validate_contract_document,
    validate_contract_file,
    validate_image_source_config,
    validate_loaded_profile,
    validate_module_configs,
    validate_observability_config,
    validate_outputs,
    validate_pinned_image_digests,
    validate_profile_shape,
    validate_secret_refs,
)


def _module(provides=None, consumes=None, category="cat", name="mod", schema=True):
    spec = {"provides": provides or [], "consumes": consumes or []}
    if schema:
        spec["configSchema"] = {"type": "object"}
    return {
        "kind": "Module",
        "metadata": {"category": category, "name": name},
        "spec": spec,
    }


def _inst(inst_id, module, config=None, index=0):
    return {
        "index": index,
        "id": inst_id,
        "config": config or {},
        "module": module,
    }


def _consume(**overrides):
    entry = {
        "name": "db",
        "contract": {"kind": "sql-database"},
        "mappedFrom": "spec.config.db",
    }
    entry.update(overrides)
    return entry


def _codes(diagnostics):
    return [(d.code, d.path) for d in diagnostics]


class ProfileShapeTest(unittest.TestCase):
    def test_shape_reports_schema_error_with_root_path(self):
        diagnostics = validate_profile_shape({})
        self.assertTrue(diagnostics)
        self.assertTrue(all(d.code == "E010" for d in diagnostics))

    def test_shape_reports_duplicate_module_id_and_skips_non_dict(self):
        profile = {
            "spec": {
                "modules": [
                    {"id": "a"},
                    "not-a-dict",
                    {"id": "a"},
                ]
            }
        }
        diagnostics = validate_profile_shape(profile)
        dupes = [d for d in diagnostics if d.code == "E011"]
        self.assertEqual([d.path for d in dupes], ["spec.modules[2].id"])

    def test_has_errors_ignores_warnings(self):
        from cli.diagnostics import Diagnostic

        self.assertFalse(has_errors([Diagnostic("warning", "W1", "m", "p")]))
        self.assertTrue(has_errors([Diagnostic("error", "E1", "m", "p")]))


class ContractDocumentTest(unittest.TestCase):
    def test_contract_document_invalid_uses_e023(self):
        diagnostics = validate_contract_document({})
        self.assertTrue(diagnostics)
        self.assertTrue(all(d.code == "E023" for d in diagnostics))
        self.assertIn("contract", [d.path for d in diagnostics])

    def test_contract_file_missing_returns_load_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            diagnostics = validate_contract_file(Path(tmpdir) / "missing.yaml")
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0].code, "E020")

    def test_contract_file_invalid_document_reports_e023(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "contract.yaml"
            path.write_text("foo: bar\n", encoding="utf-8")
            diagnostics = validate_contract_file(path)
        self.assertTrue(diagnostics)
        self.assertTrue(all(d.code == "E023" for d in diagnostics))


class LoadModuleInstancesTest(unittest.TestCase):
    def _profile(self, modules):
        return {"spec": {"modules": modules}}

    def _run(self, module_doc, module_instance=None):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            profile_dir = root / "profiles" / "p"
            module_dir = root / "modules" / "cat" / "mod"
            profile_dir.mkdir(parents=True)
            module_dir.mkdir(parents=True)
            (module_dir / "module.yaml").write_text(yaml.safe_dump(module_doc), encoding="utf-8")
            instance = {"id": "m", "source": "cat/mod", "config": {}}
            instance.update(module_instance or {})
            with patch.dict("os.environ", {"CDS_MODULE_PATH": str(root / "modules")}):
                return load_module_instances(profile_dir / "profile.yaml", self._profile([instance]))

    def test_wrong_kind_reports_e021_on_source_path(self):
        instances, diagnostics = self._run({"kind": "Profile"})
        self.assertEqual(instances, [])
        self.assertEqual(_codes(diagnostics), [("E021", "spec.modules[0].source")])

    def test_module_schema_errors_report_e021_under_module_path(self):
        instances, diagnostics = self._run({"kind": "Module"})
        self.assertEqual(instances, [])
        self.assertTrue(diagnostics)
        self.assertTrue(all(d.code == "E021" for d in diagnostics))
        self.assertTrue(all(d.path.startswith("spec.modules[0]") for d in diagnostics))

    def test_disabled_module_is_skipped(self):
        instances, diagnostics = self._run({"kind": "Profile"}, {"enabled": False})
        self.assertEqual(instances, [])
        self.assertEqual(diagnostics, [])

    def test_unresolvable_source_skips_module(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profile = self._profile([{"id": "m", "source": "modules/none", "config": {}}])
            with patch.dict("os.environ", {"CDS_MODULE_PATH": tmpdir}):
                instances, diagnostics = load_module_instances(Path(tmpdir) / "profile.yaml", profile)
        self.assertEqual(instances, [])
        self.assertTrue(has_errors(diagnostics))

    def test_unparseable_module_yaml_skips_module(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module_dir = root / "modules" / "cat" / "mod"
            module_dir.mkdir(parents=True)
            (module_dir / "module.yaml").write_text("a: [unclosed\n", encoding="utf-8")
            profile = self._profile([{"id": "m", "source": "cat/mod", "config": {}}])
            with patch.dict("os.environ", {"CDS_MODULE_PATH": str(root / "modules")}):
                instances, diagnostics = load_module_instances(root / "profile.yaml", profile)
        self.assertEqual(instances, [])
        self.assertTrue(has_errors(diagnostics))


class ModuleConfigAndSecretsTest(unittest.TestCase):
    def test_missing_config_schema_reports_e021(self):
        inst = _inst("m", _module(schema=False))
        diagnostics = validate_module_configs([inst])
        self.assertEqual(_codes(diagnostics), [("E021", "module:m.spec.configSchema")])

    def test_config_schema_violation_reports_e030_with_subpath(self):
        module = _module()
        module["spec"]["configSchema"] = {
            "type": "object",
            "properties": {"port": {"type": "integer"}},
        }
        inst = _inst("m", module, {"port": "x"}, index=3)
        diagnostics = validate_module_configs([inst])
        self.assertEqual(_codes(diagnostics), [("E030", "spec.modules[3].config.port")])

    def test_config_schema_root_violation_has_no_subpath(self):
        module = _module()
        module["spec"]["configSchema"] = {"type": "object", "required": ["x"]}
        diagnostics = validate_module_configs([_inst("m", module, {}, index=1)])
        self.assertEqual(_codes(diagnostics), [("E030", "spec.modules[1].config")])

    def test_unknown_secret_ref_in_nested_config_reports_e050(self):
        profile = {"spec": {"secrets": {"values": {"known": "CDS_KNOWN"}}}}
        config = {"a": {"b": ["secrets.known", "secrets.missing"]}, "c": "plain"}
        diagnostics = validate_secret_refs(profile, [_inst("m", _module(), config, index=2)])
        self.assertEqual(_codes(diagnostics), [("E050", "spec.modules[2].config.a.b[1]")])


class ContractBindingsTest(unittest.TestCase):
    def _bind(self, consume, config, providers=None, registry=None):
        consumer = _inst("app", _module(consumes=[consume], category="orch", name="app"), config)
        instances = [consumer] + (providers or [])
        if registry is None:
            return validate_contract_bindings(instances)
        with patch.object(validator, "_load_compatibility_registry", return_value=registry):
            return validate_contract_bindings(instances)

    def _provider(self, kind="sql-database", category="wh", name="pg"):
        return _inst(
            "pg",
            _module(provides=[{"name": "sql", "contract": {"kind": kind}}], category=category, name=name),
            index=1,
        )

    def test_registry_load_failure_reports_e092(self):
        with patch.object(validator, "_load_compatibility_registry", side_effect=CompatibilityRegistryError("boom")):
            diagnostics = validate_contract_bindings([])
        self.assertEqual(_codes(diagnostics), [("E092", "cli/resources/compatibility-registry.json")])

    def test_incomplete_consume_entry_reports_e021(self):
        diagnostics = self._bind({"name": "db"}, {})
        self.assertEqual(_codes(diagnostics), [("E021", "module:app.spec.consumes")])

    def test_malformed_required_if_reports_e021(self):
        diagnostics = self._bind(_consume(required=False, requiredIf="bogus"), {})
        self.assertEqual(_codes(diagnostics), [("E021", "module:app.spec.consumes")])

    def test_optional_unresolvable_binding_is_skipped(self):
        self.assertEqual(self._bind(_consume(required=False), {}), [])

    def test_required_unresolvable_binding_reports_e041(self):
        diagnostics = self._bind(_consume(), {})
        self.assertEqual(_codes(diagnostics), [("E041", "spec.modules[0].config")])

    def test_conditionally_required_unresolvable_binding_mentions_condition(self):
        diagnostics = self._bind(
            _consume(required=False, requiredIf="config.mode==on"),
            {"mode": "on"},
        )
        self.assertEqual(_codes(diagnostics), [("E041", "spec.modules[0].config")])
        self.assertIn("required because config.mode==on", diagnostics[0].message)

    def test_required_if_false_keeps_binding_optional(self):
        diagnostics = self._bind(
            _consume(required=False, requiredIf="config.mode==on"),
            {"mode": "off"},
        )
        self.assertEqual(diagnostics, [])

    def test_binding_without_contract_ref_reports_e041(self):
        diagnostics = self._bind(_consume(), {"db": {"other": 1}})
        self.assertEqual(_codes(diagnostics), [("E041", "spec.modules[0].config")])
        self.assertIn("contractRef", diagnostics[0].message)

    def test_conditionally_required_binding_without_contract_ref_mentions_condition(self):
        diagnostics = self._bind(
            _consume(required=False, requiredIf="config.mode==on"),
            {"mode": "on", "db": {"x": 1}},
        )
        self.assertEqual(_codes(diagnostics), [("E041", "spec.modules[0].config")])
        self.assertIn("required because", diagnostics[0].message)

    def test_optional_empty_binding_is_skipped(self):
        self.assertEqual(self._bind(_consume(required=False), {"db": {}}), [])

    def test_invalid_contract_ref_reports_e041(self):
        diagnostics = self._bind(_consume(), {"db": {"contractRef": "nodot"}})
        self.assertEqual(_codes(diagnostics), [("E041", "spec.modules[0].config")])
        self.assertIn("Invalid contract ref", diagnostics[0].message)

    def test_unknown_producer_reports_e041(self):
        diagnostics = self._bind(_consume(), {"db": {"contractRef": "ghost.sql"}})
        self.assertEqual(_codes(diagnostics), [("E041", "spec.modules[0].config")])
        self.assertIn("unknown module", diagnostics[0].message)

    def test_producer_without_named_contract_reports_e041(self):
        diagnostics = self._bind(
            _consume(), {"db": {"contractRef": "pg.other"}}, providers=[self._provider()]
        )
        self.assertEqual(_codes(diagnostics), [("E041", "spec.modules[0].config")])
        self.assertIn("does not provide", diagnostics[0].message)

    def test_kind_mismatch_reports_e042(self):
        diagnostics = self._bind(
            _consume(), {"db": {"contractRef": "pg.sql"}}, providers=[self._provider(kind="object-store")]
        )
        self.assertEqual(_codes(diagnostics), [("E042", "spec.modules[0].config")])

    def test_unsupported_registry_pairing_reports_e043(self):
        registry = {("sql-database", "wh/pg", "orch/app"): "unsupported"}
        diagnostics = self._bind(
            _consume(), {"db": {"contractRef": "pg.sql"}}, providers=[self._provider()], registry=registry
        )
        self.assertEqual(_codes(diagnostics), [("E043", "spec.modules[0].config")])

    def test_tested_registry_pairing_is_clean(self):
        registry = {("sql-database", "wh/pg", "orch/app"): "tested"}
        diagnostics = self._bind(
            _consume(), {"db": {"contractRef": "pg.sql"}}, providers=[self._provider()], registry=registry
        )
        self.assertEqual(diagnostics, [])


class CompatibilityRegistryTest(unittest.TestCase):
    def _load(self, registry):
        schema = {"type": "object"}
        with patch.object(validator, "_load_schema", side_effect=[schema, registry]):
            return _load_compatibility_registry()

    def test_schema_violation_raises_registry_error(self):
        with patch.object(
            validator,
            "_load_schema",
            side_effect=[{"type": "object", "required": ["pairings"]}, {}],
        ):
            with self.assertRaises(CompatibilityRegistryError) as ctx:
                _load_compatibility_registry()
        self.assertIn("validation failed", str(ctx.exception))

    def test_duplicate_pairing_raises_registry_error(self):
        pairing = {"contract": "k", "provider": "a/b", "consumer": "c/d", "status": "tested"}
        with self.assertRaises(CompatibilityRegistryError) as ctx:
            self._load({"pairings": [pairing, dict(pairing)]})
        self.assertIn("pairings[1]", str(ctx.exception))

    def test_valid_registry_builds_lookup(self):
        pairing = {"contract": "k", "provider": "a/b", "consumer": "c/d", "status": "unsupported"}
        self.assertEqual(self._load({"pairings": [pairing]}), {("k", "a/b", "c/d"): "unsupported"})

    def test_module_key_requires_category_and_name(self):
        self.assertEqual(_module_key({"metadata": {"category": "a", "name": "b"}}), "a/b")
        self.assertIsNone(_module_key({"metadata": {"category": "a"}}))
        self.assertIsNone(_module_key({}))

    def test_bundled_registry_loads(self):
        self.assertIsInstance(_load_compatibility_registry(), dict)


class PinnedImageDigestsTest(unittest.TestCase):
    def test_stale_pin_warns_once_per_image_and_skips_non_dict_module(self):
        entries = [{"image": "pg:18@sha256:old", "service": "db"}]
        instances = [
            _inst("a", _module()),
            _inst("b", _module()),
            {"index": 2, "id": "c", "config": {}, "module": None},
        ]
        result = {"status": "stale", "latest_digest": "sha256:new"}
        with (
            patch.object(validator, "collect_pinned_images_for_module", return_value=entries),
            patch.object(validator, "check_digest_staleness", return_value=result) as check,
        ):
            diagnostics = validate_pinned_image_digests(instances)
        self.assertEqual(check.call_count, 1)
        self.assertEqual(
            _codes(diagnostics),
            [
                ("W100", "module:a.spec.implementation.compose.services.db.image"),
                ("W100", "module:b.spec.implementation.compose.services.db.image"),
            ],
        )

    def test_non_stale_pin_is_ignored(self):
        with (
            patch.object(
                validator, "collect_pinned_images_for_module", return_value=[{"image": "i", "service": "s"}]
            ),
            patch.object(validator, "check_digest_staleness", return_value={"status": "current"}),
        ):
            self.assertEqual(validate_pinned_image_digests([_inst("a", _module())]), [])


class ImageSourceConfigTest(unittest.TestCase):
    def test_non_dict_config_and_non_registry_sources_are_skipped(self):
        instances = [
            {"index": 0, "id": "a", "config": None, "module": {}},
            _inst("b", _module(), {"image": "x"}),
            _inst("c", _module(), {"image": {"source": "build"}}),
        ]
        self.assertEqual(validate_image_source_config(instances), [])

    def test_registry_source_without_tag_reports_e103(self):
        inst = _inst("a", _module(), {"image": {"source": "registry"}}, index=4)
        self.assertEqual(
            _codes(validate_image_source_config([inst])), [("E103", "spec.modules[4].config.image.tag")]
        )

    def test_registry_source_with_latest_tag_warns(self):
        inst = _inst("a", _module(), {"image": {"source": "registry", "tag": "latest"}})
        diagnostics = validate_image_source_config([inst])
        self.assertEqual(_codes(diagnostics), [("W097", "spec.modules[0].config.image.tag")])
        self.assertEqual(diagnostics[0].level, "warning")


class OutputsTest(unittest.TestCase):
    def _outputs(self, contracts, instances=None):
        profile = {"spec": {"outputs": {"contracts": contracts}}}
        return validate_outputs(profile, instances or [])

    def test_non_dict_outputs_block_is_ignored(self):
        self.assertEqual(self._outputs(["x"]), [])

    def test_output_without_from_reports_e060(self):
        diagnostics = self._outputs({"o": {"nope": 1}})
        self.assertEqual(_codes(diagnostics), [("E060", "spec.outputs.contracts.o")])

    def test_invalid_ref_reports_e060(self):
        diagnostics = self._outputs({"o": {"from": "nodot"}})
        self.assertEqual(_codes(diagnostics), [("E060", "spec.outputs.contracts.o.from")])

    def test_unknown_module_reports_e060(self):
        diagnostics = self._outputs({"o": {"from": "ghost.sql"}})
        self.assertEqual(_codes(diagnostics), [("E060", "spec.outputs.contracts.o.from")])
        self.assertIn("unknown module", diagnostics[0].message)

    def test_missing_provide_reports_e060(self):
        inst = _inst("pg", _module(provides=[{"name": "sql", "contract": {"kind": "k"}}]))
        diagnostics = self._outputs({"o": {"from": "pg.other"}}, [inst])
        self.assertEqual(_codes(diagnostics), [("E060", "spec.outputs.contracts.o.from")])
        self.assertIn("does not provide", diagnostics[0].message)

    def test_valid_output_is_clean(self):
        inst = _inst("pg", _module(provides=[{"name": "sql", "contract": {"kind": "k"}}]))
        self.assertEqual(self._outputs({"o": {"from": "pg.sql"}}, [inst]), [])


class ObservabilityTest(unittest.TestCase):
    def _check(self, observability, instances=None):
        profile = {"spec": {"observability": observability}}
        return validate_observability_config(profile, instances or [])

    def _shipping(self, **shipping):
        return {"logShipping": shipping}

    def test_absent_observability_is_clean(self):
        self.assertEqual(validate_observability_config({"spec": {}}, []), [])

    def test_non_dict_observability_reports_e100(self):
        self.assertEqual(_codes(self._check("x")), [("E100", "spec.observability")])

    def test_missing_log_shipping_is_clean(self):
        self.assertEqual(self._check({}), [])

    def test_non_dict_log_shipping_reports_e100(self):
        self.assertEqual(
            _codes(self._check({"logShipping": "x"})), [("E100", "spec.observability.logShipping")]
        )

    def test_missing_enabled_reports_e100(self):
        self.assertEqual(
            _codes(self._check(self._shipping())), [("E100", "spec.observability.logShipping.enabled")]
        )

    def test_non_boolean_enabled_reports_e100(self):
        self.assertEqual(
            _codes(self._check(self._shipping(enabled="yes"))),
            [("E100", "spec.observability.logShipping.enabled")],
        )

    def test_non_dict_retention_reports_e100(self):
        diagnostics = self._check(self._shipping(enabled=True, retention=5))
        self.assertEqual(_codes(diagnostics), [("E100", "spec.observability.logShipping.retention")])

    def test_invalid_retention_values_report_e101(self):
        diagnostics = self._check(
            self._shipping(enabled=True, retention={"rawDays": 0, "structuredDays": True})
        )
        self.assertEqual(
            _codes(diagnostics),
            [
                ("E101", "spec.observability.logShipping.retention.rawDays"),
                ("E101", "spec.observability.logShipping.retention.structuredDays"),
            ],
        )

    def test_structured_days_below_raw_days_reports_e101(self):
        diagnostics = self._check(
            self._shipping(enabled=True, retention={"rawDays": 30, "structuredDays": 7})
        )
        self.assertEqual(
            _codes(diagnostics), [("E101", "spec.observability.logShipping.retention.structuredDays")]
        )

    def test_valid_retention_without_sink_is_clean(self):
        diagnostics = self._check(
            self._shipping(enabled=True, retention={"rawDays": 7, "structuredDays": 30})
        )
        self.assertEqual(diagnostics, [])

    def test_sink_without_string_contract_ref_reports_e100(self):
        for sink in ("x", {"contractRef": 3}, {}):
            with self.subTest(sink=sink):
                diagnostics = self._check(self._shipping(enabled=True, sink=sink))
                self.assertEqual(_codes(diagnostics), [("E100", "spec.observability.logShipping.sink")])

    def test_invalid_sink_ref_reports_e102(self):
        diagnostics = self._check(self._shipping(enabled=True, sink={"contractRef": "nodot"}))
        self.assertEqual(
            _codes(diagnostics), [("E102", "spec.observability.logShipping.sink.contractRef")]
        )

    def test_unknown_sink_module_reports_e102(self):
        diagnostics = self._check(self._shipping(enabled=True, sink={"contractRef": "ghost.logs"}))
        self.assertEqual(
            _codes(diagnostics), [("E102", "spec.observability.logShipping.sink.contractRef")]
        )
        self.assertIn("unknown module", diagnostics[0].message)

    def test_sink_missing_provide_reports_e102(self):
        inst = _inst("loki", _module(provides=[{"name": "logs", "contract": {"kind": "log-sink"}}]))
        diagnostics = self._check(
            self._shipping(enabled=True, sink={"contractRef": "loki.other"}), [inst]
        )
        self.assertEqual(
            _codes(diagnostics), [("E102", "spec.observability.logShipping.sink.contractRef")]
        )
        self.assertIn("does not provide", diagnostics[0].message)

    def test_sink_wrong_kind_reports_e102(self):
        inst = _inst("loki", _module(provides=[{"name": "logs", "contract": {"kind": "sql-database"}}]))
        diagnostics = self._check(
            self._shipping(enabled=True, sink={"contractRef": "loki.logs"}), [inst]
        )
        self.assertEqual(
            _codes(diagnostics), [("E102", "spec.observability.logShipping.sink.contractRef")]
        )
        self.assertIn('expected "log-sink"', diagnostics[0].message)

    def test_valid_sink_is_clean(self):
        inst = _inst("loki", _module(provides=[{"name": "logs", "contract": {"kind": "log-sink"}}]))
        diagnostics = self._check(
            self._shipping(enabled=True, sink={"contractRef": "loki.logs"}), [inst]
        )
        self.assertEqual(diagnostics, [])


class LoadedProfilePipelineTest(unittest.TestCase):
    def test_shape_errors_stop_pipeline_before_module_loading(self):
        with patch.object(validator, "load_module_instances") as load:
            diagnostics = validate_loaded_profile({}, Path("profile.yaml"))
        load.assert_not_called()
        self.assertTrue(all(d.code == "E010" for d in diagnostics))

    def test_module_load_errors_stop_pipeline(self):
        from cli.diagnostics import Diagnostic

        profile = {"spec": {"modules": []}}
        with (
            patch.object(validator, "validate_profile_shape", return_value=[]),
            patch.object(
                validator,
                "load_module_instances",
                return_value=([], [Diagnostic("error", "E021", "m", "p")]),
            ),
            patch.object(validator, "validate_module_configs") as configs,
        ):
            diagnostics = validate_loaded_profile(profile, Path("profile.yaml"))
        configs.assert_not_called()
        self.assertEqual(_codes(diagnostics), [("E021", "p")])


if __name__ == "__main__":
    unittest.main()


class BackupConfigTest(unittest.TestCase):
    def _policy(self, workload_class, **overrides):
        policy = {
            "workloadClass": workload_class,
            "retentionDays": 30,
            "rpoMinutes": 60,
            "rtoMinutes": 240,
            "restoreVerification": {"frequencyDays": 30},
        }
        policy.update(overrides)
        return policy

    def _profile(self, backup=None, environment="production", secrets=("backup_key",), required=True):
        spec = {
            "secrets": {
                "values": {alias: {"env": "CDS_X", "required": required} for alias in secrets}
            }
        }
        if backup is not None:
            spec["backup"] = backup
        return {"metadata": {"environment": environment}, "spec": spec}

    def _instances(self):
        return [
            _inst("pg", _module(category="warehouse")),
            _inst("dag", _module(category="orchestration")),
            _inst("bi", _module(category="bi")),
        ]

    def _full_backup(self):
        return {
            "encryption": {"keyFrom": "secrets.backup_key"},
            "policies": [
                self._policy(c) for c in ("warehouse", "orchestration-metadata", "bi-metadata", "configuration")
            ],
        }

    def test_required_classes_follow_module_categories(self):
        self.assertEqual(
            validator.required_backup_workload_classes(self._instances()),
            ["bi-metadata", "configuration", "orchestration-metadata", "warehouse"],
        )

    def test_disabled_modules_do_not_require_a_class(self):
        inst = _inst("bi", _module(category="bi"))
        inst["instance"] = {"enabled": False}
        self.assertEqual(validator.required_backup_workload_classes([inst]), ["configuration"])

    def test_non_production_without_backup_is_clean(self):
        profile = self._profile(environment="local")
        self.assertEqual(validator.validate_backup_config(profile, self._instances()), [])

    def test_production_without_backup_reports_e133(self):
        diagnostics = validator.validate_backup_config(self._profile(), self._instances())
        self.assertEqual(_codes(diagnostics), [("E133", "spec.backup")])

    def test_complete_production_backup_is_clean(self):
        profile = self._profile(self._full_backup())
        self.assertEqual(validator.validate_backup_config(profile, self._instances()), [])

    def test_production_missing_class_reports_e133(self):
        backup = self._full_backup()
        backup["policies"] = [p for p in backup["policies"] if p["workloadClass"] != "warehouse"]
        diagnostics = validator.validate_backup_config(self._profile(backup), self._instances())
        self.assertEqual(_codes(diagnostics), [("E133", "spec.backup.policies")])
        self.assertIn("warehouse", diagnostics[0].message)

    def test_production_without_encryption_reports_e134(self):
        backup = self._full_backup()
        del backup["encryption"]
        diagnostics = validator.validate_backup_config(self._profile(backup), self._instances())
        self.assertEqual(_codes(diagnostics), [("E134", "spec.backup.encryption")])

    def test_production_without_restore_verification_reports_e135(self):
        backup = self._full_backup()
        del backup["policies"][0]["restoreVerification"]
        diagnostics = validator.validate_backup_config(self._profile(backup), self._instances())
        self.assertEqual(_codes(diagnostics), [("E135", "spec.backup.policies[0].restoreVerification")])

    def test_duplicate_workload_class_reports_e130(self):
        backup = {"policies": [self._policy("warehouse"), self._policy("warehouse")]}
        diagnostics = validator.validate_backup_config(self._profile(backup, environment="local"), [])
        self.assertEqual(_codes(diagnostics), [("E130", "spec.backup.policies[1].workloadClass")])

    def test_unknown_encryption_secret_reports_e131(self):
        backup = {"encryption": {"keyFrom": "secrets.ghost"}, "policies": [self._policy("warehouse")]}
        diagnostics = validator.validate_backup_config(self._profile(backup, environment="local"), [])
        self.assertEqual(_codes(diagnostics), [("E131", "spec.backup.encryption.keyFrom")])

    def _target(self, ref, instances=None):
        backup = {"target": {"contractRef": ref}, "policies": [self._policy("warehouse")]}
        return validator.validate_backup_config(self._profile(backup, environment="local"), instances or [])

    def test_target_invalid_unknown_and_missing_report_e132(self):
        provider = _inst("repo", _module(provides=[{"name": "t", "contract": {"kind": "backup-target"}}]))
        self.assertEqual(_codes(self._target("nodot")), [("E132", "spec.backup.target.contractRef")])
        self.assertIn("unknown module", self._target("ghost.t")[0].message)
        self.assertIn("does not provide", self._target("repo.other", [provider])[0].message)

    def test_target_wrong_kind_reports_e132(self):
        provider = _inst("repo", _module(provides=[{"name": "t", "contract": {"kind": "sql-database"}}]))
        diagnostics = self._target("repo.t", [provider])
        self.assertEqual(_codes(diagnostics), [("E132", "spec.backup.target.contractRef")])
        self.assertIn("expected \"backup-target\"", diagnostics[0].message)

    def test_valid_target_is_clean(self):
        provider = _inst("repo", _module(provides=[{"name": "t", "contract": {"kind": "backup-target"}}]))
        self.assertEqual(self._target("repo.t", [provider]), [])

    def test_identity_and_secrets_categories_require_classes(self):
        instances = [
            _inst("kc", _module(category="identity")),
            _inst("vault", _module(category="secrets")),
        ]
        self.assertEqual(
            validator.required_backup_workload_classes(instances),
            ["configuration", "identity-metadata", "secrets-metadata"],
        )

    def _capable_target(self, *, encrypted=True, supports_restore=True):
        spec = {
            "endpoint": "repo",
            "repository": "b",
            "protocol": "s3",
        }
        if encrypted is not None:
            spec["encrypted"] = encrypted
        if supports_restore is not None:
            spec["supportsRestoreVerification"] = supports_restore
        return _inst(
            "repo",
            _module(provides=[{"name": "t", "contract": {"kind": "backup-target", "spec": spec}}]),
        )

    def _production_backup_with_target(self, provider, **kwargs):
        backup = {
            "target": {"contractRef": "repo.t"},
            "encryption": {"keyFrom": "secrets.backup_key"},
            "policies": [
                self._policy(c)
                for c in ("warehouse", "orchestration-metadata", "bi-metadata", "configuration")
            ],
        }
        instances = self._instances() + [provider]
        return validator.validate_backup_config(self._profile(backup, **kwargs), instances)

    def test_production_target_without_encryption_advertisement_reports_e132(self):
        provider = self._capable_target(encrypted=False, supports_restore=None)
        diagnostics = self._production_backup_with_target(provider)
        codes = {d.code for d in diagnostics}
        self.assertIn("E132", codes)
        self.assertIn("does not advertise client-side encryption", " ".join(d.message for d in diagnostics))

    def test_production_target_without_restore_advertisement_reports_e132(self):
        provider = self._capable_target(encrypted=True, supports_restore=False)
        diagnostics = self._production_backup_with_target(provider)
        self.assertIn("E132", {d.code for d in diagnostics})
        self.assertIn("does not advertise restore verification", " ".join(d.message for d in diagnostics))

    def test_production_capable_target_is_clean(self):
        provider = self._capable_target()
        self.assertEqual(self._production_backup_with_target(provider), [])

    def test_production_target_capability_from_config_reference(self):
        provider = self._capable_target(encrypted="${config.tlsEnabled}", supports_restore="${config.restoreOk}")
        provider["config"] = {"tlsEnabled": True, "restoreOk": "true"}
        self.assertEqual(self._production_backup_with_target(provider), [])

        provider["config"] = {"tlsEnabled": False, "restoreOk": True}
        diagnostics = self._production_backup_with_target(provider)
        self.assertIn("E132", {d.code for d in diagnostics})

    def test_production_optional_encryption_alias_reports_e134(self):
        backup = self._full_backup()
        diagnostics = validator.validate_backup_config(
            self._profile(backup, required=False), self._instances()
        )
        self.assertIn("E134", {d.code for d in diagnostics})
        self.assertIn("required: true", " ".join(d.message for d in diagnostics))

    def test_schema_rejects_inline_key_material_and_bad_bounds(self):
        profile = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Profile",
            "metadata": {"name": "p", "environment": "local"},
            "spec": {
                "runtime": {"type": "docker-compose"},
                "modules": [{"id": "a", "source": "x", "version": "1", "enabled": True, "config": {}}],
                "backup": {
                    "encryption": {"keyFrom": "hunter2-literal-key"},
                    "policies": [self._policy("warehouse", rpoMinutes=0), self._policy("nope")],
                },
            },
        }
        paths = {d.path for d in validate_profile_shape(profile)}
        self.assertTrue(
            {
                "spec.backup.encryption.keyFrom",
                "spec.backup.policies.0.rpoMinutes",
                "spec.backup.policies.1.workloadClass",
            }
            <= paths
        )


class AuthenticationConfigTest(unittest.TestCase):
    """Production database/cache authentication policy (issue #206)."""

    def _profile(self, environment="production", secrets=None):
        values = {
            "db_password": {"env": "CDS_DB_PASSWORD", "required": True},
            "cache_password": {"env": "CDS_CACHE_PASSWORD", "required": True},
        }
        values.update(secrets or {})
        return {
            "metadata": {"environment": environment},
            "spec": {"secrets": {"values": values}},
        }

    def _sql_module(self, advertisement):
        spec = {"authentication": "${config.authentication.hostAuth}"} if advertisement else {}
        provides = [{"name": "sql-database", "contract": {"kind": "sql-database", "spec": spec}}]
        return _module(category="warehouse", name="pg", provides=provides)

    def _cache_module(self, advertisement=True):
        spec = {"authentication": "${ifNonempty:config.passwordFrom,required,}"} if advertisement else {}
        provides = [{"name": "cache-service", "contract": {"kind": "cache-service", "spec": spec}}]
        return _module(category="cache", name="keydb", provides=provides)

    def test_non_production_is_untouched(self):
        profile = self._profile(environment="local")
        unauthenticated_cache = _inst("keydb", self._cache_module())
        missing_ad = _inst("pg", self._sql_module(advertisement=True))
        self.assertEqual(validator.validate_authentication_config(profile, [unauthenticated_cache, missing_ad]), [])

    def test_sql_database_trust_reports_e139(self):
        inst = _inst("pg", self._sql_module(True), {"authentication": {"hostAuth": "trust"}})
        diagnostics = validator.validate_authentication_config(self._profile(), [inst])
        self.assertEqual(_codes(diagnostics), [("E139", "spec.modules[0].config")])
        self.assertIn("trust", diagnostics[0].message)

    def test_sql_database_without_advertisement_reports_e139(self):
        inst = _inst("pg", self._sql_module(advertisement=None))
        diagnostics = validator.validate_authentication_config(self._profile(), [inst])
        self.assertEqual(_codes(diagnostics), [("E139", "spec.modules[0].config")])

    def test_sql_database_config_driven_advertisement_fails_closed_when_unset(self):
        inst = _inst("pg", self._sql_module(advertisement=True))
        diagnostics = validator.validate_authentication_config(self._profile(), [inst])
        self.assertEqual(_codes(diagnostics), [("E139", "spec.modules[0].config")])

    def test_sql_database_legacy_md5_warns(self):
        inst = _inst("pg", self._sql_module(True), {"authentication": {"hostAuth": "md5"}})
        diagnostics = validator.validate_authentication_config(self._profile(), [inst])
        self.assertEqual(_codes(diagnostics), [("W105", "spec.modules[0].config")])

    def test_sql_database_scram_is_clean(self):
        config = {"authentication": {"hostAuth": "scram-sha-256"}, "passwordFrom": "secrets.db_password"}
        inst = _inst("pg", self._sql_module(True), config)
        self.assertEqual(validator.validate_authentication_config(self._profile(), [inst]), [])

    def test_cache_without_password_reports_e137(self):
        inst = _inst("keydb", self._cache_module())
        diagnostics = validator.validate_authentication_config(self._profile(), [inst])
        self.assertEqual(_codes(diagnostics), [("E137", "spec.modules[0].config")])

    def test_cache_with_required_secret_is_clean(self):
        inst = _inst("keydb", self._cache_module(), {"passwordFrom": "secrets.cache_password"})
        self.assertEqual(validator.validate_authentication_config(self._profile(), [inst]), [])

    def test_cache_without_advertisement_reports_e137(self):
        inst = _inst("keydb", self._cache_module(advertisement=False), {"passwordFrom": "secrets.cache_password"})
        diagnostics = validator.validate_authentication_config(self._profile(), [inst])
        self.assertEqual(_codes(diagnostics), [("E137", "spec.modules[0].config")])

    def test_optional_secret_alias_reports_e138(self):
        profile = self._profile(secrets={"cache_password": {"env": "CDS_CACHE_PASSWORD"}})
        inst = _inst("keydb", self._cache_module(), {"passwordFrom": "secrets.cache_password"})
        diagnostics = validator.validate_authentication_config(profile, [inst])
        self.assertEqual(_codes(diagnostics), [("E138", "spec.modules[0].config.passwordFrom")])
        self.assertIn("required", diagnostics[0].message)

    def test_plaintext_credential_reports_e138(self):
        inst = _inst("keydb", self._cache_module(True), {"passwordFrom": "hunter2"})
        diagnostics = validator.validate_authentication_config(self._profile(), [inst])
        self.assertEqual(_codes(diagnostics), [("E138", "spec.modules[0].config.passwordFrom")])
        self.assertIn("plaintext", diagnostics[0].message)

    def test_profile_input_credential_reports_e138(self):
        inst = _inst("keydb", self._cache_module(True), {"passwordFrom": "${CDS_CACHE_PASSWORD}"})
        diagnostics = validator.validate_authentication_config(self._profile(), [inst])
        self.assertEqual(_codes(diagnostics), [("E138", "spec.modules[0].config.passwordFrom")])
        self.assertIn("profile input", diagnostics[0].message)

    def test_empty_credential_reports_e138(self):
        inst = _inst("keydb", self._cache_module(True), {"passwordFrom": ""})
        diagnostics = validator.validate_authentication_config(self._profile(), [inst])
        # An empty credential also leaves the cache unauthenticated (E137).
        self.assertEqual(
            _codes(diagnostics),
            [
                ("E137", "spec.modules[0].config"),
                ("E138", "spec.modules[0].config.passwordFrom"),
            ],
        )
        self.assertIn("empty", diagnostics[-1].message)

    def test_indirect_config_reference_credential_is_clean(self):
        config = {
            "authentication": {"hostAuth": "scram-sha-256"},
            "passwordFrom": "secrets.db_password",
            "initDbEnv": {"ANALYTICS_DB_PASSWORD": "config.passwordFrom"},
        }
        inst = _inst("pg", self._sql_module(True), config)
        self.assertEqual(validator.validate_authentication_config(self._profile(), [inst]), [])

    def test_unrelated_providers_are_untouched(self):
        provides = [{"name": "http-service", "contract": {"kind": "http-service", "spec": {}}}]
        inst = _inst("web", _module(provides=provides), {"adminToken": "hunter2"})
        self.assertEqual(validator.validate_authentication_config(self._profile(), [inst]), [])

    def test_disabled_modules_are_still_validated(self):
        # load_module_instances already skips enabled: false instances, so
        # the policy never sees them; this documents that the function
        # itself has no environment-based opt-out beyond metadata.environment.
        profile = self._profile(environment="development")
        inst = _inst("keydb", self._cache_module())
        self.assertEqual(validator.validate_authentication_config(profile, [inst]), [])


class WebServingModeTest(unittest.TestCase):
    """Production web-serving policy (issue #216)."""

    def _profile(self, environment="production"):
        return {"metadata": {"environment": environment}, "spec": {}}

    def _web_module(self):
        return _module(category="bi", name="superset")

    def test_development_mode_is_rejected_in_production(self):
        inst = _inst("superset", self._web_module(), {"webServer": {"mode": "development"}})
        diagnostics = validator.validate_web_serving_mode(self._profile(), [inst])
        self.assertEqual(_codes(diagnostics), [("E140", "spec.modules[0].config.webServer.mode")])
        self.assertIn("development web servers are prohibited", diagnostics[0].message)

    def test_production_mode_is_clean(self):
        inst = _inst("superset", self._web_module(), {"webServer": {"mode": "production"}})
        self.assertEqual(validator.validate_web_serving_mode(self._profile(), [inst]), [])

    def test_unset_mode_uses_the_module_default_and_is_clean(self):
        # Validation runs before configSchema defaults apply: an absent
        # webServer.mode means the module default (production), never the
        # development server.
        inst = _inst("superset", self._web_module())
        self.assertEqual(validator.validate_web_serving_mode(self._profile(), [inst]), [])

    def test_development_mode_is_allowed_outside_production(self):
        inst = _inst("superset", self._web_module(), {"webServer": {"mode": "development"}})
        self.assertEqual(
            validator.validate_web_serving_mode(self._profile(environment="local"), [inst]), []
        )

    def test_non_string_and_placeholder_modes_pass(self):
        # Only a literal "development" is rejected: a ${CDS_*} placeholder
        # cannot be resolved at validation time and profile inputs are
        # checked elsewhere (E138-style credential checks).
        inst = _inst("superset", self._web_module(), {"webServer": {"mode": "${CDS_WEB_MODE}"}})
        self.assertEqual(validator.validate_web_serving_mode(self._profile(), [inst]), [])

    def test_non_dict_web_server_block_is_ignored(self):
        inst = _inst("superset", self._web_module(), {"webServer": "development"})
        self.assertEqual(validator.validate_web_serving_mode(self._profile(), [inst]), [])


class ModuleBackupConfigTest(unittest.TestCase):
    """E141/E143: the built-in backup capability is production-only and
    requires an encryption key."""

    def _inst(self, backup):
        return _inst("pg", _module(category="warehouse"), config={"backup": backup})

    def _profile(self, environment="production"):
        return {"metadata": {"environment": environment}, "spec": {}}

    def test_disabled_backup_is_clean(self):
        self.assertEqual(
            validator.validate_module_backup_config(
                self._profile(), [self._inst({"enabled": False})]
            ),
            [],
        )

    def test_enabled_without_key_reports_e141(self):
        diagnostics = validator.validate_module_backup_config(
            self._profile(), [self._inst({"enabled": True})]
        )
        self.assertEqual(
            _codes(diagnostics),
            [("E141", "spec.modules[0].config.backup.encryptionKeyFrom")],
        )

    def test_enabled_with_non_secret_key_reports_e141(self):
        diagnostics = validator.validate_module_backup_config(
            self._profile(),
            [self._inst({"enabled": True, "encryptionKeyFrom": "backup-key"})],
        )
        self.assertEqual(
            _codes(diagnostics),
            [("E141", "spec.modules[0].config.backup.encryptionKeyFrom")],
        )

    def test_enabled_with_secret_ref_is_clean(self):
        self.assertEqual(
            validator.validate_module_backup_config(
                self._profile(),
                [self._inst({"enabled": True, "encryptionKeyFrom": "secrets.backup_key"})],
            ),
            [],
        )

    def test_absent_backup_block_is_clean(self):
        self.assertEqual(
            validator.validate_module_backup_config(self._profile(), [self._inst({})]),
            [],
        )

    def test_backup_outside_production_reports_e143(self):
        # The backup pieces are production-only (`production: true`), so
        # enabling backup on a learning profile would silently render no
        # backup; validation fails closed instead.
        diagnostics = validator.validate_module_backup_config(
            self._profile(environment="local"),
            [self._inst({"enabled": True, "encryptionKeyFrom": "secrets.backup_key"})],
        )
        self.assertEqual(
            _codes(diagnostics),
            [("E143", "spec.modules[0].config.backup.enabled")],
        )


class BackupModuleCategoryTest(unittest.TestCase):
    """E143: backup modules (metadata.category "backup", issue #668) are
    production-only pieces; instantiating one in a non-production profile
    would silently render no backup at all, so validation fails closed."""

    def _inst(self, category="backup"):
        return _inst("restic", _module(category=category))

    def _profile(self, environment="production"):
        return {"metadata": {"environment": environment}, "spec": {}}

    def test_backup_module_in_production_is_clean(self):
        self.assertEqual(
            validator.validate_module_backup_config(self._profile(), [self._inst()]),
            [],
        )

    def test_backup_module_outside_production_reports_e143(self):
        diagnostics = validator.validate_module_backup_config(
            self._profile(environment="local"), [self._inst()]
        )
        self.assertEqual(
            _codes(diagnostics),
            [("E143", "spec.modules[0].id")],
        )

    def test_non_backup_category_module_is_clean_outside_production(self):
        self.assertEqual(
            validator.validate_module_backup_config(
                self._profile(environment="local"),
                [self._inst(category="warehouse")],
            ),
            [],
        )


class ProductionMarkerShapeTest(unittest.TestCase):
    """E144: the production mapping form is only valid on services, not on
    individual volume-mount entries, where the renderer would silently
    leak it into the rendered output."""

    def _module(self, volumes):
        return {
            "kind": "Module",
            "metadata": {"category": "warehouse", "name": "mod"},
            "spec": {
                "provides": [],
                "consumes": [],
                "configSchema": {"type": "object"},
                "implementation": {
                    "kind": "docker-compose",
                    "compose": {
                        "services": {
                            "db": {"image": "postgres:18", "volumes": volumes},
                        },
                        "volumes": {"data": {}},
                    },
                },
            },
        }

    def test_volume_item_without_marker_is_clean(self):
        diagnostics = validator.validate_module_marker_shapes(
            [_inst("db", self._module([{"type": "volume", "source": "data", "target": "/data"}]))]
        )
        self.assertEqual(diagnostics, [])

    def test_volume_item_with_boolean_marker_is_clean(self):
        diagnostics = validator.validate_module_marker_shapes(
            [
                _inst(
                    "db",
                    self._module(
                        [
                            {
                                "type": "volume",
                                "source": "data",
                                "target": "/data",
                                "production": True,
                            }
                        ]
                    ),
                )
            ]
        )
        self.assertEqual(diagnostics, [])

    def test_volume_item_with_mapping_marker_reports_e144(self):
        diagnostics = validator.validate_module_marker_shapes(
            [
                _inst(
                    "db",
                    self._module(
                        [
                            {
                                "type": "volume",
                                "source": "data",
                                "target": "/data",
                                "production": {"read_only": True},
                            }
                        ]
                    ),
                )
            ]
        )
        self.assertEqual(
            _codes(diagnostics),
            [
                (
                    "E144",
                    "module:db.spec.implementation.compose.services.db.volumes[0].production",
                )
            ],
        )
        self.assertIn("mapping form", diagnostics[0].message)

    def test_volume_item_inside_production_mapping_with_mapping_marker_reports_e144(self):
        module = self._module([])
        module["spec"]["implementation"]["compose"]["services"]["db"]["production"] = {
            "volumes": [
                {
                    "type": "volume",
                    "source": "data",
                    "target": "/data",
                    "production": {"read_only": True},
                }
            ]
        }
        diagnostics = validator.validate_module_marker_shapes([_inst("db", module)])
        self.assertEqual(
            _codes(diagnostics),
            [
                (
                    "E144",
                    "module:db.spec.implementation.compose.services.db."
                    "production.volumes[0].production",
                )
            ],
        )

    def test_volume_item_inside_production_mapping_with_boolean_marker_is_clean(self):
        module = self._module([])
        module["spec"]["implementation"]["compose"]["services"]["db"]["production"] = {
            "volumes": [
                {
                    "type": "volume",
                    "source": "data",
                    "target": "/data",
                    "production": True,
                }
            ]
        }
        diagnostics = validator.validate_module_marker_shapes([_inst("db", module)])
        self.assertEqual(diagnostics, [])

    def test_service_level_mapping_marker_is_clean(self):
        module = self._module(
            [{"type": "volume", "source": "data", "target": "/data"}]
        )
        module["spec"]["implementation"]["compose"]["services"]["db"]["production"] = {
            "image": "postgres:18-alpine"
        }
        diagnostics = validator.validate_module_marker_shapes([_inst("db", module)])
        self.assertEqual(diagnostics, [])

    def test_service_string_marker_reports_e144(self):
        module = self._module([])
        module["spec"]["implementation"]["compose"]["services"]["db"]["production"] = "true"
        diagnostics = validator.validate_module_marker_shapes([_inst("db", module)])
        self.assertEqual(
            _codes(diagnostics),
            [("E144", "module:db.spec.implementation.compose.services.db.production")],
        )
        self.assertIn("boolean", diagnostics[0].message)

    def test_volume_definition_string_marker_reports_e144(self):
        module = self._module([])
        module["spec"]["implementation"]["compose"]["volumes"]["data"] = {
            "production": "true"
        }
        diagnostics = validator.validate_module_marker_shapes([_inst("db", module)])
        self.assertEqual(
            _codes(diagnostics),
            [("E144", "module:db.spec.implementation.compose.volumes.data.production")],
        )
        self.assertIn("boolean", diagnostics[0].message)

    def test_marker_on_other_list_entries_reports_e144(self):
        # No marker is implemented on entries of other service list fields;
        # any marker (boolean or mapping) fails closed there.
        for marker in ({"read_only": True}, True):
            with self.subTest(marker=marker):
                module = self._module([])
                module["spec"]["implementation"]["compose"]["services"]["db"]["ports"] = [
                    {"target": 5432, "production": marker}
                ]
                diagnostics = validator.validate_module_marker_shapes([_inst("db", module)])
                self.assertEqual(
                    _codes(diagnostics),
                    [("E144", "module:db.spec.implementation.compose.services.db.ports[0].production")],
                )
                self.assertIn("only implemented on services", diagnostics[0].message)

    def test_marker_in_production_mapping_list_entry_reports_e144(self):
        module = self._module([])
        module["spec"]["implementation"]["compose"]["services"]["db"]["production"] = {
            "ports": [{"target": 5432, "production": True}]
        }
        diagnostics = validator.validate_module_marker_shapes([_inst("db", module)])
        self.assertEqual(
            _codes(diagnostics),
            [
                (
                    "E144",
                    "module:db.spec.implementation.compose.services.db."
                    "production.ports[0].production",
                )
            ],
        )

    def test_mount_string_marker_message_names_boolean_not_mapping(self):
        module = self._module(
            [{"type": "volume", "source": "data", "target": "/data", "production": "true"}]
        )
        diagnostics = validator.validate_module_marker_shapes([_inst("db", module)])
        self.assertEqual(len(diagnostics), 1)
        self.assertIn("must be a boolean", diagnostics[0].message)
        self.assertNotIn("mapping form", diagnostics[0].message)

    def test_e144_flows_through_validate_loaded_profile(self):
        # End-to-end wiring: the rule is part of the validation pipeline,
        # so an offending template surfaces E144 with its precise path.
        module = self._module(
            [{"type": "volume", "source": "data", "target": "/data", "production": {"r": 1}}]
        )
        inst = _inst("db", module)
        with (
            patch.object(validator, "validate_profile_shape", return_value=[]),
            patch.object(validator, "load_module_instances", return_value=([inst], [])),
        ):
            diagnostics = validate_loaded_profile({"spec": {"modules": []}}, Path("profile.yaml"))
        self.assertTrue(
            any(
                d.code == "E144"
                and d.path
                == "module:db.spec.implementation.compose.services.db.volumes[0].production"
                for d in diagnostics
            )
        )


class EnabledFromShapeTest(unittest.TestCase):
    """E145: `enabledFrom` / `conditionallyEnabledFrom` must be non-empty
    strings on the positions where the marker is implemented; anything
    else crashes the renderer (a boolean `enabledFrom: true` raises
    TypeError in _resolve_expr), and no marker is implemented on entries
    of other service list fields."""

    def _module(self, service_overrides=None, volumes=None):
        service = {"image": "postgres:18", "volumes": volumes or []}
        service.update(service_overrides or {})
        return {
            "kind": "Module",
            "metadata": {"category": "warehouse", "name": "mod"},
            "spec": {
                "provides": [],
                "consumes": [],
                "configSchema": {"type": "object"},
                "implementation": {
                    "kind": "docker-compose",
                    "compose": {
                        "services": {"db": service},
                        "volumes": {"data": {}},
                    },
                },
            },
        }

    def _scan(self, module):
        return validator.validate_module_marker_shapes([_inst("db", module)])

    def test_string_condition_on_service_and_mount_is_clean(self):
        module = self._module(
            {"enabledFrom": "config.backup.enabled"},
            [{"type": "volume", "source": "data", "target": "/data",
              "enabledFrom": "config.storage.enabled"}],
        )
        self.assertEqual(self._scan(module), [])

    def test_bool_condition_on_service_reports_e145(self):
        # The plausible authoring mistake (`production: true` is a boolean
        # gate, so writers try `enabledFrom: true`): crashes the renderer
        # today, fails closed with E145 instead.
        module = self._module({"enabledFrom": True})
        diagnostics = self._scan(module)
        self.assertEqual(
            _codes(diagnostics),
            [("E145", "module:db.spec.implementation.compose.services.db.enabledFrom")],
        )
        self.assertIn("non-empty string", diagnostics[0].message)

    def test_bool_condition_on_mount_entry_reports_e145(self):
        module = self._module(
            volumes=[{"type": "volume", "source": "data", "target": "/data",
                      "enabledFrom": True}]
        )
        self.assertEqual(
            _codes(self._scan(module)),
            [("E145", "module:db.spec.implementation.compose.services.db.volumes[0].enabledFrom")],
        )

    def test_bool_conditionally_enabled_from_on_healthcheck_reports_e145(self):
        module = self._module(
            {"healthcheck": {"conditionallyEnabledFrom": True, "test": ["CMD", "true"]}}
        )
        self.assertEqual(
            _codes(self._scan(module)),
            [
                (
                    "E145",
                    "module:db.spec.implementation.compose.services.db."
                    "healthcheck.conditionallyEnabledFrom",
                )
            ],
        )

    def test_bool_condition_inside_production_mapping_reports_e145(self):
        module = self._module({"production": {"enabledFrom": True}})
        self.assertEqual(
            _codes(self._scan(module)),
            [
                (
                    "E145",
                    "module:db.spec.implementation.compose.services.db."
                    "production.enabledFrom",
                )
            ],
        )

    def test_bool_condition_on_volume_definition_reports_e145(self):
        module = self._module()
        module["spec"]["implementation"]["compose"]["volumes"]["data"]["enabledFrom"] = 1
        self.assertEqual(
            _codes(self._scan(module)),
            [("E145", "module:db.spec.implementation.compose.volumes.data.enabledFrom")],
        )

    def test_condition_on_other_list_entries_reports_e145(self):
        module = self._module({"ports": [{"target": 5432, "enabledFrom": "config.enabled"}]})
        self.assertEqual(
            _codes(self._scan(module)),
            [("E145", "module:db.spec.implementation.compose.services.db.ports[0].enabledFrom")],
        )

    def test_e145_flows_through_validate_loaded_profile(self):
        module = self._module({"enabledFrom": True})
        inst = _inst("db", module)
        with (
            patch.object(validator, "validate_profile_shape", return_value=[]),
            patch.object(validator, "load_module_instances", return_value=([inst], [])),
        ):
            diagnostics = validate_loaded_profile({"spec": {"modules": []}}, Path("profile.yaml"))
        self.assertTrue(
            any(
                d.code == "E145"
                and d.path == "module:db.spec.implementation.compose.services.db.enabledFrom"
                for d in diagnostics
            )
        )


class KubernetesMarkerShapeTest(unittest.TestCase):
    """E144/E145 for the `implementation.kubernetes` block: production
    gates are implemented on volume/configMap specs, enabledFrom on
    volume specs (guarded) and workloads (unguarded -- a bad shape
    crashes the helm renderer)."""

    def _module(self, kubernetes):
        return {
            "kind": "Module",
            "metadata": {"category": "warehouse", "name": "mod"},
            "spec": {
                "provides": [],
                "consumes": [],
                "configSchema": {"type": "object"},
                "implementation": {
                    "kind": "docker-compose",
                    "compose": {},
                    "kubernetes": kubernetes,
                },
            },
        }

    def _scan(self, kubernetes):
        return validator.validate_module_marker_shapes([_inst("pg", self._module(kubernetes))])

    def test_valid_markers_are_clean(self):
        kubernetes = {
            "volumes": {
                "data": {"enabledFrom": "config.storage.enabled", "production": True},
            },
            "configMaps": {"code": {"production": {"name": "release-code"}}},
            "workloads": {"app": {"enabledFrom": "config.enabled"}},
        }
        self.assertEqual(self._scan(kubernetes), [])

    def test_bool_enabled_from_on_workload_reports_e145(self):
        # The workload guard is unguarded: a boolean shape crashes the
        # helm renderer today; E145 fails closed instead.
        kubernetes = {"workloads": {"app": {"enabledFrom": True}}}
        diagnostics = self._scan(kubernetes)
        self.assertEqual(
            _codes(diagnostics),
            [("E145", "module:pg.spec.implementation.kubernetes.workloads.app.enabledFrom")],
        )

    def test_bool_enabled_from_on_volume_spec_reports_e145(self):
        # The volume-spec guard checks isinstance(str), so bad shapes
        # silently misbehave today; E145 fails closed instead.
        kubernetes = {"volumes": {"data": {"enabledFrom": True}}}
        self.assertEqual(
            _codes(self._scan(kubernetes)),
            [("E145", "module:pg.spec.implementation.kubernetes.volumes.data.enabledFrom")],
        )

    def test_string_production_on_volume_spec_reports_e144(self):
        kubernetes = {"volumes": {"data": {"production": "true"}}}
        self.assertEqual(
            _codes(self._scan(kubernetes)),
            [("E144", "module:pg.spec.implementation.kubernetes.volumes.data.production")],
        )

    def test_string_production_on_configmap_spec_reports_e144(self):
        kubernetes = {"configMaps": {"code": {"production": "true"}}}
        self.assertEqual(
            _codes(self._scan(kubernetes)),
            [("E144", "module:pg.spec.implementation.kubernetes.configMaps.code.production")],
        )

    def test_enabled_from_on_configmap_reports_e145(self):
        kubernetes = {"configMaps": {"code": {"enabledFrom": "config.enabled"}}}
        self.assertEqual(
            _codes(self._scan(kubernetes)),
            [("E145", "module:pg.spec.implementation.kubernetes.configMaps.code.enabledFrom")],
        )

    def test_production_on_workload_reports_e144(self):
        kubernetes = {"workloads": {"app": {"production": True}}}
        self.assertEqual(
            _codes(self._scan(kubernetes)),
            [("E144", "module:pg.spec.implementation.kubernetes.workloads.app.production")],
        )
