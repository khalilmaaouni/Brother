#!/usr/bin/env python3
"""Tests for scripts/merge_pins.py and the frozen-head path of scripts/merge_verified.sh (MG1.d).

HERMETIC BY CONSTRUCTION. A throwaway bare repository is the hub, a stand-in `gh` (a shell script first on the tool's
PATH, or a runner answering from a dict) models pull requests as files whose head sha is read from the hub itself, so
a branch that advances moves its pull request's head the way GitHub's would, and its `pr merge` refuses a
--match-head-commit that is not the head, the way GitHub's does. Nothing here reads the network, the real hub, or a
home folder. A real run of the tool needs a terminal, so it gets a pseudo terminal from os.openpty(); where the
sandbox denies one, those cases skip as NO-DATA.

WHAT IT PROVES, one guard per case: a run line that advanced after pinning still merges the pinned sha through its
frozen-head pull request with the gated tree, a static PR is merged by the same gh path, a force pushed run line is
refused, a main whose protection cannot be read refuses the real run before any write, a dry run prints `will freeze`
and writes nothing, the frozen branch is never force pushed, the pull request is opened from the frozen branch and its
number is never guessed, and the prompt reads the terminal alone. The spec's mutations M-MG1-D-1 to M-MG1-D-10 are
applied by hand to the module or the shell tool and each turns a named case here red.

Run: python3 -B scripts/test_merge_pins.py
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import merge_gate  # noqa: E402
import merge_pins  # noqa: E402

TOOL_FILES = ("merge_verified.sh", "merge_gate.py", "tmp_sandbox.py", "merge_pins.py", "merge_reach.py",
              "merge_precompute.py")
RUN_LINE = "loop/run-2026-09-30"
NO_PTY = "NO-DATA: no pseudo terminal here, so the terminal prompt cannot be exercised"

GH_STUB = r"""#!/bin/sh
# the stand-in gh: a pull request is $FIX/pr-<n> holding its head branch; the head sha is read from the hub itself, so
# a branch that advances moves the pull request's head the way GitHub's would; a merge is done for real in the hub and
# refused when --match-head-commit is not the head, the way GitHub's is. Reached through merge_verified.sh it finds its
# fixture from its OWN path ($FIX/bin/gh): that tool starts itself in a clean environment (review 2026-10-06), so no
# name of the test's crosses into it. Called directly with FIX set, FIX is used as given.
FIX="${FIX:-${0%/bin/gh}}"
echo "$*" >> "$FIX/gh.log"
HUB="$FIX/hub.git"
oid() { git --git-dir="$HUB" rev-parse "refs/heads/$1" 2>/dev/null; }
state() { if [ -f "$FIX/merged-$1" ]; then echo MERGED; else echo OPEN; fi; }
case "$1 $2" in
  "pr view")
    n="$3"; [ -f "$FIX/pr-$n" ] || { echo "no such pull request $n"; exit 1; }
    ref="$(cat "$FIX/pr-$n")"
    case "$*" in
      *"state,headRefName,headRefOid"*)
        printf '{"state": "%s", "headRefName": "%s", "headRefOid": "%s"}\n' "$(state "$n")" "$ref" "$(oid "$ref")" ;;
      *) printf '%s\tmain\tMERGEABLE\t%s\t%s\n' "$(state "$n")" "$(oid "$ref")" "$ref" ;;
    esac ;;
  "pr list")
    head=""; while [ $# -gt 0 ]; do [ "$1" = "--head" ] && head="$2"; shift; done
    out="["; sep=""
    for f in "$FIX"/pr-*; do
      n="${f##*pr-}"; [ "$(cat "$f")" = "$head" ] || continue; [ "$(state "$n")" = OPEN ] || continue
      out="$out$sep{\"number\": $n, \"headRefName\": \"$head\", \"headRefOid\": \"$(oid "$head")\"}"; sep=", "
    done
    echo "$out]" ;;
  "pr create")
    head=""; while [ $# -gt 0 ]; do [ "$1" = "--head" ] && head="$2"; shift; done
    [ -n "$(oid "$head")" ] || { echo "no branch $head on the hub"; exit 1; }
    n=$(( $(ls "$FIX" | grep -c '^pr-') + 1 ))
    printf '%s\n' "$head" > "$FIX/pr-$n"
    echo "https://example.invalid/pull/$n" ;;
  "pr merge")
    n="$3"; sha=""; while [ $# -gt 0 ]; do [ "$1" = "--match-head-commit" ] && sha="$2"; shift; done
    ref="$(cat "$FIX/pr-$n")"
    [ "$(oid "$ref")" = "$sha" ] || { echo "head of #$n is $(oid "$ref"), not $sha: refused"; exit 1; }
    cd "$FIX/merger" && git fetch -q hub && git checkout -q -B m hub/main \
      && git merge -q --no-ff -m "merge $n" "$sha" && git push -q hub m:main || exit 1
    : > "$FIX/merged-$n" ;;
  "api repos/"*)
    [ -f "$FIX/protection-unreadable" ] && { echo '{"message":"boom"}'; exit 1; }
    admins=true; [ -f "$FIX/admin-bypass" ] && admins=false
    if [ -f "$FIX/protected" ]; then echo '{"required_pull_request_reviews": {}, "enforce_admins": {"enabled": '"$admins"'}}'
    else echo '{"message":"Branch not protected"}'; exit 1; fi ;;
  *) echo "gh stub: unhandled $*" >&2; exit 3 ;;
esac
"""


class Proc:
    def __init__(self, code, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


def _clean_env(extra=None):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("MERGE_", "GIT_", "GH_", "GITHUB_", "BROTHER_")) and k != "XDG_CONFIG_HOME"}
    env.update(extra or {})
    return env


def _pty():
    """(master, slave) or None where the sandbox denies a pseudo terminal."""
    try:
        return os.openpty()
    except OSError:
        return None


# ---------------------------------------------------------------------------------------------------------------------
# the pure functions
# ---------------------------------------------------------------------------------------------------------------------
class TestMovingRef(unittest.TestCase):
    def test_the_run_line_is_moving(self):
        # M-MG1-D-1: False for loop/run-* turns this red
        self.assertTrue(merge_pins.is_moving_ref(RUN_LINE))
        self.assertTrue(merge_pins.is_moving_ref("refs/heads/" + RUN_LINE))
        self.assertTrue(merge_pins.is_moving_ref("loop/run-x", ["other/*"]), "a pattern list cannot drop the default")

    def test_a_feature_branch_is_static(self):
        for ref in ("fix/mg1d-2026-10-04", "main", "loop/runner", "merge-pin/pr2-abcdef012345"):
            self.assertFalse(merge_pins.is_moving_ref(ref), ref)

    def test_the_environment_can_only_add_patterns(self):
        patterns = merge_pins.moving_patterns({"MERGE_PINS_MOVING": "wip/*, loop/run-*,"})
        self.assertEqual(patterns, ["loop/run-*", "wip/*"])
        self.assertEqual(merge_pins.moving_patterns({}), ["loop/run-*"])
        self.assertTrue(merge_pins.is_moving_ref("wip/x", patterns))
        self.assertTrue(merge_pins.is_moving_ref(RUN_LINE, merge_pins.moving_patterns({"MERGE_PINS_MOVING": "x/*"})))

    def test_hostile_input_is_refused(self):
        for bad in (None, 1, True, "", " ", b"x", "a b", "refs/heads/", "-x"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                merge_pins.is_moving_ref(bad)
        for bad in ("x", 1, [""], [1], [None]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                merge_pins.is_moving_ref("fix/x", bad)
        with self.assertRaises(ValueError):
            merge_pins.moving_patterns({"MERGE_PINS_MOVING": 7})

    def test_the_pin_branch_names_the_pr_and_twelve_sha_chars(self):
        self.assertEqual(merge_pins.pin_branch("12", "a" * 40), "merge-pin/pr12-" + "a" * 12)
        for bad in (("x", "a" * 40), ("1", "short"), ("1", "A" * 40), (1, "a" * 40)):
            with self.assertRaises(ValueError, msg=repr(bad)):
                merge_pins.pin_branch(*bad)


class TestOwnerConfirmed(unittest.TestCase):
    def test_a_stdin_that_is_not_a_terminal_is_false(self):
        # M-MG1-D-7: True without a terminal turns this red
        out = io.StringIO()
        self.assertFalse(merge_pins.owner_confirmed("2", io.StringIO("2\n"), out))
        read_end, write_end = os.pipe()
        try:
            os.write(write_end, b"2\n")
            os.close(write_end)
            with os.fdopen(read_end, "r") as pipe:
                self.assertFalse(merge_pins.owner_confirmed("2", pipe, out))
        finally:
            pass
        self.assertEqual(out.getvalue(), "", "a prompt was written to something that is not a terminal")

    def _typed(self, text, expected="2"):
        pair = _pty()
        if pair is None:
            self.skipTest(NO_PTY)
        master, slave = pair
        out = io.StringIO()
        try:
            os.write(master, text)
            with os.fdopen(slave, "r") as terminal:
                answer = merge_pins.owner_confirmed(expected, terminal, out)
        finally:
            os.close(master)
        return answer, out.getvalue()

    def test_the_typed_count_confirms(self):
        answer, prompt = self._typed(b"2\n")
        self.assertTrue(answer)
        self.assertIn("how many pull requests", prompt)

    def test_a_different_typed_count_does_not(self):
        # M-MG1-D-8: accepting a different answer turns this red
        self.assertFalse(self._typed(b"3\n")[0])
        self.assertFalse(self._typed(b"22\n")[0])
        self.assertFalse(self._typed(b"\n")[0])

    def test_an_end_of_file_without_a_line_is_false(self):
        # on a terminal, ^D after "2" delivers the 2 without a newline and a second ^D on the empty line is EOF
        self.assertFalse(self._typed(b"2\x04\x04")[0])

    def test_hostile_input_is_refused(self):
        for bad in (None, 2, True, "", "two", "2.0", b"2"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                merge_pins.owner_confirmed(bad, io.StringIO(""), io.StringIO())
        self.assertFalse(merge_pins.owner_confirmed("2", object(), io.StringIO()))

    def test_it_takes_no_environment_file_or_flag(self):
        import inspect
        self.assertEqual(list(inspect.signature(merge_pins.owner_confirmed).parameters), ["expected", "stdin", "stdout"])
        source = inspect.getsource(merge_pins.owner_confirmed)
        for forbidden in ("os.environ", "open(", "argv"):
            self.assertNotIn(forbidden, source)


class TestMainProtected(unittest.TestCase):
    def answers(self, code, out):
        seen = []

        def runner(argv, cwd=None, env=None):
            seen.append(argv)
            return Proc(code, out, "")
        return runner, seen

    def test_a_required_pull_request_enforced_on_admins_is_true(self):
        runner, seen = self.answers(0, json.dumps({"required_pull_request_reviews": {"required_approving_review_count": 0},
                                                   "enforce_admins": {"enabled": True}}))
        self.assertIs(merge_pins.main_protected("o/r", runner), True)
        self.assertEqual(seen, [["gh", "api", "repos/o/r/branches/main/protection"]])

    def test_a_protection_an_admin_may_bypass_is_false(self):
        # the owner and every session share one admin account: a bypassable rule protects nothing (fail closed)
        for admins in ({"enabled": False}, {}, None, "true"):
            answer = json.dumps({"required_pull_request_reviews": {}, "enforce_admins": admins})
            self.assertIs(merge_pins.main_protected("o/r", self.answers(0, answer)[0]), False, answer)
        self.assertIs(merge_pins.main_protected("o/r", self.answers(0, json.dumps({"required_pull_request_reviews": {}}))[0]),
                      False)

    def test_a_protection_without_it_or_no_protection_is_false(self):
        self.assertIs(merge_pins.main_protected("o/r", self.answers(0, json.dumps({"enforce_admins": {"enabled": True}}))[0]),
                      False)
        self.assertIs(merge_pins.main_protected("o/r", self.answers(1, json.dumps({"message": "Branch not protected"}))[0]),
                      False)

    def test_an_unreadable_answer_is_none_never_true(self):
        # M-MG1-D-6: True when the protection cannot be read turns this red
        self.assertIsNone(merge_pins.main_protected("o/r", self.answers(1, json.dumps({"message": "boom"}))[0]))
        # the live hub today: 403 "Upgrade to GitHub Pro or make this repository public to enable this feature"
        self.assertIsNone(merge_pins.main_protected("o/r", self.answers(1, json.dumps({"message": "Upgrade to GitHub Pro",
                                                                                         "status": "403"}))[0]))
        self.assertIsNone(merge_pins.main_protected("o/r", self.answers(1, "")[0]))
        self.assertIsNone(merge_pins.main_protected("o/r", self.answers(0, "not json")[0]))
        self.assertIsNone(merge_pins.main_protected("o/r", self.answers(0, "[]")[0]))
        self.assertIsNone(merge_pins.main_protected("o/r", self.answers(0, json.dumps({"message": "Moved"}))[0]))

        def denied(argv, cwd=None, env=None):
            raise PermissionError(1, "Operation not permitted", "gh")
        self.assertIsNone(merge_pins.main_protected("o/r", denied))

    def test_hostile_input_is_refused(self):
        runner, _seen = self.answers(0, "{}")
        for bad in (None, 1, "", b"x"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                merge_pins.main_protected(bad, runner)
        with self.assertRaises(ValueError):
            merge_pins.main_protected("o/r", "gh")
        with self.assertRaises(ValueError):
            merge_pins.main_protected("o/r", runner, branch="a b")


class TestOpenFrozenPr(unittest.TestCase):
    BRANCH = "merge-pin/pr2-" + "b" * 12

    def gh(self, open_prs=(), create_out="https://example.invalid/pull/9", list_code=0, create_code=0):
        seen = []

        def runner(argv, cwd=None, env=None):
            seen.append(argv)
            if argv[:3] == ["gh", "pr", "list"]:
                return Proc(list_code, json.dumps(list(open_prs)), "")
            if argv[:3] == ["gh", "pr", "create"]:
                return Proc(create_code, create_out, "")
            return Proc(3, "", "unhandled")
        return runner, seen

    def test_the_pull_request_is_opened_from_the_frozen_branch(self):
        # M-MG1-D-5: opening it from the moving ref turns this red
        runner, seen = self.gh()
        number = merge_pins.open_frozen_pr("o/r", "2", "b" * 40, self.BRANCH, runner=runner)
        self.assertEqual(number, "9")
        create = [a for a in seen if a[:3] == ["gh", "pr", "create"]]
        self.assertEqual(len(create), 1)
        argv = create[0]
        self.assertEqual(argv[argv.index("--head") + 1], self.BRANCH)
        self.assertEqual(argv[argv.index("--base") + 1], "main")
        self.assertEqual(argv[argv.index("-R") + 1], "o/r")
        self.assertIn("#2", argv[argv.index("--title") + 1])
        self.assertIn("b" * 12, argv[argv.index("--title") + 1])
        self.assertIn("b" * 40, argv[argv.index("--body") + 1])
        self.assertIn(RUN_LINE[:4], "loop", "sanity")
        self.assertNotIn(RUN_LINE, " ".join(argv))

    def test_one_already_open_at_the_pinned_head_is_returned_not_duplicated(self):
        runner, seen = self.gh(open_prs=[{"number": 7, "headRefName": self.BRANCH, "headRefOid": "b" * 40}])
        self.assertEqual(merge_pins.open_frozen_pr("o/r", "2", "b" * 40, self.BRANCH, runner=runner), "7")
        self.assertEqual([a for a in seen if a[:3] == ["gh", "pr", "create"]], [])

    def test_one_open_at_another_head_is_refused(self):
        runner, seen = self.gh(open_prs=[{"number": 7, "headRefName": self.BRANCH, "headRefOid": "c" * 40}])
        with self.assertRaises(merge_pins.PinRefused):
            merge_pins.open_frozen_pr("o/r", "2", "b" * 40, self.BRANCH, runner=runner)
        self.assertEqual([a for a in seen if a[:3] == ["gh", "pr", "create"]], [])

    def test_a_gh_failure_or_a_missing_number_is_no_data_never_guessed(self):
        for kwargs in ({"list_code": 1}, {"create_code": 1}, {"create_out": ""}, {"create_out": "Creating pull request"},
                       {"create_out": "https://example.invalid/pull/"}):
            runner, _seen = self.gh(**kwargs)
            with self.assertRaises(merge_gate.MergeNoData, msg=repr(kwargs)):
                merge_pins.open_frozen_pr("o/r", "2", "b" * 40, self.BRANCH, runner=runner)

    def test_hostile_input_is_refused(self):
        runner, _seen = self.gh()
        for args in (("", "2", "b" * 40, self.BRANCH), ("o/r", "x", "b" * 40, self.BRANCH),
                     ("o/r", "2", "short", self.BRANCH), ("o/r", "2", "b" * 40, ""), ("o/r", "2", "b" * 40, "a b")):
            with self.assertRaises(ValueError, msg=repr(args)):
                merge_pins.open_frozen_pr(*args, runner=runner)
        with self.assertRaises(ValueError):
            merge_pins.open_frozen_pr("o/r", "2", "b" * 40, self.BRANCH, runner="gh")


# ---------------------------------------------------------------------------------------------------------------------
# the hub
# ---------------------------------------------------------------------------------------------------------------------
class Fixture(unittest.TestCase):
    """A throwaway hub, a clone of it named `hub`, a static PR 1 (branch pr1) and a moving PR 2 whose head IS the run
    line branch, a second clone that moves the run line, and the tool copies under the clone's scripts/."""

    def git(self, cwd, *a):
        r = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false"] + list(a),
                           cwd=cwd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def setUp(self):
        self.fix = tempfile.mkdtemp(prefix="mg1d-")
        self.addCleanup(shutil.rmtree, self.fix, True)
        self.hub = os.path.join(self.fix, "hub.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.hub], check=True)
        self.clone = os.path.join(self.fix, "clone")
        subprocess.run(["git", "clone", "-q", "-o", "hub", self.hub, self.clone], check=True, capture_output=True)
        os.makedirs(os.path.join(self.clone, "scripts"))
        for name in TOOL_FILES:
            shutil.copy(os.path.join(HERE, name), os.path.join(self.clone, "scripts", name))
        open(os.path.join(self.clone, "scripts", ".merge-verified-fixture"), "w").close()   # the stand-in gh is allowed
        # main's gate script: verify_row compares each row's gate_sha256 with it (GATE-EDITED)
        with open(os.path.join(self.clone, "scripts", "required_fast.sh"), "w") as fh:
            fh.write("#!/bin/sh\necho stub\n")
        open(os.path.join(self.clone, "base.txt"), "w").write("base\n")
        self.git(self.clone, "add", "-A")
        self.git(self.clone, "commit", "-qm", "base")
        self.git(self.clone, "push", "-q", "hub", "HEAD:main")
        self.main = self.git(self.clone, "rev-parse", "HEAD")
        self.heads = {}
        for n, branch in (("1", "pr1"), ("2", RUN_LINE)):
            self.git(self.clone, "checkout", "-q", "-b", branch, "hub/main")
            open(os.path.join(self.clone, "f%s.txt" % n), "w").write(n + "\n")
            self.git(self.clone, "add", "f%s.txt" % n)
            self.git(self.clone, "commit", "-qm", "pr " + n)
            self.heads[n] = self.git(self.clone, "rev-parse", "HEAD")
            self.git(self.clone, "push", "-q", "hub", "%s:%s" % (branch, branch))
        self.git(self.clone, "checkout", "-q", "--detach", "hub/main")
        self.mover = os.path.join(self.fix, "mover")
        subprocess.run(["git", "clone", "-q", "-o", "hub", self.hub, self.mover], check=True, capture_output=True)
        subprocess.run(["git", "clone", "-q", "-o", "hub", self.hub, os.path.join(self.fix, "merger")], check=True,
                       capture_output=True)
        # THE CLONES CARRY THEIR OWN IDENTITY (2026-10-05, as test_merge_precompute): the tool's commit-tree used the
        # machine's global one, so under a fresh HOME (the grader's sandbox, the hermetic gate) the dry run stopped at
        # "cannot chain the dry-run tree" and every MG1 grade's green was red at base.
        for repo in (self.clone, self.mover, os.path.join(self.fix, "merger")):
            for key, value in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
                subprocess.run(["git", "-C", repo, "config", key, value], check=True, capture_output=True)
        os.makedirs(os.path.join(self.fix, "bin"))
        stub = os.path.join(self.fix, "bin", "gh")
        open(stub, "w").write(GH_STUB)
        os.chmod(stub, 0o755)
        open(os.path.join(self.fix, "protected"), "w").write("")
        open(os.path.join(self.fix, "pr-1"), "w").write("pr1\n")
        open(os.path.join(self.fix, "pr-2"), "w").write(RUN_LINE + "\n")
        self.list = os.path.join(self.fix, "list")
        open(self.list, "w").write("1 %s\n2 %s\n" % (self.heads["1"], self.heads["2"]))
        self.env = _clean_env()

    def runner(self):
        """subprocess.run with stdin closed, PATH holding the stand-in gh first, FIX for it; records every call."""
        run_env = _clean_env({"FIX": self.fix, "PATH": os.path.join(self.fix, "bin") + os.pathsep + self.env["PATH"]})
        seen = []

        def run(argv, cwd=None, env=None):
            seen.append(list(argv))
            return subprocess.run(argv, cwd=cwd, env=run_env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                  errors="replace")
        run.seen = seen
        return run

    def hub_branch(self, name):
        r = subprocess.run(["git", "--git-dir", self.hub, "rev-parse", "refs/heads/" + name], capture_output=True,
                           text=True)
        return r.stdout.strip() if r.returncode == 0 else None

    def advance_run_line(self):
        """The loop lands another commit on the run line after the pin was taken."""
        self.git(self.mover, "fetch", "-q", "hub")
        self.git(self.mover, "checkout", "-q", "-B", RUN_LINE, "hub/" + RUN_LINE)
        open(os.path.join(self.mover, "later.txt"), "w").write("later\n")
        self.git(self.mover, "add", "later.txt")
        self.git(self.mover, "commit", "-qm", "the loop landed again")
        self.git(self.mover, "push", "-q", "hub", "%s:%s" % (RUN_LINE, RUN_LINE))
        return self.git(self.mover, "rev-parse", "HEAD")

    def force_push_run_line(self):
        """The run line is rewritten: its head no longer descends from the pinned sha."""
        self.git(self.mover, "fetch", "-q", "hub")
        self.git(self.mover, "checkout", "-q", "-B", RUN_LINE, "hub/main")
        open(os.path.join(self.mover, "rewritten.txt"), "w").write("rewritten\n")
        self.git(self.mover, "add", "rewritten.txt")
        self.git(self.mover, "commit", "-qm", "rewritten")
        self.git(self.mover, "push", "-q", "--force", "hub", "%s:%s" % (RUN_LINE, RUN_LINE))
        return self.git(self.mover, "rev-parse", "HEAD")

    def tree(self, base, head):
        return self.git(self.clone, "merge-tree", "--write-tree", base, head)

    def sign(self, base, head):
        """A signed PASS row for the tree `base` plus `head` makes; returns the chained base for the next one."""
        tree = self.tree(base, head)
        log = os.path.join(self.fix, "gate-%s.log" % tree)
        with open(log, "w") as fh:
            fh.write("pass 3   fail 0   no-data 0\n")
        merge_gate.record_row(self.clone, tree, 0, log, "1", head, base, {}, runner=self.runner())   # the one row writer
        return self.git(self.clone, "commit-tree", tree, "-p", base, "-m", "next base"), tree

    def sign_chain(self):
        base = self.main
        self.trees = {}
        for n in ("1", "2"):
            base, self.trees[n] = self.sign(base, self.heads[n])


class TestFreezePin(Fixture):
    def test_the_frozen_branch_is_pushed_at_the_pinned_sha_and_read_back(self):
        runner = self.runner()
        branch = merge_pins.freeze_pin(self.clone, "2", self.heads["2"], runner=runner)
        self.assertEqual(branch, "merge-pin/pr2-" + self.heads["2"][:12])
        self.assertEqual(self.hub_branch(branch), self.heads["2"])
        pushes = [a for a in runner.seen if a[:2] == ["git", "push"]]
        self.assertEqual(len(pushes), 1)
        self.assertFalse(any(tok in ("--force", "-f") or tok.startswith("+") for tok in pushes[0]), pushes[0])

    def test_a_branch_already_at_the_sha_is_the_same_actor_again(self):
        merge_pins.freeze_pin(self.clone, "2", self.heads["2"], runner=self.runner())
        runner = self.runner()
        self.assertEqual(merge_pins.freeze_pin(self.clone, "2", self.heads["2"], runner=runner),
                         "merge-pin/pr2-" + self.heads["2"][:12])
        self.assertEqual([a for a in runner.seen if a[:2] == ["git", "push"]], [])

    def test_a_branch_at_another_sha_is_never_overwritten(self):
        # M-MG1-D-2: a force push over it turns this red
        branch = "merge-pin/pr2-" + self.heads["2"][:12]
        self.git(self.clone, "push", "-q", "hub", "%s:refs/heads/%s" % (self.heads["1"], branch))
        with self.assertRaises(merge_pins.PinRefused) as caught:
            merge_pins.freeze_pin(self.clone, "2", self.heads["2"], runner=self.runner())
        self.assertIn("never overwritten", str(caught.exception))
        self.assertEqual(self.hub_branch(branch), self.heads["1"], "the existing branch was moved")

    def test_a_sha_nobody_serves_is_no_data(self):
        with self.assertRaises(merge_gate.MergeNoData):
            merge_pins.freeze_pin(self.clone, "2", "0" * 40, runner=self.runner())
        self.assertIsNone(self.hub_branch("merge-pin/pr2-" + "0" * 12))

    def test_a_remote_that_cannot_be_read_is_no_data(self):
        with self.assertRaises(merge_gate.MergeNoData):
            merge_pins.freeze_pin(self.clone, "2", self.heads["2"], remote="nowhere", runner=self.runner())

    def test_hostile_input_is_refused(self):
        runner = self.runner()
        for args in (("", "2", self.heads["2"]), (self.clone, "x", self.heads["2"]), (self.clone, "2", "short"),
                     (self.clone, "2", None)):
            with self.assertRaises(ValueError, msg=repr(args)):
                merge_pins.freeze_pin(*args, runner=runner)
        with self.assertRaises(ValueError):
            merge_pins.freeze_pin(self.clone, "2", self.heads["2"], runner=7)
        self.assertEqual([a for a in runner.seen if a[:2] == ["git", "push"]], [])


class TestCheckPins(Fixture):
    def entry(self, n, **over):
        entry = {"pr": n, "sha": self.heads[n], "head_ref": "pr1" if n == "1" else RUN_LINE}
        entry.update(over)
        return entry

    def test_a_static_entry_needs_no_pin(self):
        lines = merge_pins.check_pins(self.clone, [self.entry("1")], runner=self.runner())
        self.assertEqual(lines, ["STATIC #1 (pr1)"])

    def test_the_moving_patterns_reach_the_check(self):
        # an added pattern (MERGE_PINS_MOVING) makes pr1 a moving head here; the default still applies beside it
        lines = merge_pins.check_pins(self.clone, [self.entry("1"), self.entry("2")], runner=self.runner(),
                                      patterns=merge_pins.moving_patterns({"MERGE_PINS_MOVING": "pr*"}))
        self.assertEqual(lines, ["WILL-FREEZE #1 (pr1)", "WILL-FREEZE #2 (%s)" % RUN_LINE])

    def test_a_moving_entry_with_no_pin_is_will_freeze_not_a_refusal(self):
        # M-MG1-D-10: refusing it turns this red
        lines = merge_pins.check_pins(self.clone, [self.entry("1"), self.entry("2")], runner=self.runner())
        self.assertEqual(lines, ["STATIC #1 (pr1)", "WILL-FREEZE #2 (%s)" % RUN_LINE])
        self.advance_run_line()
        lines = merge_pins.check_pins(self.clone, [self.entry("2")], runner=self.runner())
        self.assertEqual(lines, ["WILL-FREEZE #2 (%s)" % RUN_LINE], "an advanced run line still freezes at the pin")

    def test_a_pin_that_is_not_this_entrys_own_branch_is_refused(self):
        # a pin= naming any other ref, even one that resolves to the pinned sha, is not a pin
        other = "merge-pin/pr9-" + self.heads["2"][:12]
        self.git(self.clone, "push", "-q", "hub", "%s:refs/heads/%s" % (self.heads["2"], other))
        for pin in (other, RUN_LINE, "pr1"):
            with self.assertRaises(merge_pins.PinRefused, msg=pin) as caught:
                merge_pins.check_pins(self.clone, [self.entry("2", pin=pin)], runner=self.runner())
            self.assertIn("own frozen branch", str(caught.exception))

    def test_a_pin_branch_that_does_not_resolve_to_the_sha_is_refused(self):
        # M-MG1-D-3: skipping the remote resolution turns this red
        branch = "merge-pin/pr2-" + self.heads["2"][:12]
        with self.assertRaises(merge_pins.PinRefused) as caught:
            merge_pins.check_pins(self.clone, [self.entry("2", pin=branch)], runner=self.runner())
        self.assertIn("nothing", str(caught.exception))
        self.git(self.clone, "push", "-q", "hub", "%s:refs/heads/%s" % (self.heads["1"], branch))
        with self.assertRaises(merge_pins.PinRefused) as caught:
            merge_pins.check_pins(self.clone, [self.entry("2", pin=branch)], runner=self.runner())
        self.assertIn(self.heads["1"][:12], str(caught.exception))

    def test_a_pin_branch_at_the_sha_is_accepted_with_its_pull_request_pending(self):
        branch = merge_pins.freeze_pin(self.clone, "2", self.heads["2"], runner=self.runner())
        lines = merge_pins.check_pins(self.clone, [self.entry("2", pin=branch)], runner=self.runner())
        self.assertEqual(lines, ["PINNED #2 %s, pull request pending" % branch])

    def test_a_frozen_pull_request_is_checked_against_the_branch_and_the_sha(self):
        branch = merge_pins.freeze_pin(self.clone, "2", self.heads["2"], runner=self.runner())
        open(os.path.join(self.fix, "pr-3"), "w").write(branch + "\n")
        lines = merge_pins.check_pins(self.clone, [self.entry("2", pin=branch, frozen="3")], runner=self.runner())
        self.assertEqual(lines, ["FROZEN #2 %s #3 (OPEN)" % branch])
        open(os.path.join(self.fix, "pr-3"), "w").write("pr1\n")   # the pull request's head is another branch
        with self.assertRaises(merge_pins.PinRefused):
            merge_pins.check_pins(self.clone, [self.entry("2", pin=branch, frozen="3")], runner=self.runner())
        with self.assertRaises(merge_gate.MergeNoData):   # a pull request gh cannot read
            merge_pins.check_pins(self.clone, [self.entry("2", pin=branch, frozen="44")], runner=self.runner())
        with self.assertRaises(merge_pins.PinRefused):   # frozen without a pin is a malformed line
            merge_pins.check_pins(self.clone, [self.entry("2", frozen="3")], runner=self.runner())

    def test_a_force_pushed_run_line_is_refused(self):
        # M-MG1-D-4: skipping the ancestor check turns this red
        self.force_push_run_line()
        with self.assertRaises(merge_pins.PinRefused) as caught:
            merge_pins.check_pins(self.clone, [self.entry("2")], runner=self.runner())
        self.assertIn("force push", str(caught.exception))
        branch = "merge-pin/pr2-" + self.heads["2"][:12]
        self.git(self.clone, "push", "-q", "hub", "%s:refs/heads/%s" % (self.heads["2"], branch))
        with self.assertRaises(merge_pins.PinRefused):   # a frozen pin does not excuse the rewritten run line
            merge_pins.check_pins(self.clone, [self.entry("2", pin=branch)], runner=self.runner())

    def test_a_remote_that_cannot_be_read_is_no_data(self):
        with self.assertRaises(merge_gate.MergeNoData):
            merge_pins.check_pins(self.clone, [self.entry("2")], remote="nowhere", runner=self.runner())
        self.git(self.clone, "push", "-q", "hub", ":refs/heads/" + RUN_LINE)   # the run line is gone
        with self.assertRaises(merge_gate.MergeNoData):
            merge_pins.check_pins(self.clone, [self.entry("2")], runner=self.runner())

    def test_hostile_input_is_refused(self):
        runner = self.runner()
        for bad in ("x", None, 1, [1], [{"pr": "1"}], [{"pr": "1", "sha": "x", "head_ref": "pr1"}],
                    [{"pr": "1", "sha": self.heads["1"], "head_ref": ""}],
                    [{"pr": "1", "sha": self.heads["1"], "head_ref": "pr1", "pin": 3}],
                    [{"pr": "1", "sha": self.heads["1"], "head_ref": "pr1", "frozen": "x"}]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                merge_pins.check_pins(self.clone, bad, runner=runner)
        with self.assertRaises(ValueError):
            merge_pins.check_pins(self.clone, [], runner="git")
        self.assertEqual(merge_pins.check_pins(self.clone, [], runner=runner), [])


class TestNotePin(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mg1d-note-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "list")
        open(self.path, "w").write("# batch\n1 %s what evidence\n2 %s run-line pin=old/x\n" % ("a" * 40, "b" * 40))

    def test_the_line_is_rewritten_in_place_and_the_rest_kept(self):
        line = merge_pins.note_pin(self.path, "2", "merge-pin/pr2-" + "b" * 12)
        self.assertEqual(line, "2 %s run-line pin=merge-pin/pr2-%s" % ("b" * 40, "b" * 12))
        merge_pins.note_pin(self.path, "2", "merge-pin/pr2-" + "b" * 12, "3")
        self.assertEqual(open(self.path).read(),
                         "# batch\n1 %s what evidence\n2 %s run-line pin=merge-pin/pr2-%s frozen=3\n"
                         % ("a" * 40, "b" * 40, "b" * 12))
        self.assertEqual([n for n in os.listdir(self.dir) if n.startswith(".merge-list-")], [], "a temp file was left")

    def test_a_missing_or_doubled_line_refuses(self):
        with self.assertRaises(merge_gate.MergeNoData):
            merge_pins.note_pin(self.path, "9", "x/y")
        open(self.path, "a").write("1 %s again\n" % ("c" * 40))
        with self.assertRaises(merge_gate.MergeNoData):
            merge_pins.note_pin(self.path, "1", "x/y")
        with self.assertRaises(merge_gate.MergeNoData):
            merge_pins.note_pin(os.path.join(self.dir, "absent"), "1", "x/y")

    def test_hostile_input_is_refused(self):
        for args in ((self.path, "x", "a/b"), (self.path, "1", "a b"), (self.path, "1", "a/b", "x"), ("", "1", "a/b")):
            with self.assertRaises(ValueError, msg=repr(args)):
                merge_pins.note_pin(*args)


class TestCommandLine(Fixture):
    def tool(self, *args, env=None, stdin=subprocess.DEVNULL):
        return subprocess.run([sys.executable, "-B", os.path.join(self.clone, "scripts", "merge_pins.py")] + list(args),
                              env=env or self.env, capture_output=True, text=True, timeout=60, stdin=stdin)

    def test_main_refuses_an_argv_that_is_not_a_list_of_str(self):
        for bad in (0, True, b"x", "moving", ["moving", 1], object()):
            with self.assertRaises(ValueError):
                merge_pins.main(bad)
        self.assertEqual(merge_pins.main([]), 2)
        self.assertEqual(merge_pins.main(["nonsense"]), 2)

    def test_moving_answers_with_its_exit_code(self):
        self.assertEqual(self.tool("moving", RUN_LINE).returncode, 0)
        self.assertEqual(self.tool("moving", "fix/x").returncode, 1)
        self.assertEqual(self.tool("moving").returncode, 2)

    def test_protected_reads_the_gh_it_is_handed_never_path(self):
        stub = os.path.join(self.fix, "bin", "gh")
        env = _clean_env({"FIX": self.fix, "PATH": "/usr/bin:/bin"})
        self.assertEqual(self.tool("protected", "--gh", stub, "o/r", env=env).returncode, 0)
        os.remove(os.path.join(self.fix, "protected"))
        self.assertEqual(self.tool("protected", "--gh", stub, "o/r", env=env).returncode, 1)
        open(os.path.join(self.fix, "protection-unreadable"), "w").close()
        r = self.tool("protected", "--gh", stub, "o/r", env=env)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("NO-DATA", r.stdout)
        r = self.tool("protected", "o/r", env=env)   # no gh handed in and none on the system PATH: NO-DATA
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(self.tool("protected", "--gh", "gh", "o/r", env=env).returncode, 2, "a relative gh")

    def test_a_steered_environment_refuses_every_write_and_the_prompt(self):
        for command in (("freeze", self.clone, "hub", "2", self.heads["2"]),
                        ("open", "o/r", "2", self.heads["2"], "merge-pin/pr2-" + self.heads["2"][:12]),
                        ("note", self.list, "2", "pin=x/y"), ("confirm", "2")):
            for name in ("MERGE_GATE_LEDGER", "MERGE_ALL_X", "GIT_DIR", "PR_PARK_REPO"):
                r = self.tool(*command, env=_clean_env({name: "x"}))
                self.assertEqual(r.returncode, 2, (command[0], name, r.stderr))
                self.assertIn(name, r.stderr)
        self.assertIsNone(self.hub_branch("merge-pin/pr2-" + self.heads["2"][:12]), "a refused freeze pushed")
        self.assertNotIn("pin=", open(self.list).read())

    def test_confirm_without_a_terminal_exits_one(self):
        r = self.tool("confirm", "2")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("no terminal typed", r.stdout)
        self.assertEqual(self.tool("confirm", "two").returncode, 2)

    def test_check_prints_one_line_per_entry_and_its_refusals(self):
        env = _clean_env({"FIX": self.fix, "PATH": "/usr/bin:/bin"})
        r = self.tool("check", self.clone, "hub", "2", self.heads["2"], RUN_LINE, env=env)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "WILL-FREEZE #2 (%s)" % RUN_LINE), r.stderr)
        # the refusal is the answer, so it is on stdout too: the shell reads stdout alone and classifies the first token
        r = self.tool("check", self.clone, "hub", "2", self.heads["2"], RUN_LINE, "pin=merge-pin/none", env=env)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("STOP: "), r.stdout)
        r = self.tool("check", self.clone, "nowhere", "2", self.heads["2"], RUN_LINE, env=env)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("NO-DATA: "), r.stdout)
        # MERGE_PINS_MOVING reaches the check: pr1 becomes a moving head under an added pattern
        r = self.tool("check", self.clone, "hub", "1", self.heads["1"], "pr1", env=dict(env, MERGE_PINS_MOVING="pr*"))
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "WILL-FREEZE #1 (pr1)"), r.stderr)
        r = self.tool("check", self.clone, "hub", "1", self.heads["1"], "pr1", env=env)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "STATIC #1 (pr1)"), r.stderr)
        self.assertEqual(self.tool("check", self.clone, "hub", "2", self.heads["2"], RUN_LINE, "bogus=1",
                                   env=env).returncode, 2)


# ---------------------------------------------------------------------------------------------------------------------
# the shell tool, run for real against the throwaway hub
# ---------------------------------------------------------------------------------------------------------------------
class TheFrozenHeadPath(Fixture):
    """scripts/merge_verified.sh with the stand-in gh: PR 1 is static, PR 2's head is the run line. A real run reads
    the owner's typed count from a pseudo terminal; a dry run needs none."""

    def run_tool(self, *args, tty=True, answer=None, extra=None):
        env = _clean_env({"FIX": self.fix, "PATH": os.path.join(self.fix, "bin") + os.pathsep + self.env["PATH"]})
        env.update(extra or {})
        tool = ["sh", os.path.join(self.clone, "scripts", "merge_verified.sh")] + list(args)
        master = slave = None
        if tty:
            # the two preconditions of a real run, each named when it is missing: the tool refuses a HOME that is not
            # the account's own (a hermetic sandbox gives it a throwaway one), and it needs a terminal on stdin
            import pwd
            if env.get("HOME") != pwd.getpwuid(os.getuid()).pw_dir:
                self.skipTest("NO-DATA: HOME is not this account's own home, so the tool refuses every real run "
                              "before anything; the real run cannot be exercised here")
            pair = _pty()
            if pair is None:
                self.skipTest(NO_PTY)
            master, slave = pair
            if answer is None:
                with open(self.list) as fh:
                    answer = "%d\n" % sum(1 for line in fh if line.split() and not line.lstrip().startswith("#"))
            os.write(master, answer.encode("ascii"))
        try:
            r = subprocess.run(tool, cwd=self.clone, env=env, capture_output=True, text=True, timeout=120,
                               stdin=slave if tty else subprocess.DEVNULL)
        finally:
            for fd in (master, slave):
                if fd is not None:
                    os.close(fd)
        log_path = os.path.join(self.fix, "gh.log")
        log = open(log_path).read() if os.path.exists(log_path) else ""
        return r.returncode, r.stdout + r.stderr, log

    def merges(self, log):
        return [line for line in log.splitlines() if line.startswith("pr merge ")]

    def test_a_run_line_that_advanced_after_pinning_merges_the_pinned_sha_through_its_frozen_pr(self):
        # M-MG1-D-1 (no freeze: the moved head is refused by the stand-in gh) and M-MG1-D-9 (the moving number is
        # merged instead of the frozen one) both turn this red
        self.sign_chain()
        later = self.advance_run_line()
        rc, out, log = self.run_tool(self.list)
        self.assertEqual(rc, 0, out)
        self.assertIn("DONE: 2 of 2", out)
        merges = self.merges(log)
        self.assertEqual(len(merges), 2, log)
        self.assertTrue(merges[0].startswith("pr merge 1 "), merges[0])             # the static PR, the same gh path
        self.assertIn("--match-head-commit " + self.heads["1"], merges[0])
        self.assertTrue(merges[1].startswith("pr merge 3 "), merges[1])             # the frozen-head pull request
        self.assertIn("--match-head-commit " + self.heads["2"], merges[1])
        branch = "merge-pin/pr2-" + self.heads["2"][:12]
        self.assertEqual(self.hub_branch(branch), self.heads["2"])
        self.assertEqual(open(os.path.join(self.fix, "pr-3")).read().strip(), branch)
        self.assertIn("pin=%s frozen=3" % branch, open(self.list).read().splitlines()[1])
        self.assertIn("FROZEN #2 at %s on %s as #3" % (self.heads["2"], branch), out)
        main_now = self.hub_branch("main")
        self.assertEqual(self.git(self.clone, "rev-parse", main_now + "^{tree}"), self.trees["2"], "main holds another tree")
        self.assertNotEqual(subprocess.run(["git", "--git-dir", self.hub, "merge-base", "--is-ancestor", later, main_now]).returncode,
                            0, "the loop's later commit reached main")
        self.assertEqual(self.hub_branch(RUN_LINE), later, "the run line itself was touched")

    def test_the_stand_in_gh_refuses_a_moved_head_the_way_github_does(self):
        # the fixture's own teeth: without the frozen pull request, pr merge 2 at the pin is refused once the head moved
        self.advance_run_line()
        env = _clean_env({"FIX": self.fix})
        r = subprocess.run([os.path.join(self.fix, "bin", "gh"), "pr", "merge", "2", "--merge", "--match-head-commit",
                            self.heads["2"]], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self.hub_branch("main"), self.main)

    def test_a_dry_run_prints_will_freeze_and_the_protection_and_writes_nothing(self):
        # M-MG1-D-10 at the tool: a refusal of the unfrozen moving entry turns this red
        self.sign_chain()
        rc, out, log = self.run_tool("--dry-run", self.list, tty=False)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count("WOULD MERGE"), 2, out)
        self.assertIn("[STATIC #1 (pr1)]", out)
        self.assertIn("[WILL-FREEZE #2 (%s)]" % RUN_LINE, out)
        self.assertIn("PIN #2: WILL-FREEZE", out)
        self.assertIn("PROTECTION: main requires a pull request", out)
        self.assertEqual(self.merges(log), [])
        self.assertNotIn("pr create", log)
        self.assertIsNone(self.hub_branch("merge-pin/pr2-" + self.heads["2"][:12]))
        self.assertNotIn("pin=", open(self.list).read())
        os.remove(os.path.join(self.fix, "protected"))
        rc, out, _log = self.run_tool("--dry-run", self.list, tty=False)
        self.assertEqual(rc, 0, out)
        self.assertIn("does not require a pull request", out)

    def test_a_force_pushed_run_line_is_refused_before_any_write(self):
        self.sign(self.main, self.heads["2"])
        open(self.list, "w").write("2 %s\n" % self.heads["2"])
        self.force_push_run_line()
        rc, out, log = self.run_tool(self.list)
        self.assertEqual(rc, 1, out)
        self.assertIn("force push", out)
        self.assertEqual(self.merges(log), [])
        self.assertNotIn("pr create", log)
        self.assertIsNone(self.hub_branch("merge-pin/pr2-" + self.heads["2"][:12]))
        self.assertEqual(self.hub_branch("main"), self.main)

    def test_an_unreadable_protection_refuses_the_real_run_before_any_write(self):
        # M-MG1-D-6 at the tool: reading an unreadable protection as required turns this red
        self.sign_chain()
        open(os.path.join(self.fix, "protection-unreadable"), "w").close()
        rc, out, log = self.run_tool(self.list)
        self.assertEqual(rc, 2, out)
        self.assertIn("branch protection cannot be read", out)
        self.assertEqual(self.merges(log), [])
        self.assertNotIn("pr create", log)
        self.assertIsNone(self.hub_branch("merge-pin/pr2-" + self.heads["2"][:12]))
        self.assertEqual(self.hub_branch("main"), self.main)

    def test_a_wrong_typed_count_writes_nothing(self):
        self.sign_chain()
        rc, out, log = self.run_tool(self.list, answer="9\n")
        self.assertEqual(rc, 1, out)
        self.assertIn("typed count did not match", out)
        self.assertEqual(self.merges(log), [])
        self.assertNotIn("pr create", log)
        self.assertEqual(self.hub_branch("main"), self.main)

    def test_a_moving_first_entry_is_frozen_only_after_the_typed_count(self):
        # the freeze and the pull request are writes and run AFTER owner_confirmed: with the moving entry first and
        # the wrong count typed, no branch is pushed, no pull request opened, no list line rewritten (a confirm_once
        # moved below the freeze block turns this red)
        self.sign(self.main, self.heads["2"])
        open(self.list, "w").write("2 %s\n1 %s\n" % (self.heads["2"], self.heads["1"]))
        rc, out, log = self.run_tool(self.list, answer="9\n")
        self.assertEqual(rc, 1, out)
        self.assertIn("typed count did not match", out)
        self.assertIsNone(self.hub_branch("merge-pin/pr2-" + self.heads["2"][:12]), "the branch was pushed before the prompt")
        self.assertNotIn("pr create", log)
        self.assertFalse(os.path.exists(os.path.join(self.fix, "pr-3")))
        self.assertEqual(self.merges(log), [])
        self.assertNotIn("pin=", open(self.list).read())
        self.assertEqual(self.hub_branch("main"), self.main)

    def test_every_pin_is_checked_before_the_first_merge(self):
        # PR 1 is sound and first; PR 2's run line was force pushed: nothing merges, rather than PR 1 landing and the
        # batch stopping at PR 2 (a per entry check alone turns this red)
        self.sign_chain()
        self.force_push_run_line()
        rc, out, log = self.run_tool(self.list)
        self.assertEqual(rc, 1, out)
        self.assertIn("force push", out)
        self.assertIn("refused before any write", out)
        self.assertEqual(self.merges(log), [])
        self.assertEqual(self.hub_branch("main"), self.main)

    def test_a_protection_an_admin_may_bypass_refuses_the_real_run(self):
        self.sign_chain()
        open(os.path.join(self.fix, "admin-bypass"), "w").close()
        rc, out, log = self.run_tool(self.list)
        self.assertEqual(rc, 2, out)
        self.assertIn("does not require a pull request", out)
        self.assertEqual(self.merges(log), [])
        self.assertEqual(self.hub_branch("main"), self.main)

    def test_a_rerun_after_a_crash_between_the_freeze_and_the_pull_request_finishes_the_entry(self):
        self.sign_chain()
        branch = merge_pins.freeze_pin(self.clone, "2", self.heads["2"], runner=self.runner())
        merge_pins.note_pin(self.list, "2", branch)
        self.advance_run_line()
        rc, out, log = self.run_tool(self.list)
        self.assertEqual(rc, 0, out)
        self.assertEqual(log.count("pr create"), 1, log)
        self.assertTrue(self.merges(log)[1].startswith("pr merge 3 "), log)
        self.assertEqual(self.hub_branch(branch), self.heads["2"])
        self.assertIn("pin=%s frozen=3" % branch, open(self.list).read())
        # a second rerun of the same list stops on the merged frozen pull request and writes nothing more
        rc, out, log = self.run_tool(self.list)
        self.assertNotEqual(rc, 0)
        self.assertIn("state is MERGED", out)
        self.assertEqual(len(self.merges(log)), 2)

    def test_the_tool_carries_the_frozen_head_path(self):
        text = open(os.path.join(HERE, "merge_verified.sh"), encoding="utf-8").read()
        for needle in ('"$PY_PINS" check', '"$PY_PINS" freeze', '"$PY_PINS" open', '"$PY_PINS" confirm',
                       '"$PY_PINS" protected', 'gh pr merge "$target" -R "$REPO" --merge --match-head-commit "$sha"',
                       "confirm_once || ", 'case "${pin_line%% *}" in'):
            self.assertIn(needle, text)
        self.assertNotIn('${frozen:+"frozen=$frozen"} 2>&1)', text, "the pin check answer must be stdout alone")
        self.assertNotIn("git push", text, "the tool itself never pushes; merge_pins.freeze_pin does, after the prompt")


if __name__ == "__main__":
    unittest.main()
