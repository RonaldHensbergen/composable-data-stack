import unittest
import subprocess
import tempfile
from pathlib import Path

class TestCDSWorkflow(unittest.TestCase):
    
    def setUp(self):
        """Clone repo before each test."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo_path = Path(self.tmpdir.name) / "repo"
        
        repo_url = "https://github.com/your-org/your-repo.git"
        result = subprocess.run(
            ["git", "clone", repo_url, str(self.repo_path)],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to clone repo: {result.stderr}")
        
        self.profile_path = "profiles/local-dagster-postgres-superset/profile.yaml"
    
    def tearDown(self):
        """Clean up temp directory after each test."""
        self.tmpdir.cleanup()
    
    def test_commands(self):
        """Test all CDS commands with and without profile."""
        commands = ["validate", "plan", "render"]
        
        for cmd in commands:
            with self.subTest(command=cmd, use_profile=False):
                result = subprocess.run(
                    ["cds", cmd],
                    cwd=self.repo_path,
                    capture_output=True,
                    text=True
                )
                self.assertEqual(
                    result.returncode, 0,
                    f"cds {cmd} failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
                )
            
            with self.subTest(command=cmd, use_profile=True):
                result = subprocess.run(
                    ["cds", cmd, self.profile_path],
                    cwd=self.repo_path,
                    capture_output=True,
                    text=True
                )
                self.assertEqual(
                    result.returncode, 0,
                    f"cds {cmd} with profile failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
                )

if __name__ == "__main__":
    unittest.main()