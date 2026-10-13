"""perturb_pool.py driven both ways. The copy rules run against a real, tiny
git repository this file makes for itself; the pool runs on fakes so each
NO-DATA rule is shown without a minutes long suite."""
import contextlib
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


class W4bTheCountSettles(unittest.TestCase):
    """The load spike from the baselines must not fix the pool at one worker."""

    def _fake(self, loads, wait_s=120):
        loads, t, slept = list(loads), [0.0], []

        def cap(requested, load1=None):
            return PP.capped_workers(requested, load1=loads.pop(0) if load1 is None else load1, ncpu=8)

        def sleep(s):
            slept.append(s)
            t[0] += s
        n = PP.settled_workers(4, wait_s=wait_s, cap=cap, sleep=sleep, clock=lambda: t[0])
        return n, slept

    def test_a_spike_that_decays_gets_the_full_count(self):
        n, slept = self._fake([12.0, 9.0, 3.0])
        self.assertEqual((n, len(slept)), (4, 2))

    def test_a_load_that_stays_high_gives_up_at_the_wait(self):
        n, slept = self._fake([12.0] * 40, wait_s=20)
        self.assertEqual(n, 1)
        self.assertEqual(sum(slept), 20)

    def test_a_quiet_machine_never_waits(self):
        self.assertEqual(self._fake([2.0]), (4, []))


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


NOTE = """# Brother 9.9.9

## Files behind these claims

| Claim | Source file | Goes red when this file breaks |
|---|---|---|
| one | `scripts/a.py` | `scripts/test_x.py` |
| one | `scripts/b.py` | `scripts/test_x.py` |
| two | `scripts/c.py` | NO-DATA |
| two | `scripts/a.py` | `scripts/test_x.py` |
| three | `scripts/d.py` | `scripts/test_y.py` |

## After
"""
FILES = ("scripts/a.py", "scripts/b.py", "scripts/d.py")


class TheMeasurement(Base):
    """PP1.a: measure_table runs the pool over a note's table and records
    wall, workers used, load and counts; infra Nones are counted apart."""

    def note(self, text=NOTE):
        path = os.path.join(self.top, "note.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_table_rows_reads_only_suite_rows_once_each_in_order(self):
        self.assertEqual(PP.table_rows(self.note()),
                         [("scripts/a.py", "scripts/test_x.py"),
                          ("scripts/b.py", "scripts/test_x.py"),
                          ("scripts/d.py", "scripts/test_y.py")])

    def test_a_missing_note_or_one_without_a_table_has_no_rows(self):
        self.assertEqual(PP.table_rows(os.path.join(self.top, "absent.md")), [])
        self.assertEqual(PP.table_rows(self.note("# no table here\n")), [])

    def fake_runner(self, answers, seen):
        def runner(rows, source_root, baseline_wall, workers):
            seen.append((list(rows), source_root, baseline_wall, workers))
            return [(f, s) + answers[f] for f, s in rows]
        return runner

    def test_the_result_counts_infra_nones_apart_from_data_nones(self):
        answers = {"scripts/a.py": (True, "red"),
                   "scripts/b.py": (None, "no work copy: W6: disk floor"),
                   "scripts/d.py": (None, "scripts/d.py does not parse")}
        seen = []
        ticks = iter([100.0, 107.5])
        out = PP.measure_table(self.note(), self.src, 4, 42.0,
                               runner=self.fake_runner(answers, seen),
                               clock=lambda: next(ticks), load=lambda: 1.0)
        self.assertEqual(out["rows"], 3)
        self.assertEqual((out["covered"], out["uncovered"], out["no_data"], out["infra"]),
                         (1, 0, 1, 1))
        self.assertEqual(out["wall_seconds"], 7.5)
        self.assertEqual(out["baseline_wall"], 42.0)
        self.assertEqual(out["workers_requested"], 4)
        self.assertEqual(out["load1_before"], 1.0)
        self.assertEqual(out["load1_after"], 1.0)
        self.assertEqual(out["commit"], self.sha)
        self.assertEqual(seen[0][2], 42.0)

    def test_each_infra_prefix_is_infra(self):
        details = ["no work copy: x", "the worker ended before this row (exit 1): y",
                   "baseline in the copy is not green (1): z"]
        for detail in details:
            out = PP.measure_table(self.note(), self.src, 2, 10.0,
                                   runner=self.fake_runner({f: (None, detail) for f in FILES}, []),
                                   load=lambda: 0.0)
            self.assertEqual((out["infra"], out["no_data"]), (3, 0), detail)

    def test_the_worker_count_is_the_capped_one(self):
        seen = []
        ncpu = os.cpu_count() or 1
        out = PP.measure_table(self.note(), self.src, 4, 10.0,
                               runner=self.fake_runner({f: (False, "green") for f in FILES}, seen),
                               load=lambda: float(ncpu) + 50.0)
        self.assertEqual(out["workers_used"], 1)
        self.assertEqual(seen[0][3], 1, "the runner was asked for more than the cap")
        self.assertEqual(out["uncovered"], 3)

    def test_an_empty_table_never_calls_the_runner(self):
        seen = []
        out = PP.measure_table(self.note("# none\n"), self.src, 4, 10.0,
                               runner=self.fake_runner({}, seen), load=lambda: 0.0)
        self.assertEqual(seen, [])
        self.assertEqual((out["rows"], out["wall_seconds"], out["no_data"]), (0, 0, 0))

    def cli(self, argv, runner=None):
        err, outbuf = io.StringIO(), io.StringIO()
        runner = runner or self.fake_runner({f: (True, "red") for f in FILES}, [])
        with mock.patch.object(PP, "run_rows", runner), \
                mock.patch.object(PP, "SOURCE_ROOT", self.src), \
                contextlib.redirect_stdout(outbuf), contextlib.redirect_stderr(err):
            code = PP.main(argv)
        return code, outbuf.getvalue(), err.getvalue()

    def test_the_cli_prints_one_json_line_and_writes_out_whole(self):
        out_path = os.path.join(self.top, "measured.json")
        with open(out_path, "w") as fh:
            fh.write("{corrupt")
        code, out, _ = self.cli(["--measure", self.note(), "--workers", "2",
                                 "--baseline-wall", "30", "--out", out_path])
        self.assertEqual(code, 0)
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 1, out)
        printed = json.loads(lines[0])
        self.assertEqual(printed["covered"], 3)
        with open(out_path) as fh:
            self.assertEqual(json.load(fh), printed)
        self.assertEqual(sorted(os.listdir(self.top)), ["measured.json", "note.md", "src"],
                         "a temp file was left beside --out")

    def test_the_cli_refuses_an_empty_table_and_writes_nothing(self):
        out_path = os.path.join(self.top, "measured.json")
        code, out, _ = self.cli(["--measure", self.note("# none\n"), "--workers", "2",
                                 "--baseline-wall", "30", "--out", out_path])
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", out)
        self.assertFalse(os.path.exists(out_path))

    def test_a_runner_that_raises_leaves_no_out_file(self):
        def boom(*a, **k):
            raise OSError("no space")
        out_path = os.path.join(self.top, "measured.json")
        code, out, _ = self.cli(["--measure", self.note(), "--workers", "2",
                                 "--baseline-wall", "30", "--out", out_path], runner=boom)
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", out)
        self.assertEqual(sorted(os.listdir(self.top)), ["note.md", "src"])

    def test_any_other_argument_shape_is_the_usage_error(self):
        for argv in ([], ["--measure"], ["--measure", "n", "--workers", "x",
                                          "--baseline-wall", "3"], ["--bogus"]):
            with self.assertRaises(SystemExit) as ctx:
                self.cli(argv)
            self.assertIn("usage", str(ctx.exception.code), argv)


if __name__ == "__main__":
    unittest.main()
