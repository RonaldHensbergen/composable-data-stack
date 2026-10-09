"""Regression tests for the vault profile's init-db.sh bootstrap script.

Guards the defects fixed under #836 without needing Docker: psql
`:'var'` interpolation does not apply inside `DO $do$` dollar-quoted
bodies, every referenced psql variable must be passed with `-v`, and all
roles must come from configuration (no hardcoded role names).
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "profiles" / "local-dagster-postgres-superset-vault" / "init-db.sh"


def _read_script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


class VaultInitDbTest(unittest.TestCase):
    def test_no_dollar_quoted_do_block(self) -> None:
        text = _read_script()
        self.assertNotIn("DO $do$", text)
        self.assertNotIn("$do$;", text)

    def test_every_psql_variable_is_passed_via_dash_v(self) -> None:
        text = _read_script()
        header, _, body = text.partition("<<'SQL'")
        defined = set(re.findall(r"^\s*-v\s+([A-Za-z_][A-Za-z0-9_]*)\s*=", header, re.M))
        referenced = set(re.findall(r":'([A-Za-z_][A-Za-z0-9_]*)'", body))
        referenced |= set(re.findall(r"(?<![':]):([A-Za-z_][A-Za-z0-9_]*)", body))
        self.assertTrue(referenced, "expected psql variable references in init-db.sh")
        self.assertEqual(
            referenced - defined,
            set(),
            f"psql variables used but never passed with -v: {sorted(referenced - defined)}",
        )

    def test_no_hardcoded_role_names(self) -> None:
        text = _read_script()
        self.assertNotRegex(text, r"CREATE USER [A-Za-z_]")
        self.assertNotRegex(text, r"rolname = '[A-Za-z_]")


if __name__ == "__main__":
    unittest.main()
