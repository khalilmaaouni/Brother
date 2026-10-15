#!/usr/bin/env python3
"""FR1.a: the driver's preflight phase writes one cv1.preflight.1 record, and cut.py --check reuses it (asks
cut_preflight.py --cheap-only) only when it covers the exact commit, tree, export tree id and interpreter. Every
doubt runs the full preflight. Cases drive the real entry points: cut_driver.preflight_phase, cut.reusable_preflight
and cut.check_mode (its chain replaced), with a scripted runner and a throwaway git repository. Nothing here runs a
real cut, rehearsal, export, push or tag.

Hermetic: no HOME read, no `ps`, no network, no docs/plan. Python 3.9, standard library only. No em or en dashes.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cut as C  # noqa: E402
import cut_driver as D  # noqa: E402
import cut_rehearsal as CR  # noqa: E402

try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n" % os.path.basename(__file__))

V = "9.9.9"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
OTHER = "b" * 40
EXPORT_ID = "e" * 40
BOTH_OK = ("OK       no-cut-commit    x\nOK       export-real-home y\nOK       virgin-gate      z\n"
           "cut-preflight: CLEAR\n")


def git(cwd, *args):
    return subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


class DriverRunner(object):
    """The cut_driver runner shape (cmd, cwd, stream=False) -> (rc, text): first needle wins, git is real."""

    def __init__(self, handlers=()):
        self.handlers = list(handlers)
        self.calls = []

    def __call__(self, cmd, cwd, stream=False):
        self.calls.append(list(cmd))
        line = " ".join(cmd)
        for needle, result in self.handlers:
            if needle in line:
                return result
        if cmd[0] == "git":
            return D.real_runner(cmd, cwd)
        return 127, "unhandled %s" % line


class CutRunner(object):
    """The cut.py runner shape (cmd, **kw) -> CompletedProcess: first needle wins, git is real, anything else
    exits 0 with no output. Records every argv."""

    def __init__(self, handlers=()):
        self.handlers = list(handlers)
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        line = " ".join(cmd)
        for needle, result in self.handlers:
            if needle in line:
                return subprocess.CompletedProcess(cmd, result[0], result[1], "")
        if cmd[0] == "git":
            return subprocess.run(cmd, **kw)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def saw(self, needle):
        return [c for c in self.calls if needle in " ".join(c)]


class Fixture(object):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fr1a-")
        self.ev = os.path.join(self.tmp, "ev")
        env = dict(GIT_ENV, BROTHER_CUT_TEST="1", BROTHER_CUT_EVIDENCE_DIR=self.ev,
                   BROTHER_SCRATCH=os.path.join(self.tmp, "scratch"))
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "repo")
        git(self.tmp, "init", "-q", "-b", "main", self.root)
        with open(os.path.join(self.root, "a.txt"), "w") as fh:
            fh.write("a\n")
        git(self.root, "add", "a.txt")
        git(self.root, "commit", "-q", "-m", "candidate")
        self.cand = git(self.root, "rev-parse", "HEAD")
        self.tree = git(self.root, "rev-parse", "HEAD^{tree}")
        self.logs = 0

    def log(self, text=BOTH_OK):
        self.logs += 1
        path = os.path.join(self.tmp, "preflight-%d.log" % self.logs)
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def write(self, verdict="PASS", text=BOTH_OK, candidate=None, tree=None, export_id=EXPORT_ID, now=None,
              version=V, log=None):
        """A record made by the real writer into the throwaway evidence folder; returns its path."""
        return CR.record_preflight(version, candidate or self.cand, tree or self.tree, export_id, verdict,
                                   log or self.log(text), 1.5, now=now)

    def edit(self, path, **fields):
        with open(path) as fh:
            data = json.load(fh)
        data.update(fields)
        with open(path, "w") as fh:
            json.dump(data, fh)

    def newest(self, now=None, candidate=None, tree=None):
        return CR.newest_preflight(V, candidate or self.cand, tree or self.tree, now=now)

    def reusable(self, built=EXPORT_ID, now=None):
        runner = CutRunner([("--export-tree-id", (0, "EXPORT-TREE: %s\n" % built))])
        return C.reusable_preflight(self.root, V, runner, now=now), runner


class TheRecord(Fixture, unittest.TestCase):
    def test_preflight_phase_writes_a_record_on_pass(self):
        runner = DriverRunner([("--export-tree-id", (0, "EXPORT-TREE: %s\n" % EXPORT_ID)),
                               ("cut_preflight.py", (0, BOTH_OK))])
        verdict, detail = D.preflight_phase(self.root, V, runner)
        self.assertEqual(verdict, "PASS")
        names = [n for n in os.listdir(self.ev) if n.startswith("preflight-%s-%s-" % (V, self.cand[:12]))]
        self.assertEqual(len(names), 1, os.listdir(self.ev))
        self.assertIn(names[0], detail)
        with open(os.path.join(self.ev, names[0])) as fh:
            data = json.load(fh)
        self.assertEqual(data["schema"], CR.PREFLIGHT_SCHEMA)
        self.assertEqual((data["candidate_sha"], data["candidate_tree"], data["export_tree_id"], data["verdict"]),
                         (self.cand, self.tree, EXPORT_ID, "PASS"))
        self.assertEqual([r["name"] for r in data["rows"]], ["no-cut-commit", "export-real-home", "virgin-gate"])
        self.assertEqual(data["interpreter"], "%d.%d.%d" % sys.version_info[:3])
        self.assertEqual(len(data["log_sha256"]), 64)
        rec, why = self.newest()
        self.assertEqual((rec is not None, why), (True, ""))

    def test_a_failed_preflight_writes_a_record_that_is_never_reused(self):
        runner = DriverRunner([("cut_preflight.py", (1, "REFUSED  plugin-bumps    x\ncut-preflight: REFUSED\n"))])
        verdict, detail = D.preflight_phase(self.root, V, runner)
        self.assertEqual(verdict, "FAIL")
        self.assertEqual([c for c in runner.calls if "--export-tree-id" in c], [])
        (name,) = [n for n in os.listdir(self.ev) if n.startswith("preflight-")]
        with open(os.path.join(self.ev, name)) as fh:
            data = json.load(fh)
        self.assertEqual((data["verdict"], data["export_tree_id"]), ("FAIL", ""))
        # the isolated condition: everything else about this record would pass; only the verdict is wrong
        os.remove(os.path.join(self.ev, name))
        self.write(verdict="FAIL")
        rec, why = self.newest()
        self.assertIsNone(rec)
        self.assertIn("FAIL", why)

    def test_an_unwritable_evidence_folder_keeps_the_verdict(self):
        runner = DriverRunner([("--export-tree-id", (0, "EXPORT-TREE: %s\n" % EXPORT_ID)),
                               ("cut_preflight.py", (0, BOTH_OK))])
        with mock.patch.object(CR, "record_preflight", side_effect=OSError("disk full")):
            verdict, detail = D.preflight_phase(self.root, V, runner)
        self.assertEqual(verdict, "PASS")
        self.assertIn("record not written", detail)
        with mock.patch.object(CR, "record_preflight", side_effect=ValueError("bad field")):
            verdict, detail = D.preflight_phase(self.root, V, runner)
        self.assertEqual(verdict, "PASS")
        self.assertIn("record not written", detail)

    def test_an_export_id_that_cannot_be_read_writes_a_record_that_is_never_reused(self):
        answers = {"a NO-DATA exit": (2, "NO-DATA: no tree\n"),
                   "an id printed with a failing exit": (1, "EXPORT-TREE: %s\n" % EXPORT_ID),
                   "two ids": (0, "EXPORT-TREE: %s\nEXPORT-TREE: %s\n" % (EXPORT_ID, OTHER))}
        for label, answer in answers.items():
            runner = DriverRunner([("--export-tree-id", answer), ("cut_preflight.py", (0, BOTH_OK))])
            verdict, detail = D.preflight_phase(self.root, V, runner)
            self.assertEqual(verdict, "PASS", label)
            self.assertIn("never reused", detail, label)
            rec, why = self.newest()
            self.assertIsNone(rec, label)
            self.assertIn("names no export tree", why, label)
            for name in os.listdir(self.ev):
                if name.startswith("preflight-"):
                    os.remove(os.path.join(self.ev, name))

    def test_hostile_arguments_return_a_reason(self):
        good = dict(candidate=self.cand, tree=self.tree)
        for version in ("../x", "", None, 9, "1.2", "1.2.3.4"):
            rec, why = CR.newest_preflight(version, **good)
            self.assertEqual((rec, bool(why)), (None, True), version)
        for cand in (self.cand[:39], self.cand + "0", self.cand.upper(), "", None, 7, ["a"]):
            rec, why = CR.newest_preflight(V, cand, self.tree)
            self.assertEqual((rec, bool(why)), (None, True), cand)
            rec, why = CR.newest_preflight(V, self.cand, cand)
            self.assertEqual((rec, bool(why)), (None, True), cand)
        for now in (float("nan"), float("inf"), "x", True):
            rec, why = CR.newest_preflight(V, self.cand, self.tree, now=now)
            self.assertEqual((rec, bool(why)), (None, True), now)
        rec, why = CR.newest_preflight(V, self.cand, self.tree, directory=7)
        self.assertEqual((rec, bool(why)), (None, True))
        log = self.log()
        for args in (("../x", self.cand, self.tree, "", "PASS", log, 1), (V, self.cand[:39], self.tree, "", "PASS", log, 1),
                     (V, self.cand, self.tree.upper(), "", "PASS", log, 1), (V, self.cand, self.tree, "zz", "PASS", log, 1),
                     (V, self.cand, self.tree, "", "pass", log, 1), (V, self.cand, self.tree, "", "PASS ", log, 1),
                     (V, self.cand, self.tree, "", "PASS", log, -1), (V, self.cand, self.tree, "", "PASS", log, float("nan")),
                     (V, self.cand, self.tree, "", "PASS", log, "1"), (V, self.cand, self.tree, "", "PASS", "", 1),
                     (V, self.cand, self.tree, "", "PASS", os.path.join(self.tmp, "nolog"), 1),
                     (V, self.cand, self.tree, "", "PASS", self.tmp, 1)):
            with self.assertRaises(ValueError, msg=repr(args)):
                CR.record_preflight(*args)
        self.assertFalse(os.path.isdir(self.ev) and [n for n in os.listdir(self.ev) if n.startswith("preflight-")])


class ExactlyThisCandidate(Fixture, unittest.TestCase):
    def test_another_candidate_sha_is_not_reused(self):
        self.write()
        rec, why = self.newest(candidate=OTHER)
        self.assertIsNone(rec)
        self.assertIn("no preflight record", why)

    def test_another_tree_is_not_reused(self):
        self.write()
        rec, why = self.newest(tree=OTHER)
        self.assertIsNone(rec)
        self.assertIn("no preflight record", why)

    def test_another_export_tree_id_is_not_reused(self):
        self.write()
        (path, why), _runner = self.reusable(built=OTHER)
        self.assertEqual(path, "")
        self.assertIn("export tree", why)
        (path, why), _runner = self.reusable(built=EXPORT_ID)
        self.assertEqual(why, "")
        self.assertTrue(path.endswith(".json"))

    def test_another_interpreter_is_not_reused(self):
        path = self.write()
        self.edit(path, interpreter="2.7.18")
        rec, why = self.newest()
        self.assertIsNone(rec)
        self.assertIn("python 2.7.18", why)


class OnlyAWholePass(Fixture, unittest.TestCase):
    def test_a_record_without_both_export_rows_ok_is_not_reused(self):
        cases = {"no virgin-gate row": "OK       export-real-home x\ncut-preflight: CLEAR\n",
                 "no export-real-home row": "OK       virgin-gate      x\ncut-preflight: CLEAR\n",
                 "a NO-DATA virgin-gate": "OK       export-real-home x\nNO-DATA  virgin-gate      x\n",
                 "a REFUSED export-real-home": "REFUSED  export-real-home x\nOK       virgin-gate      x\n",
                 "no rows at all": "CLEAR  x\ncut-preflight: CLEAR\n"}
        for label, text in cases.items():
            for name in os.listdir(self.ev) if os.path.isdir(self.ev) else ():
                os.remove(os.path.join(self.ev, name))
            self.write(text=text)
            rec, why = self.newest()
            self.assertIsNone(rec, label)
            self.assertIn("no OK", why, label)

    def test_a_stale_record_is_not_reused_and_a_23_h_one_is(self):
        stamp = 1_800_000_000
        self.write(now=stamp)
        rec, why = self.newest(now=stamp + 23 * 3600)
        self.assertEqual((rec is not None, why), (True, ""))
        rec, why = self.newest(now=stamp + 25 * 3600)
        self.assertIsNone(rec)
        self.assertIn("old", why)

    def test_a_future_record_is_not_reused(self):
        stamp = 1_800_000_000
        self.write(now=stamp + 400)
        rec, why = self.newest(now=stamp)
        self.assertIsNone(rec)
        rec, why = self.newest(now=stamp + 400)
        self.assertEqual((rec is not None, why), (True, ""))

    def test_an_altered_or_missing_log_is_not_reused(self):
        log = self.log()
        self.write(log=log)
        with open(log, "a") as fh:
            fh.write("one more line\n")
        rec, why = self.newest()
        self.assertIsNone(rec)
        self.assertIn("missing or changed", why)
        os.remove(log)
        rec, why = self.newest()
        self.assertIsNone(rec)
        self.assertIn("missing or changed", why)

    def test_a_newer_fail_beats_an_older_pass(self):
        stamp = 1_800_000_000
        self.write(now=stamp)
        self.write(verdict="FAIL", now=stamp + 10)
        rec, why = self.newest(now=stamp + 20)
        self.assertIsNone(rec)
        self.assertIn("FAIL", why)

    def test_a_tie_goes_to_the_worse_verdict(self):
        path = self.write(now=1_800_000_000)
        twin = os.path.join(self.ev, "preflight-%s-%s-twin.json" % (V, self.cand[:12]))
        shutil.copy(path, twin)
        self.edit(twin, verdict="FAIL")
        rec, why = self.newest(now=1_800_000_010)
        self.assertIsNone(rec)
        self.assertIn("FAIL", why)

    def test_a_newer_pass_beats_an_older_fail(self):
        stamp = 1_800_000_000
        self.write(verdict="FAIL", now=stamp)
        newer = self.write(now=stamp + 10)
        rec, why = self.newest(now=stamp + 20)
        self.assertEqual(why, "")
        self.assertEqual(rec["_path"], newer)

    def test_the_newest_of_many_decides(self):
        stamp = 1_800_000_000
        for i in range(50):
            self.write(now=stamp + i, candidate=OTHER if i % 2 else None)
        last = self.write(now=stamp + 60)
        rec, why = self.newest(now=stamp + 100)
        self.assertEqual(rec["_path"], last)


class CorruptAndForeign(Fixture, unittest.TestCase):
    def test_a_corrupt_or_foreign_record_is_skipped_and_kept(self):
        good = self.write()
        os.makedirs(self.ev, exist_ok=True)
        with open(good) as fh:
            whole = json.load(fh)
        junk = {"preflight-%s-truncated-1.json" % V: '{"schema": "cv1.preflight.1", "ver',
                "preflight-%s-list-2.json" % V: "[1, 2]",
                "preflight-%s-norows-3.json" % V: json.dumps({k: v for k, v in whole.items() if k != "rows"}),
                "preflight-%s-foreign-4.json" % V: json.dumps(dict(whole, schema="cv1.rehearsal.2")),
                "preflight-%s-lower-5.json" % V: json.dumps(dict(whole, verdict="pass")),
                "preflight-%s-negative-6.json" % V: json.dumps(dict(whole, seconds=-1)),
                "preflight-%s-string-7.json" % V: json.dumps(dict(whole, seconds="1")),
                "preflight-%s-rowverdict-8.json" % V: json.dumps(dict(whole, rows=[{"name": "x", "verdict": "OK "}])),
                "preflight-%s-badid-9.json" % V: json.dumps(dict(whole, export_tree_id="zz"))}
        for name, body in junk.items():
            with open(os.path.join(self.ev, name), "w") as fh:
                fh.write(body)
        rec, why = self.newest()
        self.assertEqual((rec is not None, why), (True, ""))
        self.assertEqual(rec["_path"], good)
        for name in junk:
            self.assertTrue(os.path.exists(os.path.join(self.ev, name)), name)

    def test_only_junk_gives_a_reason_naming_the_file(self):
        os.makedirs(self.ev)
        name = "preflight-%s-truncated-1.json" % V
        with open(os.path.join(self.ev, name), "w") as fh:
            fh.write("{")
        rec, why = self.newest()
        self.assertIsNone(rec)
        self.assertIn(name, why)
        self.assertTrue(os.path.exists(os.path.join(self.ev, name)))

    def test_no_evidence_folder_and_an_empty_one_say_no_record(self):
        rec, why = self.newest()
        self.assertEqual((rec, "no preflight record" in why), (None, True))
        os.makedirs(self.ev)
        rec, why = self.newest()
        self.assertEqual((rec, "no preflight record" in why), (None, True))


class CheckMode(Fixture, unittest.TestCase):
    """cut.check_mode with its chain replaced: the question is only what the preflight is asked."""

    def run_check(self, runner, full_chain=False):
        out = io.StringIO()
        with mock.patch.object(C, "chain", lambda *a, **k: ([], [])), \
                mock.patch.object(C, "_cut_own_paths", lambda root: ()), contextlib.redirect_stdout(out):
            code = C.check_mode(self.root, V, (None, "", "test"), (), None, runner=runner, full_chain=full_chain)
        return code, out.getvalue()

    def test_check_mode_asks_cheap_only_on_an_exact_record(self):
        path = self.write()
        runner = CutRunner([("--export-tree-id", (0, "EXPORT-TREE: %s\n" % EXPORT_ID))])
        code, out = self.run_check(runner)
        asked = [c for c in runner.saw("cut_preflight.py") if "--export-tree-id" not in c]
        self.assertEqual(len(asked), 1, runner.calls)
        self.assertIn("--cheap-only", asked[0])
        self.assertIn("REUSED preflight %s" % path, out)
        self.assertNotIn("preflight not reused", out)

    def test_check_mode_runs_the_full_preflight_and_says_why_otherwise(self):
        runner = CutRunner([("--export-tree-id", (0, "EXPORT-TREE: %s\n" % EXPORT_ID))])
        code, out = self.run_check(runner)
        asked = [c for c in runner.saw("cut_preflight.py") if "--export-tree-id" not in c]
        self.assertEqual(len(asked), 1, runner.calls)
        self.assertNotIn("--cheap-only", asked[0])
        self.assertIn("preflight not reused: no preflight record", out)
        self.assertNotIn("REUSED preflight", out)

    def test_full_chain_never_reuses(self):
        self.write()
        runner = CutRunner([("--export-tree-id", (0, "EXPORT-TREE: %s\n" % EXPORT_ID))])
        code, out = self.run_check(runner, full_chain=True)
        asked = runner.saw("cut_preflight.py")
        self.assertEqual(len(asked), 1, runner.calls)
        self.assertNotIn("--cheap-only", asked[0])
        self.assertIn("--full-chain", out)
        self.assertNotIn("REUSED preflight", out)

    def test_main_hands_full_chain_to_check_mode(self):
        for argv, want in ((["--check", "--full-chain", "--version", V], True), (["--check", "--version", V], False)):
            with mock.patch.object(C, "check_mode", return_value=C.EXIT_OK) as check, \
                    contextlib.redirect_stdout(io.StringIO()):
                C.main(argv, root=self.root, runner=CutRunner())
            self.assertEqual(check.call_count, 1, argv)
            self.assertIs(check.call_args[1].get("full_chain"), want, argv)

    def test_a_reuse_check_that_raises_runs_the_full_preflight(self):
        self.write()
        runner = CutRunner([("--export-tree-id", (0, "EXPORT-TREE: %s\n" % EXPORT_ID))])
        with mock.patch.object(CR, "newest_preflight", side_effect=RuntimeError("boom")):
            path, why = C.reusable_preflight(self.root, V, runner)
        self.assertEqual(path, "")
        self.assertIn("boom", why)


if __name__ == "__main__":
    unittest.main()
