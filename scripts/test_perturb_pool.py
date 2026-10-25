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
import export_public  # noqa: E402
import perturb_pool as PP  # noqa: E402

ENV = PP.clean_env({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})


def sh(args, cwd):
    return subprocess.run(args, cwd=cwd, env=ENV, capture_output=True, text=True, check=True)


class Base(unittest.TestCase):
    def setUp(self):
        # CS8.a: a pool copy lives under the one scratch root, so the fixture does too (never the system temp)
        # realpath: macOS hands out /var/... which is a link to /private/var/...
        self.top = os.path.realpath(tempfile.mkdtemp(prefix="pool-test-", dir=export_public.scratch_root()))
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
                raise PP.CopyFailed("W2: the copy path contains /tmp")
            PP.make_copy(src, dest, floor_gib=0)
        out, seen = self.run_pool(lambda rows: [[f, s, True, "red"] for f, s in rows], copier)
        self.assertEqual(len(out), 3, "a row was dropped")
        self.assertEqual([v for _, _, v, _ in out], [True, None, True])
        self.assertIn("no work copy", out[1][3])
        self.assertEqual(len(seen), 1)

    def test_a_disk_refusal_while_a_worker_runs_waits_for_its_copy_to_go(self):
        # 1.1.8 rehearsal, 2026-10-24: copy W6 read 15.0 GiB under the 15 GiB floor while five workers held copies, and 30
        # rows read NO-DATA; each worker's copy is removed when it ends, so the slice is measured after one does
        def copier(src, dest):
            if dest.endswith("copy1"):
                raise PP.CopyFailed("W6: NO-DATA: 15.0 GiB free under the pool, floor is 15")
            PP.make_copy(src, dest, floor_gib=0)
        out, seen = self.run_pool(lambda rows: [[f, s, True, "red"] for f, s in rows], copier)
        self.assertEqual([v for _, _, v, _ in out], [True, True, True])
        self.assertEqual(len(seen), 2)

    def test_a_disk_refusal_with_nothing_running_is_no_data(self):
        def copier(src, dest):
            if not dest.endswith("copy0"):
                raise PP.CopyFailed("W6: NO-DATA: 14.0 GiB free under the pool, floor is 15")
            PP.make_copy(src, dest, floor_gib=0)
        out, seen = self.run_pool(lambda rows: [[f, s, True, "red"] for f, s in rows], copier)
        self.assertEqual([v for _, _, v, _ in out], [True, None, True])
        self.assertIn("no work copy: W6", out[1][3])
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



# FR1.d1 and FR1.d2 (docs/plan/specs/FR1.md): baseline receipts, the disk floor
# read first, the worker count read again. Fixtures isolate one condition each.
import release_note_from_tree as RN  # noqa: E402
import release_note_perturb as RP  # noqa: E402
import cut_rehearsal as CR  # noqa: E402

VER = "9.9.9"
SUITE = "scripts/test_x.py"


def _quiet(*args, **kw):
    return contextlib.redirect_stderr(io.StringIO())


class TestFR1D(unittest.TestCase):
    """The baseline receipts (d1)."""

    def setUp(self):
        self.top = os.path.realpath(tempfile.mkdtemp(prefix="fr1d-test-", dir=export_public.scratch_root()))
        self.addCleanup(shutil.rmtree, self.top, True)
        self.root = os.path.join(self.top, "repo")
        self.ev = os.path.join(self.top, "evidence")
        for rel in CR.SCRIPT_DIRS:
            os.makedirs(os.path.join(self.root, rel))
            with open(os.path.join(self.root, rel, "keep.txt"), "w") as fh:
                fh.write("keep\n")
        with open(os.path.join(self.root, SUITE), "w") as fh:
            fh.write("# suite\n")
        sh(["git", "init", "-q", "-b", "main", self.root], self.top)
        sh(["git", "add", "-A"], self.root)
        sh(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
            "commit", "-q", "-m", "one"], self.root)
        RP._RECEIPT_DURATION.clear()
        self.addCleanup(RP._RECEIPT_DURATION.clear)
        self.path = RP.baseline_path(VER, self.ev)

    def write(self, seconds=12.0, now=None, suite=SUITE):
        return RP.write_baseline_receipt(suite, seconds, VER, root=self.root, directory=self.ev, now=now)

    def load(self, now=None, suite=SUITE):
        return RP.load_baseline_seconds(suite, VER, root=self.root, directory=self.ev, now=now)

    def edit(self, fn):
        data = json.load(open(self.path))
        fn(data["suites"][SUITE])
        json.dump(data, open(self.path, "w"))

    def commit_change(self, rel, text):
        with open(os.path.join(self.root, rel), "a") as fh:
            fh.write(text)
        sh(["git", "add", "-A"], self.root)
        sh(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
            "commit", "-q", "-m", "two"], self.root)

    # pooled_rows through its real entry point
    def pooled(self, perturb, version=VER):
        pool = lambda pairs, root, wall, workers: [(f, s, True, "d") for f, s in pairs]
        env = {"BROTHER_CUT_TEST": "1", "BROTHER_CUT_EVIDENCE_DIR": self.ev}
        with mock.patch.dict(os.environ, env), mock.patch.object(RN, "ROOT", self.root), \
                mock.patch.object(RN, "subject_files", return_value=["scripts/a.py"]), \
                mock.patch("refresh_cut.dirty_paths", return_value=([], "")), \
                mock.patch.object(PP, "free_gib", lambda p: 999.0), _quiet():
            return RN.pooled_rows([("c", SUITE)], perturb, pool, workers=1, version=version)

    def real_perturb(self, rc=0):
        calls = []
        patches = (mock.patch.object(RP, "run_suite", lambda s, **k: calls.append(s) or (rc, "")),
                   mock.patch.object(RP, "sibling_suite", lambda rel: None))
        for p_ in patches:
            p_.start()
            self.addCleanup(p_.stop)
        return RP, calls

    def test_a_green_baseline_writes_a_receipt(self):
        perturb, calls = self.real_perturb()
        self.pooled(perturb)
        entry = json.load(open(self.path))["suites"][SUITE]
        self.assertEqual((calls, entry["exit"], entry["interpreter"]), ([SUITE], 0, sys.executable))
        self.assertEqual(entry["suite_sha256"], RP._file_sha(self.root, SUITE))
        self.assertEqual(entry["trees"], RP.script_tree_key(self.root)[0])
        self.assertGreater(entry["seconds"], 0)

    def test_an_exact_receipt_skips_the_baseline_run(self):
        self.assertEqual(self.write(12.0), "")
        perturb, calls = self.real_perturb()
        rows, problem = self.pooled(perturb)
        self.assertEqual((calls, problem, len(rows)), ([], None, 1))
        self.assertEqual(RP._RECEIPT_DURATION[SUITE], 12.0)

    def test_a_changed_suite_reruns_its_baseline(self):
        self.write()
        # commit, so only the suite digest can differ once the trees are re-pinned
        self.commit_change(SUITE, "# more\n")
        self.edit(lambda e: e.update(trees=RP.script_tree_key(self.root)[0]))
        self.assertIsNone(self.load()[0])
        self.assertIn("changed since", self.load()[1])

    def test_a_changed_script_tree_reruns_its_baseline(self):
        self.write()
        self.commit_change("products/brothermode/scripts/keep.txt", "more\n")
        self.assertIn("script tree changed", self.load()[1])

    def test_another_interpreter_reruns_its_baseline(self):
        self.write()
        self.edit(lambda e: e.update(interpreter="/elsewhere/python"))
        self.assertIn("another interpreter", self.load()[1])

    def test_a_stale_receipt_reruns_its_baseline(self):
        self.write(now=1000.0)
        self.assertIsNone(self.load(now=1000.0 + RP.RECEIPT_MAX_AGE_S + 1)[0])
        self.assertEqual(self.load(now=1000.0 + RP.RECEIPT_MAX_AGE_S)[0], 12.0)
        self.assertIsNone(self.load(now=1000.0 - RP.RECEIPT_FUTURE_SLACK_S - 1)[0])

    def test_a_red_or_unrunnable_baseline_writes_no_receipt(self):
        perturb, calls = self.real_perturb(rc=1)
        self.pooled(perturb)
        self.assertEqual(calls, [SUITE])
        self.assertFalse(os.path.exists(self.path))
        for bad in (None, 0, -3.0, float("nan"), float("inf"), "12", True):
            self.assertNotEqual(self.write(bad), "", bad)
        self.assertFalse(os.path.exists(self.path))
        self.assertNotEqual(self.write(suite="scripts/missing.py"), "")

    def test_the_wall_is_the_larger_of_receipt_and_live(self):
        RP._RECEIPT_DURATION[SUITE] = 100.0
        self.assertEqual(RP.wall_for(SUITE), 100 * RP.WALL_FACTOR + 1)
        with mock.patch.dict(RP._LAST_DURATION, {SUITE: 200.0}):
            self.assertEqual(RP.wall_for(SUITE), 200 * RP.WALL_FACTOR + 1)
        with mock.patch.dict(RP._LAST_DURATION, {SUITE: 50.0}):
            self.assertEqual(RP.wall_for(SUITE), 100 * RP.WALL_FACTOR + 1)
        RP._RECEIPT_DURATION.clear()
        self.assertEqual(RP.wall_for(SUITE), RP.TIMEOUT)
        self.assertEqual(RP._C01_CALIBRATED.get((self.root, SUITE)), None)

    def test_a_dirty_script_tree_reads_and_writes_no_receipt(self):
        self.write()
        with open(os.path.join(self.root, "scripts", "keep.txt"), "a") as fh:
            fh.write("dirty\n")
        self.assertIsNone(self.load()[0])
        os.remove(self.path)
        self.assertIn("uncommitted", self.write())
        self.assertFalse(os.path.exists(self.path))

    def test_a_corrupt_receipt_file_is_replaced_not_trusted(self):
        for junk in ('{"schema": "cv1.bas', "[1, 2]", json.dumps({"schema": RP.BASELINE_SCHEMA, "suites": []})):
            os.makedirs(self.ev, exist_ok=True)
            open(self.path, "w").write(junk)
            self.assertIsNone(self.load()[0])
            self.assertEqual(self.write(7.0, now=5.0), "")
            self.assertEqual(self.load(now=5.0)[0], 7.0)
        self.assertTrue([e for e in os.listdir(self.ev) if ".corrupt-" in e])
        self.edit(lambda e: e.pop("trees"))  # an entry missing a field is ignored
        self.assertIsNone(self.load(now=5.0)[0])

    def test_a_perturb_object_without_receipts_still_runs_every_baseline(self):
        self.write()

        class Bare(object):
            def __init__(self):
                self.ran = []

            def run_suite(self, s):
                self.ran.append(s)
                return 0, ""

            def wall_for(self, s):
                return 60

            def sibling_suite(self, rel):
                return None
        bare = Bare()
        self.pooled(bare)
        self.assertEqual(bare.ran, [SUITE])

    def test_a_receipt_for_some_suites_reuses_only_those(self):
        self.write()
        self.assertIsNone(self.load(suite="scripts/test_other.py")[0])
        self.assertEqual(self.write(3.0, suite=SUITE), "")  # merge keeps one file whole
        self.assertEqual(self.load()[0], 3.0)

    def test_no_version_uses_no_receipts(self):
        self.write()
        perturb, calls = self.real_perturb()
        self.pooled(perturb, version=None)
        self.assertEqual(calls, [SUITE])

    # ---- FR1.d2: the disk floor first, the worker count re-read, a line per admission ----
    ROWS8 = [("scripts/m%d.py" % i, "scripts/test_%s.py" % "xy"[i % 2]) for i in range(8)]

    def pool_run(self, rows, workers, settled, cap, dies=()):
        """run_rows with a synchronous spawn and a fake copier. Returns (out, spawned row lists, say lines)."""
        spawned, lines, n = [], [], [0]
        base = tempfile.mkdtemp(prefix="run-", dir=self.top)

        def copier(src, dest):
            os.makedirs(dest)
            open(dest + PP.MARKER_SUFFIX, "w").close()

        def spawn(argv, env):
            mine = json.load(open(argv[4]))
            spawned.append([tuple(r) for r in mine])
            n[0] += 1
            if n[0] - 1 in dies:
                return FakeProc(1, b"worker died")
            json.dump([[f, s_, True, "red"] for f, s_ in mine], open(argv[6], "w"))
            return FakeProc(0)
        with mock.patch.object(PP, "settled_workers", lambda w, **k: settled), _quiet():
            out = PP.run_rows(rows, self.top, 5, workers=workers, base=base, spawn=spawn,
                              copier=copier, cap=cap, say=lines.append)
        return out, spawned, [l for l in lines if l.startswith("workers:")]

    def test_the_disk_floor_is_read_before_any_baseline(self):
        calls = []
        perturb = type("P", (), {"run_suite": lambda self, s: calls.append(s) or (0, ""),
                                 "wall_for": lambda self, s: 60, "sibling_suite": lambda self, r: None})()
        with mock.patch.dict(os.environ, {"BROTHER_CUT_TEST": "1", "BROTHER_CUT_EVIDENCE_DIR": self.ev}), \
                mock.patch.object(RN, "ROOT", self.root), \
                mock.patch.object(RN, "subject_files", return_value=["scripts/a.py"]), \
                mock.patch("refresh_cut.dirty_paths", return_value=([], "")), \
                mock.patch.object(PP, "free_gib", lambda p: 5.0):
            rows, problem = RN.pooled_rows([("c", SUITE)], perturb, lambda *a: [], workers=1, version=VER)
        self.assertEqual((rows, calls), ([], []))
        self.assertTrue(problem.startswith("NO-DATA") and "5.0 GiB" in problem and "floor is 15" in problem, problem)

    def floor_run(self):
        """pooled_rows to its floor read, on a harness that runs no suite: (rows, problem, baseline calls)."""
        calls = []
        perturb = type("P", (), {"run_suite": lambda self, s: calls.append(s) or (0, ""),
                                 "wall_for": lambda self, s: 60, "sibling_suite": lambda self, r: None})()
        with mock.patch.dict(os.environ, {"BROTHER_CUT_TEST": "1", "BROTHER_CUT_EVIDENCE_DIR": self.ev}), \
                mock.patch.object(RN, "ROOT", self.root), \
                mock.patch.object(RN, "subject_files", return_value=["scripts/a.py"]), \
                mock.patch("refresh_cut.dirty_paths", return_value=([], "")):
            rows, problem = RN.pooled_rows([("c", SUITE)], perturb, lambda *a: [], workers=1, version=VER)
        return rows, problem, calls

    def test_the_floor_is_read_where_the_copies_live_not_in_the_system_temp(self):
        import export_public
        asked = []
        with mock.patch.object(PP, "free_gib", lambda p: asked.append(p) or 5.0):
            rows, problem, calls = self.floor_run()
        self.assertEqual(asked, [export_public.scratch_root()])
        self.assertNotEqual(os.path.realpath(asked[0]), os.path.realpath(tempfile.gettempdir()))
        self.assertTrue(problem.startswith("NO-DATA") and "5.0 GiB" in problem, problem)
        self.assertEqual((rows, calls), ([], []))

    def test_a_scratch_root_that_cannot_be_used_is_no_data_before_any_baseline(self):
        for exc in (PP.CopyFailed("no root"), PermissionError(13, "no", "x")):
            with self.subTest(exc=type(exc).__name__), \
                    mock.patch.object(PP, "pool_scratch_root", side_effect=exc), \
                    mock.patch.object(PP, "free_gib", lambda p: 999.0):
                rows, problem, calls = self.floor_run()
            self.assertEqual((rows, calls), ([], []))
            self.assertTrue(problem.startswith("NO-DATA") and "scratch root cannot be used" in problem, problem)

    def test_an_unreadable_disk_reading_blocks(self):
        def boom(path):
            raise OSError("no such volume")
        self.assertIn("cannot be read", PP.floor_problem("/x", free=boom))
        self.assertEqual(PP.floor_problem("/x", free=lambda p: 15.0), "")
        self.assertNotEqual(PP.floor_problem("/x", free=lambda p: 14.99), "")
        with mock.patch.object(PP, "free_gib", boom):
            self.assertNotEqual(PP.floor_problem("/x"), "")

    def test_a_cap_that_decays_admits_the_waiting_slices(self):
        out, spawned, lines = self.pool_run(self.ROWS8, 4, 2, lambda w: 4)
        self.assertEqual(len(spawned), 4, "the base starts settled_workers once and never again")
        self.assertEqual([len(x) for x in spawned], [2, 2, 2, 2])
        self.assertIn("workers: 4 requested, 2 admitted", lines[0])
        self.assertIn("workers: 4 requested, 3 admitted", lines[1])

    def test_the_cap_never_grows_past_the_request(self):
        out, spawned, lines = self.pool_run(self.ROWS8, 2, 1, lambda w: 99)
        self.assertEqual(len(spawned), 2)
        admitted = [int(l.split(", ")[1].split()[0]) for l in lines]
        self.assertLessEqual(max(admitted), 2)
        out, spawned, _ = self.pool_run(self.ROWS8[:3], 8, 1, lambda w: 99)
        self.assertEqual(len(spawned), 3, "never more workers than rows")

    def test_a_load_that_stays_high_merges_the_waiting_rows_into_one_worker(self):
        out, spawned, lines = self.pool_run(self.ROWS8, 4, 1, lambda w: 1)
        self.assertEqual([len(x) for x in spawned], [2, 6])
        self.assertTrue([l for l in lines if "merged into 1 worker" in l], lines)

    def test_every_row_is_answered_once_in_the_order_asked(self):
        for settled, cap in ((1, lambda w: 1), (2, lambda w: 4), (4, lambda w: 4), (1, lambda w: 2)):
            out, spawned, _ = self.pool_run(self.ROWS8, 4, settled, cap)
            self.assertEqual([(f, s_) for f, s_, _v, _d in out], self.ROWS8)
            asked = [r for part in spawned for r in part]
            self.assertEqual(sorted(asked), sorted(self.ROWS8), "a row was handed out twice or never")
            self.assertEqual([v for _f, _s, v, _d in out], [True] * 8)
        self.assertEqual(PP.run_rows([], self.top, 5), [])

    def test_the_workers_line_names_requested_admitted_and_load(self):
        _, _, lines = self.pool_run(self.ROWS8, 4, 2, lambda w: 4)
        import re
        for line in lines[:2]:
            self.assertRegex(line, r"^workers: 4 requested, \d+ admitted, load (\d+\.\d|unknown)$")

    def test_a_worker_that_dies_leaves_its_rows_no_data_and_the_rest_run(self):
        out, spawned, _ = self.pool_run(self.ROWS8, 4, 1, lambda w: 1, dies=(0,))
        by = {(f, s_): (v, d) for f, s_, v, d in out}
        for key in spawned[0]:
            self.assertIsNone(by[key][0])
            self.assertIn("the worker ended before this row", by[key][1])
        for key in spawned[1]:
            self.assertEqual(by[key][0], True)

if __name__ == "__main__":
    unittest.main()
