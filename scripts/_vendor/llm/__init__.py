"""Standalone, provider-agnostic LLM completion seam.

Self-contained under `scripts/_vendor/llm` so anyone who clones just this
repository can use it without any external dependency or path/git reference.

    from llm import complete, preflight_provider

    text = complete("Summarize this table schema in one sentence.")
"""

from __future__ import annotations

from .client import complete, preflight_provider
from .config import load_env_file

__all__ = ["complete", "preflight_provider", "load_env_file"]
