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
        if not isinstance(m, dict):
            continue
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


def _validate_modules_shape(label: str, modules: Any) -> list[Diagnostic]:
    """
    Shared spec.modules shape validation: used for both environment overlays
    and extends parents/child so extends does not need a second validation
    path. Returns error diagnostics only; an empty list means modules is a
    well-formed list of module mappings with unique, present ids.
    """
    diagnostics: list[Diagnostic] = []

    if not isinstance(modules, list):
        return [
            Diagnostic(
                level="error",
                code="E093",
                message=f"spec.modules in {label} must be a list, got {type(modules).__name__}.",
                path="spec.modules",
            )
        ]

    non_dict_indices = [i for i, m in enumerate(modules) if not isinstance(m, dict)]
    if non_dict_indices:
        diagnostics.append(
            Diagnostic(
                level="error",
                code="E093",
                message=(
                    f"Module entr{'y' if len(non_dict_indices) == 1 else 'ies'} in {label} "
                    f"must be a mapping, not a scalar/list, at index {non_dict_indices}."
                ),
                path="spec.modules",
            )
        )
        return diagnostics

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
        return diagnostics

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

    return diagnostics


def _merge_profile_docs(
    base: dict[str, Any],
    overlay: dict[str, Any],
    base_source: str,
    overlay_source: str,
    provenance: dict[str, str],
) -> tuple[dict[str, Any], list[Diagnostic]]:
    """
    Merges two profile-shaped documents (deep-merge for everything except
    spec.modules, which is merged by stable id). This is the single merge
    path shared by environment overlays and `extends` composition, per
    issue #175's requirement to reuse the resolver built for #229 rather
    than introduce a second merge engine.
    """
    diagnostics: list[Diagnostic] = []

    base_spec = base.get("spec", {})
    base_modules = base_spec.get("modules", []) if isinstance(base_spec, dict) else []
    overlay_spec = overlay.get("spec", {})
    overlay_modules = overlay_spec.get("modules", []) if isinstance(overlay_spec, dict) else []

    for label, modules in ((base_source, base_modules), (overlay_source, overlay_modules)):
        diagnostics += _validate_modules_shape(label, modules)
    if any(d.level == "error" for d in diagnostics):
        return {}, diagnostics

    base_without_modules = dict(base)
    base_spec_without_modules = (
        {k: v for k, v in base_spec.items() if k != "modules"} if isinstance(base_spec, dict) else {}
    )
    base_without_modules["spec"] = base_spec_without_modules

    overlay_without_modules = dict(overlay)
    if isinstance(overlay.get("spec"), dict):
        overlay_without_modules["spec"] = {k: v for k, v in overlay["spec"].items() if k != "modules"}

    merged = _merge_value(
        base_without_modules, overlay_without_modules, base_source, overlay_source, "", provenance
    )
    merged.setdefault("spec", {})
    if not isinstance(merged["spec"], dict):
        # An overlay/parent that sets `spec: null` (or any non-mapping) wins
        # the deep-merge in _merge_value since it's a full-value override,
        # not a sub-key merge. Normalize back to a dict here so the
        # spec.modules assignment below doesn't crash; the profile will
        # still fail downstream shape/schema validation as expected.
        merged["spec"] = {}
    merged["spec"]["modules"] = _merge_modules(
        base_modules, overlay_modules, base_source, overlay_source, provenance
    )
    return merged, diagnostics


def _derive_profiles_root(profile_dir: Path) -> Path | None:
    # Use the innermost (last, i.e. closest to profile_dir) "profiles"
    # segment, not the first/outermost one. A path can contain more than one
    # segment literally named "profiles" (e.g. a checkout at
    # ".../profiles/<repo>/profiles/prod"); picking the outermost match would
    # derive an overly broad root and let `extends` escape the profile's own
    # repo-local profiles/ tree into an unrelated sibling project.
    parts = profile_dir.parts
    for index in range(len(parts) - 1, -1, -1):
        if parts[index] == "profiles":
            return Path(*parts[: index + 1])
    return None


def _resolve_extends_ref(ref: str, profile_dir: Path, profiles_root: Path) -> Path:
    ref_path = Path(ref)
    if ref_path.suffix in (".yaml", ".yml") or ".." in ref_path.parts or ref_path.is_absolute():
        return (profile_dir / ref_path).resolve()
    if len(ref_path.parts) > 1:
        return (profile_dir / ref_path / "profile.yaml").resolve()
    return (profiles_root / ref / "profile.yaml").resolve()


def _compose_extends(
    profile_file: Path,
    stack: tuple[Path, ...],
) -> tuple[dict[str, Any] | None, dict[str, str], list[Diagnostic]]:
    """
    Recursively resolves `extends` on the profile at profile_file, merging
    one or more parent profiles left-to-right (later parents win) and then
    the child's own document on top of all parents. Reuses
    _merge_profile_docs so this is the same merge/provenance model as
    environment overlays, not a separate engine, per issue #175.
    """
    resolved_file = profile_file.resolve()
    if resolved_file in stack:
        chain = " -> ".join(str(p) for p in (*stack, resolved_file))
        return None, {}, [
            Diagnostic(
                level="error",
                code="E113",
                message=f"Cycle detected in profile extends chain: {chain}.",
                path="extends",
            )
        ]

    doc, diagnostics = load_yaml_file(profile_file)
    if doc is None:
        return None, {}, diagnostics

    profile_dir = profile_file.parent.resolve()
    new_stack = (*stack, resolved_file)
    merged, provenance, merge_diagnostics = _resolve_and_merge_extends(
        doc, profile_dir, str(profile_file), new_stack
    )
    diagnostics += merge_diagnostics
    return merged, provenance, diagnostics


def _compose_extends_from_doc(
    doc: dict[str, Any],
    profile_dir: Path,
    source_label: str,
) -> tuple[dict[str, Any] | None, dict[str, str], list[Diagnostic]]:
    """
    In-memory counterpart to _compose_extends(): resolves `doc`'s own
    `extends` chain the same way, but for an already-loaded document that
    has no file of its own on disk (e.g. a profile assembled at runtime and
    planned/validated without being written to disk first, per issue #349).
    `profile_dir` anchors relative extends refs (and derives the profiles
    root) the same way a real profile file's parent directory would;
    `source_label` stands in for the file path in diagnostics and merge
    provenance. `doc` has no file identity of its own to cycle back to, so
    cycle detection (E113) only applies to on-disk *parent* extends chains,
    which still recurse through _compose_extends() normally.
    """
    return _resolve_and_merge_extends(doc, profile_dir, source_label, ())


def _resolve_and_merge_extends(
    doc: dict[str, Any],
    profile_dir: Path,
    source_label: str,
    stack: tuple[Path, ...],
) -> tuple[dict[str, Any] | None, dict[str, str], list[Diagnostic]]:
    """
    Shared tail of _compose_extends()/_compose_extends_from_doc(): resolves
    `doc`'s own `extends` list (if any) into merged parent(s) plus `doc`
    itself, given the directory `doc` should be treated as living in
    (`profile_dir`) and a label to use in diagnostics/provenance in place of
    a real file path (`source_label`). `stack` is only used for cycle
    detection against on-disk parent recursion; callers with no file
    identity of their own to cycle back to (i.e. _compose_extends_from_doc)
    pass ().
    """
    diagnostics: list[Diagnostic] = []
    extends = doc.get("extends")
    if extends is None:
        return doc, {}, diagnostics

    if not isinstance(extends, list) or not extends or not all(
        isinstance(ref, str) and ref.strip() for ref in extends
    ):
        diagnostics.append(
            Diagnostic(
                level="error",
                code="E110",
                message="'extends' must be a non-empty list of non-empty profile references.",
                path="extends",
            )
        )
        return None, {}, diagnostics

    profiles_root = _derive_profiles_root(profile_dir)
    if profiles_root is None:
        diagnostics.append(
            Diagnostic(
                level="error",
                code="E111",
                message=(
                    f'Cannot resolve "extends" for {source_label}: its directory does not '
                    'reside under a "profiles/" root.'
                ),
                path="extends",
            )
        )
        return None, {}, diagnostics

    merged: dict[str, Any] | None = None
    merged_source: str | None = None
    provenance: dict[str, str] = {}

    for ref in extends:
        parent_path = _resolve_extends_ref(ref, profile_dir, profiles_root)

        if not _is_within(parent_path, profiles_root):
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E111",
                    message=f'Parent profile "{ref}" resolves outside the profiles root "{profiles_root}".',
                    path="extends",
                )
            )
            return None, {}, diagnostics

        if not parent_path.is_file():
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E112",
                    message=f'Parent profile "{ref}" not found (looked for {parent_path}).',
                    path="extends",
                )
            )
            return None, {}, diagnostics

        parent_doc, parent_provenance, parent_diagnostics = _compose_extends(parent_path, stack)
        diagnostics += parent_diagnostics
        if parent_doc is None:
            return None, {}, diagnostics

        if merged is None:
            merged = parent_doc
            merged_source = str(parent_path)
            provenance = parent_provenance
        else:
            merged, merge_diagnostics = _merge_profile_docs(
                merged, parent_doc, merged_source, str(parent_path), provenance
            )
            diagnostics += merge_diagnostics
            if merge_diagnostics:
                return None, {}, diagnostics
            merged_source = str(parent_path)

    child_without_extends = {k: v for k, v in doc.items() if k != "extends"}
    merged, merge_diagnostics = _merge_profile_docs(
        merged, child_without_extends, merged_source, source_label, provenance
    )
    diagnostics += merge_diagnostics
    if merge_diagnostics:
        return None, {}, diagnostics

    return merged, provenance, diagnostics


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


def resolve_extends(
    profile_path: str,
) -> tuple[dict[str, Any] | None, dict[str, str], list[Diagnostic]]:
    """
    Resolves only a profile's `extends` chain (no --environment overlay, no
    full validate_loaded_profile pass). Used by build_plan()/validate_profile()
    in place of a bare load_yaml_file() so `extends` composition applies even
    when no --environment is selected, while preserving their own
    lighter-weight/defensive diagnostic handling instead of resolve_profile()'s
    full validation, which would mask those diagnostics.
    """
    return _compose_extends(Path(profile_path), ())


def resolve_extends_from_profile(
    profile: dict[str, Any],
    profile_dir: Path,
    source_label: str = "<in-memory profile>",
) -> tuple[dict[str, Any] | None, dict[str, str], list[Diagnostic]]:
    """
    In-memory counterpart to resolve_extends() (issue #349): resolves only
    `profile`'s own `extends` chain against on-disk parent profiles, without
    requiring `profile` itself to be loaded from (or written to) disk
    first. `profile_dir` only needs to exist and reside under a
    "profiles/" root if `profile` actually declares `extends`; it does not
    need to contain a profile.yaml of its own. `source_label` stands in
    for a real file path in diagnostics and merge provenance.
    """
    return _compose_extends_from_doc(profile, profile_dir, source_label)


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
    base, provenance, diagnostics = _compose_extends(profile_file, ())
    if base is None:
        return None, {}, diagnostics

    resolved, out_provenance = _resolve_environment_overlay_and_validate(
        base, provenance, profile_file.parent, str(profile_file), environment, profile_file, diagnostics
    )
    return resolved, out_provenance, diagnostics


def resolve_profile_from_profile(
    profile: dict[str, Any],
    profile_dir: Path,
    environment: str | None = None,
    source_label: str = "<in-memory profile>",
) -> tuple[dict[str, Any] | None, dict[str, str], list[Diagnostic]]:
    """
    In-memory counterpart to resolve_profile() (issue #349): resolves
    `profile`'s own `extends` chain and, if `environment` is set, merges
    profile_dir/environments/<environment>.yaml over the result, then runs
    the same validate_loaded_profile() pass -- all without requiring
    `profile` to be loaded from (or written to) disk first. `profile_dir`
    only needs to exist and reside under a "profiles/" root if `extends`
    or `environment` are actually used; it does not need to contain a
    profile.yaml of its own. `source_label` stands in for a real file path
    in diagnostics and merge provenance.
    """
    base, provenance, diagnostics = _compose_extends_from_doc(profile, profile_dir, source_label)
    if base is None:
        return None, {}, diagnostics

    anchor_file = profile_dir / "profile.yaml"
    resolved, out_provenance = _resolve_environment_overlay_and_validate(
        base, provenance, profile_dir, source_label, environment, anchor_file, diagnostics
    )
    return resolved, out_provenance, diagnostics


def _resolve_environment_overlay_and_validate(
    base: dict[str, Any],
    provenance: dict[str, str],
    profile_dir: Path,
    source_label: str,
    environment: str | None,
    anchor_file: Path,
    diagnostics: list[Diagnostic],
) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """
    Shared tail of resolve_profile()/resolve_profile_from_profile(): applies
    an optional environments/<environment>.yaml overlay over `base` (merged
    the same way `extends` parents are, via _merge_profile_docs) and runs
    validate_loaded_profile() against the result (or against `base` directly
    when environment is None), appending all diagnostics to `diagnostics` in
    place. `anchor_file` is passed through to validate_loaded_profile()
    purely to anchor module `source:` resolution via its .parent -- unlike
    a resolve_profile() caller's real profile file, it does not need to
    exist for resolve_profile_from_profile()'s in-memory case.

    Returns (resolved_profile_or_None, provenance_to_report); the reported
    provenance intentionally differs between failure branches to preserve
    resolve_profile()'s pre-existing behavior (empty on overlay-resolution
    failures, the extends-derived provenance on validation failures).
    """
    if environment is None:
        diagnostics.extend(validate_loaded_profile(base, anchor_file))
        if any(d.level == "error" for d in diagnostics):
            return None, provenance
        return base, provenance

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
        return None, {}

    if not overlay_file.is_file():
        diagnostics.append(
            Diagnostic(
                level="error",
                code="E091",
                message=f'Unknown environment "{environment}": {overlay_file} does not exist.',
                path="environment",
            )
        )
        return None, {}

    overlay, overlay_diags = load_yaml_file(overlay_file)
    diagnostics.extend(overlay_diags)
    if overlay is None:
        return None, {}

    merged, merge_diagnostics = _merge_profile_docs(base, overlay, source_label, str(overlay_file), provenance)
    diagnostics.extend(merge_diagnostics)
    if merge_diagnostics:
        return None, {}

    diagnostics.extend(validate_loaded_profile(merged, anchor_file))
    if any(d.level == "error" for d in diagnostics):
        return None, provenance

    return merged, provenance
