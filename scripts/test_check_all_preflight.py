#!/usr/bin/env python3
"""R2.4: preflight assertions over scripts/check_all.sh, and their refusals.

read_text(), first_run_check_index(), unset_loop_index() and
assert_clean_index() are the one validation point every caller routes
through. Each refuses a corrupt, missing or hostile input with a deliberate
ValueError: a script that has lost its root cd, its seventeen-variable git
location unset, or its assert-clean abort line BLOCKS here, and never reads
as the safe case.

Enforced here:
  R13: the guard call runs BEFORE the first run_check and carries || exit 2.
  R14: the root cd runs before the unset loop.
  R15: ROOT is captured after that cd.
"""

import os
import tempfile
import unittest
from typing import List

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK_ALL = os.path.join(HERE, "check_all.sh")

CD_MARKER = 'cd "$(dirname "$0")/.." || exit 1'
ROOT_MARKER = 'ROOT="$(pwd)"'
UNSET_MARKER = "\nunset GIT_DIR "
RUN_CHECK_MARKER = '\nrun_check "'
ASSERT_CLEAN_MARKER = "--assert-clean"
GUARD_PATH = "scripts/git_location_guard.py"
WHERE_MARKER = "--where check_all"
EXIT_2_MARKER = "|| exit 2"

UNSET_NAMES = [
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_PREFIX",
    "GIT_NAMESPACE",
    "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_GRAFT_FILE",
    "GIT_SHALLOW_FILE",
    "GIT_INTERNAL_SUPER_PREFIX",
    "GIT_REPLACE_REF_BASE",
    "GIT_NO_REPLACE_OBJECTS",
]
UNSET_COUNT = 17
REQUIRED_UNSET_NAMES = ["GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                        "GIT_COMMON_DIR", "GIT_CONFIG", "GIT_NAMESPACE"]

GOOD_UNSET_LINE = "unset " + " ".join(UNSET_NAMES)
SHORT_UNSET_LINE = "unset " + " ".join(UNSET_NAMES[:6])
GOOD_GUARD_LINE = ("python3 scripts/git_location_guard.py --assert-clean "
                   "--where check_all || exit 2")
GOOD_FIRST_CHECK = ('run_check "surface" /usr/bin/python3 -m unittest -v '
                    'tests/test_surface.py')


def _require_str(text, name):
    """Refuse anything that is not a str."""
    if not isinstance(text, str):
        raise ValueError("%s: text must be str, got %s"
                         % (name, type(text).__name__))
    return text


def _line_start(text, hit):
    return text.rfind("\n", 0, hit) + 1


def _line_at(text, start):
    return text[start:].split("\n", 1)[0]


def _cd_index(text):
    return text.find(CD_MARKER)


def _root_index(text):
    return text.find(ROOT_MARKER)


def _unset_line_start(text):
    hit = text.find(UNSET_MARKER)
    if hit < 0:
        return -1
    return hit + 1


def _first_run_check_start(text):
    hit = text.find(RUN_CHECK_MARKER)
    if hit < 0:
        return -1
    return hit + 1


def _refuse_root_before_cd(text):
    root_at = _root_index(text)
    if root_at < 0:
        return
    cd_at = _cd_index(text)
    if cd_at < 0:
        raise ValueError(
            "check_all.sh captures ROOT with no root cd before it")
    if cd_at > root_at:
        raise ValueError(
            "ROOT must be captured AFTER the root cd: cd at %d, ROOT at %d"
            % (cd_at, root_at))


def read_text(path: str) -> str:
    """Read path as bytes and return its utf-8 text, or refuse."""
    if not isinstance(path, str):
        raise ValueError("read_text: path must be str, got %s"
                         % (type(path).__name__,))
    if not path:
        raise ValueError("read_text: path is empty")
    if not os.path.isfile(path):
        raise ValueError("read_text: not a regular file: %r" % (path,))
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise ValueError("read_text: cannot read %r: %s" % (path, exc))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("read_text: %r is not utf-8: %s" % (path, exc))
    _refuse_root_before_cd(text)
    return text


def first_run_check_index(text: str) -> int:
    """Offset of the first run_check call, or a refusal."""
    _require_str(text, "first_run_check_index")
    cd_at = _cd_index(text)
    if cd_at < 0:
        raise ValueError("check_all.sh has no root cd before its checks")
    first = _first_run_check_start(text)
    if first < 0:
        raise ValueError("check_all.sh has no run_check call")
    unset_at = _unset_line_start(text)
    if unset_at >= 0 and cd_at > unset_at:
        raise ValueError(
            "the root cd must run BEFORE the unset loop: cd at %d, "
            "unset at %d" % (cd_at, unset_at))
    if cd_at > first:
        raise ValueError(
            "the root cd must run BEFORE the first run_check: cd at %d, "
            "first run_check at %d" % (cd_at, first))
    if unset_at >= 0 and unset_at > first:
        raise ValueError(
            "the git location unset must run BEFORE the first run_check: "
            "unset at %d, first run_check at %d" % (unset_at, first))
    return first


def _unset_names(line: str) -> List[str]:
    head = line.split("#", 1)[0].strip()
    parts = head.split()
    if parts and parts[0] == "unset":
        parts = parts[1:]
    return parts


def unset_loop_index(text: str) -> int:
    """Offset of the unset loop line, or a refusal."""
    _require_str(text, "unset_loop_index")
    start = _unset_line_start(text)
    if start < 0:
        raise ValueError("check_all.sh has no unset GIT_DIR loop")
    names = _unset_names(_line_at(text, start))
    if len(names) != UNSET_COUNT:
        raise ValueError(
            "the unset loop must name exactly %d git location variables, "
            "saw %d: %r" % (UNSET_COUNT, len(names), names))
    if len(set(names)) != len(names):
        raise ValueError("the unset loop names a variable twice: %r"
                         % (names,))
    missing = [name for name in REQUIRED_UNSET_NAMES if name not in names]
    if missing:
        raise ValueError(
            "the unset loop is missing required git location variables: %r"
            % (missing,))
    return start


def assert_clean_index(text: str) -> int:
    """Offset of the assert-clean line, or a refusal."""
    _require_str(text, "assert_clean_index")
    hit = text.find(ASSERT_CLEAN_MARKER)
    if hit < 0:
        raise ValueError(
            "check_all.sh never calls git_location_guard.py --assert-clean")
    start = _line_start(text, hit)
    line = _line_at(text, start)
    for required in (GUARD_PATH, ASSERT_CLEAN_MARKER, WHERE_MARKER,
                     EXIT_2_MARKER):
        if required not in line:
            raise ValueError(
                "the --assert-clean line is missing %r: %r"
                % (required, line))
    first = first_run_check_index(text)
    if start > first:
        raise ValueError(
            "the --assert-clean call must run BEFORE the first run_check: "
            "assert-clean at %d, first run_check at %d" % (start, first))
    return start


def _fixture_clean():
    return "\n".join([
        "#!/bin/sh",
        CD_MARKER,
        ROOT_MARKER,
        GOOD_UNSET_LINE,
        GOOD_GUARD_LINE,
        "run_check() {",
        "  :",
        "}",
        GOOD_FIRST_CHECK,
        "",
    ])


def _fixture_guard_after_first():
    return "\n".join([
        "#!/bin/sh",
        CD_MARKER,
        ROOT_MARKER,
        GOOD_UNSET_LINE,
        "run_check() {",
        "  :",
        "}",
        GOOD_FIRST_CHECK,
        GOOD_GUARD_LINE,
        "",
    ])


def _fixture_guard_missing():
    lines = _fixture_clean().split("\n")
    lines.remove(GOOD_GUARD_LINE)
    return "\n".join(lines)


def _fixture_guard_without_exit():
    return _fixture_clean().replace(EXIT_2_MARKER, "", 1)


def _fixture_unset_missing():
    lines = _fixture_clean().split("\n")
    lines.remove(GOOD_UNSET_LINE)
    return "\n".join(lines)


def _fixture_unset_short():
    return _fixture_clean().replace(GOOD_UNSET_LINE, SHORT_UNSET_LINE, 1)


def _fixture_root_before_cd():
    return "\n".join([
        "#!/bin/sh",
        ROOT_MARKER,
        CD_MARKER,
        GOOD_UNSET_LINE,
        GOOD_GUARD_LINE,
        GOOD_FIRST_CHECK,
        "",
    ])


@unittest.skipUnless(os.path.isfile(CHECK_ALL),
                     "scripts/check_all.sh is not present")
class CheckAllPreflightTest(unittest.TestCase):

    def test_check_all_unsets_before_first_run_check(self):
        text = read_text(CHECK_ALL)
        unset_at = unset_loop_index(text)
        first_at = first_run_check_index(text)
        self.assertLess(
            unset_at, first_at,
            "the git location unset must run BEFORE the first run_check")

    def test_check_all_asserts_clean(self):
        text = read_text(CHECK_ALL)
        idx = assert_clean_index(text)
        line = _line_at(text, idx)
        self.assertIn(GUARD_PATH, line)
        self.assertIn(ASSERT_CLEAN_MARKER, line)
        self.assertIn(WHERE_MARKER, line)
        self.assertIn(EXIT_2_MARKER, line)
        self.assertLess(
            idx, first_run_check_index(text),
            "the --assert-clean call must run BEFORE the first run_check")

    def test_check_all_cd_before_unset(self):
        text = read_text(CHECK_ALL)
        cd_at = _cd_index(text)
        unset_at = unset_loop_index(text)
        self.assertGreaterEqual(cd_at, 0,
                                "check_all.sh has no root cd")
        self.assertLess(cd_at, unset_at,
                        "the root cd must run BEFORE the unset loop")

    def test_check_all_root_captured_after_cd(self):
        text = read_text(CHECK_ALL)
        cd_at = _cd_index(text)
        root_at = _root_index(text)
        self.assertGreaterEqual(cd_at, 0, "check_all.sh has no root cd")
        self.assertGreaterEqual(root_at, 0, "check_all.sh never captures ROOT")
        self.assertLess(cd_at, root_at,
                        "ROOT must be captured AFTER the root cd")

    def test_check_all_unset_has_seventeen_vars(self):
        text = read_text(CHECK_ALL)
        idx = unset_loop_index(text)
        names = _unset_names(_line_at(text, idx))
        self.assertEqual(
            len(names), 17,
            "the unset loop must name exactly seventeen location variables, "
            "saw %d: %r" % (len(names), names))
        for required in REQUIRED_UNSET_NAMES:
            self.assertIn(required, names)


class CorruptOrHostileInputIsRefusedTest(unittest.TestCase):

    def test_clean_fixture_is_accepted(self):
        text = _fixture_clean()
        self.assertGreaterEqual(assert_clean_index(text), 0)
        self.assertGreaterEqual(unset_loop_index(text), 0)
        self.assertGreaterEqual(first_run_check_index(text), 0)

    def test_assert_clean_refuses_when_after_first_run_check(self):
        with self.assertRaises(ValueError):
            assert_clean_index(_fixture_guard_after_first())

    def test_assert_clean_refuses_when_missing(self):
        with self.assertRaises(ValueError):
            assert_clean_index(_fixture_guard_missing())

    def test_assert_clean_refuses_when_no_exit(self):
        with self.assertRaises(ValueError):
            assert_clean_index(_fixture_guard_without_exit())

    def test_root_before_cd_is_refused(self):
        with self.assertRaises(ValueError):
            _refuse_root_before_cd(_fixture_root_before_cd())

    def test_unset_missing_is_refused(self):
        with self.assertRaises(ValueError):
            unset_loop_index(_fixture_unset_missing())

    def test_unset_short_is_refused(self):
        with self.assertRaises(ValueError):
            unset_loop_index(_fixture_unset_short())

    def test_hostile_input_is_refused(self):
        hostile = (None, 123, 1.5, True, float("nan"), b"text", [], {},
                   set(), object())
        for bad in hostile:
            for fn in (read_text, first_run_check_index, unset_loop_index,
                       assert_clean_index):
                with self.assertRaises(ValueError):
                    fn(bad)
        root = tempfile.mkdtemp(prefix="r24_preflight_")
        self.addCleanup(os.rmdir, root)
        missing = os.path.join(root, "no_such_file.sh")
        with self.assertRaises(ValueError):
            read_text(missing)
        with self.assertRaises(ValueError):
            read_text(root)
        bad_bytes = os.path.join(root, "not-utf8.sh")
        with open(bad_bytes, "wb") as handle:
            handle.write(b"\xff\xfe\x00\x01")
        self.addCleanup(os.remove, bad_bytes)
        with self.assertRaises(ValueError):
            read_text(bad_bytes)
        root_first = os.path.join(root, "root-first.sh")
        with open(root_first, "w", encoding="utf-8") as handle:
            handle.write(_fixture_root_before_cd())
        self.addCleanup(os.remove, root_first)
        with self.assertRaises(ValueError):
            read_text(root_first)


if __name__ == "__main__":
    unittest.main()
