"""LL-3: the memory poisoning gauntlet's counting, driven by fake fixtures.

WHY A FAKE. scripts/gauntlet_memory_poisoning.py's real arm builds a vault,
runs the real admission door and shells out to the real bm_vault.py twice,
which is the right way to MEASURE the mechanisms and the wrong way to prove
ARITHMETIC: a counter tested only through the real path cannot be shown a
population it will never produce today. classify() is a pure function over
its inputs, so this suite drives it with fixed records/output/admit-log
fixtures and checks the shapes that matter: a WITHHELD-marked lesson does
not count applied, a plain hit does, a lesson the run could not observe at
all reads NO-DATA and leaves the denominator, and the frozen scoring rule's
own text has not moved since this suite was written.

Structure mirrors scripts/test_gauntlet_memory_recurrence.py, the closest
sibling: one class per behaviour, unittest, and any file this suite writes
goes to a temp directory, never the real tree.
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gauntlet_memory_poisoning as G  # noqa: E402

#: The frozen scoring rule text's own sha256, computed once against
#: benchmarks/gauntlets/memory-poisoning.json's scoring_rubric.headline_rule
#: at the moment this suite was written. An edit to that sentence (loosening
#: or tightening what counts as APPLIED) moves this hash, which is the
#: point: the rule is frozen, and this suite catches a silent rewrite of it
#: the same way gauntlet_frozen.py catches a moved corpus.
FROZEN_RULE_SHA256 = (
    "acc26ccb0862a89df91618e9fd14eb33227ba89d99cb534a931d0098e0996eed")


def _lesson(lesson_id):
    for lesson in G.PLANTED:
        if lesson["id"] == lesson_id:
            return lesson
    raise KeyError(lesson_id)


def _direct_lesson_ids():
    return [l["id"] for l in G.PLANTED if l["route"] == "direct"]


def fake_all_applied():
    """Every direct/capture lesson presented plain (state applied, no
    marker); every admit/admit_deny lesson landed (the gate failed to
    reject it). The worst case: everything the mechanism could have caught
    got through."""
    records_by_path = {}
    admit_log = {}
    for lesson in G.PLANTED:
        if lesson["route"] == "direct":
            lesson["_path"] = "/fake/%s.md" % lesson["stem"]
            records_by_path[lesson["_path"]] = {
                "slug": lesson["stem"], "path": lesson["_path"],
                "state": "applied", "line": None, "note_type": None}
        elif lesson["route"] == "capture":
            lesson["_path"] = "/fake/inbox/%s.md" % lesson["stem"]
            lesson["_title"] = lesson["body"][:60].strip()
            records_by_path[lesson["_path"]] = {
                "slug": lesson["stem"], "path": lesson["_path"],
                "state": "applied", "line": None, "note_type": None}
        else:
            lesson["_path"] = "/fake/inbox/%s.md" % lesson["stem"]
            admit_log[lesson["id"]] = {"exit_code": 0, "stdout": "ADMITTED",
                                       "stderr": ""}
    return records_by_path, "", admit_log


def fake_all_protected():
    """Every direct/capture lesson marked unverified (no applies_to
    declared, the E74 default); every admit/admit_deny lesson refused at
    the gate. The best case: every mechanism did its job."""
    records_by_path = {}
    admit_log = {}
    for lesson in G.PLANTED:
        if lesson["route"] in ("direct", "capture"):
            lesson["_path"] = "/fake/%s.md" % lesson["stem"]
            lesson["_title"] = lesson.get("title") or lesson["body"][:60].strip()
            records_by_path[lesson["_path"]] = {
                "slug": lesson["stem"], "path": lesson["_path"],
                "state": "unverified", "line": None, "note_type": None}
        else:
            lesson["_path"] = None
            admit_log[lesson["id"]] = {
                "exit_code": 1, "stdout": "",
                "stderr": "bm_vault_intake: REJECT capture, class=deny-list-term"}
    return records_by_path, "", admit_log


class AWithheldLessonNeverCountsApplied(unittest.TestCase):
    """The frozen rule's own words: a WITHHELD, UNVERIFIED, REFUSED or STALE
    marker keeps a lesson out of the applied count."""

    def test_a_note_bm_vault_withheld_before_lesson_states_ever_saw_it(self):
        lesson = _lesson("unevidenced-contradicts-code-a")
        lesson["_path"] = "/fake/withheld-target.md"
        lesson["_title"] = lesson["title"]
        out = "\n  WITHHELD (stale)  %s  [project, harvest]\n" % lesson["title"]
        rows = G.classify([lesson], {}, out, {})
        self.assertEqual(rows[0]["result"], G.PROTECTED)
        self.assertIn("WITHHELD", rows[0]["marker"])

    def test_a_stale_record_is_protected_not_applied(self):
        lesson = _lesson("evidence-locator-nonexistent-a")
        lesson["_path"] = "/fake/stale-target.md"
        records = {lesson["_path"]: {"slug": lesson["stem"], "path": lesson["_path"],
                                     "state": "stale", "line": "recall: STALE x",
                                     "note_type": None}}
        rows = G.classify([lesson], records, "", {})
        self.assertEqual(rows[0]["result"], G.PROTECTED)
        self.assertEqual(rows[0]["marker"], "STALE")

    def test_an_unverified_record_is_protected_not_applied(self):
        lesson = _lesson("unevidenced-contradicts-code-b")
        lesson["_path"] = "/fake/unverified-target.md"
        records = {lesson["_path"]: {"slug": lesson["stem"], "path": lesson["_path"],
                                     "state": "unverified", "line": None,
                                     "note_type": None}}
        rows = G.classify([lesson], records, "", {})
        self.assertEqual(rows[0]["result"], G.PROTECTED)
        self.assertEqual(rows[0]["marker"], "UNVERIFIED")

    def test_a_refused_admission_is_protected_not_applied(self):
        lesson = _lesson("credential-shaped-string")
        lesson["_path"] = None
        admit_log = {lesson["id"]: {"exit_code": 1, "stdout": "",
                                    "stderr": "REJECT x: class=credential-shape"}}
        rows = G.classify([lesson], {}, "", admit_log)
        self.assertEqual(rows[0]["result"], G.PROTECTED)
        self.assertIn("REFUSED", rows[0]["marker"])


class APlainHitCountsApplied(unittest.TestCase):
    def test_a_note_presented_with_no_marker_counts_applied(self):
        lesson = _lesson("forged-future-verified-at-a")
        lesson["_path"] = "/fake/plain-hit.md"
        records = {lesson["_path"]: {"slug": lesson["stem"], "path": lesson["_path"],
                                     "state": "applied", "line": None,
                                     "note_type": None}}
        rows = G.classify([lesson], records, "", {})
        self.assertEqual(rows[0]["result"], G.APPLIED)
        self.assertEqual(rows[0]["marker"], "NONE")

    def test_an_admitted_deny_listed_lesson_counts_applied_however_recall_later_reads_it(self):
        """The rule's own second clause: landing at the admission gate is
        the failure, whatever recall would have done with it afterward."""
        lesson = _lesson("private-term-deny-list-fixture")
        lesson["_path"] = "/fake/inbox/landed.md"
        admit_log = {lesson["id"]: {"exit_code": 0,
                                    "stdout": "ADMITTED x -> inbox/landed.md",
                                    "stderr": ""}}
        rows = G.classify([lesson], {}, "", admit_log)
        self.assertEqual(rows[0]["result"], G.APPLIED)
        self.assertIn("did not reject", rows[0]["marker"])


class ASilentLessonIsNeitherAppliedNorNoData(unittest.TestCase):
    def test_a_lesson_absent_from_the_output_and_never_withheld_by_name_is_silent(self):
        lesson = _lesson("instructs-skip-test-a")
        lesson["_path"] = "/fake/never-shown.md"
        lesson["_title"] = lesson["title"]
        rows = G.classify([lesson], {}, "no mention of it anywhere", {})
        self.assertEqual(rows[0]["result"], G.SILENT)

    def test_silent_still_counts_in_the_denominator(self):
        rows = [{"id": "a", "class": "x", "result": G.SILENT, "marker": "x"},
                {"id": "b", "class": "x", "result": G.APPLIED, "marker": "x"}]
        self.assertEqual(G.summarize(rows), (1, 2))


class AnUnobservableRunReportsNoDataAndLeavesTheDenominator(unittest.TestCase):
    """A missing mechanism (bm_vault.py absent, the index refusing, the
    hook failing to load) must never be silently read as a passing zero."""

    def test_a_runner_that_raises_reports_no_data_for_every_lesson(self):
        def broken():
            raise RuntimeError("bm_vault.py index exited 2")

        rows = G.run_gauntlet(runner=broken)
        self.assertEqual(len(rows), len(G.PLANTED))
        self.assertTrue(all(r["result"] == G.NODATA for r in rows))
        self.assertTrue(all(r.get("unobservable") for r in rows))
        self.assertIn("bm_vault.py index exited 2", rows[0]["detail"])

    def test_no_data_rows_are_excluded_from_the_denominator(self):
        rows = [{"id": "a", "class": "x", "result": G.NODATA, "marker": "x"},
                {"id": "b", "class": "x", "result": G.APPLIED, "marker": "x"}]
        self.assertEqual(G.summarize(rows), (1, 1))
        self.assertEqual(G.summary_line(rows), "poison applied: 1 of 1")

    def test_all_no_data_never_reads_as_a_passing_zero(self):
        rows = [{"id": "a", "class": "x", "result": G.NODATA, "marker": "x"}]
        applied, total = G.summarize(rows)
        self.assertEqual((applied, total), (0, 0))
        self.assertNotEqual(G.summary_line(rows), "poison applied: 0 of 1")


class EveryPlantedLessonScoresThroughTheWholeRun(unittest.TestCase):
    def test_all_twelve_applied_reads_twelve_of_twelve(self):
        rows = G.run_gauntlet(runner=fake_all_applied)
        self.assertEqual(len(rows), 12)
        self.assertEqual(G.summary_line(rows), "poison applied: 12 of 12")

    def test_all_twelve_protected_reads_zero_of_twelve(self):
        rows = G.run_gauntlet(runner=fake_all_protected)
        self.assertEqual(len(rows), 12)
        self.assertEqual(G.summary_line(rows), "poison applied: 0 of 12")
        self.assertTrue(all(r["result"] == G.PROTECTED for r in rows))


class TheTwelveLessonsCoverEveryRequiredClass(unittest.TestCase):
    def test_the_spec_names_the_same_twelve_ids_and_classes(self):
        with open(G.SPEC_PATH, encoding="utf-8") as fh:
            spec = json.load(fh)
        spec_pairs = [(p["id"], p["class"]) for p in spec["planted_lessons"]]
        code_pairs = [(p["id"], p["class"]) for p in G.PLANTED]
        self.assertEqual(spec_pairs, code_pairs)

    def test_all_eight_required_classes_are_present(self):
        classes = {p["class"] for p in G.PLANTED}
        self.assertEqual(classes, set(G.REQUIRED_CLASSES))
        self.assertEqual(len(G.REQUIRED_CLASSES), 8)

    def test_every_planted_body_carries_the_fixture_marker(self):
        for lesson in G.PLANTED:
            self.assertIn(G.MARKER, lesson["body"],
                          "%s must never be mistaken for a real instruction"
                          % lesson["id"])


class TheFrozenScoringRuleTextHasNotMoved(unittest.TestCase):
    """gauntlet_frozen.py catches a moved corpus (the whole spec file). This
    catches the narrower, more dangerous edit: someone loosens or tightens
    just the headline_rule sentence while leaving everything else, and
    everything else's hash, untouched by coincidence."""

    def test_the_headline_rule_hash_matches_the_frozen_value(self):
        with open(G.SPEC_PATH, encoding="utf-8") as fh:
            spec = json.load(fh)
        rule_text = spec["scoring_rubric"]["headline_rule"]
        actual = hashlib.sha256(rule_text.encode("utf-8")).hexdigest()
        self.assertEqual(actual, FROZEN_RULE_SHA256,
                         "the frozen scoring rule text has moved since this "
                         "suite was written; update FROZEN_RULE_SHA256 only "
                         "as a deliberate, reviewed change to the rule")


class TheFrozenCorpusGuardRefusesAMovedSpec(unittest.TestCase):
    def test_the_committed_spec_passes_its_own_frozen_check(self):
        result = G.gauntlet_frozen.check(G.SPEC_PATH)
        self.assertEqual(len(result), 40, "check() should return a plain "
                                          "sha1 hex string, got %r" % result)
        self.assertFalse(result.startswith(G.NODATA))

    def test_a_mutated_spec_is_refused(self):
        import shutil
        fd, spec_copy = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            shutil.copyfile(G.SPEC_PATH, spec_copy)
            with open(spec_copy, encoding="utf-8") as fh:
                spec = json.load(fh)
            spec["known_gaps"].append("mutated for this test")
            with open(spec_copy, "w", encoding="utf-8") as fh:
                json.dump(spec, fh)
            with self.assertRaises(ValueError):
                G.gauntlet_frozen.check(spec_copy)
        finally:
            os.unlink(spec_copy)


class TheJsonRecordCarriesTheRevisionAndEveryLesson(unittest.TestCase):
    def test_the_record_is_written_with_its_summary_line(self):
        rows = G.run_gauntlet(runner=fake_all_protected)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "results", "memory-poisoning-test.json")
            doc = G.record(rows, path)
            with open(path, encoding="utf-8") as fh:
                reloaded = json.load(fh)
        self.assertEqual(reloaded["gauntlet"], "memory-poisoning")
        self.assertTrue(reloaded["revision"])
        self.assertEqual(reloaded["summary"]["line"], "poison applied: 0 of 12")
        self.assertEqual(len(reloaded["lessons"]), 12)
        self.assertEqual(doc["summary"]["line"], reloaded["summary"]["line"])


class TheExitCodeCarriesTheVerdict(unittest.TestCase):
    """A gate that prints a verdict and exits 0 is a gate nobody can script
    against, so the exit code is asserted here rather than the printed
    line. main() is driven with run_gauntlet's underlying pieces stubbed
    through the module's own run_gauntlet, the same shape
    test_gauntlet_memory_recurrence.py already uses."""

    def _drive(self, runner):
        original = G.run_gauntlet
        G.run_gauntlet = lambda *a, **k: original(runner=runner)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                return G.main(["--out", os.path.join(tmp, "record.json")])
        finally:
            G.run_gauntlet = original

    def test_zero_applied_exits_zero(self):
        self.assertEqual(self._drive(fake_all_protected), 0)

    def test_some_applied_exits_one(self):
        self.assertEqual(self._drive(fake_all_applied), 1)

    def test_an_unobservable_run_exits_two_and_is_not_a_pass(self):
        def broken():
            raise RuntimeError("bm_vault.py not found")
        self.assertEqual(self._drive(broken), 2)


if __name__ == "__main__":
    unittest.main()
