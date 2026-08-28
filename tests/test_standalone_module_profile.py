"""
Generic single-module profile smoke test.

For every real module.yaml under modules/ (and modules-experimental/, if
present), this builds a minimal profile that references only that module in
isolation -- no other modules, no dependsOn, no contract bindings supplied by
a sibling module -- and runs it through cli.validator.validate_profile.

Modules that `consumes` a required contract (e.g. a database) cannot resolve
that binding on their own, so validate_profile is expected to report an
"unresolved contract binding" diagnostic (E030/E041/E042) for those. That is
correct, expected behavior, not a bug: it proves the module correctly
declares its external dependency. Any other diagnostic code (E001, E010,
E011, E020, E021, E022, ...) indicates a genuine problem with the module's
own definition -- e.g. invalid YAML, a schema violation in module.yaml
itself, or a malformed consumes/provides entry -- and fails the test.
"""
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import yaml

from cli.validator import validate_profile

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MODULE_ROOTS = ("modules", "modules-experimental")

# Diagnostic codes that only ever fire because a required contract binding
# (e.g. a database the module consumes) wasn't supplied -- expected when a
# module is exercised standalone rather than wired up in a real profile.
_EXPECTED_UNBOUND_CODES = {"E030", "E041", "E042"}


def _discover_modules() -> list[tuple[str, Path]]:
    """Returns (source, module_yaml_path) pairs for every real module."""
    discovered = []
    for root_name in _MODULE_ROOTS:
        root = _REPO_ROOT / root_name
        if not root.is_dir():
            continue
        for module_yaml in sorted(root.glob("*/*/module.yaml")):
            source = str(module_yaml.parent.relative_to(root))
            discovered.append((source, module_yaml))
    return discovered


class StandaloneModuleProfileTest(unittest.TestCase):
    """Builds a throwaway single-module profile per module and validates it."""

    def test_every_module_forms_a_valid_standalone_profile(self):
        modules = _discover_modules()
        self.assertTrue(modules, "expected to discover at least one module.yaml")

        for source, module_yaml in modules:
            module_root = module_yaml.parents[2]  # .../modules
            with self.subTest(module=source):
                module_def = yaml.safe_load(module_yaml.read_text(encoding="utf-8"))
                version = module_def.get("metadata", {}).get("version", "0.1.0")

                profile = {
                    "apiVersion": "cds/v1alpha1",
                    "kind": "Profile",
                    "metadata": {"name": "standalone-smoke", "environment": "local"},
                    "spec": {
                        "runtime": {"type": "docker-compose"},
                        "modules": [
                            {
                                "id": "under-test",
                                "source": source,
                                "version": version,
                                "enabled": True,
                                "config": {},
                            }
                        ],
                    },
                }

                with tempfile.TemporaryDirectory() as tmp:
                    profile_path = Path(tmp) / "profile.yaml"
                    profile_path.write_text(yaml.safe_dump(profile))

                    with unittest.mock.patch.dict(
                        "os.environ", {"CDS_MODULE_PATH": str(module_root)}, clear=False
                    ):
                        diagnostics = validate_profile(str(profile_path))

                unexpected = [
                    d for d in diagnostics if d.level == "error" and d.code not in _EXPECTED_UNBOUND_CODES
                ]
                self.assertEqual(
                    unexpected,
                    [],
                    f"module {source} failed standalone validation with unexpected "
                    f"diagnostics: {[d.format() for d in unexpected]}",
                )


if __name__ == "__main__":
    unittest.main()
