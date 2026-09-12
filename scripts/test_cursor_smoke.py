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
