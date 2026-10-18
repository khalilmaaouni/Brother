#!/usr/bin/env python3
"""brother-approve (scripts/loop/brother_approve.c): only the owner's phone code makes an approval line, once.

The REAL verifier is built by scripts/brother_approve_build.py build(test=True) into a folder under TMPDIR and driven
as a binary against test keychains this file creates and deletes; the login keychain is never opened (the test build
refuses to run without a keychain argument). Codes are computed here by totp() from the seed the enrollment prints on
its terminal (a pty this test owns), exactly as a phone would. Owner presence at enrollment is driven through the test
build's seam (BROTHER_APPROVE_TEST_PRESENCE); without it the real LocalAuthentication call runs with every dialog
forbidden, so it answers no. The production build is built too, and only ever run with arguments it refuses.
ONE CONDITION PER FIXTURE: every refusal starts from a good enrolled keychain and breaks one thing, and every refusal
case carries its positive control, so a check that cannot fail shows up red.
NO-DATA, never an error and never a pass (CY1.c F7): inside the landing sandbox (scripts/loop/sandbox.sb denies the
Keychain daemon) every case that needs a test keychain is SKIPPED with the reason, and if a trivial C program cannot be
compiled there every case is skipped. A toolchain that compiles a trivial program but not the verifier is a FAILURE.
Run: python3 -B scripts/loop/test_brother_approve.py"""
import base64, hashlib, hmac, os, pty, pwd, re, secrets, shutil, struct, subprocess, sys, tempfile, threading, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import brother_approve_build as BAB  # noqa: E402

SOURCE = os.path.join(HERE, "brother_approve.c")
ACCOUNT = pwd.getpwuid(os.getuid()).pw_name
SHA = "0123456789abcdef0123456789abcdef01234567"
SHA2 = "fedcba9876543210fedcba9876543210fedcba98"
ROOT = BIN = PROD = None
NO_KEYCHAIN = ""   # set when this host forbids a test keychain: the cases that need one are NO-DATA
KEYCHAINS = []
PRESENT = {"BROTHER_APPROVE_TEST_PRESENCE": "confirmed"}


def totp(seed, step):
    mac = hmac.new(seed, struct.pack(">Q", step), hashlib.sha1).digest()
    off = mac[19] & 15
    return "%06d" % ((struct.unpack(">I", mac[off:off + 4])[0] & 0x7fffffff) % 1000000)


def make_test_keychain(folder):
    path = os.path.join(folder, "kc-%s.keychain" % secrets.token_hex(4))
    for argv in (["security", "create-keychain", "-p", "pw", path], ["security", "unlock-keychain", "-p", "pw", path]):
        subprocess.run(argv, check=True, capture_output=True, timeout=60)
    KEYCHAINS.append(path)
    return path


def need_keychain():
    if NO_KEYCHAIN:
        raise unittest.SkipTest(NO_KEYCHAIN)


def setUpModule():
    global ROOT, BIN, PROD, NO_KEYCHAIN
    ROOT = tempfile.mkdtemp(prefix="brother-approve-")   # under TMPDIR: the one folder a sandbox lets a suite write
    probe = os.path.join(ROOT, "probe.c")
    with open(probe, "w") as fh:
        fh.write("int main(void) { return 0; }\n")
    r = subprocess.run(["xcrun", "--sdk", "macosx", "clang", "-o", probe[:-2], probe], capture_output=True, text=True,
                       timeout=300)
    if r.returncode != 0:
        shutil.rmtree(ROOT, True)
        raise unittest.SkipTest("NO-DATA: this host cannot compile even a trivial C program (clang exit %d: %s); "
                                "nothing here was tested" % (r.returncode, (r.stderr or "").strip()[-200:]))
    BIN = BAB.build(os.path.join(ROOT, "bin", "brother-approve"), SOURCE, secrets.token_hex(16), test=True)
    PROD = BAB.build(os.path.join(ROOT, "prod", "brother-approve"), SOURCE, secrets.token_hex(16))
    try:
        make_test_keychain(ROOT)
    except subprocess.CalledProcessError as exc:
        NO_KEYCHAIN = ("NO-DATA: this host forbids a test keychain (security create-keychain exit %d: %s), as the "
                       "landing sandbox does; the cases that need one did not run" % (
                           exc.returncode, (exc.stderr or b"").decode("utf-8", "replace").strip()[-200:]))


def tearDownModule():
    bad = []
    for kc in KEYCHAINS:
        r = subprocess.run(["security", "delete-keychain", kc], capture_output=True, text=True)
        if r.returncode != 0 and os.path.exists(kc):
            bad.append(kc)
    shutil.rmtree(ROOT, True)
    if bad:
        raise AssertionError("test keychains not deleted: %s" % bad)


def run(*args, binary=None, env=None, timeout=60, input=None):
    return subprocess.run([binary or BIN] + [str(a) for a in args], capture_output=True, text=True, timeout=timeout, env=env,
                          input=input if input is not None else "")


def check(line, kc, **kw):
    """`check` with the line on stdin, exactly as given (no newline added): the verifier never reads it from argv."""
    return run("check", kc, input=line, **kw)


def on_a_pty(argv, pipes=False, env=None):
    """Run argv with a controlling terminal (a pty this test owns). pipes=True puts pipes on stdin and stdout while the
    terminal stays reachable at /dev/tty: only the program's own tty test can refuse it then. -> (exit, tty text)"""
    rin, win = os.pipe()
    rout, wout = os.pipe()
    pid, fd = pty.fork()
    if pid == 0:
        try:
            if pipes:
                os.dup2(rin, 0)
                os.dup2(wout, 1)
            os.execve(argv[0], argv, dict(os.environ, **(env or {})))
        finally:
            os._exit(127)
    os.close(rin); os.close(wout); os.close(win)
    out = b""
    while True:
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
    _, status = os.waitpid(pid, 0)
    os.close(fd); os.close(rout)
    return os.waitstatus_to_exitcode(status), out.decode("utf-8", "replace")


def enrolled():
    """-> (keychain, seed bytes, tty text) for a fresh keychain enrolled through the real enroll on a pty."""
    need_keychain()
    kc = make_test_keychain(ROOT)
    rc, text = on_a_pty([BIN, "enroll", kc], env=PRESENT)
    assert rc == 0, (rc, text[-400:])
    m = re.search(r"otpauth://totp/Brother:cut\?secret=([A-Z2-7]{32})&issuer=Brother&algorithm=SHA1&digits=6&period=30", text)
    assert m, text[-400:]
    return kc, base64.b32decode(m.group(1)), text


def status(kc):
    r = run("status", kc)
    m = re.fullmatch(r"enrolled key_id=([0-9a-f]{16}) last_step=(\d+) failures=(\d+) locked_until=(\d+)\n", r.stdout)
    return r.returncode, (m.groups() if m else None)


def settle(need=12):
    """Wait until at least `need` seconds remain in the current 30 s step, so one case stays inside one step."""
    while 30 - time.time() % 30 < need:
        time.sleep(0.5)
    return int(time.time()) // 30


def wrong(seed, step):
    good = {totp(seed, step), totp(seed, step - 1)}
    return next(c for c in ("%06d" % ((int(totp(seed, step)) + k) % 1000000) for k in range(1, 9)) if c not in good)


class Verifier(unittest.TestCase):
    def test_a_session_written_line_is_refused(self):
        """T1: a line nobody's verify printed is refused by check, whatever its shape; a real line is the control."""
        kc, seed, _ = enrolled()
        _, (kid, _, _, _) = status(kc)
        body = "cy1.approval.1 1.1.6 %s %d %s" % (SHA, int(time.time()) // 30, kid)
        forged = [body + " " + "0" * 64,                                                  # hand typed HMAC
                  body + " " + hmac.new(secrets.token_bytes(32), body.encode(), hashlib.sha256).hexdigest(),  # wrong key
                  "cy1.approval.1 1.1.6 %s" % SHA, "", "ok", body + " " + "0" * 64 + " extra"]
        for line in forged:
            self.assertIn(check(line, kc).returncode, (2, 3), line)
        step = settle(4)
        good = run("verify", "1.1.6", SHA, totp(seed, step), kc)
        self.assertEqual(good.returncode, 0, good.stderr)
        line = good.stdout.strip()
        self.assertEqual(check(line, kc).returncode, 0, "the control: a line verify printed is accepted")
        for hostile in (line + "\n", line + " ", " " + line, line.replace(" ", "  ", 1), line.replace(" ", "\t", 1), line.upper(),
                        line[:10] + "\x00" + line[10:], line + "\x00", line + "\n" + line, line + " " * 300):
            self.assertEqual(check(hostile, kc).returncode, 3, repr(hostile))
        # CY1.c F4: the line never travels on argv, where every process of the machine can read it
        self.assertEqual(run("check", line, kc).returncode, 2, "a line on argv is a usage error, never a check")
        self.assertEqual(run("check", kc, input="").returncode, 3, "nothing on stdin")

    def test_a_code_is_accepted_once(self):
        """T2, T3: the owner's code is accepted once; the same code again and the step before it are refused."""
        kc, seed, _ = enrolled()
        step = settle(12)
        r = run("verify", "1.1.6", SHA, totp(seed, step), kc)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"\Acy1\.approval\.1 1\.1\.6 %s %d [0-9a-f]{16} [0-9a-f]{64}\n\Z" % (SHA, step))
        self.assertEqual(status(kc)[1][1], str(step))
        self.assertEqual(run("verify", "1.1.6", SHA, totp(seed, step), kc).returncode, 3, "replay of the same code")
        self.assertEqual(run("verify", "1.1.6", SHA, totp(seed, step - 1), kc).returncode, 3, "the step before it")
        self.assertEqual(run("verify", "1.1.7", SHA2, totp(seed, step), kc).returncode, 3, "same code, other release")

    def test_the_window_is_the_current_and_previous_step(self):
        kc, seed, _ = enrolled()
        step = settle(12)
        self.assertEqual(run("verify", "1.1.6", SHA, totp(seed, step - 2), kc).returncode, 3, "two steps ago is stale")
        self.assertEqual(run("verify", "1.1.6", SHA, totp(seed, step + 1), kc).returncode, 3, "the next step is early")
        r = run("verify", "1.1.6", SHA, totp(seed, step - 1), kc)
        self.assertEqual(r.returncode, 0, "the previous step is inside the RFC 6238 window: " + r.stderr)
        self.assertEqual(run("verify", "1.1.6", SHA, totp(seed, step), kc).returncode, 0, "a later step still accepted")

    def test_the_line_binds_version_and_sha(self):
        """T5: change the version, the sha or the step in a real line and check refuses it."""
        kc, seed, _ = enrolled()
        step = settle(4)
        line = run("verify", "1.1.6", SHA, totp(seed, step), kc).stdout.strip()
        ok = check(line, kc)
        self.assertEqual((ok.returncode, ok.stdout.split()[:3]), (0, ["ok", "1.1.6", SHA]))
        parts = line.split(" ")
        for i, value in ((1, "1.1.7"), (2, SHA2), (3, str(step + 1)), (4, "0" * 16)):
            moved = parts[:i] + [value] + parts[i + 1:]
            self.assertEqual(check(" ".join(moved), kc).returncode, 3, (i, value))

    def test_enroll_refused_while_an_item_exists(self):
        """T6: a second enrollment never replaces the item (the pinned key id stays); another item's line is refused."""
        kc, seed, _ = enrolled()
        before = status(kc)
        rc, text = on_a_pty([BIN, "enroll", kc], env=PRESENT)
        self.assertEqual(rc, 7, text)
        self.assertNotIn("otpauth", text)
        self.assertEqual(status(kc), before)
        other, oseed, _ = enrolled()
        self.assertNotEqual(status(other)[1][0], before[1][0], "a second item has its own key id")
        step = settle(4)
        line = run("verify", "1.1.6", SHA, totp(oseed, step), other).stdout.strip()
        self.assertEqual(check(line, other).returncode, 0, "the control")
        self.assertEqual(check(line, kc).returncode, 3, "a line from another item")

    def test_lockout_and_serialized_failures(self):
        """T7: failures serialize on the home lock (2 s each inside it); 5 in a row lock out for an hour, and a correct
        code during the lockout is refused."""
        kc, seed, _ = enrolled()
        settle(25)
        step = int(time.time()) // 30
        bad = wrong(seed, step)
        res, t0 = [], time.monotonic()
        threads = [threading.Thread(target=lambda: res.append(run("verify", "1.1.6", SHA, bad, kc).returncode)) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertGreaterEqual(time.monotonic() - t0, 4.0, "two parallel failures took less than 4 s")
        self.assertEqual(res, [3, 3])
        self.assertEqual(status(kc)[1][2], "2")
        for _ in range(3):
            self.assertEqual(run("verify", "1.1.6", SHA, wrong(seed, int(time.time()) // 30), kc).returncode, 3)
        _, (_, _, fails, until) = status(kc)
        self.assertGreater(int(until), time.time() + 3500, "the fifth failure starts an hour of lockout")
        self.assertEqual(run("verify", "1.1.6", SHA, wrong(seed, int(time.time()) // 30), kc).returncode, 5, "sixth")
        now = int(time.time()) // 30
        self.assertEqual(run("verify", "1.1.6", SHA, totp(seed, now), kc).returncode, 5, "a correct code inside the lockout")

    def test_unreadable_state_refuses(self):
        """T8: a missing item, a corrupt one, one this program did not create and a locked keychain all exit non zero."""
        need_keychain()
        empty = make_test_keychain(ROOT)
        code = totp(b"x" * 20, int(time.time()) // 30)
        for args in (("status",), ("verify", "1.1.6", SHA, code)):
            self.assertEqual(run(*args, empty).returncode, 44, args)
        self.assertEqual(check("cy1.approval.1 1.1.6 %s 1 %s %s" % (SHA, "0" * 16, "0" * 64), empty).returncode, 44)
        for i, value in enumerate(("garbage", "cy1s1 " + "0" * 40 + " " + "0" * 64 + " 0 0", "cy1s1 " + "0" * 40 + " " + "0" * 64 + " 0 0 0 x")):
            kc = make_test_keychain(ROOT)
            subprocess.run(["security", "add-generic-password", "-s", "brother-approve", "-a", ACCOUNT, "-w", value, "-T", BIN, kc],
                           check=True, capture_output=True)
            self.assertEqual(run("status", kc).returncode, 1, value)
            self.assertEqual(run("verify", "1.1.6", SHA, code, kc).returncode, 1, value)
            self.assertEqual(on_a_pty([BIN, "enroll", kc], env=PRESENT)[0], 7, "a corrupt item is never replaced")
        foreign = make_test_keychain(ROOT)   # created by /usr/bin/security for itself: the verifier is not trusted
        subprocess.run(["security", "add-generic-password", "-s", "brother-approve", "-a", ACCOUNT, "-w", "x", foreign],
                       check=True, capture_output=True)
        self.assertEqual(run("status", foreign, timeout=30).returncode, 1)
        kc, seed, _ = enrolled()
        self.assertEqual(status(kc)[0], 0, "the control")
        subprocess.run(["security", "lock-keychain", kc], check=True, capture_output=True)
        try:
            self.assertEqual(run("status", kc, timeout=30).returncode, 1, "a locked keychain")
            self.assertEqual(run("verify", "1.1.6", SHA, totp(seed, int(time.time()) // 30), kc, timeout=30).returncode, 1)
        finally:
            subprocess.run(["security", "unlock-keychain", "-p", "pw", kc], check=True, capture_output=True)

    def test_hardened_runtime_flag(self):
        """T9: the built binary carries the runtime flag, and an injected library does not load into it (the same probe
        does load into an ad hoc build without the flag: the control that the probe can fire)."""
        need_keychain()
        for binary in (BIN, PROD):
            sig = subprocess.run(["codesign", "-dv", binary], capture_output=True, text=True).stderr
            self.assertRegex(sig, r"flags=0x\w+\([^)]*\bruntime\b[^)]*\)", binary)
        lib, src = os.path.join(ROOT, "probe.dylib"), os.path.join(ROOT, "probe.c")
        with open(src, "w") as fh:
            fh.write('#include <stdlib.h>\n#include <fcntl.h>\n#include <unistd.h>\n__attribute__((constructor)) static void hit(void) {'
                     ' const char *p = getenv("PROBE_MARK"); if (p) close(open(p, O_CREAT | O_WRONLY, 0600)); }\n')
        subprocess.run(["xcrun", "--sdk", "macosx", "clang", "-dynamiclib", "-o", lib, src], check=True, capture_output=True)
        plain = os.path.join(ROOT, "plain-approve")
        subprocess.run(["xcrun", "--sdk", "macosx", "clang", "-Wno-deprecated-declarations", '-DBROTHER_BUILD_SALT="00"',
                        "-DBROTHER_APPROVE_TEST"] + BAB.LINK + ["-o", plain, SOURCE], check=True, capture_output=True)
        subprocess.run(["codesign", "-f", "-s", "-", plain], check=True, capture_output=True)
        empty = make_test_keychain(ROOT)
        for binary, loads in ((plain, True), (BIN, False)):
            mark = os.path.join(ROOT, "mark-%s" % secrets.token_hex(4))
            env = dict(os.environ, DYLD_INSERT_LIBRARIES=lib, PROBE_MARK=mark)
            run("status", empty, binary=binary, env=env)
            self.assertEqual(os.path.exists(mark), loads, binary)

    def test_enroll_needs_a_tty(self):
        """T10: no export path (every other command exits 2); enroll on pipes refuses even with /dev/tty reachable."""
        need_keychain()
        kc = make_test_keychain(ROOT)
        rc, text = on_a_pty([BIN, "enroll", kc], pipes=True, env=PRESENT)
        self.assertEqual(rc, 6, text)
        self.assertNotIn("otpauth", text)
        self.assertEqual(run("status", kc).returncode, 44, "nothing was enrolled")
        ran = os.path.join(ROOT, "ran-%s" % secrets.token_hex(4))
        fake = os.path.join(ROOT, "fake-approve")
        with open(fake, "w") as fh:
            fh.write("#!/bin/sh\ntouch '%s'\n" % ran)
        os.chmod(fake, 0o700)
        self.assertEqual(BAB.enroll(fake, False, True), 6)
        self.assertEqual(BAB.enroll(fake, True, False), 6)
        self.assertFalse(os.path.exists(ran), "the helper ran enroll without a terminal")
        self.assertEqual(BAB.enroll(fake, True, True), 0, "the control: with a terminal it runs")
        self.assertTrue(os.path.exists(ran))
        for cmd in (["get"], ["show"], ["export"], ["read"], ["dump"], ["seed"], [], ["status", kc, "x"]):
            self.assertEqual(run(*cmd).returncode, 2, cmd)
        for version, sha, code in (("1.1.6", SHA, "1234567"), ("1.1.6", SHA, "12a456"), ("1.1.6", SHA[:39], "123456"),
                                   ("1.1 6", SHA, "123456"), ("", SHA, "123456"), ("1.1.6", "", "123456"),
                                   ("1.1.6", SHA, ""), ("1.1.6", SHA.upper(), "123456"), ("v1.1.6", SHA, "123456")):
            self.assertEqual(run("verify", version, sha, code, kc).returncode, 2, (version, sha, code))

    def test_enroll_needs_owner_presence(self):
        """CY1.c F1: a terminal is not the owner. On a pty this test owns (what a session can open), enroll refuses
        with exit 8 and writes nothing unless the owner is confirmed present; the real LocalAuthentication call (every
        dialog forbidden) answers no, as does the seam set to anything but confirmed. The control: confirmed enrolls."""
        need_keychain()
        for env in ({}, {"BROTHER_APPROVE_TEST_PRESENCE": "denied"}, {"BROTHER_APPROVE_TEST_PRESENCE": "CONFIRMED"}):
            kc = make_test_keychain(ROOT)
            rc, text = on_a_pty([BIN, "enroll", kc], env=env)
            self.assertEqual(rc, 8, (env, text))
            self.assertNotIn("otpauth", text)
            self.assertIn("Touch ID or your login password", text, "the owner is told what confirms it")
            self.assertEqual(run("status", kc).returncode, 44, "nothing was enrolled: %s" % env)
            rc, text = on_a_pty([BIN, "enroll", kc], env=PRESENT)
            self.assertEqual(rc, 0, "the control on the same keychain: %s" % text[-300:])
            self.assertIn("otpauth://", text)

    def test_the_production_build_has_no_test_seam(self):
        """CY1.c F2 and F1: the installed build refuses a keychain argument for every command (exit 2, before any
        keychain call), carries no presence seam, and links the real LocalAuthentication call."""
        for args in (("status", "/x.keychain"), ("enroll", "/x.keychain"), ("check", "/x.keychain"),
                     ("verify", "1.1.6", SHA, "123456", "/x.keychain")):
            self.assertEqual(run(*args, binary=PROD, input="x").returncode, 2, args)
        self.assertEqual(run("status", binary=BIN).returncode, 2, "the test build always names its own keychain")
        with open(PROD, "rb") as fh:
            prod = fh.read()
        with open(BIN, "rb") as fh:
            test = fh.read()
        self.assertIn(b"BROTHER_APPROVE_TEST_PRESENCE", test, "the control: the seam is visible in the test build")
        self.assertNotIn(b"BROTHER_APPROVE_TEST_PRESENCE", prod)
        for binary in (prod, test):
            self.assertIn(b"evaluatePolicy:localizedReason:reply:", binary)
        links = subprocess.run(["otool", "-L", PROD], capture_output=True, text=True, timeout=60).stdout
        self.assertIn("LocalAuthentication.framework", links)

    def test_the_qr_decodes_to_the_uri(self):
        """The QR the owner scans decodes (CoreImage, the same detector family a phone uses) to the printed URI."""
        _, _, text = enrolled()
        rows = []
        for line in text.splitlines():
            cells = re.findall(r"\x1b\[(\d+);(\d+)m▀", line)
            if cells:
                rows.append("".join("1" if f == "30" else "0" for f, _ in cells))
                rows.append("".join("1" if b == "40" else "0" for _, b in cells))
        self.assertEqual((len(rows), len(rows[0])), (50, 49))
        uri = re.search(r"otpauth://\S+", text).group(0)
        decoder = os.path.join(ROOT, "decode.swift")
        with open(decoder, "w") as fh:
            fh.write(DECODER)
        env = {k: v for k, v in os.environ.items() if k != "SDKROOT"}   # /usr/bin/python3 exports the CLT SDK
        r = subprocess.run(["xcrun", "--sdk", "macosx", "swift", decoder], input="\n".join(rows), capture_output=True,
                           text=True, timeout=300, env=env)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, uri), r.stderr[-400:])


class Build(unittest.TestCase):
    def test_the_pin_names_the_key_id_and_the_verifier_sha256(self):
        """After enrollment the helper pins the key id (from status, no secret) and the sha256 of the binary file."""
        import hashlib, json
        fake = os.path.join(ROOT, "pin-approve")
        with open(fake, "w") as fh:
            fh.write("#!/bin/sh\n[ \"$1\" = status ] && echo 'enrolled key_id=0123456789abcdef last_step=0 failures=0 "
                     "locked_until=0' && exit 0\nexit 2\n")
        os.chmod(fake, 0o700)
        key_path = os.path.join(ROOT, "pin.json")
        with open(fake, "rb") as fh:
            sha = hashlib.sha256(fh.read()).hexdigest()
        self.assertEqual(BAB.write_pin(fake, key_path), ("0123456789abcdef", sha))
        with open(key_path) as fh:
            self.assertEqual(json.load(fh), {"key_id": "0123456789abcdef", "verifier_sha256": sha})
        with open(fake, "w") as fh:
            fh.write("#!/bin/sh\necho 'enrolled key_id=zz'\n")
        with self.assertRaises(BAB.Failure):
            BAB.write_pin(fake, key_path)
        with open(key_path) as fh:
            self.assertEqual(json.load(fh)["verifier_sha256"], sha, "a failed read leaves the pin as it was")

    def test_a_failed_build_leaves_nothing_new_at_target(self):
        target = os.path.join(ROOT, "b1", "brother-approve")
        with self.assertRaises(BAB.Failure):
            BAB.build(target, os.path.join(ROOT, "missing.c"), "ab")
        self.assertFalse(os.path.exists(target))
        os.makedirs(os.path.dirname(target))
        broken = os.path.join(ROOT, "broken.c")
        with open(broken, "w") as fh:
            fh.write("int main(void) { return }\n")
        with open(target, "w") as fh:
            fh.write("old")
        with self.assertRaises(BAB.Failure):
            BAB.build(target, broken, "ab")
        with open(target) as fh:
            self.assertEqual(fh.read(), "old", "a failed build never replaces what is there")
        self.assertEqual(sorted(os.listdir(os.path.dirname(target))), ["brother-approve"], "no temp file left")
        with self.assertRaises(BAB.Failure):
            BAB.build(target, SOURCE, "not hex")

    def test_every_build_is_a_new_code_identity_and_unsalted_is_refused(self):
        second = BAB.build(os.path.join(ROOT, "b2", "brother-approve"), SOURCE, secrets.token_hex(16))
        self.assertEqual(os.stat(second).st_mode & 0o777, 0o700)

        def cdhash(p):
            return [l for l in subprocess.run(["codesign", "-dvvv", p], capture_output=True, text=True).stderr.splitlines()
                    if l.startswith("CDHash=")]
        self.assertNotEqual(cdhash(BIN), cdhash(second))
        r = subprocess.run(["xcrun", "--sdk", "macosx", "clang", "-fsyntax-only", SOURCE], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("BROTHER_BUILD_SALT", r.stderr)


DECODER = r'''import Foundation
import CoreImage
let rows = String(data: FileHandle.standardInput.readDataToEndOfFile(), encoding: .utf8)!.split(separator: "\n").map { Array($0) }
let s = 8, h = rows.count * s, w = rows[0].count * s
var px = [UInt8](repeating: 255, count: w * h)
for (y, r) in rows.enumerated() { for (x, c) in r.enumerated() where c == "1" {
  for dy in 0..<s { for dx in 0..<s { px[(y * s + dy) * w + x * s + dx] = 0 } } } }
let img = CGImage(width: w, height: h, bitsPerComponent: 8, bitsPerPixel: 8, bytesPerRow: w,
  space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGBitmapInfo(rawValue: 0),
  provider: CGDataProvider(data: Data(px) as CFData)!, decode: nil, shouldInterpolate: false, intent: .defaultIntent)!
let det = CIDetector(ofType: CIDetectorTypeQRCode, context: nil, options: [CIDetectorAccuracy: CIDetectorAccuracyHigh])!
for f in det.features(in: CIImage(cgImage: img)) { if let q = f as? CIQRCodeFeature { print(q.messageString ?? "") } }
'''

if __name__ == "__main__":
    unittest.main()
