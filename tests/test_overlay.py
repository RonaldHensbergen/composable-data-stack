import tempfile
import unittest
from pathlib import Path

import yaml

from cli.overlay import _merge_modules, _merge_value, resolve_profile


class MergeValueTest(unittest.TestCase):
    def test_recursive_mapping_merge(self):
        base = {"a": {"x": 1, "y": 2}, "b": 1}
        overlay = {"a": {"y": 20, "z": 30}}
        provenance = {}
        result = _merge_value(base, overlay, "base.yaml", "overlay.yaml", "", provenance)
        self.assertEqual(result, {"a": {"x": 1, "y": 20, "z": 30}, "b": 1})

    def test_overlay_only_key_added(self):
        base = {"a": 1}
        overlay = {"b": 2}
        provenance = {}
        result = _merge_value(base, overlay, "base.yaml", "overlay.yaml", "", provenance)
        self.assertEqual(result, {"a": 1, "b": 2})

    def test_non_mapping_arrays_are_replaced_wholesale_not_concatenated(self):
        base = {"tags": ["a", "b", "c"]}
        overlay = {"tags": ["z"]}
        provenance = {}
        result = _merge_value(base, overlay, "base.yaml", "overlay.yaml", "", provenance)
        self.assertEqual(result["tags"], ["z"])

    def test_scalar_overlay_replaces_base_scalar(self):
        base = {"replicas": 1}
        overlay = {"replicas": 3}
        provenance = {}
        result = _merge_value(base, overlay, "base.yaml", "overlay.yaml", "", provenance)
        self.assertEqual(result["replicas"], 3)

    def test_provenance_tracks_base_and_overlay_sources(self):
        base = {"a": {"x": 1, "y": 2}}
        overlay = {"a": {"y": 20}}
        provenance = {}
        _merge_value(base, overlay, "base.yaml", "overlay.yaml", "", provenance)
        self.assertEqual(provenance["a.x"], "base.yaml")
        self.assertEqual(provenance["a.y"], "overlay.yaml")

    def test_deterministic_regardless_of_key_order(self):
        base = {"a": 1, "b": 2, "c": 3}
        overlay_orders = [{"c": 30, "a": 10}, {"a": 10, "c": 30}]
        results = [
            _merge_value(dict(base), dict(o), "base.yaml", "overlay.yaml", "", {})
            for o in overlay_orders
        ]
        self.assertEqual(results[0], results[1])

    def test_type_mismatch_overlay_scalar_replaces_base_dict_wholesale(self):
        base = {"spec": {"timeout": {"retries": 3}}}
        overlay = {"spec": {"timeout": "5s"}}
        provenance = {}
        result = _merge_value(base, overlay, "base.yaml", "overlay.yaml", "", provenance)
        self.assertEqual(result["spec"]["timeout"], "5s")
        self.assertEqual(provenance["spec.timeout"], "overlay.yaml")

    def test_type_mismatch_overlay_dict_replaces_base_scalar_wholesale(self):
        base = {"spec": {"timeout": "5s"}}
        overlay = {"spec": {"timeout": {"retries": 3}}}
        result = _merge_value(base, overlay, "base.yaml", "overlay.yaml", "", {})
        self.assertEqual(result["spec"]["timeout"], {"retries": 3})


class MergeModulesTest(unittest.TestCase):
    def test_merges_by_id_not_position(self):
        base_modules = [
            {"id": "dagster", "config": {"image": "dagster:1.0"}},
            {"id": "superset", "config": {"image": "superset:1.0"}},
        ]
        overlay_modules = [
            {"id": "dagster", "config": {"image": "dagster:2.0"}},
        ]
        provenance = {}
        result = _merge_modules(base_modules, overlay_modules, "base.yaml", "overlay.yaml", provenance)
        by_id = {m["id"]: m for m in result}
        self.assertEqual(by_id["dagster"]["config"]["image"], "dagster:2.0")
        self.assertEqual(by_id["superset"]["config"]["image"], "superset:1.0")
        # Base order preserved for untouched entries.
        self.assertEqual([m["id"] for m in result], ["dagster", "superset"])

    def test_overlay_only_module_id_is_appended(self):
        base_modules = [{"id": "dagster", "config": {}}]
        overlay_modules = [{"id": "vault", "config": {}}]
        result = _merge_modules(base_modules, overlay_modules, "base.yaml", "overlay.yaml", {})
        self.assertEqual([m["id"] for m in result], ["dagster", "vault"])

    def test_enable_disable_via_overlay_is_preserved(self):
        base_modules = [{"id": "vault", "config": {}, "enabled": True}]
        overlay_modules = [{"id": "vault", "config": {}, "enabled": False}]
        result = _merge_modules(base_modules, overlay_modules, "base.yaml", "overlay.yaml", {})
        self.assertEqual(result[0]["enabled"], False)

    def test_module_level_provenance_reflects_overlay_when_touched(self):
        base_modules = [{"id": "dagster", "config": {"x": 1}}]
        overlay_modules = [{"id": "dagster", "config": {"x": 2}}]
        provenance = {}
        _merge_modules(base_modules, overlay_modules, "base.yaml", "overlay.yaml", provenance)
        self.assertEqual(provenance["spec.modules[dagster]"], "overlay.yaml")

    def test_module_untouched_by_overlay_keeps_base_provenance(self):
        base_modules = [{"id": "superset", "config": {}}]
        provenance = {}
        _merge_modules(base_modules, [], "base.yaml", "overlay.yaml", provenance)
        self.assertEqual(provenance["spec.modules[superset]"], "base.yaml")


class ResolveProfileFixtureTest(unittest.TestCase):
    """Full resolve_profile() against real files on disk."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.profile_dir = self.root / "profiles" / "analytics"
        self.profile_dir.mkdir(parents=True)
        self.modules_dir = self.root / "modules" / "warehouse" / "postgres"
        self.modules_dir.mkdir(parents=True)

        (self.modules_dir / "module.yaml").write_text(
            yaml.safe_dump(
                {
                    "apiVersion": "cds/v1alpha1",
                    "kind": "Module",
                    "metadata": {"name": "postgres", "category": "warehouse", "version": "0.1.0"},
                    "spec": {
                        "configSchema": {"type": "object", "additionalProperties": True},
                        "implementation": {"kind": "docker-compose", "compose": {"services": {}}},
                    },
                }
            )
        )

        self.base_profile = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Profile",
            "metadata": {"name": "analytics", "environment": "local"},
            "spec": {
                "modules": [
                    {
                        "id": "db",
                        "source": "../../modules/warehouse/postgres",
                        "enabled": True,
                        "config": {"replicas": 1},
                    }
                ]
            },
        }
        self.profile_path = self.profile_dir / "profile.yaml"
        self.profile_path.write_text(yaml.safe_dump(self.base_profile))

    def _write_overlay(self, name, content):
        env_dir = self.profile_dir / "environments"
        env_dir.mkdir(exist_ok=True)
        (env_dir / f"{name}.yaml").write_text(yaml.safe_dump(content))

    def test_standalone_profile_unaffected_when_no_environment_selected(self):
        resolved, provenance, diagnostics = resolve_profile(str(self.profile_path), environment=None)
        self.assertEqual(provenance, {})
        self.assertFalse(any(d.level == "error" for d in diagnostics))
        self.assertEqual(resolved["spec"]["modules"][0]["config"]["replicas"], 1)

    def test_environment_only_resolves_when_explicitly_selected(self):
        self._write_overlay("prod", {"spec": {"modules": [{"id": "db", "config": {"replicas": 3}}]}})
        resolved, _prov, diags = resolve_profile(str(self.profile_path), environment=None)
        self.assertFalse(any(d.level == "error" for d in diags))
        self.assertEqual(resolved["spec"]["modules"][0]["config"]["replicas"], 1)

    def test_merges_overlay_when_environment_selected(self):
        self._write_overlay("prod", {"spec": {"modules": [{"id": "db", "config": {"replicas": 3}}]}})
        resolved, provenance, diagnostics = resolve_profile(str(self.profile_path), environment="prod")
        self.assertFalse(any(d.level == "error" for d in diagnostics), diagnostics)
        self.assertEqual(resolved["spec"]["modules"][0]["config"]["replicas"], 3)
        self.assertIn("spec.modules[db]", provenance)

    def test_unknown_environment_is_rejected(self):
        resolved, _prov, diagnostics = resolve_profile(str(self.profile_path), environment="does-not-exist")
        self.assertIsNone(resolved)
        self.assertTrue(any(d.code == "E091" for d in diagnostics))

    def test_path_traversal_in_environment_name_is_rejected(self):
        resolved, _prov, diagnostics = resolve_profile(
            str(self.profile_path), environment="../../../../etc/passwd"
        )
        self.assertIsNone(resolved)
        self.assertTrue(any(d.code in ("E090", "E091") for d in diagnostics))

    def test_malformed_overlay_not_a_mapping_is_rejected(self):
        env_dir = self.profile_dir / "environments"
        env_dir.mkdir(exist_ok=True)
        (env_dir / "broken.yaml").write_text("- just\n- a\n- list\n")
        resolved, _prov, diagnostics = resolve_profile(str(self.profile_path), environment="broken")
        self.assertIsNone(resolved)
        self.assertTrue(any(d.level == "error" for d in diagnostics), diagnostics)

    def test_duplicate_module_ids_within_overlay_rejected(self):
        self._write_overlay(
            "dupes",
            {"spec": {"modules": [{"id": "db", "config": {}}, {"id": "db", "config": {}}]}},
        )
        resolved, _prov, diagnostics = resolve_profile(str(self.profile_path), environment="dupes")
        self.assertIsNone(resolved)
        self.assertTrue(any(d.code == "E093" for d in diagnostics))

    def test_module_missing_id_in_overlay_rejected_cleanly_not_a_crash(self):
        self._write_overlay("noid", {"spec": {"modules": [{"config": {}}]}})
        resolved, _prov, diagnostics = resolve_profile(str(self.profile_path), environment="noid")
        self.assertIsNone(resolved)
        self.assertTrue(any(d.code == "E093" for d in diagnostics))

    def test_fully_merged_profile_receives_normal_validation(self):
        self._write_overlay(
            "badref",
            {"spec": {"modules": [{"id": "ghost", "source": "does/not/exist", "config": {}}]}},
        )
        resolved, _prov, diagnostics = resolve_profile(str(self.profile_path), environment="badref")
        self.assertIsNone(resolved)
        self.assertTrue(any(d.level == "error" for d in diagnostics))

    def test_overlay_module_missing_source_gets_the_normal_diagnostic(self):
        self._write_overlay("nosource", {"spec": {"modules": [{"id": "newmod", "config": {}}]}})
        resolved, _prov, diagnostics = resolve_profile(str(self.profile_path), environment="nosource")
        self.assertIsNone(resolved)
        self.assertTrue(any("source" in d.message.lower() for d in diagnostics), diagnostics)


if __name__ == "__main__":
    unittest.main()
