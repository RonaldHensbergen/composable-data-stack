import unittest
from pathlib import Path

import yaml


class UpstreamImageHardeningTest(unittest.TestCase):
    def setUp(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        self.services = {}
        for name, path in {
            "postgres": repo_root / "modules" / "warehouse" / "postgres" / "module.yaml",
            "keydb": repo_root / "modules" / "cache" / "keydb" / "module.yaml",
            "vault": repo_root / "modules" / "secrets" / "vault" / "module.yaml",
        }.items():
            module = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.services[name] = module["spec"]["implementation"]["compose"]["services"][name]

    def test_images_are_digest_pinned(self) -> None:
        for name, service in self.services.items():
            with self.subTest(service=name):
                self.assertRegex(service["image"], r"^[^@]+@sha256:[0-9a-f]{64}$")
        self.assertTrue(self.services["keydb"]["image"].startswith("eqalpha/keydb:6.3.4@sha256:"))

    def test_services_have_restricted_runtime(self) -> None:
        expected_users = {"postgres": "postgres", "keydb": "keydb", "vault": "vault"}

        for name, service in self.services.items():
            with self.subTest(service=name):
                self.assertEqual(service["user"], expected_users[name])
                self.assertTrue(service["init"])
                self.assertTrue(service["read_only"])
                self.assertEqual(service["cap_drop"], ["ALL"])
                self.assertEqual(service["security_opt"], ["no-new-privileges:true"])
                self.assertGreater(service["pids_limit"], 0)
                self.assertTrue(all(port.startswith("127.0.0.1:") for port in service["ports"]))
                self.assertTrue(all("noexec" in mount for mount in service["tmpfs"]))
                self.assertTrue(all("nosuid" in mount for mount in service["tmpfs"]))
                self.assertTrue(all("nodev" in mount for mount in service["tmpfs"]))


if __name__ == "__main__":
    unittest.main()
