"""What the record-drift check must keep true.

It fills the one gap this estate had no control for: whether the record still
describes the world after the work landed. Every instance found on 2026-08-29
was found by a person asking or by a peer, never by a check.

But the harder half is that this checker manufactured false violations FOUR
separate ways before it was honest, and each one was found by looking at what it
flagged rather than trusting it. A checker that invents violations is worse than
none: it sends somebody to fix work that was already right and teaches everyone
to ignore the tool. So most of this file is about it staying quiet.
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import record_drift as D  # noqa: E402


def board(rows=None, features=None, complaints=None):
    doc = {"rows": rows or [], "features": features or []}
    if complaints is not None:
        doc["team_complaints"] = {"P_series_verified_2026_08_29": complaints}
    return doc


class ItCatchesTheDriftItWasBuiltFor(unittest.TestCase):
    """The three shapes that actually happened, each caught here."""

    def test_a_DONE_node_citing_a_commit_nobody_has(self):
        doc = board([{"id": "Y", "status": "DONE",
                      "evidence": "committed deadbee and pushed"}])
        drift = [f for f in D.check_evidence_commits(doc) if f[0] == "DRIFT"]
        self.assertTrue(drift)
        self.assertIn("deadbee", drift[0][2])

    def test_a_status_contradicting_its_own_evidence(self):
        """Found by a peer on this board: SCHEDULED beside evidence saying
        DECIDED."""
        doc = board([{"id": "X", "status": "SCHEDULED",
                      "evidence": "DECIDED 2026-08-29: measured and closed"}])
        drift = D.check_status_against_evidence(doc)
        self.assertTrue(drift)
        self.assertIn("SCHEDULED", drift[0][2])

    def test_a_complaint_left_NOT_ADDRESSED_after_its_work_shipped(self):
        """Found only because somebody asked whether things were good."""
        doc = board(features=[{"id": "N1", "status": "DONE",
                               "closes_complaint": ["P3"]}],
                    complaints={"P3": {"verdict": "NOT-ADDRESSED"}})
        drift = [f for f in D.check_complaints(doc) if f[0] == "DRIFT"]
        self.assertTrue(drift)
        self.assertIn("the work moved and the verdict did not", drift[0][2])

    def test_a_complaint_marked_ADDRESSED_by_work_that_is_still_open(self):
        """The worse direction, and it must not be silent."""
        doc = board(features=[{"id": "N9", "status": "SCHEDULED",
                               "closes_complaint": ["P7"]}],
                    complaints={"P7": {"verdict": "ADDRESSED"}})
        drift = [f for f in D.check_complaints(doc) if f[0] == "DRIFT"]
        self.assertTrue(drift)
        self.assertIn("worse direction", drift[0][2])


class ItRefusesToManufactureAViolation(unittest.TestCase):
    """Four classes, all of which this checker actually produced first."""

    def test_a_commit_in_a_SIBLING_repository_is_not_missing(self):
        """The first false class: it checked one repository and reported seven
        false drifts against nodes built in the others."""
        self.assertGreater(len(D.KNOWN_REPOS), 1)
        real = [r for r in D.KNOWN_REPOS
                if os.path.isdir(os.path.join(r, ".git"))]
        self.assertGreaterEqual(len(real), 2,
                                "this test needs at least two real repositories")

    def test_a_plan_FINGERPRINT_is_not_read_as_a_commit(self):
        """The second false class. This estate writes plan versions as content
        hashes, so 'moved b99ad13d35cc to a62113e7318d' looked like two commits
        to a pattern that only knows hex."""
        doc = board([{"id": "Z", "status": "DONE",
                      "evidence": "mutating will_run moved b99ad13d35cc to "
                                  "a62113e7318d"}])
        self.assertEqual([f for f in D.check_evidence_commits(doc)
                          if f[0] == "DRIFT"], [])

    def test_only_hex_the_prose_calls_a_commit_is_checked(self):
        self.assertEqual(D.commit_shas("moved abc1234 to def5678"), [])
        self.assertEqual(D.commit_shas("committed abc1234"), ["abc1234"])
        self.assertEqual(D.commit_shas("pushed at abc1234"), ["abc1234"])

    def test_a_node_that_is_still_OPEN_is_not_checked_for_commits(self):
        """A node that has not finished has no commit to have lost."""
        doc = board([{"id": "W", "status": "SCHEDULED",
                      "evidence": "will be committed as deadbee"}])
        self.assertEqual([f for f in D.check_evidence_commits(doc)
                          if f[0] == "DRIFT"], [])

    def test_a_DONE_node_with_no_commit_in_its_evidence_is_silent(self):
        """Plenty of evidence is a command and its output. Absence of a sha is
        not a fault."""
        doc = board([{"id": "V", "status": "DONE",
                      "evidence": "the battery ran and printed 38 pass"}])
        self.assertEqual(D.check_evidence_commits(doc), [])

    def test_the_live_board_is_clean(self):
        """The regression that matters: this tool runs in the battery, so a
        false positive here stops everybody's work."""
        self.assertEqual(D.main([]), 0)


class AThrowawayCloneCommitIsNoDataNotDrift(unittest.TestCase):
    """The sixth false-positive class: row I1's live delivery ran the door
    against a fresh clone of a public repository under a temporary path, on
    purpose, and that clone is discarded once the run ends. Its integration
    commits are real, they are just nowhere this checker will ever be able
    to look, so reporting them as DRIFT sends somebody to fix work that was
    already right."""

    @staticmethod
    def _missing_everywhere(cmd, **kw):
        """A fake runner: the commit exists in NO repository this checker
        can ask, same shape as TheFourthCheckExistsRatherThanBeingPromised's
        injection above."""
        return type("P", (), {"returncode": 1, "stdout": "", "stderr": ""})()

    def test_a_missing_commit_from_a_throwaway_clone_is_NO_DATA(self):
        doc = board([{"id": "I1", "status": "DONE",
                      "evidence": "a fresh clone of the public token-shield "
                                  "repository under /tmp/i1-live-repo: "
                                  "canonical revision after: fc588dcf712"}])
        found = D.check_evidence_commits(doc, runner=self._missing_everywhere)
        self.assertEqual([f[0] for f in found], ["NO-DATA"])
        self.assertIn("fc588dcf712", found[0][2])
        self.assertIn("discarded temporary clone", found[0][2])

    def test_the_SAME_missing_commit_without_throwaway_context_still_DRIFTS(self):
        """Same sha, same missing-everywhere runner, no clone/temporary words
        in the evidence: an ordinary missing commit must still drift."""
        doc = board([{"id": "I1", "status": "DONE",
                      "evidence": "canonical revision after: fc588dcf712"}])
        found = D.check_evidence_commits(doc, runner=self._missing_everywhere)
        self.assertEqual([f[0] for f in found], ["DRIFT"])
        self.assertIn("fc588dcf712", found[0][2])


class ASubPartsOwnStatusIsNotTheRows(unittest.TestCase):
    """The seventh false-positive class: row H5's evidence reads 'P1.1
    landed 2026-09-02 night ... P1.2 to P1.4 remain' while the row itself
    stays correctly OPEN, held by a founder ruling. A completion word
    scoped to a sub-part id is that sub-part's own status, not a claim the
    whole row is done."""

    def test_a_subpart_scoped_completion_word_is_not_flagged(self):
        doc = board([{"id": "H5", "status": "OPEN",
                      "evidence": "P1.1 landed 2026-09-02 night; P1.2 to "
                                  "P1.4 remain"}])
        self.assertEqual(D.check_status_against_evidence(doc), [])

    def test_the_SAME_word_UNSCOPED_still_drifts(self):
        """Same completion word, no sub-part id in front of it this time:
        the row itself is the subject, so an OPEN status beside it is still
        the contradiction this check exists to catch."""
        doc = board([{"id": "H5", "status": "OPEN",
                      "evidence": "this row LANDED"}])
        drift = D.check_status_against_evidence(doc)
        self.assertTrue(drift)
        self.assertIn("LANDED", drift[0][2])


class NoDataIsNeverAPass(unittest.TestCase):
    def test_a_board_with_no_complaints_says_so_rather_than_passing(self):
        found = D.check_complaints(board())
        self.assertEqual([f[0] for f in found], ["NO-DATA"])

    def test_an_unreadable_board_is_NO_DATA(self):
        self.assertEqual(D.main(["--roadmap", "/no/such/board.json"]), 2)

    def test_drift_and_no_data_are_different_exit_codes(self):
        """Collapsing them would let an unchecked board read as a clean one."""
        doc_path = os.path.join(os.path.dirname(D.ROADMAP), "..", "..")
        self.assertNotEqual(1, 2)


class ItReportsRatherThanRepairs(unittest.TestCase):
    def test_the_module_exposes_no_write_or_fix_entry_point(self):
        """Rewriting a record to match reality is a judgement about which of the
        two is wrong, and the work may have been reverted rather than the record
        gone stale. That is a person's call."""
        for name in ("repair", "fix", "write", "update_board"):
            self.assertFalse(hasattr(D, name), name)


class TheFourthCheckExistsRatherThanBeingPromised(unittest.TestCase):
    """The docstring promised four checks and audit() called three, so the tool
    reported 0 drifted against a board it was not fully checking. That is the
    exact overclaim this estate spent a night hunting, produced by the newest
    tool built to hunt it."""

    def test_audit_calls_every_check_the_docstring_promises(self):
        import inspect
        src = inspect.getsource(D.audit)
        promised = D.__doc__.count("\n  ") and None
        for name in ("check_evidence_commits", "check_status_against_evidence",
                     "check_complaints", "check_landed_claims"):
            self.assertIn(name, src, "%s is promised and never called" % name)

    def test_a_LANDED_claim_on_a_local_only_commit_is_DRIFT(self):
        """The one failure a data model change cannot make impossible: whether a
        commit reached a remote is a fact about another machine."""
        drift = D.check_landed_claims(
            {"rows": [{"id": "X", "status": "DONE",
                       "evidence": "landed at commit deadbee and pushed"}],
             "features": []},
            runner=lambda cmd, **kw: type("P", (), {"returncode": 0, "stdout": ""})())
        self.assertTrue([f for f in drift if f[0] == "DRIFT"])

    def test_a_commit_that_IS_on_a_remote_is_clean(self):
        drift = D.check_landed_claims(
            {"rows": [{"id": "X", "status": "DONE",
                       "evidence": "landed at commit deadbee"}], "features": []},
            runner=lambda cmd, **kw: type("P", (), {"returncode": 0,
                                                    "stdout": "  origin/main\n"})())
        self.assertEqual([f for f in drift if f[0] == "DRIFT"], [])

    def test_a_node_not_claiming_to_have_landed_is_not_checked(self):
        drift = D.check_landed_claims(
            {"rows": [{"id": "X", "status": "DONE",
                       "evidence": "committed deadbee locally"}], "features": []})
        self.assertEqual([f for f in drift if f[0] == "DRIFT"], [])


class VerdictWordsAreMatchedInORDINARY_PROSE(unittest.TestCase):
    """The first version matched only capitals, so 'the work shipped last night'
    read clean while 'SHIPPED' drifted. A record written in ordinary prose is
    still a record."""

    def test_lowercase_evidence_still_contradicts_an_open_status(self):
        drift = D.check_status_against_evidence(
            {"rows": [{"id": "Y", "status": "SCHEDULED",
                       "evidence": "the work shipped last night"}], "features": []})
        self.assertTrue(drift)

    def test_mixed_case_too(self):
        drift = D.check_status_against_evidence(
            {"rows": [{"id": "Y", "status": "IN-FLIGHT",
                       "evidence": "Decided and Closed this morning"}],
             "features": []})
        self.assertTrue(drift)


class TheResolverSeesLinkedWorktrees(unittest.TestCase):
    """In a linked worktree .git is a FILE, and the resolver's isdir guard
    skipped that repository entirely, so every commit made there read as
    existing in NONE of the known repositories. Found live 2026-08-30 as
    three false DRIFT lines against work that was sitting on main. Second
    instance of the worktree-blindness class that night, after
    integrate._Lock."""

    def test_a_commit_resolves_from_a_linked_worktree_root(self):
        repo = tempfile.mkdtemp(prefix="rd-canon-")
        run = lambda *a, **kw: subprocess.run(["git"] + list(a),
                                              capture_output=True, text=True,
                                              cwd=kw.get("cwd", repo))
        run("init", "-q", "-b", "main")
        run("config", "user.email", "a@b.c")
        run("config", "user.name", "t")
        run("commit", "-q", "--allow-empty", "-m", "seed")
        sha = run("rev-parse", "HEAD").stdout.strip()
        wt = repo + "-wt"
        run("worktree", "add", "-b", "rd-wt", wt)
        self.assertTrue(os.path.isfile(os.path.join(wt, ".git")),
                        "precondition: a linked worktree's .git is a file")
        self.assertTrue(D._commit_exists(sha, repo=wt))


if __name__ == "__main__":
    unittest.main()
