"""Calibration for scripts/risk_weighted_sut.py.

The property this file exists to assert is the deciding one from
docs/plan/ORCH-1020-WBS.json's DOM-20.02 row: unwatched time is reported PER
RISK CLASS, never pooled across classes, and a unit with no risk class is
its own NO-DATA bucket, never merged into whichever named class happens to
read as the lowest one. A break in one class must never move another
class's figure, and an unattributable run-wide break must never be dropped
from either.

Every fixture is built in a temp directory, mirroring
scripts/test_safe_unwatched_time.py's own fixture shape (same event and
claim helpers, extended with a risk_class argument), so the two suites stay
readable side by side.
"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'scripts'))
import risk_weighted_sut as rws  # noqa: E402
import safe_unwatched_time as sut  # noqa: E402

try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__))

# Vocabulary taken from the instruments themselves, never re-spelled: a test
# that re-typed these could drift from the reader it calibrates and still
# pass.
OPENED = sut.EV_RUN_OPENED
FINISHED = sut.EV_UNIT_DONE
VERIFIED = sut.EV_EVIDENCE_VERIFIED
REFUSED = sut.EV_INTEGRATE_REFUSED
ISSUED = sut.EV_RECEIPT_ISSUED

BASE = datetime.datetime(2026, 9, 18, 22, 0, 0, tzinfo=datetime.timezone.utc)


def at(minute):
    return (BASE + datetime.timedelta(minutes=minute)).isoformat()


def event(minute, kind, payload=None, unit_id=None):
    return {"at": at(minute), "type": kind, "unit_id": unit_id,
            "payload": payload or {}}


def claim(unit_id, minute, exit_code=0, state="done", risk_class="medium"):
    """A claims.json entry carrying evidence.risk_class the way
    scripts/integrate.py (ORCH-02) and scripts/claim_store.py actually
    thread it through. risk_class=None omits the field entirely (the
    "claim exists, evidence carries no risk_class" edge case);
    risk_class="" leaves it present but blank (the "empty string" edge
    case the spec names)."""
    when = (BASE + datetime.timedelta(minutes=minute)).timestamp()
    evidence = {"check_command": "python3 -c pass", "files_changed": ["a.py"]}
    if exit_code is not None:
        evidence["exit_code"] = exit_code
    if risk_class is not None:
        evidence["risk_class"] = risk_class
    return {"unit_id": unit_id, "state": state, "claimed_at": when,
            "released_at": when, "evidence": evidence}


def write_run(root, name, events, claims=None):
    run_dir = os.path.join(root, name)
    os.makedirs(run_dir)
    with open(os.path.join(run_dir, "journal.jsonl"), "w",
              encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")
    if claims is not None:
        with open(os.path.join(run_dir, "claims.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(claims, fh, indent=1)
    return run_dir


#: A ninety minute run, two risk classes, neither breaks. u1 is "critical"
#: (a schema-migration-shaped unit), u2 is "medium" (a README-shaped one).
TWO_CLASSES_CLEAN = [
    event(0, OPENED, {"units": 2}),
    event(30, FINISHED, {"files_changed": 1}, "u1"),
    event(31, VERIFIED, {"check_exit": 0}, "u1"),
    event(60, FINISHED, {"files_changed": 1}, "u2"),
    event(61, VERIFIED, {"check_exit": 0}, "u2"),
    event(85, ISSUED, {"receipts": 2, "unproven": 0, "verified": 2}),
]

CLEAN_CLAIMS = {"u1": claim("u1", 30, risk_class="critical"),
                "u2": claim("u2", 60, risk_class="medium")}


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="rws-")
        self.addCleanup(shutil.rmtree, self.root, True)


class NeverPooled(Sandbox):
    """The deciding property: DOM-20.02's own words, thirty minutes of
    README work is never compared with thirty minutes of schema migration.
    """

    def test_two_clean_classes_each_span_the_whole_run(self):
        run = write_run(self.root, "clean", TWO_CLASSES_CLEAN, CLEAN_CLAIMS)
        per_class, code = rws.measure(run)
        self.assertEqual(code, 0)
        self.assertEqual(set(per_class), {"critical", "medium"})
        for risk_class in ("critical", "medium"):
            report = per_class[risk_class]
            self.assertEqual(report["broken_by"], "none")
            self.assertAlmostEqual(report["minutes"], 85.0, places=3)
            self.assertEqual(report["units"], 1)

    def test_a_break_in_one_class_never_moves_the_other(self):
        """u1 (critical) is refuted at minute 40. u2 (medium) must read
        exactly as if the break never happened: this is the whole point of
        the row, and the one assertion that would catch a pooled sum."""
        events = list(TWO_CLASSES_CLEAN) + [
            event(40, VERIFIED, {"check_exit": 1}, "u1")]
        run = write_run(self.root, "one-breaks",
                        sorted(events, key=lambda e: e["at"]), CLEAN_CLAIMS)
        per_class, code = rws.measure(run)
        self.assertEqual(code, 0)
        self.assertAlmostEqual(per_class["critical"]["minutes"], 40.0,
                               places=3)
        self.assertEqual(per_class["critical"]["broken_by"], sut.REFUTED)
        self.assertAlmostEqual(per_class["medium"]["minutes"], 85.0,
                               places=3)
        self.assertEqual(per_class["medium"]["broken_by"], "none")
        self.assertEqual(per_class["medium"]["units"], 1)

    def test_only_that_classs_own_unit_is_excluded_past_its_break(self):
        """u1 closes at minute 30 (before its own break at 40): still
        counted. A second critical unit closing at minute 50 (after the
        break) must not be, the same rule safe_unwatched_time.py enforces
        for the whole run, now scoped to one class."""
        events = list(TWO_CLASSES_CLEAN) + [
            event(40, VERIFIED, {"check_exit": 1}, "u1"),
            event(50, FINISHED, {"files_changed": 1}, "u3")]
        claims = dict(CLEAN_CLAIMS)
        claims["u3"] = claim("u3", 50, risk_class="critical")
        run = write_run(self.root, "late-unit",
                        sorted(events, key=lambda e: e["at"]), claims)
        per_class, _ = rws.measure(run)
        self.assertEqual(per_class["critical"]["units"], 1)

    def test_an_unattributable_receipt_break_hits_every_class(self):
        """receipt.issued names no unit. It cannot be pinned to one class,
        so it must break BOTH, not get dropped and not get guessed onto
        just one."""
        events = [e for e in TWO_CLASSES_CLEAN if e["type"] != ISSUED]
        events.append(event(45, ISSUED, {"receipts": 2, "unproven": 1}))
        run = write_run(self.root, "wide-break",
                        sorted(events, key=lambda e: e["at"]), CLEAN_CLAIMS)
        per_class, code = rws.measure(run)
        self.assertEqual(code, 0)
        for risk_class in ("critical", "medium"):
            self.assertEqual(per_class[risk_class]["broken_by"], sut.UNPROVEN)
            self.assertAlmostEqual(per_class[risk_class]["minutes"], 45.0,
                                   places=3)


class AttributionEdges(Sandbox):
    """Findings confirmed from the Muse adversarial pass on this row's own
    generalized design (drafts/DOM-20.02-muse.txt, 2026-09-18): a name
    collision between a real declared risk class and the reserved unknown
    bucket, and an unattributable break silently vanishing instead of
    breaking every class."""

    def test_a_unit_literally_declaring_risk_class_unknown_raises(self):
        claims = dict(CLEAN_CLAIMS)
        claims["u2"] = claim("u2", 60, risk_class=rws.UNKNOWN_RISK_CLASS)
        run = write_run(self.root, "collide", TWO_CLASSES_CLEAN, claims)
        with self.assertRaises(ValueError):
            rws.measure(run)

    def test_a_refuted_break_with_no_unit_id_hits_every_class(self):
        """evidence.verified without a unit_id (a malformed or stripped
        record) must not be dropped and read as safe; it cannot be pinned
        to one class, so it ends every class's span, the same treatment as
        the receipt-wide break."""
        events = list(TWO_CLASSES_CLEAN) + [
            event(40, VERIFIED, {"check_exit": 1}, unit_id=None)]
        run = write_run(self.root, "unattributed",
                        sorted(events, key=lambda e: e["at"]), CLEAN_CLAIMS)
        per_class, code = rws.measure(run)
        self.assertEqual(code, 0)
        for risk_class in ("critical", "medium"):
            self.assertEqual(per_class[risk_class]["broken_by"], sut.REFUTED)
            self.assertAlmostEqual(per_class[risk_class]["minutes"], 40.0,
                                   places=3)

    def test_an_empty_string_claim_key_breaks_every_class_not_none(self):
        """A claims.json entry keyed by "" cannot match any real class's
        unit_ids set. Left unattributed on purpose (like the two cases
        above) rather than silently excluded from every class's breaks."""
        claims = dict(CLEAN_CLAIMS)
        claims[""] = claim("", 45, exit_code=1, risk_class="critical")
        run = write_run(self.root, "blank-key", TWO_CLASSES_CLEAN, claims)
        per_class, code = rws.measure(run)
        self.assertEqual(code, 0)
        for risk_class in ("critical", "medium"):
            self.assertEqual(per_class[risk_class]["broken_by"], sut.REFUTED)

    def test_zero_unproven_receipts_does_not_break_but_nonzero_does(self):
        """The direct contrast: N=0 must read safe, N>0 must not, for
        every class alike."""
        run = write_run(self.root, "n-zero", TWO_CLASSES_CLEAN, CLEAN_CLAIMS)
        per_class, _ = rws.measure(run)
        for risk_class in ("critical", "medium"):
            self.assertEqual(per_class[risk_class]["broken_by"], "none")

        events = [e for e in TWO_CLASSES_CLEAN if e["type"] != ISSUED]
        events.append(event(85, ISSUED, {"receipts": 2, "unproven": 1}))
        run2 = write_run(self.root, "n-nonzero", events, CLEAN_CLAIMS)
        per_class2, _ = rws.measure(run2)
        for risk_class in ("critical", "medium"):
            self.assertEqual(per_class2[risk_class]["broken_by"], sut.UNPROVEN)


class UnknownBucket(Sandbox):
    def test_the_unknown_bucket_name_is_no_real_risk_class(self):
        """Added by the orchestrator after its own mutation SURVIVED: renaming
        the bucket to "low" left every test green, because no test used a real
        class named low. The estate's risk classes, pinned here as measured in
        the tree on 2026-09-18, must never share a name with the bucket."""
        real_classes = {"low", "normal", "medium", "high", "critical"}
        self.assertNotIn(rws.UNKNOWN_RISK_CLASS, real_classes)

    def test_a_unit_with_no_risk_class_gets_its_own_nodata_bucket(self):
        events = list(TWO_CLASSES_CLEAN) + [
            event(70, FINISHED, {"files_changed": 1}, "u3")]
        claims = dict(CLEAN_CLAIMS)
        claims["u3"] = claim("u3", 70, risk_class=None)
        run = write_run(self.root, "unknown",
                        sorted(events, key=lambda e: e["at"]), claims)
        per_class, code = rws.measure(run)
        self.assertEqual(code, 3)
        self.assertIn(rws.UNKNOWN_RISK_CLASS, per_class)
        self.assertTrue(per_class[rws.UNKNOWN_RISK_CLASS]["nodata"])
        self.assertIn("u3", per_class[rws.UNKNOWN_RISK_CLASS]["nodata"])

    def test_the_unknown_bucket_never_merges_into_medium(self):
        """medium is the lowest-severity-reading name present in this
        fixture. u3's missing risk class must not quietly become a third
        medium unit."""
        events = list(TWO_CLASSES_CLEAN) + [
            event(70, FINISHED, {"files_changed": 1}, "u3")]
        claims = dict(CLEAN_CLAIMS)
        claims["u3"] = claim("u3", 70, risk_class=None)
        run = write_run(self.root, "unknown2",
                        sorted(events, key=lambda e: e["at"]), claims)
        per_class, _ = rws.measure(run)
        self.assertEqual(per_class["medium"]["units"], 1)
        self.assertAlmostEqual(per_class["medium"]["minutes"], 85.0,
                               places=3)

    def test_an_empty_string_risk_class_is_unknown_not_a_real_class(self):
        claims = dict(CLEAN_CLAIMS)
        claims["u2"] = claim("u2", 60, risk_class="")
        run = write_run(self.root, "blank", TWO_CLASSES_CLEAN, claims)
        per_class, code = rws.measure(run)
        self.assertEqual(code, 3)
        self.assertNotIn("", per_class)
        self.assertIn(rws.UNKNOWN_RISK_CLASS, per_class)

    def test_a_unit_named_in_the_journal_but_never_claimed_is_unknown(self):
        """A unit can close (unit.done) and still have no claims.json
        entry at all, e.g. a crash between the two writes. It must not
        silently disappear from every class."""
        events = list(TWO_CLASSES_CLEAN) + [
            event(70, FINISHED, {"files_changed": 1}, "ghost")]
        run = write_run(self.root, "ghost",
                        sorted(events, key=lambda e: e["at"]), CLEAN_CLAIMS)
        per_class, code = rws.measure(run)
        self.assertEqual(code, 3)
        self.assertIn("ghost", per_class[rws.UNKNOWN_RISK_CLASS]["nodata"])

    def test_the_unknown_bucket_line_is_no_data_never_a_number(self):
        claims = dict(CLEAN_CLAIMS)
        claims["u3"] = claim("u3", 70, risk_class=None)
        run = write_run(self.root, "unknown3", TWO_CLASSES_CLEAN, claims)
        per_class, _ = rws.measure(run)
        line = rws.report_line(rws.UNKNOWN_RISK_CLASS,
                               per_class[rws.UNKNOWN_RISK_CLASS])
        self.assertIn("NO-DATA", line)
        self.assertNotIn("min", line)


class NoData(Sandbox):
    def test_a_missing_directory_exits_2(self):
        per_class, code = rws.measure(os.path.join(self.root, "nope"))
        self.assertEqual(code, 2)
        self.assertIn(rws.GLOBAL_NODATA_KEY, per_class)

    def test_a_run_with_no_journal_events_is_no_data_and_exits_3(self):
        run = os.path.join(self.root, "empty")
        os.makedirs(run)
        per_class, code = rws.measure(run)
        self.assertEqual(code, 3)
        self.assertEqual(set(per_class), {rws.GLOBAL_NODATA_KEY})

    def test_a_journal_with_no_receipt_is_no_data(self):
        events = [e for e in TWO_CLASSES_CLEAN if e["type"] != ISSUED]
        run = write_run(self.root, "receiptless", events, CLEAN_CLAIMS)
        per_class, code = rws.measure(run)
        self.assertEqual(code, 3)
        self.assertIn("no receipt", per_class[rws.GLOBAL_NODATA_KEY]["nodata"])

    def test_no_unit_id_anywhere_is_no_data(self):
        events = [event(0, OPENED, {"units": 0}),
                 event(10, ISSUED, {"receipts": 1, "unproven": 0})]
        run = write_run(self.root, "no-units", events, {})
        per_class, code = rws.measure(run)
        self.assertEqual(code, 3)
        self.assertIn(rws.GLOBAL_NODATA_KEY, per_class)


class DamagedRecords(Sandbox):
    def test_a_truncated_final_line_is_skipped_not_fatal(self):
        run = write_run(self.root, "torn", TWO_CLASSES_CLEAN, CLEAN_CLAIMS)
        with open(os.path.join(run, "journal.jsonl"), "a",
                  encoding="utf-8") as fh:
            fh.write('{"at": "2026-09-18T23:40:00+00:00", "type": "unit.d')
        per_class, code = rws.measure(run)
        self.assertEqual(code, 0)
        for risk_class in ("critical", "medium"):
            self.assertEqual(per_class[risk_class]["skipped_lines"], 1)

    def test_a_claim_stamped_before_the_run_started_is_not_this_runs_break(self):
        claims = dict(CLEAN_CLAIMS)
        claims["old"] = claim("old", -30, exit_code=None,
                              risk_class="critical")
        run = write_run(self.root, "carried", TWO_CLASSES_CLEAN, claims)
        per_class, _ = rws.measure(run)
        self.assertEqual(per_class["critical"]["broken_by"], "none")
        self.assertAlmostEqual(per_class["critical"]["minutes"], 85.0,
                               places=3)


class ReportLineShape(Sandbox):
    def test_a_named_class_line_has_the_shape_row_s10_extends(self):
        run = write_run(self.root, "clean", TWO_CLASSES_CLEAN, CLEAN_CLAIMS)
        per_class, _ = rws.measure(run)
        self.assertEqual(
            rws.report_line("critical", per_class["critical"]),
            "safe unwatched time, risk class critical: 85.0 min over 1 "
            "units, broken by none")


class TheRealRunStore(unittest.TestCase):
    """Read only. Asserts nothing about a particular figure, only that the
    instrument either measures a real run or says why it could not, the
    same discipline test_safe_unwatched_time.py already runs on this
    directory."""

    def test_every_committed_run_directory_measures_or_says_why(self):
        root = os.path.join(REPO_ROOT, "docs", "plan", "runs")
        if not os.path.isdir(root):
            self.skipTest("no docs/plan/runs in this tree")
        seen = 0
        for name in sorted(os.listdir(root)):
            run = os.path.join(root, name)
            if not os.path.isdir(run):
                continue
            per_class, code = rws.measure(run)
            self.assertIn(code, (0, 3), "%s returned %d" % (name, code))
            self.assertTrue(per_class, "%s produced no bucket at all" % name)
            for risk_class, report in per_class.items():
                if report.get("nodata"):
                    self.assertTrue(report["nodata"].strip())
                else:
                    self.assertGreaterEqual(report["minutes"], 0.0)
            seen += 1
        self.assertGreater(seen, 0,
                           "no committed run directory found under "
                           "docs/plan/runs")


if __name__ == '__main__':
    unittest.main()
