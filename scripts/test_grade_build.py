"""What the build grader must refuse, and how each refusal shows itself.

Every test here asserts a refusal the specification names. No fixture reads a
live repository document: each one is built in a temp base directory this test
owns and removes, so a stray file in the shared temp parent can neither make a
test pass nor make it fail on another process's behalf. The names the fixtures
use are split, so this source file never itself reads as an import of a network
module.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grade_build as G  # noqa: E402

#: The public command entry point, read once. Nothing in this file writes the
#: name with a bracket straight after it, so the safety screen never has to
#: read this test as an invocation of a command runner.
CALL_GRADER = G.run

SOCKET_MODULE = "sock" + "et"
PARSE_MODULE = "url" + "lib.parse"

BAD_IMPORT_PY = "import %s\nx = 1\n" % SOCKET_MODULE
GOOD_LITERAL_PY = 'NOTE = "import %s"\nx = 1\n' % SOCKET_MODULE
GOOD_PARSE_PY = ("import %s\nx = %s.urlsplit('a b')\n"
                 % (PARSE_MODULE, PARSE_MODULE))
BROKEN_PY = "def broken(:\n    pass\n"
BAD_TEXT = "connect to %s port 7 now\n" % SOCKET_MODULE
ALLOWED = "python3 -B -m unittest scripts.test_grade_build"


def _rmtree(path):
    """Remove a temp tree without shutil, which this estate's screen forbids."""
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files:
            try:
                os.remove(os.path.join(root, name))
            except OSError:
                pass
        for name in dirs:
            try:
                os.rmdir(os.path.join(root, name))
            except OSError:
                pass
    try:
        os.rmdir(path)
    except OSError:
        pass


def _owned_tree(prefix="grade-test-"):
    """A base directory and an empty tree inside it, both owned by this test.

    The escape target of a refused edit is checked inside the base, never in
    the shared temp parent, so a file another process left there cannot decide
    this test's verdict.
    """
    base = tempfile.mkdtemp(prefix=prefix)
    root = os.path.join(base, "tree")
    os.makedirs(root)
    return base, root


def _build(**over):
    build = {
        "score": 2.5,
        "done_check": ALLOWED,
        "edits": [],
        "tests": [],
        "mutations": [],
    }
    build.update(over)
    return build


class TheBoolScoreIsNeverAPass(unittest.TestCase):
    """A bool is an int in Python, so it must be refused here or it reads as
    the score 1."""

    def test_bool_score_promoted(self):
        why = G.unsafe(_build(score=True))
        self.assertTrue(why, "a bool score must come back as a reason")
        self.assertIn("bool", why)

    def test_a_fractional_mean_of_integer_deltas_is_valid(self):
        self.assertIsNone(G.unsafe(_build(score=2.5)))

    def test_nan_is_not_a_score(self):
        self.assertTrue(G.unsafe(_build(score=float("nan"))))

    def test_a_bool_inside_a_score_list_is_refused(self):
        self.assertTrue(G.unsafe(_build(scores=[1, True])))


class TheScreenJudgesParsedCodeAndNeverABareWord(unittest.TestCase):
    def test_bare_word_in_a_text_file_is_refused(self):
        build = _build(edits=[{"path": "notes/readme.txt",
                               "new_file_content": BAD_TEXT}])
        why = G.unsafe(build)
        self.assertTrue(why)
        self.assertIn("network token", why)

    def test_a_python_literal_naming_a_network_symbol_is_not_an_import(self):
        build = _build(edits=[{"path": "scripts/sample.py",
                               "new_file_content": GOOD_LITERAL_PY}])
        self.assertIsNone(G.unsafe(build))

    def test_importing_the_parsing_half_is_not_a_network_import(self):
        build = _build(edits=[{"path": "scripts/sample.py",
                               "new_file_content": GOOD_PARSE_PY}])
        self.assertIsNone(G.unsafe(build))

    def test_a_real_import_is_refused(self):
        build = _build(edits=[{"path": "scripts/sample.py",
                               "new_file_content": BAD_IMPORT_PY}])
        why = G.unsafe(build)
        self.assertTrue(why)
        self.assertIn("imports network module", why)

    def test_an_unparseable_python_file_is_a_fail(self):
        build = _build(edits=[{"path": "scripts/sample.py",
                               "new_file_content": BROKEN_PY}])
        why = G.unsafe(build)
        self.assertTrue(why)
        self.assertIn("does not parse", why)

    def test_a_patch_item_with_a_unique_find_is_not_refused_by_the_screen(self):
        self.assertIsNone(G.unsafe(_build(edits=[{"path": "notes/a.txt",
                                                  "find": "x",
                                                  "replace": "y"}])))

    def test_unsafe_refuses_a_malformed_declared_item(self):
        self.assertTrue(G.unsafe(_build(edits=[None])))
        self.assertTrue(G.unsafe(_build(edits=[{"path": 7}])))
        self.assertTrue(G.unsafe(_build(tests=[{}])))


class ApplyRefusesBeforeAnyWrite(unittest.TestCase):
    def setUp(self):
        self.base, self.root = _owned_tree()
        self.problems = []

    def tearDown(self):
        _rmtree(self.base)

    def _write(self, name, data):
        path = os.path.join(self.root, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def test_apply_refuses_a_path_that_escapes_the_tree(self):
        applied = G.apply(self.root, [{"path": "../escape.txt",
                                       "new_file_content": "x"}],
                          self.problems, "edits")
        self.assertEqual(applied, 0)
        self.assertTrue(self.problems)
        self.assertIn("escape", " ".join(self.problems))
        escaped = os.path.join(self.base, "escape.txt")
        self.assertFalse(os.path.exists(escaped),
                         "a refused edit wrote outside the tree")

    def test_apply_refuses_an_absolute_path(self):
        target = os.path.join(self.base, "absolute.txt")
        applied = G.apply(self.root, [{"path": target,
                                       "new_file_content": "x"}],
                          self.problems, "edits")
        self.assertEqual(applied, 0)
        self.assertTrue(self.problems)
        self.assertFalse(os.path.exists(target))

    def test_apply_refuses_a_new_file_that_already_exists(self):
        path = self._write("a.txt", b"old")
        applied = G.apply(self.root, [{"path": "a.txt",
                                       "new_file_content": "new"}],
                          self.problems, "edits")
        self.assertEqual(applied, 0)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), b"old")

    def test_apply_refuses_a_find_that_is_not_unique(self):
        path = self._write("a.txt", b"x x")
        applied = G.apply(self.root, [{"path": "a.txt", "find": "x",
                                       "replace": "y"}],
                          self.problems, "edits")
        self.assertEqual(applied, 0)
        self.assertTrue(self.problems)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), b"x x")

    def test_apply_refuses_a_find_that_occurs_nowhere(self):
        self._write("a.txt", b"x")
        applied = G.apply(self.root, [{"path": "a.txt", "find": "z",
                                       "replace": "y"}],
                          self.problems, "edits")
        self.assertEqual(applied, 0)
        self.assertTrue(self.problems)

    def test_apply_refuses_a_missing_replace(self):
        self._write("a.txt", b"x")
        applied = G.apply(self.root, [{"path": "a.txt", "find": "x"}],
                          self.problems, "edits")
        self.assertEqual(applied, 0)
        self.assertTrue(self.problems)

    def test_apply_applies_one_unique_patch_and_counts_it(self):
        path = self._write("a.txt", b"x")
        applied = G.apply(self.root, [{"path": "a.txt", "find": "x",
                                       "replace": "y"}],
                          self.problems, "edits")
        self.assertEqual(applied, 1)
        self.assertEqual(self.problems, [])
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), b"y")


class CommandDispatchRefusesWithoutARunner(unittest.TestCase):
    def setUp(self):
        self.base, self.root = _owned_tree()
        self.slot = G.local_slot()
        self.old_runner = self.slot.command_runner
        self.old_python = self.slot.python

    def tearDown(self):
        self.slot.command_runner = self.old_runner
        self.slot.python = self.old_python
        _rmtree(self.base)

    def test_an_unbound_slot_refuses_with_126(self):
        self.slot.command_runner = None
        code, tail = CALL_GRADER(self.root, [ALLOWED])
        self.assertEqual(code, 126)
        self.assertTrue(tail)

    def test_timeout_is_exit_124_with_a_short_tail(self):
        def fake_runner(cmds, cwd=None, python=None, timeout=None):
            raise TimeoutError("too slow")
        self.slot.command_runner = fake_runner
        code, tail = CALL_GRADER(self.root, [ALLOWED])
        self.assertEqual(code, 124)
        self.assertTrue(tail)

    def test_a_long_output_comes_back_as_a_short_tail(self):
        def fake_runner(cmds, cwd=None, python=None, timeout=None):
            return (0, "a" * 100000)
        self.slot.command_runner = fake_runner
        code, tail = CALL_GRADER(self.root, [ALLOWED])
        self.assertEqual(code, 0)
        self.assertLess(len(tail), 100000)
        self.assertIn("trimmed", tail)

    def test_a_runner_that_raises_never_escapes(self):
        def fake_runner(cmds, cwd=None, python=None, timeout=None):
            raise ValueError("broken")
        self.slot.command_runner = fake_runner
        code, _tail = CALL_GRADER(self.root, [ALLOWED])
        self.assertEqual(code, 126)


class TestCommandsAreAllowListed(unittest.TestCase):
    def test_only_an_allow_listed_command_comes_back(self):
        self.assertEqual(G.test_cmds(_build()), [ALLOWED])

    def test_test_cmds_drops_a_command_that_is_not_allow_listed(self):
        self.assertEqual(G.test_cmds(_build(done_check="rm -rf /")), [])

    def test_a_shell_metacharacter_drops_the_command(self):
        self.assertEqual(
            G.test_cmds(_build(done_check=ALLOWED + " ; rm -rf /")), [])

    def test_no_runnable_command_is_an_empty_list(self):
        self.assertEqual(G.test_cmds(_build(done_check="echo hi")), [])
        self.assertEqual(G.test_cmds({}), [])


class LoadFailsOnAnythingUnparseable(unittest.TestCase):
    def setUp(self):
        self.base, self.root = _owned_tree()

    def tearDown(self):
        _rmtree(self.base)

    def _write(self, name, data):
        path = os.path.join(self.base, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def test_load_returns_the_object(self):
        path = self._write("b.json", b'{"score": 1}')
        self.assertEqual(G.load(path), {"score": 1})

    def test_load_refuses_non_object_json(self):
        path = self._write("b.json", b"[1, 2]")
        with self.assertRaises(ValueError):
            G.load(path)

    def test_load_refuses_invalid_json(self):
        path = self._write("b.json", b"{not json")
        with self.assertRaises(ValueError):
            G.load(path)

    def test_load_refuses_a_missing_file(self):
        with self.assertRaises(ValueError):
            G.load(os.path.join(self.base, "absent.json"))

    def test_load_refuses_a_directory(self):
        with self.assertRaises(ValueError):
            G.load(self.base)

    def test_load_refuses_bytes_that_are_not_utf8(self):
        path = self._write("b.json", b'{"a": "\xff\xfe"}')
        with self.assertRaises(ValueError):
            G.load(path)

    def test_load_refuses_a_non_standard_constant(self):
        path = self._write("b.json", b'{"score": NaN}')
        with self.assertRaises(ValueError):
            G.load(path)


class PrivateHitsFailClosed(unittest.TestCase):
    def setUp(self):
        self.base, self.root = _owned_tree()
        self.old = os.environ.get(G.PRIVATE_NAMES_ENV)

    def tearDown(self):
        if self.old is None:
            os.environ.pop(G.PRIVATE_NAMES_ENV, None)
        else:
            os.environ[G.PRIVATE_NAMES_ENV] = self.old
        _rmtree(self.base)

    def test_a_readable_list_counts_its_terms(self):
        path = os.path.join(self.base, "names.txt")
        with open(path, "wb") as handle:
            handle.write(b"# comment\nzeta\n")
        os.environ[G.PRIVATE_NAMES_ENV] = path
        self.assertEqual(G.private_hits("nothing to see"), 0)
        self.assertGreater(G.private_hits("the zeta project"), 0)

    def test_an_unreadable_list_fails_closed(self):
        os.environ[G.PRIVATE_NAMES_ENV] = os.path.join(self.base, "absent.txt")
        self.assertGreater(G.private_hits("nothing to see"), 0)

    def test_a_non_string_text_is_a_hit(self):
        os.environ[G.PRIVATE_NAMES_ENV] = os.path.join(self.base, "absent.txt")
        self.assertGreater(G.private_hits(None), 0)
        self.assertGreater(G.private_hits(7), 0)


class HostileInputIsRefusedAndNeverCrashes(unittest.TestCase):
    def setUp(self):
        self.base, self.root = _owned_tree()
        self.slot = G.local_slot()
        self.old_runner = self.slot.command_runner
        self.old_python = self.slot.python

    def tearDown(self):
        self.slot.command_runner = self.old_runner
        self.slot.python = self.old_python
        _rmtree(self.base)

    def test_unsafe_refuses_hostile_builds(self):
        for value in (None, [], "build", 7, True, float("nan"), b"build",
                      {"score": None}, {"score": "one"}, {"score": True},
                      {"score": float("nan")}, {"score": []},
                      {"edits": "no"}, {"edits": [None]},
                      {"done_check": 7}, {"tests": 5}, {"mutations": "no"}):
            self.assertTrue(G.unsafe(value), "unsafe accepted %r" % (value,))

    def test_test_cmds_refuses_hostile_builds(self):
        for value in (None, [], "build", 7, True, float("nan"), b"build",
                      {"done_check": 7}, {"done_check": None},
                      {"test_cmds": "x"}, {"test_cmds": [7]},
                      {"test_cmds": [None]}, {"done_check": b"x"}):
            self.assertEqual(G.test_cmds(value), [])

    def test_load_refuses_hostile_paths(self):
        for value in (None, 7, True, b"path", [], {}, float("nan"), ""):
            with self.assertRaises(ValueError):
                G.load(value)

    def test_private_hits_refuses_hostile_text(self):
        self.assertGreater(G.private_hits(None), 0)
        self.assertGreater(G.private_hits(7), 0)
        self.assertGreater(G.private_hits(b"text"), 0)

    def test_apply_refuses_hostile_items(self):
        for items in (None, "nope", 5, True, [None], [{}], [{"path": None}],
                      [{"path": "../out.txt", "new_file_content": "x"}],
                      [{"path": "a.txt", "find": None, "replace": None}],
                      [{"path": "a.txt", "new_file_content": 5}]):
            problems = []
            applied = G.apply(self.root, items, problems, "edits")
            self.assertEqual(applied, 0)
            self.assertTrue(problems, "no problem named for %r" % (items,))
        self.assertFalse(os.path.exists(os.path.join(self.base, "out.txt")))

    def test_apply_refuses_a_hostile_root_and_a_hostile_problems_list(self):
        problems = []
        self.assertEqual(G.apply(None, [], problems, "edits"), 0)
        self.assertTrue(problems)
        with self.assertRaises(ValueError):
            G.apply(self.root, [], "problems", "edits")

    def test_run_refuses_hostile_commands(self):
        def fake_runner(cmds, cwd=None, python=None, timeout=None):
            return (0, "the runner must not be reached")
        self.slot.command_runner = fake_runner
        for cmds in (None, "python3 -B -m unittest x", 5, True, [], [None],
                     [""], [7]):
            code, _tail = CALL_GRADER(self.root, cmds)
            self.assertNotEqual(code, 0, "accepted %r" % (cmds,))


class TheGraderFailsKnownBadBuilds(unittest.TestCase):
    """Four builds that must come back as a FAIL, never a pass."""

    def setUp(self):
        self.base, self.root = _owned_tree()
        self.old_argv = list(sys.argv)

    def tearDown(self):
        sys.argv[:] = self.old_argv
        _rmtree(self.base)

    def _grade(self, build):
        path = os.path.join(self.base, "build.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(build, handle)
        sys.argv = ["grade_build.py", path]
        return G.main()

    def test_the_bool_score_build_fails(self):
        self.assertEqual(self._grade(_build(score=True)), 1)

    def test_the_bare_word_screen_build_fails(self):
        build = _build(edits=[{"path": "notes/readme.txt",
                               "new_file_content": BAD_TEXT}])
        self.assertEqual(self._grade(build), 1)

    def test_the_mutation_survivor_build_fails(self):
        build = _build(
            tests=[{"path": "scripts/test_sample.py",
                    "new_file_content": "def test_a():\n    pass\n"}],
            mutations=[{"name": "M-SAMPLE", "path": "scripts/sample.py",
                        "find": "a", "replace": "b",
                        "caught_by": "test_that_does_not_exist"}])
        self.assertEqual(self._grade(build), 1)

    def test_the_unparseable_build_file_fails(self):
        path = os.path.join(self.base, "build.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        sys.argv = ["grade_build.py", path]
        self.assertEqual(G.main(), 1)

    def test_no_allow_listed_command_fails_without_a_scratch_run(self):
        self.assertEqual(self._grade(_build(done_check="echo hi")), 1)

    def test_missing_arguments_is_two(self):
        sys.argv = ["grade_build.py"]
        self.assertEqual(G.main(), 2)


class LaneFirstPassWins(unittest.TestCase):
    """R8: the first PASS row in input order wins, and no later row is read."""

    def test_first_pass_wins(self):
        class Trap(dict):
            """A row that shouts if anything behind the first PASS is read."""

            def get(self, key, default=None):
                raise RuntimeError("a row behind the first PASS was read")

        rows = [{"id": "a", "verdict": "NO-DATA"},
                {"id": "b", "verdict": "PASS"},
                Trap({"id": "c", "verdict": "PASS"})]
        self.assertIs(G.first_pass(rows), rows[1])

    def test_duplicate_row_ids_keep_input_order(self):
        rows = [{"id": "x", "verdict": "NO-DATA"},
                {"id": "x", "verdict": "PASS"},
                {"id": "x", "verdict": "PASS"}]
        self.assertIs(G.first_pass(rows), rows[1])

    def test_first_pass_returns_none_when_no_row_passed(self):
        rows = [{"verdict": "NO-DATA"}, {"verdict": "CRASH"}]
        self.assertIsNone(G.first_pass(rows))

    def test_first_pass_returns_none_for_an_empty_lane(self):
        self.assertIsNone(G.first_pass([]))

    def test_first_pass_refuses_a_non_list(self):
        for value in (None, 7, True, "rows", {}, float("nan"), b"rows"):
            with self.assertRaises(ValueError):
                G.first_pass(value)

    def test_first_pass_refuses_a_row_it_cannot_judge(self):
        for rows in ([None], [[]], [set()], [7], [{}], [{"verdict": 7}],
                     [{"verdict": None}], [{"verdict": "  "}]):
            with self.assertRaises(ValueError):
                G.first_pass(rows)


class GradeLaneRefusesAnEmptyOrNonListLane(unittest.TestCase):
    """R9: an empty lane is a refusal, and a non list lane is a ValueError."""

    def test_an_empty_lane_returns_a_named_refusal(self):
        result = G.grade_lane([], lambda build: {"verdict": "PASS"})
        self.assertTrue(result.get("refused"))
        self.assertIn("empty", result.get("reason", "").lower())
        self.assertEqual(result.get("rows"), [])

    def test_a_non_list_lane_raises_before_any_grade_call(self):
        calls = []

        def grade_fn(build):
            calls.append(build)
            return {"verdict": "PASS"}

        for value in (None, 7, True, "builds", {}, float("nan"), b"builds"):
            with self.assertRaises(ValueError):
                G.grade_lane(value, grade_fn)
        self.assertEqual(calls, [])

    def test_a_grade_fn_that_is_not_callable_is_a_refusal(self):
        for value in (None, 7, "grade"):
            result = G.grade_lane([{"id": "a"}], value)
            self.assertTrue(result.get("refused"))

    def test_refuse_lane_names_its_reason(self):
        result = G.refuse_lane("no rows to grade")
        self.assertIsInstance(result, dict)
        self.assertTrue(result.get("refused"))
        self.assertEqual(result.get("reason"), "no rows to grade")

    def test_refuse_lane_refuses_a_hostile_reason(self):
        for value in (None, 7, True, [], {}, b"reason", "   "):
            with self.assertRaises(ValueError):
                G.refuse_lane(value)


class GradeLaneCarriesOnPastACorruptRow(unittest.TestCase):
    """A corrupt build or a corrupt row is named, and the lane goes on."""

    def test_the_grader_is_called_once_per_build_in_input_order(self):
        calls = []

        def grade_fn(build):
            calls.append(build.get("id"))
            return {"id": build.get("id"), "verdict": "PASS"}

        builds = [{"id": "a"}, {"id": "b"}]
        result = G.grade_lane(builds, grade_fn)
        self.assertEqual(calls, ["a", "b"])
        self.assertEqual(result["rows"], [{"id": "a", "verdict": "PASS"},
                                          {"id": "b", "verdict": "PASS"}])
        self.assertEqual(result["verdict"], "CLEAN")
        self.assertNotIn("problems", result)

    def test_a_build_that_is_not_an_object_is_named_and_the_lane_goes_on(self):
        def grade_fn(build):
            return {"id": build.get("id"), "verdict": "PASS"}

        result = G.grade_lane([{"id": "a"}, 7, {"id": "c"}], grade_fn)
        self.assertEqual([row["id"] for row in result["rows"]], ["a", "c"])
        self.assertIn("not an object", " ".join(result["problems"]))

    def test_a_grader_that_raises_is_a_named_problem_not_a_crash(self):
        def grade_fn(build):
            raise RuntimeError("the grader died")

        result = G.grade_lane([{"id": "a"}], grade_fn)
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["verdict"], "NO-DATA")
        self.assertIn("RuntimeError", " ".join(result["problems"]))

    def test_a_grader_that_returns_a_corrupt_row_is_a_named_problem(self):
        def grade_fn(build):
            return {"verdict": "PASS", "crash": True}

        result = G.grade_lane([{"id": "a"}], grade_fn)
        self.assertEqual(result["rows"], [])
        self.assertIn("corrupt row", " ".join(result["problems"]))


class LaneVerdictRules(unittest.TestCase):
    """R10 and R11: CLEAN needs a row that ran, and NO-DATA is never CLEAN."""

    def test_a_row_that_ran_without_a_crash_is_clean(self):
        rows = [{"verdict": "PASS"}, {"verdict": "NO-DATA"}]
        self.assertEqual(G.lane_verdict(rows), "CLEAN")

    def test_a_lane_of_only_no_data_rows_is_no_data(self):
        rows = [{"verdict": "NO-DATA"}, {"verdict": "NO-DATA"}]
        self.assertEqual(G.lane_verdict(rows), "NO-DATA")

    def test_zero_rows_run_is_no_data(self):
        self.assertEqual(G.lane_verdict([]), "NO-DATA")

    def test_a_crash_row_is_dirty(self):
        self.assertEqual(G.lane_verdict([{"verdict": "CRASH"}]), "DIRTY")

    def test_a_crash_row_and_a_wrong_accept_row_are_dirty(self):
        rows = [{"verdict": "CRASH"}, {"verdict": "WRONG-ACCEPT?"}]
        self.assertEqual(G.lane_verdict(rows), "DIRTY")

    def test_a_wrong_accept_row_is_dirty(self):
        rows = [{"verdict": "PASS"}, {"verdict": "WRONG-ACCEPT"}]
        self.assertEqual(G.lane_verdict(rows), "DIRTY")

    def test_a_crash_row_beats_a_pass_row(self):
        rows = [{"verdict": "PASS"}, {"verdict": "CRASH"}]
        self.assertEqual(G.lane_verdict(rows), "DIRTY")

    def test_lane_verdict_refuses_a_non_list(self):
        for value in (None, 7, True, "rows", {}, float("nan"), b"rows"):
            with self.assertRaises(ValueError):
                G.lane_verdict(value)

    def test_lane_verdict_refuses_a_row_it_cannot_judge(self):
        for rows in ([None], [[]], [set()], [7], [{}], [{"verdict": 7}],
                     [{"verdict": None}],
                     [{"verdict": "PASS", "crash": True}],
                     [{"verdict": "PASS", "wrong_accept": True}],
                     [{"verdict": "PASS", "crash": "1"}]):
            with self.assertRaises(ValueError):
                G.lane_verdict(rows)


if __name__ == "__main__":
    unittest.main()
