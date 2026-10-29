import importlib.util
import re
import tempfile
import unittest
from pathlib import Path

import yaml


class K3dE2eProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parent.parent
        helper_path = cls.repo_root / "scripts" / "k8s" / "clusterip_profile.py"
        spec = importlib.util.spec_from_file_location("clusterip_profile", helper_path)
        cls.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.helper)

    def test_nodeports_are_removed_from_isolated_profile(self) -> None:
        original_path = (
            self.repo_root
            / "profiles"
            / "local-dagster-postgres-superset"
            / "profile.yaml"
        )
        original = original_path.read_text(encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "profile.yaml"
            target = Path(tmpdir) / "profile.e2e.yaml"
            source.write_text(original, encoding="utf-8")

            converted = self.helper.write_clusterip_profile(source, target)
            profile = yaml.safe_load(target.read_text(encoding="utf-8"))

        self.assertEqual(converted, ["dagster", "superset"])
        modules = {module["id"]: module for module in profile["spec"]["modules"]}
        for module_id in converted:
            service = modules[module_id]["config"]["kubernetesService"]
            self.assertEqual(service, {"type": "ClusterIP"})
        self.assertEqual(original_path.read_text(encoding="utf-8"), original)

    def test_e2e_installs_the_generated_profile(self) -> None:
        script = (self.repo_root / "scripts" / "k8s" / "e2e.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn('clusterip_profile.py" "$PROFILE" "$E2E_PROFILE"', script)
        self.assertIn('install.sh" "$E2E_PROFILE"', script)


if __name__ == "__main__":
    unittest.main()


class K3dLocalImagesTest(unittest.TestCase):
    def test_build_script_builds_every_image_imported_into_the_cluster(self) -> None:
        scripts = Path(__file__).resolve().parent.parent / "scripts" / "k8s"
        env = (scripts / "k3d-env.sh").read_text(encoding="utf-8")
        build = (scripts / "build-images.sh").read_text(encoding="utf-8")

        declared = re.search(r"CDS_LOCAL_IMAGES=\((.*?)\)", env).group(1)
        images = re.findall(r'"([^"]+)"', declared)

        self.assertIn("local/postgres:custom", images)
        for image in images:
            self.assertIn(f"-t {image} ", build)
