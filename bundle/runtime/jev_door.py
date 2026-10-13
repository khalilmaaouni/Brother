"""JW4.a: second opinions where you already work, the one entry the review
and deliver verbs call.

review: a failing check's output is reduced to an extract (at most
MAX_LINES lines of at most MAX_LINE_CHARS characters, the home folder as ~,
every absolute path by its last component), passed through the private
content gate, and asked as J065 (noul: does this read as an infra flake?).
deliver: a done note is reduced and gated the same way and asked as J102
(noul: does the done claim lack a quoted verification?). Only noul questions:
choice and score are quarantined (jev_calibration.QUARANTINED_QTYPES).

A shadow call returns no answer, so the door waits up to wait_s for the
worker (jev_seam.drain) and reads the one terminal attempt row for its own
attempt id (jev_outcome.terminal_rows). Every result is one line ending
"(recorded, not acted on)". Nothing here returns or changes a verdict or an
exit code: the caller's deterministic verdict is printed first and decides.

FAIL DIRECTION: empty or binary text, a gate refusal (a private term, a
missing or malformed term list, an oversized input), an off mode, an absent
registry, a call that was not dispatched and no answer in time each read
NO-DATA; nothing leaves the machine unless the gate passed. An answer that
is unsure, or a bridge that could not answer, reads "no opinion".

Footprint: one consult() call per use (one attempt row, one bridge call when
the mode is not off, bounded by the seams daily budget), a wait of at most
wait_s seconds, no file written by this module itself.

Python 3, standard library only.
"""
import argparse
import math
import os
import re
import sys
from typing import Callable, Optional, Sequence, Tuple

import coe_outside_gate
import jev_checks
import jev_outcome
import jev_seam

NOT_ACTED = "(recorded, not acted on)"
OK = "OK"
NO_DATA = "NO-DATA"
MAX_LINES = 200
MAX_LINE_CHARS = 400
FLAKE_ID = "J065"
DONE_ID = "J102"
_ABS_PATH_RE = re.compile(r"(?<![\w.])~?/[^\s'\"`:,;()<>\[\]{}|]*")
_ONE_LINE_RE = re.compile(r"\s+")


def reduce_text(text: str, max_lines: int = MAX_LINES) -> str:
    """The last max_lines lines (the failure is usually at the end), each cut
    to MAX_LINE_CHARS, home as ~, every absolute path by its basename."""
    if not isinstance(max_lines, int) or isinstance(max_lines, bool) or max_lines < 1:
        max_lines = MAX_LINES
    for home in sorted({os.path.expanduser("~"), os.path.realpath(os.path.expanduser("~"))},
                       key=len, reverse=True):
        home = home.rstrip("/")
        if home:
            text = text.replace(home, "~")
    lines = [line[:MAX_LINE_CHARS] for line in text.splitlines()[-max_lines:]]
    return "\n".join(_ABS_PATH_RE.sub(_basename, line) for line in lines)


def _basename(match) -> str:
    token = match.group(0)
    return os.path.basename(token.rstrip("/")) or token


def _wait(wait_s) -> float:
    try:
        value = float(wait_s)
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) and value > 0 else 0.0


def _ask(entry_id: str, text, ask: Callable, *, max_lines, wait_s, gate_terms, ledger_dir,
         seams_config, registry, runner) -> Tuple[str, Optional[dict], str]:
    """(state, answered row or None, reason): the five steps both doors share."""
    if not isinstance(text, str) or not text.strip() or "\x00" in text:
        return NO_DATA, None, "nothing to ask: the text is empty or binary"
    reduced = reduce_text(text, max_lines)
    gate = coe_outside_gate.check(reduced, terms=gate_terms)
    if not gate.allowed:
        return NO_DATA, None, "not asked: %s" % gate.reason
    config = seams_config if seams_config is not None else jev_seam.load_seams_config()
    if jev_seam.resolve_mode(config, entry_id) == jev_seam.OFF:
        return NO_DATA, None, "not asked: %s is off in the Jev seams config" % entry_id
    if registry is None:
        registry, why = jev_seam.registry_or_nodata()
        if registry is None:
            return NO_DATA, None, why
    ledger = ledger_dir or jev_seam.DEFAULT_LEDGER_DIR
    result = ask(reduced, seams_config=config, registry=registry, ledger_dir=ledger,
                 runner=runner)
    attempt_id = getattr(result, "attempt_id", "")
    if getattr(result, "mode", jev_seam.OFF) == jev_seam.OFF or not attempt_id:
        return NO_DATA, None, "not asked: %s" % (getattr(result, "reason", None) or "the call was not made")
    wait = _wait(wait_s)
    jev_seam.drain(timeout=wait)
    row = jev_outcome.terminal_rows(jev_outcome.attempts_path(ledger), {attempt_id}).get(attempt_id)
    if row is None:
        return NO_DATA, None, "no answer within %g s; it is recorded if it lands" % wait
    if row.get("phase") != "answered":
        return OK, None, "no opinion: Jev was unsure or could not answer (%s)" % row.get("phase")
    return OK, row, ""


def _said(row: Optional[dict], reason: str):
    """(said, prob, reason) from an answered row; an unsure one becomes no opinion."""
    if row is None:
        return None, None, reason
    prob = row.get("prob")
    if isinstance(prob, bool) or not isinstance(prob, (int, float)) or not math.isfinite(prob):
        prob = None
    said = jev_outcome.jev_says(row)
    if said is None:
        shown = "" if prob is None else " (p=%.2f)" % prob
        return None, prob, "no opinion: Jev was unsure%s" % shown
    return said, prob, ""


def _line(body: str) -> str:
    return _ONE_LINE_RE.sub(" ", "second opinion: %s %s" % (body, NOT_ACTED)).strip()


def ask_flake(text: str, *, max_lines: int = MAX_LINES, wait_s: float = 20.0,
              gate_terms: Optional[dict] = None, ledger_dir: Optional[str] = None,
              seams_config: Optional[dict] = None, registry: Optional[list] = None,
              runner: Optional[Callable] = None) -> Tuple[str, Optional[bool], Optional[float], str]:
    """(state, said, prob, reason) for J065: said True reads as an infra flake,
    False as a real failure, None as no answer; reason is empty on an answer."""
    def ask(reduced, **kw):
        return jev_checks.check_gate_failure_flake(reduced, "none given", False, **kw)
    state, row, reason = _ask(FLAKE_ID, text, ask, max_lines=max_lines, wait_s=wait_s,
                              gate_terms=gate_terms, ledger_dir=ledger_dir,
                              seams_config=seams_config, registry=registry, runner=runner)
    said, prob, reason = _said(row, reason)
    return state, said, prob, reason


def review_second_opinion(failing_output: str, *, max_lines: int = MAX_LINES,
                          wait_s: float = 20.0, gate_terms: Optional[dict] = None,
                          ledger_dir: Optional[str] = None, seams_config: Optional[dict] = None,
                          registry: Optional[list] = None,
                          runner: Optional[Callable] = None) -> Tuple[str, str]:
    state, said, prob, reason = ask_flake(
        failing_output, max_lines=max_lines, wait_s=wait_s, gate_terms=gate_terms,
        ledger_dir=ledger_dir, seams_config=seams_config, registry=registry, runner=runner)
    if said is None:
        return state, _line("NO-DATA (%s)" % reason if state == NO_DATA else reason)
    kind = "an infra flake" if said else "a real failure"
    return state, _line("this failure reads as %s (p=%.2f); the check's own exit code still decides"
                        % (kind, prob))


_DELIVER_WORDS = {
    (True, False): "agrees: no verifying command is quoted",
    (False, True): "agrees: a verification is quoted",
    (True, True): "disagrees: it finds no verifying command quoted, the done check read one",
    (False, False): "disagrees: it reads a verification as quoted, the done check found none",
}


def deliver_second_opinion(done_note: str, quotes_verification: bool, *, wait_s: float = 20.0,
                           gate_terms: Optional[dict] = None, ledger_dir: Optional[str] = None,
                           seams_config: Optional[dict] = None, registry: Optional[list] = None,
                           runner: Optional[Callable] = None) -> Tuple[str, str]:
    if not isinstance(quotes_verification, bool):
        return NO_DATA, _line("NO-DATA (not asked: whether the note quotes a verification "
                              "must be yes or no)")

    def ask(reduced, **kw):
        return jev_checks.check_done_claim_verification(
            reduced, quotes_verification, not quotes_verification, **kw)
    state, row, reason = _ask(DONE_ID, done_note, ask, max_lines=MAX_LINES, wait_s=wait_s,
                              gate_terms=gate_terms, ledger_dir=ledger_dir,
                              seams_config=seams_config, registry=registry, runner=runner)
    said, prob, reason = _said(row, reason)
    if said is None:
        return state, _line("NO-DATA (%s)" % reason if state == NO_DATA else reason)
    return state, _line("%s (p=%.2f)" % (_DELIVER_WORDS[(said, quotes_verification)], prob))


def _read(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(coe_outside_gate.MAX_CONTENT_BYTES * 4)
    except (OSError, ValueError, TypeError):
        return None


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="One second opinion line; it never changes a verdict.",
                                     allow_abbrev=False)
    sub = parser.add_subparsers(dest="verb")
    sub.required = True
    review = sub.add_parser("review", allow_abbrev=False)
    review.add_argument("--output", required=True)
    review.add_argument("--max-lines", type=int, default=MAX_LINES)
    review.add_argument("--wait", type=float, default=20.0)
    deliver = sub.add_parser("deliver", allow_abbrev=False)
    deliver.add_argument("--note", required=True)
    deliver.add_argument("--quotes", required=True, choices=("yes", "no"))
    deliver.add_argument("--wait", type=float, default=20.0)
    args = parser.parse_args(argv)
    path = args.output if args.verb == "review" else args.note
    text = _read(path)
    if text is None:
        print(_line("NO-DATA (not asked: %s could not be read)" % os.path.basename(path)))
        return 0
    if args.verb == "review":
        _, line = review_second_opinion(text, max_lines=args.max_lines, wait_s=args.wait)
    else:
        _, line = deliver_second_opinion(text, args.quotes == "yes", wait_s=args.wait)
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
