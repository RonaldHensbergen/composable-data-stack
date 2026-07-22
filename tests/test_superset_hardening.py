import re
import unittest
from pathlib import Path

import yaml


class SupersetHardeningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent

    def test_image_uses_pinned_base_and_non_root_runtime(self) -> None:
        dockerfile = (self.repo_root / "images" / "superset" / "Dockerfile").read_text(encoding="utf-8")

        self.assertRegex(
            dockerfile,
            r"(?m)^FROM apache/superset:6\.1\.0@sha256:[0-9a-f]{64}$",
        )
        users = re.findall(r"^USER\s+(\S+)$", dockerfile, flags=re.MULTILINE)
        self.assertEqual(users[-1], "superset")

    def test_service_has_restricted_runtime(self) -> None:
        module = yaml.safe_load(
            (self.repo_root / "modules" / "bi" / "superset" / "module.yaml").read_text(encoding="utf-8")
        )
        services = module["spec"]["implementation"]["compose"]["services"]

        for name in ("superset-init", "superset"):
            with self.subTest(service=name):
                service = services[name]
                self.assertTrue(service["read_only"])
                self.assertEqual(service["cap_drop"], ["ALL"])
                self.assertEqual(service["security_opt"], ["no-new-privileges:true"])
                self.assertIn("/tmp:rw,noexec,nosuid,nodev", service["tmpfs"])
                self.assertIn("/app/superset_home:rw,noexec,nosuid,nodev", service["tmpfs"])


if __name__ == "__main__":
    unittest.main()
