import unittest

from cli.graph import validate_dependency_graph


class GraphValidationTest(unittest.TestCase):
    def test_valid_acyclic_graph_produces_no_diagnostics(self):
        module_ids = {"a", "b", "c"}
        depends_on_map = {"a": [], "b": ["a"], "c": ["a", "b"]}

        diagnostics = validate_dependency_graph(module_ids, depends_on_map)

        self.assertEqual(diagnostics, [])

    def test_unknown_dependency_is_reported(self):
        module_ids = {"a", "b"}
        depends_on_map = {"a": [], "b": ["missing"]}

        diagnostics = validate_dependency_graph(module_ids, depends_on_map)

        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0].code, "E040")
        self.assertIn('depends on unknown module "missing"', diagnostics[0].message)

    def test_self_dependency_is_reported(self):
        module_ids = {"a"}
        depends_on_map = {"a": ["a"]}

        diagnostics = validate_dependency_graph(module_ids, depends_on_map)

        # Both the explicit self-dependency check and the DFS cycle detector
        # fire for a self-referencing module.
        self.assertEqual(len(diagnostics), 2)
        self.assertTrue(all(d.code == "E040" for d in diagnostics))
        self.assertTrue(any("cannot depend on itself" in d.message for d in diagnostics))

    def test_direct_cycle_is_detected(self):
        module_ids = {"a", "b"}
        depends_on_map = {"a": ["b"], "b": ["a"]}

        diagnostics = validate_dependency_graph(module_ids, depends_on_map)

        cycle_diags = [d for d in diagnostics if "cycle" in d.message]
        self.assertEqual(len(cycle_diags), 1)
        self.assertEqual(cycle_diags[0].code, "E040")

    def test_indirect_cycle_across_three_modules_is_detected(self):
        module_ids = {"a", "b", "c"}
        depends_on_map = {"a": ["b"], "b": ["c"], "c": ["a"]}

        diagnostics = validate_dependency_graph(module_ids, depends_on_map)

        cycle_diags = [d for d in diagnostics if "cycle" in d.message]
        self.assertEqual(len(cycle_diags), 1)

    def test_deep_linear_chain_does_not_raise_recursion_error(self):
        # Regression guard: dfs() in cli/graph.py recurses per dependency edge.
        # A long (but acyclic) dependency chain must not trigger a
        # RecursionError.
        depth = 900
        module_ids = {f"m{i}" for i in range(depth)}
        depends_on_map = {
            f"m{i}": [f"m{i - 1}"] if i > 0 else [] for i in range(depth)
        }

        diagnostics = validate_dependency_graph(module_ids, depends_on_map)

        self.assertEqual(diagnostics, [])

    def test_shared_dependency_diamond_produces_no_false_cycle(self):
        # a depends on b and c; b and c both depend on d. Not a cycle.
        module_ids = {"a", "b", "c", "d"}
        depends_on_map = {"a": ["b", "c"], "b": ["d"], "c": ["d"], "d": []}

        diagnostics = validate_dependency_graph(module_ids, depends_on_map)

        self.assertEqual(diagnostics, [])

    def test_module_with_no_dependencies_entry_is_ignored(self):
        module_ids = {"a", "b"}
        depends_on_map = {"a": []}

        diagnostics = validate_dependency_graph(module_ids, depends_on_map)

        self.assertEqual(diagnostics, [])


if __name__ == "__main__":
    unittest.main()
