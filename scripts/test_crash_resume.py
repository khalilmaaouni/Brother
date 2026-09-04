"""The crash and resume proof: parity blocker P0.7, as a formal test.

Parity is not achieved if a long run only works while the original session
survives. So this drives the REAL controller as a subprocess, kills it with
SIGKILL mid-run, and proves from the durable state alone: nothing lost, nothing
duplicated, and the restart knows exactly what the crash left behind.

SIGKILL and not SIGTERM, deliberately: a controller cannot clean up after
SIGKILL, so whatever these tests find on disk is what a power cut would leave.
A proof that lets the dying process tidy up first proves the tidy-up, not the
crash.
"""
import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brother_run as BR  # noqa: E402
import claim_store as C  # noqa: E402
import continuity  # noqa: E402
import journal  # noqa: E402
import work_record as WR  # noqa: E402


def canon():
    d = tempfile.mkdtemp(prefix="canon-")
    run = lambda *a: subprocess.run(["git"] + list(a), cwd=d,
                                    capture_output=True, text=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "a@b.c")
    run("config", "user.name", "t")
    with open(os.path.join(d, "base.txt"), "w", encoding="utf-8") as fh:
        fh.write("base\n")
    run("add", "-A")
    run("commit", "-q", "-m", "R0")
    return d


class TheControllerDiesAndTheStateSurvives(unittest.TestCase):
    def setUp(self):
        self.repo = canon()
        self.store = os.path.join(tempfile.mkdtemp(), "claims.json")
        rec, problems = WR.create("crash proof", [
            {"id": "CR1", "done_check": "true", "owns": ["a.txt"]},
            {"id": "CR2", "done_check": "true", "owns": ["b.txt"]}],
            store=tempfile.mkdtemp())
        assert not problems, problems
        self.plan = rec["path"]

    def spawn(self, owner, worker=("sleep", "30")):
        # --slots 2, PINNED: CR1 and CR2 have no dependency between them and
        # this proof needs BOTH durably claimed before the kill lands. The
        # real scheduler's disk-derived capacity (graph_loop.py's
        # machine_capacity) legitimately drops to 1 slot under this
        # estate's own cleanup band; that is capacity POLICY, owned by
        # test_resource_gate.py, and must not decide how many workers a
        # concurrency proof exercises.
        return subprocess.Popen(
            [sys.executable, os.path.join(HERE, "loop_bridge.py"),
             "--plan", self.plan, "--claims", self.store, "--owner", owner,
             "--cwd", self.repo, "--slots", "2", "--worker-cmd"] + list(worker),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def wait_for_claims(self, n, timeout=20):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.exists(self.store):
                try:
                    with open(self.store, encoding="utf-8") as fh:
                        data = json.load(fh)
                    if sum(1 for v in data.values()
                           if v.get("state") == "claimed") >= n:
                        return data
                except ValueError:
                    pass  # sbe: allow-silent mid-write poll loop; the atomic rename makes this transient and the deadline above still fires
            time.sleep(0.1)
        return None

    def test_the_whole_arc(self):
        """One test on purpose: the arc IS the proof, and its steps only mean
        anything in sequence."""
        # 1. A controller starts and durably claims both units.
        proc = self.spawn("crash-A")
        data = self.wait_for_claims(2)
        self.assertIsNotNone(data, "the controller never claimed its units")

        # 2. It dies without warning, mid-worker.
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)

        # 3. THE CLAIMS SURVIVE THE CRASH, still marked claimed: the crash left
        # evidence, not silence. This is claim-before-spawn paying off.
        with open(self.store, encoding="utf-8") as fh:
            after = json.load(fh)
        held = [u for u, v in after.items() if v.get("state") == "claimed"]
        self.assertEqual(sorted(held), ["CR1", "CR2"])

        # 4. NO DUPLICATE CLAIM while genuinely still live. PRE-EXISTING BUG
        # FOUND AND FIXED HERE, unrelated to slots: commit 7090983 ("Gap 1:
        # crash recovery reads a dead owner in seconds, not the full 20
        # minute lease") made live() also check the owning pid, so a claim
        # whose pid is confirmed dead ON THIS HOST is instantly reclaimable
        # by design (see test_claim_store.py's own
        # test_a_dead_owner_reads_abandoned_within_seconds_not_the_full_lease).
        # That commit never updated this test, so asserting refusal
        # against CR1 straight after the SIGKILL above (same host,
        # confirmed-dead pid) would fail against Gap 1's own intended
        # contract, not prove anything. This was masked until now because
        # the disk-band slot=1 issue this session fixed meant the second
        # unit was never even claimed, so this line was never reached.
        # What Gap 1 explicitly still protects is a claim from ANOTHER
        # host (its hostname guard: a claim's pid is only checked against
        # this host's process table when the hostname matches). Prove that
        # still holds, on a COPY of the store so CR1's real claim above is
        # untouched and the rest of this arc proceeds exactly as designed.
        with open(self.store, encoding="utf-8") as fh:
            cross_host = json.load(fh)
        cross_host["CR1"]["hostname"] = "some-other-host-entirely"
        cross_host_store = os.path.join(tempfile.mkdtemp(), "claims.json")
        with open(cross_host_store, "w", encoding="utf-8") as fh:
            json.dump(cross_host, fh)
        stolen, problem = C.acquire(cross_host_store, "CR1", "opportunist")
        self.assertIsNone(stolen)
        self.assertIn("claimed by crash-A", problem)

        # 5. A restart reconciles FIRST and sees exactly what happened. Both
        # units read "abandoned", not "in-flight": reconcile's own
        # liveness check is the same live() Gap 1 changed, so a confirmed-
        # dead same-host pid is recognised within seconds rather than
        # reported falsely "in-flight" until the full lease expires. This
        # status name is the other half of the pre-existing bug fixed in
        # step 4 above: "abandoned" here is reported rather than acted on,
        # exactly like "in-flight" was.
        found, _ = C.reconcile(self.store)
        self.assertEqual({f["unit_id"]: f["status"] for f in found},
                         {"CR1": "abandoned", "CR2": "abandoned"})
        self.assertTrue(all(f["owner"] == "crash-A" for f in found))

        # 6. THE RIGHTFUL OWNER RESUMES: same owner re-acquires without waiting
        # for expiry, attempt 2, and a fast worker completes the work.
        proc2 = self.spawn("crash-A", worker=("true",))
        out, err = proc2.communicate(timeout=120)
        self.assertIn("CLAIMED", out + err)

        # 7. NOTHING IS LOST AND NOTHING RERUN AS ATTEMPT 1: the store shows
        # both units released with attempt 2, and the release records are KEPT,
        # so a completed unit stays distinguishable from one nobody took.
        with open(self.store, encoding="utf-8") as fh:
            final = json.load(fh)
        for uid in ("CR1", "CR2"):
            self.assertNotEqual(final[uid]["state"], "claimed", uid)
            self.assertEqual(final[uid]["attempt"], 2, uid)
            self.assertIn("released_at", final[uid], uid)

    def test_after_expiry_another_owner_may_take_over_and_says_so(self):
        proc = self.spawn("crash-A")
        self.assertIsNotNone(self.wait_for_claims(2))
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)
        # the lease runs out
        with open(self.store, encoding="utf-8") as fh:
            data = json.load(fh)
        for v in data.values():
            v["expires_at"] = time.time() - 1
        with open(self.store, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        found, _ = C.reconcile(self.store)
        self.assertTrue(all(f["status"] == "abandoned" for f in found))
        taken, _ = C.acquire(self.store, "CR1", "successor")
        self.assertIsNotNone(taken)
        self.assertEqual(taken["reclaimed_from"], "crash-A",
                         "a takeover must name whose work it inherits")


class ACapsuleSurvivesAKillAfterIntegration(unittest.TestCase):
    """E73.2's own done-check, in the fast local form this file's other
    fixtures already use (claim_store.acquire/release against a bare tempdir,
    no subprocess): a unit integrates, its capsule is written, and the
    process dies right there -- nothing more is ever written to run_dir,
    exactly what a SIGKILL leaves. --continue's own resume screen
    (brother_run._print_resume_screen, E73.2) then reads that capsule
    straight off disk and prints it, never guessing.

    A REAL brother_run.py subprocess, killed mid-drain and resumed through
    its own --continue flag end to end, is NOT reproduced here: that is
    test_brother_run.py's TheRunsJournalChainsFromOpenToAcceptance's
    territory (the real door, the real loop_bridge, a stub model) and
    E73.3's own fourteen-point hostile resume matrix. This proves the
    narrower, fast claim that belongs beside this file's other crash
    fixtures: the capsule that a real kill would leave behind is readable
    and prints correctly."""

    def test_a_run_killed_after_integration_resumes_and_prints_the_capsule(self):
        run_dir = tempfile.mkdtemp(prefix="capsule-crash-")
        claims_path = os.path.join(run_dir, "claims.json")
        claim, problem = C.acquire(claims_path, "CR1", "crash-B")
        self.assertTrue(claim, problem)
        C.release(claims_path, "CR1", "crash-B", state="done",
                  evidence={"exit_code": 0})
        with open(os.path.join(run_dir, "W-w1.json"), "w",
                 encoding="utf-8") as fh:
            json.dump({"outcome": "capsule survives a kill after integration",
                      "work_id": "w1",
                      "rows": [{"id": "CR1", "title": "first",
                               "status": "DONE"}]}, fh)
        journal.append(run_dir, "run.opened",
                       payload={"cwd": "/some/target/repo", "resumed": False})
        journal.append(run_dir, "unit.done", unit_id="CR1", payload={})
        ok, problem = continuity.write_capsule(run_dir)
        self.assertTrue(ok, problem)

        # THE KILL: nothing more is ever written to run_dir after this
        # point -- exactly what a SIGKILL leaves behind: the capsule from
        # the last checkpoint before the process died, claims.json still
        # marked done, no process, no in-memory state anywhere.

        # THE RESUME: brother_run.py's own --continue hook reads the
        # capsule straight off disk and prints it, never rebuilding or
        # guessing.
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            BR._print_resume_screen(
                run_dir, "capsule survives a kill after integration")
        printed = out.getvalue()
        self.assertIn("capsule survives a kill after integration", printed)
        self.assertIn("CR1", printed)
        self.assertIn("integrated", printed)
        self.assertNotIn("NO-DATA: 'capsule survives a kill", printed)

    def test_a_run_with_no_capsule_reads_no_data_naming_the_outcome(self):
        """A run from before E73.1/E73.2, or one whose only capsule write
        ever attempted failed: --continue must say so by name, never guess
        or stay silent."""
        run_dir = tempfile.mkdtemp(prefix="capsule-crash-nodata-")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            BR._print_resume_screen(run_dir, "an outcome with no capsule")
        printed = out.getvalue()
        self.assertIn("NO-DATA", printed)
        self.assertIn("an outcome with no capsule", printed)


if __name__ == "__main__":
    unittest.main()
