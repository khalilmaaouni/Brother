import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import benchmark_harness as bh  # noqa: E402

ADD_TASK = bh.task_by_id("add-two-ints")

# Fixture arms: real subprocesses (python3 -c ...), never a simulated
# result -- these exist to prove the harness's own mechanics honestly, not
# to stand in for a live model call (see the module docstring's NOT BUILT
# section for why no live model arm is wired into the test suite).
CORRECT_ADD_ARM = [
    sys.executable, "-c",
    "open('solution.py', 'w').write('def add(a, b):\\n    return a + b\\n')",
]

# Hardcodes the exact visible-test case (add(2, 3) == 5) and nothing else:
# passes the visible test, fails every held-out case.
GAMING_ARM = [
    sys.executable, "-c",
    "open('solution.py', 'w').write('def add(a, b):\\n    return 5\\n')",
]

USAGE_REPORTING_ARM = [
    sys.executable, "-c",
    "import json\n"
    "open('solution.py', 'w').write('def add(a, b):\\n    return a + b\\n')\n"
    "json.dump({'prompt_tokens': 10, 'completion_tokens': 20}, open('usage.json', 'w'))\n",
]

SLEEPY_ARM = [sys.executable, "-c", "import time; time.sleep(5)"]

# C1 fixture: Opus's own adversarial probe. solution.py has a WRONG add()
# implementation, plus a top-level os._exit(0) -- executed by hidden_test.py's
# `import solution` line, before any hidden assertion runs. Built via %r so
# the multi-line content needs no manual escaping (its \n's are handled by
# repr(), not hand-doubled backslashes).
MALICIOUS_EXIT_SOLUTION = (
    "import os\n"
    "def add(a, b):\n"
    "    return 999999\n"
    "os._exit(0)\n"
)
MALICIOUS_EXIT_ARM = [
    sys.executable, "-c",
    "open('solution.py', 'w').write(%r)" % MALICIOUS_EXIT_SOLUTION,
]

# Deletes the starter file outright, so no solution_files entry exists at
# all after the arm exits (a no-op arm that leaves the NotImplementedError
# stub in place is a different, already-covered case: the hidden test runs
# against the stub and fails normally).
NO_SOLUTION_ARM = [sys.executable, "-c", "import os; os.remove('solution.py')"]

# WBS-90.02 family B/C fixtures, same real-subprocess-not-simulated
# discipline as the family A fixtures above.
JOURNEY_TASK = bh.task_by_id("journey-step-progress")
JOURNEY_CORRECT_SOLUTION = (
    "def step_status(depth, evidence_count):\n"
    "    unlocked = evidence_count >= depth + 1\n"
    "    if evidence_count == 0:\n"
    "        label = 'locked'\n"
    "    elif unlocked:\n"
    "        label = 'unlocked'\n"
    "    else:\n"
    "        label = 'partial'\n"
    "    return {'depth': depth, 'unlocked': unlocked, 'label': label}\n"
)
JOURNEY_CORRECT_ARM = [
    sys.executable, "-c", "open('solution.py', 'w').write(%r)" % JOURNEY_CORRECT_SOLUTION,
]
# Hardcodes the exact visible-test case (depth=1, evidence_count=2) and
# nothing else: passes the visible test, fails every held-out case.
JOURNEY_GAMING_SOLUTION = (
    "def step_status(depth, evidence_count):\n"
    "    return {'depth': 1, 'unlocked': True, 'label': 'unlocked'}\n"
)
JOURNEY_GAMING_ARM = [
    sys.executable, "-c", "open('solution.py', 'w').write(%r)" % JOURNEY_GAMING_SOLUTION,
]

GOLDEN_MASTER_TASK = bh.task_by_id("golden-master-protected-diff")
GOLDEN_MASTER_CORRECT_SOLUTION = (
    "def classify_change(before, after, protected_fields):\n"
    "    if before is None:\n"
    "        return 'NO-DATA'\n"
    "    for f in protected_fields:\n"
    "        if before.get(f) != after.get(f):\n"
    "            return 'FAIL'\n"
    "    return 'PASS'\n"
)
GOLDEN_MASTER_CORRECT_ARM = [
    sys.executable, "-c", "open('solution.py', 'w').write(%r)" % GOLDEN_MASTER_CORRECT_SOLUTION,
]
# Passes the visible test's two exact cases (before is None -> NO-DATA,
# unchanged before/after -> PASS) while ignoring protected_fields entirely
# -- so it never catches a protected field that actually changed, which is
# the whole point of the "consequence handling" hidden cases.
GOLDEN_MASTER_GAMING_SOLUTION = (
    "def classify_change(before, after, protected_fields):\n"
    "    return 'NO-DATA' if before is None else 'PASS'\n"
)
GOLDEN_MASTER_GAMING_ARM = [
    sys.executable, "-c", "open('solution.py', 'w').write(%r)" % GOLDEN_MASTER_GAMING_SOLUTION,
]


class BenchmarkHarnessTests(unittest.TestCase):
    def test_correct_arm_passes_visible_and_hidden(self):
        result = bh.run_task(ADD_TASK, "correct", CORRECT_ADD_ARM)
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(bh.exit_code_for_verdict(result["verdict"]), 0)
        self.assertTrue(result["visible_pass"])
        self.assertTrue(result["hidden_pass"])
        self.assertEqual(result["exit_code"], 0)

    def test_gaming_arm_passes_visible_fails_hidden(self):
        """The exact scenario this harness exists to catch: a solution
        that satisfies the one case the arm could see, and nothing else."""
        result = bh.run_task(ADD_TASK, "gaming", GAMING_ARM)
        self.assertTrue(result["visible_pass"], "gaming arm should pass the visible test it hardcoded")
        self.assertFalse(result["hidden_pass"], "gaming arm must fail the held-out test it never saw")
        self.assertEqual(result["verdict"], "FAIL")
        self.assertEqual(bh.exit_code_for_verdict(result["verdict"]), 1)

    def test_c1_os_exit_at_import_time_cannot_forge_a_pass(self):
        """Opus's own adversarial probe (C1, CRITICAL): a solution.py with
        a WRONG add() implementation that calls os._exit(0) at module
        scope. Before this fix, hidden_test.py's `import solution` line
        ran that exit call before a single hidden assertion executed, so
        the process's own exit code came back 0 and the harness read
        exit-code-0 as PASS regardless of what the hidden test would have
        found (proven: this exact arm scored hidden_pass=true pre-fix).
        It must now score FAIL, not PASS."""
        result = bh.run_task(ADD_TASK, "malicious-exit", MALICIOUS_EXIT_ARM)
        self.assertEqual(
            result["verdict"],
            "FAIL",
            "os._exit(0) at import time forged a PASS verdict: %r" % (result,),
        )
        self.assertFalse(result["hidden_pass"])
        self.assertEqual(bh.exit_code_for_verdict(result["verdict"]), 1)

    def test_no_arm_command_is_honest_no_data(self):
        result = bh.run_task(ADD_TASK, "none", None)
        self.assertEqual(result["verdict"], "NO-DATA")
        self.assertIsNone(result["hidden_pass"])
        self.assertIsNotNone(result["launch_error"])
        self.assertEqual(bh.exit_code_for_verdict(result["verdict"]), 2)

    def test_unlaunchable_arm_is_no_data_not_a_crash(self):
        result = bh.run_task(ADD_TASK, "missing-binary", ["/no/such/binary/for/benchmark-harness-test"])
        self.assertEqual(result["verdict"], "NO-DATA")
        self.assertIn("failed to launch", result["launch_error"])

    def test_timeout_is_a_fail_not_a_silent_pass(self):
        result = bh.run_task(ADD_TASK, "sleepy", SLEEPY_ARM, timeout=1)
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["verdict"], "FAIL")

    def test_no_solution_file_at_all_is_fail_not_no_data(self):
        """The arm ran (exit 0) but left no solution_files entry on disk at
        all: the hidden test cannot even be mounted. That is a real,
        measured failure of the arm's output, not an unmeasured metric --
        scoring it NO-DATA would let a no-op arm dodge being counted
        against it."""
        result = bh.run_task(ADD_TASK, "empty", NO_SOLUTION_ARM)
        self.assertEqual(result["exit_code"], 0)
        self.assertIsNone(result["hidden_pass"])
        self.assertEqual(result["verdict"], "FAIL")
        self.assertEqual(result["verdict_reason"], "arm produced no solution file to evaluate")
        self.assertEqual(bh.exit_code_for_verdict(result["verdict"]), 1)

    def test_untouched_starter_stub_fails_hidden_test(self):
        """A no-op arm that leaves the NotImplementedError starter stub in
        place still has a solution file on disk, so this is a distinct
        code path from the no-file case above: the hidden test runs
        against the stub and fails for real, on the merits."""
        noop_arm = [sys.executable, "-c", "pass"]
        result = bh.run_task(ADD_TASK, "noop", noop_arm)
        self.assertFalse(result["hidden_pass"])
        self.assertEqual(result["verdict"], "FAIL")

    def test_usage_reported_when_arm_writes_it(self):
        """M2: the arm's self-reported usage is wrapped with a provenance
        marker so a caller cannot mistake it for a measured observation."""
        result = bh.run_task(ADD_TASK, "usage", USAGE_REPORTING_ARM)
        self.assertEqual(
            result["usage"],
            {
                "self_reported": {"prompt_tokens": 10, "completion_tokens": 20},
                "source": "arm-written usage.json",
            },
        )

    def test_usage_is_no_data_string_when_arm_writes_nothing(self):
        """Never fabricate token/cost as 0 -- absence is reported as the
        NO-DATA vocabulary, not a number."""
        result = bh.run_task(ADD_TASK, "correct", CORRECT_ADD_ARM)
        self.assertIsInstance(result["usage"], str)
        self.assertTrue(result["usage"].startswith("NO-DATA"))

    def test_second_fixed_task_also_scores_honestly(self):
        dedupe_task = bh.task_by_id("dedupe-preserve-order")
        correct_arm = [
            sys.executable, "-c",
            "open('solution.py', 'w').write("
            "'def dedupe(items):\\n"
            "    seen = set()\\n"
            "    out = []\\n"
            "    for x in items:\\n"
            "        if x not in seen:\\n"
            "            seen.add(x)\\n"
            "            out.append(x)\\n"
            "    return out\\n'"
            ")",
        ]
        result = bh.run_task(dedupe_task, "correct", correct_arm)
        self.assertEqual(result["verdict"], "PASS")

    def test_unknown_task_id_cli_is_no_data(self):
        exit_code = bh.main(["--task", "does-not-exist"])
        self.assertEqual(exit_code, 2)

    def test_list_tasks_cli_exits_zero(self):
        exit_code = bh.main(["--list-tasks"])
        self.assertEqual(exit_code, 0)

    def test_list_families_cli_exits_zero(self):
        exit_code = bh.main(["--list-families"])
        self.assertEqual(exit_code, 0)

    def test_list_arms_cli_exits_zero(self):
        exit_code = bh.main(["--list-arms"])
        self.assertEqual(exit_code, 0)

    def test_every_task_declares_a_known_family(self):
        for task in bh.TASKS:
            self.assertIn("family", task, task["id"])
            self.assertIn(task["family"], bh.FAMILIES, task["id"])

    def test_all_three_families_have_at_least_one_task(self):
        families_present = {task["family"] for task in bh.TASKS}
        self.assertEqual(families_present, set(bh.FAMILIES))

    def test_family_b_uses_role_only_naming(self):
        """Founder order: the app's real name never appears in this
        repo's own artifacts, family B included -- role phrasing only.
        Asserts the POSITIVE (the approved role phrase is present),
        never a negative check against the forbidden name itself: that
        would write the very string this rule exists to keep out."""
        family_b_task = bh.task_by_id("journey-step-progress")
        self.assertEqual(family_b_task["family"], "B")
        self.assertIn("native mobile reference journey", family_b_task["prompt"])
        self.assertIn("native mobile reference journey", bh.FAMILIES["B"])

    def test_arms_registry_names_the_four_roadmap_arms_all_no_data(self):
        """WBS-90.01: investigated live tonight (see module docstring) --
        none of the 4 named arms is safely wireable in this environment
        without spawning a live nested agent session, so every one is
        honest NO-DATA with a concrete, non-empty reason, never a bare
        gap."""
        self.assertEqual(set(bh.ARMS), {"brother", "gsd", "superpowers", "bmad-loop"})
        for name, entry in bh.ARMS.items():
            self.assertIsNone(entry["command"], name)
            self.assertTrue(entry["reason"].strip(), name)

    def test_arm_command_override_always_wins_over_the_registry(self):
        self.assertEqual(
            bh.resolve_arm_command("gsd", ["echo", "hi"]),
            ["echo", "hi"],
        )

    def test_known_arm_name_without_override_resolves_to_registry_none(self):
        self.assertIsNone(bh.resolve_arm_command("gsd", None))

    def test_unregistered_arm_name_without_override_is_also_none(self):
        self.assertIsNone(bh.resolve_arm_command("some-ad-hoc-label", None))

    def test_family_b_journey_task_correct_solution_passes(self):
        result = bh.run_task(JOURNEY_TASK, "correct", JOURNEY_CORRECT_ARM)
        self.assertEqual(result["verdict"], "PASS", result)
        self.assertTrue(result["visible_pass"])
        self.assertTrue(result["hidden_pass"])

    def test_family_b_journey_task_gaming_solution_fails_hidden(self):
        result = bh.run_task(JOURNEY_TASK, "gaming", JOURNEY_GAMING_ARM)
        self.assertTrue(result["visible_pass"], "gaming arm should pass the visible test it hardcoded")
        self.assertFalse(result["hidden_pass"], "gaming arm must fail the held-out cases it never saw")
        self.assertEqual(result["verdict"], "FAIL")

    def test_family_c_golden_master_task_correct_solution_passes(self):
        result = bh.run_task(GOLDEN_MASTER_TASK, "correct", GOLDEN_MASTER_CORRECT_ARM)
        self.assertEqual(result["verdict"], "PASS", result)
        self.assertTrue(result["visible_pass"])
        self.assertTrue(result["hidden_pass"])

    def test_family_c_golden_master_task_gaming_solution_fails_hidden(self):
        """The exact consequence-handling failure family C exists to
        catch: a solution that never checks protected_fields still
        passes the two visible cases, but must fail on a real protected
        field change it never saw."""
        result = bh.run_task(GOLDEN_MASTER_TASK, "gaming", GOLDEN_MASTER_GAMING_ARM)
        self.assertTrue(result["visible_pass"])
        self.assertFalse(result["hidden_pass"])
        self.assertEqual(result["verdict"], "FAIL")


class BenchmarkHarnessProcessGroupTests(unittest.TestCase):
    """H1 regression: a plain backgrounded process the arm spawns (no
    setsid of its own -- exactly Opus's "backgrounds a watcher process"
    scenario, not a double-detach that would need full sandboxing to
    catch) must not survive run_arm() returning. Opus proved a watcher
    left alive past the arm's own exit can win a race against eval_dir;
    the fix kills the arm's whole process group in run_arm's own finally,
    before run_task ever creates eval_dir.

    Deliberately does NOT test this by racing a real watcher against
    eval_dir creation and timing who wins: on a fast local machine, even
    the pre-fix harness usually wins that race (the copy+hidden-test step
    is a handful of local file operations; a grandchild python process
    starting cold rarely beats it), so a race-based version of this test
    passed against the pre-fix code before this was rewritten -- it would
    have been a tautology, proving nothing.

    Also deliberately does NOT check liveness with `os.kill(pid, 0)`: a
    first version of this test did, and on this machine (heavy concurrent
    process churn across many worktree sessions) it produced a FALSE
    ALIVE reading -- os.getpgid() on the very same pid reported ESRCH
    (no such process) in the same run, meaning the grandchild's original
    pid had already been reaped and REUSED by an unrelated process by the
    time the check ran. A pid is not a stable identity once a process has
    exited. Instead this checks BEHAVIOR: a live process keeps writing a
    heartbeat file every 50ms; a dead one does not, regardless of what a
    pid number gets reused for afterward. The spawner blocks on the
    grandchild's first heartbeat write before it (the spawner) exits, so
    the file is guaranteed to exist with the fix in place, and there is
    no dependency on the grandchild reaching any particular line before
    SIGKILL can land on it."""

    def setUp(self):
        self.workspace_dir = tempfile.mkdtemp(prefix="bh-test-pg-ws-")
        self.heartbeat_path = os.path.join(self.workspace_dir, "heartbeat.txt")
        self.heartbeat_script = os.path.join(
            tempfile.gettempdir(), "bh_test_heartbeat_%d.py" % os.getpid()
        )
        with open(self.heartbeat_script, "w", encoding="utf-8") as fh:
            fh.write(
                "import sys, time\n"
                "path = sys.argv[1]\n"
                "i = 0\n"
                "while True:\n"
                "    i += 1\n"
                "    with open(path, 'w') as out:\n"
                "        out.write(str(i))\n"
                "    time.sleep(0.05)\n"
            )

    def tearDown(self):
        shutil.rmtree(self.workspace_dir, ignore_errors=True)
        try:
            os.remove(self.heartbeat_script)
        except OSError:
            pass

    def test_run_arm_kills_the_whole_process_group_not_just_the_direct_child(self):
        # The spawner (the arm's own direct process) launches the
        # heartbeat grandchild as a plain child (no setsid of its own),
        # then blocks -- itself, before exiting -- until that grandchild's
        # first heartbeat write lands. That guarantees the heartbeat file
        # exists with at least one write by the time run_arm's kill runs.
        spawner_src = (
            "import os, subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, %r, %r])\n"
            "deadline = time.time() + 5\n"
            "while not os.path.isfile(%r) and time.time() < deadline:\n"
            "    time.sleep(0.01)\n"
        ) % (self.heartbeat_script, self.heartbeat_path, self.heartbeat_path)
        arm = [sys.executable, "-c", spawner_src]

        arm_result = bh.run_arm(arm, self.workspace_dir, timeout=10)

        self.assertIsNone(arm_result.get("launch_error"))
        self.assertFalse(arm_result.get("timed_out"))
        self.assertTrue(
            os.path.isfile(self.heartbeat_path),
            "the grandchild's heartbeat file never appeared -- broken "
            "test fixture, not evidence about the fix",
        )

        with open(self.heartbeat_path, "r", encoding="utf-8") as fh:
            before = fh.read()
        time.sleep(0.5)
        with open(self.heartbeat_path, "r", encoding="utf-8") as fh:
            after = fh.read()
        self.assertEqual(
            before,
            after,
            "the grandchild kept writing heartbeats after run_arm() "
            "returned -- the process-group kill did not reach it",
        )


if __name__ == "__main__":
    unittest.main()
