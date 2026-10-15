#!/usr/bin/env python3
"""CX2.b: brother_run.py --unfinished is a read only listing, and the status
skill names it instead of --continue.

The command line is driven as a subprocess with --runs-root, so nothing
touches the real runs root. Run folders are written with the engine's own
writer (_write_run_target) and a Work document of the shape _is_work_document
accepts. Every case runs under an empty HOME."""
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brother_run  # noqa: E402
import codex_surface  # noqa: E402

BROTHER_RUN = os.path.join(HERE, "brother_run.py")
ROWS_HALF = [{"id": "A1", "status": "DONE"}, {"id": "A2", "status": "PENDING"}]
ROWS_ALL_DONE = [{"id": "A1", "status": "DONE"}, {"id": "A2", "status": "DONE"}]


class Fixture:
    """A temp world: an empty HOME, a runs root, a git repo to point --cwd at."""

    def __init__(self, test):
        self.dir = tempfile.mkdtemp(prefix="cx2_unfinished_")
        test.addCleanup(self._remove)
        self.home = os.path.join(self.dir, "home")
        self.runs_root = os.path.join(self.dir, "runs")
        self.repo = self.make_repo("repo")
        os.makedirs(self.home)
        os.makedirs(self.runs_root)
        self.runs_dir = os.path.join(self.runs_root, "docs", "plan", "runs")
        self.sentinel = os.path.join(self.dir, "worker_ran.txt")
        stub = os.path.join(self.dir, "stub_worker.py")
        with open(stub, "w") as fh:
            fh.write("import sys\nopen(%r, 'a').write('ran\\n')\nsys.exit(1)\n" % self.sentinel)
        self.env = {"PATH": os.environ.get("PATH", ""), "HOME": self.home,
                    "MODEL_WORKER_CMD": "%s %s" % (shlex.quote(sys.executable), shlex.quote(stub)),
                    "DOOR_MODEL_CMD": "%s %s" % (shlex.quote(sys.executable), shlex.quote(stub))}

    def _remove(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def make_repo(self, name):
        path = os.path.join(self.dir, name)
        os.makedirs(path)
        env = {"PATH": os.environ.get("PATH", ""), "HOME": self.dir}
        for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "t@example.test"],
                    ["git", "config", "user.name", "t"]):
            subprocess.run(cmd, cwd=path, env=env, check=True)
        with open(os.path.join(path, "a.txt"), "w") as fh:
            fh.write("a\n")
        subprocess.run(["git", "add", "a.txt"], cwd=path, env=env, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, env=env, check=True)
        return path

    def run(self, name, outcome, rows, cwd=None, receipt=False, work_doc=None):
        """One run folder written the way the engine writes it."""
        run_dir = os.path.join(self.runs_dir, name)
        os.makedirs(run_dir)
        brother_run._write_run_target(run_dir, cwd or self.repo)
        doc = work_doc if work_doc is not None else {"outcome": outcome, "rows": rows}
        with open(os.path.join(run_dir, "work.json"), "w") as fh:
            json.dump(doc, fh)
        if receipt:
            os.makedirs(os.path.join(run_dir, brother_run.RECEIPT_DIRNAME))
            with open(os.path.join(run_dir, brother_run.RECEIPT_DIRNAME, brother_run.RECEIPT_FILENAME), "w") as fh:
                fh.write("{}")
        return run_dir

    def cli(self, *args, cwd=None):
        argv = [sys.executable, "-B", BROTHER_RUN] + list(args)
        if "--cwd" not in args:
            argv += ["--cwd", cwd or self.repo]
        if "--runs-root" not in args:
            argv += ["--runs-root", self.runs_root]
        return subprocess.run(argv, capture_output=True, text=True, env=self.env, timeout=120)

    def snapshot(self):
        """{path: sha1} of every file under the world, minus the stub's own sentinel."""
        out = {}
        for base, dirs, files in os.walk(self.dir):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
            for name in files:
                path = os.path.join(base, name)
                if path == self.sentinel:
                    continue
                with open(path, "rb") as fh:
                    out[path] = hashlib.sha1(fh.read()).hexdigest()
            for d in dirs:
                out[os.path.join(base, d) + "/"] = ""
        return out


class Unfinished(unittest.TestCase):
    def test_unfinished_lists_the_outcome_rows_and_receipt_path_exit_0(self):
        fx = Fixture(self)
        run_dir = fx.run("2026-10-01T000000-a", "ship the widget", ROWS_HALF, receipt=True)
        first = fx.cli("--unfinished")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        receipt = os.path.join(run_dir, "receipt", "receipt.json")
        self.assertEqual(first.stdout.splitlines(), [
            "brother_run: 1 unfinished run(s) for %s" % os.path.abspath(fx.repo),
            "  1. ship the widget (1 of 2 units done; receipt: %s)" % receipt])
        self.assertEqual(fx.cli("--unfinished").stdout, first.stdout, "a second call printed something else")

    def test_unfinished_many_runs_numbered_oldest_first(self):
        fx = Fixture(self)
        fx.run("2026-10-03T000000-c", "third", ROWS_HALF)
        fx.run("2026-10-01T000000-a", "first", ROWS_HALF)
        fx.run("2026-10-02T000000-b", "second", ROWS_HALF)
        proc = fx.cli("--unfinished")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        lines = proc.stdout.splitlines()
        self.assertIn("3 unfinished run(s)", lines[0])
        self.assertEqual([l.split(" (")[0] for l in lines[1:]], ["  1. first", "  2. second", "  3. third"])
        # the same order and numbering --continue N uses
        found = brother_run.find_unfinished_runs(fx.runs_root, fx.repo)
        self.assertEqual([o for _d, o, _r in found], ["first", "second", "third"])

    def test_unfinished_with_nothing_is_no_data_exit_2_and_names_the_cwd(self):
        fx = Fixture(self)
        # no runs folder at all
        proc = fx.cli("--unfinished")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("NO-DATA", proc.stdout)
        self.assertIn("no unfinished run found", proc.stdout)
        self.assertIn(os.path.abspath(fx.repo), proc.stdout)
        self.assertNotIn("Traceback", proc.stdout + proc.stderr)
        # an empty runs folder
        os.makedirs(fx.runs_dir)
        proc = fx.cli("--unfinished")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        # a directory that does not exist reads NO-DATA too, never a crash
        missing = os.path.join(fx.dir, "no_such_repo")
        proc = fx.cli("--unfinished", "--cwd", missing)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn(missing, proc.stdout)

    def test_unfinished_starts_no_worker_and_changes_no_file(self):
        fx = Fixture(self)
        fx.run("2026-10-01T000000-a", "ship the widget", ROWS_HALF, receipt=True)
        before = fx.snapshot()
        proc = fx.cli("--unfinished")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("resuming", proc.stdout + proc.stderr)
        self.assertFalse(os.path.exists(fx.sentinel), "a worker was started")
        self.assertEqual(fx.snapshot(), before, "--unfinished changed a file")

    def test_unfinished_refuses_an_outcome_resume_continue_plan_or_contract(self):
        extras = {
            "outcome": ["do something"],
            "resume": ["--resume", "/nonexistent/run"],
            "continue bare": ["--continue"],
            "continue N": ["--continue", "1"],
            "plan": ["--plan", "/nonexistent/plan.json"],
            "contract": ["--contract", "/nonexistent/contract.json"],
        }
        for label, extra in extras.items():
            with self.subTest(label):
                fx = Fixture(self)
                fx.run("2026-10-01T000000-a", "ship the widget", ROWS_HALF)
                before = fx.snapshot()
                proc = fx.cli("--unfinished", *extra)
                self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                self.assertNotIn("unfinished run(s)", proc.stdout)
                self.assertIn("--unfinished replaces", proc.stderr)
                self.assertFalse(os.path.exists(fx.sentinel))
                self.assertEqual(fx.snapshot(), before)

    def test_unfinished_ignores_other_repos_and_finished_runs(self):
        fx = Fixture(self)
        other = fx.make_repo("other_repo")
        fx.run("2026-10-01T000000-a", "mine unfinished", ROWS_HALF)
        fx.run("2026-10-02T000000-b", "mine finished", ROWS_ALL_DONE)
        fx.run("2026-10-03T000000-c", "theirs unfinished", ROWS_HALF, cwd=other)
        mine = fx.cli("--unfinished")
        self.assertEqual(mine.returncode, 0, mine.stdout + mine.stderr)
        self.assertIn("mine unfinished", mine.stdout)
        self.assertNotIn("mine finished", mine.stdout)
        self.assertNotIn("theirs unfinished", mine.stdout)
        theirs = fx.cli("--unfinished", cwd=other)
        self.assertIn("theirs unfinished", theirs.stdout)
        self.assertNotIn("mine unfinished", theirs.stdout)
        # only finished runs left: NO-DATA, not an empty pass
        fx2 = Fixture(self)
        fx2.run("2026-10-01T000000-a", "done already", ROWS_ALL_DONE)
        self.assertEqual(fx2.cli("--unfinished").returncode, 2)

    def test_unfinished_survives_a_corrupt_target_or_work_document(self):
        fx = Fixture(self)
        fx.run("2026-10-01T000000-a", "good run", ROWS_HALF)
        truncated = fx.run("2026-10-02T000000-b", "x", ROWS_HALF)
        with open(os.path.join(truncated, "target.json"), "w") as fh:
            fh.write('{"cwd": "' + fx.repo[:5])
        fx.run("2026-10-03T000000-c", "x", None, work_doc=["not", "a", "work", "document"])
        fx.run("2026-10-04T000000-d", "x", None, work_doc={"outcome": "rows are strings", "rows": ["A1", "A2"]})
        fx.run("2026-10-05T000000-e", "x", None, work_doc={"outcome": "rows is a number", "rows": 7})
        with open(os.path.join(fx.runs_dir, "2026-10-06T000000-f"), "w") as fh:
            fh.write("a file where a run folder belongs")
        proc = fx.cli("--unfinished")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(proc.stdout.splitlines()[0].split(" for ")[0], "brother_run: 1 unfinished run(s)")
        self.assertIn("good run", proc.stdout)

    def test_a_receipt_present_is_named_and_absent_reads_no_data(self):
        fx = Fixture(self)
        with_receipt = fx.run("2026-10-01T000000-a", "has receipt", ROWS_HALF, receipt=True)
        fx.run("2026-10-02T000000-b", "no receipt", ROWS_HALF)
        # a receipt DIRECTORY with no file inside is not a receipt
        os.makedirs(os.path.join(fx.runs_dir, "2026-10-03T000000-c", "receipt"))
        fx.run("2026-10-04T000000-d", "empty receipt dir", ROWS_HALF)
        os.makedirs(os.path.join(fx.runs_dir, "2026-10-04T000000-d", "receipt"))
        rows = brother_run.unfinished_summary(fx.runs_root, fx.repo)
        by_outcome = {r["outcome"]: r for r in rows}
        self.assertEqual(by_outcome["has receipt"]["receipt"],
                         os.path.join(with_receipt, "receipt", "receipt.json"))
        self.assertIsNone(by_outcome["no receipt"]["receipt"])
        self.assertIsNone(by_outcome["empty receipt dir"]["receipt"])
        self.assertEqual((by_outcome["has receipt"]["done"], by_outcome["has receipt"]["total"]), (1, 2))
        out = fx.cli("--unfinished").stdout
        self.assertIn("has receipt (1 of 2 units done; receipt: %s)" % by_outcome["has receipt"]["receipt"], out)
        self.assertIn("no receipt (1 of 2 units done; receipt: NO-DATA, none written yet)", out)

    def test_render_unfinished_codes(self):
        self.assertEqual(brother_run.render_unfinished([], "/x")[0], 2)
        code, lines = brother_run.render_unfinished(
            [{"n": 1, "outcome": "o", "done": 0, "total": 3, "receipt": None}], "/x")
        self.assertEqual(code, 0)
        self.assertIn("0 of 3 units done", lines[1])

    def test_the_status_body_names_unfinished_and_not_continue(self):
        body = [b for v, _d, b in codex_surface.VERBS if v == "status"][0]
        self.assertIn("brother_run.py --unfinished --cwd <repo>", body)
        self.assertNotIn("--continue", body)
        self.assertIn("NO-DATA", body)


if __name__ == "__main__":
    unittest.main()
