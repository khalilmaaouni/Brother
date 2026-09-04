#!/usr/bin/env python3
"""Drive release_note_perturb backwards on a fixture tree, never the live one.

Every verdict class has to be reachable and has to be reachable for the right
reason. The two that matter most, and the two this estate has been burned by
before:

  * a file the suite only IMPORTS must read as not covered. A checker that
    called an import coverage would have passed the exact table row (loom.py
    under test_brother_run.py) the external critic disproved, and would have
    read green for as long as the defect stood.
  * the restore check must have teeth. It is driven here by making the
    digest function disagree with itself, which is the only way to prove the
    comparison is load-bearing rather than decorative.

The fixture tree is three small modules and a suite under a temporary
directory. Nothing here runs a suite from the real repository, so this test
can never perturb a tracked file.
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release_note_perturb as P  # noqa: E402

CALLED = '''"""A module the fixture suite really drives."""


def greet():
    return "hi"


if __name__ == "__main__":
    print(greet())
'''

IMPORTED_ONLY = '''"""A module the fixture suite imports and never calls."""


def unused():
    return 1
'''

NO_GUARD = '''"""A module with no __main__ guard at all."""


def value():
    return 7
'''

SUITE = '''import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mod_called
import mod_imported  # noqa: F401  imported, never called, on purpose

assert mod_called.greet() == "hi"
print("OK")
'''

RED_SUITE = '''import sys
print("this suite is red before anything is perturbed")
sys.exit(1)
'''


def fixture_tree(tmp, table_rows, heading=True):
    """A tiny repository: scripts/ with two modules and a suite, plus a note
    whose table carries `table_rows` as (claim, file, suite) triples."""
    P.reset_ledger()  # a new fixture tree is a new run
    scripts = os.path.join(tmp, "scripts")
    releases = os.path.join(tmp, "docs", "releases")
    os.makedirs(scripts, exist_ok=True)
    os.makedirs(releases, exist_ok=True)
    for name, body in (("mod_called.py", CALLED),
                       ("mod_imported.py", IMPORTED_ONLY),
                       ("mod_no_guard.py", NO_GUARD),
                       ("test_calls.py", SUITE),
                       ("test_red.py", RED_SUITE)):
        with open(os.path.join(scripts, name), "w", encoding="utf-8") as fh:
            fh.write(body)
    lines = ["# fixture note", ""]
    if heading:
        lines.append(P.TABLE_HEADING)
        lines.append("")
        lines.append("| Claim | Source file | Goes red under |")
        lines.append("|---|---|---|")
        for claim, rel, suite in table_rows:
            lines.append("| %s | `%s` | %s |"
                         % (claim, rel,
                            P.NODATA if suite is None else "`%s`" % suite))
    lines.append("")
    path = os.path.join(releases, "9.9.9.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path


class TheInjectionPoint(unittest.TestCase):
    def test_the_guard_line_is_found(self):
        self.assertEqual(P.guard_lineno(CALLED), 8)

    def test_a_module_without_a_guard_reports_none(self):
        self.assertIsNone(P.guard_lineno(NO_GUARD))

    def test_the_block_lands_above_the_guard_not_after_it(self):
        out = P.perturbed_source(CALLED)
        self.assertLess(out.index("_release_note_perturb_install()"),
                        out.index('if __name__ == "__main__":'))

    def test_a_guardless_module_takes_the_block_at_the_end(self):
        out = P.perturbed_source(NO_GUARD)
        self.assertTrue(out.rstrip().endswith("_release_note_perturb_install()"))

    def test_the_perturbed_source_is_still_valid_python(self):
        import ast
        for body in (CALLED, NO_GUARD, IMPORTED_ONLY):
            ast.parse(P.perturbed_source(body))

    def test_a_file_that_is_not_python_is_refused_not_guessed(self):
        self.assertIsNone(P.perturbed_source("def (:\n"))


class TheImportIsNotCoverage(unittest.TestCase):
    """The defect this whole tool exists for: a suite that merely imports a
    file must not be reported as covering it."""

    def test_a_module_the_suite_calls_goes_red(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_tree(tmp, [])
            verdict, detail = P.covers("scripts/test_calls.py",
                                       "scripts/mod_called.py", root=tmp)
            self.assertIs(verdict, True, detail)

    def test_a_module_the_suite_only_imports_stays_green(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_tree(tmp, [])
            verdict, detail = P.covers("scripts/test_calls.py",
                                       "scripts/mod_imported.py", root=tmp)
            self.assertIs(verdict, False, detail)
            self.assertIn("stayed green", detail)

    def test_a_suite_already_red_is_no_data_never_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_tree(tmp, [])
            verdict, detail = P.covers("scripts/test_red.py",
                                       "scripts/mod_called.py", root=tmp)
            self.assertIsNone(verdict)
            self.assertIn("not green before the perturbation", detail)

    def test_a_missing_file_is_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_tree(tmp, [])
            verdict, detail = P.covers("scripts/test_calls.py",
                                       "scripts/nope.py", root=tmp)
            self.assertIsNone(verdict)
            self.assertIn("unreadable", detail)

    def test_a_missing_suite_is_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_tree(tmp, [])
            verdict, detail = P.covers("scripts/test_nope.py",
                                       "scripts/mod_called.py", root=tmp)
            self.assertIsNone(verdict)
            self.assertIn("does not exist", detail)


class TheRestore(unittest.TestCase):
    def test_the_file_comes_back_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_tree(tmp, [])
            path = os.path.join(tmp, "scripts", "mod_called.py")
            with open(path, "rb") as fh:
                before = fh.read()
            P.covers("scripts/test_calls.py", "scripts/mod_called.py",
                     root=tmp)
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(), before)

    def test_the_file_comes_back_even_when_the_suite_crashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_tree(tmp, [])
            path = os.path.join(tmp, "scripts", "mod_called.py")
            with open(path, "rb") as fh:
                before = fh.read()
            original_run = P.run_suite
            calls = {"n": 0}

            def exploding(rel, root=None):
                calls["n"] += 1
                if calls["n"] > 1:
                    raise KeyboardInterrupt("suite interrupted")
                return original_run(rel, root=root)

            P.run_suite = exploding
            try:
                with self.assertRaises(KeyboardInterrupt):
                    P.covers("scripts/test_calls.py", "scripts/mod_called.py",
                             root=tmp)
            finally:
                P.run_suite = original_run
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(), before)

    def test_a_restore_that_does_not_match_is_a_failure_not_a_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_tree(tmp, [])
            original_hash = P.sha256_bytes
            seen = {"n": 0}

            def drifting(data):
                seen["n"] += 1
                return original_hash(data) + ("x" if seen["n"] > 1 else "")

            P.sha256_bytes = drifting
            try:
                with self.assertRaises(P.RestoreFailed) as ctx:
                    P.covers("scripts/test_calls.py", "scripts/mod_called.py",
                             root=tmp)
            finally:
                P.sha256_bytes = original_hash
            self.assertIn("did not restore byte-identically", str(ctx.exception))


class TheLedger(unittest.TestCase):
    """The per-measurement restore check is not enough on its own. The first
    full run of this tool restored every file it perturbed and still finished
    with scripts/decide.py carrying an injected block, so something wrote it
    after the restore returned. The ledger is what turns that from a silently
    dirty tree into a named refusal at the very next step."""

    def setUp(self):
        self.saved = dict(P._RESTORE_LEDGER)
        P._RESTORE_LEDGER.clear()
        self.addCleanup(self._restore_ledger)

    def _restore_ledger(self):
        P._RESTORE_LEDGER.clear()
        P._RESTORE_LEDGER.update(self.saved)

    def test_an_empty_ledger_passes(self):
        P.check_ledger()

    def test_a_measurement_records_the_file_it_restored(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_tree(tmp, [])
            P.covers("scripts/test_calls.py", "scripts/mod_called.py",
                     root=tmp)
            self.assertIn(os.path.join(tmp, "scripts", "mod_called.py"),
                          P._RESTORE_LEDGER)
            P.check_ledger()

    def test_a_file_written_after_its_measurement_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_tree(tmp, [])
            path = os.path.join(tmp, "scripts", "mod_called.py")
            P.covers("scripts/test_calls.py", "scripts/mod_called.py",
                     root=tmp)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("# somebody else wrote here\n")
            with self.assertRaises(P.RestoreFailed) as ctx:
                P.check_ledger()
            self.assertIn("mod_called.py", str(ctx.exception))
            self.assertIn("changed after this run restored it",
                          str(ctx.exception))

    def test_the_next_measurement_refuses_on_a_dirty_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_tree(tmp, [])
            path = os.path.join(tmp, "scripts", "mod_called.py")
            P.covers("scripts/test_calls.py", "scripts/mod_called.py",
                     root=tmp)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("# somebody else wrote here\n")
            with self.assertRaises(P.RestoreFailed):
                P.covers("scripts/test_calls.py", "scripts/mod_imported.py",
                         root=tmp)

    def test_the_whole_verdict_reports_a_late_write_as_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            note = fixture_tree(tmp, [("called", "scripts/mod_called.py",
                                       "scripts/test_calls.py")])
            path = os.path.join(tmp, "scripts", "mod_called.py")
            original_covers = P.covers

            def covers_then_dirty(*args, **kwargs):
                out = original_covers(*args, **kwargs)
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write("# a late writer\n")
                return out

            P.covers = covers_then_dirty
            try:
                code, lines = P.drive(note, root=tmp)
            finally:
                P.covers = original_covers
            self.assertEqual(code, 1, "\n".join(lines))
            self.assertTrue(any("did not come back clean" in ln
                                for ln in lines), "\n".join(lines))


class TheTimeoutWall(unittest.TestCase):
    def setUp(self):
        P.reset_ledger()
        self.addCleanup(P.reset_ledger)

    def test_an_untimed_suite_gets_the_flat_fallback(self):
        self.assertEqual(P.wall_for("scripts/never_run.py"), P.TIMEOUT)

    def test_the_wall_follows_the_suites_own_green_runtime(self):
        # Well above the floor on purpose: a duration whose multiple lands
        # under 60 would be testing the floor, not the calibration.
        P._LAST_DURATION["scripts/slow.py"] = 100.0
        self.assertEqual(P.wall_for("scripts/slow.py"),
                         int(100.0 * P.WALL_FACTOR) + 1)
        self.assertGreater(P.wall_for("scripts/slow.py"), 60)

    def test_a_very_fast_suite_still_gets_a_floor(self):
        P._LAST_DURATION["scripts/instant.py"] = 0.01
        self.assertEqual(P.wall_for("scripts/instant.py"), 60)

    def test_a_timed_out_run_does_not_calibrate_the_wall(self):
        """A run that never finished says nothing about how long this suite
        takes when it behaves, so it must not shrink the next wall."""
        with tempfile.TemporaryDirectory() as tmp:
            fixture_tree(tmp, [])
            with open(os.path.join(tmp, "scripts", "test_hangs.py"), "w",
                      encoding="utf-8") as fh:
                fh.write("import time\nwhile True:\n    time.sleep(1)\n")
            P.run_suite("scripts/test_hangs.py", root=tmp, timeout=2)
            self.assertNotIn("scripts/test_hangs.py", P._LAST_DURATION)
            self.assertEqual(P.wall_for("scripts/test_hangs.py"), P.TIMEOUT)

    def test_a_green_run_records_its_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_tree(tmp, [])
            P.run_suite("scripts/test_calls.py", root=tmp)
            self.assertIn("scripts/test_calls.py", P._LAST_DURATION)

    def test_a_red_run_does_not_widen_the_wall(self):
        """The runaway this tool actually had: a perturbed run that finished
        slowly recorded its own duration, which widened the wall for the next
        perturbation, which was allowed to run longer still. Only green
        calibrates."""
        with tempfile.TemporaryDirectory() as tmp:
            fixture_tree(tmp, [])
            P.run_suite("scripts/test_red.py", root=tmp)
            self.assertNotIn("scripts/test_red.py", P._LAST_DURATION)
            self.assertEqual(P.wall_for("scripts/test_red.py"), P.TIMEOUT)

    def test_a_perturbed_measurement_leaves_the_wall_where_green_put_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_tree(tmp, [])
            P.run_suite("scripts/test_calls.py", root=tmp)
            green = P._LAST_DURATION["scripts/test_calls.py"]
            # baseline=0 so the only run this call makes is the perturbed one.
            P.covers("scripts/test_calls.py", "scripts/mod_called.py",
                     root=tmp, baseline=0)
            self.assertEqual(P._LAST_DURATION["scripts/test_calls.py"], green)

    def test_a_hanging_suite_is_no_data_not_a_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_tree(tmp, [])
            with open(os.path.join(tmp, "scripts", "test_hangs.py"), "w",
                      encoding="utf-8") as fh:
                fh.write("import time\nwhile True:\n    time.sleep(1)\n")
            rc, tail = P.run_suite("scripts/test_hangs.py", root=tmp,
                                   timeout=2)
            self.assertIsNone(rc)
            self.assertIn("did not finish within 2s", tail)

    def test_a_grandchild_does_not_outlive_the_wall(self):
        """subprocess.run's timeout kills the direct child only, and these
        suites spawn their subject as a subprocess. An orphan that survives
        can write into the tree after the restore, which is exactly the shape
        that left a file dirty on the first full run."""
        with tempfile.TemporaryDirectory() as tmp:
            fixture_tree(tmp, [])
            marker = os.path.join(tmp, "grandchild-was-here.txt")
            child = os.path.join(tmp, "scripts", "grandchild.py")
            with open(child, "w", encoding="utf-8") as fh:
                fh.write("import time\n"
                         "time.sleep(6)\n"
                         "with open(%r, 'w') as out:\n"
                         "    out.write('here')\n" % marker)
            with open(os.path.join(tmp, "scripts", "test_spawns.py"), "w",
                      encoding="utf-8") as fh:
                fh.write("import subprocess\nimport sys\nimport time\n"
                         "subprocess.Popen([sys.executable, %r])\n"
                         "while True:\n    time.sleep(1)\n" % child)
            rc, _tail = P.run_suite("scripts/test_spawns.py", root=tmp,
                                    timeout=2)
            self.assertIsNone(rc)
            time.sleep(8)
            self.assertFalse(os.path.exists(marker),
                             "an orphaned grandchild outlived the timeout and "
                             "wrote into the tree")


class TheTableParser(unittest.TestCase):
    def test_rows_read_back_with_their_suite(self):
        text = "\n".join([
            P.TABLE_HEADING, "",
            "| Claim | Source file | Goes red under |",
            "|---|---|---|",
            "| a | `scripts/x.py` | `scripts/test_x.py` |",
            "| b | `scripts/y.py` | NO-DATA |",
            ""])
        self.assertEqual(P.parse_table(text),
                         [("a", "scripts/x.py", "scripts/test_x.py"),
                          ("b", "scripts/y.py", None)])

    def test_a_note_with_no_table_reads_empty_never_a_pass(self):
        self.assertEqual(P.parse_table("# a note\n\nno table here\n"), [])

    def test_the_next_heading_ends_the_table(self):
        text = "\n".join([
            P.TABLE_HEADING, "",
            "| Claim | Source file | Goes red under |",
            "|---|---|---|",
            "| a | `scripts/x.py` | `scripts/test_x.py` |",
            "", "## Something else", "",
            "| z | `scripts/z.py` | `scripts/test_z.py` |", ""])
        self.assertEqual(P.parse_table(text),
                         [("a", "scripts/x.py", "scripts/test_x.py")])


class TheWholeVerdict(unittest.TestCase):
    def test_every_row_red_is_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            note = fixture_tree(tmp, [("called", "scripts/mod_called.py",
                                       "scripts/test_calls.py")])
            code, lines = P.drive(note, root=tmp)
            self.assertEqual(code, 0, "\n".join(lines))
            self.assertIn("release-note-perturb: PASS", lines[-1])

    def test_one_import_only_row_fails_the_whole_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            note = fixture_tree(tmp, [
                ("called", "scripts/mod_called.py", "scripts/test_calls.py"),
                ("imported", "scripts/mod_imported.py",
                 "scripts/test_calls.py")])
            code, lines = P.drive(note, root=tmp)
            self.assertEqual(code, 1, "\n".join(lines))
            self.assertTrue(any("mod_imported.py" in ln and ln.startswith("FAIL")
                                for ln in lines), "\n".join(lines))

    def test_a_no_data_suite_column_never_counts_as_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            note = fixture_tree(tmp, [("orphan", "scripts/mod_imported.py",
                                       None)])
            code, lines = P.drive(note, root=tmp)
            self.assertEqual(code, 2, "\n".join(lines))
            self.assertTrue(any(ln.startswith(P.NODATA) for ln in lines))

    def test_a_note_with_no_table_is_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            note = fixture_tree(tmp, [], heading=False)
            code, lines = P.drive(note, root=tmp)
            self.assertEqual(code, 2, "\n".join(lines))
            self.assertIn("carries no", lines[-1])

    def test_a_missing_note_is_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, lines = P.drive(os.path.join(tmp, "nope.md"), root=tmp)
            self.assertEqual(code, 2, "\n".join(lines))


class TheSiblingFallback(unittest.TestCase):
    def test_the_convention_name_is_found_when_it_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_tree(tmp, [])
            os.rename(os.path.join(tmp, "scripts", "test_calls.py"),
                      os.path.join(tmp, "scripts", "test_mod_called.py"))
            self.assertEqual(P.sibling_suite("scripts/mod_called.py", root=tmp),
                             "scripts/test_mod_called.py")

    def test_no_sibling_reads_none_rather_than_a_guess(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_tree(tmp, [])
            self.assertIsNone(P.sibling_suite("scripts/mod_imported.py",
                                              root=tmp))


class E116TheRealPerturbationIsALongCheckTheBatterySkips(unittest.TestCase):
    """This tool runs one full suite per file row in the release note, tens of
    minutes on this tree, and it rewrites tracked source files in place while
    it does. scripts/check_all.sh registered it as an ordinary step, so every
    battery round paid that and two concurrent rounds raced on those files.

    It is gated now: skipped unless BROTHER_LONG_CHECKS=1, which the release
    cut sets. Driven both ways here, because a gate nobody drove backwards is
    a claim. The gate's own lines are read out of check_all.sh between its
    markers and run VERBATIM, so this proves the shipped text rather than a
    paraphrase of it; only run_check() and the counters come with it, and the
    real battery is never started.
    """

    CHECK_ALL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "check_all.sh")
    BEGIN = "# E116-LONG-CHECK-BEGIN release-note-perturb"
    END = "# E116-LONG-CHECK-END"

    def check_all_lines(self):
        with open(self.CHECK_ALL, encoding="utf-8") as fh:
            return fh.readlines()

    def shell_function(self, name):
        """The literal text of `<name>() { ... }` from check_all.sh, matched
        by its own opening and closing braces so a later edit that moves the
        function still gets the current body."""
        lines = self.check_all_lines()
        opener = "%s() {" % name
        start = next((i for i, l in enumerate(lines)
                      if l.startswith(opener)), None)
        self.assertIsNotNone(start, "%s is not defined in check_all.sh" % name)
        end = next(i for i in range(start, len(lines))
                   if lines[i].rstrip("\n") == "}")
        return "".join(lines[start:end + 1])

    def gate_block(self):
        """The shipped gate, verbatim, between its own markers."""
        lines = self.check_all_lines()
        start = next((i for i, l in enumerate(lines)
                      if l.strip() == self.BEGIN), None)
        self.assertIsNotNone(start, "the gate's BEGIN marker is gone")
        end = next((i for i in range(start, len(lines))
                    if lines[i].strip() == self.END), None)
        self.assertIsNotNone(end, "the gate's END marker is gone")
        return "".join(lines[start + 1:end])

    def drive(self, env_value):
        """Runs the shipped gate against a fake scripts/release_note_perturb.py
        in a temp tree, so whether the real command line ran is a fact on disk
        rather than an inference from the printed line. The gate's own text is
        not edited: the fake sits at the path the gate names.

        Returns (stdout, ran, exit)."""
        with tempfile.TemporaryDirectory() as tmp:
            os.mkdir(os.path.join(tmp, "scripts"))
            marker = os.path.join(tmp, "it-ran")
            fake = os.path.join(tmp, "scripts", "release_note_perturb.py")
            with open(fake, "w", encoding="utf-8") as fh:
                fh.write("with open(%r, 'w') as m:\n    m.write('ran')\n"
                         "print('fake step done')\n" % marker)
            harness = os.path.join(tmp, "harness.sh")
            with open(harness, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\n"
                         "pass=0; fail=0; nodata=0\n"
                         'failed_names=""; nodata_names=""\n'
                         + self.shell_function("run_check")
                         + "\n"
                         + self.gate_block()
                         + '\necho "counters pass=$pass fail=$fail '
                           'nodata=$nodata names:$nodata_names"\n')
            os.chmod(harness, 0o755)
            env = dict(os.environ)
            if env_value is None:
                env.pop("BROTHER_LONG_CHECKS", None)
            else:
                env["BROTHER_LONG_CHECKS"] = env_value
            proc = subprocess.run(["sh", harness], cwd=tmp, env=env,
                                  capture_output=True, text=True, timeout=60)
            return proc.stdout, os.path.exists(marker), proc.returncode

    def test_the_registration_is_inside_the_gate_not_beside_it(self):
        """The command line itself is unchanged, so scripts/system_doc.py's
        battery parser still finds it; what changed is that it now sits
        between the gate's markers."""
        block = self.gate_block()
        self.assertIn('run_check "release-note-perturb"', block)
        self.assertIn("python3 scripts/release_note_perturb.py", block)
        self.assertIn('"${BROTHER_LONG_CHECKS:-0}" = "1"', block)
        lines = [l for l in self.check_all_lines()
                 if l.startswith('run_check "release-note-perturb"')]
        self.assertEqual(len(lines), 1, lines)

    def test_unset_skips_the_command_and_reads_no_data_never_pass(self):
        out, ran, code = self.drive(None)
        self.assertFalse(ran, "the long command ran without being asked to")
        self.assertIn("NO-DATA", out)
        self.assertNotIn("PASS", out)
        self.assertIn("BROTHER_LONG_CHECKS=1", out)
        self.assertIn("nodata=1", out)
        self.assertIn("names: release-note-perturb", out)
        self.assertEqual(code, 0)

    def test_the_expected_duration_is_printed_before_it_starts(self):
        out, ran, _code = self.drive("1")
        self.assertTrue(ran, "the long command did not run when asked to")
        self.assertIn("expected about 90 minute(s)", out)
        self.assertLess(out.index("expected about 90 minute(s)"),
                        out.index("fake step done"),
                        "the duration was printed after the step, not before")

    def test_set_to_one_runs_it_and_it_counts_as_a_pass(self):
        out, ran, _code = self.drive("1")
        self.assertTrue(ran)
        self.assertIn("PASS", out)
        self.assertIn("pass=1", out)
        self.assertIn("nodata=0", out)

    def test_any_other_value_does_not_turn_it_on(self):
        for value in ("0", "yes", "true", ""):
            out, ran, _code = self.drive(value)
            self.assertFalse(ran, "BROTHER_LONG_CHECKS=%r ran it" % value)
            self.assertIn("NO-DATA", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
