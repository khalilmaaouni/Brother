#!/usr/bin/env python3
"""FX-06.3: the runflow word, and the decisions the driver's block builds on it.

The block this module serves sits in scripts/loop/loop_until.sh, right after
make_run_dir. It asks scripts/loop/run_ledger.py for `mode` exactly once, then
hands those bytes here: `plan` prints the word the driver tests and, when the
mode verb printed one, the single NOTE line an unknown value earns in the log.

Keeping the decisions here, as plain functions that read no environment and
write nothing, is what lets the suite feed them the exact bytes a mode verb
can print: shadow, on, an unknown value with its NOTE, and the empty answer of
a missing or broken reader. `records` is the guard the driver's begin and end
calls sit behind; it is False for off, so an off run creates no ledger.

Run as a tool: python3 -B scripts/loop/loop_runflow.py plan   (stdin: the mode
verb's stdout; stdout: the word, then the note line or an empty line).
"""
import sys

#: The only two words under which the driver records anything (R-FX-06-10).
RECORDING_WORDS = ("shadow", "on")


def _text(value, what):
    """One validation, reached by every public function that takes a value:
    a wrong type is a ValueError, never a crash and never a silent accept."""
    if not isinstance(value, str):
        raise ValueError("%s must be a str, not %s" % (what, type(value).__name__))
    return value


def resolve(mode_out):
    """The runflow word, from the mode verb's stdout.

    The mode verb prints the resolved word as its first line. Anything that is
    not shadow or on, including the empty output of a missing reader, reads
    off: the default behaviour of the loop, never an error."""
    text = _text(mode_out, "mode output")
    lines = text.splitlines()
    first = lines[0].strip().lower() if lines else ""
    return first if first in RECORDING_WORDS else "off"


def note(mode_out):
    """The NOTE the mode verb printed as its second line, or "" for none."""
    text = _text(mode_out, "mode output")
    lines = text.splitlines()
    if len(lines) > 1 and "NOTE" in lines[1]:
        return lines[1].strip()
    return ""


def note_line(mode_out):
    """The one line the driver appends to its log for an unknown value, "" when
    there is none: a recording word gets no line, and neither does a reader
    that printed nothing."""
    text = note(mode_out)
    return ("NOTE runflow: %s" % text) if text else ""


def records(runflow):
    """True only when the driver may write (R-FX-06-11 to R-FX-06-13)."""
    text = _text(runflow, "runflow")
    return text in RECORDING_WORDS


def end_state(failed):
    """The state the driver records for stage 05: FAILED when the run's end
    failed, DONE otherwise."""
    if not isinstance(failed, bool):
        raise ValueError("failed must be a bool, not %s" % type(failed).__name__)
    return "FAILED" if failed else "DONE"


def plan(mode_out):
    """Two lines for the driver: the word, then the note line or an empty
    line. The driver takes line one as RUNFLOW and line two as its log line."""
    return "%s\n%s" % (resolve(mode_out), note_line(mode_out))


def _main(argv):
    if list(argv) != ["plan"]:
        print("usage: loop_runflow.py plan   (reads the mode verb's stdout on stdin)")
        return 2
    sys.stdout.write(plan(sys.stdin.read()) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
