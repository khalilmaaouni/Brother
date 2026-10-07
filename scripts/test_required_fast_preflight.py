"""R2.3: required_fast.sh neutralizes every git location variable before its
first check and asserts the environment is clean.

The script runs under `sh`, which has no errexit, so the assert-clean call
must carry its own `|| exit 2`. The tests below read the script as text and
fail if the unset loop follows the first run_check, if the unset loop names
fewer than the seventeen variables, or if the assert-clean line is missing
its abort.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "required_fast.sh")

GIT_LOCATION_VARS = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_PREFIX",
    "GIT_NAMESPACE", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
    "GIT_IMPLICIT_WORK_TREE", "GIT_GRAFT_FILE", "GIT_SHALLOW_FILE",
    "GIT_INTERNAL_SUPER_PREFIX", "GIT_REPLACE_REF_BASE", "GIT_NO_REPLACE_OBJECTS")

def read_text(path):
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise ValueError("path could not be read: %s" % exc)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("path is not valid utf-8: %s" % exc)

def _checked(text):
    if not isinstance(text, str) or not text:
        raise ValueError("text must be a non-empty string")
    return text

def first_run_check_index(text):
    text = _checked(text)
    idx = text.find('run_check "version-truth"')
    if idx < 0:
        raise ValueError("no first run_check call found")
    return idx

def unset_loop_index(text):
    text = _checked(text)
    needle = "unset GIT_DIR"
    idx = text.find(needle)
    if idx < 0:
        raise ValueError("no unset of git location variables found")
    line_start = text.rfind("\n", 0, idx) + 1
    line_end = text.find("\n", idx)
    line = text[line_start:] if line_end < 0 else text[line_start:line_end]
    if not line.strip().startswith("unset "):
        raise ValueError("the git location unset is commented out or not an unset command")
    missing = [name for name in GIT_LOCATION_VARS if name not in line]
    if missing:
        raise ValueError("unset line is missing %s" % missing)
    return idx

def assert_clean_index(text):
    text = _checked(text)
    needle = "git_location_guard.py --assert-clean --where required_fast || exit 2"
    idx = text.find(needle)
    if idx < 0:
        raise ValueError("no assert-clean line with abort found")
    return idx

class TheFastGateNeutralizesBeforeFirstCheck(unittest.TestCase):
    def test_required_fast_unsets_before_first_check(self):
        text = read_text(GATE)
        unset_idx = unset_loop_index(text)
        first_idx = first_run_check_index(text)
        self.assertLess(unset_idx, first_idx,
                        "the unset loop must run before the first run_check")

    def test_required_fast_asserts_clean(self):
        text = read_text(GATE)
        unset_idx = unset_loop_index(text)
        assert_idx = assert_clean_index(text)
        first_idx = first_run_check_index(text)
        self.assertGreater(assert_idx, unset_idx,
                           "assert-clean must come after the unset loop")
        self.assertLess(assert_idx, first_idx,
                        "assert-clean must come before the first run_check")

    def test_required_fast_keeps_cd_before_unset(self):
        text = read_text(GATE)
        cd_idx = text.find('cd "$(dirname "$0")/.." || exit 1')
        self.assertGreaterEqual(cd_idx, 0, "the cd line must exist")
        unset_idx = unset_loop_index(text)
        self.assertLess(cd_idx, unset_idx, "the cd must come before the unset loop")

    def test_unset_line_names_all_seventeen_variables(self):
        text = read_text(GATE)
        unset_idx = unset_loop_index(text)
        line_end = text.find("\n", unset_idx)
        line = text[unset_idx:] if line_end < 0 else text[unset_idx:line_end]
        for name in GIT_LOCATION_VARS:
            self.assertIn(name, line)

class HostileInputIsRefused(unittest.TestCase):
    def test_helpers_refuse_hostile_input(self):
        for bad in (None, 123, 3.5, True, b"x", [], {}, ""):
            self.assertRaises(ValueError, read_text, bad)
            self.assertRaises(ValueError, first_run_check_index, bad)
            self.assertRaises(ValueError, unset_loop_index, bad)
            self.assertRaises(ValueError, assert_clean_index, bad)

    def test_read_text_refuses_missing_file(self):
        self.assertRaises(ValueError, read_text, os.path.join(HERE, "no-such-file-r23"))

if __name__ == "__main__":
    unittest.main()
