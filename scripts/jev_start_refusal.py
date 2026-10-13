"""JW2.a: a refused loop start gets one second opinion line on its refusal.

scripts/loop/start_run.sh refuse() restores the controls, prints its one
`START REFUSED [<step>]: <fix>` line, runs this helper, then exits 2 whatever
the helper did. The helper asks J065 (noul: does this read as a transient
condition rather than a real block?) through the shared door core
jev_door.ask_flake, so it cannot drift from the review door, and prints one
labelled line. The refusal and its exit code never change.

FAIL DIRECTION: an empty step, binary text, a gate refusal (a private term, a
missing term list), J065 off, no registry, no answer within wait_s each print
one NO-DATA line; an unsure answer prints "no opinion". main() returns 0 in
every case, a missing argument and an internal error included. An interrupted
launch (step "signal") asks nothing.

Footprint: at most one Jev question (one attempt row, one bridge call when
J065 is not off), a wait of at most wait_s seconds, no file written here.

Python 3, standard library only.
"""
import os
import re
import sys
from typing import Callable, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jev_door  # noqa: E402

SKIP_STEPS: Tuple[str, ...] = ("signal",)
MAX_MESSAGE_CHARS = 400
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _line(body: str) -> str:
    return " ".join(body.split())


def refusal_second_opinion(step: str, message: str, *, wait_s: float = 8.0,
                           gate_terms: Optional[dict] = None, ledger_dir: Optional[str] = None,
                           seams_config: Optional[dict] = None, registry: Optional[list] = None,
                           runner: Optional[Callable] = None) -> Tuple[str, str]:
    """(state, line): one labelled line, or ("OK", "") for a step that asks nothing."""
    if not isinstance(step, str) or not step.strip():
        return jev_door.NO_DATA, "second opinion: NO-DATA (not asked: no refusal step given)"
    if step in SKIP_STEPS:
        return jev_door.OK, ""
    if _CONTROL_RE.search(step):
        step = "(unreadable step)"
    if not isinstance(message, str):
        message = ""
    text = ("START REFUSED [%s]: %s" % (step, message[:MAX_MESSAGE_CHARS])).rstrip()
    state, said, prob, reason = jev_door.ask_flake(
        text, max_lines=4, wait_s=wait_s, gate_terms=gate_terms, ledger_dir=ledger_dir,
        seams_config=seams_config, registry=registry, runner=runner)
    if said is None:
        if state == jev_door.NO_DATA:
            return state, _line("second opinion: NO-DATA (%s)" % reason)
        why = reason[len("no opinion: "):] if reason.startswith("no opinion: ") else reason
        return state, _line("second opinion: no opinion (%s)" % why)
    kind = "a transient condition" if said else "a real block"
    return state, _line("second opinion (recorded, not acted on): this refusal reads as %s "
                        "(p=%.2f); the refusal and its exit code stand" % (kind, prob))


def _arg(argv: Sequence[str], i: int) -> str:
    if len(argv) <= i:
        return ""
    raw = argv[i]
    # argv bytes that are not UTF-8 arrive as surrogates; replace them, never fail on them
    return raw.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        _, line = refusal_second_opinion(_arg(args, 0), _arg(args, 1))
    except Exception as exc:  # sbe: allow-silent a second opinion never fails a refusal; the error is printed
        line = "second opinion: NO-DATA (the helper failed: %s)" % type(exc).__name__
    if line:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
