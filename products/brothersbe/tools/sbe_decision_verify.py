#!/usr/bin/env python3
"""Verification-only check: does a human decision actually bind a decision
packet at the current head?

WHY VERIFICATION-ONLY. `decisions.bind_human_decision` (src/brothersbe/
decisions.py) is the one place that decides whether a human decision
authorizes release over a given packet at a given head; this tool is a thin
CLI around that single call, reading both documents from disk and printing
the house 3-tuple it returns. IT CREATES NOTHING: no flag, no fallback, no
code path in this file writes or synthesizes a decision or a packet, ever.
A verification step that can also mint the thing it verifies is not a
verification step, it is a rubber stamp with a delay. `tools/
test_sbe_ci_handshake.py` scans this file's own source for exactly that
shape (opening a file in write mode, dumping JSON anywhere but stdout,
writing text to a path) and fails if this file ever grows one.

Usage:
    python3 tools/sbe_decision_verify.py <human-decision.json> <decision-packet.json>

Exit codes:
  0  PASS      the decision binds the packet at the current head
  2  FAIL      both files read; the binding itself failed
  3  NO-DATA   either file is missing or unreadable, HEAD could not be
               resolved, or the binding call itself answered NO-DATA; the
               printed line names what was missing, never a traceback

Python floor 3.9, standard library only.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from brothersbe import decisions  # noqa: E402
from sbe_checks import say  # noqa: E402


def _load(path):
    """One dict with named keys "document" and "problem", never a bare
    2-tuple: decisions.py states the house rule this mirrors ("no helper
    returns a bare two-value tuple ... a caller wants two things reads two
    keys off one dict"), because a literal (x, y) return reads to the
    honesty meta-test as an unregistered (verdict, evidence) pair it cannot
    prove is never PASS, and `_load` has no registry entry to prove it in.
    `problem` is a NO-DATA sentence naming exactly what went wrong (absent
    file, unreadable, not JSON); None when the load succeeded. Never raises:
    a missing or malformed input is this tool's most ordinary case, not an
    exceptional one."""
    if not path or not os.path.isfile(path):
        return {"document": None, "problem": "no file at %r" % path}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return {"document": json.load(fh), "problem": None}
    except (OSError, ValueError) as exc:
        return {"document": None, "problem": "%r did not read as JSON (%s)" % (path, exc)}


def _current_head(root):
    """`git rev-parse HEAD` in `root`, or None when git cannot answer."""
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                 capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) < 2:
        print("NO-DATA: usage: sbe_decision_verify.py <human-decision.json> <decision-packet.json>")
        return 3

    loaded_decision = _load(argv[0])
    loaded_packet = _load(argv[1])
    decision, decision_problem = loaded_decision["document"], loaded_decision["problem"]
    packet, packet_problem = loaded_packet["document"], loaded_packet["problem"]
    if decision_problem or packet_problem:
        say("NO-DATA: %s" % "; ".join(p for p in (decision_problem, packet_problem) if p))
        return 3

    head_commit = _current_head(ROOT)
    if head_commit is None:
        say("NO-DATA: git could not resolve HEAD in %s, so nothing was bound" % ROOT)
        return 3

    try:
        verdict, evidence, problems = decisions.bind_human_decision(decision, packet, head_commit)
    except AttributeError:
        # A boundary call into a sibling module, guarded explicitly: this
        # checkout's decisions.py may predate `bind_human_decision` (a
        # partial or stale install), and that is a NO-DATA about the
        # checkout, never a traceback naming an internal attribute.
        print("NO-DATA: this checkout's decisions.py has no bind_human_decision; "
              "update src/brothersbe/decisions.py before verifying a decision")
        return 3
    say("%-8s %s" % (verdict, evidence))
    for problem in problems:
        say("  - %s" % problem)

    if verdict == "PASS":
        # Binding and authorization are different questions (the hostile
        # replay of 2026-08-20 proved a HOLD once read as a release here).
        # The evidence carries an `authorizes` flag; a well-bound HOLD is a
        # verified NON-authorization and must not exit as if release may
        # proceed. An evidence value without the flag predates the fix and
        # is refused the same way: absence of the signal is never a yes.
        if getattr(evidence, "authorizes", False) is True:
            return 0
        print("  - bound, and does NOT authorize release; exit is the FAIL exit on purpose")
        return 2
    if verdict == "FAIL":
        return 2
    return 3


if __name__ == "__main__":
    sys.exit(main())
