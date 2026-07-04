import re
import unittest
from pathlib import Path

import yaml


class PythonVersionAlignmentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent
        self.dockerfile = self.repo_root / "images" / "dagster" / "Dockerfile"
        self.pyproject = self.repo_root / "pyproject.toml"
        self.ci_workflow = self.repo_root / ".github" / "workflows" / "ci.yml"

    def test_python_versions_are_aligned(self) -> None:
        docker_version = self._docker_python_version()
        pyproject_version = self._pyproject_min_python_version()
        ci_versions = self._ci_python_versions()

        self.assertEqual(
            pyproject_version,
            docker_version,
            f"pyproject requires-python ({pyproject_version}) must match Docker image Python ({docker_version})",
        )

        self.assertEqual(
            ci_versions,
            {docker_version},
            f"CI Python versions ({sorted(ci_versions)}) must match Docker image Python ({docker_version})",
        )

    def _docker_python_version(self) -> str:
        content = self.dockerfile.read_text(encoding="utf-8")
        match = re.search(r"^FROM\s+python:(\d+\.\d+)(?:[.-].*)?$", content, flags=re.MULTILINE)
        self.assertIsNotNone(match, "Could not parse Python version from images/dagster/Dockerfile")
        return match.group(1)

    def _pyproject_min_python_version(self) -> str:
        content = self.pyproject.read_text(encoding="utf-8")
        match = re.search(r'^requires-python\s*=\s*"\s*>=\s*(\d+\.\d+)\s*"\s*$', content, flags=re.MULTILINE)
        self.assertIsNotNone(match, "Could not parse requires-python from pyproject.toml")
        return match.group(1)

    def _ci_python_versions(self) -> set[str]:
        workflow = yaml.safe_load(self.ci_workflow.read_text(encoding="utf-8")) or {}
        jobs = workflow.get("jobs", {})
        if not isinstance(jobs, dict):
            self.fail("Invalid CI workflow: jobs section missing or not an object")

        versions: set[str] = set()

        lint_job = jobs.get("lint", {})
        if isinstance(lint_job, dict):
            steps = lint_job.get("steps", [])
            if isinstance(steps, list):
                for step in steps:
                    if not isinstance(step, dict):
                        continue
                    if step.get("uses") != "actions/setup-python@v6":
                        continue
                    with_section = step.get("with", {})
                    if isinstance(with_section, dict):
                        lint_version = with_section.get("python-version")
                        if isinstance(lint_version, str):
                            versions.add(lint_version)

        test_job = jobs.get("test", {})
        if isinstance(test_job, dict):
            strategy = test_job.get("strategy", {})
            if isinstance(strategy, dict):
                matrix = strategy.get("matrix", {})
                if isinstance(matrix, dict):
                    matrix_versions = matrix.get("python-version", [])
                    if isinstance(matrix_versions, list):
                        for version in matrix_versions:
                            if isinstance(version, str):
                                versions.add(version)

        self.assertTrue(versions, "Could not find Python versions in CI workflow")
        return versions


if __name__ == "__main__":
    unittest.main()