import re
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

MIN_SAFE_DAGSTER_IMAGE_DEPENDENCIES = {
    "msgpack": (1, 2, 1),
    "setuptools": (78, 1, 1),
}


class DagsterHardeningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent
        self.dockerfile = (self.repo_root / "images" / "dagster" / "base" / "Dockerfile").read_text(encoding="utf-8")
        self.entrypoint = (self.repo_root / "images" / "dagster" / "entrypoint.sh").read_text(encoding="utf-8")
        self.requirements = (self.repo_root / "images" / "dagster" / "requirements.txt").read_text(encoding="utf-8")
        self.workspace = yaml.safe_load(
            (self.repo_root / "images" / "dagster" / "workspace.yaml").read_text(encoding="utf-8")
        )
        module = yaml.safe_load(
            (self.repo_root / "modules" / "orchestration" / "dagster" / "module.yaml").read_text(encoding="utf-8")
        )
        self.config_schema = module["spec"]["configSchema"]
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
        self.assertIn("HOME=/opt/dagster/dagster_home", self.dockerfile)

    def test_backend_controls_build_and_runtime(self) -> None:
        for name in ("user-code", "dagster-webserver", "dagster-daemon"):
            with self.subTest(service=name):
                service = self.services[name]
                self.assertEqual(service["build"]["args"]["DB_BACKEND"], "${config.storage.backend}")
                self.assertEqual(service["environment"]["DB_BACKEND"], "${config.storage.backend}")

    def test_vulnerable_python_packages_are_patched(self) -> None:
        pinned: dict[str, tuple[int, ...]] = {}
        for line in self.requirements.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            match = re.match(r"^([A-Za-z0-9._-]+)\s*(>=|==)\s*([0-9]+(?:\.[0-9]+)*)", line)
            if match:
                pkg, _, ver = match.groups()
                normalized_pkg = pkg.lower()
                version = tuple(int(x) for x in ver.strip().split("."))
                pinned[normalized_pkg] = max(pinned.get(normalized_pkg, (0,)), version)

        for pkg, min_ver in MIN_SAFE_DAGSTER_IMAGE_DEPENDENCIES.items():
            with self.subTest(package=pkg):
                self.assertIn(pkg, pinned, msg=f"{pkg} must be pinned in images/dagster/requirements.txt")
                self.assertGreaterEqual(
                    pinned[pkg],
                    min_ver,
                    msg=f"{pkg} must be >={'.'.join(str(part) for part in min_ver)} to fix known CVEs",
                )

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
                socket_mount = next(
                    volume
                    for volume in volumes
                    if volume == "dagster-grpc-socket:/var/run/dagster"
                    or (
                        isinstance(volume, dict)
                        and volume.get("source") == "dagster-grpc-socket"
                        and volume.get("target") == "/var/run/dagster"
                    )
                )
                if name == "user-code":
                    self.assertEqual(socket_mount, "dagster-grpc-socket:/var/run/dagster")
                else:
                    self.assertTrue(socket_mount["read_only"])

        user_code_volumes = self.services["user-code"]["volumes"]
        definitions_mount = next(
            volume
            for volume in user_code_volumes
            if isinstance(volume, dict)
            and volume.get("target") == "${config.definitionsFile.containerPath}"
        )
        self.assertTrue(definitions_mount["read_only"])

    def test_definitions_mount_target_must_be_absolute(self) -> None:
        target_schema = self.config_schema["properties"]["definitionsFile"]["properties"]["containerPath"]
        validator = Draft202012Validator(target_schema)

        relative_errors = list(validator.iter_errors("workdirs/dagster/definitions.py"))
        absolute_errors = list(validator.iter_errors("/app/workdirs/dagster/definitions.py"))

        self.assertEqual(len(relative_errors), 1)
        self.assertEqual(absolute_errors, [])

    def test_user_code_uses_shared_unix_socket(self) -> None:
        user_code = self.services["user-code"]
        grpc_server = self.workspace["load_from"][0]["grpc_server"]

        self.assertIn("-s", user_code["command"])
        self.assertIn("/var/run/dagster/user-code.sock", user_code["command"])
        self.assertNotIn("-p", user_code["command"])
        self.assertEqual(grpc_server["socket"], "/var/run/dagster/user-code.sock")
        self.assertNotIn("host", grpc_server)
        self.assertNotIn("port", grpc_server)


class DagsterHardenedVariantTest(unittest.TestCase):
    """Mirrors DagsterHardeningTest's key invariants for the Alpine-based
    images/dagster/hardened/Dockerfile, so the hardened variant can't silently
    regress (e.g. lose its digest pin) without a test failing."""

    def setUp(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        self.dockerfile = (repo_root / "images" / "dagster" / "hardened" / "Dockerfile").read_text(encoding="utf-8")

    def test_base_image_is_digest_pinned(self) -> None:
        from_lines = re.findall(r"^FROM\s+(\S+)", self.dockerfile, flags=re.MULTILINE)

        self.assertTrue(from_lines, "expected at least one FROM line")
        for image in from_lines:
            with self.subTest(image=image):
                self.assertRegex(image, r"^[^@]+@sha256:[0-9a-f]{64}$")

    def test_image_has_minimal_immutable_runtime(self) -> None:
        users = re.findall(r"^USER\s+(\S+)$", self.dockerfile, flags=re.MULTILINE)

        self.assertEqual(users[-1], "dagster")
        self.assertNotIn("COPY . /app", self.dockerfile)

    def test_pip_bundled_packages_are_removed_from_runtime_stage(self) -> None:
        # python:3.14-alpine ships pip together with its transitive dependencies
        # (CacheControl -> msgpack) and setuptools in the system Python. These
        # packages have known CVEs and are not needed at runtime (the app uses
        # /opt/venv). Uninstalling pip itself removes its vendored msgpack and
        # setuptools copies too, so the runtime stage only needs to uninstall
        # pip — not each vendored package individually — to keep them out of
        # the Trivy vulnerability scan.
        uninstall_lines = [
            line.strip() for line in self.dockerfile.splitlines()
            if "pip" in line and "uninstall" in line
        ]
        full_uninstall = " ".join(uninstall_lines)
        self.assertIn(
            "pip",
            full_uninstall,
            msg="'pip' must be uninstalled in the runtime stage to remove its vendored msgpack/setuptools copies",
        )



if __name__ == "__main__":
    unittest.main()
