"""F2b (the fixed-folder half): repair_wave.py, commit_scan.py,
land_batch.py and model_call.py each resolved a sibling tool from a
hardcoded ~/.claude/bin rather than their own install location, so a
copy running anywhere else (a repository checkout, a future second
install, the public export tree which ships scripts/ but not
plugin/) reached for the wrong sibling copy, or nothing at all if
~/.claude/bin did not exist there. Every fix resolves from the
running file's own directory instead:
os.path.dirname(os.path.abspath(__file__)).

NOTE on why this is not proven by relocating the file and running
--selftest: for a plain `python3 <path>/tool.py` invocation, Python
already puts the script's OWN directory on sys.path[0], so a sibling
module sitting right next to it imports successfully whether or not
BIN itself is hardcoded or self resolving; measured directly while
building this test, a relocated repair_wave.py with its siblings
copied alongside it ran clean even before this fix, purely from
Python's own default, telling nothing about BIN. What is NOT
protected by that default is BIN's other use, os.path.join(BIN, "..."),
fed straight into subprocess.run() for judge_calibrate.py,
grade_lanes_par.sh and probe_wave.py (repair_wave.py), or
land_apply.py and commit_scan.py (land_batch.py), or or_ask.py
(model_call.py's BRIDGE): a real, unconditional dependency on BIN's
own value with no fallback. So the direct, honest test is of BIN's
OWN defining expression: the real source line is read from each file
and executed in isolation, with __file__ bound to an arbitrary
relocated path, proving the constant no longer depends on a
hardcoded string; every later os.path.join(BIN, ...) then inherits
that correctness structurally.
"""
import os
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def _the_one_line(path, wanted, what):
    """The ONE source line of `path` that `wanted` accepts: a statement
    is found by what it is, never by where it sits. No such line, or
    more than one, is an AssertionError naming the count, so a
    statement that is missing or doubled turns its test RED. Every
    finder below (and the sibling sweep, which imports them) routes
    through here."""
    with open(path, encoding="utf-8") as fh:
        hits = [line for line in fh if wanted(line)]
    if len(hits) != 1:
        raise AssertionError("%s: %d matching line(s) in %s, exactly one is owed" % (what, len(hits), path))
    return hits[0]


def _defining_line(path, varname):
    """The exact source line in `path` that starts an assignment to
    `varname`. Reads the REAL file so a test proves what its own code
    does, never a hand-written stand-in for it."""
    return _the_one_line(path, lambda line: line.lstrip().startswith(varname + " ="), "an assignment to %r" % varname)


def _line_containing(path, needle):
    """The exact source line in `path` containing `needle`, for a
    plain statement rather than an assignment (commit_scan.py's
    sys.path.insert call names no variable of its own)."""
    return _the_one_line(path, lambda line: needle in line, "a line containing %r" % needle)


class _FakeSysModule:
    """A stand-in for the `sys` name inside an exec()'d snippet, so a
    `sys.path.insert(...)` statement can be observed without touching
    this test process's own real sys.path."""

    def __init__(self):
        self.path = []


class TestBinAndBridgeResolveToTheFilesOwnDirectory(unittest.TestCase):
    def test_repair_wave_bin_resolves_to_its_own_directory(self):
        line = _defining_line(os.path.join(HERE, "repair_wave.py"), "BIN")
        fake_path = "/somewhere/else/entirely/repair_wave.py"
        fake_sys = _FakeSysModule()
        ns = {"__file__": fake_path, "os": os, "sys": fake_sys}
        exec(compile(line, "<repair_wave.py:BIN>", "exec"), ns)
        self.assertEqual(ns["BIN"], os.path.dirname(fake_path))
        self.assertNotIn("claude/bin", ns["BIN"])
        self.assertIn(os.path.dirname(fake_path), fake_sys.path)

    def test_commit_scan_sys_path_insert_resolves_to_its_own_directory(self):
        line = _line_containing(os.path.join(HERE, "commit_scan.py"), "sys.path.insert(0,")
        fake_path = "/somewhere/else/entirely/commit_scan.py"
        fake_sys = _FakeSysModule()
        ns = {"__file__": fake_path, "os": os, "sys": fake_sys}
        exec(compile(line, "<commit_scan.py:sys.path.insert>", "exec"), ns)
        self.assertEqual(fake_sys.path, [os.path.dirname(fake_path)])

    def test_land_batch_bin_resolves_to_its_own_directory(self):
        line = _defining_line(os.path.join(HERE, "land_batch.py"), "BIN")
        fake_path = "/somewhere/else/entirely/land_batch.py"
        ns = {"__file__": fake_path, "os": os}
        exec(compile(line, "<land_batch.py:BIN>", "exec"), ns)
        self.assertEqual(ns["BIN"], os.path.dirname(fake_path))
        self.assertNotIn("claude/bin", ns["BIN"])

    def test_model_call_bridge_resolves_to_its_own_directory(self):
        line = _defining_line(os.path.join(HERE, "model_call.py"), "BRIDGE")
        fake_path = "/somewhere/else/entirely/model_call.py"
        ns = {"__file__": fake_path, "os": os}
        exec(compile(line, "<model_call.py:BRIDGE>", "exec"), ns)
        self.assertEqual(ns["BRIDGE"], os.path.join(os.path.dirname(fake_path), "or_ask.py"))


def _owned_line_holds(case, where, line, file_relative=True):
    """The property both sweeps protect, on ONE owned source line: no
    hardcoded bin path, and the file relative resolution itself
    (file_relative=False only for a site that resolves through another
    variable of its own, abc/model_conformance.py's AB)."""
    case.assertNotIn("claude/bin", line, msg="%s still hardcodes claude/bin: %r" % (where, line))
    if file_relative:
        case.assertIn("dirname(os.path.abspath(__file__))", line,
                      msg="%s does not resolve from its own file location: %r" % (where, line))


class TestNoHardcodedClaudeBinLeftAtTheOwnedLines(unittest.TestCase):
    """The four sites this task owns (repair_wave.py, commit_scan.py,
    land_batch.py, model_call.py), each found by WHAT THE STATEMENT IS,
    never by a line number. A sibling sweep of every OTHER ~/.claude/bin
    reference in scripts/loop was run by hand and is reported in the
    task's own summary, not encoded here: most are deliberate
    subprocess calls to the deployed pipeline (loop_until.sh and
    friends always run from ~/.claude/bin in production) or docstring
    mentions, outside this task's exclusive ownership.

    A LINE NUMBER IS NOT AN IDENTITY (measured 2026-10-06). These were
    four pins in one loop: repair_wave.py:17, commit_scan.py:31,
    land_batch.py:10, model_call.py:41. The merge 7e9065c3f of
    2026-10-03 moved land_batch.py's BIN below line 10, the pin read
    docstring text, and this file was red on the release line from
    then on while all four statements were correct. The loop stopped
    at its first failure, so when the merge 52a711d5e of 2026-10-04
    moved model_call.py's BRIDGE off line 41 nothing said so. Hence
    one case per statement: one stale or broken site never hides the
    next."""

    def test_repair_wave_bin_line_is_file_relative(self):
        _owned_line_holds(self, "repair_wave.py BIN", _defining_line(os.path.join(HERE, "repair_wave.py"), "BIN"))

    def test_commit_scan_path_insert_line_is_file_relative(self):
        _owned_line_holds(self, "commit_scan.py sys.path.insert",
                          _line_containing(os.path.join(HERE, "commit_scan.py"), "sys.path.insert(0,"))

    def test_land_batch_bin_line_is_file_relative(self):
        _owned_line_holds(self, "land_batch.py BIN", _defining_line(os.path.join(HERE, "land_batch.py"), "BIN"))

    def test_model_call_bridge_line_is_file_relative(self):
        _owned_line_holds(self, "model_call.py BRIDGE", _defining_line(os.path.join(HERE, "model_call.py"), "BRIDGE"))


class TestTheOwnedLineProperty(unittest.TestCase):
    """_owned_line_holds itself, one guard per case: each refused line
    below is refused by exactly ONE of its two assertions, so deleting
    either assertion turns a case here red."""

    GOOD = "BIN = os.path.dirname(os.path.abspath(__file__))\n"

    def test_a_file_relative_line_holds(self):
        _owned_line_holds(self, "fixture", self.GOOD)

    def test_a_hardcoded_bin_path_is_refused_even_beside_the_file_relative_form(self):
        with self.assertRaises(AssertionError):
            _owned_line_holds(self, "fixture", self.GOOD.rstrip() + '; OLD = os.path.expanduser("~/.claude/bin")\n')

    def test_a_line_that_resolves_from_anywhere_else_is_refused(self):
        with self.assertRaises(AssertionError):
            _owned_line_holds(self, "fixture", 'BIN = "/opt/tools"\n')

    def test_a_site_resolving_through_its_own_variable_holds_without_the_file_relative_form(self):
        _owned_line_holds(self, "fixture", "sys.path.insert(0, os.path.dirname(AB))\n", file_relative=False)

    def test_a_site_resolving_through_its_own_variable_still_refuses_a_hardcoded_bin_path(self):
        with self.assertRaises(AssertionError):
            _owned_line_holds(self, "fixture", 'sys.path.insert(0, os.path.expanduser("~/.claude/bin"))\n',
                              file_relative=False)


class TestAnOwnedStatementIsFoundByWhatItIs(unittest.TestCase):
    """The finder itself, on a scratch file, one condition per case. A
    statement that cannot be found, or is found twice, is a RED: never
    a skip, never a guess at which line was meant."""

    STATEMENT = "BIN = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, BIN)\n"

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="bin-resolution-")
        self.addCleanup(shutil.rmtree, self.d, True)

    def scratch(self, text):
        path = os.path.join(self.d, "tool.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_ten_lines_inserted_above_never_lose_the_statement(self):
        """THE REGRESSION THIS CLASS EXISTS FOR: the same statement, ten
        lines lower, is the same statement."""
        self.assertEqual(_defining_line(self.scratch("import os, sys\n" + self.STATEMENT), "BIN"), self.STATEMENT)
        moved = "import os, sys\n" + "# a line some later edit put above\n" * 10 + self.STATEMENT
        self.assertEqual(_defining_line(self.scratch(moved), "BIN"), self.STATEMENT)
        self.assertEqual(_line_containing(self.scratch(moved), "sys.path.insert(0,"), self.STATEMENT)

    def test_a_missing_assignment_is_red(self):
        with self.assertRaises(AssertionError):
            _defining_line(self.scratch("import os, sys\n"), "BIN")

    def test_a_doubled_assignment_is_red_never_the_first_one(self):
        with self.assertRaises(AssertionError):
            _defining_line(self.scratch(self.STATEMENT + 'BIN = os.path.expanduser("~/.claude/bin")\n'), "BIN")

    def test_a_missing_plain_statement_is_red(self):
        with self.assertRaises(AssertionError):
            _line_containing(self.scratch("import os, sys\n"), "sys.path.insert(0,")

    def test_a_doubled_plain_statement_is_red_never_the_first_one(self):
        doubled = "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n" * 2
        with self.assertRaises(AssertionError):
            _line_containing(self.scratch(doubled), "sys.path.insert(0,")


if __name__ == "__main__":
    unittest.main()
