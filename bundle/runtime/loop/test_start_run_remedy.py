#!/usr/bin/env python3
"""The two remedy lines of scripts/loop/start_run.sh (M40.b, 2026-10-16), tested through the launcher itself.

WHY. On 10-14 two launches were refused on battery with the remedy at the END of a long sentence, and six were refused
NO START after up to 51 s of intake because the pinned Claude program was signed out while another installed one was
signed in; nothing said which. Both refusals were right and stay exactly as they were. What changes is what is READ:
step 0 POWER puts "plug in the charger first" at the front of a battery refusal, and step 2 PINS names whether the
pinned program is signed in, which installed one is, and the remedy ("sign in to <path>" or "set
BROTHER_CLAUDE_BIN=<path>"). The sign in line only reports: it never refuses and never reads a program it cannot
read as signed in.

FIXTURE. test_start_run.py's own (a git launch worktree over a bare upstream, stub intake, driver and live check
through START_RUN_INTAKE, START_RUN_DRIVER, START_RUN_LIVE_CHECK, evidence under START_RUN_EVIDENCE), reused by
reference, not copied. PATH is narrowed to a stub folder, a python3 shim and the system folders, and HOME is a temp
folder, so no real claude program on this machine is ever asked.

Python 3.9 and 3.13, standard library only, no network.
"""
import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_start_run as TSR  # noqa: E402  the launcher fixture of record

STUB_CLAUDE = '''#!/bin/sh
echo "$0 $*" >> "$TEST_EVD/auth-calls.txt"
[ "$1 $2" = "auth status" ] || exit 9
printf '{"loggedIn": %s, "authMethod": "stub"}\\n' "%s"
[ "%s" = true ]
'''


class StartRunRemedy(unittest.TestCase):
    # test_start_run.StartRunTest's fixture and helpers, by reference (its test methods are not inherited)
    setUp = TSR.StartRunTest.setUp
    _git_env = TSR.StartRunTest._git_env
    git = TSR.StartRunTest.git
    _env = TSR.StartRunTest._env
    run_launcher = TSR.StartRunTest.run_launcher
    assertRefused = TSR.StartRunTest.assertRefused
    go = TSR.StartRunTest.go

    def claude(self, folder, signed_in):
        path = os.path.join(self.tmp, folder, "claude")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        state = "true" if signed_in else "false"
        TSR._write(path, STUB_CLAUDE % ("%s", state, state), executable=True)
        return path

    def narrow_path(self, *first):
        shim = os.path.join(self.tmp, "shim")
        os.makedirs(shim, exist_ok=True)
        os.symlink(os.path.realpath(shutil.which("python3")), os.path.join(shim, "python3"))
        return os.pathsep.join(list(first) + [shim, "/usr/bin", "/bin", "/usr/sbin", "/sbin"])

    def auth_calls(self):
        try:
            with open(os.path.join(self.evd, "auth-calls.txt")) as fh:
                return fh.read().splitlines()
        except OSError:
            return []

    def signin_line(self, out):
        lines = [l for l in out.splitlines() if " signin " in l]
        self.assertEqual(len(lines), 1, out)
        self.assertLess(out.index("OK   pins   BROTHER_CODEX_BIN"), out.index(lines[0]))
        self.assertLess(out.index(lines[0]), out.index("controls"))
        return lines[0]

    # L12: step 0 POWER -------------------------------------------------------------------------------------------
    def test_a_battery_refusal_reads_the_remedy_first(self):
        result = self.go("--dry", BROTHER_POWER_SOURCE="Battery Power")
        self.assertRefused(result, "power", "START REFUSED [power]: plug in the charger first: the machine draws from battery")
        self.assertNotIn("OK   tree", result[1])

    def test_an_unreadable_source_is_refused_as_before_without_the_battery_remedy(self):
        result = self.go("--dry", BROTHER_POWER_SOURCE="UPS Power")
        self.assertRefused(result, "power", "START REFUSED [power]: the power source is unreadable")
        self.assertNotIn("plug in the charger first", result[1])

    def test_ac_starts_as_before(self):
        rc, out = self.go("--dry")
        self.assertEqual(rc, 0, out)
        self.assertIn("OK   power  on AC", out)

    # L11: step 2 PINS --------------------------------------------------------------------------------------------
    def test_a_signed_out_pin_names_the_signed_in_sibling_and_both_remedies(self):
        pinned = self.claude("pinned", False)
        sibling = self.claude("sibling", True)
        rc, out = self.go("--dry", BROTHER_CLAUDE_BIN=pinned, PATH=self.narrow_path(os.path.dirname(sibling)))
        self.assertEqual(rc, 0, out)   # reporting only: the launch goes on, the intake's proof stays the refusal
        self.assertEqual(self.signin_line(out),
                         "WARN signin BROTHER_CLAUDE_BIN=%s is not signed in; %s is: sign in to %s, or set "
                         "BROTHER_CLAUDE_BIN=%s" % (pinned, sibling, pinned, sibling))
        self.assertIn("DRY  would prepare the intake", out)

    def test_a_signed_out_pin_with_no_signed_in_sibling_says_so(self):
        pinned = self.claude("pinned", False)
        other = self.claude("other", False)
        # the pin is on PATH too: found again among the installed programs, it is never asked a second time
        rc, out = self.go("--dry", BROTHER_CLAUDE_BIN=pinned,
                          PATH=self.narrow_path(os.path.dirname(pinned), os.path.dirname(other)))
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.signin_line(out),
                         "WARN signin BROTHER_CLAUDE_BIN=%s is not signed in, and no other installed claude program "
                         "reads signed in: sign in to %s" % (pinned, pinned))
        self.assertEqual(sorted(c.split()[0] for c in self.auth_calls()), sorted([other, pinned]))

    def test_a_signed_in_pin_says_so_and_asks_no_other_program(self):
        pinned = self.claude("pinned", True)
        sibling = self.claude("sibling", True)
        rc, out = self.go("--dry", BROTHER_CLAUDE_BIN=pinned, PATH=self.narrow_path(os.path.dirname(sibling)))
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.signin_line(out), "OK   signin BROTHER_CLAUDE_BIN=%s is signed in" % pinned)
        self.assertEqual([c.split()[0] for c in self.auth_calls()], [pinned])

    def test_a_pin_that_cannot_say_is_never_read_as_signed_in(self):
        rc, out = self.go("--dry", PATH=self.narrow_path())   # the fixture's pin exits 0 and prints nothing
        self.assertEqual(rc, 0, out)
        line = self.signin_line(out)
        self.assertTrue(line.startswith("WARN signin could not tell whether BROTHER_CLAUDE_BIN=%s is signed in" % self.bin), line)
        self.assertNotIn("is signed in;", line)

    def test_the_loop_token_lane_asks_no_program(self):
        pinned = self.claude("pinned", False)
        rc, out = self.go("--dry", BROTHER_CLAUDE_BIN=pinned, BROTHER_LOOP_TOKEN="on", PATH=self.narrow_path())
        self.assertEqual(rc, 0, out)
        self.assertTrue(self.signin_line(out).startswith("OK   signin BROTHER_LOOP_TOKEN=on"), out)
        self.assertEqual(self.auth_calls(), [])

    def test_the_pins_refusal_is_unchanged(self):
        plain = os.path.join(self.tmp, "not-a-program")
        TSR._write(plain, "x\n")
        self.assertRefused(self.go(BROTHER_CLAUDE_BIN=plain), "pins", "not an executable file")


if __name__ == "__main__":
    unittest.main()
