#!/usr/bin/env python3
"""Calibration for tools/bm_vault_enrich_gate.py, WBS VB13-05: the enrichment
draft gate and golden-set evals.

The property under test is the row's own observable: a schema-violating draft
(out-of-enum value, out-of-range confidence) never queues, writing nothing; a
prompt-version bump against a failing golden set refuses, leaving whatever
version was blessed before untouched; a promoted draft's model, prompt
version and confidence land in per-attribute provenance through that
module's own writer; the review queue orders by confidence ascending; and a
missing golden file is NO-DATA, never a silent pass.

No em or en dashes anywhere in this file.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_enrich_gate as gate      # noqa: E402
import bm_vault_lifecycle as lc          # noqa: E402
import bm_vault_promotions as promo      # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

GOOD_RECORD = {"legal_name": "TOYOTA FINANCE AUSTRALIA LIMITED", "country": "AU",
               "entity_status": "ACTIVE", "registration_status": "ISSUED"}

GOLDENS_FIXTURE = os.path.join(HERE, "fixtures", "enrich-goldens.json")


def note(body="a target note"):
    return "\n".join(["---", "type: reference", "status: open", "---", "",
                       "# %s" % body, ""])


class GateFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-enrich-gate-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        with open(os.path.join(self.vault, "target.md"), "w", encoding="utf-8") as fh:
            fh.write(note())
        self.state_path = os.path.join(self.tmp, "gate_state.json")
        self.store_path = os.path.join(self.tmp, "provenance.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _inbox(self):
        d = os.path.join(self.vault, "00-Inbox")
        return set(os.listdir(d)) if os.path.isdir(d) else set()


class ASchemaViolatingDraftNeverQueues(GateFixture):
    def test_out_of_enum_value_refuses_and_writes_nothing(self):
        before = self._inbox()
        bad = dict(GOOD_RECORD, country="ZZ")  # ZZ is not in the declared enum
        ok, message, rel = gate.file_extraction_draft(
            self.vault, "target.md", bad, "test-model", "v1", 0.5)
        self.assertFalse(ok)
        self.assertIn("REFUSED: schema violation", message)
        self.assertIn("country", message)
        self.assertIsNone(rel)
        self.assertEqual(before, self._inbox(), "a refused draft must write nothing")

    def test_out_of_range_confidence_refuses_and_writes_nothing(self):
        before = self._inbox()
        ok, message, rel = gate.file_extraction_draft(
            self.vault, "target.md", GOOD_RECORD, "test-model", "v1", 1.5)
        self.assertFalse(ok)
        self.assertIn("REFUSED", message)
        self.assertIn("range", message)
        self.assertIsNone(rel)
        self.assertEqual(before, self._inbox())

    def test_missing_required_attribute_refuses(self):
        incomplete = {"legal_name": "X"}
        ok, message, rel = gate.file_extraction_draft(
            self.vault, "target.md", incomplete, "test-model", "v1", 0.5)
        self.assertFalse(ok)
        self.assertIn("REFUSED: schema violation", message)
        self.assertIsNone(rel)

    def test_empty_prompt_version_refuses(self):
        ok, message, rel = gate.file_extraction_draft(
            self.vault, "target.md", GOOD_RECORD, "test-model", "", 0.5)
        self.assertFalse(ok)
        self.assertIn("REFUSED", message)
        self.assertIsNone(rel)

    def test_a_conforming_draft_queues_with_model_and_prompt_version_and_confidence(self):
        ok, message, rel = gate.file_extraction_draft(
            self.vault, "target.md", GOOD_RECORD, "test-model", "v1", 0.42)
        self.assertTrue(ok, message)
        self.assertTrue(rel.startswith("00-Inbox/"), rel)
        with open(os.path.join(self.vault, rel), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("drafting_model: test-model", text)
        self.assertIn("prompt_version: v1", text)
        self.assertIn("confidence: 0.42", text)
        state, _record, problems = lc.read_promotion(text)
        self.assertEqual(state, "candidate")
        self.assertEqual(problems, [])


class CALIBRATION_the_schema_gate_actually_gates(GateFixture):
    """Break validate_extraction to accept everything and watch the
    out-of-enum test above fail, proving that test exercises the real gate."""

    def test_breaking_validate_extraction_lets_the_bad_value_through(self):
        real = gate.validate_extraction
        gate.validate_extraction = lambda record: (True, None)
        try:
            bad = dict(GOOD_RECORD, country="ZZ")
            ok, _message, rel = gate.file_extraction_draft(
                self.vault, "target.md", bad, "test-model", "v1", 0.5)
            self.assertTrue(ok, "with the schema gate broken the out-of-enum "
                             "value must be admitted, proving the real gate bites")
            self.assertIsNotNone(rel)
        finally:
            gate.validate_extraction = real


class TheGoldenSetGatesAPromptVersionBump(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-enrich-gate-goldens-")
        self.state_path = os.path.join(self.tmp, "gate_state.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _doctored_copy(self):
        with open(GOLDENS_FIXTURE, encoding="utf-8") as fh:
            data = json.load(fh)
        data["cases"][0]["expected"]["country"] = "ZZ"
        path = os.path.join(self.tmp, "doctored-goldens.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return path

    def test_the_checked_in_golden_set_passes_and_blesses_the_version(self):
        ok, lines = gate.bump_prompt_version(GOLDENS_FIXTURE, "v1", self.state_path)
        self.assertTrue(ok, "\n".join(lines))
        self.assertTrue(any("BLESSED" in line for line in lines))
        state = gate.load_state(self.state_path)
        self.assertEqual(state["blessed_prompt_version"], "v1")

    def test_a_doctored_expected_output_fails_naming_the_case(self):
        doctored = self._doctored_copy()
        ok, lines = gate.bump_prompt_version(doctored, "v1", self.state_path)
        self.assertFalse(ok)
        self.assertTrue(any("[FAIL] 5493006W3QUS5LMH6R84" in line for line in lines))

    def test_a_bump_against_a_failing_golden_set_refuses_and_leaves_blessed_version_unchanged(self):
        ok, _lines = gate.bump_prompt_version(GOLDENS_FIXTURE, "v1", self.state_path)
        self.assertTrue(ok)
        doctored = self._doctored_copy()
        ok, lines = gate.bump_prompt_version(doctored, "v2", self.state_path)
        self.assertFalse(ok)
        self.assertTrue(any("REFUSED" in line and "v2" in line for line in lines))
        state = gate.load_state(self.state_path)
        self.assertEqual(state["blessed_prompt_version"], "v1",
                          "the failing bump to v2 must not disturb the blessed v1")

    def test_missing_golden_file_is_no_data_never_a_pass(self):
        ok, lines = gate.bump_prompt_version(
            os.path.join(self.tmp, "no-such-file.json"), "v1", self.state_path)
        self.assertIsNone(ok)
        self.assertTrue(any(line.startswith("NO-DATA") for line in lines))


class CALIBRATION_the_golden_runner_actually_compares(unittest.TestCase):
    """Break extract() to always match; the doctored-expected-output test
    above must then fail, proving it exercises the real comparison."""

    def test_breaking_extract_hides_the_doctored_mismatch(self):
        tmp = tempfile.mkdtemp(prefix="bm-enrich-gate-calib-")
        try:
            with open(GOLDENS_FIXTURE, encoding="utf-8") as fh:
                data = json.load(fh)
            # Only ONE case survives here, deliberately: the lambda below can
            # only cheat by returning a fixed dict regardless of input, and a
            # second case with a different expected value would then mismatch
            # for an unrelated reason, muddying what this calibration proves.
            only_case = data["cases"][0]
            only_case["expected"]["country"] = "ZZ"
            data["cases"] = [only_case]
            doctored = os.path.join(tmp, "doctored.json")
            with open(doctored, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            real = gate.extract
            gate.extract = lambda raw: only_case["expected"]
            try:
                ok, _lines = gate.run_goldens(doctored)
                self.assertTrue(ok, "with extract() broken to always match, the "
                                 "doctored mismatch is hidden, proving the real "
                                 "comparison bites in the unbroken test")
            finally:
                gate.extract = real
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class APromotedDraftCarriesModelPromptVersionAndConfidenceIntoProvenance(GateFixture):
    def test_carry_provenance_after_promotion(self):
        ok, _message, rel = gate.file_extraction_draft(
            self.vault, "target.md", GOOD_RECORD, "vendor-model-7", "v3", 0.81)
        self.assertTrue(ok)
        rc = promo.cmd_promote(self.vault, rel, "validated", "khalil",
                                "2026-08-30", apply_changes=True)
        self.assertEqual(rc, 0)
        rc = promo.cmd_promote(self.vault, rel, "canonical", "khalil",
                                "2026-08-30", apply_changes=True)
        self.assertEqual(rc, 0)

        ok, message, record = gate.carry_provenance(self.vault, rel, store_path=self.store_path)
        self.assertTrue(ok, message)
        self.assertEqual(record["note"], "target.md")
        self.assertEqual(record["attribute"], "entity_extract")
        self.assertEqual(record["confidence"], 0.81)
        self.assertEqual(record["verification_status"], "unverified")
        self.assertIn("vendor-model-7", record["source"])
        self.assertIn("v3", record["source"])

        import bm_vault_attribute_provenance as attrprov
        data = attrprov.load_store(self.store_path)
        latest = attrprov.latest_record(data["records"], "target.md", "entity_extract")
        self.assertIsNotNone(latest)
        self.assertIn("vendor-model-7", latest["source"])
        self.assertIn("v3", latest["source"])
        self.assertEqual(latest["confidence"], 0.81)

    def test_an_unpromoted_draft_refuses_to_carry(self):
        ok, _message, rel = gate.file_extraction_draft(
            self.vault, "target.md", GOOD_RECORD, "test-model", "v1", 0.5)
        self.assertTrue(ok)
        ok, message, record = gate.carry_provenance(self.vault, rel, store_path=self.store_path)
        self.assertFalse(ok)
        self.assertTrue(message.startswith("REFUSED"))
        self.assertIsNone(record)


class TheReviewQueueOrdersByConfidenceAscending(GateFixture):
    def test_three_drafts_list_least_confident_first(self):
        for model, confidence in (("model-a", 0.9), ("model-b", 0.3), ("model-c", 0.6)):
            ok, message, _rel = gate.file_extraction_draft(
                self.vault, "target.md", GOOD_RECORD, model, "v1", confidence)
            self.assertTrue(ok, message)
        rows = gate.queue_drafts(self.vault)
        self.assertEqual([r["confidence"] for r in rows], [0.3, 0.6, 0.9])
        self.assertEqual([r["model"] for r in rows], ["model-b", "model-c", "model-a"])

    def test_unresolvable_note_is_no_data(self):
        rows = gate.queue_drafts(self.vault, note_ident="no-such-note.md")
        self.assertIsNone(rows)


if __name__ == "__main__":
    unittest.main()
