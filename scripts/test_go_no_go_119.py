#!/usr/bin/env python3
"""Four root cause fixes in the 1.1.9 release decision tooling, each with its own harness.

Decisions implemented (docs/handover/2026-10-28/05-DECISIONS-TAKEN.md, D1, D2, D3, D6, taken 2026-10-29):

  F1  ONE rule picks the release plan: release_window_clock_rows.release_plan_path, imported by
      go_no_go_119 instead of a second copy. Before: the clock rows tool took the last sorted
      BROTHER-*-WBS.json, which is the LOOP plan, and answered NO-DATA about the wrong plan.
  F2  no-ungated-landings learns the judge kernel distinction (D6's flip condition): when a kernel
      file changed in base..candidate the intermediate trees were judged by a superseded gate, so
      the candidate's own tree gate is the evidence of record.
  F3  journeys-fresh judges the J1/J2 rows the plan's journey_rows claims (D1, D2), and lists the
      rest as not claimed by this release.
  F4  scope-no-strays reads the ship list from the plan's units when nobody passes --ship.

HERMETIC. Every case builds its own throwaway git repository and files under tempfile. Nothing here
reads this checkout's gate ledger or plan. Python 3.9 syntax only: runs under python3 and
/usr/bin/python3.

Run: python3 -B scripts/test_go_no_go_119.py   and   /usr/bin/python3 -B scripts/test_go_no_go_119.py
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import go_no_go_119 as go  # noqa: E402
import release_window_clock_rows as rcr  # noqa: E402

GO_PATH = os.path.join(HERE, "go_no_go_119.py")
RCR_PATH = os.path.join(HERE, "release_window_clock_rows.py")
PASS, FAIL, NODATA = "PASS", "FAIL", "NO-DATA"
KERNEL_LIST = "scripts/kernel_closure.txt"
MANIFEST = "bundle/runtime/RUNTIME-MANIFEST.json"
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t.invalid", "-c", "commit.gpgsign=false"]


def _git(root, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    return subprocess.run(GIT + ["-C", root] + list(args), capture_output=True, text=True,
                          check=True, env=env).stdout.strip()


def _write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class Fixture(unittest.TestCase):
    """A throwaway git repository per test."""

    def repo(self):
        root = tempfile.mkdtemp(prefix="go119-")
        self.addCleanup(shutil.rmtree, root, True)
        _git(root, "init", "-q")
        return root

    def commit(self, root, files, msg="c"):
        """Write files ({rel: text}), commit them, return (commit sha, tree sha)."""
        for rel, text in files.items():
            _write(root, rel, text)
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", msg)
        return _git(root, "rev-parse", "HEAD"), _git(root, "rev-parse", "HEAD^{tree}")

    def ledger(self, root, records):
        _write(root, os.path.join(".git", "brother-whole-gate.jsonl"),
               "".join(json.dumps(r) + "\n" for r in records))

    def cli(self, root, base, *extra):
        got = subprocess.run([sys.executable, "-B", GO_PATH, "--root", root, "--candidate", "HEAD",
                              "--base", base] + list(extra), capture_output=True, text=True)
        return got.returncode, got.stdout

    @staticmethod
    def rowline(out, name):
        for line in out.splitlines():
            if line.split()[1:2] == [name]:
                return line
        return ""


def _pass(tree):
    return {"tree_sha": tree, "status": PASS, "exit_code": 0}


# ==========================================================================
# F1: one rule for which plan is the release plan
# ==========================================================================
class PlanRule(Fixture):
    def plans(self, root, names, body=None):
        for n in names:
            doc = dict(body or {}) if "LOOP" not in n else {"loop": True}
            doc["marker"] = n
            _write(root, os.path.join("docs", "plan", n), json.dumps(doc))

    def test_highest_version_wins_numerically(self):
        root = self.repo()
        self.plans(root, ["BROTHER-1.1.9-WBS.json", "BROTHER-1.1.10-WBS.json",
                          "BROTHER-LOOP-HARDENING-WBS.json"])
        fn = getattr(rcr, "release_plan_path", None)
        self.assertIsNotNone(fn, "release_window_clock_rows has no release_plan_path")
        self.assertEqual(os.path.basename(fn(root) or ""), "BROTHER-1.1.10-WBS.json")

    def test_only_the_loop_plan_is_none_never_the_loop_file(self):
        root = self.repo()
        self.plans(root, ["BROTHER-LOOP-HARDENING-WBS.json"])
        fn = getattr(rcr, "release_plan_path", None)
        self.assertIsNotNone(fn, "release_window_clock_rows has no release_plan_path")
        self.assertIsNone(fn(root))

    def test_an_explicit_plan_is_returned_untouched(self):
        fn = getattr(rcr, "release_plan_path", None)
        self.assertIsNotNone(fn, "release_window_clock_rows has no release_plan_path")
        self.assertEqual(fn("/nowhere", plan="/x/y.json"), "/x/y.json")

    def test_an_unreadable_plan_dir_is_none(self):
        fn = getattr(rcr, "release_plan_path", None)
        self.assertIsNotNone(fn, "release_window_clock_rows has no release_plan_path")
        self.assertIsNone(fn(os.path.join(tempfile.gettempdir(), "go119-no-such-root-xyz")))

    def test_read_window_reads_the_highest_versioned_plan(self):
        root = self.repo()
        band = {"cut_band": ["2026-11-06", "2026-11-06"], "calendar": {"start": "2026-10-26"}}
        self.plans(root, ["BROTHER-1.1.9-WBS.json", "BROTHER-1.1.10-WBS.json",
                          "BROTHER-LOOP-HARDENING-WBS.json"], body=band)
        got, why = rcr.read_window(root)
        self.assertIsNotNone(got, "read_window answered NO-DATA about the wrong plan: %s" % why)
        self.assertEqual(got[2], "BROTHER-1.1.10-WBS.json")

    def test_cli_exit_codes_follow_the_release_plan_not_the_loop_plan(self):
        # a window that is clear on any date, plus a LOOP plan that sorts after it
        clear = tempfile.mkdtemp(prefix="go119-")
        self.addCleanup(shutil.rmtree, clear, True)
        rcr._fixture(clear, review_by="2099-12-31", covered="match",
                     start="2098-12-01", band="2099-01-01")
        self.plans(clear, ["BROTHER-LOOP-HARDENING-WBS.json"])
        got = subprocess.run([sys.executable, "-B", RCR_PATH, "--root", clear],
                             capture_output=True, text=True)
        self.assertEqual(got.returncode, 0, got.stdout)
        # only the LOOP plan: NO-DATA, exit 3
        only = tempfile.mkdtemp(prefix="go119-")
        self.addCleanup(shutil.rmtree, only, True)
        self.plans(only, ["BROTHER-LOOP-HARDENING-WBS.json"])
        got = subprocess.run([sys.executable, "-B", RCR_PATH, "--root", only],
                             capture_output=True, text=True)
        self.assertEqual(got.returncode, 3, got.stdout)

    def test_go_no_go_shares_the_one_implementation(self):
        root = self.repo()
        self.plans(root, ["BROTHER-1.1.9-WBS.json", "BROTHER-1.1.10-WBS.json",
                          "BROTHER-LOOP-HARDENING-WBS.json"])
        self.assertEqual((go._plan(root) or {}).get("marker"), "BROTHER-1.1.10-WBS.json")
        self.assertFalse(hasattr(go, "_VERSIONED"),
                         "go_no_go_119 still carries its own copy of the version rule")

    def test_go_no_go_with_only_the_loop_plan_has_no_plan(self):
        root = self.repo()
        self.plans(root, ["BROTHER-LOOP-HARDENING-WBS.json"])
        self.assertIsNone(go._plan(root))


# ==========================================================================
# F2: no-ungated-landings and the judge kernel
# ==========================================================================
class KernelDistinction(Fixture):
    def build(self, kernel_changes, list_text="# generated\nk/kernel.py\n", list_in_base=True):
        """base -> c1 (touches a non kernel file) -> c2 (touches kernel or not).
        Returns (root, base, c1 tree, c2 tree)."""
        root = self.repo()
        files = {"k/kernel.py": "v0\n", "other.txt": "v0\n"}
        if list_in_base:
            files[KERNEL_LIST] = list_text
        base, _t0 = self.commit(root, files, "base")
        _c1, t1 = self.commit(root, {"other.txt": "v1\n"}, "c1")
        _c2, t2 = self.commit(root, {("k/kernel.py" if kernel_changes else "other.txt"): "v2\n"}, "c2")
        return root, base, t1, t2

    def test_kernel_changed_and_the_tip_is_gated_reads_pass(self):
        root, base, _t1, t2 = self.build(kernel_changes=True)
        self.ledger(root, [_pass(t2)])
        verdict, text = go.row_no_ungated_landings(root, "HEAD", base)
        self.assertEqual(verdict, PASS, text)
        self.assertIn("kernel", text)
        self.assertIn("k/kernel.py", text)
        self.assertIn("evidence of record", text)

    def test_kernel_changed_and_the_tip_has_no_record_reads_fail(self):
        root, base, t1, _t2 = self.build(kernel_changes=True)
        self.ledger(root, [_pass(t1)])  # a middle tree is gated, the tip is not
        verdict, text = go.row_no_ungated_landings(root, "HEAD", base)
        self.assertEqual(verdict, FAIL, text)
        self.assertIn("kernel", text)

    def test_kernel_changed_and_the_tip_record_is_not_a_pass_reads_fail(self):
        root, base, _t1, t2 = self.build(kernel_changes=True)
        self.ledger(root, [{"tree_sha": t2, "status": FAIL, "exit_code": 1}])
        verdict, text = go.row_no_ungated_landings(root, "HEAD", base)
        self.assertEqual(verdict, FAIL, text)

    def test_no_kernel_path_changed_keeps_the_per_commit_rule(self):
        root, base, _t1, t2 = self.build(kernel_changes=False)
        self.ledger(root, [_pass(t2)])  # c1 is the uncovered middle commit
        verdict, text = go.row_no_ungated_landings(root, "HEAD", base)
        self.assertEqual(verdict, FAIL, text)
        self.assertIn("1 of 2", text)

    def test_no_kernel_path_changed_and_every_commit_covered_reads_pass(self):
        root, base, t1, t2 = self.build(kernel_changes=False)
        self.ledger(root, [_pass(t1), _pass(t2)])
        verdict, text = go.row_no_ungated_landings(root, "HEAD", base)
        self.assertEqual(verdict, PASS, text)
        self.assertNotIn("kernel", text)

    def test_an_unreadable_kernel_list_reads_nodata(self):
        root, base, _t1, t2 = self.build(kernel_changes=True, list_in_base=False)
        self.ledger(root, [_pass(t2)])
        verdict, text = go.row_no_ungated_landings(root, "HEAD", base)
        self.assertEqual(verdict, NODATA, text)
        self.assertIn("kernel", text)

    def test_the_kernel_list_is_read_from_the_candidate_tree(self):
        # the base list names nothing that changes; the candidate's list adds k/kernel.py
        root = self.repo()
        base, _t0 = self.commit(root, {"k/kernel.py": "v0\n", "other.txt": "v0\n",
                                       KERNEL_LIST: "z/never.py\n"}, "base")
        _c1, t1 = self.commit(root, {KERNEL_LIST: "z/never.py\nk/kernel.py\n"}, "list grows")
        _c2, t2 = self.commit(root, {"k/kernel.py": "v2\n"}, "kernel edit")
        self.ledger(root, [_pass(t2)])
        verdict, text = go.row_no_ungated_landings(root, "HEAD", base)
        self.assertEqual(verdict, PASS, text)
        self.assertIn("kernel", text)

    def test_exit_codes_at_the_entry_point(self):
        root, base, t1, t2 = self.build(kernel_changes=True)
        self.ledger(root, [_pass(t1)])
        code, out = self.cli(root, base)
        self.assertEqual(self.rowline(out, "no-ungated-landings")[:4], "FAIL", out)
        self.assertEqual(code, 1, out)
        self.ledger(root, [_pass(t2)])
        code, out = self.cli(root, base)
        self.assertEqual(self.rowline(out, "no-ungated-landings")[:4], "PASS", out)
        self.assertEqual(code, 3, out)  # every other row here is NO-DATA: never a GO


# ==========================================================================
# F3: journeys-fresh judges the rows the release claims
# ==========================================================================
class JourneysClaimed(Fixture):
    def build(self, journey_rows, rows, journey="J2", identity=None, with_key=True):
        root = self.repo()
        manifest = "{}\n"
        want = hashlib.sha256(manifest.encode("utf-8")).hexdigest()
        plan = {"units": []}
        if with_key:
            plan["journey_rows"] = journey_rows
        self.commit(root, {
            MANIFEST: manifest,
            os.path.join("docs", "plan", "BROTHER-1.1.9-WBS.json"): json.dumps(plan),
            os.path.join("docs", "plan", "JOURNEY-%s.json" % journey): json.dumps({
                "journey": journey, "identity": identity or want, "clean_home": True,
                "rows": rows}),
        }, "base")
        return root

    def test_an_unclaimed_failing_row_is_listed_and_does_not_fail(self):
        root = self.build({"A.adopt": "J2"}, {"A.adopt": PASS, "B.hook-cost": FAIL})
        verdict, text = go.row_journeys_fresh(root, "HEAD")
        self.assertEqual(verdict, PASS, text)
        self.assertIn("not claimed by this release", text)
        self.assertIn("J2/B.hook-cost", text)

    def test_a_claimed_failing_row_reads_fail(self):
        root = self.build({"A.adopt": "J2", "B.hook-cost": "J2"},
                          {"A.adopt": PASS, "B.hook-cost": FAIL})
        verdict, text = go.row_journeys_fresh(root, "HEAD")
        self.assertEqual(verdict, FAIL, text)
        self.assertIn("J2/B.hook-cost", text)

    def test_a_claimed_row_absent_from_every_record_reads_nodata(self):
        root = self.build({"A.adopt": "J2", "Z.never-walked": "J2"}, {"A.adopt": PASS})
        verdict, text = go.row_journeys_fresh(root, "HEAD")
        self.assertEqual(verdict, NODATA, text)
        self.assertIn("J2/Z.never-walked", text)

    def test_a_claimed_fail_outranks_a_claimed_absent_row(self):
        root = self.build({"A.adopt": "J2", "Z.never-walked": "J2"}, {"A.adopt": FAIL})
        verdict, text = go.row_journeys_fresh(root, "HEAD")
        self.assertEqual(verdict, FAIL, text)

    def test_a_claimed_nodata_row_reads_nodata_not_fail(self):
        root = self.build({"A.adopt": "J2", "B.hook-cost": "J2"},
                          {"A.adopt": PASS, "B.hook-cost": NODATA})
        verdict, text = go.row_journeys_fresh(root, "HEAD")
        self.assertEqual(verdict, NODATA, text)
        self.assertIn("J2/B.hook-cost", text)

    def test_a_claimed_fail_outranks_a_claimed_nodata_row(self):
        root = self.build({"A.adopt": "J2", "B.hook-cost": "J2"},
                          {"A.adopt": FAIL, "B.hook-cost": NODATA})
        verdict, text = go.row_journeys_fresh(root, "HEAD")
        self.assertEqual(verdict, FAIL, text)
        self.assertIn("J2/A.adopt", text)

    def test_an_unknown_verdict_on_a_claimed_row_fails_closed(self):
        root = self.build({"A.adopt": "J2"}, {"A.adopt": "MAYBE"})
        verdict, text = go.row_journeys_fresh(root, "HEAD")
        self.assertEqual(verdict, FAIL, text)

    def test_nodata_and_fail_exit_codes_at_the_entry_point(self):
        root = self.build({"A.adopt": "J2", "B.hook-cost": "J2"},
                          {"A.adopt": PASS, "B.hook-cost": NODATA})
        code, out = self.cli(root, _git(root, "rev-parse", "HEAD"))
        self.assertEqual(self.rowline(out, "journeys-fresh")[:7], "NO-DATA", out)
        self.assertEqual(code, 3, out)
        root = self.build({"A.adopt": "J2", "B.hook-cost": "J2"},
                          {"A.adopt": FAIL, "B.hook-cost": NODATA})
        code, out = self.cli(root, _git(root, "rev-parse", "HEAD"))
        self.assertEqual(self.rowline(out, "journeys-fresh")[:4], "FAIL", out)
        self.assertEqual(code, 1, out)

    def test_a_plan_without_journey_rows_reads_nodata_never_all_rows(self):
        root = self.build({}, {"A.adopt": PASS}, with_key=False)
        verdict, text = go.row_journeys_fresh(root, "HEAD")
        self.assertEqual(verdict, NODATA, text)
        self.assertIn("journey_rows", text)

    def test_a_plan_claiming_no_j1_or_j2_row_reads_nodata(self):
        root = self.build({"A.accept-recorded": "J4"}, {"A.adopt": PASS})
        verdict, text = go.row_journeys_fresh(root, "HEAD")
        self.assertEqual(verdict, NODATA, text)

    def test_no_plan_at_all_reads_nodata(self):
        root = self.repo()
        manifest = "{}\n"
        self.commit(root, {MANIFEST: manifest, os.path.join("docs", "plan", "JOURNEY-J2.json"):
                           json.dumps({"journey": "J2", "clean_home": True, "rows": {"A": PASS},
                                       "identity": hashlib.sha256(manifest.encode()).hexdigest()})})
        verdict, text = go.row_journeys_fresh(root, "HEAD")
        self.assertEqual(verdict, NODATA, text)

    def test_the_clean_home_condition_is_unchanged(self):
        root = self.build({"A.adopt": "J2"}, {"A.adopt": PASS}, identity="0" * 64)
        verdict, text = go.row_journeys_fresh(root, "HEAD")
        self.assertEqual(verdict, FAIL, text)
        self.assertIn("another tree", text)

    def test_exit_codes_at_the_entry_point(self):
        root = self.build({"A.adopt": "J2"}, {"A.adopt": FAIL})
        base = _git(root, "rev-parse", "HEAD")
        code, out = self.cli(root, base)
        self.assertEqual(self.rowline(out, "journeys-fresh")[:4], "FAIL", out)
        self.assertEqual(code, 1, out)
        root = self.build({"A.adopt": "J2"}, {"A.adopt": PASS})
        base = _git(root, "rev-parse", "HEAD")
        code, out = self.cli(root, base)
        self.assertEqual(self.rowline(out, "journeys-fresh")[:4], "PASS", out)
        self.assertEqual(code, 3, out)


# ==========================================================================
# claims-match-measured binds every record to the candidate and reads a score as a number or nothing
# ==========================================================================
class ClaimsMeasured(Fixture):
    """One measurement contract (2026-10-30). Two reproductions before it: a walk record on ANOTHER tree with
    scores={J1: "not-a-score"} read PASS here, and a valid hand re-measure satisfied cut_preflight while this row
    read NO-DATA because the two read different files. Now both read docs/plan/REMEASURE-<version>.json (cells of
    0 to 2 per journey row, a seat other than the walker's, bound to the candidate) through one derivation."""

    ROWS = {"J1": ["A.one", "B.two"], "J2": ["C.three", "D.four", "E.five"]}

    def build(self, cells=None, claims=None, seat="seat-remeasure", walker="seat-jt0", bound=None,
              remeasure=True, extra_scores=None):
        root = self.repo()
        manifest = "{}\n"
        want = hashlib.sha256(manifest.encode("utf-8")).hexdigest()
        claims = claims if claims is not None else {"A.one": "J1", "C.three": "J2"}
        plan = {"units": [], "journey_rows": claims,
                "journey_targets": {"baseline_and_target_1.1.9": {"J1 install": [2.5, 5.0], "J2 first goal": [2.0, 5.5],
                                                                  "J3 overnight run": [0.5, 0.5]}}}
        files = {MANIFEST: manifest, os.path.join("docs", "plan", "BROTHER-1.1.9-WBS.json"): json.dumps(plan)}
        for journey, rows in self.ROWS.items():
            doc = {"journey": journey, "host": "macos", "identity": want, "candidate_identity": want,
                   "clean_home": True, "claude_bin": [], "walked_by": walker, "rows": {r: PASS for r in rows}}
            if extra_scores:
                doc["scores"] = extra_scores
            files[os.path.join("docs", "plan", "JOURNEY-%s.json" % journey)] = json.dumps(doc)
        if remeasure:
            if cells is None:
                cells = {"%s/%s" % (j, r): {"score": 2, "evidence": "walked by hand"}
                         for j, rows in self.ROWS.items() for r in rows}
            files[os.path.join("docs", "plan", "REMEASURE-1.1.9.json")] = json.dumps(
                {"candidate_identity": bound or want, "seat": seat, "cells": cells})
        self.commit(root, files, "base")
        return root

    def test_a_valid_hand_re_measure_reads_pass_with_totals_out_of_two_per_cell(self):
        root = self.build()
        verdict, text = go.row_claims_match_measured(root, "HEAD")
        self.assertEqual(verdict, PASS, text)
        self.assertIn("J1 4/4", text)
        self.assertIn("J2 6/6", text)
        self.assertIn("J3", text)  # targeted, not claimed: published as not measured

    def test_the_preflight_and_the_decision_read_the_same_record(self):
        import cut_preflight as P
        root = self.build()
        self.assertEqual(P.check_re_measure(root, "1.1.9")[0], P.OK)
        self.assertEqual(go.row_claims_match_measured(root, "HEAD")[0], PASS)
        root = self.build(seat="seat-jt0")  # the walker re-measuring itself
        self.assertEqual(P.check_re_measure(root, "1.1.9")[0], P.REFUSED)
        verdict, text = go.row_claims_match_measured(root, "HEAD")
        self.assertEqual(verdict, NODATA, text)
        self.assertIn("may not re-measure", text)

    def test_a_cell_outside_0_to_2_measures_nothing(self):
        for score in (3, -1, 11, "2", True, None, 1.5):
            with self.subTest(score=repr(score)):
                cells = {"%s/%s" % (j, r): {"score": 2, "evidence": "walked by hand"}
                         for j, rows in self.ROWS.items() for r in rows}
                cells["J1/A.one"] = {"score": score, "evidence": "walked by hand"}
                verdict, text = go.row_claims_match_measured(self.build(cells=cells), "HEAD")
                self.assertEqual(verdict, NODATA, text)
                self.assertIn("not scored 0, 1 or 2", text)

    def test_a_record_for_another_candidate_measures_nothing(self):
        verdict, text = go.row_claims_match_measured(self.build(bound="0" * 64), "HEAD")
        self.assertEqual(verdict, NODATA, text)
        self.assertIn("another candidate", text)

    def test_scores_in_a_walk_record_count_for_nothing(self):
        root = self.build(remeasure=False, extra_scores={"J1": 9, "J2": 9})
        verdict, text = go.row_claims_match_measured(root, "HEAD")
        self.assertEqual(verdict, NODATA, text)
        self.assertIn("REMEASURE-1.1.9.json", text)

    def test_a_claimed_journey_with_no_walk_record_fails(self):
        root = self.build(claims={"A.one": "J1", "C.three": "J2", "Z.accept": "J4"})
        verdict, text = go.row_claims_match_measured(root, "HEAD")
        self.assertEqual(verdict, FAIL, text)
        self.assertIn("J4", text)


# ==========================================================================
# must-haves-done: a must have that is not DONE keeps the release unready (owner's rule, 2026-10-30)
# ==========================================================================
class MustHavesDone(Fixture):
    def build(self, must_have, units, slipped=None):
        root = self.repo()
        plan = {"units": [{"id": i, "state": st} for i, st in units.items()]}
        if must_have is not None:
            plan["must_have"] = must_have
        if slipped is not None:
            plan["slipped"] = {"units": {i: {"reason": "r", "owns": []} for i in slipped}}
        self.commit(root, {os.path.join("docs", "plan", "BROTHER-1.1.9-WBS.json"): json.dumps(plan)}, "base")
        return root

    def test_every_must_have_done_reads_pass(self):
        verdict, text = go.row_must_haves_done(self.build(["A", "B"], {"A": "DONE", "B": "DONE", "C": "OPEN"}))
        self.assertEqual(verdict, PASS, text)

    def test_an_open_must_have_fails(self):
        verdict, text = go.row_must_haves_done(self.build(["A", "B"], {"A": "DONE", "B": "OPEN"}))
        self.assertEqual(verdict, FAIL, text)
        self.assertIn("B OPEN", text)

    def test_a_slipped_must_have_still_fails(self):
        """The rule's point: slipping a must have never makes the decision green."""
        verdict, text = go.row_must_haves_done(self.build(["A", "B"], {"A": "DONE"}, slipped=["B"]))
        self.assertEqual(verdict, FAIL, text)
        self.assertIn("B slipped", text)

    def test_a_must_have_missing_from_the_plan_fails(self):
        verdict, text = go.row_must_haves_done(self.build(["A", "Z"], {"A": "DONE"}))
        self.assertEqual(verdict, FAIL, text)
        self.assertIn("Z not in the plan", text)

    def test_a_plan_naming_no_must_have_reads_nodata_never_pass(self):
        for names in (None, [], ["A", 5]):
            with self.subTest(names=names):
                verdict, text = go.row_must_haves_done(self.build(names, {"A": "DONE"}))
                self.assertEqual(verdict, NODATA, text)

    def test_an_empty_list_passes_only_with_a_recorded_decision_in_the_tree(self):
        """Owner decision 2026-10-30: the six must haves open the next plan; the empty list passes with the record."""
        root = self.repo()
        plan = {"units": [{"id": "A", "state": "DONE"}], "must_have": [], "must_have_decision": "docs/plan/DECISION.md"}
        self.commit(root, {os.path.join("docs", "plan", "BROTHER-1.1.9-WBS.json"): json.dumps(plan),
                           os.path.join("docs", "plan", "DECISION.md"): "# the decision\n"}, "base")
        verdict, text = go.row_must_haves_done(root)
        self.assertEqual(verdict, PASS, text)
        self.assertIn("DECISION.md", text)
        root = self.repo()
        self.commit(root, {os.path.join("docs", "plan", "BROTHER-1.1.9-WBS.json"): json.dumps(plan)}, "base")
        verdict, text = go.row_must_haves_done(root)
        self.assertEqual(verdict, NODATA, "a named decision that is not in the tree passed: %s" % text)
        root = self.repo()
        plan2 = {"units": [{"id": "A", "state": "DONE"}], "must_have": [], "must_have_decision": ""}
        self.commit(root, {os.path.join("docs", "plan", "BROTHER-1.1.9-WBS.json"): json.dumps(plan2)}, "base")
        self.assertEqual(go.row_must_haves_done(root)[0], NODATA)

    def test_the_row_is_in_the_decision(self):
        self.assertIn("must-haves-done", go.ROWS)
        root = self.build(["A"], {"A": "OPEN"})
        code, out = self.cli(root, _git(root, "rev-parse", "HEAD"))
        self.assertEqual(self.rowline(out, "must-haves-done")[:4], "FAIL", out)
        self.assertEqual(code, 1, out)


# ==========================================================================
# F4: the ship list comes from the plan when nobody passes one
# ==========================================================================
class ShipFromPlan(Fixture):
    def build(self, units=True, plan=True):
        root = self.repo()
        files = {"a.py": "v0\n", "b.py": "v0\n"}
        if plan:
            body = {"units": [{"id": "U1", "owns": ["a.py"], "state": "OPEN"},
                              {"id": "U2", "owns": ["b.py"], "state": "OPEN"}] if units else []}
            files[os.path.join("docs", "plan", "BROTHER-1.1.9-WBS.json")] = json.dumps(body)
        base, _t = self.commit(root, files, "base")
        self.commit(root, {"a.py": "v1\n", "b.py": "v1\n"}, "work")
        return root, base

    def test_no_ship_reads_the_plans_unit_ids(self):
        root, base = self.build()
        verdict, text = go.row_scope_no_strays(root, "HEAD", base, None)
        self.assertNotEqual(verdict, NODATA, text)
        self.assertIn("U1", text)
        self.assertIn("U2", text)

    def test_an_empty_ship_list_is_the_same_as_none(self):
        root, base = self.build()
        verdict, text = go.row_scope_no_strays(root, "HEAD", base, [])
        self.assertNotEqual(verdict, NODATA, text)
        self.assertIn("U1", text)

    def test_ship_stays_an_override(self):
        root, base = self.build()
        verdict, text = go.row_scope_no_strays(root, "HEAD", base, ["U1"])
        self.assertEqual(verdict, FAIL, text)  # b.py is owned only by the slipped U2
        self.assertIn("b.py", text)

    def test_no_plan_reads_nodata(self):
        root, base = self.build(plan=False)
        verdict, text = go.row_scope_no_strays(root, "HEAD", base, None)
        self.assertEqual(verdict, NODATA, text)

    def test_a_plan_with_no_units_reads_nodata(self):
        root, base = self.build(units=False)
        verdict, text = go.row_scope_no_strays(root, "HEAD", base, None)
        self.assertEqual(verdict, NODATA, text)

    def test_exit_codes_at_the_entry_point(self):
        root, base = self.build()
        code, out = self.cli(root, base)  # no --ship: the case every real run is
        line = self.rowline(out, "scope-no-strays")
        self.assertEqual(line[:4], "PASS", out)
        self.assertIn("U1", line)
        self.assertEqual(code, 3, out)
        code, out = self.cli(root, base, "--ship", "U1")
        self.assertEqual(self.rowline(out, "scope-no-strays")[:4], "FAIL", out)
        self.assertEqual(code, 1, out)


# ==========================================================================
# F4b: the plan's slipped record names what slipped, so the row needs no --ship
# ==========================================================================
class SlippedFromPlan(Fixture):
    def build(self, kept_owns=("a.py",), slipped=None, changed=("a.py", "b.py"), with_slipped=True,
              kernel=None):
        """A plan with one kept unit K1 and (by default) one slipped unit S1 owning b.py."""
        root = self.repo()
        if slipped is None:
            slipped = {"S1": {"reason": "slips to 1.1.10", "owns": ["b.py"], "harness": []}}
        plan = {"units": [{"id": "K1", "owns": list(kept_owns), "state": "OPEN"}]}
        if with_slipped:
            plan["slipped"] = {"units": slipped}
        files = {"a.py": "v0\n", "b.py": "v0\n", "c.py": "v0\n",
                 os.path.join("docs", "plan", "BROTHER-1.1.9-WBS.json"): json.dumps(plan)}
        if kernel is not None:  # None: the candidate carries no kernel list at all
            files[KERNEL_LIST] = kernel
        base, _t = self.commit(root, files, "base")
        self.commit(root, dict((c, "v1\n") for c in changed), "work")
        return root, base

    # Kernel maintenance (2026-10-30): a judge kernel file is judged by the tip's whole gate, so a
    # slipped unit owning it is no stray. Measured: scripts/close_unit.py, fixed at its source.
    def test_a_changed_kernel_file_owned_only_by_a_slipped_unit_is_not_a_stray(self):
        root, base = self.build(kernel="# generated\nb.py\n")
        verdict, text = go.row_scope_no_strays(root, "HEAD", base, None)
        self.assertEqual(verdict, PASS, text)
        self.assertIn("kernel maintenance, judged by the tip's gate", text)
        self.assertIn("b.py", text)

    def test_the_same_file_outside_the_kernel_list_is_a_stray(self):
        root, base = self.build(kernel="# generated\nz.py\n")
        verdict, text = go.row_scope_no_strays(root, "HEAD", base, None)
        self.assertEqual(verdict, FAIL, text)
        self.assertIn("b.py", text)

    def test_an_unreadable_kernel_list_keeps_the_strict_rule(self):
        root, base = self.build(kernel=None)
        verdict, text = go.row_scope_no_strays(root, "HEAD", base, None)
        self.assertEqual(verdict, FAIL, text)
        self.assertIn("b.py", text)

    def test_a_non_kernel_stray_still_fails_beside_kernel_maintenance(self):
        root, base = self.build(
            slipped={"S1": {"reason": "r", "owns": ["b.py", "c.py"]}},
            changed=("a.py", "b.py", "c.py"), kernel="b.py\n")
        verdict, text = go.row_scope_no_strays(root, "HEAD", base, None)
        self.assertEqual(verdict, FAIL, text)
        self.assertIn("c.py", text)
        self.assertNotIn("b.py", text)

    def test_a_file_owned_only_by_a_slipped_unit_is_a_stray(self):
        root, base = self.build()
        verdict, text = go.row_scope_no_strays(root, "HEAD", base, None)
        self.assertEqual(verdict, FAIL, text)
        self.assertIn("b.py", text)
        self.assertIn("S1", text)

    def test_a_file_also_owned_by_a_kept_unit_is_not_a_stray(self):
        root, base = self.build(kept_owns=("a.py", "b.py"))
        verdict, text = go.row_scope_no_strays(root, "HEAD", base, None)
        self.assertEqual(verdict, PASS, text)

    def test_a_slipped_file_the_candidate_never_touched_passes(self):
        root, base = self.build(changed=("a.py",))
        verdict, text = go.row_scope_no_strays(root, "HEAD", base, None)
        self.assertEqual(verdict, PASS, text)

    def test_a_slipped_record_without_owns_reads_nodata(self):
        root, base = self.build(slipped={"S1": {"reason": "slips", "harness": []}})
        verdict, text = go.row_scope_no_strays(root, "HEAD", base, None)
        self.assertEqual(verdict, NODATA, text)
        self.assertIn("S1", text)

    def test_a_slipped_record_with_owns_not_a_list_reads_nodata(self):
        root, base = self.build(slipped={"S1": {"reason": "slips", "owns": "b.py"}})
        verdict, text = go.row_scope_no_strays(root, "HEAD", base, None)
        self.assertEqual(verdict, NODATA, text)

    def test_a_slipped_key_without_units_reads_nodata(self):
        root = self.repo()
        plan = {"units": [{"id": "K1", "owns": ["a.py"], "state": "OPEN"}], "slipped": {"note": "x"}}
        base, _t = self.commit(root, {"a.py": "v0\n", os.path.join("docs", "plan",
                                                                     "BROTHER-1.1.9-WBS.json"):
                                      json.dumps(plan)}, "base")
        self.commit(root, {"a.py": "v1\n"}, "work")
        verdict, text = go.row_scope_no_strays(root, "HEAD", base, None)
        self.assertEqual(verdict, NODATA, text)

    def test_ship_still_overrides_the_slipped_record(self):
        # --ship ["K1"] with b.py owned by an OPEN unit K2 outside it: the old rule judges K2 slipped
        root = self.repo()
        plan = {"units": [{"id": "K1", "owns": ["a.py"], "state": "OPEN"},
                          {"id": "K2", "owns": ["b.py"], "state": "OPEN"}],
                "slipped": {"units": {"S1": {"reason": "r", "owns": ["c.py"]}}}}
        base, _t = self.commit(root, {"a.py": "v0\n", "b.py": "v0\n", "c.py": "v0\n",
                                      os.path.join("docs", "plan", "BROTHER-1.1.9-WBS.json"):
                                      json.dumps(plan)}, "base")
        self.commit(root, {"b.py": "v1\n"}, "work")
        verdict, text = go.row_scope_no_strays(root, "HEAD", base, ["K1"])
        self.assertEqual(verdict, FAIL, text)
        self.assertIn("b.py", text)

    def test_exit_codes_at_the_entry_point(self):
        root, base = self.build()
        code, out = self.cli(root, base)
        line = self.rowline(out, "scope-no-strays")
        self.assertEqual(line[:4], "FAIL", out)
        self.assertIn("S1", line)
        self.assertEqual(code, 1, out)
        root, base = self.build(slipped={"S1": {"reason": "slips"}}, changed=("a.py",))
        code, out = self.cli(root, base)
        self.assertEqual(self.rowline(out, "scope-no-strays")[:7], "NO-DATA", out)
        self.assertEqual(code, 3, out)


if __name__ == "__main__":
    unittest.main()
