#!/usr/bin/env python3
"""test_hp1_acceptance.py: the HP1 acceptance, the gate the 1.1.0 cut reads (spec docs/plan/specs/HP1.md: HP1.b, HP1.c,
HP1.d; the unit's done check in docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json).

Every case runs the unit's real checkers on the REAL tree: the verifier (host_live_verify.verify) on
docs/plan/evidence/HP1-host-live.jsonl, which only the owner run of HP1.d writes, never a builder; the owner steps
checker on docs/how-to/host-live-proof.md; the install page checker on the two install pages. A missing, incomplete or
stale evidence file is a FAILED test whose message is the verifier's own line, never a skip and never green: a check
that could pass without the live run is not a check (spec HP1.b).

Under 1.1.0 only the Claude Code and Codex rows decide (host_live_verify.DECIDING_HOSTS_1_1_0, owner decision
2026-10-03, docs/decisions/scope-1.1.0-defer-to-1.1.1-2026-10-03.json); the Antigravity rows are reported NO-DATA
(experimental, certification moved to 1.1.1) and never decide.

Exit code, the convention scripts/check_all.sh reads: 0 every case green; 2 when every failure is a NO-DATA line (the
evidence has not arrived: the battery counts NO-DATA, never a pass); 1 when any case is RED or errored. Starts no host
session and spends no model call: the only processes are the checkers' own safe listed commands and the driver's
--print-owner-commands, through the runners below.

Run: python3 scripts/test_hp1_acceptance.py -v
"""
import os
import subprocess
import sys
import time
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# THE CUT'S GATE ON THE REAL TREE (2026-10-04): its evidence and board live under docs/plan, and the public export does not ship the board,
# so there every case skips, stated (a skip is NO-DATA, never a pass), the way test_release_plan_live.py does.
SHIPPED = os.path.isfile(os.path.join(ROOT, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json"))   # the board, as test_release_plan_live keys it
sys.path.insert(0, HERE)
import host_doc_check as hdc  # noqa: E402
import host_live_verify as verifier  # noqa: E402

EVIDENCE = os.path.join("docs", "plan", "evidence", "HP1-host-live.jsonl")
NO_DATA = "NO-DATA"
COMMAND_TIMEOUT_S = 600


def exit_runner(argv, cwd, env):
    """check_pages' runner: the exit code of one safe listed command, or None (the checker reads NO-DATA, never 0)."""
    try:
        return subprocess.run(argv, cwd=cwd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=COMMAND_TIMEOUT_S).returncode
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def output_runner(argv, cwd, env):
    """check_owner_steps' runner: (exit code, output) of one runnable step, or (None, "") when it could not run."""
    try:
        done = subprocess.run(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=COMMAND_TIMEOUT_S)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, ""
    return done.returncode, done.stdout.decode("utf-8", "replace")


def label(refusals):
    """The failure message for a checker's refusal list: NO-DATA when every line is one, else FAIL naming them."""
    word = NO_DATA if all(NO_DATA in line for line in refusals) else "FAIL"
    return "%s: %d refusal(s):\n%s" % (word, len(refusals), "\n".join(refusals))


@unittest.skipUnless(SHIPPED, "the launch board is not shipped in this tree: the HP1 acceptance reads NO-DATA here")
class TestRecordedEvidence(unittest.TestCase):
    """HP1.b: the verifier on the real evidence file against the real checkout, the deciding hosts of 1.1.0."""
    longMessage = False

    def test_the_deciding_hosts_are_green_on_the_recorded_evidence(self):
        code, line = verifier.verify(os.path.join(ROOT, EVIDENCE), ROOT, int(time.time()), verifier.DECIDING_HOSTS_1_1_0)
        self.assertTrue(code == verifier.GREEN, line)
        self.assertTrue("antigravity: NO-DATA: experimental, certification moved to 1.1.1" in line,
                        "FAIL: the experimental host is not reported: " + line)


@unittest.skipUnless(SHIPPED, "the launch board is not shipped in this tree: the HP1 acceptance reads NO-DATA here")
class TestOwnerSteps(unittest.TestCase):
    """HP1.d: the owner hand steps page resolves, and its runnable steps print what the page expects."""
    longMessage = False

    def test_the_owner_page_resolves_and_its_runnable_steps_match(self):
        refusals = hdc.check_owner_steps(ROOT, hdc.OWNER_PAGE, runner=output_runner)
        self.assertTrue(not refusals, label(refusals))


@unittest.skipUnless(SHIPPED, "the launch board is not shipped in this tree: the HP1 acceptance reads NO-DATA here")
class TestRealHostDocs(unittest.TestCase):
    """HP1.c: the two install pages against the tree and the evidence rows."""
    longMessage = False

    def test_the_install_pages_match_the_tree_and_the_evidence(self):
        refusals = hdc.check_pages(ROOT, EVIDENCE, runner=exit_runner)
        self.assertTrue(not refusals, label(refusals))


def exit_code(result):
    """The gate's exit code for a unittest result: 0 green; 2 when every failure is a NO-DATA assertion and nothing
    errored (the evidence has not arrived); 1 for any RED failure, any error, or a result that is neither. Tested by
    scripts/test_host_live_verify.py (TestAcceptanceGate), outside this file, so a mapping that always answers 0 dies."""
    if result.wasSuccessful():
        return 0
    if result.errors or not result.failures:
        return 1
    return 2 if all("AssertionError: %s" % NO_DATA in text for _case, text in result.failures) else 1


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)
    return exit_code(unittest.main(module=__name__, argv=[sys.argv[0]] + args, exit=False).result)


if __name__ == "__main__":
    sys.exit(main())
