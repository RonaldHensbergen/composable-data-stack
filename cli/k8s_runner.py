"""Bounded Helm lifecycle operations for the Kubernetes render target."""
from __future__ import annotations

import json
import os
import subprocess  # nosec B404
import tempfile
from pathlib import Path
from typing import IO, Any

import yaml

from .state import parse_k8s_workloads_json
from .up_runner import run_streamed


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
) -> int:
    """Install or upgrade a rendered chart without persisting secret values."""
    secret_values = _secret_values(plan)
    secret_path = _write_secret_values(secret_values)
    context_args = ["--kube-context", kube_context] if kube_context else []
    timeout_arg = f"{max(1, int(timeout))}s"
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
        timeout_arg,
    ]
    if not detach:
        command.append("--wait")

    try:
        result = run_streamed(command, log_file, timeout=timeout + 30)
    finally:
        secret_path.unlink(missing_ok=True)
    if result != 0 or detach:
        return result

    workloads = get_k8s_workloads(namespace, release, kube_context)
    for workload in workloads:
        kind = str(workload.get("kind", "")).lower()
        name = str((workload.get("metadata") or {}).get("name", ""))
        if not kind or not name:
            continue
        if kind == "job":
            wait_command = _kubectl_command(kube_context, namespace) + [
                "wait",
                "--for=condition=complete",
                f"job/{name}",
                f"--timeout={timeout_arg}",
            ]
        else:
            wait_command = _kubectl_command(kube_context, namespace) + [
                "rollout",
                "status",
                f"{kind}/{name}",
                f"--timeout={timeout_arg}",
            ]
        result = run_streamed(wait_command, log_file, timeout=timeout + 30)
        if result != 0:
            return result
    return 0


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
        command, capture_output=True, text=True, timeout=30
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
