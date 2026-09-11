"""D-001: the plan comes from the session, and no headless model call is nested.

THE DEFECT THIS SUITE PINS (persona dogfood 2026-09-07, founder persona A1
and lead engineer A3, scenarios A1-S1, A1-S4 and A3-S1, the largest block of
the 28 failing scenarios). Asked to build something from inside a Claude Code
session, the door shelled out to a headless model command for decomposition.
Inside a session that nested command hangs or fails with empty stderr, and
door.py dutifully retried it three times. The user's own words: "it tried the
same broken internal call three times and gave up, no worktree, no test run,
nothing".

THE ROUTE THAT REPLACES IT, adopted in docs/plan/PLAN-THREE-ENGINES-2026-09-08.md
step 2 and docs/plan/DECISION-APPROACH-90PCT-2026-09-08.md: the session's own
model writes the units, brother_run validates them through door.py's single
validation path and runs everything else exactly as before. The nested command
stays as an opt-in for headless use (--model-cmd on door.py, DOOR_MODEL_CMD
for the engine), so nothing regresses for the documented headless path.

No network and no real model anywhere in here: the worker is the same stub
seam scripts/test_brother_run.py uses, and the decomposer is not called at
all, which is the entire point of tests one and two.
"""
import json
import os
import shlex
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
BROTHER_RUN = os.path.join(HERE, "brother_run.py")
import brother_paths  # noqa: E402
import brother_run as _br  # noqa: E402
import door  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
try:
    import tmp_sandbox as _tmp
    _tmp.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n"
                     % os.path.basename(__file__))


def sh(args, cwd=None, env=None):
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=600)


def write_stub(tmpdir, name, body):
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("#!/usr/bin/env python3\n" + textwrap.dedent(body))
    os.chmod(path, 0o755)
    return path


def make_repo(tmp):
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    for args in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "a@b.c"],
                 ["config", "user.name", "t"]):
        sh(["git"] + args, cwd=repo)
    with open(os.path.join(repo, "base.txt"), "w", encoding="utf-8") as fh:
        fh.write("base\n")
    sh(["git", "add", "-A"], cwd=repo)
    sh(["git", "commit", "-q", "-m", "R0"], cwd=repo)
    return repo


#: The same writer stub scripts/test_brother_run.py uses: it reads the whole
#: prompt off argv, finds the files it was told to write, and writes them.
WRITER_MODEL = """
    import re, sys
    prompt = sys.argv[-1] if len(sys.argv) > 1 else ""
    m = re.search(r"Declared write scope: ([^\\n]+)", prompt)
    for path in (p.strip() for p in (m.group(1).split(",") if m else [])):
        if path:
            with open(path, "w") as fh:
                fh.write("written by the stub model\\n")
    print("stub model wrote: %s" % (m.group(1) if m else "(nothing declared)"))
"""

#: The same writer, plus a lane census. Each worker marks itself live in a
#: shared directory, holds the mark long enough for its peers to be seen, and
#: records how many marks existed while it held one. The peak of that column
#: is the number of lanes that actually ran together.
LANE_CENSUS_MODEL = """
    import os, re, sys, time
    probe = os.environ["BROTHER_TEST_LANE_PROBE"]
    live = os.path.join(probe, "live")
    os.makedirs(live, exist_ok=True)
    mark = os.path.join(live, str(os.getpid()))
    open(mark, "w").close()
    time.sleep(1.0)
    seen = len([n for n in os.listdir(live)])
    with open(os.path.join(probe, "census.log"), "a") as fh:
        fh.write("%d\\n" % seen)
    prompt = sys.argv[-1] if len(sys.argv) > 1 else ""
    m = re.search(r"Declared write scope: ([^\\n]+)", prompt)
    for path in (p.strip() for p in (m.group(1).split(",") if m else [])):
        if path:
            with open(path, "w") as fh:
                fh.write("written by the stub model\\n")
    print("stub model wrote: %s" % (m.group(1) if m else "(nothing declared)"))
    os.remove(mark)
"""

#: A stand-in for the headless client the default path spawns. It writes its
#: own argv where a test can find it, so "nothing nested a model call" is
#: measured rather than asserted from the absence of an error.
TATTLING_CLIENT = """
    import os, sys
    with open(os.environ["BROTHER_TEST_SPAWN_LOG"], "a") as fh:
        fh.write(" ".join(sys.argv) + "\\n")
    sys.exit(1)
"""

#: The same stand-in, but a working one: it prints a valid unit list, so the
#: documented headless path can be driven end to end without a real model.
ANSWERING_CLIENT = """
    import json, os, sys
    sys.stdin.read()
    with open(os.environ["BROTHER_TEST_SPAWN_LOG"], "a") as fh:
        fh.write(" ".join(sys.argv) + "\\n")
    print(json.dumps([{"id": "H1", "objective": "create the headless file",
                       "done_check": "test -f headless.txt",
                       "writes": ["headless.txt"], "deps": []}]))
"""


def two_units():
    return [
        {"id": "A1", "objective": "create file one",
         "done_check": "test -f one.txt", "writes": ["one.txt"], "deps": []},
        {"id": "A2", "objective": "create file two",
         "done_check": "test -f two.txt", "writes": ["two.txt"], "deps": []},
    ]


def write_plan(tmp, units, name="plan.json"):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(units, fh)
    return path


#: The complete outcome contract fixture U4 ships, the one record every
#: reader of the schema is tested against.
CONTRACT_FIXTURE = os.path.join(HERE, "fixtures", "outcome-contract",
                                "complete.json")


def write_contract(tmp, question, commands=(), name="contract.json"):
    """A copy of the complete fixture asking `question` and promising
    `commands` as its success checks, written outside the target repository.
    U6 (A-prime amendment 2): the --plan route inside a coding session needs
    one of these, and every command it promises must be run by some unit of
    the plan, so the checks are named by the caller alongside the units."""
    with open(CONTRACT_FIXTURE, encoding="utf-8") as fh:
        record = json.load(fh)
    record["question"] = question
    record["success_checks"] = [
        {"id": "sc-%d" % (i + 1), "command": command, "expect": "exit-0"}
        for i, command in enumerate(commands)]
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=1)
    return path


def run_dirs(runs_root):
    """The run directories this runs root holds, if any. _resolve_runs_root
    creates docs/plan/runs by probing it, so the directory existing is not
    evidence that a run started; a child of it is."""
    runs = os.path.join(runs_root, "docs", "plan", "runs")
    return sorted(os.listdir(runs)) if os.path.isdir(runs) else []


class PlanFileRunBase(unittest.TestCase):
    """One repository, one worker stub, and a PATH whose first entry shadows
    every headless client this engine could spawn."""

    MODEL_BODY = WRITER_MODEL

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brother-run-plan-")
        self.repo = make_repo(self.tmp)
        self.model = write_stub(self.tmp, "writer_model.py", self.MODEL_BODY)
        self.spawn_log = os.path.join(self.tmp, "spawned-argv.log")

        # THE SHADOW. Every name the default decomposer could resolve to gets
        # a stub of its own, ahead of the real one on PATH, so a nested call
        # cannot quietly succeed against the machine's real client and cannot
        # quietly fail against its absence either: it leaves a line in
        # spawn_log whichever way it goes.
        self.shim_dir = os.path.join(self.tmp, "shims")
        os.makedirs(self.shim_dir)
        self.shadowed = sorted({door.DEFAULT_MODEL_CMD[0],
                                door.default_model_cmd()[0], "claude", "codex"})
        for name in self.shadowed:
            write_stub(self.shim_dir, name, TATTLING_CLIENT)

        self.env = dict(os.environ)
        self.env.pop("DOOR_MODEL_CMD", None)
        # A NEUTRAL BASE, added with U6 (the outcome contract gate). Two of
        # this engine's routing rules read the session marker variables, and
        # this process inherits them whenever the suite is itself run from
        # inside a coding session. Left inherited, the same test is green in
        # a plain terminal and red in a session, which is a property of the
        # developer's shell rather than of the engine. Every test that wants
        # a session sets its own marker, one line, visibly.
        for var in (tuple(brother_paths.CLAUDE_MARKER_VARS)
                    + tuple(brother_paths.CODEX_MARKER_VARS)):
            self.env.pop(var, None)
        self.env["MODEL_WORKER_CMD"] = "%s %s" % (shlex.quote(sys.executable), shlex.quote(self.model))
        self.env["BROTHER_TEST_SPAWN_LOG"] = self.spawn_log
        self.env["PATH"] = self.shim_dir + os.pathsep + self.env.get("PATH", "")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def spawned(self):
        if not os.path.isfile(self.spawn_log):
            return []
        with open(self.spawn_log, encoding="utf-8") as fh:
            return [ln for ln in fh.read().splitlines() if ln.strip()]


class ThePlanFromTheSessionReachesTheWorkerWithNoNestedModelCall(PlanFileRunBase):

    def test_a_two_unit_plan_file_integrates_and_spawns_no_model_command(self):
        """D-001's own done-check. The units come from a file the session
        wrote; the run must reach the workers, the integration and the receipt
        without ever spawning door.DEFAULT_MODEL_CMD[0] or the host's own
        headless client."""
        plan = write_plan(self.tmp, two_units())
        # U6, A-prime amendment 2: the --plan route inside a session carries
        # its outcome contract. The session is named here rather than
        # inherited, so this test measures the engine and not the shell the
        # suite happens to be run from.
        env = dict(self.env)
        env["CLAUDECODE"] = "1"
        proc = sh([sys.executable, BROTHER_RUN, "two files exist",
                   "--cwd", self.repo, "--runs-root", self.tmp,
                   "--plan", plan,
                   "--contract",
                   write_contract(self.tmp, "two files exist",
                                  commands=("test -f one.txt",
                                            "test -f two.txt"))],
                  env=env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        self.assertEqual(
            self.spawned(), [],
            "the run spawned a headless model command (%s) even though the "
            "plan was handed to it: %r" % ("/".join(self.shadowed),
                                           self.spawned()))
        self.assertIn("integrated (2):", out, out)
        self.assertIn("brother_run: receipt: ", out, out)
        self.assertTrue(os.path.exists(os.path.join(self.repo, "one.txt")), out)
        self.assertTrue(os.path.exists(os.path.join(self.repo, "two.txt")), out)


class InsideASessionTheEngineNamesTheRouteInsteadOfNesting(PlanFileRunBase):

    def test_no_plan_and_no_model_command_in_a_session_is_no_data_at_exit_2(self):
        """The persona failure itself: inside a session, with nobody having
        named a decomposer, the engine must refuse BEFORE the first nested
        attempt and name the route out (write the plan, rerun with --plan),
        rather than retrying a broken call three times and leaving no
        worktree, no test run and nothing."""
        env = dict(self.env)
        env["CLAUDECODE"] = "1"
        env.pop("BROTHER_CLIENT", None)
        proc = sh([sys.executable, BROTHER_RUN, "two files exist",
                   "--cwd", self.repo, "--runs-root", self.tmp], env=env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 2, out)
        self.assertIn("NO-DATA", out, out)
        self.assertIn("--plan", out, out)
        self.assertEqual(self.spawned(), [], out)
        self.assertEqual(run_dirs(self.tmp), [], "a refusal opened a run")

    def test_a_model_command_in_the_environment_still_opts_back_in(self):
        """The opt-in half of the same rule: a session that names its own
        decomposer is taken at its word, session or not."""
        decomposer = write_stub(self.tmp, "decomposer.py", """
            import json, sys
            sys.stdin.read()
            print(json.dumps([
                {"id": "A1", "objective": "create file one",
                 "done_check": "test -f one.txt", "writes": ["one.txt"],
                 "deps": []},
            ]))
        """)
        env = dict(self.env)
        env["CLAUDECODE"] = "1"
        env["DOOR_MODEL_CMD"] = "%s %s" % (shlex.quote(sys.executable), shlex.quote(decomposer))
        proc = sh([sys.executable, BROTHER_RUN, "one file exists",
                   "--cwd", self.repo, "--runs-root", self.tmp], env=env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("integrated (1):", out, out)


class OutsideASessionTheHeadlessDefaultIsUnchanged(PlanFileRunBase):

    def test_resolve_cmd_still_falls_through_to_the_hosts_own_client(self):
        """The regression guard for the documented headless path: with no
        --model-cmd and no DOOR_MODEL_CMD, door still resolves the host's own
        default, exactly as it did before D-001 was fixed."""
        saved = os.environ.pop("DOOR_MODEL_CMD", None)
        try:
            self.assertEqual(door.resolve_cmd(None), door.default_model_cmd())
        finally:
            if saved is not None:
                os.environ["DOOR_MODEL_CMD"] = saved

    def test_a_plain_terminal_run_with_no_plan_still_asks_the_decomposer(self):
        """Outside a session (no marker variable), a bare outcome behaves as
        it always has: the default client is spawned and its answer is
        scheduled. This is what makes the refusal above a ROUTING rule rather
        than the removal of a capability."""
        env = dict(self.env)
        for var in brother_paths.CLAUDE_MARKER_VARS:
            env.pop(var, None)
        for var in brother_paths.CODEX_MARKER_VARS:
            env.pop(var, None)
        env["BROTHER_CLIENT"] = brother_paths.CLAUDE
        write_stub(self.shim_dir, door.DEFAULT_MODEL_CMD[0], ANSWERING_CLIENT)
        proc = sh([sys.executable, BROTHER_RUN, "a headless file exists",
                   "--cwd", self.repo, "--runs-root", self.tmp], env=env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        self.assertTrue(self.spawned(),
                        "the headless path spawned nothing: %s" % out)
        self.assertIn("integrated (1):", out, out)


class AnInvalidPlanIsRefusedByNameAndNothingIsWritten(PlanFileRunBase):

    def test_a_dangling_dependency_is_refused_at_exit_1_store_untouched(self):
        """The plan is validated by the SAME contract door.py already drives
        (work_record.check_units), so a plan the session wrote by hand cannot
        put anything unschedulable into the store. A dangling edge drops a
        unit from the ready set forever, and the refusal says so by name."""
        units = two_units()
        units[1]["deps"] = ["A9"]
        plan = write_plan(self.tmp, units, "bad-plan.json")
        proc = sh([sys.executable, BROTHER_RUN, "two files exist",
                   "--cwd", self.repo, "--runs-root", self.tmp,
                   "--plan", plan], env=self.env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 1, out)
        self.assertIn("A2 depends on 'A9'", out, out)
        self.assertEqual(run_dirs(self.tmp), [],
                         "a refused plan left a run directory behind")
        self.assertEqual(self.spawned(), [], out)

    def test_a_plan_path_that_is_not_there_is_refused_before_anything_runs(self):
        proc = sh([sys.executable, BROTHER_RUN, "two files exist",
                   "--cwd", self.repo, "--runs-root", self.tmp,
                   "--plan", os.path.join(self.tmp, "absent.json")],
                  env=self.env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 1, out)
        self.assertIn("absent.json", out, out)
        self.assertEqual(run_dirs(self.tmp), [], out)


class AtMostThreeLanesRunTogether(PlanFileRunBase):
    """The debate judgment of 2026-09-08 caps a run at three worktrees at
    once. The cap is not new code: brother_run's --slots defaults to 3
    (scripts/brother_run.py line 3900) and graph_loop.plan refuses a fourth
    ready unit a slot ("no free slot: capacity is %d", scripts/graph_loop.py
    lines 295 to 322). This is the test that was missing, and it measures the
    lanes rather than reading the flag."""

    MODEL_BODY = LANE_CENSUS_MODEL

    def test_four_independent_units_never_hold_four_worktrees_at_once(self):
        units = [{"id": "U%d" % i, "objective": "create file %d" % i,
                  "done_check": "test -f f%d.txt" % i,
                  "writes": ["f%d.txt" % i], "deps": []}
                 for i in range(1, 5)]
        probe = os.path.join(self.tmp, "probe")
        os.makedirs(probe)
        env = dict(self.env)
        env["BROTHER_TEST_LANE_PROBE"] = probe
        plan = write_plan(self.tmp, units, "four.json")
        proc = sh([sys.executable, BROTHER_RUN, "four files exist",
                   "--cwd", self.repo, "--runs-root", self.tmp,
                   "--plan", plan], env=env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("integrated (4):", out, out)

        census_path = os.path.join(probe, "census.log")
        self.assertTrue(os.path.isfile(census_path),
                        "no lane census was written: %s" % out)
        with open(census_path, encoding="utf-8") as fh:
            census = [int(ln) for ln in fh.read().split()]
        self.assertEqual(len(census), 4, census)
        self.assertLessEqual(
            max(census), 3,
            "%d lanes held a worktree at once against a cap of 3: %r"
            % (max(census), census))
        # The cap's own arithmetic, from the engine's own narration: four
        # ready units and three finished in the first round means the fourth
        # waited for a free lane rather than opening one.
        self.assertIn("round 1 done, 3 of 4 piece(s) finished", out, out)
        # And the scheduler's own claim lines, which live in the run log
        # rather than in the person's output (loop_bridge's narration is the
        # engine talking to its maintainer): three lanes claimed in the first
        # round, never four, with the fourth unit claimed in the second.
        match = re.search(r"verbatim, is in (.+?run\.log)", out)
        self.assertIsNotNone(match, "the run log was never named: %s" % out)
        with open(match.group(1), encoding="utf-8") as fh:
            log = fh.read()
        self.assertIn("CLAIMED (3):", log)
        self.assertNotIn("CLAIMED (4)", log)


if __name__ == "__main__":
    unittest.main(verbosity=2)
