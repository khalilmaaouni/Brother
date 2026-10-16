#!/usr/bin/env python3
"""D30 (L22): the diagnostician runs from a NAMED frozen root on every pass outside a proof, and a parked unit gets a fact.

THE SHAPE. The real loop_pass.sh runs with cwd T, a scratch landing tree (a git checkout with a bare upstream, so the pass
reaches its diagnostician rung). T holds a TRIPWIRE diag_round.py in scripts/ and scripts/loop/: it records itself and
exits 97, so a landing tree copy that ever runs is read from a file, never guessed. HOME's bin holds the deployed tools the
round calls (real diag_brief, diag_apply, diag_notes, plan_store, plan_path; zero cost stubs for the rest) and a frozen
root, <bin>/candidate by default: a real copy of scripts/diag_round.py plus a fake fan out that answers every brief with
one fact and the grep that proves it against T. The deploy stamp <bin>/.deploy-stamp.json is written the way
deploy_stamped.deploy writes it ({"files": {"bin/<path>": sha256}}, through its own hashes()). A parked unit is a run
folder whose STATUS reads EXHAUSTED. No note exists before the pass, and no case writes one by hand.
Cases, one condition each:
  1. the default frozen root, stamped: a NEW diagnosis fact lands in diag_notes from the round the pass launched.
  2. BROTHER_FROZEN_ROOT names another stamped root inside the bin: that root runs, the default (absent) is not needed.
  3. the round leaves the frozen root byte identical to its stamp (no cache file), so the next pass still runs it.
  4. no frozen root: NO-DATA, nothing runs.
  5. one byte of the root differs from the stamp: NO-DATA, nothing runs.
  6. one extra file in the root: NO-DATA, nothing runs.
  7. no deploy stamp: NO-DATA, nothing runs.
  8. a stamp with no entry for a root holding no files: NO-DATA, nothing runs.
  9. a frozen root inside the landing tree, with a stamp naming it: NO-DATA (outside the bin), the tree copy never runs.
 10. a relative frozen root (a committed symlink in T to the stamped root): NO-DATA, nothing runs.
 11. a proof (BROTHER_PROOF_PHASE with BROTHER_CODE_ROOT): the code root runs exactly as before, no --code-root, no
     added environment, and the frozen root is never read.
 12 to 14. brother_night_tick: BROTHER_FROZEN_ROOT is neither a proof nor a code root to the tick; a code root alone is
     not a proof (_in_proof); a proof with no code root stays NO-DATA.
Every case also asserts the landing tree tripwire stayed empty.
Run: python3 -B scripts/loop/test_diag_frozen_root.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
PASS = os.path.join(HERE, "loop_pass.sh")
sys.path.insert(0, HERE)
import deploy_stamped  # noqa: E402  the stamp writer's own hash function and file name

SUB, UNIT, MARK = "U9.a", "U9", "FROZEN-FACT-MARKER"
TRIPWIRE = ('#!/usr/bin/env python3\nimport os, sys\n'
            'open(os.path.join(os.environ["HOME"], "tripwire"), "a").write(os.path.realpath(__file__) + "\\n")\nsys.exit(97)\n')
WITNESS = ('#!/usr/bin/env python3\nimport os, sys\n'
           'open(os.path.join(os.environ["HOME"], "witness"), "a").write("CODE " + " ".join(sys.argv[1:]) + " PYTHONDONTWRITEBYTECODE="'
           ' + str(os.environ.get("PYTHONDONTWRITEBYTECODE")) + "\\n")\n')
# the fake model: one fact plus the grep that proves it against the landing tree. The package import makes a run that may
# write byte code leave a cache file in the root it ran from, which case 3 reads.
FANOUT = '''import json, sys
from plugin.runtime.brother.core import helper  # noqa: F401
for job in json.load(open(sys.argv[1])):
    with open(job["out"], "w") as fh:
        json.dump({"fact": "docs/fact.txt already carries %s, which no brief showed", "prove": "grep -n %s docs/fact.txt",
                   "expect": "%s", "hint": "read docs/fact.txt first"}, fh)
''' % (MARK, MARK, MARK)
STUBS = {
    "loop_heartbeat.py": "", "salvage.py": 'print("SALVAGE nothing")\n',
    "loop_done.py": "import sys\nsys.exit(1)\n",
    "pass_digest.py": 'import sys\nif "--ready-counts" in sys.argv:\n    print("0 0"); sys.exit(0)\nprint("DIGEST")\n',
    "land_batch.py": 'print("fp-1")\n',
    "runner_pool.py": 'def admissible(plan, sub):\n    return ""\nif __name__ == "__main__":\n    print("started 0")\n',
    "grade_build.py": 'def private_hits(text):\n    return []\ndef record_claim(*a, **k):\n    return None\n',
}
REAL_BIN = ("diag_brief.py", "diag_apply.py", "diag_notes.py", "plan_store.py", "plan_path.py", "loop_procs.py",
            "loop_switches.py")
# a live loop on this machine must not decide a fixture: only this box's processes count as "already running"
PGREP = '#!/bin/sh\n[ "$1" = "-f" ] || exec /usr/bin/pgrep "$@"\n/usr/bin/pgrep -fl "$2" | grep -qF "$D30_BOX"\n'


def w(path, body, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, mode)


def git(cwd, *args):
    r = subprocess.run(["git", "-c", "core.hooksPath=/dev/null"] + list(args), cwd=cwd, capture_output=True, text=True,
                       timeout=60)
    if r.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args), r.stderr))
    return r.stdout


class FrozenRootPass(unittest.TestCase):
    def setUp(self):
        root = os.path.join(os.path.expanduser("~"), ".claude", "brother-scratch")
        os.makedirs(root, exist_ok=True)
        self.box = os.path.realpath(tempfile.mkdtemp(prefix="run-d30-frozen-", dir=root))
        self.home = os.path.join(self.box, "home")
        self.bin = os.path.join(self.home, ".claude", "bin")
        self.ev = os.path.join(self.home, ".claude", "evidence")
        self.tree = os.path.join(self.box, "tree")
        self.cand = os.path.join(self.bin, "candidate")
        self.pathbin = os.path.join(self.box, "pathbin")
        w(os.path.join(self.pathbin, "pgrep"), PGREP, 0o755)
        run = os.path.join(self.ev, "unit-runs", SUB + "-101010")
        w(os.path.join(run, "STATUS"), "EXHAUSTED 3 rounds, no build passed\n")
        for name, body in STUBS.items():
            w(os.path.join(self.bin, name), body, 0o755)
        for name in REAL_BIN:
            shutil.copy2(os.path.join(HERE, name), self.bin)
        # the landing tree: plan, spec, the fact the model will name, and a tripwire copy of the diagnostician in each place
        plan = {"units": [{"id": UNIT, "state": "OPEN", "sub_units": [SUB], "spec": "docs/plan/specs/U9.md",
                           "owns": [], "evidence": ""}]}
        w(os.path.join(self.tree, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json"), json.dumps(plan))
        w(os.path.join(self.tree, "docs", "plan", "specs", "U9.md"), "# U9\n\n### %s build the thing\n" % SUB)
        w(os.path.join(self.tree, "docs", "fact.txt"), "the module exists: %s 42\n" % MARK)
        for rel in ("scripts/diag_round.py", "scripts/loop/diag_round.py"):
            w(os.path.join(self.tree, rel), TRIPWIRE, 0o755)
        bare = os.path.join(self.box, "upstream.git")
        git(self.box, "init", "-q", "--bare", "-b", "main", bare)
        git(self.box, "init", "-q", "-b", "main", self.tree)
        for k, v in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
            git(self.tree, "config", k, v)
        self.commit("tree")
        git(self.tree, "remote", "add", "origin", bare)
        git(self.tree, "push", "-q", "-u", "origin", "main")

    def tearDown(self):
        for _ in range(60):   # the round is detached: let this box's processes end, then reap any straggler
            if subprocess.run(["/usr/bin/pgrep", "-f", self.box], capture_output=True).returncode != 0:
                break
            time.sleep(0.5)
        reaped = subprocess.run(["/usr/bin/pkill", "-f", self.box], capture_output=True)
        self.assertIn(reaped.returncode, (0, 1), "pkill failed: %r" % reaped.stderr)   # 1 is "nothing left to reap"
        shutil.rmtree(self.box, ignore_errors=True)

    def commit(self, msg):
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-q", "-m", msg)

    def plant_root(self, root):
        """A frozen root as the deploy stages one: the real diagnostician and the fan out module (here a fake model)."""
        os.makedirs(os.path.join(root, "scripts"), exist_ok=True)
        shutil.copy2(os.path.join(REPO, "scripts", "diag_round.py"), os.path.join(root, "scripts", "diag_round.py"))
        core = os.path.join(root, "plugin", "runtime", "brother", "core")
        for pkg in ("plugin", "plugin/runtime", "plugin/runtime/brother", "plugin/runtime/brother/core"):
            w(os.path.join(root, pkg, "__init__.py"), "")
        w(os.path.join(core, "helper.py"), "X = 1\n")
        w(os.path.join(core, "or_fanout.py"), FANOUT)
        return root

    def stamp(self, extra=None):
        """<bin>/.deploy-stamp.json exactly as deploy_stamped.deploy writes it: every file of the bin, keyed bin/<path>."""
        files = {"bin/" + n: s for n, s in deploy_stamped.hashes(Path(self.bin)).items() if n != deploy_stamped.STAMP}
        files.update(extra or {})
        w(os.path.join(self.bin, deploy_stamped.STAMP), json.dumps({"schema": 1, "revision": "f" * 40, "files": files}))

    def run_pass(self, **env):
        base = {k: v for k, v in os.environ.items()
                if not k.startswith("BROTHER_") and k not in ("LOCAL_SLOTS", "WORKERS_PER_ROUND",
                                                               "PYTHONDONTWRITEBYTECODE", "PYTHONPATH")}
        base.update(HOME=self.home, BROTHER_LAUNCH_WORKTREE=self.tree, BROTHER_SCOPE=".", D30_BOX=self.box,
                    PATH=self.pathbin + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"))
        base.update(env)
        r = subprocess.run(["bash", PASS], cwd=self.tree, env=base, capture_output=True, text=True, timeout=180)
        return r.stdout + r.stderr

    def read(self, *rel):
        p = os.path.join(self.home, *rel)
        if not os.path.isfile(p):
            return ""
        with open(p, encoding="utf-8") as fh:
            return fh.read()

    def note(self):
        return self.read(".claude", "evidence", "diag-notes", UNIT + ".txt")

    def wait_round(self):
        """The round is detached: wait for its closing ROUND line (or 90 s), never a fixed sleep."""
        for _ in range(180):
            if "ROUND " in self.read(".claude", "evidence", "diag-round.log"):
                return
            time.sleep(0.5)

    def assert_nothing_ran(self, out):
        time.sleep(1.0)   # a launch would have opened the round's log at once; give it the moment it needs
        self.assertEqual(self.read(".claude", "evidence", "diag-round.log"), "", "a diagnostician was launched: " + out)
        self.assertEqual(self.read("tripwire"), "", "landing tree code ran: " + out)
        self.assertEqual(self.note(), "", out)

    # 1
    def test_a_parked_unit_gets_a_new_fact_from_the_default_frozen_root_with_no_hand_note(self):
        self.plant_root(self.cand)
        self.stamp()
        self.assertEqual(self.note(), "")
        out = self.run_pass()
        self.assertIn("DIAGNOSE started detached from " + self.cand, out)
        self.wait_round()
        log = self.read(".claude", "evidence", "diag-round.log")
        self.assertIn("ROUND   1 proven", log, out + log)
        self.assertIn(SUB + ": VERIFIED FACT: docs/fact.txt already carries " + MARK, self.note(), log)
        self.assertEqual(self.read("tripwire"), "", "landing tree code ran: " + out + log)

    # 2
    def test_the_named_frozen_root_is_the_one_that_runs(self):
        alt = self.plant_root(os.path.join(self.bin, "frozen-alt"))
        self.stamp()
        out = self.run_pass(BROTHER_FROZEN_ROOT=alt)
        self.assertIn("DIAGNOSE started detached from " + alt, out)
        self.wait_round()
        self.assertIn(SUB + ": VERIFIED FACT:", self.note(), out + self.read(".claude", "evidence", "diag-round.log"))
        self.assertEqual(self.read("tripwire"), "", out)

    # 3
    def test_a_round_leaves_the_frozen_root_identical_to_its_stamp(self):
        self.plant_root(self.cand)
        self.stamp()
        out = self.run_pass()
        self.wait_round()
        self.assertIn(SUB + ": VERIFIED FACT:", self.note(), out)
        caches = [d for d, _, _ in os.walk(self.cand) if os.path.basename(d) == "__pycache__"]
        self.assertEqual(caches, [], "the round wrote into the root it was checked from")
        os.remove(os.path.join(self.ev, "unit-runs", SUB + "-101010", "DIAGNOSED"))   # ask again, so the next pass launches
        self.assertNotIn("NO-DATA", self.run_pass())

    # 4
    def test_no_frozen_root_reads_no_data_and_runs_nothing(self):
        self.stamp()
        out = self.run_pass()
        self.assertIn("DIAGNOSE skipped: no frozen code root (BROTHER_CODE_ROOT) and no deployed candidate", out)
        self.assert_nothing_ran(out)

    # 5
    def test_one_byte_off_the_stamp_reads_no_data_and_runs_nothing(self):
        self.plant_root(self.cand)
        self.stamp()
        with open(os.path.join(self.cand, "scripts", "diag_round.py"), "a", encoding="utf-8") as fh:
            fh.write("\n")
        out = self.run_pass()
        self.assertIn("DIAGNOSE skipped: NO-DATA, the frozen root " + self.cand, out)
        self.assertIn("differs from the deploy stamp at 1 path(s), first scripts/diag_round.py", out)
        self.assert_nothing_ran(out)

    # 6
    def test_one_extra_file_in_the_root_reads_no_data_and_runs_nothing(self):
        self.plant_root(self.cand)
        self.stamp()
        w(os.path.join(self.cand, "scripts", "extra.py"), "X = 2\n")
        out = self.run_pass()
        self.assertIn("differs from the deploy stamp at 1 path(s), first scripts/extra.py", out)
        self.assert_nothing_ran(out)

    # 7
    def test_no_deploy_stamp_reads_no_data_and_runs_nothing(self):
        self.plant_root(self.cand)
        out = self.run_pass()
        self.assertIn("has an unreadable deploy stamp or file", out)
        self.assert_nothing_ran(out)

    # 8
    def test_a_stamp_with_no_entry_for_an_empty_root_reads_no_data(self):
        os.makedirs(os.path.join(self.cand, "plugin", "runtime", "brother", "core"))
        self.stamp()
        out = self.run_pass()
        self.assertIn("has no entry in the deploy stamp", out)
        self.assert_nothing_ran(out)

    # 9
    def test_a_frozen_root_in_the_landing_tree_never_runs_even_with_a_stamp_naming_it(self):
        inside = os.path.join(self.tree, "frozen")
        self.plant_root(inside)
        w(os.path.join(inside, "scripts", "diag_round.py"), TRIPWIRE, 0o755)
        self.commit("a frozen root inside the landing tree")
        git(self.tree, "push", "-q")
        prefix = "bin/" + os.path.relpath(inside, os.path.realpath(self.bin)) + "/"
        self.stamp({prefix + n: s for n, s in deploy_stamped.hashes(Path(inside)).items()})
        out = self.run_pass(BROTHER_FROZEN_ROOT=inside)
        self.assertIn("is outside the deployed bin", out)
        self.assert_nothing_ran(out)

    # 10
    def test_a_relative_frozen_root_reads_no_data(self):
        self.plant_root(self.cand)
        self.stamp()
        os.symlink(self.cand, os.path.join(self.tree, "frozen"))
        self.commit("a relative way to the frozen root")
        git(self.tree, "push", "-q")
        out = self.run_pass(BROTHER_FROZEN_ROOT="frozen")
        self.assertIn("is not an absolute path", out)
        self.assert_nothing_ran(out)

    # 11
    def test_in_a_proof_the_code_root_runs_as_before_and_the_frozen_root_is_never_read(self):
        code = os.path.join(self.box, "code")
        w(os.path.join(code, "scripts", "diag_round.py"), WITNESS, 0o755)
        out = self.run_pass(BROTHER_CODE_ROOT=code, BROTHER_PROOF_PHASE="RB", BROTHER_FROZEN_ROOT="/nonexistent/frozen")
        time.sleep(1.0)
        self.assertEqual(self.read("witness"), "CODE --limit 6 PYTHONDONTWRITEBYTECODE=None\n", out)
        self.assertIn("DIAGNOSE started detached from " + code, out)
        self.assertNotIn("NO-DATA, the frozen root", out)
        self.assertEqual(self.read("tripwire"), "", out)


TICK_WATCHDOG = '''import json, os
def read_day_plan_rows(path):
    return json.load(open(path))["rows"]
def ready_set_summary(rows):
    return {"ready": [%(label)r] + [r["id"] for r in rows], "in_flight": [], "event_wait": []}
if __name__ == "__main__":
    print(%(label)r)
'''


class NightTickNames(unittest.TestCase):
    """brother_night_tick reads BROTHER_PROOF_PHASE as the proof and BROTHER_CODE_ROOT as where frozen code lives; the
    diagnostician's BROTHER_FROZEN_ROOT is neither. Each call runs in a fresh interpreter, as test_code_root.py does."""

    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="d30-tick-")
        self.addCleanup(t.cleanup)
        self.box = os.path.realpath(t.name)
        self.landing, self.candidate = os.path.join(self.box, "landing"), os.path.join(self.box, "candidate")
        for tree in (self.landing, self.candidate):
            w(os.path.join(tree, "scripts", "task_watchdog.py"), TICK_WATCHDOG % {"label": os.path.basename(tree)})
        w(os.path.join(self.landing, "docs", "plan", "LIVE-STATE.json"), '{"rows": [{"id": "R1"}]}')

    def tick(self, expr, **values):
        env = {k: v for k, v in os.environ.items() if not k.startswith("BROTHER_")}
        env.update(BROTHER_NIGHTWATCH_ROOT=self.landing, HOME=self.box, **values)
        code = "import json, sys\nsys.path.insert(0, %r)\nimport brother_night_tick as T\nprint(json.dumps(%s))\n" % (HERE, expr)
        p = subprocess.run([sys.executable, "-B", "-c", code], env=env, cwd=self.box, capture_output=True, text=True,
                           timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return json.loads(p.stdout.strip().splitlines()[-1])

    # 12
    def test_the_frozen_root_is_neither_a_proof_nor_a_code_root_to_the_tick(self):
        got = self.tick("[T.ready_state()['ready_ids'], T.run_watchdog(), T._frozen_code(), T._in_proof()]",
                        BROTHER_FROZEN_ROOT=self.candidate)
        self.assertEqual(got, [["landing", "R1"], {"exit": 0, "headline": "landing"}, False, False])

    # 13
    def test_a_code_root_alone_is_not_a_proof_but_still_the_code(self):
        got = self.tick("[T._in_proof(), T._frozen_code(), T.ready_state()['ready_ids']]", BROTHER_CODE_ROOT=self.candidate)
        self.assertEqual(got, [False, True, ["candidate", "R1"]])

    # 14
    def test_a_proof_with_no_code_root_stays_no_data(self):
        got = self.tick("[T._in_proof(), T.ready_state(), T.run_watchdog()]", BROTHER_PROOF_PHASE="RB")
        self.assertEqual(got, [True, None, {"exit": None, "headline": None}])



class TestCheckRunsIsolated(unittest.TestCase):
    """Security review 2026-10-16 (HIGH): the frozen check and the launcher run `python3 -c` with the landing tree as
    their cwd, and `-c` puts the cwd first on sys.path, so a worker planted hashlib.py would run inside the integrity
    check. Both lines must run isolated (-I)."""

    def test_both_diagnostician_python_lines_are_isolated(self):
        with open(PASS, encoding="utf-8") as fh:
            text = fh.read()
        check = [l for l in text.splitlines() if '"$FROZEN_CHECK"' in l]
        launch = [l for l in text.splitlines() if "subprocess.Popen([sys.executable,'-B',sys.argv[1]" in l]
        self.assertEqual(len(check), 1, check)
        self.assertEqual(len(launch), 1, launch)
        self.assertIn("python3 -I -B -c", check[0])
        self.assertIn("python3 -I -c", launch[0])

    def test_isolation_is_what_stops_a_planted_module(self):
        box = tempfile.mkdtemp(prefix="isolated-")
        try:
            hit = os.path.join(box, "ran")
            with open(os.path.join(box, "hashlib.py"), "w", encoding="utf-8") as fh:
                fh.write("open(%r, 'w').close()\n" % hit)
            plain = subprocess.run([sys.executable, "-B", "-c", "import hashlib"], cwd=box, capture_output=True)
            self.assertTrue(os.path.exists(hit), "the hazard did not reproduce: %r" % plain.stderr)
            os.unlink(hit)
            iso = subprocess.run([sys.executable, "-I", "-B", "-c", "import hashlib"], cwd=box, capture_output=True)
            self.assertEqual(iso.returncode, 0, iso.stderr)
            self.assertFalse(os.path.exists(hit), "a planted module ran under -I")
        finally:
            shutil.rmtree(box, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()
