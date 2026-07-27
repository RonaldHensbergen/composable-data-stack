import json
import unittest
from pathlib import Path

from cli.security import (
    _ENVIRONMENT_TO_CLASS,
    _flatten_profile_by_module,
    _infer_profile_class,
    _rule_matches,
    run_security_validation,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RULE_SCHEMA_PATH = _REPO_ROOT / "security" / "rule-schema.json"
_RULE_SET_PATH = _REPO_ROOT / "security" / "rule-set.json"


class InferProfileClassTest(unittest.TestCase):
    """
    Regression test for a bug where classification read profile["name"].
    A key that doesn't exist at the profile's top level (the real path is
    metadata.name), so every profile was silently classified as "local"
    regardless of its declared environment.
    """

    def test_reads_declared_metadata_environment(self):
        for environment, expected_class in _ENVIRONMENT_TO_CLASS.items():
            with self.subTest(environment=environment):
                profile = {"metadata": {"name": "anything", "environment": environment}}
                self.assertEqual(_infer_profile_class(profile), expected_class)

    def test_defaults_to_local_when_environment_is_missing(self):
        profile = {"metadata": {"name": "anything"}}
        self.assertEqual(_infer_profile_class(profile), "local")

    def test_ignores_a_nonexistent_top_level_name(self):
        profile = {
            "name": "production-sounding-name",
            "metadata": {"name": "anything", "environment": "local"},
        }
        self.assertEqual(_infer_profile_class(profile), "local")


class FlattenModuleProductionSuitabilityTest(unittest.TestCase):
    """cli/security.py resolving each module's own module.yaml to check productionSuitable."""

    def test_exposes_production_suitable_false_from_the_real_vault_module(self):
        profile_path = _REPO_ROOT / "profiles" / "local-dagster-postgres-superset-vault" / "profile.yaml"
        import yaml

        profile = yaml.safe_load(profile_path.read_text())
        flat = _flatten_profile_by_module(profile, profile_dir=profile_path.parent)
        marker = [
            (module_id, path, value)
            for module_id, path, value in flat
            if path == "_module.productionSuitable"
        ]
        self.assertEqual(marker, [("vault", "_module.productionSuitable", False)])

    def test_no_marker_without_profile_dir(self):
        profile_path = _REPO_ROOT / "profiles" / "local-dagster-postgres-superset-vault" / "profile.yaml"
        import yaml

        profile = yaml.safe_load(profile_path.read_text())
        flat = _flatten_profile_by_module(profile)
        markers = [p for _, p, _ in flat if p == "_module.productionSuitable"]
        self.assertEqual(markers, [])

    def test_respects_cds_module_path_override(self):
        import os
        import tempfile
        from unittest.mock import patch

        import yaml

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            profile_dir = root / "profiles" / "local"
            modules_root = root / "modules"
            module_dir = modules_root / "secrets" / "custom"
            profile_dir.mkdir(parents=True)
            module_dir.mkdir(parents=True)

            (module_dir / "module.yaml").write_text(
                yaml.safe_dump(
                    {
                        "apiVersion": "cds/v1alpha1",
                        "kind": "Module",
                        "metadata": {
                            "name": "custom",
                            "category": "secrets",
                            "version": "0.1.0",
                            "productionSuitable": False,
                        },
                        "spec": {
                            "configSchema": {"type": "object", "additionalProperties": False},
                            "implementation": {"kind": "docker-compose", "compose": {"services": {}}},
                        },
                    }
                ),
                encoding="utf-8",
            )

            profile = {
                "spec": {
                    "modules": [
                        {"id": "custom", "source": "secrets/custom", "enabled": True, "config": {}}
                    ]
                }
            }

            with patch.dict(os.environ, {"CDS_MODULE_PATH": str(modules_root)}):
                flat = _flatten_profile_by_module(profile, profile_dir=profile_dir)

            markers = [(m, p, v) for m, p, v in flat if p == "_module.productionSuitable"]
            self.assertEqual(markers, [("custom", "_module.productionSuitable", False)])


class ContradictoryProductionDeclarationRuleTest(unittest.TestCase):
    """CDS-SEC-073: a non-local profile using a productionSuitable:false module."""

    @classmethod
    def setUpClass(cls):
        rule_set = json.loads(_RULE_SET_PATH.read_text())
        cls.rule = next(r for r in rule_set["rules"] if r["id"] == "CDS-SEC-073")

    def test_fires_for_staging_or_prod_profile_using_a_non_production_module(self):
        flat_items = [("vault", "_module.productionSuitable", False)]
        for profile_class in ("staging", "prod"):
            with self.subTest(profile_class=profile_class):
                findings = _rule_matches(self.rule, flat_items, profile_class=profile_class)
                self.assertEqual(len(findings), 1)
                self.assertEqual(findings[0]["module"], "vault")

    def test_does_not_fire_for_local_or_dev_profile(self):
        flat_items = [("vault", "_module.productionSuitable", False)]
        for profile_class in ("local", "dev"):
            with self.subTest(profile_class=profile_class):
                findings = _rule_matches(self.rule, flat_items, profile_class=profile_class)
                self.assertEqual(findings, [])

    def test_does_not_fire_when_no_module_is_flagged(self):
        flat_items = [("dagster", "config.image", "dagster:1.8.0")]
        findings = _rule_matches(self.rule, flat_items, profile_class="prod")
        self.assertEqual(findings, [])


class RealVaultProfileEndToEndTest(unittest.TestCase):
    """
    Full run_security_validation pass against the actual checked-in vault
    profile; proves the fix end to end, not just against constructed data.
    """

    def test_current_local_declaration_is_clean(self):
        profile_path = _REPO_ROOT / "profiles" / "local-dagster-postgres-superset-vault" / "profile.yaml"
        findings, _diags = run_security_validation(profile_path, _RULE_SCHEMA_PATH, _RULE_SET_PATH)
        hits = [f for f in findings if f["rule_id"] == "CDS-SEC-073"]
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
