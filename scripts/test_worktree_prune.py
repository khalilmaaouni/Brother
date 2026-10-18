#!/usr/bin/env python3
"""C1.a: the prune lists only clean, pushed, idle, unleased worktrees and removes them without --force.

Git is a fake runner over real temporary folders: a build may not start processes, and every fact the prune reads
reaches it through the runner seam (cut_ready's protocol: runner(argv, cwd, timeout=, env=) -> returncode, stdout)."""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cut_ready  # noqa: E402
import worktree_prune as P  # noqa: E402

OLD = 200000.0          # seconds of idleness given to a tree, well past the 86400 s window
FREE = lambda worktree: ""  # noqa: E731  no lease anywhere


class Result:
    def __init__(self, returncode, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


class FakeGit:
    """Answers the git calls the prune makes, per worktree folder, and records every argv it was given."""

    def __init__(self, repo, trees):
        self.repo = repo
        self.trees = trees          # path -> {"status", "remote", "ignored", "fail", "remove"}
        self.extra = ""             # raw porcelain appended to the worktree list
        self.list_code = 0
        self.calls = []

    def __call__(self, argv, cwd, timeout=None, env=None):
        self.calls.append((list(argv), cwd))
        if argv == ["git", "worktree", "list", "--porcelain"]:
            text = "worktree %s\nHEAD %s\nbranch refs/heads/main\n\n" % (self.repo, "a" * 40)
            for path in self.trees:
                text += "worktree %s\nHEAD %s\nbranch refs/heads/b\n\n" % (path, "b" * 40)
            return Result(self.list_code, text + self.extra)
        if argv[:3] == ["git", "worktree", "remove"]:
            spec = self.trees.get(argv[-1], {})
            return Result(spec.get("remove", 0))
        spec = self.trees.get(cwd)
        if spec is None:
            return Result(128)
        key = {("git", "status", "--porcelain"): "status",
               ("git", "branch", "-r", "--contains", "HEAD"): "remote",
               ("git", "status", "--ignored", "--porcelain"): "ignored"}.get(tuple(argv))
        if key is None or key == spec.get("fail"):
            return Result(128)
        default = {"status": "", "remote": "  origin/main\n", "ignored": ""}[key]
        return Result(0, spec.get(key, default))

    def argvs(self):
        return [argv for argv, cwd in self.calls]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "brother-wt")
        self.repo = os.path.join(self.tmp, "hub")
        os.makedirs(self.repo)
        os.makedirs(self.root)
        self.now = time.time()

    def tree(self, name, age=OLD):
        path = os.path.join(self.root, name)
        os.makedirs(os.path.join(path, "src"))
        with open(os.path.join(path, "src", "a.py"), "w") as fh:
            fh.write("x = 1\n")
        for top, dirs, files in os.walk(path):
            for entry in [top] + [os.path.join(top, f) for f in files]:
                os.utime(entry, (self.now - age, self.now - age))
        return path

    def main(self, argv, runner, leased=FREE):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = P.main(argv + ["--root", self.root, "--repo", self.repo], runner=runner, leased=leased, now=self.now)
        return code, out.getvalue()


class Requirements(Base):
    def test_a_dirty_worktree_is_refused(self):  # REQ-C1A-01
        dirty, clean = self.tree("dirty"), self.tree("clean")
        git = FakeGit(self.repo, {dirty: {"status": " M src/a.py\n?? new.txt\n"}, clean: {}})
        code, out = self.main(["--dry"], git)
        self.assertEqual(code, 0, out)
        self.assertIn("KEEP %s: dirty" % dirty, out)
        self.assertIn("PRUNE %s" % clean, out)
        self.assertNotIn("PRUNE %s\n" % dirty, out)
        self.assertNotIn(["git", "worktree", "remove", dirty], git.argvs())   # --dry removes nothing
        self.assertEqual(P.refusal(dirty, git, FREE), "dirty: git status --porcelain names 2 changed path(s)")
        self.assertEqual(P.candidates(self.root, self.now, runner=git, leased=FREE, repo=self.repo), [clean])

    def test_an_unpushed_worktree_is_refused(self):  # REQ-C1A-02
        path = self.tree("local")
        git = FakeGit(self.repo, {path: {"remote": ""}})
        self.assertEqual(P.refusal(path, git, FREE), "unpushed: HEAD is in no hub ref")
        self.assertEqual(P.candidates(self.root, self.now, runner=git, leased=FREE, repo=self.repo), [])

    def test_ignored_files_refuse_except_caches(self):  # REQ-C1A-03
        build, caches = self.tree("build"), self.tree("caches")
        git = FakeGit(self.repo, {
            build: {"ignored": "!! src/__pycache__/\n!! .env.local\n"},
            caches: {"ignored": "!! src/__pycache__/\n!! a.pyc\n!! \"with space/b.pyc\"\n"}})
        why = P.refusal(build, git, FREE)
        self.assertTrue(why.startswith("ignored: 1 ignored path(s)"), why)
        self.assertIn(".env.local", why)
        self.assertEqual(P.refusal(caches, git, FREE), "")
        self.assertEqual(P.candidates(self.root, self.now, runner=git, leased=FREE, repo=self.repo), [caches])
        pyc_folder = FakeGit(self.repo, {build: {"ignored": "!! out.pyc/\n"}})
        self.assertNotEqual(P.refusal(build, pyc_folder, FREE), "")   # a folder named *.pyc is not one pyc file

    def test_a_leased_worktree_is_refused(self):  # REQ-C1A-04
        held, idle = self.tree("held"), self.tree("idle")
        git = FakeGit(self.repo, {held: {}, idle: {}})
        lease = lambda w: "claimed by builder seat-0" if w == held else ""  # noqa: E731
        self.assertEqual(P.refusal(held, git, lease), "claimed by builder seat-0")
        self.assertEqual(P.refusal(held, git, lambda w: True), "a live lease holds it")
        self.assertEqual(P.candidates(self.root, self.now, runner=git, leased=lease, repo=self.repo), [idle])
        code, out = self.main(["--apply"], git, leased=lease)
        self.assertEqual(code, 0, out)
        self.assertIn("KEEP %s: claimed by builder seat-0" % held, out)
        self.assertNotIn(["git", "worktree", "remove", held], git.argvs())
        # a builder that starts between --dry and --apply: apply asks again and keeps the tree
        later = FakeGit(self.repo, {idle: {}})
        self.assertEqual(P.apply([idle], later, lambda w: "claimed by builder late", self.repo), 1)
        self.assertNotIn(["git", "worktree", "remove", idle], later.argvs())
        # a lease reading that fails or answers nonsense holds
        def boom(w):
            raise OSError("unreadable")
        self.assertIn("lease reading failed", P.refusal(idle, later, boom))
        self.assertIn("not a reason", P.refusal(idle, later, lambda w: 5))

    def test_default_lease_reads_the_sentry_claim(self):  # REQ-C1A-04, the default reader
        held, other = self.tree("held"), self.tree("other")
        mark = os.path.join(self.tmp, "worktree.claim")
        self.assertEqual(P._sentry_lease(held, mark), "")                          # no file, no claim
        with open(mark, "w") as fh:
            fh.write("pid=%d\nat=%s\nstart=\nroot=%s\n" % (os.getpid(), time.time(), held))
        self.assertIn("live pid %d" % os.getpid(), P._sentry_lease(held, mark))
        self.assertEqual(P._sentry_lease(other, mark), "")
        with open(mark, "w") as fh:
            fh.write("pid=%d\nat=1\n" % os.getpid())
        self.assertIn("names no tree", P._sentry_lease(other, mark))              # unknown tree holds
        with open(mark, "w") as fh:
            fh.write("pid=nope\n")
        self.assertIn("no readable pid", P._sentry_lease(other, mark))

    def test_default_lease_reads_builder_claims(self):  # REQ-C1A-04, the default reader
        held, other = self.tree("held"), self.tree("other")
        admin = os.path.join(self.tmp, "admin")
        for path, branch in ((held, "build/C1.a"), (other, "build/other")):
            gitdir = os.path.join(admin, os.path.basename(path))
            os.makedirs(gitdir)
            with open(os.path.join(gitdir, "HEAD"), "w") as fh:
                fh.write("ref: refs/heads/%s\n" % branch)
            with open(os.path.join(path, ".git"), "w") as fh:
                fh.write("gitdir: %s\n" % gitdir)
        runs = os.path.join(self.tmp, "runs")
        self.assertEqual(P._builder_lease(held, runs), "")                         # no runs folder, no claim
        folder = os.path.join(runs, "C1.a-010203")
        os.makedirs(folder)
        record = {"state": "CLAIMED", "sub": "C1.a", "builder": "seat", "pid": os.getpid(), "branch": "build/C1.a",
                  "at": time.time()}
        with open(os.path.join(folder, "CLAIM"), "w") as fh:
            json.dump(record, fh)
        with open(os.path.join(folder, "PID"), "w") as fh:
            fh.write("%d\n" % os.getpid())
        self.assertIn("claimed by builder seat", P._builder_lease(held, runs))
        self.assertEqual(P._builder_lease(other, runs), "")
        with open(os.path.join(folder, "CLAIM"), "w") as fh:
            fh.write("{torn")
        self.assertIn("cannot be read", P._builder_lease(other, runs))            # a corrupt record holds

    def test_apply_never_forces(self):  # REQ-C1A-05
        one, two = self.tree("one"), self.tree("two")
        git = FakeGit(self.repo, {one: {}, two: {"remove": 1}})
        self.assertEqual(P.apply([one, two], git, FREE, self.repo), 1)
        removes = [(argv, cwd) for argv, cwd in git.calls if argv[:3] == ["git", "worktree", "remove"]]
        self.assertEqual(removes, [(["git", "worktree", "remove", one], self.repo),
                                   (["git", "worktree", "remove", two], self.repo)])
        for argv in git.argvs():
            self.assertNotIn("--force", argv)
            self.assertNotIn("-f", argv)
        code, out = self.main(["--apply"], git)
        self.assertEqual(code, 1, out)                       # a removal that fails exits 1
        self.assertIn("REMOVED %s" % one, out)
        self.assertIn("FAILED %s" % two, out)

    def test_disk_floor_names_the_prune(self):  # REQ-C1A-06
        row = cut_ready.disk_row(self.tmp, floor_gib=15, free=lambda path: 14.98)
        self.assertEqual(row["verdict"], cut_ready.FAIL)
        self.assertIn("14.98", row["detail"])
        self.assertIn("run python3 scripts/worktree_prune.py --dry", row["detail"])
        ok = cut_ready.disk_row(self.tmp, floor_gib=15, free=lambda path: 15.0)
        self.assertNotIn("worktree_prune", ok["detail"])


class Edges(Base):
    def test_empty_root_lists_nothing(self):
        git = FakeGit(self.repo, {})
        self.assertEqual(self.main(["--dry"], git), (0, ""))
        self.assertEqual(P.candidates(self.root, self.now, runner=git, leased=FREE, repo=self.repo), [])

    def test_fresh_and_future_trees_are_kept(self):
        fresh, future, idle = self.tree("fresh", age=60), self.tree("future", age=-3600), self.tree("idle")
        git = FakeGit(self.repo, {fresh: {}, future: {}, idle: {}})
        code, out = self.main(["--dry"], git)
        self.assertIn("KEEP %s: active" % fresh, out)
        self.assertIn("KEEP %s: active" % future, out)
        self.assertEqual(P.candidates(self.root, self.now, runner=git, leased=FREE, repo=self.repo), [idle])
        self.assertEqual(P.candidates(self.root, self.now, 10.0 * OLD, git, FREE, self.repo), [])

    def test_outside_root_space_and_symlink(self):
        outside = os.path.join(self.tmp, "elsewhere")
        os.makedirs(outside)
        spaced = self.tree("with space")
        link_root = os.path.join(self.tmp, "link-wt")
        os.symlink(self.root, link_root)
        git = FakeGit(self.repo, {outside: {}, spaced: {}})
        self.assertEqual(P.candidates(self.root, self.now, runner=git, leased=FREE, repo=self.repo), [spaced])
        self.assertEqual(P.candidates(link_root, self.now, runner=git, leased=FREE, repo=self.repo), [spaced])
        code, out = self.main(["--dry"], git)
        self.assertNotIn(outside, out)

    def test_corrupt_locked_and_main_entries_are_kept(self):
        idle = self.tree("idle")
        locked = self.tree("locked")
        git = FakeGit(self.repo, {idle: {}})
        git.extra = "worktree %s\nHEAD %s\nlocked\n\nworktree %s\nsurprise line\n\ngarbage\n" % (
            locked, "c" * 40, os.path.join(self.root, "odd"))
        code, out = self.main(["--dry"], git)
        self.assertEqual(code, 0, out)
        self.assertIn("KEEP %s: git reports it locked" % locked, out)
        self.assertIn("KEEP %s: the porcelain line 'surprise line' does not parse" % os.path.join(self.root, "odd"), out)
        self.assertIn("KEEP None: the porcelain line 'garbage' does not parse", out)
        self.assertEqual(P.candidates(self.root, self.now, runner=git, leased=FREE, repo=self.repo), [idle])

    def test_git_failures_keep_the_worktree(self):
        status, remote, ignored = self.tree("s"), self.tree("r"), self.tree("i")
        git = FakeGit(self.repo, {status: {"fail": "status"}, remote: {"fail": "remote"}, ignored: {"fail": "ignored"}})
        for path in (status, remote, ignored):
            self.assertIn("failed (exit 128)", P.refusal(path, git, FREE))
        self.assertEqual(P.candidates(self.root, self.now, runner=git, leased=FREE, repo=self.repo), [])
        raising = lambda argv, cwd, timeout=None, env=None: 1 / 0  # noqa: E731
        self.assertIn("could not run", P.refusal(status, raising, FREE))
        garbage = lambda argv, cwd, timeout=None, env=None: object()  # noqa: E731
        self.assertNotEqual(P.refusal(status, garbage, FREE), "")

    def test_git_that_cannot_list_exits_3(self):
        git = FakeGit(self.repo, {})
        git.list_code = 127
        self.assertEqual(self.main(["--dry"], git)[0], 3)
        with self.assertRaises(LookupError):
            P.candidates(self.root, self.now, runner=git, leased=FREE, repo=self.repo)

    def test_an_already_removed_worktree_is_a_counted_failure(self):
        gone = os.path.join(self.root, "gone")
        git = FakeGit(self.repo, {gone: {}})
        self.assertEqual(P.apply([gone], git, FREE, self.repo), 1)
        self.assertNotIn(["git", "worktree", "remove", gone], git.argvs())

    def test_hostile_inputs_refuse(self):
        values = [None, 0, True, -1, float("nan"), "", "x", b"x", [], ["x"], {}, {"a": 1}, (), {1, 2}, object()]
        functions = {P.candidates: 6, P.refusal: 3, P.apply: 4, P.main: 5}
        saved = sys.argv
        sys.argv = ["worktree_prune.py"]
        try:
            for function, arity in functions.items():
                for value in values:
                    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                        try:
                            function(*([value] * arity))
                        except (ValueError, LookupError, SystemExit):
                            pass
        finally:
            sys.argv = saved
        for bad in (0, b"x", object(), "--dry", [1], None):
            with self.assertRaises((SystemExit, ValueError)):
                with contextlib.redirect_stderr(io.StringIO()):
                    P.main(bad, runner=FakeGit(self.repo, {}), leased=FREE) if bad is not None else P.main(None)
        with self.assertRaises(ValueError):
            P.candidates(self.root, True)
        with self.assertRaises(ValueError):
            P.candidates(self.root, self.now, float("nan"))
        with self.assertRaises(ValueError):
            P.apply("/a/str/is/not/a/list", FakeGit(self.repo, {}))
        with self.assertRaises(ValueError):
            P.refusal("relative/path", FakeGit(self.repo, {}), FREE)


if __name__ == "__main__":
    unittest.main()
