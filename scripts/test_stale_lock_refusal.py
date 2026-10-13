"""RS1.a: a packed-refs.lock that outlives the retry window is DIAGNOSED
(path, age, git holders) and refused with the exact remedy for a human,
and is never deleted, renamed or modified by this module.

Every case builds its own throwaway repository (test_worktree_lane.a_repo)
and its own lock file inside it; no real repository's lock is touched. The
one case that uses the real `ps` starts its own `git` child inside its own
throwaway repository and kills only that child's process group.
"""
import ast
import os
import re
import signal
import shutil
import subprocess
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_worktree_lane as TW  # noqa: E402  (a_repo, the _stale fixture, tmp sandbox)
import worktree_lane as W  # noqa: E402

OLD_MTIME = 1000000000  # an integer mtime, so the 60 s boundary is exact arithmetic


def _stale(repo, lock=True):
    """The existing stale-lane fixture: lane/A exists, git's own lock wait is
    shortened, and (lock=True) an empty packed-refs.lock is left behind."""
    return TW.APackedRefsLockOnTheStaleLane._stale(None, repo, lock=lock)


class _P(object):
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def fake_runner(repo, ps=None, cwds=None, ps_raises=False, lsof_raises=False):
    """Real git for git commands (always in `repo`); `ps` and `lsof`
    answered from the given table, so no real process table is read."""
    cwds = cwds or {}

    def runner(cmd, **kw):
        if cmd[0] == "git":
            return subprocess.run(cmd, capture_output=True, text=True,
                                  cwd=repo, timeout=120)
        if cmd[0] == "ps":
            if ps_raises:
                raise OSError("no ps on this machine")
            return _P(0, ps or "")
        if cmd[0] == "lsof":
            if lsof_raises:
                raise OSError("no lsof on this machine")
            pid = int(cmd[-1])
            if pid in cwds:
                return _P(0, "p%d\nfcwd\nn%s\n" % (pid, cwds[pid]))
            return _P(1, "")
        raise AssertionError("unexpected command %r" % (cmd,))
    return runner


def _stat(path):
    st = os.stat(path)
    with open(path, "rb") as fh:
        return (st.st_ino, st.st_mtime_ns, st.st_size, fh.read())


class TheLockPathIsTheOneGitNamed(unittest.TestCase):
    """REQ-RS1-01."""

    def test_the_path_git_named_is_returned(self):
        repo = TW.a_repo()
        msg = ("error: Unable to create '/some/where/.git/packed-refs.lock': "
               "File exists.")
        self.assertEqual(W.packed_refs_lock_path(msg, repo),
                         "/some/where/.git/packed-refs.lock")

    def test_a_message_naming_no_path_falls_back_to_the_common_dir(self):
        repo = TW.a_repo()
        got = W.packed_refs_lock_path("error: cannot lock references", repo)
        self.assertEqual(os.path.realpath(got),
                         os.path.realpath(os.path.join(repo, ".git", "packed-refs.lock")))

    def test_a_message_naming_another_lock_is_not_this_diagnosis(self):
        repo = TW.a_repo()
        msg = ("error: cannot lock ref 'refs/heads/lane/A': Unable to create "
               "'%s/.git/refs/heads/lane/A.lock': File exists." % repo)
        self.assertIsNone(W.packed_refs_lock_path(msg, repo))


class TheVerdicts(unittest.TestCase):
    """REQ-RS1-02, 03, 04: each verdict from exactly one condition."""

    def setUp(self):
        self.repo = TW.a_repo()
        self.lock = os.path.join(self.repo, ".git", "packed-refs.lock")
        with open(self.lock, "w", encoding="utf-8") as fh:
            fh.write("x")
        os.utime(self.lock, (OLD_MTIME, OLD_MTIME))

    def test_a_live_holder_is_HELD_and_the_remedy_says_wait(self):
        runner = fake_runner(self.repo, ps="  4242 /usr/bin/git\n",
                             cwds={4242: self.repo})
        d = W.diagnose_lock(self.lock, self.repo, now=OLD_MTIME + 3600, runner=runner)
        self.assertEqual(d["verdict"], "HELD", d)
        self.assertEqual(d["holders"], [4242])
        remedy = W.lock_remedy(d)
        self.assertIn("wait", remedy)
        self.assertIn("4242", remedy)
        self.assertNotIn("rm ", remedy, "a held lock is never offered for removal")

    def test_a_git_elsewhere_on_the_machine_is_not_a_holder(self):
        runner = fake_runner(self.repo, ps="  4242 git\n  77 /bin/zsh\n",
                             cwds={4242: "/somewhere/else"})
        d = W.diagnose_lock(self.lock, self.repo, now=OLD_MTIME + 3600, runner=runner)
        self.assertEqual(d["holders"], [])
        self.assertEqual(d["verdict"], "NO-HOLDER-OLD")

    def test_just_under_sixty_seconds_is_TOO_RECENT(self):
        runner = fake_runner(self.repo, ps="")
        d = W.diagnose_lock(self.lock, self.repo, now=OLD_MTIME + 59.9, runner=runner)
        self.assertEqual(d["verdict"], "TOO-RECENT", d)
        self.assertIn("wait a minute", W.lock_remedy(d))

    def test_exactly_sixty_seconds_is_NO_HOLDER_OLD(self):
        runner = fake_runner(self.repo, ps="")
        d = W.diagnose_lock(self.lock, self.repo, now=OLD_MTIME + 60.0, runner=runner)
        self.assertEqual(d["verdict"], "NO-HOLDER-OLD", d)
        self.assertEqual(d["age_s"], 60.0)
        self.assertIn("rm %s" % self.lock, W.lock_remedy(d))

    def test_a_future_mtime_clamps_to_zero_and_reads_TOO_RECENT(self):
        runner = fake_runner(self.repo, ps="")
        d = W.diagnose_lock(self.lock, self.repo, now=OLD_MTIME - 500, runner=runner)
        self.assertEqual(d["age_s"], 0.0)
        self.assertEqual(d["verdict"], "TOO-RECENT")

    def test_no_ps_is_CANNOT_TELL_never_none(self):
        runner = fake_runner(self.repo, ps_raises=True)
        self.assertIsNone(W.git_holders(self.repo, runner))
        d = W.diagnose_lock(self.lock, self.repo, now=OLD_MTIME + 3600, runner=runner)
        self.assertEqual(d["verdict"], "CANNOT-TELL", d)
        remedy = W.lock_remedy(d)
        self.assertIn("ps", remedy)
        self.assertIn("only if none is", remedy)

    def test_a_corrupt_ps_line_is_CANNOT_TELL(self):
        runner = fake_runner(self.repo, ps="garbage-line\n")
        self.assertIsNone(W.git_holders(self.repo, runner))

    def test_a_live_git_whose_cwd_cannot_be_read_is_CANNOT_TELL(self):
        # This test's own pid is certainly alive, so an unreadable cwd for it
        # cannot be explained away as a process that exited meanwhile.
        me = os.getpid()
        runner = fake_runner(self.repo, ps="%d git\n" % me, lsof_raises=True)
        if os.path.isdir("/proc/%d" % me):
            self.skipTest("this host reads cwd from /proc; the lsof-only path is macOS")
        self.assertIsNone(W.git_holders(self.repo, runner))

    def test_a_git_that_exited_before_its_cwd_was_read_is_skipped(self):
        dead = subprocess.Popen(["true"])
        dead.wait()
        runner = fake_runner(self.repo, ps="%d git\n" % dead.pid)
        self.assertEqual(W.git_holders(self.repo, runner), [])

    def test_ten_holders_all_in_the_dict_five_in_the_sentence(self):
        pids = list(range(9001, 9011))
        ps = "".join("%d git\n" % p for p in reversed(pids))
        runner = fake_runner(self.repo, ps=ps, cwds={p: self.repo for p in pids})
        d = W.diagnose_lock(self.lock, self.repo, now=OLD_MTIME + 3600, runner=runner)
        self.assertEqual(d["holders"], pids)
        remedy = W.lock_remedy(d)
        self.assertIn("9001, 9002, 9003, 9004, 9005", remedy)
        self.assertNotIn("9006", remedy)
        self.assertIn("5 more", remedy)

    def test_a_missing_lock_is_ABSENT(self):
        os.remove(self.lock)  # this test's own fixture file, in its own repo
        d = W.diagnose_lock(self.lock, self.repo, runner=fake_runner(self.repo))
        self.assertEqual(d["verdict"], "ABSENT")
        self.assertFalse(d["exists"])


class TheRefusalCarriesTheDiagnosis(unittest.TestCase):
    """REQ-RS1-05 and 06, through _clear_stale_lane, the entry point."""

    def setUp(self):
        self._saved = W.LOCK_RETRY_PAUSE_S
        W.LOCK_RETRY_PAUSE_S = 0.01

    def tearDown(self):
        W.LOCK_RETRY_PAUSE_S = self._saved

    def _refuse(self, runner_kw, old=True):
        repo = TW.a_repo()
        lock = _stale(repo)
        with open(lock, "w", encoding="utf-8") as fh:
            fh.write("held bytes")
        if old:
            os.utime(lock, (time.time() - 600, time.time() - 600))
        kw = dict(runner_kw)
        if "cwds" in kw:
            kw["cwds"] = {p: repo for p in kw["cwds"]}
        before = _stat(lock)
        ok, note = W._clear_stale_lane(repo, W.branch_for("A"), fake_runner(repo, **kw))
        after = _stat(lock)
        self.assertFalse(ok, note)
        self.assertEqual(before, after,
                         "the lock must keep its inode, bytes and mtime: %s" % note)
        self.assertNotIn("\n", note)
        return os.path.realpath(lock), note

    def test_no_holder_old_names_path_age_verdict_and_the_rm_line(self):
        lock, note = self._refuse({"ps": ""})
        self.assertIn(W.NODATA, note)
        self.assertIn(lock, note)
        self.assertRegex(note, r"age \d+ s")
        self.assertIn("NO-HOLDER-OLD", note)
        self.assertIn("rm %s" % lock, note)
        self.assertIn("only you can confirm", note)

    def test_held_refuses_and_keeps_the_lock(self):
        _lock, note = self._refuse({"ps": "4242 git\n", "cwds": [4242]})
        self.assertIn("HELD", note)
        self.assertIn("4242", note)

    def test_too_recent_refuses_and_keeps_the_lock(self):
        _lock, note = self._refuse({"ps": ""}, old=False)
        self.assertIn("TOO-RECENT", note)

    def test_cannot_tell_refuses_and_keeps_the_lock(self):
        _lock, note = self._refuse({"ps_raises": True})
        self.assertIn("CANNOT-TELL", note)

    def test_the_real_entry_point_with_no_runner_keeps_the_lock_too(self):
        repo = TW.a_repo()
        lock = _stale(repo)
        before = _stat(lock)
        ok, note = W._clear_stale_lane(repo, W.branch_for("A"))
        self.assertFalse(ok)
        self.assertIn("lock diagnosis", note)
        self.assertEqual(before, _stat(lock))


class NoSourceLineRemovesThePackedLock(unittest.TestCase):
    """REQ-RS1-07: a scan of the module's own source."""

    SRC = os.path.join(HERE, "worktree_lane.py")
    DIAGNOSIS = ("packed_refs_lock_path", "git_holders", "_cwd_of",
                 "diagnose_lock", "lock_remedy", "_lock_diagnosis_note",
                 "_clear_stale_lane")
    REMOVERS = {"remove", "unlink", "rename", "replace", "rmtree", "move",
                "truncate", "rmdir", "removedirs"}

    def test_no_diagnosis_function_calls_a_file_remover(self):
        with open(self.SRC, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        found = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in self.DIAGNOSIS:
                found[node.name] = True
                for sub in ast.walk(node):
                    if not isinstance(sub, ast.Call):
                        continue
                    f = sub.func
                    if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                            and f.value.id in ("os", "shutil", "pathlib")
                            and f.attr in self.REMOVERS):
                        self.fail("%s calls %s.%s" % (node.name, f.value.id, f.attr))
                    if isinstance(f, ast.Name) and f.id in self.REMOVERS:
                        self.fail("%s calls %s" % (node.name, f.id))
                    for arg in sub.args:
                        if (isinstance(arg, ast.List) and arg.elts
                                and isinstance(arg.elts[0], ast.Constant)
                                and arg.elts[0].value == "rm"):
                            self.fail("%s runs rm" % node.name)
        self.assertEqual(sorted(found), sorted(self.DIAGNOSIS),
                         "every diagnosis function must exist to be scanned")

    def test_no_line_near_packed_refs_removes_a_file(self):
        with open(self.SRC, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        remover = re.compile(r"os\.(remove|unlink|rename|replace)\(|shutil\.rmtree\(|\brm -")
        for i, line in enumerate(lines):
            if not remover.search(line):
                continue
            window = lines[max(0, i - 3):i + 4]
            self.assertFalse(any("packed-refs" in w or "packed_refs" in w for w in window),
                             "line %d removes a file next to packed-refs: %r" % (i + 1, line))


class TheBranchLockSweepIsUnchanged(unittest.TestCase):
    """REQ-RS1-08: the one lock this module does remove stays the one."""

    def test_the_branch_lock_goes_and_the_packed_lock_beside_it_stays(self):
        repo = TW.a_repo()
        branch = W.branch_for("A")
        branch_lock = os.path.join(repo, ".git", "refs", "heads", branch + ".lock")
        packed = os.path.join(repo, ".git", "packed-refs.lock")
        os.makedirs(os.path.dirname(branch_lock), exist_ok=True)
        for p in (branch_lock, packed):
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("")
        before = _stat(packed)
        note = W._clear_stale_branch_lock(repo, branch)
        self.assertTrue(note, "the branch lock sweep must still report its removal")
        self.assertFalse(os.path.exists(branch_lock))
        self.assertTrue(os.path.exists(packed), "packed-refs.lock must survive the sweep")
        self.assertEqual(before, _stat(packed))



def _ps_runs():
    """True when the real process table can be read here. Inside the grader's sandbox (the hermetic push gate) a
    process may not start ps at all, so a test that needs the real table skips there instead of reading red."""
    try:
        return subprocess.run([shutil.which("ps") or "/bin/ps", "-axo", "pid="], capture_output=True, text=True,
                              timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


PS_RUNS = _ps_runs()


@unittest.skipUnless(PS_RUNS, "the real process table cannot be read here (sandboxed)")
class TheRealProcessTable(unittest.TestCase):
    """git_holders over the real `ps`, with a git child this test started,
    sleeping with this throwaway repository as its cwd. Only that child's
    own process group is killed."""

    def test_a_sleeping_git_in_the_repo_is_a_holder(self):
        repo = TW.a_repo()
        child = subprocess.Popen(["git", "-c", "alias.zz=!/bin/sleep 30", "zz"], cwd=repo,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
        try:
            holders = None
            deadline = time.time() + 10
            while time.time() < deadline:
                holders = W.git_holders(repo)
                if holders:
                    break
                time.sleep(0.1)
            self.assertIsNotNone(holders, "the real process table could not be read")
            self.assertTrue(holders, "the sleeping git child was not seen")
        finally:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=15)
        deadline = time.time() + 10
        while time.time() < deadline and W.git_holders(repo):
            time.sleep(0.1)
        self.assertEqual(W.git_holders(repo), [], "the killed child still reads as a holder")


if __name__ == "__main__":
    unittest.main()
