"""End-to-end plan/render tests for the marquez lineage-sink endpoint (#1029).

The experimental marquez module is multi-service (marquez-api + marquez-web),
so the module instance id is not a DNS name on the Compose network. Its
`lineage-sink` contract and the web UI's MARQUEZ_HOST must point at the API
service's rendered compose name (via ${service.services.<name>}), for any
instance id.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from cli.planner import build_plan
from cli.renderer import render_compose

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = REPO_ROOT / "profiles" / "local-dagster-postgres-marquez" / "profile.yaml"

_TEST_ENV = {
    "CDS_MODULE_PATH": str(REPO_ROOT),
    "CDS_POSTGRES_SUPERUSER_PASSWORD": "superuser-pw",
    "CDS_ANALYTICS_DB_PASSWORD": "analytics-pw",
    "CDS_DAGSTER_DB_PASSWORD": "dagster-pw",
    "CDS_MARQUEZ_DB_PASSWORD": "marquez-pw",
    "CDS_ANALYTICS_DB_NAME": "analytics",
    "CDS_ANALYTICS_DB_USER": "analytics",
    "CDS_DAGSTER_DB_NAME": "dagster",
    "CDS_DAGSTER_DB_USER": "dagster",
    "CDS_MARQUEZ_DB_NAME": "marquez",
    "CDS_MARQUEZ_DB_USER": "marquez",
}


def _profile_variant(tmpdir: str, new_marquez_id: str | None = None):
    """The shipped marquez profile, with sources rewritten to be relative to
    CDS_MODULE_PATH (the repo root) and optionally the marquez instance id
    renamed (to prove the endpoint survives non-conventional ids)."""
    doc = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    for module in doc["spec"]["modules"]:
        module["source"] = module["source"].replace("../../", "")
    if new_marquez_id:
        for module in doc["spec"]["modules"]:
            if module["id"] == "marquez":
                module["id"] = new_marquez_id
            if module["id"] == "dagster":
                module["dependsOn"] = [
                    new_marquez_id if dep == "marquez" else dep for dep in module.get("dependsOn", [])
                ]
                module["config"]["lineageSink"]["contractRef"] = f"{new_marquez_id}.lineage-sink"
        outputs = doc["spec"].get("outputs", {}).get("contracts", {})
        if "lineageApi" in outputs:
            outputs["lineageApi"]["from"] = f"{new_marquez_id}.lineage-sink"
    profile_path = Path(tmpdir) / "profile.yaml"
    profile_path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return str(profile_path)


def _plan_and_render(profile_path: str):
    with patch.dict(os.environ, _TEST_ENV):
        plan, plan_diagnostics = build_plan(profile_path)
        assert plan is not None, [d.format() for d in plan_diagnostics if d.level == "error"]
        assert [d.format() for d in plan_diagnostics if d.level == "error"] == []
        compose_text, render_diagnostics = render_compose(plan)
    assert [d.format() for d in render_diagnostics if d.level == "error"] == []
    return plan, yaml.safe_load(compose_text)


class MarquezLineageSinkHostTest(unittest.TestCase):
    def test_plan_advertises_the_api_service_dns_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            plan, _compose = _plan_and_render(_profile_variant(tmpdir))
        marquez = next(m for m in plan["modules"] if m["id"] == "marquez")
        spec = marquez["provides"]["lineage-sink"]["spec"]
        self.assertEqual(spec["host"], "marquez-api")
        self.assertEqual(spec["connectionUri"], "http://marquez-api:5000")
        self.assertEqual(spec["serviceName"], "marquez-api")

    def test_rendered_compose_points_at_resolvable_names(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _plan, compose = _plan_and_render(_profile_variant(tmpdir))
        services = compose["services"]
        self.assertIn("marquez-api", services)
        self.assertIn("marquez-web", services)
        self.assertNotIn("marquez", services)
        # The web UI reaches the API backend over the Compose network.
        self.assertEqual(services["marquez-web"]["environment"]["MARQUEZ_HOST"], "marquez-api")
        # Dagster consumes the contract's connectionUri verbatim.
        for dagster_service in ("dagster-user-code", "dagster-webserver", "dagster-daemon"):
            env = services[dagster_service]["environment"]
            self.assertEqual(env["OPENLINEAGE_URL"], "http://marquez-api:5000")
            self.assertEqual(env["OPENLINEAGE_ENDPOINT"], "/api/v1/lineage")

    def test_non_conventional_instance_id_survives_the_naming_rule(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            plan, compose = _plan_and_render(_profile_variant(tmpdir, new_marquez_id="lineage"))
        lineage = next(m for m in plan["modules"] if m["id"] == "lineage")
        spec = lineage["provides"]["lineage-sink"]["spec"]
        self.assertEqual(spec["host"], "lineage-marquez-api")
        self.assertEqual(spec["connectionUri"], "http://lineage-marquez-api:5000")
        services = compose["services"]
        self.assertIn("lineage-marquez-api", services)
        self.assertIn("lineage-marquez-web", services)
        self.assertEqual(
            services["lineage-marquez-web"]["environment"]["MARQUEZ_HOST"],
            "lineage-marquez-api",
        )
        for dagster_service in ("dagster-user-code", "dagster-webserver", "dagster-daemon"):
            self.assertEqual(
                services[dagster_service]["environment"]["OPENLINEAGE_URL"],
                "http://lineage-marquez-api:5000",
            )


if __name__ == "__main__":
    unittest.main()
