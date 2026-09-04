#!/usr/bin/env python3
"""Provision the local TenderNed Superset database, dataset, charts, and dashboard."""

from __future__ import annotations

import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

DATABASE_NAME = "CDS Tender Analytics"
DATASET_SCHEMA = "tender_analytics"
DATASET_TABLE = "tenders_dashboard"
DASHBOARD_TITLE = "TenderNed Analytics"
DASHBOARD_SLUG = "tender-analytics"


class SupersetApiError(RuntimeError):
    """Raised when the local Superset API rejects a provisioning operation."""


class SupersetClient:
    def __init__(self, base_url: str) -> None:
        parsed_url = urllib.parse.urlsplit(base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("Superset URL must be an absolute HTTP or HTTPS URL")
        self.base_url = base_url.rstrip("/")
        cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cookie_jar)
        )
        self.access_token = ""
        self.csrf_token = ""

    def login(self, username: str, password: str) -> None:
        result = self._request(
            "POST",
            "/api/v1/security/login",
            {
                "username": username,
                "password": password,
                "provider": "db",
                "refresh": True,
            },
            authenticated=False,
        )
        self.access_token = result["access_token"]
        self.csrf_token = self._request("GET", "/api/v1/security/csrf_token/")["result"]

    def list_all(self, resource: str) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({"q": "(page:0,page_size:100)"})
        return self._request("GET", f"/api/v1/{resource}/?{query}")["result"]

    def create(self, resource: str, payload: dict[str, Any]) -> int:
        response = self._request("POST", f"/api/v1/{resource}/", payload)
        return int(response["id"])

    def update(self, resource: str, resource_id: int, payload: dict[str, Any]) -> None:
        self._request("PUT", f"/api/v1/{resource}/{resource_id}", payload)

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        authenticated: bool = True,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if authenticated:
            headers["Authorization"] = f"Bearer {self.access_token}"
            if method in {"POST", "PUT", "PATCH", "DELETE"}:
                headers["X-CSRFToken"] = self.csrf_token
        # The constructor rejects schemes that could open local files.
        request = urllib.request.Request(  # noqa: S310
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise SupersetApiError(
                f"Superset {method} {path} failed with HTTP {error.code}: {body}"
            ) from error
        except urllib.error.URLError as error:
            raise SupersetApiError(
                f"Cannot reach Superset at {self.base_url}: {error.reason}"
            ) from error


@dataclass(frozen=True)
class ChartDefinition:
    name: str
    viz_type: str
    params: dict[str, Any]


def analytics_uri() -> str:
    user = urllib.parse.quote(os.environ["CDS_ANALYTICS_DB_USER"], safe="")
    password = urllib.parse.quote(os.environ["CDS_ANALYTICS_DB_PASSWORD"], safe="")
    database = urllib.parse.quote(os.environ["CDS_ANALYTICS_DB_NAME"], safe="")
    return f"postgresql+psycopg2://{user}:{password}@postgres:5432/{database}"


def chart_definitions(dataset_id: int) -> list[ChartDefinition]:
    datasource = f"{dataset_id}__table"
    base = {
        "datasource": datasource,
        "adhoc_filters": [],
        "granularity_sqla": "publicatie_datum",
        "time_range": "No filter",
    }
    return [
        ChartDefinition(
            "Total tenders",
            "big_number_total",
            {
                **base,
                "viz_type": "big_number_total",
                "metric": "count",
                "header_font_size": 0.45,
                "subheader_font_size": 0.15,
                "y_axis_format": "SMART_NUMBER",
            },
        ),
        ChartDefinition(
            "Published in last 30 days",
            "big_number_total",
            {
                **base,
                "viz_type": "big_number_total",
                "metric": "count",
                "time_range": "Last 30 days",
                "header_font_size": 0.45,
                "subheader_font_size": 0.15,
                "y_axis_format": "SMART_NUMBER",
            },
        ),
        ChartDefinition(
            "PDF enriched tenders",
            "big_number_total",
            {
                **base,
                "viz_type": "big_number_total",
                "metric": "count",
                "adhoc_filters": [
                    {
                        "clause": "WHERE",
                        "comparator": True,
                        "expressionType": "SIMPLE",
                        "filterOptionName": "filter_pdf_enriched",
                        "fromFormData": True,
                        "operator": "==",
                        "sqlExpression": None,
                        "subject": "has_pdf",
                    }
                ],
                "header_font_size": 0.45,
                "subheader_font_size": 0.15,
                "y_axis_format": "SMART_NUMBER",
            },
        ),
        ChartDefinition(
            "Tender publications by month",
            "echarts_timeseries_line",
            {
                **base,
                "viz_type": "echarts_timeseries_line",
                "granularity_sqla": "publicatie_maand",
                "x_axis": "publicatie_maand",
                "time_grain_sqla": "P1M",
                "metrics": ["count"],
                "groupby": [],
                "row_limit": 10000,
                "truncate_metric": True,
                "show_legend": False,
                "rich_tooltip": True,
                "tooltipTimeFormat": "%b %Y",
                "y_axis_format": "SMART_NUMBER",
                "x_axis_time_format": "smart_date",
            },
        ),
        ChartDefinition(
            "Tenders by contract type",
            "pie",
            {
                **base,
                "viz_type": "pie",
                "groupby": ["type_opdracht"],
                "metric": "count",
                "row_limit": 10,
                "sort_by_metric": True,
                "show_labels": True,
                "show_legend": True,
                "label_type": "key_value_percent",
                "number_format": "SMART_NUMBER",
            },
        ),
        ChartDefinition(
            "European tender share",
            "pie",
            {
                **base,
                "viz_type": "pie",
                "groupby": ["europees"],
                "metric": "count",
                "row_limit": 10,
                "sort_by_metric": True,
                "show_labels": True,
                "show_legend": True,
                "label_type": "key_value_percent",
                "number_format": "SMART_NUMBER",
            },
        ),
        ChartDefinition(
            "Top contracting authorities",
            "table",
            {
                **base,
                "viz_type": "table",
                "query_mode": "aggregate",
                "groupby": ["opdrachtgever_naam"],
                "metrics": ["count"],
                "percent_metrics": [],
                "timeseries_limit_metric": "count",
                "order_desc": True,
                "row_limit": 12,
                "include_search": True,
                "page_length": 12,
                "table_timestamp_format": "%Y-%m-%d %H:%M:%S",
            },
        ),
    ]


def dashboard_position(chart_ids: list[tuple[int, str]]) -> str:
    if len(chart_ids) != 7:
        raise ValueError("TenderNed dashboard requires exactly seven charts")

    layout: dict[str, Any] = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {
            "id": "ROOT_ID",
            "type": "ROOT",
            "children": ["GRID_ID"],
        },
        "GRID_ID": {
            "id": "GRID_ID",
            "type": "GRID",
            "children": ["ROW-KPIS", "ROW-TRENDS", "ROW-DETAILS"],
            "parents": ["ROOT_ID"],
        },
    }
    rows = [
        ("ROW-KPIS", chart_ids[0:3], 4, 18),
        ("ROW-TRENDS", chart_ids[3:5], 6, 34),
        ("ROW-DETAILS", chart_ids[5:7], 6, 34),
    ]
    for row_id, charts, width, height in rows:
        children = [f"CHART-{chart_id}" for chart_id, _ in charts]
        layout[row_id] = {
            "id": row_id,
            "type": "ROW",
            "children": children,
            "parents": ["ROOT_ID", "GRID_ID"],
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
        }
        for chart_id, chart_name in charts:
            layout[f"CHART-{chart_id}"] = {
                "id": f"CHART-{chart_id}",
                "type": "CHART",
                "children": [],
                "parents": ["ROOT_ID", "GRID_ID", row_id],
                "meta": {
                    "chartId": chart_id,
                    "height": height,
                    "width": width,
                    "sliceName": chart_name,
                },
            }
    return json.dumps(layout, separators=(",", ":"), sort_keys=True)


def find_one(
    records: list[dict[str, Any]], field: str, value: str
) -> dict[str, Any] | None:
    return next((record for record in records if record.get(field) == value), None)


def provision(client: SupersetClient) -> tuple[int, list[tuple[int, str]]]:
    database_payload = {
        "database_name": DATABASE_NAME,
        "sqlalchemy_uri": analytics_uri(),
        "expose_in_sqllab": True,
        "allow_dml": False,
    }
    database = find_one(client.list_all("database"), "database_name", DATABASE_NAME)
    if database is None:
        database_id = client.create("database", database_payload)
    else:
        database_id = int(database["id"])
        client.update("database", database_id, database_payload)

    dataset = next(
        (
            record
            for record in client.list_all("dataset")
            if record.get("table_name") == DATASET_TABLE
            and record.get("schema") == DATASET_SCHEMA
            and int(record.get("database", {}).get("id", -1)) == database_id
        ),
        None,
    )
    if dataset is None:
        dataset_id = client.create(
            "dataset",
            {
                "database": database_id,
                "schema": DATASET_SCHEMA,
                "table_name": DATASET_TABLE,
            },
        )
    else:
        dataset_id = int(dataset["id"])

    dashboards = client.list_all("dashboard")
    dashboard = find_one(dashboards, "slug", DASHBOARD_SLUG) or find_one(
        dashboards, "dashboard_title", DASHBOARD_TITLE
    )
    dashboard_payload = {
        "dashboard_title": DASHBOARD_TITLE,
        "slug": DASHBOARD_SLUG,
        "published": True,
        "json_metadata": json.dumps(
            {
                "cross_filters_enabled": True,
                "default_filters": "{}",
                "expanded_slices": {},
                "native_filter_configuration": [],
                "refresh_frequency": 0,
                "timed_refresh_immune_slices": [],
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
    }
    if dashboard is None:
        dashboard_id = client.create("dashboard", dashboard_payload)
    else:
        dashboard_id = int(dashboard["id"])

    existing_charts = client.list_all("chart")
    chart_ids: list[tuple[int, str]] = []
    for definition in chart_definitions(dataset_id):
        payload = {
            "slice_name": definition.name,
            "viz_type": definition.viz_type,
            "datasource_id": dataset_id,
            "datasource_type": "table",
            "params": json.dumps(
                definition.params, separators=(",", ":"), sort_keys=True
            ),
            "dashboards": [dashboard_id],
        }
        existing = next(
            (
                record
                for record in existing_charts
                if record.get("slice_name") == definition.name
                and int(record.get("datasource_id", -1)) == dataset_id
                and record.get("datasource_type") == "table"
            ),
            None,
        )
        if existing is None:
            chart_id = client.create("chart", payload)
        else:
            chart_id = int(existing["id"])
            client.update("chart", chart_id, payload)
        chart_ids.append((chart_id, definition.name))

    dashboard_payload["position_json"] = dashboard_position(chart_ids)
    client.update("dashboard", dashboard_id, dashboard_payload)
    return dashboard_id, chart_ids


def required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SupersetApiError(f"Required environment variable is missing: {name}")
    return value


def main() -> int:
    for name in (
        "CDS_ANALYTICS_DB_NAME",
        "CDS_ANALYTICS_DB_USER",
        "CDS_ANALYTICS_DB_PASSWORD",
        "CDS_SUPERSET_ADMIN_PASSWORD",
    ):
        required_env(name)

    base_url = os.environ.get("SUPERSET_URL", "http://127.0.0.1:8088")
    client = SupersetClient(base_url)
    client.login(
        os.environ.get("SUPERSET_ADMIN_USERNAME", "admin"),
        os.environ["CDS_SUPERSET_ADMIN_PASSWORD"],
    )
    dashboard_id, chart_ids = provision(client)
    print(f"dashboard={base_url}/superset/dashboard/{DASHBOARD_SLUG}/")
    print(f"dashboard_id={dashboard_id}")
    print(f"charts={len(chart_ids)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, SupersetApiError, ValueError) as error:
        print(f"ERROR {error}", file=sys.stderr)
        raise SystemExit(1) from error
