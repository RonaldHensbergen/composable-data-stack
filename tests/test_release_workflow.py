import unittest
from pathlib import Path

import yaml


class ReleaseWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        path = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "release.yml"
        self.steps = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]["release"]["steps"]

    def test_release_is_published_explicitly_after_creation(self) -> None:
        names = [s.get("name", "") for s in self.steps]
        create = names.index("Create GitHub release")
        publish = next(i for i, n in enumerate(names) if n.startswith("Publish the release"))
        self.assertGreater(publish, create)
        run = self.steps[publish]["run"]
        self.assertIn('gh release edit "${GITHUB_REF_NAME}" --draft=false', run)
        self.assertIn("isDraft", run)


if __name__ == "__main__":
    unittest.main()
