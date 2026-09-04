#!/usr/bin/env python3
"""Row S32's own check: the review pass, scored against the seeded fixtures.

THE SCORE RULE IS FIXED BEFORE EXECUTION, and it is the design's section 4.2
(docs/plan/REVIEW-DEPTH-DESIGN-2026-09-05.md) written out in `score` below:

  1.0  the finding names the right file AND carries a verification command
       this run re-executed to a nonzero exit on the seeded tree and to 0 on
       the fixed one.
  0.5  the finding names the right file but carries no verification, or one
       that does not discriminate the two trees.
  0.0  no finding names the file, or no reviewer was dispatched at all.

BOTH DEFECT FIXTURES ARE DRIVEN BOTH WAYS, and the SAME canned finding is
fed to the seeded tree and to the fixed one. What separates them is the
finding's own check, re-executed by the pass: a fixture that scored the same
on both trees would be measuring the reviewer's vocabulary rather than the
defect (memory: a-control-nobody-drove-backwards-is-a-claim).

NO MODEL RUNS HERE. The reviewer is a stub script written into a temporary
directory that prints a canned finding and appends a line to a marker file,
so the docs-only control can assert that no reviewer was dispatched AT ALL
rather than that none answered.

Every tree this writes is a throwaway temporary directory. Nothing here
touches the real repository, the claim store or the vault.
"""
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

import receipt_door  # noqa: E402
import review_pass  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures", "review-depth")

#: The stub reviewer. Reads the prompt off stdin and throws it away, records
#: that it was invoked, prints the canned answer it was pointed at.
STUB = '''import sys

sys.stdin.read()
with open(sys.argv[2], "a", encoding="utf-8") as fh:
    fh.write("dispatched\\n")
with open(sys.argv[1], encoding="utf-8") as fh:
    sys.stdout.write(fh.read())
'''


def sh(argv, cwd):
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                          check=True)


def load_truth(name):
    with open(os.path.join(FIXTURES, name, "ground-truth.json"),
              encoding="utf-8") as fh:
        return json.load(fh)


def build_repo(name, variant, parent):
    """(repo, delivered revision). Two commits: the tree BEFORE the unit
    landed, then what the unit delivered. The unit's own diff is therefore
    exactly rev^1..rev, which is what the pass reads."""
    repo = os.path.join(parent, "%s-%s" % (name, variant))
    os.makedirs(repo)
    for argv in (["git", "init", "-q", "-b", "main"],
                 ["git", "config", "user.email", "s32@example.invalid"],
                 ["git", "config", "user.name", "s32 fixture"]):
        sh(argv, repo)
    for stage, message in (("base", "base"), (variant, "deliver")):
        src = os.path.join(FIXTURES, name, stage)
        for entry in sorted(os.listdir(src)):
            shutil.copy2(os.path.join(src, entry), os.path.join(repo, entry))
        sh(["git", "add", "-A"], repo)
        sh(["git", "commit", "-q", "-m", message], repo)
    rev = sh(["git", "rev-parse", "HEAD"], repo).stdout.strip()
    return repo, rev


def stub_cmd(parent, answer_path):
    """(argv, marker path). The marker file does not exist until the stub
    runs, so "no reviewer was dispatched" is observable rather than
    inferred."""
    os.makedirs(parent, exist_ok=True)
    stub = os.path.join(parent, "stub_reviewer.py")
    with open(stub, "w", encoding="utf-8") as fh:
        fh.write(STUB)
    marker = os.path.join(parent, "dispatched.log")
    return [sys.executable, stub, answer_path, marker], marker


def run_fixture(name, variant, parent, answer="reviewer.json"):
    """(stamp, marker path) for one fixture tree."""
    truth = load_truth(name)
    repo, rev = build_repo(name, variant, parent)
    answer_path = (os.path.join(FIXTURES, name, answer) if answer else
                   os.path.join(parent, "unused.json"))
    cmd, marker = stub_cmd(os.path.join(parent, "%s-%s-stub" % (name, variant)),
                           answer_path)
    row = dict(truth["unit"])
    claims = {row["id"]: {"evidence": {"canonical_rev": rev}}}
    stamps = review_pass.review_rows([row], claims, repo, cmd=cmd)
    return stamps[row["id"]], marker


def score(seeded, fixed, truth):
    """The design's section 4.2 rule. Written once, here, and applied to
    every fixture: a rule stated per fixture is a rule that can be bent per
    fixture."""
    wanted = truth["expected"]["file"]
    if seeded.get("state") != "ran":
        return 0.0
    named = [f for f in seeded["findings"] if f["file"] == wanted]
    if not named:
        return 0.0
    confirmed = [f for f in named
                 if f["state"] == receipt_door.FINDING_CONFIRMED
                 and f["check_exit_code"] not in (None, 0)]
    if not confirmed:
        return 0.5
    commands = {f["check_command"] for f in confirmed}
    discriminated = [f for f in fixed.get("findings") or []
                     if f["check_command"] in commands
                     and f["state"] == receipt_door.FINDING_NOT_REPRODUCED
                     and f["check_exit_code"] == 0]
    return 1.0 if discriminated else 0.5


class TheFixturesMeasureTheDiffAndNothingElse(unittest.TestCase):
    """The design's section 2.2 finding, asserted rather than quoted: on
    these change sets the estate's two EXISTING risk readings send nobody,
    and only the reading of the diff does. A fixture whose unit already
    tripped the declared-words or changed-paths reading would measure
    something that already worked."""

    def test_the_two_existing_readings_would_have_sent_nobody(self):
        for name in ("bom", "bad-utf8"):
            row = load_truth(name)["unit"]
            tier, cls, reviewer = receipt_door.unit_tier(
                row, row["files_changed_by_unit"], "")
            self.assertEqual(("low", "", ""), (tier, cls, reviewer),
                             "%s: the unit's own words or paths already name "
                             "a risk class, so this fixture measures the "
                             "wrong reading" % name)

    def test_the_diff_is_what_arms_the_reviewer(self):
        for name in ("bom", "bad-utf8"):
            truth = load_truth(name)
            row = truth["unit"]
            tier, cls, reviewer = receipt_door.unit_tier(
                row, row["files_changed_by_unit"],
                "+++ b/jsonl2csv.py\n+    with open(src) as fh:\n")
            self.assertEqual(
                (truth["expected"]["tier"], truth["expected"]["class"],
                 truth["expected"]["reviewer"]), (tier, cls, reviewer), name)

    def test_a_context_line_never_arms_a_reviewer(self):
        """The '+' test is on ADDED lines only. A diff that merely SHOWS a
        decode boundary in its context, and adds nothing, arms nobody."""
        self.assertEqual("", receipt_door.diff_risk(
            "+++ b/x.py\n     with open(src) as fh:\n+    return 1\n"))


class ASeededDefectScoresOnlyWhenItsCheckDiscriminates(unittest.TestCase):
    """Both defect fixtures, both ways. The seeded tree must confirm the
    finding with a real nonzero exit code, and the fixed tree must leave
    zero confirmed findings with the identical canned review."""

    def _both_ways(self, name):
        truth = load_truth(name)
        with tempfile.TemporaryDirectory(prefix="s32-%s-" % name) as tmp:
            seeded, seeded_marker = run_fixture(name, "seeded", tmp)
            fixed, _fixed_marker = run_fixture(name, "fixed", tmp)
            self.assertTrue(os.path.isfile(seeded_marker),
                            "%s: the reviewer was never dispatched" % name)
            return truth, seeded, fixed

    def test_the_bom_fixture_scores_one(self):
        truth, seeded, fixed = self._both_ways("bom")
        self._assert_full_score("bom", truth, seeded, fixed)

    def test_the_bad_utf8_fixture_scores_one(self):
        truth, seeded, fixed = self._both_ways("bad-utf8")
        self._assert_full_score("bad-utf8", truth, seeded, fixed)

    def _assert_full_score(self, name, truth, seeded, fixed):
        self.assertEqual("ran", seeded["state"], name)
        self.assertEqual(truth["expected"]["tier"], seeded["tier"], name)
        self.assertEqual(truth["expected"]["class"], seeded["class"], name)
        self.assertEqual(truth["expected"]["reviewer"], seeded["reviewer"],
                         name)
        self.assertEqual(1, len(seeded["findings"]), name)
        found = seeded["findings"][0]
        self.assertEqual(truth["expected"]["file"], found["file"], name)
        self.assertEqual(truth["expected"]["seeded_finding_state"],
                         found["state"], name)
        self.assertNotIn(found["check_exit_code"], (None, 0),
                         "%s: the finding's check did not fail on the seeded "
                         "tree, so nothing was proved" % name)
        self.assertIs(False, found["repaired"], name)

        self.assertEqual(1, len(fixed["findings"]), name)
        repaired = fixed["findings"][0]
        self.assertEqual(truth["expected"]["fixed_finding_state"],
                         repaired["state"], name)
        self.assertEqual(0, repaired["check_exit_code"], name)
        self.assertIsNone(repaired["repaired"], name)
        self.assertEqual(
            [], [f for f in fixed["findings"]
                 if f["state"] == receipt_door.FINDING_CONFIRMED],
            "%s: the fixed tree still carries a confirmed finding" % name)

        self.assertEqual(truth["expected"]["score"], score(seeded, fixed,
                                                           truth), name)
        print("\n%s: seeded exit %s (%s), fixed exit %s (%s), score %.1f"
              % (name, found["check_exit_code"], found["state"],
                 repaired["check_exit_code"], repaired["state"],
                 score(seeded, fixed, truth)))

    def test_the_delivered_check_passed_on_both_trees(self):
        """The point of the whole row, made mechanical: the unit's OWN check
        is green on the seeded tree. A green there proves only what it looked
        at, and the defect is what it never looked at."""
        for name in ("bom", "bad-utf8"):
            truth = load_truth(name)
            with tempfile.TemporaryDirectory(prefix="s32-own-") as tmp:
                for variant in ("seeded", "fixed"):
                    repo, _rev = build_repo(name, variant, tmp)
                    code, _detail, _trunc = review_pass.integrate._run_check(
                        truth["unit"]["done_check"], repo)
                    self.assertEqual(0, code, "%s/%s: the delivered check "
                                     "did not pass" % (name, variant))


class TheDocsOnlyControlDispatchesNoReviewer(unittest.TestCase):
    """The negative control. Nothing about a README crosses a risk boundary,
    so the assertion is not that the reviewer found nothing: it is that no
    reviewer command was ever executed."""

    def test_no_reviewer_is_dispatched_and_the_state_says_why(self):
        truth = load_truth("docs-only")
        with tempfile.TemporaryDirectory(prefix="s32-docs-") as tmp:
            stamp, marker = run_fixture("docs-only", "seeded", tmp,
                                        answer=None)
        self.assertEqual("low", stamp["tier"])
        self.assertEqual([], stamp["findings"])
        self.assertFalse(os.path.isfile(marker),
                         "a reviewer was dispatched on a documentation-only "
                         "delivery")
        self.assertIn(receipt_door.NODATA, stamp["state"])
        self.assertIn("crossed no risk boundary", stamp["state"])
        self.assertEqual(truth["expected"]["score"],
                         score(stamp, stamp, {"expected": {"file": "x"}}))
        print("\ndocs-only: %s" % stamp["state"])


class ARewordedFindingIsWorthHalfAndReadsNoData(unittest.TestCase):
    """The honesty trap. A well-formed finding that only restates the diff
    and carries no command is the fluent failure this design is built
    around: it must score 0.5, and its state on the receipt must be
    no-data."""

    def test_a_finding_with_no_verification_scores_half(self):
        truth = load_truth("bom")
        with tempfile.TemporaryDirectory(prefix="s32-reword-") as tmp:
            seeded, _marker = run_fixture("bom", "seeded", tmp,
                                          answer="reviewer-reword.json")
            fixed, _m2 = run_fixture("bom", "fixed", tmp,
                                     answer="reviewer-reword.json")
        found = seeded["findings"][0]
        self.assertEqual(receipt_door.FINDING_NO_DATA, found["state"])
        self.assertEqual(receipt_door.NODATA, found["check_command"])
        self.assertIsNone(found["check_exit_code"])
        self.assertIsNone(found["repaired"])
        self.assertEqual(0.5, score(seeded, fixed, truth))
        print("\nreworded finding: state %s, score 0.5" % found["state"])


class TheSeamIsNoDataWhenNoReviewerIsReachable(unittest.TestCase):
    """NO-DATA is never a pass. An unset seam, and a command that cannot
    run, both have to leave a sentence naming what was missing."""

    def test_an_unset_environment_variable_names_itself(self):
        truth = load_truth("bom")
        saved = os.environ.pop(review_pass.MODEL_CMD_ENV, None)
        try:
            self.assertEqual([], review_pass.resolve_cmd())
            with tempfile.TemporaryDirectory(prefix="s32-unset-") as tmp:
                repo, rev = build_repo("bom", "seeded", tmp)
                row = dict(truth["unit"])
                stamps = review_pass.review_rows(
                    [row], {row["id"]: {"evidence": {"canonical_rev": rev}}},
                    repo)
            stamp = stamps[row["id"]]
        finally:
            if saved is not None:
                os.environ[review_pass.MODEL_CMD_ENV] = saved
        self.assertEqual("high", stamp["tier"])
        self.assertEqual([], stamp["findings"])
        self.assertIn(receipt_door.NODATA, stamp["state"])
        self.assertIn(review_pass.MODEL_CMD_ENV, stamp["state"])

    def test_the_environment_variable_is_the_seam(self):
        saved = os.environ.get(review_pass.MODEL_CMD_ENV)
        os.environ[review_pass.MODEL_CMD_ENV] = "/bin/echo hello world"
        try:
            self.assertEqual(["/bin/echo", "hello", "world"],
                             review_pass.resolve_cmd())
        finally:
            if saved is None:
                os.environ.pop(review_pass.MODEL_CMD_ENV, None)
            else:
                os.environ[review_pass.MODEL_CMD_ENV] = saved

    def test_a_reviewer_that_cannot_run_is_not_a_clean_review(self):
        truth = load_truth("bom")
        with tempfile.TemporaryDirectory(prefix="s32-broken-") as tmp:
            repo, rev = build_repo("bom", "seeded", tmp)
            row = dict(truth["unit"])
            stamps = review_pass.review_rows(
                [row], {row["id"]: {"evidence": {"canonical_rev": rev}}},
                repo, cmd=[os.path.join(tmp, "no-such-reviewer")])
        stamp = stamps[row["id"]]
        self.assertIn(receipt_door.NODATA, stamp["state"])
        self.assertEqual([], stamp["findings"])


class TheParserRefusesWhatIsNotAFinding(unittest.TestCase):
    """Strict parse, driven both ways: what survives and what is dropped
    with its reason."""

    def test_a_well_formed_array_survives(self):
        kept, dropped = review_pass.parse_findings(
            '[{"location": "a.py:1", "failure": "it breaks"}]')
        self.assertEqual(1, len(kept))
        self.assertEqual([], dropped)

    def test_a_fenced_answer_is_still_read(self):
        kept, dropped = review_pass.parse_findings(
            'Here is what I found:\n```json\n[{"location": "a.py:1", '
            '"failure": "it breaks"}]\n```\n')
        self.assertEqual(1, len(kept))
        self.assertEqual([], dropped)

    def test_an_entry_missing_a_location_or_a_failure_is_dropped(self):
        kept, dropped = review_pass.parse_findings(
            '[{"failure": "no location"}, {"location": "a.py:1"}, "prose"]')
        self.assertEqual([], kept)
        self.assertEqual(3, len(dropped))

    def test_prose_alone_is_no_data(self):
        kept, dropped = review_pass.parse_findings("Looks good to me.")
        self.assertEqual([], kept)
        self.assertTrue(dropped)

    def test_an_empty_array_is_an_answer_not_a_failure(self):
        kept, dropped = review_pass.parse_findings("[]")
        self.assertEqual([], kept)
        self.assertEqual([], dropped)


class TheReceiptCarriesTheReviewAndOrdersItFirst(unittest.TestCase):
    """The visible half: what a reviewer opening the delivery actually sees.
    A confirmed finding puts its file at the top of the reading order with
    the exit code beside it; a not_reproduced finding moves nothing."""

    def _record(self, state, exit_code):
        return {"rows": [{
            "id": "U1", "status": "DONE", "owns": ["jsonl2csv.py"],
            "files_changed_by_unit": ["jsonl2csv.py"],
            receipt_door.REVIEW_FIELD: {
                "tier": "high", "class": "encoding",
                "reviewer": "backend-reviewer", "unmeasured_classes": [],
                "state": "ran",
                "findings": [{
                    "id": "U1-1", "unit": "U1", "file": "jsonl2csv.py",
                    "reviewer": "backend-reviewer", "severity": "major",
                    "failure": "it drops a record",
                    "check_command": "python3 check_header.py",
                    "check_exit_code": exit_code, "state": state,
                    "repaired": False if state ==
                    receipt_door.FINDING_CONFIRMED else None}]}}]}

    RECEIPTS = [{"id": "U1", "command": "python3 test_jsonl2csv.py",
                 "exit_code": 0, "state": "verified", "reason": "",
                 "output_location": "the run log",
                 "check_passed_before": False}]

    def test_a_confirmed_finding_leads_the_reading_order(self):
        record = self._record(receipt_door.FINDING_CONFIRMED, 1)
        order = receipt_door.reading_order(record, self.RECEIPTS)
        first = order[receipt_door.REVIEW_FIRST]
        self.assertEqual(["jsonl2csv.py"], [e["path"] for e in first])
        self.assertIn("exited 1", first[0]["why"])
        self.assertIn("backend-reviewer", first[0]["why"])
        self.assertEqual([], order[receipt_door.LOW_RISK_MECHANICAL])
        print("\nREVIEW FIRST: %s" % first[0]["why"])

    def test_a_not_reproduced_finding_moves_no_file(self):
        record = self._record(receipt_door.FINDING_NOT_REPRODUCED, 0)
        order = receipt_door.reading_order(record, self.RECEIPTS)
        self.assertEqual([], order[receipt_door.REVIEW_FIRST])
        self.assertEqual(["jsonl2csv.py"],
                         [e["path"] for e in
                          order[receipt_door.LOW_RISK_MECHANICAL]])

    def test_the_review_block_is_present_and_reads_ran(self):
        block = receipt_door.review_findings(
            self._record(receipt_door.FINDING_CONFIRMED, 1))
        self.assertEqual("ran", block["pass_state"])
        self.assertEqual(1, len(block["units_reviewed"]))
        self.assertEqual(1, len(block["findings"]))

    def test_a_run_with_no_stamp_at_all_reads_no_data(self):
        block = receipt_door.review_findings({"rows": [{"id": "U1"}]})
        self.assertIn(receipt_door.NODATA, block["pass_state"])
        self.assertEqual([], block["findings"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
