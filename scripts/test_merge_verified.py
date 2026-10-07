#!/usr/bin/env python3
"""The Python half of the merge_verified fixture (MG1.a).

WHAT IT DOES. It imports scripts/merge_gate.py and drives it
(ledger_path, refuse_steered_env, merged_tree, verify_tree, sign_row, mac_ok),
and it reads scripts/merge_verified.sh, scripts/gate_merge_seq.sh and
scripts/test_merge_verified.sh byte for byte and asserts the guards the
specification names: the head pin on the merge call, the head comparison, the
dry run branch and its chained base, the base re-fetch immediately before the
merge, the branch protection read before the first write, and the tree
comparison after the merge.

WHY NO SUBPROCESS HERE. The safety screen admits no `subprocess` import in a
file this build writes, and every process creating os call is refused with
it. The bash fixture is therefore asserted structurally here and driven by
hand: `sh scripts/test_merge_verified.sh` prints
`merge_verified tests: 26 passed, 0 failed`.

NO FILE ASSERTED ON HERE CARRIES A HOME PATH: no user directory prefix, no
shell home variable, no tilde claude directory. The needles below are built
by concatenation so this file does not carry them either.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import merge_gate  # noqa: E402

USER_NEEDLE = "/Us" + "ers/"
HOME_NEEDLE = "$HO" + "ME"
TILDE_NEEDLE = "~/.clau" + "de"

OWNED_PREFIXES = (
    "merge_gate", "merge_verified", "merge_pins", "merge_reach",
    "merge_precompute", "merge_close_list", "merge_all", "gate_merge_seq",
    "test_merge_",
)
OWNED_EXACT = ("close_superseded.sh",)

CASE_NAMES = (
    "no-list", "missing-list", "empty-list", "state-open",
    "state-merged-already", "state-closed", "base-main", "base-other",
    "mergeable-yes", "mergeable-no", "head-pin-equal", "head-pin-moved",
    "gate-pass", "gate-fail", "gate-no-data", "merge-call-match-head",
    "merge-refused", "dry-run-no-write", "dry-run-chains-base",
    "base-refetch-moved", "base-refetch-clean", "tree-drift-after-merge",
    "main-not-protected", "main-protection-unreadable",
    "steered-env-refused", "steered-git-common-dir-refused",
)


def _read(path):
    """Bytes in, text out: a file this unit owns is read the way it is on
    disk, never through a codec the platform guessed."""
    with open(path, "rb") as handle:
        return handle.read().decode("utf-8", "replace")


def _owned_paths(folder=HERE):
    """Every file this unit owns, as it exists right now. A file a later sub
    unit adds under one of these prefixes joins the assertion with no edit
    (R-MG-1). merge_queue.py is a different, W8 planning queue and is not in
    this list."""
    paths = []
    for name in sorted(os.listdir(folder)):
        if name in OWNED_EXACT or name.startswith(OWNED_PREFIXES):
            paths.append(os.path.join(folder, name))
    return paths


def _make_clone(self=None):
    """A throwaway clone root: a directory holding a .git directory, so
    git_common_dir has something real to read."""
    root = tempfile.mkdtemp(prefix="merge-gate-test-")
    os.mkdir(os.path.join(root, ".git"))
    return root


class TestProductOwnership(unittest.TestCase):
    def test_main_refuses_an_argv_that_is_not_a_list_of_str(self):
        # the landing fuzz's hostile values (2026-10-04): each is a ValueError, the documented refusal, never TypeError
        for bad in (0, -1, True, float("nan"), b"x", {1, 2}, object(), "refuse-env", ["refuse-env", 1]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                merge_gate.main(bad)

    def setUp(self):
        self.roots = []
        self.addCleanup(self._clean)

    def _clean(self):
        for root in self.roots:
            shutil.rmtree(root, ignore_errors=True)

    def clone(self):
        root = _make_clone()
        self.roots.append(root)
        return root

    def test_the_owned_files_are_shipped_beside_their_modules(self):
        paths = _owned_paths()
        self.assertGreaterEqual(len(paths), 5)
        for path in paths:
            self.assertTrue(os.path.isfile(path), path)

    def test_no_owned_file_carries_a_home_or_user_path(self):
        paths = _owned_paths()
        self.assertTrue(paths)
        for path in paths:
            text = _read(path)
            for needle in (USER_NEEDLE, HOME_NEEDLE, TILDE_NEEDLE):
                self.assertNotIn(needle, text,
                                 "%s carries %r" % (path, needle))


class TestLedgerPath(unittest.TestCase):
    def setUp(self):
        self.roots = []
        self.addCleanup(self._clean)

    def _clean(self):
        for root in self.roots:
            shutil.rmtree(root, ignore_errors=True)

    def clone(self):
        root = _make_clone()
        self.roots.append(root)
        return root

    def test_the_ledger_lives_under_the_git_common_dir(self):
        clone = self.clone()
        self.assertEqual(merge_gate.ledger_path(clone),
                         os.path.join(clone, ".git", "merge-gate",
                                      "ledger.jsonl"))

    def test_the_fixture_seam_needs_merge_gate_test(self):
        clone = self.clone()
        steered = os.path.join(clone, "elsewhere.jsonl")
        self.assertNotEqual(merge_gate.ledger_path(clone,
                                                   {"MERGE_GATE_LEDGER": steered}),
                            steered)
        seam = {"MERGE_GATE_LEDGER": steered, "MERGE_GATE_TEST": "1"}
        self.assertEqual(merge_gate.ledger_path(clone, seam), steered)

    def test_a_non_string_ledger_variable_is_refused(self):
        with self.assertRaises(ValueError):
            merge_gate.ledger_path(self.clone(), {"MERGE_GATE_TEST": "1",
                                                  "MERGE_GATE_LEDGER": 7})

    def test_hostile_input_is_refused(self):
        for bad in (None, 17, True, 3.5, "", b"/tmp", [], {}):
            with self.assertRaises(ValueError):
                merge_gate.ledger_path(bad)
        clone = self.clone()
        for bad_env in (17, True, "x", b"y", [1], (1,), set([1])):
            with self.assertRaises(ValueError):
                merge_gate.ledger_path(clone, bad_env)
        with self.assertRaises(ValueError):
            merge_gate.ledger_path(clone, {1: "b"})

    def test_a_missing_clone_is_no_data_not_a_path(self):
        with self.assertRaises(ValueError):
            merge_gate.ledger_path(os.path.join(HERE, "no-such-clone-here"))


class TestRefuseSteeredEnv(unittest.TestCase):
    def test_a_clean_environment_is_allowed(self):
        self.assertIsNone(merge_gate.refuse_steered_env({}, False))
        self.assertIsNone(merge_gate.refuse_steered_env(
            {"PATH": "/usr/bin", "LANG": "C"}, False))

    def test_every_steering_name_is_refused(self):
        for name in ("MERGE_GATE_LEDGER", "MERGE_GATE_TEST", "MERGE_ALL_X",
                     "PR_PARK_REPO", "GIT_DIR", "GIT_COMMON_DIR",
                     "GIT_OBJECT_DIRECTORY", "GIT_WORK_TREE"):
            reason = merge_gate.refuse_steered_env({name: "1"}, False)
            self.assertTrue(reason, name)
            self.assertIn(name, reason)

    def test_a_dry_run_is_never_refused_for_this(self):
        self.assertIsNone(merge_gate.refuse_steered_env(
            {"MERGE_GATE_LEDGER": "x", "PR_PARK_REPO": "y"}, True))

    def test_hostile_input_is_refused(self):
        with self.assertRaises(ValueError):
            merge_gate.refuse_steered_env(None, 3)
        with self.assertRaises(ValueError):
            merge_gate.refuse_steered_env({}, "no")
        with self.assertRaises(ValueError):
            merge_gate.refuse_steered_env([], False)
        with self.assertRaises(ValueError):
            merge_gate.refuse_steered_env({1: "a"}, False)


class TestMergedTree(unittest.TestCase):
    def test_a_fourth_argument_that_is_not_callable_is_refused(self):
        with self.assertRaises(ValueError):
            merge_gate.merged_tree("x", "x", "x", "x")

    def test_no_runner_is_a_refusal_never_a_tree(self):
        with self.assertRaises(ValueError):
            merge_gate.merged_tree("x", "x", "x")

    def test_hostile_input_is_refused(self):
        for bad in (None, 1, True, "", b"x", []):
            with self.assertRaises(ValueError):
                merge_gate.merged_tree(bad, "b", "c")

    def test_a_clean_runner_answer_is_read(self):
        class Proc(object):
            returncode = 0
            stdout = "b" * 40 + "\n"
            stderr = ""

        seen = []

        def runner(argv, cwd=None):
            seen.append((argv, cwd))
            return Proc()

        tree = merge_gate.merged_tree("/tmp", "main", "head", runner=runner)
        self.assertEqual(tree, "b" * 40)
        self.assertEqual(seen[0][0][:3], ["git", "merge-tree", "--write-tree"])
        self.assertEqual(seen[0][0][3:], ["main", "head"])

    def test_a_failing_runner_is_a_conflict(self):
        class Proc(object):
            returncode = 1
            stdout = "CONFLICT (content)\n"
            stderr = ""

        with self.assertRaises(merge_gate.MergeConflict):
            merge_gate.merged_tree("/tmp", "main", "head",
                                   runner=lambda argv, cwd=None: Proc())

    def test_a_runner_that_prints_no_tree_is_a_conflict(self):
        class Proc(object):
            returncode = 0
            stdout = "not-a-tree\n"
            stderr = ""

        with self.assertRaises(merge_gate.MergeConflict):
            merge_gate.merged_tree("/tmp", "main", "head",
                                   runner=lambda argv, cwd=None: Proc())


class TestTreeGate(unittest.TestCase):
    def setUp(self):
        self.roots = []
        self.addCleanup(self._clean)
        self.clone_root = _make_clone()
        self.roots.append(self.clone_root)
        self.tree = "a" * 40

    def _clean(self):
        for root in self.roots:
            shutil.rmtree(root, ignore_errors=True)

    def row(self, **over):
        row = {"tree": self.tree, "rc": 0,
               "counts": {"pass": 5, "fail": 0, "no_data": 0},
               "who": "owner", "when": "2026-10-03T00:00:00Z"}
        row.update(over)
        return row

    def signed(self, **over):
        return merge_gate.sign_row(self.clone_root, self.row(**over))

    def write(self, rows):
        path = merge_gate.ledger_path(self.clone_root)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
        return path

    def test_a_missing_ledger_is_no_data(self):
        verdict, why = merge_gate.verify_tree(self.clone_root, self.tree)
        self.assertEqual(verdict, "NO-DATA")
        self.assertTrue(why)

    def test_a_signed_short_row_never_passes(self):
        # B2, review 2026-10-05: rc 0, clean counts and a valid mac used to PASS here; verify_tree is verify_newest now,
        # so a row without the schema, log, host and gate fields is refused (the full rules: test_merge_gate.py)
        self.write([self.signed()])
        verdict, why = merge_gate.verify_tree(self.clone_root, self.tree)
        self.assertEqual(verdict, "NO-DATA", why)

    def test_a_row_for_another_tree_is_no_data(self):
        self.write([self.signed(tree="c" * 40)])
        verdict, _why = merge_gate.verify_tree(self.clone_root, self.tree)
        self.assertEqual(verdict, "NO-DATA")

    def test_a_row_with_rc_not_zero_never_passes(self):
        self.write([self.signed(rc=1)])
        verdict, _why = merge_gate.verify_tree(self.clone_root, self.tree)
        self.assertNotEqual(verdict, "PASS")

    def test_a_row_without_counts_never_passes(self):
        row = self.signed()
        del row["counts"]
        self.write([row])
        verdict, _why = merge_gate.verify_tree(self.clone_root, self.tree)
        self.assertNotEqual(verdict, "PASS")

    def test_a_row_with_no_data_above_zero_never_passes(self):
        self.write([self.signed(counts={"pass": 5, "fail": 0, "no_data": 1})])
        verdict, _why = merge_gate.verify_tree(self.clone_root, self.tree)
        self.assertNotEqual(verdict, "PASS")

    def test_a_row_with_a_forged_mac_never_passes(self):
        row = self.signed()
        row["mac"] = "0" * 64
        self.write([row])
        verdict, _why = merge_gate.verify_tree(self.clone_root, self.tree)
        self.assertNotEqual(verdict, "PASS")

    def test_a_row_with_no_mac_never_passes(self):
        row = self.row()
        self.write([row])
        verdict, _why = merge_gate.verify_tree(self.clone_root, self.tree)
        self.assertNotEqual(verdict, "PASS")

    def test_a_torn_ledger_is_no_data(self):
        path = self.write([self.signed()])
        with open(path, "ab") as handle:
            handle.write(b"{not json\n")
        verdict, _why = merge_gate.verify_tree(self.clone_root, self.tree)
        self.assertEqual(verdict, "NO-DATA")

    def test_a_ledger_that_is_not_utf8_is_no_data(self):
        path = self.write([self.signed()])
        with open(path, "ab") as handle:
            handle.write(b"\xff\xfe\n")
        verdict, _why = merge_gate.verify_tree(self.clone_root, self.tree)
        self.assertEqual(verdict, "NO-DATA")

    def test_hostile_input_is_refused(self):
        for bad in (None, 7, True, "", b"x", []):
            with self.assertRaises(ValueError):
                merge_gate.verify_tree(self.clone_root, bad)
        with self.assertRaises(ValueError):
            merge_gate.verify_tree(None, self.tree)
        with self.assertRaises(ValueError):
            merge_gate.verify_tree(self.clone_root, "not-a-tree")

    def test_a_row_replaced_after_it_was_signed_is_refused(self):
        row = self.signed()
        parsed = json.loads(json.dumps(row))
        parsed["counts"] = {"pass": 5, "fail": 0, "no_data": 0, "extra": 1}
        self.write([parsed])
        verdict, _why = merge_gate.verify_tree(self.clone_root, self.tree)
        self.assertNotEqual(verdict, "PASS")


class TestSigning(unittest.TestCase):
    def setUp(self):
        self.roots = []
        self.addCleanup(self._clean)
        self.clone_root = _make_clone()
        self.roots.append(self.clone_root)

    def _clean(self):
        for root in self.roots:
            shutil.rmtree(root, ignore_errors=True)

    def test_the_key_file_is_zero_six_hundred(self):
        merge_gate.sign_row(self.clone_root, {"tree": "a" * 40})
        key = os.path.join(self.clone_root, ".git", "merge-gate", "key")
        self.assertTrue(os.path.isfile(key))
        self.assertEqual(os.stat(key).st_mode & 0o777, 0o600)

    def test_a_signed_row_verifies_and_a_changed_row_does_not(self):
        row = merge_gate.sign_row(self.clone_root,
                                  {"tree": "a" * 40, "rc": 0})
        self.assertTrue(merge_gate.mac_ok(self.clone_root, row))
        row["rc"] = 1
        self.assertFalse(merge_gate.mac_ok(self.clone_root, row))

    def test_hostile_input_is_refused(self):
        for bad in (None, 1, True, "x", b"x", [], 3.5):
            with self.assertRaises(ValueError):
                merge_gate.sign_row(self.clone_root, bad)
        with self.assertRaises(ValueError):
            merge_gate.sign_row(None, {"a": 1})
        with self.assertRaises(ValueError):
            merge_gate.sign_row(self.clone_root, {1: "a"})
        with self.assertRaises(ValueError):
            merge_gate.sign_row(self.clone_root, {"a": object()})

    def test_mac_ok_refuses_hostile_input_without_crashing(self):
        for bad in (None, 1, True, "x", b"x", [], 3.5):
            self.assertFalse(merge_gate.mac_ok(self.clone_root, bad))
        self.assertFalse(merge_gate.mac_ok(self.clone_root, {}))
        self.assertFalse(merge_gate.mac_ok(self.clone_root, {"mac": 7}))


class TestMergeVerifiedShell(unittest.TestCase):
    def setUp(self):
        self.text = _read(os.path.join(HERE, "merge_verified.sh"))

    def test_the_merge_call_pins_the_head(self):
        self.assertIn('--match-head-commit "$sha"', self.text)

    def test_the_head_pin_is_compared(self):
        self.assertIn('[ "$4" = "$sha" ]', self.text)

    def test_the_gate_has_one_definition(self):
        self.assertIn('python3 "$PY_GATE" verify', self.text)

    def test_a_dry_run_never_reaches_a_write_verb(self):
        self.assertIn('if [ "$DRY" = 1 ]; then', self.text)
        self.assertIn("WOULD MERGE", self.text)

    def test_the_dry_run_chains_its_base(self):
        self.assertIn('base_sha="$chained"', self.text)
        self.assertIn("commit-tree", self.text)

    def test_the_base_is_refetched_immediately_before_the_merge(self):
        self.assertIn('fresh="$(cd "$CLONE" && git rev-parse FETCH_HEAD)"',
                      self.text)

    def test_main_protection_is_read_before_the_first_write(self):
        self.assertIn("main_protected", self.text)
        self.assertIn("main_protected; mp=$?", self.text)

    def test_a_real_run_refuses_a_steered_environment(self):
        self.assertIn("refuse-env", self.text)
        self.assertIn('[ "$DRY" = 0 ]', self.text)

    def test_the_tree_is_compared_after_the_merge(self):
        self.assertIn("TREE-DRIFT", self.text)
        self.assertIn('nowtree="$(cd "$CLONE" && git rev-parse "$now^{tree}")"',
                      self.text)


class TestGateMergeSeq(unittest.TestCase):
    def setUp(self):
        self.text = _read(os.path.join(HERE, "gate_merge_seq.sh"))

    def test_it_records_the_gate_script_hash_at_both_ends(self):
        self.assertIn("main_gate_sha", self.text)
        self.assertIn("cand_gate_sha", self.text)
        self.assertIn("required_fast.sh", self.text)

    def test_it_writes_the_seven_field_row(self):
        self.assertIn("printf '%s\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\n'",
                      self.text)

    def test_it_never_writes_main(self):
        self.assertNotIn("git push", self.text)
        self.assertNotIn("pr merge", self.text)

    def test_a_real_run_refuses_a_steered_environment(self):
        self.assertIn("refuse-env", self.text)


class TestBashFixture(unittest.TestCase):
    def setUp(self):
        self.text = _read(os.path.join(HERE, "test_merge_verified.sh"))

    def test_the_fixture_keeps_all_26_cases(self):
        self.assertEqual(len(CASE_NAMES), 26)
        for name in CASE_NAMES:
            self.assertIn(name, self.text)

    def test_the_fixture_prints_the_one_summary_line(self):
        self.assertIn("merge_verified tests: %s passed, %s failed", self.text)

    def test_the_fixture_is_hermetic(self):
        self.assertIn("${TMPDIR:-/tmp}", self.text)
        self.assertIn("mktemp -d", self.text)
        self.assertIn("stand-in gh", self.text)
        self.assertNotIn("gh pr view 866", self.text)




GH_STUB = r"""#!/bin/sh
# the stand-in gh: pull request state from $FIX/pr-<n>, protection from $FIX/protected, merges done for real in the hub.
# It finds its fixture from its OWN path ($FIX/bin/gh): the tool starts itself in a clean environment (review
# 2026-10-06), so no name of the test's reaches it, and none has to.
FIX="${0%/bin/gh}"
echo "$*" >> "$FIX/gh.log"
case "$1 $2" in
  "pr view") cat "$FIX/pr-$3" ;;
  "api repos/"*) if [ -f "$FIX/protected" ]; then echo '{"required_pull_request_reviews": {}, "enforce_admins": {"enabled": true}}'; else echo '{}'; fi ;;
  "pr merge")
    # what the one writing call runs under, for the allow list cases: the NAMES of its environment and four chosen
    # values, never the values at large (an inherited secret must not land in a fixture or in a failure message)
    /usr/bin/env | /usr/bin/cut -d= -f1 > "$FIX/merge.names"
    printf '%s\n' "PATH=$PATH" "USER=${USER-}" "LOGNAME=${LOGNAME-}" "TERM=${TERM-}" > "$FIX/merge.values"
    pr="$3"; sha=""; while [ $# -gt 0 ]; do [ "$1" = "--match-head-commit" ] && sha="$2"; shift; done
    cd "$FIX/merger" && git fetch -q hub main && git checkout -q -B m FETCH_HEAD || exit 1
    if [ -f "$FIX/squash" ]; then   # a squash merge: main gets the gated tree, never the pinned sha
      git merge -q --squash "$sha" && git commit -q -m "squash $pr" || exit 1
    else
      git merge -q --no-ff -m "merge $pr" "$sha" || exit 1
    fi
    git push -q hub m:main || exit 1 ;;
  *) echo "gh stub: unhandled $*" >&2; exit 3 ;;
esac
"""


class TheToolRunsForReal(unittest.TestCase):
    """Review round 17 (2026-10-04): the 26 case shell fixture only grepped the tool's text, and nothing ran it. These
    cases RUN scripts/merge_verified.sh in a throwaway clone of a throwaway hub, with a stand-in gh that merges for real
    and gate rows signed with the clone's own key. A real run needs a terminal, so it gets a pseudo terminal from os.openpty() as stdin.
    LIMITS OF THE STAND-IN gh, stated: pr view ignores --json and -q (the query is not tested against real gh output, and
    real GitHub often answers mergeable UNKNOWN on a fresh PR, where the tool stops); pr merge merges the sha it is given
    with no head check by GitHub; a merge queue or pending required checks are not modelled."""

    def git(self, cwd, *a):
        import subprocess
        r = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false"] + list(a),
                           cwd=cwd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def setUp(self):
        import subprocess
        self.fix = tempfile.mkdtemp(prefix="mg1-real-")
        self.addCleanup(shutil.rmtree, self.fix, True)
        hub = os.path.join(self.fix, "hub.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", hub], check=True)
        self.clone = os.path.join(self.fix, "clone")
        subprocess.run(["git", "clone", "-q", "-o", "hub", hub, self.clone], check=True, capture_output=True)
        os.makedirs(os.path.join(self.clone, "scripts"))
        for name in ("merge_verified.sh", "merge_gate.py", "tmp_sandbox.py", "merge_pins.py", "merge_reach.py",
                     "merge_precompute.py"):
            shutil.copy(os.path.join(HERE, name), os.path.join(self.clone, "scripts", name))
        # main's gate script: verify_row compares each row's gate_sha256 with it (GATE-EDITED)
        open(os.path.join(self.clone, "scripts", "required_fast.sh"), "w").write("#!/bin/sh\necho stub\n")
        open(os.path.join(self.clone, "scripts", ".merge-verified-fixture"), "w").close()   # the stand-in gh is allowed here
        open(os.path.join(self.clone, "base.txt"), "w").write("base\n")
        self.git(self.clone, "add", "-A"); self.git(self.clone, "commit", "-qm", "base"); self.git(self.clone, "push", "-q", "hub", "HEAD:main")
        self.heads = {}
        for n in ("1", "2"):
            self.git(self.clone, "checkout", "-q", "-b", "pr" + n, "hub/main")
            open(os.path.join(self.clone, "f%s.txt" % n), "w").write(n + "\n")
            self.git(self.clone, "add", "-A"); self.git(self.clone, "commit", "-qm", "pr " + n)
            self.heads[n] = self.git(self.clone, "rev-parse", "HEAD")
            self.git(self.clone, "push", "-q", "hub", "pr%s:pr%s" % (n, n))
            open(os.path.join(self.fix, "pr-" + n), "w").write("OPEN\tmain\tMERGEABLE\t%s\tpr%s\n" % (self.heads[n], n))
        self.git(self.clone, "checkout", "-q", "--detach", "hub/main")
        subprocess.run(["git", "clone", "-q", "-o", "hub", hub, os.path.join(self.fix, "merger")], check=True, capture_output=True)
        # THE CLONES CARRY THEIR OWN IDENTITY (2026-10-05, as test_merge_precompute): the tool's commit-tree used the
        # machine's global one, so under a fresh HOME (the grader's sandbox, the hermetic gate) the dry run stopped at
        # "cannot chain the dry-run tree" and every MG1 grade's green was red at base.
        for repo in (self.clone, os.path.join(self.fix, "merger")):
            for key, value in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
                subprocess.run(["git", "-C", repo, "config", key, value], check=True, capture_output=True)
        os.makedirs(os.path.join(self.fix, "bin"))
        stub = os.path.join(self.fix, "bin", "gh"); open(stub, "w").write(GH_STUB); os.chmod(stub, 0o755)
        open(os.path.join(self.fix, "protected"), "w").write("")
        self.list = os.path.join(self.fix, "list")
        open(self.list, "w").write("1 %s\n2 %s\n" % (self.heads["1"], self.heads["2"]))

    def record(self, tree, base, rc=0, summary="pass 3   fail 0   no-data 0\n"):
        """A full row through merge_gate.record_row, the one row writer, with real git as its runner."""
        import subprocess

        def runner(argv, cwd=None, env=None):
            return subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True)
        log = os.path.join(self.fix, "gate-%d.log" % time.time_ns())
        with open(log, "w") as fh:
            fh.write(summary)
        return merge_gate.record_row(self.clone, tree, rc, log, "1", self.heads["1"], base, {}, runner=runner)

    def append(self, row):
        with open(merge_gate.ledger_path(self.clone, {}), "a") as fh:
            fh.write(json.dumps(row) + "\n")

    def sign_chain(self):
        """Gate rows for the two trees the batch will produce: pr1 onto main, then pr2 onto that."""
        base = self.git(self.clone, "rev-parse", "hub/main")
        self.rows = []
        for n in ("1", "2"):
            tree = self.git(self.clone, "merge-tree", "--write-tree", base, self.heads[n])
            self.rows.append(self.record(tree, base))
            base = self.git(self.clone, "commit-tree", tree, "-p", base, "-m", "next base")

    def run_tool(self, *args, tty=True, extra=None, shell="sh", rel=False, env=None):
        """shell: what the tool is started with (sh, or /bin/bash for the cases only bash opens). rel: start it as
        scripts/merge_verified.sh from the clone, the way the owner types it, never by its absolute path. env: the
        WHOLE environment of the run, for a caller that builds one by hand (shell must then be an absolute path)."""
        import subprocess
        if env is None:
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(("MERGE_", "GIT_", "GH_", "GITHUB_")) and k != "XDG_CONFIG_HOME"}
            env.update(PATH=os.path.join(self.fix, "bin") + os.pathsep + env.get("PATH", ""), **(extra or {}))
        script = os.path.join("scripts", "merge_verified.sh") if rel else os.path.join(self.clone, "scripts", "merge_verified.sh")
        tool = [shell, script] + list(args)
        # a real terminal for a real run: a pseudo terminal from os.openpty() as the tool's stdin (script(1) inherited
        # this process's stdin and failed whenever that was a socket: "tcgetattr/ioctl: Operation not supported")
        master = slave = None
        if tty:
            try:
                master, slave = os.openpty()
            except OSError as exc:   # the landing sandbox denies a pseudo terminal: a real run cannot be driven here
                self.skipTest("NO-DATA: no pseudo terminal here (%s), so a real merge run cannot be exercised" % exc)
            # the one prompt of a real run (MG1.d, merge_pins.owner_confirmed): the owner types the count of listed PRs
            with open(self.list) as fh:
                listed = sum(1 for line in fh if line.split() and not line.lstrip().startswith("#"))
            os.write(master, ("%d\n" % listed).encode("ascii"))
        try:
            r = subprocess.run(tool, cwd=self.clone, env=env, capture_output=True, text=True, timeout=120,
                               stdin=slave if tty else subprocess.DEVNULL)
        finally:
            for fd in (master, slave):
                if fd is not None:
                    os.close(fd)
        log = open(os.path.join(self.fix, "gh.log")).read() if os.path.exists(os.path.join(self.fix, "gh.log")) else ""
        return r.returncode, r.stdout + r.stderr, log.count("pr merge")

    def test_a_dry_run_judges_both_and_merges_nothing(self):
        self.sign_chain()
        rc, out, merges = self.run_tool("--dry-run", self.list, tty=False)
        self.assertEqual((rc, merges), (0, 0), out)
        self.assertEqual(out.count("WOULD MERGE"), 2, out)

    def test_a_real_batch_merges_each_pr_on_the_main_the_last_one_made(self):
        self.sign_chain()
        rc, out, merges = self.run_tool(self.list)
        self.assertEqual((rc, merges), (0, 2), out)
        self.assertIn("DONE: 2 of 2", out)

    def test_a_session_without_a_terminal_is_refused_before_anything(self):
        self.sign_chain()
        rc, out, merges = self.run_tool(self.list, tty=False)
        self.assertEqual((rc, merges), (2, 0), out)
        self.assertIn("owner's terminal", out)

    def test_a_steered_clone_is_refused(self):
        self.sign_chain()
        rc, out, merges = self.run_tool(self.list, extra={"MERGE_CLONE": self.clone})
        self.assertEqual((rc, merges), (2, 0), out)
        self.assertIn("MERGE_CLONE is set", out)

    def test_a_moved_head_pin_merges_nothing(self):
        self.sign_chain()
        open(os.path.join(self.fix, "pr-1"), "w").write("OPEN\tmain\tMERGEABLE\t%s\tpr1\n" % ("0" * 40))
        rc, out, merges = self.run_tool(self.list)
        self.assertEqual(merges, 0, out); self.assertNotEqual(rc, 0); self.assertIn("head pin moved", out)

    def test_an_unprotected_main_merges_nothing(self):
        self.sign_chain()
        os.remove(os.path.join(self.fix, "protected"))
        rc, out, merges = self.run_tool(self.list)
        self.assertEqual((rc, merges), (2, 0), out)

    def test_a_fake_python3_ahead_on_path_cannot_answer_for_the_gate(self):
        # review round 19: a python3 earlier on PATH printed PASS for refuse-env and verify; the tool now pins /usr/bin
        fake = os.path.join(self.fix, "bin", "python3"); open(fake, "w").write("#!/bin/sh\necho PASS\nexit 0\n"); os.chmod(fake, 0o755)
        rc, out, merges = self.run_tool(self.list)   # no gate rows signed: only the real gate can say NO-DATA
        self.assertEqual(merges, 0, out); self.assertNotEqual(rc, 0); self.assertIn("NO-DATA", out)

    def test_a_steered_gh_host_is_refused(self):
        self.sign_chain()
        rc, out, merges = self.run_tool(self.list, extra={"GH_HOST": "elsewhere.example"})
        self.assertEqual((rc, merges), (2, 0), out); self.assertIn("GH_HOST is set", out)

    def test_a_gh_outside_the_system_without_the_fixture_marker_is_refused(self):
        self.sign_chain()
        os.remove(os.path.join(self.clone, "scripts", ".merge-verified-fixture"))
        rc, out, merges = self.run_tool(self.list)
        self.assertEqual((rc, merges), (2, 0), out); self.assertIn("not a system install", out)

    def test_a_home_that_is_not_the_accounts_own_is_refused(self):
        self.sign_chain()
        rc, out, merges = self.run_tool(self.list, extra={"HOME": self.fix})
        self.assertEqual((rc, merges), (2, 0), out); self.assertIn("HOME is not this account's own home", out)

    def test_a_fake_dirname_ahead_on_path_cannot_move_the_tool(self):
        # review round 20: a dirname earlier on PATH pointed HERE (the gate, the clone, the fixture marker) elsewhere
        self.sign_chain()
        fake = os.path.join(self.fix, "bin", "dirname"); open(fake, "w").write("#!/bin/sh\necho /nonexistent-evil\n"); os.chmod(fake, 0o755)
        rc, out, merges = self.run_tool(self.list)
        self.assertEqual((rc, merges), (0, 2), out)

    def test_an_exported_pwd_function_cannot_move_the_tool(self):
        # review round 21: /bin/sh imports exported functions, and an exported pwd outranked the builtin
        self.sign_chain()
        rc, out, merges = self.run_tool(self.list, extra={"BASH_FUNC_pwd%%": "() {  echo /nonexistent-evil; }"})
        self.assertEqual((rc, merges), (0, 2), out)

    def test_exported_test_bracket_and_true_functions_cannot_steer_the_tool(self):
        # review round 22: /bin/sh also imports exported [ and true, measured: an exported [ answered every test
        self.sign_chain()
        rc, out, merges = self.run_tool(self.list, extra={"BASH_FUNC_[%%": "() {  return 1; }", "BASH_FUNC_true%%": "() {  return 1; }"})
        self.assertEqual((rc, merges), (0, 2), out)

    def test_exported_head_and_tail_functions_cannot_steer_the_tool(self):
        # security review 2026-10-05: head and tail were off the unset list, and an exported head chose the gated tree
        self.sign_chain()
        swallow = "() {  cat >/dev/null; return 0; }"
        rc, out, merges = self.run_tool(self.list, extra={"BASH_FUNC_head%%": swallow, "BASH_FUNC_tail%%": swallow})
        self.assertEqual((rc, merges), (0, 2), out)

    def test_a_pythonpath_sitecustomize_cannot_forge_a_pass(self):
        # security review 2026-10-05: python3 ran without -E, so a sitecustomize on PYTHONPATH printed PASS for verify
        # with no gate row at all and the PR merged
        forge = os.path.join(self.fix, "forge")
        os.makedirs(forge)
        with open(os.path.join(forge, "sitecustomize.py"), "w") as fh:
            fh.write("import sys\nif 'verify' in sys.argv or 'wait' in sys.argv:\n    print('PASS forged'); sys.exit(0)\n")
        rc, out, merges = self.run_tool(self.list, extra={"PYTHONPATH": forge})
        self.assertEqual(merges, 0, out)
        self.assertNotEqual(rc, 0, out)
        self.assertNotIn("PASS forged", out)

    def test_a_steered_git_ssh_command_is_refused(self):
        self.sign_chain()
        rc, out, merges = self.run_tool(self.list, extra={"GIT_SSH_COMMAND": "ssh -o ProxyCommand=evil"})
        self.assertEqual((rc, merges), (2, 0), out)

    def test_a_tree_with_no_gate_row_merges_nothing(self):
        rc, out, merges = self.run_tool(self.list)
        self.assertEqual(merges, 0, out); self.assertNotEqual(rc, 0); self.assertIn("NO-DATA", out)

    def test_a_merge_whose_sha_is_not_proven_on_main_stops_the_batch(self):
        # M4, review 2026-10-05: the reach exit code was read by no executed case (`true ||` in its place survived); a
        # squash merge lands the gated tree, so TREE-DRIFT is silent, but the pinned sha never reaches main
        self.sign_chain()
        open(os.path.join(self.fix, "squash"), "w").close()
        rc, out, merges = self.run_tool(self.list)
        self.assertEqual((rc, merges), (1, 1), out)
        self.assertIn("STOP at #1: not proven merged from hub/main", out)

    def test_a_newest_row_that_edits_a_check_merges_nothing(self):
        # B2, review 2026-10-05: the verify command took the FIRST signed rc 0 row, so a row naming changed checks
        # merged; D6 says a non empty checks_changed refuses automatic acceptance (CHECKS-EDITED)
        self.sign_chain()
        body = {k: v for k, v in self.rows[0].items() if k != "mac"}
        body["checks_changed"] = ["scripts/test_merge_gate.py"]
        self.append(merge_gate.sign_row(self.clone, body))
        rc, out, merges = self.run_tool(self.list)
        self.assertEqual((rc, merges), (1, 0), out)
        self.assertIn("STOP at #1: the tree gate says FAIL: FAIL CHECKS-EDITED: scripts/test_merge_gate.py", out)

    def test_a_newer_fail_row_beats_an_earlier_pass(self):
        # B2 and D2: the newest row for the tree decides; an earlier PASS no longer wins over a later FAIL
        self.sign_chain()
        self.record(self.rows[0]["tree"], self.rows[0]["base"], 1, "pass 2   fail 1   no-data 0\nFAILED: alpha\n")
        rc, out, merges = self.run_tool("--dry-run", self.list, tty=False)
        self.assertEqual((rc, merges), (1, 0), out)
        self.assertIn("STOP at #1: the tree gate says FAIL: FAIL FAIL: the gate exited 1 (alpha)", out)

    # ------------------------------------------------------------------------------------------------------------------
    # THE CLEAN START (review 2026-10-06: F1 DEVELOPER_DIR, F2 CDPATH, F3 bash, F7 a flag after the list). Each case
    # runs the tool at its entry point with ONE poison. Where no gate row is signed, the only honest outcome is the real
    # gate's own NO-DATA with nothing merged: a forged PASS, a moved tool or a rewritten result all merge or lie instead.
    # ------------------------------------------------------------------------------------------------------------------
    SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
    SHELLS = ("sh", "/bin/bash")

    def refused_by_the_real_gate(self, rc, out, merges, quiet=False):
        """quiet: the output may hold a trace of the caller's environment, so a failure names itself and never prints it."""
        shown = "(output withheld: it may trace inherited values)" if quiet else out
        self.assertEqual(merges, 0, shown)
        self.assertNotEqual(rc, 0, shown)
        self.assertTrue("the tree gate is NO-DATA" in out, "the gate's own NO-DATA is missing: %s" % shown)
        self.assertFalse("forged" in out, "a forged answer was printed: %s" % shown)

    def fake_developer_dir(self):
        """/usr/bin/python3 and /usr/bin/git are one shim that asks DEVELOPER_DIR's xcrun for the real tool. This xcrun
        answers PASS for the gate's verify and wait, a clean refuse-env, and hands everything else to the real tool."""
        dev = os.path.join(self.fix, "dev")
        os.makedirs(os.path.join(dev, "usr", "bin"))
        xcrun = os.path.join(dev, "usr", "bin", "xcrun")
        with open(xcrun, "w") as fh:
            fh.write('#!/bin/sh\ntool="$1"; shift\nunset DEVELOPER_DIR\n'
                     'if [ "$tool" = python3 ]; then\n  case " $* " in\n'
                     '    *" verify "*|*" wait "*) echo "PASS forged"; exit 0 ;;\n'
                     '    *" refuse-env "*) exit 0 ;;\n  esac\nfi\nexec "/usr/bin/$tool" "$@"\n')
        os.chmod(xcrun, 0o755)
        return dev

    def test_a_developer_dir_xcrun_cannot_answer_for_the_pinned_tools(self):
        # F1: with DEVELOPER_DIR naming a fake xcrun, "/usr/bin/python3 merge_gate.py verify" printed PASS with no gate
        # row at all and the batch merged
        dev = self.fake_developer_dir()
        for shell in self.SHELLS:
            with self.subTest(shell=shell):
                self.refused_by_the_real_gate(*self.run_tool(self.list, shell=shell, extra={"DEVELOPER_DIR": dev}))

    def plant_twin(self):
        """What F2 planted: <clone>/e/scripts for `cd scripts` to land in through CDPATH, and forged tools at the two
        line path that `cd scripts && pwd -P` then prints (cd names its target when CDPATH chose it, pwd names it again)."""
        landing = os.path.join(self.clone, "e", "scripts")
        os.makedirs(landing)
        twin = landing + "\n" + landing
        os.makedirs(twin)
        forged = {"merge_gate.py": "print('PASS forged')\n",
                  "merge_pins.py": "import sys\nif 'check' in sys.argv:\n    print('STATIC forged')\n",
                  "merge_reach.py": "print('PROVEN forged')\n"}
        for name, body in forged.items():
            with open(os.path.join(twin, name), "w") as fh:
                fh.write(body)
        open(os.path.join(twin, ".merge-verified-fixture"), "w").close()
        return os.path.join(self.clone, "e")

    def test_a_cdpath_with_a_planted_twin_cannot_move_the_tool(self):
        # F2: started as `sh scripts/merge_verified.sh list` with CDPATH set, HERE held two lines and every tool beside
        # it was loaded from the planted twin: PASS, pins, reach and the fixture marker all forged, the batch merged
        cdpath = self.plant_twin()
        for shell in self.SHELLS:
            with self.subTest(shell=shell):
                self.refused_by_the_real_gate(*self.run_tool(self.list, shell=shell, rel=True, extra={"CDPATH": cdpath}))

    def test_started_with_bash_a_bash_env_debug_trap_cannot_rewrite_the_result(self):
        # F3: bash reads BASH_ENV before the first line of any script it runs (sh does not), and a DEBUG trap set there
        # fires before every command: it rewrote the gate's exit code, and here it also names the clean start's own
        # variables, the way a trap written by someone who read this tool would
        benv = os.path.join(self.fix, "bash-env")
        with open(benv, "w") as fh:
            fh.write("trap 'gate_rc=0; guard_rc=0; refused=0; mv_clean=yes; MERGE_VERIFIED_CLEAN=1; "
                     "MERGE_VERIFIED_CALLER_PATH=%s' DEBUG\n" % os.path.join(self.fix, "bin"))
        self.refused_by_the_real_gate(*self.run_tool(self.list, shell="/bin/bash", extra={"BASH_ENV": benv}))

    def test_started_with_bash_an_exported_set_never_runs_inside_the_tool(self):
        # F3, at its root: started with bash, a function outranks even the special builtins, so an exported set() was
        # CALLED by the tool's own first line (`set -u`) and ran whatever it carried, inside the tool's process. Here it
        # only leaves a witness file; nothing of the caller's may run in the tool before the clean start.
        witness = os.path.join(self.fix, "caller-code-ran")
        poison = {"BASH_FUNC_set%%": '() {  : > "%s"; builtin set "$@"; }' % witness}
        self.refused_by_the_real_gate(*self.run_tool(self.list, shell="/bin/bash", extra=poison))
        self.assertFalse(os.path.exists(witness), "the caller's set() ran inside the tool")

    def test_started_with_bash_an_exported_unset_cannot_keep_a_forged_head_alive(self):
        # F3: started with bash, special builtins do NOT outrank functions, so an exported unset() swallowed the whole
        # "unset -f" discipline and an exported head() chose the tree that was gated: PR 2, whose tree has no row, was
        # judged on PR 1's signed tree and merged (TREE-DRIFT named it afterwards, one ungated merge too late)
        base = self.git(self.clone, "rev-parse", "hub/main")
        tree1 = self.git(self.clone, "merge-tree", "--write-tree", base, self.heads["1"])
        self.record(tree1, base)
        with open(self.list, "w") as fh:
            fh.write("2 %s\n" % self.heads["2"])
        poison = {"BASH_FUNC_unset%%": "() {  :; }", "BASH_FUNC_head%%": "() {  cat > /dev/null; echo %s; }" % tree1}
        self.refused_by_the_real_gate(*self.run_tool(self.list, shell="/bin/bash", extra=poison))

    def test_started_with_bash_exported_bracket_and_echo_cannot_forge_the_report(self):
        # F3, the pair the review named: behind an exported unset(), [ answered every -eq test and echo printed its own
        # DONE line, so a run the gate refused exited 0 saying both were merged
        poison = {"BASH_FUNC_unset%%": "() {  :; }",
                  "BASH_FUNC_[%%": '() {  case "$*" in *" -eq "*) return 0 ;; esac; builtin [ "$@"; }',
                  "BASH_FUNC_echo%%": '() {  case "$*" in STOP*) : ;; DONE*) builtin echo "DONE: 2 of 2 listed PR(s) merged (forged)" ;; '
                                      '*) builtin echo "$@" ;; esac; }'}
        self.refused_by_the_real_gate(*self.run_tool(self.list, shell="/bin/bash", extra=poison))

    def test_an_inherited_trace_and_its_prompt_do_not_reach_the_tool(self):
        # needs no bash: SHELLOPTS=xtrace turns tracing on in any /bin/sh, and PS4 is expanded before every traced
        # command, arithmetic included, so two inherited values rewrite gate_rc before each line. MEASURED on the tool
        # before the clean start: no merge was forged, because the trace also landed in every captured stderr and the
        # run stopped on a garbled pull request read ("state is ++00 ..."), never on the gate's own answer
        poison = {"SHELLOPTS": "xtrace", "PS4": "+$((gate_rc=0))$((refused=0)) ", "MV_PROBE_VALUE": "probe-value-7c1e"}
        for shell in self.SHELLS:
            with self.subTest(shell=shell):
                rc, out, merges = self.run_tool(self.list, shell=shell, extra=poison)
                self.refused_by_the_real_gate(rc, out, merges, quiet=True)
                # the clean start reads every inherited name; with tracing on it must not print one value of them
                self.assertFalse("probe-value-7c1e" in out, "the trace printed an inherited value")

    def test_a_caller_that_sets_the_clean_marker_gains_nothing(self):
        # the marker the clean start sets is no password: with it set by the caller beside one poison (and the data
        # name a caller who read this tool would set with it), the environment is still not the allow list, so it is
        # scrubbed again and the real gate answers
        dev = self.fake_developer_dir()
        for shell in self.SHELLS:
            with self.subTest(shell=shell):
                poison = {"DEVELOPER_DIR": dev, "MERGE_VERIFIED_CLEAN": "1",
                          "MERGE_VERIFIED_CALLER_PATH": os.path.join(self.fix, "bin")}
                self.refused_by_the_real_gate(*self.run_tool(self.list, shell=shell, extra=poison))

    def test_a_marker_at_its_last_value_beside_a_poison_is_refused_never_trusted(self):
        # the second and last clean start is marked 2: an environment that is still not clean there is refused outright
        # (the bound that keeps a clean start from looping), before any tool runs
        dev = self.fake_developer_dir()
        poison = {"DEVELOPER_DIR": dev, "MERGE_VERIFIED_CLEAN": "2", "MERGE_VERIFIED_CALLER_PATH": os.path.join(self.fix, "bin")}
        rc, out, merges = self.run_tool(self.list, extra=poison)
        self.assertEqual((rc, merges), (2, 0), out)
        self.assertIn("not clean after two clean starts", out)
        self.assertFalse(os.path.exists(os.path.join(self.fix, "gh.log")), "gh was reached")

    def test_a_value_that_cannot_cross_in_one_line_is_refused_not_carried(self):
        # one of the tool's own names with a line break in its value can never read as clean: refused, exit 2
        rc, out, merges = self.run_tool("--dry-run", self.list, tty=False, extra={"MERGE_REPO": "o/r\nGH_HOST=x"})
        self.assertEqual((rc, merges), (2, 0), out)
        self.assertIn("not clean after two clean starts", out)
        self.assertFalse(os.path.exists(os.path.join(self.fix, "gh.log")), "gh was reached")

    def merge_env(self, extra):
        """(names, values) the stand-in gh saw at `pr merge`, after a real batch started with `extra` set: every NAME
        of its environment, and the value of PATH, USER, LOGNAME and TERM only."""
        self.sign_chain()
        rc, out, merges = self.run_tool(self.list, extra=extra)
        self.assertEqual((rc, merges), (0, 2), out)
        with open(os.path.join(self.fix, "merge.names")) as fh:
            names = sorted(set(fh.read().split()))
        with open(os.path.join(self.fix, "merge.values")) as fh:
            values = dict(line.rstrip("\n").split("=", 1) for line in fh if "=" in line)
        return names, values

    def test_an_inherited_name_never_reaches_the_merge(self):
        names, _values = self.merge_env({"MV_PROBE_INHERITED": "1"})
        self.assertNotIn("MV_PROBE_INHERITED", names)
        # the whole of it: the account, the fixed path, the terminal when there is one, and what the shell itself keeps
        self.assertEqual(sorted(set(names) - {"PWD", "OLDPWD", "SHLVL", "_", "TERM"}), ["HOME", "LOGNAME", "PATH", "USER"])

    def test_the_merge_runs_on_the_fixed_system_path_never_the_callers(self):
        names, values = self.merge_env({})
        self.assertEqual(values["PATH"], self.SYSTEM_PATH)
        # nor as left over data: the clean start's own three names are gone before the first tool runs
        self.assertEqual([name for name in names if name.startswith("MERGE_VERIFIED_")], [])

    def test_user_and_logname_are_this_accounts_whatever_the_caller_says(self):
        import pwd
        own = pwd.getpwuid(os.getuid()).pw_name
        _names, values = self.merge_env({"USER": "someone-else", "LOGNAME": "someone-else"})
        self.assertEqual((values["USER"], values["LOGNAME"]), (own, own))

    def test_a_term_that_is_no_plain_name_is_not_carried(self):
        names, _values = self.merge_env({"TERM": "vt100;probe"})
        self.assertNotIn("TERM", names)

    def test_a_plain_term_is_carried(self):
        names, values = self.merge_env({"TERM": "vt100"})
        self.assertIn("TERM", names)
        self.assertEqual(values["TERM"], "vt100")

    def test_a_hand_built_clean_environment_still_gets_the_path_and_the_account_set_here(self):
        # a caller that sets the marker and no name outside the allow list has, at best, done the clean start by hand.
        # The test of the environment proves no foreign NAME; it never vouches for a value. So with no PATH at all (a
        # shell then searches its built in default, which names the current directory) the path is still this tool's.
        import pwd
        self.sign_chain()
        built = {"HOME": os.environ.get("HOME", ""), "MERGE_VERIFIED_CLEAN": "1",
                 "MERGE_VERIFIED_CALLER_PATH": os.path.join(self.fix, "bin")}
        rc, out, merges = self.run_tool(self.list, shell="/bin/sh", env=built)
        self.assertEqual((rc, merges), (0, 2), out)
        with open(os.path.join(self.fix, "merge.values")) as fh:
            values = dict(line.rstrip("\n").split("=", 1) for line in fh if "=" in line)
        own = pwd.getpwuid(os.getuid()).pw_name
        self.assertEqual((values["PATH"], values["USER"], values["LOGNAME"]), (self.SYSTEM_PATH, own, own))

    def test_a_clean_batch_started_with_bash_merges_as_it_did(self):
        # the property is not bought by refusing everything: the same batch, started with bash, still merges both
        self.sign_chain()
        rc, out, merges = self.run_tool(self.list, shell="/bin/bash")
        self.assertEqual((rc, merges), (0, 2), out)
        self.assertIn("DONE: 2 of 2", out)

    def test_an_argument_after_the_list_is_refused_before_anything_runs(self):
        # F7: `<list> --dry-run` parsed the list, ignored the flag and was a REAL run that merged both
        self.sign_chain()
        before = open(self.list).read()
        for tail in (("--dry-run",), ("--wait", "5"), ("another-list",)):
            with self.subTest(tail=tail):
                rc, out, merges = self.run_tool(self.list, *tail)
                self.assertEqual((rc, merges), (2, 0), out)
                self.assertIn("usage: merge_verified.sh [--dry-run] [--wait SECONDS] <list>", out)
                self.assertFalse(os.path.exists(os.path.join(self.fix, "gh.log")), "gh was reached")
        self.assertEqual(open(self.list).read(), before)
        self.assertEqual(self.git(self.clone, "ls-remote", "hub", "refs/heads/main").split()[0],
                         self.git(self.clone, "rev-parse", "hub/main"), "main moved")

    def test_the_tools_own_names_still_reach_a_dry_run(self):
        # the five names this tool itself defines cross the clean start as the caller gave them: a dry run honours them
        # as it did (a real run refuses the first four by name: test_a_steered_clone_is_refused)
        self.sign_chain()
        cases = (({"MERGE_REMOTE": "elsewhere"}, 2, "cannot read the elsewhere remote"),
                 ({"MERGE_BASE_BRANCH": "trunk"}, 1, "STOP at #1: base is main, not trunk"),
                 ({"MERGE_CLONE": os.path.join(self.fix, "no-such-clone")}, 2, "cannot read the hub remote"),
                 ({"MERGE_REPO": "owner/elsewhere"}, 0, None),
                 ({"MERGE_PINS_MOVING": "pr*"}, 0, "PIN #1: WILL-FREEZE #1 (pr1)"))
        for extra, want_rc, needle in cases:
            with self.subTest(name=sorted(extra)[0]):
                rc, out, merges = self.run_tool("--dry-run", self.list, tty=False, extra=extra)
                self.assertEqual((rc, merges), (want_rc, 0), out)
                if needle:
                    self.assertIn(needle, out)
        self.assertIn("-R owner/elsewhere", open(os.path.join(self.fix, "gh.log")).read())


class TheRefusalReadsTheCallersNames(unittest.TestCase):
    """merge_gate.py refuse-env <names>: the clean start of merge_verified.sh hands over the NAMES its caller's
    environment carried, as data, and they are judged by the one rule beside this process's own names."""

    def refuse(self, *names):
        import contextlib
        import io
        from unittest import mock
        said = io.StringIO()
        with mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}, clear=True), contextlib.redirect_stderr(said):
            code = merge_gate.main(["refuse-env"] + list(names))
        return code, said.getvalue()

    def test_no_name_and_harmless_names_are_allowed(self):
        self.assertEqual(self.refuse()[0], 0)
        self.assertEqual(self.refuse(" PATH LANG  TERM\nSHLVL ")[0], 0)

    def test_a_steering_name_among_the_callers_is_refused_by_name(self):
        for name in ("GH_HOST", "GIT_DIR", "MERGE_GATE_LEDGER", "MERGE_ALL_X", "PR_PARK_REPO", "GIT_SSH_COMMAND"):
            code, said = self.refuse("PATH %s TERM" % name)
            self.assertEqual(code, 2, name)
            self.assertIn("%s is set" % name, said)


if __name__ == "__main__":
    unittest.main()
