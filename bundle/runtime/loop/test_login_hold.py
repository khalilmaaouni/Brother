#!/usr/bin/env python3
"""S1: the run's login holder (brother_login.hold / ask_holder), the seat lane that asks it first
(native_worker.login_child), and the Codex login probe (adapters.codex.login_probe).

WHY. In 1.1.5 the login Keychain locked while the owner slept, and every seat started after that failed its fresh
Keychain read ("Keychain did not respond"). The holder reads the saved login ONCE at launch and answers the run, so a
Keychain that stops answering after launch no longer stops the night's seats.

S1.b (security review 2026-10-18, HIGH). S1's holder answered any process of the same uid on a named socket, so a
seat's own shell could read the login. The holder now answers only on an unnamed socket pair whose other end it hands
to the command it starts: an attacker of the same uid that does not hold that descriptor has nothing to connect to.
test_only_the_launched_command_can_ask is the case that was red on S1 (its attacker found the socket and read the
login) and is green now; the seat sandbox also denies a connect to any socket under ~/.claude/bin.

A FAKE reader stands in for the signed Keychain reader; the real Keychain is never read (a child's fallback reader is a
program that fails like a locked Keychain). ONE CONDITION PER FIXTURE: each refusal starts from a good holder and
breaks one thing. The canary token is searched for in every environment, argv and log line a case produces.
Run: python3 -B scripts/loop/test_login_hold.py"""
import contextlib, hashlib, io, json, os, shutil, socket, stat, struct, subprocess, sys, tempfile, threading, time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import brother_login as BL  # noqa: E402
import native_worker as NW  # noqa: E402
from adapters import codex as CX  # noqa: E402
from adapters.codex import CodexAdapter  # noqa: E402


def login_probe(*a):
    return CX.login_probe(*a)

PREFIX = "sk-" + "ant-oat01-"   # joined so no file holds the key shape at rest; the push gate scans for it
CANARY = PREFIX + "HOLDCANARY" + "q4Wn" * 8 + "-_END"
CANARY_SHA = hashlib.sha256(CANARY.encode()).hexdigest()
ENV_KEYS = ("BROTHER_LOOP_TOKEN", "BROTHER_RUN_DIR", "BROTHER_LOOP_HOLD", BL.TOKEN_VAR, BL.HOLD_FD_VAR)

#: The run's command stand in (the launcher), started BY the holder. Its fallback Keychain reader is a program that
#: fails like a locked Keychain, so a seat that did not get the login from the holder fails. It never writes the login
#: anywhere: only its digest. argv: out_json mode [done_file]
CHILD = r'''
import hashlib, json, os, subprocess, sys, time
sys.path.insert(0, %(here)r)
import brother_login as BL
import native_worker as NW
BL.READER = %(locked)r
out, mode = sys.argv[1], sys.argv[2]
res = {"token_var_in_env": BL.TOKEN_VAR in os.environ, "hold_fd_var": os.environ.get(BL.HOLD_FD_VAR, "")}
def seat():
    os.environ["BROTHER_LOOP_TOKEN"] = "on"
    try:
        env, fd = NW.login_child()
    except BL.Failure as exc:
        return "FAILURE " + str(exc)
    try:
        got = os.read(fd, 65536)
    finally:
        os.close(fd)
    return hashlib.sha256(got).hexdigest()
if mode == "seats":
    res["seats"] = [seat() for _ in range(3)]
elif mode == "exit5":
    sys.exit(5)
elif mode == "attack":
    res["seats"] = [seat()]
    # a program this process starts with every descriptor it holds (an exec that keeps them, as pty.fork does)
    p = subprocess.run([sys.executable, "-B", %(attack)r, %(root)r], close_fds=False, capture_output=True, text=True,
                       timeout=60)
    res["grandchild_rc"], res["grandchild_out"] = p.returncode, p.stdout + p.stderr
    with open(out + ".tmp", "w") as fh:
        json.dump(res, fh)
    os.rename(out + ".tmp", out)
    deadline = time.monotonic() + 60
    while not os.path.exists(sys.argv[3]) and time.monotonic() < deadline:
        time.sleep(0.05)
    sys.exit(0)
elif mode == "run_end":
    res["seats"] = [seat()]
    with open(out + ".first", "w") as fh:
        fh.write("1")
    deadline = time.monotonic() + 60
    while os.path.isdir(os.environ["BROTHER_RUN_DIR"]) and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(1.5)   # longer than the holder's poll: it has seen its run folder go
    try:
        res["after"] = BL.ask_holder()
    except BL.Failure as exc:
        res["after"] = "FAILURE " + str(exc)
    res["seats"].append(seat())
    with open(out, "w") as fh:
        json.dump(res, fh)
    sys.exit(7)
with open(out + ".tmp", "w") as fh:
    json.dump(res, fh)
os.rename(out + ".tmp", out)
'''

#: The attacker: a process of the same uid that does NOT hold the holder's descriptor, knowing what a seat's shell
#: knows (the folders, the environment). It connects to every unix socket it can find under the folders it is given and
#: under ~/.claude/bin, reads whatever answers, then asks the holder the documented way. Exit 3 when anything it read
#: is a Claude login, 0 otherwise; it prints what it tried, never what it read. argv: folders...
ATTACK = r'''
import os, socket, stat, sys
sys.path.insert(0, %(here)r)
import brother_login as BL
got, tried = b"", []
roots = sys.argv[1:] + [os.path.expanduser("~/.claude/bin")]
for root in roots:
    for d, dirs, files in os.walk(root):
        for name in dirs + files:
            p = os.path.join(d, name)
            try:
                if not stat.S_ISSOCK(os.lstat(p).st_mode):
                    continue
            except OSError:
                continue   # sbe: allow-silent a path that vanished or cannot be read is not a socket to probe
            for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
                s = socket.socket(socket.AF_UNIX, kind)
                s.settimeout(2)
                try:
                    s.connect(p)
                    if kind == socket.SOCK_DGRAM:
                        s.send(b"login?")
                    while True:
                        c = s.recv(8192)
                        if not c:
                            break
                        got += c
                    tried.append("read " + name)
                except OSError as exc:
                    tried.append("refused %%s %%s" %% (name, type(exc).__name__))
                finally:
                    s.close()
try:
    got += BL.ask_holder().encode()
    tried.append("asked")
except Exception as exc:   # any refusal, whatever the holder's shape
    tried.append("ask refused " + type(exc).__name__)
print(" | ".join(tried))
sys.exit(3 if BL.TOKEN.search(got) else 0)
'''


def short_dir():
    """A scratch folder short enough for a unix socket path (104 bytes on macOS): only the sandbox case binds one."""
    for base in (tempfile.gettempdir(), os.path.expanduser("~/.claude/brother-scratch"), "/tmp"):
        base = os.path.realpath(base)
        if len(base) <= 40:
            try:
                os.makedirs(base, exist_ok=True)
                return os.path.realpath(tempfile.mkdtemp(prefix="lh", dir=base))
            except OSError:
                continue   # sbe: allow-silent try the next candidate base; none left is a named SkipTest below
    raise unittest.SkipTest("NO-DATA: no writable scratch folder short enough for a unix socket")


def sockets_under(root):
    found = []
    for d, dirs, files in os.walk(root):
        for n in dirs + files:
            try:
                if stat.S_ISSOCK(os.lstat(os.path.join(d, n)).st_mode):
                    found.append(os.path.join(d, n))
            except OSError:
                continue   # sbe: allow-silent an entry that vanished mid walk is not a socket left behind
    return found


class FailAfterFirst(object):
    """A reader that answers the canary once, then fails the way a locked Keychain does."""

    def __init__(self, value=None):
        self.calls = 0
        self.value = CANARY.encode() if value is None else value

    def __call__(self):
        self.calls += 1
        if self.calls > 1:
            raise BL.Failure("Keychain did not respond. Unlock it and try again.")
        return self.value


class Base(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="login-hold-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.run_dir = os.path.join(self.root, "run")
        os.makedirs(self.run_dir)
        saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
                                 for k, v in saved_env.items()])
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        for name in ("read_saved", "HOLD_ANSWER_S"):
            self.addCleanup(setattr, BL, name, getattr(BL, name))
        self.locked = os.path.join(self.root, "locked-keychain")
        with open(self.locked, "w") as fh:
            fh.write("#!/bin/sh\nexit 9\n")
        os.chmod(self.locked, 0o700)
        self.attack = os.path.join(self.root, "attack.py")
        with open(self.attack, "w") as fh:
            fh.write(ATTACK % {"here": HERE})
        self.child = os.path.join(self.root, "child.py")
        with open(self.child, "w") as fh:
            fh.write(CHILD % {"here": HERE, "locked": self.locked, "attack": self.attack, "root": self.root})
        self.threads = []
        self.addCleanup(self.stop_holders)

    def stop_holders(self):
        shutil.rmtree(self.run_dir, True)
        for t in self.threads:
            t.join(20)

    def thread(self, fn):
        out = {}

        def go():
            try:
                out["rc"] = fn()
            except BaseException as exc:   # noqa: B902 the case asserts on it
                out["exc"] = exc
        t = threading.Thread(target=go, daemon=True)
        t.start()
        self.threads.append(t)
        out["thread"] = t
        return out

    def hold(self, reader, mode, *extra):
        """hold() on a thread, starting the CHILD in mode; returns (the dict its result lands in, the child's out)."""
        out = os.path.join(self.root, "out-%s.json" % mode)
        cmd = [sys.executable, "-B", self.child, out, mode] + list(extra)
        os.environ["BROTHER_RUN_DIR"] = self.run_dir
        return self.thread(lambda: BL.hold(self.run_dir, cmd, reader=reader)), out

    def wait_for(self, path, t=None, timeout=60):
        deadline = time.monotonic() + timeout
        while not os.path.exists(path) and time.monotonic() < deadline:
            if t is not None and not t.is_alive():
                break
            time.sleep(0.05)
        self.assertTrue(os.path.exists(path), "the child wrote %s" % path)
        with open(path) as fh:
            return fh.read()

    def pair(self, value=None, serve=True):
        """An in process holder end: (holder end, asker end), the asker's descriptor number named in os.environ."""
        mine, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(theirs.close)
        self.addCleanup(mine.close)
        os.environ[BL.HOLD_FD_VAR] = str(theirs.fileno())
        if serve:
            stop = threading.Event()
            self.addCleanup(stop.set)
            self.thread(lambda: BL.serve(mine, CANARY.encode() if value is None else value, lambda: not stop.is_set()))
        return mine, theirs

    def seat(self):
        """One token lane seat's login, the way native_worker hands it: (child_env, what the child reads on the fd)."""
        os.environ["BROTHER_LOOP_TOKEN"] = "on"
        env, fd = NW.login_child()
        try:
            got = os.read(fd, 65536)
        finally:
            os.close(fd)
        return env, got


class HolderServesSeats(Base):
    def test_a_seat_gets_the_login_after_the_reader_fails(self):
        """REQ-S1A-01: the reader answers once (at launch), then fails like a locked Keychain, and the started command's
        own fallback reader is locked too. Every seat of the command still gets the login, from the holder; the
        command's environment names the descriptor and carries no login, an inherited one included."""
        reader = FailAfterFirst()
        os.environ[BL.TOKEN_VAR] = PREFIX + "INHERITED" + "a" * 24
        h, out = self.hold(reader, "seats")
        res = json.loads(self.wait_for(out, h["thread"]))
        h["thread"].join(20)
        self.assertEqual(h.get("rc"), 0, h)
        self.assertEqual(reader.calls, 1, "the holder reads the login once, at launch")
        self.assertEqual(res["seats"], [CANARY_SHA] * 3)
        self.assertFalse(res["token_var_in_env"], "an inherited login never reaches the command")
        self.assertRegex(res["hold_fd_var"], r"^[0-9]+$")

    def test_only_the_launched_command_can_ask(self):
        """THE FINDING (S1.b): while the holder serves the command it started, two processes of the same uid that do
        not hold its descriptor try every unix socket they can find and the documented ask: a sibling (started by
        this test, knowing the command's environment) and a grandchild the command starts with every descriptor it
        has open after its seats ran (the exec path pty.fork takes). Neither gets the login; the command did. Red on
        S1: its attacker found the named socket and read the login."""
        done = os.path.join(self.root, "done")
        h, out = self.hold(FailAfterFirst(), "attack", done)
        res = json.loads(self.wait_for(out, h["thread"]))
        self.assertEqual(res["seats"], [CANARY_SHA], "the started command still gets the login")
        self.assertEqual(res["grandchild_rc"], 0, res["grandchild_out"])
        env = dict(os.environ, **{BL.HOLD_FD_VAR: res["hold_fd_var"]})
        p = subprocess.run([sys.executable, "-B", self.attack, self.root], env=env, capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("ask refused Failure", p.stdout, "the sibling's ask was made and refused")
        self.assertNotIn(CANARY, p.stdout + p.stderr + res["grandchild_out"])
        self.assertEqual(sockets_under(self.root), [], "the holder names no socket on disk")
        with open(done, "w") as fh:
            fh.write("1")
        h["thread"].join(20)
        self.assertEqual(h.get("rc"), 0, h)

    def test_no_holder_is_todays_fresh_read(self):
        """Empty: no holder named, so the seat reads the Keychain fresh, as before S1."""
        calls = []
        BL.read_saved = lambda: calls.append(1) or CANARY.encode()
        self.assertEqual(BL.ask_holder(), "")
        env, got = self.seat()
        self.assertEqual(got, CANARY.encode())
        self.assertEqual(len(calls), 1)

    def test_the_rollback_switch_skips_the_holder(self):
        """BROTHER_LOOP_HOLD=off at launch is the rollback: the seat reads fresh even with a holder up."""
        self.pair()
        self.assertEqual(BL.ask_holder(), CANARY, "the holder is up")
        calls = []
        BL.read_saved = lambda: calls.append(1) or CANARY.encode()
        os.environ["BROTHER_LOOP_HOLD"] = "off"
        self.seat()
        self.assertEqual(len(calls), 1)

    def test_an_ended_holder_refuses_loudly(self):
        """Stale: the holder's end is closed (its run ended or it died). S2 round 1 (review B): this read the Keychain
        fresh with no word, and a Keychain locked by then failed naming the Keychain, never the holder. A named holder
        whose peer is gone now refuses, saying the run's login holder has ended, and the Keychain is never read."""
        mine, _ = self.pair(serve=False)
        mine.close()
        with self.assertRaises(BL.Failure) as ctx:
            BL.ask_holder()
        self.assertIn("The run's login holder has ended", str(ctx.exception))
        calls = []
        BL.read_saved = lambda: calls.append(1) or CANARY.encode()
        with self.assertRaises(BL.Failure) as ctx:
            self.seat()
        self.assertIn("The run's login holder has ended", str(ctx.exception))
        self.assertEqual(calls, [], "no fresh Keychain read behind an ended holder")

    def test_two_seats_asking_at_once_are_both_served(self):
        self.pair()
        got = []
        ts = [threading.Thread(target=lambda: got.append(BL.ask_holder())) for _ in range(4)]
        [t.start() for t in ts]
        [t.join(10) for t in ts]
        self.assertEqual(got, [CANARY] * 4)

    def test_the_holder_ends_with_its_run(self):
        """REQ-S1A-05: removing the run folder ends the answers: the command's next ask finds an ended holder and
        refuses naming it (S2 round 1: never a quiet fresh read), and hold returns the command's exit status."""
        h, out = self.hold(FailAfterFirst(), "run_end")
        self.wait_for(out + ".first", h["thread"])
        shutil.rmtree(self.run_dir)
        res = json.loads(self.wait_for(out, h["thread"]))
        h["thread"].join(20)
        self.assertEqual(res["seats"][0], CANARY_SHA)
        self.assertTrue(res["after"].startswith("FAILURE The run's login holder has ended"), res["after"])
        self.assertTrue(res["seats"][1].startswith("FAILURE The run's login holder has ended"), res["seats"][1])
        self.assertEqual(h.get("rc"), 7, h)

    def test_the_holder_ends_with_its_command(self):
        """The command ends first: hold returns its exit status at once, the run folder still there."""
        h, _ = self.hold(FailAfterFirst(), "exit5")
        h["thread"].join(20)
        self.assertFalse(h["thread"].is_alive())
        self.assertEqual(h.get("rc"), 5, h)
        self.assertTrue(os.path.isdir(self.run_dir))


class TokenNeverInEnvironment(Base):
    def test_the_token_never_rides_an_environment(self):
        """REQ-S1A-02, the claude transport: the seat's command line (native_worker.session_argv) and its child env
        carry the descriptor variable and never the token text nor the holder's descriptor name; the child reads the
        token on the descriptor alone."""
        self.pair()
        os.environ[BL.TOKEN_VAR] = PREFIX + "INHERITED" + "a" * 24   # a parent's token: never the child's
        os.environ["BROTHER_LOOP_TOKEN"] = "on"
        parent = dict(os.environ)
        env, fd = NW.login_child()
        try:
            argv = NW.session_argv("claude-opus-5-5", "low", self.root, "/nonexistent/claude", env=env, token_on=True)
            self.assertIn(BL.FD_VAR, env)
            self.assertEqual(env[BL.FD_VAR], str(fd))
            self.assertNotIn(BL.TOKEN_VAR, env)
            self.assertNotIn(BL.HOLD_FD_VAR, env, "a seat is never told where the holder is")
            self.assertNotIn(CANARY, json.dumps(env))
            self.assertNotIn(CANARY, " ".join(argv))
            self.assertNotIn(BL.FD_VAR, argv, "the descriptor name is not unset by the scrub")
            self.assertEqual(os.read(fd, 65536), CANARY.encode())
        finally:
            os.close(fd)
        self.assertEqual(dict(os.environ), parent, "the parent environment is untouched")
        self.assertNotIn(CANARY, json.dumps(dict(os.environ)))

    def test_with_login_drops_the_holder_name(self):
        env, fd = BL.with_login({BL.HOLD_FD_VAR: "5", "LANG": "C"}, CANARY.encode())
        os.close(fd)
        self.assertNotIn(BL.HOLD_FD_VAR, env)
        self.assertEqual(env["LANG"], "C")


class CodexLogin(Base):
    def adapter(self):
        prog = os.path.join(self.root, "codex")
        with open(prog, "w") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(prog, 0o700)
        return CodexAdapter(program=prog, root=self.root)

    def test_codex_probe_names_the_file(self):
        """REQ-S1A-03, the codex transport: the Codex argv carries no token, and the probe refuses a missing auth file
        by its path, never copying or printing the content of one that is there."""
        auth = os.path.join(self.root, "auth.json")
        with open(auth, "w") as fh:
            json.dump({"tokens": {"access_token": CANARY}}, fh)
        absent = os.path.join(self.root, "absent.json")
        before = set(os.listdir(self.root))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            argv, stdin, _ = self.adapter().argv("luna", "run the check", 60, {"transport": "codex", "id": "gpt-6-luna"})
            ok = login_probe(auth)
            missing = login_probe(absent)
        self.assertNotIn(CANARY, " ".join(argv) + stdin)
        self.assertEqual(ok, ("OK", auth))
        self.assertIs(CodexAdapter.login_probe, CX.login_probe)
        self.assertEqual(missing[0], "FAIL")
        self.assertIn(absent, missing[1])
        self.assertTrue(missing[1].endswith(" is missing"), missing[1])
        self.assertTrue(missing[1].startswith("codex login: "))
        self.assertNotIn(CANARY, out.getvalue() + err.getvalue() + repr(ok) + repr(missing))
        self.assertEqual(set(os.listdir(self.root)), before | {"codex"}, "nothing copied")

    def test_codex_probe_refuses_a_malformed_file(self):
        auth = os.path.join(self.root, "auth.json")
        with open(auth, "w") as fh:
            fh.write(CANARY + " not json")
        verdict = login_probe(auth)
        self.assertEqual(verdict[0], "FAIL")
        self.assertIn(auth, verdict[1])
        self.assertNotIn(CANARY, verdict[1])

    def test_codex_probe_refuses_an_unreadable_file(self):
        if os.getuid() == 0:
            self.skipTest("NO-DATA: root reads a mode 000 file")
        auth = os.path.join(self.root, "auth.json")
        with open(auth, "w") as fh:
            json.dump({"a": 1}, fh)
        os.chmod(auth, 0)
        self.addCleanup(os.chmod, auth, 0o600)
        verdict = login_probe(auth)
        self.assertEqual(verdict[0], "FAIL")
        self.assertIn(auth, verdict[1])

    def test_codex_probe_refuses_a_folder_and_a_non_object(self):
        self.assertEqual(login_probe(self.root), ("FAIL", "codex login: %s is not a file" % self.root))
        auth = os.path.join(self.root, "auth.json")
        with open(auth, "w") as fh:
            fh.write("[]")
        self.assertEqual(login_probe(auth)[0], "FAIL")

    def test_codex_probe_default_reads_codex_home(self):
        saved = os.environ.get("CODEX_HOME")
        self.addCleanup(lambda: os.environ.pop("CODEX_HOME", None) if saved is None
                        else os.environ.__setitem__("CODEX_HOME", saved))
        os.environ["CODEX_HOME"] = self.root
        verdict = login_probe()
        self.assertEqual(verdict[0], "FAIL")
        self.assertIn(os.path.join(self.root, "auth.json"), verdict[1])


class Refusals(Base):
    def refused_before_start(self, run_dir, command=None, reader=None):
        """hold refuses with Failure, and fast, and the command never started (it would write a marker)."""
        marker = os.path.join(self.root, "started")
        reader = reader or FailAfterFirst()
        command = [sys.executable, "-c", "open(%r, 'w').close()" % marker] if command is None else command
        h = self.thread(lambda: BL.hold(run_dir, command, reader=reader))
        h["thread"].join(10)
        self.assertFalse(h["thread"].is_alive(), "hold went on to serve instead of refusing")
        self.assertIsInstance(h.get("exc"), BL.Failure, h)
        time.sleep(0.2)
        self.assertFalse(os.path.exists(marker), "the command never started")
        return reader

    def test_hostile_run_folders_are_refused(self):
        """REQ-S1A-06: a run folder with a newline, a relative one, a missing one. Each refuses before the reader runs
        and before the command starts."""
        nl = os.path.join(self.root, "bad\nrun")
        os.makedirs(nl)
        for run in (nl, "relative/run", os.path.join(self.root, "absent")):
            with self.subTest(run=run):
                self.assertEqual(self.refused_before_start(run).calls, 0)

    def test_an_empty_command_is_refused(self):
        for cmd in ([], [""], "python3"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.refused_before_start(self.run_dir, command=cmd).calls, 0)

    def test_a_command_that_cannot_start_is_refused(self):
        self.refused_before_start(self.run_dir, command=[os.path.join(self.root, "no-such-program")])

    def test_a_login_that_is_not_a_token_starts_nothing(self):
        self.refused_before_start(self.run_dir, reader=FailAfterFirst(b"not a token"))

    def test_hostile_descriptor_names_are_refused(self):
        """A named descriptor that is not the holder's: never the fresh read (refused is not absent), one condition
        each: not a number, stdio, out of range, closed, a regular file, a stream socket."""
        calls = []
        BL.read_saved = lambda: calls.append(1) or CANARY.encode()
        r, w = os.pipe()
        os.close(r)
        os.close(w)
        fh = open(os.path.join(self.root, "plain"), "w")
        self.addCleanup(fh.close)
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        BL.HOLD_ANSWER_S = 0.5   # a check that did not refuse would wait for an answer that never comes
        for raw in ("abc", "-1", "2", "1e3", "9999999", str(r), str(fh.fileno()), str(a.fileno())):
            with self.subTest(raw=raw):
                os.environ[BL.HOLD_FD_VAR] = raw
                with self.assertRaises(BL.Failure):
                    BL.ask_holder()
                with self.assertRaises(BL.Failure):
                    self.seat()
        self.assertEqual(calls, [])
        b.setblocking(False)
        with self.assertRaises(BlockingIOError, msg="nothing was sent to a socket of the wrong kind"):
            b.recv(64)

    def test_a_full_pipe_never_blocks_the_holder(self):
        """An asker whose pipe is already full gets nothing, and the holder goes on answering the next one."""
        _, theirs = self.pair()
        r, w = os.pipe()
        self.addCleanup(os.close, r)
        os.set_blocking(w, False)
        try:
            while True:
                os.write(w, b"x" * 65536)
        except BlockingIOError:
            pass   # sbe: allow-silent the pipe is full, which is the state this test needs
        os.set_blocking(w, True)   # the holder must make it non blocking itself
        theirs.sendmsg([BL._ASK], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack("i", w))])
        os.close(w)
        BL.HOLD_ANSWER_S = 3
        time.sleep(0.3)
        self.assertEqual(BL.ask_holder(), CANARY)

    def test_malformed_requests_are_refused_and_the_holder_keeps_serving(self):
        """A request with the wrong words, with no descriptor, with two, or with a regular file to answer into: each
        refused and logged without content, the file stays empty, and a good ask is still answered."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            _, theirs = self.pair()
            plain = os.path.join(self.root, "answer-here")
            fh = open(plain, "wb")
            self.addCleanup(fh.close)
            r1, w1 = os.pipe()
            r2, w2 = os.pipe()
            for r in (r1, r2):
                self.addCleanup(os.close, r)
            rights = lambda *fds: [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack("%di" % len(fds), *fds))]
            theirs.sendmsg([b"give"], rights(w1))
            theirs.sendmsg([BL._ASK])
            theirs.sendmsg([BL._ASK], rights(w1, w2))
            theirs.sendmsg([BL._ASK], rights(fh.fileno()))
            os.close(w1)
            os.close(w2)
            time.sleep(0.3)
            self.assertEqual(BL.ask_holder(), CANARY)
        self.assertEqual(os.path.getsize(plain), 0, "no answer into a file")
        self.assertEqual(err.getvalue().count("refused a malformed request"), 4, err.getvalue())
        self.assertNotIn(CANARY, err.getvalue())

    def fake_holder(self, payload, close=True):
        """A holder end that takes one request and answers payload into it: a broken or hostile holder."""
        mine, _ = self.pair(serve=False)
        mine.settimeout(10)

        def answer():
            fd = None
            while fd is None:
                fd = BL._take_request(mine)
            os.write(fd, payload)
            if not close:
                time.sleep(5)   # a stuck holder that lets go late
            os.close(fd)
        self.thread(answer)

    def test_an_answer_that_is_not_a_token_is_refused(self):
        bad = b"not a token at all"
        self.fake_holder(struct.pack(">I", len(bad)) + bad)
        with self.assertRaises(BL.Failure):
            BL.ask_holder()

    def test_a_short_read_raises(self):
        """Partially done: a holder killed mid answer sends fewer bytes than it announced."""
        tok = CANARY.encode()
        self.fake_holder(struct.pack(">I", len(tok)) + tok[:-5])
        with self.assertRaises(BL.Failure):
            BL.ask_holder()

    def test_a_holder_that_never_finishes_its_answer_times_out(self):
        """A holder that keeps the answer pipe open and writes nothing: the asker gives up at its own deadline, long
        before the holder lets go (5 s here), so no seat waits on a stuck holder."""
        BL.HOLD_ANSWER_S = 0.5
        self.fake_holder(b"", close=False)
        started = time.monotonic()
        with self.assertRaises(BL.Failure):
            BL.ask_holder()
        self.assertLess(time.monotonic() - started, 3, "the asker's own deadline ended the wait")

    def test_a_refused_holder_parks_the_seat(self):
        """A holder that answers nothing raises, and login_child does not fall back to the Keychain: refused is not
        absent."""
        self.fake_holder(b"")
        calls = []
        BL.read_saved = lambda: calls.append(1) or CANARY.encode()
        with self.assertRaises(BL.Failure):
            self.seat()
        self.assertEqual(calls, [])

    def test_a_held_login_that_is_not_a_token_never_reaches_a_child(self):
        """with_login checks a held value itself, whoever handed it in."""
        with self.assertRaises(BL.Failure):
            BL.with_login({}, b"not a token")


class SeatSandbox(Base):
    def test_the_seat_sandbox_denies_a_socket_under_claude_bin(self):
        """Defense in depth (S1.b): inside the real seat profile, on both lanes, a connect to a listening unix socket
        under HOME/.claude/bin is refused while the same connect to one in the seat's temp folder is not (so the
        refusal is the rule, not a sandbox that refuses every socket). HOME is a scratch folder handed to the profile
        the way _sandbox_head hands the real one. NO-DATA, never a pass, where sandbox-exec cannot run here."""
        probe = subprocess.run(["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)", "/usr/bin/true"],
                               capture_output=True)
        if probe.returncode != 0:
            self.skipTest("NO-DATA: sandbox-exec cannot run here (nested in another sandbox?)")
        short = short_dir()
        self.addCleanup(shutil.rmtree, short, True)
        home = os.path.join(short, "home")
        wt = os.path.join(short, "wt")
        os.makedirs(os.path.join(home, ".claude", "bin", ".hold"))
        os.makedirs(os.path.join(wt, ".tmp"))
        listeners = []
        for path in (os.path.join(home, ".claude", "bin", ".hold", "s.sock"), os.path.join(wt, ".tmp", "c.sock")):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.bind(path)
            s.listen(4)
            self.addCleanup(s.close)
            listeners.append(path)
        code = ("import socket, sys\nfor p in sys.argv[1:]:\n    s = socket.socket(socket.AF_UNIX)\n    try:\n"
                "        s.connect(p); print('connected', p)\n    except OSError as e:\n        print('refused', p, e.errno)\n")
        for token_on in (False, True):
            with self.subTest(token_on=token_on):
                head = NW._sandbox_head(wt, "", {}, token_on)
                head[head.index("HOME=" + os.path.realpath(os.path.expanduser("~")))] = "HOME=" + home
                cut = head.index("env")
                p = subprocess.run(head[:cut] + ["/usr/bin/python3", "-B", "-c", code] + listeners,
                                   capture_output=True, text=True, timeout=60, cwd=wt)
                self.assertIn("refused %s" % listeners[0], p.stdout, p.stdout + p.stderr)
                self.assertIn("connected %s" % listeners[1], p.stdout, p.stdout + p.stderr)


class EntryPoint(Base):
    def test_the_hold_command_runs_the_way_a_launch_runs_it(self):
        """brother_login.py hold --run DIR -- COMMAND through main(), in its own process with a fake reader program:
        the command's seats get the login (their own reader locked), the hold exits with the command's status, and
        nothing it prints carries the login."""
        reader = os.path.join(self.root, "brother-keychain")
        with open(reader, "w") as fh:
            fh.write("#!/bin/sh\n[ \"$1\" = get ] && printf '%%s\\n' '%s' && exit 0\nexit 9\n" % CANARY)
        os.chmod(reader, 0o700)
        out = os.path.join(self.root, "out-main.json")
        code = ("import sys; sys.path.insert(0, %r); import brother_login as BL; BL.READER = %r; "
                "sys.exit(BL.main(sys.argv[1:]))" % (HERE, reader))
        p = subprocess.run([sys.executable, "-B", "-c", code, "hold", "--run", self.run_dir, "--",
                            sys.executable, "-B", self.child, out, "seats"], capture_output=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn(CANARY.encode(), p.stdout + p.stderr)
        with open(out) as fh:
            self.assertEqual(json.load(fh)["seats"], [CANARY_SHA] * 3)
        p = subprocess.run([sys.executable, "-B", "-c", code, "hold", "--run", self.run_dir, "--",
                            sys.executable, "-B", self.child, out, "exit5"], capture_output=True, timeout=120)
        self.assertEqual(p.returncode, 5, p.stderr)

    def test_the_hold_command_refuses_a_bad_line(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            for argv in (["hold"], ["hold", "--run"], ["hold", "--run", self.run_dir],
                         ["hold", "--run", self.run_dir, "/usr/bin/true"], ["hold", "--run", self.run_dir, "--"],
                         ["hold", "--run", "relative", "--", "/usr/bin/true"]):
                with self.subTest(argv=argv):
                    self.assertEqual(BL.main(argv), 1)
        self.assertNotIn("Traceback", err.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=1)
