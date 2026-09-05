"""What scripts/repeat_control.py must keep true.

Mirrors scripts/test_attempt_hook.py's own style: tempfile fixtures in the real
record shapes (repeat-guard's {"sig","approach","ok","exit_code","err"} rows,
one file per session; .vault_recall_seen's plain "SESSION:BASENAME" lines;
the attempt ledger's {"problem","class","outcome","note"} rows), unittest,
subprocess against the real script so its argparse and exit codes are proven
too. Every test here failed before scripts/repeat_control.py existed: there was
no module to import and no script to invoke, so every one of these assertions
was an ImportError or a "No such file" from subprocess. Trivially true, stated
because the brief asked for it stated rather than assumed.

EXTENDED 2026-09-05 (evidence audit, lane E53 instrument-honest), three classes
added below to drive the three fixed defects, none of the original 9 changed:
MechanismOverridesParitySchedule (defect 1: the arm is the mechanism, never the
calendar, both ways), ZeroCollisionsReadsNoData (defect 2: a corpus with no
cross-session collision anywhere reads NO-DATA rather than 0.00; the existing
TwoFullArmsKnownRates class above already covers the "one real collision reads
a rate" half), PreStartSessionIsExcluded (defect 3: a session before --start is
excluded from both arms and counted on its own line). All three point the new
--evidence-store/--repeat-lessons flags at empty tmp paths so the primary
signal reads a deterministic NO-DATA and never touches this machine's real
evidence store.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import repeat_control as R  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
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

SCRIPT = os.path.join(HERE, "repeat_control.py")


def _write_session(guard_dir, session_id, rows, mtime=None):
    """One repeat-guard session file, real shape, one JSON line per row."""
    path = os.path.join(guard_dir, "%s.jsonl" % session_id)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _row(sig, ok, approach="do the thing"):
    return {"sig": sig, "approach": approach, "ok": ok, "exit_code": None if ok else 1,
            "err": "" if ok else "boom"}


def _write_seen(path, entries):
    """entries: list of (session_id, basename)."""
    with open(path, "w", encoding="utf-8") as f:
        for session, base in entries:
            f.write("%s:%s\n" % (session, base))


class TwoFullArmsKnownRates(unittest.TestCase):
    """Five sessions each arm, one repeat charged in each, different repeat
    counts so the two rates cannot be mistaken for each other."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="repeat-control-test-")
        self.guard_dir = os.path.join(self.tmp, "repeat-guard")
        os.makedirs(self.guard_dir)
        self.seen_path = os.path.join(self.tmp, ".vault_recall_seen")
        self.ledger_path = os.path.join(self.tmp, "attempts.jsonl")
        base_t = time.time() - 100000

        # ON arm: on1..on5, each 2 attempts. Sig "A" fails in on1 then again in
        # on3 -> exactly one repeat, charged to on1.
        for i in range(1, 6):
            rows = [_row("keep-%d" % i, True), _row("keep2-%d" % i, True)]
            if i == 1:
                rows[0] = _row("A", False)
            if i == 3:
                rows[0] = _row("A", False)
            _write_session(self.guard_dir, "on%d" % i, rows, mtime=base_t + i * 10)

        # OFF arm: off1..off5, each 2 attempts. Sig "B" fails in off1, off2 and
        # off4 -> two repeats (off1->off2, off2->off4).
        for i in range(1, 6):
            rows = [_row("keep-%d" % i, True), _row("keep2-%d" % i, True)]
            if i in (1, 2, 4):
                rows[0] = _row("B", False)
            _write_session(self.guard_dir, "off%d" % i, rows, mtime=base_t + 100 + i * 10)

        _write_seen(self.seen_path, [("on%d" % i, "file.py") for i in range(1, 6)])
        # No entries at all for the off sessions: presence classification is
        # exactly "no recall record", which is what makes them the off arm.

        with open(self.ledger_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"problem": "p", "class": "c", "outcome": "failed",
                                 "note": ""}) + "\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self):
        buf = io.StringIO()
        code = R.run(guard_dir=self.guard_dir, recall_log=self.seen_path,
                      ledger=self.ledger_path, out=buf)
        return code, buf.getvalue()

    def test_both_arms_report_expected_rates_and_exit_0(self):
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("recall on: 5 session(s), 10 tool call(s), 5 lesson(s) shown, "
                       "1 repeat(s), 10.00 repeat(s) per hundred attempts", out)
        self.assertIn("recall off: 5 session(s), 10 tool call(s), 0 lesson(s) shown, "
                       "2 repeat(s), 20.00 repeat(s) per hundred attempts", out)
        self.assertIn("comparison:", out)
        self.assertIn("difference -10.00", out)

    def test_subprocess_cli_matches_and_exits_0(self):
        r = subprocess.run(
            [sys.executable, SCRIPT, "--guard-log", self.guard_dir,
             "--recall-log", self.seen_path, "--ledger", self.ledger_path],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("recall on: 5 session(s)", r.stdout)
        self.assertIn("recall off: 5 session(s)", r.stdout)


class OneArmBelowMinimum(unittest.TestCase):
    """Four on-sessions is short of the default floor of five: NO-DATA for
    that arm, exit 2, even though the off arm has plenty."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="repeat-control-test-")
        self.guard_dir = os.path.join(self.tmp, "repeat-guard")
        os.makedirs(self.guard_dir)
        self.seen_path = os.path.join(self.tmp, ".vault_recall_seen")
        base_t = time.time() - 100000
        for i in range(1, 5):  # only 4
            _write_session(self.guard_dir, "on%d" % i, [_row("k-%d" % i, True)],
                            mtime=base_t + i)
        for i in range(1, 6):  # 5
            _write_session(self.guard_dir, "off%d" % i, [_row("k-%d" % i, True)],
                            mtime=base_t + 100 + i)
        _write_seen(self.seen_path, [("on%d" % i, "f.py") for i in range(1, 5)])

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_short_arm_is_no_data_and_exit_2(self):
        buf = io.StringIO()
        code = R.run(guard_dir=self.guard_dir, recall_log=self.seen_path,
                      ledger=os.path.join(self.tmp, "no-such-ledger.jsonl"), out=buf)
        out = buf.getvalue()
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA: recall on has 4 session(s), fewer than 5", out)
        self.assertIn("recall off: 5 session(s)", out)
        self.assertIn("NO-DATA: the comparison needs both arms", out)


class SameSessionRepeatDoesNotCount(unittest.TestCase):
    """A sig failing twice inside ONE session's own log is not a repeat: a
    repeat requires a strictly later, different session."""

    def test_two_failures_of_one_sig_in_one_session_is_zero_repeats(self):
        sessions = {
            "s1": {"mtime": 1000.0, "rows": [_row("X", False), _row("X", False),
                                              _row("Y", True)]},
        }
        repeats = R.compute_repeats(sessions)
        self.assertEqual(repeats, {})

    def test_the_same_sig_failing_again_in_a_later_session_is_one_repeat(self):
        sessions = {
            "s1": {"mtime": 1000.0, "rows": [_row("X", False)]},
            "s2": {"mtime": 2000.0, "rows": [_row("X", False)]},
        }
        repeats = R.compute_repeats(sessions)
        self.assertEqual(repeats, {"s1": 1})


class MissingLogsReadNoData(unittest.TestCase):
    """Every one of the three input paths, absent on its own, prints its own
    named NO-DATA line rather than a silent zero."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="repeat-control-test-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_guard_dir_reads_no_data(self):
        missing = os.path.join(self.tmp, "no-such-guard-dir")
        buf = io.StringIO()
        code = R.run(guard_dir=missing,
                      recall_log=os.path.join(self.tmp, "no-seen"),
                      ledger=os.path.join(self.tmp, "no-ledger"), out=buf)
        out = buf.getvalue()
        self.assertIn("NO-DATA: no repeat-guard directory at %s" % missing, out)
        self.assertEqual(code, 2)

    def test_missing_recall_log_reads_no_data(self):
        guard_dir = os.path.join(self.tmp, "repeat-guard")
        os.makedirs(guard_dir)
        missing = os.path.join(self.tmp, "no-such-seen-file")
        buf = io.StringIO()
        R.run(guard_dir=guard_dir, recall_log=missing,
              ledger=os.path.join(self.tmp, "no-ledger"), out=buf)
        self.assertIn("NO-DATA: no recall-seen log at %s" % missing, buf.getvalue())

    def test_missing_ledger_reads_no_data_naming_its_path(self):
        guard_dir = os.path.join(self.tmp, "repeat-guard")
        os.makedirs(guard_dir)
        missing = os.path.join(self.tmp, "no-such-ledger.jsonl")
        buf = io.StringIO()
        R.run(guard_dir=guard_dir, recall_log=os.path.join(self.tmp, "no-seen"),
              ledger=missing, out=buf)
        self.assertIn("NO-DATA: no attempt ledger at %s" % missing, buf.getvalue())


class RealLogsStayReadOnly(unittest.TestCase):
    """Reading the estate's own real logs (when present) must never write to
    them: mtimes before and after are identical."""

    def test_real_default_paths_are_never_written(self):
        before = {}
        for path in (R.DEFAULT_GUARD_DIR, R.DEFAULT_RECALL_LOG, R.DEFAULT_LEDGER):
            if os.path.exists(path):
                before[path] = os.path.getmtime(path)
        if not before:
            self.skipTest("none of the real default paths exist on this machine")
        buf = io.StringIO()
        R.run(out=buf)  # every default: the real paths, read-only
        for path, mtime in before.items():
            self.assertEqual(os.path.getmtime(path), mtime,
                             "%s was modified by a read-only run" % path)


def _mtime_for(year, month, day):
    """Noon local time on the given date, as an epoch float, so
    date.fromtimestamp never lands on the wrong side of a day boundary."""
    import datetime as _dt
    return time.mktime(_dt.datetime(year, month, day, 12, 0, 0).timetuple())


class MechanismOverridesParitySchedule(unittest.TestCase):
    """Defect 1. A real lesson-shown session is 'on' even when its own date
    would have scored 'off' under the retired day-parity coin flip, and a
    session with no real shown record is 'off' even on a parity 'on' day.
    --start 2026-01-01: day offset 0 (2026-01-01) is parity 'on', offset 1
    (2026-01-02) is parity 'off' -- the two sessions below are placed
    exactly backwards from what the mechanism must report."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="repeat-control-test-")
        self.guard_dir = os.path.join(self.tmp, "repeat-guard")
        os.makedirs(self.guard_dir)
        self.seen_path = os.path.join(self.tmp, ".vault_recall_seen")
        self.ledger_path = os.path.join(self.tmp, "no-ledger.jsonl")
        self.no_evidence = os.path.join(self.tmp, "no-evidence")
        self.no_lessons = os.path.join(self.tmp, "no-lessons.jsonl")

        # Parity 'off' day, but a real recall-seen entry: mechanism says on.
        _write_session(self.guard_dir, "shown-on-off-day", [_row("k1", True)],
                        mtime=_mtime_for(2026, 1, 2))
        # Parity 'on' day, but no recall-seen entry at all: mechanism says off.
        _write_session(self.guard_dir, "silent-on-on-day", [_row("k2", True)],
                        mtime=_mtime_for(2026, 1, 1))
        _write_seen(self.seen_path, [("shown-on-off-day", "f.py")])

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_shown_session_is_on_regardless_of_parity_day(self):
        buf = io.StringIO()
        R.run(guard_dir=self.guard_dir, recall_log=self.seen_path,
              ledger=self.ledger_path, start="2026-01-01", min_sessions=1,
              out=buf, evidence_store=self.no_evidence, repeat_lessons=self.no_lessons)
        out = buf.getvalue()
        # The parity SCHEDULE (informational) says one session lands on each
        # day, exactly the reverse of the mechanism's own verdict below.
        self.assertIn("scheduled arm by parity (design intent, not used for "
                       "the comparison): 1 on-day session(s), 1 off-day "
                       "session(s)", out)
        self.assertIn("recall on: 1 session(s), 1 tool call(s), 1 lesson(s) shown", out)
        # The off arm's one session is the SILENT one, never the shown one:
        # 0 lessons shown in the off arm proves the shown session did not
        # leak in there by parity.
        self.assertIn("recall off: 1 session(s), 1 tool call(s), 0 lesson(s) shown", out)


class ZeroCollisionsReadsNoData(unittest.TestCase):
    """Defect 2. Five sessions each arm, every sig distinct across every
    session (no sig ever fails twice anywhere): the secondary detector has
    zero cross-session collisions in the whole corpus, so both arm lines
    and the comparison line must say NO-DATA by name, never 0.00. (The
    "one real collision reads a rate" half is already covered by
    TwoFullArmsKnownRates above.)"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="repeat-control-test-")
        self.guard_dir = os.path.join(self.tmp, "repeat-guard")
        os.makedirs(self.guard_dir)
        self.seen_path = os.path.join(self.tmp, ".vault_recall_seen")
        self.no_evidence = os.path.join(self.tmp, "no-evidence")
        self.no_lessons = os.path.join(self.tmp, "no-lessons.jsonl")
        base_t = time.time() - 100000
        for i in range(1, 6):
            # Every failing sig is unique to its own session: no sig ever
            # recurs, so compute_repeats charges zero repeats anywhere.
            _write_session(self.guard_dir, "on%d" % i,
                            [_row("only-on-%d" % i, False)], mtime=base_t + i * 10)
        for i in range(1, 6):
            _write_session(self.guard_dir, "off%d" % i,
                            [_row("only-off-%d" % i, False)], mtime=base_t + 100 + i * 10)
        _write_seen(self.seen_path, [("on%d" % i, "f.py") for i in range(1, 6)])

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_zero_corpus_collisions_is_no_data_not_zero(self):
        buf = io.StringIO()
        code = R.run(guard_dir=self.guard_dir, recall_log=self.seen_path,
                      ledger=os.path.join(self.tmp, "no-ledger.jsonl"), out=buf,
                      evidence_store=self.no_evidence, repeat_lessons=self.no_lessons)
        out = buf.getvalue()
        self.assertNotIn("0.00 repeat(s) per hundred attempts", out)
        self.assertIn(
            "recall on: 5 session(s), 5 tool call(s), 5 lesson(s) shown, "
            "NO-DATA: repeat signal never collided across sessions: the "
            "fingerprint cannot be told from a detector that cannot fire", out)
        self.assertIn(
            "recall off: 5 session(s), 5 tool call(s), 0 lesson(s) shown, "
            "NO-DATA: repeat signal never collided across sessions: the "
            "fingerprint cannot be told from a detector that cannot fire", out)
        self.assertIn(
            "comparison: NO-DATA: repeat signal never collided across "
            "sessions: the fingerprint cannot be told from a detector that "
            "cannot fire", out)
        self.assertEqual(code, 0)  # both arms still had enough sessions


class PreStartSessionIsExcluded(unittest.TestCase):
    """Defect 3. A session whose guard file predates --start is excluded
    from BOTH arms, never folded into whichever one presence would pick,
    and the exclusion is counted on its own line."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="repeat-control-test-")
        self.guard_dir = os.path.join(self.tmp, "repeat-guard")
        os.makedirs(self.guard_dir)
        self.seen_path = os.path.join(self.tmp, ".vault_recall_seen")
        self.no_evidence = os.path.join(self.tmp, "no-evidence")
        self.no_lessons = os.path.join(self.tmp, "no-lessons.jsonl")

        # Before --start, WITH a real shown entry: if this leaked into the
        # "on" arm instead of being excluded, the on arm would read 1
        # session instead of the NO-DATA-below-minimum this test expects.
        _write_session(self.guard_dir, "pre-start-shown", [_row("k1", True)],
                        mtime=_mtime_for(2025, 12, 31))
        # On/after --start, no shown entry: the one real "off" session.
        _write_session(self.guard_dir, "post-start-silent", [_row("k2", True)],
                        mtime=_mtime_for(2026, 1, 1))
        _write_seen(self.seen_path, [("pre-start-shown", "f.py")])

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pre_start_session_excluded_from_both_arms(self):
        buf = io.StringIO()
        R.run(guard_dir=self.guard_dir, recall_log=self.seen_path,
              ledger=os.path.join(self.tmp, "no-ledger.jsonl"),
              start="2026-01-01", min_sessions=1, out=buf,
              evidence_store=self.no_evidence, repeat_lessons=self.no_lessons)
        out = buf.getvalue()
        self.assertIn("excluded: 1 session(s) before 2026-01-01", out)
        # The excluded session had a real shown entry; if it were not
        # excluded it would fill the "on" arm instead of leaving it short.
        self.assertIn("NO-DATA: recall on has 0 session(s), fewer than 1", out)
        self.assertIn("recall off: 1 session(s), 1 tool call(s), 0 lesson(s) shown", out)


if __name__ == "__main__":
    unittest.main()
