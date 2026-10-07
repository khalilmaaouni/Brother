"""perturb_pool.py driven both ways. The copy rules run against a real, tiny
git repository this file makes for itself; the pool runs on fakes so each
NO-DATA rule is shown without a minutes long suite."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import perturb_pool as PP  # noqa: E402

ENV = PP.clean_env({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})


def sh(args, cwd):
    return subprocess.run(args, cwd=cwd, env=ENV, capture_output=True, text=True, check=True)


class Base(unittest.TestCase):
    def setUp(self):
        # realpath: macOS hands out /var/... which is a link to /private/var/...
        self.top = os.path.realpath(tempfile.mkdtemp(prefix="pool-test-"))
        self.addCleanup(shutil.rmtree, self.top, True)
        if PP.FORBIDDEN_PATH_TEXT in self.top:
            self.skipTest("NO-DATA: this machine's temp root contains /tmp, which W2 refuses")
        self.src = os.path.join(self.top, "src")
        os.makedirs(self.src)
        sh(["git", "init", "-q", "-b", "main", self.src], self.top)
        with open(os.path.join(self.src, "a.txt"), "w") as fh:
            fh.write("one\n")
        sh(["git", "add", "a.txt"], self.src)
        sh(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
            "commit", "-q", "-m", "one"], self.src)
        self.sha = sh(["git", "rev-parse", "HEAD"], self.src).stdout.strip()


class TheCopy(Base):
    def test_a_copy_is_a_real_clone_at_the_named_commit_and_clean(self):
        dest = os.path.join(self.top, "copy0")
        self.assertEqual(PP.make_copy(self.src, dest, floor_gib=0), self.sha)
        self.assertEqual(sh(["git", "rev-parse", "HEAD"], dest).stdout.strip(), self.sha)
        self.assertEqual(sh(["git", "status", "--porcelain"], dest).stdout, "",
                         "the marker must stand beside the copy, never inside it")
        self.assertTrue(os.path.isfile(dest + PP.MARKER_SUFFIX))

    def test_uncommitted_work_in_the_source_is_not_in_the_copy(self):
        with open(os.path.join(self.src, "a.txt"), "w") as fh:
            fh.write("dirty\n")
        dest = os.path.join(self.top, "copy0")
        PP.make_copy(self.src, dest, floor_gib=0)
        self.assertEqual(open(os.path.join(dest, "a.txt")).read(), "one\n")

    def test_w2_a_path_with_tmp_in_it_is_refused_before_anything_is_made(self):
        dest = os.path.join(self.top, "tmp0")
        with self.assertRaises(PP.CopyFailed) as ctx:
            PP.make_copy(self.src, dest, floor_gib=0)
        self.assertIn("W2", str(ctx.exception))
        self.assertFalse(os.path.exists(dest))

    def test_w6_under_the_disk_floor_nothing_is_made(self):
        dest = os.path.join(self.top, "copy0")
        with self.assertRaises(PP.CopyFailed) as ctx:
            PP.make_copy(self.src, dest, floor_gib=15, free=lambda p: 14.9)
        self.assertIn("W6", str(ctx.exception))
        self.assertFalse(os.path.exists(dest))

    def test_w5_only_a_copy_this_module_made_is_removed(self):
        stranger = os.path.join(self.top, "someones-work")
        os.makedirs(stranger)
        with self.assertRaises(PP.CopyFailed):
            PP.remove_copy(stranger)
        self.assertTrue(os.path.isdir(stranger))
        dest = os.path.join(self.top, "copy0")
        PP.make_copy(self.src, dest, floor_gib=0)
        PP.remove_copy(dest)
        self.assertFalse(os.path.exists(dest))
        self.assertFalse(os.path.exists(dest + PP.MARKER_SUFFIX))

    def test_a_source_that_is_not_a_repository_is_copy_failed(self):
        with self.assertRaises(PP.CopyFailed):
            PP.make_copy(self.top, os.path.join(self.top, "copy0"), floor_gib=0)

    def test_an_inherited_git_dir_does_not_reach_git(self):
        os.environ["GIT_DIR"] = "/nonexistent/real/.git"
        self.addCleanup(os.environ.pop, "GIT_DIR", None)
        self.assertNotIn("GIT_DIR", PP.clean_env())
        self.assertEqual(PP.make_copy(self.src, os.path.join(self.top, "copy0"), floor_gib=0),
                         self.sha)


class W4TheWorkerCap(unittest.TestCase):
    def test_a_quiet_machine_gets_what_it_asked_up_to_its_cores(self):
        self.assertEqual(PP.capped_workers(4, load1=2.0, ncpu=8), 4)
        self.assertEqual(PP.capped_workers(32, load1=2.0, ncpu=8), 8)

    def test_a_loaded_machine_gets_one(self):
        self.assertEqual(PP.capped_workers(8, load1=8.0, ncpu=8), 1)
        self.assertEqual(PP.capped_workers(8, load1=36.0, ncpu=8), 1)

    def test_never_zero(self):
        self.assertEqual(PP.capped_workers(0, load1=0.0, ncpu=8), 1)


class FakeProc(object):
    def __init__(self, returncode=0, err=b""):
        self.returncode, self._err = returncode, err

    def communicate(self):
        return b"", self._err


class ThePool(Base):
    ROWS = [("scripts/a.py", "scripts/test_x.py"), ("scripts/b.py", "scripts/test_x.py"),
            ("scripts/c.py", "scripts/test_y.py")]

    def run_pool(self, answer, copier=None, workers=2):
        """`answer(argv)` plays the worker: returns the rows it writes, or None."""
        seen = []

        def spawn(argv, env):
            seen.append((argv, env))
            rows = json.load(open(argv[4]))
            wrote = answer(rows)
            if wrote is not None:
                json.dump(wrote, open(argv[6], "w"))
            return FakeProc(0 if wrote is not None else 1, b"worker died")
        orig = PP.capped_workers
        PP.capped_workers = lambda requested, **kw: requested
        self.addCleanup(setattr, PP, "capped_workers", orig)
        out = PP.run_rows(self.ROWS, self.src, 600, workers=workers, base=self.top,
                          spawn=spawn,
                          copier=copier or (lambda s, d: PP.make_copy(s, d, floor_gib=0)))
        return out, seen

    def test_verdicts_come_back_in_the_order_asked_whoever_ran_them(self):
        out, seen = self.run_pool(lambda rows: [[f, s, True, "red"] for f, s in rows])
        self.assertEqual([(f, s) for f, s, _, _ in out], self.ROWS)
        self.assertEqual([v for _, _, v, _ in out], [True, True, True])
        self.assertEqual(len(seen), 2)

    def test_a_false_stays_false_and_a_none_stays_none(self):
        verdicts = {"scripts/a.py": True, "scripts/b.py": False, "scripts/c.py": None}
        out, _ = self.run_pool(lambda rows: [[f, s, verdicts[f], "x"] for f, s in rows])
        self.assertEqual([v for _, _, v, _ in out], [True, False, None])

    def test_a_copy_that_cannot_be_made_is_no_data_for_its_rows_never_a_skip(self):
        def copier(src, dest):
            if dest.endswith("copy1"):
                raise PP.CopyFailed("W6: disk floor")
            PP.make_copy(src, dest, floor_gib=0)
        out, seen = self.run_pool(lambda rows: [[f, s, True, "red"] for f, s in rows], copier)
        self.assertEqual(len(out), 3, "a row was dropped")
        self.assertEqual([v for _, _, v, _ in out], [True, None, True])
        self.assertIn("no work copy", out[1][3])
        self.assertEqual(len(seen), 1)

    def test_a_worker_that_dies_mid_list_is_no_data_for_what_it_never_answered(self):
        out, _ = self.run_pool(lambda rows: [[rows[0][0], rows[0][1], True, "red"]]
                               if len(rows) > 1 else None)
        by = {f: (v, d) for f, _, v, d in out}
        self.assertEqual(by["scripts/a.py"][0], True)
        self.assertIsNone(by["scripts/c.py"][0])
        self.assertIsNone(by["scripts/b.py"][0])
        self.assertIn("worker", by["scripts/b.py"][1])

    def test_the_child_gets_no_git_variable_and_a_scratch_without_tmp(self):
        os.environ["GIT_DIR"] = "/nonexistent/real/.git"
        self.addCleanup(os.environ.pop, "GIT_DIR", None)
        _, seen = self.run_pool(lambda rows: [[f, s, True, "red"] for f, s in rows])
        for argv, env in seen:
            self.assertFalse([k for k in env if k.startswith("GIT_")])
            self.assertNotIn(PP.FORBIDDEN_PATH_TEXT, env["TMPDIR"])
            self.assertTrue(os.path.isdir(env["TMPDIR"]))

    def test_every_copy_is_removed_and_the_source_is_untouched(self):
        self.run_pool(lambda rows: [[f, s, True, "red"] for f, s in rows])
        self.assertEqual([e for e in os.listdir(self.top) if e.startswith("copy")], [])
        self.assertEqual(sh(["git", "status", "--porcelain"], self.src).stdout, "")
        self.assertEqual(sh(["git", "rev-parse", "HEAD"], self.src).stdout.strip(), self.sha)


if __name__ == "__main__":
    unittest.main()
