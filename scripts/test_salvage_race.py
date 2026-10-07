#!/usr/bin/env python3
"""salvage.py promote must never overwrite a STATUS that changed after selection: a landing quarantine always wins.

WHY (audit 2026-09-27, reproduced on hub main e7e17784c): promote chose its candidates from a STATUS read at
selection, a landing wrote "QUARANTINE refused at the gates" after that read, and promote then overwrote it with
READY, so the refused build was offered to the landing again. Each case below isolates ONE guard in promote: the
compare with the selection snapshot, the newest run check, the refusal of a candidate with no snapshot, the shared
lock, and the rule that a refusal touches nothing in the run folder. Every fixture lives under a temp directory; no
real run history, ledger or HOME is read."""
import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

LOOP = Path(__file__).resolve().parent / "loop"
sys.path.insert(0, str(LOOP))
import salvage as S  # noqa: E402

QUARANTINE = b"QUARANTINE refused at the gates alone, gates RED | previous: EXHAUSTED\n"


def plant(runs, sub, stamp, status=b"EXHAUSTED after 5 rounds\n", age=0.0):
    """One run folder holding one grader PASS build with probes CLEAN; its lane log and folder dated age seconds ago."""
    run = runs / ("%s-%s" % (sub, stamp)); rd = run / "round0"
    (rd / "out").mkdir(parents=True); (rd / "probes" / "logs").mkdir(parents=True)
    build = rd / "out" / ("%s-r0-build.json" % sub); build.write_text('{"edits": []}')
    (rd / "lane.log").write_text("%s PASS %s-r0\n" % (sub, sub))
    (rd / "probes" / "logs" / (sub + ".done")).write_text("CLEAN\n")
    if status is not None:
        (run / "STATUS").write_bytes(status)
    t = time.time() - age
    os.utime(str(rd / "lane.log"), (t, t)); os.utime(str(run), (t, t))
    return run, build


def select(runs, sub):
    """The PROMOTE candidate for sub, chosen exactly as main() chooses it."""
    view = S.plan_view(S.candidates(str(runs), {sub}), lambda b: "", "shadow", str(runs))
    chosen = [c for c in view if c["action"] == "PROMOTE"]
    assert len(chosen) == 1, [c["action"] for c in view]
    return chosen[0]


def folder_state(run):
    return sorted(os.listdir(str(run))), os.stat(str(run)).st_mtime_ns


class Race(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.runs = Path(tmp.name) / "runs"; self.runs.mkdir()
        self.root = Path(tmp.name)

    def test_entry_point_keeps_a_quarantine_written_after_selection(self):
        """The audit's shape at main(): the landing quarantines the run between selection and the write."""
        run, _ = plant(self.runs, "X.a", "010000")
        board = self.root / "plan.json"
        board.write_text(json.dumps({"units": [{"id": "X", "state": "OPEN", "sub_units": ["X.a"], "evidence": ""}]}))
        real = S.plan_view
        def racing_view(*a, **k):
            cands = real(*a, **k)
            (run / "STATUS").write_bytes(QUARANTINE)   # the landing, after selection read EXHAUSTED
            return cands
        out = io.StringIO()
        stubs = {"grade_build": types.SimpleNamespace(preflight=lambda b: ""),
                 "check_wave": types.SimpleNamespace(checker_mode=lambda: "shadow")}
        with mock.patch.object(S, "plan_view", side_effect=racing_view), \
                mock.patch.dict(os.environ, {"SALVAGE_RUNS": str(self.runs), "SALVAGE_PLAN": str(board)}), \
                mock.patch.object(sys, "argv", ["salvage.py", "promote"]), mock.patch.dict(sys.modules, stubs), \
                contextlib.redirect_stdout(out):
            rc = S.main()
        self.assertEqual(rc, 0)
        self.assertEqual((run / "STATUS").read_bytes(), QUARANTINE)
        self.assertTrue(S.refused_at_landing(str(run)))
        self.assertIn("SALVAGE refused X.a", out.getvalue())
        self.assertIn("SALVAGE promoted 0: none", out.getvalue())

    def test_any_change_since_selection_is_refused_and_touches_nothing(self):
        """A change that is NOT a quarantine: only the snapshot compare can refuse it."""
        run, _ = plant(self.runs, "X.a", "010000")
        c = select(self.runs, "X.a")
        changed = b"EXHAUSTED after 6 rounds, a later runner wrote this\n"
        with open(str(run / "STATUS"), "wb") as f: f.write(changed)   # same file, so the folder mtime stays put
        before = folder_state(run)
        why = S.promote(c)
        self.assertTrue(why and "changed since selection" in why, why)
        self.assertEqual((run / "STATUS").read_bytes(), changed)
        self.assertEqual(folder_state(run), before)   # no .before-salvage, no temp file, folder mtime unmoved

    def test_a_status_that_appears_after_selection_is_refused(self):
        run, _ = plant(self.runs, "X.a", "010000", status=None)   # killed mid way: no STATUS at selection
        c = select(self.runs, "X.a")
        (run / "STATUS").write_bytes(b"STALLED dead runner pid 1\n")
        self.assertTrue(S.promote(c))
        self.assertEqual((run / "STATUS").read_bytes(), b"STALLED dead runner pid 1\n")

    def test_a_status_that_vanished_after_selection_is_refused(self):
        run, _ = plant(self.runs, "X.a", "010000")
        c = select(self.runs, "X.a")
        (run / "STATUS").unlink()
        self.assertTrue(S.promote(c))
        self.assertFalse((run / "STATUS").exists())

    def test_an_unreadable_quarantine_is_never_selected(self):
        """An unreadable STATUS is unknown, never absent: a quarantine salvage cannot read is left alone."""
        run, _ = plant(self.runs, "X.a", "010000", status=QUARANTINE)
        os.chmod(str(run / "STATUS"), 0); self.addCleanup(os.chmod, str(run / "STATUS"), 0o644)
        view = S.plan_view(S.candidates(str(self.runs), {"X.a"}), lambda b: "", "shadow", str(self.runs))
        actions = [c["action"] for c in view]
        self.assertNotIn("PROMOTE", actions)
        self.assertTrue(any("unreadable" in a for a in actions), actions)

    def test_a_status_unreadable_at_promotion_is_refused_and_kept(self):
        run, _ = plant(self.runs, "X.a", "010000")
        c = select(self.runs, "X.a")
        (run / "STATUS").write_bytes(QUARANTINE)
        os.chmod(str(run / "STATUS"), 0); self.addCleanup(os.chmod, str(run / "STATUS"), 0o644)
        why = S.promote(c)
        self.assertTrue(why and "unreadable" in why, why)
        os.chmod(str(run / "STATUS"), 0o644)
        self.assertEqual((run / "STATUS").read_bytes(), QUARANTINE)

    def test_plan_view_leaves_a_candidate_whose_newest_run_is_unreadable(self):
        """Entry point plan_view (2026-09-30): the newest run of X.a holds a STATUS that exists but cannot be
        read, so the older clean build is LEFT, never promoted over an unknown."""
        plant(self.runs, "X.a", "010000", age=60)
        newer, _ = plant(self.runs, "X.a", "020000")
        os.chmod(str(newer / "STATUS"), 0); self.addCleanup(os.chmod, str(newer / "STATUS"), 0o644)
        view = S.plan_view(S.candidates(str(self.runs), {"X.a"}), lambda b: "", "shadow", str(self.runs))
        older = [c for c in view if c["run"].endswith("010000")]
        self.assertEqual(len(older), 1, [c["run"] for c in view])
        self.assertEqual(older[0]["action"], "leave: newest run of X.a is UNREADABLE")

    def test_promote_refuses_when_a_newer_run_with_an_unreadable_status_appeared(self):
        """Entry point promote (2026-09-30): a run of X.a newer than the selected one appears after selection with
        a STATUS that cannot be read; the promotion is refused and the selected STATUS is untouched."""
        run, _ = plant(self.runs, "X.a", "010000", age=60)
        c = select(self.runs, "X.a")
        newer, _ = plant(self.runs, "X.a", "020000")
        os.chmod(str(newer / "STATUS"), 0); self.addCleanup(os.chmod, str(newer / "STATUS"), 0o644)
        why = S.promote(c)
        self.assertTrue(why and "UNREADABLE" in why, why)
        self.assertEqual((run / "STATUS").read_bytes(), b"EXHAUSTED after 5 rounds\n")

    def test_a_candidate_with_no_selection_snapshot_is_refused(self):
        run, build = plant(self.runs, "X.a", "010000")
        c = {"sub": "X.a", "run": str(run), "build": str(build), "checker": "none"}
        self.assertTrue(S.promote(c))
        self.assertEqual((run / "STATUS").read_bytes(), b"EXHAUSTED after 5 rounds\n")

    def test_a_newer_ready_run_after_selection_blocks_the_promotion(self):
        run, _ = plant(self.runs, "X.a", "010000", age=600)
        c = select(self.runs, "X.a")
        newer = self.runs / "X.a-020000"; newer.mkdir()
        (newer / "STATUS").write_bytes(b"READY /elsewhere/X.a-r0-build.json (round 0)\n")
        t = time.time() + 5; os.utime(str(newer), (t, t))
        why = S.promote(c)
        self.assertTrue(why and "READY" in why, why)
        self.assertEqual((run / "STATUS").read_bytes(), b"EXHAUSTED after 5 rounds\n")

    def test_a_runner_started_after_selection_blocks_the_promotion(self):
        run, _ = plant(self.runs, "X.a", "010000", age=600)
        c = select(self.runs, "X.a")
        newer = self.runs / "X.a-020000"; newer.mkdir()
        (newer / "PID").write_text(str(os.getpid()))   # alive, no STATUS yet: a runner at work
        t = time.time() + 5; os.utime(str(newer), (t, t))
        why = S.promote(c)
        self.assertTrue(why and "RUNNING" in why, why)
        self.assertEqual((run / "STATUS").read_bytes(), b"EXHAUSTED after 5 rounds\n")

    def test_promote_waits_for_the_shared_status_lock(self):
        """A writer holding <runs>/.status.lock is waited for, and what it wrote is then seen and kept."""
        run, _ = plant(self.runs, "X.a", "010000")
        c = select(self.runs, "X.a")
        result = []
        with open(str(self.runs / ".status.lock"), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            t = threading.Thread(target=lambda: result.append(S.promote(c))); t.start()
            time.sleep(0.4)
            waited = t.is_alive()
            (run / "STATUS").write_bytes(QUARANTINE)
        t.join(10)
        self.assertTrue(waited, "promote wrote without waiting for the lock")
        self.assertTrue(result and result[0], result)
        self.assertEqual((run / "STATUS").read_bytes(), QUARANTINE)

    def test_an_unchanged_status_is_promoted_and_the_old_one_kept(self):
        run, build = plant(self.runs, "X.a", "010000")
        c = select(self.runs, "X.a")
        self.assertEqual(S.promote(c), "")
        st = (run / "STATUS").read_text().split()
        self.assertEqual(st[:2], ["READY", str(build)])
        self.assertEqual((run / "STATUS.before-salvage").read_bytes(), b"EXHAUSTED after 5 rounds\n")
        self.assertEqual(S.newest_status(str(self.runs), "X.a")[0], "READY")

    def test_a_lane_log_naming_no_pass_build_is_not_a_candidate(self):
        run, _ = plant(self.runs, "X.a", "010000")
        (run / "round0" / "lane.log").write_text("X.a FAIL X.a-r0\n")
        self.assertEqual(S.candidates(str(self.runs), {"X.a"}), [])

    def test_a_selftest_that_raises_exits_nonzero(self):
        with mock.patch.object(S, "_selftest_body", side_effect=RuntimeError("boom")), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(S.selftest(), 1)
        self.assertIn("FAILED before it could finish", out.getvalue())

    def test_newest_run_is_by_clock_not_by_folder_name_across_midnight(self):
        """HHMMSS wraps at midnight: 235959 from last night is OLDER than 000100 from this morning."""
        plant(self.runs, "X.a", "235959", age=600)
        late, _ = plant(self.runs, "X.a", "000100", age=60)
        self.assertEqual(S.newest_status(str(self.runs), "X.a")[1], str(late))
        view = S.plan_view(S.candidates(str(self.runs), {"X.a"}), lambda b: "", "shadow", str(self.runs))
        promoted = [c["run"] for c in view if c["action"] == "PROMOTE"]
        self.assertEqual(promoted, [str(late)])



class AnUnreadableStatusIsNotDead(unittest.TestCase):
    """2026-09-30: any OSError reading STATUS fell through to the PID check, so an unreadable STATUS read DEAD."""

    def test_unreadable_status_with_a_gone_pid_reads_unreadable_never_dead(self):
        with tempfile.TemporaryDirectory() as d:
            runs = Path(d)
            run, _ = plant(runs, "X.a", "100000")
            (run / "PID").write_text("999999\n")
            os.chmod(run / "STATUS", 0)
            try:
                word, _ = S.newest_status(str(runs), "X.a")
            finally:
                os.chmod(run / "STATUS", 0o600)
            self.assertEqual(word, "UNREADABLE")

if __name__ == "__main__":
    unittest.main()
