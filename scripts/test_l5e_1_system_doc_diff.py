"""Tests for L5e.1: SYSTEM.md regeneration and diff.

RQ-1 and RQ-2 are enforced here: the checked-in SYSTEM.md must be byte
identical to what scripts/system_doc.py would regenerate for the same tree,
and any difference must BLOCK rather than pass. Every failure direction the
specification names produces a non-empty diff rather than an exception, and
every hostile input is refused with the module's own deliberate error.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import system_doc as S  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager can copy this test without scripts/tmp_sandbox.py beside it.
    # Say so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYSTEM_MD = os.path.join(REPO_ROOT, "SYSTEM.md")
GENERATOR = os.path.join(REPO_ROOT, "scripts", "system_doc.py")


def _fixture_repo():
    """A small but real tree the generator can describe in full."""
    root = tempfile.mkdtemp(prefix="l5e1-")
    scripts = os.path.join(root, "scripts")
    os.makedirs(scripts)
    with open(os.path.join(scripts, "system_doc.py"), "w", encoding="utf-8") as fh:
        fh.write('"""system_doc: a fixture generator that exists."""\n')
    with open(os.path.join(scripts, "fixture.py"), "w", encoding="utf-8") as fh:
        fh.write('"""fixture: a part that exists in the fixture tree."""\n')
    with open(os.path.join(scripts, "check_all.sh"), "w", encoding="utf-8") as fh:
        fh.write('run_check "fixture" python3 scripts/fixture.py\n')
    return root


def _regenerate(root):
    """Write root/SYSTEM.md using the same code path the battery uses."""
    saved = (S.ROOT, S.SCRIPTS, S.BATTERY, S.OUT)
    S.ROOT = root
    S.SCRIPTS = os.path.join(root, "scripts")
    S.BATTERY = os.path.join(root, "scripts", "check_all.sh")
    S.OUT = os.path.join(root, "SYSTEM.md")
    try:
        return S.main(["--out", S.OUT])
    finally:
        S.ROOT, S.SCRIPTS, S.BATTERY, S.OUT = saved


class SystemDocDiff(unittest.TestCase):
    """RQ-1 and RQ-2: regenerate, diff, and block on any difference."""

    @unittest.skipUnless(
        os.path.isfile(SYSTEM_MD) and os.path.isfile(GENERATOR),
        "SYSTEM.md or scripts/system_doc.py is absent")
    def test_system_doc_diff(self):
        """Assert compute_system_doc_diff returns an empty string."""
        self.assertEqual(S.compute_system_doc_diff(REPO_ROOT), "")

    def test_a_current_temp_tree_passes_with_an_empty_diff(self):
        root = _fixture_repo()
        _regenerate(root)
        self.assertEqual(S.compute_system_doc_diff(root), "")

    def test_a_stale_system_md_blocks(self):
        root = _fixture_repo()
        _regenerate(root)
        with open(os.path.join(root, "SYSTEM.md"), "a", encoding="utf-8") as fh:
            fh.write("\nsomebody edited this by hand\n")
        self.assertNotEqual(S.compute_system_doc_diff(root), "")

    def test_a_missing_generator_blocks(self):
        root = _fixture_repo()
        os.remove(os.path.join(root, "scripts", "system_doc.py"))
        _regenerate(root)
        self.assertNotEqual(S.compute_system_doc_diff(root), "")

    def test_an_empty_system_md_blocks(self):
        root = _fixture_repo()
        _regenerate(root)
        open(os.path.join(root, "SYSTEM.md"), "w").close()
        self.assertNotEqual(S.compute_system_doc_diff(root), "")

    def test_a_missing_system_md_blocks(self):
        root = _fixture_repo()
        self.assertNotEqual(S.compute_system_doc_diff(root), "")

    def test_hostile_repo_root_is_refused(self):
        for bad in (None, 123, 3.5, True, "", b"/tmp", [], {}, ()):
            with self.assertRaises(ValueError):
                S.compute_system_doc_diff(bad)

    def test_a_missing_directory_is_refused(self):
        with self.assertRaises(ValueError):
            S.compute_system_doc_diff(
                os.path.join(tempfile.mkdtemp(), "nope"))


class HostileInputIsRefused(unittest.TestCase):
    """RQ-10: hostile or missing input returns a refusal or raises ValueError,
    never a raw interpreter exception."""

    def test_purpose_refuses_a_non_string_path(self):
        for bad in (None, 123, b"/tmp/x.py", [], {}, ()):
            with self.assertRaises(ValueError):
                S.purpose(bad)

    def test_purpose_returns_no_data_for_corrupt_bytes(self):
        root = tempfile.mkdtemp(prefix="l5e1-")
        path = os.path.join(root, "broken.py")
        with open(path, "wb") as fh:
            fh.write(b"\xff\xfe\x00\x01")
        self.assertIsNone(S.purpose(path))

    def test_purpose_returns_no_data_for_a_missing_file(self):
        self.assertIsNone(
            S.purpose(os.path.join(tempfile.mkdtemp(), "nope.py")))

    def test_parts_refuses_a_non_directory_argument(self):
        for bad in ({}, [], b"/tmp", 123, "", True):
            with self.assertRaises(ValueError):
                S.parts(bad)

    def test_parts_returns_no_rows_for_a_missing_directory(self):
        self.assertEqual(S.parts(os.path.join(tempfile.mkdtemp(), "nope")), [])

    def test_render_refuses_none_rows(self):
        with self.assertRaises(ValueError):
            S.render(None, [])

    def test_render_refuses_a_non_pair_check(self):
        rows = [{"module": "a", "purpose": "x", "proven_by": [], "has_tests": False}]
        for bad in (float("nan"), 1, "cmd", None, ("a",), ("a", 1)):
            with self.assertRaises(ValueError):
                S.render(rows, [bad])

    def test_render_refuses_a_row_with_no_module(self):
        for bad_row in ({"module": None}, {"module": ""}, {"module": 7}, "row"):
            with self.assertRaises(ValueError):
                S.render([bad_row], [])

    def test_render_refuses_duplicate_modules(self):
        rows = [{"module": "a", "purpose": "x", "proven_by": []},
                {"module": "a", "purpose": "y", "proven_by": []}]
        with self.assertRaises(ValueError):
            S.render(rows, [])

    def test_main_refuses_an_unwritable_out(self):
        self.assertEqual(S.main(["--out", tempfile.mkdtemp()]), 2)

    def test_main_refuses_a_missing_parent(self):
        self.assertEqual(
            S.main(["--out", os.path.join(tempfile.mkdtemp(), "nope", "x.md")]),
            2)


if __name__ == "__main__":
    unittest.main()
