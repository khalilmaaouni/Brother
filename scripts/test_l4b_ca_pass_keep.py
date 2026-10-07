#!/usr/bin/env python3
"""L4b.2: the PASS-path keep for scripts/check_all.sh.

run_check already kept a FAIL check's full output and named the handle on its
summary line. This sub unit extends the same mechanism to the PASS path: every
passing check's full stdout plus stderr lands in
${TMPDIR:-/tmp}/check-all-pass-NAME-PID.txt and the summary line names that
handle, so a session or a subagent can read a passing check's output from a
handle instead of re-running it.

Three requirements are defended here:

  RQ-CA-PASS-KEEP    the PASS branch writes the keep and names the handle
  RQ-KEEP-NAMING     the key is check-all-pass-NAME-PID, so two concurrent
                     batteries never share one file
  RQ-SUMMARY-STABLE  verdict, counters, printf shape, the OK line preference
                     and the skip screen are untouched; only a handle suffix
                     is added

run_check is shell, so PassBranchSourceTest reads the real scripts/check_all.sh
as bytes and pins the PASS sub-branch of its `0)` case, its ordering against
the skip screen and the PASS verdict, and every part the change must not
touch. CaPassKeepReadTest drives check_ca_pass_keep(), which refuses anything
that is not a whole, readable, non-empty, UTF-8 keep: a missing, empty,
unreadable or corrupt keep is never a pass and never a crash.

Red without the change: the assertions below read text the L4b.2 edit
introduces, and the key test indexes a key that does not exist without it.
"""

import os
import tempfile
import unittest


HERE = os.path.dirname(os.path.abspath(__file__))
CHECK_ALL = os.path.join(HERE, "check_all.sh")

PASS_VERDICT = 'pass=$((pass+1));   verdict="PASS   "'
PASS_KEY = "check-all-pass-$name-$$.txt"
FAIL_KEY = "check-all-fail-$name-$$.txt"
PASS_WRITE = '"$out" 2>/dev/null > "$keep"'
HANDLE = '[full: $keep]'
HANDLE_NO_DATA = '[full: NO-DATA: $keep]'
SYMLINK_GUARD = '[ ! -L "$keep" ]'
SUMMARY_PRINTF = "printf '%-7s exit %-3s %-34s %s\\n' \"$verdict\" \"$code\" \"$name\" \"$last\""
OK_LINE_GREP = "grep -E '^OK($| \\()'"
SKIP_GREP = "grep -E '\\.\\.\\. skipped '"
NO_DATA_GREP = "grep -q 'NO-DATA'"
FAIL_NAME_GREP = "grep -E '^ *(FAIL|ERROR):"


def _read_source():
    """The real check_all.sh, read as bytes, decoded once."""
    with open(CHECK_ALL, "rb") as handle:
        return handle.read().decode("utf-8")


def _pass_region(text):
    """The PASS sub-branch of run_check's `0)` case, verdict to `fi ;;`."""
    start = text.index(PASS_VERDICT)
    end = text.index("fi ;;", start)
    return text[start:end]


def _drop_tree(root):
    """Remove a tree this test built. shutil is off limits in this lane."""
    if not os.path.lexists(root):
        return
    if os.path.islink(root) or not os.path.isdir(root):
        os.remove(root)
        return
    for entry in os.listdir(root):
        _drop_tree(os.path.join(root, entry))
    os.rmdir(root)


def check_ca_pass_keep(path):
    """True only for a whole, readable, non-empty, UTF-8 keep.

    A keep that is missing, empty, a directory or unreadable returns False: it
    blocks, and it is never read as a pass. A path that is not a string, or
    content that is not UTF-8 at all, raises ValueError: a refusal is the
    feature, a raw interpreter exception is the bug. The keep is never read
    for a verdict; run_check is asserted below to hold that line.
    """
    if isinstance(path, bool) or not isinstance(path, str):
        raise ValueError("keep path must be a string, got %r" % (type(path).__name__,))
    if not path.strip():
        raise ValueError("keep path is empty")
    if os.path.isdir(path):
        return False
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return False
    if not data.strip():
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("keep is not valid UTF-8: %s" % (exc,))
    return True


class PassBranchSourceTest(unittest.TestCase):
    """RW-CA-PASS-KEEP: the PASS branch keeps what it captured and names it."""

    @classmethod
    def setUpClass(cls):
        cls.text = _read_source()
        cls.pass_region = _pass_region(cls.text)

    def test_pass_branch_writes_the_full_captured_output_to_a_keep(self):
        self.assertIn(PASS_WRITE, self.pass_region)
        self.assertNotIn('>> "$keep"', self.pass_region)

    def test_pass_keep_key_is_the_check_all_pass_key(self):
        self.assertIn(PASS_KEY, self.pass_region)

    def test_pass_keep_key_carries_the_process_id(self):
        self.assertIn(PASS_KEY, self.pass_region)
        self.assertNotIn("check-all-pass-$name.txt", self.pass_region)

    def test_pass_keep_key_is_distinct_from_the_fail_keep_key(self):
        self.assertIn(PASS_KEY, self.pass_region)
        self.assertNotIn("check-all-fail-", self.pass_region)
        self.assertIn(FAIL_KEY, self.text)

    def test_keep_write_happens_after_the_skip_screen_and_the_verdict(self):
        screen = self.text.index(NO_DATA_GREP)
        verdict = self.text.index(PASS_VERDICT)
        write = verdict + self.pass_region.index(PASS_WRITE)
        self.assertLess(screen, verdict)
        self.assertLess(verdict, write)

    def test_pass_branch_names_the_handle_on_the_summary_line(self):
        self.assertIn(HANDLE, self.pass_region)
        self.assertIn('last="$last  %s"' % HANDLE, self.pass_region)

    def test_unwritable_keep_reports_no_data_for_the_keep_only(self):
        self.assertIn(HANDLE_NO_DATA, self.pass_region)
        tail = self.pass_region[self.pass_region.index(HANDLE):]
        self.assertNotIn("nodata=$((nodata+1))", tail)
        self.assertNotIn("fail=$((fail+1))", tail)
        self.assertNotIn("verdict=", tail)

    def test_keep_write_refuses_to_follow_a_symlink_at_the_keep_path(self):
        self.assertIn(SYMLINK_GUARD, self.pass_region)

    def test_run_check_never_reads_the_keep_for_a_verdict(self):
        for reader in ('< "$keep"', 'cat "$keep"'):
            self.assertNotIn(reader, self.pass_region)

    def test_ok_line_skip_screen_and_fail_branch_are_untouched(self):
        self.assertIn(OK_LINE_GREP, self.text)
        self.assertIn(SKIP_GREP, self.text)
        self.assertIn(NO_DATA_GREP, self.text)
        self.assertIn(FAIL_NAME_GREP, self.text)
        self.assertIn(SUMMARY_PRINTF, self.text)
        self.assertIn(PASS_VERDICT, self.text)


class CaPassKeepReadTest(unittest.TestCase):
    """RW-REFUSE-BAD-KEEP: a keep is evidence only if it reads back whole."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="l4b2-ca-pass-keep-")
        self.addCleanup(_drop_tree, self.tmp)

    def _write_keep(self, name, payload):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as handle:
            handle.write(payload)
        return path

    def test_a_whole_readable_keep_is_accepted(self):
        path = self._write_keep("check-all-pass-surface-4242.txt", b"OK\n")
        self.assertTrue(check_ca_pass_keep(path))

    def test_empty_output_lands_a_file_and_that_file_is_not_a_pass(self):
        path = self._write_keep("check-all-pass-quiet-4242.txt", b"")
        self.assertTrue(os.path.isfile(path))
        self.assertFalse(check_ca_pass_keep(path))

    def test_missing_keep_blocks_and_is_never_a_pass(self):
        self.assertFalse(check_ca_pass_keep(os.path.join(self.tmp, "absent.txt")))

    def test_a_directory_where_a_keep_belongs_blocks(self):
        self.assertFalse(check_ca_pass_keep(self.tmp))

    def test_corrupt_bytes_raise_value_error_and_never_crash(self):
        path = self._write_keep("check-all-pass-corrupt-4242.txt", b"\xff\xfe\x00\x80")
        with self.assertRaises(ValueError):
            check_ca_pass_keep(path)

    def test_hostile_paths_are_refused_never_accepted(self):
        for hostile in (None, 7, 0.0, float("nan"), True, False, b"/tmp/x",
                        [], {}, (), "", "   "):
            with self.assertRaises(ValueError, msg=repr(hostile)):
                check_ca_pass_keep(hostile)

    def test_the_pass_key_the_shell_writes_is_one_this_reader_accepts(self):
        text = _read_source()
        start = text.index(PASS_KEY)
        self.assertEqual(text[start:start + len(PASS_KEY)], PASS_KEY)
        path = self._write_keep("check-all-pass-surface-4242.txt", b"OK (skipped=1)\n")
        self.assertTrue(check_ca_pass_keep(path))

    def test_two_concurrent_lanes_get_distinct_keys_from_the_shell_template(self):
        text = _read_source()
        start = text.index(PASS_KEY)
        template = text[start:start + len(PASS_KEY)]
        first = template.replace("$name", "surface").replace("$$", "1111")
        second = template.replace("$name", "surface").replace("$$", "2222")
        self.assertEqual(first, "check-all-pass-surface-1111.txt")
        self.assertEqual(second, "check-all-pass-surface-2222.txt")
        self.assertNotEqual(first, second)

    def test_a_stale_keep_is_readable_and_still_never_a_verdict(self):
        path = self._write_keep("check-all-pass-stale-4242.txt", b"OK\n")
        self.assertTrue(check_ca_pass_keep(path))
        self.assertNotIn('cat "$keep"', _pass_region(_read_source()))


if __name__ == "__main__":
    unittest.main()
