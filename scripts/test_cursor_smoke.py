#!/usr/bin/env python3
"""test_cursor_smoke: drives scripts/cursor_smoke.py without a real
cursor-agent.

The real smoke needs a logged-in Cursor CLI. These tests stub cursor-agent
with small Python scripts written into a temp directory and passed through
--cursor-agent. They always pass --witness-root pointing at a temp directory
and never touch the real ~/.cursor.

Covered:
  1. An absent binary gives NO-DATA exit 2.
  2. A status that says logged in under the throwaway HOME gives FAIL.
  3. A print run that exits 0 with no auth refusal gives FAIL.
  4. The happy default path gives PASS exit 0.
  5. The witness helper returns NO-DATA for a missing directory and a
     different hash after a file changes.
  6. --signed-in with Not logged in gives NO-DATA exit 2.
  7. A default-mode run without --keep removes its cursor-smoke- work
     directory from TMPDIR.
  8. A print failure saying only "author" does not satisfy the auth
     boundary.
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cursor_smoke  # noqa: E402


def write_stub(dirpath, body):
    path = os.path.join(dirpath, "cursor-agent-stub")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("#!/usr/bin/env python3\n")
        fh.write(body)
    os.chmod(path, 0o755)
    return path


STUB_HAPPY = '''
import sys
args = sys.argv[1:]
if args and args[0] == "status":
    print("Not logged in")
    sys.exit(0)
if "-p" in args or "--print" in args:
    sys.stderr.write("please login: authentication required\\n")
    sys.exit(1)
sys.exit(0)
'''

STUB_STATUS_LOGGED_IN = '''
import sys
args = sys.argv[1:]
if args and args[0] == "status":
    print("Logged in as founder")
    sys.exit(0)
if "-p" in args or "--print" in args:
    sys.stderr.write("please login: authentication required\\n")
    sys.exit(1)
sys.exit(0)
'''

STUB_PRINT_OK = '''
import sys
args = sys.argv[1:]
if args and args[0] == "status":
    print("Not logged in")
    sys.exit(0)
if "-p" in args or "--print" in args:
    print("ok")
    sys.exit(0)
sys.exit(0)
'''

STUB_PRINT_AUTHOR = '''
import sys
args = sys.argv[1:]
if args and args[0] == "status":
    print("Not logged in")
    sys.exit(0)
if "-p" in args or "--print" in args:
    sys.stderr.write("author\\n")
    sys.exit(1)
sys.exit(0)
'''

STUB_SIGNED_OUT = '''
import sys
args = sys.argv[1:]
if args and args[0] == "status":
    print("Not logged in")
    sys.exit(0)
sys.exit(0)
'''


class TheNoDataGuard(unittest.TestCase):
    def test_an_absent_binary_is_no_data(self):
        with tempfile.TemporaryDirectory() as witness:
            self.assertEqual(
                cursor_smoke.main(["--cursor-agent", "/no/such/cursor-agent",
                                  "--witness-root", witness]), 2)

    def test_signed_in_not_logged_in_is_no_data(self):
        with tempfile.TemporaryDirectory() as d:
            stub = write_stub(d, STUB_SIGNED_OUT)
            witness = os.path.join(d, "witness")
            os.makedirs(witness)
            self.assertEqual(
                cursor_smoke.main(["--cursor-agent", stub, "--signed-in",
                                  "--witness-root", witness]), 2)


class TheDefaultMode(unittest.TestCase):
    def _run(self, stub_body, expect):
        with tempfile.TemporaryDirectory() as d:
            stub = write_stub(d, stub_body)
            witness = os.path.join(d, "witness")
            os.makedirs(witness)
            code = cursor_smoke.main(["--cursor-agent", stub,
                                     "--witness-root", witness])
            self.assertEqual(code, expect)

    def test_happy_path_gives_pass(self):
        self._run(STUB_HAPPY, 0)

    def test_status_logged_in_fails_isolation(self):
        self._run(STUB_STATUS_LOGGED_IN, 1)

    def test_print_exit_zero_fails_auth_boundary(self):
        self._run(STUB_PRINT_OK, 1)

    def test_print_output_only_author_fails_auth_boundary(self):
        self._run(STUB_PRINT_AUTHOR, 1)

    def test_work_directory_is_removed_without_keep(self):
        with tempfile.TemporaryDirectory() as d:
            stub = write_stub(d, STUB_HAPPY)
            witness = os.path.join(d, "witness")
            os.makedirs(witness)
            with tempfile.TemporaryDirectory() as tmp:
                old_tmpdir = os.environ.get("TMPDIR")
                old_cache = tempfile.tempdir
                os.environ["TMPDIR"] = tmp
                tempfile.tempdir = None
                try:
                    code = cursor_smoke.main(["--cursor-agent", stub,
                                              "--witness-root", witness])
                finally:
                    if old_tmpdir is None:
                        os.environ.pop("TMPDIR", None)
                    else:
                        os.environ["TMPDIR"] = old_tmpdir
                    tempfile.tempdir = old_cache
                self.assertEqual(code, 0)
                leftovers = [n for n in os.listdir(tmp)
                             if n.startswith("cursor-smoke-")]
                self.assertEqual([], leftovers)


class TheLiveDenyCanary(unittest.TestCase):
    """WBS-70.04: deny_verdict() is the pure decision at the heart of
    forbidden_action_probe, driven directly here so the contract is
    pinned without paying for a real cursor-agent turn. The live path
    itself was run three times by hand in the session that added this
    feature: a real bm_store claim, a real cursor-agent -p turn against
    it, and a genuine measured PASS each time. The third of those three
    real transcripts named the claimed session and refusal language but
    never repeated the exact lifecycle uuid in its own final summary
    (the model's prose, not the tool output it read), which is why
    deny_verdict gates on session-plus-language rather than the uuid
    alone; test_a_real_transcript_with_no_lifecycle_mention_still_passes
    below is that exact transcript, verbatim."""

    LIFECYCLE = "4d0c42e7ee1643f2807b671ee612ebf5"

    def _toy_file(self, tmp, content):
        path = os.path.join(tmp, "locked.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return path

    def test_a_transcript_naming_this_runs_own_claim_and_an_unchanged_file_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._toy_file(tmp, cursor_smoke.LOCKED_CONTENT)
            transcript = ("NOT DONE. denied by lifecycle %s owned by %s"
                          % (self.LIFECYCLE, cursor_smoke.DENY_SESSION))
            verdict, message = cursor_smoke.deny_verdict(
                transcript, self.LIFECYCLE, path, cursor_smoke.LOCKED_CONTENT)
        self.assertEqual(verdict, "PASS", message)

    def test_a_transcript_never_naming_the_claim_is_no_data(self):
        """The turn may never have attempted the write at all: never a
        false PASS on the strength of an unchanged file alone, since an
        unattempted write also leaves the file unchanged."""
        with tempfile.TemporaryDirectory() as tmp:
            path = self._toy_file(tmp, cursor_smoke.LOCKED_CONTENT)
            verdict, message = cursor_smoke.deny_verdict(
                "the agent did something else entirely", self.LIFECYCLE,
                path, cursor_smoke.LOCKED_CONTENT)
        self.assertEqual(verdict, "NO-DATA", message)

    def test_the_claim_named_but_the_file_changed_anyway_fails(self):
        """Cursor did not honour the deny: the fence's own identity was
        named in the transcript, yet the file moved."""
        with tempfile.TemporaryDirectory() as tmp:
            path = self._toy_file(tmp, "edited by the agent\n")
            transcript = ("denied by lifecycle %s owned by %s"
                          % (self.LIFECYCLE, cursor_smoke.DENY_SESSION))
            verdict, message = cursor_smoke.deny_verdict(
                transcript, self.LIFECYCLE, path, cursor_smoke.LOCKED_CONTENT)
        self.assertEqual(verdict, "FAIL", message)

    def test_a_real_transcript_with_no_lifecycle_mention_still_passes(self):
        """Verbatim (minus markdown emphasis) from the third of three real
        cursor-agent -p turns run by hand for this feature, 2026-09-14:
        it named the claimed session and refused the write, but its own
        final summary never repeated the lifecycle uuid the tool output
        carried."""
        transcript = (
            "The line was not appended. Brother refused the write, and "
            "locked.txt is unchanged. locked.txt is already inside an "
            "active Brother fence: record locked-by-a-different-session, "
            "owned by session a-different-session-owns-this. This session "
            "is bm1-938858e70920092ebe7f2ee6, so it is not the writer for "
            "that path. The plugin fence blocked the edit before it "
            "landed.")
        with tempfile.TemporaryDirectory() as tmp:
            path = self._toy_file(tmp, cursor_smoke.LOCKED_CONTENT)
            verdict, message = cursor_smoke.deny_verdict(
                transcript, "84db757b775d42ad95390419ebc3712c", path,
                cursor_smoke.LOCKED_CONTENT)
        self.assertEqual(verdict, "PASS", message)
        self.assertNotIn("84db757b775d42ad95390419ebc3712c", transcript)

    def test_refusal_language_with_no_session_named_is_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._toy_file(tmp, cursor_smoke.LOCKED_CONTENT)
            verdict, message = cursor_smoke.deny_verdict(
                "the write was denied and refused", self.LIFECYCLE, path,
                cursor_smoke.LOCKED_CONTENT)
        self.assertEqual(verdict, "NO-DATA", message)

    def test_the_session_named_with_no_refusal_language_is_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._toy_file(tmp, cursor_smoke.LOCKED_CONTENT)
            verdict, message = cursor_smoke.deny_verdict(
                "owned by %s, nothing else" % cursor_smoke.DENY_SESSION,
                self.LIFECYCLE, path, cursor_smoke.LOCKED_CONTENT)
        self.assertEqual(verdict, "NO-DATA", message)

    def test_the_lifecycle_regex_reads_bm_stores_own_claim_line(self):
        """Never re-typed: this is bm_store.py's own success line
        (cmd_claim), read verbatim so a wording change there breaks this
        test rather than silently no longer matching."""
        sample = ("claimed 'locked-by-a-different-session' as lifecycle "
                  "4d0c42e7ee1643f2807b671ee612ebf5 (version 1, session "
                  "a-different-session-owns-this)")
        m = cursor_smoke._LIFECYCLE_RE.search(sample)
        self.assertIsNotNone(m, sample)
        self.assertEqual(m.group(1), self.LIFECYCLE)


class TheWitness(unittest.TestCase):
    def test_missing_directory_is_no_data(self):
        got, why = cursor_smoke.founder_witness("/no/such/cursor/root")
        self.assertIsNone(got)
        self.assertIn("no", why)

    def test_hash_changes_after_a_file_changes(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "plugins"))
            path = os.path.join(root, "plugins", "a.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("a")
            before, _ = cursor_smoke.founder_witness(root)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("b")
            after, _ = cursor_smoke.founder_witness(root)
            self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main(verbosity=2)
