"""JW2.a: what scripts/jev_start_refusal.py and the refuse() line of
scripts/loop/start_run.sh must keep true.

In process, the cases drive the REAL seam (jev_door.ask_flake through
jev_checks.check_gate_failure_flake) with an injected runner that returns a
bridge shaped answer, in a throwaway HOME and BROTHER_JEV_STATE_DIR set before
any product import, with the private content gate's term list replaced by a
fixture. The shell cases run the launcher at its entry point: the real one,
in place, with an argument refusal under an empty HOME (it refuses before any
git or tool step), and copies of it beside a helper that is absent, exits 9,
sleeps or records the order. The order case reaches a refusal AFTER the hold
was lifted, through the launcher's own seams (START_RUN_EVIDENCE,
START_RUN_INTAKE, START_RUN_LIVE_CHECK, BROTHER_POWER_SOURCE) in a throwaway
git fixture. No case calls a model, reads this machine's real home, or
starts ps.

Python 3.9 and 3.13, standard library only, no network.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

_HOME = tempfile.mkdtemp(prefix="jev-start-refusal-home-")
os.environ["HOME"] = _HOME
os.environ["BROTHER_JEV_STATE_DIR"] = os.path.join(_HOME, "jev-state")
for _var in ("BROTHER_TRANSPORTS", "BROTHER_DECISION_BRIDGE", "BROTHER_JEV_SEAMS_OFF"):
    os.environ.pop(_var, None)
os.makedirs(os.path.join(_HOME, ".claude"))
with open(os.path.join(_HOME, ".claude", "coe-outside-gate-terms.json"), "w", encoding="utf-8") as _fh:
    json.dump({"vendor-fixture": ["ACMEWIDGET"]}, _fh)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOOP = os.path.join(HERE, "loop")
LAUNCHER = os.path.join(LOOP, "start_run.sh")
sys.path.insert(0, HERE)
sys.path.insert(0, LOOP)

import jev_checks  # noqa: E402
import jev_decide  # noqa: E402
import jev_registry  # noqa: E402
import jev_seam  # noqa: E402
import jev_start_refusal  # noqa: E402
import test_start_run as tsr  # noqa: E402  its stubs and git helpers, never its test class

TERMS = {"vendor-fixture": ["ACMEWIDGET"]}
SHADOW = {"modes": {"J065": "shadow"}}
TRANSIENT = ("second opinion (recorded, not acted on): this refusal reads as a transient condition "
             "(p=0.91); the refusal and its exit code stand")
REAL = ("second opinion (recorded, not acted on): this refusal reads as a real block "
        "(p=0.07); the refusal and its exit code stand")
NO_STEP = "second opinion: NO-DATA (not asked: no refusal step given)"
BOGUS = "START REFUSED [args]: unknown argument --bogus (see the usage at the top of this file)"


def _registry():
    for path in (os.path.join(ROOT, "data", "jev-registry.json"),
                 os.path.join(ROOT, "bundle", "runtime", "data", "jev-registry.json"),
                 os.path.join(HERE, "fixtures", "jev-registry-fixture.json")):
        if os.path.isfile(path):
            return jev_registry.load(path)
    raise AssertionError("no Jev registry copy in this tree")


REGISTRY = _registry()


class Runner(object):
    """A bridge shaped answer for whatever questions it is handed; records each call."""

    def __init__(self, prob, returncode=0, gate=None):
        self.prob = prob
        self.returncode = returncode
        self.gate = gate
        self.calls = []

    def __call__(self, argv, stdin_text):
        self.calls.append(stdin_text)
        if self.gate is not None:
            self.gate.wait(10)
        if self.returncode:
            return self.returncode, "", "fixture bridge failure"
        payload = json.loads(stdin_text)
        answers = {qid: {"noul": self.prob, "confidence": self.prob} for qid in payload["questions"]}
        return 0, json.dumps({"model": jev_decide.MODEL_PREFIX + "-fixture",
                              "answers": answers, "usage": {"cost": 0.0}}), ""


class Recorder(object):
    def __init__(self):
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("must not be called")


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jev-start-refusal-")
        self.ledger = os.path.join(self.root, "ledger")
        patcher = mock.patch.object(jev_decide, "_default_runner", Recorder())
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        jev_seam.drain(timeout=10)
        shutil.rmtree(self.root, ignore_errors=True)

    def ask(self, step, message, runner=None, **kw):
        kw.setdefault("seams_config", SHADOW)
        kw.setdefault("registry", REGISTRY)
        kw.setdefault("gate_terms", TERMS)
        kw.setdefault("wait_s", 10.0)
        return jev_start_refusal.refusal_second_opinion(step, message, ledger_dir=self.ledger,
                                                        runner=runner, **kw)

    def sent(self, runner):
        """The refusal text the one bridge call carried."""
        self.assertEqual(len(runner.calls), 1, runner.calls)
        return runner.calls[0]


class Answers(Base):
    """REQ-JW2-01: one labelled line for each kind of answer."""

    def test_transient(self):
        runner = Runner(0.91)
        self.assertEqual(self.ask("live", "the live check could not answer (exit 3)", runner),
                         ("OK", TRANSIENT))
        self.assertIn("START REFUSED [live]: the live check could not answer (exit 3)", self.sent(runner))

    def test_real_block(self):
        self.assertEqual(self.ask("tree", "2 changed path(s); commit and push them", Runner(0.07)),
                         ("OK", REAL))

    def test_unsure_is_no_opinion(self):
        state, line = self.ask("tree", "1 commit(s) behind hub/main", Runner(0.5))
        self.assertEqual(state, "OK")
        self.assertEqual(line, "second opinion: no opinion (Jev was unsure or could not answer (abstain))")

    def test_a_bridge_that_fails_is_no_opinion(self):
        state, line = self.ask("tree", "cannot fetch hub", Runner(0.9, returncode=1))
        self.assertEqual(state, "OK")
        self.assertTrue(line.startswith("second opinion: no opinion (Jev was unsure or could not answer"), line)

    def test_a_hung_bridge_is_no_data_within_the_wait(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        began = time.monotonic()
        state, line = self.ask("live", "the live check could not answer", Runner(0.9, gate=gate), wait_s=0.5)
        self.assertLess(time.monotonic() - began, 5)
        self.assertEqual(state, "NO-DATA")
        self.assertEqual(line, "second opinion: NO-DATA (no answer within 0.5 s; it is recorded if it lands)")


class Edges(Base):
    """The spec's edge list."""

    def test_empty_step_is_no_data_and_asks_nothing(self):
        runner = Runner(0.9)
        self.assertEqual(self.ask("", "x", runner), ("NO-DATA", NO_STEP))
        self.assertEqual(self.ask("   ", "x", runner), ("NO-DATA", NO_STEP))
        self.assertEqual(runner.calls, [])

    def test_empty_message_still_asks(self):
        runner = Runner(0.91)
        self.assertEqual(self.ask("args", "", runner), ("OK", TRANSIENT))
        self.assertIn("START REFUSED [args]:", self.sent(runner))

    def test_a_control_character_in_the_step_is_unreadable(self):
        runner = Runner(0.91)
        self.ask("tree\nINJECTED", "x", runner)
        sent = self.sent(runner)
        self.assertIn("START REFUSED [(unreadable step)]: x", sent)
        self.assertNotIn("INJECTED", sent)

    def test_a_nul_byte_is_no_data(self):
        runner = Runner(0.9)
        state, line = self.ask("tree", "bad\x00text", runner)
        self.assertEqual(state, "NO-DATA")
        self.assertTrue(line.startswith("second opinion: NO-DATA (nothing to ask"), line)
        self.assertEqual(runner.calls, [])

    def test_a_long_message_is_cut(self):
        runner = Runner(0.91)
        self.ask("tree", "a" * 399 + "Z" + "Q" * 200, runner)
        sent = self.sent(runner)
        self.assertIn("START REFUSED [tree]: " + "a" * 300, sent)
        self.assertNotIn("Q", sent)

    def test_the_folded_intake_rows_are_kept(self):
        runner = Runner(0.07)
        message = ("not READY (exit 1, last verdict: INTAKE NOT READY); the refusing rows: "
                   "REFUSED budget: over REFUSED scope: empty NO-DATA plan_lint: no clock (full: intake.log)")
        self.assertEqual(self.ask("intake", message, runner), ("OK", REAL))
        sent = self.sent(runner)
        for row in ("REFUSED budget: over", "REFUSED scope: empty", "NO-DATA plan_lint: no clock"):
            self.assertIn(row, sent)

    def test_the_same_refusal_twice_is_two_attempts(self):
        first, second = Runner(0.91), Runner(0.91)
        self.assertEqual(self.ask("live", "x", first), ("OK", TRANSIENT))
        self.assertEqual(self.ask("live", "x", second), ("OK", TRANSIENT))
        self.assertEqual((len(first.calls), len(second.calls)), (1, 1))


class AsksNothing(Base):
    """REQ-JW2-03, REQ-JW2-04, REQ-JW2-05."""

    def test_an_interrupted_launch_asks_nothing(self):
        runner = Runner(0.91)
        with mock.patch.object(jev_checks, "check_gate_failure_flake", Recorder()) as rec:
            self.assertEqual(self.ask("signal", "interrupted", runner), ("OK", ""))
        self.assertEqual(rec.calls, 0)
        self.assertEqual(runner.calls, [])

    def test_a_private_term_is_never_sent(self):
        runner = Runner(0.91)
        with mock.patch.object(jev_checks, "check_gate_failure_flake", Recorder()) as rec:
            state, line = self.ask("tree", "cannot fetch the ACMEWIDGET remote", runner)
        self.assertEqual(state, "NO-DATA")
        self.assertTrue(line.startswith("second opinion: NO-DATA (not asked: "), line)
        self.assertEqual(rec.calls, 0)
        self.assertEqual(runner.calls, [])

    def test_j065_off_asks_nothing_and_says_so(self):
        runner = Runner(0.91)
        state, line = self.ask("live", "x", runner, seams_config={"modes": {"J065": "off"}})
        self.assertEqual((state, line),
                         ("NO-DATA", "second opinion: NO-DATA (not asked: J065 is off in the Jev seams config)"))
        self.assertEqual(runner.calls, [])

    def test_a_missing_term_list_is_no_data(self):
        runner = Runner(0.91)
        absent = os.path.join(self.root, "absent-terms.json")
        with mock.patch.object(jev_start_refusal.jev_door.coe_outside_gate, "DEFAULT_TERMS_PATH", absent):
            state, line = self.ask("live", "x", runner, gate_terms=None)
        self.assertEqual(state, "NO-DATA")
        self.assertTrue(line.startswith("second opinion: NO-DATA (not asked: "), line)
        self.assertEqual(runner.calls, [])


class Main(Base):
    """main() returns 0 in every case and prints one line or nothing."""

    def main(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = jev_start_refusal.main(argv)
        return rc, out.getvalue()

    def test_no_argument(self):
        self.assertEqual(self.main([]), (0, NO_STEP + "\n"))

    def test_an_interrupted_launch_prints_nothing(self):
        self.assertEqual(self.main(["signal", "interrupted"]), (0, ""))

    def test_an_internal_error_still_returns_0(self):
        with mock.patch.object(jev_start_refusal.jev_door, "ask_flake", side_effect=RuntimeError("boom")):
            self.assertEqual(self.main(["tree", "x"]),
                             (0, "second opinion: NO-DATA (the helper failed: RuntimeError)\n"))

    def test_undecodable_argv_is_replaced(self):
        raw = b"bad \xff byte".decode("utf-8", "surrogateescape")
        with mock.patch.object(jev_start_refusal, "refusal_second_opinion",
                               return_value=("OK", "seen")) as ask:
            self.assertEqual(self.main(["tree", raw]), (0, "seen\n"))
        self.assertEqual(ask.call_args[0], ("tree", "bad � byte"))


class Static(unittest.TestCase):
    """REQ-JW2-06: the shared door core, never its own consult."""

    def test_reads_through_the_door_core(self):
        with open(jev_start_refusal.__file__, "r", encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn("jev_door.ask_flake(", source)
        self.assertNotIn("consult", source)


def _clean_env(home, **extra):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("BROTHER_", "START_RUN_", "GIT_", "PYTHON"))}
    env.update(HOME=home, STOP_LOOP_ONLY="1")
    env.update(extra)
    return env


class Shell(unittest.TestCase):
    """REQ-JW2-01, REQ-JW2-02, REQ-JW2-03 at the launcher's entry point."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="jev-start-refusal-sh-"))
        self.addCleanup(tsr._remove_tree, self.tmp)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)

    def run_sh(self, script, args, cwd=None, **extra):
        p = subprocess.run(["bash", script] + list(args), cwd=cwd or self.tmp,
                           env=_clean_env(self.home, **extra), stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, timeout=60)
        return p.returncode, p.stdout.splitlines()

    def copy(self, helper=None):
        """A copy of the launcher in <tmp>/kit/loop beside <tmp>/kit/jev_start_refusal.py (or none)."""
        kit = os.path.join(self.tmp, "kit")
        os.makedirs(os.path.join(kit, "loop"))
        for name in ("start_run.sh", "power_rule.py"):
            shutil.copy(os.path.join(LOOP, name), os.path.join(kit, "loop", name))
        if helper is not None:
            tsr._write(os.path.join(kit, "jev_start_refusal.py"), helper)
        return os.path.join(kit, "loop", "start_run.sh")

    def test_the_real_launcher_prints_the_refusal_then_one_no_data_line(self):
        rc, lines = self.run_sh(LAUNCHER, ["--bogus"])
        self.assertEqual(rc, 2, lines)
        self.assertEqual(len(lines), 2, lines)
        self.assertEqual(lines[0], BOGUS)
        self.assertTrue(lines[1].startswith("second opinion: NO-DATA (not asked: "), lines)
        self.assertEqual(os.listdir(self.home), [])

    def test_no_helper(self):
        self.assertEqual(self.run_sh(self.copy(), ["--bogus"]), (2, [BOGUS]))

    def test_a_helper_that_exits_9(self):
        self.assertEqual(self.run_sh(self.copy("import sys\nsys.exit(9)\n"), ["--bogus"]), (2, [BOGUS]))

    def test_a_helper_that_hangs_then_is_killed_mid_wait(self):
        """bash itself may report the kill on its own stderr; the refusal line and the exit code stand."""
        helper = "import os, signal, time\ntime.sleep(0.5)\nos.kill(os.getpid(), signal.SIGKILL)\n"
        rc, lines = self.run_sh(self.copy(helper), ["--bogus"])
        self.assertEqual((rc, lines[:1]), (2, [BOGUS]), lines)
        self.assertEqual([line for line in lines if "second opinion" in line or "START" in line], [BOGUS])

    def test_a_helper_that_sleeps_past_its_wait(self):
        helper = "import time\ntime.sleep(1.5)\nprint('late')\n"
        self.assertEqual(self.run_sh(self.copy(helper), ["--bogus"]), (2, [BOGUS, "late"]))

    def test_the_controls_are_restored_before_the_helper_runs(self):
        """M-JW2-ORDER: the helper records whether the lifted hold is back when it runs."""
        helper = ("import os\nevd = os.environ['TEST_EVD']\n"
                  "print('HELPER hold %s' % ('back' if os.path.exists(os.path.join(evd, 'LOOP-HOLD.txt')) else 'missing'))\n")
        script = self.copy(helper)
        evd = os.path.join(self.tmp, "evidence")
        bare = os.path.join(self.tmp, "upstream.git")
        repo = os.path.join(self.tmp, "launch")
        os.makedirs(evd)
        git_env = _clean_env(self.home, GIT_CONFIG_NOSYSTEM="1",
                             GIT_CONFIG_GLOBAL=os.path.join(self.home, ".gitconfig"))
        tsr._git(self.tmp, git_env, "init", "-q", "--bare", bare)
        tsr._git(self.tmp, git_env, "init", "-q", repo)
        tsr._git(repo, git_env, "checkout", "-q", "-b", "main")
        for k, v in (("user.email", "t@example.invalid"), ("user.name", "t"), ("commit.gpgsign", "false")):
            tsr._git(repo, git_env, "config", k, v)
        tsr._write(os.path.join(repo, "README"), "fixture\n")
        tsr._git(repo, git_env, "add", "README")
        tsr._git(repo, git_env, "commit", "-q", "-m", "fixture")
        tsr._git(repo, git_env, "remote", "add", "hub", bare)
        tsr._git(repo, git_env, "push", "-q", "-u", "hub", "main:main")
        intake = os.path.join(self.tmp, "stub_intake.py")
        live = os.path.join(self.tmp, "stub_live.sh")
        fakebin = os.path.join(self.tmp, "fakebin")
        tsr._write(intake, tsr.STUB_INTAKE)
        tsr._write(live, tsr.STUB_LIVE)
        tsr._write(fakebin, "#!/bin/sh\nexit 0\n", executable=True)
        tsr._write(os.path.join(evd, "LOOP-HOLD.txt"), "held by a test\n")
        rc, lines = self.run_sh(
            script, ["--deadline", tsr.DATED, "--budget-usd", "5", "--lift-hold", "owner said go"], cwd=repo,
            GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.path.join(self.home, ".gitconfig"),
            START_RUN_EVIDENCE=evd, START_RUN_INTAKE=intake, START_RUN_LIVE_CHECK=live, TEST_EVD=evd,
            TEST_INTAKE_MODE="refuse", BROTHER_CLAUDE_BIN=fakebin, BROTHER_CODEX_BIN=fakebin,
            BROTHER_POWER_SOURCE="AC Power")
        self.assertEqual(rc, 2, lines)
        refused = [i for i, line in enumerate(lines) if line.startswith("START REFUSED [intake]")]
        self.assertEqual(len(refused), 1, lines)
        self.assertEqual(lines[refused[0] + 1:], ["HELPER hold back"], lines)
        self.assertIn("OK   controls restored exactly as found", lines[:refused[0]])


if __name__ == "__main__":
    unittest.main()
