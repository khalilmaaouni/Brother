#!/usr/bin/env python3
"""BASE1's FROZEN harness: the brother arm and one competitor arm run bounded with a usage record, an app task and an
analytics task with hidden tests join the harness, the pilot record refuses an unrun arm, and priority.py ranks against
the owner's complaints. Written by the session before the code; never edited by its builder (HARN1).

    python3 -B scripts/test_benchmark_arms.py Arms      BASE1.a
    python3 -B scripts/test_benchmark_arms.py Tasks     BASE1.b
    python3 -B scripts/test_benchmark_arms.py Pilot     BASE1.c
    python3 -B scripts/test_benchmark_arms.py Ranked    BASE1.d

RED BY ASSERTION, hermetic. Python 3.9, standard library."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
HARNESS = os.path.join(HERE, "benchmark_harness.py")
ARMS = os.path.join(HERE, "benchmark_arms.py")
PILOT = os.path.join(ROOT, "docs", "plan", "BENCHMARK-PILOT-1.1.9.json")


def need(case, mod, name, what):
    case.assertTrue(hasattr(mod, name), "%s defines no %s" % (what, name))
    return getattr(mod, name)


class Arms(unittest.TestCase):
    """BASE1.a"""

    def setUp(self):
        self.assertTrue(os.path.isfile(ARMS), "scripts/benchmark_arms.py does not exist yet")

    def test_the_brother_arm_is_runnable(self):
        """REQ-BASE1A-01."""
        p = subprocess.run([sys.executable, "-B", HARNESS, "--list-arms"], capture_output=True, text=True, timeout=120, cwd=ROOT)
        out = p.stdout + p.stderr
        self.assertRegex(out, r"brother\s+.*runnable", "the brother arm is not listed runnable: %s" % out[-400:])
        self.assertEqual(1, len([l for l in out.splitlines() if "competitor" in l.lower() and "runnable" in l]),
                         "no single competitor arm is listed runnable: %s" % out[-400:])
        import benchmark_arms as A
        arm_command = need(self, A, "arm_command", "benchmark_arms")
        ws = tempfile.mkdtemp(prefix="arm-ws-")
        cmd = arm_command("brother", ws, 200000, 600)
        self.assertIsInstance(cmd, list)
        self.assertTrue(any("200000" in str(a) for a in cmd), "the token ceiling is not in the command: %r" % (cmd,))
        with self.assertRaises(Exception):
            arm_command("teleport", ws, 1, 1)

    def test_a_ceiling_is_enforced(self):
        """REQ-BASE1A-02. An arm that runs past its seconds ceiling is killed and read FAIL with the ceiling named."""
        import benchmark_harness as H
        run_task = need(self, H, "run_task", "benchmark_harness")
        tasks = need(self, H, "TASKS", "benchmark_harness")
        task = tasks[0] if isinstance(tasks, (list, tuple)) else list(tasks.values())[0]
        got = run_task(task, [sys.executable, "-c", "import time; time.sleep(30)"], timeout=2, label="sleeper")
        verdict = got.get("verdict") if isinstance(got, dict) else getattr(got, "verdict", None)
        self.assertEqual("FAIL", verdict, got)
        self.assertIn("ceiling", json.dumps(got, default=str).lower())


class Tasks(unittest.TestCase):
    """BASE1.b"""

    def test_the_two_tasks_exist_with_hidden_tests(self):
        """REQ-BASE1B-01."""
        p = subprocess.run([sys.executable, "-B", HARNESS, "--list-tasks"], capture_output=True, text=True, timeout=120, cwd=ROOT)
        out = p.stdout + p.stderr
        for task in ("app-incremental-change", "analytics-duplicate-keys"):
            self.assertIn(task, out, "task %s is not listed: %s" % (task, out[-400:]))
        import benchmark_harness as H
        tasks = need(self, H, "TASKS", "benchmark_harness")
        by_id = {t["id"]: t for t in (tasks if isinstance(tasks, (list, tuple)) else tasks.values())}
        for task in ("app-incremental-change", "analytics-duplicate-keys"):
            self.assertTrue(by_id[task].get("hidden_test"), "%s has no hidden test" % task)
            self.assertTrue(by_id[task].get("visible_test"), "%s has no visible test" % task)

    def test_hidden_tests_decide(self):
        """REQ-BASE1B-02."""
        import benchmark_harness as H
        run_task = need(self, H, "run_task", "benchmark_harness")
        tasks = need(self, H, "TASKS", "benchmark_harness")
        by_id = {t["id"]: t for t in (tasks if isinstance(tasks, (list, tuple)) else tasks.values())}
        task = by_id["analytics-duplicate-keys"]
        cheat = "import os; open(os.path.join(os.getcwd(), %r), 'w').write('def answer():\\n    return 0\\n')" % task["solution_file"]
        got = run_task(task, [sys.executable, "-c", cheat], timeout=60, label="cheat")
        verdict = got.get("verdict") if isinstance(got, dict) else getattr(got, "verdict", None)
        self.assertEqual("FAIL", verdict, "a solution that fails the hidden test passed: %r" % (got,))


class Pilot(unittest.TestCase):
    """BASE1.c"""

    def runs(self, arm_cmd="x", cost=1.0):
        out = []
        for task in ("app-incremental-change", "analytics-duplicate-keys"):
            for arm in ("brother-baseline", "brother-candidate", "competitor"):
                out.append({"task": task, "arm": arm, "command": arm_cmd, "verdict": "PASS", "cost_usd": cost,
                            "interventions": 0, "wall_s": 10.0})
        return out

    def test_the_record_carries_two_tasks_and_three_arms(self):
        """REQ-BASE1C-01."""
        import benchmark_arms as A
        pilot_record = need(self, A, "pilot_record", "benchmark_arms")
        rec = pilot_record(self.runs())
        self.assertEqual({"app-incremental-change", "analytics-duplicate-keys"}, set(rec.get("tasks", [])))
        self.assertEqual({"brother-baseline", "brother-candidate", "competitor"}, set(rec.get("arms", [])))
        self.assertIn("accepted_delivery_rate", rec)
        self.assertTrue(os.path.isfile(PILOT), "docs/plan/BENCHMARK-PILOT-1.1.9.json does not exist yet")

    def test_the_pilot_record_refuses_an_unrun_arm(self):
        """REQ-BASE1C-02."""
        import benchmark_arms as A
        pilot_record = need(self, A, "pilot_record", "benchmark_arms")
        with self.assertRaises(Exception):
            pilot_record(self.runs(arm_cmd=None))
        with self.assertRaises(Exception):
            pilot_record(self.runs(cost=None))


class Ranked(unittest.TestCase):
    """BASE1.d"""

    def test_a_string_closes_complaint_matches(self):
        """REQ-BASE1D-01."""
        import priority as P
        rank = need(self, P, "rank", "priority")
        roadmap = {"rows": [{"id": "N1", "status": "OPEN", "closes_complaint": "C-DRIFT"},
                            {"id": "N2", "status": "OPEN", "closes_complaint": ["C-AUTH"]}], "features": []}
        got = rank(roadmap, [{"id": "C-DRIFT", "words": "objective drift", "weight": 5}])
        self.assertEqual(["N1"], [r.get("id") for r in got if r.get("score", 0) > 0])

    def test_the_owners_complaints_drive_the_ranking(self):
        """REQ-BASE1D-02."""
        import priority as P
        rank = need(self, P, "rank", "priority")
        roadmap = {"rows": [{"id": "N1", "status": "OPEN", "closes_complaint": ["C-DRIFT"]},
                            {"id": "N2", "status": "OPEN", "closes_complaint": ["C-AUTH"]}], "features": []}
        got = rank(roadmap, [{"id": "C-AUTH", "words": "fragile auth", "weight": 5}, {"id": "C-DRIFT", "words": "drift", "weight": 1}])
        self.assertEqual("N2", got[0].get("id"))
        self.assertEqual([], rank(roadmap, []))


if __name__ == "__main__":
    unittest.main()
