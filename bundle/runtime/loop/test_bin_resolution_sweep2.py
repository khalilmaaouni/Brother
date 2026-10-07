"""F2b sibling sweep, the remaining 6 sites named by the audit: spec_intake.py:9,
probe_wave.py:13, spec_accept.py:14, spec_council.py:9 and unit_runner.py:15 all take the
identical BIN = os.path.dirname(os.path.abspath(__file__)) fix already proven for
repair_wave.py, commit_scan.py, land_batch.py and model_call.py in test_bin_resolution.py.

abc/model_conformance.py:9 is different: it lives in a subdirectory (abc/), the sibling it
imports (model_router.py) lives one directory UP, in scripts/loop/ itself, and
~/.claude/bin/abc/ does not exist as a deployed path (checked directly with ls: only a flat
~/.claude/bin/model_router.py does, abc/ is never installed there today). Its fix reuses the
file's own already-computed AB variable (its own directory, defined at the file's own top) one
level up, os.path.dirname(AB), rather than the same-directory pattern every other site uses.

Reuses test_bin_resolution.py's own helpers rather than a second copy of them.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_bin_resolution import _defining_line, _line_containing, _FakeSysModule, _owned_line_holds  # noqa: E402


def _extract_clause(line, prefix):
    """One ';'-joined clause from a compound statement line, the one starting with `prefix`.
    spec_intake.py and its siblings write BIN and its sys.path.insert as two clauses on ONE
    physical line; abc/model_conformance.py additionally chains an `import` after its
    sys.path.insert. Executing the whole line would run that import for real (harmless here,
    since model_router.py is a safe stdlib-only import) or, for AB's own line, would call the
    real os.makedirs against a directory built from a FAKE path, which must never happen in a
    test. Isolating the one clause under test avoids both."""
    for clause in line.split(";"):
        if clause.strip().startswith(prefix):
            return clause.strip()
    raise AssertionError("no clause starting with %r in %r" % (prefix, line))


class TestRemainingSiblingSweepSites(unittest.TestCase):
    def _assert_bin_resolves(self, filename):
        line = _defining_line(os.path.join(HERE, filename), "BIN")
        fake_path = "/somewhere/else/entirely/" + filename
        fake_sys = _FakeSysModule()
        ns = {"__file__": fake_path, "os": os, "sys": fake_sys}
        exec(compile(line, "<%s:BIN>" % filename, "exec"), ns)
        self.assertEqual(ns["BIN"], os.path.dirname(fake_path))
        self.assertNotIn("claude/bin", ns["BIN"])
        self.assertIn(os.path.dirname(fake_path), fake_sys.path)

    def test_spec_intake_bin_resolves_to_its_own_directory(self):
        self._assert_bin_resolves("spec_intake.py")

    def test_probe_wave_bin_resolves_to_its_own_directory(self):
        self._assert_bin_resolves("probe_wave.py")

    def test_spec_accept_bin_resolves_to_its_own_directory(self):
        self._assert_bin_resolves("spec_accept.py")

    def test_spec_council_bin_resolves_to_its_own_directory(self):
        self._assert_bin_resolves("spec_council.py")

    def test_unit_runner_bin_resolves_to_its_own_directory(self):
        self._assert_bin_resolves("unit_runner.py")

    def test_abc_model_conformance_resolves_via_its_own_AB_variable_one_level_up(self):
        path = os.path.join(HERE, "abc", "model_conformance.py")
        ab_clause = _extract_clause(_line_containing(path, "AB = os.path.dirname"), "AB =")
        insert_clause = _extract_clause(_line_containing(path, "sys.path.insert(0,"), "sys.path.insert(0,")
        fake_path = "/somewhere/else/entirely/abc/model_conformance.py"
        fake_sys = _FakeSysModule()
        ns = {"__file__": fake_path, "os": os, "sys": fake_sys}
        exec(compile(ab_clause, "<model_conformance.py:AB>", "exec"), ns)
        exec(compile(insert_clause, "<model_conformance.py:sys.path.insert>", "exec"), ns)
        self.assertEqual(fake_sys.path, [os.path.dirname(os.path.dirname(fake_path))])
        self.assertNotIn("claude/bin", fake_sys.path[0])


class TestNoHardcodedClaudeBinAtTheseSixLines(unittest.TestCase):
    """Each swept statement found by what it is, never by a line number
    (2026-10-06: the same pins in test_bin_resolution.py went stale and
    red the day a merge put lines above one of them; these five and the
    fixed line 9 below were one edit away from the same). One case per
    statement, so one never hides the next."""

    def _bin_line_holds(self, filename):
        _owned_line_holds(self, filename + " BIN", _defining_line(os.path.join(HERE, filename), "BIN"))

    def test_spec_intake_bin_line_is_file_relative(self):
        self._bin_line_holds("spec_intake.py")

    def test_probe_wave_bin_line_is_file_relative(self):
        self._bin_line_holds("probe_wave.py")

    def test_spec_accept_bin_line_is_file_relative(self):
        self._bin_line_holds("spec_accept.py")

    def test_spec_council_bin_line_is_file_relative(self):
        self._bin_line_holds("spec_council.py")

    def test_unit_runner_bin_line_is_file_relative(self):
        self._bin_line_holds("unit_runner.py")

    def test_abc_model_conformance_path_insert_line_carries_no_hardcoded_bin(self):
        path = os.path.join(HERE, "abc", "model_conformance.py")
        _owned_line_holds(self, "abc/model_conformance.py sys.path.insert",
                          _line_containing(path, "sys.path.insert(0,"), file_relative=False)


if __name__ == "__main__":
    unittest.main()
