import importlib.util
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

import yaml

from cli.k8s_renderer import render_helm
from cli.planner import build_plan
from cli.renderer import render_compose

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILE = REPO_ROOT / "profiles" / "local-dagster-postgres-superset" / "profile.yaml"
DEFINITIONS_PATH = REPO_ROOT / "workdirs" / "dagster" / "definitions.py"

SECRET_VALUES = {
    "CDS_ANALYTICS_DB_NAME": "analytics",
    "CDS_ANALYTICS_DB_PASSWORD": "sentinel-analytics-password",
    "CDS_ANALYTICS_DB_USER": "analytics",
    "CDS_DAGSTER_DB_NAME": "dagster",
    "CDS_DAGSTER_DB_PASSWORD": "sentinel-dagster-password",
    "CDS_DAGSTER_DB_USER": "dagster",
    "CDS_POSTGRES_SUPERUSER_PASSWORD": "sentinel-postgres-password",
    "CDS_SUPERSET_ADMIN_PASSWORD": "sentinel-admin-password",
    "CDS_SUPERSET_DB_NAME": "superset",
    "CDS_SUPERSET_DB_PASSWORD": "sentinel-superset-password",
    "CDS_SUPERSET_DB_USER": "superset",
    "CDS_SUPERSET_SECRET_KEY": "sentinel-superset-secret-key",
}

SAMPLE_DATA_ENABLED = {
    "enabled": True,
    "hostPath": "./workdirs/shared-data/incoming/offers-1000.csv",
    "containerPath": "/app/data/cds/incoming/offers-1000.csv",
}

SAMPLE_CONTAINER_PATH = "/app/data/cds/incoming/offers-1000.csv"
SAMPLE_HOST_PATH = "./workdirs/shared-data/incoming/offers-1000.csv"


def _dagster_module(plan):
    return next(m for m in plan["modules"] if m["id"] == "dagster")


def _enabled_plan(plan):
    enabled = deepcopy(plan)
    _dagster_module(enabled)["config"]["sampleData"] = dict(SAMPLE_DATA_ENABLED)
    return enabled


def _compose_service(compose_doc, fragment):
    return next(
        service for name, service in compose_doc["services"].items() if fragment in name
    )


def _sample_binds(compose_doc):
    return {
        name: [
            entry
            for entry in service.get("volumes", [])
            if isinstance(entry, dict)
            and "offers-1000.csv" in str(entry.get("source", ""))
        ]
        for name, service in compose_doc["services"].items()
    }


class SampleDataParameterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tempdir = tempfile.TemporaryDirectory()
        env_file = Path(cls._tempdir.name) / ".env"
        env_file.write_text(
            "\n".join(f"{key}={value}" for key, value in SECRET_VALUES.items()) + "\n",
            encoding="utf-8",
        )
        with mock.patch.dict(os.environ, SECRET_VALUES, clear=False):
            plan, diagnostics = build_plan(str(PROFILE), env_file=str(env_file))
        errors = [d.format() for d in diagnostics if d.level == "error"]
        if errors or plan is None:
            raise AssertionError(f"real profile did not produce a plan: {errors}")
        cls.plan = plan

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tempdir.cleanup()

    def _render_helm(self, plan):
        files, diagnostics = render_helm(plan)
        errors = [d.format() for d in diagnostics if d.level == "error"]
        self.assertEqual(errors, [])
        return files

    def _render_compose(self, plan):
        output, diagnostics = render_compose(plan)
        errors = [d.format() for d in diagnostics if d.level == "error"]
        self.assertEqual(errors, [])
        return yaml.safe_load(output)

    def test_schema_default_is_disabled_with_bundled_fixture_paths(self):
        sample_data = _dagster_module(self.plan)["config"]["sampleData"]
        self.assertEqual(
            sample_data,
            {
                "enabled": False,
                "hostPath": SAMPLE_HOST_PATH,
                "containerPath": SAMPLE_CONTAINER_PATH,
            },
        )

    def test_helm_default_omits_sample_data_configmap_and_mounts(self):
        files = self._render_helm(self.plan)
        self.assertFalse(any("sample-data" in name for name in files))
        for workload in (
            "dagster-user-code-deployment.yaml",
            "dagster-webserver-deployment.yaml",
            "dagster-daemon-deployment.yaml",
        ):
            # The CDS_OFFERS_FIXTURE_PATH env value legitimately names the
            # path; what must be absent when disabled is the volumeMount.
            self.assertNotIn(
                f"mountPath: {SAMPLE_CONTAINER_PATH}", files[f"templates/{workload}"]
            )

    def test_helm_enabled_renders_configmap_and_mounts_on_all_workloads(self):
        files = self._render_helm(_enabled_plan(self.plan))

        configmap = yaml.safe_load(
            files["templates/dagster-configmap-sample-data.yaml"]
        )
        self.assertEqual(configmap["kind"], "ConfigMap")
        self.assertEqual(
            configmap["metadata"]["name"], "{{ .Release.Name }}-dagster-sample-data"
        )
        csv_content = configmap["data"]["offers-1000.csv"]
        self.assertTrue(csv_content.startswith("Index,ean,Stock,Price"))
        # 1,000 data rows plus the header, matching OFFERS_EXPECTED_ROWS.
        self.assertEqual(len(csv_content.splitlines()), 1001)

        for workload in (
            "dagster-user-code-deployment.yaml",
            "dagster-webserver-deployment.yaml",
            "dagster-daemon-deployment.yaml",
        ):
            template = files[f"templates/{workload}"]
            self.assertIn(f"mountPath: {SAMPLE_CONTAINER_PATH}", template)
            self.assertIn("subPath: offers-1000.csv", template)
            self.assertIn("{{ .Release.Name }}-dagster-sample-data", template)

        user_code = files["templates/dagster-user-code-deployment.yaml"]
        self.assertIn("- name: CDS_SAMPLE_DATA_ENABLED\n", user_code)
        self.assertIn("value: 'true'", user_code)
        self.assertIn("- name: CDS_OFFERS_FIXTURE_PATH\n", user_code)
        self.assertIn(f"value: {SAMPLE_CONTAINER_PATH}", user_code)

    def test_compose_default_omits_sample_bind_but_pins_fixture_path(self):
        compose = self._render_compose(self.plan)
        binds = _sample_binds(compose)
        self.assertTrue(all(not entries for entries in binds.values()), binds)
        env = _compose_service(compose, "user-code")["environment"]
        self.assertIs(env["CDS_SAMPLE_DATA_ENABLED"], False)
        self.assertEqual(env["CDS_OFFERS_FIXTURE_PATH"], SAMPLE_CONTAINER_PATH)

    def test_compose_enabled_renders_readonly_bind_on_all_dagster_services(self):
        compose = self._render_compose(_enabled_plan(self.plan))
        binds = _sample_binds(compose)
        self.assertEqual(
            sorted(name for name, entries in binds.items() if entries),
            ["dagster-daemon", "dagster-user-code", "dagster-webserver"],
        )
        for entries in binds.values():
            for entry in entries:
                self.assertEqual(entry["type"], "bind")
                self.assertTrue(
                    entry["source"].endswith(
                        "workdirs/shared-data/incoming/offers-1000.csv"
                    )
                )
                self.assertEqual(entry["target"], SAMPLE_CONTAINER_PATH)
                self.assertIs(entry["read_only"], True)
        env = _compose_service(compose, "user-code")["environment"]
        self.assertIs(env["CDS_SAMPLE_DATA_ENABLED"], True)
        self.assertEqual(env["CDS_OFFERS_FIXTURE_PATH"], SAMPLE_CONTAINER_PATH)


class SampleDataScheduleTest(unittest.TestCase):
    """The load_offers_1000 job is scheduled only when the module exports
    CDS_SAMPLE_DATA_ENABLED=true (config.sampleData.enabled, issue #1117)."""

    def _load_definitions(self, env):
        saved = {
            name: sys.modules.get(name)
            for name in ("dagster", "psycopg2", "psycopg2.sql", "psycopg2.extras")
        }
        sys.modules["dagster"] = mock.MagicMock()
        sys.modules["psycopg2"] = mock.MagicMock()
        sys.modules["psycopg2.sql"] = mock.MagicMock()
        sys.modules["psycopg2.extras"] = mock.MagicMock()
        try:
            with mock.patch.dict(os.environ, env, clear=True):
                spec = importlib.util.spec_from_file_location(
                    "cds_dagster_definitions_sample_data", DEFINITIONS_PATH
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
        finally:
            for name, saved_module in saved.items():
                if saved_module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = saved_module
        return module

    def test_schedule_absent_by_default(self):
        module = self._load_definitions({})
        self.assertFalse(module.SAMPLE_DATA_ENABLED)
        self.assertIsNone(module.sample_data_schedule)

    def test_schedule_present_when_enabled(self):
        module = self._load_definitions({"CDS_SAMPLE_DATA_ENABLED": "true"})
        self.assertTrue(module.SAMPLE_DATA_ENABLED)
        self.assertIsNotNone(module.sample_data_schedule)

    def test_schedule_absent_when_explicitly_disabled(self):
        module = self._load_definitions({"CDS_SAMPLE_DATA_ENABLED": "false"})
        self.assertFalse(module.SAMPLE_DATA_ENABLED)
        self.assertIsNone(module.sample_data_schedule)


if __name__ == "__main__":
    unittest.main()
