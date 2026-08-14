import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from cli.getter import GetError, fetch_profile


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_source_repo(root: Path) -> None:
    _write(
        root / "profiles" / "demo" / "profile.yaml",
        """apiVersion: cds/v1alpha1
kind: Profile
metadata:
  name: demo
spec:
  runtime:
    type: docker-compose
  modules:
    - id: demo
      source: ../../modules/apps/demo
      config:
        sharedDataPath: ./workdirs/shared-data
""",
    )
    _write(root / "profiles" / "demo" / "workdirs" / "shared-data" / "README.txt", "profile data\n")
    _write(root / "profiles" / "demo" / ".env", "SHOULD_NOT_COPY=true\n")
    _write(
        root / "modules" / "apps" / "demo" / "module.yaml",
        """apiVersion: cds/v1alpha1
kind: Module
metadata:
  name: demo
spec:
  implementation:
    kind: docker-compose
    compose:
      services:
        app:
          build:
            context: ../../../
            dockerfile: images/demo/Dockerfile
""",
    )
    _write(
        root / "images" / "demo" / "Dockerfile",
        """FROM python:3.14-slim
COPY shared/python /app/shared/python
COPY workdirs/demo /app/workdirs/demo
COPY images/demo/entrypoint.sh /entrypoint.sh
""",
    )
    _write(root / "images" / "demo" / "entrypoint.sh", "#!/bin/sh\n")
    _write(root / "shared" / "python" / "__init__.py", "")
    _write(root / "workdirs" / "demo" / "definitions.py", "defs = []\n")
    _write(root / ".github" / "workflows" / "ci.yml", "name: CI\n")
    _write(root / ".env.example", "EXAMPLE=true\n")


class GetterTest(unittest.TestCase):
    def test_fetch_profile_copies_profile_module_and_build_assets(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)

            actions, manifest_path = fetch_profile(
                "demo",
                remote=str(source_root),
                destination_root=destination_root,
            )

            self.assertGreater(len(actions), 0)
            self.assertTrue((destination_root / "profiles" / "demo" / "profile.yaml").exists())
            self.assertTrue((destination_root / "profiles" / "demo" / "workdirs" / "shared-data" / "README.txt").exists())
            self.assertTrue((destination_root / "modules" / "apps" / "demo" / "module.yaml").exists())
            self.assertTrue((destination_root / "images" / "demo" / "Dockerfile").exists())
            self.assertTrue((destination_root / "images" / "demo" / "entrypoint.sh").exists())
            self.assertTrue((destination_root / "shared" / "python" / "__init__.py").exists())
            self.assertTrue((destination_root / "workdirs" / "demo" / "definitions.py").exists())
            self.assertFalse((destination_root / "profiles" / "demo" / ".env").exists())

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entry = manifest["profiles"]["demo"]
            self.assertEqual(entry["requestedProfile"], "demo")
            self.assertEqual(entry["sourceProfile"], "profiles/demo/profile.yaml")
            self.assertIn("modules/apps/demo", entry["assetRoots"])
            self.assertIn("images/demo/Dockerfile", entry["assetRoots"])

    def test_fetch_profile_requires_force_for_conflicting_files(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)

            fetch_profile("demo", remote=str(source_root), destination_root=destination_root)
            profile_file = destination_root / "profiles" / "demo" / "profile.yaml"
            profile_file.write_text("changed\n", encoding="utf-8")

            with self.assertRaises(GetError):
                fetch_profile("demo", remote=str(source_root), destination_root=destination_root)

            fetch_profile(
                "demo",
                remote=str(source_root),
                destination_root=destination_root,
                force=True,
            )
            self.assertIn("kind: Profile", profile_file.read_text(encoding="utf-8"))

    def test_fetch_profile_dry_run_ignores_conflicting_files(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)

            fetch_profile("demo", remote=str(source_root), destination_root=destination_root)
            profile_file = destination_root / "profiles" / "demo" / "profile.yaml"
            profile_file.write_text("changed\n", encoding="utf-8")

            actions, manifest_path = fetch_profile(
                "demo",
                remote=str(source_root),
                destination_root=destination_root,
                dry_run=True,
            )

            self.assertGreater(len(actions), 0)
            self.assertEqual(
                manifest_path,
                (destination_root / ".cds" / "get-manifest.json").resolve(),
            )
            self.assertEqual(profile_file.read_text(encoding="utf-8"), "changed\n")

    def test_fetch_profile_resolves_templated_dockerfile_from_module_config_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _write(
                source_root / "profiles" / "demo" / "profile.yaml",
                """apiVersion: cds/v1alpha1
kind: Profile
metadata:
  name: demo
spec:
  runtime:
    type: docker-compose
  modules:
    - id: demo
      source: ../../modules/apps/demo
      config:
        image:
          variant: hardened
""",
            )
            _write(
                source_root / "modules" / "apps" / "demo" / "module.yaml",
                """apiVersion: cds/v1alpha1
kind: Module
metadata:
  name: demo
spec:
  configSchema:
    type: object
    additionalProperties: false
    default: {}
    properties:
      image:
        type: object
        additionalProperties: false
        default: {}
        properties:
          variant:
            type: string
            default: base
  implementation:
    kind: docker-compose
    compose:
      services:
        app:
          build:
            context: ../../../
            dockerfile: images/demo/${config.image.variant}/Dockerfile
""",
            )
            _write(
                source_root / "images" / "demo" / "hardened" / "Dockerfile",
                """FROM python:3.14-slim
COPY shared/python /app/shared/python
""",
            )
            _write(source_root / "shared" / "python" / "__init__.py", "")
            _write(source_root / ".github" / "workflows" / "ci.yml", "name: CI\n")
            _write(source_root / ".env.example", "EXAMPLE=true\n")

            fetch_profile("demo", remote=str(source_root), destination_root=destination_root)

            self.assertTrue(
                (destination_root / "images" / "demo" / "hardened" / "Dockerfile").exists()
            )
            self.assertTrue((destination_root / "shared" / "python" / "__init__.py").exists())
            self.assertFalse((destination_root / ".github" / "workflows" / "ci.yml").exists())
            self.assertFalse((destination_root / ".env.example").exists())

    def test_fetch_profile_supports_single_file_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _write(
                source_root / "profiles" / "demo.yaml",
                """apiVersion: cds/v1alpha1
kind: Profile
metadata:
  name: demo
spec:
  runtime:
    type: docker-compose
  modules:
    - id: demo
      source: ../modules/apps/demo
""",
            )
            _write(
                source_root / "modules" / "apps" / "demo" / "module.yaml",
                """apiVersion: cds/v1alpha1
kind: Module
metadata:
  name: demo
spec:
  implementation:
    kind: docker-compose
    compose:
      services:
        app:
          image: demo:latest
""",
            )

            _, manifest_path = fetch_profile(
                "demo",
                remote=str(source_root),
                destination_root=destination_root,
            )

            self.assertTrue((destination_root / "profiles" / "demo.yaml").exists())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["profiles"]["demo"]["sourceProfile"], "profiles/demo.yaml")

    def test_fetch_profile_reports_invalid_json_copy_instruction_as_get_error(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _write(
                source_root / "profiles" / "demo" / "profile.yaml",
                """apiVersion: cds/v1alpha1
kind: Profile
metadata:
  name: demo
spec:
  runtime:
    type: docker-compose
  modules:
    - id: demo
      source: ../../modules/apps/demo
""",
            )
            _write(
                source_root / "modules" / "apps" / "demo" / "module.yaml",
                """apiVersion: cds/v1alpha1
kind: Module
metadata:
  name: demo
spec:
  implementation:
    kind: docker-compose
    compose:
      services:
        app:
          build:
            context: ../../../
            dockerfile: images/demo/Dockerfile
""",
            )
            _write(
                source_root / "images" / "demo" / "Dockerfile",
                """FROM python:3.14-slim
COPY ["shared/python", dest]
""",
            )

            with self.assertRaises(GetError) as ctx:
                fetch_profile("demo", remote=str(source_root), destination_root=destination_root)

            self.assertIn("Could not parse COPY sources", str(ctx.exception))

    def test_fetch_profile_reports_invalid_shell_copy_instruction_as_get_error(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _write(
                source_root / "profiles" / "demo" / "profile.yaml",
                """apiVersion: cds/v1alpha1
kind: Profile
metadata:
  name: demo
spec:
  runtime:
    type: docker-compose
  modules:
    - id: demo
      source: ../../modules/apps/demo
""",
            )
            _write(
                source_root / "modules" / "apps" / "demo" / "module.yaml",
                """apiVersion: cds/v1alpha1
kind: Module
metadata:
  name: demo
spec:
  implementation:
    kind: docker-compose
    compose:
      services:
        app:
          build:
            context: ../../../
            dockerfile: images/demo/Dockerfile
""",
            )
            _write(
                source_root / "images" / "demo" / "Dockerfile",
                """FROM python:3.14-slim
COPY "shared/python /app/
""",
            )

            with self.assertRaises(GetError) as ctx:
                fetch_profile("demo", remote=str(source_root), destination_root=destination_root)

            self.assertIn("Could not parse COPY sources", str(ctx.exception))

    def test_fetch_profile_preserves_hash_characters_inside_quoted_copy_sources(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _write(
                source_root / "profiles" / "demo" / "profile.yaml",
                """apiVersion: cds/v1alpha1
kind: Profile
metadata:
  name: demo
spec:
  runtime:
    type: docker-compose
  modules:
    - id: demo
      source: ../../modules/apps/demo
""",
            )
            _write(
                source_root / "modules" / "apps" / "demo" / "module.yaml",
                """apiVersion: cds/v1alpha1
kind: Module
metadata:
  name: demo
spec:
  implementation:
    kind: docker-compose
    compose:
      services:
        app:
          build:
            context: ../../../
            dockerfile: images/demo/Dockerfile
""",
            )
            _write(
                source_root / "images" / "demo" / "Dockerfile",
                """FROM python:3.14-slim
COPY "shared/file#1.txt" /app/
""",
            )
            _write(source_root / "shared" / "file#1.txt", "ok\n")

            fetch_profile("demo", remote=str(source_root), destination_root=destination_root)

            self.assertTrue((destination_root / "shared" / "file#1.txt").exists())

    def test_fetch_profile_skips_copy_from_stage_sources(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _write(
                source_root / "profiles" / "demo" / "profile.yaml",
                """apiVersion: cds/v1alpha1
kind: Profile
metadata:
  name: demo
spec:
  runtime:
    type: docker-compose
  modules:
    - id: demo
      source: ../../modules/apps/demo
""",
            )
            _write(
                source_root / "modules" / "apps" / "demo" / "module.yaml",
                """apiVersion: cds/v1alpha1
kind: Module
metadata:
  name: demo
spec:
  implementation:
    kind: docker-compose
    compose:
      services:
        app:
          build:
            context: ../../../
            dockerfile: images/demo/Dockerfile
""",
            )
            _write(
                source_root / "images" / "demo" / "Dockerfile",
                """FROM python:3.14-slim AS builder
RUN mkdir -p /opt/venv
FROM python:3.14-slim
COPY --from=builder /opt/venv /opt/venv
COPY shared/python /app/shared/python
""",
            )
            _write(source_root / "shared" / "python" / "__init__.py", "")

            fetch_profile("demo", remote=str(source_root), destination_root=destination_root)

            self.assertTrue((destination_root / "images" / "demo" / "Dockerfile").exists())
            self.assertTrue((destination_root / "shared" / "python" / "__init__.py").exists())

    def test_fetch_profile_rejects_profile_paths_outside_source_repo(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            outside_root = source_root.parent / "outside"
            outside_root.mkdir(parents=True, exist_ok=True)
            _write(
                outside_root / "evil.yaml",
                """apiVersion: cds/v1alpha1
kind: Profile
metadata:
  name: evil
spec:
  runtime:
    type: docker-compose
  modules: []
""",
            )
            (source_root / "profiles").mkdir(parents=True, exist_ok=True)

            with self.assertRaises(GetError) as ctx:
                fetch_profile(
                    f"../{outside_root.name}/evil.yaml",
                    remote=str(source_root),
                    destination_root=destination_root,
                )

            self.assertIn('profile "../outside/evil.yaml" resolves outside the source repository', str(ctx.exception))

    def test_fetch_profile_rejects_absolute_dockerfile_outside_source_repo(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _write(
                source_root / "profiles" / "demo" / "profile.yaml",
                """apiVersion: cds/v1alpha1
kind: Profile
metadata:
  name: demo
spec:
  runtime:
    type: docker-compose
  modules:
    - id: demo
      source: ../../modules/apps/demo
""",
            )
            _write(
                source_root / "modules" / "apps" / "demo" / "module.yaml",
                """apiVersion: cds/v1alpha1
kind: Module
metadata:
  name: demo
spec:
  implementation:
    kind: docker-compose
    compose:
      services:
        app:
          build:
            context: ../../../
            dockerfile: /etc/passwd
""",
            )

            with self.assertRaises(GetError) as ctx:
                fetch_profile("demo", remote=str(source_root), destination_root=destination_root)

            self.assertIn('build.dockerfile "/etc/passwd" resolves outside the source repository', str(ctx.exception))

    def test_fetch_profile_preserves_executable_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)

            entrypoint = source_root / "images" / "demo" / "entrypoint.sh"
            os.chmod(entrypoint, 0o755)

            fetch_profile("demo", remote=str(source_root), destination_root=destination_root)

            source_mode = stat.S_IMODE(entrypoint.stat().st_mode)
            destination_entrypoint = destination_root / "images" / "demo" / "entrypoint.sh"
            mode = stat.S_IMODE(destination_entrypoint.stat().st_mode)
            self.assertEqual(mode, source_mode)
            if os.name != "nt":
                self.assertEqual(mode, 0o755)


if __name__ == "__main__":
    unittest.main()
