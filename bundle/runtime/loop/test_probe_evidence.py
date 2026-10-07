#!/usr/bin/env python3
"""A CLEAN probe verdict must mean the probes really ran, inside the sandbox, and saw no defect.

usage (repo root): python3 -B scripts/loop/test_probe_evidence.py

Four defects, reproduced by an outside audit on 2026-09-27 against e7e17784c and re-run identically by the
orchestrator, each with its own fixtures here:
  4  the probe child ran straight through Python, never through grade_build.sandboxed(), so a build module the
     probe imported wrote outside its scratch tree and the probe still exited 0.
  6  admission searched for the substring "fire(" and results were ordinary stdout lines starting PROBE, so a
     script with fire( in a comment that printed one forged record produced CLEAN.
  7  an exception type outside a finite crash list defaulted to REFUSED, so an uncaught OverflowError read as
     a deliberate refusal and the lane read CLEAN.
  8  probe_wave.lane_verdict ignored a CRASH an aborted run had already printed, because the aborted run
     withholds its counts line, so a second clean adversary made the lane CLEAN.

EVERY FIXTURE ISOLATES ONE CONDITION, and every one drives an ENTRY POINT: probe_build.py as a process, or
lane_verdict with the log text probe_wave writes. A case that cannot run on this host says NO-DATA as a skip
reason, which the lane's done check refuses, never a pass.
"""
import ast, contextlib, io, json, os, re, subprocess, sys, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE]
import probe_build as P  # noqa: E402

PROBE = os.path.join(HERE, "probe_build.py")
_TMP = tempfile.mkdtemp(prefix="probe-evidence-")
_REPO = []


def repo():
    """One throwaway git repository as the CWD of every probe_build run (scratch() works from the CWD's repo)."""
    if not _REPO:
        d = os.path.join(_TMP, "repo"); os.makedirs(d)
        with open(os.path.join(d, "README"), "w") as fh:
            fh.write("fixture\n")
        for argv in (["init", "-q"], ["add", "README"], ["-c", "user.email=f@x", "-c", "user.name=f", "commit", "-qm", "fixture"]):
            subprocess.check_call(["git", "-C", d] + argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _REPO.append(d)
    return _REPO[0]


def module(name, source):
    return {"edits": [{"path": name + ".py", "new_file_content": source}], "tests": []}


def run_probe(body, build=None, sandbox=None):
    """probe_build.py against a build and a probe body: (exit code, output). sandbox=None leaves BROTHER_SANDBOX
    unset, which is how the loop runs it; "off" is the explicit escape hatch."""
    d = tempfile.mkdtemp(dir=_TMP)
    home = os.path.join(d, "home"); os.makedirs(home)
    bf, pf = os.path.join(d, "build.json"), os.path.join(d, "probe.py")
    with open(bf, "w") as fh:
        json.dump(build or {"edits": [], "tests": []}, fh)
    with open(pf, "w") as fh:
        fh.write(body)
    env = {k: v for k, v in os.environ.items() if k != "BROTHER_SANDBOX"}
    env.update(HOME=home, LOCAL_SLOTS="1", PYTHONDONTWRITEBYTECODE="1")
    if sandbox is not None:
        env["BROTHER_SANDBOX"] = sandbox
    r = subprocess.run([sys.executable, "-B", PROBE, bf, pf], cwd=repo(), env=env, capture_output=True, text=True, timeout=240)
    return r.returncode, r.stdout + r.stderr


def sandbox_usable():
    """'' when sandbox-exec can apply the grader's own profile here, else why not (nested sandboxes refuse)."""
    d = tempfile.mkdtemp(dir=_TMP)
    try:
        r = subprocess.run(["sandbox-exec", "-f", P.G.SANDBOX_PROFILE, "-D", "ROOT=" + d, "-D", "TMP=" + d, "/usr/bin/true"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return "sandbox-exec cannot start: %s" % exc
    return "" if r.returncode == 0 else "sandbox-exec exit %d: %s" % (r.returncode, r.stderr.strip()[:120])


# ---------------------------------------------------------------- finding 4: the probe child runs sandboxed

class TheProbeChildRunsInTheSandbox(unittest.TestCase):
    def test_an_imported_build_cannot_write_outside_its_tree(self):
        # ONE CONDITION: the sandbox. The probe itself is a clean control; only the build's import side effect
        # reaches outside. Audit repro: outside_tree_write=True, probe-exit=0, BROTHER_SANDBOX unset.
        marker = os.path.join(tempfile.mkdtemp(dir=_TMP), "outside-build-tree.txt")
        build = module("audit_subject", "from pathlib import Path\nPath(%r).write_text('escaped')\n" % marker)
        code, text = run_probe("import audit_subject\nfire('control', lambda: True)\n", build)
        self.assertFalse(os.path.exists(marker), "the build wrote outside its tree:\n" + text)
        self.assertNotEqual(code, 0, text)

    def test_the_sandbox_really_refused_the_write(self):
        # The same fixture, read for the sandbox's own refusal, so a pass here means enforcement was observed,
        # not merely that nothing ran.
        why = sandbox_usable()
        if why:
            self.skipTest("NO-DATA: " + why)
        marker = os.path.join(tempfile.mkdtemp(dir=_TMP), "outside-build-tree.txt")
        build = module("audit_subject", "def escape(p):\n    open(p, 'w').write('escaped')\n")
        code, text = run_probe("import audit_subject\nfire('escape', lambda: audit_subject.escape(%r))\n" % marker, build)
        self.assertFalse(os.path.exists(marker), text)
        self.assertRegex(text, r"(?m)^CRASH\s+escape\s+PermissionError", text)
        self.assertEqual(code, 1, text)

    def _main_in_process(self, **patches):
        """probe_build._main with scratch() pointed at a temp dir and subprocess.run recorded, never executed."""
        d = tempfile.mkdtemp(dir=_TMP)
        bf, pf = os.path.join(d, "build.json"), os.path.join(d, "probe.py")
        with open(bf, "w") as fh:
            fh.write('{"edits": [], "tests": []}')
        with open(pf, "w") as fh:
            fh.write("fire('control', lambda: True)\n")
        root = tempfile.mkdtemp(dir=_TMP); calls = []
        env = {k: v for k, v in os.environ.items() if k != "BROTHER_SANDBOX"}
        out = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(P.G, "scratch", return_value=root), \
                mock.patch.object(P.subprocess, "run", side_effect=lambda *a, **k: calls.append(a) or subprocess.CompletedProcess(a, 0, "", "")), \
                mock.patch.object(sys, "argv", ["probe_build.py", bf, pf]), contextlib.redirect_stdout(out), \
                contextlib.ExitStack() as stack:
            for target, attr, value in patches.values():
                stack.enter_context(mock.patch.object(target, attr, value))
            code = P._main()
        return code, out.getvalue(), calls

    def test_a_host_with_no_sandbox_is_no_data_never_an_unsandboxed_run(self):
        # ONE CONDITION: sandbox-exec is absent (grade_build.sandboxed hands the command back unwrapped).
        code, text, calls = self._main_in_process(present=(P.G, "_sandbox_present", lambda: False))
        self.assertEqual(calls, [], "the probe ran unsandboxed")
        self.assertEqual(code, 2, text)
        self.assertIn("NO-DATA", text)

    def test_a_refused_sandbox_is_no_data(self):
        # ONE CONDITION: the profile is missing, so grade_build.sandboxed raises SandboxRefused.
        code, text, calls = self._main_in_process(profile=(P.G, "SANDBOX_PROFILE", os.path.join(_TMP, "absent.sb")))
        self.assertEqual(calls, [], "the probe ran with no sandbox profile")
        self.assertEqual(code, 2, text)
        self.assertIn("NO-DATA", text)

    def test_a_timed_out_child_offers_no_counts(self):
        # ONE CONDITION: the child times out. A real fire() call, so admission passes and only the timeout path
        # can answer; the record channel must not turn a killed run into a denominator.
        d = tempfile.mkdtemp(dir=_TMP)
        bf, pf = os.path.join(d, "build.json"), os.path.join(d, "probe.py")
        with open(bf, "w") as fh:
            fh.write('{"edits": [], "tests": []}')
        with open(pf, "w") as fh:
            fh.write("fire('control', lambda: True)\n")
        out = io.StringIO()
        with mock.patch.object(P.G, "scratch", return_value=tempfile.mkdtemp(dir=_TMP)), \
                mock.patch.object(P.subprocess, "run", side_effect=subprocess.TimeoutExpired("probe", 180)), \
                mock.patch.object(sys, "argv", ["probe_build.py", bf, pf]), contextlib.redirect_stdout(out):
            code = P._main()
        # nothing was received before the deadline, so the run is an unknown: exit 2, never 1 (which says "dirt was
        # seen") and never 0 (X3 finding 2, 2026-09-27: a timeout now drains and publishes what it did receive)
        self.assertEqual(code, 2, out.getvalue())
        self.assertNotIn("PROBES   ", out.getvalue())
        self.assertIn("timed out", out.getvalue())

    def test_the_off_switch_is_said_out_loud(self):
        # ONE CONDITION: BROTHER_SANDBOX=off. The escape hatch stays, and the probe output names it.
        code, text = run_probe("fire('control', lambda: 'ok')\n", sandbox="off")
        self.assertEqual(code, 0, text)
        self.assertIn("SANDBOX OFF", text)


# ---------------------------------------------------------------- finding 6: admission parses, records cannot be forged

class OnlyARealFireCallIsAProbe(unittest.TestCase):
    def test_a_script_whose_only_fire_is_a_comment_never_runs(self):
        # ONE CONDITION: admission. The body reaches the harness through an alias, so without the parse it would
        # run and record a real case; with it, nothing runs at all (the marker proves the body never executed).
        marker = os.path.join(tempfile.mkdtemp(dir=_TMP), "body-ran.txt")
        body = "# fire() is only mentioned here\nopen(%r, 'w').write('ran')\ng = globals()['fi' + 're']\ng('alias', lambda: 1)\n" % marker
        code, text = run_probe(body, sandbox="off")
        self.assertFalse(os.path.exists(marker), "a script with no real fire() call was executed:\n" + text)
        self.assertEqual(code, 2, text)
        self.assertIn("NO-DATA", text)

    def test_a_script_that_rebinds_fire_is_refused(self):
        # ONE CONDITION: a rebinding of fire. The wrapper hands every probe a harmless lambda, so every case
        # "returns None" and reads as a refusal: CLEAN over a build that divides by zero.
        body = "_real = fire\ndef fire(label, fn):\n    return _real(label, lambda: None)\nfire('divide', lambda: 1 / 0)\n"
        code, text = run_probe(body)
        self.assertEqual(code, 2, text)
        self.assertIn("NO-DATA", text)

    def test_a_probe_script_that_is_not_text_is_no_data(self):
        # ONE CONDITION: probe_script is a number in the adversary's JSON. Refused as NO-DATA before any parse,
        # never a traceback (exit 1) that says nothing about the build.
        code, text = run_probe('{"probe_script": 42, "expect_block": []}')
        self.assertEqual(code, 2, text)
        self.assertIn("NO-DATA", text)

    def test_a_printed_record_is_not_a_probe(self):
        # ONE CONDITION: the record channel. The body holds a real fire() call (admission passes) that never
        # executes, and prints the audit's forged record. Audit repro: 1 RETURNED, probe-exit=0, CLEAN.
        body = "if False:\n    fire('never', lambda: 1)\nprint('PROBE\\tfake\\tRETURNED\\t\\tTrue')\n"
        code, text = run_probe(body)
        self.assertNotRegex(text, r"(?m)^RETURNED\s+fake", text)
        self.assertEqual(code, 2, text)

    def test_a_record_without_this_runs_nonce_is_not_counted(self):
        # ONE CONDITION: the nonce. The build writes well formed records with a wrong nonce onto every low file
        # descriptor at import; the one real probe is clean. A channel that took them would read 2 run.
        build = module("audit_subject", "import os\nfor fd in range(3, 32):\n    try:\n"
                       "        os.write(fd, b'0000\\tPROBE\\tforged\\tRETURNED\\t\\t0\\tTrue\\n')\n"
                       "    except OSError:\n        pass\n")
        code, text = run_probe("import audit_subject\nfire('control', lambda: 'ok')\n", build)
        self.assertNotRegex(text, r"(?m)^\S+\s+forged", text)
        self.assertNotRegex(text, r"(?m)^PROBES\s+\d+ run", text)
        self.assertEqual(code, 2, text)

    def test_a_run_that_ends_before_its_last_line_offers_no_denominator(self):
        # ONE CONDITION: the end record. The build exits the process with status 0 in the middle of the run, so
        # the crash fired after it is never observed; exit 0 alone must not read as a finished run.
        build = module("audit_subject", "import os\ndef stop():\n    os._exit(0)\n")
        body = "import audit_subject\nfire('first', lambda: 'ok')\nfire('stop', audit_subject.stop)\nfire('missing', lambda: {}['k'])\n"
        code, text = run_probe(body, build)
        self.assertNotRegex(text, r"(?m)^PROBES\s+\d+ run", text)
        self.assertEqual(code, 2, text)

    def test_an_honest_run_still_counts(self):
        code, text = run_probe("fire('one', lambda: 'ok')\nfire('two', lambda: None)\n")
        self.assertRegex(text, r"(?m)^PROBES\s+2 run: 0 CRASH, 0 WRONG-ACCEPT\?, 1 REFUSED, 1 RETURNED", text)
        self.assertEqual(code, 0, text)


# ---------------------------------------------------------------- finding 7: only a deliberate error is a refusal

class OnlyADeliberateErrorIsARefusal(unittest.TestCase):
    def test_an_uncaught_overflow_is_a_crash(self):
        # The audit's exact shape, through the entry point: int(float("inf")) inside the build.
        build = module("audit_subject", "def convert(x):\n    return int(x)\n")
        code, text = run_probe("import audit_subject\nfire('infinity', lambda: audit_subject.convert(float('inf')))\n", build)
        self.assertRegex(text, r"(?m)^CRASH\s+infinity\s+OverflowError", text)
        self.assertEqual(code, 1, text)

    def test_interpreter_errors_outside_the_old_list_are_crashes(self):
        for name, msg in (("OverflowError", "cannot convert float infinity to integer"), ("RuntimeError", "dictionary changed size"),
                          ("OSError", "bad descriptor"), ("KeyboardInterrupt", ""), ("EOFError", "EOF when reading a line"),
                          ("NotImplementedError", ""), ("LookupError", "unknown encoding: x")):
            with self.subTest(name=name):
                self.assertEqual(P.classify(name, "builtins", msg), "CRASH")

    def test_a_type_the_probe_itself_defines_is_not_the_builds_refusal(self):
        self.assertEqual(P.classify("Refused", "__main__", "no"), "CRASH")

    def test_the_harness_does_not_count_the_probe_scripts_own_class_as_the_builds(self):
        # ONE CONDITION: the probe script (module __main__, a file inside the tree) defines the exception. The
        # harness must not call it the repository's own deliberate error, or an adversary could excuse anything.
        body = "class Mine(Exception):\n    pass\ndef boom():\n    raise Mine('no')\nfire('self_defined', boom)\n"
        code, text = run_probe(body)
        self.assertRegex(text, r"(?m)^CRASH\s+self_defined\s+Mine", text)
        self.assertEqual(code, 1, text)

    def test_value_and_type_errors_stay_refusals(self):
        self.assertEqual(P.classify("ValueError", "builtins", "spec is not acceptable"), "REFUSED")
        self.assertEqual(P.classify("TypeError", "builtins", "path must be a str"), "REFUSED")

    def test_the_builds_own_declared_error_is_a_refusal(self):
        # ONE CONDITION: the exception class is defined in the build's own module, which is what the house law
        # calls "the module's own deliberate error". Through the entry point, so the harness decides "own".
        build = module("audit_subject", "class Refused(Exception):\n    pass\ndef check(x):\n    raise Refused('bad input')\n")
        code, text = run_probe("import audit_subject\nfire('declared', lambda: audit_subject.check(None))\n", build)
        self.assertRegex(text, r"(?m)^REFUSED\s+declared\s+Refused", text)
        self.assertEqual(code, 0, text)


# ---------------------------------------------------------------- finding 8: an aborted run's dirt still counts

def _lane_verdict():
    """probe_wave.lane_verdict compiled on its own: probe_wave runs its whole wave at import."""
    path = os.path.join(HERE, "probe_wave.py")
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "lane_verdict")
    ns = {"re": re}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), path, "exec"), ns)
    return ns["lane_verdict"]


lane_verdict = _lane_verdict()
ABORTED_CRASH = ("CRASH    defect" + " " * 64 + "ZeroDivisionError division by zero\n"
                 "PROBES-ABORTED  child exit=1 after 1 line(s); counts withheld, an unfinished run cannot say the build is clean\n"
                 "FAIL the probe script itself died (exit 1): RuntimeError: harness died\nprobe-exit=1\n")
CLEAN = "RETURNED control" + " " * 64 + "True\nPROBES   1 run: 0 CRASH, 0 WRONG-ACCEPT?, 0 REFUSED, 1 RETURNED, 0 NO-DATA\nprobe-exit=0\n"


class AnAbortedRunsDirtStillCounts(unittest.TestCase):
    def test_a_crash_printed_before_the_death_makes_the_lane_dirty(self):
        # The audit's pair: an aborted adversary that saw a CRASH, and a clean one. Audit repro: CLEAN.
        self.assertEqual(lane_verdict([ABORTED_CRASH, CLEAN])[3], "DIRTY")

    def test_a_wrong_accept_printed_before_the_death_makes_the_lane_dirty(self):
        aborted = ABORTED_CRASH.replace("CRASH    defect", "WRONG-ACCEPT? defect")
        self.assertEqual(lane_verdict([aborted, CLEAN])[3], "DIRTY")

    def test_an_aborted_run_never_buys_a_clean(self):
        aborted = ABORTED_CRASH.replace("CRASH    defect", "RETURNED defect")
        self.assertEqual(lane_verdict([aborted])[3], "NO-DATA")

    def test_the_real_pair_through_probe_build(self):
        # The composition probe_wave performs: probe_build's own output plus the exit line probe_wave appends.
        logs = []
        for body in ("fire('defect', lambda: 1 / 0)\nraise RuntimeError('harness died')\n", "fire('control', lambda: True)\n"):
            code, text = run_probe(body)
            logs.append(text + "probe-exit=%d\n" % code)
        self.assertEqual(lane_verdict(logs)[3], "DIRTY", logs)


class ATimedOutRunPublishesWhatItReceived(unittest.TestCase):
    """X3 finding 2 (Codex cross lane review, 2026-09-27): on a probe timeout the runner returned before draining the
    authenticated record channel, so a CRASH the harness had already reported never reached the log, and a second,
    clean adversary made the lane CLEAN. A real child fires one case through the real channel and then outlives its
    deadline; only the deadline is shortened (180 s to DEADLINE_S), the pipe, nonce and kill are the runner's own."""
    DEADLINE_S = 5.0

    def timed_out(self, probe_body):
        d = tempfile.mkdtemp(dir=_TMP)
        bf, pf = os.path.join(d, "build.json"), os.path.join(d, "probe.py")
        with open(bf, "w") as fh:
            json.dump(module("target", "def f(x):\n    return 10 / x\n"), fh)
        with open(pf, "w") as fh:
            fh.write(probe_body + "import time\ntime.sleep(120)\n")
        real_run = subprocess.run

        def short(*a, **kw):   # the probe child is the one call carrying the record channel
            if "pass_fds" in kw:
                self.assertEqual(kw.get("timeout"), 180)
                kw["timeout"] = self.DEADLINE_S
            return real_run(*a, **kw)
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"BROTHER_SANDBOX": "off"}), \
                mock.patch.object(P.G, "scratch", return_value=tempfile.mkdtemp(dir=_TMP)), \
                mock.patch.object(P.subprocess, "run", side_effect=short), \
                mock.patch.object(sys, "argv", ["probe_build.py", bf, pf]), contextlib.redirect_stdout(out):
            code = P._main()
        return code, out.getvalue()

    def test_a_crash_received_before_the_deadline_is_published_and_the_lane_is_dirty(self):
        code, text = self.timed_out("import target\nfire('zero_input', lambda: target.f(0))\n")
        self.assertEqual(code, 1, text)
        self.assertTrue(re.search(r"^CRASH\s+zero_input\s.*ZeroDivisionError", text, re.M), text)
        self.assertIn("PROBES-ABORTED", text)
        self.assertNotIn("PROBES   ", text)
        self.assertIn("timed out", text)
        self.assertEqual(lane_verdict([text + "probe-exit=%d\n" % code, CLEAN])[3], "DIRTY", text)

    def test_a_timed_out_run_that_saw_nothing_wrong_is_no_data_never_clean(self):
        code, text = self.timed_out("import target\nfire('control', lambda: target.f(5))\n")
        self.assertEqual(code, 2, text)
        self.assertTrue(re.search(r"^RETURNED\s+control", text, re.M), text)
        self.assertIn("PROBES-ABORTED", text)
        self.assertNotIn("PROBES   ", text)
        self.assertEqual(lane_verdict([text + "probe-exit=%d\n" % code])[3], "NO-DATA", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
