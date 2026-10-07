#!/usr/bin/env python3
"""The probe stage is a CONTROL, not an audit: prove that every path which cannot say the build is sound refuses it.

usage (repo root): python3 scripts/test_probe_is_a_control.py

Three defects were reproduced on 2026-09-22 and each has fixtures here:
  A  probe_admits("DIRTY", []) returned True. The verdict was never checked against a list of recognised words,
     so a hostile verdict and a word nobody has coined yet both admitted.
  B  a probe child that fired one harmless case and then raised exited 1, probe_build printed its counts line
     BEFORE reporting the death, and probe_wave's reader took those counts and called the lane CLEAN.
  C  the refusal oracle read the integers 1, 2, 3 and the empty containers {} [] () as refusals, and classified
     a wrong arity call (measured: 124 of 846 CRASH lines on disk) as the build correctly refusing something.

EVERY FIXTURE ISOLATES ONE CONDITION. A fixture that trips two guards proves neither, which was measured on
this module's siblings the same night. The mutation list at the bottom names, per guard, the single edit that
must turn this file red, and each one is proved by exactly one fixture.

HERMETIC: nothing here reads HOME, a network, or any file outside the repo. Prove with `env -i HOME=/nonexistent`.
"""
import ast, json, os, re, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOOP = os.path.join(ROOT, "scripts", "loop")


def load_names(path, names):
    """Bind the named top level objects out of a script that is not importable as a module.

    unit_runner.py runs its whole pipeline at import time, so it cannot be imported. The alternative, reading
    its source as text and asserting on the text, is exactly the check that was defeated on 2026-09-21 by an
    edit one line upstream. So the FUNCTION and the TABLE are compiled and driven with values."""
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src)
    # LIFT THE MODULE LEVEL IMPORTS TOO. Measured 2026-09-22: lifting only the named objects gave
    # every case NameError: name 're' is not defined, five errors, because lane_verdict uses the
    # module's own `import re` and an exec namespace built from the function alone does not carry
    # it. The same loader defect bit test_unit_runner_probe_gate.py the same night. Imports are
    # cheap and side effect free, so taking all of them costs nothing and removes the class.
    imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    keep = [n for n in tree.body
            if (isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names)
            or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets))]
    ns = {}
    # EACH IMPORT ON ITS OWN, because one that cannot resolve must not take the rest with it.
    # Lifting them as a block failed with ModuleNotFoundError on a sibling script that needs
    # sys.path prepared first, which is a different problem from the one being solved here: the
    # lifted function needs the STDLIB names it uses (re), not the module's local siblings.
    for node in imports:
        try:
            exec(compile(ast.Module(body=[node], type_ignores=[]), path, "exec"), ns)
        except Exception:
            continue                       # a sibling that needs sys.path is not our business
    exec(compile(ast.Module(body=keep, type_ignores=[]), path, "exec"), ns)
    missing = [n for n in names if n not in ns]
    assert not missing, "not found in %s: %s" % (path, missing)
    return ns


# THE PATH GOES IN BEFORE ANY LIFT. load_names executes unit_runner's own `import probe_build as P`; with scripts/
# first on sys.path that cached scripts/probe_build.py (a different module, landed with L4b.1, whose classify answers
# PASS) and the import below then handed it back, so every classify fixture here was red against the wrong module.
sys.path.insert(0, os.path.expanduser("~/.claude/bin"))
sys.path.insert(0, LOOP)
UR = load_names(os.path.join(LOOP, "unit_runner.py"), ["probe_outcome", "probe_admits", "PROBE_VERDICT", "UNKNOWN_VERDICT"])
outcome, admits = UR["probe_outcome"], UR["probe_admits"]

import probe_build as P  # noqa: E402
assert os.path.dirname(os.path.abspath(P.__file__)) == LOOP, P.__file__


class TheTableAdmitsExactlyOneWord(unittest.TestCase):
    """Requirement 2: enumerate every verdict; exactly one admits; every other word, named or not, refuses."""

    def test_exactly_one_verdict_admits(self):
        admitting = [k for k, v in UR["PROBE_VERDICT"].items() if v == "ADMIT"]
        self.assertEqual(admitting, ["CLEAN"])

    def test_the_word_nobody_has_thought_of_yet_does_not_admit(self):
        # ONE CONDITION: an unrecognised word. Findings are empty and the types are right, so no other guard
        # can produce this answer.
        for word in ("BANANA", "clean-ish", "PASS", "OK", "READY", "CLEAN-ENOUGH", "", "   "):
            self.assertEqual(outcome(word, []), "UNKNOWN", word)
            self.assertFalse(admits(word, []), word)


class DirtyRefuses(unittest.TestCase):
    """Defect A, the reproduced case: probe_admits("DIRTY", []) returned True."""

    def test_dirty_with_no_findings_still_refuses(self):
        # ONE CONDITION: the table's answer for DIRTY. Empty findings on purpose, so guard 3 cannot fire and
        # this fixture can only die when PROBE_VERDICT["DIRTY"] stops saying REFUSE.
        self.assertEqual(outcome("DIRTY", []), "REFUSE")
        self.assertFalse(admits("DIRTY", []))


class EvidenceOutranksTheWord(unittest.TestCase):
    def test_a_finding_refuses_even_under_a_clean_verdict(self):
        # ONE CONDITION: guard 3. The verdict is the admitting word, so only the findings guard can refuse here.
        self.assertEqual(outcome("CLEAN", ["CRASH  some_probe  KeyError 'id'"]), "REFUSE")


class AnUnknownIsNeitherAPassNorARejection(unittest.TestCase):
    """Requirement 3: PROBED-AND-CLEAN, PROBED-AND-DIRTY and NOT-PROBED are three states, not two."""

    def test_the_three_states_are_three_distinct_answers(self):
        self.assertEqual({outcome("CLEAN", []), outcome("DIRTY", []), outcome("NO-DATA", [])},
                         {"ADMIT", "REFUSE", "UNKNOWN"})

    def test_no_data_does_not_admit_and_is_not_a_refusal(self):
        # ONE CONDITION: the table's answer for NO-DATA. Counting it as REFUSE would be the mirror defect.
        self.assertEqual(outcome("NO-DATA", []), "UNKNOWN")

    def test_an_unreadable_verdict_type_is_unknown_not_a_crash(self):
        # ONE CONDITION: guard 1. A list has no .strip(), so removing the isinstance check makes this RAISE.
        self.assertEqual(outcome(["CLEAN"], []), "UNKNOWN")

    def test_an_unreadable_finding_set_is_unknown_not_a_rejection(self):
        # ONE CONDITION: guard 2. The verdict is the admitting word and the finding set is a str, so removing
        # the isinstance check makes len() succeed and turns this into REFUSE.
        self.assertEqual(outcome("CLEAN", "CRASH one_probe"), "UNKNOWN")


class OnlyACleanFinishedRunAdmits(unittest.TestCase):
    def test_the_one_admitting_case(self):
        self.assertEqual(outcome("CLEAN", []), "ADMIT")
        self.assertTrue(admits("CLEAN", []))

    def test_whitespace_around_the_admitting_word_is_normalised(self):
        self.assertEqual(outcome(" CLEAN \n", []), "ADMIT")


# ---------------------------------------------------------------- defect B, at probe_build's own exit point

BUILD = '{"edits": [], "tests": []}'


def module(name, source):
    """A build that adds one module to the tree, the way a real build ships the code a probe imports."""
    return {"edits": [{"path": name + ".py", "new_file_content": source}], "tests": []}
_TINY = []


def tiny_repo():
    """One throwaway git repository, used as the CWD for every probe_build fixture.

    grade_build.scratch() makes a `git clone --local` of whatever repository the CWD sits in, once per run.
    Against this estate's own repository that is tens of seconds per fixture (the first version of this file
    did not finish inside 120 s), and it measures this checkout rather than the code under test. The build
    these fixtures apply is empty, so the contents of the sandbox are irrelevant to every assertion here."""
    if not _TINY:
        d = tempfile.mkdtemp(prefix="probe-control-repo-")
        with open(os.path.join(d, "README"), "w") as fh:
            fh.write("fixture\n")
        for cmd in (["init", "-q"], ["add", "README"],
                    ["-c", "user.email=f@x", "-c", "user.name=f", "commit", "-qm", "fixture"]):
            subprocess.check_call(["git", "-C", d] + cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _TINY.append(d)
    return _TINY[0]


def run_probe(body, build=BUILD):
    """probe_build.py against an empty build and a probe body. Exit code captured WITHOUT a pipe.

    The child gets its OWN HOME and LOCAL_SLOTS=1. grade_build.local_slot() takes one of five machine wide
    file locks shared with every live grader on this box, so without this the fixture would block behind the
    council's real work (measured here: the first run of this file did not finish inside 120 s) and would
    fail outright under an empty HOME, which cannot create the slot directory. A test must measure the code,
    never the machine it happens to run on."""
    if P.G.sandbox_ready():   # inside another sandbox (the hermetic push check) the probe answers NO-DATA by design: no verdict here
        raise unittest.SkipTest("NO-DATA: %s" % P.G.sandbox_ready())
    d = tempfile.mkdtemp(prefix="probe-control-")
    home = os.path.join(d, "home"); os.makedirs(home)
    bf, pf, of = (os.path.join(d, n) for n in ("build.json", "probe.py", "out.log"))
    with open(bf, "w") as fh:
        fh.write(build)
    with open(pf, "w") as fh:
        fh.write(body)
    env = dict(os.environ, HOME=home, LOCAL_SLOTS="1", PYTHONDONTWRITEBYTECODE="1")
    with open(of, "w") as fh:
        code = subprocess.call([sys.executable, os.path.join(LOOP, "probe_build.py"), bf, pf],
                               cwd=tiny_repo(), env=env, stdout=fh, stderr=subprocess.STDOUT)
    with open(of, encoding="utf-8") as fh:
        return code, fh.read()


class ADeadChildOffersNoDenominator(unittest.TestCase):
    """Defect B. Reproduced 2026-09-22: one harmless case then a raise printed
    "PROBES   1 run: 0 CRASH, 0 WRONG-ACCEPT?, 0 REFUSED, 1 RETURNED" and probe_wave read CLEAN off it."""

    def test_one_case_then_a_crash_withholds_the_counts_line(self):
        # ONE CONDITION: the child died. The one case that ran is harmless, so no dirt guard can fire.
        code, text = run_probe('fire("harmless", lambda: "ok")\nraise SystemError("probe blew up")\n')
        self.assertNotIn("PROBES   1 run", text, "a run that did not finish must not offer a denominator")
        self.assertIn("PROBES-ABORTED", text)
        self.assertEqual(code, 2, "no dirt observed and the run did not finish: an unknown, never 0")

    def test_dirt_seen_before_the_death_is_still_dirt(self):
        # ONE CONDITION: a CRASH was observed. Asymmetry on purpose: unknown never buys a pass, and evidence
        # of dirt is never discarded just because the harness later died.
        code, text = run_probe('fire("boom", lambda: {}["missing"])\nraise SystemError("probe blew up")\n')
        self.assertIn("CRASH", text)
        self.assertEqual(code, 1)

    def test_a_finished_clean_run_still_exits_zero(self):
        code, text = run_probe('fire("harmless", lambda: "ok")\n')
        self.assertIn("PROBES   1 run", text)
        self.assertEqual(code, 0)


PW = load_names(os.path.join(LOOP, "probe_wave.py"), ["lane_verdict"])
lane_verdict = PW["lane_verdict"]


class TheWaveReaderRefusesADeadChildsCounts(unittest.TestCase):
    """probe_wave.lane_verdict, THE function the script itself calls, driven with log text.

    It was inline in probe_wave's loop and the only fixture possible against it was a source-text assertion.
    Measured 2026-09-22: deleting the exit code check killed ONLY that text fixture, so the behaviour was not
    under test at all. Extracting the function is what makes this a real check."""

    def test_counts_without_a_zero_exit_are_not_clean(self):
        # ONE CONDITION: the exit line. The counts are identical in this fixture and the next; only the exit differs.
        self.assertEqual(lane_verdict(["PROBES   1 run: 0 CRASH, 0 WRONG-ACCEPT?\nprobe-exit=1\n"])[3], "NO-DATA")

    def test_the_same_counts_with_a_zero_exit_are_clean(self):
        self.assertEqual(lane_verdict(["PROBES   1 run: 0 CRASH, 0 WRONG-ACCEPT?\nprobe-exit=0\n"])[3], "CLEAN")

    def test_dirt_from_a_dead_child_is_still_dirty(self):
        # ONE CONDITION: dirt outranks the unfinished run.
        self.assertEqual(lane_verdict(["PROBES   4 run: 2 CRASH, 0 WRONG-ACCEPT?\nprobe-exit=1\n"])[3], "DIRTY")

    def test_no_logs_at_all_is_no_data(self):
        self.assertEqual(lane_verdict([])[3], "NO-DATA")

    def test_a_log_with_no_counts_line_is_no_data(self):
        # A timeout prints no counts line at all: no denominator, so nothing to read as clean.
        self.assertEqual(lane_verdict(["FAIL probe timed out after 180 s\nprobe-exit=1\n"])[3], "NO-DATA")

    def test_one_finished_clean_adversary_carries_the_lane(self):
        self.assertEqual(lane_verdict(["FAIL probe timed out after 180 s\nprobe-exit=1\n",
                                       "PROBES   9 run: 0 CRASH, 0 WRONG-ACCEPT?\nprobe-exit=0\n"])[3], "CLEAN")


# ---------------------------------------------------------------- defect C, the refusal oracle

class AnUnreachedCallProvesNothing(unittest.TestCase):
    """Defect C. A probe that never entered the target function is NO-DATA for that probe, never evidence that
    the implementation correctly refused. Judging a refusal properly needs the function's CONTRACT (its real
    signature and its documented rejects), which this runner does not read: OUT OF SCOPE TODAY, so the narrower
    honest option is taken and the unreached call reports nothing learned."""

    def test_a_wrong_arity_call_is_no_data(self):
        # ONE CONDITION: the arity branch. Measured 2026-09-22: 124 of 846 CRASH lines on disk are this shape.
        self.assertEqual(P.classify("TypeError", "builtins", "machine_capacity() takes 0 positional arguments but 2 were given",
                                    at_call=True), "NO-DATA")

    def test_a_missing_keyword_only_argument_is_no_data(self):
        # ONE CONDITION: the arity branch, keyword-only form. Measured 2026-09-29: 28 probe log lines on disk carry
        # exactly this message, and all 5 R4.2 passing builds read DIRTY at the probe stage on it. The adversary
        # called consult() without its keyword-only arguments; Python refused the call before the body ran,
        # exactly as it does for the positional twin above.
        self.assertEqual(P.classify("TypeError", "builtins", "consult() missing 3 required keyword-only arguments: "
                                    "'seams_config', 'registry', and 'ledger_dir'", at_call=True), "NO-DATA")
        self.assertEqual(P.classify("TypeError", "builtins",
                                    "f() missing 1 required keyword-only argument: 'ledger_dir'", at_call=True), "NO-DATA")

    def test_the_interpreters_own_keyword_only_message_is_no_data(self):
        # The same condition on the text THIS interpreter really raises, so a wording change on 3.9 or 3.13 goes red.
        def consult(*, seams_config, registry):
            return seams_config, registry
        try:
            consult()
        except TypeError as exc:
            message = str(exc)
        self.assertEqual(P.classify("TypeError", "builtins", message, at_call=True), "NO-DATA", message)

    def test_a_keyword_only_call_that_never_entered_the_body_is_no_data_end_to_end(self):
        # ENTRY POINT: probe_build.py itself, not the helper. On the base this printed CRASH and exited 1 (DIRTY).
        code, text = run_probe('def consult(*, seams_config, registry, ledger_dir):\n    return None\n'
                               'fire("kwonly", lambda: consult())\n')
        self.assertNotIn("\nCRASH", "\n" + text)
        self.assertIn("none of them reached the build", text)
        self.assertEqual(code, 2)

    def test_a_type_error_raised_inside_a_keyword_only_body_is_still_a_crash_end_to_end(self):
        # The carve out stays narrow: the call was well formed, the body mishandled the value. Still DIRTY.
        code, text = run_probe('def consult(*, seams_config):\n    return len(seams_config)\n'
                               'fire("body", lambda: consult(seams_config=None))\n')
        self.assertIn("CRASH", text)
        self.assertEqual(code, 1)

    # THE MESSAGE IS NOT THE PLACE (verifier finding, 2026-09-29). An arity TypeError is excused only when the
    # harness saw it raised AT THE PROBE'S OWN CALL (the innermost traceback frame is the probe script's code, so
    # the callee never got a frame). The same text raised inside the build's body is the build calling its own
    # helper wrong: a real defect, CRASH. Each fixture below isolates one condition, through probe_build.py.

    def test_an_arity_message_with_no_call_site_flag_is_a_crash(self):
        # ONE CONDITION: the flag. A caller re-reading a log line has no traceback, so it gets the conservative CRASH.
        for message in ("consult() missing 3 required keyword-only arguments: 'a', 'b', and 'c'",
                        "f() missing 1 required positional argument: 'path'",
                        "f() takes 1 positional argument but 2 were given",
                        "f() takes no arguments (1 given)",
                        "f() takes from 1 to 2 positional arguments but 3 were given",
                        "f() got an unexpected keyword argument 'mode'"):
            with self.subTest(message=message):
                self.assertEqual(P.classify("TypeError", "builtins", message), "CRASH")
                self.assertEqual(P.classify("TypeError", "builtins", message, at_call=False), "CRASH")

    def test_only_a_type_error_is_excused_at_the_call_site(self):
        # ONE CONDITION: the exception type. An AssertionError whose text happens to carry arity words is still raw.
        self.assertEqual(P.classify("AssertionError", "builtins", "helper() takes 1 positional argument but 2 were given",
                                    at_call=True), "CRASH")

    def test_a_non_arity_type_error_at_the_call_site_is_not_excused(self):
        # ONE CONDITION: the message. The flag alone excuses nothing: a raw shape at the call site is still CRASH.
        self.assertEqual(P.classify("TypeError", "builtins", "unsupported operand type(s) for +: 'int' and 'str'",
                                    at_call=True), "CRASH")

    def test_a_raw_operand_error_is_a_crash(self):
        # Pins the RAW_TYPEERROR "unsupported operand" alternative, which survived the 2026-09-29 sweep: this exact
        # text matches no other alternative, so deleting it would turn a real crash into REFUSED.
        self.assertEqual(P.classify("TypeError", "builtins", "unsupported operand type(s) for +: 'int' and 'str'"), "CRASH")

    def test_a_build_function_called_without_a_keyword_only_argument_is_no_data(self):
        # (a) THE ADVERSARY'S CALL, the build living in its own module as it does in a real run.
        build = json.dumps(module("target", "def consult(*, seams_config, registry, ledger_dir):\n    return None\n"))
        code, text = run_probe("import target\nfire('kwonly', lambda: target.consult())\n", build)
        self.assertRegex(text, r"(?m)^NO-DATA\s+kwonly\s+TypeError", text)
        self.assertEqual(code, 2, text)

    def test_a_build_function_called_without_a_positional_argument_is_no_data(self):
        # (b) the positional twin of (a).
        build = json.dumps(module("target", "def consult(path):\n    return path\n"))
        code, text = run_probe("import target\nfire('positional', lambda: target.consult())\n", build)
        self.assertRegex(text, r"(?m)^NO-DATA\s+positional\s+TypeError", text)
        self.assertEqual(code, 2, text)

    def test_a_build_function_handed_straight_to_fire_is_no_data(self):
        # (a) again with no lambda: fire(label, fn) itself makes the bad call, so the innermost frame is the harness.
        build = json.dumps(module("target", "def consult(*, seams_config):\n    return None\n"))
        code, text = run_probe("import target\nfire('direct', target.consult)\n", build)
        self.assertRegex(text, r"(?m)^NO-DATA\s+direct\s+TypeError", text)
        self.assertEqual(code, 2, text)

    def test_the_build_calling_its_own_helper_without_a_keyword_only_argument_is_a_crash(self):
        # (c) the verifier's case e: a well formed call, and the BODY calls its helper wrong. On the base: NO-DATA.
        build = json.dumps(module("target", "def _helper(x, *, mode):\n    return x\n"
                                            "def consult(x):\n    return _helper(x)\n"))
        code, text = run_probe("import target\nfire('inner_kwonly', lambda: target.consult(1))\n", build)
        self.assertRegex(text, r"(?m)^CRASH\s+inner_kwonly\s+TypeError", text)
        self.assertEqual(code, 1, text)

    def test_the_build_calling_its_own_helper_without_a_positional_argument_is_a_crash(self):
        # (d) the positional twin of (c), the hole open since 2026-09-21. On the base: NO-DATA.
        build = json.dumps(module("target", "def _helper(x, y):\n    return x\n"
                                            "def consult(x):\n    return _helper(x)\n"))
        code, text = run_probe("import target\nfire('inner_positional', lambda: target.consult(1))\n", build)
        self.assertRegex(text, r"(?m)^CRASH\s+inner_positional\s+TypeError", text)
        self.assertEqual(code, 1, text)

    def test_a_clean_refusal_beside_an_inner_arity_defect_is_dirty(self):
        # The verifier's case g, the harm itself: on the base this read "0 CRASH ... 1 REFUSED ... 1 NO-DATA", exit 0.
        build = json.dumps(module("target", "def _helper(x, *, mode):\n    return x\n"
                                            "def consult(x):\n    if x is None:\n        raise ValueError('no')\n"
                                            "    return _helper(x)\n"))
        code, text = run_probe("import target\nfire('refuses', lambda: target.consult(None))\n"
                               "fire('inner', lambda: target.consult(1))\n", build)
        self.assertIn("1 CRASH", text)
        self.assertEqual(code, 1, text)

    def test_a_build_frame_that_borrows_the_probe_file_name_is_still_the_build(self):
        # ONE CONDITION: the globals half of the frame test. The build compiles its body under the probe file's own
        # name (read off the outermost frame at import, the path the interpreter really gave the probe script), so
        # only "this module's globals" tells its frame from the adversary's.
        src = ("import sys\nf = sys._getframe()\nwhile f.f_back is not None:\n    f = f.f_back\n"
               "SRC = 'def _helper(x, *, mode):\\n    return x\\ndef consult(x):\\n    return _helper(x)\\n'\n"
               "exec(compile(SRC, f.f_code.co_filename, 'exec'), globals())\n")
        code, text = run_probe("import target\nfire('forged_name', lambda: target.consult(1))\n",
                               json.dumps(module("target", src)))
        self.assertRegex(text, r"(?m)^CRASH\s+forged_name\s+TypeError", text)
        self.assertEqual(code, 1, text)

    def test_a_build_frame_that_borrows_the_probe_globals_is_still_the_build(self):
        # ONE CONDITION: the file half of the frame test. The build runs its body in the probe module's globals
        # under its own file name, so only the code's file tells its frame from the adversary's.
        src = ("import __main__\n"
               "SRC = 'def _helper(x, *, mode):\\n    return x\\ndef consult(x):\\n    return _helper(x)\\n'\n"
               "exec(compile(SRC, 'target_body.py', 'exec'), __main__.__dict__)\n"
               "consult = __main__.__dict__['consult']\n")
        code, text = run_probe("import target\nfire('forged_globals', lambda: target.consult(1))\n",
                               json.dumps(module("target", src)))
        self.assertRegex(text, r"(?m)^CRASH\s+forged_globals\s+TypeError", text)
        self.assertEqual(code, 1, text)

    def test_real_crashes_inside_a_build_body_stay_crashes(self):
        # (e) one run per crash, so no guard masks another.
        for name, source in (("TypeError", "def consult(x):\n    return len(x)\n"),
                             ("AssertionError", "def consult(x):\n    assert x\n"),
                             ("KeyError", "def consult(x):\n    return {}[x]\n")):
            with self.subTest(name=name):
                code, text = run_probe("import target\nfire('body', lambda: target.consult(None))\n",
                                       json.dumps(module("target", source)))
                self.assertRegex(text, r"(?m)^CRASH\s+body\s+%s" % name, text)
                self.assertEqual(code, 1, text)

    def test_a_missing_module_attribute_is_no_data(self):
        # ONE CONDITION: the module-attribute branch.
        self.assertEqual(P.classify("AttributeError", "builtins", "module 'target' has no attribute 'validate'"), "NO-DATA")

    def test_a_wrong_type_that_reaches_the_body_is_still_a_crash(self):
        # The carve out must stay narrow: failing to CALL is excused, failing to HANDLE is not.
        self.assertEqual(P.classify("TypeError", "builtins", "unhashable type: 'dict'"), "CRASH")

    def test_a_cli_usage_exit_is_still_a_real_refusal(self):
        self.assertEqual(P.classify("SystemExit", "builtins", "2"), "REFUSED")


class TheValueOracleDoesNotGuess(unittest.TestCase):
    """Defect C, second half: bare integers and empty containers were read as refusals with no contract to
    read them against."""

    def test_integers_and_empty_containers_are_not_refusals(self):
        for value in ("1", "2", "3", "{}", "[]", "()", "''"):
            self.assertIsNone(P.REFUSAL_VALUE.search(value), value)

    def test_the_measured_refusal_shapes_still_match(self):
        # The 2026-09-20 measurement that justified this pattern: every case was None, False or a quarantine
        # record. Narrowing must not have thrown those away.
        for value in ("None", "(None, QuarantineRecord(reason='bad id'))", "(False, ('unreadable',), None)",
                      "ok=False", "REJECTED", "no_decision"):
            self.assertIsNotNone(P.REFUSAL_VALUE.search(value), value)


# ---------------------------------------------------------------- the edges requirement 6 names

class Edges(unittest.TestCase):
    def test_zero_probes_executed(self):
        code, text = run_probe('if False:\n    fire("never", lambda: 1)\n')
        self.assertIn("NO-DATA", text)
        self.assertEqual(code, 2)

    def test_exactly_one_probe(self):
        code, _ = run_probe('fire("only", lambda: "ok")\n')
        self.assertEqual(code, 0)

    def test_one_probe_that_crashes(self):
        code, text = run_probe('fire("only", lambda: [][3])\n')
        self.assertIn("CRASH", text)
        self.assertEqual(code, 1)

    def test_all_probes_crash(self):
        code, text = run_probe('for i in range(3):\n    fire("p%d" % i, lambda: {}["k"])\n')
        self.assertEqual(text.count("\nCRASH") + text.startswith("CRASH"), 3)
        self.assertEqual(code, 1)

    def test_every_probe_is_unreached_so_nothing_is_learned(self):
        # All three fire a call that never enters a function body: conclusive count 0, so exit 2, not 0.
        body = ('import types\nm = types.ModuleType("absent_target")\n'
                'for i in range(3):\n    fire("p%d" % i, lambda: m.validate(1))\n')
        code, text = run_probe(body)
        self.assertIn("none of them reached the build", text)
        self.assertEqual(code, 2)

    def test_a_verdict_file_written_twice_never_lets_an_unknown_erase_dirt(self):
        with open(os.path.join(LOOP, "probe_wave.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('if not (verdict == "NO-DATA" and os.path.isfile(done) and open(done, encoding="utf-8").read().strip() == "DIRTY"):', src)

    def test_a_verdict_arriving_after_the_stage_moved_on_reads_as_unknown(self):
        # unit_runner reads the .done file once, after probe_wave returns. A verdict that lands later is simply
        # absent at read time, which is "NO-DATA" -> UNKNOWN -> READY-UNPROBED, retried by probe_round.
        self.assertEqual(outcome("NO-DATA", []), "UNKNOWN")

    def test_the_runner_branches_on_the_outcome_not_on_the_word(self):
        with open(os.path.join(LOOP, "unit_runner.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('if outcome == "UNKNOWN":', src)
        self.assertNotIn('if verdict == "NO-DATA":', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)

# MUTATIONS, one per guard, each proved by exactly one fixture (run and recorded 2026-09-22):
#  unit_runner PROBE_VERDICT["DIRTY"] -> "ADMIT"      : DirtyRefuses                      (only)
#  unit_runner UNKNOWN_VERDICT        -> "ADMIT"      : TheTableAdmitsExactlyOneWord      (only)
#  unit_runner guard 3 (len(finds))   deleted         : EvidenceOutranksTheWord           (only)
#  unit_runner guard 1 (verdict type) deleted         : ...unreadable_verdict_type        (only, raises)
#  unit_runner guard 2 (finds type)   deleted         : ...unreadable_finding_set         (only)
#  probe_build counts line printed before the exit check : ADeadChildOffersNoDenominator  (only)
#  probe_build arity branch -> "REFUSED"              : ...wrong_arity_call_is_no_data    (only)
#  probe_build ARITY keyword-only alternative deleted : ...keyword_only_* (three, 2026-09-29)
#  probe_build at_call guard dropped from the arity line : ...own_helper_* (two) and ...inner_arity_defect_is_dirty
#  probe_build harness at_call always "1"             : ...own_helper_* (two) and ...inner_arity_defect_is_dirty
#  probe_build harness at_call always "0"             : ...build_function_* (three)
#  probe_build harness globals check dropped         : ...borrows_the_probe_file_name    (only)
#  probe_build harness file check dropped            : ...borrows_the_probe_globals      (only)
#  probe_build ARITY dropped from the CRASH screen    : ...no_call_site_flag_is_a_crash
#  probe_wave  done_ok check deleted                  : ...counts_without_a_zero_exit     (only)
