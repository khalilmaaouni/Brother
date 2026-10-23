#!/usr/bin/env python3
"""1.1.6 S2: the seat gate covers both hosts, a reboot is named at the next launch, the holder and the grant are wired.

WHY. seat_gate kept only proofs of transport claude, so a signed out Codex never stopped the arm. start_run.sh recorded
no boot identity, so a run killed by a reboot in the night read as a run that ended. The login holder (S1) and the
spend guard grant (SG1) existed as tools nothing started.

TESTED AT THE ENTRY POINTS. seat_gate and prepare in process (prepare is the intake's entry, its lines are what the
launcher reads); start_run.sh as a subprocess with its own seams (START_RUN_INTAKE, START_RUN_DRIVER,
START_RUN_LIVE_CHECK, START_RUN_HOLDER, START_RUN_GRANT, START_RUN_BOOTTIME) and every record under a throwaway
START_RUN_EVIDENCE; stop_loop.sh as a subprocess limited by STOP_LOOP_ONLY to this test's own folder. The stub intake
prints the seat lines through the REAL loop_intake.seat_gate and seat_lines, so the launcher reads what the intake
would print. The codex probe is driven once through the real CodexAdapter.login_probe on a fixture CODEX_HOME.

The mutations that must turn this suite red, each against its own case:
  M-S2A-CLAUDE-ONLY  seat_gate never calls codex_seat           -> test_a_signed_out_codex_refuses_the_arm
  M-S2A-TWO-ROWS     notify_once ignores the key                 -> test_both_signed_in_start_and_no_second_row
  M-S2A-NO-BOOT      reboot_note answers ""                      -> test_a_reboot_is_named_at_the_next_launch
  M-S2A-NO-REAP      stop_loop.sh skips the holder               -> test_the_holder_is_started_and_reaped
  M-S2A-NO-WORDS     the grant gets no words                     -> test_the_grant_gets_the_words
  M-S2A-LOGIN-NAME   the claude remedy drops brother-login       -> test_a_signed_out_claude_refuses_the_arm

OUT OF SCOPE, named: a run that ended on its own before a reboot is still named killed by it (the launch record does
not say whether the run ended; the spec compares boot times only); the first pass may start a seat before the holder
is up, and that seat reads the login itself (S1's fallback). Python 3.9 and 3.13, standard library only, no network.
Run: python3 -B scripts/loop/test_seat_gate_both_hosts.py
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "start_run.sh")
STOP = os.path.join(HERE, "stop_loop.sh")
sys.path.insert(0, HERE)
import loop_intake as LI  # noqa: E402
import test_start_run as TSR  # noqa: E402  the launcher fixture's git helpers, deadline and live check stub, reused

DATED = TSR.DATED
EVENING = "%02d:%02d" % (18, 0)   # the in process prepare's deadline, after the fixture clock's noon

STUB_INTAKE = r'''import json, os, sys
sys.path.insert(0, os.environ["TEST_LOOP_DIR"])
import loop_intake as LI
evd = os.environ["TEST_EVD"]
argv = sys.argv[1:]
words = argv[argv.index("--words") + 1] if "--words" in argv else ""
seats = os.environ.get("TEST_SEATS", "in")
proofs = [{"status": "OK", "role": "worker", "model": "m-claude", "transport": "claude", "program": "/p/claude",
           "version": "1", "cause": "fixture", "cached": False, "binding": {"transport": "claude"}},
          {"status": "OK", "role": "checker", "model": "m-codex", "transport": "codex", "program": "/p/codex",
           "version": "1", "cause": "fixture", "cached": False, "binding": {"transport": "codex"}}]
claude = (lambda program: ("REFUSED", "Not logged in")) if seats == "claude-out" else (lambda program: ("OK", "ok"))
codex = (lambda: ("FAIL", "codex login: /fixture/auth.json is missing")) if seats == "codex-out" else (lambda: ("OK", "/f"))
proofs = LI.seat_gate(proofs, probe=claude, native=True, codex_probe=codex)
lines = LI.seat_lines(proofs)
print("\n".join(lines))
os.makedirs(os.path.join(evd, "loop-intake"), exist_ok=True)
with open(os.path.join(evd, "loop-intake", "CURRENT.json"), "w") as fh:
    json.dump({"verdict": "NOT READY" if lines else "READY", "owner_words": words}, fh)
if lines:
    print("INTAKE NOT READY")
    sys.exit(1)
with open(os.path.join(evd, "loop-intake", "launch-env.sh"), "w") as fh:
    fh.write("export TEST_FROM_ENV=1\n")
print("INTAKE READY")
'''

STUB_DRIVER = '''#!/bin/bash
RUN="$BROTHER_RUNS_ROOT/run-20990101-000000-$$"
mkdir -p "$RUN"
echo "LOOP UNTIL $1 | gap $2s | log $TEST_EVD/loop.log"
sleep "${TEST_DRIVER_SLEEP:-3}"
'''

#: The holder's command line since S1.b: hold --run DIR -- COMMAND. The stub starts COMMAND (the driver) and ends with
#: it, as the real one does; a refusing holder starts nothing.
STUB_HOLDER = r'''import os, signal, subprocess, sys
evd = os.environ["TEST_EVD"]
if os.environ.get("TEST_HOLDER_MODE") == "fail":
    sys.stderr.write("holder refused: stub\n")
    sys.exit(1)
with open(os.path.join(evd, "holder-argv.txt"), "w") as fh:
    fh.write("\n".join(sys.argv[1:]))
a = sys.argv[1:]
if a[:2] != ["hold", "--run"] or len(a) < 5 or a[3] != "--":
    sys.stderr.write("Use brother-login hold --run DIR -- COMMAND.\n")
    sys.exit(1)
p = subprocess.Popen(a[4:])
signal.signal(signal.SIGTERM, lambda *_: (p.terminate(), sys.exit(143)))
sys.exit(p.wait())
'''

STUB_GRANT = r'''import json, os, sys
with open(os.path.join(os.environ["TEST_EVD"], "grant-argv.json"), "w") as fh:
    json.dump(sys.argv[1:], fh)
sys.exit(int(os.environ.get("TEST_GRANT_RC", "0")))
'''


def _proof(transport, status="OK"):
    return {"status": status, "role": "worker", "model": "m-" + transport, "transport": transport, "program": "/p",
            "version": "1", "cause": "fixture", "cached": False, "binding": {"transport": transport}}


def _rows(run_dir):
    path = os.path.join(run_dir, "notify-request.jsonl")
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return [json.loads(l) for l in fh if l.strip()]


def process_table_unreadable():
    """'' when ps can read this process, else why not. Inside the landing box (close_unit, land_batch.boxed) ps is
    refused, and stop_loop.sh then answers NO-DATA and signals nothing: a stop case there is NO-DATA, never a pass."""
    try:
        r = subprocess.run(["ps", "-o", "pid=", "-p", str(os.getpid())], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return "ps cannot run here (%s)" % type(exc).__name__
    return "" if r.returncode == 0 and r.stdout.strip() == str(os.getpid()) else "ps answered %d" % r.returncode


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class SeatGateInProcess(unittest.TestCase):
    """seat_gate, codex_seat and the intake's prepare: the gate's own entry and the intake's."""

    def setUp(self):
        self.saved = os.environ.get("BROTHER_LOOP_TOKEN")
        self.addCleanup(self._restore)

    def _restore(self):
        if self.saved is None:
            os.environ.pop("BROTHER_LOOP_TOKEN", None)
        else:
            os.environ["BROTHER_LOOP_TOKEN"] = self.saved

    def test_a_signed_out_claude_refuses_the_arm(self):
        os.environ["BROTHER_LOOP_TOKEN"] = "on"
        out = LI.seat_gate([_proof("claude")], probe=lambda program: ("REFUSED", "Not logged in"), native=True,
                           codex_probe=lambda: ("OK", "/f"))
        self.assertEqual(out[0]["status"], "FAIL")
        self.assertIn("brother-login", out[0]["remedy"])
        lines = LI.seat_lines(out)
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("SEAT     claude signed out:"), lines)
        self.assertIn("brother-login", lines[0])

    def test_a_signed_out_codex_refuses_the_arm(self):
        out = LI.seat_gate([_proof("codex")], probe=lambda program: ("OK", "ok"), native=True,
                           codex_probe=lambda: ("FAIL", "codex login: /fixture/auth.json is missing"))
        self.assertEqual(out[0]["status"], "FAIL")
        self.assertIsNone(out[0]["binding"])
        self.assertIn("codex login", out[0]["remedy"])
        self.assertEqual([l.split()[1] for l in LI.seat_lines(out)], ["codex"])

    def test_the_default_codex_probe_is_the_adapters_on_a_signed_out_home(self):
        """Both hosts: the real CodexAdapter.login_probe answers for a CODEX_HOME with no auth file, then with one."""
        home = tempfile.mkdtemp(prefix="test-seat-codex-")
        self.addCleanup(TSR._remove_tree, home)
        saved = os.environ.get("CODEX_HOME")
        os.environ["CODEX_HOME"] = home
        try:
            out = LI.seat_gate([_proof("codex")], native=False)
            self.assertEqual(out[0]["status"], "FAIL", out)
            self.assertIn(os.path.join(home, "auth.json"), out[0]["cause"])
            with open(os.path.join(home, "auth.json"), "w") as fh:
                fh.write('{"tokens": {}}')
            self.assertEqual(LI.seat_gate([_proof("codex")], native=False)[0]["status"], "OK")
        finally:
            if saved is None:
                os.environ.pop("CODEX_HOME", None)
            else:
                os.environ["CODEX_HOME"] = saved

    def test_an_unreadable_codex_seat_state_refuses(self):
        def boom():
            raise RuntimeError("probe broke")
        out = LI.seat_gate([_proof("codex")], native=False, codex_probe=boom)
        self.assertEqual(out[0]["status"], "FAIL")
        self.assertIn("could not be probed", out[0]["cause"])

    def test_both_signed_in_leave_every_proof_ok(self):
        out = LI.seat_gate([_proof("claude"), _proof("codex"), _proof("bridge")], probe=lambda program: ("OK", "ok"),
                           native=True, codex_probe=lambda: ("OK", "/f"))
        self.assertEqual([p["status"] for p in out], ["OK", "OK", "OK"])
        self.assertEqual(LI.seat_lines(out), [])

    def test_prepare_prints_the_seat_line_and_is_not_ready(self):
        """The intake's own entry: a reach probe through seat_gate, the SEAT line in prepare's lines, NOT READY."""
        import test_intake_parallel as TIP

        def reach(plan):
            proofs = [dict(_proof("codex" if r == "worker" else "claude"), role=r, model=m, binding=None) for r, m in plan]
            return LI.seat_gate(proofs, probe=lambda program: ("OK", "ok"), native=False,
                                codex_probe=lambda: ("FAIL", "codex login: /fixture/auth.json is missing"))
        rec, lines = LI.prepare({"finisher": "strong"}, EVENING, 10.0, None, TIP._ROLES, TIP._REG,
                                dict(TIP._good(), reach=reach), env={})
        self.assertEqual(rec["verdict"], "NOT READY")
        self.assertTrue(any(l.startswith("SEAT     codex signed out:") and "codex login" in l for l in lines), lines)


class NotifyAndReboot(unittest.TestCase):
    """notify_once and reboot_note, every edge the spec names."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="test-seat-notify-"))
        self.addCleanup(TSR._remove_tree, self.tmp)

    def test_notify_once_writes_one_row_per_key(self):
        run = os.path.join(self.tmp, "run\nwith a newline")
        os.makedirs(run)
        self.assertTrue(LI.notify_once(run, "seat-signed-out:codex", "seat", 10.0))
        self.assertFalse(LI.notify_once(run, "seat-signed-out:codex", "seat", 11.0))
        self.assertTrue(LI.notify_once(run, "seat-signed-out:claude", "seat", 12.0))
        self.assertEqual([(r["key"], r["step"], r["at"]) for r in _rows(run)],
                         [("seat-signed-out:codex", "seat", 10.0), ("seat-signed-out:claude", "seat", 12.0)])

    def test_notify_once_into_a_file_raises(self):
        afile = os.path.join(self.tmp, "afile")
        TSR._write(afile, "x")
        with self.assertRaises(OSError):
            LI.notify_once(afile, "k", "seat", 1.0)

    def _rec(self, name, body, mtime):
        path = os.path.join(self.tmp, name)
        TSR._write(path, body if isinstance(body, str) else json.dumps(body))
        os.utime(path, (mtime, mtime))
        return path

    def test_reboot_note_edges(self):
        self.assertEqual(LI.reboot_note(os.path.join(self.tmp, "absent"), "B"), "")          # empty
        self.assertEqual(LI.reboot_note(self.tmp, "B"), "")                                 # no record
        self._rec("launch-1.json", {"stamp": "S1", "boot": "A"}, 100)
        self.assertEqual(LI.reboot_note(self.tmp, "A"), "")                                 # exactly one, equal
        self.assertEqual(LI.reboot_note(self.tmp, "B"), "the last run S1 was killed by a reboot at B")
        self._rec("launch-2.json", {"stamp": "S2", "boot": "B"}, 200)
        self.assertEqual(LI.reboot_note(self.tmp, "B"), "")                                 # many: the newest wins
        self._rec("launch-3.json", "{not json", 300)
        note = LI.reboot_note(self.tmp, "C")
        self.assertIn("launch-3.json does not parse", note)                                 # corrupt: skipped, named
        self.assertIn("the last run S2 was killed by a reboot at C", note)
        self._rec("launch-4.json", {"stamp": "S4"}, 400)
        note = LI.reboot_note(self.tmp, "C")
        self.assertIn("boot not recorded", note)                                            # unknown: never a reboot
        self.assertNotIn("killed", note)
        self._rec("launch-5.json", {"stamp": "S5", "boot": "x" * (4 * 1024 * 1024)}, 500)
        note = LI.reboot_note(self.tmp, "C")
        self.assertLess(len(note), 1000)                                                    # a 4 MB boot field
        self.assertIn("launch-5.json is over 1 MiB, skipped", note)
        self.assertIn("cannot be told", LI.reboot_note(self.tmp, ""))                       # the boot now unreadable
        afile = os.path.join(self.tmp, "launch-1.json")
        self.assertIn("cannot be read", LI.reboot_note(afile, "C"))                         # records folder is a file


class LaunchFixture(unittest.TestCase):
    """The launcher fixture, no cases of its own: a throwaway git checkout, evidence folder, runs root and stubs
    (scripts/loop/test_login_hold_wired.py reuses it with the real holder)."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="test-seat-launch-"))
        self.addCleanup(TSR._remove_tree, self.tmp)
        self.home = os.path.join(self.tmp, "home")
        self.evd = os.path.join(self.tmp, "evidence")
        self.runs = os.path.join(self.tmp, "runs")
        for d in (self.home, self.evd, self.runs, os.path.join(self.home, ".claude", "evidence")):
            os.makedirs(d)
        bare, self.repo = os.path.join(self.tmp, "upstream.git"), os.path.join(self.tmp, "launch")
        env = self._git_env()
        TSR._git(self.tmp, env, "init", "-q", "--bare", bare)
        TSR._git(self.tmp, env, "init", "-q", self.repo)
        TSR._git(self.repo, env, "checkout", "-q", "-b", "main")
        for k, v in (("user.email", "t@example.invalid"), ("user.name", "t"), ("commit.gpgsign", "false")):
            TSR._git(self.repo, env, "config", k, v)
        TSR._write(os.path.join(self.repo, "README"), "fixture\n")
        TSR._git(self.repo, env, "add", "README")
        TSR._git(self.repo, env, "commit", "-q", "-m", "fixture")
        TSR._git(self.repo, env, "remote", "add", "hub", bare)
        TSR._git(self.repo, env, "push", "-q", "-u", "hub", "main:main")
        self.stubs = {}
        for name, body, ex in (("stub_intake.py", STUB_INTAKE, False), ("stub_driver.sh", STUB_DRIVER, False),
                               ("stub_live.sh", TSR.STUB_LIVE, False), ("stub_holder.py", STUB_HOLDER, False),
                               ("stub_grant.py", STUB_GRANT, False), ("fakebin", "#!/bin/sh\nexit 0\n", True),
                               # 1.1.7 LR1.d: the landing canary step of start_run.sh, stood in
                               ("stub_canary.sh", TSR.STUB_CANARY, False)):
            self.stubs[name] = os.path.join(self.tmp, name)
            TSR._write(self.stubs[name], body, executable=ex)
        self.addCleanup(self._reap_holders)

    def _git_env(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(HOME=self.home, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.path.join(self.home, ".gitconfig"))
        return env

    def _env(self, **extra):
        env = self._git_env()
        env.update(START_RUN_EVIDENCE=self.evd, START_RUN_INTAKE=self.stubs["stub_intake.py"],
                   START_RUN_DRIVER=self.stubs["stub_driver.sh"], START_RUN_LIVE_CHECK=self.stubs["stub_live.sh"],
                   START_RUN_HOLDER=self.stubs["stub_holder.py"], START_RUN_GRANT=self.stubs["stub_grant.py"],
                   START_RUN_CANARY=self.stubs["stub_canary.sh"],
                   START_RUN_TIMING_WAIT_S="0", START_RUN_BOOTTIME="boot A",
                   TEST_EVD=self.evd, TEST_LOOP_DIR=HERE, BROTHER_RUNS_ROOT=self.runs,
                   BROTHER_CLAUDE_BIN=self.stubs["fakebin"], BROTHER_CODEX_BIN=self.stubs["fakebin"],
                   BROTHER_POWER_SOURCE="AC Power")
        for k in ("BROTHER_POWER_WINDOW_S", "BROTHER_LOOP_TOKEN", "BROTHER_RUN_DIR"):
            env.pop(k, None)
        for k, v in extra.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v
        return env

    def launch(self, *more, **extra):
        p = subprocess.run(["bash", SCRIPT, "--deadline", DATED, "--budget-usd", "5"] + list(more), cwd=self.repo,
                           env=self._env(**extra), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=90)
        return p.returncode, p.stdout

    def _holder_pids(self):
        out = []
        for d in os.listdir(self.runs):
            f = os.path.join(self.runs, d, "login-holder.pid")
            if os.path.exists(f):
                with open(f) as fh:
                    out.append(int(fh.read().split()[0]))
        return out

    def _reap_holders(self):
        for pid in self._holder_pids():
            try:
                cmd = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], stdout=subprocess.PIPE,
                                     universal_newlines=True).stdout
            except OSError:
                cmd = "stub_holder.py"   # no ps (the landing box): the pid file is this fixture's own, written seconds ago
            if "stub_holder.py" in cmd:
                try:
                    os.killpg(pid, signal.SIGKILL)   # the holder leads the run's group: its driver goes with it
                except OSError:
                    pass  # sbe: allow-silent already gone, the state this cleanup wants

    def assertRefused(self, result, step, why):
        rc, out = result
        self.assertEqual(rc, 2, out)
        self.assertIn("START REFUSED [%s]" % step, out)
        self.assertIn(why, out)
        self.assertNotIn("STARTED", out)


class LauncherAndStop(LaunchFixture):
    """start_run.sh and stop_loop.sh at their entry points, in a throwaway git fixture and evidence folder."""

    def test_a_signed_out_claude_refuses_the_arm(self):
        run = os.path.join(self.tmp, "named-run")
        result = self.launch(TEST_SEATS="claude-out", BROTHER_LOOP_TOKEN="on", BROTHER_RUN_DIR=run)
        self.assertRefused(result, "seat", "claude signed out")
        self.assertIn("brother-login", result[1])
        self.assertEqual([r["key"] for r in _rows(run)], ["seat-signed-out:claude"])

    def test_a_signed_out_codex_refuses_the_arm(self):
        result = self.launch(TEST_SEATS="codex-out")
        self.assertRefused(result, "seat", "codex signed out")
        self.assertIn("codex login", result[1])
        days = [d for d in os.listdir(os.path.join(self.evd, "loop-starts")) if d.startswith("notify-")]
        self.assertEqual(len(days), 1, days)   # no run folder named: the launcher's own day folder
        self.assertEqual([r["key"] for r in _rows(os.path.join(self.evd, "loop-starts", days[0]))],
                         ["seat-signed-out:codex"])

    def test_both_signed_in_start_and_no_second_row(self):
        run = os.path.join(self.tmp, "named-run")
        self.assertRefused(self.launch(TEST_SEATS="codex-out", BROTHER_RUN_DIR=run), "seat", "codex signed out")
        self.assertRefused(self.launch(TEST_SEATS="codex-out", BROTHER_RUN_DIR=run), "seat", "codex signed out")
        self.assertEqual(len(_rows(run)), 1, _rows(run))
        rc, out = self.launch(TEST_SEATS="in", BROTHER_RUN_DIR=run)
        self.assertEqual(rc, 0, out)
        self.assertIn("STARTED", out)
        self.assertEqual(len(_rows(run)), 1, _rows(run))

    def test_a_reboot_is_named_at_the_next_launch(self):
        rc, out = self.launch(TEST_SEATS="in", START_RUN_BOOTTIME="boot A")
        self.assertEqual(rc, 0, out)
        starts = os.path.join(self.evd, "loop-starts")
        recs = [n for n in os.listdir(starts) if n.startswith("launch-") and n.endswith(".json")]
        self.assertEqual(len(recs), 1, recs)
        with open(os.path.join(starts, recs[0])) as fh:
            self.assertEqual(json.load(fh)["boot"], "boot A")
        rc, out = self.launch(TEST_SEATS="in", START_RUN_BOOTTIME="boot A")
        self.assertNotIn("killed by a reboot", out)
        rc, out = self.launch(TEST_SEATS="codex-out", START_RUN_BOOTTIME="boot B")
        self.assertIn("killed by a reboot at boot B", out)
        marked = []
        for n in sorted(os.listdir(starts)):
            if n.startswith("launch-"):
                with open(os.path.join(starts, n)) as fh:
                    marked.append(json.load(fh))
        self.assertEqual([m.get("delivered") for m in marked].count(False), 1, marked)

    def test_the_holder_is_started_and_reaped(self):
        why = process_table_unreadable()
        if why:
            self.skipTest("NO-DATA: %s, so stop_loop.sh reads no process and signals nothing" % why)
        rc, out = self.launch(TEST_SEATS="in", BROTHER_LOOP_TOKEN="on", TEST_DRIVER_SLEEP="20")
        self.assertEqual(rc, 0, out)
        self.assertIn("OK   holder", out)
        pids = self._holder_pids()
        self.assertEqual(len(pids), 1, out)
        self.assertTrue(_alive(pids[0]))
        with open(os.path.join(self.evd, "holder-argv.txt")) as fh:
            argv = fh.read().split("\n")
        self.assertEqual(argv[:2], ["hold", "--run"])
        self.assertEqual(argv[3], "--", "the holder starts the driver (S1.b)")
        self.assertIn(self.stubs["stub_driver.sh"], argv, "and the driver is its command")
        p = subprocess.run(["bash", STOP, "--reason", "test"],
                           env=dict(self._env(), STOP_LOOP_ONLY=self.tmp, STOP_DRIVER_GRACE="2"),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=60)
        self.assertIn("login holder", p.stdout)
        deadline = time.time() + 5
        while _alive(pids[0]) and time.time() < deadline:
            time.sleep(0.1)
        self.assertFalse(_alive(pids[0]), p.stdout)

    def test_a_holder_that_fails_refuses_started(self):
        result = self.launch(TEST_SEATS="in", BROTHER_LOOP_TOKEN="on", TEST_HOLDER_MODE="fail", TEST_DRIVER_SLEEP="20")
        self.assertRefused(result, "holder", "holder refused: stub")

    def test_no_token_lane_no_holder(self):
        rc, out = self.launch(TEST_SEATS="in")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self._holder_pids(), [])

    def test_the_grant_gets_the_words(self):
        rc, out = self.launch("--grant-tokens", "5000000", TEST_SEATS="in")
        self.assertEqual(rc, 0, out)
        with open(os.path.join(self.evd, "grant-argv.json")) as fh:
            argv = json.load(fh)
        with open(os.path.join(self.evd, "loop-intake", "CURRENT.json")) as fh:
            words = json.load(fh)["owner_words"]
        self.assertTrue(words)
        self.assertEqual(argv[argv.index("--words") + 1], words)
        self.assertEqual(argv[argv.index("--tokens") + 1], "5000000")
        self.assertEqual(argv[argv.index("--project-slug") + 1], self.repo.replace("/", "-").replace(".", "-"))
        self.assertTrue(argv[argv.index("--until") + 1].startswith(DATED.replace(" ", "T")), argv)

    def test_a_refused_grant_refuses_the_start(self):
        result = self.launch("--grant-tokens", "5000000", TEST_SEATS="in", TEST_GRANT_RC="2")
        self.assertRefused(result, "grant", "loop_budget_grant")

    def test_no_grant_asked_no_grant_called(self):
        rc, out = self.launch(TEST_SEATS="in")
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(os.path.join(self.evd, "grant-argv.json")))


def load_tests(loader, tests, pattern):
    """The unit's one done check command runs the wiring's end to end suite too (test_login_hold_wired.py): S2's
    holder step is proven only where the real holder runs, and the plan's done_check field must be the check."""
    import test_login_hold_wired
    tests.addTests(loader.loadTestsFromModule(test_login_hold_wired))
    return tests


if __name__ == "__main__":
    unittest.main()
