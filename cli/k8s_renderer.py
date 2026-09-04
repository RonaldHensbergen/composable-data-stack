# pyright: reportMissingModuleSource=false
# cli/k8s_renderer.py
"""
Render a Helm chart from a composition plan.

This renderer is a sibling of `render_compose`: both consume the same resolved
plan and neither reads the other's output. The division of labour is recorded in
docs/adr/0001-kubernetes-render-target.md -- a module's compose fragment
describes the container and is translated mechanically, while its
`implementation.kubernetes` block declares the orchestration shape that compose
cannot express.

Secret values never reach disk here. `${CDS_*}` references are rewritten to
Kubernetes `$(VAR)` expansions backed by `secretKeyRef` entries, so the chart is
safe to commit and the values are supplied at install time.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from .diagnostics import Diagnostic
from .renderer import (
    _build_context,
    _compose_service_name,
    _merge_init_db_env,
    _resolve_expr,
    _resolve_profile_dir,
    _resolve_project_root,
    _substitute_values,
)
from .utils import _atomic_write

CHART_API_VERSION = "v2"

# Compose exposes ${CDS_FOO}; Kubernetes expands $(CDS_FOO) from earlier env entries.
_CDS_VAR_PATTERN = re.compile(r"\$\{(CDS_[A-Z0-9_]+)\}")

# `${k8s.service.<compose service>}` resolves to the Service DNS name of that
# workload. Modules need it because a Service name is only known once the module
# id is bound, which happens in the profile rather than in the module.
_K8S_SERVICE_PATTERN = re.compile(r"\$\{k8s\.service\.([A-Za-z0-9_.-]+)\}")

_DURATION_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)(ns|us|ms|s|m|h)?$")

_DURATION_MULTIPLIERS = {
    "ns": 1e-9,
    "us": 1e-6,
    "ms": 1e-3,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
}

# The wait-for gates need a tiny image with a shell. busybox is already a
# transitive dependency of most clusters and stays out of the module's concern.
WAIT_IMAGE = "busybox:1.37.0"


class K8sRenderError(Exception):
    """Raised when the plan cannot be expressed as a chart at all."""


def render_helm(
    plan: dict[str, Any],
    output_dir: str | None = None,
) -> tuple[dict[str, str], list[Diagnostic]]:
    """
    Render a Helm chart from a resolved plan.

    Returns a mapping of chart-relative path -> file contents, plus diagnostics.
    Files are written to `output_dir` when given.
    """
    diagnostics: list[Diagnostic] = []
    metadata = plan.get("metadata", {})
    release = _sanitize_name(metadata.get("name", "cds"))
    secrets = plan.get("secrets", {})
    profile_dir = _resolve_profile_dir(plan)
    project_root = _resolve_project_root(profile_dir)

    modules = plan.get("modules", [])
    renderable, unsupported = _partition_modules(modules)
    for module_id, kind in unsupported:
        diagnostics.append(
            Diagnostic(
                level="error",
                code="E072",
                message=(
                    f'Module "{module_id}" does not support the "kubernetes" render '
                    f'target (implementation kind "{kind}", no "kubernetes" block). '
                    "Add spec.implementation.kubernetes to the module, or render this "
                    "profile with --target=compose."
                ),
                path=f"module:{module_id}.implementation.kubernetes",
            )
        )
    if unsupported:
        return {}, diagnostics

    # Service names must be known before any workload renders, because
    # ${k8s.service.*} can point forward to a workload declared later.
    service_names = _collect_service_names(renderable)
    diagnostics.extend(_check_binding_services(renderable, service_names))
    if any(diagnostic.level == "error" for diagnostic in diagnostics):
        return {}, diagnostics

    values: dict[str, Any] = {
        "nameOverride": "",
        "fullnameOverride": "",
        "namespace": plan.get("runtime", {}).get("namespace", release),
        # Populated at install time. The renderer never writes secret values.
        "secrets": {},
        "modules": {},
    }

    files: dict[str, str] = {}
    manifests: list[tuple[str, dict[str, Any]]] = []
    secret_keys: set[str] = set()

    for module in renderable:
        module_id = module["id"]
        k8s = module["implementation"]["kubernetes"]
        context = _build_context(module, secrets)
        context["k8s"] = {"service": service_names}
        compose_services = deepcopy(
            module["implementation"].get("compose", {}).get("services", {})
        )
        _merge_init_db_env(compose_services, module, secrets)

        (
            module_values,
            module_manifests,
            module_files,
            module_secret_keys,
            module_diags,
        ) = _render_module(
            module=module,
            module_id=module_id,
            k8s=k8s,
            compose_services=compose_services,
            context=context,
            release=release,
            service_names=service_names,
            profile_dir=profile_dir,
            project_root=project_root,
        )
        values["modules"][module_id] = {
            "config": _values_config(module.get("config", {})),
            "workloads": module_values,
        }
        manifests.extend(module_manifests)
        files.update(module_files)
        secret_keys.update(module_secret_keys)
        diagnostics.extend(module_diags)

    if secret_keys:
        manifests.append(
            (
                "secret.yaml",
                _secret_manifest(release, sorted(secret_keys)),
            )
        )

    files["Chart.yaml"] = _dump(_chart_metadata(metadata, release))
    files["values.yaml"] = _dump(values)
    files[".helmignore"] = ".git/\n*.tmp\n"
    files["templates/NOTES.txt"] = _notes(release, renderable, service_names)

    for name, manifest in manifests:
        files[f"templates/{name}"] = _dump(manifest)

    diagnostics.extend(_check_unresolved(files))
    diagnostics.extend(_check_secret_references(files, secret_keys))

    if output_dir and not any(
        diagnostic.level == "error" for diagnostic in diagnostics
    ):
        _write_chart(Path(output_dir), files)

    return files, diagnostics


# ---------------------------------------------------------------------------
# Module rendering
# ---------------------------------------------------------------------------


def _render_module(
    module: dict[str, Any],
    module_id: str,
    k8s: dict[str, Any],
    compose_services: dict[str, Any],
    context: dict[str, Any],
    release: str,
    service_names: dict[str, str],
    profile_dir: Path | None,
    project_root: Path | None,
) -> tuple[
    dict[str, Any],
    list[tuple[str, dict[str, Any]]],
    dict[str, str],
    set[str],
    list[Diagnostic],
]:
    diagnostics: list[Diagnostic] = []
    manifests: list[tuple[str, dict[str, Any]]] = []
    files: dict[str, str] = {}
    secret_keys: set[str] = set()
    module_values: dict[str, Any] = {}

    volume_specs = _substitute_values(deepcopy(k8s.get("volumes", {})), context)
    for volume_spec in volume_specs.values():
        enabled_from = volume_spec.get("enabledFrom")
        if isinstance(enabled_from, str):
            volume_spec["enabledFrom"] = _resolve_expr(enabled_from, context)
    configmap_specs = _substitute_values(deepcopy(k8s.get("configMaps", {})), context)

    # ConfigMaps are module-scoped: one manifest, mounted by whichever
    # containers name it.
    configmaps, cm_diags = _render_configmaps(
        module_id, configmap_specs, release, profile_dir, project_root
    )
    diagnostics.extend(cm_diags)
    for cm_name, cm in configmaps.items():
        manifests.append((f"{module_id}-configmap-{cm_name}.yaml", cm["manifest"]))

    for workload_name, workload_raw in k8s.get("workloads", {}).items():
        workload = _substitute_values(deepcopy(workload_raw), context)

        enabled_from = workload.get("enabledFrom")
        if enabled_from and _resolve_expr(enabled_from, context) is False:
            continue

        full_name = _compose_service_name(module_id, workload_name)
        service_name = workload.get("serviceName") or full_name

        containers = []
        init_containers = []
        pod_volumes: dict[str, dict[str, Any]] = {}
        pvcs: dict[str, dict[str, Any]] = {}
        declared_resources = workload.get("resources") or {}

        for compose_name in [
            *(workload.get("initContainers", []) or []),
            *(workload.get("containers", []) or []),
        ]:
            if compose_name not in declared_resources:
                diagnostics.append(
                    Diagnostic(
                        level="warning",
                        code="W072",
                        message=(
                            f'Container "{compose_name}" in Kubernetes workload '
                            f'"{module_id}/{workload_name}" has no resource requests or limits.'
                        ),
                        path=(
                            f"module:{module_id}.implementation.kubernetes.workloads."
                            f"{workload_name}.resources.{compose_name}"
                        ),
                    )
                )

        for compose_name in workload.get("initContainers", []) or []:
            spec = compose_services.get(compose_name)
            if spec is None:
                diagnostics.append(
                    _missing_service(module_id, workload_name, compose_name)
                )
                continue
            container, vols, claims, keys = _translate_container(
                compose_name,
                spec,
                workload,
                workload_name,
                module_id,
                context,
                configmaps,
                volume_specs,
                pod_volumes,
                pvcs,
                is_init=True,
            )
            secret_keys |= keys
            init_containers.append(container)

        for compose_name in workload.get("containers", []):
            spec = compose_services.get(compose_name)
            if spec is None:
                diagnostics.append(
                    _missing_service(module_id, workload_name, compose_name)
                )
                continue
            container, vols, claims, keys = _translate_container(
                compose_name,
                spec,
                workload,
                workload_name,
                module_id,
                context,
                configmaps,
                volume_specs,
                pod_volumes,
                pvcs,
                is_init=False,
            )
            secret_keys |= keys
            containers.append(container)

        if not containers:
            continue

        # waitFor gates run before everything else: they replace compose
        # depends_on conditions, which Kubernetes has no equivalent for.
        wait_containers = _wait_containers(workload.get("waitFor", []) or [])
        init_containers = wait_containers + init_containers

        pod_spec: dict[str, Any] = {}
        if init_containers:
            pod_spec["initContainers"] = init_containers
        pod_spec["containers"] = containers
        psc = workload.get("podSecurityContext")
        if psc:
            pod_spec["securityContext"] = psc
        if pod_volumes:
            pod_spec["volumes"] = [
                dict(v, name=n) for n, v in sorted(pod_volumes.items())
            ]

        kind = workload.get("kind", "Deployment")
        manifest = _workload_manifest(
            kind=kind,
            name=full_name,
            release=release,
            module_id=module_id,
            replicas=workload.get("replicas", 1),
            pod_spec=pod_spec,
            pvcs=pvcs,
            service_name=service_name if kind == "StatefulSet" else None,
        )
        config_checksum = _configmap_checksum(pod_volumes, configmaps)
        if config_checksum:
            manifest["spec"]["template"]["metadata"]["annotations"] = {
                "cds.dev/config-checksum": config_checksum
            }
        manifests.append((f"{module_id}-{workload_name}-{kind.lower()}.yaml", manifest))

        # A StatefulSet needs its governing headless Service regardless of
        # whether the module exposes ports.
        svc = workload.get("service")
        if svc and svc.get("type") != "None":
            manifests.append(
                (
                    f"{module_id}-{workload_name}-service.yaml",
                    _service_manifest(service_name, release, module_id, full_name, svc),
                )
            )
        elif kind == "StatefulSet":
            manifests.append(
                (
                    f"{module_id}-{workload_name}-service.yaml",
                    _service_manifest(
                        service_name,
                        release,
                        module_id,
                        full_name,
                        {"type": "None", "ports": []},
                    ),
                )
            )

        module_values[workload_name] = {
            "replicas": workload.get("replicas", 1),
            "serviceName": service_name,
            "resources": workload.get("resources", {}),
        }

    return module_values, manifests, files, secret_keys, diagnostics


def _missing_service(module_id: str, workload: str, compose_name: str) -> Diagnostic:
    return Diagnostic(
        level="error",
        code="E073",
        message=(
            f'Workload "{workload}" of module "{module_id}" names compose service '
            f'"{compose_name}", which the module does not define.'
        ),
        path=f"module:{module_id}.implementation.kubernetes.workloads.{workload}",
    )


# ---------------------------------------------------------------------------
# Compose service -> Kubernetes container
# ---------------------------------------------------------------------------


def _translate_container(
    compose_name: str,
    spec: dict[str, Any],
    workload: dict[str, Any],
    workload_name: str,
    module_id: str,
    context: dict[str, Any],
    configmaps: dict[str, dict[str, Any]],
    volume_specs: dict[str, Any],
    pod_volumes: dict[str, dict[str, Any]],
    pvcs: dict[str, dict[str, Any]],
    is_init: bool,
) -> tuple[dict[str, Any], dict, dict, set[str]]:
    spec = _substitute_values(deepcopy(spec), context)
    spec.pop("enabledFrom", None)
    overrides = (workload.get("containerOverrides") or {}).get(compose_name, {})

    container: dict[str, Any] = {"name": _k8s_name(compose_name)}

    container["image"] = spec.get("image", "")
    # A service with a build context has no registry to pull from; Never fails
    # fast and legibly when the image was not loaded into the cluster, whereas
    # IfNotPresent produces an opaque ImagePullBackOff against docker.io.
    if "build" in spec:
        container["imagePullPolicy"] = workload.get("imagePullPolicy", "Never")
    elif workload.get("imagePullPolicy"):
        container["imagePullPolicy"] = workload["imagePullPolicy"]

    # Compose `entrypoint` names the executable and maps to Kubernetes
    # `command`; compose `command` supplies its arguments and maps to `args`.
    # Mapping compose `command` onto Kubernetes `command` instead would replace
    # the image's ENTRYPOINT, silently skipping whatever setup it performs --
    # for the Dagster image that is the script which materialises dagster.yaml
    # and workspace.yaml, so the container starts and then fails on missing files.
    entrypoint = overrides.get("entrypoint", spec.get("entrypoint"))
    arguments = overrides.get("command", spec.get("command"))
    if entrypoint is not None:
        container["command"] = [str(x) for x in _as_list(entrypoint)]
    if arguments is not None:
        container["args"] = [str(x) for x in _as_list(arguments)]
    if overrides.get("args") is not None:
        container["args"] = [str(x) for x in overrides["args"]]

    env, secret_keys = _translate_env(
        spec.get("environment", {}), overrides.get("env", {})
    )
    if env:
        container["env"] = env

    ports = _translate_ports(spec.get("ports", []), overrides.get("ports"))
    if ports:
        container["ports"] = ports

    sec_ctx = _translate_security_context(spec)
    if overrides.get("securityContext"):
        sec_ctx.update(overrides["securityContext"])
    if sec_ctx:
        container["securityContext"] = sec_ctx

    resources = (workload.get("resources") or {}).get(compose_name)
    if resources:
        container["resources"] = _helm_to_yaml(
            "modules", module_id, "workloads", workload_name, "resources", compose_name
        )

    mounts = _translate_volumes(
        spec,
        compose_name,
        module_id,
        configmaps,
        volume_specs,
        pod_volumes,
        pvcs,
        overrides.get("dropVolumeMounts", []) or [],
        context=context,
    )
    if mounts:
        container["volumeMounts"] = mounts

    if not is_init:
        dropped = set(overrides.get("dropProbes", []) or [])
        healthcheck = spec.get("healthcheck")
        if isinstance(healthcheck, dict):
            enabled_from = healthcheck.get("conditionallyEnabledFrom")
            if (
                isinstance(enabled_from, str)
                and _resolve_expr(enabled_from, context) is False
            ):
                healthcheck = None
        probes = _translate_healthcheck(healthcheck)
        for probe_kind in ("livenessProbe", "readinessProbe", "startupProbe"):
            if (
                probe_kind[:-5] in {"liveness", "readiness", "startup"}
                and probe_kind.replace("Probe", "") in dropped
            ):
                probes.pop(probe_kind, None)
            if overrides.get(probe_kind):
                probes[probe_kind] = overrides[probe_kind]
        container.update(probes)

    return container, pod_volumes, pvcs, secret_keys


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _translate_env(
    environment: Any, extra: dict[str, Any]
) -> tuple[list[dict[str, Any]], set[str]]:
    """
    Build the container env list.

    Every `${CDS_*}` reference becomes a `$(CDS_*)` expansion. Kubernetes expands
    those only against entries appearing EARLIER in the same env list, and never
    against envFrom, so each referenced key is emitted first as its own
    secretKeyRef entry.
    """
    pairs: list[tuple[str, Any]] = []
    if isinstance(environment, dict):
        pairs = list(environment.items())
    elif isinstance(environment, list):
        for item in environment:
            if isinstance(item, str) and "=" in item:
                k, v = item.split("=", 1)
                pairs.append((k, v))
    pairs.extend(extra.items())

    referenced: set[str] = set()
    body: list[dict[str, Any]] = []
    for key, value in pairs:
        if value is None:
            text = ""
        elif isinstance(value, bool):
            text = "true" if value else "false"
        else:
            text = str(value)
        referenced.update(_CDS_VAR_PATTERN.findall(text))
        body.append({"name": key, "value": _CDS_VAR_PATTERN.sub(r"$(\1)", text)})

    # A key that is itself a CDS_* secret must not be re-declared as a literal.
    body = [e for e in body if e["name"] not in referenced]

    header = [
        {
            "name": key,
            "valueFrom": {
                "secretKeyRef": {"name": "{{ .Release.Name }}-secrets", "key": key}
            },
        }
        for key in sorted(referenced)
    ]
    return header + body, referenced


def _translate_ports(compose_ports: Any, override: Any) -> list[dict[str, Any]]:
    if override is not None:
        return [
            {
                "name": p["name"],
                "containerPort": int(p["containerPort"]),
                "protocol": p.get("protocol", "TCP"),
            }
            for p in override
        ]
    ports: list[dict[str, Any]] = []
    seen: set[int] = set()
    for entry in compose_ports or []:
        target = _compose_port_target(entry)
        if target is None or target in seen:
            continue
        seen.add(target)
        ports.append({"containerPort": target, "protocol": "TCP"})
    return ports


def _compose_port_target(entry: Any) -> int | None:
    """
    Extract the container port from a compose port mapping.

    Host bindings such as `127.0.0.1:5432:5432` carry no meaning in a cluster;
    only the container side survives, and ADR 0001 records the loss.
    """
    if isinstance(entry, dict):
        try:
            return int(entry.get("target"))
        except TypeError, ValueError:
            return None
    text = str(entry).split("/")[0]
    parts = text.split(":")
    try:
        return int(parts[-1])
    except ValueError:
        return None


def _translate_security_context(spec: dict[str, Any]) -> dict[str, Any]:
    """Map the compose hardening flags onto a container securityContext."""
    ctx: dict[str, Any] = {}
    if spec.get("read_only"):
        ctx["readOnlyRootFilesystem"] = True
    cap_drop = spec.get("cap_drop")
    cap_add = spec.get("cap_add")
    caps: dict[str, Any] = {}
    if cap_drop:
        caps["drop"] = list(cap_drop)
    if cap_add:
        caps["add"] = list(cap_add)
    if caps:
        ctx["capabilities"] = caps
    for opt in spec.get("security_opt", []) or []:
        if str(opt).replace(" ", "") in {
            "no-new-privileges:true",
            "no-new-privileges=true",
        }:
            ctx["allowPrivilegeEscalation"] = False
    if spec.get("privileged"):
        ctx["privileged"] = True
    return ctx


def _translate_healthcheck(healthcheck: Any) -> dict[str, Any]:
    """
    Turn a compose healthcheck into Kubernetes probes.

    `start_period` becomes a startupProbe rather than being folded into
    initialDelaySeconds, because those mean different things: a startupProbe
    suspends the liveness probe until the container has come up, which is the
    behaviour start_period actually describes.
    """
    if not isinstance(healthcheck, dict) or healthcheck.get("disable"):
        return {}
    test = healthcheck.get("test")
    handler = _probe_handler(test)
    if handler is None:
        return {}

    interval = _duration_seconds(healthcheck.get("interval"), 10)
    timeout = _duration_seconds(healthcheck.get("timeout"), 1)
    retries = int(healthcheck.get("retries", 3))
    start_period = _duration_seconds(healthcheck.get("start_period"), 0)

    base = {
        "periodSeconds": max(1, interval),
        "timeoutSeconds": max(1, timeout),
        "failureThreshold": max(1, retries),
    }
    probes = {
        "readinessProbe": dict(handler, **base),
        "livenessProbe": dict(handler, **base),
    }
    if start_period:
        # Allow the container start_period seconds to come up, checked at the
        # same interval, before liveness is allowed to kill it.
        probes["startupProbe"] = dict(
            handler,
            periodSeconds=max(1, interval),
            timeoutSeconds=max(1, timeout),
            failureThreshold=max(1, -(-start_period // max(1, interval)) + retries),
        )
    return probes


def _probe_handler(test: Any) -> dict[str, Any] | None:
    if not test:
        return None
    if isinstance(test, str):
        return {"exec": {"command": ["/bin/sh", "-c", test]}}
    if not isinstance(test, list) or not test:
        return None
    head = str(test[0])
    if head == "NONE":
        return None
    if head == "CMD-SHELL":
        return {
            "exec": {"command": ["/bin/sh", "-c", " ".join(str(x) for x in test[1:])]}
        }
    if head == "CMD":
        return {"exec": {"command": [str(x) for x in test[1:]]}}
    return {"exec": {"command": ["/bin/sh", "-c", " ".join(str(x) for x in test)]}}


def _duration_seconds(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return int(value)
    match = _DURATION_PATTERN.match(str(value).strip())
    if not match:
        return default
    amount = float(match.group(1))
    unit = match.group(2) or "s"
    return max(0, int(amount * _DURATION_MULTIPLIERS[unit]))


# ---------------------------------------------------------------------------
# Volumes
# ---------------------------------------------------------------------------


def _translate_volumes(
    spec: dict[str, Any],
    compose_name: str,
    module_id: str,
    configmaps: dict[str, dict[str, Any]],
    volume_specs: dict[str, Any],
    pod_volumes: dict[str, dict[str, Any]],
    pvcs: dict[str, dict[str, Any]],
    drop_mounts: list[str],
    context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    mounts: list[dict[str, Any]] = []
    dropped = {str(d).rstrip("/") for d in drop_mounts}

    # tmpfs is what makes readOnlyRootFilesystem survivable; an in-memory
    # emptyDir is its exact Kubernetes counterpart.
    for entry in spec.get("tmpfs", []) or []:
        path = str(entry).split(":", 1)[0]
        if path.rstrip("/") in dropped:
            continue
        name = _k8s_name(f"tmp-{path}")
        pod_volumes.setdefault(name, {"emptyDir": {"medium": "Memory"}})
        mounts.append({"name": name, "mountPath": path})

    for entry in spec.get("volumes", []) or []:
        source, target, read_only, kind = _parse_volume_entry(entry)
        if target is None or target.rstrip("/") in dropped:
            continue

        cm = _configmap_for_target(configmaps, source, target, compose_name)
        if cm is not None:
            name = cm["volume_name"]
            pod_volumes.setdefault(
                name,
                {
                    "configMap": {
                        "name": cm["manifest"]["metadata"]["name"],
                        "defaultMode": cm["mode"],
                    }
                },
            )
            mounts.append(
                {
                    "name": name,
                    "mountPath": cm["mount_path"],
                    "subPath": cm["key"],
                    "readOnly": True,
                }
            )
            continue

        if kind == "bind":
            # A host path has no cluster meaning. Directory binds become an
            # emptyDir; file binds must be declared as ConfigMaps instead.
            name = _k8s_name(f"{compose_name}-{Path(target).name or 'data'}")
            pod_volumes.setdefault(name, {"emptyDir": {}})
            mounts.append({"name": name, "mountPath": target})
            continue

        vol_spec = volume_specs.get(source, {"type": "emptyDir"})
        if vol_spec.get("enabledFrom") is False:
            continue
        name = _k8s_name(f"{module_id}-{source}")
        if vol_spec.get("type") == "persistentVolumeClaim":
            size = vol_spec.get("size")
            if size is None and vol_spec.get("sizeFrom") and context is not None:
                size = _resolve_expr(vol_spec["sizeFrom"], context)
            claim = {
                "accessModes": vol_spec.get("accessModes", ["ReadWriteOnce"]),
                "resources": {"requests": {"storage": size or "1Gi"}},
            }
            if vol_spec.get("storageClassName"):
                claim["storageClassName"] = vol_spec["storageClassName"]
            pvcs.setdefault(name, claim)
        else:
            empty: dict[str, Any] = {}
            if vol_spec.get("medium"):
                empty["medium"] = vol_spec["medium"]
            pod_volumes.setdefault(name, {"emptyDir": empty})
        mount = {"name": name, "mountPath": target}
        if read_only:
            mount["readOnly"] = True
        mounts.append(mount)

    return mounts


def _parse_volume_entry(entry: Any) -> tuple[str | None, str | None, bool, str]:
    if isinstance(entry, dict):
        return (
            entry.get("source"),
            entry.get("target"),
            bool(entry.get("read_only")),
            entry.get("type", "volume"),
        )
    parts = str(entry).split(":")
    if len(parts) < 2:
        return None, None, False, "volume"
    source, target = parts[0], parts[1]
    read_only = len(parts) > 2 and "ro" in parts[2].split(",")
    kind = "bind" if source.startswith((".", "/")) else "volume"
    return source, target, read_only, kind


def _configmap_for_target(
    configmaps: dict[str, dict[str, Any]],
    source: Any,
    target: str,
    compose_name: str,
) -> dict[str, Any] | None:
    for cm in configmaps.values():
        # An omitted `containers` list means the file applies module-wide.
        if cm["containers"] and compose_name not in cm["containers"]:
            continue
        if cm["mount_path"] == target or (
            cm["from_bind"] and str(source) == cm["from_bind"]
        ):
            # A module that names no mountPath inherits the compose bind target,
            # which is where the container already expects the file.
            return dict(cm, mount_path=cm["mount_path"] or target)
    return None


def _configmap_checksum(
    pod_volumes: dict[str, dict[str, Any]],
    configmaps: dict[str, dict[str, Any]],
) -> str | None:
    mounted_names = {
        volume["configMap"]["name"]
        for volume in pod_volumes.values()
        if "configMap" in volume
    }
    mounted_data = {
        configmap["manifest"]["metadata"]["name"]: configmap["manifest"]["data"]
        for configmap in configmaps.values()
        if configmap["manifest"]["metadata"]["name"] in mounted_names
    }
    if not mounted_data:
        return None

    serialized = yaml.safe_dump(mounted_data, sort_keys=True).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _render_configmaps(
    module_id: str,
    specs: dict[str, Any],
    release: str,
    profile_dir: Path | None,
    project_root: Path | None,
) -> tuple[dict[str, dict[str, Any]], list[Diagnostic]]:
    out: dict[str, dict[str, Any]] = {}
    diagnostics: list[Diagnostic] = []

    for name, spec in specs.items():
        key = spec["key"]
        content = spec.get("content")
        from_bind = spec.get("fromBind")
        mount_path = spec.get("mountPath")

        if content is None and from_bind:
            resolved, diag = _read_bind_file(
                from_bind, module_id, name, profile_dir, project_root
            )
            if diag is not None:
                diagnostics.append(diag)
                continue
            content = resolved
        if content is None:
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E074",
                    message=(
                        f'ConfigMap "{name}" of module "{module_id}" supplies neither '
                        '"content" nor a readable "fromBind" file.'
                    ),
                    path=f"module:{module_id}.implementation.kubernetes.configMaps.{name}",
                )
            )
            continue

        # A ConfigMap tops out around 1MiB; truncating would ship a chart that
        # installs and then misbehaves, so refuse instead.
        if len(content.encode("utf-8")) > 1_000_000:
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E075",
                    message=(
                        f'ConfigMap "{name}" of module "{module_id}" exceeds the 1MiB '
                        "ConfigMap limit. Bake this file into the image instead."
                    ),
                    path=f"module:{module_id}.implementation.kubernetes.configMaps.{name}",
                )
            )
            continue

        if mount_path is None and not from_bind:
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E080",
                    message=(
                        f'ConfigMap "{name}" of module "{module_id}" needs a "mountPath": '
                        "there is no compose bind mount to inherit the path from."
                    ),
                    path=f"module:{module_id}.implementation.kubernetes.configMaps.{name}.mountPath",
                )
            )
            continue

        cm_name = _k8s_name(f"{module_id}-{name}")
        out[name] = {
            "volume_name": _k8s_name(f"cm-{module_id}-{name}"),
            "key": key,
            "mount_path": mount_path,
            "from_bind": from_bind,
            "containers": spec.get("containers") or [],
            "mode": _octal(spec.get("mode", "0444")),
            "manifest": {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": "{{ .Release.Name }}-" + cm_name,
                    "labels": _labels(release, module_id),
                },
                "data": {key: content},
            },
        }
    return out, diagnostics


def _read_bind_file(
    from_bind: str,
    module_id: str,
    name: str,
    profile_dir: Path | None,
    project_root: Path | None,
) -> tuple[str | None, Diagnostic | None]:
    candidates = []
    raw = Path(from_bind)
    if raw.is_absolute():
        candidates.append(raw)
    else:
        if profile_dir:
            candidates.append(profile_dir / raw)
        if project_root:
            candidates.append(project_root / raw)
        candidates.append(Path.cwd() / raw)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8"), None
    return None, Diagnostic(
        level="error",
        code="E076",
        message=(
            f'ConfigMap "{name}" of module "{module_id}" references bind source '
            f'"{from_bind}", which was not found relative to the profile or project root.'
        ),
        path=f"module:{module_id}.implementation.kubernetes.configMaps.{name}.fromBind",
    )


def _octal(value: Any) -> int:
    if isinstance(value, int):
        return value
    text = str(value).strip()
    try:
        return int(text, 8)
    except ValueError:
        return 0o444


# ---------------------------------------------------------------------------
# Manifests
# ---------------------------------------------------------------------------


def _wait_containers(wait_for: list[dict[str, Any]]) -> list[dict[str, Any]]:
    containers = []
    for index, gate in enumerate(wait_for):
        host = gate["host"]
        port = gate["port"]
        timeout_seconds = int(gate.get("timeoutSeconds", 300))
        attempts = max(1, (timeout_seconds + 1) // 2)
        containers.append(
            {
                "name": _k8s_name(f"wait-{index}-{host}"),
                "image": WAIT_IMAGE,
                "command": [
                    "/bin/sh",
                    "-c",
                    (
                        "attempt=0; "
                        f"until nc -z {host} {port}; do "
                        "attempt=$((attempt + 1)); "
                        f'if [ "$attempt" -ge {attempts} ]; then '
                        f'echo "timed out waiting for {host}:{port}"; exit 1; fi; '
                        f"echo waiting for {host}:{port}; sleep 2; done"
                    ),
                ],
                "securityContext": {
                    "readOnlyRootFilesystem": True,
                    "allowPrivilegeEscalation": False,
                    "runAsNonRoot": True,
                    "runAsUser": 65534,
                    "capabilities": {"drop": ["ALL"]},
                },
                "resources": {
                    "requests": {"cpu": "10m", "memory": "16Mi"},
                    "limits": {"cpu": "100m", "memory": "32Mi"},
                },
            }
        )
    return containers


def _workload_manifest(
    kind: str,
    name: str,
    release: str,
    module_id: str,
    replicas: int,
    pod_spec: dict[str, Any],
    pvcs: dict[str, dict[str, Any]],
    service_name: str | None,
) -> dict[str, Any]:
    labels = _labels(release, module_id)
    selector = {
        "app.kubernetes.io/name": name,
        "app.kubernetes.io/instance": "{{ .Release.Name }}",
    }
    pod_labels = dict(labels, **selector)

    manifest: dict[str, Any] = {
        "apiVersion": "batch/v1" if kind == "Job" else "apps/v1",
        "kind": kind,
        "metadata": {"name": "{{ .Release.Name }}-" + name, "labels": pod_labels},
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": selector},
            "template": {"metadata": {"labels": pod_labels}, "spec": pod_spec},
        },
    }
    if kind == "StatefulSet":
        manifest["spec"]["serviceName"] = service_name or name
        if pvcs:
            manifest["spec"]["volumeClaimTemplates"] = [
                {"metadata": {"name": n}, "spec": claim}
                for n, claim in sorted(pvcs.items())
            ]
    elif pvcs:
        # A Deployment cannot own claim templates; the claims are separate
        # objects and the pod references them by name.
        manifest["spec"]["template"]["spec"].setdefault("volumes", []).extend(
            {
                "name": n,
                "persistentVolumeClaim": {"claimName": "{{ .Release.Name }}-" + n},
            }
            for n in sorted(pvcs)
        )
    if kind == "Job":
        manifest["spec"].pop("replicas", None)
        manifest["spec"].pop("selector", None)
        manifest["spec"]["template"]["spec"]["restartPolicy"] = "OnFailure"
    return manifest


def _service_manifest(
    service_name: str,
    release: str,
    module_id: str,
    workload_name: str,
    svc: dict[str, Any],
) -> dict[str, Any]:
    selector = {
        "app.kubernetes.io/name": workload_name,
        "app.kubernetes.io/instance": "{{ .Release.Name }}",
    }
    ports = []
    for port in svc.get("ports", []):
        entry = {
            "name": port["name"],
            "port": int(port["port"]),
            "targetPort": int(port.get("targetPort", port["port"])),
            "protocol": port.get("protocol", "TCP"),
        }
        if port.get("nodePort"):
            entry["nodePort"] = int(port["nodePort"])
        ports.append(entry)

    spec: dict[str, Any] = {"selector": selector}
    if svc.get("type") == "None":
        spec["clusterIP"] = "None"
        spec["ports"] = ports or [{"name": "none", "port": 55555}]
    else:
        spec["type"] = svc.get("type", "ClusterIP")
        spec["ports"] = ports

    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": service_name,
            "labels": _labels(release, module_id),
        },
        "spec": spec,
    }


def _secret_manifest(release: str, keys: list[str]) -> str:
    """
    The Secret is emitted as raw template text, not as a dumped mapping.

    Each key uses Helm's `required` function so a manual install fails before
    workload creation when its install-time value is absent. A YAML dumper
    would quote the template expressions into scalars, so this template is
    emitted directly.
    """
    labels = "\n".join(f"    {k}: {v}" for k, v in _labels(release, "cds").items())
    return (
        "apiVersion: v1\n"
        "kind: Secret\n"
        "metadata:\n"
        "  name: {{ .Release.Name }}-secrets\n"
        "  labels:\n"
        f"{labels}\n"
        "  annotations:\n"
        f"    cds.dev/required-keys: {','.join(keys)}\n"
        "type: Opaque\n"
        "stringData:\n"
        + "".join(
            f'  {key}: {{{{ required "secrets.{key} is required" '
            f'(index .Values.secrets "{key}") | quote }}}}\n'
            for key in keys
        )
    )


def _labels(release: str, module_id: str) -> dict[str, str]:
    return {
        "app.kubernetes.io/managed-by": "cds",
        "app.kubernetes.io/part-of": release,
        "cds.dev/module": module_id,
    }


def _chart_metadata(metadata: dict[str, Any], release: str) -> dict[str, Any]:
    version = str(metadata.get("version") or "0.1.0")
    return {
        "apiVersion": CHART_API_VERSION,
        "name": release,
        "description": (
            metadata.get("description") or f"CDS profile {release}"
        ).strip(),
        "type": "application",
        "version": version,
        "appVersion": str(metadata.get("appVersion") or version),
    }


def _notes(
    release: str, modules: list[dict[str, Any]], service_names: dict[str, str]
) -> str:
    lines = [
        f"CDS profile {release} installed as release {{{{ .Release.Name }}}}.",
        "",
        "Services:",
    ]
    for svc in sorted(set(service_names.values())):
        lines.append(f"  {svc}: kubectl -n {{{{ .Release.Namespace }}}} get svc {svc}")
    lines.append("")
    lines.append(
        "Secret values are supplied at install time and are not stored in this chart."
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _partition_modules(
    modules: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    renderable, unsupported = [], []
    for module in modules:
        impl = module.get("implementation", {}) or {}
        targets = impl.get("targets") or [impl.get("kind")]
        if "kubernetes" in targets and impl.get("kubernetes"):
            renderable.append(module)
        else:
            unsupported.append((module.get("id", "?"), str(impl.get("kind"))))
    return renderable, unsupported


def _collect_service_names(modules: list[dict[str, Any]]) -> dict[str, str]:
    """
    Map each workload to its Service DNS name.

    Keys are exposed under both the module-local workload name and the
    module-prefixed name, so `${k8s.service.user-code}` resolves from inside the
    module that declares it.
    """
    names: dict[str, str] = {}
    for module in modules:
        module_id = module["id"]
        k8s = module["implementation"]["kubernetes"]
        for workload_name, workload in k8s.get("workloads", {}).items():
            if not workload.get("service") and workload.get("kind") != "StatefulSet":
                continue
            full = _compose_service_name(module_id, workload_name)
            service = workload.get("serviceName") or full
            names[workload_name] = service
            names[full] = service
    return names


def _check_binding_services(
    modules: list[dict[str, Any]], service_names: dict[str, str]
) -> list[Diagnostic]:
    module_ids = {module["id"] for module in modules}
    available_services = set(service_names.values())
    diagnostics: list[Diagnostic] = []
    for module in modules:
        for consume_name, consume in (module.get("consumes") or {}).items():
            contract_ref = str(consume.get("contractRef", ""))
            provider_id = contract_ref.split(".", 1)[0]
            if provider_id not in module_ids:
                continue
            host = ((consume.get("contract") or {}).get("spec") or {}).get("host")
            if host not in available_services:
                diagnostics.append(
                    Diagnostic(
                        level="error",
                        code="E084",
                        message=(
                            f'Contract "{module["id"]}.{consume_name}" binds to module '
                            f'"{provider_id}", but host "{host}" is not exposed by a '
                            "renderable Kubernetes Service."
                        ),
                        path=f"module:{module['id']}.consumes.{consume_name}",
                    )
                )
    return diagnostics


def _sanitize_name(name: str) -> str:
    return _k8s_name(name)


def _k8s_name(value: str) -> str:
    out = re.sub(r"[^a-z0-9-]+", "-", str(value).lower()).strip("-")
    out = re.sub(r"-+", "-", out)
    return out[:63].strip("-") or "cds"


def _dump(obj: Any) -> str:
    if isinstance(obj, str):
        return obj
    rendered = yaml.safe_dump(
        obj, sort_keys=False, default_flow_style=False, width=4096
    )
    return re.sub(
        r"^(?P<indent>\s*)resources: __CDS_HELM_TO_YAML__(?P<path>[^\n]+)$",
        _expand_helm_to_yaml,
        rendered,
        flags=re.MULTILINE,
    )


def _helm_to_yaml(*path: str) -> str:
    return "__CDS_HELM_TO_YAML__" + "|".join(path)


def _expand_helm_to_yaml(match: re.Match[str]) -> str:
    indent = match.group("indent")
    quoted = " ".join(f'"{part}"' for part in match.group("path").split("|"))
    return (
        f"{indent}resources:\n"
        f"{{{{ toYaml (index .Values {quoted}) | nindent {len(indent) + 2} }}}}"
    )


def _values_config(value: Any) -> Any:
    """Keep tunable config in values.yaml while representing secrets by key only."""
    if isinstance(value, dict):
        return {key: _values_config(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_values_config(item) for item in value]
    if isinstance(value, str):
        return _CDS_VAR_PATTERN.sub(lambda match: f"secretRef:{match.group(1)}", value)
    return value


def _write_chart(root: Path, files: dict[str, str]) -> None:
    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{root.name}.tmp-", dir=root.parent))
    backup = staging.with_name(staging.name.replace(".tmp-", ".previous-"))
    try:
        for rel, content in files.items():
            path = staging / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(path, content)
        if root.exists() or root.is_symlink():
            os.replace(root, backup)
        os.replace(staging, root)
    except Exception:
        if backup.exists() and not root.exists():
            os.replace(backup, root)
        raise
    finally:
        _remove_path(staging)
        _remove_path(backup)


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _check_unresolved(files: dict[str, str]) -> list[Diagnostic]:
    pattern = re.compile(r"\$\{((?:config|bindings|service|secrets|k8s)\.[^}]*)\}")
    found: set[str] = set()
    for content in files.values():
        found.update(pattern.findall(content))
    return [
        Diagnostic(
            level="error",
            code="E071",
            message=(
                f'Unresolved template expression "${{{expr}}}" remains in the rendered '
                "chart. A module template referenced something the plan never bound."
            ),
            path=f"chart.{expr}",
        )
        for expr in sorted(found)
    ]


def _check_secret_references(
    files: dict[str, str], secret_keys: set[str]
) -> list[Diagnostic]:
    """
    Assert the chart's secret plumbing is complete and self-consistent.

    The plan deliberately carries only env variable NAMES (see
    cli/secrets.py::load_profile_secrets), so a renderer cannot leak a value it
    never receives. What it can get wrong is the plumbing: leaving a compose
    `${CDS_X}` behind, which Kubernetes does not expand and which would reach a
    container as the literal string, or expanding `$(CDS_X)` against a key no
    secretKeyRef entry supplies, which yields an empty value at runtime.

    The value-leak class is closed by the sentinel test, which controls its own
    fixture values and can therefore assert on the real strings.
    """
    diagnostics: list[Diagnostic] = []
    for rel, content in sorted(files.items()):
        for name in sorted(set(_CDS_VAR_PATTERN.findall(content))):
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E078",
                    message=(
                        f'Chart file "{rel}" still contains the compose-style reference '
                        f'"${{{name}}}". Kubernetes does not expand it, so the container '
                        "would receive the literal text."
                    ),
                    path=f"chart.{rel}",
                )
            )

    declared = set(secret_keys)
    expansion = re.compile(r"\$\((CDS_[A-Z0-9_]+)\)")
    for rel, content in sorted(files.items()):
        for name in sorted(set(expansion.findall(content))):
            if name not in declared:
                diagnostics.append(
                    Diagnostic(
                        level="error",
                        code="E079",
                        message=(
                            f'Chart file "{rel}" expands "$({name})" but no secretKeyRef '
                            "entry supplies it, so the container would see an empty value."
                        ),
                        path=f"chart.{rel}",
                    )
                )
    return diagnostics
