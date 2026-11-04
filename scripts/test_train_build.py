#!/usr/bin/env python3
"""Tests for the train in scripts/merge_queue.py (FLOW1.a): one train, one branch, one whole gate.

TESTED AT THE ENTRY POINT. Every case runs `merge_queue.py train ...` as a driver would, in a fixture git repository
(a bare hub, a clone of it named `hub`, a mover clone that changes the hub while the gate runs). The whole gate is a
STUB scripts/required_fast.sh committed into the fixture: it counts its own runs, fails only on a tree carrying
fail.txt, and runs a one-shot hook the case wrote (to move the line or a member while the gate is running). The real
required_fast.sh is never run. Review records are written through merge_gate.append_review_record, the one writer,
over whole gate rows written through gate_ledger.append_ledger; no row is typed by hand.

ONE GUARD PER CASE, each shown red under its own mutation (the FLOW1.a evidence folder holds the sweep's output):
  an unreviewed head enters the train          -> test_an_unreviewed_head_does_not_ride
  the overlap check is removed                  -> test_overlapping_write_sets_never_ride_together
  a head that moved is kept                     -> test_a_member_whose_head_moved_before_the_train_leaves
  a head that moves during the gate is kept     -> test_a_member_that_moves_during_the_gate_leaves_and_the_gate_answers_again
  a line move does not restart the gate         -> test_a_line_move_rebuilds_the_train_and_the_gate_answers_again
  the base is ignored, or not checked           -> the dev line case and test_a_base_off_the_allow_list_is_refused_before_anything_runs
  the train PR review step is not printed       -> test_the_review_step_for_the_train_head_is_printed_and_works
  a train branch is overwritten                 -> the three train branch cases (checked out, not ours, appears during the gate)
  a member branch is a refspec                  -> test_a_refspec_in_a_member_branch_writes_no_ref_and_the_member_leaves
  the train's own restart cap                   -> test_a_train_that_keeps_moving_after_each_gate_ends_at_the_trains_own_cap
  a conflict between members                    -> test_conflicting_members_second_left_out
  a member that edits a gate check              -> test_a_member_that_edits_a_gate_check_is_left_out_by_name

TRAIN_BUILD_SRC_merge_queue_py (a test seam; the same for every copied tool, with . written as _) names another
file to copy into the fixture, so a mutated copy is run without touching the real one.

Run: python3 -B scripts/test_train_build.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import gate_ledger  # noqa: E402
import merge_gate  # noqa: E402
import merge_precompute  # noqa: E402
import export_public  # noqa: E402

# every module merge_queue.py train imports, plus the gate launcher; copied under the fixture clone's scripts/
# because gate_merge_seq.sh finds its clone from its own directory
TOOL_FILES = ("graph_loop.py", "integrate.py", "worktree_lane.py", "claim_store.py", "fault_barrier.py", "journal.py",
              "brother_paths.py", "gate_ledger.py", "merge_gate.py", "merge_precompute.py", "tmp_sandbox.py",
              "heavy_slot.py", "merge_pins.py", "resource_gate.py", "gate_merge_seq.sh")
OWNS_STUB = "print('PASS: stand-in owns check, the throwaway tree has no scripts/loop component')\n"
DEV = "plan/1.1.9-line-2026-10-25"   # on merge_gate.ALLOWED_BASES: the only named dev line the tools know
GIT = "git -c user.email=t@t -c user.name=t -c commit.gpgsign=false "
SCRATCH_ROOT = os.path.join(export_public.scratch_root(), "flow1a")


def _scratch():
    try:
        os.makedirs(SCRATCH_ROOT, exist_ok=True)
        return SCRATCH_ROOT
    except OSError:
        return None


def put(path, text):
    with open(path, "w") as fh:
        fh.write(text)


def get(path):
    with open(path) as fh:
        return fh.read()


def _clean_env(tmp):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("MERGE_", "GIT_", "GH_", "GITHUB_", "BROTHER_")) and k != "XDG_CONFIG_HOME"}
    env["BROTHER_HEAVY_SLOT"] = "off"
    env["TMPDIR"] = tmp
    return env


class Train(unittest.TestCase):
    """A throwaway hub with a line of two commits, a clone named `hub` carrying the tools, members on their branches."""

    def git(self, cwd, *a):
        r = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false"] + list(a),
                           cwd=cwd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def setUp(self):
        self.fix = tempfile.mkdtemp(prefix="train-", dir=_scratch())
        self.addCleanup(shutil.rmtree, self.fix, True)
        self.tmp = os.path.join(self.fix, "tmp")
        os.makedirs(self.tmp)
        self.hub = os.path.join(self.fix, "hub.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.hub], check=True)
        self.clone = os.path.join(self.fix, "clone")
        subprocess.run(["git", "clone", "-q", "-o", "hub", self.hub, self.clone], check=True, capture_output=True)
        for key, value in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
            subprocess.run(["git", "-C", self.clone, "config", key, value], check=True, capture_output=True)
        self.runs = os.path.join(self.fix, "gate-runs")
        self.hook = os.path.join(self.fix, "hook.sh")
        os.makedirs(os.path.join(self.clone, "scripts"))
        with open(os.path.join(self.clone, "scripts", "required_fast.sh"), "w") as fh:
            fh.write("#!/bin/sh\n"
                     "echo run >> '%s'\n"
                     "if [ -f fail.txt ]; then echo 'x FAIL'; echo 'pass 0   fail 1   no-data 0'; echo 'FAILED: x'; exit 1; fi\n"
                     "if [ -f '%s' ]; then sh '%s'; fi\n"
                     "sh scripts/check_one.sh || exit 1\n"
                     "echo 'pass 1   fail 0   no-data 0'\n" % (self.runs, self.hook, self.hook))
        put(os.path.join(self.clone, "scripts", "check_one.sh"), "exit 0\n")   # a gate check the stub runs, so the closure names it
        with open(os.path.join(self.clone, merge_gate.OWNS_SCRIPT), "w") as fh:
            fh.write(OWNS_STUB)
        put(os.path.join(self.clone, "root.txt"), "root\n")
        self.git(self.clone, "add", "-A")
        self.git(self.clone, "commit", "-qm", "root")
        put(os.path.join(self.clone, "line.txt"), "line\n")   # a second commit: a root commit proves nothing as a review line
        self.git(self.clone, "add", "-A")
        self.git(self.clone, "commit", "-qm", "line")
        self.git(self.clone, "push", "-q", "hub", "HEAD:main")
        self.line0 = self.git(self.clone, "rev-parse", "HEAD")
        for name in ("merge_queue.py",) + TOOL_FILES:   # TRAIN_BUILD_SRC_<name with . as _> swaps in a mutated copy
            src = os.environ.get("TRAIN_BUILD_SRC_" + name.replace(".", "_")) or os.path.join(HERE, name)
            shutil.copy(src, os.path.join(self.clone, "scripts", name))
        self.mover = os.path.join(self.fix, "mover")
        subprocess.run(["git", "clone", "-q", "-o", "hub", self.hub, self.mover], check=True, capture_output=True)
        self.members = {}
        self.line = self.line0   # the base commit members branch from and are reviewed against

    def dev_line(self):
        """A named dev line on merge_gate.ALLOWED_BASES, one commit ahead of main; later members branch from it."""
        self.git(self.clone, "checkout", "-q", "-B", "dev-work", self.line0)
        put(os.path.join(self.clone, "dev.txt"), "dev\n")
        put(os.path.join(self.clone, "scripts", "check_one.sh"), "exit 0\n# the dev line edits a gate check\n")   # so a verdict read against main would be CHECKS-EDITED
        self.git(self.clone, "add", "dev.txt", "scripts/check_one.sh")
        self.git(self.clone, "commit", "-qm", "dev line")
        self.git(self.clone, "push", "-q", "hub", "HEAD:refs/heads/" + DEV)
        self.line = self.git(self.clone, "rev-parse", "HEAD")
        self.git(self.clone, "checkout", "-q", "--detach", self.line0)

    # -- members, reviews, the hub ------------------------------------------------------------------------------
    def member(self, mid, pr, path, owns=None, reviewed=True, verdict="PASS", content="x\n"):
        """A branch off the line with one new file, pushed to the hub; reviewed through the one writer unless told not."""
        branch = "feat/%s" % mid
        self.git(self.clone, "checkout", "-q", "-B", "work-" + mid, self.line)
        full = os.path.join(self.clone, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        put(full, content)
        self.git(self.clone, "add", path)
        self.git(self.clone, "commit", "-qm", "member " + mid)
        head = self.git(self.clone, "rev-parse", "HEAD")
        self.git(self.clone, "push", "-q", "hub", "HEAD:refs/heads/" + branch)
        self.git(self.clone, "checkout", "-q", "--detach", self.line0)
        if reviewed:
            self.review(pr, head, verdict)
        self.members[mid] = {"id": mid, "pr": pr, "head": head, "branch": branch,
                             "owns": [path] if owns is None else owns, "check_cmd": ["true"]}
        return head

    def review(self, pr, head, verdict="PASS"):
        self.whole_gate_row(head)
        merge_gate.append_review_record(self.clone, str(pr), head, self.line, "reviewer-r", verdict,
                                        runner=merge_precompute.default_runner)

    def whole_gate_row(self, head):
        """The whole gate PASS row of head's tree, through gate_ledger.append_ledger (the real gate writes it for a train)."""
        tree = self.git(self.clone, "rev-parse", head + "^{tree}")
        common = self.git(self.clone, "rev-parse", "--path-format=absolute", "--git-common-dir")
        now = time.time()
        gate_ledger.append_ledger(os.path.join(common, gate_ledger.WHOLE_GATE_FILE), {
            "schema": 1, "cut_id": gate_ledger.NO_CUT, "cut_version": "UNKNOWN", "gate_name": gate_ledger.WHOLE_GATE,
            "gate_argv": ["sh", "scripts/required_fast.sh"], "status": "PASS", "exit_code": 0, "started_unix": now,
            "ended_unix": now, "duration_s": 0, "worktree_key": "flow1a-fixture", "tree_sha": tree,
            "inputs_hash": None, "code_hash": "1" * 64, "tool_versions": {}, "verdict_source": "full",
            "cache_key": None, "deadline_unix": None})

    def hub_script(self, script):
        """`script` run in the mover clone, the way another actor changes the hub, with git carrying an identity."""
        return "cd '%s' && %s" % (self.mover, script.replace("git ", GIT))

    def move_line(self):
        return ("git fetch -q hub main && git checkout -q -B mv FETCH_HEAD && echo moved > moved.txt && git add moved.txt "
                "&& git commit -qm moved && git push -q hub mv:refs/heads/main")

    def move_member(self, mid):
        return ("git fetch -q hub feat/%s && git checkout -q -B mm FETCH_HEAD && echo more > more-%s.txt && git add more-%s.txt "
                "&& git commit -qm more && git push -q hub mm:refs/heads/feat/%s" % (mid, mid, mid, mid))

    def during_gate(self, *scripts):
        """Write the one-shot hook the stub gate runs on its first run: the hub changes while the gate is running."""
        with open(self.hook, "w") as fh:
            fh.write("set -e\nrm -f '%s'\n" % self.hook + "".join(self.hub_script(script) + "\n" for script in scripts))

    def during_gates(self, by_run):
        """A hook that stays: on gate run N it runs by_run[N] (the stub counts its runs before it calls the hook)."""
        with open(self.hook, "w") as fh:
            fh.write("n=$(wc -l < '%s' | tr -d ' ')\ncase $n in\n" % self.runs)
            for n, script in sorted(by_run.items()):
                fh.write("%d) (set -e; %s) ;;\n" % (n, self.hub_script(script)))
            fh.write("esac\n")

    def run_hub(self, script):
        subprocess.run(["sh", "-c", "set -e; " + self.hub_script(script)], check=True, capture_output=True)

    # -- the entry point ---------------------------------------------------------------------------------------
    def train(self, *ids, name="T1", pr=None, base=None):
        listing = os.path.join(self.fix, "members.json")
        with open(listing, "w") as fh:
            json.dump([self.members[i] for i in ids], fh)
        self.body = os.path.join(self.fix, "body.md")
        argv = [sys.executable, "-B", os.path.join(self.clone, "scripts", "merge_queue.py"), "train",
                "--members", listing, "--name", name, "--body", self.body] + (["--pr", pr] if pr else []) + \
               (["--base", base] if base else [])
        r = subprocess.run(argv, cwd=self.clone, env=_clean_env(self.tmp), stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=600)
        self.out = r.stdout + r.stderr
        return r.returncode

    def gate_runs(self):
        return len(get(self.runs).split()) if os.path.exists(self.runs) else 0

    def tip(self):
        return self.git(self.clone, "ls-remote", "hub", "refs/heads/main").split()[0]

    def train_branch(self, name="T1"):
        r = subprocess.run(["git", "-C", self.clone, "rev-parse", "--verify", "--quiet", "refs/heads/train/%s" % name],
                           capture_output=True, text=True)
        return r.stdout.strip() or None

    def files(self, rev):
        return set(self.git(self.clone, "ls-tree", "-r", "--name-only", rev).splitlines())

    def ancestor(self, older, newer):
        return subprocess.run(["git", "-C", self.clone, "merge-base", "--is-ancestor", older, newer]).returncode == 0


class OneTrainOneGate(Train):
    def test_reviewed_disjoint_members_ride_one_train_with_one_gate(self):
        a = self.member("a", 11, "a.txt")
        b = self.member("b", 12, "b.txt")
        hub_before = self.git(self.clone, "ls-remote", "hub")
        self.assertEqual(self.train("a", "b", pr="77"), 0, self.out)
        head = self.train_branch()
        self.assertIsNotNone(head, self.out)
        self.assertEqual(self.gate_runs(), 1, "one train must be gated once, not once per member")
        for h in (a, b, self.line0):
            self.assertTrue(self.ancestor(h, head), "%s is not on the train" % h)
        self.assertLessEqual({"a.txt", "b.txt"}, self.files(head))
        tree = self.git(self.clone, "rev-parse", head + "^{tree}")
        self.assertEqual(merge_gate.verify_tree(self.clone, tree, _clean_env(self.tmp), merge_precompute.default_runner)[0], "PASS")
        self.assertEqual(self.git(self.clone, "ls-remote", "hub"), hub_before, "the train pushed something")
        body = get(self.body)
        for needle in (a, b, self.git(self.clone, "rev-parse", a + "^{tree}"), self.git(self.clone, "rev-parse", b + "^{tree}"),
                       "reviewer-r", "true", head, tree, "log sha256", "#11", "#12"):
            self.assertIn(needle, body)
        self.assertIn("MERGE: sh scripts/merge_verified.sh --base main %s.merge-list" % self.body, self.out)
        self.assertEqual(get(self.body + ".merge-list"), "77 %s\n" % head)

    def test_an_unreviewed_head_does_not_ride(self):
        a = self.member("a", 11, "a.txt")
        self.member("u", 12, "u.txt", reviewed=False)
        self.member("f", 13, "f.txt", verdict="FAIL")   # a different shape of unreviewed: the newest record is a FAIL
        self.assertEqual(self.train("a", "u", "f"), 0, self.out)
        head = self.train_branch()
        self.assertEqual(self.files(head) - self.files(self.line0), {"a.txt"}, "an unreviewed head entered the train")
        self.assertTrue(self.ancestor(a, head))
        self.assertIn("NOT ABOARD #12 u: REFUSED", self.out)
        self.assertIn("NOT ABOARD #13 f: REFUSED", self.out)
        self.assertIn("Not on this train", get(self.body))

    def test_nobody_reviewed_is_an_empty_train_and_writes_nothing(self):
        self.member("u", 12, "u.txt", reviewed=False)
        self.assertEqual(self.train("u"), 1, self.out)
        self.assertIn("EMPTY TRAIN", self.out)
        self.assertIsNone(self.train_branch())
        self.assertFalse(os.path.exists(self.body))
        self.assertEqual(self.gate_runs(), 0, "a gate ran for a train nobody could ride")

    def test_overlapping_write_sets_never_ride_together(self):
        a = self.member("a", 11, "a.txt")
        self.member("c", 12, "c.txt", owns=["a.txt"])            # the same file as a
        d = self.member("d", 13, "sub/d.txt", owns=["sub/"])      # a directory
        self.member("e", 14, "sub/e.txt", owns=["sub/e.txt"])     # inside that directory
        self.assertEqual(self.train("a", "c", "d", "e"), 0, self.out)
        head = self.train_branch()
        self.assertEqual(self.files(head) - self.files(self.line0), {"a.txt", "sub/d.txt"}, "two members owning one path rode together")
        self.assertTrue(self.ancestor(a, head) and self.ancestor(d, head))
        self.assertIn("NOT ABOARD #12 c: DEFERRED", self.out)
        self.assertIn("NOT ABOARD #14 e: DEFERRED", self.out)

    def test_a_member_whose_head_moved_before_the_train_leaves(self):
        a = self.member("a", 11, "a.txt")
        b = self.member("b", 12, "b.txt")
        self.run_hub(self.move_member("a"))   # the branch now holds a commit nobody reviewed
        self.assertEqual(self.train("a", "b"), 0, self.out)
        head = self.train_branch()
        self.assertEqual(self.files(head) - self.files(self.line0), {"b.txt"}, "a head that moved stayed on the train")
        self.assertFalse(self.ancestor(a, head))
        self.assertTrue(self.ancestor(b, head))
        self.assertIn("NOT ABOARD #11 a: LEFT", self.out)

    def test_a_member_that_moves_during_the_gate_leaves_and_the_gate_answers_again(self):
        self.member("a", 11, "a.txt")
        b = self.member("b", 12, "b.txt")
        self.during_gate(self.move_member("a"))
        self.assertEqual(self.train("a", "b"), 0, self.out)
        head = self.train_branch()
        self.assertEqual(self.files(head) - self.files(self.line0), {"b.txt"}, "a member that moved mid gate stayed on the train")
        self.assertTrue(self.ancestor(b, head))
        self.assertEqual(self.gate_runs(), 2, "the train without the moved member must be gated again")
        self.assertIn("1 restart(s)", self.out)

    def test_a_line_move_rebuilds_the_train_and_the_gate_answers_again(self):
        a = self.member("a", 11, "a.txt")
        b = self.member("b", 12, "b.txt")
        self.during_gate(self.move_line())
        self.assertEqual(self.train("a", "b"), 0, self.out)
        head, line1 = self.train_branch(), self.tip()
        self.assertNotEqual(line1, self.line0, "the fixture hook did not move the line")
        self.assertTrue(self.ancestor(line1, head), "the train head does not carry the moved line")
        self.assertTrue(self.ancestor(a, head) and self.ancestor(b, head))
        self.assertEqual(self.gate_runs(), 2, "a moved line must be gated again, and only once more")
        tree = self.git(self.clone, "rev-parse", head + "^{tree}")
        self.assertEqual(merge_gate.verify_tree(self.clone, tree, _clean_env(self.tmp), merge_precompute.default_runner)[0], "PASS")
        self.assertIn("1 restart(s)", self.out)

    def test_a_failing_gate_is_not_ready(self):
        self.member("a", 11, "a.txt")
        self.member("x", 12, "fail.txt")
        self.assertEqual(self.train("a", "x"), 1, self.out)
        self.assertIn("FAIL", self.out)
        self.assertIsNone(self.train_branch(), "a branch was written for a train whose gate failed")
        self.assertFalse(os.path.exists(self.body))

    def test_a_pin_without_a_head_is_refused_not_dropped(self):
        self.member("a", 11, "a.txt")
        self.members["h"] = {"id": "h", "branch": "feat/h", "owns": ["h.txt"], "check_cmd": ["true"]}
        self.assertEqual(self.train("a", "h"), 0, self.out)
        self.assertIn("NOT ABOARD #? h: REFUSED", self.out)

    # -- review round 1 (F1 to F7) -----------------------------------------------------------------------------
    def test_dev_line_members_assemble_gate_and_print_the_base_command(self):
        self.dev_line()
        a = self.member("a", 11, "a.txt")
        b = self.member("b", 12, "b.txt")
        self.assertEqual(self.train("a", "b", pr="77", base=DEV), 0, self.out)
        head = self.train_branch()
        self.assertTrue(self.ancestor(self.line, head), "the train does not stand on the dev line tip")
        self.assertTrue(self.ancestor(a, head) and self.ancestor(b, head))
        self.assertIn("dev.txt", self.files(head))
        self.assertEqual(self.gate_runs(), 1)
        tree = self.git(self.clone, "rev-parse", head + "^{tree}")
        self.assertEqual(merge_gate.verify_tree(self.clone, tree, _clean_env(self.tmp), merge_precompute.default_runner, base=DEV)[0], "PASS")
        self.assertIn("MERGE: sh scripts/merge_verified.sh --base %s %s.merge-list" % (DEV, self.body), self.out)
        self.assertIn("onto %s " % DEV, get(self.body))

    def test_a_base_off_the_allow_list_is_refused_before_anything_runs(self):
        self.member("a", 11, "a.txt")
        self.git(self.clone, "push", "-q", "hub", "%s:refs/heads/side-line" % self.line0)   # a real branch, on no list
        self.assertEqual(self.train("a", base="side-line"), 2, self.out)
        self.assertIn("is not a named merge base", self.out)
        self.assertNotIn("train gate", self.out, "the refusal must come before any gate, not from the gate worker")
        self.assertEqual(self.gate_runs(), 0)
        self.assertIsNone(self.train_branch())

    def test_the_review_step_for_the_train_head_is_printed_and_works(self):
        self.member("a", 11, "a.txt")
        self.assertEqual(self.train("a"), 0, self.out)
        steps = [l for l in self.out.splitlines() if l.startswith("STEP 2")]
        self.assertEqual(len(steps), 1, self.out)
        self.assertIn("<train pull request number>", steps[0])
        self.assertEqual(self.train("a", pr="77"), 0, self.out)
        head = self.train_branch()
        step = [l for l in self.out.splitlines() if l.startswith("STEP 2")][0]
        self.assertIn(" 77 %s %s " % (head, self.tip()), step)
        # the printed command, run as printed with a reviewer who wrote nothing, over the whole gate row the real gate writes
        import shlex
        self.whole_gate_row(head)
        argv = shlex.split(step.split("it is NOT written for you): ", 1)[1])
        argv[argv.index("<reviewer>")] = "reviewer-r"
        r = subprocess.run([sys.executable, "-B"] + argv[1:], cwd=self.clone, env=_clean_env(self.tmp), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PASS", merge_gate.review_check(self.clone, "77", head, self.tip(), runner=merge_precompute.default_runner))

    def test_a_checked_out_train_branch_is_refused_and_untouched(self):
        self.member("a", 11, "a.txt")
        wt = os.path.join(self.fix, "wt-train")
        self.git(self.clone, "worktree", "add", "-q", "-b", "train/T1", wt, self.line0)
        put(os.path.join(wt, "precious.txt"), "unpushed\n")
        self.git(wt, "add", "precious.txt")
        self.git(wt, "commit", "-qm", "unpushed work")
        precious = self.git(wt, "rev-parse", "HEAD")
        self.assertEqual(self.train("a"), 2, self.out)
        self.assertIn("checked out in a worktree", self.out)
        self.assertEqual(self.train_branch(), precious, "the branch moved")
        self.assertEqual(self.gate_runs(), 0, "a gate ran for a train that could not be written")

    def test_a_train_branch_this_tool_did_not_write_is_refused_and_one_it_wrote_is_rebuilt(self):
        self.member("a", 11, "a.txt")
        self.git(self.clone, "branch", "train/T1", self.line0)   # a person's branch, not checked out anywhere
        self.assertEqual(self.train("a"), 2, self.out)
        self.assertIn("did not write that tip", self.out)
        self.assertEqual(self.train_branch(), self.line0)
        self.git(self.clone, "branch", "-D", "train/T1")
        self.assertEqual(self.train("a"), 0, self.out)       # first build: the branch did not exist
        first = self.train_branch()
        self.member("b", 12, "b.txt")
        self.assertEqual(self.train("a", "b"), 0, self.out)  # rebuild of its own branch
        self.assertNotEqual(self.train_branch(), first)

    def test_a_train_branch_that_appears_during_the_gate_is_not_overwritten(self):
        self.member("a", 11, "a.txt")
        with open(self.hook, "w") as fh:   # someone makes train/T1 in the clone while the gate runs
            fh.write("rm -f '%s'\n%sbranch train/T1 %s\n" % (self.hook, "git -C '%s' " % self.clone, self.line0))
        self.assertEqual(self.train("a"), 2, self.out)
        self.assertEqual(self.train_branch(), self.line0, "a branch made during the gate was overwritten")

    def test_a_refspec_in_a_member_branch_writes_no_ref_and_the_member_leaves(self):
        self.member("a", 11, "a.txt")
        self.member("b", 12, "b.txt")
        self.members["a"]["branch"] = "feat/a:refs/heads/victim"
        self.members["b"]["branch"] = "+feat/b"
        self.member("c", 13, "c.txt")
        self.members["c"]["branch"] = "feat/*"   # a glob refspec would fetch every feat/ branch into private refs
        self.assertEqual(self.train("a", "b", "c"), 1, self.out)
        self.assertEqual(self.out.count(": LEFT"), 3, self.out)
        self.assertEqual(self.git(self.clone, "for-each-ref", "refs/train-fetch"), "", "a refspec left private refs behind")
        for ref in ("refs/heads/victim", "refs/heads/feat/b"):
            r = subprocess.run(["git", "-C", self.clone, "rev-parse", "--verify", "--quiet", ref], capture_output=True)
            self.assertNotEqual(r.returncode, 0, "%s was written by a refspec" % ref)

    def test_a_train_that_keeps_moving_after_each_gate_ends_at_the_trains_own_cap(self):
        for mid, pr in (("a", 11), ("b", 12), ("c", 13), ("d", 14), ("e", 15)):
            self.member(mid, pr, mid + ".txt")
        self.during_gates({1: self.move_member("a"), 2: self.move_member("b"), 3: self.move_member("c"), 4: self.move_member("d")})
        self.assertEqual(self.train("a", "b", "c", "d", "e"), 2, self.out)
        self.assertIn("STALE-BASE", self.out)
        self.assertIn("after a gate", self.out, "the cap that fired is not the train's own")
        self.assertEqual(self.gate_runs(), 4)
        self.assertIsNone(self.train_branch())

    def test_conflicting_members_second_left_out(self):
        p = self.member("p", 11, "shared.txt", owns=["p.txt"], content="one\n")   # declared write sets lie: both write shared.txt
        self.member("q", 12, "shared.txt", owns=["q.txt"], content="two\n")
        self.assertEqual(self.train("p", "q"), 0, self.out)
        head = self.train_branch()
        self.assertTrue(self.ancestor(p, head))
        self.assertEqual(self.git(self.clone, "show", head + ":shared.txt"), "one")
        self.assertIn("NOT ABOARD #12 q: CONFLICT", self.out)

    def test_a_member_that_edits_a_gate_check_is_left_out_by_name(self):
        a = self.member("a", 11, "a.txt")
        self.member("e", 12, "scripts/check_one.sh", content="exit 0\n# edited\n")
        self.assertEqual(self.train("a", "e"), 0, self.out)
        self.assertTrue(self.ancestor(a, self.train_branch()))
        self.assertIn("NOT ABOARD #12 e: REFUSED it edits gate checks (scripts/check_one.sh)", self.out)
        self.assertEqual(self.gate_runs(), 1)


if __name__ == "__main__":
    unittest.main()
