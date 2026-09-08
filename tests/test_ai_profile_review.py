"""Tests for scripts/ai_profile_review.py.

Kept independent of any real LLM provider: `complete()` is monkeypatched in
every test that exercises the non-dry-run path, mirroring how
llm/README.md's "single seam" is meant to be stubbed.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "ai_profile_review.py"
EXAMPLE_PROFILE = REPO_ROOT / "profiles" / "local-dagster-postgres-superset" / "profile.yaml"

for _path in (str(REPO_ROOT), str(REPO_ROOT / "scripts" / "_vendor")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

_spec = importlib.util.spec_from_file_location("ai_profile_review", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
ai_profile_review = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ai_profile_review
_spec.loader.exec_module(ai_profile_review)


class ParseStatusTest(unittest.TestCase):
    def test_parses_clean_status(self) -> None:
        report = "## Guardrail violations\nNone found.\n\nSTATUS: clean\n"
        self.assertEqual(ai_profile_review.parse_status(report), "clean")

    def test_parses_findings_status_case_insensitively(self) -> None:
        report = "some report\nstatus: FINDINGS\n"
        self.assertEqual(ai_profile_review.parse_status(report), "findings")

    def test_returns_none_when_status_line_missing(self) -> None:
        self.assertIsNone(ai_profile_review.parse_status("no status line here"))


class ReviewProfileTest(unittest.TestCase):
    def test_dry_run_skips_model_call_and_reports_dry_run_status(self) -> None:
        with mock.patch.object(ai_profile_review, "complete") as mock_complete:
            result = ai_profile_review.review_profile(
                str(EXAMPLE_PROFILE), environment=None, dry_run=True
            )
        mock_complete.assert_not_called()
        self.assertEqual(result["status"], "dry-run")
        self.assertIn("Profile YAML", result["report"])
        self.assertIsNone(result["error"])

    def test_clean_model_reply_yields_clean_status(self) -> None:
        fake_report = (
            "## Guardrail violations\nNone found.\n\n"
            "## Simplification opportunities\nNone found.\n\nSTATUS: clean\n"
        )
        with (
            mock.patch.object(ai_profile_review, "preflight_provider"),
            mock.patch.object(ai_profile_review, "complete", return_value=fake_report) as mock_complete,
        ):
            result = ai_profile_review.review_profile(
                str(EXAMPLE_PROFILE), environment=None, dry_run=False
            )
        mock_complete.assert_called_once()
        self.assertEqual(mock_complete.call_args.kwargs["system"], ai_profile_review.SYSTEM_PROMPT)
        self.assertEqual(result["status"], "clean")
        self.assertEqual(result["report"], fake_report)

    def test_model_reply_without_status_line_is_unparsed(self) -> None:
        with (
            mock.patch.object(ai_profile_review, "preflight_provider"),
            mock.patch.object(ai_profile_review, "complete", return_value="free-form reply"),
        ):
            result = ai_profile_review.review_profile(
                str(EXAMPLE_PROFILE), environment=None, dry_run=False
            )
        self.assertEqual(result["status"], "unparsed")

    def test_invalid_profile_short_circuits_before_any_model_call(self) -> None:
        with mock.patch.object(ai_profile_review, "complete") as mock_complete:
            result = ai_profile_review.review_profile(
                "definitely-not-a-real-profile", environment=None, dry_run=False
            )
        mock_complete.assert_not_called()
        self.assertEqual(result["status"], "invalid")
        self.assertIsNotNone(result["error"])


class MainExitCodeTest(unittest.TestCase):
    def _run_main(self, argv: list[str]) -> int:
        with mock.patch.object(sys, "argv", ["ai_profile_review.py", *argv]):
            return ai_profile_review.main()

    def test_dry_run_exits_zero(self) -> None:
        with mock.patch("builtins.print"):
            code = self._run_main([str(EXAMPLE_PROFILE), "--dry-run", "--json"])
        self.assertEqual(code, 0)

    def test_findings_status_exits_nonzero(self) -> None:
        fake_report = "STATUS: findings\n"
        with (
            mock.patch.object(ai_profile_review, "preflight_provider"),
            mock.patch.object(ai_profile_review, "complete", return_value=fake_report),
            mock.patch("builtins.print"),
        ):
            code = self._run_main([str(EXAMPLE_PROFILE)])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
