#!/usr/bin/env python3
"""PG1.g: plan_clock reads the four bars from real timestamps; a missing time is NO-DATA, never a pass."""
import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plan_clock as PC  # noqa: E402

T0 = 1_800_000_000.0   # a fixed close time


def touch(path, mtime, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    os.utime(path, (mtime, mtime))


def stamp(t):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))


class World:
    def __init__(self, root):
        self.root = root
        self.handover = os.path.join(root, "handovers")
        self.specs = os.path.join(root, "specs")
        self.runs = os.path.join(root, "runs")
        self.scores = os.path.join(root, "SCORES.json")
        self.plan = os.path.join(root, "plan.json")
        os.makedirs(self.runs)
        pack = os.path.join(self.handover, "2026-10-15")
        os.makedirs(pack)
        os.utime(pack, (T0, T0))
        with open(self.plan, "w") as fh:
            json.dump({"units": [{"id": "AA1", "sub_units": ["AA1.a", "AA1.b"]}, {"id": "BB1", "sub_units": ["BB1.a"]}]}, fh)
        touch(os.path.join(self.specs, "AA1.md"), T0 + 100)
        touch(os.path.join(self.specs, "BB1.md"), T0 + 250)
        self.set_scores({"AA1.a": 9, "AA1.b": 10, "BB1.a": 9})
        self.run("AA1.a", "000100", admit=T0 + 400, landed=T0 + 2000)

    def set_scores(self, d):
        with open(self.scores, "w") as fh:
            json.dump(d, fh)

    def run(self, sub, tag, admit=None, landed=None):
        run = os.path.join(self.runs, "%s-%s" % (sub, tag))
        os.makedirs(os.path.join(run, "round0"), exist_ok=True)
        if admit is not None:
            touch(os.path.join(run, "round0", "jobs.json"), admit, "[]")
        if landed is not None:
            touch(os.path.join(run, "STATUS"), landed, "LANDED %s/round0/out/b.json at %s commit abc\n" % (run, stamp(landed)))

    def argv(self, *extra):
        return ["--version", "1.1.5", "--handover-root", self.handover, "--scores", self.scores, "--specs", self.specs,
                "--runs", self.runs, "--plan", self.plan] + list(extra)

    def main(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = PC.main(self.argv(*extra))
        return rc, out.getvalue()


class TestPG1G(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.w = World(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_close_time_is_the_newest_pack_or_none(self) -> None:
        older = os.path.join(self.w.handover, "2026-10-01")
        os.makedirs(older)
        os.utime(older, (T0 - 9999, T0 - 9999))
        self.assertEqual(PC.close_time(self.w.handover), T0)
        empty = os.path.join(self.w.root, "nopacks")
        os.makedirs(empty)
        self.assertIsNone(PC.close_time(empty))
        self.assertIsNone(PC.close_time(os.path.join(self.w.root, "absent")))

    def test_spec_times_follow_the_floor(self) -> None:
        subs = PC.plan_subs_of(self.w.plan)
        st = PC.spec_times(self.w.scores, self.w.specs, 9, subs)
        self.assertEqual((st["first_spec_9"], st["all_specs_9"]), (T0 + 100, T0 + 250))
        self.w.set_scores({"AA1.a": 9, "AA1.b": 8, "BB1.a": 9, "ZZ9.a": 10})
        st = PC.spec_times(self.w.scores, self.w.specs, 9, subs)
        self.assertEqual((st["first_spec_9"], st["all_specs_9"]), (T0 + 250, None))
        self.assertEqual(st["ignored"], ["ZZ9.a"])
        self.w.set_scores({"AA1.a": 8, "AA1.b": 8, "BB1.a": True})
        st = PC.spec_times(self.w.scores, self.w.specs, 9, subs)
        self.assertEqual((st["first_spec_9"], st["all_specs_9"]), (None, None))
        with open(self.w.scores, "w") as fh:
            fh.write("{not json")
        st = PC.spec_times(self.w.scores, self.w.specs, 9, subs)
        self.assertEqual((st["first_spec_9"], st["all_specs_9"]), (None, None))

    def test_loop_times_come_from_the_run_directories(self) -> None:
        subs = PC.plan_subs_of(self.w.plan)
        self.w.run("BB1.a", "000200", admit=T0 + 300)
        self.w.run("XX9.a", "000300", admit=T0 + 1, landed=T0 + 2)   # another plan's run
        lt = PC.loop_times(self.w.runs, subs)
        self.assertEqual(lt["first_admission"], T0 + 300)
        self.assertAlmostEqual(lt["first_landing"], T0 + 2000, delta=1)
        self.assertEqual(PC.loop_times(os.path.join(self.w.root, "absent"), subs),
                         {"first_admission": None, "first_landing": None})

    def test_a_missing_timestamp_is_no_data_never_pass(self) -> None:
        rows = PC.verdicts(T0, {"first_spec_9": T0 + 10}, PC.BARS, 4, 3)
        by = {r[0]: r for r in rows}
        self.assertEqual(by["first_spec_9"][2], "PASS")
        for bar in ("all_specs_9", "first_admission", "first_landing"):
            self.assertEqual(by[bar][2], "NO-DATA", by[bar])
            self.assertIsNone(by[bar][1])
        self.assertTrue(all(r[2] == "NO-DATA" for r in PC.verdicts(None, {b: T0 for b in PC.ORDER}, PC.BARS, 4, 3)))
        empty = os.path.join(self.w.root, "empty")
        os.makedirs(empty)
        rc, out = self.w.main("--runs", empty)
        self.assertEqual(rc, 2, out)

    def test_the_exit_follows_the_worst_verdict(self) -> None:
        self.w.run("BB1.a", "000200", admit=T0 + 300, landed=T0 + 900)
        rc, out = self.w.main()
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count("PASS "), 4, out)
        touch(os.path.join(self.w.specs, "BB1.md"), T0 + 5000)
        rc, out = self.w.main()
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL    all_specs_9", out)
        self.w.set_scores({"AA1.a": 1, "AA1.b": 1, "BB1.a": 1})
        rc, out = self.w.main()
        self.assertEqual(rc, 2, out)   # NO-DATA outranks FAIL
        self.assertEqual(PC.exit_code([]), 2)

    def test_the_detail_prints_the_width_arithmetic(self) -> None:
        rows = PC.verdicts(T0, {}, PC.BARS, 4, 39)
        detail = [r for r in rows if r[0] == "all_specs_9"][0][3]
        self.assertIn("width 4 over 39 sub units is 10 wave(s) x 190 s = 1900 s", detail)

    def test_hostile_inputs(self) -> None:
        future = PC.verdicts(T0, {"first_spec_9": T0 - 5}, PC.BARS, 4, 3)
        self.assertEqual(future[0][2], "FAIL")
        self.assertIn("clock before close", future[0][3])
        rc, out = self.w.main("--version", "1.1")
        self.assertEqual(rc, 2)
        with open(self.w.scores, "w") as fh:
            fh.write(" " * (PC.MAX_SCORE_BYTES + 1))
        st = PC.spec_times(self.w.scores, self.w.specs, 9, PC.plan_subs_of(self.w.plan))
        self.assertIsNone(st["first_spec_9"])


if __name__ == "__main__":
    unittest.main()
