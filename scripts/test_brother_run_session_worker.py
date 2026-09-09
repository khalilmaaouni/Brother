"""FX-A: inside a session the units are the session's own work, not a worker's.

THE DEFECT THIS SUITE PINS (persona dogfood 2026-09-07, scenarios A1-S1,
A1-S4, A3-S1 and A3-S3; measured again by an Opus pre-flight on 2026-09-08).
D-001 closed the DECOMPOSER half of "a model call cannot be made from inside
a coding session": scripts/brother_run.py refuses to spawn a headless
decomposer in a session and names the --plan route instead. The WORKER half
was left open. scripts/model_worker.py resolves its command from
MODEL_WORKER_CMD or falls back to the host's own headless client, with no
session guard anywhere on that path, so every BUILD IT scenario reached the
worker and died there: three attempts, empty stderr, no worktree anybody
could use and no test run.

THE ROUTE THAT REPLACES IT is the same shape the founder already adopted for
the decomposer. Inside a session, with nobody having named a worker command,
brother_run runs the plan up to the claims and stops: it claims the ready
units, opens one worktree per unit under the run directory, writes the
handoff, and prints the block naming each unit's worktree, objective, check
and declared writes, plus the exact command that verifies them. The session
does the work in those worktrees; the continuation command commits what it
finds in each lane, runs each check there, merges what passes and writes the
receipt. Exit code 3 is that state and only that state: the units are
claimed, and the work is the session's own.

Outside a session, or with MODEL_WORKER_CMD named, nothing changes: the
headless worker is spawned exactly as it always was. The last test here is
that regression guard, and it is what makes this a routing rule rather than
the removal of a capability.

No network and no real model anywhere in here. The base class is
test_brother_run_plan.PlanFileRunBase, reused rather than copied, so the PATH
shim that tattles on any nested client is the same one D-001's own suite
measures with.
"""
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
BROTHER_RUN = os.path.join(HERE, "brother_run.py")
import brother_run as _br  # noqa: E402
from test_brother_run_plan import (  # noqa: E402
    PlanFileRunBase, run_dirs, sh, two_units, write_contract, write_plan)


def git(args, cwd):
    return subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True,
                          text=True, timeout=60)


class SessionWorkerBase(PlanFileRunBase):
    """A session with NO worker command named, which is the whole point: the
    inherited base sets MODEL_WORKER_CMD to its writer stub, and that is
    exactly the opt-in this route must leave alone. Every test below removes
    it and sets the session marker by name, so what is measured is the
    engine's routing and never the shell the suite happens to run in."""

    def setUp(self):
        super(SessionWorkerBase, self).setUp()
        self.env.pop("MODEL_WORKER_CMD", None)
        self.env["CLAUDECODE"] = "1"
        self.contract = write_contract(self.tmp, "two files exist",
                                       commands=("test -f one.txt",
                                                 "test -f two.txt"))

    def plan_run(self, units=None, extra=()):
        plan = write_plan(self.tmp, units or two_units())
        return sh([sys.executable, BROTHER_RUN, "two files exist",
                   "--cwd", self.repo, "--runs-root", self.tmp,
                   "--plan", plan, "--contract", self.contract] + list(extra),
                  env=self.env)

    def run_dir(self):
        runs = run_dirs(self.tmp)
        self.assertEqual(len(runs), 1, "expected one run directory: %r" % runs)
        return os.path.join(self.tmp, "docs", "plan", "runs", runs[0])

    def handoff(self):
        """The run directory's own handoff record, or the assertion that
        names its absence. Read from disk rather than parsed out of stdout:
        the block a person reads is prose, and the paths a test drives are
        data."""
        path = os.path.join(self.run_dir(), _br.SESSION_DIRNAME,
                            _br.SESSION_HANDOFF_FILENAME)
        self.assertTrue(os.path.isfile(path), "no handoff at %s" % path)
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def claims(self):
        path = os.path.join(self.run_dir(), _br.CLAIMS_FILENAME)
        self.assertTrue(os.path.isfile(path), "no claim store at %s" % path)
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)


class InsideASessionTheUnitsAreClaimedAndNoWorkerIsSpawned(SessionWorkerBase):

    def test_a_two_unit_plan_claims_both_opens_both_lanes_and_spawns_nothing(self):
        """THE CALIBRATION. Before the fix this run reached model_worker.py,
        which resolved the host's own headless client and spawned it, so the
        PATH shim logged an argv line; the assertion on self.spawned() is the
        one that fails on the untouched tree. After the fix nothing is
        spawned, the exit code is 3, both worktrees exist with the claim
        recorded, and the block names them."""
        proc = self.plan_run()
        out = proc.stdout + proc.stderr
        self.assertEqual(
            self.spawned(), [],
            "the run spawned a headless client (%s) for the WORKER inside a "
            "session: %r" % ("/".join(self.shadowed), self.spawned()))
        self.assertEqual(proc.returncode, _br.EXIT_UNITS_ARE_YOURS, out)

        record = self.handoff()
        self.assertEqual(sorted(record["units"]), ["A1", "A2"], record)
        for unit_id in ("A1", "A2"):
            lane = record["units"][unit_id]
            self.assertTrue(os.path.isdir(lane["worktree"]),
                            "no worktree for %s at %s" % (unit_id,
                                                          lane["worktree"]))
            # A real lane, not merely a directory: git answers inside it and
            # it sits on its own branch, which is what integration merges.
            self.assertEqual(lane["branch"], "lane/" + unit_id, lane)
            self.assertEqual(
                git(["rev-parse", "--abbrev-ref", "HEAD"],
                    lane["worktree"]).stdout.strip(), lane["branch"], lane)
            # The block a person reads names the same path, with the unit's
            # objective, its check and what it may write beside it.
            self.assertIn(lane["worktree"], out, out)
        self.assertIn("create file one", out, out)
        self.assertIn("test -f one.txt", out, out)
        self.assertIn("one.txt", out, out)
        # The claim store holds both, so a second session cannot start on
        # work this one just handed to its own model.
        claims = self.claims()
        self.assertEqual(sorted(claims), ["A1", "A2"], claims)
        for unit_id in ("A1", "A2"):
            self.assertEqual(claims[unit_id]["state"], "claimed", claims)
        # And the continuation command is quoted ready to paste.
        self.assertIn("--continue", out, out)
        self.assertIn(os.path.abspath(self.repo), out, out)
        # Nothing was merged: canonical is untouched until the checks pass.
        self.assertFalse(os.path.exists(os.path.join(self.repo, "one.txt")), out)


class ContinuingAfterTheSessionDidTheWorkIntegratesIt(SessionWorkerBase):

    def test_continue_commits_each_lane_runs_its_check_and_writes_the_receipt(self):
        """The other half of the route: the session did the work in the
        worktrees it was handed, and the continuation command is what turns
        that into a merged, verified delivery. The session here writes the
        file and runs the unit's own check in its own lane, exactly as the
        printed block asks; committing is the engine's job, the same
        model_worker.commit_changes the headless worker already uses."""
        self.assertEqual(self.plan_run().returncode, _br.EXIT_UNITS_ARE_YOURS)
        record = self.handoff()
        for unit_id, want in (("A1", "one.txt"), ("A2", "two.txt")):
            lane = record["units"][unit_id]["worktree"]
            with open(os.path.join(lane, want), "w", encoding="utf-8") as fh:
                fh.write("written by the session\n")
            check = subprocess.run(record["units"][unit_id]["done_check"],
                                   cwd=lane, shell=True, capture_output=True,
                                   text=True, timeout=60)
            self.assertEqual(check.returncode, 0,
                             "the session's own check failed in %s" % lane)

        proc = sh([sys.executable, BROTHER_RUN, "--continue",
                   "--cwd", self.repo, "--runs-root", self.tmp], env=self.env)
        out = proc.stdout + proc.stderr
        self.assertEqual(self.spawned(), [],
                         "continuing spawned a headless client: %r"
                         % self.spawned())
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("integrated (2):", out, out)
        self.assertIn("brother_run: receipt: ", out, out)
        self.assertTrue(os.path.exists(os.path.join(self.repo, "one.txt")), out)
        self.assertTrue(os.path.exists(os.path.join(self.repo, "two.txt")), out)

    def test_a_lane_the_session_left_untouched_does_not_integrate(self):
        """The check still decides. A session that hands back a lane it never
        wrote in gets that unit refused, not merged: this route removes the
        spawned worker and nothing else, and a unit whose own check is red on
        its own lane has proven nothing."""
        self.assertEqual(self.plan_run().returncode, _br.EXIT_UNITS_ARE_YOURS)
        record = self.handoff()
        lane = record["units"]["A1"]["worktree"]
        with open(os.path.join(lane, "one.txt"), "w", encoding="utf-8") as fh:
            fh.write("written by the session\n")
        proc = sh([sys.executable, BROTHER_RUN, "--continue",
                   "--cwd", self.repo, "--runs-root", self.tmp], env=self.env)
        out = proc.stdout + proc.stderr
        self.assertNotEqual(proc.returncode, 0, out)
        self.assertTrue(os.path.exists(os.path.join(self.repo, "one.txt")), out)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "two.txt")), out)




class AHandoffOutranksTheRouteSoNoLaneIsDestroyed(SessionWorkerBase):
    """The safety property the two-process shape creates and has to hold.

    Once units are handed over, their work lives on `lane/<unit>` branches in
    worktrees under the run directory. worktree_lane.acquire() DELIBERATELY
    removes a leftover lane/<unit> branch before creating a new one (its own
    stale-lane rule, right for a crashed run), so a continue that took the
    spawned route would delete exactly the work the session had just done.
    A continue therefore follows the handoff, not the shell it is typed in."""

    def test_continuing_from_a_plain_terminal_still_verifies_the_lanes(self):
        self.assertEqual(self.plan_run().returncode, _br.EXIT_UNITS_ARE_YOURS)
        record = self.handoff()
        for unit_id, want in (("A1", "one.txt"), ("A2", "two.txt")):
            lane = record["units"][unit_id]["worktree"]
            with open(os.path.join(lane, want), "w", encoding="utf-8") as fh:
                fh.write("written by the session\n")

        # A plain terminal: no session marker, no worker command, which
        # before this rule was the shape that reached the spawned worker and
        # its lane-clearing acquire.
        env = dict(self.env)
        for var in _br.brother_paths.CLAUDE_MARKER_VARS:
            env.pop(var, None)
        for var in _br.brother_paths.CODEX_MARKER_VARS:
            env.pop(var, None)
        proc = sh([sys.executable, BROTHER_RUN, "--continue",
                   "--cwd", self.repo, "--runs-root", self.tmp], env=env)
        out = proc.stdout + proc.stderr
        self.assertEqual(self.spawned(), [],
                         "the lanes were re-acquired and a worker spawned "
                         "over the session's own work: %r" % self.spawned())
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("integrated (2):", out, out)
        self.assertTrue(os.path.exists(os.path.join(self.repo, "one.txt")), out)
        self.assertTrue(os.path.exists(os.path.join(self.repo, "two.txt")), out)

class ADependentUnitIsHandedOverAfterItsDependenciesLand(SessionWorkerBase):
    """A handoff names ONE batch, because that is all the scheduler admits at
    once. A unit whose dependency has not integrated is not ready, has no
    lane, and cannot be handed to anybody; it becomes the session's work on
    the continue AFTER the one that merged what it waits on. This is the
    test that the route composes across batches rather than only working for
    a graph with no edges."""

    def do_the_work(self, handoff):
        for unit_id, lane in handoff["units"].items():
            target = lane["writes"][0]
            with open(os.path.join(lane["worktree"], target), "w",
                      encoding="utf-8") as fh:
                fh.write("written by the session for %s\n" % unit_id)

    def test_three_units_take_two_handovers_and_land_in_order(self):
        units = [
            {"id": "A1", "objective": "create file one",
             "done_check": "test -f one.txt", "writes": ["one.txt"],
             "deps": []},
            {"id": "A2", "objective": "create file two",
             "done_check": "test -f two.txt", "writes": ["two.txt"],
             "deps": []},
            {"id": "A3", "objective": "create file three",
             "done_check": "test -f three.txt", "writes": ["three.txt"],
             "deps": ["A1", "A2"]},
        ]
        contract = write_contract(self.tmp, "three files exist",
                                  commands=("test -f one.txt",
                                            "test -f two.txt",
                                            "test -f three.txt"),
                                  name="three-contract.json")
        plan = write_plan(self.tmp, units, "three.json")
        first = sh([sys.executable, BROTHER_RUN, "three files exist",
                    "--cwd", self.repo, "--runs-root", self.tmp,
                    "--plan", plan, "--contract", contract], env=self.env)
        self.assertEqual(first.returncode, _br.EXIT_UNITS_ARE_YOURS,
                         first.stdout + first.stderr)
        handoff = self.handoff()
        self.assertEqual(sorted(handoff["units"]), ["A1", "A2"],
                         "A3 depends on both and must not be handed over yet")
        self.do_the_work(handoff)

        # ONE call does both halves: its first round verifies and merges the
        # batch the handoff named, and its second finds A3 newly ready and
        # hands THAT over, so the run stops at exit 3 again with a new block
        # rather than making somebody type --continue twice for one step.
        second = sh([sys.executable, BROTHER_RUN, "--continue",
                     "--cwd", self.repo, "--runs-root", self.tmp],
                    env=self.env)
        out = second.stdout + second.stderr
        self.assertEqual(second.returncode, _br.EXIT_UNITS_ARE_YOURS, out)
        self.assertIn("round 1 done, 2 of 3 piece(s) finished", out, out)
        handoff = self.handoff()
        self.assertEqual(sorted(handoff["units"]), ["A3"], handoff)
        self.do_the_work(handoff)

        third = sh([sys.executable, BROTHER_RUN, "--continue",
                    "--cwd", self.repo, "--runs-root", self.tmp], env=self.env)
        out = third.stdout + third.stderr
        self.assertEqual(third.returncode, 0, out)
        # The report lists what the RUN integrated, not what this call did,
        # so all three are named once the last one lands.
        self.assertIn("integrated (3):", out, out)
        self.assertEqual(self.spawned(), [], out)
        for name in ("one.txt", "two.txt", "three.txt"):
            self.assertTrue(os.path.exists(os.path.join(self.repo, name)), out)

class TheHeadlessWorkerIsUnchanged(SessionWorkerBase):

    def test_outside_a_session_the_worker_is_still_spawned(self):
        """The regression guard. With no session marker the engine takes the
        documented headless path: model_worker.py resolves the host's own
        client and spawns it, which the PATH shim records. That the shim then
        fails the unit is beside the point; the measurement is that the spawn
        happened at all."""
        env = dict(self.env)
        for var in _br.brother_paths.CLAUDE_MARKER_VARS:
            env.pop(var, None)
        for var in _br.brother_paths.CODEX_MARKER_VARS:
            env.pop(var, None)
        env["BROTHER_CLIENT"] = _br.brother_paths.CLAUDE
        plan = write_plan(self.tmp, two_units())
        proc = sh([sys.executable, BROTHER_RUN, "two files exist",
                   "--cwd", self.repo, "--runs-root", self.tmp,
                   "--plan", plan], env=env)
        out = proc.stdout + proc.stderr
        self.assertNotEqual(proc.returncode, _br.EXIT_UNITS_ARE_YOURS, out)
        self.assertTrue(self.spawned(),
                        "the headless worker spawned nothing: %s" % out)

    def test_a_named_worker_command_in_a_session_still_opts_back_in(self):
        """The opt-in half: a session that names its own worker is taken at
        its word, exactly as DOOR_MODEL_CMD is on the decomposer side."""
        env = dict(self.env)
        env["MODEL_WORKER_CMD"] = "%s %s" % (sys.executable, self.model)
        plan = write_plan(self.tmp, two_units())
        proc = sh([sys.executable, BROTHER_RUN, "two files exist",
                   "--cwd", self.repo, "--runs-root", self.tmp,
                   "--plan", plan, "--contract", self.contract], env=env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("integrated (2):", out, out)
        self.assertEqual(self.spawned(), [], out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
