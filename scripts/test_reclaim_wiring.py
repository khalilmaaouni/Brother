#!/usr/bin/env python3
"""ORCH-23 calibration for reclaim_wiring.py.

Worktree wiring is exercised against REAL invocations of
reclaim_worktrees.py (report mode only, never --apply) and
orchestrator_dependents.py, using real temporary git fixtures, mirroring
test_reclaim_worktrees.py's own style: mocking either script here would only
prove a mock is self-consistent, never that the command line this module
builds is one the real scripts actually accept. A first draft of this
module built orchestrator_dependents.py's argv without --path or --root;
every subprocess call it made would have exited 2 (NO-DATA) for the wrong
reason, and a suite that only ever injected a fake `run` never caught it.

reclaim_worktrees.py is NEVER invoked with --apply anywhere in this file,
even against a temporary fixture, per the worker brief: apply_worktree_
reclaim() and wire_worktrees(apply=True) are exercised only through an
injected fake `run` that never actually starts the real tool's --apply path.
"""
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
import reclaim_wiring as RW  # noqa: E402


def run_git(args, cwd=None):
    subprocess.run(["git"] + list(args), cwd=cwd, check=True, capture_output=True, text=True)


class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class WorktreeWiringRealFixture(unittest.TestCase):
    """A real bare remote, a real root checkout, real worktrees: the same
    shape scripts/test_reclaim_worktrees.py already builds, so this module's
    own subprocess calls run against the real reclaim_worktrees.py and the
    real orchestrator_dependents.py, never a fake."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="reclaim-wiring-test-")
        self.remote = os.path.join(self.tmp, "remote.git")
        run_git(["init", "--bare", "-q", self.remote])
        self.root = os.path.join(self.tmp, "root")
        run_git(["clone", "-q", self.remote, self.root])
        run_git(["-C", self.root, "config", "user.email", "t@example.com"])
        run_git(["-C", self.root, "config", "user.name", "t"])
        with open(os.path.join(self.root, "a.txt"), "w") as f:
            f.write("root\n")
        run_git(["-C", self.root, "add", "a.txt"])
        run_git(["-C", self.root, "commit", "-q", "-m", "init"])
        branch = subprocess.run(
            ["git", "-C", self.root, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()
        run_git(["-C", self.root, "push", "-q", "-u", "origin", branch])
        self.real_root = os.path.realpath(self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _worktree(self, name, branch, pushed=True):
        path = os.path.join(self.tmp, name)
        run_git(["-C", self.root, "worktree", "add", "-q", "-b", branch, path])
        run_git(["-C", path, "config", "user.email", "t@example.com"])
        run_git(["-C", path, "config", "user.name", "t"])
        with open(os.path.join(path, "b.txt"), "w") as f:
            f.write(name + "\n")
        run_git(["-C", path, "add", "b.txt"])
        run_git(["-C", path, "commit", "-q", "-m", "wt commit"])
        if pushed:
            run_git(["-C", path, "push", "-q", "-u", "origin", branch])
        return os.path.realpath(path)

    def test_clean_pushed_worktree_with_no_consumer_is_eligible(self):
        path = self._worktree("wt-clean", "wt-clean-branch")
        result = RW.wire_worktrees([self.real_root], apply=False)
        self.assertIn(path, result["candidates"], result)
        self.assertIn(path, result["eligible"], result)
        self.assertEqual(result["blocked"], [])
        self.assertIsNone(result["applied"])

    def test_a_consumer_file_blocks_eligibility(self):
        path = self._worktree("wt-consumed", "wt-consumed-branch")
        consumer_dir = os.path.join(self.tmp, "consumer")
        os.makedirs(consumer_dir)
        with open(os.path.join(consumer_dir, "job.sh"), "w") as f:
            f.write("cd %s && ./run.sh\n" % path)
        result = RW.wire_worktrees([self.real_root, consumer_dir], apply=False)
        self.assertIn(path, result["candidates"], result)
        self.assertNotIn(path, result["eligible"], result)
        self.assertIn(path, [p for p, _ in result["blocked"]], result)

    def test_dependents_clear_reflects_a_real_clean_run(self):
        path = self._worktree("wt-alone", "wt-alone-branch")
        clear, detail = RW.dependents_clear(path, [self.real_root])
        self.assertTrue(clear, detail)
        self.assertIn("CLEAR", detail)

    def test_dependents_clear_reflects_a_real_refusal(self):
        path = self._worktree("wt-named", "wt-named-branch")
        consumer_dir = os.path.join(self.tmp, "consumer2")
        os.makedirs(consumer_dir)
        with open(os.path.join(consumer_dir, "job.sh"), "w") as f:
            f.write("cd %s\n" % path)
        clear, detail = RW.dependents_clear(path, [consumer_dir])
        self.assertFalse(clear, detail)
        self.assertIn("REFUSED", detail)

    def test_dependents_clear_reflects_a_real_no_data(self):
        # a root that does not exist makes orchestrator_dependents.py exit 2
        clear, detail = RW.dependents_clear(
            "/does/not/matter", [os.path.join(self.tmp, "does-not-exist")], repo=self.real_root)
        self.assertFalse(clear, detail)
        self.assertIn("NO-DATA", detail)


class WorktreeApplyNeverRunsForReal(unittest.TestCase):
    """apply_worktree_reclaim() and wire_worktrees(apply=True) are checked
    only against an injected fake `run`: the worker brief forbids invoking
    reclaim_worktrees.py with --apply at all, even in a temp fixture, so
    these assert the COMMAND LINE built is correct without ever letting a
    real --apply subprocess start."""

    def test_apply_command_carries_skip_list(self):
        calls = []

        def fake_run(cmd, timeout=120):
            calls.append(cmd)
            return FakeCompleted(0, "1 released, 0 held, 0 failed\n")

        proc = RW.apply_worktree_reclaim(["/r"], ["/skip1", "/skip2"], run=fake_run)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(len(calls), 1)
        cmd = calls[0]
        self.assertIn("reclaim_worktrees.py", cmd[1])
        self.assertIn("--apply", cmd)
        self.assertIn("--skip", cmd)
        self.assertIn("/skip1", cmd)
        self.assertIn("/skip2", cmd)

    def test_wire_worktrees_never_applies_with_zero_eligible(self):
        def fake_run(cmd, timeout=120):
            if "orchestrator_dependents.py" in cmd[1]:
                return FakeCompleted(1, "dependents: REFUSED\n")
            if "--apply" in cmd:
                self.fail("reclaim_worktrees.py --apply must never be invoked here: %r" % cmd)
            return FakeCompleted(0, "WOULD RELEASE /tmp/wt1\n")

        result = RW.wire_worktrees(["/r"], apply=True, run=fake_run)
        self.assertEqual(result["eligible"], [])
        self.assertIsNone(result["applied"])

    def test_wire_worktrees_applies_only_eligible_and_skips_the_rest(self):
        applied_cmd = []

        def fake_run(cmd, timeout=120):
            if "orchestrator_dependents.py" in cmd[1]:
                path = cmd[cmd.index("--path") + 1]
                if path == "/tmp/wt1":
                    return FakeCompleted(0, "dependents: CLEAR\n")
                return FakeCompleted(2, "dependents: NO-DATA\n")
            if "--apply" in cmd:
                applied_cmd.append(cmd)
                return FakeCompleted(0, "1 released, 0 held, 0 failed\n")
            return FakeCompleted(0, "WOULD RELEASE /tmp/wt1\nWOULD RELEASE /tmp/wt2\n")

        result = RW.wire_worktrees(["/r"], apply=True, run=fake_run)
        self.assertEqual(result["eligible"], ["/tmp/wt1"])
        self.assertEqual([p for p, _ in result["blocked"]], ["/tmp/wt2"])
        self.assertIsNotNone(result["applied"])
        self.assertEqual(len(applied_cmd), 1)
        self.assertIn("--skip", applied_cmd[0])
        self.assertIn("/tmp/wt2", applied_cmd[0])
        self.assertNotIn("/tmp/wt1", applied_cmd[0])


class EvidenceRetention(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="reclaim-wiring-evidence-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make(self, name, size, age_seconds, now):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as f:
            f.write(b"x" * size)
        os.utime(path, (now - age_seconds, now - age_seconds))
        return path

    def test_older_than_retention_is_listed(self):
        now = time.time()
        old = self._make("old.log", 10, 3 * 86400, now)
        result = RW.scan_prunable(self.tmp, retention_days=1, max_bytes=10**9, now=now)
        self.assertEqual([e["path"] for e in result], [old])

    def test_within_retention_but_past_budget_is_listed(self):
        now = time.time()
        newer = self._make("new.log", 100, 10, now)
        older = self._make("older.log", 100, 20, now)
        result = RW.scan_prunable(self.tmp, retention_days=10, max_bytes=50, now=now)
        paths = [e["path"] for e in result]
        self.assertNotIn(newer, paths)
        self.assertIn(older, paths)

    def test_within_both_is_never_listed(self):
        now = time.time()
        self._make("keep.log", 10, 10, now)
        result = RW.scan_prunable(self.tmp, retention_days=10, max_bytes=10**9, now=now)
        self.assertEqual(result, [])

    def test_missing_path_raises(self):
        with self.assertRaises(RW.ScanIncomplete):
            RW.scan_prunable(os.path.join(self.tmp, "nope"), 1, 10**9)

    def test_stat_failure_raises_not_skips(self):
        path = self._make("bad.log", 10, 0, time.time())
        real_lstat = os.lstat

        def flaky(p, *a, **kw):
            if p == path:
                raise OSError(5, "boom")
            return real_lstat(p, *a, **kw)

        with mock.patch.object(os, "lstat", side_effect=flaky):
            with self.assertRaises(RW.ScanIncomplete):
                RW.scan_prunable(self.tmp, retention_days=0, max_bytes=10**9)


class EvidenceApply(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="reclaim-wiring-apply-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _entry(self, name, content=b"x"):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as f:
            f.write(content)
        return {"path": path, "size": len(content), "mtime": 0, "reasons": ["test"]}

    def test_free_file_is_deleted(self):
        entry = self._entry("free.log")
        deleted, blocked = RW.apply_prune([entry], in_use=lambda p: False)
        self.assertEqual(deleted, [entry["path"]])
        self.assertEqual(blocked, [])
        self.assertFalse(os.path.exists(entry["path"]))

    def test_in_use_file_is_blocked_not_deleted(self):
        entry = self._entry("busy.log")
        deleted, blocked = RW.apply_prune([entry], in_use=lambda p: True)
        self.assertEqual(deleted, [])
        self.assertEqual(len(blocked), 1)
        self.assertTrue(os.path.exists(entry["path"]))

    def test_unknown_owner_blocks_mutation_target(self):
        """THE MUTATION THIS GUARDS: if apply_prune ever stopped treating a
        failed liveness check as busy, this goes red. A check that raises
        must block the delete, never proceed past it as if it were free."""
        entry = self._entry("unknown.log")

        def raising(_path):
            raise RuntimeError("cannot determine owner")

        deleted, blocked = RW.apply_prune([entry], in_use=raising)
        self.assertEqual(deleted, [])
        self.assertEqual(len(blocked), 1)
        self.assertTrue(os.path.exists(entry["path"]))

    def test_default_in_use_blocks_when_lsof_cannot_run(self):
        entry = self._entry("default.log")
        with mock.patch.object(RW.subprocess, "run", side_effect=OSError("lsof missing")):
            deleted, blocked = RW.apply_prune([entry])
        self.assertEqual(deleted, [])
        self.assertEqual(len(blocked), 1)
        self.assertTrue(os.path.exists(entry["path"]))


class FileInUseFailsClosed(unittest.TestCase):
    def test_oserror_reads_as_in_use(self):
        with mock.patch.object(RW.subprocess, "run", side_effect=OSError("no lsof")):
            self.assertTrue(RW.file_in_use("/does/not/matter"))

    def test_exit_2_reads_as_in_use(self):
        with mock.patch.object(RW.subprocess, "run", return_value=FakeCompleted(2, "")):
            self.assertTrue(RW.file_in_use("/does/not/matter"))

    def test_exit_1_empty_reads_as_free(self):
        with mock.patch.object(RW.subprocess, "run", return_value=FakeCompleted(1, "")):
            self.assertFalse(RW.file_in_use("/does/not/matter"))

    def test_exit_0_with_output_reads_as_in_use(self):
        with mock.patch.object(RW.subprocess, "run", return_value=FakeCompleted(0, "p123\n")):
            self.assertTrue(RW.file_in_use("/does/not/matter"))


class UnregisteredClones(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="reclaim-wiring-clones-")
        self.repo = os.path.join(self.tmp, "repo")
        run_git(["init", "-q", self.repo])
        run_git(["-C", self.repo, "config", "user.email", "t@example.com"])
        run_git(["-C", self.repo, "config", "user.name", "t"])
        with open(os.path.join(self.repo, "f.txt"), "w") as f:
            f.write("hello\n")
        run_git(["-C", self.repo, "add", "f.txt"])
        run_git(["-C", self.repo, "commit", "-q", "-m", "init"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_plain_clone_outside_any_repo_is_unregistered(self):
        scan_dir = os.path.join(self.tmp, "scan")
        os.makedirs(scan_dir)
        clone = os.path.realpath(os.path.join(scan_dir, "clone"))
        run_git(["clone", "-q", self.repo, clone])
        found = RW.unregistered_clones([scan_dir], [self.repo])
        self.assertIn(clone, [c["path"] for c in found])
        self.assertGreater([c for c in found if c["path"] == clone][0]["bytes"], 0)

    def test_a_registered_worktree_is_never_listed(self):
        scan_dir = os.path.join(self.tmp, "scan2")
        os.makedirs(scan_dir)
        wt = os.path.join(scan_dir, "wt")
        run_git(["-C", self.repo, "worktree", "add", "-q", "-b", "wtbranch", wt])
        found = RW.unregistered_clones([scan_dir], [self.repo])
        self.assertEqual(found, [])

    def test_missing_scan_root_raises(self):
        with self.assertRaises(RW.ScanIncomplete):
            RW.unregistered_clones([os.path.join(self.tmp, "nope")], [self.repo])

    def test_git_failure_on_a_known_repo_raises(self):
        not_a_repo = os.path.join(self.tmp, "notrepo")
        os.makedirs(not_a_repo)
        with self.assertRaises(RW.ScanIncomplete):
            RW.unregistered_clones([self.tmp], [not_a_repo])


if __name__ == "__main__":
    unittest.main(verbosity=2)
