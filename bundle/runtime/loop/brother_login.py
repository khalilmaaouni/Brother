#!/usr/bin/env python3
"""brother-login: one command that gives the loop a long-lived Claude login, and no agent ever sees the token.

    python3 scripts/loop/brother_login.py      sign in once; the token lands in your login Keychain
    python3 scripts/loop/brother_login.py forget   remove it again (then revoke it in Claude settings)

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
import os
import pty
import pwd
import re
import resource
import secrets
import select
import shutil
import signal
import struct
import subprocess
import sys
import termios
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


def read_saved():
    """The saved token, bytes, or Failure (missing, locked, malformed). Never printed by anyone."""
    r = quiet(["get"] + identity())
    require(r.returncode == 0, MISSING)
    value = r.stdout[:-1] if r.stdout.endswith(b"\n") else r.stdout
    require(TOKEN.fullmatch(value) is not None, "Saved login is not a Claude token. Run brother-login again.")
    return value


def with_login(scrubbed_env):
    """(env, fd) for one claude child: env is a COPY of scrubbed_env naming FD_VAR=fd and carrying no TOKEN_VAR (an
    inherited one included), and fd is the read end of a pipe holding the fresh Keychain token, its write end already
    closed, so it reads exactly once and then reads empty. The caller passes fd to that one child (pass_fds) and closes
    it after the spawn. The token is never in any environment, argv, file or log; os.environ is never touched."""
    value = read_saved()
    env = {k: v for k, v in scrubbed_env.items() if k != TOKEN_VAR}
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


def save(value):
    """Write the token as the one item through the reader (stdin only), then prove it by reading it back."""
    require(len(value) < 4096, "Login is too long. Update Brother.")
    r = quiet(["set"] + identity(), value + b"\n")
    require(r.returncode != FOREIGN, OLD_ITEM)
    require(r.returncode == 0, "Login was not confirmed saved. Unlock Keychain and try again.")
    require(hmac.compare_digest(read_saved(), value), "Login verification failed. Run brother-login again.")


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


def _interrupted(signum, frame):
    raise KeyboardInterrupt


def main(argv, tty=None, deadline_s=SIGN_IN_S):
    """The entry point: 0 on a verified save or removal, 1 on any refusal, 130 when cancelled. Every failure prints a
    fixed sentence: no traceback, no child output, no token."""
    try:
        signal.signal(signal.SIGTERM, _interrupted)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        if argv == ["forget"]:
            forget()
        else:
            require(argv == [], "Use brother-login or brother-login forget.")
            build_reader()
            save(capture(tty, deadline_s))
            print("Brother login saved and verified in your login Keychain.")
        return 0
    except Failure as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled. No successful change confirmed.", file=sys.stderr)
        return 130
    except Exception:   # sbe: allow-silent a traceback could carry the transcript; the fixed sentence is the whole report
        print("Brother login failed. Retry or contact support.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
