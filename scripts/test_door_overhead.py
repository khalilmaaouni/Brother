#!/usr/bin/env python3
"""UX1.a: scripts/door_overhead.py, its sample rules and the bypass pin (docs/plan/specs/UX1.md).

The start and first action measures run the real files as subprocesses in fixtures under a temporary root; a patched
`_run_cmd` stands in only where a failure has to be forced or the argv and environment recorded. Python 3.9 and 3.13,
standard library only, no network; nothing is written into the repository.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import door_overhead as D
import fast_path

#: What fast_path.eligible answered for the fixture trivial unit, measured once at build time (2026-10-12).
PINNED_ELIGIBLE = True


def _tmp(case):
    root = tempfile.mkdtemp(prefix="test-door-overhead-")
    case.addCleanup(shutil.rmtree, root, True)
    return root


def _summary(n, lo, mid, hi):
    return {"n": n, "min": lo, "median": mid, "max": hi, "samples": [mid] * n, "claimable": n >= 5}


class Recorder(object):
    """Wraps the real _run_cmd: records every call and can force one run of a chosen script to answer `forced`."""

    def __init__(self, script=None, fail_at=None, forced=(1, b"", 5.0)):
        self.calls, self.script, self.fail_at, self.forced, self.seen = [], script, fail_at, forced, 0
        self.real = D._run_cmd

    def __call__(self, argv, cwd, env, stdin_bytes=None):
        self.calls.append((list(argv), cwd, dict(env)))
        if self.script is not None and self.script in argv:
            self.seen += 1
            if self.fail_at is None or self.seen == self.fail_at:
                return self.forced
        return self.real(argv, cwd, env, stdin_bytes)


class Summarize(unittest.TestCase):
    """REQ-UX1-01 and REQ-UX1-02."""

    def test_four_samples_not_claimable(self):
        s = D.summarize([4, 1, 3, 2])
        self.assertEqual((s["n"], s["min"], s["median"], s["max"]), (4, 1, 2.5, 4))
        self.assertIs(s["claimable"], False)

    def test_one_five_and_six_samples(self):
        one = D.summarize([7.5])
        self.assertEqual((one["n"], one["min"], one["median"], one["max"], one["claimable"]), (1, 7.5, 7.5, 7.5, False))
        five = D.summarize([5, 1, 4, 2, 3])
        self.assertEqual((five["n"], five["min"], five["median"], five["max"], five["claimable"]), (5, 1, 3, 5, True))
        six = D.summarize([6, 1, 5, 2, 4, 3])
        self.assertEqual((six["n"], six["median"], six["claimable"]), (6, 3.5, True))
        self.assertEqual(six["samples"], [6, 1, 5, 2, 4, 3])

    def test_bad_lists_refused(self):
        for bad in ([1, -2, 3], [1, "2", 3], [], [1, None], [True, 1], [1, float("nan")], "123"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                D.summarize(bad)


class MeasureStart(unittest.TestCase):
    """REQ-UX1-03 and REQ-UX1-04: the real session start, and a failed sample."""

    def test_real_start_five_runs(self):
        block = D.measure_start(5, _tmp(self))
        self.assertEqual(block["status"], "OK", block)
        self.assertEqual(block["reference_bytes"], 281)
        for key in ("bytes", "latency_ms"):
            self.assertEqual(block[key]["n"], 5)
            self.assertEqual(len(block[key]["samples"]), 5)
            self.assertIs(block[key]["claimable"], True)
        self.assertTrue(all(b <= 512 for b in block["bytes"]["samples"]), block["bytes"])
        self.assertTrue(all(ms > 0 for ms in block["latency_ms"]["samples"]))
        self.assertIn("interpreter start included", block["timing"])

    def test_start_runs_in_its_own_home(self):
        root = _tmp(self)
        rec = Recorder()
        with mock.patch.object(D, "_run_cmd", rec):
            D.measure_start(1, root)
        runs = [c for c in rec.calls if D.SESSIONSTART in c[0]]
        self.assertEqual(len(runs), 1)
        env = runs[0][2]
        self.assertTrue(env["HOME"].startswith(root), env["HOME"])
        self.assertNotEqual(env["HOME"], os.environ.get("HOME"))
        self.assertEqual(sorted(env), ["HOME", "PATH", "PYTHONDONTWRITEBYTECODE"])

    def test_failed_sample_is_no_data_and_kept(self):
        rec = Recorder(script=D.SESSIONSTART, fail_at=3)
        with mock.patch.object(D, "_run_cmd", rec):
            block = D.measure_start(5, _tmp(self))
        self.assertEqual(block["status"], "NO-DATA")
        self.assertEqual(len(block["bytes_samples"]), 5)
        self.assertEqual(block["bytes_samples"].count(None), 1)
        self.assertIsNone(block["bytes_samples"][2])
        self.assertNotIn("bytes", block)

    def test_missing_script_is_no_data_with_the_path(self):
        gone = os.path.join(_tmp(self), "bm_sessionstart.py")
        with mock.patch.object(D, "SESSIONSTART", gone):
            block = D.measure_start(5, _tmp(self))
        self.assertEqual(block["status"], "NO-DATA")
        self.assertIn(gone, block["why"])

    def test_git_init_failure_is_no_data(self):
        def no_git(argv, cwd, env, stdin_bytes=None):
            return (128, b"", 1.0) if argv[0] == "git" else (0, b"", 1.0)
        with mock.patch.object(D, "_run_cmd", no_git):
            self.assertEqual(D.measure_start(5, _tmp(self))["status"], "NO-DATA")
            self.assertEqual(D.measure_first_action(5, _tmp(self))["status"], "NO-DATA")
            self.assertEqual(D.bypass_unchanged(_tmp(self))["status"], "NO-DATA")


class MeasureFirstAction(unittest.TestCase):
    """REQ-UX1-05."""

    def test_real_engine_five_runs_against_a_temporary_root(self):
        root = _tmp(self)
        rec = Recorder()
        with mock.patch.object(D, "_run_cmd", rec):
            block = D.measure_first_action(5, root)
        self.assertEqual(block["status"], "OK", block)
        self.assertEqual(block["latency_ms"]["n"], 5)
        self.assertEqual(block["sentence_seen"], [True] * 5)
        runs = [c[0] for c in rec.calls if D.BROTHER_RUN in c[0]]
        self.assertEqual(len(runs), 5)
        for argv in runs:
            self.assertIn("--continue", argv)
            self.assertIn("--runs-root", argv)
            self.assertTrue(argv[argv.index("--runs-root") + 1].startswith(root))
            self.assertTrue(argv[argv.index("--cwd") + 1].startswith(root))

    def test_output_without_the_sentence_is_no_data(self):
        rec = Recorder(script=D.BROTHER_RUN, fail_at=2, forced=(0, b"brother_run: something else\n", 5.0))
        with mock.patch.object(D, "_run_cmd", rec):
            block = D.measure_first_action(5, _tmp(self))
        self.assertEqual(block["status"], "NO-DATA")
        self.assertEqual(block["sentence_seen"], [True, False, True, True, True])
        self.assertEqual(len(block["latency_samples"]), 5)


class Compare(unittest.TestCase):
    """REQ-UX1-06."""

    def test_fewer_than_five_samples_give_no_percent(self):
        for n in (1, 4):
            out = D.compare(_summary(n, 10, 10, 10), _summary(n, 50, 50, 50))
            self.assertEqual(out, {"percent": None, "why": "fewer than 5 samples"})
        out = D.compare(_summary(5, 10, 10, 10), _summary(4, 50, 50, 50))
        self.assertIsNone(out["percent"])

    def test_overlapping_ranges_claim_no_difference(self):
        out = D.compare(_summary(5, 10, 20, 30), _summary(5, 25, 22, 40))
        self.assertIs(out["overlap"], True)
        self.assertEqual(out["sentence"], "no difference is claimed")

    def test_separated_ranges_give_a_percent(self):
        out = D.compare(_summary(5, 90, 100, 110), _summary(6, 40, 50, 60))
        self.assertIs(out["overlap"], False)
        self.assertAlmostEqual(out["percent"], -50.0)
        self.assertNotEqual(out["sentence"], "no difference is claimed")


class Bypass(unittest.TestCase):
    """REQ-UX1-07."""

    def test_bypass_answer_equals_the_pin(self):
        out = D.bypass_unchanged(_tmp(self))
        self.assertEqual(out["status"], "OK")
        self.assertIs(out["eligible"], PINNED_ELIGIBLE, out["reason"])

    def test_bypass_answer_comes_from_fast_path(self):
        with mock.patch.object(fast_path, "eligible", return_value=(not PINNED_ELIGIBLE, "sentinel")) as m:
            out = D.bypass_unchanged(_tmp(self))
        self.assertEqual(m.call_count, 1)
        self.assertIs(out["eligible"], not PINNED_ELIGIBLE)
        self.assertEqual(out["reason"], "sentinel")


def _good_doc(n=5):
    return {"commit": "abc", "date": "2026-10-12T00:00:00", "machine": "Darwin x arm64", "n": n, "timing": "t",
            "start": {"status": "OK", "bytes": _summary(n, 0, 0, 0), "latency_ms": _summary(n, 1.0, 2.0, 3.0),
                      "reference_bytes": 281},
            "first_action": {"status": "OK", "latency_ms": _summary(n, 1.0, 2.0, 3.0)},
            "bypass": {"status": "OK", "eligible": True, "reason": "r"}}


class CheckResult(unittest.TestCase):
    """REQ-UX1-08 (the real result is checked in MainLeavesTheRepository)."""

    def test_good_document_holds(self):
        self.assertEqual(D.check_result(_good_doc()), [])
        self.assertEqual(D.check_result(json.dumps(_good_doc(1))), [])

    def test_each_fault_refuses(self):
        doc = _good_doc(); del doc["machine"]
        self.assertTrue(any("machine" in p for p in D.check_result(doc)))
        doc = _good_doc(); doc["start"]["bytes"]["n"] = 4
        self.assertTrue(D.check_result(doc))
        doc = _good_doc(); doc["n"] = True
        self.assertTrue(D.check_result(doc))
        doc = _good_doc(4); doc["first_action"]["latency_ms"]["percent"] = 12.0
        self.assertTrue(any("percentage" in p for p in D.check_result(doc)))
        doc = _good_doc(); del doc["start"]["reference_bytes"]
        self.assertTrue(any("reference" in p for p in D.check_result(doc)))
        self.assertEqual(D.check_result("{not json"), ["the result is not JSON"])
        self.assertTrue(D.check_result("[1, 2]"))


def _porcelain():
    r = subprocess.run(["git", "status", "--porcelain", "--ignored"], cwd=REPO, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
    return r.returncode, r.stdout


def _runs_listing():
    path = os.path.join(REPO, "docs", "plan", "runs")
    return sorted(os.listdir(path)) if os.path.isdir(path) else None


class MainLeavesTheRepository(unittest.TestCase):
    """REQ-UX1-09, the real result for REQ-UX1-08, and the command line edges."""

    def test_main_writes_only_its_out_file(self):
        out_dir = _tmp(self)
        out = os.path.join(out_dir, "door-overhead.json")
        before, runs_before = _porcelain(), _runs_listing()
        with mock.patch("sys.stdout", new_callable=io.StringIO) as printed:
            code = D.main(["--n", "5", "--out", out])
        self.assertEqual(code, 0, printed.getvalue())
        self.assertEqual(_porcelain(), before)
        self.assertEqual(_runs_listing(), runs_before)
        self.assertEqual(os.listdir(out_dir), ["door-overhead.json"])
        with io.open(out, encoding="utf-8") as fh:
            text = fh.read()
        self.assertEqual(D.check_result(text), [])
        doc = json.loads(text)
        self.assertEqual(doc["bypass"]["eligible"], PINNED_ELIGIBLE)
        self.assertTrue(doc["machine"])
        lines = printed.getvalue().splitlines()
        self.assertTrue(lines[0].startswith("start bytes n=5 min="), lines)
        self.assertTrue(lines[0].endswith("(reference 281)"), lines)

    def test_one_sample_claims_nothing(self):
        out = os.path.join(_tmp(self), "r.json")
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(D.main(["--n", "1", "--out", out]), 0)
        with io.open(out, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertIs(doc["start"]["bytes"]["claimable"], False)
        self.assertIs(doc["first_action"]["latency_ms"]["claimable"], False)
        self.assertIsNone(D.compare(doc["start"]["latency_ms"], doc["start"]["latency_ms"])["percent"])

    def test_bad_n_is_a_usage_error(self):
        for argv in (["--n", "0"], ["--n", "abc"], ["--n", "-1"], ["--bogus"]):
            with mock.patch("sys.stderr", new_callable=io.StringIO):
                with self.assertRaises(SystemExit) as cm:
                    D.main(argv)
            self.assertEqual(cm.exception.code, 2, argv)

    def test_no_data_block_exits_2(self):
        no = {"status": "NO-DATA", "why": "w", "timing": "t", "reference_bytes": 281}
        with mock.patch.object(D, "measure_start", return_value=no), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as printed:
            self.assertEqual(D.main(["--n", "1"]), 2)
        self.assertIn("start NO-DATA", printed.getvalue())


HOSTILE = [None, 0, True, -1, float("nan"), "", "x", b"x", [], ["x"], {}, {"a": 1}, (), {1, 2}, object()]
REFUSALS = (ValueError, LookupError, SystemExit)


class HostileInputs(unittest.TestCase):
    """Every public function, each hostile value in every positional parameter: it returns or refuses."""

    def _call(self, fn, nargs, value):
        try:
            fn(*([value] * nargs))
        except REFUSALS:
            pass

    def test_every_public_function(self):
        quick = {"status": "NO-DATA", "why": "w", "eligible": None, "reason": "r"}
        for value in HOSTILE:
            self._call(D.summarize, 1, value)
            self._call(D.measure_start, 2, value)
            self._call(D.measure_first_action, 2, value)
            self._call(D.bypass_unchanged, 1, value)
            self._call(D.compare, 2, value)
            self.assertIsInstance(D.check_result(value), list)
            with mock.patch.object(sys, "argv", ["door_overhead.py", "--n", "abc"]), \
                    mock.patch.object(D, "measure_start", return_value=quick), \
                    mock.patch.object(D, "measure_first_action", return_value=quick), \
                    mock.patch.object(D, "bypass_unchanged", return_value=quick), \
                    mock.patch("sys.stdout", new_callable=io.StringIO), \
                    mock.patch("sys.stderr", new_callable=io.StringIO):
                self._call(D.main, 1, value)

    def test_hostile_values_are_refused_not_accepted(self):
        for value in HOSTILE:
            for fn, nargs in ((D.summarize, 1), (D.measure_start, 2), (D.measure_first_action, 2),
                              (D.bypass_unchanged, 1), (D.compare, 2)):
                with self.assertRaises(ValueError, msg="%s(%r)" % (fn.__name__, value)):
                    fn(*([value] * nargs))
            self.assertTrue(D.check_result(value), repr(value))


if __name__ == "__main__":
    unittest.main()
