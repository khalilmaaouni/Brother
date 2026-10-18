#!/usr/bin/env python3
"""G11.a: scripts/required_fast.sh runs the deploy canary and, only when EVERY row passed on a clean tree, appends
one whole-gate PASS record through scripts/gate_ledger.py keyed by HEAD^{tree} and the interpreter version.

HOW. The real gate's own header and footer (sliced from scripts/required_fast.sh by its literal anchors, the way
scripts/test_required_fast.py does) wrap stub rows in a throwaway git repository that also carries the real
gate_ledger.py, evidence_obligation.py and git_location_guard.py. The gate runs as `sh scripts/required_fast.sh`,
so the record is judged at the entry point the real gate calls. Each case isolates one guard: a FAIL row, a
NO-DATA row the obligation map allows (so the gate itself exits 0), a dirty start, an untracked file, HEAD moving
during the run, a tracked edit or an untracked file made during the run (security review 2026-10-16, finding 3),
a git status that cannot be read at record time, and an obligation step that exits non zero while every row passed. Everything lives under one
temporary directory; HOME and TMPDIR point inside it; no network.

Run: python3 -B scripts/test_whole_gate_record.py
"""
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
import gate_ledger  # noqa: E402

SCRIPT = os.path.join(HERE, "required_fast.sh")
with open(SCRIPT, encoding="utf-8") as _f:
    SOURCE = _f.read()
CHECKS_START = 'run_check "version-truth"'
FOOTER_START = '\ndone\ndrain_checks\n'
HEADER = SOURCE[:SOURCE.index(CHECKS_START)]
FOOTER = SOURCE[SOURCE.index(FOOTER_START):]
COPIED = ("gate_ledger.py", "evidence_obligation.py", "git_location_guard.py")


def git(repo, *args):
    return subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True, check=True).stdout


def clean_env(scratch):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("GIT_", "BROTHER_", "REQUIRED_FAST_", "CUT_"))
           and k not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "GITHUB_ACTIONS", "GATE_LEDGER_PATH")}
    home = tempfile.mkdtemp(dir=scratch)
    env.update(HOME=home, BROTHER_HEAVY_SLOT="off", TMPDIR=tempfile.mkdtemp(dir=scratch),
               GIT_CONFIG_GLOBAL=os.path.join(home, "gitconfig"), GIT_CONFIG_NOSYSTEM="1")
    return env


class WholeGateRecord(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix="g11a-")
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.env = clean_env(self.scratch)

    def build(self, rows, optional=(), obligations_text=None):
        repo = tempfile.mkdtemp(dir=self.scratch)
        scripts = os.path.join(repo, "scripts")
        os.makedirs(scripts)
        for name in COPIED:
            shutil.copyfile(os.path.join(HERE, name), os.path.join(scripts, name))
        if obligations_text is None:
            obligations_text = json.dumps({
                "schema": "brother.gate-obligations/v1", "default": "REQUIRED_FOR_MERGE",
                "checks": {n: {"obligation": "OPTIONAL", "reason": "test"} for n in optional}})
        with open(os.path.join(scripts, "gate_obligations.json"), "w") as f:
            f.write(obligations_text)
        with open(os.path.join(scripts, "required_fast.sh"), "w") as f:
            f.write(HEADER + "\n".join(rows) + "\n" + FOOTER)
        with open(os.path.join(repo, ".gitignore"), "w") as f:
            f.write("logs/\n__pycache__/\n")
        subprocess.run(["git", "init", "-q", repo], check=True, env=self.env)
        for key, value in (("user.name", "fixture"), ("user.email", "fixture@example.invalid")):
            subprocess.run(["git", "-C", repo, "config", key, value], check=True, env=self.env)
        subprocess.run(["git", "-C", repo, "add", "-A"], check=True, env=self.env)
        subprocess.run(["git", "-C", repo, "commit", "-qm", "fixture"], check=True, env=self.env)
        return repo

    def run_gate(self, repo):
        proc = subprocess.run(["sh", os.path.join(repo, "scripts", "required_fast.sh")], capture_output=True,
                              text=True, timeout=120, env=self.env)
        return proc.returncode, proc.stdout + proc.stderr

    def shared(self, repo):
        """The one whole-gate file every worktree of repo shares, located from git itself, not from the code under test."""
        common = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
        return os.path.join(os.path.realpath(common), "brother-whole-gate.jsonl")

    def records(self, repo):
        """Whole-gate records in the shared file AND in the checkout's own ledger: "writes nothing" means nowhere."""
        found = []
        for path in (self.shared(repo), os.path.join(repo, "logs", "gate-ledger.jsonl")):
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    found += [r for r in (json.loads(l) for l in f if l.strip())
                              if r.get("gate_name") == gate_ledger.WHOLE_GATE]
        return found

    def test_every_row_passes_on_a_clean_tree_writes_one_record_keyed_by_tree_and_interpreter(self):
        repo = self.build(['run_check "a" true', 'run_check "b" true', 'run_check "c" true'])
        # The real environment outside a cut: CUT_ID empty. Until GL1 the per-row ledger append refused it on every
        # row ("cut_id must be 32 hex"); both writers now read it through gate_ledger.cut_id_or_none, so no row is
        # refused. The whole-gate record must be written, and G11.b's reader must accept it.
        self.env["CUT_ID"] = ""
        code, out = self.run_gate(repo)
        self.assertNotIn("gate ledger append refused", out)  # GL1: a row outside a cut appends
        self.assertEqual(code, 0, out)
        self.assertIn("whole-gate: PASS record for tree", out)
        recs = self.records(repo)
        self.assertEqual(len(recs), 1, out)
        tree = git(repo, "rev-parse", "HEAD^{tree}").strip()
        version = subprocess.run(["python3", "--version"], capture_output=True, text=True,
                                 env=self.env).stdout.strip()
        self.assertEqual(recs[0]["tree_sha"], tree)
        self.assertEqual(recs[0]["status"], "PASS")
        self.assertEqual(recs[0]["tool_versions"], {"python3": version})
        self.assertEqual(recs[0]["cut_id"], gate_ledger.NO_CUT)
        self.assertTrue(gate_ledger.verify_chain(self.shared(repo)))
        ok, why = gate_ledger.whole_gate_pass_for_tree(self.shared(repo), tree)
        self.assertTrue(ok, why)
        # The same actor again on the same tree: a second record, and the chain still holds.
        code, out = self.run_gate(repo)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.records(repo)), 2, out)
        self.assertTrue(gate_ledger.verify_chain(self.shared(repo)))

    # --- G11 x M40 integration defect, 2026-10-16: the record is a fact about a TREE, so it lives in the repository's
    # common git directory, where every worktree of the repository reads it, never in the ledger of one checkout.
    def test_a_gate_run_in_a_linked_worktree_writes_the_record_every_worktree_reads(self):
        repo = self.build(['run_check "a" true'])
        linked = os.path.join(self.scratch, "linked-wt")
        subprocess.run(["git", "-C", repo, "worktree", "add", "-q", "--detach", linked, "HEAD"], check=True, env=self.env)
        code, out = self.run_gate(linked)
        self.assertEqual(code, 0, out)
        self.assertIn("whole-gate: PASS record for tree", out)
        self.assertEqual(self.shared(linked), self.shared(repo))
        self.assertIn(self.shared(repo), out)
        self.assertEqual(len(self.records(repo)), 1, out)
        self.assertEqual(len(self.records(linked)), 1, out)            # none in the linked checkout's own ledger
        tree = git(repo, "rev-parse", "HEAD^{tree}").strip()
        with mock.patch.dict(os.environ, self.env, clear=True):
            ok, why = gate_ledger.whole_gate_pass_in_repo(repo, tree)
        self.assertTrue(ok, why)
        self.assertIn("from " + self.shared(repo), why)

    def test_the_writer_writes_nothing_when_git_cannot_name_the_common_dir(self):
        repo = self.build(['run_check "a" true'])
        tree = git(repo, "rev-parse", "HEAD^{tree}").strip()
        codes = os.path.join(self.scratch, "codes.txt")
        with open(codes, "w") as f:
            f.write("a\t0\n")
        real, here = gate_ledger._git_out, os.getcwd()

        def no_common(path, *args):
            return None if "--git-common-dir" in args else real(path, *args)
        out = io.StringIO()
        try:
            os.chdir(repo)
            with mock.patch.dict(os.environ, self.env, clear=True), \
                    mock.patch.object(gate_ledger, "_git_out", no_common), contextlib.redirect_stdout(out):
                code = gate_ledger._main(["--whole-gate-record", "--codes", codes, "--tree", tree, "--obligation", "0",
                                          "--started", "1"])
        finally:
            os.chdir(here)
        self.assertEqual(code, 2, out.getvalue())
        self.assertIn("no PASS record: git could not name the common git directory", out.getvalue())
        self.assertEqual(self.records(repo), [])

    def test_a_fail_row_writes_no_record(self):
        repo = self.build(['run_check "a" true', 'run_check "b" false'])
        code, out = self.run_gate(repo)
        self.assertEqual(code, 1, out)
        self.assertEqual(self.records(repo), [], out)
        self.assertIn("no PASS record: row b exited 1", out)

    def test_a_no_data_row_writes_no_record_even_when_the_gate_allows_it(self):
        repo = self.build(['run_check "a" true', 'run_check "nd" sh -c "exit 2"'], optional=("nd",))
        code, out = self.run_gate(repo)
        self.assertEqual(code, 0, out)     # the gate lets an OPTIONAL NO-DATA through; the record does not
        self.assertEqual(self.records(repo), [], out)
        self.assertIn("no PASS record: row nd exited 2", out)

    def test_a_dirty_tracked_file_at_start_writes_no_record(self):
        repo = self.build(['run_check "a" true'])
        with open(os.path.join(repo, ".gitignore"), "a") as f:
            f.write("# edited, not committed\n")
        code, out = self.run_gate(repo)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.records(repo), [], out)
        self.assertIn("uncommitted changes", out)

    def test_an_untracked_file_at_start_writes_no_record(self):
        repo = self.build(['run_check "a" true'])
        with open(os.path.join(repo, "scripts", "forgot_to_add.py"), "w") as f:
            f.write("x = 1\n")
        code, out = self.run_gate(repo)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.records(repo), [], out)
        self.assertIn("forgot_to_add.py", out)

    def test_head_moving_during_the_run_writes_no_record(self):
        repo = self.build(['run_check "a" true',
                           'run_check "mover" sh -c "echo y > moved.txt && git add moved.txt && git commit -qm moved"'])
        code, out = self.run_gate(repo)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.records(repo), [], out)
        self.assertIn("moved during the run", out)

    # --- security review 2026-10-16, finding 3: the checkout must still be clean when the record is written. Each row
    # dirties the tree DURING the run, after the clean start snapshot, and leaves HEAD^{tree} unchanged, so only the
    # record time status check can refuse it.
    def test_a_tracked_file_edited_during_the_run_writes_no_record(self):
        repo = self.build(['run_check "a" true', 'run_check "editor" sh -c "echo edited >> .gitignore"'])
        tree = git(repo, "rev-parse", "HEAD^{tree}").strip()
        code, out = self.run_gate(repo)
        self.assertEqual(code, 0, out)
        self.assertEqual(git(repo, "rev-parse", "HEAD^{tree}").strip(), tree, "HEAD^{tree} did not move")
        self.assertEqual(self.records(repo), [], out)
        self.assertIn("no PASS record: at record time the checkout has uncommitted changes", out)

    def test_an_untracked_file_made_during_the_run_writes_no_record(self):
        repo = self.build(['run_check "a" true', 'run_check "planter" sh -c "echo x = 1 > scripts/planted.py"'])
        code, out = self.run_gate(repo)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.records(repo), [], out)
        self.assertIn("at record time", out)
        self.assertIn("planted.py", out)

    def test_the_writer_refuses_when_status_cannot_be_read_at_record_time(self):
        repo = self.build(['run_check "a" true'])
        tree = git(repo, "rev-parse", "HEAD^{tree}").strip()
        real = gate_ledger._git_out
        seen = []

        def no_status(path, *args):
            seen.append(args[0])
            return None if args[0] == "status" else real(path, *args)
        gate_ledger._git_out = no_status
        try:
            with mock.patch.dict(os.environ, self.env, clear=True):   # git reads HOME: the scratch one
                ok, why = gate_ledger.record_whole_gate(os.path.join(repo, "logs", "gate-ledger.jsonl"), repo, "a\t0\n",
                                                        tree, "0", 1.0)
        finally:
            gate_ledger._git_out = real
        self.assertIn("status", seen)
        self.assertFalse(ok, why)
        self.assertIn("git status could not be read", why)
        self.assertEqual(self.records(repo), [])
        with mock.patch.dict(os.environ, self.env, clear=True):
            ok, why = gate_ledger.record_whole_gate(os.path.join(repo, "logs", "gate-ledger.jsonl"), repo, "a\t0\n",
                                                    tree, "0", 1.0)
        self.assertTrue(ok, why)   # the same clean tree with a readable status: recorded
        self.assertEqual(len(self.records(repo)), 1)

    def test_an_obligation_step_that_fails_writes_no_record_even_when_every_row_passed(self):
        repo = self.build(['run_check "a" true'], obligations_text="{ not json")
        code, out = self.run_gate(repo)
        self.assertNotEqual(code, 0, out)
        self.assertEqual(self.records(repo), [], out)
        self.assertIn("the evidence obligation step exited", out)

    def test_the_writer_refuses_rows_it_cannot_read(self):
        repo = self.build(['run_check "a" true'])
        tree = git(repo, "rev-parse", "HEAD^{tree}").strip()
        for text, why in (("", "no rows"), ("a 0\n", "not \"name TAB exit code\""), ("a\tok\n", "not \"name")):
            codes = os.path.join(self.scratch, "codes.txt")
            with open(codes, "w") as f:
                f.write(text)
            proc = subprocess.run([sys.executable, "-B", "scripts/gate_ledger.py", "--whole-gate-record", "--codes",
                                   codes, "--tree", tree, "--obligation", "0", "--started", "1"],
                                  cwd=repo, capture_output=True, text=True, env=self.env)
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertIn(why, proc.stdout)
        self.assertEqual(self.records(repo), [])


class RealGateShape(unittest.TestCase):
    """The real script carries the canary row inside the check list, reads the tree before the first check, and
    hands the writer its own rows file and obligation code after the obligation step and before the file is gone."""

    def test_the_deploy_canary_row_is_in_the_check_list(self):
        body = SOURCE[SOURCE.index(CHECKS_START):SOURCE.index(FOOTER_START)]
        rows = [l for l in body.splitlines() if l.startswith('run_check "deploy-canary"')]
        self.assertEqual(len(rows), 1, rows)
        self.assertIn("scripts/test_deploy_canary.py", rows[0])

    def test_the_snapshot_runs_before_any_check_and_the_writer_after_the_obligation_step(self):
        self.assertIn("--whole-gate-snapshot", HEADER)
        tail = FOOTER[FOOTER.index("obligation=$?"):FOOTER.index('rm -f "$codes_file"')]
        self.assertIn("--whole-gate-record", tail)
        for arg in ('--codes "$codes_file"', '--tree "$whole_gate_tree"', '--obligation "$obligation"'):
            self.assertIn(arg, tail)


if __name__ == "__main__":
    unittest.main()
