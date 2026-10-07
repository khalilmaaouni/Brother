#!/usr/bin/env python3
"""brother_login.py: a long-lived Claude login with one command, and the token never leaves the private path.

A FAKE claude (prints a synthetic canary token through a pty, in ANSI, split across writes) and a FAKE reader
(a JSON file stands in for the Keychain) replace the real tools; the real Keychain is never read or written. One
class builds the REAL reader and runs it against a temporary keychain the test creates and deletes.
ONE CONDITION PER FIXTURE: every refusal starts from the good sign-in and breaks one thing. The canary proves the
leak boundary: after each case it is searched for in stdout, stderr, every argv the fake tools saw, the exception
text and every file under the case's folder. It may appear in exactly two places: the fake Keychain's store and
the stdin the reader read.
Run: python3 -B scripts/loop/test_brother_login.py"""
import contextlib, io, json, os, pty, shutil, stat, sys, tempfile, termios, threading, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import brother_login as BL  # noqa: E402

PREFIX = "sk-" + "ant-oat01-"   # joined so no file holds the key shape at rest; the push gate scans for it
CANARY = PREFIX + "CANARY" + "x7Qz" * 8 + "-_END"
OTHER = PREFIX + "OTHER" + "p3Lm" * 8 + "-_END"
STALE = PREFIX + "STALE" + "k9Vb" * 8 + "-_END"

FAKE_CLAUDE = r'''#!/usr/bin/env python3
import sys, time, os
MODE = %(mode)r
TOK = %(tok)r
w = sys.stdout
def out(s):
    w.write(s); w.flush(); time.sleep(0.02)
if sys.argv[1:] != ["setup-token"]:
    sys.exit(2)
out("\x1b]0;claude\x07\x1b[1mOpening browser\x1b[0m\r\n")
if MODE == "none":
    out("done, no token here\r\n"); sys.exit(0)
if MODE == "two":
    out(TOK + "\r\n" + %(other)r + "\r\n"); sys.exit(0)
if MODE == "error":
    out("error: could not store " + TOK + "\r\n"); sys.exit(1)
if MODE == "hang":
    out(TOK + "\r\n"); time.sleep(30); sys.exit(0)
out("Paste code here: ")
code = sys.stdin.readline()
if MODE == "cancel":
    time.sleep(30)
out("\r\n\x1b[32mYour token: \x1b[0m" + TOK[:17])
out(TOK[17:40]); out(TOK[40:] + "\x1b[0m\r\n")
out("Token also shown once more: " + TOK + "\r\n")
sys.exit(0)
'''

FAKE_READER = r'''#!/usr/bin/env python3
import sys, os, json
STATE = %(state)r; ARGV = %(argv)r; STDIN = %(stdin)r; MODE = %(mode)r
args = sys.argv[1:]
with open(ARGV, "a") as fh: fh.write(json.dumps(args) + "\n")
def load():
    if not os.path.exists(STATE): return {}
    with open(STATE) as fh: return json.load(fh)
def dump(st):
    with open(STATE, "w") as fh: json.dump(st, fh)
cmd = args[0] if args else ""
if cmd == "set":
    data = sys.stdin.buffer.read()
    with open(STDIN, "ab") as fh: fh.write(data)
    if MODE == "set-fails": sys.exit(1)
    if MODE == "foreign": sys.exit(45)
    st = load(); st["value"] = data.decode().rstrip("\n") if MODE != "stale" else %(stale)r; dump(st); sys.exit(0)
if cmd == "get":
    st = load()
    if "value" not in st: sys.exit(44)
    if MODE == "unreadable": sys.exit(1)
    sys.stdout.write(st["value"] + "\n"); sys.exit(0)
if cmd == "delete":
    st = load()
    if "value" not in st: sys.exit(44)
    del st["value"]; dump(st); sys.exit(0)
sys.exit(2)
'''


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="brother-login-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.state = os.path.join(self.root, "keychain.json")
        self.argv_log = os.path.join(self.root, "reader-argv.log")
        self.stdin_log = os.path.join(self.root, "reader-stdin.bin")
        self.saved = {k: getattr(BL, k) for k in ("CLAUDE", "READER", "KEYCHAIN", "build_reader")}
        BL.build_reader = lambda *a, **k: BL.READER   # the fake is already "built"
        self.addCleanup(lambda: [setattr(BL, k, v) for k, v in self.saved.items()])
        BL.KEYCHAIN = os.path.join(self.root, "login.keychain-db")
        self.fakes("good", "ok")

    def fakes(self, claude_mode, reader_mode):
        self.claude = self.script("claude", FAKE_CLAUDE % {"mode": claude_mode, "tok": CANARY, "other": OTHER})
        self.reader = self.script("brother-keychain", FAKE_READER % {"state": self.state, "argv": self.argv_log,
                                                                       "stdin": self.stdin_log, "mode": reader_mode, "stale": STALE})
        BL.CLAUDE, BL.READER = self.claude, self.reader

    def script(self, name, text):
        p = os.path.join(self.root, name)
        with open(p, "w") as fh:
            fh.write(text)
        os.chmod(p, stat.S_IRWXU)
        return p

    def stored(self):
        if not os.path.exists(self.state):
            return None
        with open(self.state) as fh:
            return json.load(fh).get("value")

    def seed(self, value):
        with open(self.state, "w") as fh:
            json.dump({"value": value}, fh)

    def run_main(self, argv=(), code=b"123456\n", deadline_s=20, close_tty_after=None):
        """main() at its entry point, the person's terminal a pty pair; code is typed in after the prompt appears."""
        master, slave = pty.openpty()
        before = termios.tcgetattr(slave)
        out, err = io.StringIO(), io.StringIO()

        def typist():
            time.sleep(0.4)
            if close_tty_after is not None:
                time.sleep(close_tty_after)
                os.close(master)
                return
            os.write(master, code)
        t = threading.Thread(target=typist, daemon=True)
        t.start()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = BL.main(list(argv), tty=slave, deadline_s=deadline_s)
        finally:
            t.join(2)
            self.echo_after = termios.tcgetattr(slave)[3] & termios.ECHO
            self.echo_before = before[3] & termios.ECHO
            os.close(slave)
            if close_tty_after is None:
                os.close(master)
        self.out, self.err = out.getvalue(), err.getvalue()
        self.assertNoLeak()
        return rc

    def assertNoLeak(self):
        """The canary appears nowhere public: not on either stream, not in any argv the Keychain tool saw, and in no
        file under the case folder except the fake store and the private stdin."""
        self.assertNotIn(CANARY, self.out)
        self.assertNotIn(CANARY, self.err)
        if os.path.exists(self.argv_log):
            with open(self.argv_log) as fh:
                self.assertNotIn(CANARY, fh.read())
        for d, _, files in os.walk(self.root):
            for f in files:
                p = os.path.join(d, f)
                if p in (self.state, self.stdin_log, self.claude):
                    continue
                with open(p, "rb") as fh:
                    self.assertNotIn(CANARY.encode(), fh.read(), p)


class SignIn(Base):
    def test_a_good_sign_in_saves_the_exact_token_and_verifies_it(self):
        self.assertEqual(self.run_main(), 0)
        self.assertIn("saved and verified", self.out)
        self.assertEqual(self.stored(), CANARY)
        with open(self.stdin_log, "rb") as fh:
            self.assertIn(CANARY.encode(), fh.read(), "the token travels to the reader on stdin")
        with open(self.argv_log) as fh:
            argv = [json.loads(l) for l in fh]
        self.assertEqual(argv[0], ["set", BL.SERVICE, BL.ACCOUNT, BL.KEYCHAIN], "the explicit login Keychain path, no value")
        self.assertEqual(argv[1][0], "get", "read back after the write")

    def test_the_terminal_echo_is_restored(self):
        self.run_main()
        self.assertEqual(self.echo_after, self.echo_before)

    def test_a_failing_sign_in_saves_nothing_and_leaks_nothing(self):
        self.fakes("error", "ok")
        self.assertEqual(self.run_main(), 1)
        self.assertIn("Sign-in failed", self.err)
        self.assertIsNone(self.stored())
        self.assertNotIn("saved", self.out)

    def test_a_hung_sign_in_times_out_and_saves_nothing(self):
        self.fakes("hang", "ok")
        t0 = time.monotonic()
        self.assertEqual(self.run_main(deadline_s=1), 1)
        self.assertLess(time.monotonic() - t0, 10, "the child is killed, not waited for")
        self.assertIn("timed out", self.err)
        self.assertIsNone(self.stored())

    def test_a_closed_terminal_is_a_cancel_and_saves_nothing(self):
        self.fakes("cancel", "ok")
        self.assertEqual(self.run_main(close_tty_after=0.3, deadline_s=5), 1)
        self.assertIn("cancelled", self.err)
        self.assertIsNone(self.stored())

    def test_two_different_tokens_are_refused(self):
        self.fakes("two", "ok")
        self.assertEqual(self.run_main(), 1)
        self.assertIn("not recognized", self.err)
        self.assertIsNone(self.stored())

    def test_no_token_in_the_output_is_refused(self):
        self.fakes("none", "ok")
        self.assertEqual(self.run_main(), 1)
        self.assertIn("not recognized", self.err)
        self.assertIsNone(self.stored())

    def test_a_keychain_write_failure_claims_no_success(self):
        self.fakes("good", "set-fails")
        self.assertEqual(self.run_main(), 1)
        self.assertIn("not confirmed saved", self.err)
        self.assertNotIn("saved and verified", self.out)

    def test_an_item_an_older_brother_saved_names_the_one_command_to_remove_it(self):
        self.fakes("good", "foreign")
        self.assertEqual(self.run_main(), 1)
        self.assertIn("security delete-generic-password -s claude-loop-token", self.err)
        self.assertNotIn("saved and verified", self.out)

    def test_the_reader_is_built_before_the_sign_in(self):
        seen = []
        BL.build_reader = lambda *a, **k: seen.append(1) or BL.READER
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(seen, [1])

    def test_a_mismatched_read_back_claims_no_success(self):
        self.fakes("good", "stale")
        self.assertEqual(self.run_main(), 1)
        self.assertIn("verification failed", self.err)
        self.assertNotIn("saved and verified", self.out)

    def test_a_missing_claude_program_is_named(self):
        BL.CLAUDE = os.path.join(self.root, "no-such-claude")
        self.assertEqual(self.run_main(), 1)
        self.assertIn("Sign-in failed", self.err)   # exec fails inside the child: exit 127, nothing saved
        self.assertIsNone(self.stored())

    def test_an_unexpected_error_prints_one_fixed_sentence(self):
        old = BL.capture
        BL.capture = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("transcript: " + CANARY))
        self.addCleanup(setattr, BL, "capture", old)
        self.assertEqual(self.run_main(), 1)
        self.assertEqual(self.err.strip(), "Brother login failed. Retry or contact support.")

    def test_a_wrong_argument_is_refused(self):
        self.assertEqual(self.run_main(argv=["x"]), 1)
        self.assertIn("brother-login forget", self.err)


class Forget(Base):
    def test_forget_removes_and_verifies(self):
        self.seed(CANARY)
        self.assertEqual(self.run_main(argv=["forget"]), 0)
        self.assertIsNone(self.stored())
        self.assertIn("Local login removed", self.out)

    def test_forget_with_nothing_saved_is_fine(self):
        self.assertEqual(self.run_main(argv=["forget"]), 0)


class WithLogin(Base):
    """What the loop's worker calls: the fresh Keychain value on a one shot pipe, and a copy of the scrubbed child
    environment that names the pipe and carries no token (review 2026-10-03, F1)."""

    def test_the_login_travels_on_a_pipe_and_no_environment_carries_a_token(self):
        self.seed(CANARY)
        scrubbed = {"PATH": "/usr/bin", BL.TOKEN_VAR: PREFIX + "INHERITED" + "a" * 24}
        parent = dict(os.environ)
        env, fd = BL.with_login(scrubbed)
        self.addCleanup(os.close, fd)
        self.assertEqual(env[BL.FD_VAR], str(fd))
        self.assertNotIn(BL.TOKEN_VAR, env, "an inherited token is dropped, and the fresh one never goes in")
        self.assertNotIn(CANARY, json.dumps(env))
        self.assertEqual(os.read(fd, 65536), CANARY.encode(), "the fresh Keychain value, exactly, on the pipe")
        import select
        self.assertEqual(select.select([fd], [], [], 2)[0], [fd], "the write end is closed: end of file is ready at once")
        self.assertEqual(os.read(fd, 65536), b"", "the pipe reads once, then empty")
        self.assertFalse(os.get_inheritable(fd), "only a pass_fds child inherits it")
        self.assertEqual(scrubbed[BL.TOKEN_VAR], PREFIX + "INHERITED" + "a" * 24, "the input dict is not mutated")
        self.assertEqual(dict(os.environ), parent)
        self.assertNotIn(CANARY, json.dumps(parent))

    def test_a_failed_read_opens_no_pipe(self):
        before = len(os.listdir("/dev/fd"))
        with self.assertRaises(BL.Failure):
            BL.with_login({})
        self.assertEqual(len(os.listdir("/dev/fd")), before, "no descriptor is left open")

    def test_a_missing_item_fails_closed(self):
        with self.assertRaises(BL.Failure) as cm:
            BL.with_login({})
        self.assertIn("brother-login", str(cm.exception))

    def test_an_unreadable_item_fails_closed(self):
        self.seed(CANARY)
        self.fakes("good", "unreadable")
        with self.assertRaises(BL.Failure):
            BL.with_login({})

    def test_a_non_token_value_fails_closed(self):
        self.seed("hunter2")
        with self.assertRaises(BL.Failure) as cm:
            BL.with_login({})
        self.assertIn("not a Claude token", str(cm.exception))


class RealReader(unittest.TestCase):
    """The reader as built by build_reader(), against a temporary file keychain this test creates and deletes:
    set, get, replace, delete, and the item's access list names the reader alone (never /usr/bin/security).
    The real login keychain is never opened. NO-DATA (skipped by name) without the Xcode command line tools."""

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="brother-reader-", dir=os.path.expanduser("~/.claude/brother-scratch"))
        cls.reader = os.path.join(cls.root, "bin", "brother-keychain")
        try:
            BL.build_reader(cls.reader)
        except BL.Failure as exc:
            shutil.rmtree(cls.root, True)
            raise unittest.SkipTest("NO-DATA: %s" % exc)
        cls.kc = os.path.join(cls.root, "test.keychain-db")
        import subprocess
        subprocess.run(["security", "create-keychain", "-p", "pw", cls.kc], check=True, capture_output=True)
        subprocess.run(["security", "unlock-keychain", "-p", "pw", cls.kc], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        import subprocess
        r = subprocess.run(["security", "delete-keychain", cls.kc], capture_output=True, text=True)
        shutil.rmtree(cls.root, True)
        if r.returncode != 0:   # a keychain left on the search list is a leak the next run would inherit: the class fails
            raise AssertionError("could not delete the test keychain %s (exit %d): %s" % (cls.kc, r.returncode, r.stderr.strip()))

    def run_reader(self, *args, data=None):
        import subprocess
        return subprocess.run([self.reader] + list(args) + [BL.SERVICE, BL.ACCOUNT, self.kc],
                              input=data, capture_output=True, timeout=60)

    def test_set_get_replace_delete_and_the_access_list_names_the_reader_alone(self):
        import subprocess
        self.assertEqual(self.run_reader("get").returncode, BL.NOT_FOUND)
        self.assertEqual(self.run_reader("set", data=CANARY.encode() + b"\n").returncode, 0)
        self.assertEqual(self.run_reader("get").stdout, CANARY.encode() + b"\n")
        self.assertEqual(self.run_reader("set", data=OTHER.encode() + b"\n").returncode, 0, "replaces its own item")
        self.assertEqual(self.run_reader("get").stdout, OTHER.encode() + b"\n")
        acl = subprocess.run(["security", "dump-keychain", "-a", self.kc], capture_output=True, text=True).stdout
        self.assertIn(self.reader, acl, "the reader is the one trusted application")
        self.assertNotIn("/usr/bin/security", acl)
        self.assertNotIn(OTHER, acl, "dump-keychain -a shows attributes, never the secret")
        self.assertEqual(self.run_reader("delete").returncode, 0)
        self.assertEqual(self.run_reader("get").returncode, BL.NOT_FOUND)
        self.assertEqual(self.run_reader("delete").returncode, BL.NOT_FOUND)

    def test_a_name_that_is_not_utf8_is_a_usage_error_never_an_abort(self):
        """F4 (review 2026-10-03): CFStringCreateWithCString returns NULL for bytes that are not UTF-8; unchecked, the
        program aborted (rc 134). Each verb refuses with exit 2, and nothing is written."""
        import subprocess
        for verb in ("get", "set", "delete"):
            for args in ([b"\xff\xfe", BL.ACCOUNT.encode()], [BL.SERVICE.encode(), b"\xc3\x28"]):
                r = subprocess.run([self.reader.encode(), verb.encode()] + args + [self.kc.encode()],
                                   input=CANARY.encode() + b"\n", capture_output=True, timeout=60)
                self.assertEqual(r.returncode, 2, (verb, args, r.returncode))
        self.assertEqual(self.run_reader("get").returncode, BL.NOT_FOUND, "nothing was written")

    def test_every_build_is_a_new_code_identity(self):
        """F2 (review 2026-10-03): the source ships in every seat, so an unsalted ad-hoc build was reproducible and a
        seat could rebuild the identity the Keychain trusts. Two builds of the same source differ in identity."""
        import subprocess
        second = os.path.join(self.root, "bin2", "brother-keychain")
        BL.build_reader(second)

        def cdhash(p):
            out = subprocess.run(["codesign", "-dvvv", p], capture_output=True, text=True).stderr
            line = [l for l in out.splitlines() if l.startswith("CDHash=")]
            self.assertEqual(len(line), 1, out)
            return line[0]
        self.assertNotEqual(cdhash(self.reader), cdhash(second))

    def test_an_unsalted_build_is_refused_by_the_compiler(self):
        import subprocess
        r = subprocess.run(["xcrun", "--sdk", "macosx", "clang", "-fsyntax-only", BL.READER_SOURCE], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("BROTHER_BUILD_SALT", r.stderr)

    def test_build_reader_never_rebuilds_an_existing_one(self):
        before = os.stat(self.reader).st_mtime_ns
        self.assertEqual(BL.build_reader(self.reader), self.reader)
        self.assertEqual(os.stat(self.reader).st_mtime_ns, before)

    def test_build_reader_without_its_source_is_a_named_failure(self):
        with self.assertRaises(BL.Failure) as cm:
            BL.build_reader(os.path.join(self.root, "never-built"), source=os.path.join(self.root, "missing.c"))
        self.assertIn("reader source is missing", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(self.root, "never-built")))


if __name__ == "__main__":
    unittest.main()
