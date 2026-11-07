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
            / "local-kubernetes-example"
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
            self.assertEqual(service, {"type": "ClusterIP", "nodePort": 0})
        self.assertEqual(original_path.read_text(encoding="utf-8"), original)

    def test_clusterip_profile_renders_no_nodeport_despite_extends(self) -> None:
        # local-kubernetes-example extends local-dagster-postgres-superset,
        # whose dagster/superset configs carry nodePort 30300/30808. The
        # deep merge keeps base values for keys absent in the child, so the
        # ClusterIP conversion must set nodePort explicitly (0), otherwise
        # the rendered Service keeps nodePort and the API server rejects it
        # ("nodePort: Forbidden ... when `type` is 'ClusterIP'").
        from cli.k8s_renderer import render_helm
        from cli.planner import build_plan

        source = self.repo_root / "profiles" / "local-kubernetes-example" / "profile.yaml"
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text(
                "\n".join(
                    f"{key}=sentinel"
                    for key in (
                        "CDS_POSTGRES_SUPERUSER_PASSWORD",
                        "CDS_SUPERSET_ADMIN_PASSWORD",
                        "CDS_SUPERSET_DB_NAME",
                        "CDS_SUPERSET_DB_PASSWORD",
                        "CDS_SUPERSET_DB_USER",
                        "CDS_SUPERSET_SECRET_KEY",
                        "CDS_DAGSTER_DB_PASSWORD",
                        "CDS_DAGSTER_DB_USER",
                        "CDS_DAGSTER_DB_NAME",
                        "CDS_ANALYTICS_DB_PASSWORD",
                        "CDS_ANALYTICS_DB_USER",
                        "CDS_ANALYTICS_DB_NAME",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            target = source.parent / ".cds-e2e-test.profile.yaml"
            try:
                self.helper.write_clusterip_profile(source, target)
                plan, plan_diags = build_plan(str(target), env_file=str(env_file))
                self.assertEqual(
                    [d for d in plan_diags if d.level == "error"], [],
                    msg=str(plan_diags),
                )
                files, render_diags = render_helm(plan, env_file=str(env_file))
            finally:
                target.unlink(missing_ok=True)
        self.assertEqual(
            [d for d in render_diags if d.level == "error"], [], msg=str(render_diags)
        )
        services = [
            yaml.safe_load(content)
            for path, content in files.items()
            if path.startswith("templates/") and "service" in path
            and yaml.safe_load(content).get("kind") == "Service"
        ]
        self.assertGreater(len(services), 0)
        for service in services:
            self.assertEqual(service["spec"].get("type"), "ClusterIP")
            for port in service["spec"]["ports"]:
                self.assertNotIn("nodePort", port)

    def test_e2e_installs_the_generated_profile(self) -> None:
        script = (self.repo_root / "scripts" / "k8s" / "e2e.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn('clusterip_profile.py" "$PROFILE" "$E2E_PROFILE"', script)
        self.assertIn('install.sh" "$E2E_PROFILE"', script)
        self.assertIn('profiles/local-kubernetes-example/profile.yaml', script)
        self.assertIn('istio/base', script)
        self.assertIn('istio/istiod', script)
        self.assertIn('ISTIO_VERSION="${ISTIO_VERSION:-1.30.8}"', script)
        self.assertIn('peerauthentication "${CDS_RELEASE}-mesh-mtls"', script)

    def test_make_k3d_e2e_runs_the_example_profile(self) -> None:
        # `make k3d-e2e` expands to `e2e.sh $(PROFILE)`; without a
        # target-specific pin it would run the compose base profile and the
        # mesh module (and its PeerAuthentication) would never be installed.
        makefile = (self.repo_root / "Makefile").read_text(encoding="utf-8")
        self.assertIn("scripts/k8s/e2e.sh $(PROFILE)", makefile)
        self.assertRegex(
            makefile,
            r"k3d-e2e:\s+PROFILE\s*=\s*profiles/local-kubernetes-example/profile\.yaml",
        )


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


if __name__ == "__main__":
    unittest.main()
