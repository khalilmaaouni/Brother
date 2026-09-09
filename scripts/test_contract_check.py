"""Calibration for scripts/contract_check.py, the checker for the one
outcome-contract record the door, Intake V2 and Daybook all read or write
(docs/schema/outcome-contract-v1.json, the 2026-09-08 debate judgment,
amended after the 2026-09-08 Opus adversarial review, findings F1 to F12).

Every case asserts the EXIT CODE, never only the printed line, mirroring
scripts/test_leaf_pin_check.py: a checker that prints FAIL and exits 0
manufactures a pass for every wrapper above it. NO-DATA (2) is asserted as
NOT 0 first and exactly 2 second, so a future refactor that merges NO-DATA
into FAIL's exit code is caught even if nobody re-reads the assertion order.

A structural test at the bottom (SchemaAndCheckerAgree) reads the schema
file directly and asserts that every field name contract_check.py's hand
rules mention by name is still a real node in the schema, so the schema
and the checker cannot silently drift apart on the vocabulary they share.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import contract_check as cc  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures", "outcome-contract")
SCHEMA = os.path.join(HERE, "..", "docs", "schema", "outcome-contract-v1.json")


def fixture(name):
    return os.path.join(FIXTURES, name + ".json")


def load_schema():
    with open(SCHEMA, encoding="utf-8") as fh:
        return json.load(fh)


def load_fixture(name):
    with open(fixture(name), encoding="utf-8") as fh:
        return json.load(fh)


def run_main(argv):
    """Return (exit_code, stdout)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cc.main(argv)
    return code, buf.getvalue()


class Fixtures(unittest.TestCase):

    def test_complete_record_passes(self):
        code, out = run_main([fixture("complete")])
        self.assertEqual(code, 0, out)
        self.assertIn("PASS", out)
        self.assertNotIn("FAIL", out)

    def test_no_project_names_the_field(self):
        code, out = run_main([fixture("no-project")])
        self.assertEqual(code, 1, out)
        self.assertIn("project", out)
        self.assertIn("FAIL", out)

    def test_no_question_names_the_field(self):
        code, out = run_main([fixture("no-question")])
        self.assertEqual(code, 1, out)
        self.assertIn("question", out)
        self.assertIn("empty", out)

    def test_missing_must_answer_why_names_the_field(self):
        code, out = run_main([fixture("missing-must-answer")])
        self.assertEqual(code, 1, out)
        self.assertIn("must_answer", out)
        self.assertIn("why", out)

    def test_audit_without_ticket_names_the_field(self):
        code, out = run_main([fixture("audit-without-ticket")])
        self.assertEqual(code, 1, out)
        self.assertIn("ticket", out)
        self.assertIn("audit.required", out)

    def test_two_questions_once_contracted_names_the_field(self):
        code, out = run_main([fixture("two-questions-contracted")])
        self.assertEqual(code, 1, out)
        self.assertIn("questions", out)
        self.assertIn("A-prime", out)

    def test_a_fail_line_never_lies_about_its_own_exit_code(self):
        """The lesson test_leaf_pin_check.py was written to encode: a FAIL
        string with exit 0 is worse than no check at all. Every fixture
        that prints FAIL here must also carry a nonzero exit."""
        for name in ("no-project", "no-question", "missing-must-answer",
                     "audit-without-ticket", "two-questions-contracted",
                     "contracted-without-checks"):
            code, out = run_main([fixture(name)])
            if "FAIL" in out:
                self.assertNotEqual(code, 0, "%s: %s" % (name, out))


class NoData(unittest.TestCase):

    def test_missing_record_file_is_no_data_not_a_failure(self):
        code, out = run_main(["/nonexistent-outcome-contract-record.json"])
        self.assertNotEqual(code, 0, out)
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)

    def test_missing_schema_file_is_no_data(self):
        code, out = run_main([fixture("complete"),
                              "--schema", "/nonexistent-schema.json"])
        self.assertNotEqual(code, 0, out)
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)

    def test_malformed_json_record_is_no_data(self):
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False) as fh:
            fh.write("{not json")
            path = fh.name
        try:
            code, out = run_main([path])
        finally:
            os.unlink(path)
        self.assertNotEqual(code, 0, out)
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)


class Amendments(unittest.TestCase):
    """The twelve findings from the 2026-09-08 Opus adversarial review,
    each driven by its own crafted fixture (F1, F4, F7, F8, F9, F10, F12).
    F2, F3, F5, F6 and F11 are covered by the classes below them, since
    they are structural, prose or data driven rather than fixture driven."""

    def test_delivered_uncited_answer_names_the_field_and_receipt_id(self):
        code, out = run_main([fixture("delivered-uncited-answer")])
        self.assertEqual(code, 1, out)
        self.assertIn("must_answer.ticket", out)
        self.assertIn("receipt_id", out)

    def test_delivered_dangling_receipt_names_the_field_and_receipt_id(self):
        code, out = run_main([fixture("delivered-dangling-receipt")])
        self.assertEqual(code, 1, out)
        self.assertIn("must_answer.ticket", out)
        self.assertIn("receipt_id", out)
        self.assertIn("no-such-receipt", out)

    def test_bad_receipt_ref_names_the_grammar(self):
        code, out = run_main([fixture("bad-receipt-ref")])
        self.assertEqual(code, 1, out)
        self.assertIn("ref", out)
        self.assertIn("file:", out)
        self.assertIn("evidence:", out)
        self.assertIn("url:", out)

    def test_provenance_for_absent_field_is_caught(self):
        code, out = run_main([fixture("provenance-for-absent-field")])
        self.assertEqual(code, 1, out)
        self.assertIn("extra_field", out)
        self.assertIn("not a field of project", out)

    def test_empty_language_is_caught(self):
        code, out = run_main([fixture("empty-language")])
        self.assertEqual(code, 1, out)
        self.assertIn("language", out)
        self.assertIn("empty", out)

    def test_contains_without_value_names_the_check(self):
        code, out = run_main([fixture("contains-without-value")])
        self.assertEqual(code, 1, out)
        self.assertIn("success_checks.sc-2", out)
        self.assertIn("value", out)

    def test_typo_key_names_the_unknown_field(self):
        code, out = run_main([fixture("typo-key")])
        self.assertEqual(code, 1, out)
        self.assertIn("sucess_checks", out)
        self.assertIn("unexpected field", out)
        self.assertIn("success_checks", out)
        self.assertIn("missing required field", out)

    def test_delivered_with_open_question_is_capped_at_zero(self):
        code, out = run_main([fixture("delivered-with-open-question")])
        self.assertEqual(code, 1, out)
        self.assertIn("questions", out)
        self.assertIn("delivered", out)

    def test_contracted_without_checks_names_the_field(self):
        """A schema correction found by U5: a draft may carry zero
        success_checks, but once state reaches the same pivot the
        questions cap reads (contracted), at least one is required."""
        code, out = run_main([fixture("contracted-without-checks")])
        self.assertEqual(code, 1, out)
        self.assertIn("success_checks", out)
        self.assertIn("contracted", out)

    def test_draft_without_checks_passes(self):
        """The other half of the same correction: a draft with zero
        success_checks and its one open question is a valid record, not
        a schema violation papered over with a NO-DATA placeholder."""
        code, out = run_main([fixture("draft-without-checks")])
        self.assertEqual(code, 0, out)
        self.assertIn("PASS", out)
        self.assertNotIn("FAIL", out)


class DecisionField(unittest.TestCase):
    """F2: decision is a required top level field, object or null, that
    never carries the decision record's own options, criteria or weights.
    """

    def test_decision_null_is_valid(self):
        record = load_fixture("complete")
        record["decision"] = None
        problems = cc.check(record, load_schema())
        self.assertEqual(problems, [], problems)

    def test_decision_missing_is_a_required_field_failure(self):
        record = load_fixture("complete")
        del record["decision"]
        problems = cc.check(record, load_schema())
        self.assertTrue(any("decision" in p for p in problems), problems)

    def test_decision_object_missing_close_call_is_caught(self):
        record = load_fixture("complete")
        record["decision"] = {"record": "docs/plan/decision.json"}
        problems = cc.check(record, load_schema())
        self.assertTrue(any("close_call" in p for p in problems), problems)

    def test_decision_object_with_extra_key_is_caught(self):
        record = load_fixture("complete")
        record["decision"] = {
            "record": "docs/plan/decision.json",
            "close_call": False,
            "criteria": ["cost", "speed"],
        }
        problems = cc.check(record, load_schema())
        self.assertTrue(any("criteria" in p for p in problems), problems)


class PersonaEnum(unittest.TestCase):
    """F3: persona is one of the three intake personas or NO-DATA."""

    def test_unknown_persona_value_is_rejected(self):
        record = load_fixture("complete")
        record["persona"] = "founder"
        problems = cc.check(record, load_schema())
        self.assertTrue(any("persona" in p for p in problems), problems)

    def test_no_data_persona_is_accepted(self):
        record = load_fixture("complete")
        record["persona"] = "NO-DATA"
        problems = cc.check(record, load_schema())
        self.assertEqual(problems, [], problems)


class PivotGuard(unittest.TestCase):
    """F5: the state pivot the questions cap reads (x-question-pivot) is
    guarded. A schema whose enum drops the pivot state FAILs cleanly,
    naming the missing pivot, never a traceback on stderr."""

    def test_renamed_pivot_state_fails_without_a_traceback(self):
        schema = load_schema()
        schema["properties"]["state"]["enum"] = [
            "draft", "planned", "in-flight", "delivered", "superseded",
        ]
        record = load_fixture("complete")
        record["state"] = "planned"

        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False) as sf:
            json.dump(schema, sf)
            schema_path = sf.name
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False) as rf:
            json.dump(record, rf)
            record_path = rf.name

        try:
            proc = subprocess.run(
                [sys.executable, os.path.join(HERE, "contract_check.py"),
                 record_path, "--schema", schema_path],
                capture_output=True, text=True)
        finally:
            os.unlink(schema_path)
            os.unlink(record_path)

        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("FAIL", proc.stdout)
        self.assertIn("pivot state", proc.stdout)
        self.assertEqual(proc.stderr, "", proc.stderr)

    def test_in_process_check_also_reports_the_missing_pivot(self):
        """Same scenario driven through cc.check() directly (no subprocess,
        no temp files), so the guard is proven both in process and as a
        real CLI run above."""
        schema = load_schema()
        schema["properties"]["state"]["enum"] = [
            "draft", "planned", "in-flight", "delivered", "superseded",
        ]
        record = load_fixture("complete")
        record["state"] = "planned"
        problems = cc.check(record, schema)
        self.assertTrue(
            any("pivot state" in p for p in problems), problems)


class RequiredFieldsDataDriven(unittest.TestCase):
    """F11: every top level required field, deleted in memory from the
    complete fixture, must be named in the resulting problem list. No new
    fixtures: this mutates the already-loaded complete record directly."""

    @classmethod
    def setUpClass(cls):
        cls.schema = load_schema()
        cls.complete = load_fixture("complete")

    def test_deleting_any_required_field_is_caught(self):
        for name in self.schema["required"]:
            record = dict(self.complete)
            del record[name]
            problems = cc.check(record, self.schema)
            self.assertTrue(
                any(name in p for p in problems),
                "deleting required field %r produced no problem naming "
                "it: %r" % (name, problems))


class SchemaAndCheckerAgree(unittest.TestCase):
    """The structural drift guard the brief asks for: every field name
    contract_check.py's hand rules (docstring and hand_rules()) mention by
    name must exist as a real node in the schema file, so the two files
    cannot quietly disagree about what these fields even are."""

    @classmethod
    def setUpClass(cls):
        cls.schema = load_schema()

    def test_schema_is_valid_json_with_the_expected_top_level_shape(self):
        self.assertEqual(self.schema.get("type"), "object")
        self.assertIn("properties", self.schema)

    def test_every_hand_enforced_top_level_field_is_in_the_schema(self):
        props = self.schema["properties"]
        for field in ("question", "language", "ticket", "audit", "state",
                     "questions", "must_answer", "receipts",
                     "success_checks", "project", "decision"):
            self.assertIn(field, props,
                          "%s is named in contract_check.py's hand rules "
                          "but missing from the schema" % field)

    def test_provenance_is_a_real_project_property(self):
        project_props = self.schema["properties"]["project"]["properties"]
        self.assertIn("provenance", project_props)

    def test_state_enum_matches_the_checkers_fallback_order(self):
        """hand_rules() reads the state order from the schema's own enum
        (_state_order), falling back to a fixed list only if that node is
        missing. If the schema's enum ever changes, this fails loudly
        rather than the two silently disagreeing on what "contracted or
        later" means."""
        self.assertEqual(self.schema["properties"]["state"]["enum"],
                         cc.STATE_ORDER_FALLBACK)

    def test_question_pivot_is_a_real_state_in_the_enum(self):
        """F5: x-question-pivot must itself be a value the state enum
        carries, or the guard would fire on every single run."""
        pivot = self.schema.get("x-question-pivot", cc.QUESTION_PIVOT_DEFAULT)
        self.assertIn(pivot, self.schema["properties"]["state"]["enum"])

    def test_receipt_ref_prefixes_are_named_in_the_schema_description(self):
        """F4: the grammar the checker hard codes (RECEIPT_REF_PREFIXES)
        must be the same one the schema's own description promises."""
        ref_desc = (self.schema["properties"]["receipts"]["items"]
                    ["properties"]["ref"]["description"])
        for prefix in cc.RECEIPT_REF_PREFIXES:
            self.assertIn(prefix, ref_desc)

    def test_contract_check_reads_this_schema_by_default(self):
        self.assertEqual(os.path.normpath(cc.DEFAULT_SCHEMA),
                         os.path.normpath(os.path.abspath(SCHEMA)))


class ReadmeDocumentsTheStateMapping(unittest.TestCase):
    """F3/F6: the orchestrator ruling that Daybook's four display columns
    are derived, never stored, must be written into the README, not only
    decided in a chat transcript."""

    def test_readme_carries_the_state_and_daybook_heading(self):
        readme = os.path.join(HERE, "..", "docs", "schema", "README.md")
        with open(readme, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("State and the Daybook columns", text)
        for column in ("open", "close-call", "decided", "superseded"):
            self.assertIn(column, text)


if __name__ == "__main__":
    unittest.main()
