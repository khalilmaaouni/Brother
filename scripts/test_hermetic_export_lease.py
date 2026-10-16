"""SP4: hermetic_test_check.check() takes its export tree through export_tree_cache.lease_into when the caller passes no
build of its own, so an unchanged export is built once and leased after that. Driven at the entry point (check()) on a
temporary git repository with a stub exporter that counts its builds; the cache lives under a temporary BROTHER_SCRATCH,
never the real one. What is cached is the exported bytes, never a verdict: every call still runs the selected test."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import export_public  # noqa: E402
import hermetic_test_check as H  # noqa: E402

TEST = "scripts/test_leased.py"


def _git(cwd, *args):
    """git with hooks off, a fixed identity and no GIT_ variable from the host; raises on a non zero exit."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                        "-c", "core.hooksPath=/dev/null"] + list(args), cwd=cwd, capture_output=True, text=True,
                       env=env, timeout=60)
    if r.returncode != 0:
        raise AssertionError("git %s failed: %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout


class Base(unittest.TestCase):
    TEST_BODY = "print('OK')\n"

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="test-lease-root-")
        self.addCleanup(shutil.rmtree, self.root, True)
        # OUTSIDE the root: a scratch folder inside it would make the root dirty, and a dirty root is never cached
        self.scratch = tempfile.mkdtemp(prefix="test-lease-scratch-")
        self.addCleanup(shutil.rmtree, self.scratch, True)
        for rel, body in (("scripts/required_fast.sh", "exit 0\n"), (TEST, self.TEST_BODY),
                          ("docs/plan/EXPORT-ALLOWLIST.txt", "scripts\n")):
            os.makedirs(os.path.dirname(os.path.join(self.root, rel)), exist_ok=True)
            with open(os.path.join(self.root, rel), "w") as fh:
                fh.write(body)
        _git(self.root, "init", "-q", "-b", "main")
        self.commit("fixture")
        env = mock.patch.dict(os.environ, {"BROTHER_SCRATCH": self.scratch})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("BROTHER_SANDBOX", None)   # restored by the patcher: an opt out would unwrap every run
        self.builds = []
        stub = mock.patch.object(export_public, "build_orphan_commit", self.stub_exporter)
        stub.start()
        self.addCleanup(stub.stop)

    def commit(self, message):
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", message)

    def stub_exporter(self, dest, allowlist, root):
        """The exporter's shape (an orphan commit of the allowlisted paths in `dest`), counting every build."""
        self.builds.append(dest)
        shutil.copytree(os.path.join(root, "scripts"), os.path.join(dest, "scripts"))
        _git(dest, "init", "-q")
        _git(dest, "add", "-A", "-f")
        _git(dest, "commit", "-q", "-m", "export")
        return ["scripts"], True

    def entries(self):
        base = os.path.join(self.scratch, "export-tree-cache")
        return sorted(n for n in os.listdir(base) if len(n) == 16) if os.path.isdir(base) else []

    def orphan(self):
        names = self.entries()
        self.assertEqual(len(names), 1, names)
        return os.path.join(self.scratch, "export-tree-cache", names[0], "orphan")


class Canned(Base):
    """The run itself is canned (the sandbox wrap pinned present); what is measured is the build count and the tree each
    run was handed."""

    def setUp(self):
        super(Canned, self).setUp()
        for name, value in (("_sandbox_present", True), ("_sandbox_applies", "")):
            p = mock.patch.object(H.G, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        self.runs = []

    def runner(self, cmd, **kw):
        home = kw["env"]["HOME"]
        tree = kw["cwd"]
        self.runs.append({"home": sorted(os.listdir(home)), "git_is_file": os.path.isfile(os.path.join(tree, ".git")),
                          "test_there": os.path.isfile(os.path.join(tree, TEST))})
        return subprocess.CompletedProcess(cmd, 0, "OK\n", "")

    def check(self, **kw):
        res = H.check(self.root, [TEST], self.runner, **kw)
        self.assertEqual(len(res), 1, res)
        self.assertEqual(res[0][0], H.OK, res)
        self.assertIn("passes on the export tree", res[0][2], res)
        return res

    def test_an_unchanged_tree_is_built_once_over_two_calls_and_both_calls_run_the_test(self):
        self.check()
        self.check()
        self.assertEqual(len(self.builds), 1, "the second call rebuilt an export it already had")
        self.assertEqual(len(self.runs), 2, "a cached export must never stand in for a run")
        for run in self.runs:
            self.assertTrue(run["git_is_file"], "the run was not handed a leased worktree")
            self.assertTrue(run["test_there"])
            self.assertEqual([n for n in run["home"] if n != "tmp"], [], "HOME was not empty: %r" % run["home"])

    def test_a_new_commit_builds_again(self):
        self.check()
        with open(os.path.join(self.root, "scripts", "more.py"), "w") as fh:
            fh.write("x = 1\n")
        self.commit("more")
        self.check()
        self.assertEqual(len(self.builds), 2, "a new HEAD tree reused the old export")

    def test_a_dirty_root_builds_every_time_and_caches_nothing(self):
        with open(os.path.join(self.root, "scripts", "untracked.py"), "w") as fh:
            fh.write("x = 1\n")
        self.check()
        self.check()
        self.assertEqual(len(self.builds), 2, "a dirty root was served from the cache")
        self.assertEqual(self.entries(), [])
        self.assertFalse(any(r["git_is_file"] for r in self.runs), "a dirty root's run was handed a leased tree")

    def test_a_partial_entry_with_no_meta_is_rebuilt_then_reused(self):
        self.check()
        os.remove(os.path.join(os.path.dirname(self.orphan()), "meta.json"))
        self.check()
        self.assertEqual(len(self.builds), 2, "an entry with no meta.json was reused")
        self.check()
        self.assertEqual(len(self.builds), 2, "the rebuilt entry was not reused")

    def test_a_modified_entry_is_rebuilt_and_never_handed_out(self):
        self.check()
        with open(os.path.join(self.orphan(), TEST), "a") as fh:
            fh.write("# tampered\n")
        self.check()
        self.assertEqual(len(self.builds), 2, "a modified entry was reused")
        with open(os.path.join(self.orphan(), TEST)) as fh:
            self.assertNotIn("tampered", fh.read())

    def test_a_caller_build_is_used_exactly_and_the_cache_is_never_touched(self):
        handed = []

        def build(dest):
            handed.append(dest)
            os.makedirs(os.path.join(dest, "scripts"))
            shutil.copy(os.path.join(self.root, TEST), os.path.join(dest, TEST))
        self.check(build=build)
        self.check(build=build)
        self.assertEqual(len(handed), 2, "the caller's build ran %d times over two calls" % len(handed))
        self.assertEqual(self.builds, [], "the stub exporter ran although the caller passed its own build")
        self.assertFalse(os.path.isdir(os.path.join(self.scratch, "export-tree-cache")), "the cache was touched")

    def test_the_slow_host_hunt_leases_the_same_way(self):
        # the sibling caller of the same default builder (hermetic_test_check.slow_host), at its own entry point
        with mock.patch.dict(os.environ, {"LOCAL_SLOTS": "1"}):
            for _ in range(2):
                rows = H.slow_host(self.root, [TEST], 0, repeats=1, runner=self.runner, held="0",
                                   hog_starter=lambda n: [])
                self.assertEqual([r[0] for r in rows], [H.OK], rows)
        self.assertEqual(len(self.builds), 1, "the slow host rebuilt an export it already had")
        self.assertEqual(len(self.runs), 2)
        self.assertTrue(all(r["git_is_file"] for r in self.runs))


def _sandbox_runs_here():
    """True when the grader's sandbox can start a process here, MEASURED by starting one (macOS refuses a nested
    sandbox, so inside the hermetic push check this reads False and the class skips: NO-DATA, never a pass)."""
    if not H.G.contained():
        return False
    home = tempfile.mkdtemp(prefix="test-lease-sandbox-probe-")
    try:
        r = subprocess.run(H.G.sandboxed(["/usr/bin/true"], home, home), capture_output=True, timeout=30)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError, H.G.SandboxRefused):
        return False
    finally:
        shutil.rmtree(home, ignore_errors=True)


@unittest.skipUnless(_sandbox_runs_here(), "the grader's sandbox cannot start a process here (no sandbox-exec, no "
                                            "profile, or this run is already inside a sandbox, which macOS will not nest)")
class TheLeasedTreeUnderTheRealSandbox(Base):
    """The real run: the default runner, the real sandbox. The fixture test checks its own HOME is empty, finds the cache
    entry through its worktree link and tries to write into it."""
    TEST_BODY = (
        "import os, sys\n"
        "extra = [n for n in os.listdir(os.environ['HOME']) if n != 'tmp']\n"
        "if extra:\n"
        "    print('HOME is not empty: %r' % extra); sys.exit(1)\n"
        "top = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
        "with open(os.path.join(top, '.git')) as fh:\n"
        "    gitdir = fh.read().split('gitdir:', 1)[1].strip()\n"
        "orphan = os.path.dirname(os.path.dirname(os.path.dirname(gitdir)))\n"
        "try:\n"
        "    with open(os.path.join(orphan, 'scripts', 'planted.txt'), 'w') as fh:\n"
        "        fh.write('x')\n"
        "except OSError:\n"
        "    print('OK the cache entry refused the write'); sys.exit(0)\n"
        "print('WROTE into the cache entry'); sys.exit(1)\n")

    def test_a_test_runs_with_an_empty_home_on_the_leased_tree_and_cannot_write_into_the_cache(self):
        for _ in range(2):
            res = H.check(self.root, [TEST])
            self.assertEqual(res[0][0], H.OK, res)
            self.assertIn("OK the cache entry refused the write", res[0][2], res)
        self.assertEqual(len(self.builds), 1)
        self.assertFalse(os.path.exists(os.path.join(self.orphan(), "scripts", "planted.txt")))
        self.assertEqual(_git(self.orphan(), "status", "--porcelain"), "", "the cache entry was modified")


if __name__ == "__main__":
    unittest.main()
