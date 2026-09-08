"""Standalone LLM completion seam, vendored from the sibling `llm` repo.

Vendored (not a package/path dependency) so `scripts/` stays self-contained
for anyone who clones just this repository. Re-sync by copying
`client.py`/`config.py`/`__init__.py` from the source repo's `llm/` package
when it changes; this copy is not meant to diverge locally.

    from llm import complete, preflight_provider

    text = complete("Summarize this table schema in one sentence.")
"""

from __future__ import annotations

from .client import complete, preflight_provider
from .config import load_env_file

__all__ = ["complete", "preflight_provider", "load_env_file"]
