#!/usr/bin/env python3
"""FR1.f (docs/plan/specs/FR1.md): progress on a --check, a line a minute while a step is silent, and the 2b lines
reach the log.

Measured 2026-10-13: a 1.1.2 rehearsal printed nothing for 68 minutes inside step 2b (refresh_cut captured the
child whole) and nothing between the lines of any silent step (cut._stream beat only on an output line, and a --check
holds no fence to beat). The cases below drive the real entry points: cut._stream with a real child process, cut.main
--check, perturb_pool.run_rows and refresh_cut.regenerate with a real child script. A fake clock and a fake wait stand
in for minutes; the real-child cases use an interval of a third of a second.

Python 3.9, standard library only. No network. No em or en dashes.
"""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cut as C  # noqa: E402
import perturb_pool as PP  # noqa: E402
import refresh_cut as RC  # noqa: E402
import test_cut as TC  # noqa: E402

try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n"
                     % os.path.basename(__file__))

V = "9.9.9"


class FakeClock(object):
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class TheSilenceTicker(unittest.TestCase):

    def test_a_61_s_silent_step_prints_a_still_running_line(self):
        clock, lines, calls = FakeClock(), [], []

        def wait(interval):
            calls.append(interval)
            clock.now += 61
            return len(calls) >= 3

        C._silence_ticker("required_fast.sh", 0.0, 60, lines.append, clock, threading.Event(), wait)
        self.assertEqual(len(lines), 2, lines)
        self.assertIn("still running required_fast.sh: 61 s", lines[0])
        self.assertIn("still running required_fast.sh: 122 s", lines[1])
        self.assertEqual(calls, [60, 60, 60])

    def test_a_stop_that_is_set_ends_the_ticker_without_a_line(self):
        stop, lines = threading.Event(), []
        stop.set()
        C._silence_ticker("x", 0.0, 60, lines.append, FakeClock(), stop)
        self.assertEqual(lines, [])
        # the wait said "not ended" yet the event is set: still no line
        C._silence_ticker("x", 0.0, 60, lines.append, FakeClock(), stop, wait=lambda s: False)
        self.assertEqual(lines, [])


class AStreamedStep(unittest.TestCase):

    def stream(self, code, interval_s, heartbeat=None):
        lines = []
        beat = heartbeat or C.ProgressBeat(interval_s=interval_s, emit=lines.append)
        proc = C._stream("child", [sys.executable, "-c", code], os.getcwd(), emit=lines.append, heartbeat=beat)
        return proc, lines

    def test_a_real_silent_child_prints_a_line_at_a_short_interval(self):
        proc, lines = self.stream("import time; time.sleep(1.2); print('x')", 0.3)
        ticks = [l for l in lines if "still running child" in l]
        self.assertGreaterEqual(len(ticks), 2, lines)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("child | x", " ".join(lines))
        self.assertEqual([l for l in lines if "running, " in l], [], "the ticker speaks; the beat stays quiet on lines")

    def test_the_ticker_stops_with_the_step(self):
        proc, lines = self.stream("import time; time.sleep(0.7)", 0.3)
        self.assertIn("] end child: exit 0", lines[-1])
        count = len(lines)
        time.sleep(0.9)
        self.assertEqual(len(lines), count, lines[count:])

    def test_a_step_that_ends_under_the_interval_prints_no_ticker_line(self):
        proc, lines = self.stream("print('quick')", 30)
        self.assertEqual([l for l in lines if "still running" in l or "running," in l], [])

    def test_a_beat_without_ticks_still_beats_on_every_output_line(self):
        class Plain(object):
            def __init__(self):
                self.calls = []

            def maybe(self, label, force=False):
                self.calls.append((label, force))
                return True

        plain = Plain()
        proc, lines = self.stream("print(1); print(2); print(3)", 0, heartbeat=plain)
        self.assertEqual(plain.calls, [("child", True)] + [("child", False)] * 3 + [("child", True)])
        self.assertFalse([l for l in lines if "still running" in l])


class TheProgressBeat(unittest.TestCase):

    def test_progress_beat_never_touches_the_fence(self):
        clock, lines = FakeClock(), []
        beat = C.ProgressBeat(interval_s=60, clock=clock, emit=lines.append)
        boom = AssertionError("a ProgressBeat ran a command")
        with mock.patch.object(C, "_run", side_effect=boom), \
                mock.patch.object(subprocess, "Popen", side_effect=boom), \
                mock.patch.object(subprocess, "run", side_effect=boom):
            for now in (10, 61, 100, 130, 200):
                clock.now = now
                self.assertTrue(beat.maybe("export rows"))
            self.assertTrue(beat.maybe("export rows", force=True))
        self.assertTrue(lines)

    def test_it_says_a_line_only_after_the_interval_and_a_forced_call_only_restarts_the_clock(self):
        clock, lines = FakeClock(), []
        beat = C.ProgressBeat(interval_s=60, clock=clock, emit=lines.append)
        clock.now = 59
        beat.maybe("a")
        self.assertEqual(lines, [])
        clock.now = 61
        beat.maybe("a")
        self.assertEqual(len(lines), 1)
        self.assertIn("a: running, 61 s since the last line", lines[0])
        clock.now = 100
        beat.maybe("a", force=True)
        clock.now = 150
        beat.maybe("a")
        self.assertEqual(len(lines), 1, lines)

    def test_hostile_intervals_fall_back_to_the_default(self):
        for bad in (0, -1, -0.5, float("nan"), float("inf"), "5", None, True, [3], {}):
            with self.subTest(interval=bad):
                self.assertEqual(C.ProgressBeat(interval_s=bad).interval_s, float(C.HEARTBEAT_INTERVAL_S))
        self.assertEqual(C.ProgressBeat(interval_s=30).interval_s, 30.0)
        self.assertEqual(C.ProgressBeat(interval_s=0.3).interval_s, 0.3)


def _check(heartbeat_seen):
    """cut.main --check through test_cut's own harness, with the real chain wrapped so the heartbeat it gets is
    recorded. Returns (code, runner)."""
    real = C.chain
    runner = TC.Runner(TC.GREEN)

    def spy(*args, **kw):
        heartbeat_seen.append(kw.get("heartbeat"))
        return real(*args, **kw)

    with mock.patch.object(C, "chain", side_effect=spy):
        code, _ = TC._main(["--check"], runner)
    return code, runner


class WhoGetsWhichBeat(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        TC.setUpModule()  # the cut harness patches the gates, the canary and the export rows; undone below

    @classmethod
    def tearDownClass(cls):
        unittest.case.doModuleCleanups()

    def test_check_mode_passes_a_progress_beat_to_the_chain(self):
        seen = []
        code, runner = _check(seen)
        self.assertEqual(code, C.EXIT_OK, runner.calls)
        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0], C.ProgressBeat)
        self.assertFalse([c for c in runner.calls if "checkpoint" in c], runner.calls)

    def test_a_real_cut_still_gets_the_fence_heartbeat_not_the_progress_beat(self):
        seen = []
        real = C.chain

        def spy(*args, **kw):
            seen.append(kw.get("heartbeat"))
            return real(*args, **kw)

        with mock.patch.object(C, "chain", side_effect=spy):
            code, _ = TC._main(["--yes", "--release-notes-file", TC.CLEAN_NOTES_FILE], TC.Runner(TC.GREEN))
        self.assertEqual(code, C.EXIT_OK)
        self.assertTrue(seen)
        for beat in seen:
            self.assertIsInstance(beat, C.Heartbeat)
            self.assertNotIsInstance(beat, C.ProgressBeat)


def _scripted_pool(base, answers):
    """run_rows with a synchronous spawn: the 'worker' writes its out file from `answers` (file_rel -> verdict)."""
    rows = [("scripts/a.py", "scripts/test_x.py"), ("scripts/b.py", "scripts/test_x.py"),
            ("scripts/c.py", "scripts/test_y.py")]
    lines = []

    class Proc(object):
        returncode = 0

        def communicate(self):
            return b"", b""

    def spawn(argv, env):
        mine = __import__("json").load(open(argv[4]))
        with open(argv[6], "w", encoding="utf-8") as fh:
            __import__("json").dump([[f, s, answers[f], "d"] for f, s in mine], fh)
        return Proc()

    def copier(source, dest):
        os.makedirs(dest)
        with open(dest + PP.MARKER_SUFFIX, "w", encoding="utf-8") as fh:
            fh.write("x\n")

    with mock.patch.object(PP, "settled_workers", lambda n: n):
        got = PP.run_rows(rows, base, 5, workers=2, base=base, spawn=spawn, copier=copier,
                          say=lambda l: lines.append(l) if l.startswith("row ") else None)  # FR1.d2 adds workers lines
    return rows, got, lines


class ThePoolSaysARowAsItIsAnswered(unittest.TestCase):

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="fr1f-pool-")
        self.addCleanup(shutil.rmtree, self.base, True)

    def test_a_row_answered_prints_one_line(self):
        before = threading.active_count()
        rows, got, lines = _scripted_pool(self.base, {"scripts/a.py": True, "scripts/b.py": False,
                                                      "scripts/c.py": None})
        self.assertEqual(len(got), 3)
        self.assertEqual(len(lines), 3, lines)
        joined = "\n".join(lines)
        self.assertRegex(lines[0], r"^row 1/3 scripts/\w\.py -> scripts/test_\w\.py (red|NOT red|NO-DATA) after \d+ s$")
        self.assertEqual(sorted(l.split(" ")[1] for l in lines), ["1/3", "2/3", "3/3"])
        self.assertIn("scripts/a.py -> scripts/test_x.py red after", joined)
        self.assertIn("scripts/b.py -> scripts/test_x.py NOT red after", joined)
        self.assertIn("scripts/c.py -> scripts/test_y.py NO-DATA after", joined)
        self.assertEqual(threading.active_count(), before, "the watcher is joined before run_rows returns")

    def test_a_torn_answer_file_is_skipped_until_it_is_whole(self):
        path = os.path.join(self.base, "out0.json")
        lines, stop = [], threading.Event()
        stop.set()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('[["scripts/a.py", "scripts/test_x.py", tr')
        PP._watch_rows([path, os.path.join(self.base, "missing.json")], 1, lines.append, stop, poll_s=0)
        self.assertEqual(lines, [])
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('[["scripts/a.py", "scripts/test_x.py", true, "d"]]')
        PP._watch_rows([path], 1, lines.append, stop, poll_s=0)
        PP._watch_rows([path], 1, lines.append, stop, poll_s=0)
        self.assertEqual(len(lines), 2, "a second watcher has its own memory; each says the row once")
        PP._watch_rows([path, path], 1, lines.append, stop, poll_s=0)
        self.assertEqual(len(lines), 3, "one watcher says a row once however often it is read")

    def test_a_row_with_no_pair_shape_never_raises(self):
        path = os.path.join(self.base, "out0.json")
        for text in ('"text"', "[1, 2]", "[[]]", "{}", "null"):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            stop = threading.Event()
            stop.set()
            PP._watch_rows([path], 1, lambda l: None, stop, poll_s=0)


class RefreshCutStreamsTheChild(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="fr1f-refresh-")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "scripts"))

    def child(self, body):
        with open(os.path.join(self.root, "scripts", "release_note_from_tree.py"), "w", encoding="utf-8") as fh:
            fh.write("import sys, time\n" + body)

    class Stamped(object):
        """A stderr stand-in that stamps every write with the clock."""

        def __init__(self):
            self.writes = []

        def write(self, text):
            self.writes.append((time.monotonic(), text))
            return len(text)

        def flush(self):
            pass

    def test_refresh_cut_streams_the_child_progress_lines(self):
        self.child("sys.stderr.write('pool: 3 row(s)\\n'); sys.stderr.flush()\n"
                   "print('parsed line one'); sys.stdout.flush()\n"
                   "time.sleep(1.2)\n"
                   "sys.stderr.write('row 1/3 done\\n'); sys.stderr.flush()\n")
        mine = self.Stamped()
        with mock.patch.object(sys, "stderr", mine):
            ok, lines = RC.regenerate(V, root=self.root)
        finished = time.monotonic()
        self.assertTrue(ok, lines)
        self.assertEqual(lines, ["parsed line one"])
        first = [t for t, text in mine.writes if "pool: 3 row(s)" in text]
        self.assertTrue(first, mine.writes)
        self.assertGreater(finished - first[0], 0.8, "the first line must reach us while the child still runs")
        self.assertTrue([1 for _, text in mine.writes if "row 1/3 done" in text])

    def test_refresh_cut_keeps_the_failure_tail(self):
        self.child("sys.stderr.write('first\\n'); sys.stderr.write('boom happened\\n')\n"
                   "print('parsed line'); sys.exit(3)\n")
        with mock.patch.object(sys, "stderr", self.Stamped()):
            ok, lines = RC.regenerate(V, root=self.root)
        self.assertFalse(ok)
        self.assertEqual(lines[0], "parsed line")
        self.assertIn("exited 3: boom happened", lines[-1])

    def test_a_child_that_cannot_start_is_no_data(self):
        with mock.patch.object(subprocess, "Popen", side_effect=OSError("no such interpreter")):
            ok, lines = RC.regenerate(V, root=self.root)
        self.assertFalse(ok)
        self.assertIn("NO-DATA: could not run", lines[0])

    def test_invalid_utf8_on_the_childs_stderr_is_read_with_replacement(self):
        self.child("sys.stderr.buffer.write(b'bad \\xff byte\\n'); sys.exit(2)\n")
        with mock.patch.object(sys, "stderr", self.Stamped()):
            ok, lines = RC.regenerate(V, root=self.root)
        self.assertFalse(ok)
        self.assertIn("exited 2: bad", lines[-1])


class TheBatchLine(unittest.TestCase):

    def test_the_pool_batch_line_names_rows_wall_and_workers(self):
        import release_note_from_tree as RN
        import release_note_perturb as RP
        err = io.StringIO()
        seen = []

        def pool(pairs, root, wall, workers):
            seen.append((len(pairs), wall, workers))
            return [(f, s, True, "d") for f, s in pairs]

        with mock.patch.object(RP, "wall_for", return_value=300), \
                mock.patch("perturb_pool.free_gib", lambda path: 999.0), \
                mock.patch.object(RP, "run_suite", return_value=(0, "")), \
                mock.patch.object(RP, "sibling_suite", return_value=None), \
                mock.patch.object(RN, "subject_files", return_value=["scripts/a.py"]), \
                mock.patch("refresh_cut.dirty_paths", return_value=([], "")), \
                contextlib.redirect_stderr(err):
            RN.pooled_rows([("c1", "scripts/test_x.py")], RP, pool, workers=4, version=V)
        self.assertEqual(seen, [(1, 300, 4)])
        self.assertIn("pool: 1 row(s), wall 300 s, 4 worker(s) requested", err.getvalue())


if __name__ == "__main__":
    unittest.main()
