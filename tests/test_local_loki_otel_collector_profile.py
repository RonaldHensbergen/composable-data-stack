"""Contract coverage for the local-loki-otel-collector profile.

Names `profiles/local-loki-otel-collector` (see docs/contract-coverage.md,
issue #1065) and exercises its contract behavior: the profile validates,
plans, and renders to Docker Compose without errors, with the loki module's
`log-sink` contract and the otel-collector module's `log-sink`/`metrics-sink`/
`trace-sink` contracts resolved, the loopback-only host publishes wired, and
the Vector -> Loki storage endpoint pointing at the actually-rendered
`loki-storage` service (regression for the namesake module id `loki`).
The runtime proof (up -> ingest -> query -> down) lives in
tests/test_observability_runtime_smoke.py and runs under
CDS_RUN_DOCKER_SMOKE=1.
"""
import unittest
from pathlib import Path

import yaml

from cli.planner import build_plan
from cli.renderer import render_compose
from cli.validator import validate_profile

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = REPO_ROOT / "profiles" / "local-loki-otel-collector" / "profile.yaml"


class LocalLokiOtelCollectorProfileTest(unittest.TestCase):
    def test_profile_validates_without_errors(self) -> None:
        self.assertTrue(PROFILE_PATH.exists(), f"profile not found at {PROFILE_PATH}")
        diagnostics = validate_profile(str(PROFILE_PATH))
        errors = [d for d in diagnostics if d.level == "error"]
        self.assertEqual(errors, [])

    def test_profile_plans_and_renders_compose_for_both_observability_modules(self) -> None:
        plan, plan_diags = build_plan(str(PROFILE_PATH))
        self.assertIsNotNone(plan)
        errors = [d for d in plan_diags if d.level == "error"]
        self.assertEqual(errors, [])

        module_ids = {m["id"] for m in plan["modules"]}
        self.assertEqual(module_ids, {"loki", "otel-collector"})

        loki = next(m for m in plan["modules"] if m["id"] == "loki")
        provided = loki["provides"]["log-sink"]["spec"]
        # The contract advertises Loki's native OTLP/HTTP endpoint on the
        # loki-storage service (issue #1076); the Vector gateway on the
        # namesake `loki` service stays the host-loopback plain-JSON endpoint.
        self.assertEqual(provided["host"], "loki-storage")
        self.assertEqual(provided["port"], 3100)
        self.assertEqual(provided["protocol"], "otlp-http")
        self.assertEqual(provided["ingestPath"], "/otlp/v1/logs")
        self.assertEqual(provided["connectionUri"], "http://loki-storage:3100")
        self.assertEqual(provided["serviceName"], "loki-storage")

        otel = next(m for m in plan["modules"] if m["id"] == "otel-collector")
        sinks = otel["provides"]
        self.assertEqual(sorted(sinks), ["log-sink", "metrics-sink", "trace-sink"])
        for name, ingest_path in (
            ("log-sink", "/v1/logs"),
            ("metrics-sink", "/v1/metrics"),
            ("trace-sink", "/v1/traces"),
        ):
            spec = sinks[name]["spec"]
            self.assertEqual(spec["host"], "otel-collector")
            self.assertEqual(spec["port"], 4318)
            self.assertEqual(spec["protocol"], "otlp-http")
            self.assertEqual(spec["ingestPath"], ingest_path)
            self.assertEqual(
                spec["connectionUri"], "http://otel-collector:4318"
            )

        output, render_diags = render_compose(plan)
        errors = [d for d in render_diags if d.level == "error"]
        self.assertEqual(errors, [])
        compose = yaml.safe_load(output)

        self.assertEqual(
            sorted(compose["services"]), ["loki", "loki-storage", "otel-collector"]
        )

        loki_service = compose["services"]["loki"]
        self.assertEqual(loki_service["ports"], ["127.0.0.1:8686:8686"])
        # The Vector gateway must reach the rendered `loki-storage` service —
        # with the namesake module id `loki` a `${service.host}-loki-storage`
        # template would render the non-existent `loki-loki-storage`.
        self.assertEqual(
            loki_service["environment"]["LOKI_ENDPOINT"],
            "http://loki-storage:3100",
        )
        bind_targets = {
            v["target"]
            for v in loki_service["volumes"]
            if isinstance(v, dict) and v.get("type") == "bind"
        }
        self.assertIn("/etc/vector/vector.yaml", bind_targets)

        otel_service = compose["services"]["otel-collector"]
        self.assertEqual(
            otel_service["ports"],
            ["127.0.0.1:4317:4317", "127.0.0.1:4318:4318", "127.0.0.1:13133:13133"],
        )
        # The pinned contrib image is distroless, so no container-side
        # healthcheck is rendered; the health port is probed from the host.
        self.assertNotIn("healthcheck", otel_service)
        # No backends.* bindings: every signal falls back to the discard
        # endpoint instead of a forwarding URL.
        env = otel_service["environment"]
        self.assertEqual(env["BACKEND_LOGS_ENDPOINT"], "")
        self.assertEqual(env["BACKEND_METRICS_ENDPOINT"], "")
        self.assertEqual(env["BACKEND_TRACES_ENDPOINT"], "")


if __name__ == "__main__":
    unittest.main()
