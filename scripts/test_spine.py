"""The spine, pinned: an outcome reaches merged canonical with no manual step between.

It drives the REAL command line: the real scheduler, durable claims, worktree
lanes, a real spawned worker that knows nothing about lanes, the scope audit,
and serial integration. The property under test is the one the parity effort
exists for: a unit counts only when its work is verified ON the canonical
revision everybody else will live with, and the second unit verifies on the
base the first one advanced.

Built by RUNNING the spine rather than reading it, and the first run found two
defects reading had missed: the scheduler's node shape silently dropped
done_check, turning every integration into NO-DATA, and the spawned worker
bound one cwd at construction, so workers wrote beside their lanes.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import work_record as WR  # noqa: E402


def sh(args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          timeout=300)


class TheSpine(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="spine-")
        for a in (["init", "-q", "-b", "main"],
                  ["config", "user.email", "a@b.c"],
                  ["config", "user.name", "t"]):
            sh(["git"] + a, self.repo)
        with open(os.path.join(self.repo, "base.txt"), "w", encoding="utf-8") as fh:
            fh.write("base\n")
        sh(["git", "add", "-A"], self.repo)
        sh(["git", "commit", "-q", "-m", "R0"], self.repo)

        rec, problems = WR.create("two files exist and are merged serially", [
            {"id": "S1", "done_check": "test -f one.txt", "owns": ["one.txt"]},
            {"id": "S2", "done_check": "test -f two.txt", "owns": ["two.txt"]}],
            store=tempfile.mkdtemp())
        self.assertEqual(problems, [])
        self.plan = rec["path"]
        self.claims = os.path.join(tempfile.mkdtemp(), "claims.json")

        self.worker = os.path.join(tempfile.mkdtemp(), "worker.sh")
        with open(self.worker, "w", encoding="utf-8") as fh:
            fh.write(
                '#!/bin/sh\n'
                'brief=$(cat)\n'
                'unit=$(printf \'%s\' "$brief" | python3 -c '
                '"import json,sys; print(json.load(sys.stdin).get(\'unit_id\',\'\'))" '
                '2>/dev/null)\n'
                'case "$unit" in\n'
                '  S1) echo "made by S1" > one.txt ;;\n'
                '  S2) echo "made by S2" > two.txt ;;\n'
                'esac\n'
                'git add -A && git commit -qm "work for $unit"\n')
        os.chmod(self.worker, 0o755)

    def run_spine(self, owner):
        # --slots 2, PINNED: this test's own property is that S1 and S2 (no
        # dependency between them) run TOGETHER in one batch. The real
        # scheduler derives capacity from host disk (graph_loop.py's
        # machine_capacity), which legitimately drops to 1 slot under this
        # estate's own cleanup band; a concurrency test's invariant must not
        # depend on how much disk happens to be free on whatever machine
        # runs it. Capacity POLICY itself stays owned by test_resource_gate.py.
        return sh([sys.executable, os.path.join(HERE, "loop_bridge.py"),
                   "--plan", self.plan, "--claims", self.claims,
                   "--owner", owner, "--cwd", self.repo, "--slots", "2",
                   "--worker-cmd", "sh", self.worker])

    def test_outcome_to_merged_canonical_with_no_manual_step(self):
        proc = self.run_spine("spine-test")
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)

        self.assertIn("CLAIMED (2)", out)
        self.assertIn("per-writer worktrees", out)
        self.assertEqual(out.count("INTEGRATED"), 2, out)
        self.assertEqual(out.count("integrated=True"), 2, out)

        self.assertTrue(os.path.exists(os.path.join(self.repo, "one.txt")))
        self.assertTrue(os.path.exists(os.path.join(self.repo, "two.txt")))
        log = sh(["git", "log", "--oneline"], self.repo).stdout
        # E45: the engine writes its own merge subject now.
        self.assertEqual(log.count("Brother integrated "), 2, log)

        # THE ADVANCING BASE, in the run's own words: the second integration
        # applied to the revision the first one produced, not to R0.
        lines = [l for l in out.splitlines() if "applied to" in l]
        self.assertEqual(len(lines), 2)
        first_after = lines[0].split("ON canonical at")[-1].strip()
        second_base = lines[1].split("applied to")[-1].split("and its")[0].strip()
        self.assertEqual(first_after, second_base,
                         "the second unit must verify on the base the first "
                         "advanced:\n%s" % out)

        with open(self.claims, encoding="utf-8") as fh:
            store = json.load(fh)
        for uid in ("S1", "S2"):
            self.assertEqual(store[uid]["state"], "done", uid)

    def test_a_rerun_does_not_duplicate_the_integrations(self):
        self.run_spine("spine-test")
        self.run_spine("spine-again")
        log = sh(["git", "log", "--oneline"], self.repo).stdout
        self.assertEqual(log.count("Brother integrated "), 2,
                         "a rerun duplicated integrations:\n" + log)


if __name__ == "__main__":
    unittest.main()
