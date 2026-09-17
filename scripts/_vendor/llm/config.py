"""Typed, environment-driven settings for the LLM seam.

Every setting is read from an environment variable. Fail-fast with a clear
message naming the missing variable — never a silent default that hides a
misconfiguration.
"""

from __future__ import annotations

import os
from pathlib import Path

_LLM_PROVIDERS = ("copilot_cli", "azure_openai", "ollama")


def load_env_file(path: str | Path) -> Path | None:
    """Load a `KEY=VALUE` file into os.environ if present.

    Existing env vars win (os.environ.setdefault). `# comments` and blank
    lines are skipped; surrounding quotes on the value are stripped. Not
    called automatically — invoke it yourself (e.g. `load_env_file(".env")`)
    before using the package if you want `.env`-style config.
    """
    p = Path(path)
    if not p.exists():
        return None
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return p


def _resolve(env_var: str, *, default: str | None = None) -> str:
    value = os.environ.get(env_var)
    if value:
        return value
    if default is not None:
        return default
    raise RuntimeError(
        f"setting {env_var!r} is not set. Set it in the shell or in a "
        f"`.env` file loaded via load_env_file()."
    )


def LLM_PROVIDER() -> str:
    """Which LLM backend `complete()` uses.

    Explicit switch, no fallback — the selected provider is used as-is, and
    an unreachable provider fails loudly rather than silently switching:
      - `copilot_cli` (default) — GitHub Copilot via the local `copilot -p`
        CLI. No cloud account or API key required.
      - `azure_openai` — a customer's Azure OpenAI / Foundry deployment.
      - `ollama` — any local OpenAI-compatible server (Ollama, LM Studio,
        vLLM, ...) reached via the `openai` SDK with a custom `base_url`.
        No cloud account or API key required.
    """
    value = (os.environ.get("LLM_PROVIDER") or "copilot_cli").strip().lower()
    if value not in _LLM_PROVIDERS:
        raise RuntimeError(
            f"LLM_PROVIDER={value!r} is not supported; expected one of {_LLM_PROVIDERS}."
        )
    return value


# --- GitHub Copilot CLI -----------------------------------------------------


def COPILOT_CLI_PATH() -> str:
    """Name/path of the GitHub Copilot CLI executable (default `copilot`)."""
    return os.environ.get("COPILOT_CLI_PATH") or "copilot"


def COPILOT_MODEL() -> str | None:
    """Optional Copilot model override; None lets the CLI pick its default."""
    return os.environ.get("COPILOT_MODEL") or None


def COPILOT_TIMEOUT() -> int:
    """Seconds to wait for one `copilot -p` invocation before giving up."""
    raw = os.environ.get("COPILOT_TIMEOUT") or "120"
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"COPILOT_TIMEOUT must be an integer, got {raw!r}") from exc
    if value < 1:
        raise RuntimeError("COPILOT_TIMEOUT must be >= 1")
    return value


# --- Azure OpenAI -----------------------------------------------------------


def AZURE_OPENAI_ENDPOINT() -> str:
    return _resolve("AZURE_OPENAI_ENDPOINT")


def AZURE_OPENAI_DEPLOYMENT() -> str:
    return _resolve("AZURE_OPENAI_DEPLOYMENT")


def AZURE_OPENAI_API_VERSION() -> str:
    return os.environ.get("AZURE_OPENAI_API_VERSION") or "2024-10-21"


# --- Ollama / local OpenAI-compatible servers -------------------------------


def OLLAMA_BASE_URL() -> str:
    """Base URL of the local OpenAI-compatible server (default: Ollama's)."""
    return os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434/v1"


def OLLAMA_MODEL() -> str:
    """Model name as known to the local server (e.g. `llama3.1`, `qwen2.5-coder`).

    No default: local installs pull different models, so a silent default
    would likely name a model the user never pulled.
    """
    return _resolve("OLLAMA_MODEL")


def OLLAMA_API_KEY() -> str:
    """API key sent to the local server. Most local servers ignore its value
    but the `openai` SDK requires a non-empty string."""
    return os.environ.get("OLLAMA_API_KEY") or "ollama"
