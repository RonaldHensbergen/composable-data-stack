import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


def _load_dashboard_module():
    path = (
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "tender"
        / "provision-dashboard.py"
    )
    spec = importlib.util.spec_from_file_location("cds_tender_dashboard_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


_DASHBOARD = _load_dashboard_module()


class _FakeClient:
    def __init__(self) -> None:
        self.resources = {
            "database": [],
            "dataset": [],
            "dashboard": [],
            "chart": [],
        }
        self.created: list[tuple[str, int]] = []
        self.updated: list[tuple[str, int]] = []

    def list_all(self, resource):
        return self.resources[resource]

    def create(self, resource, payload):
        resource_id = len(self.resources[resource]) + 1
        record = {"id": resource_id, **payload}
        if resource == "dataset":
            record["database"] = {"id": payload["database"]}
        self.resources[resource].append(record)
        self.created.append((resource, resource_id))
        return resource_id

    def update(self, resource, resource_id, payload):
        record = next(
            item for item in self.resources[resource] if item["id"] == resource_id
        )
        record.update(payload)
        self.updated.append((resource, resource_id))


class TenderDashboardTest(unittest.TestCase):
    def test_client_rejects_non_http_urls(self) -> None:
        with self.assertRaisesRegex(ValueError, "absolute HTTP"):
            _DASHBOARD.SupersetClient("file:///tmp/superset")

    def test_analytics_uri_percent_encodes_credentials(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "CDS_ANALYTICS_DB_USER": "analytics user",
                "CDS_ANALYTICS_DB_PASSWORD": "slash/colon:at@",
                "CDS_ANALYTICS_DB_NAME": "analytics",
            },
            clear=False,
        ):
            uri = _DASHBOARD.analytics_uri()

        self.assertEqual(
            uri,
            "postgresql+psycopg2://analytics%20user:slash%2Fcolon%3Aat%40@postgres:5432/analytics",
        )

    def test_dashboard_defines_seven_analytical_views(self) -> None:
        definitions = _DASHBOARD.chart_definitions(42)

        self.assertEqual(len(definitions), 7)
        self.assertEqual(len({definition.name for definition in definitions}), 7)
        self.assertEqual(
            {definition.viz_type for definition in definitions},
            {"big_number_total", "echarts_timeseries_line", "pie", "table"},
        )
        for definition in definitions:
            self.assertEqual(definition.params["datasource"], "42__table")
        by_name = {definition.name: definition for definition in definitions}
        self.assertEqual(
            by_name["Tender publications by month"].params["granularity_sqla"],
            "publicatie_maand",
        )
        self.assertEqual(
            by_name["Published in last 30 days"].params["granularity_sqla"],
            "publicatie_datum",
        )

    def test_dashboard_layout_contains_every_chart_once(self) -> None:
        charts = [(index, f"Chart {index}") for index in range(1, 8)]
        position = json.loads(_DASHBOARD.dashboard_position(charts))

        chart_nodes = [
            node
            for node in position.values()
            if isinstance(node, dict) and node.get("type") == "CHART"
        ]
        self.assertEqual(len(chart_nodes), 7)
        self.assertEqual(
            {node["meta"]["chartId"] for node in chart_nodes}, set(range(1, 8))
        )
        for row_id in ("ROW-KPIS", "ROW-TRENDS", "ROW-DETAILS"):
            widths = [
                position[node_id]["meta"]["width"]
                for node_id in position[row_id]["children"]
            ]
            self.assertEqual(sum(widths), 12)

    def test_provisioning_is_idempotent(self) -> None:
        client = _FakeClient()
        env = {
            "CDS_ANALYTICS_DB_USER": "analytics",
            "CDS_ANALYTICS_DB_PASSWORD": "password",
            "CDS_ANALYTICS_DB_NAME": "analytics",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            first_dashboard, first_charts = _DASHBOARD.provision(client)
            initial_creates = list(client.created)
            second_dashboard, second_charts = _DASHBOARD.provision(client)

        self.assertEqual(first_dashboard, second_dashboard)
        self.assertEqual(first_charts, second_charts)
        self.assertEqual(len(initial_creates), 10)
        self.assertEqual(client.created, initial_creates)
        self.assertEqual(len(client.resources["chart"]), 7)
        self.assertEqual(client.updated.count(("dashboard", first_dashboard)), 2)


if __name__ == "__main__":
    unittest.main()
