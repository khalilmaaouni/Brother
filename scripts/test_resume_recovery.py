"""RESUME-FIX F4: a worker that returned before a kill is recovered on the
resume, never run a second time, and only while every binding its
checkpoint names still holds.

Every case drives the entry point, loop_bridge.run_node, twice: once as the
run that gets killed (its worker really returns and commits in a real git
lane, under a real managed-safety fence in that lane's own store), and once
as the resume. The success case proves the worker is not asked again and
that the scope audit measured the ORIGINAL pre-edit baseline. Every refusal
case breaks exactly ONE binding between the two runs and proves the worker
was asked again, nothing was recovered, and which binding refused it, so
each guard has a fixture only it can refuse.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import journal  # noqa: E402
import loop_bridge as B  # noqa: E402
import managed_safety  # noqa: E402
import worktree_lane  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n"
                     % os.path.basename(__file__))


def git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


class Spawn(object):
    """bm_worker_spawn's shape. The child writes out.txt in its lane and
    commits it, the way model_worker.commit_changes does after a turn."""
    DEFAULT_TIMEOUT_SECONDS = 60

    def __init__(self):
        self.launches = 0

    def SpawningWorker(self, argv, cwd=None, environ=None, timeout=None):
        spawn = self

        class Child(object):
            def run(self, unit):
                spawn.launches += 1
                with open(os.path.join(cwd, "out.txt"), "a",
                          encoding="utf-8") as fh:
                    fh.write("written by launch %d\n" % spawn.launches)
                git(["add", "-A"], cwd)
                git(["commit", "-q", "-m", "unit %s" % unit["unit_id"]], cwd)
                return {"status": "returned", "artifacts": ["out.txt"],
                        "worker_claim": "wrote out.txt"}
        return Child()


class Asked(object):
    """Counts how often run_node asks the worker, whatever it then does."""

    def __init__(self, inner):
        self.inner, self.asked = inner, 0

    def run(self, unit, cwd=None):
        self.asked += 1
        return self.inner.run(unit, cwd=cwd)

    def replay_refusal(self, unit_id):
        return self.inner.replay_refusal(unit_id)


class CheckVerify(object):
    """Runs the unit's own done_check in the cwd it is handed."""

    def __init__(self):
        self.ran_in = []

    def verify(self, unit, cwd=None):
        self.ran_in.append(cwd)
        code = subprocess.run(unit["done_check"], shell=True, cwd=cwd).returncode
        return {"verdict": "PASS" if code == 0 else "FAIL",
                "reason": "exit %d" % code}

    def is_pass(self, result):
        return result.get("verdict") == "PASS"


class NoRepair(object):
    def __init__(self):
        self.called = 0

    def repair(self, unit, verdict, worker, cwd=None, max_attempts=3):
        self.called += 1
        return {"outcome": "EXHAUSTED", "attempts": [],
                "final_verdict": verdict, "reason": "not in this test"}


class KilledThenResumed(unittest.TestCase):
    """The state a real SIGKILL leaves after a worker returned: its commit
    in the lane, its fence claim active under a dead session, its budget
    recorded, its checkpoint written, and the settle path's own journal
    event saying the claim was killed rather than released."""

    UNIT = "U"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="resume-recovery-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        for args in (["init", "-q", "-b", "main"],
                     ["config", "user.email", "t@example.invalid"],
                     ["config", "user.name", "t"]):
            git(args, self.repo)
        with open(os.path.join(self.repo, "seed.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("seed\n")
        git(["add", "-A"], self.repo)
        git(["commit", "-q", "-m", "seed"], self.repo)
        self.base = git(["rev-parse", "HEAD"], self.repo)
        self.run_dir = os.path.join(self.tmp, "run-20260926T000000-u")
        os.makedirs(self.run_dir)
        for patcher in (
                mock.patch.dict(os.environ,
                                {journal.RUN_DIR_ENV_VAR: self.run_dir}),
                mock.patch.object(B.resource_gate, "read",
                                  lambda *a, **k: {"disk_free_gib": 500.0}),
                mock.patch.object(B, "LOAD_REFUSALS_LOG",
                                  os.path.join(self.tmp, "refusals.jsonl"))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.lane, branch, problem = worktree_lane.acquire(self.repo, self.UNIT)
        self.assertTrue(self.lane, problem)
        self.spawn = Spawn()
        with mock.patch.object(B, "_process_identity",
                               lambda: (dead_pid(), B.claim_store._hostname())):
            self.first = B.run_node(self.node(), self.parts(),
                                    B.LaneWorker(self.spawn, ["stub"]),
                                    cwd=self.lane)
        self.assertEqual(self.first["verdict"], "PASS", self.first)
        self.assertEqual(self.spawn.launches, 1)
        self.assertTrue(os.path.isfile(self.checkpoint_path()))
        journal.append(self.run_dir, "claim.orphaned_by_kill",
                       unit_id=self.UNIT)

    def node(self, owns=("out.txt",)):
        return {"id": self.UNIT, "name": "one unit",
                "done_check": "test -f out.txt", "owns": list(owns)}

    def parts(self):
        self.verify, self.repair = CheckVerify(), NoRepair()
        return {"spawn": None, "verify": self.verify, "repair": self.repair}

    def resume(self, node=None, cwd=None):
        worker = Asked(B.LaneWorker(self.spawn, ["stub"]))
        record = B.run_node(node or self.node(), self.parts(), worker,
                            cwd=cwd or self.lane)
        return record, worker.asked

    def events(self, kind):
        return [e for e in journal.read(self.run_dir) or []
                if e.get("type") == kind]

    def checkpoint_path(self):
        return B.worker_checkpoint_path(self.run_dir, self.UNIT)

    def edit_checkpoint(self, reseal=True, **fields):
        with open(self.checkpoint_path(), encoding="utf-8") as fh:
            saved = json.load(fh)
        saved["body"].update(fields)
        if reseal:
            saved["seal"] = B._checkpoint_seal(saved["body"])
        with open(self.checkpoint_path(), "w", encoding="utf-8") as fh:
            json.dump(saved, fh)
        return saved["body"]

    def edit_budget(self, **fields):
        path = B.worker_budget_path(self.run_dir, self.UNIT)
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
        state.update(fields)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh)

    def fence_states(self, lane=None):
        bs, _fh, problem = managed_safety._load_fence_modules()
        self.assertIsNotNone(bs, problem)
        store = bs.ReadOnlyStore(os.path.realpath(lane or self.lane))
        try:
            return [r["state"] for r in bs._exec(
                store, "SELECT state FROM records WHERE name=?",
                (self.UNIT,)).fetchall()]
        finally:
            store.close()

    def assertRefused(self, record, asked, because, lane=None):
        """The worker was asked again, nothing was recovered, the named
        binding is the one that refused, and the fence was not moved."""
        self.assertEqual(asked, 1, record)
        self.assertEqual(self.events("worker.recovered"), [])
        whys = [e["payload"]["why"] for e in self.events("worker.recovery_refused")]
        self.assertEqual(len(whys), 1, whys)
        self.assertIn(because, whys[0])
        self.assertEqual(self.fence_states(lane), ["active"])


class ACapturedWorkerResultIsRecovered(KilledThenResumed):

    def test_the_worker_is_not_asked_again_and_its_edit_is_checked(self):
        record, asked = self.resume()
        self.assertEqual(asked, 0)
        self.assertEqual(self.spawn.launches, 1)
        self.assertEqual(record["verdict"], "PASS", record)
        self.assertTrue(record["integrable"], record)
        self.assertEqual(record["worker_status"], "returned")
        self.assertEqual(self.verify.ran_in, [self.lane])
        # Measured against the ORIGINAL base: against the lane's HEAD the
        # audit would see nothing changed at all.
        self.assertEqual(record["scope"]["verdict"], "CLEAN", record["scope"])
        self.assertIn("1 path(s) changed", record["scope"]["reason"])
        recovered = self.events("worker.recovered")
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["payload"]["base"], self.base[:12])
        self.assertEqual(self.fence_states(), ["adopted"])

    def test_after_adoption_a_new_fence_claim_is_possible_again(self):
        """The dead session's claim no longer blocks the unit, so a repair
        that needs a worker after a recovered red check can still get one."""
        self.resume()
        token, why = managed_safety.materialize(
            self.lane, {"unit_id": self.UNIT, "write_scope": ["out.txt"]})
        self.assertTrue(token, why)

    def test_a_second_recovery_after_a_win_is_refused(self):
        unit = {"unit_id": self.UNIT, "write_scope": ["out.txt"]}
        self.assertIsNotNone(B.recover_worker_result(unit, self.lane))
        self.assertIsNone(B.recover_worker_result(unit, self.lane))
        whys = [e["payload"]["why"] for e in self.events("worker.recovery_refused")]
        self.assertEqual(len(whys), 1, whys)
        self.assertIn("no active fence claim", whys[0])

    def test_of_two_concurrent_recoveries_exactly_one_wins(self):
        unit = {"unit_id": self.UNIT, "write_scope": ["out.txt"]}
        gate, won = threading.Barrier(2), []

        def race():
            gate.wait()
            won.append(B.recover_worker_result(unit, self.lane))
        threads = [threading.Thread(target=race) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertEqual(sorted(w is not None for w in won), [False, True])
        self.assertEqual(len(self.events("worker.recovered")), 1)
        self.assertEqual(self.fence_states(), ["adopted"])


class EveryBindingIsCheckedOnItsOwn(KilledThenResumed):

    def test_an_ordinarily_released_unit_is_never_a_candidate(self):
        journal.append(self.run_dir, "claim.released", unit_id=self.UNIT)
        record, asked = self.resume()
        self.assertEqual(asked, 1)
        self.assertEqual(self.events("worker.recovered"), [])
        self.assertEqual(self.events("worker.recovery_refused"), [])

    def test_a_worker_killed_before_it_returned_has_no_checkpoint(self):
        os.unlink(self.checkpoint_path())
        self.assertRefused(*self.resume(), because="no checkpoint")

    def test_a_corrupt_checkpoint_is_refused(self):
        # A field no other binding reads, so only the seal can refuse it.
        self.edit_checkpoint(reseal=False, worker_result={"status": "forged"})
        self.assertRefused(*self.resume(), because="seal")

    def test_a_checkpoint_from_another_run_is_refused(self):
        self.edit_checkpoint(run="run-some-other-run")
        self.assertRefused(*self.resume(), because="different run")

    def test_a_lane_that_moved_is_refused(self):
        moved = os.path.join(self.tmp, "moved-lane")
        git(["worktree", "move", self.lane, moved], self.repo)
        self.assertRefused(*self.resume(cwd=moved), because="not in the lane",
                           lane=moved)

    def test_a_lane_whose_head_moved_is_refused(self):
        with open(os.path.join(self.lane, "out.txt"), "a",
                  encoding="utf-8") as fh:
            fh.write("a write nobody checkpointed\n")
        git(["commit", "-q", "-am", "foreign"], self.lane)
        self.assertRefused(*self.resume(), because="lane HEAD")

    def test_a_lane_on_another_branch_is_refused(self):
        git(["checkout", "-q", "-b", "lane/elsewhere"], self.lane)
        self.assertRefused(*self.resume(), because="lane branch")

    def test_a_base_equal_to_the_lane_commit_is_refused(self):
        self.edit_checkpoint(base=git(["rev-parse", "HEAD"], self.lane))
        self.assertRefused(*self.resume(), because="proper ancestor")

    def test_a_base_off_the_lanes_own_history_is_refused(self):
        with open(os.path.join(self.repo, "later.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("canonical moved on\n")
        git(["add", "-A"], self.repo)
        git(["commit", "-q", "-m", "later"], self.repo)
        self.edit_checkpoint(base=git(["rev-parse", "HEAD"], self.repo))
        self.assertRefused(*self.resume(), because="proper ancestor")

    def test_foreign_dirt_in_the_lane_is_refused(self):
        with open(os.path.join(self.lane, "stray.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("not the worker's\n")
        self.assertRefused(*self.resume(), because="uncommitted")

    def test_a_worker_still_in_flight_is_refused(self):
        self.edit_budget(in_flight=True)
        self.assertRefused(*self.resume(), because="may still be writing")

    def test_a_later_worker_attempt_is_refused(self):
        with open(B.worker_budget_path(self.run_dir, self.UNIT),
                  encoding="utf-8") as fh:
            attempts = json.load(fh)["attempts"]
        self.edit_budget(attempts=attempts + 1)
        self.assertRefused(*self.resume(), because="another worker attempt")

    def test_a_live_owner_is_refused(self):
        self.edit_checkpoint(owner_pid=os.getpid())
        self.assertRefused(*self.resume(), because="not proven dead")

    def test_a_session_that_never_held_the_lane_is_refused_and_mints_nothing(self):
        bogus = "brother-managed-" + "0" * 32
        self.edit_checkpoint(fence_session=bogus)
        _bs, fh, _problem = managed_safety._load_fence_modules()
        token = fh.token_path(os.path.realpath(self.lane), bogus)
        self.assertRefused(*self.resume(), because="never held a claim")
        self.assertFalse(os.path.exists(token))

    def test_a_claim_another_session_now_holds_is_refused_by_the_store(self):
        """adopt() leaves this refusal to the store's own ownership guard
        (live-session-adopt-blocked) rather than re-checking it, so this is
        the fixture that proves the store still gives it."""
        with open(self.checkpoint_path(), encoding="utf-8") as fh:
            prior = json.load(fh)["body"]["fence_session"]
        unit = {"unit_id": self.UNIT, "write_scope": ["out.txt"]}
        ok, why = managed_safety.adopt(self.lane, unit, prior)
        self.assertTrue(ok, why)
        token, why = managed_safety.materialize(self.lane, unit)
        self.assertTrue(token, why)
        record, asked = self.resume()
        self.assertEqual(asked, 1, record)
        self.assertEqual(self.events("worker.recovered"), [])
        whys = [e["payload"]["why"] for e in self.events("worker.recovery_refused")]
        self.assertEqual(len(whys), 1, whys)
        self.assertIn("store refused the adoption", whys[0])
        self.assertEqual(sorted(self.fence_states()), ["active", "adopted"])

    def test_a_changed_write_scope_is_refused(self):
        self.assertRefused(*self.resume(node=self.node(owns=("out.txt",
                                                             "more.txt"))),
                           because="write scope changed")


if __name__ == "__main__":
    unittest.main()
