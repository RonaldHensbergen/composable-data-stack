"""Single seam for LLM calls.

Business logic depends on `complete(...)`, not on any one backend. Tests stub
this with a one-liner monkeypatch.

`complete()` dispatches on `config.LLM_PROVIDER()` — an explicit switch with
no fallback between providers. The selected provider is used as-is; an
unreachable provider raises a clear error rather than silently switching to
the other.
"""

from __future__ import annotations

import shutil
import subprocess
from functools import cache
from typing import Any

from . import config

_AAD_SCOPE = "https://cognitiveservices.azure.com/.default"


def complete(
    prompt: str,
    system: str | None = None,
    *,
    temperature: float = 0.0,
    max_tokens: int | None = None,
) -> str:
    """One-shot chat completion against the configured LLM provider."""
    provider = config.LLM_PROVIDER()
    if provider == "copilot_cli":
        return _complete_copilot_cli(prompt, system)
    if provider == "ollama":
        return _complete_ollama(prompt, system, temperature=temperature, max_tokens=max_tokens)
    return _complete_azure_openai(prompt, system, temperature=temperature, max_tokens=max_tokens)


def preflight_provider() -> None:
    """Validate the configured LLM provider once before a long run.

    For `copilot_cli` and `ollama` — both locally-installed dependencies —
    this avoids repeating the same install/not-running failure on every call
    by failing fast once at startup. `azure_openai` has no equivalent local
    precondition to check ahead of time.
    """
    provider = config.LLM_PROVIDER()
    if provider == "copilot_cli":
        _copilot_preflight()
    elif provider == "ollama":
        _ollama_preflight()


# --- Azure OpenAI / Foundry ------------------------------------------------


@cache
def _client() -> Any:
    """Build the Azure OpenAI client, authenticating via DefaultAzureCredential.

    Requires the `openai` and `azure-identity` packages. Imported lazily so
    this module has no hard dependency on `azure-identity` unless
    `azure_openai` is actually selected.
    """
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    from openai import AzureOpenAI

    return AzureOpenAI(
        azure_endpoint=config.AZURE_OPENAI_ENDPOINT(),
        azure_ad_token_provider=get_bearer_token_provider(
            DefaultAzureCredential(), _AAD_SCOPE
        ),
        api_version=config.AZURE_OPENAI_API_VERSION(),
    )


def _chat_messages(prompt: str, system: str | None) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return messages


def _complete_azure_openai(
    prompt: str,
    system: str | None,
    *,
    temperature: float,
    max_tokens: int | None,
) -> str:
    """One-shot chat completion against the configured Azure OpenAI deployment."""
    kwargs: dict[str, Any] = {
        "model": config.AZURE_OPENAI_DEPLOYMENT(),
        "messages": _chat_messages(prompt, system),
        "temperature": temperature,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    response = _client().chat.completions.create(**kwargs)
    return (response.choices[0].message.content or "").strip()


# --- GitHub Copilot CLI ----------------------------------------------------


def _complete_copilot_cli(prompt: str, system: str | None) -> str:
    """One-shot completion via the local GitHub Copilot CLI (`copilot -p`).

    The CLI is an agent surface: it takes a single prompt string and returns
    free-form stdout, so the system prompt is folded into the prompt and
    there is no temperature/max-tokens knob. Callers should normalize the
    agent-style output (fences/prose) themselves if they need strict parsing.
    """
    _copilot_preflight()
    exe = _copilot_executable()
    message = f"{system}\n\n{prompt}" if system else prompt

    argv = [exe, "-p", message, "--no-ask-user"]
    model = config.COPILOT_MODEL()
    if model:
        argv += ["--model", model]

    try:
        result = subprocess.run(  # noqa: S603 — exe resolved via shutil.which; args are not shell-interpreted
            argv,
            capture_output=True,
            text=True,
            timeout=config.COPILOT_TIMEOUT(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"GitHub Copilot CLI timed out after {config.COPILOT_TIMEOUT()}s. "
            f"Raise COPILOT_TIMEOUT or simplify the request."
        ) from exc

    if result.returncode != 0:
        raise RuntimeError(
            f"GitHub Copilot CLI exited with code {result.returncode}: "
            f"{(result.stderr or result.stdout or '').strip()}"
        )
    return (result.stdout or "").strip()


@cache
def _copilot_executable() -> str:
    """Resolve the Copilot CLI path, failing fast if it isn't installed.

    Preflight only checks presence; an unauthenticated CLI surfaces as a
    non-zero exit from the actual call, carrying the CLI's own message.
    """
    exe = config.COPILOT_CLI_PATH()
    resolved = shutil.which(exe)
    if not resolved:
        raise RuntimeError(
            f"GitHub Copilot CLI {exe!r} not found on PATH. Install it and run "
            f"`copilot` to sign in, or set LLM_PROVIDER=azure_openai instead."
        )
    return resolved


@cache
def _copilot_preflight() -> None:
    """Ensure the Copilot CLI can serve non-interactive prompts."""
    exe = _copilot_executable()
    try:
        result = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [exe, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "GitHub Copilot CLI preflight timed out. Ensure the CLI is installed "
            "and responsive, then run `copilot` once to complete sign-in."
        ) from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            "GitHub Copilot CLI is not ready. Run `copilot` once to install/sign in "
            f"and retry. Details: {detail}"
        )
    _complete_copilot_probe()


def _complete_copilot_probe() -> None:
    """Issue one tiny non-interactive prompt to ensure the CLI is usable."""
    exe = _copilot_executable()
    argv = [exe, "-p", "Reply with exactly OK", "--no-ask-user"]
    model = config.COPILOT_MODEL()
    if model:
        argv += ["--model", model]
    try:
        result = subprocess.run(  # noqa: S603 — exe resolved via shutil.which; args are fixed
            argv,
            capture_output=True,
            text=True,
            timeout=min(config.COPILOT_TIMEOUT(), 30),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "GitHub Copilot CLI readiness probe timed out; run `copilot` manually "
            "to finish install/sign-in, then retry"
        ) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            f"Copilot probe failed with code {result.returncode}: {detail}"
        )


# --- Ollama / local OpenAI-compatible servers -------------------------------


@cache
def _ollama_client() -> Any:
    """Build a client for a local OpenAI-compatible server (Ollama by default).

    Requires the `openai` package. Imported lazily so this module has no
    hard dependency on it unless `ollama` is actually selected.
    """
    from openai import OpenAI

    return OpenAI(base_url=config.OLLAMA_BASE_URL(), api_key=config.OLLAMA_API_KEY())


def _complete_ollama(
    prompt: str,
    system: str | None,
    *,
    temperature: float,
    max_tokens: int | None,
) -> str:
    """One-shot chat completion against a local OpenAI-compatible server."""
    kwargs: dict[str, Any] = {
        "model": config.OLLAMA_MODEL(),
        "messages": _chat_messages(prompt, system),
        "temperature": temperature,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    response = _ollama_client().chat.completions.create(**kwargs)
    return (response.choices[0].message.content or "").strip()


def _ollama_preflight() -> None:
    """Ensure the local OpenAI-compatible server is reachable before a long run."""
    try:
        _ollama_client().models.list()
    except Exception as exc:
        raise RuntimeError(
            f"Local LLM server at {config.OLLAMA_BASE_URL()} is not reachable: {exc}. "
            f"Start it (e.g. `ollama serve`) and ensure OLLAMA_MODEL is pulled."
        ) from exc
