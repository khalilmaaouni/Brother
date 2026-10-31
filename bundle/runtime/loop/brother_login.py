#!/usr/bin/env python3
"""brother-login: one command that gives the loop a long-lived Claude login, and no agent ever sees the token.

    python3 scripts/loop/brother_login.py      sign in once; the token lands in your login Keychain
    python3 scripts/loop/brother_login.py forget   remove it again (then revoke it in Claude settings)
    python3 scripts/loop/brother_login.py hold --run DIR -- COMMAND...   hold the login in memory for one loop run,
        answering COMMAND and only what COMMAND passes the descriptor to (see hold())

HOW. `claude setup-token` is run inside a private pseudo terminal owned by this helper: nothing of its transcript
reaches the screen, a log, or a file. The one token it prints is parsed after the child exits, handed on stdin
(never argv, never a shell) to the loop's own Keychain reader, and read back to prove the write. The item is
SERVICE under the account running this command, in the explicit login Keychain path.

THE READER (review 2026-10-03, finding 2): an item written by /usr/bin/security trusts /usr/bin/security, which any
seat on the off lane can still run (D10), so it would read the login silently. The item is therefore created by
READER, a small program (brother_keychain.c beside this file) built once and ad-hoc signed into ~/.claude/bin, a
folder no seat can read or run from. The Keychain trusts that program's code identity alone; every other reader,
/usr/bin/security included, makes the Keychain ask the person. The reader is never rebuilt while it exists: a new
build is a new identity, and the item would stop opening without a question.
The reader is built with a fresh random salt (review 2026-10-03, F2): its source ships in every seat's worktree, and an
unsalted ad-hoc build is reproducible byte for byte, so a seat could have rebuilt the trusted identity.
The loop's native worker (scripts/loop/native_worker.py) reads the item only when BROTHER_LOOP_TOKEN is exactly
"on", through with_login(), and hands it to the headless claude child ON A PIPE (CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR),
never in its environment (review 2026-10-03, F1: any process of the same user, a seat's own Bash tool included, reads a
running process's environment with sysctl KERN_PROCARGS2, and the seat sandbox cannot deny that call).

Trusted helper: run it in Terminal, never through an agent tool. Standard library plus the Xcode command line tools.
Test: python3 -B scripts/loop/test_brother_login.py (a fake claude and a fake reader; the real Keychain is never touched)
"""
import errno
import fcntl
import hmac
import json
import math
import os
import pty
import pwd
import re
import resource
import secrets
import select
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import termios
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SERVICE = "claude-loop-token"
TOKEN_VAR = "CLAUDE_CODE_OAUTH_TOKEN"   # never set for a child: an environment is readable by every process of the user
FD_VAR = "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR"   # the CLI reads its login from this descriptor (proven live 2026-10-03)
_USER = pwd.getpwuid(os.getuid())
ACCOUNT = _USER.pw_name
HOME = _USER.pw_dir
KEYCHAIN = HOME + "/Library/Keychains/login.keychain-db"
READER = HOME + "/.claude/bin/brother-keychain"   # tests point this at a fake
READER_SOURCE = os.path.join(HERE, "brother_keychain.c")
CLAUDE = None   # resolved by claude_program() at call time; tests set it to a fake
TOKEN = re.compile(rb"sk-ant-oat01-[A-Za-z0-9_-]{20,2048}")
ANSI = re.compile(rb"\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]")
SIGN_IN_S = 300
MISSING = "Brother cannot read its login. Run brother-login (python3 scripts/loop/brother_login.py), or unlock your Keychain."
NOT_FOUND = 44    # the reader's exit status for an item that is not there
FOREIGN = 45      # the reader's exit status for an item another program created (an older Brother used /usr/bin/security)
OLD_ITEM = ("A login saved by an older Brother is in the way. Remove it, then run brother-login again:\n"
            "  security delete-generic-password -s %s -a \"$USER\" ~/Library/Keychains/login.keychain-db" % SERVICE)
#: THE EXPIRY AHEAD (1.1.6 S4). The item is the token, then SAVED_AT and whole epoch seconds, written in one set, so a
#: re-login replaces both at once. read_saved strips the field: every caller (with_login, hold, status) sees the bare
#: token. `claude setup-token` makes "a one-year OAuth token" (code.claude.com/docs/en/authentication, "Generate a
#: long-lived token", read 2026-10-18; its --help says only "long-lived"); a year is counted as 365 days. WARN_S is a
#: choice, not a measurement: two weeks, so an owner away for a week still meets the warning on a status.
SAVED_AT = b"\nsaved_at="
LIFETIME_S = 365 * 86400
WARN_S = 14 * 86400
CLOCK = time.time   # tests point this at a fake clock


class Failure(Exception):
    """A plain message for the person at the keyboard. Never carries child output, a token or a traceback."""


def require(ok, message):
    if not ok:
        raise Failure(message)


def claude_program():
    return CLAUDE or shutil.which("claude")


def base_env():
    # No inherited auth, debug, telemetry or Python injection variables reach the child.
    return {"HOME": HOME, "USER": ACCOUNT, "LOGNAME": ACCOUNT, "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "en_US.UTF-8", "TERM": "xterm-256color"}


def build_reader(target=None, source=None):
    """Build and ad-hoc sign the reader at target when it is not there yet. Never rebuilds an existing one (a new
    identity would lock the saved item). Failure names the toolchain; nothing else is touched."""
    target = target or READER
    if os.path.isfile(target):
        return target
    source = source or READER_SOURCE
    require(os.path.isfile(source), "Brother's Keychain reader source is missing. Update Brother.")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".build-%d" % os.getpid()
    try:
        salt = '-DBROTHER_BUILD_SALT="%s"' % secrets.token_hex(16)   # a unique identity per build, never reproducible
        for argv in (["xcrun", "--sdk", "macosx", "clang", "-Wno-deprecated-declarations", salt, "-framework", "Security",
                      "-framework", "CoreFoundation", "-o", tmp, source],
                     ["codesign", "-s", "-", tmp]):
            r = subprocess.run(argv, capture_output=True, env=base_env(), timeout=300, check=False)
            require(r.returncode == 0, "Brother could not build its Keychain reader. Install the Xcode command line "
                                       "tools (xcode-select --install) and try again.")
        os.chmod(tmp, 0o700)
        os.rename(tmp, target)
    except (OSError, subprocess.TimeoutExpired):
        raise Failure("Brother could not build its Keychain reader. Check ~/.claude/bin is writable and try again.") from None
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return target


def quiet(args, data=None):
    """Run the reader with everything captured. Returns the CompletedProcess; never raises on its exit code."""
    try:
        return subprocess.run([READER] + args, input=b"" if data is None else data, capture_output=True,
                              env=base_env(), timeout=60, check=False)
    except subprocess.TimeoutExpired:
        raise Failure("Keychain did not respond. Unlock it and try again.") from None
    except OSError:
        raise Failure(MISSING) from None   # the reader is not built yet: brother-login builds it


def identity():
    return [SERVICE, ACCOUNT, KEYCHAIN]


def read_item():
    """(token, saved_at bytes or None) from the one item, or Failure (missing, locked, malformed). A login saved
    before 1.1.6 S4 carries no saved_at: None, never a time. Neither value is printed by anyone."""
    r = quiet(["get"] + identity())
    require(r.returncode == 0, MISSING)
    value = r.stdout[:-1] if r.stdout.endswith(b"\n") else r.stdout
    value, mark, saved_at = value.partition(SAVED_AT)
    require(TOKEN.fullmatch(value) is not None, "Saved login is not a Claude token. Run brother-login again.")
    return value, saved_at if mark else None


def read_saved():
    """The saved token, bytes, or Failure (missing, locked, malformed). Never printed by anyone."""
    return read_item()[0]


def age_line(saved_at, now, lifetime_s, warn_s):
    """The status line for a login saved at saved_at (epoch seconds; bytes or text from the item are read as a number):
    its age, plus "Renew within N days: run brother-login." when under warn_s of lifetime_s is left, or a lapsed line
    once it is gone. A saved_at that is missing, not a number, not finite, zero or below, or after now answers NO-DATA,
    never fresh. A lifetime or warning window that is not a finite number (above zero for the lifetime) refuses."""
    ok = lambda x: not isinstance(x, bool) and isinstance(x, (int, float)) and math.isfinite(x)
    require(ok(lifetime_s) and lifetime_s > 0 and ok(warn_s) and warn_s >= 0 and ok(now) and now > 0,
            "Brother's login lifetime is unknown here. Update Brother.")
    try:
        at = None if isinstance(saved_at, bool) else float(saved_at)
    except (TypeError, ValueError):
        at = None
    if at is None or not math.isfinite(at) or at <= 0 or at > now:
        return "Brother login age: NO-DATA (saved without a usable time). Run brother-login to record one."
    age = now - at
    line = "Brother login saved %d days ago." % (age // 86400)
    left = lifetime_s - age
    if left <= 0:
        return line + " It has lapsed (a setup-token login lasts one year): run brother-login."
    if left < warn_s:
        return line + " Renew within %d days: run brother-login." % math.ceil(left / 86400)
    return line


#: CUT1.d: ONE coordinator per login scope. RENEW_REUSE_S is how long a renewal answers every consumer that waited on
#: the lock (the same actor twice included) without a second refresh; a choice, not a measurement, kept well under the
#: shortest token lifetime it could cover.
RENEW_REUSE_S = 60
SCOPE_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
PARK_PROMPT = ("Brother's %s login cannot be renewed here (%s). Run brother-login (python3 scripts/loop/brother_login.py) "
               "in Terminal; the run then resumes from its checkpoint.")
#: the four places a sign in can fail, in the order a detail is matched against them; anything else is the provider
AUTH_CLASSES = (("Keychain", re.compile(r"keychain|unlock", re.I)),
                ("signing", re.compile(r"codesign|signing|code identity|reader", re.I)),
                ("Brother approval", re.compile(r"brother-approve|approval|authenticator", re.I)))
_SECRETISH = re.compile(r"sk-[A-Za-z0-9_-]{8,}|[A-Za-z0-9_-]{32,}")


def auth_class(detail):
    """provider login, Keychain, signing or Brother approval: where a failed sign in failed, from its words."""
    text = detail if isinstance(detail, str) else ""
    for name, pattern in AUTH_CLASSES:
        if pattern.search(text):
            return name
    return "provider login"


def _scrub(text):
    """A detail with anything shaped like a token or a code removed: none is ever written or printed."""
    return _SECRETISH.sub("[removed]", text if isinstance(text, str) else repr(text))[:300]


def _read_login_state(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}   # sbe: allow-silent missing or corrupt reads as no record: the refresher runs, nothing reads RENEWED


def _write_login_state(path, data):
    tmp = "%s.tmp-%d-%d" % (path, os.getpid(), threading.get_ident())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, sort_keys=True)
    os.replace(tmp, path)


def refresh_once(scope, refresher, state_dir, attempts=2, notify=None):
    """(verdict, detail): RENEWED, PARKED, or the refresher's own failure. One file lock per scope under state_dir, so
    any number of concurrent consumers cause one refresher call: a renewal younger than RENEW_REUSE_S answers every
    later consumer without a second one. refresher() answers (verdict, detail): RENEWED, EXPIRED (cannot be renewed
    here), or anything else for a failure worth trying again, up to `attempts` calls. EXPIRED writes one park record
    (its class, never a token or a code) and calls notify(prompt) ONCE, naming brother-login; a parked scope still
    asks the refresher on every call, reads PARKED without a new prompt while it stays EXPIRED, and a RENEWED answer
    clears the park. A refresher that raises is a failure, never a renewal."""
    if not isinstance(scope, str) or not SCOPE_RE.fullmatch(scope) or not callable(refresher) \
            or not isinstance(state_dir, str) or not state_dir or isinstance(attempts, bool) \
            or not isinstance(attempts, int) or attempts < 1:
        return "FAIL", "refresh_once needs a scope name, a refresher, a state folder and attempts of 1 or more"
    try:
        os.makedirs(state_dir, exist_ok=True)
        lock = open(os.path.join(state_dir, "login-%s.lock" % scope), "a")
    except OSError as exc:
        return "FAIL", "the login lock cannot be opened in %s (%s)" % (state_dir, type(exc).__name__)
    path = os.path.join(state_dir, "login-%s.json" % scope)
    with lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)   # released when the file closes
        rec = _read_login_state(path)
        now = CLOCK()
        at = rec.get("at")
        if rec.get("state") == "renewed" and isinstance(at, (int, float)) and not isinstance(at, bool) \
                and 0 <= now - at < RENEW_REUSE_S:
            return "RENEWED", "renewed %d s ago by another consumer of %s" % (now - at, scope)
        verdict, detail = "FAIL", "the refresher never answered"
        for _ in range(attempts):
            try:
                got = refresher()
            except Exception as exc:   # sbe: allow-silent a raising refresher is a failure, named by its type only
                got = ("FAIL", "the refresher raised %s" % type(exc).__name__)
            if not (isinstance(got, (tuple, list)) and len(got) == 2 and isinstance(got[0], str)):
                got = ("FAIL", "the refresher answered %r, not (verdict, detail)" % (type(got).__name__,))
            verdict, detail = got[0], _scrub(got[1])
            if verdict in ("RENEWED", "EXPIRED"):
                break
        try:
            if verdict == "RENEWED":
                _write_login_state(path, {"state": "renewed", "at": now})
                return "RENEWED", detail
            if verdict == "EXPIRED":
                if rec.get("state") != "parked":
                    cls = auth_class(detail)
                    _write_login_state(path, {"state": "parked", "at": now, "class": cls, "detail": detail})
                    if notify is not None:
                        notify(PARK_PROMPT % (scope, cls))
                return "PARKED", detail
        except OSError as exc:
            return "FAIL", "%s, and the login record could not be written (%s)" % (detail, type(exc).__name__)
        return verdict, detail


def with_login(scrubbed_env, value=None):
    """(env, fd) for one claude child: env is a COPY of scrubbed_env naming FD_VAR=fd and carrying no TOKEN_VAR (an
    inherited one included), and fd is the read end of a pipe holding the token, its write end already closed, so it
    reads exactly once and then reads empty. value: the token the run's holder answered (ask_holder), or None for a
    fresh Keychain read. The caller passes fd to that one child (pass_fds) and closes it after the spawn. The token is
    never in any environment, argv, file or log; os.environ is never touched."""
    value = read_saved() if value is None else value
    require(TOKEN.fullmatch(value) is not None, "The held login is not a Claude token. Restart the run.")
    env = {k: v for k, v in scrubbed_env.items() if k not in (TOKEN_VAR, HOLD_FD_VAR)}   # a child never names the holder
    r, w = os.pipe()   # both ends non inheritable; pass_fds opens r to the one child
    try:
        require(os.write(w, value) == len(value), "Brother could not hand over its login. Try again.")   # < pipe buffer
    except BaseException:
        os.close(r)
        raise
    finally:
        os.close(w)
    env[FD_VAR] = str(r)
    return env, r


#: THE RUN'S LOGIN HOLDER (1.1.6 S1, S1.b). In 1.1.5 the login Keychain locked while the owner slept and every seat
#: started after that failed its fresh read. `hold --run DIR -- COMMAND...` reads the login ONCE at launch, keeps it in
#: memory, and starts COMMAND (the run's launcher) as the ONLY holder of the other end of an unnamed socket pair, named to
#: it by HOLD_FD_VAR (a descriptor number, never the login). No path exists on disk: S1's named socket answered any
#: process of the same uid, a seat's own shell included, which defeated the reader's code identity ACL (security
#: review 2026-10-18 01h5x, HIGH). A process gets the login only by holding the descriptor, so only processes the
#: launcher passes it to can ask (every Python hop closes it unless pass_fds names it; native_worker's seats never get
#: it). A request is one datagram carrying the write end of the asker's own pipe; the answer is a 4 byte length then
#: the token, written into that pipe and nowhere else, so a holder killed mid answer is a short read, never a truncated
#: token. It stops answering when the run folder is removed, and returns COMMAND's exit status once it ends.
HOLD_FD_VAR = "BROTHER_LOGIN_HOLD_FD"
HOLD_POLL_S = 0.5    # how soon the holder sees its run folder gone or its command ended
HOLD_ANSWER_S = 10   # how long an asker waits for its answer
_ASK = b"login?"
#: the holder's end is closed: macOS answers ECONNRESET on the first send after that, then EDESTADDRREQ (measured)
_DEAD = (errno.ECONNRESET, errno.EDESTADDRREQ, errno.ECONNREFUSED, errno.ENOTCONN, errno.EPIPE)
#: S2 round 1 (review B): a named holder whose peer is gone (its run folder removed, its command ended, or it was
#: stopped or killed) is a refusal that names the holder, never a quiet fresh Keychain read that, locked by then, named
#: the Keychain. runner_pool.LOGIN_HINTS classes it ("The run's login holder"), so the unit parks once, owner told.
HOLDER_ENDED = ("The run's login holder has ended, so this run no longer has its login. Restart the run "
                "(start_run.sh) to read it again.")
#: S2 round 1 (review A): the holder descriptor THIS process kept when take_hold took the name from its own
#: environment, so the process's own in process roles (build plan, repair advisor) still ask the holder. "" when none;
#: _BROKEN when the name named nothing open here (S2 round 3, DeepSeek lane L2): never the number itself, which a socket
#: opened later in this process could take; ask_holder refuses _BROKEN as a name that is not a descriptor.
_HELD = ""
_BROKEN = "broken"
#: THE HELD RUN'S MARK (S2 round 3, finding 1). hold() sets HELD_RUN_VAR=1 in its command's environment, and the driver
#: and the pass keep it where they unset HOLD_FD_VAR, so every process of a held run knows it is one. It names no
#: descriptor and carries no login. A Claude role host of a held run that was handed no holder (diag_round and its fan
#: out) then refuses with UNHANDED (native_worker.login_child), never a quiet fresh Keychain read. The finisher's job,
#: which outlives the run and reads fresh by design, clears it with the name.
HELD_RUN_VAR = "BROTHER_LOGIN_HELD_RUN"
UNHANDED = ("The run's login holder was not handed to this process, so it has no login here and does not read the "
            "Keychain itself. Restart the run (start_run.sh), or run this role off the token lane.")


def _run_folder(run_dir):
    """The resolved run folder. Refuses one that is not absolute, carries a control character (a newline included) or
    does not exist."""
    require(isinstance(run_dir, str) and os.path.isabs(run_dir) and not re.search(r"[\x00-\x1f\x7f]", run_dir),
            "A login holder needs an absolute run folder with no control character in its name.")
    run = os.path.realpath(run_dir)
    require(os.path.isdir(run), "The run folder to hold the login for does not exist: %s" % run)
    return run


def _take_request(chan):
    """The write end of one asker's pipe, or None (no request yet, or one that is refused: every descriptor it carried
    is closed, and the refusal is logged without any content)."""
    try:
        msg, anc, _, _ = chan.recvmsg(len(_ASK) + 1, socket.CMSG_SPACE(4 * struct.calcsize("i")))
    except socket.timeout:
        return None   # sbe: allow-silent no request arrived within the poll window; the holder simply polls again
    except OSError:
        return None   # sbe: allow-silent the asker went away mid send; it raises on its own empty answer
    fds = []
    for level, kind, data in anc:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            n = len(data) // struct.calcsize("i")
            fds.extend(struct.unpack("%di" % n, data[:n * struct.calcsize("i")]))
    ok = msg == _ASK and len(fds) == 1   # a longer message or more descriptors arrive cut, and fail this too
    if ok:
        try:
            ok = stat.S_ISFIFO(os.fstat(fds[0]).st_mode)   # an answer goes into a pipe, never a file or a socket
        except OSError:
            ok = False
    if not ok:
        for fd in fds:
            os.close(fd)
        print("brother-login hold: refused a malformed request", file=sys.stderr, flush=True)
        return None
    return fds[0]


def _answer(fd, value):
    """The framed token into the asker's pipe, without ever blocking the holder (a full pipe is the asker's loss)."""
    try:
        fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) | os.O_NONBLOCK)
        os.write(fd, struct.pack(">I", len(value)) + value)
    except OSError:   # sbe: allow-silent an asker that hung up or gave a full pipe gets nothing, and raises on its short read
        pass
    finally:
        os.close(fd)


def serve(chan, value, running):
    """Answer each request on chan (the holder's end of the pair) while running() is true."""
    chan.settimeout(HOLD_POLL_S)
    while running():
        fd = _take_request(chan)
        if fd is not None:
            _answer(fd, value)


def hold(run_dir, command, reader=None):
    """Hold the saved login for one run: refuse a hostile run folder or an empty command, read the login ONCE with
    reader (default read_saved, the signed reader), start command with the pair's other end as its one extra
    descriptor, answer until the run folder is removed or command ends, and return command's exit status. Failure on
    any refusal: nothing is started then."""
    run = _run_folder(run_dir)
    require(isinstance(command, (list, tuple)) and command and all(isinstance(a, str) and a for a in command),
            "A login holder needs the command of the run it holds the login for.")
    value = (reader or read_saved)()
    require(isinstance(value, bytes) and TOKEN.fullmatch(value) is not None,
            "Saved login is not a Claude token. Run brother-login again.")
    mine, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        env = {k: v for k, v in os.environ.items() if k not in (TOKEN_VAR, FD_VAR)}
        env[HOLD_FD_VAR] = str(theirs.fileno())
        env[HELD_RUN_VAR] = "1"   # the run is held: no process of it reads the Keychain fresh (S2 round 3)
        try:
            # its own session (S2 round 1, review C): the command (the driver) leads its own process group, as it
            # does off the token lane, so a stop signals its whole group, a foreground refresh child included
            child = subprocess.Popen(list(command), env=env, pass_fds=(theirs.fileno(),), start_new_session=True)
        except OSError:
            raise Failure("The login holder could not start the run's command: %s" % command[0]) from None
        finally:
            theirs.close()
        serve(mine, value, lambda: child.poll() is None and os.path.isdir(run))
    finally:
        mine.close()   # the run folder is gone or the command ended: every later ask finds a dead holder
    return child.wait()


def ask_holder(environ=None):
    """The token the run's holder answers on the descriptor HOLD_FD_VAR names (in environ, default os.environ; for
    this process's own environment, else the descriptor take_hold kept here), as text, or "" when no holder is named.
    Raises Failure when the named holder has ended (HOLDER_ENDED), when the named descriptor is not a datagram unix
    socket, or on a refused request, a short read or an answer that is not a Claude token: refused or ended is not
    absent, so the caller never falls back to a fresh read. A process that does not hold the descriptor has nothing
    to ask."""
    own = environ is None or environ is os.environ
    raw = (os.environ if environ is None else environ).get(HOLD_FD_VAR, "") or (_HELD if own else "")
    if not raw:
        return ""
    require(re.fullmatch(r"[0-9]{1,6}", raw) is not None,
            "The run's login holder is named by something that is not a descriptor. Restart the run.")
    fd = int(raw)
    try:
        os.set_inheritable(fd, False)   # no program this process starts by exec ever inherits it
        chan = socket.socket(fileno=os.dup(fd))   # refuses a descriptor that is not a socket
    except OSError:
        raise Failure("The run's login holder descriptor is not an open socket. Restart the run.") from None
    r, w = os.pipe()
    try:
        with chan:
            require(chan.family == socket.AF_UNIX and chan.type == socket.SOCK_DGRAM,
                    "The run's login holder descriptor is not the holder's kind of socket. Restart the run.")
            try:
                chan.sendmsg([_ASK], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack("i", w))])
            except OSError as exc:
                if exc.errno in _DEAD:
                    raise Failure(HOLDER_ENDED) from None
                raise Failure("The run's login holder did not take the request. Restart the run.") from None
        os.close(w)
        w = None
        data = b""
        deadline = time.monotonic() + HOLD_ANSWER_S
        while len(data) <= 8192:
            left = deadline - time.monotonic()
            require(left > 0 and select.select([r], [], [], left)[0], "The run's login holder did not answer. Restart the run.")
            chunk = os.read(r, 8192)
            if not chunk:
                break
            data += chunk
    finally:
        os.close(r)
        if w is not None:
            os.close(w)
    require(data, "The run's login holder refused this request.")
    require(len(data) >= 4 and struct.unpack(">I", data[:4])[0] == len(data) - 4,
            "The run's login holder broke off its answer. Restart the run.")
    require(TOKEN.fullmatch(data[4:]) is not None, "The run's login holder answered something that is not a login.")
    return data[4:].decode("ascii")


def take_hold(environ=None):
    """THE HOP (1.1.6 S2): a loop process that holds the run's holder descriptor hands it to ONE child, never to all.
    Pops HOLD_FD_VAR from environ (default os.environ), so no other child this process starts is told a number it
    does not hold (a Python spawn closes every descriptor it is not given, and a seat told a closed one refuses), and
    answers (env, fds) for the one child: ({}, ()) when none is named; ({HOLD_FD_VAR: raw}, (fd,)) when raw names a
    socket open here; ({HOLD_FD_VAR: raw}, ()) when it names anything else, so that child's ask refuses (a broken hop
    is loud, never a quiet fresh read). The caller spawns that child with env=dict(os.environ, **env), pass_fds=fds.
    S2 round 1 (review A): over this process's own environment the name is kept in _HELD, so this process's own in
    process roles still ask the holder (ask_holder), and a held descriptor is made close on exec, so no program this
    process starts gets it unless pass_fds names it."""
    global _HELD
    own = environ is None or environ is os.environ
    environ = os.environ if environ is None else environ
    raw = environ.pop(HOLD_FD_VAR, "")
    if not raw:
        return {}, ()
    try:
        held = re.fullmatch(r"[0-9]{1,6}", raw) is not None and stat.S_ISSOCK(os.fstat(int(raw)).st_mode)
        if held:
            os.set_inheritable(int(raw), False)
    except OSError:
        held = False   # sbe: allow-silent not open here: the name goes on alone and that child's ask refuses
    if own:
        _HELD = raw if held else _BROKEN   # S2 round 3: validated first, so a broken name is never kept as a number
    return {HOLD_FD_VAR: raw}, ((int(raw),) if held else ())


def save(value):
    """Write the token and its saved_at (CLOCK, whole seconds) as the one item through the reader (stdin only), then
    prove both by reading them back."""
    require(len(value) < 4096, "Login is too long. Update Brother.")
    saved_at = b"%d" % int(CLOCK())
    r = quiet(["set"] + identity(), value + SAVED_AT + saved_at + b"\n")
    require(r.returncode != FOREIGN, OLD_ITEM)
    require(r.returncode == 0, "Login was not confirmed saved. Unlock Keychain and try again.")
    token, back = read_item()
    require(hmac.compare_digest(token, value) and back == saved_at,
            "Login verification failed. Run brother-login again.")


def extract(raw):
    """The one token in a finished transcript. Two different tokens, or none, is a refusal: nothing is saved."""
    plain = ANSI.sub(b"", raw)
    found = set(re.findall(rb"(?<![A-Za-z0-9_-])(sk-ant-oat01-[A-Za-z0-9_-]{20,2048})(?![A-Za-z0-9_-])", plain))
    require(len(found) == 1, "Login output was not recognized. Update Brother and try again.")
    return found.pop()


def capture(tty=None, deadline_s=SIGN_IN_S):
    """Run `claude setup-token` in a private pty, relay the person's typing to it with echo off, and return the token
    bytes after the child has exited 0. tty: the person's terminal fd (default /dev/tty; tests hand in a pty slave)."""
    program = claude_program()
    require(program is not None, "Claude Code is missing. Install it, then retry.")
    own = tty is None
    if own:
        require(sys.stdin.isatty() and sys.stdout.isatty(), "Open Terminal and run brother-login there.")
        tty = os.open("/dev/tty", os.O_RDWR)
    old = termios.tcgetattr(tty)
    hidden = old[:]
    hidden[3] &= ~(termios.ECHO | termios.ECHONL)
    pid = master = None
    reaped = False
    raw = bytearray()
    try:
        termios.tcsetattr(tty, termios.TCSANOW, hidden)
        print("Sign in in the browser. If it gives you a code, paste it here")
        print("and press Return. Typing stays hidden. Ctrl-C cancels.", flush=True)
        pid, master = pty.fork()
        if pid == 0:
            try:
                os.close(tty)
                fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 4096, 0, 0))   # no wrapping of the token
                os.chdir(HOME)
                os.execve(program, [program, "setup-token"], base_env())
            except BaseException:
                os._exit(127)
        deadline = time.monotonic() + deadline_s
        eof = False
        while not eof:
            require(time.monotonic() < deadline, "Sign-in timed out. Check your browser and connection, then retry.")
            ready, _, _ = select.select([master, tty], [], [], 0.2)
            if master in ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError as exc:
                    if exc.errno != errno.EIO:
                        raise
                    chunk = b""
                if not chunk:
                    eof = True
                raw.extend(chunk)
                require(len(raw) <= 2_000_000, "Unexpected login output. Update Brother and try again.")
            if tty in ready and not eof:
                try:
                    line = os.read(tty, 4096)
                except OSError:
                    line = b""
                require(line, "Sign-in cancelled.")
                os.write(master, line.replace(b"\n", b"\r"))
        while True:
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                reaped = True
                break
            require(time.monotonic() < deadline, "Sign-in did not finish. Try again.")
            time.sleep(0.05)
        require(os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0,
                "Sign-in failed. Check your connection and Claude account, then retry.")
        return extract(bytes(raw))
    finally:
        if pid and not reaped:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:  # sbe: allow-silent the group is already gone, which is the state this cleanup wants
                pass
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:  # sbe: allow-silent already reaped, which is the state this cleanup wants
                pass
        if master is not None:
            os.close(master)
        termios.tcsetattr(tty, termios.TCSAFLUSH, old)
        if own:
            os.close(tty)


def forget():
    r = quiet(["delete"] + identity())
    require(r.returncode in (0, NOT_FOUND), "Could not remove login. Unlock Keychain.")
    r = quiet(["get"] + identity())
    require(r.returncode == NOT_FOUND, "Removal could not be verified.")
    print("Local login removed. Stop running loops and revoke it in Claude settings.")


def status():
    """Whether a well formed login is saved, read through the same reader a seat uses. The value is dropped unread by
    anyone: never printed, measured or logged. Proves the item exists and is shaped like a Claude token, not that it is
    valid or unrevoked. A Failure carries the login lane's own remedy (brother-login), never the interactive sign in.
    Then the age line (age_line, 1.1.6 S4): an unknown age is NO-DATA at exit 0; a lapsed login is a Failure."""
    _, saved_at = read_item()
    print("Brother login is present and well formed in your login Keychain.")
    line = age_line(saved_at, CLOCK(), LIFETIME_S, WARN_S)
    require("lapsed" not in line, line)
    print(line)


def _interrupted(signum, frame):
    raise KeyboardInterrupt


def main(argv, tty=None, deadline_s=SIGN_IN_S):
    """The entry point: 0 on a verified save or removal, the held command's own exit status for hold, 1 on any
    refusal, 130 when cancelled. Every failure prints a
    fixed sentence: no traceback, no child output, no token."""
    try:
        signal.signal(signal.SIGTERM, _interrupted)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        if argv == ["forget"]:
            forget()
        elif argv == ["status"]:
            status()
        elif argv[:1] == ["hold"]:
            require(len(argv) >= 5 and argv[1] == "--run" and argv[3] == "--", "Use brother-login hold --run DIR -- COMMAND.")
            return hold(argv[2], argv[4:])
        else:
            require(argv == [], "Use brother-login, brother-login status, brother-login forget or "
                                "brother-login hold --run DIR -- COMMAND.")
            build_reader()
            save(capture(tty, deadline_s))
            print("Brother login saved and verified in your login Keychain.")
        return 0
    except Failure as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        if argv[:1] == ["hold"]:   # a stopped holder writes into its driver's log: say what ended (S2 round 1)
            print("brother-login hold: stopped; the run's login is no longer held.", file=sys.stderr)
        else:
            print("Cancelled. No successful change confirmed.", file=sys.stderr)
        return 130
    except Exception:   # sbe: allow-silent a traceback could carry the transcript; the fixed sentence is the whole report
        print("Brother login failed. Retry or contact support.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
