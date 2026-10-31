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


def _automated_modules(identity_kind: str = "oidc-provider") -> list[dict]:
    return [
        _module("postgres", "sql-database"),
        _module("keydb", "cache-service"),
        _module("keycloak", identity_kind),
        _module("traefik", "reverse-proxy"),
        _module("restic", "backup-target"),
    ]


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
            [
                ("Basic", True),
                ("Automated", False),
                ("Observed", False),
                ("Secured", False),
                ("Resilient", False),
            ],
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
                "modules": _automated_modules()
                + [
                    _module("flux", "gitops-target"),
                    _module("istio", "service-mesh"),
                ],
            }
        )

        self.assertEqual(report["highestTier"], "Automated")
        self.assertEqual(
            [(tier["name"], tier["achieved"]) for tier in report["tiers"]],
            [
                ("Basic", True),
                ("Automated", True),
                ("Observed", False),
                ("Secured", False),
                ("Resilient", False),
            ],
        )
        gitops = next(
            capability
            for capability in report["capabilities"]
            if capability["id"] == "gitops"
        )
        self.assertTrue(gitops["bound"])
        self.assertEqual(gitops["providerInstances"], ["flux"])

    def test_observed_requires_automated_and_any_observability_contract(self) -> None:
        without_observability = build_maturity_report(
            {"metadata": {"name": "automated"}, "modules": _automated_modules()}
        )
        self.assertEqual(without_observability["highestTier"], "Automated")

        with_logging = build_maturity_report(
            {
                "metadata": {"name": "observed"},
                "modules": _automated_modules() + [_module("loki", "log-sink")],
            }
        )

        self.assertEqual(with_logging["highestTier"], "Observed")
        self.assertEqual(
            [(tier["name"], tier["achieved"]) for tier in with_logging["tiers"]],
            [
                ("Basic", True),
                ("Automated", True),
                ("Observed", True),
                ("Secured", False),
                ("Resilient", False),
            ],
        )

    def test_secured_requires_observed_and_a_security_contract(self) -> None:
        modules = _automated_modules(identity_kind="oidc-provider")
        modules.append(_module("loki", "log-sink"))
        without_security_contract = build_maturity_report(
            {"metadata": {"name": "observed"}, "modules": modules}
        )
        self.assertEqual(without_security_contract["highestTier"], "Observed")

        with_security_contract = build_maturity_report(
            {
                "metadata": {"name": "secured"},
                "modules": modules + [_module("certificates", "tls-cert")],
            }
        )

        self.assertEqual(with_security_contract["highestTier"], "Secured")
        self.assertEqual(
            [tier["achieved"] for tier in with_security_contract["tiers"]],
            [True, True, True, True, False],
        )

    def test_identity_broker_alone_does_not_make_profile_secured(self) -> None:
        broker_only = _automated_modules(identity_kind="identity-broker") + [
            _module("loki", "log-sink")
        ]
        report = build_maturity_report(
            {"metadata": {"name": "broker-only"}, "modules": broker_only}
        )
        self.assertEqual(report["highestTier"], "Observed")

        same_module = _automated_modules(identity_kind="oidc-provider")
        keycloak = next(m for m in same_module if m["id"] == "keycloak")
        keycloak["provides"]["broker"] = {"kind": "identity-broker"}
        report = build_maturity_report(
            {
                "metadata": {"name": "single-idp"},
                "modules": same_module + [_module("loki", "log-sink")],
            }
        )
        self.assertEqual(report["highestTier"], "Observed")

    def test_identity_broker_with_separate_oidc_provider_is_secured(self) -> None:
        modules = _automated_modules(identity_kind="oidc-provider") + [
            _module("loki", "log-sink"),
            _module("broker", "identity-broker"),
        ]
        report = build_maturity_report(
            {"metadata": {"name": "brokered"}, "modules": modules}
        )
        self.assertEqual(report["highestTier"], "Secured")

    def test_resilient_with_realistic_restic_topology(self) -> None:
        restic = _module("restic", "backup-target")
        restic["consumes"] = {
            "a": {
                "contract": {"kind": "backup-source"},
                "contractRef": "duckdb.backup-source",
            },
            "b": {
                "contract": {"kind": "backup-source"},
                "contractRef": "dagster.backup-source",
            },
            "c": {
                "contract": {"kind": "backup-source"},
                "contractRef": "postgres.backup-source",
            },
        }
        modules = [
            _module("postgres", "sql-database", "backup-source"),
            _module("duckdb", "file-database", "backup-source"),
            _module("dagster", "backup-source"),
            _module("keydb", "cache-service"),
            _module("keycloak", "oidc-provider"),
            _module("traefik", "reverse-proxy"),
            restic,
            _module("loki", "log-sink"),
            _module("certs", "tls-cert"),
        ]
        report = build_maturity_report(
            {"metadata": {"name": "realistic"}, "modules": modules}
        )

        self.assertEqual(report["highestTier"], "Resilient")

        restic["consumes"].pop("a")
        report = build_maturity_report(
            {"metadata": {"name": "realistic"}, "modules": modules}
        )
        self.assertEqual(report["highestTier"], "Secured")

    def test_resilient_requires_backup_for_each_stateful_instance(self) -> None:
        modules = _automated_modules() + [
            _module("loki", "log-sink"),
            _module("mesh", "service-mesh"),
        ]
        without_state_backup = build_maturity_report(
            {"metadata": {"name": "secured"}, "modules": modules}
        )
        self.assertEqual(without_state_backup["highestTier"], "Secured")

        postgres = _module("postgres", "sql-database", "backup-source")
        duckdb = _module("duckdb", "file-database", "backup-source")
        restic = _module("restic", "backup-target")
        restic["consumes"] = {
            "database": {
                "contract": {"kind": "backup-source"},
                "contractRef": "postgres.backup-source",
            }
        }
        protected_modules = [
            postgres,
            duckdb,
            _module("keydb", "cache-service"),
            _module("keycloak", "identity-broker"),
            _module("traefik", "reverse-proxy"),
            restic,
            _module("loki", "log-sink"),
            _module("mesh", "service-mesh"),
        ]
        resilient = build_maturity_report(
            {
                "metadata": {"name": "partially-backed-up"},
                "modules": protected_modules,
            }
        )

        self.assertEqual(resilient["highestTier"], "Secured")
        self.assertFalse(resilient["tiers"][-1]["achieved"])

        restic["consumes"]["files"] = {
            "contract": {"kind": "backup-source"},
            "contractRef": "duckdb.backup-source",
        }
        resilient = build_maturity_report(
            {"metadata": {"name": "resilient"}, "modules": protected_modules}
        )

        self.assertEqual(resilient["highestTier"], "Resilient")
        self.assertEqual(
            [tier["achieved"] for tier in resilient["tiers"]],
            [True, True, True, True, True],
        )

    def test_enabled_builtin_backup_protects_its_stateful_module(self) -> None:
        modules = _automated_modules()
        postgres = next(module for module in modules if module["id"] == "postgres")
        postgres["provides"]["backup"] = {"kind": "backup-target"}
        postgres["config"] = {"backup": {"enabled": True}}
        modules.extend(
            [_module("loki", "log-sink"), _module("mesh", "service-mesh")]
        )

        report = build_maturity_report(
            {"metadata": {"name": "resilient"}, "modules": modules}
        )

        self.assertEqual(report["highestTier"], "Resilient")

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
        self.assertIn("Observed: not met", text)
        self.assertIn("Secured: not met", text)
        self.assertIn("Resilient: not met", text)


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
            env_file=str(Path("/profiles/minimal/.env")),
            environment="prod",
            allow_missing_secrets=True,
        )
        report = json.loads(output.getvalue())
        self.assertEqual(report["highestTier"], "Basic")
        self.assertEqual(
            [(tier["name"], tier["achieved"]) for tier in report["tiers"]],
            [
                ("Basic", True),
                ("Automated", False),
                ("Observed", False),
                ("Secured", False),
                ("Resilient", False),
            ],
        )


if __name__ == "__main__":
    unittest.main()
