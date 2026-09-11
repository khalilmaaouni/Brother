"""test_continuity_days_gap: E73's floor file scores "Resume days later" at
0.5 because every proven kill point in scripts/test_continuity_matrix.py
resumes INSIDE the same session -- the clock never moves and the repository
never advances between the crash and the resume. The capability a developer
actually feels is coming back on Monday to a run that stopped Friday: the
lease is long dead, and the repository the run was written against may have
moved underneath it. This file proves the resume screen tells the truth in
that shape, never the same-session shape the matrix already covers.

Same LOCAL FORM the matrix uses (claim_store and journal called directly
against a bare run_dir, no subprocess, no loop_bridge), same seeding helpers,
copied rather than imported so this file stands alone the way the matrix
does. THE ONE NEW INGREDIENT: continuity.capsule(run_dir, clock=...) already
threads its `clock` parameter into claim_store.reconcile() (continuity.py's
own capsule() body); this file supplies a clock reading days ahead of the
real one, per this row's own instruction never to sleep and never to touch
the system clock. No journal write is ever backdated -- journal.append()
always stamps the real wall-clock "at" -- only the "now" continuity reads
against it moves, which is exactly what a session with a fast-forwarded
clock and an unmoved journal would look like from the outside.

THREE CASES:

  (1) THE CONTROL. No gap, no repository movement: must read exactly as
      the matrix's own kill point 04 (active, "wait:") reads, and the
      journal-age phrase must not appear at all -- proving the new code
      is silent when there is nothing to say.

  (2) THE GAP ALONE. Three days pass, the repository does not move. The
      claimed unit's lease (twenty minutes, claim_store.DEFAULT_TTL_SECONDS)
      is long expired, so claim_store's own dead_reason already reads it
      abandoned with no code change needed here; what this row adds is
      naming the gap, in days, on the resume screen, and a safe resume
      stays the safe next action because nothing it owns actually moved.

  (3) THE GAP PLUS A MOVED FILE. Same three-day gap, but a real commit
      lands on the target repository in between, touching the exact file
      the abandoned unit declared it owns. The unit must still read
      "abandoned" (claim_store's rule, unchanged) but its detail must
      name the file as stale rather than implying its prior result still
      holds, and the safe next action must say to re-verify against the
      new canonical revision rather than to resume as though nothing
      happened.

Python 3, standard library only. No network.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import brother_run as BR  # noqa: E402
import claim_store as C  # noqa: E402
import continuity  # noqa: E402
import journal  # noqa: E402
import work_record as WR  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

DAY = 86400.0


def _canon():
    """A real, throwaway git repo for the run's target cwd, mirroring
    test_continuity_matrix.py's own _canon() exactly, so continuity's git
    reads (HEAD, worktrees, status, and this row's new diff-between-two-
    revisions read) all answer for real instead of degrading to NO-DATA."""
    d = tempfile.mkdtemp(prefix="continuity-daysgap-repo-")
    run = lambda *a: subprocess.run(["git"] + list(a), cwd=d,
                                    capture_output=True, text=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "a@b.c")
    run("config", "user.name", "t")
    with open(os.path.join(d, "a.txt"), "w", encoding="utf-8") as fh:
        fh.write("base a\n")
    with open(os.path.join(d, "b.txt"), "w", encoding="utf-8") as fh:
        fh.write("base b\n")
    run("add", "-A")
    run("commit", "-q", "-m", "R0")
    return d


class ResumeDaysLaterMatrix(unittest.TestCase):
    """One shared two-unit shape (DG1 owns a.txt, DG2 owns b.txt), the
    matrix's own shape, so a reader comparing the two files sees the same
    fixture doing new work rather than a second vocabulary."""

    def setUp(self):
        self.run_dir = tempfile.mkdtemp(prefix="continuity-daysgap-")
        self.repo = _canon()
        self.claims_path = os.path.join(self.run_dir, BR.CLAIMS_FILENAME)
        self.rec, problems = WR.create(
            "the resume-days-later proof",
            [{"id": "DG1", "done_check": "true", "owns": ["a.txt"]},
             {"id": "DG2", "done_check": "true", "owns": ["b.txt"]}],
            store=self.run_dir)
        self.assertFalse(problems, problems)
        self.record_path = self.rec["path"]

    def _journal(self, etype, unit_id=None, payload=None):
        return journal.append(self.run_dir, etype,
                              parent_ids=journal.previous(self.run_dir),
                              unit_id=unit_id, payload=payload or {})

    def _capsule(self, clock=None):
        cap, problem = continuity.capsule(self.run_dir, clock=clock)
        self.assertIsNotNone(cap, problem)
        return cap

    def _bucket_of(self, cap, unit_id):
        for bucket, ids in cap["buckets"].items():
            if unit_id in ids:
                return bucket
        return None

    def _detail_of(self, cap, unit_id):
        return next(u["detail"] for u in cap["units"] if u["id"] == unit_id)

    def _advance_repo(self, filename, body):
        """One real commit on self.repo, touching `filename`, mirroring
        exactly the on-disk footprint a session's own work would leave
        between two resumes of the same run."""
        path = os.path.join(self.repo, filename)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        subprocess.run(["git", "add", "-A"], cwd=self.repo,
                       capture_output=True, text=True, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "R1"], cwd=self.repo,
                       capture_output=True, text=True, check=True)

    # -- case 1: the control, no gap, no movement --------------------------

    def test_no_gap_no_movement_reads_exactly_as_the_matrix_happy_path(self):
        """Mirrors test_continuity_matrix.py's own kill point 04 (claimed,
        owner confirmed alive) with no clock override at all: must read
        "active", the ordinary "wait:" next action, and the journal-age
        phrase must be entirely absent -- the new code must say nothing
        when there is nothing to say."""
        self._journal("run.opened", payload={"cwd": self.repo, "resumed": False})
        claim, problem = C.acquire(self.claims_path, "DG1", "matrix-owner")
        self.assertTrue(claim, problem)
        cap = self._capsule()
        self.assertEqual(self._bucket_of(cap, "DG1"), "active")
        self.assertEqual(self._bucket_of(cap, "DG2"), "pending")
        self.assertTrue(cap["next_action"].startswith("wait:"), cap["next_action"])
        self.assertNotIn("day(s) since", cap["zone3"]["WHERE WE WERE"])

    # -- case 2: three days pass, nothing else changes ----------------------

    def test_three_days_later_active_unit_reads_abandoned_and_gap_is_named(self):
        """Only the clock capsule() reads moves; no journal write is ever
        backdated. The claim's twenty-minute lease is long expired under a
        three-day-later "now", so claim_store's own dead_reason already
        reads DG1 abandoned with no code change here; this asserts the
        screen also NAMES the gap in days, and that a plain gap with no
        repository movement still recommends a safe resume, not a
        re-verify."""
        self._journal("run.opened", payload={"cwd": self.repo, "resumed": False})
        claim, problem = C.acquire(self.claims_path, "DG1", "matrix-owner")
        self.assertTrue(claim, problem)
        real_now = time.time()
        three_days_later = lambda: real_now + 3 * DAY
        cap = self._capsule(clock=three_days_later)
        self.assertEqual(self._bucket_of(cap, "DG1"), "abandoned")
        where = cap["zone3"]["WHERE WE WERE"]
        self.assertIn("day(s) since the last recorded activity", where)
        self.assertIn("3.0 day", where)
        self.assertTrue(cap["next_action"].startswith("resume:"),
                        cap["next_action"])

    # -- case 3: three days pass AND the repository moves underneath -------

    def test_three_days_later_with_a_moved_file_reads_stale_and_recommends_reverify(self):
        """The abandoned unit's own declared file (a.txt) is touched by a
        real commit landed between this run's last checkpoint and the
        resume. The unit must still read "abandoned" (claim_store's rule
        is unchanged) but its detail must name the file as stale rather
        than silently implying its prior claim still holds, and the safe
        next action must say to re-verify against the new canonical
        revision rather than to resume as though nothing happened. DG2
        (untouched b.txt, never claimed) must stay ordinary pending with
        no stale annotation, proving only the unit whose declared files
        actually moved is flagged."""
        self._journal("run.opened", payload={"cwd": self.repo, "resumed": False})
        claim, problem = C.acquire(self.claims_path, "DG1", "matrix-owner")
        self.assertTrue(claim, problem)
        # A capsule checkpoint lands here, exactly as brother_run.py's own
        # lifecycle checkpoints do, recording the canonical revision the
        # run was written against (R0) before the gap opens.
        ok, problem = continuity.write_capsule(self.run_dir)
        self.assertTrue(ok, problem)
        # THE GAP: the repository moves underneath the stopped run.
        self._advance_repo("a.txt", "changed while nobody was looking\n")
        real_now = time.time()
        three_days_later = lambda: real_now + 3 * DAY
        cap = self._capsule(clock=three_days_later)
        self.assertEqual(self._bucket_of(cap, "DG1"), "abandoned")
        detail = self._detail_of(cap, "DG1")
        self.assertIn("NO-DATA", detail)
        self.assertIn("stale", detail)
        self.assertIn("a.txt", cap["next_action"] + detail)
        self.assertTrue(cap["next_action"].startswith("re-verify:"),
                        cap["next_action"])
        self.assertEqual(self._bucket_of(cap, "DG2"), "pending")
        self.assertNotIn("stale", self._detail_of(cap, "DG2"))

    # -- case 4: a prior capsule.json carrying an argument-shaped revision -

    def test_argument_shaped_prior_revision_never_reaches_git_and_reads_unreadable(self):
        """Security review of commit 5654683b (MEDIUM, argument injection):
        _files_changed_between() built its argv from the PRIOR capsule.json's
        canonical_revision, an untrusted value read off disk, with no shape
        check -- a value beginning with "-" is read by git as an option, not
        a revision. This proves the fix from the caller's own vantage: a
        hand-written prior capsule.json whose canonical_revision looks like
        a git flag must never let that string reach a subprocess argv, and
        the resume screen must still render, naming the shape of the
        problem rather than silently dropping the staleness check or
        crashing."""
        self._journal("run.opened", payload={"cwd": self.repo, "resumed": False})
        claim, problem = C.acquire(self.claims_path, "DG1", "matrix-owner")
        self.assertTrue(claim, problem)
        # A hand-written prior capsule, standing in for one an attacker (or
        # simply a corrupted write) put on disk -- never produced through
        # continuity.write_capsule(), which only ever writes a real git
        # revision here.
        poisoned = "--output=/tmp/x"
        with open(os.path.join(self.run_dir, continuity.CAPSULE_FILENAME),
                  "w", encoding="utf-8") as fh:
            json.dump({"canonical_revision": poisoned}, fh)
        real_run = subprocess.run
        seen_argvs = []

        def spy(*args, **kwargs):
            seen_argvs.append(args[0] if args else kwargs.get("args"))
            return real_run(*args, **kwargs)

        with mock.patch("continuity.subprocess.run", side_effect=spy):
            cap = self._capsule()
        for argv in seen_argvs:
            self.assertNotIn(poisoned, argv,
                             "the poisoned revision reached a subprocess argv: %r"
                             % (argv,))
        detail = self._detail_of(cap, "DG1")
        self.assertIn("NO-DATA", detail)
        self.assertNotIn(poisoned, detail)
        self.assertNotIn(poisoned, json.dumps(cap))

    # -- case 5: git cannot answer for the CURRENT revision -----------------

    def test_an_unreadable_current_revision_reads_stale_not_clean(self):
        """Security review of commit 68c02d56 (should-fix, trust asymmetry):
        the staleness check was guarded by
        `if (canonical_revision and prior_revision and ...)`, so a git
        failure on THIS call (canonical_revision is None) silently skipped
        the whole check -- a prior checkpoint recorded a real revision, git
        cannot answer now, and the missing answer was read as "nothing
        moved" rather than "cannot be told". This proves the fix: a missing
        current revision must be treated the same as the existing
        argument-shaped-prior-revision case above -- every non-DONE row
        that owns a declared file reads stale, and the safe next action is
        re-verify, never a plain resume."""
        self._journal("run.opened", payload={"cwd": self.repo, "resumed": False})
        claim, problem = C.acquire(self.claims_path, "DG1", "matrix-owner")
        self.assertTrue(claim, problem)
        # A real prior checkpoint, holding a real revision (R0).
        ok, problem = continuity.write_capsule(self.run_dir)
        self.assertTrue(ok, problem)
        with mock.patch("continuity._canonical_revision", return_value=None):
            cap = self._capsule()
        self.assertEqual(self._bucket_of(cap, "DG1"), "active")
        detail = self._detail_of(cap, "DG1")
        self.assertIn("NO-DATA", detail)
        self.assertIn("stale", detail)
        self.assertTrue(cap["next_action"].startswith("re-verify:"),
                        cap["next_action"])
        # DG2 (b.txt, never claimed) stays "pending" -- staleness never
        # invents a claim -- but its detail is ALSO annotated stale: git
        # cannot say what did or did not move, so (per the existing
        # argument-shaped-revision path this mirrors) every non-DONE
        # owning row is treated as unverifiable, never only the one row
        # that happened to be claimed.
        self.assertEqual(self._bucket_of(cap, "DG2"), "pending")
        self.assertIn("stale", self._detail_of(cap, "DG2"))


if __name__ == "__main__":
    unittest.main()
