"""M2-3 and M2-4 (U5, objections 9 and 10): the runner's straggler cut grades what is on disk at the cut and never
SIGKILLs a fan out that holds calls; a round whose every call was refused for the drain ends DRAINED, exit 5.

Entry point: `python3 unit_runner.py <unit> <sub> 1` as a subprocess. The real unit_runner.py bytes are copied into a
scratch bin beside real copies of bounded.py, fanout_verdict.py, loop_hold.py and plan_store.py, and small stubs for the modules
around the fan out (brief, grader, advisors), so the only code that decides anything below is the runner's own. The
fan out is a fake `plugin.runtime.brother.core.or_fanout` under the scratch code root (BROTHER_CODE_ROOT) that
reserves on a REAL openrouter_ledger in a scratch state root. A copy also sits under the launch worktree, so a
runner that starts the fan out from the wrong tree runs, and says where it ran. Nothing calls a model.

Timing: FANOUT_TIMEOUT_S shortens the fan out's per call timeout (1 s; 5 s in the TERM case), so the straggler wait
(twice the call timeout plus 60 s) ends 70 s after that fan out starts; the TERM case therefore takes about 80 s.
Run from the repository root: python3 -B scripts/test_runner_straggler_settles.py
"""
import glob, json, os, shutil, subprocess, sys, tempfile, textwrap, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
LOOP = os.path.join(HERE, "loop")
SCRATCH = os.path.join(os.path.expanduser("~"), ".claude", "brother-scratch")
#: The REAL loop modules every runner harness copies beside unit_runner.py (the rest are STUBS). ONE list: three harnesses
#: each kept their own and all three went red together when unit_runner began importing loop_roles (FX-11.6, 2026-10-02).
REAL_LOOP = ("unit_runner.py", "bounded.py", "fanout_verdict.py", "loop_hold.py", "plan_store.py", "diag_notes.py", "loop_switches.py",
             "loop_roles.py")

STUBS = {
    "probe_build.py": "import re\nclass G(object):\n    @staticmethod\n    def private_hits(text): return []\n"
                      "def lessons(): return ''\ndef classify(*a): return 'OK'\nREFUSAL_VALUE = re.compile('x^')\n",
    "brief_fit.py": "def fit(brief, rules, lessons, note): return brief + rules + (note or '')\n",
    "stage_log.py": "def enter(*a, **k): pass\ndef leave(*a, **k): pass\n",
    "repair_advisor.py": "def enabled(sub): return False\ndef advise(*a, **k): return None\n",
    "self_check.py": "def screen(wd): return 0, 0\n",
    "build_plan.py": "def enabled(sub): return False\ndef plan(*a, **k): return None\n",
    "grade_build.py": "def brief_screen_mode(environ=None): return 'off'\n",   # FX-09: unit_runner reads the brief screen switch
    "worker_mix.py": "DEFAULT_MIX = 'deepseek:1'\nTRANSPORT_PATIENCE = {'bridge': 1}\ndef parse(mix): return [('deepseek', 1)]\n"
                     "def arm_stats(unit_class=None): return {}\ndef picks(n, picks, known, stats=None): return picks\n"
                     "def patience(models, transports): return 1\n"
                     # FX-10: the one deadline names the runner calls, at today's values (the switch is off here)
                     "DISPATCH_FLOOR_S = 300\ndef one_deadline(env=None): return False\n"
                     "def measured(models, transports, registry=None): return {}\ndef patience_of(block): return 1\n"
                     "def findings(block): return []\n"
                     "def deadlines(pat, knob=420, grace=60, settle=30):\n"
                     "    t = max(knob, int(pat * 1.5)); return {'call': t, 'round': max(480, t), 'wave': t, 'reap': 2 * t + grace}\n",
    "ev_gate.py": "import os\nDEFAULT_VALUE = 5.0\ndef history(sub, since=0.0, exclude_run=None): return 0, 0\n"
                  "def should_continue(rounds, passes, round_cost=None, value=1.0):\n"
                  "    return (False, 'not worth another round') if os.environ.get('FAKE_EV_STOP') else (True, 'go')\n"
                  # FX-13.4: the runner prices through these two
                  "def round_cost_from_env(env=None): return None\n"
                  "def landing_value(open_subs, env=None): return DEFAULT_VALUE * (3.0 if open_subs <= 1 else 1.0)\n",
    "mix_advice.py": "RUNS = ''\ndef newest_runs(r, n): return []\ndef cheapest_valid(x): return None\ndef gather(r): return []\n",
    "model_router.py": "import os\nPUBLIC = 'public'\nclass Refused(Exception): pass\n"
                       "def registry(): return {'deepseek': {'transport': 'bridge'}}\n"
                       "def chain(kind, sens, pin=None, registry_arg=None): return ['deepseek']\n"
                       "def code_root():\n    v = os.environ.get('BROTHER_CODE_ROOT')\n"
                       "    if not v: raise Refused('a proof phase runs frozen code only: BROTHER_CODE_ROOT is not set')\n    return v\n",
    "unit_ledger.py": "def unit_classes(plan): return {}\n",
    "build_brief.py": "import sys\nopen(sys.argv[4], 'w').write('BRIEF\\n')\n",
    "brief_check.py": "import sys\nsys.exit(0)\n",
    "jev_brief.py": "import sys\nsys.exit(1)\n",
    # a COMPLETED FAIL by default: grade_one.sh's file per build closing exit=1 (unit_runner.judged reads it, 2026-10-03);
    # FAKE_GRADE_MODE=timeout sleeps past GRADE_TIMEOUT and writes nothing; =infra closes exit=3, the grader's own fault;
    # =mixed grades only the first build; =stale writes each grade under a name that matches no build
    "grade_lane.sh": ('#!/bin/sh\nls "$1/out" >> "$1/graded.txt"\necho "----" >> "$1/graded.txt"\n'
                      '[ "$FAKE_GRADE_MODE" = timeout ] && sleep 30\nmkdir -p "$1/grades"\n'
                      'c=1; [ "$FAKE_GRADE_MODE" = infra ] && c=3\n'
                      'i=0; for b in "$1"/out/*.json; do [ -e "$b" ] || continue; n=$(basename "$b" .json); n=${n%-build}; i=$((i+1)); [ "$FAKE_GRADE_MODE" = stale ] && n="$n-other"; [ "$FAKE_GRADE_MODE" = mixed ] && [ $i -gt 1 ] && continue; '
                      'printf "FAIL stub grader\\nexit=%s\\n" "$c" > "$1/grades/$n.txt"; done\necho "FAIL stub grader"\n'),
}

FAILURE_LEDGER = ("import os, sys\nwith open(os.environ['FAKE_FANOUT_RECORD'], 'a') as f:\n"
                  "    f.write('failure_ledger %s %s\\n' % (sys.argv[1], os.path.abspath(__file__)))\n")

FAKE_FANOUT = textwrap.dedent('''
    import json, os, signal, sys, time
    from plugin.runtime.brother.core import openrouter_ledger as L
    born = time.time()
    args = sys.argv[1:]
    jobs = json.load(open(args[0]))
    results = args[args.index("--results") + 1]
    state, mode = os.environ["BROTHER_OR_STATE_ROOT"], os.environ["FAKE_FANOUT_MODE"]
    def note(s):
        with open(os.environ["FAKE_FANOUT_RECORD"], "a") as f: f.write(s + "\\n")
    def build(job):
        os.makedirs(os.path.dirname(job["out"]), exist_ok=True)
        tmp = job["out"] + ".part"
        with open(tmp, "w") as f: json.dump({"edits": [], "tests": [], "id": job["id"]}, f)
        os.replace(tmp, job["out"])
    note("cwd %s" % os.getcwd()); note("pid %d" % os.getpid())
    if mode == "drain":
        json.dump([{"id": j["id"], "ok": False, "error": "DrainRefused: a call of 420 s would outlive the deadline"} for j in jobs], open(results, "w"))
        sys.exit(0)
    for job in jobs[:-1]:
        build(job)
    rid = L.reserve(state, 20, 0.02, jobs[-1]["id"], timeout_seconds=300)
    note("reserved %s" % rid)
    if mode == "ignores_term":
        signal.signal(signal.SIGTERM, lambda s, f: note("TERM %.1f" % (time.time() - born)))
        while True:
            time.sleep(1)
    time.sleep(6)
    build(jobs[-1])
    L.reconcile(state, rid, 0.01)
    note("finished")
    json.dump([{"id": j["id"], "ok": True} for j in jobs], open(results, "w"))
''')


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def tree(root):
    """A code tree holding the fake fan out, real ledger copies and the failure ledger stub."""
    core = os.path.join(root, "plugin", "runtime", "brother", "core")
    os.makedirs(core)
    for d in ("plugin", "plugin/runtime", "plugin/runtime/brother", "plugin/runtime/brother/core"):
        open(os.path.join(root, d, "__init__.py"), "w").close()
    with open(os.path.join(core, "or_fanout.py"), "w") as f: f.write(FAKE_FANOUT)
    shutil.copy(os.path.join(REPO, "plugin", "runtime", "brother", "core", "openrouter_ledger.py"), core)
    os.makedirs(os.path.join(root, "scripts", "loop"))
    shutil.copy(os.path.join(LOOP, "proof_ledger.py"), os.path.join(root, "scripts", "loop"))
    with open(os.path.join(root, "scripts", "failure_ledger.py"), "w") as f: f.write(FAILURE_LEDGER)


class Scenario(object):
    """One real runner run over the fake fan out. Fields: rc, out, run (its run folder), record (the fan out's and
    the failure ledger's notes), ledger (the OpenRouter ledger rows), code and wt (the two trees)."""

    def __init__(self, mode, workers=4, timeout=150, env_extra=None, drop=()):
        os.makedirs(SCRATCH, exist_ok=True)
        self.d = tempfile.mkdtemp(prefix="straggler-", dir=SCRATCH)
        self.code, self.wt, self.bin = (os.path.join(self.d, n) for n in ("code", "wt", "bin"))
        self.home, self.state = os.path.join(self.d, "home"), os.path.join(self.d, "state")
        os.makedirs(self.bin); os.makedirs(os.path.join(self.home, ".claude", "evidence")); os.makedirs(self.state)
        tree(self.code); tree(self.wt)
        for name in REAL_LOOP:
            shutil.copy(os.path.join(LOOP, name), self.bin)
        for name, body in STUBS.items():
            with open(os.path.join(self.bin, name), "w") as f: f.write(body)
        os.chmod(os.path.join(self.bin, "grade_lane.sh"), 0o755)
        os.makedirs(os.path.join(self.wt, "docs", "plan", "specs"))
        with open(os.path.join(self.wt, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json"), "w") as f:
            json.dump({"units": [{"id": "Z9", "state": "OPEN", "spec": "docs/plan/specs/Z9.md", "sub_units": ["Z9.1"],
                                  "evidence": "", "command_runners": []}]}, f)
        self.rec = os.path.join(self.d, "record.txt"); open(self.rec, "w").close()
        env = {k: v for k, v in os.environ.items() if not k.startswith(("BROTHER_", "GIT_")) and k not in ("RUNNER_HINT",)}
        env.update(HOME=self.home, BROTHER_CODE_ROOT=self.code, BROTHER_OR_STATE_ROOT=self.state, WORKERS_PER_ROUND=str(workers),
                   BROTHER_BUILD_PLAN_MODEL="off", BROTHER_REPAIR_ADVISOR_MODEL="off", FANOUT_TIMEOUT_S="1",
                   FAKE_FANOUT_RECORD=self.rec, FAKE_FANOUT_MODE=mode, PYTHONDONTWRITEBYTECODE="1",
                   LATE_WAIT_S="20")   # the straggler lands at 6 s; a fan out that never exits must not hold the round 240 s
        env.update(env_extra or {})
        for k in drop:
            env.pop(k, None)
        t0 = time.time()
        try:
            r = subprocess.run([sys.executable, "-B", os.path.join(self.bin, "unit_runner.py"), "Z9", "Z9.1", "1"], cwd=self.wt,
                               env=env, capture_output=True, text=True, timeout=timeout)
        except BaseException:
            self.cleanup()   # a runner killed by the timeout never reaps its fan out; the fake one that ignores TERM would live on
            raise
        self.seconds = time.time() - t0
        self.rc, self.out = r.returncode, r.stdout + r.stderr
        runs = glob.glob(os.path.join(self.home, ".claude", "evidence", "unit-runs", "Z9.1-*"))
        self.run = runs[0] if len(runs) == 1 else None
        self.record = _read(self.rec).splitlines()
        lp = os.path.join(self.state, "openrouter-ledger.jsonl")
        self.ledger = [json.loads(l) for l in _read(lp).splitlines()] if os.path.exists(lp) else []

    def status(self):
        return _read(os.path.join(self.run, "STATUS")) if self.run else ""

    def round0(self, *parts):
        return os.path.join(self.run, "round0", *parts)

    def cleanup(self):
        """Kill every fake fan out this scenario started that is still alive (by the pid it recorded), then remove it."""
        try:
            pids = [int(l.split()[1]) for l in _read(self.rec).splitlines() if l.startswith("pid ")]
        except (OSError, ValueError, IndexError):
            pids = []
        for pid in pids:
            try:
                os.kill(pid, 9)
            except OSError:
                pass
        shutil.rmtree(self.d, ignore_errors=True)


class TheEffortABPutsBothArmsInEveryRound(unittest.TestCase):
    """Owner 2026-09-27, "A/B effort": with BROTHER_EFFORT_AB the runner's jobs alternate the named efforts by build
    and round, so both arms work the same brief at the same moment; without it no job names an effort."""

    def efforts(self, env_extra):
        s = Scenario("finishes", env_extra=env_extra)
        try:
            with open(s.round0("jobs.json")) as fh:
                return [j.get("effort") for j in json.load(fh)]
        finally:
            s.cleanup()

    def test_the_arms_alternate_in_round_zero(self):
        self.assertEqual(self.efforts({"BROTHER_EFFORT_AB": "xhigh,high"}), ["xhigh", "high", "xhigh", "high"])

    def test_no_ab_names_no_effort_and_an_unknown_arm_is_dropped(self):
        self.assertEqual(self.efforts({}), [None, None, None, None])
        self.assertEqual(self.efforts({"BROTHER_EFFORT_AB": "xhigh,turbo"}), ["xhigh"] * 4)

    def calibrated(self, text):
        # ONE condition each: the calibration file's content; the run's own arms are xhigh,high throughout.
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d, True)
        cal = os.path.join(d, "loop-calibration.json")
        if text is not None:
            with open(cal, "w") as fh:
                fh.write(text)
        return self.efforts({"BROTHER_EFFORT_AB": "xhigh,high", "BROTHER_CALIBRATION_FILE": cal})

    def test_a_calibration_file_moves_the_arms_and_repeats_weight_them(self):
        self.assertEqual(self.calibrated('{"effort_ab": ["high", "high", "xhigh"]}'), ["high", "high", "xhigh", "high"])

    def test_an_absent_file_keeps_the_runs_arms(self):
        self.assertEqual(self.calibrated(None), ["xhigh", "high", "xhigh", "high"])

    def test_a_corrupt_file_keeps_the_runs_arms(self):
        self.assertEqual(self.calibrated('{"effort_ab": ['), ["xhigh", "high", "xhigh", "high"])

    def test_a_file_naming_no_known_effort_keeps_the_runs_arms(self):
        self.assertEqual(self.calibrated('{"effort_ab": ["turbo"]}'), ["xhigh", "high", "xhigh", "high"])
        self.assertEqual(self.calibrated('["high"]'), ["xhigh", "high", "xhigh", "high"])


class StragglerFinishes(unittest.TestCase):
    """Three builds at once and a fourth job holding a real RESERVE that finishes six seconds later."""

    @classmethod
    def setUpClass(cls):
        cls.s = Scenario("finishes")

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def test_the_runner_ran_to_its_end(self):
        self.assertEqual(self.s.rc, 1, self.s.out[-2000:])
        self.assertTrue(self.s.status().startswith("EXHAUSTED"), self.s.status())

    def test_the_straggler_is_never_killed_while_it_holds_a_call(self):
        """Red: the SIGKILL at the cut left the reservation with no terminal row."""
        rid = [r for r in self.s.ledger if r["type"] == "RESERVE"]
        self.assertEqual(len(rid), 1, self.s.ledger)
        terminal = [r["type"] for r in self.s.ledger if r.get("reservation_id") == rid[0]["reservation_id"] and r["type"] != "RESERVE"]
        self.assertEqual(terminal, ["RECONCILE"], "the straggler's reservation has no terminal row: %s" % self.s.out[-1500:])
        self.assertNotIn("TERM", self.s.record)

    def test_the_first_grading_is_exactly_the_builds_present_at_the_cut(self):
        graded = _read(self.s.round0("graded.txt")).split("----")[0].split()
        self.assertEqual(len(graded), 3, graded)
        self.assertFalse(any("-r3-" in g for g in graded), graded)

    def test_a_late_answer_is_graded_before_another_round_is_bought(self):
        # 2026-09-28: on a NO-PASS the round waits for its own fan out and grades what arrived after the cut, in a
        # second pass, so the set under the first grading never changes
        passes = [p.split() for p in _read(self.s.round0("graded.txt")).split("----") if p.strip()]
        self.assertEqual(len(passes), 2, passes)
        self.assertTrue(any("-r3-" in g for g in passes[1]), passes)
        self.assertIn("LATE    1 answer(s) arrived after the cut", self.s.out)

    def test_the_fan_out_runs_from_the_code_root(self):
        cwds = [os.path.realpath(l[4:]) for l in self.s.record if l.startswith("cwd ")]
        self.assertEqual(cwds, [os.path.realpath(self.s.code)])

    def test_the_failure_ledger_runs_from_the_code_root(self):
        runs = [l.split(" ")[-1] for l in self.s.record if l.startswith("failure_ledger ")]
        self.assertTrue(runs, self.s.record)
        for path in runs:
            self.assertTrue(os.path.realpath(path).startswith(os.path.realpath(self.s.code) + os.sep), path)


class StragglerIgnoresTerm(unittest.TestCase):
    """A fourth job that never ends and ignores TERM: the runner waits out the fan out's own bound, sends TERM, then
    KILL ten seconds later, and exits."""

    @classmethod
    def setUpClass(cls):
        # a 5 s call timeout puts the TERM at twice it plus 60 s, 70 s, far enough from one timeout plus 60 s (65 s)
        # that the reap loop's one second steps cannot blur the two
        cls.s = Scenario("ignores_term", env_extra={"FANOUT_TIMEOUT_S": "5"})

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def test_term_comes_first_and_only_after_the_fan_outs_own_bound(self):
        terms = [float(l.split()[1]) for l in self.s.record if l.startswith("TERM ")]
        self.assertEqual(len(terms), 1, "no TERM was sent before the kill: %s" % self.s.out[-1500:])
        self.assertGreaterEqual(terms[0], 2 * 5 + 60 - 1, "TERM came before twice the call timeout plus 60 s")

    def test_the_fan_out_is_gone_after_the_runner_exits(self):
        pid = int(next(l for l in self.s.record if l.startswith("pid ")).split()[1])
        with self.assertRaises(OSError):
            os.kill(pid, 0)
        self.assertTrue(self.s.status().startswith("EXHAUSTED"), self.s.status())


class Drained(unittest.TestCase):
    """M2-4: every fan out record is a DrainRefused refusal."""

    @classmethod
    def setUpClass(cls):
        cls.s = Scenario("drain")

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def test_a_drained_round_exits_five_with_drained(self):
        self.assertEqual(self.s.rc, 5, self.s.out[-1500:])
        self.assertTrue(self.s.status().startswith("DRAINED"), self.s.status())

    def test_a_drained_round_consumes_no_round(self):
        self.assertFalse(os.path.exists(self.s.round0("graded.txt")), "a drained round was graded")
        self.assertFalse(os.path.exists(os.path.join(self.s.run, "round1")))
        self.assertFalse([l for l in self.s.record if l.startswith("failure_ledger record ")], "a drained round was taught as a failure")


class Stops(unittest.TestCase):
    """The runner's own early exits: each says what it is and exits non zero, and neither starts a fan out."""

    def test_the_expected_value_gate_exhausts_with_exit_one(self):
        s = Scenario("finishes", env_extra={"FAKE_EV_STOP": "1"})
        self.addCleanup(s.cleanup)
        self.assertEqual(s.rc, 1, s.out[-1500:])
        self.assertTrue(s.status().startswith("EXHAUSTED by the expected value gate"), s.status())
        self.assertFalse([l for l in s.record if l.startswith("cwd ")], "a fan out started after the gate said stop")

    def test_no_code_root_blocks_before_any_fan_out(self):
        s = Scenario("finishes", drop=("BROTHER_CODE_ROOT",))
        self.addCleanup(s.cleanup)
        self.assertEqual(s.rc, 2, s.out[-1500:])
        self.assertTrue(s.status().startswith("BLOCKED no code root"), s.status())
        self.assertFalse([l for l in s.record if l.startswith("cwd ")], "a fan out started with no code root")


if __name__ == "__main__":
    unittest.main(verbosity=2)
