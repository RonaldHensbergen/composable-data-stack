import importlib.util
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "validate_cli_sbom.py"

_spec = importlib.util.spec_from_file_location("validate_cli_sbom", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
validate_cli_sbom = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = validate_cli_sbom
_spec.loader.exec_module(validate_cli_sbom)


def _valid_sbom() -> dict:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "metadata": {
            "component": {
                "type": "application",
                "name": "composable-data-stack",
                "version": "0.9.1",
            }
        },
        "components": [
            {"type": "library", "name": "jsonschema", "version": "4.22.0"},
            {"type": "library", "name": "packaging", "version": "23.0"},
            {"type": "library", "name": "rich", "version": "15.0"},
            {"type": "library", "name": "PyYAML", "version": "6.0"},
        ],
    }


class NormalizeNameTest(unittest.TestCase):
    def test_normalizes_case_and_separators(self) -> None:
        self.assertEqual(validate_cli_sbom.normalize_name("PyYAML"), "pyyaml")
        self.assertEqual(validate_cli_sbom.normalize_name("py_yaml"), "py-yaml")
        self.assertEqual(validate_cli_sbom.normalize_name("py.yaml"), "py-yaml")
        self.assertEqual(validate_cli_sbom.normalize_name("py--yaml"), "py-yaml")


class DirectDependencyNamesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.pyproject_path = Path(self._tmpdir.name) / "pyproject.toml"

    def _write_pyproject(self, dependencies: list[str]) -> None:
        deps = ",\n".join(f'  "{dep}"' for dep in dependencies)
        self.pyproject_path.write_text(
            f'[project]\nname = "example"\nversion = "1.0.0"\ndependencies = [\n{deps}\n]\n',
            encoding="utf-8",
        )

    def test_extracts_bare_names_from_versioned_requirements(self) -> None:
        self._write_pyproject(["jsonschema>=4.22.0", "packaging>=23.0", "rich>=15.0"])
        self.assertEqual(
            validate_cli_sbom.direct_dependency_names(self.pyproject_path),
            ["jsonschema", "packaging", "rich"],
        )

    def test_rejects_unparseable_requirement(self) -> None:
        self._write_pyproject(["not a valid requirement !!!"])
        with self.assertRaises(SystemExit):
            validate_cli_sbom.direct_dependency_names(self.pyproject_path)


class ValidateSbomTest(unittest.TestCase):
    def test_valid_sbom_passes(self) -> None:
        problems = validate_cli_sbom.validate_sbom(
            _valid_sbom(),
            expected_name="composable-data-stack",
            expected_version="0.9.1",
            required_dependency_names=["jsonschema", "packaging", "rich"],
        )
        self.assertEqual(problems, [])

    def test_wrong_bom_format_is_flagged(self) -> None:
        sbom = _valid_sbom()
        sbom["bomFormat"] = "SPDX"
        problems = validate_cli_sbom.validate_sbom(
            sbom, expected_name="composable-data-stack", expected_version="0.9.1", required_dependency_names=[]
        )
        self.assertTrue(any("bomFormat" in p for p in problems))

    def test_missing_root_component_is_flagged(self) -> None:
        sbom = _valid_sbom()
        del sbom["metadata"]["component"]
        problems = validate_cli_sbom.validate_sbom(
            sbom, expected_name="composable-data-stack", expected_version="0.9.1", required_dependency_names=[]
        )
        self.assertTrue(any("metadata.component is missing" in p for p in problems))

    def test_mismatched_name_is_flagged(self) -> None:
        sbom = _valid_sbom()
        sbom["metadata"]["component"]["name"] = "some-other-project"
        problems = validate_cli_sbom.validate_sbom(
            sbom, expected_name="composable-data-stack", expected_version="0.9.1", required_dependency_names=[]
        )
        self.assertTrue(any("metadata.component.name" in p for p in problems))

    def test_name_comparison_is_normalized(self) -> None:
        sbom = _valid_sbom()
        sbom["metadata"]["component"]["name"] = "Composable_Data.Stack"
        problems = validate_cli_sbom.validate_sbom(
            sbom, expected_name="composable-data-stack", expected_version="0.9.1", required_dependency_names=[]
        )
        self.assertEqual(problems, [])

    def test_mismatched_version_is_flagged(self) -> None:
        sbom = _valid_sbom()
        sbom["metadata"]["component"]["version"] = "0.1.0"
        problems = validate_cli_sbom.validate_sbom(
            sbom, expected_name="composable-data-stack", expected_version="0.9.1", required_dependency_names=[]
        )
        self.assertTrue(any("metadata.component.version" in p for p in problems))

    def test_missing_direct_dependency_is_flagged(self) -> None:
        sbom = _valid_sbom()
        problems = validate_cli_sbom.validate_sbom(
            sbom,
            expected_name="composable-data-stack",
            expected_version="0.9.1",
            required_dependency_names=["jsonschema", "packaging", "rich", "nonexistent-dep"],
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("nonexistent-dep", problems[0])

    def test_dependency_check_is_case_and_separator_insensitive(self) -> None:
        sbom = _valid_sbom()
        sbom["components"].append({"type": "library", "name": "jsonschema-specifications", "version": "2023.1"})
        problems = validate_cli_sbom.validate_sbom(
            sbom,
            expected_name="composable-data-stack",
            expected_version="0.9.1",
            required_dependency_names=["JSONSchema_Specifications"],
        )
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
