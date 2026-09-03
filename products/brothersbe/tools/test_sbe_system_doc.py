"""Fixtures for `tools/sbe_system_doc.py`, the generated SYSTEM.md (P12).

P12 was a reviewer's complaint: fifty designs after a year, none of them
describing the system. The fix is a document nothing has to remember to
update, checked by `--check`. This suite pins the two promises that make
that true rather than decorative:

  1. `render()` actually reflects what it is handed. A command, a check, a
     behaviour row or an entity added to the inputs shows up in the text
     `render()` produces; this is the "driven backwards" case, run directly
     against the pure function rather than through a copied repository,
     because `render()` (unlike `build()`, which always reads THIS
     repository) takes its three inputs as arguments.
  2. `--check` tells the two states apart. Fed the real, regenerated body it
     exits 0 and says the document still describes the code; fed a body one
     line short of that it exits 1 and names what changed, in a diff, on
     stderr, never a bare "something is wrong".

`build()` itself is exercised against the real repository this suite runs
in, because that is exactly what `scripts/system_doc.py` in the sibling
umbrella repo already does for its own suite: the object under description
is the repository carrying the description, so there is no second, smaller
repository to build as a fixture for it.
"""
import io
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import sbe_system_doc as sysdoc  # noqa: E402


class TestRenderReflectsItsInputs(unittest.TestCase):
    """The "driven backwards" case: add a fixture, watch the text name it."""

    def test_a_fixture_command_appears_in_the_rendered_commands_table(self):
        before = sysdoc.render([("doctor", "check the install")], [], [])
        after = sysdoc.render([("doctor", "check the install"),
                               ("frobnicate", "a fixture command nothing real registers")],
                              [], [])
        self.assertNotIn("frobnicate", before)
        self.assertIn("| `frobnicate` | a fixture command nothing real registers |", after)

    def test_a_fixture_check_appears_in_the_rendered_checks_table(self):
        before = sysdoc.render([], [], [])
        after = sysdoc.render([], [("fixture-check", "ran", "why this exists",
                                    "python3 fixture.py")], [])
        self.assertNotIn("fixture-check", before)
        self.assertIn("| `fixture-check` | ran | why this exists | `python3 fixture.py` |", after)

    def test_a_fixture_behaviour_row_appears_under_its_dossier(self):
        row = {"id": "B99", "starting point": "a fixture starting point",
              "trigger": "a fixture trigger", "required outcome": "a fixture outcome",
              "proof": "a fixture proof"}
        before = sysdoc.render([], [], [{"name": "fixture-dossier", "rows": [], "entities": {}}])
        after = sysdoc.render([], [], [{"name": "fixture-dossier", "rows": [row], "entities": {}}])
        self.assertNotIn("B99", before)
        self.assertIn("| B99 | a fixture starting point | a fixture trigger | a fixture outcome |",
                     after)

    def test_a_fixture_entity_appears_under_its_dossier(self):
        before = sysdoc.render([], [], [{"name": "fixture-dossier", "rows": [], "entities": {}}])
        after = sysdoc.render([], [], [{"name": "fixture-dossier", "rows": [],
                                       "entities": {"FixtureEntity": "the fixture system"}}])
        self.assertNotIn("FixtureEntity", before)
        self.assertIn("| FixtureEntity | the fixture system |", after)

    def test_absent_inputs_read_no_data_rather_than_an_empty_table(self):
        text = sysdoc.render(None, None, None)
        self.assertEqual(text.count(sysdoc.NODATA), 3)


class TestCheckTellsTheTwoStatesApart(unittest.TestCase):
    def test_check_passes_when_the_file_matches_a_regeneration(self):
        body = sysdoc.render([("doctor", "d")], [], [])
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write(body)
            path = fh.name
        try:
            real_build = sysdoc.build
            sysdoc.build = lambda: body
            try:
                code = sysdoc.main(["--check", "--out", path])
            finally:
                sysdoc.build = real_build
            self.assertEqual(code, 0)
        finally:
            os.unlink(path)

    def test_check_fails_and_names_the_drift_when_the_file_is_stale(self):
        stale = sysdoc.render([("doctor", "d")], [], [])
        fresh = sysdoc.render([("doctor", "d"), ("frobnicate", "a fixture command")], [], [])
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write(stale)
            path = fh.name
        try:
            real_build = sysdoc.build
            sysdoc.build = lambda: fresh
            try:
                old_stderr = sys.stderr
                sys.stderr = captured = io.StringIO()
                try:
                    code = sysdoc.main(["--check", "--out", path])
                finally:
                    sys.stderr = old_stderr
            finally:
                sysdoc.build = real_build
            self.assertEqual(code, 1)
            self.assertIn("frobnicate", captured.getvalue())
            self.assertIn("NO LONGER DESCRIBES THE CODE", captured.getvalue())
        finally:
            os.unlink(path)

    def test_check_fails_when_the_file_does_not_exist(self):
        code = sysdoc.main(["--check", "--out", os.path.join(tempfile.gettempdir(),
                                                             "sbe-system-doc-missing.md")])
        self.assertEqual(code, 1)


class TestBuildOverTheRealRepository(unittest.TestCase):
    """`build()` always reads THIS repository, the same way
    `scripts/system_doc.py` reads its own in the sibling umbrella repo, so
    it is exercised against the real tree rather than a copy of it."""

    def test_build_finds_real_commands_checks_and_a_dossier(self):
        body = sysdoc.build()
        self.assertIn("`doctor`", body)
        self.assertIn("## Design dossiers", body)
        self.assertNotIn(sysdoc.NODATA + ": `brothersbe.cli`", body)

    def test_build_is_idempotent(self):
        self.assertEqual(sysdoc.build(), sysdoc.build())

    def test_check_passes_over_the_committed_system_md(self):
        out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "sbe_system_doc.py"),
                             "--check"], capture_output=True, text=True, cwd=ROOT, timeout=60)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("still describes the code", out.stdout)


if __name__ == "__main__":
    unittest.main()
