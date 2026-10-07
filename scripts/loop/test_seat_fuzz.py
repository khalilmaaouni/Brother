#!/usr/bin/env python3
"""The build seat runs the landing fuzz, and every build brief carries the hostile input contract (owner ruling
2026-10-05, build quality, option A). Of 1678 finished loop runs, 51 grader PASS builds were dropped at landing for
fuzz crashes; the two replayed here are FX-31.8 (unit run 110106, adapter_conformance.main(0) -> TypeError: 'int' object is not
iterable) and MG1.a (unit runs 020740, 022018, 023251, merge_gate.main, the same class). Their main(argv) bodies are
copied below as fixtures; the helpers they call are private stubs so the fuzz judges main alone.

THE ENTRY POINTS: every seat case runs native_worker.run() as the fan out calls it, with the Claude child replaced at the
tool boundary only (runner=), so the seat, the adapter and the REAL landing fuzz child (land_apply.fuzz_new_modules) all
run. The brief cases read native_worker.contract_text and drive build_brief.py as the loop does. ONE GUARD PER FIXTURE.
Run: python3 -B scripts/loop/test_seat_fuzz.py"""
import json, os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import native_worker as NW  # noqa: E402
import land_apply as LA  # noqa: E402
import grade_build as G  # noqa: E402
from test_native_worker import Base, good_worker, DONE, load  # noqa: E402

# FX-31.8, scripts/loop/adapter_conformance.py, main as the build shipped it (round0/out/FX-31.8-r0-build.json)
FX318_CRASH = '''import json, sys
def _run_conformance(names, offline=False):
    return {"problems": [], "ok": True, "receipt": {"entries": []}}
def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    offline = "--offline" in args
    names, path = None, None
    for i, a in enumerate(args):
        if a == "--transport" and i + 1 < len(args):
            names = tuple(n.strip() for n in args[i + 1].split(",") if n.strip())
        if a == "--receipt" and i + 1 < len(args):
            path = args[i + 1]
    out = _run_conformance(names if names else (), offline=offline)
    if not out["ok"]:
        return 1
    return 0
'''
# MG1.a, scripts/merge_gate.py, the head of main as the build shipped it (round0/out/MG1.a-r1-build.json)
MG1A_CRASH = '''import sys
USAGE = "usage: merge_gate.py refuse-env | ledger-path RUN | verify TREE SHA"
def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        sys.stderr.write(USAGE + "\\n")
        return 2
    command, rest = args[0], args[1:]
    if command == "verify" and len(rest) == 2:
        return 0
    sys.stderr.write(USAGE + "\\n")
    return 2
'''
# THE CONTRACT'S FIX: argv's type is checked before list(argv)
GUARD = '''    if argv is not None and not (isinstance(argv, list) and all(isinstance(a, str) for a in argv)):
        raise SystemExit(2)
'''


def fixed(src):
    return src.replace("def main(argv=None):\n", "def main(argv=None):\n" + GUARD, 1)


FX318, MG1A = "scripts/loop/adapter_conformance.py", "scripts/merge_gate.py"


def _read(path):
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class SeatCase(Base):
    """The fixtures and helpers; no tests of its own, so a subclass never reruns another's cases."""
    def setUp(self):
        super().setUp()
        self.saved_sb = os.environ.get("BROTHER_SANDBOX")
        os.environ["BROTHER_SANDBOX"] = "off"   # the fuzz child runs bare in these cases, so they hold inside a sandbox too
        self.addCleanup(lambda: os.environ.pop("BROTHER_SANDBOX", None) if self.saved_sb is None
                        else os.environ.__setitem__("BROTHER_SANDBOX", self.saved_sb))
        self.calls = []

    def sessions(self, *steps):
        """A runner playing one step per session: a dict of files to write (and an optional stdout)."""
        def runner(argv, stdin, timeout, cwd):
            k = len(self.calls)
            self.calls.append({"stdin": stdin, "cwd": cwd, "timeout": timeout,
                               "seen": {rel: _read(os.path.join(cwd, rel)) for rel in (FX318, MG1A)}})
            step = dict(steps[min(k, len(steps) - 1)])
            out = step.pop("_stdout", DONE)
            return good_worker(argv, stdin, timeout, cwd, extra=step, stdout=out)
        return runner

    def new_content(self, rel):
        return next(e["new_file_content"] for e in load(self.out)["edits"] if e["path"] == rel)

    def fuzz_log(self, k):
        with open(os.path.join(self.root, "log", "seat-fuzz-%d.txt" % k)) as fh:
            return fh.read()

    def replay(self, rel, crash, name):
        ok, why = self.go(runner=self.sessions({rel: crash}, {rel: fixed(crash)}), timeout=NW.FIX_SESSION_S + 100)
        self.assertTrue(ok, why)
        self.assertEqual(len(self.calls), 2, "the crash must buy exactly one fix session")
        fix = self.calls[1]
        self.assertIn("THE LANDING FUZZ CRASHED ON YOUR BUILD IN THIS SEAT", fix["stdin"])
        self.assertIn("CRASH %s.main(0 x1) -> TypeError: 'int' object is not iterable" % name, fix["stdin"])
        self.assertTrue(fix["stdin"].startswith("do X.1"), "the fix session keeps the whole brief")
        self.assertEqual(fix["cwd"], self.calls[0]["cwd"], "the fix runs in the same seat")
        self.assertEqual(fix["seen"][rel], crash, "the seat is not reset before the fix: the work is still there")
        self.assertEqual(fix["timeout"], NW.FIX_SESSION_S, "a fix session is capped below the session cap")
        self.assertIn("CRASH %s.main(" % name, self.fuzz_log(0))
        self.assertNotIn("CRASH", self.fuzz_log(1))
        self.assertEqual(self.new_content(rel), fixed(crash), "the build handed back is the fixed one")


class Seat(SeatCase):
    def test_fx318_replay_the_seat_catches_the_crash_and_fixes_it_before_landing(self):
        self.replay(FX318, FX318_CRASH, "adapter_conformance")

    def test_mg1a_replay_the_seat_catches_the_crash_and_fixes_it_before_landing(self):
        self.replay(MG1A, MG1A_CRASH, "merge_gate")

    def test_a_clean_build_buys_no_fix_session(self):
        ok, why = self.go(runner=self.sessions({FX318: fixed(FX318_CRASH)}))
        self.assertTrue(ok, why)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.fuzz_log(0), "")

    def test_a_crash_still_standing_after_the_last_fix_is_handed_on_once(self):
        ok, why = self.go(runner=self.sessions({FX318: FX318_CRASH}))
        self.assertTrue(ok, why)
        self.assertEqual(len(self.calls), 1 + NW.FIX_ROUNDS, "never more fix sessions than FIX_ROUNDS")
        self.assertIn("CRASH adapter_conformance.main(", self.fuzz_log(NW.FIX_ROUNDS))
        self.assertEqual(self.new_content(FX318), FX318_CRASH)

    def test_a_failed_fix_session_keeps_the_build_in_hand(self):
        err = json.dumps({"type": "result", "is_error": True, "result": "usage limit"})
        ok, why = self.go(runner=self.sessions({FX318: FX318_CRASH}, {FX318: fixed(FX318_CRASH), "_stdout": err}))
        self.assertTrue(ok, why)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.new_content(FX318), FX318_CRASH, "the build from before the failed fix is written, never lost")

    def test_a_new_module_that_will_not_import_is_sent_back(self):
        broken = "import no_such_module_seat_fuzz_fixture\ndef main(argv=None):\n    return 0\n"
        ok, why = self.go(runner=self.sessions({FX318: broken}, {FX318: fixed(FX318_CRASH)}))
        self.assertTrue(ok, why)
        self.assertEqual(len(self.calls), 2)
        self.assertIn("FUZZ import failed adapter_conformance", self.calls[1]["stdin"])

    def test_a_new_test_file_is_not_fuzzed(self):
        crash_test = "def helper(x):\n    return len(x)\n"   # len(0) is a TypeError, but tests are never fuzzed
        ok, why = self.go(runner=self.sessions({"scripts/loop/test_seat_fixture.py": crash_test}))
        self.assertTrue(ok, why)
        self.assertEqual(len(self.calls), 1)

    def test_a_new_file_that_is_not_python_is_not_fuzzed(self):
        ok, why = self.go(runner=self.sessions({"scripts/merge_seq.sh": "#!/bin/sh\nexit 0\n"}))
        self.assertTrue(ok, why)
        self.assertEqual(len(self.calls), 1)

    def test_a_fuzz_that_cannot_run_is_no_data_never_a_fix(self):
        os.environ.pop("BROTHER_SANDBOX", None)
        old = G.sandbox_ready
        G.sandbox_ready = lambda: "no sandbox-exec on this host (fixture)"
        self.addCleanup(setattr, G, "sandbox_ready", old)
        ok, why = self.go(runner=self.sessions({FX318: FX318_CRASH}))
        self.assertTrue(ok, why)
        self.assertEqual(len(self.calls), 1, "a fuzz that could not run is not the session's fault")
        self.assertIn("FUZZ sandbox refused", self.fuzz_log(0))


PLANT = '''import os
def plant(x):
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "planted_by_fuzz.py"), "w") as fh:
        fh.write("x = 1\\n")
    return x + 1
'''


@unittest.skipUnless(G._sandbox_present() and G.sandbox_ready() == "", "needs a real sandbox (NO-DATA here, never a pass)")
class SeatWrites(SeatCase):
    def test_a_fuzzed_call_never_writes_into_the_seat(self):
        os.environ.pop("BROTHER_SANDBOX", None)   # the real sandbox, write root the scratch home
        ok, why = self.go(runner=self.sessions({"scripts/loop/plant.py": PLANT}, {"scripts/loop/plant.py": PLANT + "\n"}))
        self.assertTrue(ok, why)
        self.assertEqual(len(self.calls), 2)
        paths = [e["path"] for e in load(self.out)["edits"]]
        self.assertNotIn("scripts/loop/planted_by_fuzz.py", paths, "a fuzzed write rode into the build")


class Briefs(unittest.TestCase):
    def test_the_contract_names_every_hostile_value_and_the_fixed_regressions(self):
        text = LA.hostile_contract()
        # the list exactly as the landing fuzz holds it, in order (a value dropped from the brief is a value never warned of)
        self.assertIn("defaults included: None, 0, True, -1, nan, '', 'x', b'x', [], ['x'], {}, {'a': 1}, (), {1, 2}, object(). ", text)
        self.assertEqual(len(LA.HOSTILE), 15, "HOSTILE changed: update the list above with it")
        self.assertIn("main(0) -> TypeError: 'int' object is not iterable", text)
        self.assertIn("ValueError, LookupError", text)
        for ch in (chr(0x2014), chr(0x2013)):   # the long dashes, named by code point so this file holds none
            self.assertNotIn(ch, text)

    def test_the_native_brief_carries_the_contract(self):
        self.assertIn(LA.hostile_contract(), NW.contract_text())

    def test_the_blind_brief_carries_the_contract(self):
        sys.path.append(os.path.dirname(HERE))   # scripts/, LAST: the loop's own modules above stay the ones bound
        from test_build_brief_keeps_every_file import BriefTree
        t = BriefTree(self)
        t.write("scripts/pure.py", "import json\n")
        t.spec("Files: `scripts/pure.py` existing.\n")
        r = t.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(LA.hostile_contract(), t.read())


if __name__ == "__main__":
    unittest.main()
