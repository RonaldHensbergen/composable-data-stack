#!/usr/bin/env python3
"""Recompile the hash-locked image requirements in place.

Each `images/**/requirements*.txt` lock records the `uv pip compile` command
that produced it in its header. This reruns that command from the lock's
directory; existing pins are preserved unless the neighboring `.in` file no
longer allows them. CI follows this with `git diff --exit-code images` so a
`.in` edit without a regenerated lock fails the build.
"""

from __future__ import annotations

import shlex
import subprocess  # nosec B404
import sys
from pathlib import Path

HEADER_PREFIX = "#    uv pip compile "


def compile_command(lock: Path) -> list[str] | None:
    """Return the `uv pip compile` argv recorded in the lock header."""
    for line in lock.read_text(encoding="utf-8").splitlines()[:5]:
        if line.startswith(HEADER_PREFIX):
            return ["uv", *shlex.split(line[len("#    uv ") :])]
    return None


def main(images_root: Path = Path("images")) -> int:
    locks = sorted(images_root.rglob("requirements*.txt"))
    if not locks:
        print(f"No lock files found under {images_root}", file=sys.stderr)
        return 1
    for lock in locks:
        command = compile_command(lock)
        if command is None:
            print(f"{lock}: missing `uv pip compile` header", file=sys.stderr)
            return 1
        print(f"Recompiling {lock}")
        result = subprocess.run(command, cwd=lock.parent, check=False)  # nosec B603  # noqa: S603
        if result.returncode != 0:
            return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
