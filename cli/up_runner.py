# cli/up_runner.py
from __future__ import annotations

import re
import subprocess  # nosec B404
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Callable

from .state import format_state_output, group_services_by_health, parse_compose_ps_json

_BUILD_SERVICE_RE = re.compile(r"^([\w][\w.-]*?):\s+Building\b")

DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_TIMEOUT_SECONDS = 180.0

_SETTLED_BUCKETS = {"HEALTHY", "RUNNING", "HEALTHY EXIT", "UNHEALTHY EXIT", "UNHEALTHY"}
_FAILURE_BUCKETS = {"UNHEALTHY", "UNHEALTHY EXIT"}


def default_log_path(profile_name: str, logs_dir: Path | None = None) -> Path:
    """
    Default log path for a `cds up` run: `.cds/logs/up-<profile>-<UTC
    timestamp>.log`, relative to the current working directory. Slashes
    and spaces in `profile_name` (profiles can be passed as paths) are
    flattened so the result is always a single valid filename.

    `logs_dir` is injectable so tests don't have to write into a real
    `.cds/logs` under the repo checkout.
    """
    base = logs_dir if logs_dir is not None else Path(".cds") / "logs"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_profile = profile_name.strip().replace("/", "-").replace("\\", "-").replace(" ", "-") or "profile"
    return base / f"up-{safe_profile}-{timestamp}.log"


def run_streamed(cmd: list[str], log_file: IO[str], echo: bool = True, group_by_image: bool = False) -> int:
    """
    Runs `cmd` with stdout+stderr merged, writing each line to
    `log_file` as it arrives (flushed immediately, so `tail -f` on the
    log file works while the command is still running) and, if `echo`,
    to this process's stdout too.

    When `group_by_image` is True and `echo` is True, the output is
    annotated with section headers that identify which Docker Compose
    service each build phase belongs to.  The log file always receives
    the raw, un-annotated output.

    Returns the command's exit code. Raises FileNotFoundError if
    `cmd[0]` isn't on PATH, same as subprocess.run.
    """
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)  # nosec B603
    if process.stdout is None:
        raise RuntimeError("subprocess.Popen returned no stdout despite stdout=PIPE")
    try:
        current_service: str | None = None
        for line in process.stdout:
            log_file.write(line)
            log_file.flush()
            if echo:
                if group_by_image:
                    m = _BUILD_SERVICE_RE.match(line)
                    if m:
                        service = m.group(1)
                        if service != current_service:
                            current_service = service
                            header = f"── Building {service} "
                            header += "─" * max(1, 60 - len(header))
                            sys.stdout.write(f"\n\033[36m{header}\033[0m\n")
                sys.stdout.write(line)
                sys.stdout.flush()
    finally:
        process.stdout.close()
    return process.wait()


def start_log_tail(compose_path: str, log_file: IO[str]) -> subprocess.Popen:
    """
    Starts `docker compose logs -f` in the background, piped only to
    `log_file` (not the terminal; the terminal is showing the live
    state view while this runs). Caller is responsible for stopping it
    with `stop_log_tail` once the stack settles or `cds up` exits.
    """
    logs_cmd = ["docker", "compose", "-f", compose_path, "logs", "-f", "--no-color"]
    return subprocess.Popen(logs_cmd, stdout=log_file, stderr=subprocess.STDOUT, text=True)  # nosec B603


def stop_log_tail(process: subprocess.Popen, timeout: float = 5.0) -> None:
    """Terminates a background log-tail process started by start_log_tail."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _is_settled(grouped: dict[str, list[str]], expected_service_count: int | None) -> bool:
    if not grouped:
        return expected_service_count == 0

    pending = sum(len(names) for bucket, names in grouped.items() if bucket not in _SETTLED_BUCKETS)
    if pending:
        return False
    if expected_service_count is not None:
        seen = sum(len(names) for names in grouped.values())
        if seen < expected_service_count:
            return False
    return True


def _default_redraw(text: str) -> None:
    if sys.stdout.isatty():
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.write(text + "\n")
    else:
        print(text)
    sys.stdout.flush()


def poll_state_until_settled(
    compose_path: str,
    *,
    expected_service_count: int | None = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    use_color: bool = False,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], float] = time.monotonic,
    ps_fn: "Callable[[], subprocess.CompletedProcess] | None" = None,
    redraw_fn: "Callable[[str], None] | None" = None,
) -> tuple[bool, dict[str, list[str]]]:
    """
    Polls `docker compose ps -a --format json` every `poll_interval`
    seconds, redrawing the grouped `cds state` view each time, until
    every service `docker compose ps` reports is in a terminal bucket
    (HEALTHY, RUNNING, HEALTHY EXIT, UNHEALTHY EXIT, or UNHEALTHY) or
    `timeout` seconds elapse.

    Returns `(settled, grouped)`. `settled` is False if the loop timed
    out, or if any service ended in UNHEALTHY / UNHEALTHY EXIT.

    `ps_fn`, `sleep_fn`, `now_fn`, and `redraw_fn` are injectable so this
    can be unit tested with a fake clock and canned `ps` output instead
    of real Docker calls and real sleeping.
    """
    if ps_fn is None:
        def ps_fn() -> subprocess.CompletedProcess:
            ps_cmd = ["docker", "compose", "-f", compose_path, "ps", "-a", "--format", "json"]
            return subprocess.run(ps_cmd, capture_output=True, text=True)  # nosec B603

    if redraw_fn is None:
        redraw_fn = _default_redraw

    start = now_fn()
    grouped: dict[str, list[str]] = {}
    while True:
        ps_result = ps_fn()
        services = parse_compose_ps_json(ps_result.stdout) if ps_result.returncode == 0 else []
        grouped = group_services_by_health(services)
        redraw_fn(format_state_output(grouped, use_color=use_color))

        if _is_settled(grouped, expected_service_count):
            has_failure = any(bucket in _FAILURE_BUCKETS and names for bucket, names in grouped.items())
            return (not has_failure), grouped

        if now_fn() - start >= timeout:
            return False, grouped

        sleep_fn(poll_interval)
