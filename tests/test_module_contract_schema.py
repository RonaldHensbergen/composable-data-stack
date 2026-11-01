"""
The executable Contract: every shipped module and profile must validate
against the JSON Schema in cli/resources/.

The Contract is the written-down description of what the software must do
(docs/architecture.md, docs/modules.md): module.yaml / profile.yaml shape is
pinned by cli/resources/module.schema.json and profile.schema.json. This test
makes that Contract executable in CI for *every* module under modules/ and
modules-experimental/ and every profile under profiles/, so a module that
drifts from the Contract fails the suite instead of failing at render time.

Contract coverage (how much of the system the Contract + tests cover) is
published in docs/contract-coverage.md by scripts/check_contract_coverage.py.
"""
import json
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from cli.validator import validate_profile

REPO_ROOT = Path(__file__).resolve().parent.parent
RESOURCES = REPO_ROOT / "cli" / "resources"

MODULE_SCHEMA = json.loads((RESOURCES / "module.schema.json").read_text(encoding="utf-8"))
PROFILE_SCHEMA = json.loads((RESOURCES / "profile.schema.json").read_text(encoding="utf-8"))


def _module_files() -> list[Path]:
    files: list[Path] = []
    for root_name in ("modules", "modules-experimental"):
        files.extend(sorted((REPO_ROOT / root_name).rglob("module.yaml")))
    return files


def _profile_files() -> list[Path]:
    return sorted((REPO_ROOT / "profiles").rglob("profile.yaml"))


def _schema_failures(validator: Draft202012Validator, path: Path) -> list[str]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
    return [
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in errors
    ]


class ModuleContractSchemaTest(unittest.TestCase):
    """Every module.yaml meets module.schema.json; every profile.yaml meets
    profile.schema.json."""

    @classmethod
    def setUpClass(cls):
        Draft202012Validator.check_schema(MODULE_SCHEMA)
        Draft202012Validator.check_schema(PROFILE_SCHEMA)
        cls.module_validator = Draft202012Validator(MODULE_SCHEMA)
        cls.profile_validator = Draft202012Validator(PROFILE_SCHEMA)

    def test_modules_and_profiles_exist(self):
        self.assertGreaterEqual(len(_module_files()), 1)
        self.assertGreaterEqual(len(_profile_files()), 1)

    def test_every_module_yaml_matches_module_schema(self):
        failures = []
        for path in _module_files():
            errors = _schema_failures(self.module_validator, path)
            if errors:
                rel = path.relative_to(REPO_ROOT)
                failures.append(f"{rel}:\n  " + "\n  ".join(errors[:5]))
        self.assertEqual([], failures)

    def test_every_profile_passes_the_validator_contract(self):
        # Profiles that compose via `extends` omit top-level `spec` on disk,
        # so they are checked through the real pipeline (resolve extends,
        # then schema + semantic validation), exactly like `cds validate`.
        failures = []
        for path in _profile_files():
            diagnostics = validate_profile(str(path))
            errors = [d for d in diagnostics if d.level == "error"]
            if errors:
                rel = path.relative_to(REPO_ROOT)
                failures.append(
                    f"{rel}:\n  " + "\n  ".join(f"{d.code} {d.path}: {d.message}" for d in errors[:5])
                )
        self.assertEqual([], failures)


if __name__ == "__main__":
    unittest.main()
