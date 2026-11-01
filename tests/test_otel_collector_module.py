"""trace-sink/metrics-sink contracts + otel-collector reference module (#777).

Covers the acceptance criteria of #777:

- shared/contracts/trace-sink.yaml and metrics-sink.yaml are schema-valid via
  cli.validator.validate_contract_file (the repo-wide contract test also
  globs them; this test pins the files and their OTLP-shaped fields).
- modules-experimental/observability/otel-collector/module.yaml is
  module-schema valid, provides log-sink/metrics-sink/trace-sink over
  OTLP/HTTP, and consumes optional backend sinks to forward each signal.
- A profile with the module validates, plans, and renders to Compose: the
  provided sink contracts resolve to the rendered Compose DNS name (E150
  clean), OTLP ports are wired, and unbound backends fall back to a discard
  endpoint; a bound backend (contractRef) is resolved into the forwarding
  env vars.
"""
import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from cli.planner import build_plan
from cli.renderer import render_compose
from cli.validator import validate_contract_file, validate_profile

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = REPO_ROOT / "shared" / "contracts"
TRACE_SINK_PATH = CONTRACTS_DIR / "trace-sink.yaml"
METRICS_SINK_PATH = CONTRACTS_DIR / "metrics-sink.yaml"
MODULE_PATH = REPO_ROOT / "modules-experimental" / "observability" / "otel-collector" / "module.yaml"
MODULE_SCHEMA = json.loads(
    (REPO_ROOT / "cli" / "resources" / "module.schema.json").read_text(encoding="utf-8")
)

_TEST_ENV = {"CDS_MODULE_PATH": str(REPO_ROOT)}


def _profile(otel_config: dict, extra_modules: tuple[dict, ...] = ()) -> dict:
    # keydb stands in for an arbitrary workload module in the same profile.
    return {
        "apiVersion": "cds/v1alpha1",
        "kind": "Profile",
        "metadata": {"name": "otel-test", "environment": "local"},
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
                    "id": "otel",
                    "source": "modules-experimental/observability/otel-collector",
                    "version": "0.1.0",
                    "enabled": True,
                    "config": otel_config,
                },
                *extra_modules,
            ],
        },
    }


def _plan_and_render(profile: dict):
    with tempfile.TemporaryDirectory() as tmp:
        profile_path = Path(tmp) / "profile.yaml"
        profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")
        with unittest.mock.patch.dict(os.environ, _TEST_ENV, clear=False):
            diagnostics = validate_profile(str(profile_path))
            errors = [d.format() for d in diagnostics if d.level == "error"]
            assert errors == [], errors
            plan, plan_diagnostics = build_plan(str(profile_path))
    errors = [d.format() for d in plan_diagnostics if d.level == "error"]
    assert errors == [], errors
    assert plan is not None
    with unittest.mock.patch.dict(os.environ, _TEST_ENV, clear=False):
        compose_text, render_diagnostics = render_compose(plan)
    errors = [d.format() for d in render_diagnostics if d.level == "error"]
    assert errors == [], errors
    return plan, yaml.safe_load(compose_text)


class SinkContractTest(unittest.TestCase):
    def test_contract_files_are_schema_valid(self) -> None:
        self.assertEqual(validate_contract_file(TRACE_SINK_PATH), [])
        self.assertEqual(validate_contract_file(METRICS_SINK_PATH), [])

    def test_trace_sink_contract_shape(self) -> None:
        contract = yaml.safe_load(TRACE_SINK_PATH.read_text(encoding="utf-8"))
        self.assertEqual(contract["metadata"]["name"], "trace-sink")
        fields = contract["spec"]["fields"]
        for name in ("host", "port", "protocol"):
            self.assertTrue(fields[name]["required"], name)
        for name in ("ingestPath", "connectionUri", "serviceName"):
            self.assertIn(name, fields)
            self.assertFalse(fields[name]["required"], name)
        examples = contract["spec"]["examples"]
        self.assertTrue(any(e.get("protocol") == "otlp-grpc" and e.get("port") == 4317 for e in examples))
        self.assertTrue(
            any(e.get("protocol") == "otlp-http" and e.get("ingestPath") == "/v1/traces" for e in examples)
        )

    def test_metrics_sink_contract_shape(self) -> None:
        contract = yaml.safe_load(METRICS_SINK_PATH.read_text(encoding="utf-8"))
        self.assertEqual(contract["metadata"]["name"], "metrics-sink")
        fields = contract["spec"]["fields"]
        for name in ("host", "port", "protocol"):
            self.assertTrue(fields[name]["required"], name)
        # Both push (OTLP) and scrape (Prometheus) styles are expressible.
        self.assertIn("ingestPath", fields)
        self.assertIn("scrapePath", fields)
        self.assertTrue(
            any(e.get("protocol") == "prometheus" and e.get("scrapePath") == "/metrics" for e in contract["spec"]["examples"])
        )


class OtelCollectorModuleShapeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = yaml.safe_load(MODULE_PATH.read_text(encoding="utf-8"))

    def test_module_is_schema_valid(self) -> None:
        errors = list(Draft202012Validator(MODULE_SCHEMA).iter_errors(self.module))
        self.assertEqual(errors, [])

    def test_module_provides_the_three_sink_contracts(self) -> None:
        spec = self.module["spec"]
        self.assertFalse(self.module["metadata"]["productionSuitable"])
        self.assertEqual(self.module["metadata"]["category"], "observability")
        provides = {p["name"]: p["contract"] for p in spec["provides"]}
        self.assertEqual(sorted(provides), ["log-sink", "metrics-sink", "trace-sink"])
        self.assertEqual(provides["log-sink"]["kind"], "log-sink")
        self.assertEqual(provides["metrics-sink"]["kind"], "metrics-sink")
        self.assertEqual(provides["trace-sink"]["kind"], "trace-sink")
        for name, path in (("log-sink", "/v1/logs"), ("metrics-sink", "/v1/metrics"), ("trace-sink", "/v1/traces")):
            provided = provides[name]["spec"]
            self.assertEqual(provided["protocol"], "otlp-http")
            self.assertEqual(provided["ingestPath"], path)
            self.assertEqual(provided["port"], "${config.otlpHttpPort}")
            self.assertEqual(provided["host"], "${service.dns}")

    def test_module_consumes_optional_backends(self) -> None:
        consumes = {c["name"]: c for c in self.module["spec"]["consumes"]}
        self.assertEqual(sorted(consumes), ["log-backend", "metrics-backend", "trace-backend"])
        self.assertEqual(consumes["log-backend"]["contract"]["kind"], "log-sink")
        self.assertEqual(consumes["metrics-backend"]["contract"]["kind"], "metrics-sink")
        self.assertEqual(consumes["trace-backend"]["contract"]["kind"], "trace-sink")
        for name, path in (
            ("log-backend", "spec.config.backends.logs"),
            ("metrics-backend", "spec.config.backends.metrics"),
            ("trace-backend", "spec.config.backends.traces"),
        ):
            self.assertFalse(consumes[name]["required"])
            self.assertEqual(consumes[name]["mappedFrom"], path)

    def test_compose_service_is_hardened_and_pinned(self) -> None:
        service = self.module["spec"]["implementation"]["compose"]["services"]["otel-collector"]
        # Multi-arch manifest-list digest (not an amd64-only manifest), so the
        # pin pulls the native image on arm64 hosts too.
        self.assertEqual(
            service["image"],
            "otel/opentelemetry-collector-contrib:0.164.0"
            "@sha256:340c9ca6a36617084c6661bdc4f0fe92b7cda5a805d61673ec8db375fddc522b",
        )
        self.assertEqual(service["cap_drop"], ["ALL"])
        self.assertTrue(service["read_only"])
        self.assertIn("no-new-privileges:true", service["security_opt"])
        self.assertEqual(service["command"], ["--config", "/etc/otelcol/collector.yaml"])
        bind = next(v for v in service["volumes"] if isinstance(v, dict) and v.get("type") == "bind")
        self.assertEqual(bind["source"], "collector.yaml")
        self.assertEqual(bind["target"], "/etc/otelcol/collector.yaml")
        self.assertTrue(bind["read_only"])


class OtelCollectorPlanRenderTest(unittest.TestCase):
    def test_plan_resolves_provided_sinks_to_compose_dns(self) -> None:
        plan, _compose = _plan_and_render(_profile({}))
        otel = next(m for m in plan["modules"] if m["id"] == "otel")
        for name, path in (("log-sink", "/v1/logs"), ("metrics-sink", "/v1/metrics"), ("trace-sink", "/v1/traces")):
            provided = otel["provides"][name]["spec"]
            self.assertEqual(provided["host"], "otel-collector")
            self.assertEqual(provided["port"], 4318)
            self.assertEqual(provided["protocol"], "otlp-http")
            self.assertEqual(provided["ingestPath"], path)
            self.assertEqual(provided["connectionUri"], "http://otel-collector:4318")

    def test_render_wires_otlp_ports_and_discard_backends(self) -> None:
        _plan, compose = _plan_and_render(_profile({}))
        service = compose["services"]["otel-collector"]
        self.assertIn("otel/opentelemetry-collector-contrib:0.164.0@sha256:", service["image"])
        ports = sorted(service["ports"])
        self.assertEqual(
            ports,
            ["127.0.0.1:13133:13133", "127.0.0.1:4317:4317", "127.0.0.1:4318:4318"],
        )
        env = service["environment"]
        # No backend bound: all three forwarding endpoints render empty (the
        # collector config then falls back to its static discard endpoint).
        for signal in ("LOGS", "METRICS", "TRACES"):
            self.assertEqual(env[f"BACKEND_{signal}_ENDPOINT"], "")
        self.assertEqual(env["OTLP_GRPC_PORT"], "4317")
        self.assertEqual(env["OTLP_HTTP_PORT"], "4318")
        self.assertIn("healthcheck", service)

    def test_backend_binding_forwards_to_the_provider(self) -> None:
        backend = {
            "id": "otel-backend",
            "source": "modules-experimental/observability/otel-collector",
            "version": "0.1.0",
            "enabled": True,
            "config": {},
        }
        config = {"backends": {"traces": {"contractRef": "otel-backend.trace-sink"}}}
        plan, compose = _plan_and_render(_profile(config, (backend,)))

        otel = next(m for m in plan["modules"] if m["id"] == "otel")
        consumed = otel["consumes"]["trace-backend"]["contract"]["spec"]
        self.assertEqual(consumed["host"], "otel-backend-otel-collector")
        self.assertEqual(consumed["port"], 4318)
        self.assertEqual(consumed["connectionUri"], "http://otel-backend-otel-collector:4318")
        # Unbound signals stay absent from the consumes map.
        self.assertNotIn("log-backend", otel["consumes"])
        self.assertNotIn("metrics-backend", otel["consumes"])

        env = compose["services"]["otel-collector"]["environment"]
        # The otlphttp exporter uses a per-signal endpoint as the complete URL
        # (no /v1/<signal> is appended), so the forwarded value must be the
        # backend's connectionUri + ingestPath.
        self.assertEqual(env["BACKEND_TRACES_ENDPOINT"], "http://otel-backend-otel-collector:4318/v1/traces")
        self.assertEqual(env["BACKEND_LOGS_ENDPOINT"], "")

    def test_unknown_backend_contract_ref_fails_validation(self) -> None:
        config = {"backends": {"logs": {"contractRef": "nope.log-sink"}}}
        with tempfile.TemporaryDirectory() as tmp:
            profile_path = Path(tmp) / "profile.yaml"
            profile_path.write_text(yaml.safe_dump(_profile(config)), encoding="utf-8")
            with unittest.mock.patch.dict(os.environ, _TEST_ENV, clear=False):
                diagnostics = validate_profile(str(profile_path))
                plan, plan_diagnostics = build_plan(str(profile_path))
        codes = {d.code for d in [*diagnostics, *plan_diagnostics] if d.level == "error"}
        self.assertIn("E041", codes)


if __name__ == "__main__":
    unittest.main()
