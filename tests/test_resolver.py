import unittest

from cli.resolver import (
    is_secret_ref,
    parse_contract_ref,
    resolve_path,
    secret_name_from_ref,
)


class ParseContractRefTest(unittest.TestCase):
    def test_valid_ref_splits_module_and_contract(self):
        self.assertEqual(parse_contract_ref("warehouse.postgres-main"), ("warehouse", "postgres-main"))

    def test_missing_dot_returns_none(self):
        self.assertIsNone(parse_contract_ref("no-dot-here"))

    def test_empty_module_or_contract_returns_none(self):
        self.assertIsNone(parse_contract_ref(".contract"))
        self.assertIsNone(parse_contract_ref("module."))


class ResolvePathTest(unittest.TestCase):
    def test_resolves_nested_path(self):
        obj = {"a": {"b": {"c": 42}}}
        self.assertEqual(resolve_path(obj, "a.b.c"), 42)

    def test_missing_key_raises_key_error(self):
        with self.assertRaises(KeyError):
            resolve_path({"a": {}}, "a.b")

    def test_non_dict_intermediate_raises_key_error(self):
        with self.assertRaises(KeyError):
            resolve_path({"a": "not-a-dict"}, "a.b")


class SecretRefTest(unittest.TestCase):
    def test_is_secret_ref_true(self):
        self.assertTrue(is_secret_ref("secrets.db-password"))

    def test_is_secret_ref_false_for_bare_prefix(self):
        self.assertFalse(is_secret_ref("secrets."))

    def test_is_secret_ref_false_for_non_string_or_other_values(self):
        self.assertFalse(is_secret_ref(123))
        self.assertFalse(is_secret_ref("config.value"))

    def test_secret_name_from_ref(self):
        self.assertEqual(secret_name_from_ref("secrets.db-password"), "db-password")


if __name__ == "__main__":
    unittest.main()
