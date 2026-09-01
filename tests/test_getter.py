import contextlib
import io
import json
import os
import stat
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.getter import (
    DEFAULT_REMOTE,
    GetError,
    _parse_github_remote,
    fetch_profile,
    find_conflicts,
    format_get_plan,
)


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
                local=str(source_root),
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

            fetch_profile("demo", local=str(source_root), destination_root=destination_root)
            profile_file = destination_root / "profiles" / "demo" / "profile.yaml"
            profile_file.write_text("changed\n", encoding="utf-8")

            with self.assertRaises(GetError):
                fetch_profile("demo", local=str(source_root), destination_root=destination_root)

            fetch_profile(
                "demo",
                local=str(source_root),
                destination_root=destination_root,
                force=True,
            )
            self.assertIn("kind: Profile", profile_file.read_text(encoding="utf-8"))

    @unittest.skipIf(sys.platform.startswith("win"), "symlinks require elevated privileges on Windows")
    def test_fetch_profile_rejects_symlinked_destination_without_force(self) -> None:
        """A pre-planted symlink at a destination path (dangling or not) must be
        treated as a conflict -- Path.exists() follows symlinks and returns
        False for a dangling link, which would otherwise let it silently bypass
        the conflict check (#474)."""
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)

            profile_dest = destination_root / "profiles" / "demo" / "profile.yaml"
            profile_dest.parent.mkdir(parents=True, exist_ok=True)
            attack_target = destination_root / "outside-target.txt"
            attack_target.write_text("do not overwrite me\n", encoding="utf-8")
            profile_dest.symlink_to(attack_target)

            with self.assertRaises(GetError):
                fetch_profile("demo", local=str(source_root), destination_root=destination_root)

            # The symlink and its target must be untouched by the rejected attempt.
            self.assertTrue(profile_dest.is_symlink())
            self.assertEqual(attack_target.read_text(encoding="utf-8"), "do not overwrite me\n")

    @unittest.skipIf(sys.platform.startswith("win"), "symlinks require elevated privileges on Windows")
    def test_fetch_profile_never_writes_through_symlinked_destination_even_with_force(self) -> None:
        """Even with --force, a symlinked destination must be replaced with a
        regular file rather than written-through, so a symlink can never be
        used to redirect fetched content onto an arbitrary path (#474)."""
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)

            profile_dest = destination_root / "profiles" / "demo" / "profile.yaml"
            profile_dest.parent.mkdir(parents=True, exist_ok=True)
            attack_target = destination_root / "outside-target.txt"
            attack_target.write_text("do not overwrite me\n", encoding="utf-8")
            profile_dest.symlink_to(attack_target)

            fetch_profile(
                "demo",
                local=str(source_root),
                destination_root=destination_root,
                force=True,
            )

            # The destination is now a regular file containing the fetched
            # profile, and the symlink's former target is untouched.
            self.assertFalse(profile_dest.is_symlink())
            self.assertIn("kind: Profile", profile_dest.read_text(encoding="utf-8"))
            self.assertEqual(attack_target.read_text(encoding="utf-8"), "do not overwrite me\n")

    @unittest.skipIf(sys.platform.startswith("win"), "symlinks require elevated privileges on Windows")
    def test_fetch_profile_never_writes_manifest_through_symlinked_path(self) -> None:
        """The tracking manifest must not be written through a pre-planted
        symlink at its path either (#474)."""
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)

            manifest_path = destination_root / ".cds" / "get-manifest.json"
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            attack_target = destination_root / "outside-manifest-target.json"
            attack_target.write_text("do not overwrite me\n", encoding="utf-8")
            manifest_path.symlink_to(attack_target)

            fetch_profile("demo", local=str(source_root), destination_root=destination_root)

            self.assertFalse(manifest_path.is_symlink())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertIn("demo", manifest["profiles"])
            self.assertEqual(attack_target.read_text(encoding="utf-8"), "do not overwrite me\n")

    def test_fetch_profile_backs_up_and_warns_on_malformed_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)

            manifest_path = destination_root / ".cds" / "get-manifest.json"
            _write(manifest_path, "{not valid json")

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                _, returned_manifest_path = fetch_profile(
                    "demo",
                    local=str(source_root),
                    destination_root=destination_root,
                )

            self.assertEqual(returned_manifest_path.resolve(), manifest_path.resolve())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["profiles"]["demo"]["requestedProfile"], "demo")

            warning_output = stderr.getvalue()
            self.assertIn("WARNING", warning_output)
            self.assertIn("is malformed", warning_output)
            self.assertIn("invalid JSON", warning_output)

            backups = list(manifest_path.parent.glob("get-manifest.json.corrupt-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "{not valid json")
            self.assertIn(backups[0].name, warning_output)

    def test_fetch_profile_backs_up_and_warns_on_non_dict_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)

            manifest_path = destination_root / ".cds" / "get-manifest.json"
            _write(manifest_path, "[1, 2, 3]")

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                fetch_profile(
                    "demo",
                    local=str(source_root),
                    destination_root=destination_root,
                )

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["profiles"]["demo"]["requestedProfile"], "demo")

            warning_output = stderr.getvalue()
            self.assertIn("WARNING", warning_output)
            self.assertIn("is malformed", warning_output)
            self.assertIn("manifest root must be a JSON object", warning_output)

            backups = list(manifest_path.parent.glob("get-manifest.json.corrupt-*"))
            self.assertEqual(len(backups), 1)
            self.assertIn(backups[0].name, warning_output)

    def test_fetch_profile_warns_without_backup_when_manifest_unreadable(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)

            manifest_path = destination_root / ".cds" / "get-manifest.json"
            _write(manifest_path, "{}")

            original_read_text = Path.read_text

            def _raising_read_text(self: Path, *args: object, **kwargs: object) -> str:
                if self.resolve() == manifest_path.resolve():
                    raise OSError("permission denied")
                return original_read_text(self, *args, **kwargs)

            stderr = io.StringIO()
            with patch.object(Path, "read_text", _raising_read_text):
                with contextlib.redirect_stderr(stderr):
                    fetch_profile(
                        "demo",
                        local=str(source_root),
                        destination_root=destination_root,
                    )

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["profiles"]["demo"]["requestedProfile"], "demo")

            warning_output = stderr.getvalue()
            self.assertIn("WARNING", warning_output)
            self.assertIn("could not be read", warning_output)
            self.assertIn("permission denied", warning_output)
            self.assertNotIn("is malformed", warning_output)

            backups = list(manifest_path.parent.glob("get-manifest.json.corrupt-*"))
            self.assertEqual(len(backups), 0)

    def test_fetch_profile_loads_valid_manifest_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)

            manifest_path = destination_root / ".cds" / "get-manifest.json"
            _write(
                manifest_path,
                json.dumps(
                    {
                        "version": 1,
                        "profiles": {
                            "other": {"requestedProfile": "other", "files": []},
                        },
                    }
                ),
            )

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                fetch_profile(
                    "demo",
                    local=str(source_root),
                    destination_root=destination_root,
                )

            self.assertEqual(stderr.getvalue(), "")

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertIn("other", manifest["profiles"])
            self.assertEqual(manifest["profiles"]["demo"]["requestedProfile"], "demo")

            backups = list(manifest_path.parent.glob("get-manifest.json.corrupt-*"))
            self.assertEqual(len(backups), 0)

    def test_fetch_profile_dry_run_ignores_conflicting_files(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)

            fetch_profile("demo", local=str(source_root), destination_root=destination_root)
            profile_file = destination_root / "profiles" / "demo" / "profile.yaml"
            profile_file.write_text("changed\n", encoding="utf-8")

            actions, manifest_path = fetch_profile(
                "demo",
                local=str(source_root),
                destination_root=destination_root,
                dry_run=True,
            )

            self.assertGreater(len(actions), 0)
            self.assertEqual(manifest_path, destination_root.resolve() / ".cds" / "get-manifest.json")
            self.assertEqual(profile_file.read_text(encoding="utf-8"), "changed\n")

    def test_find_conflicts_reports_content_mismatch_after_dry_run(self) -> None:
        """find_conflicts() is read-only, so it can be run on the actions
        returned from a dry-run fetch_profile() call to report overwrite
        risk without writing anything or requiring --force (#452)."""
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)

            fetch_profile("demo", local=str(source_root), destination_root=destination_root)
            profile_file = destination_root / "profiles" / "demo" / "profile.yaml"
            profile_file.write_text("changed\n", encoding="utf-8")

            actions, _ = fetch_profile(
                "demo",
                local=str(source_root),
                destination_root=destination_root,
                dry_run=True,
            )

            conflicts = find_conflicts(actions)

            self.assertIn("profiles/demo/profile.yaml", conflicts)
            # find_conflicts() must not write or modify anything.
            self.assertEqual(profile_file.read_text(encoding="utf-8"), "changed\n")

    def test_format_get_plan_reports_conflicts_and_force_hint(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)

            fetch_profile("demo", local=str(source_root), destination_root=destination_root)
            (destination_root / "profiles" / "demo" / "profile.yaml").write_text("changed\n", encoding="utf-8")

            actions, _ = fetch_profile(
                "demo",
                local=str(source_root),
                destination_root=destination_root,
                dry_run=True,
            )
            conflicts = find_conflicts(actions)

            rendered = format_get_plan(actions, destination_root=destination_root, conflicts=conflicts)

            self.assertIn("would conflict", rendered)
            self.assertIn("--force", rendered)
            self.assertIn("profiles/demo/profile.yaml", rendered)

    def test_format_get_plan_omits_conflict_section_when_no_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)

            actions, _ = fetch_profile(
                "demo",
                local=str(source_root),
                destination_root=destination_root,
                dry_run=True,
            )

            rendered = format_get_plan(actions, destination_root=destination_root, conflicts=find_conflicts(actions))

            self.assertNotIn("would conflict", rendered)

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

            fetch_profile("demo", local=str(source_root), destination_root=destination_root)

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
                local=str(source_root),
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
                fetch_profile("demo", local=str(source_root), destination_root=destination_root)

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
                fetch_profile("demo", local=str(source_root), destination_root=destination_root)

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

            fetch_profile("demo", local=str(source_root), destination_root=destination_root)

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

            fetch_profile("demo", local=str(source_root), destination_root=destination_root)

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
                    local=str(source_root),
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
                fetch_profile("demo", local=str(source_root), destination_root=destination_root)

            self.assertIn('build.dockerfile "/etc/passwd" resolves outside the source repository', str(ctx.exception))

    @unittest.skipIf(sys.platform == "win32", "Windows does not preserve Unix executable permissions")
    def test_fetch_profile_preserves_executable_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)

            entrypoint = source_root / "images" / "demo" / "entrypoint.sh"
            os.chmod(entrypoint, 0o755)

            fetch_profile("demo", local=str(source_root), destination_root=destination_root)

            destination_entrypoint = destination_root / "images" / "demo" / "entrypoint.sh"
            mode = stat.S_IMODE(destination_entrypoint.stat().st_mode)
            self.assertEqual(mode, 0o755)


class GitHubRemoteTest(unittest.TestCase):
    def _make_tarball(self, root: Path) -> bytes:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            archive.add(root, arcname="owner-demo-repo-abcdef1")
        return buffer.getvalue()

    def test_parse_github_remote_accepts_shorthand_and_urls(self) -> None:
        self.assertEqual(_parse_github_remote("owner/repo"), ("owner", "repo"))
        self.assertEqual(
            _parse_github_remote("https://github.com/owner/repo"), ("owner", "repo")
        )
        self.assertEqual(
            _parse_github_remote("https://github.com/owner/repo.git"), ("owner", "repo")
        )
        self.assertEqual(
            _parse_github_remote("git@github.com:owner/repo.git"), ("owner", "repo")
        )
        self.assertIsNone(_parse_github_remote("not a remote at all"))

    def test_fetch_profile_downloads_default_remote_when_no_remote_given(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)
            archive_bytes = self._make_tarball(source_root)

            class _FakeResponse:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *exc_info):
                    return False

                def read(self_inner):
                    return archive_bytes

            captured_urls: list[str] = []

            def _fake_urlopen(request, timeout=30):
                captured_urls.append(request.full_url)
                return _FakeResponse()

            with patch("cli.getter.urlopen", side_effect=_fake_urlopen):
                actions, manifest_path = fetch_profile(
                    "demo",
                    destination_root=destination_root,
                )

            self.assertEqual(
                captured_urls,
                [f"https://api.github.com/repos/{DEFAULT_REMOTE}/tarball/main"],
            )
            self.assertGreater(len(actions), 0)
            self.assertTrue((destination_root / "profiles" / "demo" / "profile.yaml").exists())

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entry = manifest["profiles"]["demo"]
            self.assertEqual(entry["remote"], DEFAULT_REMOTE)
            self.assertEqual(entry["ref"], "main")

    def test_fetch_profile_downloads_explicit_owner_repo_and_ref(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)
            archive_bytes = self._make_tarball(source_root)

            class _FakeResponse:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *exc_info):
                    return False

                def read(self_inner):
                    return archive_bytes

            def _fake_urlopen(request, timeout=30):
                return _FakeResponse()

            with patch("cli.getter.urlopen", side_effect=_fake_urlopen):
                actions, _ = fetch_profile(
                    "demo",
                    remote="RonaldHensbergen/composable-data-stack",
                    ref="v1.2.3",
                    destination_root=destination_root,
                )

            self.assertGreater(len(actions), 0)
            self.assertTrue((destination_root / "modules" / "apps" / "demo" / "module.yaml").exists())

    def test_fetch_profile_raises_get_error_on_download_failure(self) -> None:
        from urllib.error import URLError

        def _fake_urlopen(request, timeout=30):
            raise URLError("network unreachable")

        with tempfile.TemporaryDirectory() as dest_dir:
            with patch("cli.getter.urlopen", side_effect=_fake_urlopen):
                with self.assertRaises(GetError) as ctx:
                    fetch_profile("demo", destination_root=Path(dest_dir))

            self.assertIn("Could not download", str(ctx.exception))

    def test_fetch_profile_rejects_unresolvable_remote(self) -> None:
        with tempfile.TemporaryDirectory() as dest_dir:
            with self.assertRaises(GetError) as ctx:
                fetch_profile(
                    "demo",
                    remote="this is not a remote",
                    destination_root=Path(dest_dir),
                )
            self.assertIn("Could not resolve remote", str(ctx.exception))


    def test_fetch_profile_rejects_both_remote_and_local(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)

            with self.assertRaises(GetError) as ctx:
                fetch_profile(
                    "demo",
                    remote="owner/repo",
                    local=str(source_root),
                    destination_root=destination_root,
                )
            self.assertIn("Specify only one of --remote and --local", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
