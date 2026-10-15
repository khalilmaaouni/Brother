#!/usr/bin/env python3
"""1.1.4: a landing rejection is the fact the next attempt needs, kept as the sub unit's diagnosis note, and bounded.

WHAT WAS WRONG (measured 2026-10-14, first live 1.1.4 run): LA5.b built to grader PASS, the landing refused it on one
neighbour test and wrote QUARANTINE, and nothing turned that refusal into a fact the pool accepts. The diagnosis note is
the fact runner_pool.fact_time counts and unit_runner carries into the brief, but only the diagnostician lane writes one,
and that lane needs a launch variable and a unit that cannot run while the unit behind it is held. Every unit depended on
LA5, the pool drained and the driver stopped.

THE CONTROL: land_batch.reject_status is the one place every landing rejection writes its STATUS (the drop, the gates
alone, a commit refusal, the unwind). It also keeps the rejection as the sub unit's note: the failing test and its first
assertion line, bounded, prefixed LANDING REFUSAL. The same failure REJECTION_REPEAT_LIMIT times writes no new note and
parks the sub unit with a cause and a remedy instead.

ENTRY POINTS, NOT HELPERS. The cases that matter run land_batch.py's main as the loop runs it (the Fixture of
test_land_batch_landings.py: a scratch tree, a bare upstream, a scratch HOME) and read the note file the landing wrote
under that HOME; the pool cases run runner_pool.py's main over the note the landing wrote. The in process cases drive
reject_status and unwind themselves for the properties a landing cannot reach cheaply.

Run: python3 -B scripts/loop/test_land_batch_rejection_fact.py
"""
import json, os, shutil, sys, tempfile, time, unittest, unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import diag_notes as DN          # noqa: E402
import land_batch as LB          # noqa: E402
import test_land_batch_landings as LL   # noqa: E402
import test_runner_pool_guard as PG     # noqa: E402
import test_land_batch_unwind as UW     # noqa: E402

SUB, UNIT = LL.SUB, LL.UNIT
# a neighbour whose two tests each pass on the base tree and fail only when the build changes the module they read
NEIGHBOUR = ("import os, sys, unittest\nsys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\nimport u1mod\n\n\n"
             "class T(unittest.TestCase):\n    def test_x_is_zero(self):\n        self.assertEqual(u1mod.X, 0)\n\n"
             "    def test_y_is_zero(self):\n        self.assertEqual(u1mod.Y, 0)\n\n\nunittest.main(argv=['x'], exit=True)\n")
TREE = {"scripts/u1mod.py": "X = 0\nY = 0\n", "scripts/test_u1mod.py": NEIGHBOUR, "scripts/check_all.sh": LL.Landings.NEIGHBOUR_REGISTRY}


def set_module(x=0, y=0):
    return {"edits": [{"path": "scripts/u1mod.py", "new_file_content": "X = %d\nY = %d\n" % (x, y)}], "tests": []}


class Harness(object):
    """The shared harness: one landing fixture per case, the notes folder under its scratch HOME."""
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="rejfact-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.code = os.path.join(self.root, "code")
        LL.write(os.path.join(self.code, "scripts", "close_unit.py"), LL.CLOSE_UNIT_STUB)

    def fixture(self, **kw):
        return LL.Fixture(os.path.join(self.root, "f"), code_root=self.code, **kw)

    def refused(self, f, build):
        """Land `build` (the edits of the fixture's one sub unit) from a READY status; return the output of the refusal."""
        f.edits(build)
        LL.write(f.status, "READY %s (round 0)\n" % f.build)
        rc, out = f.land()
        self.assertNotEqual(rc, 0, out)
        return out

    def notes_dir(self, f):
        return os.path.join(f.home, ".claude", "evidence", "diag-notes")

    def note(self, f):
        """The sub unit's line in its unit's note file, or ''."""
        return DN.note_for(UNIT, SUB, notes=self.notes_dir(f))

    def status(self, f):
        with open(f.status, encoding="utf-8") as fh:
            return fh.read()


class GatesAlone(Harness, unittest.TestCase):
    def test_a_gate_refusal_writes_a_note_naming_the_failing_test_and_its_assertion(self):
        f = self.fixture(tree_files=TREE)
        out = self.refused(f, set_module(x=1))
        self.assertIn("QUARANTINED", out)
        self.assertTrue(self.status(f).startswith("QUARANTINE refused at the gates alone"), self.status(f))
        note = self.note(f)
        self.assertRegex(note, r"^LANDING REFUSAL 1 \(gates\) sig=[0-9a-f]{12}: ", note)
        self.assertIn("scripts/test_u1mod.py", note, "the gate's failing neighbour is named")
        self.assertIn("test_x_is_zero", note, "the failing test is named")
        self.assertIn("AssertionError", note, "its first assertion line travels with it")
        self.assertIn("NOTE    ", out)

    def test_the_note_is_one_bounded_line(self):
        huge = NEIGHBOUR.replace("self.assertEqual(u1mod.X, 0)", "self.assertEqual(u1mod.X, 0, 'long ' * 2000)")
        f = self.fixture(tree_files=dict(TREE, **{"scripts/test_u1mod.py": huge}))
        self.refused(f, set_module(x=1))
        path = DN.note_path(UNIT, notes=self.notes_dir(f))
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertLess(len(lines[0]), 900, "the note stays inside write_note's own cut")
        body = self.note(f).split(": ", 1)[1]
        self.assertLessEqual(len(body), 300, "a few hundred characters, never the whole log")
        self.assertIn("test_x_is_zero", body, "the bound cuts the tail, not the failing test's name")

    def test_the_second_identical_refusal_parks_with_a_cause_and_a_remedy_and_writes_no_note(self):
        f = self.fixture(tree_files=TREE)
        self.refused(f, set_module(x=1))
        path = DN.note_path(UNIT, notes=self.notes_dir(f))
        with open(path, "rb") as fh:
            first = fh.read()
        then = os.path.getmtime(path) - 100
        os.utime(path, (then, then))   # an old time: a rewrite would move it
        out = self.refused(f, set_module(x=7))   # a different number, the same failure
        status = self.status(f)
        self.assertTrue(status.startswith("WITHHELD at landing: "), status)
        self.assertIn("; remedy: ", status, "the LD1 shape: a cause, then the remedy")
        self.assertIn("test_x_is_zero", status, "the failing test is named on the parked unit")
        self.assertEqual(len(status.splitlines()), 1, "one line, never a second a reader mistakes for a verdict")
        self.assertIn("PARKED  ", out)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), first, "no new note")
        self.assertEqual(os.path.getmtime(path), then, "the fact time the pool reads did not move")

    def test_a_different_failure_the_second_time_writes_a_new_note_and_does_not_park(self):
        f = self.fixture(tree_files=TREE)
        self.refused(f, set_module(x=1))
        first = self.note(f)
        out = self.refused(f, set_module(y=1))   # test_y_is_zero fails, not test_x_is_zero
        self.assertTrue(self.status(f).startswith("QUARANTINE refused at the gates alone"), self.status(f))
        second = self.note(f)
        self.assertNotEqual(second, first)
        self.assertTrue(second.startswith("LANDING REFUSAL 1 ("), "a new failure restarts the count: " + second)
        self.assertIn("test_y_is_zero", second)
        self.assertNotIn("PARKED", out)

    def test_a_gate_that_is_not_a_neighbour_names_its_own_failing_test(self):
        red = "print('FAIL: test_registered (m.T)')\nprint('AssertionError: suite not registered')\nraise SystemExit(1)\n"
        f = self.fixture(tree_files={"scripts/test_battery_registration.py": red})
        self.refused(f, {"edits": [{"path": "docs/landed-u1a.txt", "new_file_content": "landed\n"}], "tests": []})
        note = self.note(f)
        self.assertRegex(note, r"^LANDING REFUSAL 1 \(gates\) sig=[0-9a-f]{12}: ", note)
        self.assertIn("test_registered", note)
        self.assertIn("suite not registered", note)

    def test_a_landing_bin_without_the_notes_module_still_quarantines_and_says_so(self):
        f = self.fixture(tree_files=TREE)
        os.remove(os.path.join(f.bin, "diag_notes.py"))
        out = self.refused(f, set_module(x=1))
        self.assertIn("could not record the landing refusal", out)
        self.assertTrue(self.status(f).startswith("QUARANTINE refused at the gates alone"), self.status(f))

    def test_a_parked_sub_unit_is_a_withheld_status_the_pool_counts_as_parked(self):
        f = self.fixture(tree_files=TREE)
        self.refused(f, set_module(x=1))
        self.refused(f, set_module(x=1))
        self.assertEqual(self.status(f).split()[0], "WITHHELD")

    def test_an_unwritable_notes_folder_never_blocks_the_landing_and_is_reported(self):
        f = self.fixture(tree_files=TREE)
        LL.write(self.notes_dir(f), "a file where the notes folder should be\n")
        out = self.refused(f, set_module(x=1))
        self.assertIn("could not record the landing refusal", out)
        self.assertIn("QUARANTINED", out, "the quarantine itself still happened")
        self.assertTrue(self.status(f).startswith("QUARANTINE refused at the gates alone"), self.status(f))
        self.assertEqual(f.rev(LL.BRANCH, cwd=f.hub), f.base, "nothing reached the remote, as before")

    def test_a_note_the_diagnostician_wrote_is_replaced_never_misread_as_a_count(self):
        f = self.fixture(tree_files=TREE)
        DN.write_note(UNIT, SUB, "VERIFIED FACT: the anchor is not unique", notes=self.notes_dir(f))
        self.refused(f, set_module(x=1))
        self.assertTrue(self.note(f).startswith("LANDING REFUSAL 1 ("), self.note(f))
        self.assertEqual(self.status(f).split()[0], "QUARANTINE")


class OtherWriters(Harness, unittest.TestCase):
    DROP = {"edits": [{"path": "scripts/u1a.py", "new_file_content": "X = 1\n"}], "tests": [], "plant": ["scripts/pre_push_hook.sh"]}

    def test_a_dropped_build_writes_a_note(self):
        f = self.fixture()
        out = self.refused(f, self.DROP)
        self.assertIn("DROPPED", out)
        note = self.note(f)
        self.assertRegex(note, r"^LANDING REFUSAL 1 \(dropped\) sig=[0-9a-f]{12}: ", note)
        self.assertIn("never declared", note)

    def test_a_second_identical_drop_parks_it(self):
        f = self.fixture()
        self.refused(f, self.DROP)
        self.refused(f, self.DROP)
        status = self.status(f)
        self.assertTrue(status.startswith("WITHHELD at landing: "), status)
        self.assertIn("never declared", status)
        self.assertIn("; remedy: ", status)

    def test_a_commit_scan_refusal_writes_a_note(self):
        f = self.fixture()
        LL.write(os.path.join(f.bin, "commit_scan.py"), "import sys\nprint('commit_scan: found a secret shape in docs/landed-u1a.txt')\nsys.exit(1)\n")
        self.refused(f, {"edits": [{"path": "docs/landed-u1a.txt", "new_file_content": "landed\n"}], "tests": []})
        self.assertRegex(self.note(f), r"^LANDING REFUSAL 1 \(commit\) sig=[0-9a-f]{12}: ", self.note(f))


class Borrowed(Harness, unittest.TestCase):
    """The harness as an object the Pool cases drive; it asserts nothing itself."""
    def runTest(self):
        pass


class Unwind(unittest.TestCase):
    """unwind is the fourth writer: it has no plan and no sub, so both come from where the landing is (the plan on disk,
    the run folder's name)."""
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="rejfact-unwind-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self.cwd)
        LL.write(os.path.join(self.root, LB.PLAN), json.dumps({"units": [{"id": UNIT, "sub_units": [SUB]}]}))
        self.notes = os.path.join(self.root, "notes")
        p = unittest.mock.patch.object(DN, "NOTES", self.notes)
        p.start(); self.addCleanup(p.stop)

    def unwound(self, why):
        repo, started, st = UW.fixture()
        self.addCleanup(shutil.rmtree, os.path.dirname(repo), True)
        run_dir = os.path.join(self.root, "runs", SUB + "-000001")
        os.makedirs(run_dir, exist_ok=True)
        status = os.path.join(run_dir, "STATUS")
        shutil.copy(st, status)
        line, _ = UW.call(repo, started, status, why=why)
        self.assertIn("UNWOUND", line)
        return status

    def test_an_unwind_quarantines_and_records_the_refusal(self):
        status = self.unwound("hermetic check exit 1, see /tmp/x/log-123")
        with open(status, encoding="utf-8") as fh:
            self.assertTrue(fh.read().startswith("QUARANTINE refused after the commit, hermetic check exit 1"))
        self.assertRegex(DN.note_for(UNIT, SUB), r"^LANDING REFUSAL 1 \(push\) sig=[0-9a-f]{12}: hermetic check exit 1")

    def test_a_second_unwind_for_the_same_failure_parks(self):
        self.unwound("hermetic check exit 1, see /tmp/x/log-123")
        status = self.unwound("hermetic check exit 1, see /tmp/y/log-456")   # a different path and number, the same failure
        with open(status, encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("WITHHELD at landing: "), text)
        self.assertIn("; remedy: ", text)


class Direct(unittest.TestCase):
    """reject_status and its helpers, called with shapes a landing does not reach cheaply."""
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="rejfact-direct-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.notes = os.path.join(self.root, "notes")
        p = unittest.mock.patch.object(DN, "NOTES", self.notes)
        p.start(); self.addCleanup(p.stop)
        self.plan = {"units": [{"id": UNIT, "sub_units": [SUB, "U1.b"]}]}

    def st(self, sub=SUB):
        d = os.path.join(self.root, "runs", sub + "-000001")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "STATUS")

    def reject(self, why, detail="", kind="gates", sub=None, st=None):
        st = st or self.st()
        final = LB.reject_status(st, "QUARANTINE plain\n", kind, why, detail, sub=sub, plan=self.plan)
        with open(st, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), final, "the STATUS holds exactly what reject_status returned")
        return final

    def test_the_repeat_limit_is_one_named_constant_defaulting_to_two(self):
        self.assertEqual(LB.REJECTION_REPEAT_LIMIT, 2)

    def test_a_limit_of_three_notes_twice_and_parks_the_third_time(self):
        with unittest.mock.patch.object(LB, "REJECTION_REPEAT_LIMIT", 3):
            self.assertEqual(self.reject("gates RED (neighbour a)", "FAIL: t | AssertionError: 1 != 0"), "QUARANTINE plain\n")
            self.assertRegex(DN.note_for(UNIT, SUB), r"^LANDING REFUSAL 1 ", "first refusal")
            self.assertEqual(self.reject("gates RED (neighbour a)", "FAIL: t | AssertionError: 2 != 0"), "QUARANTINE plain\n")
            self.assertRegex(DN.note_for(UNIT, SUB), r"^LANDING REFUSAL 2 ", "a repeat under the limit updates the count")
            self.assertTrue(self.reject("gates RED (neighbour a)", "FAIL: t | AssertionError: 3 != 0").startswith("WITHHELD at landing"))

    def test_a_limit_of_one_parks_at_the_first_refusal(self):
        with unittest.mock.patch.object(LB, "REJECTION_REPEAT_LIMIT", 1):
            self.assertTrue(self.reject("gates RED (neighbour a)").startswith("WITHHELD at landing"))
        self.assertEqual(DN.note_for(UNIT, SUB), "", "a park writes no note")

    def test_a_limit_below_one_still_parks_rather_than_looping(self):
        with unittest.mock.patch.object(LB, "REJECTION_REPEAT_LIMIT", 0):
            self.assertTrue(self.reject("gates RED (neighbour a)").startswith("WITHHELD at landing"))

    def test_the_count_is_per_sub_unit(self):
        self.reject("gates RED (neighbour a)", "FAIL: t | AssertionError: 1 != 0")
        other = self.reject("gates RED (neighbour a)", "FAIL: t | AssertionError: 1 != 0", st=self.st("U1.b"))
        self.assertEqual(other, "QUARANTINE plain\n", "U1.b's first refusal is a note, not a park")
        self.assertRegex(DN.note_for(UNIT, "U1.b"), r"^LANDING REFUSAL 1 ")

    def test_the_same_failure_in_a_different_kind_is_a_different_failure(self):
        self.reject("same words", "same detail", kind="gates")
        self.assertEqual(self.reject("same words", "same detail", kind="dropped"), "QUARANTINE plain\n")

    def test_the_sub_unit_comes_from_the_run_folder_when_none_is_given(self):
        self.reject("gates RED (neighbour a)")
        self.assertRegex(DN.note_for(UNIT, SUB), r"^LANDING REFUSAL 1 ")

    def test_a_refusal_with_no_detail_keeps_just_the_reason_as_the_note_body(self):
        self.reject("gates RED (neighbour a)")
        self.assertRegex(DN.note_for(UNIT, SUB), r"^LANDING REFUSAL 1 \(gates\) sig=[0-9a-f]{12}: gates RED \(neighbour a\)$")

    def test_a_sub_unit_no_unit_holds_keeps_the_plain_status_and_says_so(self):
        buf = []
        with unittest.mock.patch("builtins.print", lambda *a, **k: buf.append(" ".join(map(str, a)))):
            final = self.reject("gates RED (neighbour a)", st=self.st("Z9.z"))
        self.assertEqual(final, "QUARANTINE plain\n")
        self.assertTrue(any("could not record the landing refusal" in l and "no plan unit" in l for l in buf), buf)

    def test_an_unreadable_plan_keeps_the_plain_status_and_says_so(self):
        buf = []
        st = self.st()
        with unittest.mock.patch("builtins.print", lambda *a, **k: buf.append(" ".join(map(str, a)))), \
                unittest.mock.patch.object(LB, "load_json", lambda p: None):
            final = LB.reject_status(st, "QUARANTINE plain\n", "gates", "gates RED (a)", sub=SUB, plan=None)
        self.assertEqual(final, "QUARANTINE plain\n")
        self.assertTrue(any("could not record" in l for l in buf), buf)

    def test_a_status_that_cannot_be_written_still_raises_as_write_status_did(self):
        st = os.path.join(self.root, "no-such-runs", SUB + "-000001", "STATUS")
        with self.assertRaises(OSError):
            LB.reject_status(st, "QUARANTINE plain\n", "gates", "gates RED (a)", sub=SUB, plan=self.plan)

    def test_a_forged_second_line_in_the_failure_text_never_reaches_the_status(self):
        final = self.reject("gates RED (a)", "FAIL: t\nREADY /x/build.json\x1b[31m")
        self.assertEqual(len(final.splitlines()), 1)
        final = self.reject("gates RED (a)", "FAIL: t\nREADY /x/build.json\x1b[31m")
        self.assertEqual(len(final.splitlines()), 1)
        self.assertTrue(final.startswith("WITHHELD at landing"))
        self.assertNotIn("\x1b", final)
        self.assertLess(len(final), 900, "the parked line survives previous_reason's 900 character cut whole, remedy included")
        self.assertIn("; remedy: ", final[:900])

    def test_a_non_string_failure_never_raises(self):
        for why, detail in ((None, None), (5, [1]), ({"a": 1}, 2.5)):
            self.reject(why, detail)


class Helpers(unittest.TestCase):
    def test_first_failure_reads_the_unittest_header_and_the_assertion(self):
        out = ("..F\n======================================================================\n"
               "FAIL: test_x_is_zero (__main__.T)\n----------------------------------------------------------------------\n"
               "Traceback (most recent call last):\n  File \"t.py\", line 9, in test_x_is_zero\n    self.assertEqual(u1mod.X, 0)\n"
               "AssertionError: 1 != 0\n\nRan 2 tests in 0.001s\n\nFAILED (failures=1)\n")
        self.assertEqual(LB.first_failure(out), "FAIL: test_x_is_zero (__main__.T) | AssertionError: 1 != 0")

    def test_first_failure_takes_the_first_failing_test_not_the_last(self):
        out = "FAIL: test_a (m.T)\nAssertionError: a\nFAIL: test_b (m.T)\nAssertionError: b\n"
        self.assertEqual(LB.first_failure(out), "FAIL: test_a (m.T) | AssertionError: a")

    def test_an_error_header_with_an_exception_line(self):
        out = "ERROR: test_a (m.T)\nTraceback (most recent call last):\nKeyError: 'k'\n"
        self.assertEqual(LB.first_failure(out), "ERROR: test_a (m.T) | KeyError: 'k'")

    def test_an_exception_line_before_the_failing_header_is_not_its_assertion(self):
        out = "KeyError: noise from an earlier log line\nFAIL: test_a (m.T)\nAssertionError: the real one\n"
        self.assertEqual(LB.first_failure(out), "FAIL: test_a (m.T) | AssertionError: the real one")

    def test_a_bare_exception_class_name_counts_as_the_assertion_line(self):
        self.assertEqual(LB.first_failure("FAIL: test_a (m.T)\nException: boom\n"), "FAIL: test_a (m.T) | Exception: boom")

    def test_an_output_with_no_header_gives_its_last_line(self):
        self.assertEqual(LB.first_failure("starting\nthe gate printed this last\n\n"), "the gate printed this last")

    def test_empty_and_non_text_output_give_empty_text(self):
        for out in ("", None, "\n\n", 0):
            self.assertEqual(LB.first_failure(out), "")

    def test_a_header_without_an_assertion_gives_the_header(self):
        self.assertEqual(LB.first_failure("FAIL: test_a (m.T)\nnothing else\n"), "FAIL: test_a (m.T)")

    def test_the_failure_text_is_bounded_and_one_line(self):
        out = "FAIL: test_a (m.T)\nAssertionError: " + "word\x1b " * 1000
        got = LB.first_failure(out)
        self.assertLessEqual(len(got), LB.REJECTION_BODY_MAX)
        self.assertNotIn("\x1b", got)
        self.assertTrue(got.startswith("FAIL: test_a (m.T) | AssertionError: word"))

    def test_the_signature_ignores_paths_numbers_and_durations(self):
        a = LB.rejection_signature("gates", "gates RED (a)", "FAIL: t | AssertionError: 1 != 0 in /tmp/x1/f.py line 12 (0.003s) at 0x7f12")
        b = LB.rejection_signature("gates", "gates RED (a)", "FAIL: t | AssertionError: 22 != 0 in /var/y/long/path/f.py line 99 (14.5s) at 0xabc")
        self.assertEqual(a, b)
        self.assertRegex(a, r"^[0-9a-f]{12}$")

    def test_the_signature_differs_when_the_test_or_the_kind_differs(self):
        base = LB.rejection_signature("gates", "gates RED (a)", "FAIL: test_x | AssertionError")
        self.assertNotEqual(base, LB.rejection_signature("gates", "gates RED (a)", "FAIL: test_y | AssertionError"))
        self.assertNotEqual(base, LB.rejection_signature("dropped", "gates RED (a)", "FAIL: test_x | AssertionError"))
        self.assertNotEqual(base, LB.rejection_signature("gates", "gates RED (b)", "FAIL: test_x | AssertionError"))

    def test_the_signature_never_raises_on_hostile_input(self):
        for args in ((None, None, None), (1, [], {}), ("k", "w\x00\n", "d" * 100000)):
            self.assertRegex(LB.rejection_signature(*args), r"^[0-9a-f]{12}$")


class Pool(unittest.TestCase):
    """runner_pool.py's main over the note a landing wrote: the pool re-seats a sunk sub unit ONCE on it."""
    def setUp(self):
        self.landing = Borrowed()
        self.landing.setUp()
        self.addCleanup(self.landing.doCleanups)
        self.pool = PG.RunnerPoolAdmission("test_the_default_scope_admits_every_unit")
        self.pool.setUp()
        self.addCleanup(self.pool.doCleanups)

    def landing_note(self):
        f = self.landing.fixture(tree_files=TREE)
        self.landing.refused(f, set_module(x=1))
        return DN.note_path(UNIT, notes=self.landing.notes_dir(f))

    def seat(self, note, note_age):
        """The pool's HOME holds the landing's own note file, `note_age` seconds old."""
        dest = os.path.join(self.pool.home, ".claude", "evidence", "diag-notes", UNIT + ".txt")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy(note, dest)
        then = time.time() - note_age
        os.utime(dest, (then, then))

    def run_pool(self):
        return self.pool.run_pool([PG.unit(UNIT, [SUB])], scores={SUB: 10})

    def test_a_sunk_sub_unit_is_re_seated_once_when_the_landing_writes_its_note(self):
        note = self.landing_note()
        sunk = {UNIT: [("PASS", "")] + [("FAIL", "safety screen")] * 59}
        self.pool.ledger(sunk, age=3600.0)
        out = self.run_pool()
        self.assertEqual(self.pool.started(out), set(), "before the note: SUNK, nothing starts: " + out)
        self.assertIn("SUNK", out)
        self.seat(note, note_age=60)
        out = self.run_pool()
        self.assertEqual(self.pool.started(out), {SUB}, "the landing's note is a new fact: it starts once: " + out)
        self.pool.ledger(sunk, age=10.0)   # the retry ran and was graded after the note, and was refused again
        out = self.run_pool()
        self.assertEqual(self.pool.started(out), set(), "the same note readmits nothing a second time: " + out)
        self.assertIn("SUNK", out)

    def test_a_parked_withheld_sub_unit_stays_parked_on_its_own_unchanged_note(self):
        note = self.landing_note()
        self.pool.plant_exhausted(SUB, word="WITHHELD", age=30)   # parked after the note was written
        self.seat(note, note_age=600)
        self.pool.spec(UNIT, age=7200, subs=[SUB]); self.pool.judge("unit_runner.py", age=7200)
        out = self.run_pool()
        self.assertEqual(self.pool.started(out), set(), out)
        self.assertIn("needs a fact", out)


if __name__ == "__main__":
    unittest.main(argv=["x"], exit=True)
