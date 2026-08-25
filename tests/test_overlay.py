import tempfile
import unittest
from pathlib import Path

import yaml

from cli.overlay import (
    _duplicate_module_ids,
    _merge_modules,
    _merge_value,
    resolve_profile,
)


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
    def test_duplicate_module_ids_ignores_non_dict_entries_rather_than_crashing(self):
        modules = [{"id": "a"}, "oops-a-string", {"id": "a"}]
        self.assertEqual(_duplicate_module_ids(modules), {"a"})

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
                        "runtime": {
                            "type": "container",
                            "service": {
                                "name": "postgres",
                                "ports": [{"name": "db", "containerPort": 5432, "protocol": "TCP"}],
                            },
                        },
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
                "runtime": {"type": "docker-compose"},
                "modules": [
                    {
                        "id": "db",
                        "source": "../../modules/warehouse/postgres",
                        "version": "0.1.0",
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

    def test_spec_modules_not_a_list_rejected_cleanly_not_a_crash(self):
        self._write_overlay("badshape", {"spec": {"modules": "not-a-list"}})
        resolved, _prov, diagnostics = resolve_profile(str(self.profile_path), environment="badshape")
        self.assertIsNone(resolved)
        self.assertTrue(any(d.code == "E093" for d in diagnostics), diagnostics)

    def test_module_entry_not_a_dict_rejected_cleanly_not_a_crash(self):
        self._write_overlay("badentry", {"spec": {"modules": ["oops-a-string"]}})
        resolved, _prov, diagnostics = resolve_profile(str(self.profile_path), environment="badentry")
        self.assertIsNone(resolved)
        self.assertTrue(any(d.code == "E093" for d in diagnostics), diagnostics)

    def test_malformed_module_list_in_base_profile_also_rejected_cleanly(self):
        self.profile_path.write_text(
            yaml.safe_dump(
                {
                    "apiVersion": "cds/v1alpha1",
                    "kind": "Profile",
                    "metadata": {"name": "analytics", "environment": "local"},
                    "spec": {"modules": ["oops-a-string"]},
                }
            )
        )
        self._write_overlay("anything", {"spec": {}})
        resolved, _prov, diagnostics = resolve_profile(str(self.profile_path), environment="anything")
        self.assertIsNone(resolved)
        self.assertTrue(any(d.code == "E093" for d in diagnostics), diagnostics)

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


class ExtendsCompositionTest(unittest.TestCase):
    """resolve_profile() honoring `extends` (issue #175)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.profiles_dir = self.root / "profiles"
        self.profiles_dir.mkdir(parents=True)
        self.modules_dir = self.root / "modules" / "warehouse" / "postgres"
        self.modules_dir.mkdir(parents=True)

        (self.modules_dir / "module.yaml").write_text(
            yaml.safe_dump(
                {
                    "apiVersion": "cds/v1alpha1",
                    "kind": "Module",
                    "metadata": {"name": "postgres", "category": "warehouse", "version": "0.1.0"},
                    "spec": {
                        "runtime": {
                            "type": "container",
                            "service": {
                                "name": "postgres",
                                "ports": [{"name": "db", "containerPort": 5432, "protocol": "TCP"}],
                            },
                        },
                        "configSchema": {"type": "object", "additionalProperties": True},
                        "implementation": {"kind": "docker-compose", "compose": {"services": {}}},
                    },
                }
            )
        )

    def _write_profile(self, name, content):
        profile_dir = self.profiles_dir / name
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "profile.yaml").write_text(yaml.safe_dump(content))
        return profile_dir / "profile.yaml"

    def _base_module(self, replicas=1):
        return {
            "id": "db",
            "source": "../../modules/warehouse/postgres",
            "version": "0.1.0",
            "enabled": True,
            "config": {"replicas": replicas},
        }

    def test_single_parent_child_wins_over_parent(self):
        self._write_profile(
            "base",
            {
                "apiVersion": "cds/v1alpha1",
                "kind": "Profile",
                "metadata": {"name": "base", "environment": "local"},
                "spec": {"runtime": {"type": "docker-compose"}, "modules": [self._base_module(1)]},
            },
        )
        child = self._write_profile(
            "child",
            {
                "apiVersion": "cds/v1alpha1",
                "kind": "Profile",
                "metadata": {"name": "child", "environment": "local"},
                "extends": ["base"],
                "spec": {"modules": [{"id": "db", "config": {"replicas": 5}}]},
            },
        )
        resolved, provenance, diagnostics = resolve_profile(str(child), environment=None)
        self.assertFalse(any(d.level == "error" for d in diagnostics), diagnostics)
        self.assertEqual(resolved["spec"]["modules"][0]["config"]["replicas"], 5)
        self.assertEqual(provenance["spec.modules[db]"], str(child))

    def test_multiple_parents_resolve_left_to_right_later_wins(self):
        self._write_profile(
            "p1",
            {
                "apiVersion": "cds/v1alpha1",
                "kind": "Profile",
                "metadata": {"name": "p1", "environment": "local"},
                "spec": {"runtime": {"type": "docker-compose"}, "modules": [self._base_module(1)]},
            },
        )
        self._write_profile(
            "p2",
            {
                "apiVersion": "cds/v1alpha1",
                "kind": "Profile",
                "metadata": {"name": "p2", "environment": "local"},
                "spec": {"modules": [{"id": "db", "config": {"replicas": 2}}]},
            },
        )
        child = self._write_profile(
            "child",
            {
                "apiVersion": "cds/v1alpha1",
                "kind": "Profile",
                "metadata": {"name": "child", "environment": "local"},
                "extends": ["p1", "p2"],
                "spec": {},
            },
        )
        resolved, _prov, diagnostics = resolve_profile(str(child), environment=None)
        self.assertFalse(any(d.level == "error" for d in diagnostics), diagnostics)
        self.assertEqual(resolved["spec"]["modules"][0]["config"]["replicas"], 2)

    def test_relative_path_parent_reference_is_supported(self):
        self._write_profile(
            "base",
            {
                "apiVersion": "cds/v1alpha1",
                "kind": "Profile",
                "metadata": {"name": "base", "environment": "local"},
                "spec": {"runtime": {"type": "docker-compose"}, "modules": [self._base_module(1)]},
            },
        )
        child = self._write_profile(
            "child",
            {
                "apiVersion": "cds/v1alpha1",
                "kind": "Profile",
                "metadata": {"name": "child", "environment": "local"},
                "extends": ["../base/profile.yaml"],
                "spec": {},
            },
        )
        resolved, _prov, diagnostics = resolve_profile(str(child), environment=None)
        self.assertFalse(any(d.level == "error" for d in diagnostics), diagnostics)
        self.assertEqual(resolved["spec"]["modules"][0]["id"], "db")

    def test_transitive_extends_chain_is_supported(self):
        self._write_profile(
            "grandparent",
            {
                "apiVersion": "cds/v1alpha1",
                "kind": "Profile",
                "metadata": {"name": "grandparent", "environment": "local"},
                "spec": {"runtime": {"type": "docker-compose"}, "modules": [self._base_module(1)]},
            },
        )
        self._write_profile(
            "parent",
            {
                "apiVersion": "cds/v1alpha1",
                "kind": "Profile",
                "metadata": {"name": "parent", "environment": "local"},
                "extends": ["grandparent"],
                "spec": {"modules": [{"id": "db", "config": {"replicas": 7}}]},
            },
        )
        child = self._write_profile(
            "child",
            {
                "apiVersion": "cds/v1alpha1",
                "kind": "Profile",
                "metadata": {"name": "child", "environment": "local"},
                "extends": ["parent"],
                "spec": {},
            },
        )
        resolved, _prov, diagnostics = resolve_profile(str(child), environment=None)
        self.assertFalse(any(d.level == "error" for d in diagnostics), diagnostics)
        self.assertEqual(resolved["spec"]["modules"][0]["config"]["replicas"], 7)

    def test_direct_cycle_is_rejected(self):
        a = self.profiles_dir / "a"
        a.mkdir(parents=True)
        b = self.profiles_dir / "b"
        b.mkdir(parents=True)
        (a / "profile.yaml").write_text(
            yaml.safe_dump(
                {
                    "apiVersion": "cds/v1alpha1",
                    "kind": "Profile",
                    "metadata": {"name": "a", "environment": "local"},
                    "extends": ["b"],
                    "spec": {"runtime": {"type": "docker-compose"}, "modules": []},
                }
            )
        )
        (b / "profile.yaml").write_text(
            yaml.safe_dump(
                {
                    "apiVersion": "cds/v1alpha1",
                    "kind": "Profile",
                    "metadata": {"name": "b", "environment": "local"},
                    "extends": ["a"],
                    "spec": {"runtime": {"type": "docker-compose"}, "modules": []},
                }
            )
        )
        resolved, _prov, diagnostics = resolve_profile(str(a / "profile.yaml"), environment=None)
        self.assertIsNone(resolved)
        self.assertTrue(any(d.code == "E106" for d in diagnostics), diagnostics)

    def test_missing_parent_profile_is_rejected(self):
        child = self._write_profile(
            "child",
            {
                "apiVersion": "cds/v1alpha1",
                "kind": "Profile",
                "metadata": {"name": "child", "environment": "local"},
                "extends": ["does-not-exist"],
                "spec": {"runtime": {"type": "docker-compose"}, "modules": []},
            },
        )
        resolved, _prov, diagnostics = resolve_profile(str(child), environment=None)
        self.assertIsNone(resolved)
        self.assertTrue(any(d.code == "E105" for d in diagnostics), diagnostics)

    def test_extends_outside_profiles_root_is_rejected(self):
        outside_dir = self.root / "outside"
        outside_dir.mkdir()
        (outside_dir / "profile.yaml").write_text(
            yaml.safe_dump(
                {
                    "apiVersion": "cds/v1alpha1",
                    "kind": "Profile",
                    "metadata": {"name": "outside", "environment": "local"},
                    "spec": {"runtime": {"type": "docker-compose"}, "modules": []},
                }
            )
        )
        child = self._write_profile(
            "child",
            {
                "apiVersion": "cds/v1alpha1",
                "kind": "Profile",
                "metadata": {"name": "child", "environment": "local"},
                "extends": ["../../outside/profile.yaml"],
                "spec": {"runtime": {"type": "docker-compose"}, "modules": []},
            },
        )
        resolved, _prov, diagnostics = resolve_profile(str(child), environment=None)
        self.assertIsNone(resolved)
        self.assertTrue(any(d.code == "E104" for d in diagnostics), diagnostics)

    def test_extends_not_a_list_is_rejected(self):
        child = self._write_profile(
            "child",
            {
                "apiVersion": "cds/v1alpha1",
                "kind": "Profile",
                "metadata": {"name": "child", "environment": "local"},
                "extends": "base",
                "spec": {"runtime": {"type": "docker-compose"}, "modules": []},
            },
        )
        resolved, _prov, diagnostics = resolve_profile(str(child), environment=None)
        self.assertIsNone(resolved)
        self.assertTrue(any(d.code == "E103" for d in diagnostics), diagnostics)

    def test_extends_combined_with_environment_overlay_applies_on_top(self):
        self._write_profile(
            "base",
            {
                "apiVersion": "cds/v1alpha1",
                "kind": "Profile",
                "metadata": {"name": "base", "environment": "local"},
                "spec": {"runtime": {"type": "docker-compose"}, "modules": [self._base_module(1)]},
            },
        )
        child_dir = self.profiles_dir / "child"
        child_dir.mkdir(parents=True, exist_ok=True)
        (child_dir / "profile.yaml").write_text(
            yaml.safe_dump(
                {
                    "apiVersion": "cds/v1alpha1",
                    "kind": "Profile",
                    "metadata": {"name": "child", "environment": "local"},
                    "extends": ["base"],
                    "spec": {},
                }
            )
        )
        env_dir = child_dir / "environments"
        env_dir.mkdir()
        (env_dir / "prod.yaml").write_text(
            yaml.safe_dump({"spec": {"modules": [{"id": "db", "config": {"replicas": 9}}]}})
        )
        resolved, _prov, diagnostics = resolve_profile(str(child_dir / "profile.yaml"), environment="prod")
        self.assertFalse(any(d.level == "error" for d in diagnostics), diagnostics)
        self.assertEqual(resolved["spec"]["modules"][0]["config"]["replicas"], 9)

    def test_no_extends_field_behaves_exactly_as_before(self):
        child = self._write_profile(
            "child",
            {
                "apiVersion": "cds/v1alpha1",
                "kind": "Profile",
                "metadata": {"name": "child", "environment": "local"},
                "spec": {"runtime": {"type": "docker-compose"}, "modules": [self._base_module(1)]},
            },
        )
        resolved, provenance, diagnostics = resolve_profile(str(child), environment=None)
        self.assertFalse(any(d.level == "error" for d in diagnostics), diagnostics)
        self.assertEqual(provenance, {})
        self.assertEqual(resolved["spec"]["modules"][0]["config"]["replicas"], 1)


if __name__ == "__main__":
    unittest.main()
