import tempfile
import unittest
from pathlib import Path

import yaml

from cli.loader import save_generated_profile


class SaveGeneratedProfileTest(unittest.TestCase):
    def _profile(self, name: str = "generated-demo") -> dict:
        return {
            "apiVersion": "cds/v1alpha1",
            "kind": "Profile",
            "metadata": {"name": name, "environment": "local"},
            "spec": {
                "runtime": {"type": "docker-compose"},
                "modules": [],
                "secrets": {"provider": {"type": "env"}, "values": {}},
            },
        }

    def test_writes_to_normal_profiles_layout(self):
        """A generated profile must land at the same profiles/<name>/profile.yaml
        location a hand-authored profile would, so it can be handed straight
        to validate_profile()/build_plan() unchanged (issue #349)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profiles_root = Path(tmpdir) / "profiles"
            profile = self._profile("generated-demo")

            result_path, diagnostics = save_generated_profile(profile, profiles_root)

            self.assertEqual(diagnostics, [])
            expected = profiles_root / "generated-demo" / "profile.yaml"
            self.assertEqual(result_path, expected)
            self.assertTrue(expected.is_file())
            self.assertEqual(yaml.safe_load(expected.read_text(encoding="utf-8")), profile)

    def test_defaults_name_from_profile_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profiles_root = Path(tmpdir) / "profiles"
            profile = self._profile("from-metadata")

            result_path, diagnostics = save_generated_profile(profile, profiles_root)

            self.assertEqual(diagnostics, [])
            self.assertEqual(result_path, profiles_root / "from-metadata" / "profile.yaml")

    def test_rejects_missing_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profiles_root = Path(tmpdir) / "profiles"
            profile = self._profile()
            del profile["metadata"]["name"]

            result_path, diagnostics = save_generated_profile(profile, profiles_root)

            self.assertIsNone(result_path)
            self.assertEqual(len(diagnostics), 1)
            self.assertEqual(diagnostics[0].code, "E114")

    def test_rejects_non_dict_profile(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profiles_root = Path(tmpdir) / "profiles"

            result_path, diagnostics = save_generated_profile("not-a-dict", profiles_root)  # type: ignore[arg-type]

            self.assertIsNone(result_path)
            self.assertEqual(diagnostics[0].code, "E114")

    def test_rejects_path_traversal_in_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profiles_root = Path(tmpdir) / "profiles"
            profile = self._profile()

            result_path, diagnostics = save_generated_profile(
                profile, profiles_root, name="../../etc/evil"
            )

            self.assertIsNone(result_path)
            self.assertEqual(diagnostics[0].code, "E115")

    def test_rejects_absolute_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profiles_root = Path(tmpdir) / "profiles"
            profile = self._profile()

            result_path, diagnostics = save_generated_profile(profile, profiles_root, name="/etc/evil")

            self.assertIsNone(result_path)
            self.assertEqual(diagnostics[0].code, "E115")

    def test_refuses_to_overwrite_existing_profile_without_force(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profiles_root = Path(tmpdir) / "profiles"
            profile = self._profile("generated-demo")

            first_path, first_diags = save_generated_profile(profile, profiles_root)
            self.assertEqual(first_diags, [])

            second_path, second_diags = save_generated_profile(profile, profiles_root)

            self.assertIsNone(second_path)
            self.assertEqual(len(second_diags), 1)
            self.assertEqual(second_diags[0].code, "E116")
            # The original file must be untouched by the rejected rewrite attempt.
            self.assertEqual(yaml.safe_load(first_path.read_text(encoding="utf-8")), profile)

    def test_overwrites_existing_profile_when_forced(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profiles_root = Path(tmpdir) / "profiles"
            profile = self._profile("generated-demo")

            first_path, _ = save_generated_profile(profile, profiles_root)

            updated_profile = self._profile("generated-demo")
            updated_profile["spec"]["runtime"] = {"type": "kubernetes"}
            second_path, second_diags = save_generated_profile(
                updated_profile, profiles_root, force=True
            )

            self.assertEqual(second_diags, [])
            self.assertEqual(second_path, first_path)
            self.assertEqual(
                yaml.safe_load(second_path.read_text(encoding="utf-8")),
                updated_profile,
            )
