"""Docker runtime smoke test for the experimental observability modules (#1065).

Runs only with CDS_RUN_DOCKER_SMOKE=1 and a working daemon; skips otherwise.
Renders `profiles/local-loki-otel-collector` (loki + otel-collector, both
experimental) to its own compose project, brings it up, and proves the
"renders green but fails at runtime" class of bugs is caught in CI:

- every container reaches a running state, and the collector's health
  endpoint (published on the host loopback) answers 200 — the pinned images
  render no container-side healthcheck (the otel contrib image is
  distroless), so health is probed over HTTP instead,
- the loki ingest endpoint accepts a raw and a structured JSON record on
  POST /logs,
- both records are queryable in the Loki store under their retention tier,
- the otel-collector OTLP/HTTP endpoint accepts a log record (visible in the
  collector's own log, since no backend is bound) and its health endpoint
  answers.
"""

import json
import os
import shutil
import subprocess
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

from cli.planner import build_plan
from cli.renderer import render_compose
from cli.validator import validate_profile

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILE = REPO_ROOT / "profiles" / "local-loki-otel-collector" / "profile.yaml"

PROJECT = "cds-observability-smoke-proof"
COMPOSE_FILE = REPO_ROOT / "docker-compose.observability-smoke.yml"

INGEST_PORT = 8686
OTLP_HTTP_PORT = 4318
HEALTH_PORT = 13133

# Retry only known-transient registry flakes (mirrors test_compose_runtime_smoke.py).
_TRANSIENT_REGISTRY_ERROR_MARKERS = (
    "Client.Timeout exceeded while awaiting headers",
    "TLS handshake timeout",
    "connection reset by peer",
    "i/o timeout",
    "net/http: request canceled",
    "toomanyrequests",
    "unexpected EOF",
)


def _http_json(
    url: str, payload: dict | None = None, timeout: int = 30
) -> tuple[int, bytes]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


class ObservabilityRuntimeSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.getenv("CDS_RUN_DOCKER_SMOKE") != "1":
            raise unittest.SkipTest(
                "Set CDS_RUN_DOCKER_SMOKE=1 to run Docker runtime proof tests"
            )

        if shutil.which("docker") is None:
            raise unittest.SkipTest("Docker CLI not available")

        docker_info = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
        )
        if docker_info.returncode != 0:
            raise unittest.SkipTest("Docker daemon is not available")

    def _compose(self) -> list[str]:
        # Bind sources resolve relative to the compose file's directory, so
        # it lives at the repo root like production (deleted afterwards).
        return ["docker", "compose", "-f", str(COMPOSE_FILE), "-p", PROJECT]

    def _run(
        self, args: list[str], env: dict[str, str], timeout: int = 300
    ) -> subprocess.CompletedProcess:
        result = subprocess.run(
            args,
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"command failed: {args}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        return result

    def _logs(
        self, env: dict[str, str], service: str | None = None, tail: int = 200
    ) -> str:
        args = [*self._compose(), "logs", "--tail", str(tail)]
        if service is not None:
            args.append(service)
        result = subprocess.run(
            args, cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120
        )
        return result.stdout + result.stderr

    def _up(self, env: dict[str, str]) -> None:
        last_output = ""
        for attempt in range(1, 4):
            result = subprocess.run(
                [*self._compose(), "up", "-d"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=1200,
            )
            if result.returncode == 0:
                return
            last_output = result.stdout + result.stderr
            if (
                not any(m in last_output for m in _TRANSIENT_REGISTRY_ERROR_MARKERS)
                or attempt == 3
            ):
                break
            time.sleep(15)
        self.fail(
            "Could not start the observability smoke stack after 3 attempt(s), "
            f"container logs follow.\nup output:\n{last_output}\nlogs:\n{self._logs(env)}"
        )

    def _wait_all_running(
        self, env: dict[str, str], deadline_seconds: int = 420
    ) -> None:
        # The loki/otel images render no container-side healthcheck (the otel
        # contrib image is distroless), so "healthy" is asserted afterwards
        # by probing the published health/ingest ports from the host.
        started = time.monotonic()
        last_ps = ""
        while time.monotonic() - started < deadline_seconds:
            result = subprocess.run(
                [*self._compose(), "ps", "--format", "json"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            last_ps = result.stdout + result.stderr
            services = self._parse_ps(result.stdout)
            if services and all(s.get("State") == "running" for s in services):
                return
            time.sleep(5)
        self.fail(
            f"observability smoke services never reached running within "
            f"{deadline_seconds}s.\nps output:\n{last_ps}\nlogs:\n{self._logs(env)}"
        )

    @staticmethod
    def _parse_ps(output: str) -> list[dict]:
        output = output.strip()
        if not output:
            return []
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError:
            parsed = [json.loads(line) for line in output.splitlines() if line.strip()]
        if isinstance(parsed, dict):
            parsed = [parsed]
        return parsed

    def _wait_http_ok(
        self,
        env: dict[str, str],
        url: str,
        payload: dict | None = None,
        deadline_seconds: int = 120,
        what: str = "endpoint",
    ) -> None:
        started = time.monotonic()
        last_error = ""
        while time.monotonic() - started < deadline_seconds:
            try:
                status, body = _http_json(url, payload, timeout=15)
            except urllib.error.URLError as error:
                status, body = 0, str(error).encode()
            if 200 <= status < 300:
                return
            last_error = f"HTTP {status}: {body.decode('utf-8', 'replace')[:500]}"
            time.sleep(5)
        self.fail(
            f"{what} {url} never answered 2xx within "
            f"{deadline_seconds}s (last: {last_error}).\nlogs:\n{self._logs(env)}"
        )

    def _wait_ingest_accepts(
        self, env: dict[str, str], url: str, payload: dict, deadline_seconds: int = 120
    ) -> None:
        self._wait_http_ok(env, url, payload, deadline_seconds, what="ingest endpoint")

    def _loki_query(self, env: dict[str, str], query: str) -> dict:
        # loki-storage has no host-published port; query it over the compose
        # network from the vector container (busybox wget in the alpine
        # image). LogQL stream selectors need the *range* query endpoint with
        # explicit start/end (an instant /query returns 400 for them).
        end = int(time.time() * 10**9)
        start = end - 3600 * 10**9
        params = urllib.parse.urlencode(
            {"query": query, "start": str(start), "end": str(end), "limit": "100"}
        )
        url = f"http://loki-storage:3100/loki/api/v1/query_range?{params}"
        result = subprocess.run(
            [*self._compose(), "exec", "-T", "loki", "wget", "-qO-", url],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"loki query failed: {query}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        return json.loads(result.stdout)

    def _wait_loki_contains(
        self,
        env: dict[str, str],
        query: str,
        marker: str,
        tier: str,
        deadline_seconds: int = 180,
    ) -> None:
        started = time.monotonic()
        last_body = ""
        while time.monotonic() - started < deadline_seconds:
            body = self._loki_query(env, query)
            last_body = json.dumps(body)
            for stream in body.get("data", {}).get("result", []):
                labels = stream.get("stream", {})
                values = stream.get("values", [])
                if labels.get("tier") == tier and any(
                    marker in line for _ts, line in values
                ):
                    return
            time.sleep(5)
        self.fail(
            f"loki never returned the {tier} record {marker!r} for query {query} "
            f"within {deadline_seconds}s.\nlast response:\n{last_body[:2000]}\n"
            f"logs:\n{self._logs(env)}"
        )

    def test_experimental_observability_modules_ingest_and_store(self) -> None:
        diagnostics = validate_profile(str(PROFILE))
        self.assertEqual(
            [d for d in diagnostics if d.level == "error"],
            [],
            msg=f"unexpected validation errors: {diagnostics}",
        )

        plan, plan_diags = build_plan(str(PROFILE))
        self.assertIsNotNone(plan)
        self.assertEqual(
            [d for d in plan_diags if d.level == "error"],
            [],
            msg=f"unexpected plan errors: {plan_diags}",
        )
        output, render_diags = render_compose(plan, output_path=str(COMPOSE_FILE))
        self.assertEqual(
            [d for d in render_diags if d.level == "error"],
            [],
            msg=f"unexpected render errors: {render_diags}",
        )

        compose = yaml.safe_load(output)
        self.assertEqual(
            sorted(compose["services"]), ["loki", "loki-storage", "otel-collector"]
        )
        self.assertEqual(
            compose["services"]["loki"]["ports"], [f"127.0.0.1:{INGEST_PORT}:8686"]
        )
        self.assertEqual(
            compose["services"]["loki"]["environment"]["LOKI_ENDPOINT"],
            "http://loki-storage:3100",
        )

        # Scrub ambient CDS_* so the shell can never override the profile.
        env = {k: v for k, v in os.environ.items() if not k.startswith("CDS_")}

        def cleanup() -> None:
            subprocess.run(
                [*self._compose(), "down", "-v", "--remove-orphans"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=300,
            )

        try:
            cleanup()
            COMPOSE_FILE.write_text(output, encoding="utf-8")
            self._up(env)
            self._wait_all_running(env)

            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            raw_marker = f"observability-smoke-raw-{stamp}"
            structured_marker = f"observability-smoke-structured-{stamp}"
            otel_marker = f"observability-smoke-otlp-{stamp}"

            ingest_url = f"http://127.0.0.1:{INGEST_PORT}/logs"
            raw_record = {"message": raw_marker, "level": "info"}
            structured_record = {
                "service": "observability-smoke",
                "profile": "local-loki-otel-collector",
                "environment": "local",
                "timestamp": stamp,
                "severity": "INFO",
                "message": structured_marker,
            }
            # Vector accepts events once its HTTP source is listening.
            self._wait_ingest_accepts(env, ingest_url, raw_record)
            status, body = _http_json(ingest_url, structured_record)
            self.assertTrue(
                200 <= status < 300,
                f"structured POST /logs failed: HTTP {status}: {body.decode('utf-8', 'replace')[:500]}",
            )

            # OTLP/HTTP ingest + health endpoint on the collector (poll until
            # the collector process is actually serving, not just running).
            self._wait_http_ok(
                env,
                f"http://127.0.0.1:{HEALTH_PORT}/",
                deadline_seconds=120,
                what="collector health endpoint",
            )
            otlp_payload = {
                "resourceLogs": [
                    {
                        "resource": {},
                        "scopeLogs": [
                            {
                                "scope": {},
                                "logRecords": [
                                    {
                                        "timeUnixNano": str(int(time.time()) * 10**9),
                                        "severityText": "INFO",
                                        "body": {"stringValue": otel_marker},
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
            self._wait_http_ok(
                env,
                f"http://127.0.0.1:{OTLP_HTTP_PORT}/v1/logs",
                otlp_payload,
                deadline_seconds=120,
                what="OTLP/HTTP ingest endpoint",
            )

            # The store must return both records under their retention tier.
            self._wait_loki_contains(env, '{tier="raw"}', raw_marker, "raw")
            self._wait_loki_contains(
                env, '{tier="structured"}', structured_marker, "structured"
            )

            # With no backend bound, the collector logs the received record.
            deadline = time.monotonic() + 60
            otel_logs = ""
            while time.monotonic() < deadline:
                otel_logs = self._logs(env, service="otel-collector", tail=100)
                if otel_marker in otel_logs:
                    break
                time.sleep(5)
            self.assertIn(
                otel_marker,
                otel_logs,
                msg=f"otel-collector never logged the ingested OTLP record.\nlogs:\n{otel_logs[-4000:]}",
            )
        finally:
            cleanup()
            if COMPOSE_FILE.exists():
                COMPOSE_FILE.unlink()


if __name__ == "__main__":
    unittest.main()
