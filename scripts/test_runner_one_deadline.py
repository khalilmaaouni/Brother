#!/usr/bin/env python3
"""FX-10.4: the runner takes its four bounds from ONE source, worker_mix.deadlines, under BROTHER_ONE_DEADLINE=on:
the round cut, the per call timeout, the fan out's own wave deadline (passed as --deadline) and the reaper; on a NO-PASS
round the late wait reaches the wave deadline, so a paid answer that lands before it is graded before another round is
bought. Switch off: today's arithmetic, no --deadline, the 480 s hard bound.

Entry point: `python3 unit_runner.py Z9 Z9.1 1` as a subprocess, over the scratch bin and fake fan out of
test_runner_straggler_settles (its Scenario), with a worker_mix stub whose deadlines() returns small values and a fake
fan out that records its argv, writes one build at once and one after the round cut, then waits for TERM.
Run from the repository root: python3 -B scripts/test_runner_one_deadline.py
"""
import os, sys, textwrap, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_runner_straggler_settles as T  # noqa: E402

ROUND, WAVE, REAP = 4, 18, 24
WM_STUB = T.STUBS["worker_mix.py"] + textwrap.dedent('''
    import os
    def one_deadline(env=None): return os.environ.get('BROTHER_ONE_DEADLINE') == 'on'
    def measured(models, transports, registry=None): return {'deepseek': {'p75_s': 150, 'p90_s': 400, 'n': 5, 'source': 'ledger'}}
    def patience_of(block): return 150
    def findings(block): return ['PATIENCE FINDING deepseek stub']
    def deadlines(pat, knob=420, grace=60, settle=30): return {'call': 2, 'round': %d, 'wave': %d, 'reap': %d}
''' % (ROUND, WAVE, REAP))

FANOUT = textwrap.dedent('''
    import json, os, signal, sys, time
    born = time.time()
    args = sys.argv[1:]
    jobs = json.load(open(args[0]))
    def note(s):
        with open(os.environ["FAKE_FANOUT_RECORD"], "a") as f: f.write(s + "\\n")
    def build(job):
        os.makedirs(os.path.dirname(job["out"]), exist_ok=True)
        with open(job["out"] + ".part", "w") as f: json.dump({"edits": [], "tests": [], "id": job["id"]}, f)
        os.replace(job["out"] + ".part", job["out"])
    note("argv " + json.dumps(args)); note("pid %d" % os.getpid())
    signal.signal(signal.SIGTERM, lambda s, f: (note("TERM %.1f" % (time.time() - born)), sys.exit(0)))
    build(jobs[0])
    if os.environ["FAKE_FANOUT_MODE"] == "quick":
        build(jobs[1]); build(jobs[2]); sys.exit(0)
    time.sleep(12)
    build(jobs[1]); note("late build at %.1f" % (time.time() - born))
    end = time.time() + 60
    while time.time() < end: time.sleep(0.2)
''')


def scenario(switch, mode):
    old = (T.STUBS["worker_mix.py"], T.FAKE_FANOUT)
    T.STUBS["worker_mix.py"], T.FAKE_FANOUT = WM_STUB, FANOUT
    try:
        return T.Scenario(mode, workers=3, timeout=120, env_extra={"BROTHER_ONE_DEADLINE": switch, "LATE_WAIT_S": "1"})
    finally:
        T.STUBS["worker_mix.py"], T.FAKE_FANOUT = old


class OneDeadline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.on = scenario("on", "late")

    @classmethod
    def tearDownClass(cls):
        cls.on.cleanup()

    def argv(self, s):
        import json
        line = next(l for l in T._read(s.rec).splitlines() if l.startswith("argv "))
        return json.loads(line[5:])

    def test_runner_passes_wave_deadline(self):
        a = self.argv(self.on)
        self.assertEqual(a[a.index("--deadline") + 1], str(WAVE), a)
        self.assertEqual(a[a.index("--timeout") + 1], "2", a)

    def test_round_cut_is_the_round_deadline(self):
        graded = T._read(os.path.join(self.on.run, "round0", "graded.txt")).split("----")
        self.assertEqual(graded[0].split(), ["Z9.1-r0-build.json"], "the cut at the round deadline graded one build")

    def test_runner_late_wait_reaches_wave(self):
        self.assertIn("LATE    1 answer(s) arrived after the cut", self.on.out)
        graded = T._read(os.path.join(self.on.run, "round0", "graded.txt")).split("----")
        self.assertEqual(graded[1].split(), ["Z9.1-r0-build.json", "Z9.1-r1-build.json"])

    def test_runner_reaps_at_the_reap_deadline(self):
        term = [float(l.split()[1]) for l in T._read(self.on.rec).splitlines() if l.startswith("TERM ")]
        self.assertEqual(len(term), 1, T._read(self.on.rec))
        self.assertGreaterEqual(term[0], REAP - 2)
        self.assertLess(term[0], REAP + 10)

    def test_patience_line_names_the_block_and_the_finding(self):
        self.assertIn("round %d s, call 2 s, wave %d s, reap %d s" % (ROUND, WAVE, REAP), self.on.out)
        self.assertIn("PATIENCE FINDING deepseek stub", self.on.out)

    def test_deadlines_below_dispatch_floor_is_reported(self):
        self.assertIn("PATIENCE NO-DATA: the call deadline 2 s is under the dispatcher's 300 s floor", self.on.out)


class SwitchOff(unittest.TestCase):
    def test_switch_default_is_today(self):
        for switch in ("off", "yes"):
            with self.subTest(switch=switch):
                s = scenario(switch, "quick")
                try:
                    import json
                    line = next(l for l in T._read(s.rec).splitlines() if l.startswith("argv "))
                    a = json.loads(line[5:])
                    self.assertNotIn("--deadline", a)
                    self.assertIn("PATIENCE soft 150 s, hard 480 s", s.out)
                    self.assertNotIn("PATIENCE FINDING", s.out)
                finally:
                    s.cleanup()


class TheSelfCheckUsesTheRoundDeadline(unittest.TestCase):
    """Every SC.screen call in unit_runner.py passes timeout=_call_timeout, never self_check's own 480 s default:
    measured 2026-09-30, twelve self check fan outs were cut at 540 s while the build fan out waited 688 s."""
    def test_every_screen_call_passes_the_call_timeout(self):
        import ast
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "loop", "unit_runner.py")
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "screen" and isinstance(n.func.value, ast.Name) and n.func.value.id == "SC"]
        self.assertTrue(calls, "no SC.screen call found: the self check moved, re-point this test")
        for c in calls:
            kw = {k.arg: k.value for k in c.keywords}
            self.assertIn("timeout", kw, "SC.screen at line %d uses self_check's constant default" % c.lineno)
            self.assertIsInstance(kw["timeout"], ast.Name, "line %d: the timeout must be the runner's deadline" % c.lineno)
            self.assertEqual(kw["timeout"].id, "_call_timeout", "line %d" % c.lineno)


if __name__ == "__main__":
    unittest.main()
