"""Shipped profiles must validate (#940).

The dbt example profile silently broke for six weeks because CI only
exercises the stable profile (mvp-proof.yml / CDS Test (Stable Profile)).
These tests run the real validator against every shipped profile and
every environment overlay so that class of breakage fails the suite.
"""
import unittest
from pathlib import Path

from cli.validator import validate_profile

_REPO_ROOT = Path(__file__).resolve().parent.parent


class ShippedProfileValidationTest(unittest.TestCase):
    def test_all_shipped_profiles_validate(self):
        profiles = sorted((_REPO_ROOT / "profiles").glob("*/profile.yaml"))
        self.assertGreaterEqual(len(profiles), 5)
        for profile in profiles:
            with self.subTest(profile=profile.parent.name):
                errors = [d for d in validate_profile(str(profile)) if d.level == "error"]
                self.assertEqual(errors, [])

    def test_all_shipped_environment_overlays_validate(self):
        overlays = sorted((_REPO_ROOT / "profiles").glob("*/environments/*.yaml"))
        self.assertGreaterEqual(len(overlays), 1)
        for overlay in overlays:
            environment = overlay.stem
            profile_file = overlay.parent.parent / "profile.yaml"
            with self.subTest(profile=profile_file.parent.name, environment=environment):
                errors = [
                    d for d in validate_profile(str(profile_file), environment=environment)
                    if d.level == "error"
                ]
                self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
