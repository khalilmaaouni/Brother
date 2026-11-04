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

    def test_every_base_bound_call_names_the_base(self):
        # MERGE2.a: each call that reads or writes against the base is handed the one chosen by --base
        # the ref is read from stdout alone: an interpreter warning on stderr (xcrun_db, under a sandboxed temp folder)
        # once became part of it and stopped every run in the hermetic pre-push check
        for needle in ('BASE_REF="$(python3 "$PY_GATE" base-ref "$REMOTE" "$BRANCH" 2>/dev/null)"',
                       'case "$BASE_REF" in *"$MV_NL"*|\'\') BASE_REF="" ;; refs/remotes/*) : ;; *) BASE_REF="" ;; esac',
                       # a refusal always names its reason, even when stderr carried no NO-DATA line
                       '${why:-merge_gate.py base-ref printed no single refs/remotes/ line}',
                       'git rev-parse --verify --quiet "$BASE_REF^{commit}"',
                       'protected ${GH:+--gh "$GH"} "$REPO" "$BRANCH"',
                       'verify --base "$BRANCH" "$CLONE" "$tree"',
                       'wait "$CLONE" "$tree" "$WAIT" "$BRANCH"',
                       'open ${GH:+--gh "$GH"} "$REPO" "$pr" "$sha" "$pin" "$BRANCH"',
                       'git fetch --quiet --no-tags "$REMOTE" "refs/heads/$BRANCH"',
                       '"${merged_state:-UNKNOWN}" "$BRANCH"'):
            self.assertIn(needle, self.text)
        self.assertNotIn("MERGE_BASE_BRANCH=", self.text)   # it never crosses the clean start

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
  "pr view")
    # MERGE1.a round 1: $FIX/inject-after N appends the line in $FIX/inject-line to the file named in $FIX/inject-target
    # as the (N+1)th read of the PR starts (a review FAIL recorded while the owner was at the prompt)
    if [ -f "$FIX/inject-after" ]; then
      echo x >> "$FIX/iviews-$3"
      if [ "$(wc -l < "$FIX/iviews-$3")" -eq "$(( $(cat "$FIX/inject-after") + 1 ))" ]; then
        cat "$FIX/inject-line" >> "$(cat "$FIX/inject-target")"
      fi
    fi
    # MERGE2.a review: $FIX/retarget-after N turns the PR's base to main from its (N+1)th read on, a retarget that
    # lands between the tool's last read and its merge unless the tool reads the PR again right before the merge
    if [ -f "$FIX/retarget-after" ]; then
      echo x >> "$FIX/views-$3"
      if [ "$(wc -l < "$FIX/views-$3")" -gt "$(cat "$FIX/retarget-after")" ]; then
        awk -F'\t' -v OFS='\t' '{ $2 = "main"; print }' "$FIX/pr-$3"; exit 0
      fi
    fi
    cat "$FIX/pr-$3" ;;
  "api repos/"*) if [ -f "$FIX/protected" ]; then echo '{"required_pull_request_reviews": {}, "enforce_admins": {"enabled": true}}'; else echo '{}'; fi ;;
  "pr merge")
    # what the one writing call runs under, for the allow list cases: the NAMES of its environment and four chosen
    # values, never the values at large (an inherited secret must not land in a fixture or in a failure message)
    [ -f "$FIX/nomerge" ] && exit 1   # a merge the host refuses (MERGE1.a: no merge record may follow it)
    /usr/bin/env | /usr/bin/cut -d= -f1 > "$FIX/merge.names"
    printf '%s\n' "PATH=$PATH" "USER=${USER-}" "LOGNAME=${LOGNAME-}" "TERM=${TERM-}" > "$FIX/merge.values"
    pr="$3"; sha=""; while [ $# -gt 0 ]; do [ "$1" = "--match-head-commit" ] && sha="$2"; shift; done
    base="$(cut -f2 "$FIX/pr-$pr")"   # the PR's own base (MERGE2.a): main, or the named dev line
    cd "$FIX/merger" && git fetch -q hub "refs/heads/$base" && git checkout -q -B m FETCH_HEAD || exit 1
    if [ -f "$FIX/squash" ]; then   # a squash merge: main gets the gated tree, never the pinned sha
      git merge -q --squash "$sha" && git commit -q -m "squash $pr" || exit 1
    else
      git merge -q --no-ff -m "merge $pr" "$sha" || exit 1
    fi
    git push -q hub "m:refs/heads/$base" || exit 1 ;;
  *) echo "gh stub: unhandled $*" >&2; exit 3 ;;
esac
"""


def whole_gate_row(clone, head, status="PASS", code_hash="1" * 64):
    """Append one whole gate row for head's tree through gate_ledger.append_ledger, the one writer; returns its id."""
    import gate_ledger
    import subprocess

    def git(*a):
        return subprocess.run(["git", "-C", clone] + list(a), capture_output=True, text=True, check=True).stdout.strip()
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir")
    tree = git("rev-parse", head + "^{tree}")
    whole = os.path.join(common, "brother-whole-gate.jsonl")
    now = time.time()
    gate_ledger.append_ledger(whole, {
        "schema": 1, "cut_id": gate_ledger.NO_CUT, "cut_version": "UNKNOWN", "gate_name": gate_ledger.WHOLE_GATE,
        "gate_argv": ["sh", "scripts/required_fast.sh"], "status": status, "exit_code": 0 if status == "PASS" else 1,
        "started_unix": now, "ended_unix": now, "duration_s": 0, "worktree_key": "merge1a-fixture", "tree_sha": tree,
        "inputs_hash": None, "code_hash": code_hash, "tool_versions": {}, "verdict_source": "full", "cache_key": None,
        "deadline_unix": None})
    with open(whole, encoding="utf-8") as fh:
        return gate_ledger._line_hash(json.loads(fh.read().splitlines()[-1]))


def sign_review(clone, pr, head, line, reviewer="reviewer-r", verdict="PASS", gate=True):
    """MERGE1.a: what a real run now needs beside its gate row, for one pull request: a whole gate PASS row of the head's
    tree (real, non zero code_hash) and a review record of exactly that head written by THE ONE WRITER
    (merge_gate.append_review_record, the review-record command): builders derived from git, row chained. Returns the
    whole gate id (None when gate is False). The cases that merge for real call this for each listed head."""
    import subprocess

    def runner(argv, cwd=None, env=None):
        return subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    gate_id = whole_gate_row(clone, head) if gate else None
    merge_gate.append_review_record(clone, str(pr), head, line, reviewer, verdict, runner=runner)
    return gate_id


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
        # a root commit under the base: the line a review names must not be the root (MERGE1.a round 1)
        self.git(self.clone, "commit", "-q", "--allow-empty", "-m", "init")
        os.makedirs(os.path.join(self.clone, "scripts"))
        for name in ("merge_verified.sh", "merge_gate.py", "tmp_sandbox.py", "merge_pins.py", "merge_reach.py",
                     "merge_precompute.py", "gate_ledger.py"):
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

    def sign_chain(self, ref="hub/main"):
        """Gate rows for the two trees the batch will produce: pr1 onto the base at `ref`, then pr2 onto that."""
        base = self.git(self.clone, "rev-parse", ref)
        self.rows = []
        for n in ("1", "2"):
            tree = self.git(self.clone, "merge-tree", "--write-tree", base, self.heads[n])
            self.rows.append(self.record(tree, base))
            base = self.git(self.clone, "commit-tree", tree, "-p", base, "-m", "next base")
        # MERGE1.a: a real run also needs a recorded review of each head, on the release line the heads were cut from
        line = self.git(self.clone, "rev-parse", "hub/main")
        for n in ("1", "2"):
            sign_review(self.clone, n, self.heads[n], line)

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
                self.assertIn("usage: merge_verified.sh [--dry-run] [--wait SECONDS] [--base NAME] <list>", out)
                self.assertFalse(os.path.exists(os.path.join(self.fix, "gh.log")), "gh was reached")
        self.assertEqual(open(self.list).read(), before)
        self.assertEqual(self.git(self.clone, "ls-remote", "hub", "refs/heads/main").split()[0],
                         self.git(self.clone, "rev-parse", "hub/main"), "main moved")

    def test_the_tools_own_names_still_reach_a_dry_run(self):
        # the four names this tool itself defines cross the clean start as the caller gave them: a dry run honours them
        # as it did (a real run refuses the first three by name: test_a_steered_clone_is_refused). MERGE_BASE_BRANCH
        # no longer crosses (MERGE2.a): a dry run refuses it too, because the base is chosen by --base alone
        self.sign_chain()
        cases = (({"MERGE_REMOTE": "elsewhere"}, 2, "cannot read the elsewhere remote"),
                 ({"MERGE_BASE_BRANCH": "trunk"}, 2, "MERGE_BASE_BRANCH is set"),
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


    # -----------------------------------------------------------------------------------------------------------------
    # MERGE2.a: a named dev line as the base (owner ruling 2026-11-01, "Yes, allow it")
    # -----------------------------------------------------------------------------------------------------------------
    DEV = "plan/1.1.9-line-2026-10-25"
    DEV_REF = "refs/remotes/hub/plan/1.1.9-line-2026-10-25"

    def dev_line(self):
        """The named dev line on the hub: one commit past main that also edits scripts/required_fast.sh, so a dev line
        row is judged against the dev line's own gate script (compared at main it reads GATE-EDITED). Both PRs are
        retargeted to it."""
        self.git(self.clone, "checkout", "-q", "-B", "dev", "hub/main")
        with open(os.path.join(self.clone, "scripts", "required_fast.sh"), "w") as fh:
            fh.write("#!/bin/sh\necho dev line stub\n")
        with open(os.path.join(self.clone, "dev.txt"), "w") as fh:
            fh.write("dev\n")
        self.git(self.clone, "add", "-A"); self.git(self.clone, "commit", "-qm", "dev line")
        self.git(self.clone, "push", "-q", "hub", "dev:refs/heads/" + self.DEV)
        self.git(self.clone, "fetch", "-q", "hub")
        self.git(self.clone, "checkout", "-q", "--detach", "hub/main")
        for n in ("1", "2"):
            with open(os.path.join(self.fix, "pr-" + n), "w") as fh:
                fh.write("OPEN\t%s\tMERGEABLE\t%s\tpr%s\n" % (self.DEV, self.heads[n], n))
        return self.git(self.clone, "rev-parse", self.DEV_REF)

    def gh_log(self):
        path = os.path.join(self.fix, "gh.log")
        if not os.path.exists(path):
            return ""
        with open(path) as fh:
            return fh.read()

    def test_a_dev_line_pr_with_a_row_for_its_merged_tree_passes_the_dry_run(self):
        dev = self.dev_line()
        self.sign_chain(self.DEV_REF)
        rc, out, merges = self.run_tool("--dry-run", "--base", self.DEV, self.list, tty=False)
        self.assertEqual((rc, merges), (0, 0), out)
        self.assertEqual(out.count("WOULD MERGE"), 2, out)
        self.assertIn("BASE: %s at %s (%s)" % (self.DEV, dev, self.DEV_REF), out)
        self.assertIn("WOULD MERGE #1 at %s onto %s (tree %s)" % (self.heads["1"], dev, self.rows[0]["tree"]), out)
        # the branch protection read is the dev line's, never main's
        self.assertIn("branches/%s/protection" % self.DEV, self.gh_log())
        self.assertNotIn("branches/main/protection", self.gh_log())

    def test_a_real_dev_line_batch_merges_into_the_dev_line_and_never_main(self):
        self.dev_line()
        self.sign_chain(self.DEV_REF)
        main_before = self.git(self.clone, "ls-remote", "hub", "refs/heads/main").split()[0]
        rc, out, merges = self.run_tool("--base", self.DEV, self.list)
        self.assertEqual((rc, merges), (0, 2), out)
        self.assertIn("MERGING INTO: %s (%s)" % (self.DEV, self.DEV_REF), out)
        self.assertEqual(out.count("PROVEN #"), 2, out)   # reachability proven from the dev line
        self.assertEqual(self.git(self.clone, "ls-remote", "hub", "refs/heads/main").split()[0], main_before)
        tip = self.git(self.clone, "ls-remote", "hub", "refs/heads/" + self.DEV).split()[0]
        self.git(self.clone, "fetch", "-q", "hub")
        self.assertEqual(self.git(self.clone, "rev-parse", tip + "^{tree}"), self.rows[1]["tree"])

    def test_a_base_not_on_the_list_is_refused_by_name(self):
        self.sign_chain()
        for base in ("trunk", "hub/main", "refs/heads/main", "refs/remotes/hub/main", "Main", "main ",
                     "plan/1.1.9-line-2026-10-24", self.DEV + "/x"):
            for dry in (True, False):
                with self.subTest(base=base, dry=dry):
                    args = (["--dry-run"] if dry else []) + ["--base", base, self.list]
                    rc, out, merges = self.run_tool(*args, tty=not dry)
                    self.assertEqual((rc, merges), (2, 0), out)
                    self.assertIn("is not a named merge base", out)
                    self.assertIn("main, " + self.DEV, out)
                    self.assertEqual(self.gh_log(), "", "gh was reached")

    def test_merge_base_branch_in_the_environment_is_still_refused(self):
        self.sign_chain()
        for value in ("main", self.DEV, "trunk"):
            for dry in (True, False):
                with self.subTest(value=value, dry=dry):
                    args = (["--dry-run"] if dry else []) + [self.list]
                    rc, out, merges = self.run_tool(*args, tty=not dry, extra={"MERGE_BASE_BRANCH": value})
                    self.assertEqual((rc, merges), (2, 0), out)
                    self.assertIn("MERGE_BASE_BRANCH is set", out)
                    self.assertEqual(self.gh_log(), "", "gh was reached")

    def test_a_row_for_the_main_tree_does_not_satisfy_a_dev_line_merge(self):
        self.dev_line()
        self.sign_chain("hub/main")   # signed rows for the trees main would have, not the dev line's
        for dry in (True, False):
            with self.subTest(dry=dry):
                rc, out, merges = self.run_tool(*(["--dry-run"] if dry else []) + ["--base", self.DEV, self.list],
                                                tty=not dry)
                self.assertEqual((rc, merges), (1, 0), out)
                self.assertNotIn("WOULD MERGE", out)
                self.assertIn("STOP at #1: the tree gate is NO-DATA", out)
                self.assertIn("no row for tree", out)

    def test_a_dev_line_pr_listed_for_main_is_refused(self):
        self.dev_line()
        self.sign_chain(self.DEV_REF)
        rc, out, merges = self.run_tool("--dry-run", self.list, tty=False)
        self.assertEqual((rc, merges), (1, 0), out)
        self.assertIn("STOP at #1: base is %s, not main" % self.DEV, out)

    def test_a_wait_on_a_base_the_precompute_worker_does_not_gate_is_refused(self):
        self.dev_line()
        self.sign_chain(self.DEV_REF)
        rc, out, merges = self.run_tool("--dry-run", "--wait", "5", "--base", self.DEV, self.list, tty=False)
        self.assertEqual((rc, merges), (2, 0), out)
        self.assertIn("gates main only", out)
        self.assertEqual(self.gh_log(), "", "gh was reached")

    def test_a_retarget_after_the_last_check_is_read_again_right_before_the_merge(self):
        # MERGE2.a review (2): --match-head-commit pins the head, never the base, so the PR is read again right before
        # gh pr merge; here GitHub answers the dev line for three reads (pin check, entry, full read) and main after
        self.dev_line()
        self.sign_chain(self.DEV_REF)
        with open(self.list, "w") as fh:
            fh.write("1 %s\n" % self.heads["1"])
        with open(os.path.join(self.fix, "retarget-after"), "w") as fh:
            fh.write("3\n")
        rc, out, merges = self.run_tool("--base", self.DEV, self.list)
        self.assertEqual((rc, merges), (1, 0), out)
        self.assertIn("STOP at #1: base is main, not %s" % self.DEV, out)

    def test_a_tag_named_like_the_base_cannot_answer_the_refetch(self):
        # the re-fetch before the merge reads refs/heads/<base>: a short name is matched against refs/tags/<base>
        # before refs/heads/<base>, so a tag of the base's name at another commit would read as "base moved"
        self.dev_line()
        self.sign_chain(self.DEV_REF)
        main_sha = self.git(self.clone, "rev-parse", "hub/main")
        self.git(self.clone, "push", "-q", "hub", "%s:refs/tags/%s" % (main_sha, self.DEV))
        rc, out, merges = self.run_tool("--base", self.DEV, self.list)
        self.assertEqual((rc, merges), (0, 2), out)
        self.assertNotIn("base moved under the pin", out)


class TheBaseIsNamed(unittest.TestCase):
    """MERGE2.a: merge_gate.base_ref is the ONE allow list of merge bases; every caller turns a name into a ref there."""

    DEV = "plan/1.1.9-line-2026-10-25"

    def test_each_allowed_base_is_its_full_remote_ref(self):
        self.assertEqual(merge_gate.ALLOWED_BASES, ("main", self.DEV))
        self.assertEqual(merge_gate.MAIN_REF, "refs/remotes/hub/main")
        self.assertEqual(merge_gate.base_ref("main"), "refs/remotes/hub/main")
        self.assertEqual(merge_gate.base_ref(self.DEV, "hub"), "refs/remotes/hub/" + self.DEV)

    def test_any_other_base_is_refused_by_name(self):
        for bad in ("trunk", "hub/main", "refs/heads/main", "Main", "main/", " main", self.DEV + "/x", "", " ",
                    None, True, b"main", 1):
            with self.subTest(bad=bad), self.assertRaises(merge_gate.MergeGateError):
                merge_gate.base_ref(bad)

    def test_a_remote_that_is_no_plain_name_is_refused(self):
        for bad in ("a b", "-x", "../x", "x/y", "", None):
            with self.subTest(bad=bad), self.assertRaises(merge_gate.MergeGateError):
                merge_gate.base_ref("main", bad)

    def test_verify_tree_refuses_an_unknown_base_before_reading_anything(self):
        with self.assertRaises(merge_gate.MergeGateError) as caught:
            merge_gate.verify_tree("/no/such/clone", "a" * 40, {}, lambda *a, **k: None, base="trunk")
        self.assertIn("not a named merge base", str(caught.exception))

    def test_the_command_line_names_the_ref_or_refuses(self):
        import contextlib
        import io
        said, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(said), contextlib.redirect_stderr(err):
            self.assertEqual(merge_gate.main(["base-ref", "hub", self.DEV]), 0)
            self.assertEqual(merge_gate.main(["base-ref", "hub", "trunk"]), 2)
            self.assertEqual(merge_gate.main(["base-ref", "hub"]), 2)
            self.assertEqual(merge_gate.main(["verify", "--base", "trunk", "/no/such/clone", "a" * 40],
                                             runner=lambda *a, **k: None), 2)
        self.assertEqual(said.getvalue(), "refs/remotes/hub/%s\n" % self.DEV)
        self.assertIn("trunk", err.getvalue())

    def test_reachability_and_the_wait_refuse_an_unknown_base(self):
        import merge_precompute
        import merge_reach
        with self.assertRaises(merge_reach.MergeReachError):
            merge_reach.prove_merged("/x", [{"pr": 1, "sha": "a" * 40, "tree": "b" * 40}], lambda p: "OPEN",
                                     base="trunk")
        with self.assertRaises(merge_gate.MergeGateError):
            merge_precompute.wait_ready("/x", "a" * 40, 0, {}, lambda *a, **k: None, base="trunk")


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
        for name in ("GH_HOST", "GIT_DIR", "MERGE_GATE_LEDGER", "MERGE_ALL_X", "PR_PARK_REPO", "PR_PARK_NOW", "GIT_SSH_COMMAND"):
            code, said = self.refuse("PATH %s TERM" % name)
            self.assertEqual(code, 2, name)
            self.assertIn("%s is set" % name, said)


class ReviewRecordEdges(unittest.TestCase):
    """MERGE1.a, the edges the frozen ReviewRecordRequired class does not isolate, and round 1's rules (2026-11-01): a
    review cannot be self signed. Rows here are written by THE ONE WRITER (merge_gate.append_review_record) and then
    broken one field at a time with edit_last, so each case trips exactly one guard. review_check is driven directly
    (the same function the tool's review-check command calls) over one real throwaway clone; the cases that run the
    tool say so."""

    def setUp(self):
        h = TheToolRunsForReal("test_a_dry_run_judges_both_and_merges_nothing")
        h.setUp()
        self.addCleanup(h.doCleanups)
        self.h = h
        for name in sorted(os.listdir(HERE)):
            if name.endswith(".py") and (name.startswith("merge_") or name == "gate_ledger.py"):
                shutil.copy(os.path.join(HERE, name), os.path.join(h.clone, "scripts", name))
        self.head = h.heads["1"]
        self.base = h.git(h.clone, "rev-parse", "hub/main")
        self.common = os.path.join(h.clone, ".git")
        self.reviews = os.path.join(self.common, REVIEW_RECORDS_FILE)
        self.merge_records = os.path.join(self.common, MERGE_RECORDS_FILE)

    def runner(self, argv, cwd=None, env=None):
        import subprocess
        return subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True)

    def check(self, head=None, base=None, pr="1", strict=None):
        return merge_gate.review_check(self.h.clone, pr, head or self.head, base or self.base, runner=self.runner,
                                       strict=strict)

    def sign(self, **over):
        return sign_review(self.h.clone, over.pop("pr", 1), over.pop("head", self.head), over.pop("line", self.base), **over)

    def rows(self):
        with open(self.reviews, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh.read().splitlines() if line.strip()]

    def write_rows(self, rows):
        with open(self.reviews, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, sort_keys=True) + "\n")

    def edit_last(self, **over):
        """Change fields of the LAST row only: its own content is no part of the chain (the next row's prev_hash covers
        it), so the file still chains and only the guard on the changed field can refuse."""
        rows = self.rows()
        rows[-1].update(over)
        self.write_rows(rows)

    def chained_row(self, **over):
        """The JSON line of a row like the last one with fields changed, chained after it."""
        import gate_ledger
        last = self.rows()[-1]
        return json.dumps(dict(last, prev_hash=gate_ledger._line_hash(last), **over), sort_keys=True) + "\n"

    def refused(self, why=None, **kw):
        with self.assertRaises(merge_gate.MergeGateError) as caught:
            self.check(**kw)
        if why:
            self.assertIn(why, str(caught.exception))
        return caught.exception

    def test_a_valid_record_passes_and_names_where_its_builders_came_from(self):
        self.sign()
        said = self.check()
        self.assertTrue(said.startswith("PASS: reviewed by reviewer-r"), said)
        self.assertIn("claim store names no builders", said)

    def test_a_missing_review_file_is_a_refusal(self):
        self.refused("does not exist")

    def test_a_recorded_tree_that_is_not_the_heads_refuses_though_its_gate_record_is(self):
        # isolates the tree comparison: the whole gate PASS id is for the head's own tree, only the record's tree differs
        self.sign()
        self.edit_last(tree=self.h.git(self.h.clone, "rev-parse", self.base + "^{tree}"))
        self.refused("head's tree")

    def test_a_whole_gate_pass_for_another_tree_refuses_though_the_recorded_tree_is_the_heads(self):
        # isolates the gate record's tree: the review names the head's own tree, its whole_gate id is a PASS of ANOTHER
        other_gate = self.sign(pr=2, head=self.h.heads["2"])
        self.sign()
        self.edit_last(whole_gate=other_gate)
        self.refused("is for tree")

    def test_a_torn_line_or_a_non_object_line_anywhere_in_the_file_is_no_data(self):
        for bad in (b"{not json\n", b"[1, 2]\n", b"\xff\xfe\n"):
            with self.subTest(bad=bad):
                if os.path.exists(self.reviews):
                    os.remove(self.reviews)
                self.sign()
                with open(self.reviews, "ab") as fh:
                    fh.write(bad)
                with self.assertRaises(merge_gate.MergeNoData):
                    self.check()

    def test_an_unreadable_review_path_is_no_data(self):
        os.mkdir(self.reviews)   # a directory where the file should be
        with self.assertRaises(merge_gate.MergeNoData):
            self.check()

    def test_a_line_the_head_carries_but_the_base_does_not_is_refused(self):
        # the head itself is an ancestor of the head and is not an ancestor of the base
        self.sign()
        self.edit_last(line=self.head)
        self.refused("base")

    def test_a_blank_or_missing_builder_entry_is_refused(self):
        self.sign()
        for bad in ([""], ["  "], ["builder-a", "\u200d"], None, "builder-a", [1]):
            with self.subTest(bad=bad):
                self.edit_last(builders=bad)
                self.refused("builders")

    def test_a_lookalike_of_a_builder_is_that_builder(self):
        self.sign()
        # declared builders that normalise to the reviewer (NFKC, no Unicode Cf, case folded, blanks collapsed)
        for reviewer, declared in ((" Builder-A ", ["builder-a"]), ("\uff42\uff55\uff49\uff4c\uff44\uff45\uff52", ["builder"]),
                                   ("build\u200der", ["builder"])):
            with self.subTest(reviewer=reviewer):
                self.edit_last(reviewer=reviewer, builders=declared)
                self.refused("one of the builders")

    def test_a_cyrillic_lookalike_reviewer_is_no_id_at_all(self):
        self.sign()
        self.edit_last(reviewer="reviewer-\u0430", builders=["reviewer-a"])   # Cyrillic a next to Latin a
        self.refused("plain ASCII")

    def test_the_builders_are_derived_from_git_whatever_the_record_declares(self):
        self.sign()
        for reviewer in ("t", "T", "t@t"):   # the author and committer name and email of the head's commit
            with self.subTest(reviewer=reviewer):
                self.edit_last(reviewer=reviewer, builders=["someone-else"])
                self.refused("one of the builders")

    def test_the_one_writer_refuses_a_self_signed_review(self):
        self.whole = whole_gate_row(self.h.clone, self.head)
        for kw, why in ((dict(reviewer="t"), "one of the builders"), (dict(reviewer="T@t"), "one of the builders"),
                        (dict(reviewer="r\u0430"), "plain ASCII"), (dict(reviewer=""), "must not be empty"), (dict(reviewer=" ‍ "), "no reviewer"),
                        (dict(verdict="MAYBE"), "PASS or FAIL"), (dict(line=self.head), "no commit")):
            with self.subTest(**kw):
                args = dict(reviewer="reviewer-r", verdict="PASS", line=self.base)
                args.update(kw)
                with self.assertRaises(merge_gate.MergeGateError) as caught:
                    merge_gate.append_review_record(self.h.clone, "1", self.head, args["line"], args["reviewer"],
                                                    args["verdict"], runner=self.runner)
                self.assertIn(why, str(caught.exception))
        self.assertFalse(os.path.exists(self.reviews), "a refused review left a row")

    def test_the_one_writer_needs_the_newest_whole_gate_row_to_be_a_pass_with_a_real_code_hash(self):
        def write():
            return merge_gate.append_review_record(self.h.clone, "1", self.head, self.base, "reviewer-r", "PASS",
                                                   runner=self.runner)
        with self.assertRaises(merge_gate.MergeGateError) as caught:
            write()
        self.assertIn("no whole gate record", str(caught.exception))
        whole_gate_row(self.h.clone, self.head, code_hash="0" * 64)
        with self.assertRaises(merge_gate.MergeGateError) as caught:
            write()
        self.assertIn("real code_hash", str(caught.exception))
        whole_gate_row(self.h.clone, self.head, status="FAIL")
        with self.assertRaises(merge_gate.MergeGateError):
            write()
        self.assertFalse(os.path.exists(self.reviews))
        whole_gate_row(self.h.clone, self.head)
        write()
        self.assertEqual(len(self.rows()), 1)

    def test_the_one_writer_chains_every_row_and_records_the_derived_builders(self):
        import gate_ledger
        self.sign()
        self.sign(reviewer="reviewer-s", gate=False)
        first, second = self.rows()
        self.assertEqual(first["prev_hash"], merge_gate.GENESIS_HASH)
        self.assertEqual(second["prev_hash"], gate_ledger._line_hash(first))
        self.assertIn("t", first["builders"])
        self.assertIn("t@t", first["builders"])

    def test_the_command_line_writer_and_the_checker_agree(self):
        import contextlib
        import io
        whole_gate_row(self.h.clone, self.head)
        said, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(said), contextlib.redirect_stderr(err):
            code = merge_gate.main(["review-record", self.h.clone, "1", self.head, self.base, "reviewer-r", "PASS"],
                                   runner=self.runner)
            refused = merge_gate.main(["review-record", self.h.clone, "1", self.head, self.base, "t", "PASS"],
                                      runner=self.runner)
            short = merge_gate.main(["review-record", self.h.clone, "1"], runner=self.runner)
        self.assertEqual((code, refused, short), (0, 2, 2), err.getvalue())
        self.assertTrue(self.check().startswith("PASS"))

    def test_the_newest_record_for_the_head_decides(self):
        self.sign()
        self.sign(reviewer="reviewer-s", gate=False)
        self.sign(verdict="FAIL", gate=False)
        self.refused("not PASS")
        self.sign(reviewer="reviewer-t", gate=False)
        self.assertIn("reviewer-t", self.check())

    def test_the_shipped_default_is_strict(self):  # the frozen valid case signs through the writer, so strict ships on
        self.assertIs(True, merge_gate.STRICT_REVIEW_RECORDS)

    def test_a_pr_number_that_is_a_boolean_or_text_is_no_review(self):
        self.sign()
        rec = self.rows()[0]
        import gate_ledger  # the chain the writer uses, so only the pr field is wrong (strict mode reads chains first)
        first = dict(rec, pr=True, prev_hash=merge_gate.GENESIS_HASH)
        second = dict(rec, pr="1", prev_hash=gate_ledger._line_hash(first))
        self.write_rows([first, second])
        self.refused("no review record for pull request")

    def test_a_later_whole_gate_fail_for_the_same_tree_voids_the_review(self):
        self.sign()
        whole_gate_row(self.h.clone, self.head, status="FAIL")
        self.refused("newest whole gate record")
        whole_gate_row(self.h.clone, self.head)   # a later PASS stands again; the review's own row is still a PASS
        self.assertTrue(self.check().startswith("PASS"))

    def test_a_line_that_is_the_root_commit_is_refused(self):
        self.sign()
        root = self.h.git(self.h.clone, "rev-list", "--max-parents=0", "hub/main")
        self.edit_last(line=root)
        self.refused("root commit")

    def test_the_writer_refuses_a_root_commit_line(self):  # review round 2 LOW: the writer, not only the checker
        root = self.h.git(self.h.clone, "rev-list", "--max-parents=0", "hub/main")
        with self.assertRaises(merge_gate.MergeGateError) as got:
            merge_gate.append_review_record(self.h.clone, "1", self.head, root, "reviewer-r", "PASS", runner=self.runner)
        self.assertIn("root commit", str(got.exception))

    def test_review_show_says_a_broken_chain_does_not_verify(self):  # review round 2 LOW: a prev_hash is not a chain
        self.sign()
        self.sign(reviewer="reviewer-s", gate=False)
        self.edit_last(prev_hash="ab" * 32)
        line = merge_gate.review_show(self.h.clone, "1", self.head)
        self.assertIn("chain does NOT verify", line)
        self.assertNotIn(", chained;", line)

    def test_a_line_older_than_the_fork_point_is_refused(self):
        h = self.h
        # main moves on (commit X), a third PR is cut from X; a line that is X's parent is non root, carried by both the
        # head and the base, and older than the point the head forked from
        h.git(h.clone, "checkout", "-q", "--detach", "hub/main")
        with open(os.path.join(h.clone, "x.txt"), "w") as fh:
            fh.write("x\n")
        h.git(h.clone, "add", "x.txt")
        h.git(h.clone, "commit", "-q", "-m", "main moves on")
        h.git(h.clone, "push", "-q", "hub", "HEAD:main")
        newbase = h.git(h.clone, "rev-parse", "HEAD")
        with open(os.path.join(h.clone, "f3.txt"), "w") as fh:
            fh.write("3\n")
        h.git(h.clone, "add", "f3.txt")
        h.git(h.clone, "commit", "-q", "-m", "pr 3")
        head3 = h.git(h.clone, "rev-parse", "HEAD")
        self.sign(pr=3, head=head3, line=newbase)
        self.assertTrue(self.check(pr="3", head=head3, base=newbase).startswith("PASS"))
        self.edit_last(line=self.base)
        self.refused("older than the point", pr="3", head=head3, base=newbase)

    def test_a_hand_written_row_is_read_only_until_the_chain_is_switched_on(self):
        # the frozen shape: a gate row with the zero code_hash, a review row with no prev_hash. Accepted today (the amended
        # harness has not landed); refused the moment strict is on, whether by the argument or by the module constant.
        from unittest import mock
        gate_id = whole_gate_row(self.h.clone, self.head, code_hash="0" * 64)
        tree = self.h.git(self.h.clone, "rev-parse", self.head + "^{tree}")
        self.write_rows([{"schema": 1, "pr": 1, "head": self.head, "tree": tree, "whole_gate": gate_id, "line": self.base,
                          "reviewer": "reviewer-r", "builders": ["builder-a"], "verdict": "PASS"}])
        self.assertTrue(self.check(strict=False).startswith("PASS"))
        with self.assertRaises(merge_gate.MergeNoData):
            self.check(strict=True)
        with mock.patch.object(merge_gate, "STRICT_REVIEW_RECORDS", True), self.assertRaises(merge_gate.MergeNoData):
            self.check()

    def test_strict_refuses_a_zero_code_hash_gate_row_even_when_the_review_chains(self):
        gate_id = whole_gate_row(self.h.clone, self.head, code_hash="0" * 64)
        tree = self.h.git(self.h.clone, "rev-parse", self.head + "^{tree}")
        self.write_rows([{"schema": 1, "pr": 1, "head": self.head, "tree": tree, "whole_gate": gate_id, "line": self.base,
                          "reviewer": "reviewer-r", "builders": ["builder-a"], "verdict": "PASS",
                          "prev_hash": merge_gate.GENESIS_HASH}])
        self.assertTrue(self.check(strict=False).startswith("PASS"))
        self.refused("real code_hash", strict=True)

    def test_a_chain_that_breaks_is_no_data(self):
        self.sign()
        self.sign(reviewer="reviewer-s", gate=False)
        self.sign(reviewer="reviewer-t", gate=False)
        good = self.rows()
        for name, rows in (("first row deleted", good[1:]), ("middle row deleted", [good[0], good[2]]),
                           ("middle row edited", [good[0], dict(good[1], reviewer="reviewer-z"), good[2]]),
                           ("rows reordered", [good[0], good[2], good[1]])):
            with self.subTest(name):
                self.write_rows(rows)
                with self.assertRaises(merge_gate.MergeNoData):
                    self.check()
        self.write_rows(good)
        self.assertTrue(self.check().startswith("PASS"))

    def test_a_hand_appended_row_after_a_written_one_is_no_data(self):
        self.sign()
        rec = dict(self.rows()[-1], reviewer="reviewer-forged")
        rec.pop("prev_hash")
        with open(self.reviews, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        with self.assertRaises(merge_gate.MergeNoData):
            self.check()

    def test_a_review_row_that_names_a_whole_gate_fail_refuses_though_a_later_pass_stands(self):
        # isolates the referenced row's own status: the newest row for the tree is a PASS, the review points at an older FAIL
        self.sign()
        fail_id = whole_gate_row(self.h.clone, self.head, status="FAIL")
        whole_gate_row(self.h.clone, self.head)
        self.edit_last(whole_gate=fail_id)
        self.refused("not a PASS")

    def test_review_show_says_what_the_owner_is_about_to_confirm(self):
        none = merge_gate.review_show(self.h.clone, "1", self.head)
        self.assertIn("none on record", none)
        self.sign()
        self.sign(reviewer="reviewer-s", gate=False)
        line = merge_gate.review_show(self.h.clone, "1", self.head)
        for part in ("REVIEW #1: PASS by reviewer-s", "at head " + self.head, "row 2 of 2", "chained", "typed count is the control"):
            self.assertIn(part, line)
        self.assertIn("none on record for head", merge_gate.review_show(self.h.clone, "2", self.head))
        os.remove(self.reviews)
        os.mkdir(self.reviews)
        self.assertIn("cannot be read", merge_gate.review_show(self.h.clone, "1", self.head))

    # ---- the tool itself ---------------------------------------------------------------------------------------------
    def test_the_prompt_shows_the_review_row_above_the_typed_count(self):
        h = self.h
        self.sign()
        self.prepare_tool()
        rc, out, merges = h.run_tool(h.list)
        self.assertEqual((rc, merges), (0, 1), out)
        shown = "REVIEW #1: PASS by reviewer-r at head %s" % self.head
        self.assertIn(shown, out)
        self.assertGreater(out.index(shown), out.index("MERGING INTO"), out)
        if "Type the count" in out:
            self.assertLess(out.index(shown), out.index("Type the count"), out)

    def prepare_tool(self):
        """PR 1 alone, its gate row, its gate PASS and review (through the writer)."""
        h = self.h
        with open(h.list, "w") as fh:
            fh.write("1 %s\n" % self.head)
        h.record(h.git(h.clone, "merge-tree", "--write-tree", self.base, self.head), self.base)

    def test_a_merge_the_host_refuses_leaves_no_merge_record(self):
        h = self.h
        self.sign()
        self.prepare_tool()
        open(os.path.join(h.fix, "nomerge"), "w").close()
        rc, out, merges = h.run_tool(h.list)
        self.assertEqual(merges, 1, out)    # gh pr merge WAS called, and refused
        self.assertNotEqual(rc, 0, out)
        self.assertIn("merge refused", out)
        self.assertFalse(os.path.exists(self.merge_records), "a merge record followed a refused merge: " + out)

    def test_a_dry_run_reports_a_missing_review_and_still_merges_nothing(self):
        h = self.h
        self.prepare_tool()
        rc, out, merges = h.run_tool("--dry-run", h.list, tty=False)
        self.assertEqual((rc, merges), (0, 0), out)
        self.assertIn("WOULD MERGE #1", out)
        self.assertIn("REVIEW #1: a real run would refuse", out)

    def test_a_fail_recorded_while_the_owner_types_is_seen_right_before_the_merge(self):
        h = self.h
        self.sign()
        self.prepare_tool()
        # the stand-in gh appends this chained FAIL as the 4th read of the PR starts: the read right before gh pr merge
        # (reads 1 to 3 are the pin check, the entry and the full read, all before the first review check passes)
        with open(os.path.join(h.fix, "inject-line"), "w") as fh:
            fh.write(self.chained_row(verdict="FAIL", reviewer="reviewer-q"))
        with open(os.path.join(h.fix, "inject-target"), "w") as fh:
            fh.write(self.reviews)
        with open(os.path.join(h.fix, "inject-after"), "w") as fh:
            fh.write("3\n")
        rc, out, merges = h.run_tool(h.list)
        self.assertEqual(merges, 0, out)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("not PASS", out)
        self.assertEqual(self.h.git(self.h.clone, "ls-remote", "hub", "refs/heads/main").split()[0], self.base, out)

    def test_a_merge_record_that_cannot_be_written_still_counts_the_merge_and_proves_it(self):
        h = self.h
        self.sign()
        self.prepare_tool()
        os.mkdir(self.merge_records)   # a directory where the record file should be: the append fails after the merge
        rc, out, merges = h.run_tool(h.list)
        self.assertEqual(merges, 1, out)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("merge record could not be appended", out)
        self.assertIn("MERGED #1", out)
        self.assertIn("PROVEN #1", out)                      # reachability still ran for the PR
        self.assertNotIn("TREE-DRIFT", out)
        self.assertIn("DONE: 1 of 1 listed PR(s) merged", out)   # and the summary counts it


REVIEW_RECORDS_FILE = "brother-review-records.jsonl"
MERGE_RECORDS_FILE = "brother-merge-records.jsonl"


class ReviewRecordRequired(unittest.TestCase):
    """MERGE1.a, FROZEN (failing first; the implementer never edits this class). A real run of merge_verified.sh merges
    a pull request only on a REVIEW RECORD for its exact head sha, checked in the same call as the merge, and appends a
    MERGE RECORD naming the actor. Measured 2026-11-01: one PR was refused by the host's classifier although its review
    was green, and another was merged before its review found a HIGH, so the merge decision lived in no record at all.

    THE REVIEW RECORD, one JSON object per line, appended to <git common dir>/brother-review-records.jsonl (the
    directory the whole gate record already lives in, gate_ledger.whole_gate_ledger, shared by every worktree):
      schema     1
      pr         the pull request number (int)
      head       the 40 hex head sha that was reviewed; it must equal the pinned sha being merged
      tree       the 40 hex tree id of that head (<head>^{tree}); it must equal the tree of the head being merged
      whole_gate the 64 hex id of a whole gate record: gate_ledger._line_hash of that record, which is also the
                 prev_hash the next record in the chain carries. The record must be in the shared whole gate ledger
                 (<git common dir>/brother-whole-gate.jsonl, chain verified), gate_name required-fast-whole-gate,
                 status PASS, and tree_sha equal to the head's tree. A FAIL record, or no record, refuses.
      line       the 40 hex tip of the release line the review was made against; it must be an ancestor of the head
                 (and, the implementer's choice, of the line's tip now)
      reviewer   the reviewer's id (non empty text)
      builders   the ids of everyone who built the change (a list of text); the reviewer must not be one of them
      verdict    "PASS"; any other verdict refuses
    THE MERGE RECORD, one JSON object per line, appended to <git common dir>/brother-merge-records.jsonl after the merge
    call succeeds, never before and never for a refused merge. Asserted here: pr, head (the sha merged) and actor (the
    account that ran the merge, the same name the tool hands gh as USER). More fields are welcome.

    Each case below breaks ONE condition and keeps the other five true, so no guard masks another. The moved head case
    keeps the tree equal (an empty commit on the reviewed head): only the head comparison can refuse it, which is the
    mutation this class exists to kill. The wrong tree case puts whole gate PASS records on both trees: only the tree
    comparison can refuse it.

    FIXTURE: TheToolRunsForReal's hub, clone, stand-in gh and pseudo terminal, used through one instance of it (no
    existing class is changed or subclassed). Its HOME stays the account's own because a real run refuses any other
    (test_a_home_that_is_not_the_accounts_own_is_refused); every record here is in the throwaway clone's .git. The
    clone receives every scripts/merge_*.py and gate_ledger.py, so a helper the implementation adds is found when it is
    named merge_*.py or lives in one of those files."""

    def setUp(self):
        import subprocess
        h = TheToolRunsForReal("test_a_dry_run_judges_both_and_merges_nothing")
        h.setUp()
        self.addCleanup(h.doCleanups)
        self.h = h
        for name in sorted(os.listdir(HERE)):
            if name.endswith(".py") and (name.startswith("merge_") or name == "gate_ledger.py"):
                shutil.copy(os.path.join(HERE, name), os.path.join(h.clone, "scripts", name))
        common = subprocess.run(["git", "-C", h.clone, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True, check=True).stdout.strip()
        self.reviews = os.path.join(common, REVIEW_RECORDS_FILE)
        self.merge_records = os.path.join(common, MERGE_RECORDS_FILE)
        self.whole = os.path.join(common, "brother-whole-gate.jsonl")
        self.base = h.git(h.clone, "rev-parse", "hub/main")
        self.use_head(h.heads["1"])

    def use_head(self, sha):
        """List PR 1 alone at sha, have the stand-in gh report it as PR 1's head, and sign its merge gate row on the
        current base, so the existing tree gate passes and only the review record decides."""
        h = self.h
        self.head = sha
        self.tree = h.git(h.clone, "rev-parse", sha + "^{tree}")
        with open(os.path.join(h.fix, "pr-1"), "w") as fh:
            fh.write("OPEN\tmain\tMERGEABLE\t%s\tpr1\n" % sha)
        with open(h.list, "w") as fh:
            fh.write("1 %s\n" % sha)
        h.record(h.git(h.clone, "merge-tree", "--write-tree", self.base, sha), self.base)

    def whole_gate(self, tree, status="PASS"):
        """Append one whole gate record for tree through gate_ledger.append_ledger, the one writer, and return its id."""
        import gate_ledger
        now = time.time()
        gate_ledger.append_ledger(self.whole, {
            "schema": 1, "cut_id": gate_ledger.NO_CUT, "cut_version": "UNKNOWN", "gate_name": gate_ledger.WHOLE_GATE,
            "gate_argv": ["sh", "scripts/required_fast.sh"], "status": status,
            "exit_code": 0 if status == "PASS" else 1, "started_unix": now, "ended_unix": now, "duration_s": 0,
            "worktree_key": "merge1a-fixture", "tree_sha": tree, "inputs_hash": None, "code_hash": "1" * 64,
            "tool_versions": {}, "verdict_source": "full", "cache_key": None, "deadline_unix": None})
        with open(self.whole, encoding="utf-8") as fh:
            last = fh.read().splitlines()[-1]
        return gate_ledger._line_hash(json.loads(last))

    def review(self, **over):
        """Append one review row CHAINED to the row before it (what the one writer does), so under strict the chain
        check passes and only the field a case overrides can refuse. The whole gate rows carry a real code_hash."""
        import gate_ledger
        prev = gate_ledger.GENESIS
        if os.path.exists(self.reviews):
            with open(self.reviews, encoding="utf-8") as fh:
                prev = gate_ledger._line_hash(json.loads(fh.read().splitlines()[-1]))
        record = {"schema": 1, "pr": 1, "head": self.head, "tree": self.tree, "whole_gate": self.gate_id,
                  "line": self.base, "reviewer": "reviewer-r", "builders": ["builder-a", "builder-b"],
                  "verdict": "PASS", "prev_hash": prev}
        record.update(over)
        with open(self.reviews, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        return record

    def merge_lines(self):
        path = os.path.join(self.h.fix, "gh.log")
        if not os.path.exists(path):
            return []
        with open(path) as fh:
            return [line for line in fh if line.startswith("pr merge")]

    def appended_merge_records(self):
        if not os.path.exists(self.merge_records):
            return []
        with open(self.merge_records, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def assert_refused(self, reason):
        """Refused, nothing merged, nothing recorded, AND the refusal says `reason`: a chain, strict or any other
        refusal can never stand in for the condition a case claims to isolate."""
        rc, out, merges = self.h.run_tool(self.h.list)
        self.assertIn(reason, out, "refused, but not for the reason this case isolates: " + out)
        self.assertEqual(merges, 0, "gh pr merge was called: " + out)
        self.assertEqual(self.merge_lines(), [], out)
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.appended_merge_records(), [], "a merge record was appended for a refused merge: " + out)
        self.assertEqual(self.h.git(self.h.clone, "ls-remote", "hub", "refs/heads/main").split()[0], self.base,
                         "main moved: " + out)

    # 1
    def test_no_review_record_for_the_head_refuses(self):
        self.gate_id = self.whole_gate(self.tree)
        self.assert_refused("no review record: ")

    def test_a_review_record_for_another_pull_request_only_refuses(self):
        self.gate_id = self.whole_gate(self.tree)
        other = self.h.heads["2"]
        self.review(pr=2, head=other, tree=self.h.git(self.h.clone, "rev-parse", other + "^{tree}"))
        self.assert_refused("no review record for pull request #1 at head " + self.head)

    # 2: the mutation this class kills. Same tree, same gate PASS, same line: only the head comparison refuses.
    def test_a_head_that_moved_after_review_refuses(self):
        h = self.h
        reviewed = self.head
        h.git(h.clone, "checkout", "-q", "pr1")
        h.git(h.clone, "commit", "-q", "--allow-empty", "-m", "pushed after the review")
        moved = h.git(h.clone, "rev-parse", "HEAD")
        h.git(h.clone, "push", "-q", "hub", "pr1:pr1")
        h.git(h.clone, "checkout", "-q", "--detach", "hub/main")
        self.assertNotEqual(moved, reviewed)
        self.assertEqual(h.git(h.clone, "rev-parse", moved + "^{tree}"), self.tree, "the fixture must keep the tree")
        self.use_head(moved)
        self.gate_id = self.whole_gate(self.tree)
        self.review(head=reviewed)
        self.assert_refused("no review record for pull request #1 at head " + moved)

    # 3
    def test_a_reviewer_in_the_builder_set_refuses(self):
        self.gate_id = self.whole_gate(self.tree)
        self.review(reviewer="builder-b")
        self.assert_refused("is one of the builders")

    # 4: whole gate PASS records on both trees, so only the tree comparison can refuse
    def test_a_review_record_whose_tree_differs_from_the_heads_refuses(self):
        other_tree = self.h.git(self.h.clone, "rev-parse", self.base + "^{tree}")
        self.assertNotEqual(other_tree, self.tree)
        self.gate_id = self.whole_gate(self.tree)
        other_gate = self.whole_gate(other_tree)
        self.review(tree=other_tree, whole_gate=other_gate)
        self.assert_refused("the review was of tree " + other_tree)

    # 5
    def test_no_whole_gate_record_for_the_tree_refuses(self):
        self.gate_id = "f" * 64
        self.whole_gate(self.h.git(self.h.clone, "rev-parse", self.base + "^{tree}"))
        self.review()
        self.assert_refused("is not in")

    def test_only_a_whole_gate_fail_record_for_the_tree_refuses(self):
        self.gate_id = self.whole_gate(self.tree, status="FAIL")
        self.review()
        self.assert_refused("not a PASS of")

    # 6: the line moved on (a commit the head does not carry) and the record names that tip; nothing else is wrong
    def test_a_line_that_is_not_an_ancestor_of_the_head_refuses(self):
        h = self.h
        h.git(h.clone, "checkout", "-q", "--detach", "hub/main")
        with open(os.path.join(h.clone, "line.txt"), "w") as fh:
            fh.write("on the line, not in the head\n")
        h.git(h.clone, "add", "line.txt")
        h.git(h.clone, "commit", "-q", "-m", "the line moves on")
        h.git(h.clone, "push", "-q", "hub", "HEAD:main")
        h.git(h.clone, "checkout", "-q", "--detach", "hub/main")
        self.base = h.git(h.clone, "rev-parse", "hub/main")
        self.use_head(self.head)
        self.gate_id = self.whole_gate(self.tree)
        self.review(line=self.base)
        self.assert_refused("is not an ancestor of head")

    # a review that found something is no review to merge on
    def test_a_review_record_whose_verdict_is_not_pass_refuses(self):
        self.gate_id = self.whole_gate(self.tree)
        self.review(verdict="FAIL")
        self.assert_refused("not PASS")

    # 7
    def test_a_valid_review_record_merges_once_at_the_exact_head_and_records_the_actor(self):
        import pwd
        # the real way: a whole gate row with a real non zero code_hash and the review row through the one writer
        sign_review(self.h.clone, 1, self.head, self.base)
        rc, out, merges = self.h.run_tool(self.h.list)
        self.assertEqual((rc, merges), (0, 1), out)
        lines = self.merge_lines()
        self.assertEqual(len(lines), 1, out)
        words = lines[0].split()
        self.assertIn("--match-head-commit", words, lines[0])
        self.assertEqual(words[words.index("--match-head-commit") + 1], self.head, lines[0])
        records = self.appended_merge_records()
        self.assertEqual(len(records), 1, "exactly one merge record must be appended: %r" % (records,))
        record = records[0]
        self.assertIn(record.get("pr"), (1, "1"), record)
        self.assertEqual(record.get("head"), self.head, record)
        self.assertEqual(record.get("actor"), pwd.getpwuid(os.getuid()).pw_name, record)


if __name__ == "__main__":
    unittest.main()
