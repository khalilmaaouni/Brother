"""The runner hands a round to the Claude native worker whenever every arm is on the claude transport (standard since
2026-10-02; BROTHER_CLAUDE_NATIVE=off turns it off), and then exactly as native_worker.py's contract says (owner order
2026-10-02, PLAN step 3 of the native worker handover): the graded tree's HEAD as --base, the launch tree as --repo, the call timeout, one job per seat, a short brief
built from the spec section, and the same out/ and results.json the blind fan out uses, so grading is unchanged.

Entry point: `python3 unit_runner.py <unit> <sub> <rounds>` as a subprocess, the real unit_runner.py and loop_switches.py
bytes beside test_runner_straggler_settles' stubs. native_worker.py is a recording stub here (its own suite,
scripts/loop/test_native_worker.py, runs the real one), so the only code that decides anything below is the runner's.
ONE CONDITION PER CASE: the switch, the arm's transport, the spec section, the seat knob, the round.
Run from the repository root: python3 -B scripts/test_unit_runner_native.py
"""
import glob, json, os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_runner_straggler_settles as TRS  # noqa: E402

LOOP = os.path.join(HERE, "loop")
SPEC = "# Z9\n\n## Z9.1 the sub unit\nSECTION BODY of Z9.1.\n\n## Z9.2 another\nNOT THIS ONE.\n"
NATIVE_STUB = '''import json, os, re, sys
FIX_ROUNDS, FIX_SESSION_S = 1, 900   # native_worker's own figures: the seat's fix session after its landing fuzz
def seats(env=None):
    v = os.environ.get("BROTHER_NATIVE_SEATS", "2")
    if not v.isdigit() or int(v) < 1: raise ValueError("BROTHER_NATIVE_SEATS must be a positive whole number")
    return int(v)
def session_seconds(env=None):
    v = os.environ.get("BROTHER_NATIVE_SESSION_S", "2700")
    if not v.isdigit() or not 600 <= int(v) <= 7200: raise ValueError("BROTHER_NATIVE_SESSION_S out of range")
    return int(v)
def spec_section(text, sub):
    m = re.search(r"^## %s\\b.*?(?=^## |\\Z)" % re.escape(sub), text, re.S | re.M)
    return m.group(0) if m else None
def contract_text(runners=()): return "CONTRACT"
def brief(sub, section, contract, note=""): return "NATIVE BRIEF %s\\n%s\\n%s\\nNOTE:%s" % (sub, section, contract, note)
if __name__ == "__main__":
    a = sys.argv[1:]
    with open(a[0]) as fh: jobs = json.load(fh)
    with open(os.environ["FAKE_FANOUT_RECORD"], "a") as fh: fh.write("native " + json.dumps(a) + "\\n")
    mode = os.environ.get("FAKE_NATIVE_MODE", "")
    if mode == "seat_wait":
        rows = [{"id": j["id"], "ok": False, "error": "NO-DATA no native seat free within 2700 s", "seat_wait": True,
                 "claude_error": False} for j in jobs]
        with open(a[a.index("--results") + 1], "w") as fh: json.dump(rows, fh)
        sys.exit(1)
    if mode in ("claude_error", "mixed"):
        rows = [{"id": j["id"], "ok": False, "error": "NO-DATA claude returned an error result: limit",
                 "claude_error": not (mode == "mixed" and k == 0)} for k, j in enumerate(jobs)]
        with open(a[a.index("--results") + 1], "w") as fh: json.dump(rows, fh)
        sys.exit(1)
    for j in jobs:
        with open(j["out"] + ".tmp", "w") as fh: json.dump({"edits": [], "tests": [], "id": j["id"]}, fh)
        os.replace(j["out"] + ".tmp", j["out"])
    with open(a[a.index("--results") + 1], "w") as fh: json.dump([{"id": j["id"], "ok": True, "error": ""} for j in jobs], fh)
'''
ROUTER_STUB = ("import os\nPUBLIC = 'public'\nclass Refused(Exception): pass\n"
               "def registry(): return {'deepseek': {'transport': 'bridge'}, 'opus55': {'transport': 'claude'}}\n"
               "def chain(kind, sens, pin=None, registry_arg=None): return [pin] if pin else ['deepseek']\n"
               "def code_root():\n    v = os.environ.get('BROTHER_CODE_ROOT')\n"
               "    if not v: raise Refused('no code root')\n    return v\n")
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


class Run(object):
    def __init__(self, env_extra, rounds=1, spec=SPEC, stubs=None):
        os.makedirs(TRS.SCRATCH, exist_ok=True)
        self.d = tempfile.mkdtemp(prefix="native-runner-", dir=TRS.SCRATCH)
        self.code, self.wt, self.bin = (os.path.join(self.d, n) for n in ("code", "wt", "bin"))
        self.home, self.state = os.path.join(self.d, "home"), os.path.join(self.d, "state")
        os.makedirs(self.bin); os.makedirs(os.path.join(self.home, ".claude", "evidence")); os.makedirs(self.state)
        TRS.tree(self.code); TRS.tree(self.wt)
        for name in TRS.REAL_LOOP:
            shutil.copy(os.path.join(LOOP, name), self.bin)
        for name, body in dict(TRS.STUBS, **dict({"model_router.py": ROUTER_STUB, "native_worker.py": NATIVE_STUB}, **(stubs or {}))).items():
            with open(os.path.join(self.bin, name), "w") as f:
                f.write(body)
        os.chmod(os.path.join(self.bin, "grade_lane.sh"), 0o755)
        os.makedirs(os.path.join(self.wt, "docs", "plan", "specs"))
        with open(os.path.join(self.wt, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json"), "w") as f:
            json.dump({"units": [{"id": "Z9", "state": "OPEN", "spec": "docs/plan/specs/Z9.md", "sub_units": ["Z9.1"],
                                  "evidence": "", "command_runners": []}]}, f)
        with open(os.path.join(self.wt, "docs", "plan", "specs", "Z9.md"), "w") as f:
            f.write(spec)
        genv = dict(os.environ, **GIT_ENV)
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
            subprocess.run(["git"] + args, cwd=self.wt, env=genv, check=True, capture_output=True)
        self.head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.wt, env=genv, capture_output=True, text=True).stdout.strip()
        self.rec = os.path.join(self.d, "record.txt"); open(self.rec, "w").close()
        env = {k: v for k, v in os.environ.items() if not k.startswith(("BROTHER_", "GIT_")) and k not in ("RUNNER_HINT",)}
        env.update(HOME=self.home, BROTHER_CODE_ROOT=self.code, BROTHER_OR_STATE_ROOT=self.state, WORKERS_PER_ROUND="4",
                   BROTHER_BUILD_PLAN_MODEL="off", BROTHER_REPAIR_ADVISOR_MODEL="off", FANOUT_TIMEOUT_S="1",
                   FAKE_FANOUT_RECORD=self.rec, FAKE_FANOUT_MODE="finishes", PYTHONDONTWRITEBYTECODE="1", LATE_WAIT_S="20")
        env.update(env_extra)
        try:
            r = subprocess.run([sys.executable, "-B", os.path.join(self.bin, "unit_runner.py"), "Z9", "Z9.1", str(rounds)],
                               cwd=self.wt, env=env, capture_output=True, text=True, timeout=200)
        except BaseException:
            shutil.rmtree(self.d, ignore_errors=True)
            raise
        self.rc, self.out = r.returncode, r.stdout + r.stderr
        runs = glob.glob(os.path.join(self.home, ".claude", "evidence", "unit-runs", "Z9.1-*"))
        self.run = runs[0] if len(runs) == 1 else None
        self.record = TRS._read(self.rec).splitlines()

    def natives(self):
        return [json.loads(l[len("native "):]) for l in self.record if l.startswith("native ")]

    def status(self):
        return TRS._read(os.path.join(self.run, "STATUS")) if self.run else ""

    def path(self, *parts):
        return os.path.join(self.run, *parts)

    def cleanup(self):
        TRS.Scenario.cleanup(self)


CLAUDE = {"BROTHER_PIN_MODEL": "opus55"}
ON = dict(CLAUDE, BROTHER_CLAUDE_NATIVE="on")
OFF = dict(CLAUDE, BROTHER_CLAUDE_NATIVE="off")


class NativeRounds(unittest.TestCase):
    def go(self, env, **kw):
        r = Run(env, **kw)
        self.addCleanup(r.cleanup)
        return r

    def test_on_with_a_claude_arm_runs_the_native_worker_on_the_graded_head(self):
        r = self.go(dict(ON, BROTHER_NATIVE_SESSION_S="901"))
        calls = r.natives()
        self.assertEqual(len(calls), 1, r.out)
        a = calls[0]
        self.assertEqual(a[a.index("--base") + 1], r.head)
        self.assertEqual(os.path.realpath(a[a.index("--repo") + 1]), os.path.realpath(r.wt))
        self.assertEqual(a[a.index("--timeout") + 1], "901")   # the native cap, never the blind call timeout
        with open(a[0]) as fh:
            jobs = json.load(fh)
        self.assertEqual(len(jobs), 2, "one job per seat (default 2), not WORKERS_PER_ROUND=4")
        self.assertEqual({j["prompt_file"] for j in jobs}, {r.path("round0", "prompts", "Z9.1-native.md")})
        with open(jobs[0]["prompt_file"]) as fh:
            text = fh.read()
        self.assertIn("SECTION BODY of Z9.1.", text)
        self.assertNotIn("NOT THIS ONE.", text)
        self.assertIn("NATIVE  on: 2 session(s) on 2 seat(s)", r.out)
        with open(r.path("round0", "graded.txt")) as fh:   # the stub grader saw the native builds in out/
            graded = fh.read()
        self.assertIn("Z9.1-r0-build.json", graded)
        self.assertIn("Z9.1-r1-build.json", graded)

    def test_the_previous_refusal_reaches_the_native_brief(self):
        r = self.go(dict(ON, RUNNER_HINT="HINT-7Q: the landing refused a red neighbour"))
        with open(r.path("round0", "prompts", "Z9.1-native.md")) as fh:
            text = fh.read()
        self.assertIn("LEARNED FROM REJECTED BUILDS OF THIS SUB UNIT", text)
        self.assertIn("HINT-7Q: the landing refused a red neighbour", text)

    def test_a_round_of_claude_errors_spends_no_attempt(self):
        r = self.go(dict(ON, FAKE_NATIVE_MODE="claude_error"))
        self.assertEqual(r.rc, 4, r.out)
        self.assertTrue(r.status().startswith("UNFUNDED round 0: every native Claude session ended in a Claude error"), r.status())
        self.assertFalse(os.path.exists(r.path("round0", "graded.txt")), "nothing is graded")

    def test_a_round_that_never_got_a_seat_spends_no_attempt(self):
        r = self.go(dict(ON, FAKE_NATIVE_MODE="seat_wait"))
        self.assertEqual(r.rc, 5, r.out)
        self.assertTrue(r.status().startswith("DRAINED round 0: no native session ran"), r.status())
        self.assertFalse(os.path.exists(r.path("round0", "graded.txt")), "nothing is graded")

    def test_one_other_failure_makes_it_a_real_round(self):
        r = self.go(dict(ON, FAKE_NATIVE_MODE="mixed"))
        self.assertNotEqual(r.rc, 4, r.out)
        self.assertFalse(r.status().startswith("UNFUNDED"), r.status())

    def test_the_seat_knob_sizes_the_round(self):
        r = self.go(dict(ON, BROTHER_NATIVE_SEATS="1"))
        with open(r.natives()[0][0]) as fh:
            self.assertEqual(len(json.load(fh)), 1)

    def test_native_is_the_standard_for_a_claude_arm(self):
        r = self.go(CLAUDE)   # no BROTHER_CLAUDE_NATIVE at all
        self.assertEqual(len(r.natives()), 1, r.out)

    def test_the_round_waits_out_two_sessions_before_its_cut_and_its_reaper(self):
        # a seat wait of up to one session, then the session, then the seat's fix session after its landing fuzz
        # (native_worker.FIX_SESSION_S 900, 2026-10-05): the cut and the reaper must cover all three (harness call
        # timeout 1 s, so the blind bounds are 480 s and 62 s and only the native widening can produce these figures)
        r = self.go(dict(ON, BROTHER_NATIVE_SESSION_S="901"))
        self.assertIn("NATIVE  session cap 901 s, round cut 2702 s, reap 2762 s", r.out)

    def test_a_bad_session_cap_blocks(self):
        r = self.go(dict(ON, BROTHER_NATIVE_SESSION_S="59"))
        self.assertEqual(r.natives(), [])
        self.assertTrue(r.status().startswith("BLOCKED"), r.status())

    def test_off_runs_the_blind_fan_out(self):
        r = self.go(OFF)
        self.assertEqual(r.natives(), [])
        self.assertTrue(any(l.startswith("cwd ") for l in r.record), r.out)

    def test_an_arm_off_the_claude_transport_runs_the_blind_fan_out_and_says_so(self):
        r = self.go({"BROTHER_CLAUDE_NATIVE": "on"})   # no pin: the arm is deepseek, a bridge model
        self.assertEqual(r.natives(), [])
        self.assertIn("NATIVE  off this round", r.out)
        self.assertTrue(any(l.startswith("cwd ") for l in r.record), r.out)

    def test_a_spec_with_no_section_is_withheld_before_any_session(self):
        r = self.go(ON, spec="# Z9\n\n## Z9.2 another\nNOT THIS ONE.\n")
        self.assertEqual(r.natives(), [])
        self.assertTrue(r.status().startswith("WITHHELD"), r.status())
        self.assertEqual(r.rc, 2)

    def test_a_native_brief_carrying_a_private_term_is_withheld(self):
        # the spec section alone carries the word, so only the native brief's own screen can refuse it
        screen = TRS.STUBS["probe_build.py"].replace("def private_hits(text): return []",
                                                     "def private_hits(text): return ['hit'] if 'SECRETWORD' in text else []")
        r = self.go(ON, spec=SPEC.replace("SECTION BODY", "SECRETWORD BODY"), stubs={"probe_build.py": screen})
        self.assertEqual(r.natives(), [])
        self.assertTrue(r.status().startswith("WITHHELD round 0 native brief: private term"), r.status())

    def test_a_bad_seat_knob_blocks(self):
        r = self.go(dict(ON, BROTHER_NATIVE_SEATS="0"))
        self.assertEqual(r.natives(), [])
        self.assertTrue(r.status().startswith("BLOCKED"), r.status())

    def test_the_next_round_reads_the_grader_lines_never_the_pasted_build(self):
        r = self.go(ON, rounds=2)
        self.assertEqual(len(r.natives()), 2, r.out)
        with open(r.path("round1", "prompts", "Z9.1-native.md")) as fh:
            text = fh.read()
        self.assertIn("ROUND 0 WAS REJECTED BY THE SANDBOX GRADER", text)
        self.assertNotIn('"edits"', text)



class AGradeThatJudgedNothing(unittest.TestCase):
    """owner 2026-10-03 (CV1.b): a grade that timed out or faulted judged nothing. It is NO-DATA: no second model round,
    no lesson, the paid build kept, the sub unit parked WITHHELD. A COMPLETED FAIL still buys the bounded repair round.
    ONE CONDITION PER CASE: the stub grader's mode."""

    def go(self, extra):
        r = Run(dict(ON, **extra), rounds=2)
        self.addCleanup(r.cleanup)
        return r

    def test_a_timed_out_grade_with_empty_output_buys_no_second_model_round(self):
        r = self.go({"FAKE_GRADE_MODE": "timeout", "GRADE_TIMEOUT": "2"})
        self.assertEqual(len(r.natives()), 1, r.out)
        self.assertTrue(r.status().startswith("WITHHELD round 0: NO-DATA, the grade judged nothing"), r.status())
        self.assertTrue(glob.glob(r.path("round0", "out", "*.json")), "the paid build was not kept")
        self.assertFalse(os.path.exists(r.path("round1")), "a repair round was started")
        self.assertNotIn("WAS REJECTED BY THE SANDBOX GRADER", r.out)

    def test_a_grader_fault_is_no_data_too(self):
        r = self.go({"FAKE_GRADE_MODE": "infra"})
        self.assertEqual(len(r.natives()), 1, r.out)
        self.assertIn("NO-DATA, the grade judged nothing", r.status())

    def test_one_completed_fail_beside_an_ungraded_paid_build_buys_no_round(self):
        r = self.go({"FAKE_GRADE_MODE": "mixed"})
        self.assertGreaterEqual(len(glob.glob(r.path("round0", "out", "*.json"))), 2, "the case needs two paid builds")
        self.assertEqual(len(glob.glob(r.path("round0", "grades", "*.txt"))), 1)
        self.assertEqual(len(r.natives()), 1, r.out)
        self.assertIn("NO-DATA, the grade judged nothing", r.status())

    def test_a_grade_naming_no_build_of_the_round_judges_nothing(self):
        r = self.go({"FAKE_GRADE_MODE": "stale"})
        self.assertTrue(glob.glob(r.path("round0", "grades", "*-other.txt")))
        self.assertEqual(len(r.natives()), 1, r.out)
        self.assertIn("NO-DATA, the grade judged nothing", r.status())

    def test_a_round_with_no_paid_build_judged_nothing(self):
        import ast, re as _re
        src = open(os.path.join(LOOP, "unit_runner.py"), encoding="utf-8").read()
        fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "judged")
        ns = {"glob": glob, "os": os, "re": _re}
        exec(compile(ast.Module([fn], []), "judged", "exec"), ns)
        d = tempfile.mkdtemp(prefix="judged-")
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(os.path.join(d, "out")); os.makedirs(os.path.join(d, "grades"))
        with open(os.path.join(d, "grades", "Z9.1-r0.txt"), "w") as fh:
            fh.write("FAIL x\nexit=1\n")
        self.assertFalse(ns["judged"](d), "no paid build, yet judged")
        open(os.path.join(d, "out", "Z9.1-r0-build.json"), "w").close()
        self.assertTrue(ns["judged"](d))
        # grade_lane grades only <sub>-r*-build.json: a stray json there is no paid build and never blocks judging
        open(os.path.join(d, "out", "notes.json"), "w").close()
        self.assertTrue(ns["judged"](d, "Z9.1"), "a stray json in out/ blocked the judgment")

    def test_a_completed_fail_still_buys_the_repair_round(self):
        r = self.go({})
        self.assertEqual(len(r.natives()), 2, r.out)
        with open(r.path("round1", "prompts", "Z9.1-native.md")) as fh:
            self.assertIn("ROUND 0 WAS REJECTED BY THE SANDBOX GRADER", fh.read())


def gate(*rows, rc=1):
    """A brief_check stub printing its rows and summary the way brief_check.py does, exiting rc."""
    body = "".join("print(%r)\n" % ("%-17s REFUSED: x" % r) for r in rows)
    summary = "print(%r)\n" % ("BRIEF Z9 Z9.1: REFUSED on " + ", ".join(rows)) if rows else "print('NO-DATA: unreadable')\n"
    return {"brief_check.py": "import sys\n" + body + summary + "sys.exit(%d)\n" % rc}


class TheBriefGate(unittest.TestCase):
    """B1 to B3 judge the blind paste; a native session reads the tree itself. ONE CONDITION PER CASE."""

    def go(self, env, stubs):
        r = Run(env, stubs=stubs)
        self.addCleanup(r.cleanup)
        return r

    def test_a_paste_refusal_is_waived_for_a_native_run(self):
        r = self.go(ON, gate("B1 NAMED SHOWN"))
        self.assertEqual(len(r.natives()), 1, r.out)
        self.assertIn("brief gate B1 NAMED SHOWN waived", r.out)

    def test_the_same_refusal_parks_a_blind_run(self):
        r = self.go(OFF, gate("B1 NAMED SHOWN"))
        self.assertTrue(r.status().startswith("WITHHELD brief refused"), r.status())

    def test_a_done_check_refusal_parks_a_native_run(self):
        r = self.go(ON, gate("B4 DONE CHECK"))
        self.assertEqual(r.natives(), [])
        self.assertTrue(r.status().startswith("WITHHELD brief refused"), r.status())

    def test_a_paste_refusal_beside_a_spec_refusal_parks(self):
        r = self.go(ON, gate("B2 IMPORTS SHOWN", "B5 FROM THE WBS"))
        self.assertTrue(r.status().startswith("WITHHELD brief refused"), r.status())

    def test_no_data_is_never_waived(self):
        r = self.go(ON, gate("B1 NAMED SHOWN", rc=2))   # only the exit code says NO-DATA here
        self.assertTrue(r.status().startswith("WITHHELD brief refused"), r.status())

    def test_a_pin_off_the_claude_transport_is_not_native(self):
        r = self.go({"BROTHER_CLAUDE_NATIVE": "on", "BROTHER_PIN_MODEL": "deepseek"}, gate("B1 NAMED SHOWN"))
        self.assertTrue(r.status().startswith("WITHHELD brief refused"), r.status())

    def test_a_waived_brief_never_reaches_a_blind_round(self):
        mix = TRS.STUBS["worker_mix.py"].replace("def picks(n, picks, known, stats=None): return picks",
                                                 "def picks(n, picks, known, stats=None): return ['deepseek']")
        r = self.go(ON, dict(gate("B3 BUDGET"), **{"worker_mix.py": mix}))
        self.assertEqual(r.natives(), [])
        self.assertTrue(r.status().startswith("WITHHELD round 0: the brief gate waived B3 BUDGET"), r.status())
        self.assertFalse(any(l.startswith("cwd ") for l in r.record), "the blind fan out must not start")

if __name__ == "__main__":
    unittest.main()
