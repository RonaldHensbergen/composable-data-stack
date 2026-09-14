import os
import tempfile
from contextlib import suppress
from pathlib import Path


def _atomic_write(path: Path, content: str, encoding: str = "utf-8", overwrite: bool = True) -> None:
    """Write `content` to `path` atomically via a temp file, then publish it.

    Avoids leaving a truncated/partial file behind if the process is
    interrupted mid-write.

    When `overwrite` is True (the default), publishing uses `os.replace()`,
    which always succeeds and silently replaces an existing `path`.

    When `overwrite` is False, publishing instead uses `os.link()`, which
    atomically fails with `FileExistsError` if `path` already exists. This
    closes the check-then-write race a caller-side `path.exists()` guard
    can't: two concurrent writers targeting the same `path` can't both
    "win" -- the loser gets `FileExistsError` instead of silently
    clobbering the winner's file.
    """
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as tmp_file:
            tmp_file.write(content)
        if overwrite:
            os.replace(tmp_name, path)
        else:
            os.link(tmp_name, path)
    finally:
        with suppress(OSError):
            os.unlink(tmp_name)
