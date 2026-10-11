#!/usr/bin/env python3
"""Tests for scripts/merge_precompute.py (MG1.c): the background pre-computation.

HERMETIC BY CONSTRUCTION. A throwaway bare repository is the hub, a stand-in `gh` answers from a dict (or, for the
command line, from files under a directory first on PATH), and the gate command is a stand-in scripts/required_fast.sh
committed into the throwaway tree that prints required_fast's own summary line and fails only when the candidate tree
carries fail.txt. No case reads the network, the real hub, or a home folder.

WHAT IT PROVES, one guard per case: list order and chaining (main plus PR 1, then plus PR 2), the stop at the first
FAIL with BLOCKED-BY and no tree recorded on top of a failing one, the skip of an already gated tree, the restart from
a moved main and STALE-BASE after the bound, the idempotent background start, the reclaim of a lock whose pid is dead
or runs another command, NO-DATA on a wait_ready timeout, and that a PR whose mergeable is still computing never
enters the list. The spec's mutations M-MG1-C-1 to M-MG1-C-8 are applied by hand to the module and each turns a named
case here red.

Run: python3 -B scripts/test_merge_precompute.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import merge_gate  # noqa: E402
import merge_precompute  # noqa: E402
import heavy_slot  # noqa: E402

TOOL_FILES = ("merge_gate.py", "tmp_sandbox.py", "merge_precompute.py", "gate_merge_seq.sh", "merge_verified.sh",
              "merge_pins.py", "heavy_slot.py", "brother_paths.py", "resource_gate.py")
# the stand-in gate: required_fast's own summary line, a FAIL only when the candidate tree carries fail.txt
GATE_STUB = ("#!/bin/sh\n"
             "if [ -f fail.txt ]; then echo 'x FAIL'; echo 'pass 0   fail 1   no-data 0'; echo 'FAILED: x'; exit 1; fi\n"
             "if [ -f slow.txt ]; then sleep 4; fi\n"
             "echo 'pass 1   fail 0   no-data 0'\n")
GH_STUB = r"""#!/bin/sh
# the stand-in gh: a pull request's JSON from $FIX/pr-<n>.json, nothing else
echo "$*" >> "$FIX/gh.log"
case "$1 $2" in
  "pr view") cat "$FIX/pr-$3.json" ;;
  *) echo "gh stub: unhandled $*" >&2; exit 3 ;;
esac
"""


def _ps_runs():
    """Inside the landing sandbox ps cannot run at all (execvp: Operation not permitted): the cases that read the
    process table through it skip as NO-DATA there, the same probe scripts/test_cv1_cut_rehearsed.py uses."""
    try:
        return str(os.getpid()) in subprocess.run(["ps", "-p", str(os.getpid()), "-o", "pid="], capture_output=True,
                                                  text=True, timeout=10).stdout
    except OSError:
        return False


PS_RUNS = _ps_runs()
NO_PS = "NO-DATA: no process listing here (sandboxed), so a worker cannot be found again through ps"


def _clean_env(extra=None):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("MERGE_", "GIT_", "GH_", "GITHUB_", "BROTHER_")) and k != "XDG_CONFIG_HOME"}
    env["BROTHER_HEAVY_SLOT"] = "off"   # the load rules are tested on their own; a worker here never waits on load
    env.update(extra or {})
    return env


class Proc:
    def __init__(self, code, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


class Fixture(unittest.TestCase):
    """A throwaway hub, a clone of it named `hub`, two PR heads, and the tool copies under the clone's scripts/."""

    def git(self, cwd, *a):
        r = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false"] + list(a),
                           cwd=cwd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def setUp(self):
        self.fix = tempfile.mkdtemp(prefix="mg1c-")
        self.addCleanup(shutil.rmtree, self.fix, True)
        self.hub = os.path.join(self.fix, "hub.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.hub], check=True)
        self.clone = os.path.join(self.fix, "clone")
        subprocess.run(["git", "clone", "-q", "-o", "hub", self.hub, self.clone], check=True, capture_output=True)
        # THE CLONE CARRIES ITS OWN IDENTITY (2026-10-05): the worker's commit-tree used the machine's global one, so under
        # a fresh HOME (the grader's sandbox, the hermetic gate) it exited 128 "Author identity unknown" and every
        # worker case waited out its 90 s: 379 s and 16 reds here, about 220 s of each MG1 grade run.
        for key, value in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
            subprocess.run(["git", "-C", self.clone, "config", key, value], check=True, capture_output=True)
        os.makedirs(os.path.join(self.clone, "scripts"))
        gate = os.path.join(self.clone, "scripts", "required_fast.sh")
        open(gate, "w").write(GATE_STUB)
        open(os.path.join(self.clone, "base.txt"), "w").write("base\n")
        self.git(self.clone, "add", "-A")
        self.git(self.clone, "commit", "-qm", "base")
        self.git(self.clone, "push", "-q", "hub", "HEAD:main")
        self.main = self.git(self.clone, "rev-parse", "HEAD")
        self.heads = {}
        for n in ("1", "2"):
            self.git(self.clone, "checkout", "-q", "-b", "pr" + n, "hub/main")
            open(os.path.join(self.clone, "f%s.txt" % n), "w").write(n + "\n")
            self.git(self.clone, "add", "-A")
            self.git(self.clone, "commit", "-qm", "pr " + n)
            self.heads[n] = self.git(self.clone, "rev-parse", "HEAD")
        self.git(self.clone, "checkout", "-q", "--detach", "hub/main")
        for name in TOOL_FILES:
            shutil.copy(os.path.join(HERE, name), os.path.join(self.clone, "scripts", name))
        self.mover = os.path.join(self.fix, "mover")
        subprocess.run(["git", "clone", "-q", "-o", "hub", self.hub, self.mover], check=True, capture_output=True)
        self.list = os.path.join(self.fix, "list")
        open(self.list, "w").write("1 %s\n2 %s\n" % (self.heads["1"], self.heads["2"]))
        self.env = _clean_env()
        self.gates = []
        self.gated = []

    def pins(self):
        return [{"pr": "1", "head": self.heads["1"]}, {"pr": "2", "head": self.heads["2"]}]

    def runner(self, hook=None):
        """subprocess.run with stdin closed; records every gate call and runs `hook(pr)` after each one."""
        def run(argv, cwd=None, env=None):
            r = subprocess.run(argv, cwd=cwd, env=env if env is not None else self.env, stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, errors="replace")
            if argv == ["sh", merge_gate.GATE_SCRIPT]:
                self.gates.append(os.path.basename(cwd))
                # what the gate actually tested: the worktree's own tree, and whether PR 1's file was in it
                self.gated.append((self.git(cwd, "rev-parse", "HEAD^{tree}"), os.path.exists(os.path.join(cwd, "f1.txt"))))
                if hook is not None:
                    hook(len(self.gates))
            return r
        return run

    def move_main(self):
        """A commit the owner (or another batch) lands on hub main while the worker runs."""
        open(os.path.join(self.mover, "moved-%d.txt" % time.time_ns()), "w").write("moved\n")
        self.git(self.mover, "add", "-A")
        self.git(self.mover, "commit", "-qm", "moved")
        self.git(self.mover, "push", "-q", "hub", "HEAD:main")
        return self.git(self.mover, "rev-parse", "HEAD")

    def tree(self, base, head):
        return self.git(self.clone, "merge-tree", "--write-tree", base, head)

    def chained_base(self, tree, base):
        return self.git(self.clone, "commit-tree", tree, "-p", base, "-m", "chain")

    def fail_head(self, n):
        """A PR head whose tree carries fail.txt, so the stand-in gate fails on it."""
        self.git(self.clone, "checkout", "-q", "pr" + n)
        open(os.path.join(self.clone, "fail.txt"), "w").write("fail\n")
        self.git(self.clone, "add", "fail.txt")   # never -A: the untracked tool copies under scripts/ stay out of the tree
        self.git(self.clone, "commit", "-qm", "pr %s fails" % n)
        self.heads[n] = self.git(self.clone, "rev-parse", "HEAD")
        self.git(self.clone, "checkout", "-q", "--detach", "hub/main")
        open(self.list, "w").write("1 %s\n2 %s\n" % (self.heads["1"], self.heads["2"]))

    def sign_pass(self, tree, rc=0, summary="pass 3   fail 0   no-data 0\n"):
        """A full row for the tree through merge_gate.record_row, the one row writer: every verify_row rule holds."""
        log = os.path.join(self.fix, "gate-%d.log" % time.time_ns())
        with open(log, "w") as fh:
            fh.write(summary)
        return merge_gate.record_row(self.clone, tree, rc, log, "1", self.heads["1"], self.main, {},
                                     runner=self.runner())


class TestChain(Fixture):
    def test_the_trees_are_chained_in_list_order(self):
        # M-MG1-C-1 (reverse order) and M-MG1-C-3 (bare main instead of the chained tree) both turn this red
        trees = merge_precompute.chain_trees(self.clone, self.main, self.pins(), runner=self.runner())
        tree1 = self.tree(self.main, self.heads["1"])
        tree2 = self.tree(self.chained_base(tree1, self.main), self.heads["2"])
        self.assertEqual(trees, [tree1, tree2])
        self.assertNotEqual(tree2, self.tree(self.main, self.heads["2"]), "bare main is not the chained tree")

    def test_hostile_input_is_refused(self):
        for bad in ("x", b"x", 1, None, {"pr": "1"}):
            with self.assertRaises(ValueError):
                merge_precompute.chain_trees(self.clone, self.main, bad, runner=self.runner())
        with self.assertRaises(ValueError):
            merge_precompute.chain_trees(self.clone, self.main, [{"pr": "1"}], runner=self.runner())
        with self.assertRaises(ValueError):
            merge_precompute.chain_trees(self.clone, self.main, [], runner=7)


class TestRunPrecompute(Fixture):
    def test_every_pr_is_gated_in_list_order_on_the_chained_tree(self):
        status = merge_precompute.run_precompute(self.clone, self.pins(), self.env, self.runner())
        self.assertEqual(status["status"], "DONE", status)
        self.assertEqual([e["pr"] for e in status["entries"]], ["1", "2"])
        self.assertEqual(self.gates, ["wt-1", "wt-2"], "the gate ran out of list order")   # M-MG1-C-1
        tree1 = self.tree(self.main, self.heads["1"])
        tree2 = self.tree(self.chained_base(tree1, self.main), self.heads["2"])
        self.assertEqual([e["tree"] for e in status["entries"]], [tree1, tree2])   # M-MG1-C-3
        self.assertEqual([e["result"] for e in status["entries"]], ["PASS", "PASS"])
        for tree in (tree1, tree2):
            self.assertEqual(merge_gate.verify_tree(self.clone, tree, self.env, self.runner())[0], "PASS")
        self.assertEqual(status["base"], self.main)
        self.assertEqual(status["restarts"], 0)

    def test_the_gate_runs_in_the_chained_tree_not_at_the_pin_head(self):
        # review 2026-10-04 (H1): PR 2's head lacks PR 1, so a worktree at the pin head would test PR 2 alone while the
        # row named the merged tree; the gate must run in a worktree holding exactly the tree the row records
        status = merge_precompute.run_precompute(self.clone, self.pins(), self.env, self.runner())
        self.assertEqual(status["status"], "DONE", status)
        self.assertEqual([t for t, _f1 in self.gated], [e["tree"] for e in status["entries"]],
                         "a gate ran in a worktree whose tree is not the one its row records")
        self.assertEqual([f1 for _t, f1 in self.gated], [True, True], "PR 2 was gated without PR 1's change")
        self.assertNotEqual(self.gated[1][0], self.git(self.clone, "rev-parse", self.heads["2"] + "^{tree}"))

    def test_run_gate_refuses_a_worktree_whose_tree_is_not_the_one_to_record(self):
        # the gate itself, with real git: a worktree at PR 2's head is asked to record the chained tree; nothing runs
        tree1 = self.tree(self.main, self.heads["1"])
        tree2 = self.tree(self.chained_base(tree1, self.main), self.heads["2"])
        worktree = os.path.join(self.fix, "wt-head2")
        self.git(self.clone, "worktree", "add", "--quiet", "--detach", worktree, self.heads["2"])
        with self.assertRaises(merge_gate.MergeNoData) as caught:
            merge_gate.run_gate(self.clone, worktree, tree2, "2", self.heads["2"], self.main, self.env, runner=self.runner())
        self.assertIn("not the tree", str(caught.exception))
        self.assertEqual(self.gates, [], "the gate ran in a worktree holding another tree")
        self.assertEqual(merge_gate.verify_tree(self.clone, tree2, self.env, self.runner())[0], "NO-DATA", "a row was written")
        self.assertFalse(os.path.exists(merge_gate.ledger_path(self.clone, self.env)))

    def test_a_fail_stops_the_chain_and_records_no_later_tree(self):
        # M-MG1-C-2: continuing after a FAIL and recording later trees turns this red
        self.fail_head("1")
        status = merge_precompute.run_precompute(self.clone, self.pins(), self.env, self.runner())
        self.assertEqual(status["status"], "FAILED", status)
        self.assertEqual(status["entries"][0]["result"], "FAIL")
        self.assertEqual(status["entries"][1]["result"], "BLOCKED-BY #1")
        self.assertEqual(self.gates, ["wt-1"], "a gate ran on top of a failing tree")
        tree1 = self.tree(self.main, self.heads["1"])
        self.assertEqual(merge_gate.verify_tree(self.clone, tree1, self.env, self.runner())[0], "FAIL")
        tree2 = self.tree(self.chained_base(tree1, self.main), self.heads["2"])
        self.assertEqual(merge_gate.verify_tree(self.clone, tree2, self.env, self.runner())[0], "NO-DATA", "a tree was recorded on top of a FAIL")

    def test_a_tree_already_gated_is_skipped(self):
        self.sign_pass(self.tree(self.main, self.heads["1"]))
        status = merge_precompute.run_precompute(self.clone, self.pins(), self.env, self.runner())
        self.assertEqual(status["status"], "DONE", status)
        self.assertEqual(status["entries"][0]["result"], "SKIPPED-ALREADY-GATED")
        self.assertEqual(status["entries"][1]["result"], "PASS")
        self.assertEqual(self.gates, ["wt-2"], "an already gated tree was gated again")

    def test_a_moved_main_restarts_the_chain_from_the_new_main(self):
        # M-MG1-C-7: skipping the re-fetch keeps recording trees on the old base and turns this red
        moved = []

        def hook(count):
            if count == 1:
                moved.append(self.move_main())

        status = merge_precompute.run_precompute(self.clone, self.pins(), self.env, self.runner(hook))
        self.assertEqual(status["status"], "DONE", status)
        self.assertEqual(status["restarts"], 1)
        self.assertEqual(status["base"], moved[0], "the chain did not restart from the moved main")
        tree1 = self.tree(moved[0], self.heads["1"])
        self.assertEqual(status["entries"][0]["tree"], tree1)
        self.assertEqual(status["entries"][1]["tree"], self.tree(self.chained_base(tree1, moved[0]), self.heads["2"]))
        self.assertEqual(self.gates, ["wt-1", "wt-1", "wt-2"])

    def test_a_main_that_keeps_moving_ends_stale_base(self):
        status = merge_precompute.run_precompute(self.clone, self.pins(), self.env,
                                                 self.runner(lambda count: self.move_main()))
        self.assertEqual(status["status"], "STALE-BASE", status)
        self.assertGreater(status["restarts"], merge_precompute.MAX_RESTARTS)
        self.assertEqual(status["entries"][1]["result"], "PENDING", "the second PR was gated on a stale base")

    def test_the_status_file_is_written_as_it_goes(self):
        path = os.path.join(self.fix, "status.json")
        status = merge_precompute.run_precompute(self.clone, self.pins(), self.env, self.runner(), status_path=path)
        with open(path) as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk["status"], "DONE")
        self.assertEqual(on_disk["entries"], status["entries"])
        self.assertTrue(on_disk["updated"])

    def test_the_worker_holds_no_slot_while_its_gate_runs(self):
        # C11, 2026-10-10: the gate child queues on heavy_slot itself at weight REQUIRED_FAST_JOBS, all or nothing, in
        # the pool HOME names. A slot the worker held was one the child could never gather, so every gate waited out
        # the whole bound and then ran unqueued. While each gate runs, every slot of the pool must be free.
        pool = tempfile.mkdtemp(prefix="slots-", dir=self.fix)
        load = os.path.join(self.fix, "load1")
        with open(load, "w") as fh:
            fh.write("0\n")
        env = _clean_env({"BROTHER_SLOT_DIR": pool, "LOCAL_SLOTS": "4", "BROTHER_LOAD_READING_FILE": load,
                          "BROTHER_HEAVY_SLOT_WAIT": "5"})
        del env["BROTHER_HEAVY_SLOT"]
        free = []

        def hook(count):
            held = heavy_slot.try_acquire(pool, 4, 4, label=False)
            free.append(len(held))
            for _index, handle in held:
                handle.close()

        merge_precompute.run_precompute(self.clone, self.pins(), env, self.runner(hook))
        self.assertEqual(free, [4, 4])

    def test_the_worker_never_queues_for_a_slot_itself(self):
        # C11, 2026-10-10: one queue, the gate child's own; no path in the worker imports heavy_slot to queue first
        with open(os.path.join(HERE, "merge_precompute.py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertIsNone(re.search(r"^\s*(import heavy_slot|from heavy_slot import)", source, re.M))

    def test_rows_are_written_only_through_run_gate(self):
        # R-MG-2: the worker never calls record_row and types no rc
        with open(os.path.join(HERE, "merge_precompute.py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertIsNone(re.search(r"record_row\s*\(", source))
        self.assertIn("merge_gate.run_gate(", source)

    def test_hostile_input_is_refused(self):
        for bad in ("x", 1, None, [1], [{"pr": 1, "head": "x"}], [{"pr": "1"}]):
            with self.assertRaises(ValueError):
                merge_precompute.run_precompute(self.clone, bad, self.env, self.runner())
        with self.assertRaises(ValueError):
            merge_precompute.run_precompute(self.clone, [], "env", self.runner())
        with self.assertRaises(ValueError):
            merge_precompute.run_precompute(self.clone, [], self.env, 3)


class TestWaitReady(Fixture):
    def test_a_timeout_is_no_data_never_a_pass(self):
        # M-MG1-C-5: a timeout read as PASS turns this red
        tree = self.tree(self.main, self.heads["1"])
        started = time.monotonic()
        verdict, why = merge_precompute.wait_ready(self.clone, tree, 1, self.env)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("timed out", why)
        self.assertGreaterEqual(time.monotonic() - started, 0.9)
        self.assertEqual(merge_precompute.wait_ready(self.clone, tree, 0, self.env)[0], "NO-DATA")

    def test_a_row_that_arrives_is_its_own_verdict(self):
        tree = self.tree(self.main, self.heads["1"])
        self.sign_pass(tree)
        self.assertEqual(merge_precompute.wait_ready(self.clone, tree, 5, self.env)[0], "PASS")
        other = self.tree(self.main, self.heads["2"])
        self.sign_pass(other, 1, "pass 0   fail 1   no-data 0\nFAILED: x\n")
        self.assertEqual(merge_precompute.wait_ready(self.clone, other, 5, self.env)[0], "FAIL")

    def test_hostile_input_is_refused(self):
        tree = self.tree(self.main, self.heads["1"])
        for bad in (-1, True, None, "5", float("nan")):
            with self.assertRaises(ValueError):
                merge_precompute.wait_ready(self.clone, tree, bad, self.env)
        with self.assertRaises(ValueError):
            merge_precompute.wait_ready(self.clone, "not-a-tree", 1, self.env)


class TestReadPins(unittest.TestCase):
    def answers(self, table, fail=()):
        seen = []

        def runner(argv, cwd=None, env=None):
            seen.append(argv)
            pr = argv[3]
            if pr in fail:
                return Proc(1, "", "no such pull request")
            return Proc(0, json.dumps(table[pr]), "")
        return runner, seen

    def info(self, **over):
        row = {"state": "OPEN", "baseRefName": "main", "headRefName": "fix/x", "mergeable": "MERGEABLE",
               "headRefOid": "a" * 40}
        row.update(over)
        return row

    def test_pins_come_back_in_list_order_from_gh(self):
        runner, seen = self.answers({"7": self.info(), "3": self.info(headRefOid="b" * 40, headRefName="loop/run-x")})
        pins = merge_precompute.read_pins(["7", "3"], "owner/repo", runner)
        self.assertEqual([p["pr"] for p in pins], ["7", "3"])
        self.assertEqual([p["head"] for p in pins], ["a" * 40, "b" * 40])
        self.assertEqual(pins[1]["head_ref"], "loop/run-x")
        self.assertEqual(seen[0][:6], ["gh", "pr", "view", "7", "-R", "owner/repo"])
        self.assertIn("--json", seen[0])

    def test_a_pr_still_computing_mergeable_never_enters_the_list(self):
        # M-MG1-C-8: letting UNKNOWN through turns this red
        runner, _seen = self.answers({"7": self.info(), "3": self.info(mergeable="UNKNOWN")})
        with self.assertRaises(merge_gate.MergeNoData) as caught:
            merge_precompute.read_pins(["7", "3"], "owner/repo", runner)
        self.assertIn("#3", str(caught.exception))
        self.assertIn("UNKNOWN", str(caught.exception))

    def test_a_conflicting_pr_is_a_refusal_naming_it(self):
        runner, _seen = self.answers({"7": self.info(mergeable="CONFLICTING")})
        with self.assertRaises(merge_precompute.PinRefused) as caught:
            merge_precompute.read_pins(["7"], "owner/repo", runner)
        self.assertIn("#7", str(caught.exception))
        self.assertNotIsInstance(caught.exception, merge_gate.MergeNoData)

    def test_a_pr_that_is_not_open_or_not_on_main_is_refused(self):
        runner, _seen = self.answers({"7": self.info(state="MERGED"), "8": self.info(baseRefName="develop")})
        for pr in ("7", "8"):
            with self.assertRaises(merge_precompute.PinRefused):
                merge_precompute.read_pins([pr], "owner/repo", runner)

    def test_a_pr_the_tool_cannot_read_stops_the_whole_step(self):
        runner, seen = self.answers({"7": self.info(), "3": self.info()}, fail=("3",))
        with self.assertRaises(merge_gate.MergeNoData):
            merge_precompute.read_pins(["7", "3", "9"], "owner/repo", runner)
        self.assertEqual(len(seen), 2, "the step went on past the PR it could not read")

    def test_bad_gh_output_is_no_data(self):
        for out in ("", "not json", "[]", json.dumps(self.info(headRefOid="short")), json.dumps({"state": "OPEN"})):
            with self.assertRaises(merge_gate.MergeNoData):
                merge_precompute.read_pins(["1"], "o/r", lambda argv, cwd=None, env=None: Proc(0, out, ""))

    def test_hostile_input_is_refused(self):
        runner, _seen = self.answers({"1": self.info()})
        for bad in ("1", b"1", 1, None, ["x"], [1], [""], ["1 2"]):
            with self.assertRaises(ValueError):
                merge_precompute.read_pins(bad, "o/r", runner)
        with self.assertRaises(ValueError):
            merge_precompute.read_pins(["1"], "", runner)
        with self.assertRaises(ValueError):
            merge_precompute.read_pins(["1"], "o/r", "gh")


class TestListAndStatus(Fixture):
    def test_the_written_list_is_the_one_merge_verified_reads(self):
        path = os.path.join(self.fix, "written")
        merge_precompute.write_list(path, self.pins())
        self.assertEqual(open(path).read(), "1 %s\n2 %s\n" % (self.heads["1"], self.heads["2"]))
        self.assertEqual(merge_precompute.parse_list(path), self.pins())

    def test_a_list_line_with_no_sha_refuses_the_list(self):
        path = os.path.join(self.fix, "bad")
        open(path, "w").write("# comment\n1 %s\n2\n" % self.heads["1"])
        with self.assertRaises(merge_gate.MergeNoData):
            merge_precompute.parse_list(path)
        open(path, "w").write("1 notasha\n")
        with self.assertRaises(merge_gate.MergeNoData):
            merge_precompute.parse_list(path)
        with self.assertRaises(merge_gate.MergeNoData):
            merge_precompute.parse_list(os.path.join(self.fix, "absent"))

    def test_no_status_and_a_truncated_status_are_no_data(self):
        self.assertEqual(merge_precompute.read_status(self.list, self.env, self.clone)["status"], "NO-DATA")
        path = merge_precompute.status_path(self.clone, self.list, self.env)
        open(path, "w").write('{"schema": 1, "status": "DO')
        self.assertEqual(merge_precompute.read_status(self.list, self.env, self.clone)["status"], "NO-DATA")
        open(path, "w").write('{"schema": 1, "status": "DONE", "entries": []}')
        self.assertEqual(merge_precompute.read_status(self.list, self.env, self.clone)["status"], "DONE")
        self.assertEqual(merge_precompute.read_status(os.path.join(self.fix, "absent"), self.env, self.clone)["status"],
                         "NO-DATA")

    def test_main_refuses_an_argv_that_is_not_a_list_of_str(self):
        for bad in (0, True, b"x", "start", ["start", 1], object()):
            with self.assertRaises(ValueError):
                merge_precompute.main(bad)


class TestBackground(Fixture):
    """The real detached worker: start_background spawns gate_merge_seq.sh, which execs into `worker` under the
    pinned /usr/bin/python3, and the status file beside the ledger reaches DONE."""

    def setUp(self):
        super().setUp()
        self.started = []
        self.addCleanup(self._reap)

    def _reap(self):
        for pid in self.started:
            try:
                os.killpg(pid, 15)
            except OSError:
                pass  # sbe: allow-silent the worker already ended, which is the expected end state

    def start(self):
        pid = merge_precompute.start_background(self.clone, self.list, self.env)
        self.started.append(pid)
        return pid

    def wait_done(self, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = merge_precompute.read_status(self.list, self.env, self.clone)
            if status["status"] not in ("NO-DATA", "RUNNING"):
                return status
            time.sleep(0.5)
        log = os.path.join(os.path.dirname(merge_gate.ledger_path(self.clone, {})), "precompute")
        logs = "\n".join(open(os.path.join(log, n)).read() for n in os.listdir(log) if n.endswith(".log"))
        self.fail("the worker did not finish in %d s; worker log:\n%s" % (timeout, logs))

    def workers(self):
        if not PS_RUNS:
            self.skipTest(NO_PS)
        out = subprocess.run(["ps", "-axo", "command="], capture_output=True, text=True).stdout
        return [line for line in out.splitlines() if "merge_precompute.py worker" in line and self.list in line]

    def slow(self):
        """PR heads whose stand-in gate sleeps (the gate runs in a worktree at the head, so the marker lives there),
        so a worker is alive long enough to be found again."""
        for n in ("1", "2"):
            self.git(self.clone, "checkout", "-q", "pr" + n)
            open(os.path.join(self.clone, "slow.txt"), "w").write("slow\n")
            self.git(self.clone, "add", "slow.txt")
            self.git(self.clone, "commit", "-qm", "pr %s slow gate" % n)
            self.heads[n] = self.git(self.clone, "rev-parse", "HEAD")
        self.git(self.clone, "checkout", "-q", "--detach", "hub/main")
        open(self.list, "w").write("1 %s\n2 %s\n" % (self.heads["1"], self.heads["2"]))

    def test_the_detached_worker_gates_the_list_and_writes_its_status(self):
        pid = self.start()
        self.assertGreater(pid, 0)
        status = self.wait_done()
        self.assertEqual(status["status"], "DONE", status)
        self.assertEqual([e["result"] for e in status["entries"]], ["PASS", "PASS"])
        tree1 = self.tree(self.main, self.heads["1"])
        self.assertEqual(merge_gate.verify_tree(self.clone, tree1, self.env, self.runner())[0], "PASS")
        self.assertEqual(status["pid"], pid, "the status was written by another process than the one started")

    def lock_with(self, pid):
        directory = merge_precompute._precompute_dir(self.clone, self.env)
        digest = merge_precompute._sha256_file(self.list)
        with open(os.path.join(directory, digest + ".lock"), "w") as fh:
            json.dump({"pid": pid, "list_sha256": digest}, fh)
        return os.path.join(directory, digest + ".lock"), os.path.join(directory, digest + ".log")

    def test_a_ps_that_cannot_answer_is_unknown_liveness_and_blocks(self):
        # the landing sandbox denies ps (2026-10-04): unknown never reclaims the lock and never spawns a second worker
        lock, log = self.lock_with(os.getpid())
        denied = lambda argv, cwd=None, env=None: (_ for _ in ()).throw(PermissionError(1, "Operation not permitted", "ps"))

        def exits_oddly(argv, cwd=None, env=None):
            return Proc(1, "", "ps: Operation not permitted") if argv[0] == "ps" else Proc(2, "", "")

        for failing in (denied, exits_oddly):
            with self.assertRaises(merge_gate.MergeNoData) as caught:
                merge_precompute.start_background(self.clone, self.list, self.env, runner=failing)
            self.assertIn("ps cannot say", str(caught.exception))
            self.assertEqual(json.load(open(lock))["pid"], os.getpid(), "the lock was reclaimed on unknown liveness")
            self.assertFalse(os.path.exists(log), "a worker was spawned on unknown liveness")
        self.assertIsNone(merge_precompute._pid_is_worker(1, exits_oddly))
        dead = lambda argv, cwd=None, env=None: Proc(1, "", "")   # ps's own answer for a pid with no row
        self.assertIs(merge_precompute._pid_is_worker(1, dead), False)
        other = lambda argv, cwd=None, env=None: Proc(0, "sleep 30\n", "")
        self.assertIs(merge_precompute._pid_is_worker(1, other), False)

    def test_a_second_start_finds_the_same_worker(self):
        # M-MG1-C-4: a second worker turns this red
        if not PS_RUNS:
            self.skipTest(NO_PS)
        self.slow()
        first = self.start()
        time.sleep(1.0)
        second = merge_precompute.start_background(self.clone, self.list, self.env)
        self.assertEqual(first, second)
        self.assertEqual(len(self.workers()), 1, self.workers())
        self.wait_done()

    def test_a_lock_whose_pid_is_dead_is_reclaimed(self):
        # M-MG1-C-6: never reclaiming a dead pid turns this red
        if not PS_RUNS:
            self.skipTest(NO_PS)
        dead = subprocess.Popen(["sh", "-c", "true"])
        dead.wait()
        lock, _log = self.lock_with(dead.pid)
        pid = self.start()
        self.assertNotEqual(pid, dead.pid)
        self.assertEqual(json.load(open(lock))["pid"], pid)
        self.wait_done()

    def test_a_lock_whose_pid_runs_another_command_is_reclaimed(self):
        if not PS_RUNS:
            self.skipTest(NO_PS)
        other = subprocess.Popen(["sleep", "30"])
        self.addCleanup(other.kill)
        self.lock_with(other.pid)
        pid = self.start()
        self.assertNotEqual(pid, other.pid)
        self.wait_done()

    def test_the_pins_command_reads_github_writes_the_list_and_starts_the_worker(self):
        os.makedirs(os.path.join(self.fix, "bin"))
        stub = os.path.join(self.fix, "bin", "gh")
        open(stub, "w").write(GH_STUB)
        os.chmod(stub, 0o755)
        for n in ("1", "2"):
            open(os.path.join(self.fix, "pr-%s.json" % n), "w").write(json.dumps(
                {"state": "OPEN", "baseRefName": "main", "headRefName": "pr" + n, "mergeable": "MERGEABLE",
                 "headRefOid": self.heads[n]}))
        env = _clean_env({"FIX": self.fix, "PATH": os.path.join(self.fix, "bin") + os.pathsep + self.env["PATH"]})
        written = os.path.join(self.fix, "pinned")
        tool = [sys.executable, "-B", os.path.join(self.clone, "scripts", "merge_precompute.py")]
        r = subprocess.run(tool + ["pins", self.clone, written, "1", "2"], env=env, capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(open(written).read(), "1 %s\n2 %s\n" % (self.heads["1"], self.heads["2"]))
        self.started.append(int(re.search(r"WORKER pid (\d+)", r.stdout).group(1)))
        self.list = written
        self.assertEqual(self.wait_done()["status"], "DONE")
        # a PR still computing its mergeable keeps the WHOLE list unwritten (exit 2), and starts no worker
        open(os.path.join(self.fix, "pr-2.json"), "w").write(json.dumps(
            {"state": "OPEN", "baseRefName": "main", "headRefName": "pr2", "mergeable": "UNKNOWN",
             "headRefOid": self.heads["2"]}))
        other = os.path.join(self.fix, "pinned-2")
        r = subprocess.run(tool + ["pins", self.clone, other, "1", "2"], env=env, capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(other))
        self.assertIn("#2", r.stderr)

    def test_the_worker_is_the_clones_own_tool_never_this_modules_neighbour(self):
        # measured 2026-10-04: a worker taken from beside the imported module gated THIS repository and wrote under
        # its git common dir; the clone that lacks the tool is NO-DATA, and a started worker names the clone's copy
        if not PS_RUNS:
            self.skipTest(NO_PS)
        os.remove(os.path.join(self.clone, "scripts", "gate_merge_seq.sh"))
        with self.assertRaises(merge_gate.MergeNoData):
            merge_precompute.start_background(self.clone, self.list, self.env)
        self.assertEqual(self.workers(), [])
        shutil.copy(os.path.join(HERE, "gate_merge_seq.sh"), os.path.join(self.clone, "scripts", "gate_merge_seq.sh"))
        self.slow()
        self.start()
        time.sleep(1.0)
        workers = self.workers()
        self.assertEqual(len(workers), 1, workers)
        self.assertIn(os.path.join(os.path.realpath(self.clone), "scripts", "merge_precompute.py"), workers[0])
        self.wait_done()

    def test_a_steered_environment_refuses_every_command(self):
        tool = [sys.executable, "-B", os.path.join(self.clone, "scripts", "merge_precompute.py")]
        for name in ("MERGE_GATE_LEDGER", "MERGE_ALL_X", "GIT_DIR", "PR_PARK_REPO"):
            r = subprocess.run(tool + ["start", self.clone, self.list], env=_clean_env({name: "x"}),
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 2, name)
            self.assertIn(name, r.stderr)
        # a spawn opens the worker's log beside the ledger before anything else: none was opened (needs no ps)
        directory = merge_precompute._precompute_dir(self.clone, self.env)
        self.assertEqual([n for n in os.listdir(directory) if n.endswith(".log")], [])


GH_TSV_STUB = r"""#!/bin/sh
# the stand-in gh for merge_verified.sh's dry run: pull request state as the tab separated line in $FIX/pr-<n>.tsv.
# It finds its fixture from its OWN path ($FIX/bin/gh): merge_verified.sh starts itself in a clean environment (review
# 2026-10-06), so no name of the test's crosses into it.
FIX="${FIX:-${0%/bin/gh}}"
echo "$*" >> "$FIX/gh.log"
case "$1 $2" in
  "pr view") cat "$FIX/pr-$3.tsv" ;;
  *) echo "gh stub: unhandled $*" >&2; exit 3 ;;
esac
"""


class TestMergeVerifiedWait(Fixture):
    """merge_verified.sh --wait SECONDS: the row is waited for through merge_precompute.wait_ready. A dry run only,
    with a stand-in gh first on PATH: nothing here merges, and no case needs a terminal."""

    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.fix, "bin"))
        stub = os.path.join(self.fix, "bin", "gh")
        open(stub, "w").write(GH_TSV_STUB)
        os.chmod(stub, 0o755)
        for n in ("1", "2"):
            open(os.path.join(self.fix, "pr-%s.tsv" % n), "w").write("OPEN\tmain\tMERGEABLE\t%s\tpr%s\n" % (self.heads[n], n))

    def run_tool(self, *args):
        env = _clean_env({"FIX": self.fix, "PATH": os.path.join(self.fix, "bin") + os.pathsep + self.env["PATH"]})
        r = subprocess.run(["sh", os.path.join(self.clone, "scripts", "merge_verified.sh")] + list(args),
                           cwd=self.clone, env=env, capture_output=True, text=True, timeout=120,
                           stdin=subprocess.DEVNULL)
        log = os.path.join(self.fix, "gh.log")
        merges = open(log).read().count("pr merge") if os.path.exists(log) else 0
        self.assertEqual(merges, 0, "a dry run reached pr merge")
        return r.returncode, r.stdout + r.stderr

    def test_the_shell_tool_waits_through_the_one_gate_definition(self):
        text = open(os.path.join(HERE, "merge_verified.sh"), encoding="utf-8").read()
        self.assertIn("--wait)", text)
        self.assertIn('"$PY_PRE" wait "$CLONE" "$tree" "$WAIT"', text)
        self.assertIn('python3 "$PY_GATE" verify', text)

    def test_a_dry_run_with_wait_times_out_as_no_data_without_a_row(self):
        started = time.monotonic()
        rc, out = self.run_tool("--dry-run", "--wait", "1", self.list)
        self.assertEqual(rc, 1, out)
        self.assertIn("NO-DATA", out)
        self.assertIn("timed out", out)
        self.assertGreaterEqual(time.monotonic() - started, 0.9)
        self.assertNotIn("WOULD MERGE", out)

    def test_a_dry_run_with_wait_judges_a_row_that_arrived(self):
        tree1 = self.tree(self.main, self.heads["1"])
        self.sign_pass(tree1)
        self.sign_pass(self.tree(self.chained_base(tree1, self.main), self.heads["2"]))
        rc, out = self.run_tool("--dry-run", "--wait", "5", self.list)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count("WOULD MERGE"), 2, out)

    def test_a_wait_that_is_not_a_whole_number_is_refused(self):
        for bad in ("abc", "-1", "1.5", ""):
            rc, out = self.run_tool("--dry-run", "--wait", bad, self.list)
            self.assertEqual(rc, 2, out)
            self.assertIn("NO-DATA", out)
        rc, out = self.run_tool("--dry-run", "--wait")
        self.assertEqual(rc, 2, out)


if __name__ == "__main__":
    unittest.main()
