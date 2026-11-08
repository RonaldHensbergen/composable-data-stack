import importlib.util
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "assign_project_order.py"

_spec = importlib.util.spec_from_file_location("assign_project_order", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
assign_project_order = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = assign_project_order
_spec.loader.exec_module(assign_project_order)


def issue(title="Some issue", labels=(), milestone=None):
    return {
        "number": 9999,
        "title": title,
        "labels": [{"name": name} for name in labels],
        "milestone": {"title": milestone} if milestone else None,
    }


def project_item(number, order, labels=(), milestone=None, type="Issue"):
    return {
        "content": {"type": type, "number": number, "title": f"item {number}"},
        "order": order,
        "labels": [{"name": name} for name in labels],
        "milestone": {"title": milestone} if milestone else None,
    }


class CategoryTest(unittest.TestCase):
    def test_fix_by_bug_label(self):
        self.assertEqual(assign_project_order.category([{"name": "bug"}], "", "Anything"), "fix")

    def test_fix_by_conventional_title(self):
        self.assertEqual(assign_project_order.category([], "", "fix: repair planner"), "fix")
        self.assertEqual(assign_project_order.category([], "", "fix(security): guard writes"), "fix")
        self.assertEqual(assign_project_order.category([], "", "feat: not a fix"), "rest")

    def test_precedence(self):
        # fix beats security, milestone beats priority:high, security beats high
        self.assertEqual(
            assign_project_order.category([{"name": "area:security"}], "", "fix: x"), "fix"
        )
        self.assertEqual(
            assign_project_order.category([{"name": "priority:high"}], "Production ready", "x"),
            "milestone",
        )
        self.assertEqual(
            assign_project_order.category([{"name": "area:security"}, {"name": "priority:high"}], "", "x"),
            "security",
        )
        self.assertEqual(assign_project_order.category([{"name": "priority:high"}], "", "x"), "high")
        self.assertEqual(assign_project_order.category([{"name": "priority:medium"}], "", "x"), "medium")
        self.assertEqual(assign_project_order.category([{"name": "area:docs"}], "", "x"), "rest")


class AssignTest(unittest.TestCase):
    def test_empty_board_uses_band_base(self):
        cases = [
            (issue(labels=["bug"]), 1000),
            (issue(milestone="Production ready"), 1100),
            (issue(labels=["area:security"]), 1200),
            (issue(labels=["vuln-scan"]), 1200),
            (issue(labels=["priority:high"]), 1300),
            (issue(labels=["priority:medium"]), 1400),
            (issue(), 1500),
        ]
        for iss, expected in cases:
            with self.subTest(expected=expected):
                cat, order = assign_project_order.assign(iss, [])
                self.assertEqual(order, expected)

    def test_appends_within_band(self):
        items = [project_item(1, 1000), project_item(2, 1002), project_item(3, 700)]
        cat, order = assign_project_order.assign(issue(labels=["bug"]), items)
        self.assertEqual((cat, order), ("fix", 1003))

    def test_bands_do_not_leak_into_each_other(self):
        items = [project_item(1, 1099)]  # fix band full up to its edge
        cat, order = assign_project_order.assign(issue(milestone="M"), items)
        self.assertEqual((cat, order), ("milestone", 1100))

    def test_full_band_falls_back_to_global_max(self):
        items = [project_item(i, 1000 + i) for i in range(100)] + [project_item(999, 700)]
        cat, order = assign_project_order.assign(issue(labels=["bug"]), items)
        self.assertEqual((cat, order), ("fix", 1100))

    def test_non_issue_items_and_missing_order_are_ignored(self):
        items = [
            project_item(1, 1000, type="PullRequest"),
            {"content": {"type": "Issue", "number": 2, "title": "no order"}, "order": None},
        ]
        cat, order = assign_project_order.assign(issue(labels=["bug"]), items)
        self.assertEqual((cat, order), ("fix", 1000))


if __name__ == "__main__":
    unittest.main()
