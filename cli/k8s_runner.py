"""Bounded Helm lifecycle operations for the Kubernetes render target."""
from __future__ import annotations

import json
import os
import subprocess  # nosec B404
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

import yaml

from .state import group_services_by_health, parse_k8s_workloads_json
from .up_runner import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    _poll_until_settled,
    _validate_command,
    run_streamed,
)


def helm_up(
    plan: dict[str, Any],
    chart_dir: Path,
    *,
    namespace: str,
    release: str,
    kube_context: str | None,
    timeout: float,
    detach: bool,
    log_file: IO[str],
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    use_color: bool = False,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], float] = time.monotonic,
    redraw_fn: Callable[[str], None] | None = None,
) -> int:
    """
    Install or upgrade a rendered chart without persisting secret values.

    `timeout` is one overall budget shared across the whole call, the way
    Compose's `--timeout` already is: it bounds `helm upgrade --install`
    itself, then whatever remains bounds the readiness poll below, rather
    than being reused in full for each step (which could let total
    wall-clock time exceed `timeout` several times over on a
    multi-workload release).

    Readiness is decided by polling `get_k8s_state()` /
    `group_services_by_health()` — the exact same functions `cds state
    --target helm` uses — instead of a separate `helm --wait` +
    `kubectl rollout status`/`wait` implementation, so "ready" here and
    "healthy" in `cds state` can never disagree.
    """
    deadline = now_fn() + timeout
    secret_values = _secret_values(plan)
    secret_path = _write_secret_values(secret_values)
    context_args = ["--kube-context", kube_context] if kube_context else []
    apply_timeout_arg = f"{max(1, int(timeout))}s"
    command = [
        "helm",
        *context_args,
        "upgrade",
        "--install",
        release,
        str(chart_dir),
        "--namespace",
        namespace,
        "--create-namespace",
        "--values",
        str(secret_path),
        "--timeout",
        apply_timeout_arg,
    ]

    try:
        result = run_streamed(command, log_file, timeout=timeout + 30)
    finally:
        secret_path.unlink(missing_ok=True)
    if result != 0 or detach:
        return result

    workloads = get_k8s_workloads(namespace, release, kube_context)
    remaining = max(0.0, deadline - now_fn())
    settled, grouped = poll_k8s_state_until_settled(
        namespace,
        release,
        kube_context,
        expected_service_count=len(workloads),
        poll_interval=poll_interval,
        timeout=remaining,
        use_color=use_color,
        sleep_fn=sleep_fn,
        now_fn=now_fn,
        redraw_fn=redraw_fn,
    )
    if not settled:
        unhealthy = [name for bucket in ("UNHEALTHY", "UNHEALTHY EXIT") for name in grouped.get(bucket, [])]
        log_file.write(
            f"helm release {release} did not settle within {timeout:.0f}s"
            + (f"; unhealthy: {', '.join(unhealthy)}" if unhealthy else "")
            + "\n"
        )
        log_file.flush()
        return 1
    return 0


def poll_k8s_state_until_settled(
    namespace: str,
    release: str,
    kube_context: str | None,
    *,
    expected_service_count: int | None = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    timeout: float = 180.0,
    use_color: bool = False,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], float] = time.monotonic,
    fetch_fn: Callable[[], list[dict[str, Any]]] | None = None,
    redraw_fn: Callable[[str], None] | None = None,
) -> tuple[bool, dict[str, list[str]]]:
    """
    Polls `get_k8s_state()` every `poll_interval` seconds, grouping with
    `group_services_by_health()` — the same pair `cds state --target
    helm` calls — until every workload settles into a terminal bucket or
    `timeout` seconds elapse. Shares its settle/failure rule with
    Compose's `poll_state_until_settled()` via `_poll_until_settled()`.

    `fetch_fn` is injectable for tests; defaults to a real
    `get_k8s_state(namespace, release, kube_context)` call.
    """
    if fetch_fn is None:
        def fetch_fn() -> list[dict[str, Any]]:
            return get_k8s_state(namespace, release, kube_context)

    def fetch_grouped() -> dict[str, list[str]]:
        return group_services_by_health(fetch_fn())

    return _poll_until_settled(
        fetch_grouped,
        expected_service_count=expected_service_count,
        poll_interval=poll_interval,
        timeout=timeout,
        use_color=use_color,
        sleep_fn=sleep_fn,
        now_fn=now_fn,
        redraw_fn=redraw_fn,
        up_done_fn=None,
        on_up_finished=None,
    )


def helm_down(
    *,
    namespace: str,
    release: str,
    kube_context: str | None,
    timeout: float,
    delete_pvcs: bool,
    log_file: IO[str],
) -> int:
    pvc_names: list[str] = []
    if delete_pvcs:
        pvc_names = _stateful_pvc_names(
            get_k8s_workloads(namespace, release, kube_context)
        )
    context_args = ["--kube-context", kube_context] if kube_context else []
    command = [
        "helm",
        *context_args,
        "uninstall",
        release,
        "--namespace",
        namespace,
        "--ignore-not-found",
        "--timeout",
        f"{max(1, int(timeout))}s",
    ]
    result = run_streamed(command, log_file, timeout=timeout + 30)
    if result != 0 or not delete_pvcs or not pvc_names:
        return result
    return run_streamed(
        _kubectl_command(kube_context, namespace)
        + ["delete", "pvc", *pvc_names, "--ignore-not-found"],
        log_file,
        timeout=timeout + 30,
    )


def get_k8s_workloads(
    namespace: str, release: str, kube_context: str | None
) -> list[dict[str, Any]]:
    command = _kubectl_command(kube_context, namespace) + [
        "get",
        "deployment,statefulset,job",
        "-l",
        f"app.kubernetes.io/instance={release}",
        "-o",
        "json",
    ]
    result = subprocess.run(  # nosec B603  # noqa: S603
        _validate_command(command),
        capture_output=True,
        text=True,
        timeout=30,
        shell=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "kubectl get workloads failed")
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("kubectl returned invalid workload JSON") from exc
    return [item for item in document.get("items", []) if isinstance(item, dict)]


def get_k8s_state(
    namespace: str, release: str, kube_context: str | None
) -> list[dict[str, Any]]:
    workloads = get_k8s_workloads(namespace, release, kube_context)
    return parse_k8s_workloads_json(json.dumps({"items": workloads}))


def _secret_values(plan: dict[str, Any]) -> dict[str, str]:
    env_names = sorted(
        {
            value
            for value in (plan.get("secrets") or {}).values()
            if isinstance(value, str) and value.startswith("CDS_")
        }
    )
    missing = [name for name in env_names if not os.environ.get(name)]
    if missing:
        raise ValueError("missing required environment variables: " + ", ".join(missing))
    return {name: os.environ[name] for name in env_names}


def _write_secret_values(values: dict[str, str]) -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix="cds-helm-secrets-", suffix=".yaml")
    path = Path(raw_path)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            yaml.safe_dump({"secrets": values}, handle, sort_keys=True)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        path.unlink(missing_ok=True)
        raise
    return path


def _stateful_pvc_names(workloads: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for workload in workloads:
        if workload.get("kind") != "StatefulSet":
            continue
        metadata = workload.get("metadata") or {}
        spec = workload.get("spec") or {}
        statefulset_name = str(metadata.get("name") or "")
        replicas = int(spec.get("replicas", 1) or 0)
        claims = spec.get("volumeClaimTemplates") or []
        for claim in claims:
            claim_name = str((claim.get("metadata") or {}).get("name") or "")
            if claim_name and statefulset_name:
                names.extend(
                    f"{claim_name}-{statefulset_name}-{ordinal}"
                    for ordinal in range(replicas)
                )
    return sorted(names)


def _kubectl_command(kube_context: str | None, namespace: str) -> list[str]:
    command = ["kubectl"]
    if kube_context:
        command.extend(["--context", kube_context])
    command.extend(["--namespace", namespace])
    return command
