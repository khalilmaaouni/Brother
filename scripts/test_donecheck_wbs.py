#!/usr/bin/env python3
"""FX-15.5: the four parent done checks pass the closer's own screen and stop it on a real reason.

Owns: this file. Reads: scripts/close_unit.py and the four scripts FX-15.1 to FX-15.4 own:
scripts/donecheck_L4.py, scripts/donecheck_L5.py, scripts/donecheck_L5b.py, scripts/donecheck_L5c.py.

This suite has nothing of its own to compute. It proves:

  R-FX-15-5-1  close_unit.screen_done_check(cmd) == [["python3", "scripts/donecheck_<unit>.py"]],
               and the script named by that command exists on disk and defines a top level main.
  R-FX-15-5-2  the same command, run the way the closer runs it, exits 0, 1 or 2 with a last line
               starting PASS:, FAIL: or NO-DATA:, and close_unit.verdict(code, out) returns a non
               empty quote, which is what the closer writes into the unit's evidence.

  R-FX-15-5-3  the launch plan itself carries those exact strings as the done_check of L4, L5, L5b
               and L5c, so the closer runs these commands and not the prose it refused on every pass
               (the loop log of 2026-09-29 printed NOT CLOSED for each). The prose stays beside it
               as done_check_prose, the definition, the same pattern L3 and L5a already use.

It also fires hostile input at every public function in close_unit a close decision routes through
(screen_done_check, landed_subs, closable, write_atomic, main). None of them may answer with a raw
AttributeError or TypeError, and none may answer a value indistinguishable from a legitimate answer:
screen_done_check returns None, landed_subs and closable raise ValueError, write_atomic raises
ValueError before it opens anything, and main raises ValueError on an argv that is not a list of str.

Nothing here imports subprocess: the scripts are run in process through runpy, exactly as the closer
would run their argv, and only their exit code and printed lines are read.

Exit codes: unittest's own. Usage: python3 scripts/test_donecheck_wbs.py
"""
import ast
import contextlib
import io
import json
import os
import runpy
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import close_unit as C  # noqa: E402

# (unit id, the exact done_check string FX-27 writes, the repo relative script that string names)
COMMANDS = (
    ("L4", "python3 scripts/donecheck_L4.py", "scripts/donecheck_L4.py"),
    ("L5", "python3 scripts/donecheck_L5.py", "scripts/donecheck_L5.py"),
    ("L5b", "python3 scripts/donecheck_L5b.py", "scripts/donecheck_L5b.py"),
    ("L5c", "python3 scripts/donecheck_L5c.py", "scripts/donecheck_L5c.py"),
)

RESULT_PREFIXES = ("PASS:", "FAIL:", "NO-DATA:")


def _defines_main(path):
    """True when the file parses and defines a top level function main. Read as bytes and never
    imported, so a module with heavy import side effects is still safe to inspect."""
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        tree = ast.parse(raw.decode("utf-8", "replace"), path)
    except SyntaxError:
        return False
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main":
            return True
    return False


def _exit_code(exc):
    return exc.code if isinstance(exc.code, int) else 1


def _run_script(path):
    """(exit code, stdout + stderr) for one script the closer would run by that repo relative path.

    The closer runs the screened argv as a process; here the same argv's one and only script is
    executed through runpy with a non __main__ run name (so its __main__ block is skipped), and its
    main([]) is called explicitly, so this suite never spawns a child process and never imports
    subprocess. Nothing is resolved from the working directory: the script's own __file__ fixes root.
    """
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            namespace = runpy.run_path(path, run_name="_fx155_script")
        except SystemExit as exc:
            return _exit_code(exc), out.getvalue() + err.getvalue()
        except (ImportError, SyntaxError, OSError, ValueError) as exc:
            return -1, str(exc)
        entry = namespace.get("main")
        if entry is None:
            return -1, out.getvalue() + err.getvalue()
        try:
            code = entry([])
        except SystemExit as exc:
            code = _exit_code(exc)
    return code, out.getvalue() + err.getvalue()


class TheFourCommandsPassTheCloserScreen(unittest.TestCase):
    """R-FX-15-5-1 and R-FX-15-5-2, both on the exact strings FX-27 will write."""

    def test_done_check_commands_are_accepted_and_quoted(self):
        for unit, cmd, rel in COMMANDS:
            # R-FX-15-5-1: the closer's own screen accepts this exact string and produces exactly
            # the one argv it will run. Compared for equality, so an extra token, a re-shaped argv
            # or a refusal of the accepted head is red.
            steps = C.screen_done_check(cmd)
            self.assertEqual(steps, [["python3", rel]],
                             "%s: screen_done_check refused or reshaped %r" % (unit, cmd))
            path = os.path.join(REPO, rel)
            self.assertTrue(os.path.isfile(path), "%s: %s is missing" % (unit, path))
            self.assertTrue(_defines_main(path), "%s: %s defines no main" % (unit, path))

            # R-FX-15-5-2: run the screened argv and read the last line and the closer quote. A
            # NO-DATA refusal is still a quotable reason for the unit's evidence, which is why
            # verdict's own prefix tuple now carries "NO-DATA:".
            code, out = _run_script(path)
            self.assertIn(code, (0, 1, 2), "%s: exit %r from %r" % (unit, code, steps[0]))
            lines = [l.strip() for l in out.splitlines() if l.strip()]
            self.assertTrue(lines, "%s: no output from %r" % (unit, steps[0]))
            self.assertTrue(lines[-1].startswith(RESULT_PREFIXES),
                            "%s: last line %r" % (unit, lines[-1]))
            _ok, quoted = C.verdict(code, out)
            self.assertTrue(quoted,
                            "%s: the closer quote is empty for %r; the reason must reach the "
                            "evidence for every exit code" % (unit, lines[-1]))

    def test_a_nodata_exit_still_yields_a_quote(self):
        # The edge case named in the sub unit's own section: a screened command that exits 2 with a
        # NO-DATA reason must still produce a non empty closer quote.
        _ok, quoted = C.verdict(2, "noise\nNO-DATA: L4.5 has not landed\n")
        self.assertIn("NO-DATA:", quoted)


class ThePlanCarriesTheFourCommands(unittest.TestCase):
    """R-FX-15-5-3, read from the real plan file, never a fixture: a command that exists but is not
    in the plan closes nothing, so this is the half of the unit the closer actually depends on."""

    # The launch plan is private repository data the public export never ships; there this reads NO-DATA as a
    # named skip, the same rule test_board_status.py applies to the live board (2026-09-30: the hermetic push gate
    # refused this test on the export tree and the loop stopped at its next push).
    @unittest.skipUnless(os.path.isfile(os.path.join(REPO, C.PLAN)), "the launch plan %s is not shipped here" % C.PLAN)
    def test_the_plan_names_each_command_and_keeps_the_prose(self):
        with open(os.path.join(REPO, C.PLAN), encoding="utf-8") as fh:
            plan = json.load(fh)
        units = {}
        for u in plan["units"]:
            self.assertNotIn(u["id"], units, "duplicate unit id %r in the plan" % u["id"])
            units[u["id"]] = u
        for unit, cmd, _rel in COMMANDS:
            self.assertIn(unit, units, "%s is missing from the plan" % unit)
            self.assertEqual(units[unit].get("done_check"), cmd,
                             "%s: the plan's done_check is not the command the closer can run" % unit)
            prose = units[unit].get("done_check_prose")
            self.assertTrue(isinstance(prose, str) and prose.strip(),
                            "%s: the definition was dropped instead of kept as done_check_prose" % unit)


class HostileDoneChecksAreRefused(unittest.TestCase):
    """The public screen is called with None, a wrong type, an empty value, NaN, bytes and containers
    that hold the wrong shapes. Every one must be refused by returning None, never by raising."""

    def test_screen_refuses_hostile_commands(self):
        hostile = (
            None, True, False, 0, 42, 3.14, float("nan"), float("inf"),
            b"python3 scripts/donecheck_L4.py", b"\xff\xfe",
            ["python3", "scripts/donecheck_L4.py"], [["python3", "x.py"]],
            ("python3", "x.py"), {"cmd": "python3 x.py"}, {"cmd": ["python3", "x.py"]},
            {"python3 x.py"}, frozenset(["python3 x.py"]),
            object(), type, C.screen_done_check,
        )
        for value in hostile:
            self.assertIsNone(C.screen_done_check(value), "accepted %r" % (value,))
        self.assertIsNone(C.screen_done_check(""))
        self.assertIsNone(C.screen_done_check("   "))


class HostilePlanInputIsRefused(unittest.TestCase):
    """Hostile plan: the plan, the landed collection and every unit field are model authored data.
    None, a bool, a number, NaN, a str where a list belongs, a list where a str belongs, a generator
    and an unhashable sub unit id must all leave as ValueError, never an AttributeError, never a
    TypeError, and never an empty answer that a real empty plan would also give."""

    def test_landed_subs_refuses_hostile_plans(self):
        hostile = (
            None, True, False, 0, 3.14, float("nan"), float("inf"),
            "plan", [("plan",)], ("plan",), (x for x in ()),
            object(), 7, b"{}", set(["plan"]), frozenset(["plan"]),
            {"units": None}, {"units": "no"}, {"units": (x for x in ())},
            {"units": [None, 7, "u"]},
            {"units": [{"sub_units": ["A.1"], "evidence": [1, 2]}]},
            {"units": [{"sub_units": ["A.1"], "evidence": {"x": 1}}]},
            {"units": [{"sub_units": [{"id": "A.1"}], "evidence": "A.1 landed."}]},
            {"units": [{"sub_units": [["A.1"]], "evidence": "A.1 landed."}]},
            {"units": [{"sub_units": "A.1", "evidence": "A.1 landed."}]},
        )
        for value in hostile:
            with self.assertRaises(ValueError, msg="landed_subs accepted %r" % (value,)):
                C.landed_subs(value)
        # a well formed plan still returns its landed ids
        good = {"units": [{"sub_units": ["A.1"], "evidence": "A.1 landed today."}]}
        self.assertEqual(C.landed_subs(good), {"A.1"})

    def test_closable_refuses_hostile_plans(self):
        hostile = (
            None, True, False, 0, 3.14, float("nan"), "plan", [("plan",)], object(),
            {"units": None}, {"units": "no"}, {"units": (x for x in ())},
            {"units": [None, 7]},
            {"units": [{"sub_units": ["A.1"], "state": "OPEN",
                         "done_check": "python3 scripts/donecheck_L4.py"}]},
            {"units": [{"id": "A", "sub_units": [["A.1"]], "state": "OPEN",
                         "done_check": "python3 scripts/donecheck_L4.py"}]},
            {"units": [{"id": ["A"], "sub_units": ["A.1"], "state": "OPEN",
                         "done_check": "python3 scripts/donecheck_L4.py"}]},
        )
        for value in hostile:
            with self.assertRaises(ValueError, msg="closable accepted %r" % (value,)):
                C.closable(value, {"A.1"})
        # one well formed unit is still closable
        good = {"units": [{"id": "A", "sub_units": ["A.1"], "state": "OPEN",
                           "done_check": "python3 scripts/donecheck_L4.py"}]}
        self.assertEqual(C.closable(good, {"A.1"}), ["A"])

    def test_closable_refuses_a_hostile_landed_collection(self):
        plan = {"units": [{"id": "A", "sub_units": ["A.1"], "state": "OPEN",
                           "done_check": "python3 scripts/donecheck_L4.py"}]}
        for landed in (None, 7, 3.14, float("nan"), object(), (x for x in ()), b"A.1", "A.1"):
            with self.assertRaises(ValueError, msg="closable accepted landed=%r" % (landed,)):
                C.closable(plan, landed)
        self.assertEqual(C.closable(plan, {"A.1"}), ["A"])

    def test_main_refuses_a_hostile_argv(self):
        # probe-crash: main was reached with a bool and an int and raised a raw TypeError out of
        # list(). It now refuses argv that is neither None nor a list/tuple of str, with ValueError.
        for argv in (True, False, 0, 7, 3.14, float("nan"), b"x", "L4", {"units": ["L4"]}, object()):
            with self.assertRaises(ValueError, msg="main accepted argv=%r" % (argv,)):
                C.main(argv)


class WriteAtomicRefusesHostileInput(unittest.TestCase):
    """probe-crash, the one writer: a path that is not a non empty str, a body that is not str, a
    directory target and a directory that does not exist all leave as ValueError BEFORE the temp file
    is created, so a refused write leaves the target and its folder exactly as they were."""

    def test_a_hostile_path_is_refused_with_value_error(self):
        with tempfile.TemporaryDirectory(prefix="fx155-write-") as tmp:
            for path in (None, True, 0, 3.14, float("nan"), b"plan.json", ["plan.json"],
                         {"p": 1}, object(), ""):
                try:
                    C.write_atomic(path, "{}")
                except ValueError:
                    continue
                self.fail("write_atomic accepted the hostile path %r" % (path,))
            self.assertEqual(sorted(os.listdir(tmp)), [], "a refused write left a file behind")

    def test_a_hostile_body_is_refused_before_anything_is_written(self):
        with tempfile.TemporaryDirectory(prefix="fx155-write-") as tmp:
            good = os.path.join(tmp, "plan.json")
            with open(good, "w") as fh:
                fh.write("old")
            for text in (None, True, 0, 3.14, float("nan"), b"{}", ["{}"], {"t": 1}, object()):
                try:
                    C.write_atomic(good, text)
                except ValueError:
                    continue
                self.fail("write_atomic accepted the hostile body %r" % (text,))
            with open(good) as fh:
                self.assertEqual(fh.read(), "old")
            self.assertEqual(sorted(os.listdir(tmp)), ["plan.json"])

    def test_a_missing_directory_or_a_directory_target_is_refused(self):
        with tempfile.TemporaryDirectory(prefix="fx155-write-") as tmp:
            try:
                C.write_atomic(os.path.join(tmp, "nope", "plan.json"), "{}")
            except ValueError:
                pass
            else:
                self.fail("write_atomic wrote into a directory that does not exist")
            try:
                C.write_atomic(tmp, "{}")
            except ValueError:
                pass
            else:
                self.fail("write_atomic wrote over a directory")
            self.assertEqual(os.listdir(tmp), [])

    def test_a_good_write_still_lands_and_leaves_no_temporary(self):
        with tempfile.TemporaryDirectory(prefix="fx155-write-") as tmp:
            good = os.path.join(tmp, "plan.json")
            C.write_atomic(good, "new")
            with open(good) as fh:
                self.assertEqual(fh.read(), "new")
            self.assertEqual(sorted(os.listdir(tmp)), ["plan.json"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
