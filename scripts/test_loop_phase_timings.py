#!/usr/bin/env python3
"""PH1.a (docs/plan/specs/PH1.md): the phase ledger read from the real log line shapes, through fixture logs in a temp
folder. Nothing here runs a loop, a driver or a cut.

Python 3.9, standard library only. No network. No em or en dashes.
"""
import contextlib
import io
import json
import math
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import loop_phase_timings as T  # noqa: E402

# the shapes as the tools print them (start_run.sh, loop_until.sh, land_batch.py, stop_loop.sh, cut_driver.phase_line)
LAUNCH = ["TIME preflight 3\n", "TIME controls 1\n", "TIME intake 12\n", "TIME driver 4\n",
          "OK   driver pid 4242: LOOP UNTIL 05:00 | gap 120s | log /x/evidence/loop-until-20261017-2230.log\n",
          "STARTED: watch with: tail -f /x/evidence/loop-until-20261017-2230.log\n",
          "LAUNCH command to first claimed unit 41 s\n"]
DRIVER = ["LOOP UNTIL 05:00 | gap 120s | log /x/evidence/loop-until-20261017-2230.log\n",
          "NOTE STARTED: running until 05:00\n",
          "22:30:10 pass 1 done (rc 0)\n",
          "PULSE pass 1 | 0.2 h | landed 0 | grade ok 1/2 | probe ok 3/4 | checker LAND 0/6 (no answer 0) | parked 0 of 1 | running 2 | a | b\n",
          "22:40:00 pass 2 done (rc 0)\n",
          "LANDED  C1.a | units with every sub unit landed (run the unit done-check): C1 | C1.a usd 0.03 | log /tmp/l\n",
          "23:59:50 pass 3 done (rc 0)\n",
          "00:10:00 pass 4 done (rc 0)\n",
          "LANDED  PH1.a | units with every sub unit landed (run the unit done-check): none | PH1.a usd 0.02 | log /tmp/l\n",
          "an unknown kind of line\n",
          "RUN END 2026-10-18T05:00:00+09:00\n"]
STOP = ["DRAINED: no runner is alive after 35s; the loop is PAUSED; nothing was killed\n",
        "STOPPED: nothing of the loop is alive\n"]
CUT = ["PHASE ready PASS 2s main is clean\n", "PHASE freeze PASS 61s frozen at abc\n",
       "PHASE preflight NO-DATA 0s the log was lost\n"]
GATE = ["GATE py3.9 rc=0 rows=12 bad=[]\n", "REDEPLOY DONE: abc deployed and launched\n"]

HOSTILE = [None, 0, True, -1, float("nan"), "", "x", b"x", [], ["x"], {}, {"a": 1}, (), {1, 2}, object()]


def by_phase(rows, phase):
    return [r for r in rows if r["phase"] == phase]


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ph1a-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def log(self, name, lines):
        path = os.path.join(self.root, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("".join(lines))
        return path

    def main(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                rc = T.main(argv)
            except SystemExit as exc:
                rc = exc.code
        return rc, out.getvalue()

    def ledger(self):
        with open(os.path.join(self.root, T.LEDGER), encoding="utf-8") as fh:
            return [json.loads(x) for x in fh]


class Requirements(Base):
    def test_a_missing_log_reads_no_data(self):
        """REQ-PH1A-01: a missing log is seconds null with the reason, never 0."""
        missing = os.path.join(self.root, "no-such.log")
        rows = T.phases({missing: T._read_source(missing)})
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["seconds"])
        self.assertIn("unreadable", rows[0]["reason"])
        self.assertIn("no-such.log", rows[0]["reason"])
        rc, _ = self.main(["--out", self.root, self.log("ok.log", LAUNCH), missing])
        self.assertEqual(rc, 0)
        nulls = [r for r in self.ledger() if r["source"] == missing]
        self.assertEqual(len(nulls), 1)
        self.assertIsNone(nulls[0]["seconds"])
        self.assertNotEqual(nulls[0]["seconds"], 0)

    def test_real_line_shapes_parse(self):
        """REQ-PH1A-02: every shape of the night's logs parses into its row."""
        kinds = [e["kind"] for e in T.parse_lines(LAUNCH + DRIVER + STOP + CUT + GATE, "night.log")]
        for k in ("time", "started", "until", "pulse", "landed", "end", "stop", "phase", "gate", "redeploy", "clock"):
            self.assertIn(k, kinds)
        rows = T.phases({"/l/start.out": LAUNCH, "/l/driver.log": DRIVER, "/l/stop.out": STOP, "/l/cut.log": CUT})
        launch = by_phase(rows, "launch")
        self.assertEqual([(r["run"], r["seconds"]) for r in launch], [("loop-until-20261017-2230", 20)])
        run = by_phase(rows, "run")
        self.assertEqual(len(run), 1)
        self.assertEqual(run[0]["run"], "loop-until-20261017-2230")
        self.assertEqual(run[0]["started"], "2026-10-17T22:30:10")
        self.assertEqual(run[0]["ended"], "2026-10-18T05:00:00")
        self.assertEqual(run[0]["seconds"], 6 * 3600 + 29 * 60 + 50)
        land = by_phase(rows, "land")
        self.assertEqual([r["seconds"] for r in land], [590, 5400])      # 22:30:10 to 22:40:00, then past midnight
        self.assertEqual(land[1]["ended"], "2026-10-18T00:10:00")
        close = by_phase(rows, "close")
        self.assertEqual([r["seconds"] for r in close], [35.0, None])
        self.assertIn("no duration", close[1]["reason"])
        self.assertEqual(by_phase(rows, "cut freeze")[0]["seconds"], 61.0)
        nodata = by_phase(rows, "cut preflight")[0]
        self.assertIsNone(nodata["seconds"])                             # phase_line writes NO-DATA as 0s
        self.assertIn("NO-DATA", nodata["reason"])
        for r in rows:
            self.assertEqual(set(r), set(T.ROW_KEYS))

    def test_a_clock_step_back_clamps(self):
        """REQ-PH1A-03: a clock that steps back clamps at 0 and says so."""
        rows = T.phases({"/d.log": ["LOOP UNTIL 05:00 | gap 60s | log /e/loop-until-a.log\n",
                                    "22:30:10 pass 1 done (rc 0)\n",
                                    "LOOP DEADLINE at 2026-10-17 22:30:00+09:00\n"]})
        run = by_phase(rows, "run")[0]
        self.assertEqual(run["seconds"], 0)
        self.assertIn("stepped back 10 s", run["reason"])
        launch = by_phase(T.phases({"/s.out": ["TIME intake 5\n", "TIME driver -3\n"]}), "launch")[0]
        self.assertEqual(launch["seconds"], 5)
        self.assertIn("stepped back", launch["reason"])

    def test_summary_order(self):
        """REQ-PH1A-04: slowest first, nulls last in file order."""
        rows = [T._row("r", p, None, None, s, "/s", None) for p, s in
                (("a", None), ("b", 3), ("c", 90), ("d", None), ("e", 0), ("f", 12.5))]
        self.assertEqual([r["phase"] for r in T.summary(rows)], ["c", "f", "b", "e", "a", "d"])
        rc, out = self.main(["--out", self.root, "--summary", self.log("c.log", CUT)])
        self.assertEqual(rc, 0)
        listed = [x.split()[1] for x in out.splitlines() if not x.startswith("LEDGER")]
        self.assertEqual(listed, ["cut", "cut", "cut"])
        self.assertTrue(out.splitlines()[0].startswith("61s"))
        self.assertTrue(out.splitlines()[2].startswith("NO-DATA"))

    def test_nothing_readable_exits_three(self):
        """REQ-PH1A-05: no source readable exits 3, and the ledger still holds the null rows."""
        missing = os.path.join(self.root, "gone.log")
        rc, out = self.main(["--run", self.root, missing, self.root])
        self.assertEqual(rc, 3)
        rows = self.ledger()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["seconds"] is None for r in rows))
        self.assertIn("is a directory", rows[1]["reason"])
        self.assertEqual(self.main(["--run", self.root, self.log("e.log", [])])[0], 0)   # empty is read: null, exit 0


class Edges(Base):
    def test_empty_log_is_one_null_row(self):
        rows = T.phases({"/e.log": []})
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["phase"])
        self.assertIsNone(rows[0]["seconds"])
        self.assertEqual(rows[0]["reason"], "the log is empty")
        self.assertEqual(T.phases({"/u.log": ["nothing known\n"]})[0]["reason"], "no line of a known shape")

    def test_torn_last_line_is_dropped(self):
        self.assertEqual(len(T.parse_lines(["TIME a 1\n", "TIME b 2"], "s")), 1)
        launch = by_phase(T.phases({"/s": ["TIME a 1\n", "TIME b 9"]}), "launch")[0]
        self.assertEqual(launch["seconds"], 1)

    def test_non_numeric_seconds_is_null(self):
        launch = by_phase(T.phases({"/s": ["TIME a 1\n", "TIME b many\n"]}), "launch")[0]
        self.assertIsNone(launch["seconds"])
        self.assertIn("not a number", launch["reason"])
        nodata = by_phase(T.phases({"/s": ["TIME NO-DATA driver: the clock could not be read\n"]}), "launch")[0]
        self.assertIsNone(nodata["seconds"])
        self.assertIn("clock could not be read", nodata["reason"])

    def test_run_without_deadline_has_ended_null(self):
        run = by_phase(T.phases({"/d": DRIVER[:5]}), "run")[0]
        self.assertIsNone(run["ended"])
        self.assertIsNone(run["seconds"])
        self.assertIn("no LOOP DEADLINE", run["reason"])

    def test_two_launches_are_two_runs(self):
        two = ["TIME a 2\n", "STARTED: watch with: tail -f /e/loop-until-one.log\n",
               "TIME a 5\n", "STARTED: watch with: tail -f /e/loop-until-two.log\n"]
        self.assertEqual([(r["run"], r["seconds"]) for r in T.phases({"/s": two})],
                         [("loop-until-one", 2), ("loop-until-two", 5)])
        drv = ["LOOP UNTIL 05:00 | gap 1s | log /e/a.log\n", "01:00:00 x\n",
               "LOOP UNTIL 05:00 | gap 1s | log /e/b.log\n", "02:00:00 x\n", "RUN END 2026-10-18T03:00:00+09:00\n"]
        runs = by_phase(T.phases({"/d": drv}), "run")
        self.assertEqual([(r["run"], r["seconds"]) for r in runs], [("a", None), ("b", 3600)])

    def test_stale_log_keeps_its_own_run_name(self):
        other = self.log("old.log", ["LOOP UNTIL 05:00 | gap 1s | log /e/loop-until-older.log\n",
                                     "RUN END 2026-10-01T05:00:00\n"])
        self.assertEqual(self.main(["--run", self.root, other])[0], 0)
        self.assertEqual(self.ledger()[0]["run"], "loop-until-older")

    def test_second_write_appends(self):
        rows = T.phases({"/c": CUT})
        path = T.write_ledger(rows, self.root)
        self.assertEqual(T.write_ledger(rows, self.root), path)
        self.assertEqual(len(self.ledger()), 2 * len(rows))

    def test_hostile_logs(self):
        big = self.log("big.log", ["TIME a " + "9" * (1 << 20) + "\n", "TIME b 1\n"])
        far = self.log("far.log", ["LOOP UNTIL 05:00 | gap 1s | log /e/f.log\n", "9999-12-31T23:59:58Z x\n",
                                   "RUN END 9999-12-31T23:59:59Z\n", "00:00:05 past the last day\n"])
        rc, _ = self.main(["--out", self.root, big, far, self.root])
        self.assertEqual(rc, 0)
        rows = self.ledger()
        self.assertEqual(by_phase(rows, "launch")[0]["seconds"], 1)
        self.assertIsNone(by_phase(rows, "run")[0]["seconds"])
        self.assertIn("is a directory", [r for r in rows if r["source"] == self.root][0]["reason"])

    def test_usage_errors_exit_two(self):
        self.assertEqual(self.main([])[0], 2)
        self.assertEqual(self.main(["x.log"])[0], 2)
        self.assertEqual(self.main(["--out", os.path.join(self.root, "nope"), "x.log"])[0], 2)

    def test_hostile_values_refuse(self):
        allowed = (ValueError, LookupError, SystemExit, T.TimingsError)
        fns = {"parse_lines": 2, "phases": 1, "write_ledger": 2, "summary": 1, "main": 1}
        for name, arity in fns.items():
            fn = {"parse_lines": T.parse_lines, "phases": T.phases, "write_ledger": T.write_ledger,
                  "summary": T.summary, "main": T.main}[name]
            for v in HOSTILE:
                with self.subTest(fn=name, value=repr(v)):
                    try:
                        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                            fn(*([v] * arity))
                    except allowed:
                        pass
        bad_rows = [[{"seconds": 1}], [dict(T._row("r", "p", None, None, True, "/s", None))],
                    [dict(T._row("r", "p", None, None, float("nan"), "/s", None))],
                    [dict(T._row("r", "p", None, None, -1, "/s", None))],
                    [dict(T._row("r", "p", None, None, "5", "/s", None))]]
        for rows in bad_rows:
            with self.subTest(rows=repr(rows)):
                self.assertRaises(ValueError, T.summary, rows)
                self.assertRaises(ValueError, T.write_ledger, rows, self.root)
        self.assertRaises(ValueError, T.parse_lines, "TIME a 1\n", "s")
        self.assertRaises(ValueError, T.parse_lines, [b"TIME a 1\n"], "s")
        self.assertRaises(ValueError, T.phases, {"/s": 3})
        self.assertRaises(ValueError, T.phases, {3: []})
        self.assertRaises(ValueError, T.write_ledger, [], os.path.join(self.root, "nope"))
        self.assertTrue(math.isnan(float("nan")))


class I15Zone(Base):
    """REQ-I15-01 to REQ-I15-08: zone aware stamps and JST clock lines read the wall clock."""

    def test_a_utc_stamp_reads_its_epoch(self):
        clocks = T._clocks(["2026-10-17T13:37:10Z\n"])
        self.assertEqual(len(clocks), 1)
        self.assertIsInstance(clocks[0], int)
        self.assertEqual(T._iso(clocks[0]), "2026-10-17T22:37:10")

    def test_a_stamp_without_a_zone_reads_no_data(self):
        self.assertEqual(T._clocks(["2026-10-17T13:37:10\n"]), [None])

    def test_a_utc_run_start_against_local_clock_lines_reads_the_wall_clock(self):
        clocks = T._clocks(["2026-10-17T13:37:10Z start\n", "22:44:10 end\n"])
        self.assertEqual(len(clocks), 2)
        self.assertIsInstance(clocks[0], int)
        self.assertIsInstance(clocks[1], int)
        self.assertEqual(clocks[1] - clocks[0], 420)

    def test_a_zone_abbreviation_reads_its_offset(self):
        clocks = T._clocks(["2026-10-17 22:30:00 CEST\n"])
        self.assertEqual(len(clocks), 1)
        self.assertIsInstance(clocks[0], int)
        self.assertEqual(T._iso(clocks[0]), "2026-10-18T05:30:00")

    def test_a_clock_line_anchors_to_the_nearest_full_stamp(self):
        clocks = T._clocks(["2026-10-17T06:00:00+09:00 early\n",
                            "2026-10-18T18:00:00+09:00 late\n",
                            "12:00:00 noon\n"])
        self.assertEqual(len(clocks), 3)
        for c in clocks:
            self.assertIsInstance(c, int)
        self.assertEqual(T._iso(clocks[2]), "2026-10-18T12:00:00")

    def test_a_far_future_stamp_reads_no_data(self):
        self.assertEqual(T._clocks(["9999-12-31T23:59:59Z\n"]), [None])


if __name__ == "__main__":
    unittest.main()
