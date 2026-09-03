"""bm_verify: run a unit's own done_check and say PASS, FAIL or NO-DATA.

W9.2. Searching bm_controller.py for a function named verify or repair returns
nothing: the engine records dispatches and results, and whether a result was any
good is decided somewhere outside it, by whoever is reading. This is that
decision, made mechanical.

THE THREE VERDICTS, and why the third one is not a convenience:

  PASS      the unit's own declared done_check ran and exited 0.
  FAIL      it ran and exited non-zero.
  NO-DATA   it did not run, or there was nothing to run.

NO-DATA IS NEVER A PASS AND NEVER A FAILURE. That is the whole reason it exists
as a third word rather than being folded into either neighbour. Folded into
PASS, a unit with no done_check closes green and the board counts work nobody
checked. Folded into FAIL, a missing interpreter reads as broken code and the
repair loop starts rewriting something that was never wrong. Both are worse than
saying plainly that nothing was measured.

WHAT COUNTS AS NOT RUN, listed rather than inferred, because a verifier that
guesses at this is the thing that produces the two bad foldings above:

  * the unit declares no done_check, or declares a blank one
  * the shell could not find the command at all (exit 127)
  * the shell found it and could not execute it (exit 126)
  * the runner itself raised

Everything else ran. A check that exits 3 ran and failed; a check that prints
nothing and exits 0 ran and passed, because plenty of good checks are silent and
treating silence as absence would make NO-DATA the usual answer.

THE VERDICT IS READ FROM THE EXIT CODE, never from what the check printed. A
check that prints the word PASS and exits 1 has failed. This estate has been
bitten by the opposite reading, and an exit code is the one part of a check's
output that a confused script cannot fake by accident.

Python 3.9, standard library only. No network.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_controller  # noqa: E402


PASS = "PASS"
FAIL = "FAIL"
NO_DATA = "NO-DATA"

#: POSIX shells use 127 for "command not found" and 126 for "found but not
#: executable". Both mean the check never ran, which is NO-DATA and not a
#: failing unit. Named rather than written as bare integers so the next reader
#: does not have to look them up to know why they are special.
EXIT_COMMAND_NOT_FOUND = 127
EXIT_COMMAND_NOT_EXECUTABLE = 126


def _verdict(verdict, reason, command="", exit_code=None, stdout="", stderr=""):
    """One shape for all three verdicts, so a caller never has to know which
    branch produced the answer before it can read it."""
    return {"verdict": verdict, "reason": reason, "command": command,
            "exit_code": exit_code,
            "stdout": (stdout or "")[:4000], "stderr": (stderr or "")[:4000]}


def verify(unit, cwd=None, runner=None):
    """Decide one unit. `unit` is any mapping carrying a done_check.

    `runner` is the CheckRunner contract already defined in bm_controller:
    run(command, cwd) -> {"exit_code": int, "stdout": str, "stderr": str}. The
    default is that module's SubprocessCheckRunner, which is the ONE place this
    estate runs a founder-authored command and which strips the git redirection
    variables before anything starts. Reusing it rather than calling subprocess
    here keeps that guarantee in one place.
    """
    command = str((unit or {}).get("done_check") or "").strip()
    if not command:
        return _verdict(NO_DATA,
                        "the unit declares no done_check, so nothing was "
                        "measured. This is not a pass: a unit that cannot say "
                        "how it would be proven has not been proven.")

    runner = runner or bm_controller.SubprocessCheckRunner()
    try:
        outcome = runner.run(command, cwd)
    except Exception as exc:  # noqa: BLE001
        # Deliberately broad: a runner is injected, and ANY failure of the
        # runner itself is the check not running, which is NO-DATA. Narrowing
        # this would let an unexpected runner error surface as a FAIL, which is
        # the exact misreading this module exists to prevent.
        # sbe: allow-silent the exception is not swallowed, it becomes the reason
        return _verdict(NO_DATA,
                        "the check runner itself failed (%s: %s), so the check "
                        "never ran" % (type(exc).__name__, exc),
                        command=command)

    code = outcome.get("exit_code")
    out, err = outcome.get("stdout", ""), outcome.get("stderr", "")

    if code == EXIT_COMMAND_NOT_FOUND:
        return _verdict(NO_DATA,
                        "exit 127, the shell could not find the command, so the "
                        "check never ran. A missing tool is not a failing unit.",
                        command, code, out, err)
    if code == EXIT_COMMAND_NOT_EXECUTABLE:
        return _verdict(NO_DATA,
                        "exit 126, the command was found but could not be "
                        "executed, so the check never ran.",
                        command, code, out, err)
    if code == 0:
        return _verdict(PASS, "the done_check ran and exited 0",
                        command, code, out, err)
    return _verdict(FAIL, "the done_check ran and exited %s" % code,
                    command, code, out, err)


def is_pass(result):
    """PASS and only PASS. Written as a function so no caller reaches for a
    truthiness test on the verdict string, under which NO-DATA is also truthy
    and therefore silently a pass."""
    return (result or {}).get("verdict") == PASS


def blocks_close(result):
    """Whether this verdict may close a unit. Neither FAIL nor NO-DATA may.

    The pair with is_pass() is the point: there is no third helper that means
    "good enough", because the moment one exists NO-DATA starts flowing through
    it."""
    return not is_pass(result)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--selftest":
        cases = [
            ({"done_check": "exit 0"}, PASS),
            ({"done_check": "exit 3"}, FAIL),
            ({"done_check": ""}, NO_DATA),
            ({"done_check": "definitely-not-a-real-command-xyz"}, NO_DATA),
        ]
        bad = []
        for unit, want in cases:
            got = verify(unit)["verdict"]
            if got != want:
                bad.append((unit.get("done_check"), want, got))
        if bad:
            for c, want, got in bad:
                print("bm_verify: FAIL %r wanted %s got %s" % (c, want, got),
                      file=sys.stderr)
            return 1
        print("bm_verify: OK, all four verdict paths behave "
              "(PASS, FAIL, and NO-DATA for both nothing-to-run and "
              "could-not-run)")
        return 0
    if len(args) == 2 and args[0] == "--check":
        print(json.dumps(verify({"done_check": args[1]}), sort_keys=True))
        return 0
    print("usage: bm_verify.py --selftest | --check '<command>'",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
