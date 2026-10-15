#!/usr/bin/env python3
"""FR0.a: scripts/cut_ready.py, the collect all table over a cut's premises.

Every fixture is a temp folder holding a fake repository root and a fake
evidence folder; every seam (runner, free, run_all, gate, directory) is
injected, so no test runs the real gate, git worktree, the network or
~/.claude.

Python 3.9, standard library only. No em or en dashes.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cut_ready as CR  # noqa: E402

try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n"
                     % os.path.basename(__file__))


class FakeResult:
    def __init__(self, returncode, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


class FakeRunner:
    def __init__(self, table=None, script=None):
        self.table = table if table is not None else {}
        self.script = script
        self.calls = []

    def __call__(self, argv, cwd, timeout=None, env=None):
        argv = list(argv)
        self.calls.append((argv, cwd, timeout, env))
        if self.script is not None:
            return self.script(argv, cwd)
        return self.table.get(tuple(argv), FakeResult(0, ""))


class Base(unittest.TestCase):
    HEAD = "a" * 40
    TREE = "b" * 40

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cut-ready-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "root")
        self.ev = os.path.join(self.tmp, "ev")
        os.makedirs(os.path.join(self.root, "scripts"))
        os.makedirs(self.ev)
        for name in ("system_doc.py", "bundle_runtime.py"):
            with open(os.path.join(self.root, "scripts", name), "w") as handle:
                handle.write("# fixture\n")
        self.runner = FakeRunner({
            ("git", "rev-parse", "HEAD"): FakeResult(0, self.HEAD + "\n"),
            ("git", "rev-parse", "HEAD^{tree}"): FakeResult(0, self.TREE + "\n"),
            ("git", "status", "--porcelain"): FakeResult(0, ""),
            ("python3", "scripts/system_doc.py", "--check"): FakeResult(0, "system ok\n"),
            ("python3", "scripts/bundle_runtime.py", "--check"): FakeResult(0, "bundle ok\n"),
        })

    def passing_run_all(self, root, version, horizon_hours=None, virgin=False):
        return [("OK", "preflight-a", "a ok"), ("OK", "preflight-b", "b ok")]

    def passing_gate(self, root, sha, runner=None, directory=""):
        return CR.parse_hub_rows(
            "PASS    exit 0   gate-sample            1s  ok\n"
            "pass 1   fail 0   no-data 0\n", 0)

    def collect(self, **kwargs):
        kwargs.setdefault("runner", self.runner)
        kwargs.setdefault("free", lambda path: 100.0)
        kwargs.setdefault("run_all", self.passing_run_all)
        kwargs.setdefault("gate", self.passing_gate)
        kwargs.setdefault("directory", self.ev)
        return CR.collect(self.root, "1.2.3", **kwargs)


class CollectAll(Base):
    def test_two_failing_rows_are_both_named_in_the_table(self):  # REQ-FR0-01
        def run_all(root, version, horizon_hours=None, virgin=False):
            return [("REFUSED", "preflight-a", "a refused"),
                    ("REFUSED", "preflight-b", "b refused")]

        hub_text = ("FAIL    exit 1   hub-row-z            1s  boom\n"
                    "pass 0   fail 1   no-data 0\n")

        def gate(root, sha, runner=None, directory=""):
            return CR.parse_hub_rows(hub_text, 1)

        rows, head, tree = self.collect(run_all=run_all, gate=gate)
        self.assertEqual(CR.EXIT_FOR[CR.overall(rows)], 1)
        table = CR.format_table(rows)
        record_path = CR.write_record(self.ev, "1.2.3", head, tree, rows, 1234, CR.HORIZON_DEFAULT)
        with open(record_path, encoding="utf-8") as handle:
            record = json.load(handle)
        names_in_record = [r["name"] for r in record["rows"]]
        for name in ("preflight-a", "preflight-b", "hub:hub-row-z"):
            self.assertIn(name, table)
            self.assertIn(name, names_in_record)

    def test_a_hub_only_row_refuses_cut_ready(self):  # REQ-FR0-02
        hub_text = ("PASS    exit 0   a-row                1s  ok\n"
                    "FAIL    exit 1   loop-owns-complete    1s  red\n"
                    "pass 1   fail 1   no-data 0\n")

        def gate(root, sha, runner=None, directory=""):
            return CR.parse_hub_rows(hub_text, 1)

        rows, head, tree = self.collect(gate=gate)
        self.assertEqual(CR.overall(rows), CR.FAIL)
        self.assertTrue(any(r["name"] == "hub:loop-owns-complete" and r["verdict"] == CR.FAIL
                            for r in rows))


class Premises(Base):
    def test_seven_gib_free_under_a_fifteen_floor_refuses(self):  # REQ-FR0-03
        low = CR.disk_row("/tmp", floor_gib=15, free=lambda path: 7.0)
        self.assertEqual(low["verdict"], CR.FAIL)
        self.assertIn("7.00", low["detail"])
        self.assertIn("15.00", low["detail"])
        ok = CR.disk_row("/tmp", floor_gib=15, free=lambda path: 15.0)
        self.assertEqual(ok["verdict"], CR.PASS)

    def test_a_row_that_cannot_run_never_passes(self):  # REQ-FR0-04
        row = CR.tree_row(self.root, runner=None)
        self.assertEqual(row["verdict"], CR.NODATA)
        self.assertEqual(CR.overall([row]), CR.NODATA)
        self.assertEqual(CR.EXIT_FOR[CR.overall([row])], 2)
        self.assertEqual(CR.EXIT_FOR[CR.NODATA], 2)
        self.assertEqual(CR.EXIT_FOR[CR.PASS], 0)
        self.assertEqual(CR.EXIT_FOR[CR.FAIL], 1)

    def test_a_dirty_tree_and_a_moved_head_are_fail_rows(self):  # REQ-FR0-06
        dirty = CR.tree_row(self.root, runner=FakeRunner({
            ("git", "status", "--porcelain"): FakeResult(0, " M scripts/a.py\n M scripts/b.py\n"),
        }))
        self.assertEqual(dirty["verdict"], CR.FAIL)
        self.assertIn("2", dirty["detail"])

        calls = {"tree": 0}

        def script(argv, cwd):
            argv = list(argv)
            if argv == ["git", "rev-parse", "HEAD"]:
                return FakeResult(0, self.HEAD + "\n")
            if argv == ["git", "rev-parse", "HEAD^{tree}"]:
                calls["tree"] += 1
                return FakeResult(0, (self.TREE if calls["tree"] == 1 else "c" * 40) + "\n")
            if argv == ["git", "status", "--porcelain"]:
                return FakeResult(0, "")
            return FakeResult(0, "")

        rows, head, tree = self.collect(runner=FakeRunner(script=script))
        self.assertTrue(any(r["name"] == "tree-stable" and r["verdict"] == CR.FAIL for r in rows))
        self.assertEqual(CR.overall(rows), CR.FAIL)

    def test_counts_line_matches_the_gate_spacing(self):
        rows = [CR.make_row("g", "n", CR.PASS, 1.0, "d"),
                CR.make_row("g", "m", CR.FAIL, 1.0, "d"),
                CR.make_row("g", "k", CR.NODATA, 1.0, "d")]
        self.assertEqual(CR.counts_line(rows), "pass 1   fail 1   no-data 1")


class Record(Base):
    def test_one_record_at_the_end_and_none_when_interrupted(self):  # REQ-FR0-05
        rows = [CR.make_row("premise", "tree-clean", CR.PASS, 1.0, "clean")]
        path = CR.write_record(self.ev, "1.2.3", "a" * 40, "b" * 40, rows, 1234, CR.HORIZON_DEFAULT)
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(os.path.basename(path), "cut-ready-1.2.3-bbbbbbbbbbbb-1234.json")
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
        for key in ("schema", "version", "candidate_sha", "candidate_tree", "verdict", "epoch",
                    "finished_at", "summary", "rows", "horizon_hours", "horizon_source"):
            self.assertIn(key, record)
        self.assertEqual(record["schema"], CR.READY_SCHEMA)
        self.assertEqual(record["verdict"], CR.PASS)
        before = sorted(os.listdir(self.ev))
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            rc = CR.main(["--version", "1.2.3", "--directory", self.ev],
                         root=self.root, runner=None)
        self.assertEqual(rc, 2)
        self.assertEqual(sorted(os.listdir(self.ev)), before)


class Hub(Base):
    def test_the_gate_runs_in_a_scratch_worktree_and_pass_lines_stay_pass(self):  # REQ-FR0-07
        text = ("PASS    exit 0   a-row                1s  NO-DATA: gate ledger append refused\n"
                "pass 1   fail 0   no-data 0\n")
        parsed = CR.parse_hub_rows(text, 0)
        self.assertTrue(any(r["name"] == "hub:a-row" and r["verdict"] == CR.PASS for r in parsed))

        seen = []

        def script(argv, cwd):
            argv = list(argv)
            seen.append((argv, cwd))
            if argv[:3] == ["git", "worktree", "add"]:
                return FakeResult(0, "")
            if argv == ["sh", "scripts/required_fast.sh"]:
                return FakeResult(0, text)
            return FakeResult(0, "")

        rows = CR.hub_rows(self.root, self.HEAD, runner=FakeRunner(script=script), directory=self.ev)
        self.assertTrue(any(r["name"] == "hub:a-row" and r["verdict"] == CR.PASS for r in rows))
        gate_calls = [cwd for argv, cwd in seen if argv == ["sh", "scripts/required_fast.sh"]]
        self.assertEqual(len(gate_calls), 1)
        self.assertNotEqual(os.path.realpath(gate_calls[0]), os.path.realpath(self.root))
        self.assertTrue(any(argv[:3] == ["git", "worktree", "add"] for argv, _ in seen))
        self.assertTrue(any(name.startswith("cut-ready-hub-") for name in os.listdir(self.ev)))

    def test_the_gate_child_gets_a_cut_id_so_its_ledger_append_is_not_refused(self):
        """2026-10-15: every hub row carried 'gate ledger append refused: cut_id must be 32 hex' because no CUT_ID reached
        required_fast.sh. The child env must carry a fresh 32 hex id, a different one on each run."""
        ids = []
        for _ in range(2):
            runner = FakeRunner(script=lambda argv, cwd: FakeResult(0, ""))
            CR.hub_rows(self.root, self.HEAD, runner=runner, directory=self.ev)
            envs = [env for argv, cwd, timeout, env in runner.calls if argv == ["sh", "scripts/required_fast.sh"]]
            self.assertEqual(len(envs), 1)
            ids.append(envs[0].get("CUT_ID", ""))
        for value in ids:
            self.assertRegex(value, r"^[0-9a-f]{32}$")
        self.assertNotEqual(ids[0], ids[1])


class Hostile(Base):
    HOSTILE = (None, 0, True, -1, float("nan"), "", "x", b"x", [], ["x"], {}, {"a": 1},
               (), {1, 2}, object())
    CASES = (
        (CR.make_row, 7),
        (CR.overall, 1),
        (CR.counts_line, 1),
        (CR.disk_row, 3),
        (CR.tree_row, 2),
        (CR.doc_rows, 2),
        (CR.preflight_rows, 4),
        (CR.parse_hub_rows, 2),
        (CR.hub_rows, 4),
        (CR.collect, 8),
        (CR.format_table, 1),
        (CR.write_record, 7),
        (CR.main, 3),
    )

    def test_every_public_function_refuses_hostile_input(self):
        for func, arity in self.CASES:
            name = func.__name__
            for value in self.HOSTILE:
                args = [value] * arity
                try:
                    func(*args)
                except (ValueError, LookupError, SystemExit):
                    continue
                except Exception as exc:
                    self.fail("%s with hostile input crashed with %s: %s"
                              % (name, type(exc).__name__, exc))


class EntryPoint(unittest.TestCase):
    """2026-10-14: main() had no runner by default, so every command line run read NO-DATA on the candidate row and
    checked nothing. The entry point must reach a real runner, and that runner must read a real repository."""

    def test_the_command_line_entry_uses_the_real_runner(self):
        seen = {}

        class Stop(Exception):
            pass

        def fake_collect(root, version, runner=None, **kw):
            seen["runner"] = runner
            raise Stop()

        saved = CR.collect
        CR.collect = fake_collect
        try:
            with self.assertRaises(Stop):
                CR.main(["--version", "1.1.4"])
        finally:
            CR.collect = saved
        self.assertIs(seen.get("runner"), CR.real_runner)

    def test_the_real_runner_reads_a_real_repository(self):
        import subprocess
        repo = tempfile.mkdtemp(prefix="cut-ready-entry-")
        try:
            for cmd in (["git", "init", "-q"], ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                                                "--allow-empty", "--no-gpg-sign", "-m", "x"]):
                subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
            head = CR._rev_parse(CR.real_runner, repo, "HEAD")
            self.assertRegex(head or "", r"^[0-9a-f]{40}$")
            self.assertIsNone(CR._rev_parse(CR.real_runner, repo, "no-such-ref"))
        finally:
            shutil.rmtree(repo, ignore_errors=True)


class EntryPointDefaults(unittest.TestCase):
    """2026-10-15: the first real run left two more defaults unwired. main() never supplied run_all (the preflight row
    read NO-DATA) and wrote the record to the system temp folder instead of the cut evidence folder every reader
    (cut_driver, rc_handover, cut_rehearsal.ready_record) looks in. Each test drives main(), not collect()."""

    def run_main_with_fake_collect(self, extra=()):
        seen = {}
        ev = tempfile.mkdtemp(prefix="cut-ready-ev-")
        self.addCleanup(shutil.rmtree, ev, True)
        rows = [CR.make_row("candidate", "candidate", CR.PASS, 0.0, "ok")]

        def fake_collect(root, version, **kw):
            seen.update(kw)
            return rows, "a" * 40, "b" * 40

        saved = CR.collect
        saved_env = dict(os.environ)
        CR.collect = fake_collect
        os.environ["BROTHER_CUT_TEST"] = "1"
        os.environ["BROTHER_CUT_EVIDENCE_DIR"] = ev
        try:
            with redirect_stdout(io.StringIO()):
                rc = CR.main(["--version", "1.1.4"] + list(extra), root=os.getcwd(), runner=lambda *a, **k: None)
        finally:
            CR.collect = saved
            os.environ.clear()
            os.environ.update(saved_env)
        return rc, seen, ev

    def test_main_supplies_the_real_preflight(self):
        rc, seen, _ev = self.run_main_with_fake_collect()
        self.assertEqual(rc, 0)
        self.assertIs(seen.get("run_all"), CR.real_run_all)

    def test_main_writes_the_record_where_the_readers_look(self):
        rc, seen, ev = self.run_main_with_fake_collect()
        self.assertEqual(seen.get("directory"), ev)
        names = [n for n in os.listdir(ev) if n.startswith("cut-ready-1.1.4-") and n.endswith(".json")]
        self.assertEqual(len(names), 1, os.listdir(ev))

    def test_an_explicit_directory_still_wins(self):
        other = tempfile.mkdtemp(prefix="cut-ready-other-")
        self.addCleanup(shutil.rmtree, other, True)
        rc, seen, ev = self.run_main_with_fake_collect(["--directory", other])
        self.assertEqual(seen.get("directory"), other)
        self.assertEqual(os.listdir(ev), [])
        self.assertEqual(len([n for n in os.listdir(other) if n.endswith(".json")]), 1)

    def test_the_real_preflight_reaches_cut_preflight_with_the_cheap_rows_only(self):
        import cut_preflight
        seen = {}

        def fake_run_all(root, version, horizon_hours=None, virgin=True, **kw):
            seen.update(root=root, version=version, horizon_hours=horizon_hours, virgin=virgin)
            return [("OK", "a", "x"), ("OK", "b", "y")]

        saved = cut_preflight.run_all
        cut_preflight.run_all = fake_run_all
        try:
            out = CR.real_run_all("/r", "1.1.4", horizon_hours=7.0, virgin=False)
        finally:
            cut_preflight.run_all = saved
        self.assertEqual(out, [("OK", "a", "x"), ("OK", "b", "y")])
        self.assertEqual(seen, {"root": "/r", "version": "1.1.4", "horizon_hours": 7.0, "virgin": False})


if __name__ == "__main__":
    unittest.main()
