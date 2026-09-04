"""The drain loop must give a failing unit its bounded second (repair) claim,
never stop the instant one round integrates nothing new, and never spin
forever on a unit that is genuinely unrepairable.

Reproduces the defect found live 2026-08-30 (forced-repair-proof, evidence at
~/.claude/evidence/forced-repair-proof-2026-08-30.md): integrate.py
classifies a unit whose own check fails on the current canonical base as
NEEDS-REPAIR-ON-NEW-BASE and leaves it SCHEDULED, eligible for a fresh claim.
The old drain measured progress only by the integrated set growing, so a
round that produced exactly that classification (nothing newly integrated)
looked like a no-op and ended the drain before the implied retry was ever
dispatched.

WHY THIS DOES NOT DRIVE THE FULL REAL SPINE, unlike test_brother_run.py's own
suite. Driving a genuine second outer claim through the real loop_bridge
surfaced a SEPARATE, previously-latent defect while this test was being
built: scripts/worktree_lane.py's acquire() cannot re-establish a unit's lane
branch on a second claim (git checkout -b fails because the branch already
exists from the first attempt, and the fallback silently drops to a
branchless detached-HEAD lane), so integrate.py merges a stale branch ref and
a genuinely-fixed second attempt can never actually reach canonical today.
That is out of scope for this fix (flagged separately) and orthogonal to the
bug this file exists to pin: brother_run.py's OWN decision about whether to
dispatch another round at all. So this drives brother_run.main() for real,
in-process, with run_loop replaced by a fake that still goes through the
REAL claim_store.py (so attempt numbers and state transitions are authentic)
but skips git worktrees, real workers and the lane machinery entirely.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brother_run  # noqa: E402
import claim_store  # noqa: E402
import work_record as WR  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))


def sh(args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          timeout=60)


class RepairDrain(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="drain-repo-")
        for a in (["init", "-q", "-b", "main"],
                  ["config", "user.email", "a@b.c"],
                  ["config", "user.name", "t"]):
            sh(["git"] + a, self.repo)
        with open(os.path.join(self.repo, "base.txt"), "w", encoding="utf-8") as fh:
            fh.write("base\n")
        sh(["git", "add", "-A"], self.repo)
        sh(["git", "commit", "-q", "-m", "R0"], self.repo)

        self.run_dir = tempfile.mkdtemp(prefix="drain-run-")
        rec, problems = WR.create("one unit that fails once then repairs",
                                  [{"id": "F1", "done_check": "true",
                                    "owns": ["f.txt"]}], store=self.run_dir)
        self.assertEqual(problems, [])
        self.claims_path = os.path.join(self.run_dir, "claims.json")
        self._orig_run_loop = brother_run.run_loop

    def tearDown(self):
        brother_run.run_loop = self._orig_run_loop

    def _fake_run_loop(self, states):
        """A stand-in for one loop_bridge.main() round: claims F1 through the
        REAL claim_store (so its attempt count and release state are
        authentic) and releases it with the next scripted state. `states`
        past its own length repeats its last entry, matching a unit that
        keeps failing forever once genuinely unrepairable."""
        calls = {"n": 0}

        def _run(plan_path, claims_path, cwd, slots):
            i = calls["n"]
            calls["n"] += 1
            state = states[i] if i < len(states) else states[-1]
            claim, problem = claim_store.acquire(claims_path, "F1", "test-owner")
            self.assertIsNotNone(claim, problem)
            # E1's contract: a done claim must carry the check's own evidence
            # or _mark_integrated rightly refuses it. This fake worker now
            # supplies real evidence for a done, exactly as the live lane
            # does; the no-evidence refusal itself is pinned in
            # test_brother_run.py's DeliveryEvidenceOrRefusal cases.
            evidence = None
            if state == "done":
                # and the artifact must really exist on canonical, because
                # E1's contract verifies each owned path at the recorded
                # revision; a done whose artifact is absent is refused.
                with open(os.path.join(self.repo, "f.txt"), "w",
                          encoding="utf-8") as fh:
                    fh.write("repaired\n")
                sh(["git", "add", "f.txt"], self.repo)
                sh(["git", "commit", "-q", "-m", "F1 lands"], self.repo)
                rev = sh(["git", "rev-parse", "HEAD"], self.repo).stdout.strip()
                evidence = {"check_command": "true", "exit_code": 0,
                            "output": "ok", "output_truncated": False,
                            "canonical_rev": rev}
            claim_store.release(claims_path, "F1", "test-owner", state=state,
                                evidence=evidence)
            text = ("CLAIMED (1): test-owner/F1/%d\n"
                    "  F1 %-8s scope=CLEAN integrated=%s\n"
                    % (claim["attempt"], state, state == "done"))
            return (0 if state == "done" else 1), text
        return _run

    def _run_main(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = brother_run.main(["ignored", "--resume", self.run_dir,
                                     "--cwd", self.repo])
        return code, out.getvalue() + err.getvalue()

    def test_second_attempt_repairs_after_a_failed_first(self):
        """Failing before this fix: the drain stopped after round 1's failure
        (done_now == done_before) and F1 was never reclaimed, so the run
        reported it refused rather than integrated."""
        brother_run.run_loop = self._fake_run_loop(["failed", "done"])
        code, out = self._run_main()
        self.assertEqual(code, 0, out)
        # The report lists integrated units on their own lines (each with the
        # done_check that verified it) since 2026-08-31, so assert the count
        # and the unit id, not the old single-line "integrated (1): F1".
        self.assertIn("integrated (1):", out, out)
        self.assertRegex(out, r"F1\s+verified by:")
        self.assertIn("refused (0):", out, out)

        with open(self.claims_path, encoding="utf-8") as fh:
            store = json.load(fh)
        self.assertEqual(store["F1"]["state"], "done", store)
        self.assertEqual(store["F1"]["attempt"], 2,
                         "the drain must have dispatched a second claim: %r"
                         % store)

    def test_a_unit_that_never_repairs_exhausts_its_bound_and_stops(self):
        """Every attempt fails: the drain must stop on its own, bounded by
        MAX_UNIT_ATTEMPTS outer claims, rather than spinning for all 25
        rounds, and must say the repair BUDGET ran out, not merely that one
        round was a no-op."""
        brother_run.run_loop = self._fake_run_loop(["failed"])
        code, out = self._run_main()
        self.assertEqual(code, 1, out)
        self.assertIn("exhausted", out.lower(), out)
        self.assertIn("refused (1):", out, out)

        # THE GOVERNOR LINE is what a round boundary looks like on the
        # user surface since the receipt door landed (2026-08-31): the
        # old "loop_bridge round N exited" line is engine vocabulary
        # and now goes to the run log verbatim instead. Same count,
        # one line per round, read off the surface a person sees.
        rounds_run = out.count("brother_run: round ")
        self.assertEqual(rounds_run, brother_run.MAX_UNIT_ATTEMPTS,
                         "the drain must stop exactly at the attempt bound, "
                         "not spin past it:\n%s" % out)

        with open(self.claims_path, encoding="utf-8") as fh:
            store = json.load(fh)
        self.assertEqual(store["F1"]["state"], "failed", store)
        self.assertEqual(store["F1"]["attempt"], brother_run.MAX_UNIT_ATTEMPTS,
                         store)

    def test_a_unit_blocked_behind_a_failure_does_not_spin_the_drain(self):
        """The harsh EVAD 2026-08-31 defect: F1 fails forever and F2 depends
        on it, so F2 is never claimable and sits at attempt 0. The old drain
        counted F2 as 'repairable' (0 < 3) and, because F1's attempts kept
        climbing, never called the round a no-op, spinning to the 25-round
        ceiling. The drain must now stop at F1's bound, not F2's phantom
        eligibility."""
        run_dir = tempfile.mkdtemp(prefix="drain-blocked-")
        rec, problems = WR.create(
            "a failing unit and a unit blocked behind it",
            [{"id": "F1", "done_check": "true", "owns": ["f1.txt"]},
             {"id": "F2", "done_check": "true", "owns": ["f2.txt"],
              "depends_on": ["F1"]}], store=run_dir)
        self.assertEqual(problems, [])

        def _run(plan_path, cp, cwd, slots):
            claim, problem = claim_store.acquire(cp, "F1", "test-owner")
            self.assertIsNotNone(claim, problem)
            claim_store.release(cp, "F1", "test-owner", state="failed")
            return 1, ("CLAIMED (1): test-owner/F1/%d\n"
                       "  F1 failed   scope=CLEAN integrated=False\n"
                       % claim["attempt"])
        brother_run.run_loop = _run

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = brother_run.main(["ignored", "--resume", run_dir,
                                     "--cwd", self.repo])
        text = out.getvalue() + err.getvalue()
        # THE GOVERNOR LINE is what a round boundary looks like on the
        # user surface since the receipt door landed (2026-08-31): the
        # old "loop_bridge round N exited" line is engine vocabulary
        # and now goes to the run log verbatim instead. Same count,
        # one line per round, read off the surface a person sees.
        rounds_run = text.count("brother_run: round ")
        self.assertLessEqual(rounds_run, brother_run.MAX_UNIT_ATTEMPTS,
                             "the drain spun past F1's bound while F2 sat "
                             "blocked:\n%s" % text)
        self.assertEqual(code, 1, text)


if __name__ == "__main__":
    unittest.main()
