#!/usr/bin/env python3
"""FR1.g (docs/plan/specs/FR1.md): the local half of a rehearsal and LOCAL-PASS.

`cut.py --check --local` runs the cheap preflight and the chain without the export rows and the Linux canary, and ends
LOCAL-READY, a line nothing reads as READY. `cut_rehearsal.py --local` records it under local-rehearsal-<version>-*.json
(schema cv1.local.1, verdict LOCAL-PASS). Nothing that decides a cut can see that file: covering_record globs
rehearsal-<version>-*.json, the driver reads only the path its rehearsal phase returns, and only row_sources reads
local records, for the rows a full check may reuse on their exact key.

HOW. cut.main --check runs on a scripted runner (the shape test_cut_rehearsal.py uses); the rehearsal cases run
cut_rehearsal.main in a throwaway git repository whose child cut.py answer is canned; the cut covering cases run the
real covering_record on that repository.

Python 3.9, standard library only. No network. No em or en dashes.
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
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n"
                     % os.path.basename(__file__))

V = "9.9.9"
HEAD = "e" * 40
KEY = "a" * 40
EXPORT_TREE = "c" * 40
GREEN = {"git rev-parse HEAD": (0, HEAD + "\n"), "git status --porcelain": (0, ""),
         "git rev-parse HEAD^{tree}": (0, KEY + "\n")}
LOCAL_CHAIN = ("retire_catalogs.py", "cut_v1.0.0.sh", "plugin_bump_gate.py", "required_fast.sh", "refresh_cut.py",
               "release_invariant.py", "donecheck_u8.py")


def label(cmd):
    """git plus two words; the basename of cmd[1] for sh and interpreters (test_cut_rehearsal's shape)."""
    if cmd[0] == "git":
        return "git " + " ".join(cmd[1:3])
    return os.path.basename(cmd[1])


class Runner(object):
    """Answers from `answers` (label -> (code, stdout)), default exit 0; records every command."""

    def __init__(self, answers):
        self.answers = dict(answers)
        self.cmds = []

    @property
    def labels(self):
        return [label(c) for c in self.cmds]

    def __call__(self, cmd, **kw):
        self.cmds.append(list(cmd))
        code, out = self.answers.get(label(list(cmd)), (0, ""))
        return subprocess.CompletedProcess(cmd, code, out, "")


class Rig(unittest.TestCase):
    """A throwaway repository, evidence folder and scratch root, with the cut test seams set."""

    def setUp(self):
        base = tempfile.mkdtemp(prefix="fr1g-")
        self.addCleanup(shutil.rmtree, base, True)
        self.base = base
        self.repo, self.ev, self.bs = (os.path.join(base, n) for n in ("repo", "evidence", "scratch"))
        home = os.path.join(base, "home")
        for d in (self.repo, home, self.bs, self.ev):
            os.makedirs(d)
        env = dict((k, v) for k, v in os.environ.items()
                   if not k.startswith(("GIT_", "BROTHER_")) and k != "XDG_CONFIG_HOME")
        env.update(HOME=home, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.test",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.test", BROTHER_SCRATCH=self.bs,
                   BROTHER_CUT_TEST="1", BROTHER_CUT_EVIDENCE_DIR=self.ev)
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        files = {"scripts/cut.py": "cut\n"}
        for rel in CR.SCRIPT_DIRS:
            files[rel + "/m.py"] = "m\n"
        for rel, text in files.items():
            path = os.path.join(self.repo, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
            proc = C._run(["git"] + args, self.repo)
            self.assertEqual(proc.returncode, 0, proc.stderr)
        self.sha = C._run(["git", "rev-parse", "HEAD"], self.repo).stdout.strip()
        self.tree = C._run(["git", "rev-parse", "HEAD^{tree}"], self.repo).stdout.strip()
        self.n = 0

    def log(self, text="log\n"):
        self.n += 1
        path = os.path.join(self.base, "x%d.log" % self.n)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def row(self, name="required-fast-hub", key=KEY, home="real", verdict="PASS"):
        path = self.log("row output %d\n" % self.n)
        return {"row": name, "argv": ["sh", "scripts/required_fast.sh"], "key": key, "home": home, "verdict": verdict,
                "seconds": 1.0, "log_path": path, "log_sha256": CR._sha256_file(path)}

    def local_record(self, rows=(), verdict="LOCAL-PASS", age_s=60, sha=None):
        return CR.record_local(V, sha or self.sha, self.tree, 0 if verdict == "LOCAL-PASS" else 1, verdict,
                               self.log(), 1.0, self.ev, now=time.time() - age_s, rows=rows)

    def ready_record(self, age_s=120):
        trees, why = CR._script_trees(self.repo, self.sha)
        self.assertEqual(why, "")
        return CR.record_rehearsal(V, self.sha, self.tree, 0, "READY", self.log("READY for %s\n" % V), 1.0,
                                   CR.running_cut_py_sha256(), self.ev, now=time.time() - age_s, script_trees=trees)

    def check(self, argv, answers=None):
        """(code, stdout, runner, mocks) of cut.main on the scripted runner; each long seam is a mock."""
        runner = Runner(dict(GREEN, **(answers or {})))
        out = io.StringIO()
        real_reuse = C.reused_or_run_export_rows
        with mock.patch.object(C, "_load_bm_store", return_value=(None, None, "absent")), \
                mock.patch.object(C, "release_paths", return_value=["scripts/"]), \
                mock.patch.object(C, "export_tree", return_value=(EXPORT_TREE, "")), \
                mock.patch.object(C, "export_rows", return_value=(True, ["export rows: stubbed"])) as rows, \
                mock.patch.object(C, "reused_or_run_export_rows", side_effect=real_reuse) as reused, \
                mock.patch.object(C, "start_canary", return_value=None) as started, \
                mock.patch.object(C, "await_canary", return_value=(True, ["linux canary: stubbed"])), \
                mock.patch.object(tempfile, "tempdir", self.base), contextlib.redirect_stdout(out):
            code = C.main(argv, root="/fake/root", runner=runner)
        return code, out.getvalue(), runner, {"export_rows": rows, "reused": reused, "start_canary": started}


class TheLocalChain(Rig):

    def test_local_flag_runs_the_chain_without_export_rows_or_canary(self):
        code, out, runner, mocks = self.check(["--check", "--local", "--version", V])
        self.assertEqual(code, C.EXIT_OK, out)
        for name in LOCAL_CHAIN:
            self.assertIn(name, runner.labels, out)
        mocks["export_rows"].assert_not_called()
        mocks["reused"].assert_not_called()
        mocks["start_canary"].assert_not_called()
        preflight = [c for c in runner.cmds if label(c) == "cut_preflight.py" and "--version" in c
                     and "--export-tree-id" not in c]
        self.assertTrue(preflight and all("--cheap-only" in c for c in preflight), preflight)

    def test_the_same_check_without_local_still_runs_the_export_rows_and_the_canary(self):
        code, out, runner, mocks = self.check(["--check", "--version", V])
        self.assertEqual(code, C.EXIT_OK, out)
        mocks["start_canary"].assert_called_once()
        self.assertTrue(mocks["reused"].called, out)

    def test_local_flag_prints_local_ready_never_ready(self):
        code, out, runner, mocks = self.check(["--check", "--local", "--version", V])
        self.assertTrue(CR._ready_line(out, V, "LOCAL-READY"), out)
        self.assertFalse(CR._ready_line(out, V), out)
        self.assertIn("never covers a cut", out)
        code, out, runner, mocks = self.check(["--check", "--local", "--version", V],
                                              {"plugin_bump_gate.py": (1, "refused\n")})
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertFalse(CR._ready_line(out, V, "LOCAL-READY"), out)
        self.assertIn("NOT READY for %s" % V, out)

    def test_local_without_check_is_refused(self):
        for argv in (["--local", "--version", V], ["--local", "--resume", "f" * 40, "--version", V],
                     ["--check", "--local", "--resume", "f" * 40, "--version", V]):
            with self.subTest(argv=argv):
                code, out, runner, mocks = self.check(argv)
                self.assertEqual(code, C.EXIT_NODATA, out)
                self.assertIn("NO-DATA: --local only means something with --check", out)
                self.assertEqual(runner.cmds, [])

    def test_local_with_full_chain_still_skips_the_export_rows(self):
        code, out, runner, mocks = self.check(["--check", "--local", "--full-chain", "--version", V])
        self.assertEqual(code, C.EXIT_OK, out)
        mocks["export_rows"].assert_not_called()
        mocks["start_canary"].assert_not_called()


class TheLocalRecord(Rig):

    def test_a_local_record_is_written_under_its_own_prefix(self):
        path = self.local_record([self.row()])
        name = os.path.basename(path)
        self.assertTrue(name.startswith("%s%s-%s-" % (CR.LOCAL_PREFIX, V, self.sha[:12])), name)
        self.assertFalse(name.startswith("rehearsal-"))
        data = json.load(open(path, encoding="utf-8"))
        self.assertEqual((data["schema"], data["verdict"]), ("cv1.local.1", "LOCAL-PASS"))
        for field in CR.RECORD_FIELDS + ("rows",):
            self.assertIn(field, data)
        self.assertEqual(CR.LOCAL_SCHEMA, "cv1.local.1")
        self.assertEqual(CR.LOCAL_VERDICTS, ("LOCAL-PASS", "NOT-READY", "NO-DATA"))

    def test_record_local_refuses_what_it_cannot_describe(self):
        good = dict(version=V, candidate=self.sha, tree=self.tree, exit_code=0, verdict="LOCAL-PASS",
                    log_path=self.log(), seconds=1.0, directory=self.ev)
        for change in ({"verdict": "READY"}, {"verdict": None}, {"verdict": ["LOCAL-PASS"]}, {"version": "9.9"},
                       {"candidate": "abc"}, {"tree": 5}, {"exit_code": True}, {"seconds": float("nan")},
                       {"seconds": -1}, {"rows": "no"}, {"rows": [{"row": "x"}]}, {"log_path": ""},
                       {"log_path": os.path.join(self.base, "absent.log")}, {"now": "x"}, {"note": 5}):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    CR.record_local(**dict(good, **change))
        self.assertEqual(os.listdir(self.ev), [])
        # a NOT-READY or NO-DATA record may have no log
        CR.record_local(**dict(good, verdict="NO-DATA", log_path=""))

    def test_a_corrupt_or_foreign_local_record_contributes_a_reason_only(self):
        held = self.row()
        self.local_record([held])
        prefix = os.path.join(self.ev, CR.LOCAL_PREFIX + V + "-")
        with open(prefix + "aa-1.json", "w", encoding="utf-8") as fh:
            fh.write('{"schema": "cv1.local.1", "ver')
        with open(prefix + "bb-1.json", "w", encoding="utf-8") as fh:
            json.dump({"schema": "cv1.rehearsal.2"}, fh)
        records, reasons = CR.row_sources(V, self.ev)
        self.assertEqual(len(records), 1)
        self.assertEqual(len(reasons), 2, reasons)
        self.assertEqual(CR.reusable_check_row(V, "required-fast-hub", KEY, "real", self.ev), (held, ""))

    def test_a_local_no_data_record_yields_no_row(self):
        self.local_record([self.row()], "NO-DATA")
        records, reasons = CR.row_sources(V, self.ev)
        self.assertEqual(records, [])
        self.assertIn("NO-DATA", reasons[0])


class TheCutNeverSeesALocalRecord(Rig):

    def cover(self):
        return CR.covering_record(self.repo, V, directory=self.ev)

    def test_covering_record_ignores_a_local_pass_record(self):
        self.local_record([self.row()])
        path, why = self.cover()
        self.assertEqual(path, "")
        self.assertIn("no record at all for %s" % V, why)

    def test_covering_record_still_finds_a_ready_record_beside_a_local_one(self):
        ready = self.ready_record(age_s=300)
        self.local_record([self.row()], age_s=10)
        path, why = self.cover()
        self.assertEqual((path, why), (ready, ""))

    def test_a_local_pass_never_becomes_a_covering_record_when_renamed(self):
        local = self.local_record([self.row()])
        renamed = os.path.join(self.ev, "rehearsal-%s-%s-%d.json" % (V, self.sha[:12], int(time.time())))
        shutil.copy(local, renamed)
        path, why = self.cover()
        self.assertEqual(path, "")
        self.assertIn("cv1.local.1", why)
        self.assertIn("REFUSED", why)

    def test_a_local_pass_verdict_inside_a_rehearsal_named_file_refuses_the_folder(self):
        data = json.load(open(self.local_record([self.row()]), encoding="utf-8"))
        data["schema"] = "cv1.rehearsal.2"
        with open(os.path.join(self.ev, "rehearsal-%s-%s-1.json" % (V, self.sha[:12])), "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        path, why = self.cover()
        self.assertEqual(path, "")
        self.assertIn("REFUSED", why)

    def test_the_driver_never_reuses_a_local_record(self):
        self.local_record([self.row()])
        said = []

        def runner(cmd, cwd=None, **kw):
            said.append(cmd)
            return 1, "REHEARSAL NOT-READY %s abc exit 1 record /x\n" % V

        verdict, detail, record = D.rehearsal_phase(self.repo, V, self.sha, 60, runner)
        self.assertNotEqual(verdict, "REUSED", detail)
        self.assertEqual(len(said), 1, "the driver ran a fresh rehearsal instead of reusing the local record")

    def test_the_driver_reads_a_local_pass_line_as_no_data(self):
        def runner(cmd, cwd=None, **kw):
            return 0, "REHEARSAL LOCAL-PASS %s abc123 exit 0 record /x/local-rehearsal-%s-abc123-1.json\n" % (V, V)

        verdict, detail, record = D.rehearsal_phase(self.repo, V, self.sha, 60, runner)
        self.assertEqual(verdict, "NO-DATA", detail)


class TheRehearsalCommand(Rig):

    def setUp(self):
        Rig.setUp(self)
        self.child_out, self.child_code, self.child_cmds = "", 0, []

    def outer(self, cmd, **kw):
        cmd = list(cmd)
        if cmd[0] != "git":
            self.child_cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, self.child_code, self.child_out, "")
        if cmd[1:3] == ["worktree", "add"]:
            os.makedirs(cmd[-2])
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[1:3] == ["worktree", "remove"]:
            shutil.rmtree(cmd[-1], ignore_errors=True)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return C._run(cmd, kw.get("cwd"), timeout=kw.get("timeout"))

    def rehearse(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = CR.main(argv, root=self.repo, runner=self.outer)
        return code, out.getvalue()

    def test_local_rehearsal_records_a_local_pass_and_prints_the_line(self):
        held = self.row()
        self.child_out = "\n".join([CR.row_line(held), "LOCAL-READY for %s: ..." % V, ""])
        code, out = self.rehearse(["--version", V, "--local"])
        self.assertEqual(code, 0, out)
        last = out.strip().splitlines()[-1]
        self.assertRegex(last, r"^REHEARSAL LOCAL-PASS %s %s exit 0 record .*local-rehearsal-" % (
            V.replace(".", r"\."), self.sha[:12]))
        self.assertIn("--local", self.child_cmds[0])
        data = json.load(open(last.split(" record ", 1)[1], encoding="utf-8"))
        self.assertEqual((data["schema"], data["verdict"], data["rows"]), ("cv1.local.1", "LOCAL-PASS", [held]))
        self.assertEqual([n for n in os.listdir(self.ev) if n.startswith("rehearsal-") and n.endswith(".json")], [])

    def test_a_plain_ready_line_is_not_a_local_pass(self):
        self.child_out = "READY for %s: fences clear\n" % V
        code, out = self.rehearse(["--version", V, "--local"])
        self.assertEqual(code, 2, out)
        self.assertIn("REHEARSAL NO-DATA", out)

    def test_a_refused_local_run_is_not_ready_and_a_full_run_has_no_local_flag(self):
        self.child_code, self.child_out = 1, "NOT READY for %s: required_fast\n" % V
        code, out = self.rehearse(["--version", V, "--local"])
        self.assertEqual(code, 1, out)
        self.assertIn("REHEARSAL NOT-READY", out)
        self.child_code, self.child_out = 0, "READY for %s: fences clear\n" % V
        code, out = self.rehearse(["--version", V])
        self.assertEqual(code, 0, out)
        self.assertNotIn("--local", self.child_cmds[-1])
        self.assertIn("record %s" % os.path.join(self.ev, "rehearsal-%s-" % V), out)

    def test_local_with_verify_is_refused(self):
        code, out = self.rehearse(["--version", V, "--local", "--verify"])
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA: --local and --verify contradict", out)
        self.assertEqual(self.child_cmds, [])


class AFullCheckReusesALocalRow(Rig):

    def test_a_local_pass_row_is_reused_by_a_full_check_on_an_exact_key(self):
        held = self.row()
        self.local_record([held])
        code, out, runner, mocks = self.check(["--check", "--version", V])
        self.assertNotIn("required_fast.sh", runner.labels, out)
        self.assertIn("REUSED required-fast-hub %s %s" % (KEY[:12], held["log_path"]), out)

    def test_a_local_record_for_another_tree_reuses_nothing(self):
        self.local_record([self.row(key="b" * 40)])
        code, out, runner, mocks = self.check(["--check", "--version", V])
        self.assertIn("required_fast.sh", runner.labels, out)
        self.assertNotIn("REUSED required-fast-hub", out)

    def test_a_local_not_ready_record_still_feeds_its_pass_rows(self):
        held = self.row()
        self.local_record([held], "NOT-READY")
        code, out, runner, mocks = self.check(["--check", "--version", V])
        self.assertNotIn("required_fast.sh", runner.labels, out)

    def test_the_newest_row_decides_between_a_local_and_a_full_record(self):
        self.local_record([self.row()], age_s=600)
        CR.record_rehearsal(V, "9" * 40, "8" * 40, 1, "NOT-READY", "", 1.0, "d" * 64, self.ev,
                            now=time.time() - 30, rows=[self.row(verdict="FAIL")])
        code, out, runner, mocks = self.check(["--check", "--version", V])
        self.assertIn("required_fast.sh", runner.labels, out)


if __name__ == "__main__":
    unittest.main()
