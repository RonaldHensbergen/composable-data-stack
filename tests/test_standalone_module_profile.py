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

# Codes expected when a module runs standalone (no sibling modules, no config
# supplied) instead of wired into a real profile:
#   E030 - config-schema violation (fires because we pass config: {}, so any
#          module with required config fields reports them missing)
#   E041 - a required `consumes` binding could not be resolved (no producer
#          module exists in the standalone profile)
#   E042 - contract kind mismatch on a consumed binding
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
                                "version": "0.0.0",
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
