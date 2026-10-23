#!/usr/bin/env python3
"""LR1.d: the landing canary sends one tiny real change through the live judge.

loop_canary.landing_stage makes a disposable worktree at the revision the DEPLOYED stamp names, makes one tiny change to a
scratch file there, and runs the judge exactly as a landing runs it (land_batch.deploy_steps' judge: the boxed whole gate
on the merged tree with the stamp's kernel overlay). main() runs the replay stage and then this stage; start_run.sh
refuses the launch on exit 1 or exit 3.

THE JUDGE IS THE ONE THING REPLACED. The whole gate takes minutes, so every case here passes a stand in judge through the
stage's own seam and checks what it was handed: a clean worktree, one commit past the stamp's revision, holding the
scratch change. The adapter that reaches the real judge is tested on its own against a stand in land_batch, at its entry
point: it must run the judge INSIDE the worktree, finish it, and put the working directory back. Nothing here runs a model,
reads the real home folder, the real deployed bin, or touches this repository's own branches.

Python 3.9 and 3.13, standard library only, no network.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import loop_canary as LC  # noqa: E402

GIT_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


#: The deployed bin's land_batch.py in every fixture: deploy_stamp as the real one reads the stamp, deploy_steps marking
#: that THIS file (the deployed one) was the judge. The checkout's own scripts/loop/land_batch.py is never the judge.
FAKE_LAND_BATCH = """import json, os
STAMP_FILE = ".deploy-stamp.json"
WHO = __file__


def deploy_stamp(bin_dir=None):
    try:
        with open(os.path.join(bin_dir or os.path.dirname(WHO), STAMP_FILE), encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    ok = isinstance(doc, dict) and isinstance(doc.get("kernel"), dict) and isinstance(doc.get("kernel_overlay"), list) \\
        and isinstance(doc.get("kernel_dir"), str)
    return doc if ok else None


def deploy_steps(stamp, log, staged):
    here = os.getcwd()
    return {"judge": lambda: (True, "the deployed judge ran in %s from %s" % (here, WHO)), "finish": lambda: None}
"""


#: A deployed land_batch whose steps leave a trace in the gate log, for the adapter tests (the child writes to the log by name).
TRACE_LAND_BATCH = FAKE_LAND_BATCH.split("def deploy_steps")[0] + """def deploy_steps(stamp, log, staged):
    log.write("steps cwd=%s rev=%s\\n" % (os.getcwd(), stamp["revision"]))
    log.flush()
    def judge():
        log.write("judge cwd=%s\\n" % os.getcwd())
        log.flush()
        mode = os.environ.get("TRACE_MODE", "")
        if mode == "raise":
            raise OSError("disk")
        if mode == "silent":
            os._exit(0)
        if mode == "sleep":
            import time
            time.sleep(30)
        if mode == "slow":
            import time
            time.sleep(2)
        if mode == "exit3":
            import atexit
            atexit.register(lambda: os._exit(3))
        return False, "readiness-gate FAIL exit 1"
    def finish():
        log.write("finish cwd=%s\\n" % os.getcwd())
        log.flush()
    return {"judge": judge, "finish": finish}
"""

#: A deployed land_batch whose judge blocks, writing its own pid first: the child the canary must kill on a TERM.
SLEEPY_LAND_BATCH = FAKE_LAND_BATCH.split("def deploy_steps")[0] + """def deploy_steps(stamp, log, staged):
    def judge():
        with open(os.environ["JUDGE_PID_FILE"], "w") as fh:
            fh.write(str(os.getpid()))
        import time
        time.sleep(300)
        return True, "never"
    return {"judge": judge, "finish": lambda: None}
"""


def slurp(path):
    with open(path) as fh:
        return fh.read()


def spoil_with(path, text):
    with open(path, "w") as fh:
        fh.write(text)


def git(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"] + list(args),
                       cwd=cwd, env=GIT_ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    if r.returncode != 0:
        raise AssertionError("git %s: %s %s" % (" ".join(args), r.stdout, r.stderr))
    return r.stdout.strip()      # stdout only: under the sandbox the git shim writes a cache notice on stderr


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="test-landing-canary-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # A refused landing judge keeps its gate log under <evidence>/loop-starts (LR1.d). Every test points that evidence
        # folder at its own temp tree, so no fixture ever writes into the real ~/.claude/evidence (it did: 16 canary-gate
        # logs from test runs were found there on 2026-10-21). A class that needs another folder sets its own and restores.
        saved_evd = os.environ.get("START_RUN_EVIDENCE")
        os.environ["START_RUN_EVIDENCE"] = os.path.join(self.tmp, "evidence")
        self.addCleanup(lambda: os.environ.pop("START_RUN_EVIDENCE", None) if saved_evd is None
                        else os.environ.__setitem__("START_RUN_EVIDENCE", saved_evd))
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q")
        git(self.repo, "checkout", "-q", "-b", "main")
        with open(os.path.join(self.repo, "README"), "w") as fh:
            fh.write("fixture\n")
        git(self.repo, "add", "README")
        git(self.repo, "commit", "-q", "-m", "fixture")
        self.rev = git(self.repo, "rev-parse", "HEAD")
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        self.write_stamp(revision=self.rev)
        spoil_with(os.path.join(self.bin, "land_batch.py"), FAKE_LAND_BATCH)
        saved_target = os.environ.get("BROTHER_DEPLOY_TARGET")      # the deployed bin is the fixture's, whatever a test calls
        os.environ["BROTHER_DEPLOY_TARGET"] = self.bin
        self.addCleanup(lambda: os.environ.pop("BROTHER_DEPLOY_TARGET", None) if saved_target is None
                        else os.environ.__setitem__("BROTHER_DEPLOY_TARGET", saved_target))
        cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, cwd)
        self.seen = []
        self.argv = ["loop_canary.py", "--landing"]

    def write_stamp(self, **over):
        doc = {"schema": 1, "revision": self.rev, "kernel": {}, "kernel_overlay": [], "kernel_dir": os.path.join(self.tmp, "kernel"),
               "pending_kernel": {}, "extra": "a stamp with extra keys is accepted"}
        doc.update(over)
        with open(os.path.join(self.bin, ".deploy-stamp.json"), "w") as fh:
            json.dump(doc, fh)

    def judge(self, ok=True, why="2 row(s) PASS boxed"):
        """A stand in for the live judge that records what it was handed."""
        def run(stamp, tree, log):
            self.seen.append({
                "tree": tree, "stamp_revision": stamp["revision"],
                "status": git(tree, "status", "--porcelain"),
                "head": git(tree, "rev-parse", "HEAD"), "parent": git(tree, "rev-parse", "HEAD~1"),
                "scratch": slurp(os.path.join(tree, LC.LANDING_SCRATCH)) if os.path.isfile(os.path.join(tree, LC.LANDING_SCRATCH)) else None,
                "log_writable": hasattr(log, "write")})
            return ok, why
        return run

    def stage(self, run="canary-1", keep=False, **kw):
        """landing_stage(...); what it said on stderr is in self.said (NO-DATA reasons go there)."""
        err, saved = io.StringIO(), sys.stderr
        sys.stderr = err
        try:
            return LC.landing_stage({"sub": "x"}, run, keep, bin_dir=kw.pop("bin_dir", self.bin), **kw)
        finally:
            sys.stderr = saved
            self.said = err.getvalue()

    def fail_git(self, *words):
        """Make LC._git fail for any command whose argv holds all of `words`; every other command runs for real."""
        real = LC._git

        def fake(argv, cwd=None):
            if all(w in argv for w in words):
                return 1, "stubbed failure"
            return real(argv, cwd)
        LC._git = fake
        self.addCleanup(setattr, LC, "_git", real)

    def refs(self):
        return git(self.repo, "for-each-ref", "--format=%(refname) %(objectname)")

    def worktrees(self):
        return [l for l in git(self.repo, "worktree", "list", "--porcelain").splitlines() if l.startswith("worktree ")]


class TestLandingCanary(Fixture):
    # the three named in the spec
    def test_the_landing_stage_reaches_would_land_on_a_good_fixture(self):
        before = (self.refs(), git(self.repo, "rev-parse", "HEAD"))
        rows = self.stage(judge=self.judge(True, "2 row(s) PASS boxed"))
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["name"], rows[0]["exit_code"]), ("landing judge", 0))
        self.assertIn("PASS boxed", rows[0]["detail"])
        seen = self.seen[0]
        self.assertEqual(seen["stamp_revision"], self.rev)
        self.assertEqual(seen["parent"], self.rev, "the merged tree is the stamp's revision plus one change")
        self.assertEqual(seen["status"], "", "clean: the judge refuses a checkout with uncommitted changes")
        self.assertIn("canary-1", seen["scratch"])
        self.assertTrue(seen["log_writable"])
        self.assertEqual((self.refs(), git(self.repo, "rev-parse", "HEAD")), before, "no branch, tag or HEAD moved in the real repository")
        self.assertEqual(len(self.worktrees()), 1, "the disposable worktree is removed")
        self.assertFalse(os.path.exists(os.path.dirname(seen["tree"])), "and so is the folder that held it and its judge log")
        self.assertFalse(os.path.exists(os.path.join(self.repo, LC.LANDING_SCRATCH)), "the real tree never holds the change")
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")

    def test_a_refused_judge_row_is_named_and_red(self):
        why = "the whole gate exited 1 boxed: plugin-runtime-tests-fast FAIL exit 1, readiness-gate FAIL exit 1"
        rows = self.stage(judge=self.judge(False, why))
        self.assertEqual(rows[0]["exit_code"], 1)
        self.assertIn("readiness-gate", rows[0]["detail"])
        self.assertIn("plugin-runtime-tests-fast", rows[0]["detail"])
        self.assertEqual(len(self.worktrees()), 1, "a refused stage removes its worktree too")
        rc, out = self.main(rows)
        self.assertEqual(rc, 1)
        self.assertEqual(out[-1].split(": ", 1)[0], "CANARY REFUSED at landing judge")
        self.assertIn("readiness-gate FAIL exit 1", out[-1], "the named row is the last line, the one start_run.sh reads")

    def test_no_data_revision_is_no_data_not_a_pass(self):
        gone = "0123456789abcdef0123456789abcdef01234567"
        self.write_stamp(revision=gone)
        self.assertEqual(self.stage(judge=self.judge()), [])
        self.assertIn("is not in this repository", self.said)
        self.assertEqual(self.seen, [], "no judge ran for a revision this repository does not hold")
        rc, out = self.main([])
        self.assertEqual(rc, 3)
        self.assertTrue(out[-1].startswith("CANARY NO-DATA"), out)

    # the edges
    def test_every_unreadable_stamp_is_no_data(self):
        stamp = os.path.join(self.bin, ".deploy-stamp.json")
        cases = (("no stamp file", lambda: os.remove(stamp)),
                 ("a torn stamp", lambda: spoil_with(stamp, '{"revision": "')),
                 ("not a dict", lambda: spoil_with(stamp, "[1]")),
                 ("no kernel, the bootstrap generation", lambda: self.write_stamp(kernel=None)),
                 ("no revision", lambda: self.write_stamp(revision=None)),
                 ("a revision that is not hex", lambda: self.write_stamp(revision="main")),
                 ("a revision that is a bool", lambda: self.write_stamp(revision=True)))
        for label, spoil in cases:
            with self.subTest(label):
                self.write_stamp()
                spoil()
                self.assertEqual(self.stage(judge=self.judge()), [])
        self.assertEqual(self.seen, [])
        self.assertEqual(self.stage(bin_dir=os.path.join(self.tmp, "nowhere"), judge=self.judge()), [])

    def test_a_worktree_that_cannot_be_made_is_no_data(self):
        os.chdir(self.tmp)          # not a repository
        self.assertEqual(self.stage(judge=self.judge()), [])

    def test_a_commit_that_cannot_be_made_is_no_data_and_leaves_nothing(self):
        saved = LC.LANDING_SCRATCH
        LC.LANDING_SCRATCH = "../escape.txt"        # a path outside the worktree: refused before any write
        self.addCleanup(setattr, LC, "LANDING_SCRATCH", saved)
        self.assertEqual(self.stage(judge=self.judge()), [])
        self.assertIn("leaves the worktree", self.said, "refused for the path, before any write")
        self.assertEqual(len(self.worktrees()), 1)

    def test_a_worktree_add_that_fails_is_no_data_and_leaves_nothing(self):
        self.fail_git("worktree", "add")
        self.assertEqual(self.stage(judge=self.judge()), [])
        self.assertIn("git worktree add failed", self.said)
        self.assertEqual(self.seen, [], "no judge ran")

    def test_a_commit_that_fails_is_no_data_and_leaves_nothing(self):
        self.fail_git("commit")
        self.assertEqual(self.stage(judge=self.judge()), [])
        self.assertIn("failed in the disposable worktree", self.said)
        self.assertEqual(self.seen, [], "no judge ran")
        self.assertEqual(len(self.worktrees()), 1)

    def test_no_temporary_folder_is_no_data(self):
        real = LC.tempfile.mkdtemp

        def broken(*a, **k):
            raise OSError("full")
        LC.tempfile.mkdtemp = broken
        self.addCleanup(setattr, LC.tempfile, "mkdtemp", real)
        self.assertEqual(self.stage(judge=self.judge()), [])
        self.assertIn("no temporary folder", self.said)

    def test_bad_arguments_are_refused_before_any_worktree(self):
        for label, args in (("a spec that is not a dict", ([], "r")), ("a spec that is None", (None, "r")),
                            ("an empty run", ({}, "")), ("a run that is not a string", ({}, 7)), ("a blank run", ({}, "  "))):
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    LC.landing_stage(args[0], args[1], bin_dir=self.bin, judge=self.judge())
        self.assertEqual(self.seen, [])
        self.assertEqual(len(self.worktrees()), 1)

    def test_the_repositorys_hooks_never_run_for_the_scratch_commit(self):
        hooks = os.path.join(self.repo, ".git", "hooks")
        marker = os.path.join(self.tmp, "post-commit-ran")
        for name, body in (("pre-commit", "#!/bin/sh\nexit 1\n"), ("commit-msg", "#!/bin/sh\nexit 1\n"),
                           ("post-commit", "#!/bin/sh\ntouch %s\n" % marker)):
            spoil_with(os.path.join(hooks, name), body)
            os.chmod(os.path.join(hooks, name), 0o755)
        rows = self.stage(judge=self.judge())
        self.assertEqual(rows[0]["exit_code"], 0, rows)
        self.assertFalse(os.path.exists(marker), "a hook ran inside the disposable worktree")

    def test_an_ignored_scratch_path_is_still_committed(self):
        spoil_with(os.path.join(self.repo, ".gitignore"), "logs/\n")
        git(self.repo, "add", ".gitignore")
        git(self.repo, "commit", "-q", "-m", "ignore logs")
        self.rev = git(self.repo, "rev-parse", "HEAD")
        self.write_stamp(revision=self.rev)
        rows = self.stage(judge=self.judge())
        self.assertEqual(rows[0]["exit_code"], 0, rows)
        self.assertIn("canary-1", self.seen[0]["scratch"])

    def test_the_commit_needs_no_configured_identity(self):
        git(self.repo, "config", "user.useConfigOnly", "true")      # git would otherwise invent one from the host name
        empty_home = os.path.join(self.tmp, "nohome")
        os.makedirs(empty_home)
        saved = {k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME")}
        os.environ["HOME"], os.environ["XDG_CONFIG_HOME"] = empty_home, empty_home
        try:
            rows = self.stage(judge=self.judge())
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertEqual(rows[0]["exit_code"], 0, rows)

    def test_the_deployed_bin_is_the_deploy_tools_own_switch_else_the_installed_bin(self):
        saved = os.environ.get("BROTHER_DEPLOY_TARGET")
        try:
            os.environ["BROTHER_DEPLOY_TARGET"] = os.path.join(self.tmp, "elsewhere")
            self.assertEqual(LC.deployed_bin(), os.path.join(self.tmp, "elsewhere"))
            os.environ.pop("BROTHER_DEPLOY_TARGET")
            self.assertEqual(LC.deployed_bin(), os.path.expanduser("~/.claude/bin"))
        finally:
            if saved is not None:
                os.environ["BROTHER_DEPLOY_TARGET"] = saved

    def test_keep_leaves_the_worktree_and_says_so(self):
        rows = self.stage(keep=True, judge=self.judge())
        self.assertEqual(rows[0]["exit_code"], 0)
        self.assertIn("kept at", rows[0]["detail"])
        tree = self.seen[0]["tree"]
        self.assertTrue(os.path.isdir(tree))
        self.addCleanup(lambda: (subprocess.run(["git", "worktree", "remove", "--force", tree], cwd=self.repo, env=GIT_ENV,
                                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
                                 shutil.rmtree(os.path.dirname(tree), True)))
        self.assertNotIn("kept at", self.stage(judge=self.judge())[0]["detail"])

    def test_two_canaries_use_two_worktrees(self):
        self.stage(run="canary-a", judge=self.judge())
        self.stage(run="canary-b", judge=self.judge())
        self.assertNotEqual(self.seen[0]["tree"], self.seen[1]["tree"])
        self.assertIn("canary-a", self.seen[0]["scratch"])
        self.assertIn("canary-b", self.seen[1]["scratch"])

    def test_a_judge_that_raises_is_a_refusal_never_a_pass(self):
        def boom(stamp, tree, log):
            raise RuntimeError("the gate fell over")
        rows = self.stage(judge=boom)
        self.assertEqual(rows[0]["exit_code"], 1)
        self.assertIn("the gate fell over", rows[0]["detail"])
        self.assertEqual(len(self.worktrees()), 1)

    def test_a_judge_answer_that_is_not_exactly_true_is_a_refusal(self):
        for answer in (1, "yes", None, [True]):
            with self.subTest(answer=answer):
                rows = self.stage(judge=lambda stamp, tree, log, a=answer: (a, "an odd answer"))
                self.assertEqual(rows[0]["exit_code"], 1)

    def test_a_judge_that_gives_no_reason_is_still_named(self):
        rows = self.stage(judge=lambda stamp, tree, log: (False, ""))
        self.assertEqual(rows[0]["exit_code"], 1)
        self.assertTrue(rows[0]["detail"].startswith("the judge gave no reason"), rows[0]["detail"])
        self.assertIn(os.path.join(self.tmp, "evidence"), rows[0]["detail"])   # the kept log stays in the test's own tree

    def test_a_revision_that_is_already_the_working_trees_head_is_accepted(self):
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.rev)
        self.assertEqual(self.stage(judge=self.judge())[0]["exit_code"], 0)

    # main(): the order and the exit codes
    def main(self, landing_rows, replay=("CANARY WOULD LAND: 1 fixture(s), 5 stage(s) passed", 0)):
        """main() with the replay stage and the landing stage replaced; (exit code, printed lines)."""
        out, called = io.StringIO(), []
        saved = {n: getattr(LC, n) for n in ("load_spec", "missing_fixture_files", "fixtures_of", "stages", "verdict_set", "landing_stage")}
        LC.load_spec = lambda path=None: {"sub": "x", "base_commit": "abc", "build": "b"}
        LC.missing_fixture_files = lambda spec: []
        LC.fixtures_of = lambda spec: [spec]
        LC.stages = lambda fx, run, keep=False, spec_gate=None: [("grade", "OK", "stub")]
        LC.verdict_set = lambda results: replay
        if landing_rows is not None:
            LC.landing_stage = lambda spec, run, keep=False, **kw: called.append((run, keep)) or landing_rows
        saved_target = os.environ.get("BROTHER_DEPLOY_TARGET")
        os.environ["BROTHER_DEPLOY_TARGET"] = self.bin
        stdout, argv = sys.stdout, sys.argv
        sys.stdout = out
        sys.argv = list(self.argv)
        try:
            rc = LC.main()
        finally:
            sys.stdout, sys.argv = stdout, argv
            if saved_target is None:
                os.environ.pop("BROTHER_DEPLOY_TARGET", None)
            else:
                os.environ["BROTHER_DEPLOY_TARGET"] = saved_target
            for n, f in saved.items():
                setattr(LC, n, f)
        self.main_called = called
        return rc, out.getvalue().splitlines()

    def test_main_is_zero_only_when_both_stages_would_land(self):
        rc, out = self.main([{"name": "landing judge", "exit_code": 0, "detail": "2 row(s) PASS boxed"}])
        self.assertEqual(rc, 0, out)
        self.assertTrue(out[-1].startswith("CANARY WOULD LAND"), out)
        self.assertEqual(len(self.main_called), 1, "the landing stage ran once")

    def test_main_passes_keep_to_the_landing_stage_only_when_asked(self):
        row = [{"name": "landing judge", "exit_code": 0, "detail": "x"}]
        self.argv = ["loop_canary.py", "--landing"]
        self.main(row)
        self.assertEqual([k for _r, k in self.main_called], [False])
        self.argv = ["loop_canary.py", "--landing", "--keep"]
        self.main(row)
        self.assertEqual([k for _r, k in self.main_called], [True])

    def test_main_without_landing_is_the_replay_only_the_intake_and_the_deploy_run(self):
        self.argv = ["loop_canary.py"]
        rc, out = self.main([{"name": "landing judge", "exit_code": 1, "detail": "would refuse"}])
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.main_called, [], "no whole gate on a deploy or an intake")
        self.assertTrue(out[-1].startswith("CANARY WOULD LAND"))

    def test_main_does_not_spend_the_whole_gate_when_the_replay_already_refused_or_has_no_data(self):
        for code, word in ((1, "CANARY REFUSED at grade: x"), (3, "CANARY NO-DATA: x")):
            with self.subTest(code=code):
                rc, out = self.main([{"name": "landing judge", "exit_code": 0, "detail": "x"}], replay=(word, code))
                self.assertEqual(rc, code)
                self.assertEqual(self.main_called, [])
                self.assertEqual(out[-1], word)

    def test_main_reads_a_nonzero_row_anywhere_in_the_rows_as_a_refusal(self):
        rows = [{"name": "a", "exit_code": 0, "detail": "fine"}, {"name": "b", "exit_code": 2, "detail": "the second row"}]
        rc, out = self.main(rows)
        self.assertEqual(rc, 1)
        self.assertEqual(out[-1], "CANARY REFUSED at b: the second row")

    def test_a_row_with_no_exit_code_is_not_a_pass(self):
        rc, out = self.main([{"name": "landing judge", "detail": "no exit code in this row"}])
        self.assertEqual(rc, 1, "a row that does not say 0 is a refusal, never a pass")

    # start_run.sh: step 4d (REQ-LR1-D04), through the launcher's own seams in a throwaway repository
    def launcher(self, body, **extra):
        from test_run_watch import Launch
        launch = Launch(self)
        spoil_with(launch.canary, body)
        rc, out = launch.launch(**extra)
        return launch, rc, out

    def test_start_run_goes_on_when_the_canary_would_land(self):
        from test_run_watch import Launch
        launch, rc, out = self.launcher("#!/bin/bash\necho '%s (1 judge row(s)): stub'\nexit 0\n" % LC.LANDING_OK)
        self.assertEqual(rc, 0, out)
        self.assertIn("OK   canary %s (1 judge row(s)): stub" % LC.LANDING_OK, out)
        launch2 = Launch(self)
        spoil_with(launch2.canary, '#!/bin/bash\n[ "$1" = --landing ] || { echo "CANARY NO-DATA: asked for no landing stage"; exit 3; }\necho \'%s: landing asked\'\n' % LC.LANDING_OK)
        rc2, out2 = launch2.launch()
        self.assertEqual(rc2, 0, out2)
        self.assertIn("OK   canary %s: landing asked" % LC.LANDING_OK, out2)
        self.assertTrue(os.path.isfile(os.path.join(launch.evd, "driver.pid")), "the driver started")

    def test_start_run_refuses_on_a_refused_row_and_starts_no_driver(self):
        launch, rc, out = self.launcher("#!/bin/bash\necho OK grade\necho 'CANARY REFUSED at landing judge: readiness-gate FAIL exit 1'\nexit 1\n")
        self.assertEqual(rc, 2, out)
        self.assertIn("START REFUSED [canary]: CANARY REFUSED at landing judge: readiness-gate FAIL exit 1", out)
        self.assertFalse(os.path.exists(os.path.join(launch.evd, "driver.pid")), "no driver was started")

    def test_start_run_refuses_on_no_data_with_the_word_no_data(self):
        launch, rc, out = self.launcher("#!/bin/bash\necho 'CANARY NO-DATA: the landing stage could not run'\nexit 3\n")
        self.assertEqual(rc, 2, out)
        self.assertIn("START REFUSED [canary]: NO-DATA\n", out)
        self.assertFalse(os.path.exists(os.path.join(launch.evd, "driver.pid")))

    def test_start_run_refuses_on_any_other_exit(self):
        launch, rc, out = self.launcher("#!/bin/bash\necho something odd\nexit 7\n")
        self.assertEqual(rc, 2, out)
        self.assertIn("START REFUSED [canary]: the canary exited 7: something odd", out)
        self.assertFalse(os.path.exists(os.path.join(launch.evd, "driver.pid")))

    def test_start_run_runs_a_python_canary_under_python(self):
        from test_run_watch import Launch
        launch = Launch(self)
        py = os.path.join(self.tmp, "canary_stub.py")
        spoil_with(py, "import sys\nprint('CANARY NO-DATA: from python')\nsys.exit(3 if '--landing' in sys.argv else 9)\n")
        rc, out = launch.launch(START_RUN_CANARY=py)
        self.assertEqual(rc, 2, out)
        self.assertIn("START REFUSED [canary]: NO-DATA\n", out)
        self.assertIn("NOTE canary: CANARY NO-DATA: from python", out)

    # review: exit 0 is not enough, the landing stage's own line is required
    def test_start_run_refuses_an_exit_0_that_never_ran_the_landing_stage(self):
        for body in ("echo 'CANARY WOULD LAND: 1 fixture(s), 5 stage(s) passed'", "true", "echo 'CANARY WOULD LAND: the replay'"):
            with self.subTest(body=body):
                launch, rc, out = self.launcher("#!/bin/bash\n%s\nexit 0\n" % body)
                self.assertEqual(rc, 2, out)
                self.assertIn("START REFUSED [canary]: the canary exited 0 without the landing stage's line", out)
                self.assertFalse(os.path.exists(os.path.join(launch.evd, "driver.pid")))

    def test_the_launcher_requires_exactly_the_line_the_canary_prints(self):
        with open(os.path.join(HERE, "start_run.sh"), encoding="utf-8") as fh:
            self.assertIn('"%s"*)' % LC.LANDING_OK, fh.read())

    # review M3, the launcher half: a TERM to start_run.sh during the canary reaches it, waits for its clean up, kills a stubborn one
    def term_the_launcher_during_the_canary(self, body, grace="5"):
        from test_run_watch import Launch
        import signal as sg, time
        launch = Launch(self)
        spoil_with(launch.canary, body)
        p = launch.popen(START_RUN_CANARY_GRACE_S=grace)
        pidfile = os.path.join(launch.evd, "canary.pid")
        for _ in range(300):
            if os.path.exists(pidfile) and slurp(pidfile).strip():
                break
            time.sleep(0.1)
        self.assertTrue(os.path.exists(pidfile), "the launcher never reached the canary")
        canary = int(slurp(pidfile).strip())
        t0 = time.time()
        os.kill(p.pid, sg.SIGTERM)
        out, _ = p.communicate(timeout=60)
        return launch, canary, out, time.time() - t0

    def test_a_term_to_the_launcher_during_the_canary_lets_the_canary_clean_up_first(self):
        body = ("#!/bin/bash\necho $$ > \"$TEST_EVD/canary.pid\"\ntrap 'sleep 1; echo cleaned > \"$TEST_EVD/canary-cleaned\"; exit 143' TERM\n"
                "while :; do sleep 0.2; done\n")
        launch, canary, out, took = self.term_the_launcher_during_the_canary(body)
        self.assertIn("START REFUSED [signal]", out)
        self.assertTrue(os.path.exists(os.path.join(launch.evd, "canary-cleaned")), "the launcher left before the canary finished cleaning up")
        self.assertFalse(self.alive(canary))
        self.assertLess(took, 4, "the launcher waits for the canary to end (about 1 s), not for its whole grace (5 s here)")
        self.assertFalse(os.path.exists(os.path.join(launch.evd, "driver.pid")), "no driver started")

    def test_a_bad_grace_value_falls_back_to_the_default_not_to_no_wait(self):
        body = ("#!/bin/bash\necho $$ > \"$TEST_EVD/canary.pid\"\ntrap 'sleep 1; echo cleaned > \"$TEST_EVD/canary-cleaned\"; exit 143' TERM\n"
                "while :; do sleep 0.2; done\n")
        for bad in ("abc", "", "-3", "2.5"):
            with self.subTest(bad=bad):
                launch, canary, out, took = self.term_the_launcher_during_the_canary(body, grace=bad)
                self.assertTrue(os.path.exists(os.path.join(launch.evd, "canary-cleaned")), "a bad grace must not mean no wait")

    def test_a_canary_that_ignores_term_is_killed_after_the_bound(self):
        body = "#!/bin/bash\necho $$ > \"$TEST_EVD/canary.pid\"\ntrap '' TERM\nwhile :; do sleep 0.2; done\n"
        launch, canary, out, took = self.term_the_launcher_during_the_canary(body)
        self.assertIn("START REFUSED [signal]", out)
        self.assertFalse(self.alive(canary), "a canary that ignores TERM must not outlive the launcher")
        self.assertGreater(took, 4, "it was given its grace first (START_RUN_CANARY_GRACE_S, 5 here, 90 by default)")
        self.assertLess(took, 30)

    def test_the_launchers_default_grace_covers_the_canarys_own_clean_up(self):
        import re
        with open(os.path.join(HERE, "start_run.sh"), encoding="utf-8") as fh:
            m = re.search(r"START_RUN_CANARY_GRACE_S:-([0-9]+)", fh.read())
        self.assertIsNotNone(m)
        self.assertGreaterEqual(int(m.group(1)), LC.CHILD_GRACE_S + 10 + 30,
                                "the child's grace, ten seconds to reap it, and the canary's own git removals")

    def test_start_run_refuses_when_the_checkout_has_no_canary(self):
        launch, rc, out = self.launcher("#!/bin/bash\nexit 0\n", START_RUN_CANARY=os.path.join(self.tmp, "absent_canary.py"))
        self.assertEqual(rc, 2, out)
        self.assertIn("START REFUSED [canary]: no ", out)
        self.assertIn("in this checkout", out)

    # the adapter to the real judge
    def fake_module(self, **attrs):
        mod = types.ModuleType("land_batch_fake")
        for k, v in attrs.items():
            setattr(mod, k, v)
        return mod

    def trace_bin(self):
        folder = os.path.join(self.tmp, "tracebin")
        os.makedirs(folder, exist_ok=True)
        spoil_with(os.path.join(folder, "land_batch.py"), TRACE_LAND_BATCH)
        return folder

    def run_live_judge(self, **env):
        """live_judge over the trace bin: (result or the RuntimeError, the gate log's lines)."""
        logpath = os.path.join(self.tmp, "gate.log")
        self.treedir = os.path.join(self.tmp, "treedir")
        os.makedirs(self.treedir, exist_ok=True)
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            with open(logpath, "w") as log:
                try:
                    out = LC.live_judge({"revision": "r"}, self.treedir, log, bin_dir=self.trace_bin())
                except RuntimeError as exc:
                    out = exc
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        return out, slurp(logpath).splitlines()

    def test_the_live_judge_runs_inside_the_worktree_and_finishes(self):
        before = os.getcwd()
        out, trace = self.run_live_judge()
        self.assertEqual(out, (False, "readiness-gate FAIL exit 1"))
        self.assertEqual([t.split()[0] for t in trace], ["steps", "judge", "finish"])
        self.assertTrue(all(t.split()[1] == "cwd=%s" % self.treedir for t in trace), "every step ran inside the worktree: %r" % (trace,))
        self.assertEqual(os.getcwd(), before, "this process never changed directory: the judge ran in a child")

    def test_the_live_judge_finishes_even_when_the_judge_raises_and_says_it_failed(self):
        out, trace = self.run_live_judge(TRACE_MODE="raise")
        self.assertIsInstance(out, RuntimeError)
        self.assertIn("without a verdict", str(out))
        self.assertEqual([t.split()[0] for t in trace], ["steps", "judge", "finish"], "finish ran after the raise")

    def test_a_judge_child_that_prints_no_verdict_is_never_a_pass(self):
        out, trace = self.run_live_judge(TRACE_MODE="silent")
        self.assertIsInstance(out, RuntimeError)
        self.assertIn("without a verdict", str(out))

    def test_a_judge_child_that_exits_non_zero_after_a_verdict_is_never_a_pass(self):
        out, trace = self.run_live_judge(TRACE_MODE="exit3")
        self.assertIsInstance(out, RuntimeError)
        self.assertIn("exited 3", str(out))

    def test_the_last_verdict_line_wins_and_a_malformed_one_is_none(self):
        self.assertEqual(LC._last('x\nJUDGE [true, "a"]\nJUDGE [false, "b"]\n', "JUDGE"), [False, "b"])
        self.assertIsNone(LC._last("JUDGE {not json\n", "JUDGE"))
        self.assertIsNone(LC._last("nothing here\n", "JUDGE"))
        self.assertIsNone(LC._last("JUDGEX [true, 1]\n", "JUDGE"), "the word, then a space")
        self.assertIsNone(LC._last(None, "JUDGE"))

    def test_a_judge_that_outlives_its_bound_is_killed_and_refused_one_that_fits_is_not(self):
        import time
        saved = LC.JUDGE_BOUND_S
        self.addCleanup(setattr, LC, "JUDGE_BOUND_S", saved)
        LC.JUDGE_BOUND_S = 10
        out, _ = self.run_live_judge(TRACE_MODE="slow")
        self.assertEqual(out, (False, "readiness-gate FAIL exit 1"), "a two second judge fits a ten second bound")
        LC.JUDGE_BOUND_S = 2
        t0 = time.time()
        out, _ = self.run_live_judge(TRACE_MODE="sleep")
        self.assertIsInstance(out, RuntimeError)
        self.assertLess(time.time() - t0, 20, "killed at its bound, not waited for")

    def test_a_judge_log_with_no_file_name_is_refused(self):
        with self.assertRaises(RuntimeError):
            LC.live_judge({"revision": "r"}, self.tmp, io.StringIO(), bin_dir=self.trace_bin())

    def test_a_judge_that_raises_is_a_refusal_row_through_the_stage(self):
        saved = os.environ.get("TRACE_MODE")
        os.environ["TRACE_MODE"] = "raise"
        self.addCleanup(lambda: os.environ.pop("TRACE_MODE", None) if saved is None else os.environ.__setitem__("TRACE_MODE", saved))
        spoil_with(os.path.join(self.bin, "land_batch.py"), TRACE_LAND_BATCH)
        rows = self.stage()
        self.assertEqual(rows[0]["exit_code"], 1, rows)
        self.assertIn("without a verdict", rows[0]["detail"])

    # review M4: the judge is the DEPLOYED land_batch, never this checkout's copy
    def test_the_default_judge_is_the_deployed_land_batch_and_never_the_checkouts(self):
        rows = self.stage()                       # no judge seam: the real adapter, over the fixture bin's land_batch.py
        self.assertEqual(rows[0]["exit_code"], 0, rows)
        self.assertIn("the deployed judge ran", rows[0]["detail"])
        self.assertIn(os.path.join(self.bin, "land_batch.py"), rows[0]["detail"], "named: the file the judge came from")
        self.assertNotIn(os.path.join(HERE, "land_batch.py"), rows[0]["detail"])

    def test_no_deployed_land_batch_is_no_data_never_the_checkouts_copy(self):
        os.remove(os.path.join(self.bin, "land_batch.py"))
        self.assertEqual(self.stage(), [])
        self.assertIn("no land_batch.py in the deployed bin", self.said)
        self.assertEqual(len(self.worktrees()), 1)
        rc, out = self.main(None)
        self.assertEqual(rc, 3, out)
        self.assertTrue(out[-1].startswith("CANARY NO-DATA"), out)

    def test_a_deployed_land_batch_that_does_not_load_is_no_data(self):
        spoil_with(os.path.join(self.bin, "land_batch.py"), "def broken(:\n")
        self.assertEqual(self.stage(), [])
        self.assertIn("gave no stamp", self.said)
        spoil_with(os.path.join(self.bin, "land_batch.py"), "raise RuntimeError('boom at import')\n")
        self.assertEqual(self.stage(), [])
        self.assertIn("boom at import", self.said)

    def test_the_live_judge_with_no_deployed_land_batch_raises_rather_than_falling_back(self):
        saved = os.environ.get("BROTHER_DEPLOY_TARGET")
        os.environ["BROTHER_DEPLOY_TARGET"] = os.path.join(self.tmp, "emptybin")
        os.makedirs(os.environ["BROTHER_DEPLOY_TARGET"])
        try:
            with self.assertRaisesRegex(RuntimeError, "no land_batch.py in the deployed bin"):
                LC.live_judge({"revision": "r"}, self.tmp, io.StringIO())
        finally:
            if saved is None:
                os.environ.pop("BROTHER_DEPLOY_TARGET", None)
            else:
                os.environ["BROTHER_DEPLOY_TARGET"] = saved

    # review minor: a git command that times out is a non zero code, so the clean up still runs
    def test_a_git_command_that_times_out_or_cannot_start_is_a_code_not_an_exception(self):
        real = LC.subprocess.run

        def slow(argv, **kw):
            raise subprocess.TimeoutExpired(argv, 300)
        LC.subprocess.run = slow
        self.addCleanup(setattr, LC.subprocess, "run", real)
        rc, text = LC._git(["status"])
        self.assertEqual(rc, 124)
        self.assertIn("timed out", text)

        def missing(argv, **kw):
            raise FileNotFoundError("git")
        LC.subprocess.run = missing
        self.assertEqual(LC._git(["status"])[0], 127)

    def test_a_timeout_while_removing_the_worktree_still_returns_the_row_and_the_box_goes(self):
        real = LC._git

        def fake(argv, cwd=None):
            if "remove" in argv:
                return 124, "git command timed out after 300 seconds"
            return real(argv, cwd)
        LC._git = fake
        self.addCleanup(setattr, LC, "_git", real)
        rows = self.stage(judge=self.judge())
        self.assertEqual(rows[0]["exit_code"], 0)
        self.assertFalse(os.path.exists(os.path.dirname(self.seen[0]["tree"])), "the box is removed even when git could not remove the worktree")

    # review M3: a TERM or an INT unwinds the finally blocks, so nothing leaks
    SIGNAL_SCRIPT = """
import os, sys, time
sys.path.insert(0, %(here)r)
import loop_canary as LC
repo, binp, ready = sys.argv[1:4]
os.chdir(repo)
os.environ["BROTHER_DEPLOY_TARGET"] = binp
LC.load_spec = lambda path=None: {"sub": "x", "base_commit": "abc", "build": "b"}
LC.missing_fixture_files = lambda spec: []
LC.fixtures_of = lambda spec: [spec]
LC.stages = lambda fx, run, keep=False, spec_gate=None: [("grade", "OK", "stub")]
LC.verdict_set = lambda results: ("CANARY WOULD LAND: stub", 0)
def judge(stamp, tree, log, lb=None):
    with open(ready, "w") as fh:
        fh.write(tree)
    time.sleep(120)
    return True, "never"
LC.live_judge = judge
sys.argv = ["loop_canary.py", "--landing"]
sys.exit(LC.main())
"""

    def interrupt_the_canary(self, sig, ignore_int=False):
        """main() blocked inside the judge, then `sig` to it: (exit code, tree path). The inherited SIGINT can be ignored,
        as a shell leaves it for a background job."""
        import signal as sg
        script = os.path.join(self.tmp, "signal_driver.py")
        spoil_with(script, self.SIGNAL_SCRIPT % {"here": HERE})
        ready = os.path.join(self.tmp, "ready.txt")
        pre = (lambda: sg.signal(sg.SIGINT, sg.SIG_IGN)) if ignore_int else None
        p = subprocess.Popen([sys.executable, "-B", script, self.repo, self.bin, ready], cwd=self.tmp, preexec_fn=pre,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        import time
        for _ in range(200):
            if os.path.exists(ready) or p.poll() is not None:
                break
            time.sleep(0.1)
        self.assertTrue(os.path.exists(ready), "the canary never reached the judge")
        tree = slurp(ready)
        os.kill(p.pid, sig)
        rc = p.wait(timeout=30)
        return rc, tree

    def test_a_term_during_the_judge_removes_both_worktrees_and_exits_143(self):
        import signal as sg
        rc, tree = self.interrupt_the_canary(sg.SIGTERM)
        self.assertEqual(rc, 143)
        self.assertFalse(os.path.exists(tree))
        self.assertFalse(os.path.exists(os.path.dirname(tree)), "the box and its judge log are gone")
        self.assertEqual(len(self.worktrees()), 1, self.worktrees())

    def test_an_int_during_the_judge_unwinds_too_even_when_the_shell_left_int_ignored(self):
        import signal as sg
        rc, tree = self.interrupt_the_canary(sg.SIGINT, ignore_int=True)
        self.assertEqual(rc, 130)
        self.assertFalse(os.path.exists(os.path.dirname(tree)))
        self.assertEqual(len(self.worktrees()), 1, self.worktrees())

    def test_a_term_kills_the_deployed_judge_child_too(self):
        import signal as sg, time
        spoil_with(os.path.join(self.bin, "land_batch.py"), SLEEPY_LAND_BATCH)
        script = os.path.join(self.tmp, "signal_driver_real.py")
        spoil_with(script, (self.SIGNAL_SCRIPT % {"here": HERE}).replace("LC.live_judge = judge\n", ""))
        pidfile = os.path.join(self.tmp, "judge.pid")
        env = dict(os.environ, JUDGE_PID_FILE=pidfile, BROTHER_DEPLOY_TARGET=self.bin)
        p = subprocess.Popen([sys.executable, "-B", script, self.repo, self.bin, os.path.join(self.tmp, "unused.txt")], cwd=self.tmp, env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        for _ in range(300):
            if os.path.exists(pidfile) and slurp(pidfile).strip():
                break
            time.sleep(0.1)
        child = int(slurp(pidfile).strip())
        self.addCleanup(lambda: os.kill(child, 9) if self.alive(child) else None)
        self.assertTrue(self.alive(child))
        os.kill(p.pid, sg.SIGTERM)
        self.assertEqual(p.wait(timeout=30), 143)
        for _ in range(50):
            if not self.alive(child):
                break
            time.sleep(0.1)
        self.assertFalse(self.alive(child), "the deployed judge child survived the canary")
        self.assertEqual(len(self.worktrees()), 1, self.worktrees())

    # round 2, R2-M1: the real land_batch's copy and gate unwind on a TERM (the reviewer's repro shape)
    REAL_GATE_OVERRIDE = """

def deploy_steps(stamp, log, staged):   # the test's gate: the real make_copy, run_group and drop_copy around `exec sleep`
    state = {}
    def judge():
        state["copy"] = make_copy(paths=[])
        r = run_group(["sh", "-c", "echo $$ > " + os.environ["GATE_PID_FILE"] + "; exec sleep 300"], 7200, None, cwd=state["copy"])
        return r.returncode == 0, "gate"
    def finish():
        import time
        time.sleep(1.5)   # a clean up that takes a moment, as a real one does: a kill that comes too soon leaks the copy
        if state.get("copy"):
            drop_copy(state.pop("copy"))
    return {"judge": judge, "finish": finish}
"""

    def real_land_batch_bin(self):
        """A bin holding the REAL land_batch.py (and the one sibling it imports) with its judge replaced by a sleeping gate."""
        folder = os.path.join(self.tmp, "realbin")
        os.makedirs(folder)
        for name in ("plan_path.py", ".deploy-stamp.json"):
            shutil.copy(os.path.join(HERE if name.endswith(".py") else self.bin, name), os.path.join(folder, name))
        spoil_with(os.path.join(folder, "land_batch.py"), slurp(os.path.join(HERE, "land_batch.py")) + self.REAL_GATE_OVERRIDE)
        return folder

    def test_a_term_during_the_real_gate_removes_the_land_copy_worktree_and_kills_the_gate(self):
        import signal as sg, time
        folder = self.real_land_batch_bin()
        script = os.path.join(self.tmp, "signal_driver_gate.py")
        spoil_with(script, (self.SIGNAL_SCRIPT % {"here": HERE}).replace("LC.live_judge = judge\n", ""))
        pidfile = os.path.join(self.tmp, "gate.pid")
        env = dict(os.environ, GATE_PID_FILE=pidfile, BROTHER_DEPLOY_TARGET=folder)
        p = subprocess.Popen([sys.executable, "-B", script, self.repo, folder, os.path.join(self.tmp, "unused.txt")], cwd=self.tmp, env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        for _ in range(400):
            if os.path.exists(pidfile) and slurp(pidfile).strip():
                break
            time.sleep(0.1)
        gate = int(slurp(pidfile).strip())
        self.addCleanup(lambda: os.kill(gate, 9) if self.alive(gate) else None)
        self.assertTrue(self.alive(gate))
        self.assertTrue(any("land-copy-" in w for w in self.worktrees()), "the judge's copy is registered while the gate runs")
        os.kill(p.pid, sg.SIGTERM)
        self.assertEqual(p.wait(timeout=90), 143)
        for _ in range(50):
            if not self.alive(gate):
                break
            time.sleep(0.1)
        self.assertFalse(self.alive(gate), "the gate (its own session) survived the canary")
        self.assertEqual([w for w in self.worktrees() if "land-copy-" in w], [], "land_batch's copy leaked")
        self.assertEqual(len(self.worktrees()), 1, self.worktrees())

    def test_a_judge_child_that_ignores_term_is_killed_after_the_grace(self):
        import time
        folder = os.path.join(self.tmp, "stubbornbin")
        os.makedirs(folder)
        spoil_with(os.path.join(folder, "land_batch.py"), FAKE_LAND_BATCH.split("def deploy_steps")[0] + """def deploy_steps(stamp, log, staged):
    def judge():
        import signal, time
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        with open(os.environ["JUDGE_PID_FILE"], "w") as fh:
            fh.write(str(os.getpid()))
        time.sleep(300)
    return {"judge": judge, "finish": lambda: None}
""")
        shutil.copy(os.path.join(self.bin, ".deploy-stamp.json"), folder)
        pidfile = os.path.join(self.tmp, "stubborn.pid")
        saved = (LC.CHILD_GRACE_S, os.environ.get("JUDGE_PID_FILE"))
        LC.CHILD_GRACE_S = 2
        os.environ["JUDGE_PID_FILE"] = pidfile
        self.addCleanup(lambda: (setattr(LC, "CHILD_GRACE_S", saved[0]), os.environ.pop("JUDGE_PID_FILE", None) if saved[1] is None
                                 else os.environ.__setitem__("JUDGE_PID_FILE", saved[1])))
        # an interrupt and the bound take the same path (real_run's BaseException and TimeoutExpired both call _kill_group)
        LC.JUDGE_BOUND_S, bound = 2, LC.JUDGE_BOUND_S
        self.addCleanup(setattr, LC, "JUDGE_BOUND_S", bound)
        t0 = time.time()
        with open(os.path.join(self.tmp, "g.log"), "w") as log:
            with self.assertRaises(RuntimeError):
                LC.live_judge({"revision": "r"}, self.tmp, log, bin_dir=folder)
        self.assertLess(time.time() - t0, 30)
        child = int(slurp(pidfile).strip())
        self.assertFalse(self.alive(child), "a judge that ignores TERM must still be gone after the grace")

    # round 2, minor 1: PYTHONPATH cannot shadow what the deployed child imports
    def test_a_planted_pythonpath_module_cannot_shadow_the_deployed_child(self):
        plant = os.path.join(self.tmp, "plant")
        os.makedirs(plant)
        for name in ("json.py", "land_batch.py", "signal.py"):
            spoil_with(os.path.join(plant, name), "raise RuntimeError('shadowed by PYTHONPATH')\n")
        saved = os.environ.get("PYTHONPATH")
        os.environ["PYTHONPATH"] = plant
        try:
            self.assertIsInstance(LC.deployed_stamp(self.bin), dict)
            rows = self.stage()
        finally:
            if saved is None:
                os.environ.pop("PYTHONPATH", None)
            else:
                os.environ["PYTHONPATH"] = saved
        self.assertEqual(rows[0]["exit_code"], 0, rows)
        self.assertIn(os.path.join(self.bin, "land_batch.py"), rows[0]["detail"])

    def test_a_hup_during_the_judge_unwinds_too(self):
        import signal as sg
        rc, tree = self.interrupt_the_canary(sg.SIGHUP)
        self.assertEqual(rc, 129)
        self.assertFalse(os.path.exists(os.path.dirname(tree)))
        self.assertEqual(len(self.worktrees()), 1, self.worktrees())

    def test_the_judge_comes_from_the_bin_the_stage_was_given_not_from_the_environments(self):
        other = os.path.join(self.tmp, "otherbin")
        os.makedirs(other)
        spoil_with(os.path.join(other, "land_batch.py"), FAKE_LAND_BATCH)
        os.environ["BROTHER_DEPLOY_TARGET"] = other          # restored by the fixture's clean up
        rows = self.stage()                                  # bin_dir=self.bin, no judge seam
        self.assertIn(os.path.join(self.bin, "land_batch.py"), rows[0]["detail"])
        self.assertNotIn(os.path.join(other, "land_batch.py"), rows[0]["detail"])

    def test_the_signal_raisers_ignore_a_second_signal_so_the_clean_up_finishes(self):
        import signal as sg
        saved = {n: sg.getsignal(getattr(sg, n)) for n in ("SIGTERM", "SIGINT", "SIGHUP")}
        self.addCleanup(lambda: [sg.signal(getattr(sg, n), h) for n, h in saved.items()])
        LC.install_signal_raisers()
        with self.assertRaises(SystemExit) as ctx:
            LC._raise_on_signal(sg.SIGTERM, None)
        self.assertEqual(ctx.exception.code, 143)
        for n in ("SIGTERM", "SIGINT", "SIGHUP"):
            self.assertEqual(sg.getsignal(getattr(sg, n)), sg.SIG_IGN, n)

    CHILD_SCRIPT = """
import os, sys
sys.path.insert(0, %(here)r)
import loop_canary as LC
LC.install_signal_raisers()
pidfile = sys.argv[1]
LC.real_run(["sh", "-c", "sleep 300 & echo $! > " + pidfile + "; wait"], None)
"""

    def test_a_term_kills_the_children_the_canary_started_not_only_its_own_process(self):
        import signal as sg, time
        script = os.path.join(self.tmp, "child_driver.py")
        spoil_with(script, self.CHILD_SCRIPT % {"here": HERE})
        pidfile = os.path.join(self.tmp, "grandchild.pid")
        p = subprocess.Popen([sys.executable, "-B", script, pidfile], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        for _ in range(100):
            if os.path.exists(pidfile) and slurp(pidfile).strip():
                break
            time.sleep(0.1)
        grand = int(slurp(pidfile).strip())
        self.addCleanup(lambda: os.kill(grand, 9) if self.alive(grand) else None)
        self.assertTrue(self.alive(grand))
        os.kill(p.pid, sg.SIGTERM)
        self.assertEqual(p.wait(timeout=30), 143)
        for _ in range(50):
            if not self.alive(grand):
                break
            time.sleep(0.1)
        self.assertFalse(self.alive(grand), "the grandchild survived the canary")

    def test_a_child_that_outlives_its_bound_is_killed_with_its_whole_group(self):
        import time
        pidfile = os.path.join(self.tmp, "slow.pid")
        saved = LC.RUN_BOUND_S
        LC.RUN_BOUND_S = 1
        self.addCleanup(setattr, LC, "RUN_BOUND_S", saved)
        rc, text = LC.real_run(["sh", "-c", "sleep 300 & echo $! > %s; wait" % pidfile], None)
        self.assertEqual(rc, 99)
        grand = int(slurp(pidfile).strip())
        for _ in range(50):
            if not self.alive(grand):
                break
            time.sleep(0.1)
        self.assertFalse(self.alive(grand))

    @staticmethod
    def alive(pid):
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True


class TestKeptGateLog(Fixture):
    """LR1.d: a refused landing judge's gate log outlives the disposable box (2026-10-21: the log went with the worktree and
    nobody could see why deploy-canary was red)."""

    def setUp(self):
        super().setUp()
        self.evidence = os.path.join(self.tmp, "evidence")
        saved = os.environ.get("START_RUN_EVIDENCE")
        os.environ["START_RUN_EVIDENCE"] = self.evidence
        self.addCleanup(lambda: os.environ.pop("START_RUN_EVIDENCE", None) if saved is None
                        else os.environ.__setitem__("START_RUN_EVIDENCE", saved))

    def refusing_judge(self, text="gate row output\nFAIL    exit 1   deploy-canary   9s  boom\n", rowfile=True, ok=False):
        def run(stamp, tree, log):
            with open(log.name, "a", encoding="utf-8") as fh:      # the judge's child writes the log by its name
                fh.write(text)
            if rowfile:
                with open(os.path.join(os.path.dirname(tree), "required-fast-fail-deploy-canary-k.txt"), "w") as fh:
                    fh.write("the failing row's own output: AssertionError at line 7\n")
            return ok, "the whole gate exited 1 boxed: deploy-canary FAIL exit 1"
        return run

    def kept(self):
        folder = os.path.join(self.evidence, "loop-starts")
        return sorted(os.path.join(folder, n) for n in os.listdir(folder)) if os.path.isdir(folder) else []

    def test_a_refusal_keeps_the_gate_log_and_the_failing_rows_output_after_cleanup(self):
        rows = self.stage(judge=self.refusing_judge())
        self.assertEqual(rows[0]["exit_code"], 1)
        found = self.kept()
        self.assertEqual(len(found), 1, found)
        self.assertIn("(gate log %s)" % found[0], rows[0]["detail"], "the REFUSED line names the copy")
        self.assertTrue(os.path.basename(found[0]).startswith("canary-gate-"))
        text = slurp(found[0])
        self.assertIn("FAIL    exit 1   deploy-canary", text)
        self.assertIn("AssertionError at line 7", text, "the failing row's own output rides along")
        self.assertIn("deploy-canary FAIL exit 1", rows[0]["detail"], "the judge's own words stay on the line")
        self.assertEqual([w for w in self.worktrees() if "loop-canary-landing" in w], [], "the box is gone, the copy is not")

    def test_a_link_planted_in_the_box_is_never_followed_into_the_evidence(self):
        """Security review 2026-10-21 (the link sorts before the real row file, so a refused read must not end the copy):
        the landing under judgement writes the box, so a required-fast-fail-*.txt link
        there must never copy a file from outside the box into the evidence folder."""
        secret = os.path.join(self.tmp, "outside-secret.txt")
        with open(secret, "w") as fh:
            fh.write("PRIVATE-OUTSIDE-THE-BOX\n")
        inner = self.refusing_judge()

        def planting(stamp, tree, log):
            os.symlink(secret, os.path.join(os.path.dirname(tree), "required-fast-fail-0-steal.txt"))
            return inner(stamp, tree, log)
        rows = self.stage(judge=planting)
        self.assertEqual(rows[0]["exit_code"], 1)
        text = slurp(self.kept()[0])
        self.assertNotIn("PRIVATE-OUTSIDE-THE-BOX", text)
        self.assertIn("AssertionError at line 7", text, "the real failing row's output is still kept")

    def test_a_judge_that_raises_keeps_the_log_too(self):
        def boom(stamp, tree, log):
            with open(log.name, "a") as fh:
                fh.write("partial gate output\n")
            raise RuntimeError("the deployed judge child exited 1 without a verdict")
        rows = self.stage(judge=boom)
        self.assertEqual(rows[0]["exit_code"], 1)
        self.assertIn("partial gate output", slurp(self.kept()[0]))

    def test_a_passing_judge_keeps_nothing(self):
        rows = self.stage(judge=self.judge(True, "2 row(s) PASS boxed"))
        self.assertEqual(rows[0]["exit_code"], 0)
        self.assertEqual(self.kept(), [])

    def test_a_huge_gate_log_is_kept_as_its_last_two_megabytes(self):
        big = "x" * (3 * 1024 * 1024) + "THE LAST LINE\n"
        self.stage(judge=self.refusing_judge(text=big, rowfile=False))
        path = self.kept()[0]
        self.assertLess(os.path.getsize(path), 2 * 1024 * 1024 + 4096)
        self.assertTrue(slurp(path).rstrip().endswith("THE LAST LINE"))
        self.assertIn("last %d of" % LC.GATE_LOG_TAIL, slurp(path))

    def test_a_copy_that_cannot_be_made_never_fails_the_refusal_itself(self):
        with open(self.evidence, "w") as fh:        # a FILE where the evidence folder should be: makedirs cannot
            fh.write("in the way")
        rows = self.stage(judge=self.refusing_judge())
        self.assertEqual(rows[0]["exit_code"], 1)
        self.assertEqual(rows[0]["detail"], "the whole gate exited 1 boxed: deploy-canary FAIL exit 1")
        self.assertIn("could not keep the gate log", self.said)


if __name__ == "__main__":
    unittest.main()
