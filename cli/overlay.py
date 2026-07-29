# cli/overlay.py
from __future__ import annotations

from pathlib import Path
from typing import Any

from .diagnostics import Diagnostic
from .loader import _is_within, load_yaml_file
from .validator import validate_loaded_profile


def _duplicate_module_ids(modules: list[dict[str, Any]]) -> set[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for m in modules:
        mid = m.get("id")
        if mid is None:
            continue
        if mid in seen:
            dupes.add(mid)
        seen.add(mid)
    return dupes


def _merge_value(
    base: Any,
    overlay: Any,
    base_source: str,
    overlay_source: str,
    path: str,
    provenance: dict[str, str],
) -> Any:
    if isinstance(base, dict) and isinstance(overlay, dict):
        result: dict[str, Any] = {}
        for key, value in base.items():
            child_path = f"{path}.{key}" if path else key
            result[key] = value
            provenance.setdefault(child_path, base_source)
        for key, value in overlay.items():
            child_path = f"{path}.{key}" if path else key
            if key in result:
                result[key] = _merge_value(
                    result[key], value, base_source, overlay_source, child_path, provenance
                )
            else:
                result[key] = value
                provenance[child_path] = overlay_source
        return result

    provenance[path] = overlay_source
    return overlay


def _merge_modules(
    base_modules: list[dict[str, Any]],
    overlay_modules: list[dict[str, Any]],
    base_source: str,
    overlay_source: str,
    provenance: dict[str, str],
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for module in base_modules:
        mid = module["id"]
        by_id[mid] = module
        order.append(mid)
        provenance[f"spec.modules[{mid}]"] = base_source

    for module in overlay_modules:
        mid = module["id"]
        if mid in by_id:
            by_id[mid] = _merge_value(
                by_id[mid], module, base_source, overlay_source, f"spec.modules[{mid}]", provenance
            )
        else:
            by_id[mid] = module
            order.append(mid)
        provenance[f"spec.modules[{mid}]"] = overlay_source

    return [by_id[mid] for mid in order]


def resolve_profile(
    profile_path: str,
    environment: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, str], list[Diagnostic]]:
    """
    Loads and, if an environment is selected, merges the profile at
    profile_path with profiles/<name>/environments/<environment>.yaml.

    Returns (resolved_profile, provenance, diagnostics). provenance maps
    dotted config paths (and "spec.modules[<id>]" for whole module entries)
    to the source file responsible for that value. resolved_profile is
    None if resolution or validation failed (see diagnostics).

    environment=None (the default) reproduces the exact behavior of
    load_yaml_file + validate_loaded_profile on the base profile alone,
    standalone profiles are unaffected by this resolver existing.
    """
    profile_file = Path(profile_path)
    base, diagnostics = load_yaml_file(profile_file)
    if base is None:
        return None, {}, diagnostics

    if environment is None:
        diagnostics += validate_loaded_profile(base, profile_file)
        provenance = {}
        return (base, provenance, diagnostics) if not any(
            d.level == "error" for d in diagnostics
        ) else (None, provenance, diagnostics)

    profile_dir = profile_file.parent
    environments_dir = profile_dir / "environments"
    overlay_file = environments_dir / f"{environment}.yaml"

    if not _is_within(overlay_file, environments_dir):
        diagnostics.append(
            Diagnostic(
                level="error",
                code="E090",
                message=f'Environment "{environment}" resolves outside the profile\'s environments/ directory.',
                path="environment",
            )
        )
        return None, {}, diagnostics

    if not overlay_file.is_file():
        diagnostics.append(
            Diagnostic(
                level="error",
                code="E091",
                message=f'Unknown environment "{environment}": {overlay_file} does not exist.',
                path="environment",
            )
        )
        return None, {}, diagnostics

    overlay, overlay_diags = load_yaml_file(overlay_file)
    diagnostics += overlay_diags
    if overlay is None:
        return None, {}, diagnostics

    base_source = str(profile_file)
    overlay_source = str(overlay_file)

    base_spec = base.get("spec", {})
    base_modules = base_spec.get("modules", []) if isinstance(base_spec, dict) else []
    overlay_spec = overlay.get("spec", {})
    overlay_modules = overlay_spec.get("modules", []) if isinstance(overlay_spec, dict) else []

    for label, modules in (("base profile", base_modules), (f"overlay {overlay_source}", overlay_modules)):
        missing_id = [i for i, m in enumerate(modules) if not m.get("id")]
        if missing_id:
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E093",
                    message=f"Module entr{'y' if len(missing_id) == 1 else 'ies'} in {label} missing required 'id' at index {missing_id}.",
                    path="spec.modules",
                )
            )
    if any(d.level == "error" for d in diagnostics):
        return None, {}, diagnostics

    for label, modules in (("base profile", base_modules), (f"overlay {overlay_source}", overlay_modules)):
        dupes = _duplicate_module_ids(modules)
        if dupes:
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E093",
                    message=f"Duplicate module id(s) in {label}: {sorted(dupes)}.",
                    path="spec.modules",
                )
            )
    if any(d.level == "error" for d in diagnostics):
        return None, {}, diagnostics

    provenance: dict[str, str] = {}

    base_without_modules = dict(base)
    base_spec_val = base.get("spec", {})
    base_spec_without_modules = (
        {k: v for k, v in base_spec_val.items() if k != "modules"}
        if isinstance(base_spec_val, dict)
        else {}
    )
    base_without_modules["spec"] = base_spec_without_modules

    overlay_without_modules = dict(overlay)
    if "spec" in overlay and isinstance(overlay.get("spec"), dict):
        overlay_spec_without_modules = {k: v for k, v in overlay["spec"].items() if k != "modules"}
        overlay_without_modules["spec"] = overlay_spec_without_modules

    merged = _merge_value(
        base_without_modules, overlay_without_modules, base_source, overlay_source, "", provenance
    )
    merged.setdefault("spec", {})
    merged["spec"]["modules"] = _merge_modules(
        base_modules, overlay_modules, base_source, overlay_source, provenance
    )

    diagnostics += validate_loaded_profile(merged, profile_file)
    if any(d.level == "error" for d in diagnostics):
        return None, provenance, diagnostics

    return merged, provenance, diagnostics
