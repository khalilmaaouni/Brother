#!/usr/bin/env python3
"""U80.a: the ways a night used to go wrong, tested as the owner lives them, each at its real entry point.

Journey 4  a night whose only unit is cooling down waits; it never stops UNPRODUCTIVE (unit D32). Entry point: the lines
           of scripts/loop/loop_pass.sh between `# D32 BEGIN` and `# D32 END`, run by bash with the pass's variables.
Journey 6  a broken deploy candidate is refused before the drain, and the live loop is never touched (unit M40). Entry
           point: scripts/loop/redeploy.sh, --check and the default mode, on test_redeploy_check's scratch fixture (a
           bare upstream, a plan clone, a loop worktree, stubbed stop, intake and stage tools).
Journey 7  a run killed with no RUN END reads ABORTED on the next status, and nothing resumes (unit X50). Entry point:
           `python3 -B scripts/loop/loop_report.py --summary LOG` as a child process under an audit hook that records
           every process start, signal, file write and rename the report makes.
Across     a landing refused for an environment red is HELD, never counted against the build (units G10 and G14).
           Entry point: land_batch.after_build, the function land_batch.main calls on land_apply's output.

Every fixture lives under a temp folder; HOME is a temp folder before any loop module is imported; nothing touches the
real ~/.claude, a real deploy or a real loop, and no network is used. Python 3.9 and 3.13, standard library only.
Run: python3 -B scripts/loop/test_loop_golden_journeys.py
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PASS_SH = os.path.join(HERE, "loop_pass.sh")
REDEPLOY_SH = os.path.join(HERE, "redeploy.sh")
LOOP_REPORT = os.path.join(HERE, "loop_report.py")

_HOME = tempfile.mkdtemp(prefix="u80-journeys-home-")
_SAVED_HOME = os.environ.get("HOME")
os.environ["HOME"] = _HOME          # land_batch reads HOME at import: never this machine's evidence
os.environ.pop("BROTHER_WBS", None)
if HERE not in sys.path:
    sys.path.insert(0, HERE)        # this folder only: the loop's own copies


def tearDownModule():
    if _SAVED_HOME is None:
        os.environ.pop("HOME", None)
    else:
        os.environ["HOME"] = _SAVED_HOME
    shutil.rmtree(_HOME, ignore_errors=True)


def _write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)


# Journey 4 ----------------------------------------------------------------------------------------------------------
def _pass_block():
    """The exit 45 block of loop_pass.sh, the lines between its D32 markers. No markers: the journey does not exist."""
    with open(PASS_SH, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    if "# D32 BEGIN" not in lines or "# D32 END" not in lines:
        raise AssertionError("loop_pass.sh has no '# D32 BEGIN' / '# D32 END' block: a cooldown only pass cannot wait")
    return "\n".join(lines[lines.index("# D32 BEGIN") + 1:lines.index("# D32 END")])


def _run_pass_end(pool_all):
    """The end of a pass that started nothing, holds no live runner, landed and closed nothing, with this pool output."""
    tmp = tempfile.mkdtemp(prefix="u80-pass-")
    try:
        script = os.path.join(tmp, "pass_end.sh")
        _write(script, "#!/bin/bash\n" + _pass_block() + "\necho 'FELL THROUGH'\nexit 0\n")
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": tmp, "POOL_ALL": pool_all,
               "STARTED": "0", "LIVE": "0", "LANDED_N": "0", "CLOSED_N": "0"}
        return subprocess.run(["bash", script], env=env, cwd=tmp, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=60)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class CooldownJourney(unittest.TestCase):
    """Journey 4: the 2026-10-14 night whose only unit sat in the re-seat hold and stopped on its first pass."""

    POOL = "SCOPE   .\nZA    ZA.a     skip: skip: ZA.a started under ten minutes ago\n%sstarted 0\n"

    def test_a_cooldown_only_night_waits_instead_of_alarming(self):
        until = int(time.time()) + 600
        r = _run_pass_end(self.POOL % ("COOLDOWN ZA ZA.a until %d\n" % until))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        waiting = [l for l in r.stdout.splitlines() if l.startswith("WAITING cooldown: ZA.a may restart after ")]
        self.assertEqual(len(waiting), 1, r.stdout)
        self.assertIn("this pass is a wait, not UNPRODUCTIVE", waiting[0])
        self.assertNotIn("PASS DID NOTHING", r.stdout)

    def test_a_night_with_nothing_cooling_still_alarms_unproductive(self):
        r = _run_pass_end(self.POOL % "")
        self.assertEqual(r.returncode, 45, r.stdout + r.stderr)
        self.assertIn("PASS DID NOTHING", r.stdout)
        self.assertNotIn("WAITING", r.stdout)


# Journey 6 ----------------------------------------------------------------------------------------------------------
class BrokenCandidateJourney(unittest.TestCase):
    """Journey 6: the deploy's own staging refuses the candidate's closure while a loop runs (the stop stub's --dry
    answers 1, a live loop). The refusal must come from the check, before any drain, stop, deploy or launch."""

    BROKEN = "dynamic loader requires a resolved import trace"

    def setUp(self):
        if not os.path.isfile(REDEPLOY_SH):
            self.fail("scripts/loop/redeploy.sh does not exist: nothing checks a candidate before the drain")
        import test_redeploy_check as TRC   # the M40 fixture, imported here so its absence fails only this journey
        self.TRC = TRC
        self.fx = TRC.RedeployFixture()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.deploy = os.path.join(self.fx.tmp, "stub_deploy.sh")
        self.start = os.path.join(self.fx.tmp, "stub_start.sh")
        TRC._write(self.deploy, TRC.STUB_DEPLOY_SH, executable=True)
        TRC._write(self.start, TRC.STUB_START_SH, executable=True)
        self.heads = {b: self.fx.upstream(b) for b in ("plan/x", "loop/x")}
        self.local = (self.fx.out(self.fx.pw, "rev-parse", "HEAD"), self.fx.out(self.fx.lw, "rev-parse", "HEAD"))

    def assertLiveLoopUntouched(self, out):
        self.assertEqual(self.fx.stop_calls(), ["--dry"], out)   # asked once, read only: no --drain, no stop, no --resume
        for name in ("deploy.txt", "start-argv.txt"):
            self.assertFalse(os.path.exists(os.path.join(self.fx.log, name)), name + " written: " + out)
        self.assertEqual({b: self.fx.upstream(b) for b in self.heads}, self.heads, out)   # nothing pushed or merged
        self.assertEqual((self.fx.out(self.fx.pw, "rev-parse", "HEAD"), self.fx.out(self.fx.lw, "rev-parse", "HEAD")),
                         self.local, out)
        self.assertEqual(self.fx.scratch_left(), [], out)

    def run_redeploy(self, mode):
        more = ["--deadline", self.TRC.DATED, "--probe-cap-usd", "5"]
        if mode is None:
            more += ["--plan", "docs/plan/P.json", "--scope", "^(A1)$", "--budget-usd", "5", "--reason", "u80 journey"]
        return self.fx.run_redeploy(*more, mode=mode, TEST_STAGE="fail", TEST_LIVE_RC="1",
                                    REDEPLOY_DEPLOY=self.deploy, REDEPLOY_START=self.start)

    def test_a_broken_candidate_fails_the_check_by_name_while_the_loop_runs(self):
        rc, out = self.run_redeploy("--check")
        self.assertEqual(rc, 1, out)
        rows = self.fx.rows(out)
        self.assertEqual(sorted(n for n, (s, _) in rows.items() if s != "PASS"), ["stage"], out)
        self.assertEqual(rows["live"][0], "PASS", out)
        self.assertIn(self.BROKEN, rows["stage"][1])
        self.assertIn("CHECK FAIL: 11 PASS, 1 FAIL, 0 NO-DATA", out)
        self.assertLiveLoopUntouched(out)

    def test_a_broken_candidate_is_refused_before_the_drain_and_nothing_is_deployed(self):
        rc, out = self.run_redeploy(None)
        self.assertEqual(rc, 2, out)
        self.assertIn(self.BROKEN, out)
        self.assertIn("REDEPLOY REFUSED [check]: the check above found 1 FAIL and 0 NO-DATA", out)
        self.assertLiveLoopUntouched(out)


# Journey 7 ----------------------------------------------------------------------------------------------------------
# The report runs as the status reader runs it, a child python with -B, under an audit hook (Python 3.8+) that records
# what it does to the machine; the record goes to stderr after a marker, so recording it writes no file.
_AUDITED = r'''
import json, os, runpy, sys
ev, on = [], [True]
WATCH = ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty", "os.kill",
         "os.rename", "os.remove", "open")
W = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
def hook(name, args):
    if not on[0] or name not in WATCH:
        return
    if name == "open":
        path, mode, flags = args
        if not isinstance(path, str) or not ((mode and any(c in mode for c in "wax+")) or (flags or 0) & W):
            return
        args = (path,)
    ev.append([name] + [a if isinstance(a, (str, int)) or a is None else repr(a) for a in args])
sys.addaudithook(hook)
script = sys.argv[1]
sys.argv = [script] + sys.argv[2:]
try:
    runpy.run_path(script, run_name="__main__")
finally:
    on[0] = False
    sys.stderr.write("\nU80-AUDIT " + json.dumps(ev) + "\n")
'''


def _dead_pid():
    """A pid that existed and is gone: a child of this test, started and reaped before the report runs."""
    p = subprocess.Popen(["/bin/sh", "-c", "exit 0"])
    p.wait()
    try:
        os.kill(p.pid, 0)
    except ProcessLookupError:
        return p.pid
    except PermissionError:
        pass   # sbe: allow-silent reused by another owner's process; the raise below names it
    raise AssertionError("pid %d was reused before the journey could run; run it again" % p.pid)


class InterruptedRunJourney(unittest.TestCase):
    """Journey 7: the driver was killed mid run (power, a kill, a crash): its log holds RUN START and RUN PID, and no
    RUN END. The next status must say ABORTED, name a restart, write its files atomically, and start nothing."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="u80-run-", dir=_HOME)
        self.addCleanup(shutil.rmtree, self.d, True)
        self.log = os.path.join(self.d, "loop-until-20261016-0100.log")
        self.body = ("LOOP UNTIL 07h00\nRUN START %s\nRUN PID %d\n"
                     "LANDED  A1.a | units with every sub unit landed (run the unit done-check): none | log x\n"
                     "PASS 2 ok\n" % (datetime.datetime(2026, 10, 16, 1).isoformat(), _dead_pid()))
        _write(self.log, self.body)

    def summary(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("BROTHER_", "PYTHON"))}
        env["HOME"] = _HOME
        p = subprocess.run([sys.executable, "-B", "-c", _AUDITED, LOOP_REPORT, "--summary", self.log], cwd=self.d,
                           env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=120)
        marked = [l for l in p.stderr.splitlines() if l.startswith("U80-AUDIT ")]
        self.assertEqual(len(marked), 1, p.stderr)
        return p.returncode, p.stdout, json.loads(marked[0][len("U80-AUDIT "):])

    def test_a_killed_run_reads_aborted_names_a_restart_and_resumes_nothing(self):
        rc, out, events = self.summary()
        self.assertEqual(rc, 0, out)
        self.assertIn("state: ABORTED\n", out)
        stem = os.path.splitext(self.log)[0]
        with open(stem + ".summary.json", encoding="utf-8") as fh:
            s = json.load(fh)
        self.assertEqual(s["state"], "ABORTED", s)
        self.assertIn("restart", s["next_action"], s)
        self.assertIn("next action: %s\n" % s["next_action"], out)
        with open(stem + ".summary.md", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), out)
        # nothing resumes: no process started, no signal but the liveness probe, the log itself not touched
        starts = [e for e in events if e[0] not in ("open", "os.rename", "os.remove", "os.kill")]
        self.assertEqual(starts, [], events)
        self.assertEqual([e for e in events if e[0] == "os.kill" and e[2] != 0], [], events)
        with open(self.log, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), self.body)

    def test_the_summary_files_are_written_atomically_beside_the_log(self):
        rc, out, events = self.summary()
        self.assertEqual(rc, 0, out)
        stem = os.path.splitext(self.log)[0]
        finals = [stem + ".summary.json", stem + ".summary.md"]
        writes = [e[1] for e in events if e[0] == "open"]
        renames = [(e[1], e[2]) for e in events if e[0] == "os.rename"]
        self.assertEqual([dst for _, dst in renames], finals, events)
        for src, dst in renames:
            self.assertEqual(os.path.dirname(src), self.d, src)   # the temp is beside its file: one rename, one disk
            self.assertTrue(os.path.basename(src).startswith("." + os.path.basename(dst) + "."), src)
            self.assertFalse(os.path.exists(src), src)
        self.assertEqual(writes, [src for src, _ in renames], events)   # never opened for writing in place, nothing else
        self.assertEqual(sorted(os.listdir(self.d)), sorted(os.path.basename(p) for p in [self.log] + finals))


# Across units: G10 and G14 ------------------------------------------------------------------------------------------
# land_apply's own output for a suite that is red on the unchanged copy too (land_apply.py, the ENVDEF branch), exit 3.
ENV_RED = ("py3     scripts/test_u1a.py exit=1\n"
           "ENVDEF  environment defect: scripts/test_u1a.py on py3 (base exit 1, build exit 1): red on the unchanged copy too, never the build's\n"
           "FUZZ    mode child (timeout 60 s)\n"
           "FUZZ    new modules 0 | crashes 0 | calls that returned instead of refusing 0\n"
           "REMEDY  repair the named suite (or what it needs) on the landing tree under the landing sandbox, then land again; the build is not charged\n"
           "VERDICT BLOCKED-ENVIRONMENT | environment defect: scripts/test_u1a.py on py3 (base exit 1, build exit 1) | touched: docs/u1a.txt\n")
READY = "READY built in the night run\n"


class EnvironmentRedJourney(unittest.TestCase):
    """A landing whose only red is the machine's: the build stays READY and HELD; no note, no count, no park."""

    def test_a_landing_refused_for_an_environment_red_is_held_never_counted(self):
        import land_batch as LB
        word, why, crashes, status = LB.after_build(3, ENV_RED, READY)
        self.assertEqual(word, "hold", why)
        self.assertTrue(why.startswith("BLOCKED-ENVIRONMENT | environment defect: scripts/test_u1a.py"), why)
        self.assertEqual(crashes, 0)
        self.assertIsNone(status)   # no QUARANTINE status: nothing is counted against the build

    def test_the_same_red_caused_by_the_build_is_still_dropped(self):
        import land_batch as LB
        red = ENV_RED.replace("ENVDEF  environment defect", "BASE    scripts/test_u1a.py on py3 green").replace(
            "VERDICT BLOCKED-ENVIRONMENT | environment defect: scripts/test_u1a.py on py3 (base exit 1, build exit 1)",
            "VERDICT RED: 1")
        word, why, crashes, status = LB.after_build(1, red, READY)
        self.assertEqual(word, "drop", why)
        self.assertTrue(status.startswith("QUARANTINE dropped at landing"), status)


if __name__ == "__main__":
    unittest.main()
