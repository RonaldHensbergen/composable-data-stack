import re
import unittest
from pathlib import Path

import yaml


class DagsterHardeningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent
        self.dockerfile = (self.repo_root / "images" / "dagster" / "Dockerfile").read_text(encoding="utf-8")
        self.entrypoint = (self.repo_root / "images" / "dagster" / "entrypoint.sh").read_text(encoding="utf-8")
        self.requirements = (self.repo_root / "images" / "dagster" / "requirements.txt").read_text(encoding="utf-8")
        module = yaml.safe_load(
            (self.repo_root / "modules" / "orchestration" / "dagster" / "module.yaml").read_text(encoding="utf-8")
        )
        self.services = module["spec"]["implementation"]["compose"]["services"]

    def test_image_has_minimal_immutable_runtime(self) -> None:
        users = re.findall(r"^USER\s+(\S+)$", self.dockerfile, flags=re.MULTILINE)

        self.assertEqual(users[-1], "dagster")
        self.assertNotIn("apt-get", self.dockerfile)
        self.assertNotIn("COPY . /app", self.dockerfile)
        self.assertNotIn("pip install", self.entrypoint)
        self.assertNotIn("dagster-docker", self.requirements)
        self.assertNotIn("dagster-postgres", self.requirements)
        self.assertIn("MySQL storage is not supported by this Dagster image", self.entrypoint)

    def test_backend_controls_build_and_runtime(self) -> None:
        for name in ("user-code", "dagster-webserver", "dagster-daemon"):
            with self.subTest(service=name):
                service = self.services[name]
                self.assertEqual(service["build"]["args"]["DB_BACKEND"], "${config.storage.backend}")
                self.assertEqual(service["environment"]["DB_BACKEND"], "${config.storage.backend}")

    def test_services_have_restricted_runtime_without_docker_socket(self) -> None:
        for name in ("user-code", "dagster-webserver", "dagster-daemon"):
            with self.subTest(service=name):
                service = self.services[name]
                volumes = service.get("volumes", [])

                self.assertTrue(service["init"])
                self.assertTrue(service["read_only"])
                self.assertEqual(service["cap_drop"], ["ALL"])
                self.assertEqual(service["security_opt"], ["no-new-privileges:true"])
                self.assertFalse(any("/var/run/docker.sock" in str(volume) for volume in volumes))
                self.assertIn("/tmp:rw,noexec,nosuid,nodev,mode=1777", service["tmpfs"])
                self.assertIn(
                    "/opt/dagster/dagster_home:rw,noexec,nosuid,nodev,uid=999,gid=999,mode=0700",
                    service["tmpfs"],
                )


if __name__ == "__main__":
    unittest.main()
