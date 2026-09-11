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


class RollingRunStartsADependentWhileASiblingIsStillLive(unittest.TestCase):
    """H4 wired: rolling_run() must let a dependent start the instant its
    one real dependency lands, beside an unrelated sibling that is still
    running, rather than waiting for the whole batch that admitted it.

    Proven on OBSERVED STATE, never on wall clock: B's worker blocks on a
    real file it polls for, so it provably cannot finish until this test
    releases it, and D's worker writes its own marker the moment it starts.
    A wave scheduler (dispatch a batch, wait for ALL of it, dispatch the
    next) would also eventually run D after A finishes; what only a rolling
    scheduler can do is start D while B, an entirely unrelated, still
    running sibling from the SAME original batch, has not finished. That
    is the fact this test pins: D.started must appear before B.release is
    ever written, with B.started already down and B.done still absent."""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="spine-roll-")
        for a in (["init", "-q", "-b", "main"],
                  ["config", "user.email", "a@b.c"],
                  ["config", "user.name", "t"]):
            sh(["git"] + a, self.repo)
        with open(os.path.join(self.repo, "base.txt"), "w", encoding="utf-8") as fh:
            fh.write("base\n")
        sh(["git", "add", "-A"], self.repo)
        sh(["git", "commit", "-q", "-m", "R0"], self.repo)

        self.claims = os.path.join(tempfile.mkdtemp(), "claims.json")
        self.marks = tempfile.mkdtemp(prefix="marks-")

        # A and B are both immediately ready and write-disjoint, so a cap of
        # 2 admits them together in the first batch. D depends on A alone
        # and is write-disjoint from B, so once A lands D has nothing left
        # to wait on: the only question is whether the scheduler offers it
        # before B, its unrelated batch-mate, is done.
        self.doc = {"rows": [
            {"id": "A", "depends_on": [], "owns": ["a.txt"],
             "done_check": "test -f a.txt", "in_ship_v1": True},
            {"id": "B", "depends_on": [], "owns": ["b.txt"],
             "done_check": "test -f b.txt", "in_ship_v1": True},
            {"id": "D", "depends_on": ["A"], "owns": ["d.txt"],
             "done_check": "test -f d.txt", "in_ship_v1": True},
        ]}

        self.worker_script = os.path.join(tempfile.mkdtemp(), "worker.sh")
        with open(self.worker_script, "w", encoding="utf-8") as fh:
            fh.write(
                '#!/bin/sh\n'
                'brief=$(cat)\n'
                'unit=$(printf \'%s\' "$brief" | python3 -c '
                '"import json,sys; print(json.load(sys.stdin).get(\'unit_id\',\'\'))" '
                '2>/dev/null)\n'
                'case "$unit" in\n'
                '  A) touch "$MARK_DIR/A.started"; echo made-a > a.txt ;;\n'
                '  D) touch "$MARK_DIR/D.started"; echo made-d > d.txt ;;\n'
                '  B)\n'
                '    touch "$MARK_DIR/B.started"\n'
                '    i=0\n'
                '    while [ ! -f "$MARK_DIR/B.release" ]; do\n'
                '      sleep 0.05; i=$((i+1))\n'
                '      if [ "$i" -gt 600 ]; then break; fi\n'
                '    done\n'
                '    echo made-b > b.txt\n'
                '    touch "$MARK_DIR/B.done"\n'
                '    ;;\n'
                'esac\n'
                'git add -A && git commit -qm "work for $unit"\n')
        os.chmod(self.worker_script, 0o755)

    def _mark(self, name):
        return os.path.join(self.marks, name)

    def _wait_for(self, path, timeout=30.0):
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.exists(path):
                return True
            time.sleep(0.05)
        return False

    def test_dependent_starts_while_unrelated_sibling_still_runs(self):
        import threading
        import loop_bridge as B  # noqa: E402  (local: avoid a module-level cycle with sys.path setup above)

        parts, problem = B.load_parts()
        self.assertIsNotNone(parts, problem)

        environ = dict(os.environ, MARK_DIR=self.marks)
        worker = B.LaneWorker(parts["spawn"], ["sh", self.worker_script],
                              environ=environ)

        outcome = {}

        def _go():
            outcome["result"] = B.rolling_run(
                self.doc, parts, worker, cwd=self.repo, cap=2,
                store=self.claims, owner="roll-test")

        thread = threading.Thread(target=_go)
        thread.start()

        self.assertTrue(self._wait_for(self._mark("B.started")),
                        "B\'s worker never started at all")
        # THE PROOF. D must start before B is released, which this test
        # controls: B literally cannot reach B.done until B.release exists,
        # so D.started appearing first is not a timing accident, it is the
        # only order the file dependency allows.
        self.assertTrue(self._wait_for(self._mark("D.started"), timeout=30.0),
                        "D never started while its unrelated sibling B was "
                        "still running, the scheduler is waving, not "
                        "rolling")
        self.assertFalse(os.path.exists(self._mark("B.done")),
                         "B had already finished by the time D started, so "
                         "this proves nothing about rolling admission")

        # Release B and let the round close.
        open(self._mark("B.release"), "w", encoding="utf-8").close()
        thread.join(timeout=60)
        self.assertIn("result", outcome, "rolling_run never returned")

        for fname in ("a.txt", "b.txt", "d.txt"):
            self.assertTrue(os.path.exists(os.path.join(self.repo, fname)),
                            fname)
        log = sh(["git", "log", "--oneline"], self.repo).stdout
        self.assertEqual(log.count("Brother integrated "), 3, log)


if __name__ == "__main__":
    unittest.main()
