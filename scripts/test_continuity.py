"""continuity.py, driven over a temp runs root with seeded runs through
claim_store.acquire/release (the real journaling path, the same seam
test_journal_projection.py already uses), never hand-typed journal lines.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import claim_store  # noqa: E402
import continuity  # noqa: E402
import journal  # noqa: E402


class FakeClock(object):
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, n):
        self.t += n


def _write_record(run_dir, rows, outcome="do a thing", work_id="w1"):
    with open(os.path.join(run_dir, "W-%s.json" % work_id), "w",
             encoding="utf-8") as fh:
        json.dump({"outcome": outcome, "work_id": work_id, "rows": rows}, fh)


class NoJournalIsNoData(unittest.TestCase):
    def test_an_empty_directory_reads_no_data_naming_the_path(self):
        with tempfile.TemporaryDirectory() as run_dir:
            cap, problem = continuity.capsule(run_dir)
            self.assertIsNone(cap)
            self.assertIn("NO-DATA", problem)
            self.assertIn(os.path.abspath(run_dir), problem)

    def test_the_cli_prints_no_data_at_a_non_zero_exit(self):
        with tempfile.TemporaryDirectory() as run_dir:
            code = continuity.main([run_dir])
            self.assertNotEqual(code, 0)


class IntegratedAndPendingUnits(unittest.TestCase):
    def test_both_print_with_the_right_states_and_the_action_names_pending(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = os.path.join(d, "20260904T000000-w1")
            os.makedirs(run_dir)
            claims_path = os.path.join(run_dir, "claims.json")
            claim, problem = claim_store.acquire(claims_path, "U1", "workerA")
            self.assertTrue(claim, problem)
            claim_store.release(claims_path, "U1", "workerA", state="done",
                                evidence={"exit_code": 0})
            _write_record(run_dir, [
                {"id": "U1", "title": "first", "status": "DONE"},
                {"id": "U2", "title": "second", "status": "SCHEDULED"}])
            cap, problem = continuity.capsule(run_dir)
            self.assertEqual(problem, "")
            self.assertEqual(cap["buckets"]["integrated"], ["U1"])
            self.assertEqual(cap["buckets"]["pending"], ["U2"])
            units = {u["id"]: u for u in cap["units"]}
            self.assertEqual(units["U1"]["bucket"], "integrated")
            self.assertEqual(units["U2"]["bucket"], "pending")
            self.assertIn("U2", cap["next_action"])
            self.assertTrue(cap["next_action"].startswith("resume:"))


class AbandonedClaimNamesTheResumeAction(unittest.TestCase):
    def test_an_expired_lease_reads_abandoned_with_the_resume_action(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = os.path.join(d, "20260904T000000-w1")
            os.makedirs(run_dir)
            claims_path = os.path.join(run_dir, "claims.json")
            clock = FakeClock()
            claim, problem = claim_store.acquire(claims_path, "U1", "workerA",
                                                 ttl=60, clock=clock)
            self.assertTrue(claim, problem)
            _write_record(run_dir, [{"id": "U1", "title": "first",
                                     "status": "SCHEDULED"}])
            clock.advance(61)
            cap, problem = continuity.capsule(run_dir, clock=clock)
            self.assertEqual(problem, "")
            self.assertEqual(cap["buckets"]["abandoned"], ["U1"])
            self.assertEqual(cap["units"][0]["attempt"], 1)
            self.assertTrue(cap["next_action"].startswith("resume:"))
            self.assertIn("U1", cap["next_action"])


class ActiveClaimSaysWait(unittest.TestCase):
    def test_a_live_lease_reads_active_and_the_action_says_wait(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = os.path.join(d, "20260904T000000-w1")
            os.makedirs(run_dir)
            claims_path = os.path.join(run_dir, "claims.json")
            clock = FakeClock()
            claim, problem = claim_store.acquire(claims_path, "U1", "workerA",
                                                 ttl=600, clock=clock)
            self.assertTrue(claim, problem)
            _write_record(run_dir, [{"id": "U1", "title": "first",
                                     "status": "SCHEDULED"}])
            cap, problem = continuity.capsule(run_dir, clock=clock)
            self.assertEqual(problem, "")
            self.assertEqual(cap["buckets"]["active"], ["U1"])
            self.assertTrue(cap["next_action"].startswith("wait:"))


class UnclearBeatsAGuess(unittest.TestCase):
    def test_a_claimed_unit_with_no_claims_json_is_unclear_not_guessed(self):
        """The journal says U1 is claimed; claims.json (the only place a
        lease lives) is gone. This must never be silently folded into
        "active" or "abandoned" -- E73.3's own "refusal when state cannot
        be trusted"."""
        with tempfile.TemporaryDirectory() as run_dir:
            journal.append(run_dir, "claim.acquired", unit_id="U1",
                           payload={"attempt": 1, "owner": "workerA"})
            _write_record(run_dir, [{"id": "U1", "title": "first",
                                     "status": "SCHEDULED"}])
            cap, problem = continuity.capsule(run_dir)
            self.assertEqual(problem, "")
            self.assertEqual(cap["buckets"]["unclear"], ["U1"])
            self.assertTrue(cap["next_action"].startswith("do not resume"))


class EnvironmentAssumptions(unittest.TestCase):
    def test_runs_root_slots_and_adapter_are_read_from_the_run_itself(self):
        with tempfile.TemporaryDirectory() as d:
            runs_root = d
            run_dir = os.path.join(runs_root, "docs", "plan", "runs",
                                   "20260904T000000-w1")
            os.makedirs(run_dir)
            journal.append(run_dir, "run.opened",
                           payload={"cwd": "/some/target/repo", "resumed": False})
            journal.append(run_dir, "dispatch.round",
                           payload={"slots": 3, "own_tools": True})
            _write_record(run_dir, [])
            cap, problem = continuity.capsule(run_dir)
            self.assertEqual(problem, "")
            env = cap["environment"]
            self.assertEqual(env["runs_root"], os.path.abspath(runs_root))
            self.assertEqual(env["target_cwd"], "/some/target/repo")
            self.assertEqual(env["slots"], 3)
            self.assertIn("product's own", env["model_adapter"])

    def test_missing_dispatch_round_reads_no_data_for_slots_and_adapter(self):
        with tempfile.TemporaryDirectory() as run_dir:
            journal.append(run_dir, "run.opened", payload={"cwd": "/x"})
            _write_record(run_dir, [])
            cap, problem = continuity.capsule(run_dir)
            self.assertEqual(problem, "")
            self.assertIn("NO-DATA", cap["environment"]["slots"])
            self.assertIn("NO-DATA", cap["environment"]["model_adapter"])


class JSONRoundTripsAndHoldsNothingDerivable(unittest.TestCase):
    def test_the_json_output_round_trips(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = os.path.join(d, "20260904T000000-w1")
            os.makedirs(run_dir)
            claims_path = os.path.join(run_dir, "claims.json")
            claim, problem = claim_store.acquire(claims_path, "U1", "workerA")
            self.assertTrue(claim, problem)
            claim_store.release(claims_path, "U1", "workerA", state="done",
                                evidence={"exit_code": 0})
            _write_record(run_dir, [{"id": "U1", "title": "first",
                                     "status": "DONE"}])
            cap, problem = continuity.capsule(run_dir)
            self.assertEqual(problem, "")
            text = json.dumps(cap, sort_keys=True)
            reloaded = json.loads(text)
            self.assertEqual(reloaded, cap)

    def test_the_capsule_never_holds_a_file_content_a_diff_or_test_output(self):
        """Nothing derivable from the repository itself: no field carries a
        source file's text, a unified diff, or captured command output --
        those live in the attempt trace and the run log, and copying them
        here would make this a second, driftable copy of the same fact."""
        with tempfile.TemporaryDirectory() as d:
            run_dir = os.path.join(d, "20260904T000000-w1")
            os.makedirs(run_dir)
            claims_path = os.path.join(run_dir, "claims.json")
            claim, problem = claim_store.acquire(claims_path, "U1", "workerA")
            self.assertTrue(claim, problem)
            claim_store.release(
                claims_path, "U1", "workerA", state="done",
                evidence={"exit_code": 0,
                         "output": "captured stdout nobody should copy here",
                         "check_command": "python3 -m pytest tests/test_x.py"})
            _write_record(run_dir, [{"id": "U1", "title": "first",
                                     "status": "DONE"}])
            cap, problem = continuity.capsule(run_dir)
            self.assertEqual(problem, "")
            blob = json.dumps(cap)
            self.assertNotIn("captured stdout", blob)
            self.assertNotIn("---", blob)  # a unified diff's own marker
            self.assertNotIn("+++", blob)
            for key in ("diff", "output", "stdout", "stderr", "file_contents"):
                for unit in cap["units"]:
                    self.assertNotIn(key, unit)
                self.assertNotIn(key, cap)


class WriteCapsuleAtEachCheckpoint(unittest.TestCase):
    """E73.2's own claim: the engine writes a capsule into its run
    directory at each lifecycle checkpoint. write_capsule() is the piece
    this file owns and can drive directly; the checkpoints themselves
    (brother_run.py's own journal.append call sites) are proven end to end
    by test_brother_run.py's TheRunsJournalChainsFromOpenToAcceptance,
    which already asserts every one of those event types lands on a real
    run's journal."""

    def test_a_stub_runs_checkpoints_each_produce_a_successful_write(self):
        """A stub run: one journal.append per lifecycle checkpoint type
        E73.2 hooks in brother_run.py (run.opened, dispatch.round,
        unit.done, run.resumed), one write_capsule() call beside each,
        exactly as brother_run.py's own hook does. The write count is
        counted against the checkpoint events, per the row's own
        instruction."""
        with tempfile.TemporaryDirectory() as run_dir:
            _write_record(run_dir, [{"id": "U1", "title": "first",
                                     "status": "SCHEDULED"}])
            checkpoints = ["run.opened", "dispatch.round", "unit.done",
                          "run.resumed"]
            writes = []
            for etype in checkpoints:
                journal.append(run_dir, etype,
                               parent_ids=journal.previous(run_dir),
                               unit_id="U1" if etype == "unit.done" else None,
                               payload={})
                writes.append(continuity.write_capsule(run_dir))
            self.assertEqual(len(writes), len(checkpoints))
            self.assertTrue(all(ok for ok, _problem in writes), writes)
            capsule_path = os.path.join(run_dir, continuity.CAPSULE_FILENAME)
            self.assertTrue(os.path.isfile(capsule_path))
            with open(capsule_path, encoding="utf-8") as fh:
                on_disk = json.load(fh)
            self.assertEqual(on_disk["units"][0]["id"], "U1")

    def test_a_capsule_write_never_raises_out_of_a_broken_dependency(self):
        """AVAILABILITY OVER BOOKKEEPING: write_capsule() must degrade to
        (False, reason) even when capsule() itself raises out of a store
        this module does not own (claim_store.reconcile choking on a
        malformed record is the real defect this guards, found live by
        test_brother_run.py's own RefuseExhaustedUnitsPullsOnlySpentClaims
        fixture once this hook started firing beside every checkpoint)."""
        with tempfile.TemporaryDirectory() as run_dir:
            journal.append(run_dir, "run.opened", payload={"cwd": "/x"})
            _write_record(run_dir, [])
            with mock.patch.object(continuity, "capsule",
                                   side_effect=KeyError("expires_at")):
                ok, problem = continuity.write_capsule(run_dir)
            self.assertFalse(ok)
            self.assertIn("expires_at", problem)
            self.assertFalse(
                os.path.isfile(os.path.join(run_dir,
                                            continuity.CAPSULE_FILENAME)))

    def test_a_write_failure_returns_false_and_a_reason_never_raises(self):
        """The other half of AVAILABILITY OVER BOOKKEEPING: a disk-level
        failure (a full disk, an unwritable directory) during the atomic
        write itself must also degrade, not raise."""
        with tempfile.TemporaryDirectory() as run_dir:
            journal.append(run_dir, "run.opened", payload={"cwd": "/x"})
            _write_record(run_dir, [])
            with mock.patch("continuity.tempfile.mkstemp",
                            side_effect=OSError("disk full")):
                ok, problem = continuity.write_capsule(run_dir)
            self.assertFalse(ok)
            self.assertIn("disk full", problem)


if __name__ == "__main__":
    unittest.main()
