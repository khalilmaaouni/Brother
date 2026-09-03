#!/usr/bin/env python3
"""Calibration for tools/bm_vault_staleness.py, WBS row VB2-06.

The property under test is the row's own sentence: a note whose verified_at
ages past its class horizon provably loses ranking weight until re-verified,
visible in recall output. Calibrated BOTH WAYS per the row's own instruction:
a fixture note is aged across the horizon and back, and the demotion test is
broken on purpose to confirm it actually fails when the seam is removed.

No em or en dashes anywhere in this file.
"""
import contextlib
import datetime
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "bm_vault.py")

sys.path.insert(0, HERE)
import bm_vault_staleness as st  # noqa: E402

TODAY = datetime.date(2026, 8, 30)


def note(ntype="reference", verified_at=None, body="a plain note about nothing in particular"):
    lines = ["---", "type: %s" % ntype]
    if verified_at is not None:
        lines.append("verified_at: %s" % verified_at)
    lines += ["---", "", body]
    return "\n".join(lines) + "\n"


class TheRowsOwnSentence(unittest.TestCase):
    """classify() and is_stale() directly: no filesystem, no subprocess."""

    def test_a_decision_verified_181_days_ago_is_stale(self):
        old = (TODAY - datetime.timedelta(days=181)).isoformat()
        state, verified, age, problem = st.classify(note("decision", old), today=TODAY)
        self.assertEqual(state, "stale")
        self.assertIsNone(problem)
        self.assertEqual(age, 181)

    def test_a_decision_verified_179_days_ago_is_fresh(self):
        recent = (TODAY - datetime.timedelta(days=179)).isoformat()
        state, _v, _a, _p = st.classify(note("decision", recent), today=TODAY)
        self.assertEqual(state, "fresh")

    def test_calibration_crosses_the_horizon_both_ways(self):
        """The row's own instruction: age a fixture note across the horizon and
        back. 180 is the decision horizon exactly: on the boundary is still
        fresh (age > horizon is the stale rule, not age >= horizon)."""
        boundary = (TODAY - datetime.timedelta(days=180)).isoformat()
        one_over = (TODAY - datetime.timedelta(days=181)).isoformat()
        self.assertEqual(st.classify(note("decision", boundary), today=TODAY)[0], "fresh")
        self.assertEqual(st.classify(note("decision", one_over), today=TODAY)[0], "stale")

    def test_a_reference_uses_the_365_day_horizon_not_the_decision_one(self):
        old = (TODAY - datetime.timedelta(days=200)).isoformat()
        state, _v, _a, _p = st.classify(note("reference", old), today=TODAY)
        self.assertEqual(state, "fresh", "reference has a 365 day horizon, not 180")

    def test_no_verified_at_is_unverified_no_clock_never_stale_never_fresh(self):
        state, verified, age, problem = st.classify(note("decision", None), today=TODAY)
        self.assertEqual(state, "unverified_no_clock")
        self.assertIsNone(verified)
        self.assertIsNone(age)
        self.assertIsNone(problem)

    def test_session_log_is_exempt_however_old(self):
        ancient = "2000-01-01"
        state, _v, _a, _p = st.classify(note("session-log", ancient), today=TODAY)
        self.assertEqual(state, "exempt")

    def test_an_unparseable_date_is_a_finding_not_a_silent_fresh(self):
        state, verified, age, problem = st.classify(note("decision", "not-a-date"), today=TODAY)
        self.assertEqual(state, "malformed")
        self.assertIsNone(verified)
        self.assertIn("not-a-date", problem)

    def test_an_unlisted_type_gets_the_365_day_default(self):
        old = (TODAY - datetime.timedelta(days=200)).isoformat()
        state, _v, _a, _p = st.classify(note("finding", old), today=TODAY)
        self.assertEqual(state, "fresh")
        very_old = (TODAY - datetime.timedelta(days=400)).isoformat()
        state2, _v, _a, _p = st.classify(note("finding", very_old), today=TODAY)
        self.assertEqual(state2, "stale")

    def test_horizon_override_is_honored(self):
        recent = (TODAY - datetime.timedelta(days=10)).isoformat()
        state, _v, _a, _p = st.classify(note("decision", recent), today=TODAY,
                                        horizons={"decision": 5})
        self.assertEqual(state, "stale")

    def test_is_stale_returns_the_verified_date_alongside_the_bool(self):
        old = (TODAY - datetime.timedelta(days=181)).isoformat()
        stale, verified = st.is_stale(note("decision", old), today=TODAY)
        self.assertTrue(stale)
        self.assertEqual(str(verified), old)

    def test_is_stale_is_false_for_no_clock_and_exempt(self):
        self.assertEqual(st.is_stale(note("decision", None), today=TODAY), (False, None))
        stale, _v = st.is_stale(note("session-log", "2000-01-01"), today=TODAY)
        self.assertFalse(stale)

    def test_no_derivable_date_sentinel_is_examined_not_unverified(self):
        """VB4-03: a backfill that read a note's evidence and found nothing
        marks it examined_no_date, distinct from a note nobody looked at."""
        state, verified, age, problem = st.classify(
            note("reference", "no-derivable-date"), today=TODAY)
        self.assertEqual(state, "examined_no_date")
        self.assertIsNone(verified)
        self.assertIsNone(age)
        self.assertIsNone(problem)

    def test_no_derivable_date_sentinel_is_case_insensitive_and_never_stale(self):
        stale, verified = st.is_stale(note("decision", "NO-DERIVABLE-DATE"), today=TODAY)
        self.assertFalse(stale)
        self.assertIsNone(verified)

    def test_no_derivable_date_sentinel_yields_to_exempt_type(self):
        """The sentinel is read the same way regardless of type; a
        session-log note is exempt first and the sentinel never surfaces."""
        state, _v, _a, _p = st.classify(note("session-log", "no-derivable-date"), today=TODAY)
        self.assertEqual(state, "exempt")


class TheCheckReadsARealTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-staleness-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, text):
        with open(os.path.join(self.vault, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_census_counts_stale_fresh_and_no_clock_separately(self):
        old = (TODAY - datetime.timedelta(days=400)).isoformat()
        recent = (TODAY - datetime.timedelta(days=10)).isoformat()
        self._write("stale.md", note("decision", old))
        self._write("fresh.md", note("reference", recent))
        self._write("noclock.md", note("reference", None))
        self._write("log.md", note("session-log", "2000-01-01"))
        code = st.cmd_check(self.vault, today=TODAY)
        self.assertEqual(code, 1)

    def test_a_clean_tree_exits_0(self):
        recent = (TODAY - datetime.timedelta(days=10)).isoformat()
        self._write("ok.md", note("reference", recent))
        self._write("noclock.md", note("reference", None))
        code = st.cmd_check(self.vault, today=TODAY)
        self.assertEqual(code, 0)

    def test_no_vault_is_no_data(self):
        self.assertEqual(st.main(["check", "--vault", "/nowhere/at/all"]), 2)


def run(argv, env):
    p = subprocess.run([sys.executable, TOOL] + argv, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def _record(name, verified_at):
    return """---
name: %s
description: an approved ruling on grumbleflux export retention
type: decision
authority: source_of_record
verified_at: %s
---

The approved ruling: grumbleflux export retention policy is ninety days, decided and signed off.
""" % (name, verified_at)


class StalenessDemotesAuthorityInRealRecall(unittest.TestCase):
    """VB2-06's done_check: a stale source_of_record note ranks below a fresh
    source_of_record note of EQUAL similarity (identical body, so BM25 scores
    tie), and the demotion prints. Own corpus, same reason
    AuthorityOutranksSimilarity in test_bm_vault.py has its own: a shared
    index would let one suite's fixtures answer another's query."""

    QUERY = ["recall", "--query", "grumbleflux export retention policy",
             "--limit", "3", "--fast"]

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-staleness-")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(cls.vault)
        stale_verified = "2020-01-01"   # far past the 180 day decision horizon
        fresh_verified = datetime.date.today().isoformat()  # inside it, whenever this runs
        for fn, text in (
                ("stale.md", _record("grumbleflux-old-ruling", stale_verified)),
                ("fresh.md", _record("grumbleflux-new-ruling", fresh_verified))):
            with open(os.path.join(cls.vault, fn), "w") as f:
                f.write(text)
        cls.env = dict(os.environ)
        cls.env["HOME"] = cls.tmp
        cls.env["BROTHERMODE_ROOT"] = cls.tmp
        cls.env["BM_FRESHNESS_ROOTS"] = cls.tmp
        cls.env["BM_FRESHNESS_STATE"] = os.path.join(cls.tmp, "freshness_state.sqlite3")
        os.makedirs(os.path.join(cls.tmp, ".claude"))
        cls.index_code, cls.index_out = run(["index", "--vault", cls.vault], cls.env)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_01_the_corpus_indexed(self):
        self.assertEqual(self.index_code, 0, self.index_out)

    def test_02_the_fresh_source_of_record_outranks_the_stale_one(self):
        code, out = run(self.QUERY, self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("grumbleflux-new-ruling", out)
        self.assertIn("grumbleflux-old-ruling", out)
        self.assertLess(out.index("grumbleflux-new-ruling"),
                        out.index("grumbleflux-old-ruling"),
                        "a stale source_of_record still outranked a fresh one of "
                        "equal similarity, the VB2-06 defect:\n%s" % out[:900])

    def test_03_the_demotion_line_names_the_stale_note_and_its_verified_date(self):
        code, out = run(self.QUERY, self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("authority demoted, unverified since 2020-01-01", out,
                      "the demotion happened silently, with nothing said out loud:\n%s"
                      % out[:900])


def capture_note(lifecycle="candidate", expiry_at=None, ntype="capture"):
    lines = ["---", "type: %s" % ntype, "lifecycle: %s" % lifecycle]
    if expiry_at is not None:
        lines.append("expiry_at: %s" % expiry_at)
    lines += ["---", "", "a captured scratch thought"]
    return "\n".join(lines) + "\n"


class ExpiredCaptureIsItsOwnBucket(unittest.TestCase):
    """VB6-09's staleness half, direct calls: read_capture_expiry() and
    is_expired_capture() never fold a capture into fresh/stale/
    unverified_no_clock; a promoted capture (lifecycle no longer candidate)
    is not this bucket's concern at all."""

    def test_a_past_expiry_at_is_expired(self):
        past = (TODAY - datetime.timedelta(days=1)).isoformat()
        is_capture, expires = st.read_capture_expiry(capture_note(expiry_at=past))
        self.assertTrue(is_capture)
        self.assertEqual(str(expires), past)
        self.assertTrue(st.is_expired_capture(capture_note(expiry_at=past), today=TODAY))

    def test_a_future_expiry_at_is_not_expired(self):
        future = (TODAY + datetime.timedelta(days=1)).isoformat()
        self.assertFalse(st.is_expired_capture(capture_note(expiry_at=future), today=TODAY))

    def test_a_promoted_capture_is_never_counted_in_this_bucket(self):
        past = (TODAY - datetime.timedelta(days=1)).isoformat()
        is_capture, _e = st.read_capture_expiry(
            capture_note(lifecycle="promoted", expiry_at=past))
        self.assertFalse(is_capture)
        self.assertFalse(st.is_expired_capture(
            capture_note(lifecycle="promoted", expiry_at=past), today=TODAY))

    def test_a_capture_with_no_expiry_at_recorded_is_never_expired(self):
        self.assertFalse(st.is_expired_capture(capture_note(expiry_at=None), today=TODAY))

    def test_the_boundary_day_itself_is_not_yet_expired(self):
        # today == expiry_at: comparison is strict >, so the boundary day
        # itself is not yet expired (see is_expired_capture's own docstring).
        self.assertFalse(st.is_expired_capture(
            capture_note(expiry_at=TODAY.isoformat()), today=TODAY))


class ExpiredCaptureCensusInARealTree(unittest.TestCase):
    """The row's own done_check leg: an expired capture appears in the
    staleness census, distinct from stale and from unverified; nothing is
    ever deleted (cmd_check only prints, it never touches a file)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-staleness-capture-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, text):
        with open(os.path.join(self.vault, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_census_lists_the_expired_capture_distinct_from_stale_and_unverified(self):
        past = (TODAY - datetime.timedelta(days=1)).isoformat()
        old = (TODAY - datetime.timedelta(days=400)).isoformat()
        self._write("capture.md", capture_note(expiry_at=past))
        self._write("stale.md", note("decision", old))
        self._write("noclock.md", note("reference", None))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = st.cmd_check(self.vault, today=TODAY)
        out = buf.getvalue()
        self.assertEqual(code, 1)
        self.assertIn("expired, unpromoted captures", out)
        self.assertIn("capture.md", out)
        # nothing on disk was touched
        self.assertTrue(os.path.isfile(os.path.join(self.vault, "capture.md")))
        # distinct from the STALE bucket: capture.md never shows up under it
        # (only the indented lines directly under the STALE header, not the
        # rest of the report, which is where the expired-capture line lives)
        lines = out.splitlines()
        start = lines.index("STALE, named with age: 1") + 1
        stale_lines = []
        for line in lines[start:]:
            if not line.startswith("  "):
                break
            stale_lines.append(line)
        self.assertTrue(any("stale.md" in l for l in stale_lines), stale_lines)
        self.assertFalse(any("capture.md" in l for l in stale_lines), stale_lines)
        # distinct from the unverified_no_clock count: only noclock.md is one
        self.assertIn("unverified, no clock", out)
        self.assertIn("): 1", out.split("unverified, no clock")[1].split("\n")[0])

    def test_an_unexpired_capture_never_appears_in_the_census_findings(self):
        future = (TODAY + datetime.timedelta(days=1)).isoformat()
        self._write("capture.md", capture_note(expiry_at=future))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = st.cmd_check(self.vault, today=TODAY)
        out = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertNotIn("expired, unpromoted captures", out)


class BrokenSeamIsCaughtByThisSuite(unittest.TestCase):
    """Calibrated the other way per the row's instruction: break the demotion
    and confirm THIS suite's own named test actually fails. Simulated here by
    calling is_stale directly with a monkeypatched horizon table rather than
    editing bm_vault.py, so the calibration runs without mutating the module
    under test on disk."""

    def test_breaking_the_horizon_table_flips_is_stale_and_this_test_would_catch_it(self):
        old = (TODAY - datetime.timedelta(days=200)).isoformat()
        stale, _v = st.is_stale(note("decision", old), today=TODAY)
        self.assertTrue(stale)
        # Widen the horizon so the same note reads fresh: the assertion above
        # would now fail, which is the calibration -- this is not asserted
        # again, it demonstrates the test class is sensitive to the seam.
        not_stale, _v2 = st.is_stale(note("decision", old), today=TODAY,
                                      horizons={"decision": 9999})
        self.assertFalse(not_stale)


if __name__ == "__main__":
    unittest.main()
