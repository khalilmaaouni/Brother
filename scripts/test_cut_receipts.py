#!/usr/bin/env python3
"""RC1.b2 (docs/plan/specs/RC1.md): on the adopt path (--resume with a covering rehearsal record) the cut asks only
the cheap preflight rows and reuses the rehearsal's rows on an exact key match: the hub required_fast, the
export-real-home row and the virgin-gate row. Any doubt (no record, a corrupt or non covering one, a key one byte
off, another home, a stale record, a FAIL row) runs the row exactly as before. The push carries the covering record
path, which the exporter checks again on its own (RC1.c).

HOW. The scripted Runner, GREEN answers and setUpModule seams of scripts/test_cut.py (its module patches are started
here too, so the evidence folder is a throwaway one and the chain's export rows are stubbed unless a case puts the
real function back); records are written with cut_rehearsal.record_rehearsal into a temporary folder.

Python 3.9, standard library only. No network. No em or en dashes.
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_cut as TC  # noqa: E402
from test_cut import C, GREEN, Runner, VERSION  # noqa: E402

CR = C.CR
HUB = "b" * 40
EXP = "a" * 40
TREES = dict((d, "d" * 40) for d in CR.SCRIPT_DIRS)
RESUME = TC.GREEN_HEAD


def setUpModule():
    TC.setUpModule()


def tearDownModule():
    # Python 3.9 runs the module cleanups TC.setUpModule registers only when this hook exists.
    pass


def capture(fn, *a, **k):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        got = fn(*a, **k)
    return got, out.getvalue()


class Rig(unittest.TestCase):
    """A temporary folder for records and row logs."""

    def setUp(self):
        import cut_preflight as P
        self.P = P
        self.tmp = tempfile.mkdtemp(prefix="test-cut-receipts-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.n = 0

    def row(self, name, key, home=None, verdict="PASS"):
        self.n += 1
        log = os.path.join(self.tmp, "row-%d.log" % self.n)
        with open(log, "w", encoding="utf-8") as fh:
            fh.write("row %s\n" % name)
        home = home or ("empty" if name == "virgin-gate" else "real")
        return {"row": name, "argv": ["sh", "x"], "key": key, "home": home, "verdict": verdict, "seconds": 1.0,
                "log_path": log, "log_sha256": CR._sha256_file(log)}

    def all_rows(self, **over):
        rows = {"required-fast-hub": self.row("required-fast-hub", HUB),
                "export-real-home": self.row("export-real-home", EXP),
                "virgin-gate": self.row("virgin-gate", EXP)}
        for name, row in over.items():
            rows[name.replace("_", "-")] = row
        return [r for r in rows.values() if r is not None]

    def record(self, rows, age_h=1.0):
        log = os.path.join(self.tmp, "rehearsal.log")
        with open(log, "w", encoding="utf-8") as fh:
            fh.write("READY for %s\n" % VERSION)
        path = CR.record_rehearsal(VERSION, RESUME, "c" * 40, 0, "READY", log, 1.0, "e" * 64,
                                   directory=self.tmp, now=time.time() - age_h * 3600, script_trees=TREES,
                                   rows=rows)
        rec, why = CR.load_record(path)
        self.assertEqual(why, "")
        return rec, path

    def chain(self, rec, path, answers=None):
        """(passed, failed, runner, stdout) of the real chain on --resume with `rec` as the reuse record."""
        r = Runner(dict(GREEN, **dict({"git rev-parse HEAD^{tree}": (0, HUB + "\n"),
                                       "cut_preflight.py": (0, "EXPORT-TREE: %s\n" % EXP),
                                       "export_public.py": (0, "EXPORT-TREE: %s\nCLEAR\n" % EXP)},
                                      **(answers or {}))))
        with mock.patch.object(self.P, "export_tree_builder", return_value=TC._fake_export_build), \
                mock.patch.object(self.P, "real_home_refusal", return_value=""), \
                mock.patch.object(C, "export_rows", TC.REAL_EXPORT_ROWS):
            (passed, failed), out = capture(C.chain, "/fake/root", VERSION, runner=r, resume=True, reuse=rec,
                                            reuse_path=path)
        return passed, failed, r, out


class TheCoveringRecordIsTheOnlyReuse(Rig):
    """REQ-RC1-11: a missing, corrupt or non covering record, or no resume at all, runs everything."""

    def test_no_resume_reads_no_record(self):
        with mock.patch.object(C, "_covering_record", side_effect=AssertionError("read a record")):
            rec, path, why = C.reuse_record("/fake/root", VERSION, None)
        self.assertEqual((rec, path), (None, ""))
        self.assertTrue(why)

    def test_a_refused_corrupt_or_missing_record_is_no_reuse(self):
        _rec, good = self.record(self.all_rows())
        corrupt = os.path.join(self.tmp, "rehearsal-%s-corrupt.json" % VERSION)
        with open(corrupt, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        for answer in (("", "REFUSED: no rehearsal record covers HEAD"), (corrupt, ""),
                       (os.path.join(self.tmp, "absent.json"), ""), ("", "")):
            with self.subTest(answer=answer):
                with mock.patch.object(C, "_covering_record", return_value=answer):
                    rec, path, why = C.reuse_record("/fake/root", VERSION, RESUME)
                self.assertEqual((rec, path), (None, ""))
                self.assertTrue(why)
        with mock.patch.object(C, "_covering_record", return_value=(good, "")) as cov:
            rec, path, why = C.reuse_record("/fake/root", VERSION, RESUME)
        self.assertEqual((path, why), (good, ""))
        self.assertEqual(len(rec["rows"]), 3)
        self.assertEqual(cov.call_args[0][:3], ("/fake/root", VERSION, RESUME))


class ThePreflightAsksOnlyTheCheapRowsOnReuse(Rig):
    """REQ-RC1-09: the streamed preflight carries --cheap-only only on the reuse path."""

    def preflight_cmd(self, **kw):
        r = Runner({})
        capture(C.preflight, "/fake/root", VERSION, r, **kw)
        return r.cmds[0]

    def test_the_flag_is_passed_only_when_asked(self):
        self.assertIn("--cheap-only", self.preflight_cmd(cheap_only=True))
        cmd = self.preflight_cmd()
        self.assertNotIn("--cheap-only", cmd)
        self.assertNotIn("--no-virgin-gate", cmd)

    def run_chain(self, resume_sha, covering):
        seen = {}

        def readiness(*a, **k):
            seen["cheap_only"] = k.get("cheap_only")
            return ["stop here"]
        with mock.patch.object(C, "readiness", side_effect=readiness), \
                mock.patch.object(C, "_covering_record", return_value=covering):
            (code, _e, note), out = capture(C.run_chain, "/fake/root", VERSION, (None, None, "x"), ["scripts/"],
                                            None, True, None, runner=Runner(GREEN), resume_sha=resume_sha)
        return seen, out

    def test_run_chain_asks_the_cheap_rows_only_with_a_covering_record(self):
        _rec, path = self.record(self.all_rows())
        seen, out = self.run_chain(RESUME, (path, ""))
        self.assertIs(seen["cheap_only"], True)
        self.assertIn("REUSE: %s" % path, out)
        for resume, covering in ((None, (path, "")), (RESUME, ("", "REFUSED: nothing covers"))):
            with self.subTest(resume=resume):
                seen, out = self.run_chain(resume, covering)
                self.assertIs(seen["cheap_only"], False)
                self.assertIn("NO REUSE: ", out)

    def test_readiness_passes_the_flag_to_the_preflight(self):
        r = Runner(GREEN)
        capture(C.readiness, "/fake/root", VERSION, (None, None, "absent"), ["scripts/"], None, r,
                include_fast=False, cheap_only=True)
        cmd = r.cmds[r.calls.index("cut_preflight.py")]
        self.assertIn("--cheap-only", cmd)

    def test_the_preflight_alias_sets_the_same_switch_and_says_so(self):
        for flag in ("--cheap-only", "--no-virgin-gate"):
            with self.subTest(flag=flag):
                seen = {}

                def run_all(*a, **k):
                    seen["virgin"] = k.get("virgin")
                    return [(self.P.OK, "x", "fine")]
                with mock.patch.object(self.P, "run_all", side_effect=run_all):
                    code, out = capture(self.P.main, ["--version", VERSION, flag])
                self.assertEqual((code, seen["virgin"]), (0, False))
                self.assertEqual(out.strip().splitlines()[-1], "cut-preflight: CLEAR (cheap rows only; "
                                 "export-real-home and virgin-gate were not asked)")
        with mock.patch.object(self.P, "run_all", return_value=[(self.P.OK, "x", "fine")]):
            code, out = capture(self.P.main, ["--version", VERSION])
        self.assertEqual(out.strip().splitlines()[-1], "cut-preflight: CLEAR")


class TheProvenRowsAreReusedOnAnExactKey(Rig):
    """REQ-RC1-10 and REQ-RC1-12: each row is skipped only on its exact key, home and a PASS, and printed."""

    def test_a_covering_record_reuses_all_three_rows(self):
        rec, path = self.record(self.all_rows())
        passed, failed, r, out = self.chain(rec, path)
        self.assertEqual(failed, [], out)
        self.assertNotIn("required_fast.sh", r.calls)
        self.assertNotIn("export_public.py", r.calls)
        self.assertIn("required_fast (reused)", passed)
        self.assertIn("export rows", passed)
        for name, key in (("required-fast-hub", HUB), ("export-real-home", EXP), ("virgin-gate", EXP)):
            self.assertIn("REUSED %s %s %s" % (name, key[:12], path), out)
        self.assertEqual(CR.parse_rows(out)[0], [], "a reused row was recorded again")

    def test_a_hub_key_one_byte_off_runs_the_hub_gate_only(self):
        rec, path = self.record(self.all_rows(required_fast_hub=self.row("required-fast-hub", HUB[:-1] + "0")))
        passed, failed, r, out = self.chain(rec, path)
        self.assertEqual(failed, [], out)
        self.assertEqual(r.calls.count("required_fast.sh"), 1, r.calls)
        self.assertNotIn("export_public.py", r.calls)
        self.assertIn("required_fast", passed)
        self.assertNotIn("REUSED required-fast-hub", out)
        self.assertIn("REUSED export-real-home", out)

    def test_an_export_key_one_byte_off_runs_both_export_rows(self):
        off = EXP[:-1] + "0"
        rec, path = self.record(self.all_rows(export_real_home=self.row("export-real-home", off),
                                              virgin_gate=self.row("virgin-gate", off)))
        passed, failed, r, out = self.chain(rec, path)
        self.assertEqual(failed, [], out)
        self.assertEqual(r.calls.count("export_public.py"), 1, r.calls)
        self.assertEqual(r.calls.count("required_fast.sh"), 1, "the virgin gate ran: %s" % r.calls)
        self.assertIn("REUSED required-fast-hub", out)

    def test_a_row_of_another_home_or_a_fail_row_runs_that_row(self):
        for over in ({"export_real_home": self.row("export-real-home", EXP, home="empty")},
                     {"export_real_home": self.row("export-real-home", EXP, verdict="FAIL")}):
            with self.subTest(over=sorted(over)):
                rec, path = self.record(self.all_rows(**over))
                passed, failed, r, out = self.chain(rec, path)
                self.assertEqual(failed, [], out)
                self.assertEqual(r.calls.count("export_public.py"), 1, r.calls)
                self.assertNotIn("REUSED export-real-home", out)
                self.assertIn("REUSED virgin-gate", out)

    def test_one_row_missing_runs_that_row_and_reuses_the_others(self):
        rec, path = self.record(self.all_rows(virgin_gate=None))
        passed, failed, r, out = self.chain(rec, path)
        self.assertEqual(failed, [], out)
        self.assertEqual(r.calls.count("required_fast.sh"), 1, "the virgin gate did not run: %s" % r.calls)
        self.assertNotIn("export_public.py", r.calls)
        self.assertIn("REUSED required-fast-hub", out)
        self.assertIn("REUSED export-real-home", out)
        self.assertEqual([x["row"] for x in CR.parse_rows(out)[0]], ["virgin-gate"])

    def test_a_stale_record_or_a_rehearsal_1_record_runs_everything(self):
        stale, spath = self.record(self.all_rows(), age_h=25.0)
        old = dict(stale, rows=[], schema="cv1.rehearsal.1")
        for rec, path in ((stale, spath), (old, spath)):
            with self.subTest(schema=rec["schema"]):
                passed, failed, r, out = self.chain(rec, path)
                self.assertEqual(failed, [], out)
                self.assertEqual(r.calls.count("required_fast.sh"), 2, r.calls)
                self.assertEqual(r.calls.count("export_public.py"), 1, r.calls)
                self.assertNotIn("REUSED", out)

    def test_a_ran_row_that_fails_still_fails_the_chain(self):
        rec, path = self.record(self.all_rows(virgin_gate=None))
        passed, failed, r, out = self.chain(rec, path, {"required_fast.sh": (1, "FAILED: x\npass 1 fail 1 no-data 0\n")})
        self.assertIn("export rows", failed, out)

    def test_an_unreadable_export_tree_is_no_data_and_fails(self):
        rec, path = self.record(self.all_rows())
        passed, failed, r, out = self.chain(rec, path, {"cut_preflight.py": (2, "NO-DATA: no build\n")})
        self.assertIn("export rows", failed, out)
        self.assertNotIn("REUSED export-real-home", out)


class ThePushCarriesTheCoveringRecord(Rig):
    """REQ-RC1-23: --rehearsal-record <path> on the push, and only when a record covers."""

    def push_cmd(self, **kw):
        r = Runner(GREEN)
        capture(C.export_public_push, "/fake/root", VERSION, r, **kw)
        return r.cmds[0]

    def test_the_flag_follows_the_record(self):
        cmd = self.push_cmd(rehearsal_record="/x/rehearsal.json")
        self.assertEqual(cmd[cmd.index("--rehearsal-record") + 1], "/x/rehearsal.json")
        self.assertNotIn("--rehearsal-record", self.push_cmd())
        self.assertNotIn("--rehearsal-record", self.push_cmd(rehearsal_record=""))

    def test_a_real_cut_passes_the_record_that_covers_its_question(self):
        r = Runner(GREEN)
        TC._main(["--yes"], r)
        push = [c for c in r.cmds if TC._key(c) == "export_public.py"][0]
        self.assertEqual(push[push.index("--rehearsal-record") + 1], TC.FAKE_REHEARSAL)


if __name__ == "__main__":
    unittest.main()
