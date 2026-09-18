"""Proves closure_integrity.check refuses a fold or a wait proven only by a
sentence.

ORCH-20 and ORCH-25 were closed CANCELLED with disposition MERGED and prose
("carried by TOKEN-01", "folded into ORCH-19") and nothing else. Every test
here is written so it would fail if check() went back to trusting the
sentence: each one asserts the EXACT pinned set of findings, not just that
the list is non-empty, since a check that reports "something is wrong"
without naming what is exactly the kind of check that quietly stops firing.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import closure_integrity as C  # noqa: E402


def unit(uid, disposition=None, merged_into=None):
    u = {"id": uid, "title": "title for %s" % uid}
    if disposition is not None:
        u["disposition"] = disposition
    if merged_into is not None:
        u["merged_into"] = merged_into
    return u


def wbs(units):
    return {"units": units}


def status(entries):
    return {"units": entries}


def ids(findings):
    return sorted(f["id"] for f in findings)


class EmptyOrUnreadablePlanIsNoData(unittest.TestCase):
    def test_no_units_key_in_wbs_is_no_data(self):
        findings = C.check({}, status({}))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["id"], "PLAN")
        self.assertIn("NO-DATA", findings[0]["reason"])

    def test_empty_units_list_in_wbs_is_no_data(self):
        findings = C.check(wbs([]), status({}))
        self.assertEqual(len(findings), 1)
        self.assertIn("NO-DATA", findings[0]["reason"])

    def test_status_with_no_units_key_is_no_data(self):
        findings = C.check(wbs([unit("A")]), {})
        self.assertEqual(len(findings), 1)
        self.assertIn("NO-DATA", findings[0]["reason"])

    def test_wbs_that_is_not_a_dict_is_no_data_not_a_crash(self):
        findings = C.check(None, status({}))
        self.assertEqual(len(findings), 1)
        self.assertIn("NO-DATA", findings[0]["reason"])


class OrdinaryRowsProduceNoFindings(unittest.TestCase):
    def test_a_row_with_no_special_state_or_disposition_is_silent(self):
        w = wbs([unit("A")])
        s = status({"A": {"state": "RUNNING"}})
        self.assertEqual(C.check(w, s), [])

    def test_a_row_with_no_status_entry_at_all_and_no_disposition_is_silent(self):
        w = wbs([unit("A")])
        s = status({})
        self.assertEqual(C.check(w, s), [])


class DoneRowsAreNotReCheckedHere(unittest.TestCase):
    """The board's own tick_class already refuses an evidence-free DONE.
    A DONE row must never be re-flagged by this module even when it carries
    disposition MERGED and no fold fields, exactly the DOM-10.02 / DOM-10.04
    shape in the real plan (a real, proven row whose scope also folded
    another row's objective in by prose in its evidence text)."""

    def test_done_plus_merged_disposition_and_no_fold_fields_is_silent(self):
        w = wbs([unit("A", disposition="MERGED")])
        s = status({"A": {"state": "DONE", "evidence": "ran it, OK"}})
        self.assertEqual(C.check(w, s), [])


class HollowFoldFindings(unittest.TestCase):
    """merged_into present with nothing else proven: each missing piece is
    its own named finding, so a partial fold reads as partial."""

    def test_cancelled_with_nothing_at_all_names_all_three_missing_pieces(self):
        w = wbs([unit("ORCH-20"), unit("ORCH-19")])
        s = status({
            "ORCH-20": {"state": "CANCELLED", "evidence": "folded into ORCH-19"},
            "ORCH-19": {"state": "DONE", "evidence": "ran, OK"},
        })
        findings = C.check(w, s)
        reasons = [f["reason"] for f in findings if f["id"] == "ORCH-20"]
        self.assertEqual(len(reasons), 3)
        self.assertTrue(any("merged_into" in r for r in reasons))
        self.assertTrue(any("carried_by_check" in r for r in reasons))
        self.assertTrue(any("fold_proof" in r for r in reasons))

    def test_merged_into_present_but_carried_by_check_and_proof_missing(self):
        w = wbs([unit("TOKEN-03", disposition="MERGED", merged_into="JEV-06"),
                  unit("JEV-06")])
        s = status({})
        findings = C.check(w, s)
        reasons = [f["reason"] for f in findings if f["id"] == "TOKEN-03"]
        self.assertEqual(len(reasons), 2)
        self.assertTrue(any("carried_by_check" in r for r in reasons))
        self.assertTrue(any("fold_proof" in r for r in reasons))
        self.assertFalse(any("merged_into" in r for r in reasons))

    def test_merged_into_naming_a_nonexistent_row_is_a_finding(self):
        w = wbs([unit("A", disposition="MERGED")])
        s = status({"A": {
            "state": "CANCELLED",
            "merged_into": "NOT-A-REAL-ROW",
            "carried_by_check": "python3 scripts/test_closure_integrity.py -v",
            "fold_proof": "test_x: FAILED (failures=1), restored",
        }})
        findings = C.check(w, s, root=C.ROOT)
        self.assertEqual(len(findings), 1)
        self.assertIn("does not name an existing row", findings[0]["reason"])

    def test_carried_by_check_naming_a_missing_file_is_a_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = wbs([unit("A", disposition="MERGED"), unit("B")])
            s = status({"A": {
                "state": "CANCELLED",
                "merged_into": "B",
                "carried_by_check": "python3 scripts/test_nonexistent.py -v",
                "fold_proof": "test_x: FAILED (failures=1), restored",
            }})
            findings = C.check(w, s, root=tmp)
            self.assertEqual(len(findings), 1)
            self.assertIn("does not exist on disk", findings[0]["reason"])

    def test_carried_by_check_with_no_path_shaped_token_is_a_finding(self):
        w = wbs([unit("A", disposition="MERGED"), unit("B")])
        s = status({"A": {
            "state": "CANCELLED",
            "merged_into": "B",
            "carried_by_check": "run the tests",
            "fold_proof": "test_x: FAILED (failures=1), restored",
        }})
        findings = C.check(w, s)
        self.assertEqual(len(findings), 1)
        self.assertIn("names no test file", findings[0]["reason"])

    def test_fold_proof_with_no_red_line_is_a_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "scripts"))
            open(os.path.join(tmp, "scripts", "test_a.py"), "w").close()
            w = wbs([unit("A", disposition="MERGED"), unit("B")])
            s = status({"A": {
                "state": "CANCELLED",
                "merged_into": "B",
                "carried_by_check": "python3 scripts/test_a.py -v",
                "fold_proof": "it works, trust me",
            }})
            findings = C.check(w, s, root=tmp)
            self.assertEqual(len(findings), 1)
            self.assertIn("no FAILED or FAIL", findings[0]["reason"])


class CompleteFoldPasses(unittest.TestCase):
    def test_a_fold_with_every_field_and_a_real_test_file_is_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "scripts"))
            open(os.path.join(tmp, "scripts", "test_a.py"), "w").close()
            w = wbs([unit("A", disposition="MERGED"), unit("B")])
            s = status({"A": {
                "state": "CANCELLED",
                "merged_into": "B",
                "carried_by_check": "python3 scripts/test_a.py -v",
                "fold_proof": "test_guard: FAILED (failures=1), restored byte identical, then OK",
            }})
            self.assertEqual(C.check(w, s, root=tmp), [])

    def test_disposition_merged_with_no_cancelled_state_still_requires_proof(self):
        # TOKEN-03's real shape: disposition MERGED, no status entry, so no
        # CANCELLED state either. The disposition alone must trigger the check.
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "scripts"))
            open(os.path.join(tmp, "scripts", "test_a.py"), "w").close()
            w = wbs([unit("A", disposition="MERGED", merged_into="B"), unit("B")])
            s = status({"A": {
                "carried_by_check": "python3 scripts/test_a.py -v",
                "fold_proof": "test_guard: FAILED (failures=2), restored",
            }})
            self.assertEqual(C.check(w, s, root=tmp), [])


class DeferredDecisionFindings(unittest.TestCase):
    def test_awaiting_human_with_neither_field_names_both(self):
        w = wbs([unit("A")])
        s = status({"A": {"state": "AWAITING-HUMAN", "evidence": "needs the founder"}})
        findings = C.check(w, s)
        reasons = [f["reason"] for f in findings if f["id"] == "A"]
        self.assertEqual(len(reasons), 2)
        self.assertTrue(any("decided_by" in r for r in reasons))
        self.assertTrue(any("flip_condition" in r for r in reasons))

    def test_defer_disposition_with_no_status_entry_names_both(self):
        w = wbs([unit("A", disposition="DEFER")])
        s = status({})
        findings = C.check(w, s)
        self.assertEqual(len(findings), 2)

    def test_awaiting_human_with_both_fields_is_silent(self):
        w = wbs([unit("A")])
        s = status({"A": {
            "state": "AWAITING-HUMAN",
            "decided_by": "the founder",
            "flip_condition": "settings.json entry approved",
        }})
        self.assertEqual(C.check(w, s), [])


class UnknownEnumValuesAreFindings(unittest.TestCase):
    def test_unknown_state_is_named(self):
        w = wbs([unit("A")])
        s = status({"A": {"state": "HALF-BAKED"}})
        findings = C.check(w, s)
        self.assertEqual(len(findings), 1)
        self.assertIn("unknown state", findings[0]["reason"])

    def test_unknown_disposition_is_named(self):
        w = wbs([unit("A", disposition="SPLIT")])
        s = status({})
        findings = C.check(w, s)
        self.assertEqual(len(findings), 1)
        self.assertIn("unknown disposition", findings[0]["reason"])

    def test_unknown_state_does_not_suppress_the_fold_check(self):
        # A typo'd state must not accidentally look like a safe unrecognised
        # state and skip the rest of the row's checks.
        w = wbs([unit("A", disposition="MERGED")])
        s = status({"A": {"state": "CANCELED"}})  # misspelled, one L
        findings = C.check(w, s)
        self.assertEqual(ids(findings), ["A", "A", "A", "A"])
        reasons = sorted(f["reason"] for f in findings)
        self.assertTrue(any("unknown state" in r for r in reasons))
        self.assertTrue(any("merged_into" in r for r in reasons))
        self.assertTrue(any("carried_by_check" in r for r in reasons))
        self.assertTrue(any("fold_proof" in r for r in reasons))


class MixedPopulationMatchesAPinnedLiteral(unittest.TestCase):
    """The expected id list is written by hand, never derived from the
    module under test, so a regression that drops a whole row's findings
    cannot pass by accident."""

    def test_many_rows_at_once(self):
        w = wbs([
            unit("done1"),
            unit("hollow", disposition="MERGED"),
            unit("target"),
            unit("waiting"),
            unit("clean", disposition="MERGED", merged_into="target"),
        ])
        s = status({
            "done1": {"state": "DONE", "evidence": "ran, OK"},
            "hollow": {"state": "CANCELLED"},
            "target": {"state": "DONE", "evidence": "ran, OK"},
            "waiting": {"state": "AWAITING-HUMAN"},
            "clean": {
                "state": "CANCELLED",
                "carried_by_check": "python3 scripts/test_closure_integrity.py -v",
                "fold_proof": "test_x: FAILED (failures=1), restored",
            },
        })
        findings = C.check(w, s, root=C.ROOT)
        self.assertEqual(ids(findings), ["hollow", "hollow", "hollow", "waiting", "waiting"])


if __name__ == "__main__":
    unittest.main()
