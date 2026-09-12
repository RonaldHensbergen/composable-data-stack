"""Tests for the vendored LLM seam's provider dispatch (scripts/_vendor/llm).

Never calls a real LLM or requires `openai`/`azure-identity` to be
installed: the lazy client builders are monkeypatched.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
_VENDOR_PATH = str(REPO_ROOT / "scripts" / "_vendor")
if _VENDOR_PATH not in sys.path:
    sys.path.insert(0, _VENDOR_PATH)

from llm import client as llm_client  # noqa: E402
from llm import config as llm_config  # noqa: E402


class ProviderConfigTest(unittest.TestCase):
    def test_default_provider_is_copilot_cli(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(llm_config.LLM_PROVIDER(), "copilot_cli")

    def test_ollama_is_an_accepted_provider(self) -> None:
        with mock.patch.dict("os.environ", {"LLM_PROVIDER": "ollama"}, clear=True):
            self.assertEqual(llm_config.LLM_PROVIDER(), "ollama")

    def test_unsupported_provider_raises(self) -> None:
        with mock.patch.dict("os.environ", {"LLM_PROVIDER": "bogus"}, clear=True):
            with self.assertRaises(RuntimeError):
                llm_config.LLM_PROVIDER()

    def test_ollama_base_url_defaults_to_localhost(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(llm_config.OLLAMA_BASE_URL(), "http://localhost:11434/v1")

    def test_ollama_model_has_no_default_and_fails_fast(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(RuntimeError):
                llm_config.OLLAMA_MODEL()

    def test_ollama_api_key_defaults_to_placeholder(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(llm_config.OLLAMA_API_KEY(), "ollama")


class CompleteDispatchTest(unittest.TestCase):
    def test_complete_dispatches_to_ollama_backend(self) -> None:
        with (
            mock.patch.dict("os.environ", {"LLM_PROVIDER": "ollama"}, clear=True),
            mock.patch.object(llm_client, "_complete_ollama", return_value="ok") as mock_ollama,
        ):
            result = llm_client.complete("hello", system="sys")
        mock_ollama.assert_called_once()
        self.assertEqual(result, "ok")

    def test_complete_dispatches_to_copilot_cli_backend_by_default(self) -> None:
        with (
            mock.patch.dict("os.environ", {}, clear=True),
            mock.patch.object(llm_client, "_complete_copilot_cli", return_value="ok") as mock_copilot,
        ):
            result = llm_client.complete("hello")
        mock_copilot.assert_called_once()
        self.assertEqual(result, "ok")

    def test_preflight_provider_checks_ollama_reachability(self) -> None:
        with (
            mock.patch.dict("os.environ", {"LLM_PROVIDER": "ollama"}, clear=True),
            mock.patch.object(llm_client, "_ollama_preflight") as mock_preflight,
        ):
            llm_client.preflight_provider()
        mock_preflight.assert_called_once()

    def test_preflight_provider_is_a_noop_for_azure_openai(self) -> None:
        with (
            mock.patch.dict("os.environ", {"LLM_PROVIDER": "azure_openai"}, clear=True),
            mock.patch.object(llm_client, "_copilot_preflight") as mock_copilot_preflight,
            mock.patch.object(llm_client, "_ollama_preflight") as mock_ollama_preflight,
        ):
            llm_client.preflight_provider()
        mock_copilot_preflight.assert_not_called()
        mock_ollama_preflight.assert_not_called()


if __name__ == "__main__":
    unittest.main()
