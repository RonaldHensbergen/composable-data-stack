import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import os

from cli.main import resolve_profile_path


class TestResolveProfilePath(unittest.TestCase):
    def test_resolve_with_no_args_and_no_env_raises_error(self):
        """When no profile arg and CDS_PROFILE_PATH not set, should raise ValueError"""
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                with patch.dict(os.environ, {}, clear=True):
                    with self.assertRaises(ValueError) as ctx:
                        resolve_profile_path(None)
                    self.assertIn("No profile specified", str(ctx.exception))
            finally:
                os.chdir(old_cwd)

    def test_resolve_with_no_args_but_env_points_to_file(self):
        """When CDS_PROFILE_PATH points to a profile.yaml file, should return it"""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_file = Path(tmpdir) / "profile.yaml"
            profile_file.write_text("apiVersion: cds/v1alpha1\n")
            
            with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(profile_file)}, clear=False):
                result = resolve_profile_path(None)
                self.assertEqual(result, str(profile_file.resolve()))

    def test_resolve_with_no_args_but_env_points_to_dir_with_profile(self):
        """When CDS_PROFILE_PATH points to a dir with profile.yaml, should return it"""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir)
            profile_file = profile_dir / "profile.yaml"
            profile_file.write_text("apiVersion: cds/v1alpha1\n")
            
            with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(profile_dir)}, clear=False):
                result = resolve_profile_path(None)
                self.assertEqual(result, str(profile_file.resolve()))

    def test_resolve_with_no_args_but_env_points_to_dir_with_single_subdir(self):
        """When CDS_PROFILE_PATH points to a dir with one profile subdir, should return it"""
        with tempfile.TemporaryDirectory() as tmpdir:
            profiles_root = Path(tmpdir)
            local_dir = profiles_root / "local"
            local_dir.mkdir()
            profile_file = local_dir / "profile.yaml"
            profile_file.write_text("apiVersion: cds/v1alpha1\n")
            
            with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(profiles_root)}, clear=False):
                result = resolve_profile_path(None)
                self.assertEqual(result, str(profile_file.resolve()))

    def test_resolve_with_profile_arg_overrides_env(self):
        """When profile arg is provided, it should be used regardless of CDS_PROFILE_PATH"""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_profile = Path(tmpdir) / "env_profile.yaml"
            env_profile.write_text("apiVersion: cds/v1alpha1\n")
            
            arg_profile = Path(tmpdir) / "arg_profile.yaml"
            arg_profile.write_text("apiVersion: cds/v1alpha1\n")
            
            with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(env_profile)}, clear=False):
                result = resolve_profile_path(str(arg_profile))
                self.assertEqual(result, str(arg_profile.resolve()))


if __name__ == "__main__":
    unittest.main()
