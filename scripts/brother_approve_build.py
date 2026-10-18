#!/usr/bin/env python3
"""brother-approve build: compile the cut's approval verifier, sign it hardened, install it, and enroll once.

    python3 scripts/brother_approve_build.py            build and install ~/.claude/bin/brother-approve
    python3 scripts/brother_approve_build.py --enroll   the same, then enroll: confirm with Touch ID or your login
                                                        password, scan the QR with your phone, and the key id and the
                                                        verifier's sha256 are written to docs/plan/APPROVER-KEY.json

WHAT. scripts/loop/brother_approve.c is compiled with a fresh random salt (an unsalted ad hoc build is reproducible
byte for byte, and its source ships in every worktree: brother_login.py build_reader, review 2026-10-03 F2), signed
`codesign -s - -o runtime` (the hardened runtime refuses DYLD_INSERT_LIBRARIES and other code injection, so nothing
can run inside the one program the Keychain item trusts), and installed with an atomic rename over any file there.

REBUILDING CHANGES THE IDENTITY. The Keychain trusts one build, and docs/plan/APPROVER-KEY.json pins its sha256
(scripts/cut.py refuses any other verifier file, CY1.c F5). After a rebuild the old item no longer opens (the
verifier refuses, it never prompts) and enroll refuses while the item exists: remove the item in Keychain Access
(brother-approve, your account) and enroll again; the new pin then reaches hub/main through a pull request you merge.

TEST BUILD (test=True): -DBROTHER_APPROVE_TEST compiles in the keychain argument and the presence seam the test
drives; a production build (main, the default) has neither, so it refuses a keychain argument.

Trusted helper: run it in Terminal yourself, never through an agent tool. Standard library plus the Xcode command
line tools. Test: python3 -B scripts/loop/test_brother_approve.py (a test keychain the test creates; the login keychain
is never touched).
"""
import hashlib
import json
import os
import pwd
import re
import secrets
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HERE, "loop", "brother_approve.c")
TARGET = os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".claude", "bin", "brother-approve")
KEY_PATH = os.path.join(os.path.dirname(HERE), "docs", "plan", "APPROVER-KEY.json")
LINK = ["-framework", "Security", "-framework", "CoreFoundation", "-framework", "LocalAuthentication", "-lobjc"]


class Failure(Exception):
    """A plain message for the person at the keyboard."""


def _env():
    user = pwd.getpwuid(os.getuid())
    env = {"HOME": user.pw_dir, "USER": user.pw_name, "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "en_US.UTF-8"}
    if os.environ.get("TMPDIR"):   # clang's temporary files: a sandbox allows only its own TMPDIR (CY1.c F7)
        env["TMPDIR"] = os.environ["TMPDIR"]
    return env


def build(target, source, salt, test=False):
    """Compile source with BROTHER_BUILD_SALT=salt, sign it ad hoc with the hardened runtime, mode 0700, and rename it
    over target. test=True adds -DBROTHER_APPROVE_TEST (a test keychain argument and the presence seam); never for an
    install. Any failure raises Failure and leaves nothing new at target (an existing file there stays as it was)."""
    if not salt or not all(c in "0123456789abcdef" for c in salt):
        raise Failure("The build salt must be lowercase hex.")
    if not os.path.isfile(source):
        raise Failure("The verifier source is missing: %s" % source)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".build-%d" % os.getpid()
    try:
        for argv in (["xcrun", "--sdk", "macosx", "clang", "-O2", "-Wno-deprecated-declarations",
                      '-DBROTHER_BUILD_SALT="%s"' % salt] + (["-DBROTHER_APPROVE_TEST"] if test is True else [])
                     + LINK + ["-o", tmp, source],
                     ["codesign", "-s", "-", "-o", "runtime", tmp]):
            r = subprocess.run(argv, capture_output=True, env=_env(), timeout=300, check=False)
            if r.returncode != 0:
                raise Failure("Could not build brother-approve (%s exit %d). Install the Xcode command line tools "
                              "(xcode-select --install) and try again." % (argv[0], r.returncode))
        os.chmod(tmp, 0o700)
        os.rename(tmp, target)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Failure("Could not install brother-approve at %s (%s)." % (target, type(exc).__name__)) from None
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return target


def enroll(binary, tty_in, tty_out):
    """Run `binary enroll` in this terminal. Refuses unless both stdin and stdout are a terminal: the seed is shown
    once, on /dev/tty, and an agent tool's pipe must never be where it lands. Returns the verifier's exit code."""
    if not (tty_in and tty_out):
        print("Enrollment runs only in Terminal, typed by you: python3 scripts/brother_approve_build.py --enroll",
              file=sys.stderr)
        return 6
    return subprocess.run([binary, "enroll"], check=False).returncode


def write_pin(binary, key_path, runner=subprocess.run):
    """After an enrollment: read the key id from `binary status` (no secret) and the sha256 of the binary file, and
    write both to key_path as {"key_id", "verifier_sha256"} through a temporary file. Returns (key_id, sha256). Any
    failure raises Failure and leaves key_path as it was."""
    try:
        r = runner([binary, "status"], capture_output=True, text=True, timeout=60, check=False)
        with open(binary, "rb") as fh:
            sha = hashlib.sha256(fh.read()).hexdigest()
    except (OSError, subprocess.SubprocessError) as exc:
        raise Failure("Could not read the enrolled key id (%s)." % type(exc).__name__) from None
    m = re.fullmatch(r"enrolled key_id=([0-9a-f]{16}) last_step=\d+ failures=\d+ locked_until=\d+\n", r.stdout or "")
    if r.returncode != 0 or not m:
        raise Failure("Could not read the enrolled key id (status exit %d)." % r.returncode)
    tmp = key_path + ".tmp-%d" % os.getpid()
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"key_id": m.group(1), "verifier_sha256": sha}, indent=2) + "\n")
        os.rename(tmp, key_path)
    except OSError as exc:
        raise Failure("Could not write %s (%s)." % (key_path, type(exc).__name__)) from None
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return m.group(1), sha


def main(argv):
    if argv not in ([], ["--enroll"]):
        print("usage: brother_approve_build.py [--enroll]", file=sys.stderr)
        return 2
    try:
        build(TARGET, SOURCE, secrets.token_hex(16))
    except Failure as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("Installed %s (hardened runtime, fresh identity)." % TARGET)
    if argv != ["--enroll"]:
        return 0
    rc = enroll(TARGET, sys.stdin.isatty(), sys.stdout.isatty())
    if rc != 0:
        return rc
    try:
        key_id, sha = write_pin(TARGET, KEY_PATH)
    except Failure as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("Enrolled. Key id %s and verifier %s written to %s. Tell the session \"enrolled\": it opens the pull request,"
          " you check the key id above is the one in it, and you merge it." % (key_id, sha[:16], KEY_PATH))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
