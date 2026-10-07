#!/usr/bin/env python3
"""Tests for scripts/donecheck_L4.py (FX-15.1), L4's parent done check.

Each case builds its own repository under a temporary directory: a stand in for L4.5's validator, a
short assessment document, and the files that document cites as `path:line`. Every case drives the real
entry point, donecheck_L4.main(["--root", root]), and judges the exit code and the LAST printed line,
which is the line the closer quotes into the unit's evidence.

HostileHelperTest is the red team's finding class. citation_problems(text, None),
citation_problems(None, root), mechanism_problems(None), mechanism_problems([...]) and read_text(None)
each raised a raw interpreter error; each is now refused with the helper's own refusal shape, and every
value in HOSTILE is asserted through all four helpers.

The module under test is loaded by path (scripts/ is not a package). A missing or unimportable module
makes every test fail with its own message instead of erroring the whole suite out at collection time.
"""
import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MODULE_PATH = os.path.join(HERE, "donecheck_L4.py")
VALIDATOR_REL = os.path.join("scripts", "check_harness_efficiency_assessment.py")
DOC_REL = os.path.join("docs", "architecture", "HARNESS-EFFICIENCY-SOLPI-ASSESSMENT.md")
ALL_IDS = ("action_fusion", "online_context_compact", "observation_pack", "evidence_preserving_reducer")
PARTIAL_IDS = ("action_fusion", "online_context_compact", "evidence_preserving_reducer")
# One hostile value per class: None, booleans where text or a path belongs, numbers, NaN and inf, bytes
# where a path belongs, containers and an empty tuple. "" is added only where the probed helper must
# refuse an empty value too (an empty path is not a file; an empty document is a named problem).
HOSTILE = (None, True, False, 0, 7, 3.5, float("nan"), float("inf"), b"bytes", bytearray(b"x"),
           [], ["--root"], {}, {"root": "/"}, {"/"}, ())
HOSTILE_WITH_EMPTY = HOSTILE + ("",)
VALIDATOR_PREFIX = '''"""Fixture stand in for L4.5's validator, written by this test."""


def validate_assessment(path):
'''
LENIENT_VALIDATOR = VALIDATOR_PREFIX + '    return []'
MISSING_ENTRY_POINT_VALIDATOR = '''"""Fixture stand in without the L4.5 entry point."""


def something_else(path):
    return []
'''


def load_under_test():
    """The module under test, loaded by path, or None when it is absent or unimportable."""
    sys.dont_write_bytecode = True
    if not os.path.isfile(MODULE_PATH):
        return None
    spec = importlib.util.spec_from_file_location("donecheck_L4_under_test", MODULE_PATH)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (ImportError, SyntaxError, OSError, ValueError):
        return None
    return module


DL4 = load_under_test()


def validator_source(body):
    """A fixture validator whose validate_assessment body the caller supplies."""
    return VALIDATOR_PREFIX + body


def document(ids=ALL_IDS, citations=("scripts/required_fast.sh:3", "scripts/check_all.sh:1")):
    """A short assessment document; its mechanism ids and its `path:line` citations are the two guards."""
    parts = ["# Harness efficiency: SoL-Pi mechanisms (fixture)",
             "The four mechanisms assessed here: %s." % ", ".join(ids)]
    if citations:
        parts.append("Read this session: %s." % " ".join("`%s`" % citation for citation in citations))
    return "\n".join(parts) + "\n"


def write_text(path, text):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def write_bytes(path, data):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def build_root(root, doc=document(), validator=LENIENT_VALIDATOR, write_validator=True,
               validator_as_dir=False):
    """Build a repository that passes: a validator stand in, the cited files, and the document."""
    validator_path = os.path.join(root, VALIDATOR_REL)
    if validator_as_dir:
        if not os.path.isdir(validator_path):
            os.makedirs(validator_path)
    elif write_validator:
        write_text(validator_path, validator)
    write_text(os.path.join(root, "scripts", "required_fast.sh"), "one\ntwo\nthree\n")
    write_text(os.path.join(root, "scripts", "check_all.sh"), "one\n")
    if doc is not None:
        write_text(os.path.join(root, DOC_REL), doc)
    return root


def run_main(argv):
    """Drive the real entry point; return (exit code, non blank printed lines)."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = DL4.main(argv)
    return code, [line for line in buffer.getvalue().splitlines() if line.strip()]


def last_line(lines):
    return lines[-1] if lines else ""


def tree_state(root):
    """Every path under root with its size and mtime, so a write of any kind shows up."""
    state = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        for name in dirnames:
            full = os.path.join(dirpath, name)
            info = os.stat(full)
            state.append((full, "dir", info.st_size, info.st_mtime_ns))
        for name in filenames:
            full = os.path.join(dirpath, name)
            info = os.stat(full)
            state.append((full, "file", info.st_size, info.st_mtime_ns))
    return state


class DonecheckL4Case(unittest.TestCase):
    """One temporary repository per case; the check reads only, so nothing has to be restored."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.assertTrue(DL4 is not None,
                        "scripts/donecheck_L4.py must exist and be importable: this suite tests it")

    def tearDown(self):
        self._tmp.cleanup()


class VerdictTest(DonecheckL4Case):

    def test_green_fixture_passes_through_main(self):
        build_root(self.root)
        code, lines = run_main(["--root", self.root])
        self.assertEqual(code, 0, lines)
        self.assertTrue(last_line(lines).startswith("PASS:"), lines)
        self.assertIn("4 of 4 mechanisms named", last_line(lines))
        self.assertIn("2 of 2 citations resolve", last_line(lines))

    def test_validator_errors_are_red(self):
        build_root(self.root, validator=validator_source(
            '    return ["action_fusion names no before/after token count"]'))
        code, lines = run_main(["--root", self.root])
        self.assertEqual(code, 1, lines)
        self.assertTrue(last_line(lines).startswith("FAIL:"), lines)
        self.assertTrue(any("no before/after token count" in line for line in lines), lines)

    def test_one_mechanism_of_four_is_red(self):
        build_root(self.root, doc=document(ids=PARTIAL_IDS))
        code, lines = run_main(["--root", self.root])
        self.assertEqual(code, 1, lines)
        self.assertTrue(last_line(lines).startswith("FAIL:"), lines)
        self.assertTrue(any("observation_pack" in line for line in lines), lines)

    def test_no_citation_is_red(self):
        build_root(self.root, doc=document(citations=()))
        code, lines = run_main(["--root", self.root])
        self.assertEqual(code, 1, lines)
        self.assertTrue(last_line(lines).startswith("FAIL:"), lines)
        self.assertTrue(any("cites no" in line for line in lines), lines)

    def test_citation_past_end_of_file_is_red(self):
        build_root(self.root, doc=document(citations=("scripts/check_all.sh:99",)))
        code, lines = run_main(["--root", self.root])
        self.assertEqual(code, 1, lines)
        self.assertTrue(last_line(lines).startswith("FAIL:"), lines)
        self.assertTrue(any("scripts/check_all.sh:99" in line for line in lines), lines)

    def test_citation_to_a_missing_file_is_red(self):
        build_root(self.root, doc=document(citations=("docs/absent.py:1", "scripts/check_all.sh:1")))
        code, lines = run_main(["--root", self.root])
        self.assertEqual(code, 1, lines)
        self.assertTrue(any("docs/absent.py:1" in line for line in lines), lines)

    def test_empty_assessment_is_not_a_pass(self):
        build_root(self.root, doc="")
        code, lines = run_main(["--root", self.root])
        self.assertEqual(code, 1, lines)
        self.assertTrue(last_line(lines).startswith("FAIL:"), lines)

    def test_repeat_run_gives_the_same_verdict(self):
        build_root(self.root, doc=document(citations=("scripts/check_all.sh:99",)))
        first_code, first_lines = run_main(["--root", self.root])
        second_code, second_lines = run_main(["--root", self.root])
        self.assertEqual(first_code, 1, first_lines)
        self.assertEqual(first_code, second_code)
        self.assertEqual(last_line(first_lines), last_line(second_lines))

    def test_main_writes_nothing_under_root(self):
        build_root(self.root)
        before = tree_state(self.root)
        code, lines = run_main(["--root", self.root])
        self.assertEqual(code, 0, lines)
        self.assertEqual(before, tree_state(self.root))

    @unittest.skipUnless(os.path.isfile(os.path.join(REPO, DOC_REL)),
                         "the L4 assessment document is not in this tree")
    def test_real_tree_case_returns_a_verdict_and_writes_nothing(self):
        before = tree_state(self.root)
        code, lines = run_main(["--root", REPO])
        self.assertIn(code, (0, 1, 2), lines)
        self.assertTrue(last_line(lines).startswith({0: "PASS:", 1: "FAIL:", 2: "NO-DATA:"}[code]), lines)
        self.assertEqual(before, tree_state(self.root))


class NoDataTest(DonecheckL4Case):

    def test_missing_validator_is_nodata(self):
        build_root(self.root, write_validator=False)
        code, lines = run_main(["--root", self.root])
        self.assertEqual(code, 2, lines)
        self.assertTrue(last_line(lines).startswith("NO-DATA:"), lines)
        self.assertIn("L4.5 has not landed", last_line(lines))

    def test_validator_without_the_entry_point_is_nodata(self):
        build_root(self.root, validator=MISSING_ENTRY_POINT_VALIDATOR)
        code, lines = run_main(["--root", self.root])
        self.assertEqual(code, 2, lines)
        self.assertTrue(last_line(lines).startswith("NO-DATA:"), lines)
        self.assertIn("L4.5 has not landed", last_line(lines))

    def test_validator_path_that_is_a_directory_is_nodata(self):
        build_root(self.root, validator_as_dir=True)
        code, lines = run_main(["--root", self.root])
        self.assertEqual(code, 2, lines)
        self.assertIn("L4.5 has not landed", last_line(lines))

    def test_validator_raising_is_nodata(self):
        build_root(self.root, validator=validator_source('    raise ValueError("the document is corrupt")'))
        code, lines = run_main(["--root", self.root])
        self.assertEqual(code, 2, lines)
        self.assertTrue(last_line(lines).startswith("NO-DATA:"), lines)

    def test_missing_assessment_is_nodata(self):
        build_root(self.root, doc=None)
        code, lines = run_main(["--root", self.root])
        self.assertEqual(code, 2, lines)
        self.assertTrue(last_line(lines).startswith("NO-DATA:"), lines)

    def test_non_utf8_assessment_is_nodata(self):
        build_root(self.root)
        write_bytes(os.path.join(self.root, DOC_REL), b"\xff\xfe\x00\x01")
        code, lines = run_main(["--root", self.root])
        self.assertEqual(code, 2, lines)
        self.assertTrue(last_line(lines).startswith("NO-DATA:"), lines)

    def test_assessment_path_that_is_a_directory_is_nodata(self):
        build_root(self.root, doc=None)
        os.makedirs(os.path.join(self.root, DOC_REL))
        code, lines = run_main(["--root", self.root])
        self.assertEqual(code, 2, lines)
        self.assertTrue(last_line(lines).startswith("NO-DATA:"), lines)


class ValidatorResultTest(DonecheckL4Case):

    def test_non_list_validator_results_are_nodata(self):
        for label, literal in (("None", "None"), ("a string", '"clean"'),
                               ("a dict", '{"errors": []}'), ("ints", "[1, 2]")):
            with self.subTest(result=label):
                with tempfile.TemporaryDirectory() as root:
                    build_root(root, validator=validator_source("    return %s" % literal))
                    code, lines = run_main(["--root", root])
                    self.assertEqual(code, 2, (label, lines))
                    self.assertTrue(last_line(lines).startswith("NO-DATA:"), (label, lines))


class HostileInputTest(DonecheckL4Case):
    """main() refuses wrong typed argv and --root values with its own NO-DATA code, never a TypeError."""

    def test_hostile_argv_is_refused_not_crashed(self):
        build_root(self.root)
        for value in ("scripts/check_all.sh", b"--root", 7, 3.5, float("nan"), True, False,
                      {"root": self.root}, {self.root}, [7], [None], object()):
            with self.subTest(argv=repr(value)):
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    code = DL4.main(value)
                self.assertEqual(code, 2, (value, buffer.getvalue()))
                self.assertTrue(buffer.getvalue().strip(), "no refusal printed for %r" % (value,))

    def test_hostile_root_value_is_refused_not_crashed(self):
        for value in (7, 3.5, float("nan"), True, None, b"/tmp", {"a": 1}, ["--root", 7], ""):
            with self.subTest(root=repr(value)):
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    code = DL4.main(["--root", value])
                self.assertEqual(code, 2, (value, buffer.getvalue()))
                self.assertTrue(buffer.getvalue().strip(), "no refusal printed for %r" % (value,))

    def test_a_root_that_is_not_a_directory_is_nodata(self):
        build_root(self.root)
        for value in (os.path.join(self.root, "scripts", "check_all.sh"),
                      os.path.join(self.root, "absent")):
            with self.subTest(root=repr(value)):
                code, lines = run_main(["--root", value])
                self.assertEqual(code, 2, lines)
                self.assertTrue(last_line(lines).startswith("NO-DATA:"), lines)


class HostileHelperTest(DonecheckL4Case):
    """The red team's finding class: a helper handed a hostile value refuses, never raises TypeError.

    Before these guards: citation_problems(text, None) and citation_problems(None, root) raised
    TypeError, mechanism_problems(None) and mechanism_problems([]) raised AttributeError, and
    read_text(None) raised TypeError. Each now returns the helper's own refusal shape, and each one of
    those calls is asserted below.
    """

    def test_citation_problems_refuses_a_hostile_root(self):
        for value in HOSTILE:
            with self.subTest(root=repr(value)):
                seen, problems = DL4.citation_problems(document(), value)
                self.assertEqual(seen, 0, (value, problems))
                self.assertTrue(problems, "no refusal for root %r" % (value,))
                self.assertTrue(all(isinstance(problem, str) for problem in problems), (value, problems))

    def test_citation_problems_refuses_a_hostile_text(self):
        for value in HOSTILE:
            with self.subTest(text=repr(value)):
                seen, problems = DL4.citation_problems(value, self.root)
                self.assertEqual(seen, 0, (value, problems))
                self.assertTrue(problems, "no refusal for text %r" % (value,))
                self.assertTrue(all(isinstance(problem, str) for problem in problems), (value, problems))

    def test_mechanism_problems_refuses_a_hostile_value(self):
        for value in HOSTILE_WITH_EMPTY:
            with self.subTest(text=repr(value)):
                problems = DL4.mechanism_problems(value)
                self.assertTrue(problems, "no refusal for %r" % (value,))
                self.assertTrue(all(isinstance(problem, str) for problem in problems), (value, problems))

    def test_read_text_refuses_a_hostile_path(self):
        for value in HOSTILE_WITH_EMPTY:
            with self.subTest(path=repr(value)):
                body, why = DL4.read_text(value)
                self.assertIsNone(body, (value, why))
                self.assertTrue(isinstance(why, str) and why, "no refusal for %r" % (value,))

    def test_load_validator_refuses_a_hostile_root(self):
        for value in HOSTILE_WITH_EMPTY:
            with self.subTest(root=repr(value)):
                module, why = DL4.load_validator(value)
                self.assertIsNone(module, (value, why))
                self.assertTrue(isinstance(why, str) and why, "no refusal for %r" % (value,))

    def test_a_pathological_line_number_is_reported_not_crashed(self):
        text = document(citations=("scripts/check_all.sh:%s" % ("9" * 6000),))
        seen, problems = DL4.citation_problems(text, self.root)
        self.assertEqual(seen, 1, problems)
        self.assertTrue(problems, "a line number that long must be reported")
        self.assertTrue(any("line number" in problem for problem in problems), problems)


if __name__ == "__main__":
    unittest.main()
