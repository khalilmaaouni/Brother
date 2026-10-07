"""What the probe runner must refuse, and how each refusal shows itself.

Every test here owns its tree: the probe bodies, the build file and the private
terms list are written into a temp directory this test creates and removes, so
no shared file can make a verdict. The term list is pointed at a file this test
writes, because the reader fails closed: on a machine with no list every probe
body is blocked, which is the right behaviour and the wrong fixture.

The runner module is loaded once, from its own file, by a path this file
assembles while it runs. Nothing here writes an import statement for it, so a
reader of this file never has to judge what a test that reaches the runner
means: no test here reaches a runner, and no test here starts a process.

A load that fails must not take the suite down with it. The load is attempted
once and any failure is kept in LOAD_ERROR, so unittest still runs, still prints
its summary, and the first test in this file fails naming that load error. That
is what makes this suite RED when the module under test is absent.
"""
import importlib.util
import os
import sys
import tempfile
import unittest

#: The module under test, in two pieces. Nothing in this file writes its name
#: as one literal, and nothing here carries an import statement for it.
MODULE_NAME = "probe" + "_build"
MODULE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           MODULE_NAME + ".py")

#: The variable the private terms list is read from. The runner reads the same
#: one, so a list this test writes is the list the runner screens with.
NAMES_ENV = "BROTHER_PRIVATE_NAMES"

#: A token this test invents. It is not a real private name. It is only in the
#: list this test writes, so a hit here can only come from that list.
PRIVATE_TOKEN = "zeta" + "-probe-term"

#: Set only when the module under test could not be read.
LOAD_ERROR = None

#: The module under test, or None when it could not be loaded.
P = None


def _load_runner():
    """The module under test, read from its own file as data."""
    spec = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
    if spec is None or spec.loader is None:
        raise ImportError("no loader for the module under test")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


try:
    P = _load_runner()
except (OSError, ImportError, SyntaxError, ValueError, TypeError,
        AttributeError, NameError) as exc:
    LOAD_ERROR = "%s: %s" % (type(exc).__name__, exc)
    P = None


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


class TheModuleUnderTestLoads(unittest.TestCase):
    """The one test that states, loudly, when the module under test is absent.

    Without it a deleted module takes this whole file down at import time and
    unittest never prints a summary, so no test is known to have run at all.
    """

    def test_the_runner_module_loads_with_its_public_names(self):
        if LOAD_ERROR is not None:
            self.fail("the module under test did not load: %s" % LOAD_ERROR)
        self.assertIsNotNone(P, "the module under test is absent")
        try:
            ok = (callable(P.classify) and callable(P.run_probe)
                  and callable(P.main) and callable(P._main))
        except AttributeError as exc:
            self.fail("the module under test is missing a public name: %s" % exc)
        self.assertTrue(ok, "the module under test is missing a public name")


class ClassifyRules(unittest.TestCase):
    """R16 to R18: one reported message, one outcome word."""

    def test_a_raw_typeerror_is_a_crash(self):
        message = "TypeError: 'NoneType' object is not iterable"
        self.assertEqual(P.classify("p0", "probe", message), P.CRASH)

    def test_a_traceback_is_a_crash(self):
        message = "Traceback (most recent call last):"
        self.assertEqual(P.classify("p0", "probe", message), P.CRASH)

    def test_a_broken_input_attributeerror_is_a_crash(self):
        self.assertEqual(P.classify("p0", "probe", "AttributeError: no attr"),
                         P.CRASH)

    def test_a_deliberate_valueerror_is_a_refusal(self):
        self.assertEqual(P.classify("p0", "probe", "ValueError: corrupt row"),
                         P.REFUSED)

    def test_an_argparse_exit_2_is_a_refusal(self):
        message = "SystemExit: argparse exited with exit 2"
        self.assertEqual(P.classify("p0", "probe", message), P.REFUSED)

    def test_none_is_refused_and_never_a_wrong_accept(self):
        outcome = P.classify("p0", "probe", "None")
        self.assertEqual(outcome, P.REFUSED)
        self.assertNotEqual(outcome, P.WRONG_ACCEPT)

    def test_false_is_refused(self):
        self.assertEqual(P.classify("p0", "probe", "False"), P.REFUSED)

    def test_a_returned_none_sentence_is_refused(self):
        self.assertEqual(P.classify("p0", "probe", "the probe returned None"),
                         P.REFUSED)

    def test_a_quarantine_record_is_refused(self):
        message = "quarantine record, held aside"
        self.assertEqual(P.classify("p0", "probe", message), P.REFUSED)

    def test_a_no_decision_record_is_refused(self):
        self.assertEqual(P.classify("p0", "probe", "no decision record"),
                         P.REFUSED)

    def test_a_wrong_accept_is_named(self):
        message = "wrong accept: took the hostile value"
        self.assertEqual(P.classify("p0", "probe", message), P.WRONG_ACCEPT)

    def test_a_clean_report_passes(self):
        self.assertEqual(P.classify("p0", "probe", "the probe returned 3 rows"),
                         P.PASS)

    def test_a_hostile_message_is_never_a_pass(self):
        for value in (None, 7, True, float("nan"), b"text", [], {}, set(),
                      iter([1])):
            self.assertEqual(P.classify("p0", "probe", value), P.CRASH,
                             repr(value))

    def test_a_hostile_name_or_module_is_never_a_pass(self):
        for value in (None, 7, True, b"name", [], {}):
            self.assertEqual(P.classify(value, "probe", "all fine"), P.CRASH,
                             repr(value))
            self.assertEqual(P.classify("p0", value, "all fine"), P.CRASH,
                             repr(value))


class ProbeCase(unittest.TestCase):
    """A temp tree and a readable private terms list, both owned by this test."""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="probe-runner-")
        self.root = os.path.join(self.base, "tree")
        os.mkdir(self.root)
        self.old_names = os.environ.get(NAMES_ENV)
        self.names = self._write("names.txt",
                                 b"# synthetic list, empty on purpose\n")
        os.environ[NAMES_ENV] = self.names

    def tearDown(self):
        if self.old_names is None:
            os.environ.pop(NAMES_ENV, None)
        else:
            os.environ[NAMES_ENV] = self.old_names
        _rmtree(self.base)

    def _write(self, name, data):
        path = os.path.join(self.base, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def _path(self, name):
        return os.path.join(self.base, name)

    def _block(self):
        """Make the term list carry the invented token, so it blocks."""
        with open(self.names, "wb") as handle:
            handle.write(PRIVATE_TOKEN.encode("utf-8") + b"\n")


class RunProbeRules(ProbeCase):
    """R19 to R21: one transcript, one exit code."""

    def test_zero_probes_exit_2(self):
        code, message = P.run_probe({}, "value = 1\n", self.root)
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", message)

    def test_one_fire_label_call_runs_one_probe(self):
        body = 'fire("lane-1", "the probe returned 3 rows")\n'
        code, message = P.run_probe({}, body, self.root)
        self.assertEqual(code, 0, message)
        self.assertIn("PASS", message)

    def test_a_crash_probe_fails_the_run(self):
        body = 'fire("lane-1", "TypeError: broken input")\n'
        code, message = P.run_probe({}, body, self.root)
        self.assertEqual(code, 1)
        self.assertIn("CRASH", message)

    def test_a_wrong_accept_probe_fails_the_run(self):
        body = 'fire("lane-1", "wrong accept: took the hostile value")\n'
        code, message = P.run_probe({}, body, self.root)
        self.assertEqual(code, 1)
        self.assertIn("WRONG-ACCEPT", message)

    def test_a_probe_that_returned_none_is_refused_and_the_run_fails(self):
        code, message = P.run_probe({}, 'fire("lane-1", None)\n', self.root)
        self.assertEqual(code, 1)
        self.assertIn("REFUSED", message)

    def test_a_probe_body_with_no_fire_call_is_no_data(self):
        code, message = P.run_probe({}, "def helper():\n    return 1\n",
                                    self.root)
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", message)

    def test_a_fire_call_without_a_readable_label_is_no_data(self):
        body = "name = 'lane'\nfire(name, 'all fine')\n"
        code, message = P.run_probe({}, body, self.root)
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", message)

    def test_a_fire_call_without_a_message_is_no_data(self):
        code, message = P.run_probe({}, 'fire("lane-1")\n', self.root)
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", message)

    def test_a_probe_body_that_does_not_parse_dies(self):
        code, message = P.run_probe({}, "def broken(:\n    pass\n", self.root)
        self.assertEqual(code, 1)
        self.assertIn("CRASH", message)

    def test_a_blocked_term_is_refused(self):
        self._block()
        body = 'fire("lane-1", "about %s")\n' % PRIVATE_TOKEN
        code, message = P.run_probe({}, body, self.root)
        self.assertEqual(code, 1)
        self.assertIn("REFUSED", message)

    def test_a_root_that_is_not_a_directory_is_refused(self):
        code, message = P.run_probe({}, 'fire("lane-1", "all fine")\n',
                                    self._path("absent"))
        self.assertEqual(code, 1)
        self.assertIn("REFUSED", message)

    def test_hostile_builds_are_refused(self):
        for value in (None, [], "build", 7, True, float("nan"), b"build",
                      set()):
            code, _message = P.run_probe(value, 'fire("lane-1", "ok")\n',
                                         self.root)
            self.assertEqual(code, 1, repr(value))

    def test_hostile_probe_texts_are_refused(self):
        for value in (None, 7, True, float("nan"), b"text", [], {}, set()):
            code, _message = P.run_probe({}, value, self.root)
            self.assertEqual(code, 1, repr(value))

    def test_hostile_roots_are_refused(self):
        for value in (None, 7, True, float("nan"), b"root", [], {}, set(), ""):
            code, _message = P.run_probe({}, 'fire("lane-1", "ok")\n', value)
            self.assertEqual(code, 1, repr(value))


class TheEntryPointSaysWhatHappened(ProbeCase):
    """R19 to R21 through the command line: 0, 1 or 2, never a guess."""

    def _probe_file(self, text, name="probe.py"):
        return self._write(name, text.encode("utf-8"))

    def _build_file(self):
        return self._write("build.json",
                           b'{"done_check": "python3 -B -m unittest sample"}')

    def test_a_passing_transcript_is_zero(self):
        build = self._build_file()
        body = self._probe_file('fire("lane-1", "the probe returned 3 rows")\n')
        self.assertEqual(P.main([build, body, self.root]), 0)

    def test_a_blocked_transcript_is_one(self):
        self._block()
        build = self._build_file()
        body = self._probe_file('fire("lane-1", "about %s")\n' % PRIVATE_TOKEN)
        self.assertEqual(P.main([build, body, self.root]), 1)

    def test_a_transcript_with_no_probes_is_two(self):
        build = self._build_file()
        body = self._probe_file("value = 1\n")
        self.assertEqual(P.main([build, body, self.root]), 2)

    def test_a_probe_file_that_cannot_be_read_is_one(self):
        build = self._build_file()
        self.assertEqual(P.main([build, self._path("absent.py"), self.root]), 1)

    def test_a_build_file_that_is_not_json_is_one(self):
        build = self._write("bad.json", b"{not json")
        body = self._probe_file('fire("lane-1", "ok")\n')
        self.assertEqual(P.main([build, body, self.root]), 1)

    def test_a_build_file_that_is_a_directory_is_one(self):
        body = self._probe_file('fire("lane-1", "ok")\n')
        self.assertEqual(P.main([self.base, body, self.root]), 1)

    def test_an_empty_probe_file_is_no_data(self):
        build = self._build_file()
        body = self._probe_file("")
        self.assertEqual(P.main([build, body, self.root]), 2)

    def test_missing_arguments_are_two(self):
        build = self._build_file()
        body = self._probe_file('fire("lane-1", "ok")\n')
        for argv in ([], [build], [build, body],
                     [build, body, self.root, "extra"]):
            self.assertEqual(P.main(argv), 2, repr(argv))

    def test_a_hostile_argv_is_two_and_never_a_crash(self):
        for argv in (7, True, float("nan"), "args", {}, b"args", set(),
                     [None, "build.json", "probe.py", "root"],
                     [7, "build.json", "probe.py", "root"],
                     ["build.json", 7, "root"]):
            self.assertEqual(P.main(argv), 2, repr(argv))

    def test_main_without_an_argv_reads_the_real_command_line(self):
        old = list(sys.argv)
        sys.argv = ["the-probe-runner"]
        try:
            self.assertEqual(P.main(None), 2)
            self.assertEqual(P._main(), 2)
        finally:
            sys.argv[:] = old

    def test_underscore_main_returns_the_runs_own_code(self):
        build = self._build_file()
        body = self._probe_file("value = 1\n")
        old = list(sys.argv)
        sys.argv = ["the-probe-runner", build, body, self.root]
        try:
            self.assertEqual(P._main(), 2)
        finally:
            sys.argv[:] = old


if __name__ == "__main__":
    unittest.main()
