import contextlib
import hashlib
import io
import json
import os
import shutil
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
    _extract_dockerfile_instruction_sources,
    _parse_github_remote,
    fetch_profile,
    format_get_plan,
    parse_allowed_sources,
    update_tracked_assets,
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
    def test_manifest_tracks_assets_dependencies_and_file_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)
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
    - id: provider
      source: ../../modules/apps/demo
      config: {}
    - id: consumer
      source: ../../modules/apps/demo
      dependsOn: [provider]
      config:
        database:
          contractRef: provider.demo-output
""",
            )
            module_path = source_root / "modules" / "apps" / "demo" / "module.yaml"
            _write(
                module_path,
                """apiVersion: cds/v1alpha1
kind: Module
metadata:
  name: demo
spec:
  provides:
    - name: demo-output
      contract:
        kind: sql-database
  consumes:
    - name: database
      contract:
        kind: sql-database
    # Same name as the provided contract: the two records must stay
    # addressable separately (the asset id includes the direction).
    - name: demo-output
      contract:
        kind: sql-database
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

            _actions, manifest_path, _conflicts = fetch_profile(
                "demo",
                local=str(source_root),
                destination_root=destination_root,
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entry = manifest["profiles"]["demo"]
            assets = {(asset["type"], asset["id"]): asset for asset in entry["assets"]}

            self.assertEqual(manifest["version"], 2)
            self.assertEqual(Path(entry["remote"]), source_root.resolve())
            self.assertIn(("profile", "demo"), assets)
            self.assertIn(("module", "provider"), assets)
            self.assertIn(("module", "consumer"), assets)
            self.assertEqual(
                assets[("module", "consumer")]["dependencies"],
                [
                    # The contractRef "<module>.<name>" maps to the provided
                    # contract's full asset id.
                    {"type": "contract", "id": "provider.demo-output.provided"},
                    {"type": "module", "id": "provider"},
                ],
            )
            self.assertEqual(
                assets[("contract", "provider.demo-output.provided")]["direction"],
                "provided",
            )
            self.assertEqual(
                assets[("contract", "consumer.database.consumed")]["direction"],
                "consumed",
            )
            # The module provides and consumes a contract with the same
            # name: both records exist with direction-suffixed ids.
            self.assertEqual(
                assets[("contract", "provider.demo-output.consumed")]["direction"],
                "consumed",
            )
            self.assertEqual(
                assets[("module", "consumer")]["destinationPath"],
                "modules/apps/demo",
            )

            local_profile = destination_root / "profiles" / "demo" / "profile.yaml"
            self.assertEqual(
                entry["fileHashes"]["profiles/demo/profile.yaml"],
                hashlib.sha256(local_profile.read_bytes()).hexdigest(),
            )

    def test_update_refreshes_selected_modules_without_overwriting_local_edits(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)
            fetch_profile("demo", local=str(source_root), destination_root=destination_root)

            local_profile = destination_root / "profiles" / "demo" / "profile.yaml"
            original_profile = local_profile.read_text(encoding="utf-8")
            local_module = destination_root / "modules" / "apps" / "demo" / "module.yaml"
            local_module.write_text("local customization\n", encoding="utf-8")
            source_module = source_root / "modules" / "apps" / "demo" / "module.yaml"
            source_module.write_text(
                source_module.read_text(encoding="utf-8") + "# upstream change\n",
                encoding="utf-8",
            )
            source_profile = source_root / "profiles" / "demo" / "profile.yaml"
            source_profile.write_text(
                source_profile.read_text(encoding="utf-8") + "# upstream change\n",
                encoding="utf-8",
            )

            result = update_tracked_assets(
                destination_root=destination_root,
                categories=["modules"],
            )

            self.assertEqual(local_module.read_text(encoding="utf-8"), "local customization\n")
            self.assertEqual(local_profile.read_text(encoding="utf-8"), original_profile)
            self.assertTrue(any("modules/apps/demo/module.yaml" in item for item in result.conflicts))
            self.assertFalse(any("profiles/demo/profile.yaml" in item for item in result.updated))
            self.assertFalse(result.errors)

    def test_update_dry_run_does_not_change_files_or_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)
            _, manifest_path, _ = fetch_profile(
                "demo", local=str(source_root), destination_root=destination_root
            )
            module_path = source_root / "modules" / "apps" / "demo" / "module.yaml"
            module_path.write_text(
                module_path.read_text(encoding="utf-8") + "# new upstream module\n",
                encoding="utf-8",
            )
            local_module = destination_root / "modules" / "apps" / "demo" / "module.yaml"
            original_local = local_module.read_text(encoding="utf-8")
            original_manifest = manifest_path.read_bytes()

            result = update_tracked_assets(
                profile="demo",
                destination_root=destination_root,
                categories=["modules"],
                dry_run=True,
            )

            self.assertTrue(any("would update" in item for item in result.updated))
            self.assertEqual(local_module.read_text(encoding="utf-8"), original_local)
            self.assertEqual(manifest_path.read_bytes(), original_manifest)
            self.assertFalse(result.errors)

    def test_update_contracts_refreshes_shared_contract_files_only(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)
            _write(
                source_root / "images" / "demo" / "Dockerfile",
                "FROM python:3.14-slim\nCOPY shared/contracts /app/contracts\n",
            )
            _write(source_root / "shared" / "contracts" / "demo.yaml", "version: old\n")
            fetch_profile("demo", local=str(source_root), destination_root=destination_root)

            _write(source_root / "shared" / "contracts" / "demo.yaml", "version: new\n")
            local_contract = destination_root / "shared" / "contracts" / "demo.yaml"
            local_profile = destination_root / "profiles" / "demo" / "profile.yaml"
            profile_before = local_profile.read_bytes()
            result = update_tracked_assets(
                destination_root=destination_root,
                categories=["contracts"],
            )

            self.assertEqual(local_contract.read_text(encoding="utf-8"), "version: new\n")
            self.assertEqual(local_profile.read_bytes(), profile_before)
            self.assertTrue(any("shared/contracts/demo.yaml" in item for item in result.updated))
            self.assertFalse(result.errors)

    def test_update_reports_manifest_and_selection_errors(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)
            fetch_profile("demo", local=str(source_root), destination_root=destination_root)

            with tempfile.TemporaryDirectory() as empty_dir:
                empty_root = Path(empty_dir)
                with self.assertRaisesRegex(
                    GetError, "No fetched-asset tracking manifest found"
                ):
                    update_tracked_assets(destination_root=empty_root)

                manifest_path = empty_root / ".cds" / "get-manifest.json"
                _write(manifest_path, json.dumps({"version": 2, "profiles": {}}))
                with self.assertRaisesRegex(GetError, "No tracked profiles found"):
                    update_tracked_assets(destination_root=empty_root)

            with self.assertRaisesRegex(GetError, "Unsupported update category: images"):
                update_tracked_assets(
                    destination_root=destination_root, categories=["profiles", "images"]
                )

            with self.assertRaisesRegex(GetError, 'No fetched profile matching "missing"'):
                update_tracked_assets(
                    profile="missing", destination_root=destination_root
                )

            # The source profile path doubles as a selection alias.
            result = update_tracked_assets(
                profile="profiles/demo/profile.yaml", destination_root=destination_root
            )
            self.assertFalse(result.errors)

    def test_update_surfaces_invalid_tracking_entries_as_profile_errors(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)
            fetch_profile("demo", local=str(source_root), destination_root=destination_root)
            manifest_path = destination_root / ".cds" / "get-manifest.json"

            def _tamper(mutate) -> None:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                mutate(manifest["profiles"]["demo"])
                manifest_path.write_text(
                    json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
                )
                result = update_tracked_assets(destination_root=destination_root)
                self.assertTrue(result.errors, "expected a profile error")

            _tamper(lambda entry: entry.update(requestedProfile=123))
            _tamper(lambda entry: entry.update(remote=""))
            _tamper(lambda entry: entry.update(ref=123))
            _tamper(lambda entry: entry.update(ref=""))
            _tamper(lambda entry: entry.pop("fileHashes"))
            _tamper(lambda entry: entry.update(assets={}))
            _tamper(lambda entry: entry.update(pinnedCommit=123))
            _tamper(lambda entry: entry.update(remote="relative/path", ref=None))

            # A non-object entry is reported per profile instead of crashing.
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["profiles"]["demo"] = "not an object"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            result = update_tracked_assets(destination_root=destination_root)
            self.assertTrue(any("not an object" in item for item in result.errors))

    def test_update_rejects_unsafe_tracked_paths(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)
            fetch_profile("demo", local=str(source_root), destination_root=destination_root)
            manifest_path = destination_root / ".cds" / "get-manifest.json"

            for source_path in ("../evil", "/etc/passwd"):
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["profiles"]["demo"]["assets"] = [
                    {"type": "module", "sourcePath": source_path}
                ]
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                result = update_tracked_assets(
                    destination_root=destination_root, categories=["modules"]
                )
                self.assertTrue(
                    any("unsafe" in item.lower() for item in result.errors), source_path
                )

            # A tracked file path escaping the destination is rejected the
            # same way when removed-upstream reporting walks it.
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entry = manifest["profiles"]["demo"]
            entry["fileHashes"]["../evil.txt"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            result = update_tracked_assets(destination_root=destination_root)
            self.assertTrue(any("unsafe" in item.lower() for item in result.errors))

    def test_update_reports_symlinked_and_directory_destinations_as_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)
            fetch_profile("demo", local=str(source_root), destination_root=destination_root)
            source_module = source_root / "modules" / "apps" / "demo" / "module.yaml"
            source_module.write_text(
                source_module.read_text(encoding="utf-8") + "# upstream change\n",
                encoding="utf-8",
            )

            local_module = destination_root / "modules" / "apps" / "demo" / "module.yaml"
            local_module.unlink()
            local_module.mkdir()
            result = update_tracked_assets(destination_root=destination_root)
            self.assertTrue(
                any("(destination is a directory)" in item for item in result.conflicts)
            )
            local_module.rmdir()

            if not sys.platform.startswith("win"):
                # A symlink at the destination is a conflict, never a
                # write-through.
                outside = destination_root.parent / f"{destination_root.name}-outside.txt"
                _write(outside, "do not touch\n")
                local_module.symlink_to(outside)
                result = update_tracked_assets(destination_root=destination_root)
                self.assertTrue(
                    any("(destination is a symlink)" in item for item in result.conflicts)
                )
                self.assertEqual(
                    outside.read_text(encoding="utf-8"), "do not touch\n"
                )
                local_module.unlink()
                _write(local_module, "local customization\n")

                # A symlinked parent directory is a conflict too.
                local_profile_dir = destination_root / "profiles" / "demo"
                real_profile = local_profile_dir / "profile.yaml"
                content = real_profile.read_text(encoding="utf-8")
                shutil.rmtree(local_profile_dir)
                redirected = destination_root.parent / f"{destination_root.name}-redirected"
                redirected.mkdir()
                (redirected / "profile.yaml").write_text(content, encoding="utf-8")
                local_profile_dir.symlink_to(redirected)
                result = update_tracked_assets(destination_root=destination_root)
                self.assertTrue(
                    any(
                        "(symlinked parent directory)" in item
                        for item in result.conflicts
                    )
                )

                # A symlinked tracking manifest is refused outright.
                manifest_path = destination_root / ".cds" / "get-manifest.json"
                shutil.move(str(manifest_path), str(manifest_path) + ".real")
                manifest_path.symlink_to(str(manifest_path) + ".real")
                with self.assertRaisesRegex(
                    GetError, "Refusing to update through symlinked tracking metadata"
                ):
                    update_tracked_assets(destination_root=destination_root)

    def test_update_reports_removed_upstream_files(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)
            fetch_profile("demo", local=str(source_root), destination_root=destination_root)

            removed = "images/demo/entrypoint.sh"
            (source_root / removed).unlink()
            local_removed = destination_root / removed

            # Unmodified local copy: reported and left in place.
            result = update_tracked_assets(destination_root=destination_root)
            self.assertTrue(
                any(
                    item.endswith(f"{removed} (removed upstream; left in place)")
                    for item in result.skipped
                )
            )
            self.assertTrue(local_removed.is_file())

            # Modified local copy: a conflict, still left in place.
            _write(local_removed, "local edit\n")
            result = update_tracked_assets(destination_root=destination_root)
            self.assertTrue(
                any(
                    item.endswith(f"{removed} (removed upstream and modified locally)")
                    for item in result.conflicts
                )
            )
            self.assertEqual(local_removed.read_text(encoding="utf-8"), "local edit\n")

            # Already absent locally: just reported as skipped.
            local_removed.unlink()
            result = update_tracked_assets(destination_root=destination_root)
            self.assertTrue(
                any(
                    item.endswith(f"{removed} (already absent locally)")
                    for item in result.skipped
                )
            )

            # A directory where the tracked file used to be: a conflict.
            local_removed.mkdir()
            result = update_tracked_assets(destination_root=destination_root)
            self.assertTrue(
                any(
                    "(removed upstream; destination is a directory)" in item
                    for item in result.conflicts
                )
            )

    def test_update_reports_locally_deleted_tracked_file_as_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)
            fetch_profile("demo", local=str(source_root), destination_root=destination_root)

            source_module = source_root / "modules" / "apps" / "demo" / "module.yaml"
            source_module.write_text(
                source_module.read_text(encoding="utf-8") + "# upstream change\n",
                encoding="utf-8",
            )
            local_module = destination_root / "modules" / "apps" / "demo" / "module.yaml"
            local_module.unlink()

            result = update_tracked_assets(destination_root=destination_root)
            self.assertTrue(
                any(
                    "(tracked file was deleted locally)" in item
                    for item in result.conflicts
                )
            )
            self.assertFalse(local_module.exists())

    def test_manifest_tracks_extends_parent_profile_and_its_module(self) -> None:
        # The composed profile merges the parent's modules, so those must
        # appear as module assets, and the primary profile must record its
        # dependencies on the parent profile and every (composed) module.
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)
            _write(
                source_root / "profiles" / "base" / "profile.yaml",
                """apiVersion: cds/v1alpha1
kind: Profile
metadata:
  name: base
spec:
  runtime:
    type: docker-compose
  modules:
    - id: demo
      source: ../../modules/apps/demo
      config: {}
""",
            )
            _write(
                source_root / "profiles" / "child" / "profile.yaml",
                """apiVersion: cds/v1alpha1
kind: Profile
metadata:
  name: child
extends:
  - base
spec: {}
""",
            )

            _actions, manifest_path, _conflicts = fetch_profile(
                "child",
                local=str(source_root),
                destination_root=destination_root,
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entry = manifest["profiles"]["child"]
            assets = {(asset["type"], asset["id"]): asset for asset in entry["assets"]}

            self.assertIn(("profile", "child"), assets)
            self.assertIn(("module", "demo"), assets)
            parent = assets[("profile", "profiles/base")]
            self.assertEqual(parent["sourcePath"], "profiles/base")
            # Only the primary profile carries the dependency edges.
            self.assertEqual(parent["dependencies"], [])
            self.assertEqual(
                assets[("profile", "child")]["dependencies"],
                [
                    {"type": "module", "id": "demo"},
                    {"type": "profile", "id": "profiles/base"},
                ],
            )

    def test_manifest_types_leftover_asset_roots(self) -> None:
        # Asset roots that are neither the (primary or parent) profiles nor
        # module directories are typed by location: shared/contracts/ is a
        # contract, everything else (build assets) is a runtime asset.
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)
            _write(
                source_root / "images" / "demo" / "Dockerfile",
                """FROM python:3.14-slim
COPY shared/python /app/shared/python
COPY shared/contracts /app/shared/contracts
COPY images/demo/entrypoint.sh /entrypoint.sh
""",
            )
            _write(
                source_root / "shared" / "contracts" / "demo-contract.yaml",
                "apiVersion: cds/v1alpha1\nkind: Contract\n",
            )

            _actions, manifest_path, _conflicts = fetch_profile(
                "demo",
                local=str(source_root),
                destination_root=destination_root,
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entry = manifest["profiles"]["demo"]
            assets = {(asset["type"], asset["id"]): asset for asset in entry["assets"]}

            self.assertIn(("contract", "shared/contracts"), assets)
            self.assertIn(("runtime", "shared/python"), assets)
            self.assertIn(("runtime", "images/demo/Dockerfile"), assets)
            self.assertIn(("runtime", "images/demo/entrypoint.sh"), assets)

    def test_fetch_profile_copies_profile_module_and_build_assets(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)

            actions, manifest_path, conflicts = fetch_profile(
                "demo",
                local=str(source_root),
                destination_root=destination_root,
            )

            self.assertEqual(conflicts, [])
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

    def test_fetch_profile_copies_extends_parent_and_its_module(self) -> None:
        # Regression test: _collect_asset_roots() must resolve `extends` so
        # `cds get` fetches a parent profile's own modules even when the
        # child profile doesn't redeclare them.
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)
            _write(
                source_root / "profiles" / "base" / "profile.yaml",
                """apiVersion: cds/v1alpha1
kind: Profile
metadata:
  name: base
spec:
  runtime:
    type: docker-compose
  modules:
    - id: demo
      source: ../../modules/apps/demo
      config: {}
""",
            )
            _write(
                source_root / "profiles" / "child" / "profile.yaml",
                """apiVersion: cds/v1alpha1
kind: Profile
metadata:
  name: child
extends:
  - base
spec: {}
""",
            )

            actions, manifest_path, _conflicts = fetch_profile(
                "child",
                local=str(source_root),
                destination_root=destination_root,
            )

            self.assertGreater(len(actions), 0)
            self.assertTrue((destination_root / "profiles" / "child" / "profile.yaml").exists())
            self.assertTrue((destination_root / "profiles" / "base" / "profile.yaml").exists())
            self.assertTrue((destination_root / "modules" / "apps" / "demo" / "module.yaml").exists())

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entry = manifest["profiles"]["child"]
            self.assertIn("modules/apps/demo", entry["assetRoots"])
            self.assertIn("profiles/base", entry["assetRoots"])

    def test_fetch_profile_diamond_extends_is_not_a_false_positive_cycle(self) -> None:
        # Regression test: _collect_extends_profile_dirs() must not treat a
        # diamond-shaped extends graph (child extends left and right, both
        # of which extend the same shared-base) as a cycle -- the shared
        # parent is legitimately reached twice via two different paths.
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)
            _write(
                source_root / "profiles" / "shared-base" / "profile.yaml",
                """apiVersion: cds/v1alpha1
kind: Profile
metadata:
  name: shared-base
spec:
  runtime:
    type: docker-compose
  modules:
    - id: demo
      source: ../../modules/apps/demo
      config: {}
""",
            )
            _write(
                source_root / "profiles" / "left" / "profile.yaml",
                """apiVersion: cds/v1alpha1
kind: Profile
metadata:
  name: left
extends:
  - shared-base
spec: {}
""",
            )
            _write(
                source_root / "profiles" / "right" / "profile.yaml",
                """apiVersion: cds/v1alpha1
kind: Profile
metadata:
  name: right
extends:
  - shared-base
spec: {}
""",
            )
            _write(
                source_root / "profiles" / "diamond-child" / "profile.yaml",
                """apiVersion: cds/v1alpha1
kind: Profile
metadata:
  name: diamond-child
extends:
  - left
  - right
spec: {}
""",
            )

            actions, manifest_path, _conflicts = fetch_profile(
                "diamond-child",
                local=str(source_root),
                destination_root=destination_root,
            )

            self.assertGreater(len(actions), 0)
            self.assertTrue((destination_root / "profiles" / "diamond-child" / "profile.yaml").exists())
            self.assertTrue((destination_root / "profiles" / "left" / "profile.yaml").exists())
            self.assertTrue((destination_root / "profiles" / "right" / "profile.yaml").exists())
            self.assertTrue((destination_root / "profiles" / "shared-base" / "profile.yaml").exists())
            self.assertTrue((destination_root / "modules" / "apps" / "demo" / "module.yaml").exists())

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
                _, returned_manifest_path, _ = fetch_profile(
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

    def test_fetch_profile_dry_run_reports_conflicts_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)

            fetch_profile("demo", local=str(source_root), destination_root=destination_root)
            profile_file = destination_root / "profiles" / "demo" / "profile.yaml"
            profile_file.write_text("changed\n", encoding="utf-8")

            actions, manifest_path, conflicts = fetch_profile(
                "demo",
                local=str(source_root),
                destination_root=destination_root,
                dry_run=True,
            )

            self.assertGreater(len(actions), 0)
            self.assertEqual(conflicts, ["profiles/demo/profile.yaml"])
            self.assertEqual(manifest_path, destination_root.resolve() / ".cds" / "get-manifest.json")
            self.assertEqual(profile_file.read_text(encoding="utf-8"), "changed\n")

            rendered = format_get_plan(actions, destination_root=destination_root, conflicts=conflicts)
            self.assertIn("Would conflict without --force (1):", rendered)
            self.assertIn("profiles/demo/profile.yaml", rendered)

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

            _, manifest_path, _ = fetch_profile(
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

    @unittest.skipIf(sys.platform.startswith("win"), "symlinks require elevated privileges on Windows")
    def test_fetch_profile_rejects_asset_symlink_escaping_source_repo(self) -> None:
        """A symlink planted *inside* an asset root (module/profile/build
        context) that resolves outside the source repository must be
        rejected as a stable GetError, not an unhandled ValueError from
        Path.relative_to() (#454)."""
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir, tempfile.TemporaryDirectory() as outside_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            outside_root = Path(outside_dir)
            _make_source_repo(source_root)

            secret_file = outside_root / "secret.txt"
            secret_file.write_text("top secret\n", encoding="utf-8")
            escape_link = source_root / "modules" / "apps" / "demo" / "escape.txt"
            escape_link.symlink_to(secret_file)

            with self.assertRaises(GetError) as ctx:
                fetch_profile("demo", local=str(source_root), destination_root=destination_root)

            self.assertIn("resolves outside the source repository", str(ctx.exception))
            self.assertFalse((destination_root / "modules" / "apps" / "demo" / "escape.txt").exists())

    def test_fetch_profile_rejects_dockerfile_copy_traversal_escaping_source_repo(self) -> None:
        """A Dockerfile COPY/ADD source containing ".." that Path.glob()
        matches outside the build context (and outside the source repo
        entirely) must be rejected as a stable GetError, not an unhandled
        ValueError from Path.relative_to() in _add_copy_action (#475)."""
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            outside_root = source_root.parent / "outside"
            _write(outside_root / "secret.txt", "top secret\n")

            _make_source_repo(source_root)
            # _make_source_repo's Dockerfile build context is "../../../"
            # (the repo root), so ".." from there escapes the repo entirely.
            _write(
                source_root / "images" / "demo" / "Dockerfile",
                """FROM python:3.14-slim
COPY shared/python /app/shared/python
COPY workdirs/demo /app/workdirs/demo
COPY images/demo/entrypoint.sh /entrypoint.sh
COPY ../outside/secret.txt /app/secret.txt
""",
            )

            with self.assertRaises(GetError) as ctx:
                fetch_profile("demo", local=str(source_root), destination_root=destination_root)

            self.assertIn("resolves outside the source repository", str(ctx.exception))
            self.assertFalse((destination_root / "images" / "demo" / "secret.txt").exists())

    def test_fetch_profile_reports_copy_failure_as_get_error(self) -> None:
        """A filesystem failure while writing a planned copy (permission
        denied, disk full, read-only destination, ...) must surface as a
        stable GetError, not a raw OSError, so callers get a predictable
        error path regardless of the underlying platform/errno (#454)."""
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)

            with patch("cli.getter.shutil.copy2", side_effect=OSError("disk full")):
                with self.assertRaises(GetError) as ctx:
                    fetch_profile("demo", local=str(source_root), destination_root=destination_root)

            self.assertIn("Could not write", str(ctx.exception))
            self.assertIn("disk full", str(ctx.exception))
            # No manifest should be left behind after a failed, partial write.
            self.assertFalse((destination_root / ".cds" / "get-manifest.json").exists())

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


class DockerfileRootedSourceTest(unittest.TestCase):
    def test_rooted_copy_source_is_skipped_on_every_platform(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            _write(root / "a.txt", "x")
            for rooted in ("/abs", "\\abs"):
                with self.subTest(rooted=rooted):
                    result = _extract_dockerfile_instruction_sources(
                        root, root / "Dockerfile", f"COPY {rooted} a.txt /dst"
                    )
                    self.assertEqual({p.name for p in result}, {"a.txt"})


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
                actions, manifest_path, _ = fetch_profile(
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
                actions, _, _ = fetch_profile(
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

    def test_fetch_profile_reports_entries_on_unexpected_archive_layout(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            _make_source_repo(source_root)
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
                archive.add(source_root, arcname="owner-demo-repo-aaa111")
                archive.add(source_root, arcname="owner-demo-repo-bbb222")
            archive_bytes = buffer.getvalue()

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
                with self.assertRaises(GetError) as ctx:
                    fetch_profile("demo", destination_root=Path(dest_dir))

            message = str(ctx.exception)
            self.assertIn("Unexpected archive layout", message)
            self.assertIn("owner-demo-repo-aaa111", message)
            self.assertIn("owner-demo-repo-bbb222", message)

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

    def test_fetch_profile_rejects_local_with_explicit_ref(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            _make_source_repo(source_root)

            with self.assertRaises(GetError) as ctx:
                fetch_profile(
                    "demo",
                    ref="v2",
                    local=str(source_root),
                    destination_root=destination_root,
                )
            self.assertIn("--ref", str(ctx.exception))
            self.assertIn("--local", str(ctx.exception))


class GetSourceTrustTest(unittest.TestCase):
    """Trusted-source allowlist and commit pinning for `cds get` (#849)."""

    FULL_SHA = "0123456789abcdef0123456789abcdef01234567"

    def _tarball(self, source_root: Path, *, with_comment: bool = True) -> bytes:
        buffer = io.BytesIO()
        pax = {"comment": self.FULL_SHA} if with_comment else {}
        with tarfile.open(
            fileobj=buffer, mode="w:gz", format=tarfile.PAX_FORMAT, pax_headers=pax
        ) as archive:
            archive.add(source_root, arcname=f"owner-repo-{self.FULL_SHA[:7]}")
        return buffer.getvalue()

    def _fetch(self, archive_bytes: bytes, dest_dir: str, **kwargs):
        class _FakeResponse:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc_info):
                return False

            def read(self_inner):
                return archive_bytes

        with patch("cli.getter.urlopen", return_value=_FakeResponse()):
            return fetch_profile("demo", destination_root=Path(dest_dir), **kwargs)

    def test_parse_allowed_sources_normalizes_entries(self) -> None:
        self.assertEqual(parse_allowed_sources(" Owner , Owner/Repo,,"), ["owner", "owner/repo"])
        self.assertEqual(parse_allowed_sources(None), [])
        self.assertEqual(parse_allowed_sources(""), [])

    def test_allowlist_rejects_unlisted_source_before_downloading(self) -> None:
        with tempfile.TemporaryDirectory() as dest_dir:
            with patch("cli.getter.urlopen") as urlopen_mock:
                with self.assertRaises(GetError) as ctx:
                    fetch_profile(
                        "demo",
                        remote="evil/repo",
                        destination_root=Path(dest_dir),
                        allowed_sources=["trusted"],
                    )
            urlopen_mock.assert_not_called()
            self.assertIn("allowlist", str(ctx.exception))

    def test_allowlist_accepts_owner_and_exact_repo_entries(self) -> None:
        for allowed in (["Owner"], ["owner/repo"]):
            with self.subTest(allowed=allowed), tempfile.TemporaryDirectory() as source_dir, (
                tempfile.TemporaryDirectory()
            ) as dest_dir:
                _make_source_repo(Path(source_dir))
                actions, _, _ = self._fetch(
                    self._tarball(Path(source_dir)),
                    dest_dir,
                    remote="owner/repo",
                    allowed_sources=allowed,
                )
                self.assertGreater(len(actions), 0)

    def test_allowlist_applies_to_default_remote(self) -> None:
        with tempfile.TemporaryDirectory() as dest_dir:
            with self.assertRaises(GetError):
                fetch_profile(
                    "demo", destination_root=Path(dest_dir), allowed_sources=["someone-else"]
                )

    def test_commit_pin_matches_and_is_recorded_in_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            _make_source_repo(Path(source_dir))
            _, manifest_path, _ = self._fetch(
                self._tarball(Path(source_dir)),
                dest_dir,
                remote="owner/repo",
                commit=self.FULL_SHA[:12].upper(),
            )
            entry = json.loads(manifest_path.read_text(encoding="utf-8"))["profiles"]["demo"]
            self.assertEqual(entry["commit"], self.FULL_SHA)
            self.assertEqual(entry["pinnedCommit"], self.FULL_SHA[:12])

    def test_unpinned_fetch_still_records_resolved_commit(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            _make_source_repo(Path(source_dir))
            _, manifest_path, _ = self._fetch(
                self._tarball(Path(source_dir)), dest_dir, remote="owner/repo"
            )
            entry = json.loads(manifest_path.read_text(encoding="utf-8"))["profiles"]["demo"]
            self.assertEqual(entry["commit"], self.FULL_SHA)
            self.assertIsNone(entry["pinnedCommit"])

    def test_commit_pin_mismatch_fails_closed_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            _make_source_repo(Path(source_dir))
            with self.assertRaises(GetError) as ctx:
                self._fetch(
                    self._tarball(Path(source_dir)),
                    dest_dir,
                    remote="owner/repo",
                    commit="deadbeefdeadbeef",
                )
            self.assertIn("does not match", str(ctx.exception))
            self.assertEqual(list(Path(dest_dir).iterdir()), [])

    def test_unpinned_fetch_falls_back_to_directory_suffix_without_pax_header(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            _make_source_repo(Path(source_dir))
            _, manifest_path, _ = self._fetch(
                self._tarball(Path(source_dir), with_comment=False),
                dest_dir,
                remote="owner/repo",
            )
            entry = json.loads(manifest_path.read_text(encoding="utf-8"))["profiles"]["demo"]
            self.assertEqual(entry["commit"], self.FULL_SHA[:7])

    def _fetch_with_api(self, archive_bytes: bytes, dest_dir: str, api_body: bytes, **kwargs):
        def fake_urlopen(request, timeout=None):
            body = api_body if "/commits/" in request.full_url else archive_bytes

            class _Resp:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *exc_info):
                    return False

                def read(self_inner):
                    return body

            return _Resp()

        with patch("cli.getter.urlopen", side_effect=fake_urlopen):
            return fetch_profile("demo", destination_root=Path(dest_dir), **kwargs)

    def test_commit_pin_verified_via_api_when_archive_has_only_abbreviated_sha(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            _make_source_repo(Path(source_dir))
            _, manifest_path, _ = self._fetch_with_api(
                self._tarball(Path(source_dir), with_comment=False),
                dest_dir,
                self.FULL_SHA.encode(),
                remote="owner/repo",
                commit=self.FULL_SHA,
            )
            entry = json.loads(manifest_path.read_text(encoding="utf-8"))["profiles"]["demo"]
            self.assertEqual(entry["commit"], self.FULL_SHA)

    def test_commit_pin_rejects_api_sha_that_disagrees_with_archive(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            _make_source_repo(Path(source_dir))
            with self.assertRaises(GetError):
                self._fetch_with_api(
                    self._tarball(Path(source_dir), with_comment=False),
                    dest_dir,
                    ("f" * 40).encode(),
                    remote="owner/repo",
                    commit=self.FULL_SHA,
                )
            self.assertEqual(list(Path(dest_dir).iterdir()), [])

    def test_allowlist_wildcard_allows_any_source(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            _make_source_repo(Path(source_dir))
            actions, _, _ = self._fetch(
                self._tarball(Path(source_dir)),
                dest_dir,
                remote="anyone/repo",
                allowed_sources=["*"],
            )
            self.assertGreater(len(actions), 0)

    def test_commit_pin_rejects_invalid_values_and_local_sources(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            _make_source_repo(Path(source_dir))
            for bad in ("main", "abc", "zzzzzzzz", self.FULL_SHA[:7]):
                with self.subTest(commit=bad), self.assertRaises(GetError):
                    fetch_profile(
                        "demo", local=source_dir, destination_root=Path(dest_dir), commit=bad
                    )
            with self.assertRaises(GetError) as ctx:
                fetch_profile(
                    "demo",
                    local=source_dir,
                    destination_root=Path(dest_dir),
                    commit=self.FULL_SHA,
                )
            self.assertIn("--commit", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
