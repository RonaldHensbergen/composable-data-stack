import tempfile
import unittest
import unittest.mock
from pathlib import Path

import yaml

import cli.loader
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
            expected = (profiles_root / "generated-demo" / "profile.yaml").resolve()
            self.assertEqual(result_path, expected)
            self.assertTrue(expected.is_file())
            self.assertEqual(yaml.safe_load(expected.read_text(encoding="utf-8")), profile)

    def test_defaults_name_from_profile_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profiles_root = Path(tmpdir) / "profiles"
            profile = self._profile("from-metadata")

            result_path, diagnostics = save_generated_profile(profile, profiles_root)

            self.assertEqual(diagnostics, [])
            self.assertEqual(result_path, (profiles_root / "from-metadata" / "profile.yaml").resolve())

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

    def test_reports_diagnostic_for_non_yaml_serializable_value(self):
        """A value yaml.safe_dump() can't represent (e.g. an arbitrary
        object) must surface as an E117 diagnostic, not an unhandled
        RepresenterError traceback -- the function's return contract
        promises (None, diagnostics) on every error path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profiles_root = Path(tmpdir) / "profiles"
            profile = self._profile("generated-demo")
            profile["spec"]["unrepresentable"] = object()

            path, diagnostics = save_generated_profile(profile, profiles_root)

            self.assertIsNone(path)
            self.assertEqual(len(diagnostics), 1)
            self.assertEqual(diagnostics[0].code, "E117")
            self.assertFalse((profiles_root / "generated-demo" / "profile.yaml").exists())

    def test_reports_diagnostic_for_write_failure(self):
        """A filesystem write failure (e.g. permission denied) must surface
        as an E117 diagnostic rather than an unhandled OSError, matching the
        (None, diagnostics) contract on every error path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profiles_root = Path(tmpdir) / "profiles"
            profile = self._profile("generated-demo")

            with unittest.mock.patch(
                "cli.loader._atomic_write", side_effect=OSError("disk full")
            ):
                path, diagnostics = save_generated_profile(profile, profiles_root)

            self.assertIsNone(path)
            self.assertEqual(len(diagnostics), 1)
            self.assertEqual(diagnostics[0].code, "E117")
            self.assertFalse((profiles_root / "generated-demo" / "profile.yaml").exists())

    def test_rejects_a_profiles_root_that_is_a_file(self):
        """CDS_PROFILE_PATH (passed in here as profiles_root) may be set to
        a single profile file or a bare profile name elsewhere in the CLI,
        but generate_profile() always needs a directory to create
        <name>/profile.yaml under. That must fail closed with a clear E118
        diagnostic instead of silently building a nonsense nested path
        underneath the file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profiles_root = Path(tmpdir) / "profiles.yaml"
            profiles_root.write_text("not a directory", encoding="utf-8")
            profile = self._profile("generated-demo")

            path, diagnostics = save_generated_profile(profile, profiles_root)

            self.assertIsNone(path)
            self.assertEqual(len(diagnostics), 1)
            self.assertEqual(diagnostics[0].code, "E118")

    def test_fails_closed_when_a_concurrent_writer_wins_the_check_then_write_race(self):
        """The upfront profile_file.exists() check is a fast, friendly E116
        for the common case, but it can't close the race between that check
        and the write: a second caller could create the same file in
        between. Simulate that race by creating the destination file inside
        a wrapped _atomic_write(), after save_generated_profile()'s own
        exists() check has already passed -- the underlying os.link()-based
        publish step must still fail closed with E116 (via FileExistsError)
        instead of silently overwriting the "winner"."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profiles_root = Path(tmpdir) / "profiles"
            profile = self._profile("generated-demo")
            target = profiles_root / "generated-demo" / "profile.yaml"

            real_atomic_write = cli.loader._atomic_write

            def racing_atomic_write(path, content, encoding="utf-8", overwrite=True):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("winner-of-the-race: true\n", encoding="utf-8")
                return real_atomic_write(path, content, encoding=encoding, overwrite=overwrite)

            with unittest.mock.patch("cli.loader._atomic_write", side_effect=racing_atomic_write):
                path, diagnostics = save_generated_profile(profile, profiles_root)

            self.assertIsNone(path)
            self.assertEqual(len(diagnostics), 1)
            self.assertEqual(diagnostics[0].code, "E116")
            self.assertEqual(target.read_text(encoding="utf-8"), "winner-of-the-race: true\n")
