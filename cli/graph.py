# cli/graph.py
from __future__ import annotations

from typing import Dict, List, Set

from .diagnostics import Diagnostic


def validate_dependency_graph(module_ids: set[str], depends_on_map: Dict[str, List[str]]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []

    for module_id, deps in depends_on_map.items():
        for dep in deps:
            if dep not in module_ids:
                diagnostics.append(
                    Diagnostic(
                        level="error",
                        code="E040",
                        message=f'Module "{module_id}" depends on unknown module "{dep}".',
                        path=f"spec.modules[{module_id}].dependsOn",
                    )
                )
            if dep == module_id:
                diagnostics.append(
                    Diagnostic(
                        level="error",
                        code="E040",
                        message=f'Module "{module_id}" cannot depend on itself.',
                        path=f"spec.modules[{module_id}].dependsOn",
                    )
                )

    visited: Set[str] = set()
    visiting: Set[str] = set()

    def dfs(node: str) -> None:
        if node in visiting:
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E040",
                    message=f'Dependency cycle detected involving "{node}".',
                    path=f"spec.modules[{node}].dependsOn",
                )
            )
            return
        if node in visited:
            return

        visiting.add(node)
        for dep in depends_on_map.get(node, []):
            if dep in module_ids:
                dfs(dep)
        visiting.remove(node)
        visited.add(node)

    for module_id in module_ids:
        dfs(module_id)

    return diagnostics
