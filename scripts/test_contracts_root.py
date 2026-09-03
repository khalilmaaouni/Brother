"""Parity check for `contracts/`, the root the charter names as the seam.

Two things this checks, and they are checked differently on purpose
(`contracts/README.md` explains why):

  1. The two schema files in `contracts/` are byte-identical to the product
     files they mirror. Data, so byte-identity is the right bar.
  2. The Python modules that hand-roll the change passport's field rules
     (`bm_passport_validator.py` in BrotherMode, `sbe_passport.py` in
     BrotherSBE) name the same five fields and the same schema marker that
     `contracts/change-passport.v1.json` requires. Code, not data, so the
     bar is "the field set matches", not byte-identity.

Run: python3 scripts/test_contracts_root.py -v
"""
import importlib.util
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACTS = os.path.join(ROOT, "contracts")


def _p(*parts):
    return os.path.join(ROOT, *parts)


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestByteIdentity(unittest.TestCase):
    def test_change_passport_matches_the_brothermode_original(self):
        root_copy = _read(_p("contracts", "change-passport.v1.json"))
        original = _read(_p("products", "brothermode", "schema", "change-passport.v1.json"))
        self.assertEqual(
            root_copy, original,
            "contracts/change-passport.v1.json has drifted from "
            "products/brothermode/schema/change-passport.v1.json; the two "
            "must match byte for byte (contracts/README.md states the rule)",
        )

    def test_handoff_package_matches_the_brothersbe_original(self):
        root_copy = _read(_p("contracts", "handoff-package.v1.json"))
        original = _read(_p("products", "brothersbe", "contracts", "handoff-package.v1.json"))
        self.assertEqual(
            root_copy, original,
            "contracts/handoff-package.v1.json has drifted from "
            "products/brothersbe/contracts/handoff-package.v1.json; the two "
            "must match byte for byte (contracts/README.md states the rule)",
        )


class TestPassportModulesMatchTheSchema(unittest.TestCase):
    """The two hand-rolled Python readers of the change passport, checked
    against the root schema's own required field set rather than against
    each other, so a schema edit is the one place that has to be right."""

    @classmethod
    def setUpClass(cls):
        with open(_p("contracts", "change-passport.v1.json"), "r", encoding="utf-8") as fh:
            cls.schema = json.load(fh)
        cls.expected_schema_marker = cls.schema["properties"]["schema"]["const"]
        cls.required = set(cls.schema["required"])

        cls.sbe_passport = _load_module(
            "sbe_passport_for_contracts_test",
            _p("products", "brothersbe", "tools", "sbe_passport.py"),
        )
        cls.bm_validator = _load_module(
            "bm_passport_validator_for_contracts_test",
            _p("products", "brothermode", "tools", "bm_passport_validator.py"),
        )

    def test_sbe_passport_field_names_are_all_required_by_the_schema(self):
        sbe_fields = {name for (_, _, name, _) in self.sbe_passport.FIELDS}
        self.assertTrue(
            sbe_fields.issubset(self.required),
            "sbe_passport.py's FIELDS names %r, not all of which the root "
            "schema requires (%r)" % (sbe_fields, self.required),
        )

    def test_bm_validator_field_names_are_all_required_by_the_schema(self):
        bm_fields = {key for (_, key) in self.bm_validator._CONSUMER_KEYS}
        self.assertTrue(
            bm_fields.issubset(self.required),
            "bm_passport_validator.py's _CONSUMER_KEYS names %r, not all of "
            "which the root schema requires (%r)" % (bm_fields, self.required),
        )

    def test_both_modules_name_the_same_five_fields(self):
        sbe_fields = {name for (_, _, name, _) in self.sbe_passport.FIELDS}
        bm_fields = {key for (_, key) in self.bm_validator._CONSUMER_KEYS}
        self.assertEqual(
            sbe_fields, bm_fields,
            "the producer and consumer sides of the passport disagree on "
            "which fields the chain names",
        )

    def test_sbe_passport_schema_marker_matches_the_schema(self):
        self.assertEqual(self.sbe_passport.SCHEMA_V1, self.expected_schema_marker)

    def test_bm_validator_schema_marker_matches_the_schema(self):
        self.assertEqual(self.bm_validator._SCHEMA_VALUE, self.expected_schema_marker)


if __name__ == "__main__":
    unittest.main()
