"""
GitOps target contract + FluxCD reference module (issue #767).

Covers the acceptance criteria of #767:

- shared/contracts/gitops-target.yaml is schema-valid via
  cli.validator.validate_contract_file (the repo-wide contract test also
  globs it, this test pins the specific file and its required fields).
- modules-experimental/gitops/fluxcd/module.yaml is module-schema valid,
  provides the gitops-target contract, and renders a working Flux
  GitRepository + Kustomization (rendered mode) / HelmRelease (chart mode)
  manifest set from a Helm-target profile via cli.k8s_renderer.render_helm.
- Raw kubernetes.manifests entries are shape-checked (E151) and gated by
  enabledFrom, and manifest-only modules get no ServiceAccount/NetworkPolicy.
"""
import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from cli.k8s_renderer import render_helm
from cli.planner import build_plan
from cli.renderer import render_compose
from cli.validator import validate_contract_file, validate_profile

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = REPO_ROOT / "shared" / "contracts" / "gitops-target.yaml"
MODULE_PATH = REPO_ROOT / "modules-experimental" / "gitops" / "fluxcd" / "module.yaml"
MODULE_SCHEMA = json.loads(
    (REPO_ROOT / "cli" / "resources" / "module.schema.json").read_text(encoding="utf-8")
)

FLUXCD_CONFIG = {
    "repositoryUrl": "https://github.com/example/cds-gitops.git",
    "branch": "main",
    "path": "clusters/local/rendered",
    "mode": "rendered",
    "syncInterval": "1m0s",
    "prune": True,
    "targetNamespace": "cds-local",
    "releaseName": "demo",
    "chartPath": "./chart",
    "fluxNamespace": "flux-system",
}


def _profile(fluxcd_config: dict) -> dict:
    # keydb stands in for "an existing Helm-target profile's" workload module:
    # a stable module with a kubernetes implementation and no required config.
    return {
        "apiVersion": "cds/v1alpha1",
        "kind": "Profile",
        "metadata": {"name": "gitops-test", "environment": "local"},
        "spec": {
            "runtime": {"type": "docker-compose", "namespace": "cds-local"},
            "modules": [
                {
                    "id": "cache",
                    "source": "modules/cache/keydb",
                    "version": "0.1.0",
                    "enabled": True,
                    "config": {},
                },
                {
                    "id": "gitops",
                    "source": "modules-experimental/gitops/fluxcd",
                    "version": "0.1.0",
                    "enabled": True,
                    "config": fluxcd_config,
                },
            ],
        },
    }


class GitopsTargetContractTest(unittest.TestCase):
    def test_contract_file_is_schema_valid(self) -> None:
        self.assertEqual(validate_contract_file(CONTRACT_PATH), [])

    def test_contract_declares_the_gitops_source_fields(self) -> None:
        contract = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(contract["kind"], "Contract")
        self.assertEqual(contract["metadata"]["name"], "gitops-target")
        fields = contract["spec"]["fields"]
        for name in ("repositoryUrl", "branch", "path"):
            self.assertTrue(fields[name]["required"], name)
        self.assertIn("syncInterval", fields)
        self.assertIn("targetNamespace", fields)
        self.assertTrue(contract["spec"]["examples"])


class FluxcdModuleShapeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = yaml.safe_load(MODULE_PATH.read_text(encoding="utf-8"))

    def test_module_is_schema_valid(self) -> None:
        errors = list(Draft202012Validator(MODULE_SCHEMA).iter_errors(self.module))
        self.assertEqual(errors, [])

    def test_module_provides_gitops_target_and_is_kubernetes_only(self) -> None:
        spec = self.module["spec"]
        provides = spec["provides"]
        self.assertEqual(len(provides), 1)
        self.assertEqual(provides[0]["name"], "gitops-target")
        self.assertEqual(provides[0]["contract"]["kind"], "gitops-target")
        impl = spec["implementation"]
        self.assertEqual(impl["kind"], "kubernetes")
        self.assertEqual(impl["targets"], ["kubernetes"])
        self.assertNotIn("compose", impl)
        self.assertEqual(
            sorted(impl["kubernetes"]["manifests"]),
            ["git-repository", "helm-release", "kustomization"],
        )
        self.assertFalse(self.module["metadata"]["productionSuitable"])


class FluxcdRenderTest(unittest.TestCase):
    def _build_plan(self, fluxcd_config: dict):
        profile = _profile(fluxcd_config)
        with tempfile.TemporaryDirectory() as tmp:
            profile_path = Path(tmp) / "profile.yaml"
            profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")
            with unittest.mock.patch.dict(
                "os.environ", {"CDS_MODULE_PATH": str(REPO_ROOT)}, clear=False
            ):
                diagnostics = validate_profile(str(profile_path))
                errors = [d.format() for d in diagnostics if d.level == "error"]
                self.assertEqual(errors, [])
                plan, plan_diagnostics = build_plan(str(profile_path))
        errors = [d.format() for d in plan_diagnostics if d.level == "error"]
        self.assertEqual(errors, [])
        self.assertIsNotNone(plan)
        return plan

    def test_plan_resolves_the_gitops_target_contract(self) -> None:
        plan = self._build_plan(dict(FLUXCD_CONFIG))
        gitops = next(m for m in plan["modules"] if m["id"] == "gitops")
        provided = gitops["provides"]["gitops-target"]["spec"]
        self.assertEqual(provided["repositoryUrl"], FLUXCD_CONFIG["repositoryUrl"])
        self.assertEqual(provided["branch"], "main")
        self.assertEqual(provided["path"], "clusters/local/rendered")
        self.assertEqual(provided["syncInterval"], "1m0s")
        self.assertEqual(provided["targetNamespace"], "cds-local")

    def _render(self, fluxcd_config: dict) -> dict:
        plan = self._build_plan(fluxcd_config)
        files, diagnostics = render_helm(plan)
        errors = [d.format() for d in diagnostics if d.level == "error"]
        self.assertEqual(errors, [])
        return files

    def test_rendered_mode_emits_git_repository_and_kustomization(self) -> None:
        files = self._render(dict(FLUXCD_CONFIG))
        self.assertIn("templates/gitops-manifest-git-repository.yaml", files)
        self.assertIn("templates/gitops-manifest-kustomization.yaml", files)
        self.assertNotIn("templates/gitops-manifest-helm-release.yaml", files)

        source = yaml.safe_load(files["templates/gitops-manifest-git-repository.yaml"])
        self.assertEqual(source["kind"], "GitRepository")
        self.assertEqual(source["metadata"]["name"], "{{ .Release.Name }}-gitops-source")
        self.assertEqual(source["metadata"]["namespace"], "flux-system")
        self.assertEqual(source["spec"]["url"], FLUXCD_CONFIG["repositoryUrl"])
        self.assertEqual(source["spec"]["ref"]["branch"], "main")
        self.assertEqual(source["spec"]["interval"], "1m0s")
        self.assertEqual(source["metadata"]["labels"]["cds.dev/module"], "gitops")

        kustomization = yaml.safe_load(files["templates/gitops-manifest-kustomization.yaml"])
        self.assertEqual(kustomization["kind"], "Kustomization")
        self.assertEqual(kustomization["metadata"]["name"], "{{ .Release.Name }}-gitops")
        self.assertEqual(kustomization["spec"]["path"], "clusters/local/rendered")
        self.assertIs(kustomization["spec"]["prune"], True)
        self.assertEqual(kustomization["spec"]["targetNamespace"], "cds-local")
        self.assertEqual(
            kustomization["spec"]["sourceRef"],
            {"kind": "GitRepository", "name": "{{ .Release.Name }}-gitops-source"},
        )
        # The enabledFrom gate is stripped from the rendered manifest.
        self.assertNotIn("enabledFrom", kustomization)

    def test_chart_mode_emits_git_repository_and_helm_release(self) -> None:
        config = dict(FLUXCD_CONFIG, mode="chart", chartPath="./chart")
        files = self._render(config)
        self.assertIn("templates/gitops-manifest-git-repository.yaml", files)
        self.assertIn("templates/gitops-manifest-helm-release.yaml", files)
        self.assertNotIn("templates/gitops-manifest-kustomization.yaml", files)

        helm_release = yaml.safe_load(files["templates/gitops-manifest-helm-release.yaml"])
        self.assertEqual(helm_release["kind"], "HelmRelease")
        self.assertEqual(helm_release["spec"]["releaseName"], "demo")
        self.assertEqual(helm_release["spec"]["targetNamespace"], "cds-local")
        chart = helm_release["spec"]["chart"]["spec"]
        self.assertEqual(chart["chart"], "./chart")
        self.assertEqual(
            chart["sourceRef"],
            {"kind": "GitRepository", "name": "{{ .Release.Name }}-gitops-source"},
        )

    def test_manifest_only_module_gets_no_service_account_or_network_policy(self) -> None:
        files = self._render(dict(FLUXCD_CONFIG))
        self.assertNotIn("templates/gitops-service-account.yaml", files)
        self.assertNotIn("templates/gitops-network-policy.yaml", files)
        # Workload modules in the same chart keep theirs.
        self.assertIn("templates/cache-service-account.yaml", files)
        self.assertIn("templates/cache-network-policy.yaml", files)

    def test_fluxcd_module_fails_closed_on_the_compose_target(self) -> None:
        plan = self._build_plan(dict(FLUXCD_CONFIG))
        _, diagnostics = render_compose(plan)
        codes = [d.code for d in diagnostics if d.level == "error"]
        self.assertIn("E070", codes)


class RawManifestDiagnosticsTest(unittest.TestCase):
    """Renderer-side shape checks for kubernetes.manifests (plans loaded from
    files bypass module.schema.json, so render_helm re-checks, like E148/E149)."""

    def _plan(self, manifests: dict) -> dict:
        return {
            "apiVersion": "cds/v1alpha1",
            "kind": "Plan",
            "metadata": {"name": "demo", "environment": "local"},
            "runtime": {},
            "secrets": {},
            "certificates": {},
            "outputs": {},
            "modules": [
                {
                    "id": "gitops",
                    "source": "modules-experimental/gitops/fluxcd",
                    "version": "0.1.0",
                    "dependsOn": [],
                    "config": {},
                    "security": {},
                    "consumes": {},
                    "provides": {},
                    "implementation": {
                        "kind": "kubernetes",
                        "targets": ["kubernetes"],
                        "kubernetes": {"manifests": manifests},
                    },
                }
            ],
        }

    def test_malformed_manifest_fails_closed_with_e151(self) -> None:
        _, diagnostics = render_helm(
            self._plan({"bad": {"apiVersion": "v1", "kind": "ConfigMap"}})
        )
        self.assertIn("E151", [d.code for d in diagnostics if d.level == "error"])

    def test_non_object_manifest_fails_closed_with_e151(self) -> None:
        _, diagnostics = render_helm(self._plan({"bad": "not-a-manifest"}))
        self.assertIn("E151", [d.code for d in diagnostics if d.level == "error"])

    def test_bad_enabled_from_shape_fails_closed_with_e145(self) -> None:
        _, diagnostics = render_helm(
            self._plan(
                {
                    "bad": {
                        "enabledFrom": True,
                        "apiVersion": "v1",
                        "kind": "ConfigMap",
                        "metadata": {"name": "n"},
                    }
                }
            )
        )
        self.assertIn("E145", [d.code for d in diagnostics if d.level == "error"])

    def test_release_prefix_is_applied_unless_name_is_templated(self) -> None:
        files, diagnostics = render_helm(
            self._plan(
                {
                    "templated": {
                        "apiVersion": "v1",
                        "kind": "ConfigMap",
                        "metadata": {"name": "{{ .Release.Name }}-already"},
                    },
                    "plain": {
                        "apiVersion": "v1",
                        "kind": "ConfigMap",
                        "metadata": {"name": "plain"},
                    },
                }
            )
        )
        self.assertEqual(
            [d.format() for d in diagnostics if d.level == "error"], []
        )
        templated = yaml.safe_load(files["templates/gitops-manifest-templated.yaml"])
        plain = yaml.safe_load(files["templates/gitops-manifest-plain.yaml"])
        self.assertEqual(templated["metadata"]["name"], "{{ .Release.Name }}-already")
        self.assertEqual(plain["metadata"]["name"], "{{ .Release.Name }}-plain")
        self.assertEqual(plain["metadata"]["labels"]["cds.dev/module"], "gitops")


if __name__ == "__main__":
    unittest.main()
