#!/usr/bin/env python3
"""Calibration for scripts/coe_registry.py.

The property this file exists to assert is not that the shipped
docs/plan/COE-SEATS.json loads (any loader can be made to do that on the
one file it was written against), it is that the bad states a permissive
loader would ALSO let through are each refused: a seat owning an attribute
outside KNOWN_ATTRIBUTES, a seat missing a required field, an unknown
effort tier, a criterion id outside C1 to C12 (including one with stray
whitespace), two seats sharing one id, a registry file that is empty,
corrupt, or not a JSON list at all. TestEnumsMatchRegistry guards the
other failure mode: docs/schema/coe-seat-v1.json's enums and this module's
own KNOWN_EFFORTS / KNOWN_CRITERIA sets silently drifting apart, mirroring
scripts/test_orchestrator_protocol.py's TestEnumsMatchInvariants for
orchestrator-task-v1.json.

TestSeatsOwningDistinguishesEmptyFromError is the test named directly by
rule e of this unit's brief: seats_owning() on a KNOWN attribute nobody
currently owns must return an empty list (real data, not a defect), while
seats_owning() on an attribute this registry has never heard of must
raise. A test that only checked one of the two could not tell a loader
that conflates them from one that does not.
"""
import ast
import copy
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import coe_registry as reg  # noqa: E402
import orchestrator_protocol as op  # noqa: E402


def _write_registry(seats):
    """A temp file holding `seats` (a Python list, already JSON-able) as
    the registry document, returning its path. The caller is responsible
    for deleting it; tests do so in a finally block so a failing
    assertion never leaks a temp file.
    """
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8")
    try:
        json.dump(seats, handle)
    finally:
        handle.close()
    return handle.name


def _write_raw(text):
    """A temp file holding the literal string `text`, for the JSON that
    is not even syntactically valid, or not valid at all (the zero byte
    case)."""
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8")
    try:
        handle.write(text)
    finally:
        handle.close()
    return handle.name


def _one_valid_seat(seat_id="architect"):
    """A single schema-valid seat dict, deep-copied fresh on every call so
    a test can mutate its own copy without affecting another test."""
    return copy.deepcopy({
        "id": seat_id,
        "owns_attributes": ["duplicate-truth"],
        "model": "opus",
        "effort": "high",
        "outside": False,
        "cost_class": "high",
        "authoritative_on": ["C5"],
    })


class TestDefaultRegistryLoads(unittest.TestCase):
    """The shipped docs/plan/COE-SEATS.json, exactly as COE-01's brief
    names it: eight seats, the two marked outside still marked outside,
    none invented, none dropped.
    """

    EXPECTED_IDS = frozenset((
        "architect", "assurance", "operations", "evidence", "owner-proxy",
        "security", "adversary-outside", "bulk-classifier",
    ))
    EXPECTED_OUTSIDE_IDS = frozenset(("adversary-outside", "bulk-classifier"))

    def test_loads_exactly_the_eight_named_seats(self):
        seats = reg.load_seats()
        self.assertEqual({s["id"] for s in seats}, self.EXPECTED_IDS)

    def test_outside_seats_are_the_two_named_and_no_others(self):
        seats = reg.load_seats()
        outside_ids = {s["id"] for s in seats if s["outside"]}
        self.assertEqual(outside_ids, self.EXPECTED_OUTSIDE_IDS)

    def test_seat_returns_the_named_seat(self):
        self.assertEqual(reg.seat("security")["id"], "security")

    def test_seat_unknown_id_raises_never_returns_none(self):
        with self.assertRaises(reg.RegistryError):
            reg.seat("no-such-seat-id")

    def test_seats_owning_returns_the_owning_seats(self):
        owners = reg.seats_owning("privacy")
        self.assertEqual([s["id"] for s in owners], ["security"])


class TestSchemaEnumsMatchRegistry(unittest.TestCase):
    # The schema's own enums and this module's KNOWN_EFFORTS /
    # KNOWN_CRITERIA sets must never be able to drift apart silently.
    def test_effort_enum_equals_known_efforts(self):
        schema = op.load_schema(reg.SCHEMA_NAME)
        enum_values = set(schema["properties"]["effort"]["enum"])
        self.assertEqual(enum_values, set(reg.KNOWN_EFFORTS))

    def test_authoritative_on_enum_equals_known_criteria(self):
        schema = op.load_schema(reg.SCHEMA_NAME)
        item_enum = schema["properties"]["authoritative_on"]["items"]["enum"]
        self.assertEqual(set(item_enum), set(reg.KNOWN_CRITERIA))


class TestUnknownAttributeIsRefused(unittest.TestCase):
    """Rule a: an unknown attribute is REFUSED, never ignored, and the
    error names the attribute."""

    def test_unknown_attribute_refuses_whole_registry(self):
        seat = _one_valid_seat()
        seat["owns_attributes"] = ["warp-drive-calibration"]
        path = _write_registry([seat])
        try:
            with self.assertRaises(reg.RegistryError) as ctx:
                reg.load_seats(path)
            self.assertIn("warp-drive-calibration", str(ctx.exception))
        finally:
            os.unlink(path)


class TestMissingRequiredFieldIsRefused(unittest.TestCase):
    """Rule b: a seat missing a required field is REFUSED."""

    def test_missing_cost_class_refused(self):
        seat = _one_valid_seat()
        del seat["cost_class"]
        path = _write_registry([seat])
        try:
            with self.assertRaises(reg.RegistryError):
                reg.load_seats(path)
        finally:
            os.unlink(path)

    def test_missing_owns_attributes_refused(self):
        seat = _one_valid_seat()
        del seat["owns_attributes"]
        path = _write_registry([seat])
        try:
            with self.assertRaises(reg.RegistryError):
                reg.load_seats(path)
        finally:
            os.unlink(path)


class TestUnknownEffortIsRefused(unittest.TestCase):
    def test_unknown_effort_refused(self):
        seat = _one_valid_seat()
        seat["effort"] = "ultra-mega"
        path = _write_registry([seat])
        try:
            with self.assertRaises(reg.RegistryError):
                reg.load_seats(path)
        finally:
            os.unlink(path)


class TestBadCriterionIdIsRefused(unittest.TestCase):
    """Rule f: authoritative_on entries must be real criterion ids. A
    typo like C13, or a value carrying stray whitespace like 'C5 ', is
    refused, never matched loosely."""

    def test_out_of_range_criterion_refused(self):
        seat = _one_valid_seat()
        seat["authoritative_on"] = ["C13"]
        path = _write_registry([seat])
        try:
            with self.assertRaises(reg.RegistryError):
                reg.load_seats(path)
        finally:
            os.unlink(path)

    def test_trailing_whitespace_criterion_refused(self):
        seat = _one_valid_seat()
        seat["authoritative_on"] = ["C5 "]
        path = _write_registry([seat])
        try:
            with self.assertRaises(reg.RegistryError):
                reg.load_seats(path)
        finally:
            os.unlink(path)


class TestAdditionalPropertyIsRefused(unittest.TestCase):
    def test_unexpected_field_refused(self):
        seat = _one_valid_seat()
        seat["extra_field_nobody_declared"] = True
        path = _write_registry([seat])
        try:
            with self.assertRaises(reg.RegistryError):
                reg.load_seats(path)
        finally:
            os.unlink(path)


class TestSeatUnknownIdRaisesNeverNone(unittest.TestCase):
    """Rule c: seat() on an unknown id RAISES. Checked again here, on a
    controlled one-seat temp registry, so this property does not depend
    on what happens to be in the shipped COE-SEATS.json today."""

    def test_raises_on_temp_registry(self):
        path = _write_registry([_one_valid_seat()])
        try:
            with self.assertRaises(reg.RegistryError):
                reg.seat("nonexistent", path)
        finally:
            os.unlink(path)


class TestNoNetworkNoOutsideCall(unittest.TestCase):
    """Rule d: the registry loads with NO network call and NO outside
    model call. Enforced by reading this module's own source and its
    import statements, rather than by trying to sandbox a real network
    stack: a static check of what the module is even capable of importing
    is a stronger guarantee than a dynamic one that only proves nothing
    was called on this particular run.
    """

    def test_imports_are_limited_to_the_declared_local_set(self):
        source_path = os.path.join(HERE, "coe_registry.py")
        with open(source_path, encoding="utf-8") as handle:
            source = handle.read()
        tree = ast.parse(source, filename=source_path)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        allowed = {"json", "os", "orchestrator_protocol"}
        self.assertTrue(
            imported.issubset(allowed),
            "coe_registry.py imports %r outside the allowed set %r"
            % (imported - allowed, allowed))

    def test_source_never_names_a_networking_primitive(self):
        source_path = os.path.join(HERE, "coe_registry.py")
        with open(source_path, encoding="utf-8") as handle:
            source = handle.read()
        for forbidden in ("socket", "urllib", "http.client", "requests",
                           "aiohttp", "urlopen", "httpx"):
            self.assertNotIn(forbidden, source,
                              "coe_registry.py's source mentions %r" % forbidden)


class TestEdgeList(unittest.TestCase):
    """The edge list this unit's brief names explicitly, beyond the ones
    already exercised above (a one-seat registry, duplicate ids, an empty
    model field are covered by the other test classes and by this one's
    own cases)."""

    def test_missing_file_raises(self):
        with self.assertRaises(reg.RegistryError):
            reg.load_seats("/tmp/coe-registry-does-not-exist-anywhere.json")

    def test_zero_byte_file_raises(self):
        path = _write_raw("")
        try:
            with self.assertRaises(reg.RegistryError):
                reg.load_seats(path)
        finally:
            os.unlink(path)

    def test_corrupt_json_raises(self):
        path = _write_raw("{ this is not json at all")
        try:
            with self.assertRaises(reg.RegistryError):
                reg.load_seats(path)
        finally:
            os.unlink(path)

    def test_empty_list_raises(self):
        path = _write_registry([])
        try:
            with self.assertRaises(reg.RegistryError):
                reg.load_seats(path)
        finally:
            os.unlink(path)

    def test_valid_json_but_not_a_list_raises(self):
        path = _write_raw(json.dumps({"id": "not-a-list-at-all"}))
        try:
            with self.assertRaises(reg.RegistryError):
                reg.load_seats(path)
        finally:
            os.unlink(path)

    def test_one_seat_registry_loads(self):
        path = _write_registry([_one_valid_seat()])
        try:
            seats = reg.load_seats(path)
            self.assertEqual(len(seats), 1)
            self.assertEqual(seats[0]["id"], "architect")
        finally:
            os.unlink(path)

    def test_duplicate_seat_ids_raise(self):
        path = _write_registry([_one_valid_seat("dup"), _one_valid_seat("dup")])
        try:
            with self.assertRaises(reg.RegistryError) as ctx:
                reg.load_seats(path)
            self.assertIn("dup", str(ctx.exception))
        finally:
            os.unlink(path)

    def test_empty_model_field_raises(self):
        seat = _one_valid_seat()
        seat["model"] = ""
        path = _write_registry([seat])
        try:
            with self.assertRaises(reg.RegistryError):
                reg.load_seats(path)
        finally:
            os.unlink(path)


class TestSeatsOwningDistinguishesEmptyFromError(unittest.TestCase):
    """Rule e: seats_owning() on an attribute no seat owns returns an
    EMPTY LIST; seats_owning() on an attribute this registry has never
    heard of RAISES. The two must not be reachable by the same code path
    with the same result, or a caller cannot tell a real coverage gap
    from its own typo."""

    def test_known_attribute_with_no_current_owner_returns_empty_list(self):
        # A one-seat registry that owns nothing touching "privacy":
        # "privacy" is still a member of KNOWN_ATTRIBUTES module wide
        # (security owns it in the shipped registry), but this temp
        # registry has no seat that claims it.
        path = _write_registry([_one_valid_seat()])
        try:
            owners = reg.seats_owning("privacy", path)
            self.assertEqual(owners, [])
        finally:
            os.unlink(path)

    def test_unknown_attribute_raises_rather_than_returning_empty(self):
        path = _write_registry([_one_valid_seat()])
        try:
            with self.assertRaises(reg.RegistryError):
                reg.seats_owning("an-attribute-nobody-ever-declared", path)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
