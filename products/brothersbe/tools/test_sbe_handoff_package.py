#!/usr/bin/env python3
"""Tests for the handoff package wire format (`contracts/handoff-package.v1.json`)
against its own fixture (`contracts/examples/handoff-package.example.json`).

Run: python3 tools/test_sbe_handoff_package.py

This is a DATA CONTRACT, not a producer: nothing in this repository emits a
handoff package yet, the same position `tools/test_sbe_contracts.py`'s own BAND K
section is in for the six lifecycle objects it fixtures by hand rather than
captures live. The fixture here is hand built for the same reason and is named as
the weaker fixture it is: these tests prove the SCHEMA and its hand-written reader
refuse what they must refuse, and they prove nothing about what a real producer in
a sibling repository will actually write.

Stdlib only, on purpose, mirroring the constraint the schema file itself states in
its own top-level description: `jsonschema` is not installed and this repository
declares no third party dependencies, so the reader below is a small, explicit,
hand-written walk of the JSON Schema vocabulary this schema actually uses (`type`,
`required`, `properties`, `items`, `minItems`, `enum`), not a general-purpose
validator. `brothersbe.contracts` was checked first for an existing helper that
already does this job (`_missing_fields`, `_shape_problem`, `_verdict`); none of
the three walk nested `properties`/`items` the way this schema needs, and all
three are private to that module, so this file mirrors their SHAPE (the same
three-value `(verdict, evidence, problems)` return every `validate_*` function in
that module already uses) rather than reaching into it.

CALIBRATION, the same discipline `tools/test_sbe_contracts.py` already holds to:
every mutation test changes exactly ONE thing on a `copy.deepcopy` of the fixture,
and re-validates the UN-MUTATED fixture as PASS in the same test, so a green here
comes from the mutation moving the verdict and never from a reader that always
says FAIL.
"""
import copy
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SCHEMA_PATH = os.path.join(ROOT, "contracts", "handoff-package.v1.json")
FIXTURE_PATH = os.path.join(ROOT, "contracts", "examples", "handoff-package.example.json")


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _blank(value):
    """A string that records nothing once whitespace is stripped away. Mirrors
    the "present but answers nothing" reading `brothersbe.contracts.answered`
    (by way of `tools/sbe_checks.py:answered`) already holds every other surface
    in this repository to: a field that is there but blank identifies nothing
    while looking like it does, which this schema's own field descriptions name
    as worse than an honest NO-DATA marker."""
    return isinstance(value, str) and value.strip() == ""


def _validate_node(value, node, path, problems):
    """Walk one JSON Schema node against one value, appending a named problem
    string per failure found (never stopping at the first one, so a reader
    gets every named gap in one pass, the same "collect, do not short circuit"
    shape `brothersbe.contracts.release_binds` already uses).

    Covers exactly the vocabulary `contracts/handoff-package.v1.json` uses
    (`type` of `object`/`array`/`string`, `required`, `properties`, `items`,
    `minItems`, `enum`) and nothing wider: this is a reader built for one
    schema, not a general JSON Schema engine, stated here so nobody mistakes
    it for one later.
    """
    node_type = node.get("type")
    if node_type == "object":
        if not isinstance(value, dict):
            problems.append("%s is %s, not an object" % (path, type(value).__name__))
            return
        for name in node.get("required", ()):
            if name not in value:
                problems.append("%s is missing required field %r" % (path, name))
        for name, subnode in node.get("properties", {}).items():
            if name in value:
                _validate_node(value[name], subnode, "%s.%s" % (path, name), problems)
    elif node_type == "array":
        if not isinstance(value, list):
            problems.append("%s is %s, not an array" % (path, type(value).__name__))
            return
        min_items = node.get("minItems")
        if min_items is not None and len(value) < min_items:
            problems.append("%s has %d item(s), fewer than the required minimum of %d"
                            % (path, len(value), min_items))
        items_schema = node.get("items")
        if items_schema is not None:
            for index, item in enumerate(value):
                _validate_node(item, items_schema, "%s[%d]" % (path, index), problems)
    elif node_type == "string":
        if not isinstance(value, str):
            problems.append("%s is %s, not a string" % (path, type(value).__name__))
            return
        enum = node.get("enum")
        if enum is not None and value not in enum:
            problems.append("%s value %r is not one of %s" % (path, value, enum))
        if _blank(value):
            problems.append("%s is present but blank; a field that records nothing is "
                            "never a silent pass" % path)
    # No other `type` appears anywhere in this schema today; a node this reader
    # does not recognize is silently unchecked rather than refused, which is an
    # accepted, narrower gap than a false refusal (the same trade-off this
    # module's own docstring names for why it is not a general validator), and
    # a new `type` added to the schema needs this function extended by name.


def validate_handoff_package(document, schema):
    """(verdict, evidence, problems): the house 3-tuple every `validate_*`
    function in `brothersbe.contracts` returns, mirrored here rather than
    imported (see the module docstring for why). `NO-DATA` means no document
    was given at all; a document that IS given but is the wrong shape, or is
    missing a field, or names an unknown `schemaVersion`, is `FAIL`, never
    `NO-DATA`, the same "absence and a broken claim are different findings"
    rule `evals/test_no_data_class.py` already fixes for every other check in
    this project."""
    if document is None:
        return "NO-DATA", "no handoff package document was given to validate", ()
    if not isinstance(document, dict):
        problem = ("the handoff package document is a JSON %s, not an object at the "
                  "top level" % type(document).__name__)
        return "FAIL", problem, (problem,)
    problems = []
    _validate_node(document, schema, "$", problems)
    if not problems:
        return ("PASS", "schemaVersion %r, every required field present"
                % document.get("schemaVersion"), ())
    summary = "%d problem(s): %s" % (len(problems), "; ".join(problems))
    return "FAIL", summary, tuple(problems)


class HandoffPackageTests(unittest.TestCase):
    def setUp(self):
        self.schema = _load(SCHEMA_PATH)
        self.fixture = _load(FIXTURE_PATH)

    def revalidate_fixture_passes(self, label):
        """Calibration: the un-mutated fixture still validates PASS, checked
        inside the same test as a mutation, so a FAIL earned above is known to
        come from the mutation and not from a reader that always says FAIL."""
        verdict, evidence, _problems = validate_handoff_package(self.fixture, self.schema)
        self.assertEqual(verdict, "PASS", (label, evidence))

    def test_the_fixture_validates_against_its_own_schema(self):
        verdict, evidence, problems = validate_handoff_package(self.fixture, self.schema)
        self.assertEqual(verdict, "PASS", evidence)
        self.assertEqual(problems, ())

    def test_no_document_is_no_data_never_a_pass_and_never_a_fail(self):
        verdict, evidence, problems = validate_handoff_package(None, self.schema)
        self.assertEqual(verdict, "NO-DATA", evidence)
        self.assertEqual(problems, ())

    def test_a_missing_top_level_required_key_fails_for_every_one_of_them(self):
        for key in self.schema["required"]:
            mutated = copy.deepcopy(self.fixture)
            del mutated[key]
            verdict, evidence, _problems = validate_handoff_package(mutated, self.schema)
            self.assertEqual(verdict, "FAIL", (key, evidence))
            self.assertIn(key, evidence, key)
            self.revalidate_fixture_passes(key)

    def test_a_missing_sub_fact_fails_for_every_named_case_in_the_brief(self):
        # The five items, each by its own sub facts, per the founder decision
        # this schema was written against: grain, contract and snapshot id for
        # the prepared dataset; split for the harness; formula for a metric
        # definition; who and when for the labelled holdout.
        cases = (
            ("preparedDataset.grain", lambda doc: doc["preparedDataset"].pop("grain")),
            ("preparedDataset.contract", lambda doc: doc["preparedDataset"].pop("contract")),
            ("preparedDataset.snapshotId",
             lambda doc: doc["preparedDataset"].pop("snapshotId")),
            ("evaluationHarness.split", lambda doc: doc["evaluationHarness"].pop("split")),
            ("metricDefinitions[0].formula",
             lambda doc: doc["metricDefinitions"][0].pop("formula")),
            ("labelledHoldout.labelledWho",
             lambda doc: doc["labelledHoldout"].pop("labelledWho")),
            ("labelledHoldout.labelledWhen",
             lambda doc: doc["labelledHoldout"].pop("labelledWhen")),
        )
        for label, mutate in cases:
            mutated = copy.deepcopy(self.fixture)
            mutate(mutated)
            verdict, evidence, _problems = validate_handoff_package(mutated, self.schema)
            self.assertEqual(verdict, "FAIL", (label, evidence))
            self.revalidate_fixture_passes(label)

    def test_an_unknown_schema_version_is_refused_rather_than_parsed_hopefully(self):
        mutated = copy.deepcopy(self.fixture)
        mutated["schemaVersion"] = "9.9"
        verdict, evidence, _problems = validate_handoff_package(mutated, self.schema)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertIn("9.9", evidence)
        self.revalidate_fixture_passes("schemaVersion 9.9")

    def test_an_extra_unknown_field_still_passes_at_any_depth(self):
        mutated = copy.deepcopy(self.fixture)
        mutated["futureTopLevelField"] = "a later generation of this contract might add this"
        mutated["preparedDataset"]["futureNestedField"] = "same idea, one level down"
        verdict, evidence, problems = validate_handoff_package(mutated, self.schema)
        self.assertEqual(verdict, "PASS", (evidence, problems))
        self.revalidate_fixture_passes("extra unknown field")

    def test_a_present_but_blank_required_field_fails_even_though_it_is_present(self):
        # The difference this test exists for: a presence-only check would let
        # `"grain": "   "` through, because the key is there. It identifies no
        # row at all, and the schema's own description for this field says a
        # NO-DATA marker is the honest way to record "not yet known", never a
        # blank string dressed up as an answer.
        mutated = copy.deepcopy(self.fixture)
        mutated["preparedDataset"]["grain"] = "   "
        verdict, evidence, _problems = validate_handoff_package(mutated, self.schema)
        self.assertEqual(verdict, "FAIL", evidence)
        self.revalidate_fixture_passes("blank grain")

    def test_the_no_data_marker_itself_is_accepted_where_the_schema_allows_it(self):
        # The fixture already carries one real NO-DATA marker
        # (`metricDefinitions[1].formula`); this asserts the document as a
        # whole still passes with it in place, so the marker convention the
        # schema's descriptions promise is proven, not just stated.
        markers = [m for m in self.fixture["metricDefinitions"] if m["formula"] == "NO-DATA"]
        self.assertTrue(markers, "fixture should carry at least one real NO-DATA example")
        verdict, evidence, problems = validate_handoff_package(self.fixture, self.schema)
        self.assertEqual(verdict, "PASS", (evidence, problems))


if __name__ == "__main__":
    unittest.main()
