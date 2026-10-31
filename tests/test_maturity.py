from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.main import main
from cli.maturity import build_maturity_report, format_maturity_report


def _module(module_id: str, *contract_kinds: str) -> dict:
    return {
        "id": module_id,
        "provides": {
            f"contract-{index}": {"kind": contract_kind}
            for index, contract_kind in enumerate(contract_kinds)
        },
    }


class MaturityReportTest(unittest.TestCase):
    def test_profile_with_no_optional_capabilities_meets_basic_only(self) -> None:
        report = build_maturity_report(
            {
                "sourceProfile": "profiles/minimal/profile.yaml",
                "metadata": {"name": "minimal"},
                "modules": [
                    _module("postgres", "sql-database"),
                    _module("keydb", "cache-service"),
                ],
            }
        )

        self.assertEqual(report["highestTier"], "Basic")
        self.assertEqual(
            [(tier["name"], tier["achieved"]) for tier in report["tiers"]],
            [("Basic", True), ("Automated", False)],
        )
        identity = next(
            capability
            for capability in report["capabilities"]
            if capability["id"] == "identity"
        )
        self.assertFalse(identity["bound"])
        self.assertEqual(identity["providerInstances"], [])

    def test_profile_with_multiple_capabilities_meets_automated(self) -> None:
        report = build_maturity_report(
            {
                "sourceProfile": "profiles/automated/profile.yaml",
                "metadata": {"name": "automated"},
                "modules": [
                    _module("postgres", "sql-database"),
                    _module("keydb", "cache-service"),
                    _module("keycloak", "identity-broker"),
                    _module("traefik", "reverse-proxy"),
                    _module("loki", "log-sink"),
                    _module("restic", "backup-target"),
                    _module("flux", "gitops-target"),
                    _module("istio", "service-mesh"),
                ],
            }
        )

        self.assertEqual(report["highestTier"], "Automated")
        self.assertEqual(
            [(tier["name"], tier["achieved"]) for tier in report["tiers"]],
            [("Basic", True), ("Automated", True)],
        )
        gitops = next(
            capability
            for capability in report["capabilities"]
            if capability["id"] == "gitops"
        )
        self.assertTrue(gitops["bound"])
        self.assertEqual(gitops["providerInstances"], ["flux"])
        self.assertNotIn("gitops", report["tiers"][1]["requirements"])

    def test_text_output_lists_tier_and_provider_modules(self) -> None:
        report = build_maturity_report(
            {
                "metadata": {"name": "example"},
                "modules": [_module("postgres", "sql-database")],
            }
        )

        text = format_maturity_report(report)

        self.assertIn("Highest tier: Not yet Basic", text)
        self.assertIn("Database | sql-database | Yes | postgres", text)


class MaturityCommandTest(unittest.TestCase):
    def test_json_command_uses_static_plan_and_allows_missing_secrets(self) -> None:
        plan = {
            "metadata": {"name": "minimal"},
            "modules": [
                _module("postgres", "sql-database"),
                _module("keydb", "cache-service"),
            ],
        }
        output = io.StringIO()
        with (
            patch.object(
                sys,
                "argv",
                [
                    "cds",
                    "maturity",
                    "minimal",
                    "--environment",
                    "prod",
                    "--format",
                    "json",
                ],
            ),
            patch("cli.main.load_env_file"),
            patch("cli.main.load_saved_environment", return_value=None),
            patch("cli.main.resolve_profile_path", return_value="/profiles/minimal/profile.yaml"),
            patch("cli.main.resolve_env_file_path", return_value=Path("/profiles/minimal/.env")),
            patch("cli.main.validate_profile", return_value=[]) as validate_profile,
            patch("cli.main.build_plan", return_value=(plan, [])) as build_plan,
            patch("cli.main._maybe_notify_update"),
            contextlib.redirect_stdout(output),
        ):
            result = main()

        self.assertEqual(result, 0)
        validate_profile.assert_called_once_with(
            "/profiles/minimal/profile.yaml", environment="prod"
        )
        build_plan.assert_called_once_with(
            "/profiles/minimal/profile.yaml",
            env_file="/profiles/minimal/.env",
            environment="prod",
            allow_missing_secrets=True,
        )
        self.assertEqual(json.loads(output.getvalue())["highestTier"], "Basic")


if __name__ == "__main__":
    unittest.main()
