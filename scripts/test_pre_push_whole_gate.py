#!/usr/bin/env python3
"""G11.b: the pre-push gate refuses a loop change whose exact pushed tree has no whole-gate PASS record.

HOW. A throwaway git repository holds a base commit and a pushed commit; git's pre-push ref line for that push is
fed on stdin to pre_push_gate.main(), the function the installed hook runs. Every OTHER check gate() calls is
replaced by one that finds nothing, so these cases judge check_whole_gate alone, reached through main() and gate().
The record is written by gate_ledger.record_whole_gate, the writer required_fast.sh calls. A4 is the last class:
check_hermetic_tests prints one timing line per push. Everything lives under one temporary directory; no network.

Run: python3 -B scripts/test_pre_push_whole_gate.py
"""
import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gate_ledger  # noqa: E402
import pre_push_gate as G  # noqa: E402

OTHER_CHECKS = ("check_handback", "check_collision", "check_correctness", "check_edition", "check_remote_rules",
                "check_drift", "check_docs_current", "check_hermetic_tests")
DEPLOY = 'CANDIDATE_ENTRIES = ("scripts/close_unit.py",)\nCANDIDATE_DATA = ("scripts/loop/sandbox.sb",)\n'


def git(repo, *args):
    return subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True,
                          check=True).stdout.strip()


class _Fixture(unittest.TestCase):
    """One throwaway repository with a base commit; no test of its own."""

    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix="g11b-")
        self.addCleanup(shutil.rmtree, self.scratch, True)
        patcher = mock.patch.dict(os.environ)          # every change below is undone when the case ends
        patcher.start()
        self.addCleanup(patcher.stop)
        for var in [k for k in os.environ if k.startswith("GIT_") or k == "GATE_LEDGER_PATH"]:
            os.environ.pop(var)
        os.environ.update(GIT_CONFIG_GLOBAL=os.path.join(self.scratch, "gitconfig"), GIT_CONFIG_NOSYSTEM="1")
        self.repo = os.path.join(self.scratch, "repo")
        subprocess.run(["git", "init", "-q", self.repo], check=True)
        git(self.repo, "config", "user.name", "fixture")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        self.write(".gitignore", "logs/\n")
        self.write("scripts/loop/deploy_stamped.py", DEPLOY)
        self.write("scripts/loop/tool.py", "x = 1\n")
        self.write("scripts/close_unit.py", "x = 1\n")
        self.write("scripts/required_fast.sh", "#!/bin/sh\n")
        self.write("docs/note.md", "one\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")

    def write(self, rel, text):
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def commit(self, rel, text):
        self.write(rel, text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "change " + rel)
        return git(self.repo, "rev-parse", "HEAD")

    def shared(self, repo=None):
        """The one whole-gate file every worktree shares, located here from git itself, not from the code under test."""
        common = git(repo or self.repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
        return os.path.join(os.path.realpath(common), "brother-whole-gate.jsonl")

    def record_for_head(self, path=None):
        """A PASS record for HEAD's tree, in the shared file unless a path (the old per checkout ledger) is named."""
        tree, why = gate_ledger.tree_snapshot(self.repo)
        self.assertIsNotNone(tree, why)
        ok, why = gate_ledger.record_whole_gate(path or self.shared(), self.repo, "a\t0\n", tree, "0", time.time())
        self.assertTrue(ok, why)

    def gate_cli(self, repo):
        """The writer exactly as scripts/required_fast.sh calls it, from that checkout's root, after an all PASS run."""
        tree = git(repo, "rev-parse", "HEAD^{tree}")
        codes = os.path.join(self.scratch, "codes.txt")
        with open(codes, "w") as f:
            f.write("a\t0\nb\t0\n")
        return subprocess.run([sys.executable, "-B", os.path.join(HERE, "gate_ledger.py"), "--whole-gate-record",
                               "--codes", codes, "--tree", tree, "--obligation", "0", "--started", "1"],
                              cwd=repo, capture_output=True, text=True)

    def worktree(self, name, sha):
        path = os.path.join(self.scratch, name)
        git(self.repo, "worktree", "add", "-q", "--detach", path, sha)
        return path

    def push(self, tip=None):
        """main() exactly as the hook runs it, with this push's ref line on stdin."""
        tip = tip or git(self.repo, "rev-parse", "HEAD")
        line = "refs/heads/feature %s refs/heads/feature %s\n" % (tip, self.base)
        return self.run_main(line)

    def run_main(self, line, *extra):
        err, out = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            for name in OTHER_CHECKS:
                stack.enter_context(mock.patch.object(G, name, lambda *a, **k: []))
            stack.enter_context(mock.patch.object(sys, "stdin", io.StringIO(line)))
            stack.enter_context(contextlib.redirect_stderr(err))
            stack.enter_context(contextlib.redirect_stdout(out))
            code = G.main(["--cwd", self.repo] + list(extra))
        return code, out.getvalue() + err.getvalue()


class PushRefusesLoopChangeWithoutRecord(_Fixture):
    def test_a_loop_file_with_no_record_is_refused_with_the_command(self):
        self.commit("scripts/loop/tool.py", "x = 2\n")
        code, out = self.push()
        self.assertEqual(code, G.EXIT_BLOCKED, out)
        self.assertIn("whole-gate", out)
        self.assertIn("sh scripts/required_fast.sh", out)
        self.assertIn("scripts/loop/tool.py", out)
        self.assertIn("does not exist", out)

    def test_a_pushed_sha_that_does_not_resolve_is_no_data(self):
        self.commit("scripts/loop/tool.py", "x = 2\n")
        code, out = self.push(tip="f" * 40)
        self.assertEqual(code, G.EXIT_NODATA, out)
        self.assertIn("does not resolve here", out)

    def test_hostile_ref_lines_are_no_data_at_the_check_itself(self):
        found = G.check_whole_gate(self.repo, None, 7)
        self.assertEqual([(f[0], f[1]) for f in found], [(G.NODATA, "whole-gate")])

    def test_a_record_for_another_tree_is_refused(self):
        self.record_for_head()                      # the base tree passed the whole gate
        self.commit("scripts/loop/tool.py", "x = 2\n")
        code, out = self.push()
        self.assertEqual(code, G.EXIT_BLOCKED, out)
        self.assertIn("not the pushed tree", out)

    def test_the_matching_record_lets_the_loop_change_through(self):
        self.commit("scripts/loop/tool.py", "x = 2\n")
        self.record_for_head()
        code, out = self.push()
        self.assertEqual(code, G.EXIT_OK, out)
        self.assertIn("whole-gate PASS on record", out)

    def test_no_loop_file_and_no_record_passes(self):
        self.commit("docs/note.md", "two\n")
        code, out = self.push()
        self.assertEqual(code, G.EXIT_OK, out)
        self.assertIn("no loop file in this push", out)

    def test_a_corrupt_ledger_is_refused(self):
        self.commit("scripts/loop/tool.py", "x = 2\n")
        self.record_for_head()
        with open(self.shared(), "a") as f:
            f.write("{ torn line\n")
        code, out = self.push()
        self.assertEqual(code, G.EXIT_BLOCKED, out)
        self.assertIn("corrupt", out)

    def test_an_unreadable_ledger_is_refused(self):
        self.commit("scripts/loop/tool.py", "x = 2\n")
        os.makedirs(self.shared())                           # a directory where the record file belongs: cannot be read
        code, out = self.push()
        self.assertEqual(code, G.EXIT_BLOCKED, out)
        self.assertIn("could not be read", out)

    def test_a_named_bin_source_outside_scripts_loop_counts_as_a_loop_change(self):
        self.commit("scripts/close_unit.py", "x = 2\n")
        code, out = self.push()
        self.assertEqual(code, G.EXIT_BLOCKED, out)
        self.assertIn("scripts/close_unit.py", out)

    def test_a_deploy_list_that_cannot_be_read_is_no_data(self):
        # The deploy file is under scripts/loop, so changing it needs a record; the case here is a LATER push of
        # another scripts/ file whose base already carries the unreadable list.
        for text, why in (("CANDIDATE_ENTRIES = make_list()\nCANDIDATE_DATA = ()\n", "could not be parsed"),
                          ('CANDIDATE_ENTRIES = ("scripts/close_unit.py",)\n', "no longer carries")):
            self.base = self.commit("scripts/loop/deploy_stamped.py", text)
            self.commit("scripts/other.py", text)
            code, out = self.push()
            self.assertEqual(code, G.EXIT_NODATA, out)
            self.assertIn(why, out)



class OneRecordForEveryWorktree(_Fixture):
    """Integration defect between G11 and M40 (2026-10-16): the record proves a fact about a TREE, but it was kept in the
    ledger of the checkout that ran the gate, so redeploy.sh, gating in one worktree and pushing the loop line from
    another worktree of the same repository at the same tree, was refused. The record now lives in ONE file in the
    repository's common git directory, which every worktree reads. Each case changes one condition."""

    def test_a_record_written_by_the_gate_in_one_worktree_clears_the_push_from_another_at_the_same_tree(self):
        tip = self.commit("scripts/loop/tool.py", "x = 2\n")
        gate = self.worktree("gate-wt", tip)
        run = self.gate_cli(gate)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn(self.shared(), run.stdout)
        self.assertFalse(os.path.exists(gate_ledger.ledger_path(gate)), "the record went to the gate checkout's own ledger")
        pusher = self.worktree("push-wt", tip)
        self.assertEqual(self.shared(pusher), self.shared())          # the same file, seen from the pushing worktree
        code, out = self.run_main("refs/heads/feature %s refs/heads/feature %s\n" % (tip, self.base), "--cwd", pusher)
        self.assertEqual(code, G.EXIT_OK, out)
        self.assertIn("whole-gate PASS on record for tree " + git(self.repo, "rev-parse", tip + "^{tree}"), out)
        self.assertIn("from " + self.shared(), out)

    def test_a_record_from_another_worktree_for_another_tree_is_refused(self):
        gate = self.worktree("gate-wt", self.base)                     # the base tree passed, in another worktree
        run = self.gate_cli(gate)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        tip = self.commit("scripts/loop/tool.py", "x = 2\n")
        pusher = self.worktree("push-wt", tip)
        code, out = self.run_main("refs/heads/feature %s refs/heads/feature %s\n" % (tip, self.base), "--cwd", pusher)
        self.assertEqual(code, G.EXIT_BLOCKED, out)
        self.assertIn("not the pushed tree", out)

    def test_a_dirty_worktree_writes_nothing_anywhere(self):
        tip = self.commit("scripts/loop/tool.py", "x = 2\n")
        gate = self.worktree("gate-wt", tip)
        with open(os.path.join(gate, "scripts", "loop", "tool.py"), "a") as f:
            f.write("# edited, not committed\n")
        run = self.gate_cli(gate)
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("uncommitted changes", run.stdout)
        self.assertFalse(os.path.exists(self.shared()))
        self.assertFalse(os.path.exists(gate_ledger.ledger_path(gate)))

    def test_a_record_in_the_checkouts_own_ledger_is_still_accepted_and_named(self):
        self.commit("scripts/loop/tool.py", "x = 2\n")
        old = gate_ledger.ledger_path(self.repo)
        self.record_for_head(old)                                      # written before the shared file existed
        code, out = self.push()
        self.assertEqual(code, G.EXIT_OK, out)
        self.assertIn("from the checkout ledger " + old, out)
        self.assertFalse(os.path.exists(self.shared()))

    def test_a_corrupt_shared_file_is_refused_even_when_the_checkout_ledger_holds_a_match(self):
        self.commit("scripts/loop/tool.py", "x = 2\n")
        self.record_for_head(gate_ledger.ledger_path(self.repo))
        with open(self.shared(), "w") as f:
            f.write("{ torn line\n")
        code, out = self.push()
        self.assertEqual(code, G.EXIT_BLOCKED, out)
        self.assertIn("corrupt", out)

    def test_a_common_dir_git_cannot_name_refuses_even_with_a_matching_record(self):
        self.commit("scripts/loop/tool.py", "x = 2\n")
        self.record_for_head(gate_ledger.ledger_path(self.repo))      # a record the old reader would accept
        real = gate_ledger._git_out

        def no_common(repo, *args):
            return None if "--git-common-dir" in args else real(repo, *args)
        with mock.patch.object(gate_ledger, "_git_out", no_common):
            code, out = self.push()
        self.assertEqual(code, G.EXIT_BLOCKED, out)
        self.assertIn("git could not name the common git directory", out)

    def test_a_common_dir_that_is_not_a_readable_directory_refuses(self):
        self.commit("scripts/loop/tool.py", "x = 2\n")
        self.record_for_head(gate_ledger.ledger_path(self.repo))
        real = gate_ledger._git_out
        gone = os.path.join(self.scratch, "no-such-common-dir")

        def elsewhere(repo, *args):
            return gone + "\n" if "--git-common-dir" in args else real(repo, *args)
        with mock.patch.object(gate_ledger, "_git_out", elsewhere):
            code, out = self.push()
        self.assertEqual(code, G.EXIT_BLOCKED, out)
        self.assertIn("not a readable directory", out)


class PushJudgedByTreeNotRange(_Fixture):
    """G11.b, adversarial review 2026-10-16: the gate judged the outgoing COMMIT RANGE, so a push whose range is empty
    read "no loop file in this push" although the pushed tip's tree differs from the remote's in a loop file. Two
    executed repros: a rewind (C2 changed a loop file, the branch moves back to C1) and a new branch created at a commit
    another remote ref already carries but that was never gated. Every ref line here is the one git itself hands the
    hook: a real git push to a bare remote, its pre-push hook capturing stdin and refusing, then main() fed that line."""

    def setUp(self):
        super().setUp()
        git(self.repo, "branch", "-M", "main")
        self.remote = os.path.join(self.scratch, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", self.remote], check=True)
        git(self.remote, "symbolic-ref", "HEAD", "refs/heads/main")
        git(self.repo, "remote", "add", "origin", self.remote)
        git(self.repo, "push", "-q", "origin", "main", "main:refs/heads/feature")
        git(self.repo, "remote", "set-head", "origin", "main")
        self.capture = os.path.join(self.scratch, "ref-lines")
        hook = os.path.join(self.repo, ".git", "hooks", "pre-push")
        with open(hook, "w") as f:
            f.write("#!/bin/sh\ncat > '%s'\nexit 1\n" % self.capture)
        os.chmod(hook, 0o755)

    def ref_lines(self, remote, *refspecs):
        """What git hands the pre-push hook for this push. The capturing hook refuses, so nothing reaches the remote."""
        if os.path.exists(self.capture):
            os.remove(self.capture)
        subprocess.run(["git", "-C", self.repo, "push", remote] + list(refspecs), capture_output=True, text=True)
        with open(self.capture) as f:
            line = f.read()
        self.assertTrue(line.strip(), "git handed the hook no ref line")
        return line

    def no_verify(self, *refspecs):
        git(self.repo, "push", "-q", "--no-verify", "origin", *refspecs)

    def record_for(self, sha):
        git(self.repo, "checkout", "-q", sha)
        self.record_for_head()

    def test_a_rewind_over_a_loop_change_is_refused_without_a_record(self):
        c1 = self.commit("docs/note.md", "two\n")
        c2 = self.commit("scripts/loop/tool.py", "x = 2\n")
        self.no_verify("%s:refs/heads/feature" % c2)
        line = self.ref_lines("origin", "+%s:refs/heads/feature" % c1)
        self.assertEqual(line.split()[1:4:2], [c1, c2], line)        # the range c2..c1 is empty
        code, out = self.run_main(line)
        self.assertEqual(code, G.EXIT_BLOCKED, out)
        self.assertIn("scripts/loop/tool.py", out)
        self.assertIn("sh scripts/required_fast.sh", out)
        self.record_for(c1)
        code, out = self.run_main(line)
        self.assertEqual(code, G.EXIT_OK, out)
        self.assertIn("whole-gate PASS on record", out)

    def test_a_new_branch_at_an_ungated_commit_another_ref_carries_is_refused_without_a_record(self):
        c2 = self.commit("scripts/loop/tool.py", "x = 2\n")
        self.no_verify("%s:refs/heads/other" % c2)                    # on the remote, never gated
        line = self.ref_lines("origin", "%s:refs/heads/fresh" % c2)
        self.assertTrue(G._is_zero(line.split()[3]), line)           # a new ref: git sends no remote sha
        code, out = self.run_main(line)
        self.assertEqual(code, G.EXIT_BLOCKED, out)
        self.assertIn("scripts/loop/tool.py", out)
        self.record_for(c2)
        code, out = self.run_main(line)
        self.assertEqual(code, G.EXIT_OK, out)
        self.assertIn("whole-gate PASS on record", out)

    def test_a_new_branch_with_no_base_to_read_counts_every_loop_file_in_the_tip(self):
        c1 = self.commit("docs/note.md", "two\n")
        git(self.repo, "remote", "set-head", "origin", "-d")          # the remote's default branch cannot be read
        line = self.ref_lines("origin", "%s:refs/heads/fresh" % c1)
        code, out = self.run_main(line)
        self.assertEqual(code, G.EXIT_BLOCKED, out)
        self.assertIn("scripts/loop/tool.py", out)
        self.assertIn("no base", out)
        self.record_for(c1)
        code, out = self.run_main(line)
        self.assertEqual(code, G.EXIT_OK, out)

    def test_a_remote_sha_that_does_not_resolve_here_counts_every_loop_file_in_the_tip(self):
        c1 = self.commit("docs/note.md", "two\n")
        code, out = self.run_main("refs/heads/feature %s refs/heads/feature %s\n" % (c1, "e" * 40))
        self.assertEqual(code, G.EXIT_BLOCKED, out)
        self.assertIn("no base", out)

    def test_the_hook_remote_name_picks_that_remote_default_branch(self):
        c1 = self.commit("docs/note.md", "two\n")
        git(self.repo, "remote", "rename", "origin", "hub")           # no origin left; hub's default is readable
        line = self.ref_lines("hub", "%s:refs/heads/fresh" % c1)
        code, out = self.run_main(line, "--remote-name", "hub")
        self.assertEqual(code, G.EXIT_OK, out)
        self.assertIn("no loop file in this push", out)

    def test_a_plain_forward_push_is_judged_as_before(self):
        self.commit("scripts/loop/tool.py", "x = 2\n")
        line = self.ref_lines("origin", "HEAD:refs/heads/feature")
        code, out = self.run_main(line)
        self.assertEqual(code, G.EXIT_BLOCKED, out)
        self.assertIn("does not exist", out)
        self.record_for_head()
        code, out = self.run_main(line)
        self.assertEqual(code, G.EXIT_OK, out)

    def test_a_docs_only_push_reads_no_whole_tree_and_asks_no_network(self):
        for refspec in ("HEAD:refs/heads/feature", "HEAD:refs/heads/fresh"):
            self.commit("docs/note.md", refspec + "\n")
            line = self.ref_lines("origin", refspec)
            calls = []
            real = G._git

            def counting(args, cwd=G.ROOT, runner=None):
                calls.append(args[0])
                return real(args, cwd, runner)
            with mock.patch.object(G, "_git", counting):
                code, out = self.run_main(line)
            self.assertEqual(code, G.EXIT_OK, out)
            self.assertIn("no loop file in this push", out)
            self.assertFalse({"ls-tree", "ls-remote", "fetch"} & set(calls), calls)
            self.assertLessEqual(len(calls), 6, calls)

    def test_a_ref_deletion_needs_nothing(self):
        self.no_verify("%s:refs/heads/feature" % self.commit("scripts/loop/tool.py", "x = 2\n"))
        line = self.ref_lines("origin", ":refs/heads/feature")
        self.assertTrue(G._is_zero(line.split()[1]), line)
        code, out = self.run_main(line)
        self.assertEqual(code, G.EXIT_OK, out)

    def test_a_loop_file_whose_name_git_would_quote_still_counts(self):
        self.commit("scripts/loop/caf\u00e9.py", "x = 1\n")
        line = self.ref_lines("origin", "HEAD:refs/heads/feature")
        code, out = self.run_main(line)
        self.assertEqual(code, G.EXIT_BLOCKED, out)
        self.assertIn("scripts/loop/caf", out)

class HermeticTimingLine(unittest.TestCase):
    """A4: one line per push saying where the hermetic wait went, read from check_hermetic_tests' own flow."""

    def test_the_push_prints_export_and_suite_seconds(self):
        import hermetic_test_check as hermetic

        def slow_builder(root):
            return lambda tree: time.sleep(0.3)

        def fake_check(root, tests, build=None, **kw):
            build("unused")
            time.sleep(0.2)
            return [(hermetic.OK, "hermetic-tests", "stub")]
        err = io.StringIO()
        with mock.patch.object(G, "_outgoing_ranges", lambda *a, **k: [["x..y"]]), \
                mock.patch.object(G, "_git", lambda *a, **k: subprocess.CompletedProcess([], 0, "", "")), \
                mock.patch.object(hermetic.P, "export_tree_builder", slow_builder), \
                mock.patch.object(hermetic, "check", fake_check), \
                mock.patch.object(hermetic, "timing_results", lambda paths, root: []), \
                contextlib.redirect_stderr(err):
            found = G.check_hermetic_tests(G.ROOT, None, None)
        self.assertIn((G.OK, "hermetic", "hermetic-tests: stub"), found)
        lines = [l for l in err.getvalue().splitlines() if l.startswith("hermetic: ")]
        self.assertEqual(len(lines), 1, err.getvalue())
        m = re.match(r"^hermetic: export (\d+\.\d) s, suites (\d+\.\d) s$", lines[0])
        self.assertTrue(m, lines[0])
        self.assertGreaterEqual(float(m.group(1)), 0.3)
        self.assertGreaterEqual(float(m.group(2)), 0.1)


if __name__ == "__main__":
    unittest.main()
