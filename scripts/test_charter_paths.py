"""test_charter_paths: drives scripts/charter_paths.py BACKWARDS.

The checker exists because a green check told nobody the charter's architecture
of record was missing from the tree (row E47). A checker that has only ever been
seen green is the same class of claim, so this drives all four verdicts over
temporary fixture trees, including the exact E47 shape: a charter naming a
record the tree does not hold must come back FAIL.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "charter_paths.py")
sys.path.insert(0, HERE)
import charter_paths  # noqa: E402


def run(root, charter=None):
    cmd = [sys.executable, SCRIPT, "--root", root]
    if charter is not None:
        cmd += ["--charter", charter]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return proc.returncode, proc.stdout + proc.stderr


class Fixture(unittest.TestCase):
    """A throwaway tree with a charter written for the case under test."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="charter-paths-fixture-")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "docs"))

    def write_charter(self, tokens):
        """tokens: what the fixture charter names in backticks."""
        body = "# fixture\n\n" + "\n".join("names `%s` here" % t for t in tokens)
        path = os.path.join(self.root, "docs", "CHARTER.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body + "\n")
        return path

    def touch(self, rel):
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write("fixture\n")


class CharterPaths(Fixture):
    EXEMPT = sorted(charter_paths.GENERIC)
    RECORD = os.path.join("docs", "plan",
                          "ADR-2026-08-23-one-brother-repository.md")

    def test_pass_when_every_named_path_exists(self):
        self.touch(self.RECORD)
        self.write_charter(self.EXEMPT + [self.RECORD])
        code, out = run(self.root)
        self.assertEqual(code, 0, out)
        self.assertIn("PASS", out)

    def test_fail_on_the_e47_shape_a_record_that_is_not_in_the_tree(self):
        # No touch: the charter names the architecture of record and the tree
        # does not hold it. This is the state row E47 was opened for.
        self.write_charter(self.EXEMPT + [self.RECORD])
        code, out = run(self.root)
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL", out)
        self.assertIn("ADR-2026-08-23-one-brother-repository.md", out)

    def test_fail_when_an_exemption_outlives_the_sentence_that_earned_it(self):
        self.touch(self.RECORD)
        self.write_charter(self.EXEMPT[1:] + [self.RECORD])
        code, out = run(self.root)
        self.assertEqual(code, 1, out)
        self.assertIn("stale exemption", out)
        self.assertIn(self.EXEMPT[0], out)

    def test_no_data_when_the_charter_cannot_be_read(self):
        code, out = run(self.root)
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)
        self.assertNotIn("PASS", out)

    def test_no_data_when_the_charter_names_no_path_at_all(self):
        self.write_charter(["nothing", "path-shaped"])
        code, out = run(self.root)
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)

    def test_a_quoted_command_line_is_not_read_as_a_path(self):
        self.assertFalse(charter_paths.path_shaped(
            "/usr/bin/python3 -m unittest tests/test_surface.py"))
        self.assertTrue(charter_paths.path_shaped("tests/test_surface.py"))

    def test_the_real_charter_passes_in_this_repository(self):
        repo = os.path.dirname(HERE)
        code, out = run(repo)
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
