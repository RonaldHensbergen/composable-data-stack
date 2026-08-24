import os
import tempfile
from contextlib import suppress
from pathlib import Path


def _atomic_write(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write `content` to `path` atomically via a temp file + os.replace.
    
    Avoids leaving a truncated/partial file behind if the process is
    interrupted mid-write, and avoids races between concurrent writers.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as tmp_file:
            tmp_file.write(content)
        os.replace(tmp_name, path)
    except OSError:
        with suppress(OSError):
            os.unlink(tmp_name)
        raise
