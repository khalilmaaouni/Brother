#!/usr/bin/env python3
"""ORCH-1020: a closure is not proven by a sentence, so this refuses one.

WHY THIS EXISTS. ORCH-20 and ORCH-25 were closed CANCELLED with disposition
MERGED and a prose sentence ("carried by TOKEN-01", "folded into ORCH-19").
Measured 2026-09-18: ORCH-19's mutation gate returned exit 0 on zero
mutants, exactly ORCH-20's property, and lane_router.route_lane (TOKEN-01)
has zero callers, so ORCH-25's guarantee was enforced nowhere. The board
(scripts/gen_orch_board.py) already refuses a DONE with empty evidence (the
"claim" class); it had no equivalent refusal for a CANCELLED or MERGED row,
so a fold could close a row and remove its safeguard from the plan without
ever being asked to prove the safeguard moved with it. This module is that
proof requirement, written once here so every reader of the board sees the
same refusal rather than each closure inventing its own.

THE SOURCE OF THE DEFECT, named per the estate's fix-at-the-source law: the
plan schema had no field that distinguished "this row's work still exists,
elsewhere, provably" from "this row's work was deleted by a claim". Adding
merged_into, carried_by_check and fold_proof as required, checked fields is
the fix; this module is the one place that requires them, so every future
closure (not just today's two) is checked the same way.

WHAT THIS DELIBERATELY DOES NOT COVER, named rather than silently skipped:
a DONE row (the board's tick_class already refuses an evidence-free DONE,
see gen_orch_board.tick_class; re-checking it here would be a second copy
of that rule, not a smaller one). A row that never reached CANCELLED,
AWAITING-HUMAN, or a MERGED/DEFER disposition (nothing here claims to be
proven, so there is nothing to disprove). Concurrent edits to the plan
files while this runs (the board already reads both files once per
invocation; this function receives already-parsed dicts and makes no
freshness claim of its own). Whether merged_into's target is ITSELF
soundly closed (a chain of folds is not walked here; a future finding
class, not this one).
"""

import os
import re

import orchestrator_invariants as INV

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The disposition vocabulary in ORCH-1020-WBS.json today: BUILD, MEASURE,
# EXTEND, WIRE, DONE, MERGED, DEFER, NO-DATA (plus the field being absent,
# which means "no disposition recorded" and is not itself unknown). There is
# no shared definition site for this vocabulary elsewhere in the repository
# (orchestrator_invariants.py defines TASK_STATES and TERMINAL_STATES but
# nothing named "disposition"), so it is typed here directly, the same way
# orchestrator_invariants.py types TASK_CLASSES directly when nothing else
# already owns it. A value outside this set is a finding (rule: unknown
# input raises, never a silent pass), not silently treated as "no special
# disposition".
KNOWN_DISPOSITIONS = frozenset((
    "BUILD", "MEASURE", "EXTEND", "WIRE", "DONE", "MERGED", "DEFER",
    "NO-DATA",
))

# "a mutation's red line": the fold_proof must quote a check that actually
# went red at some point, not just a sentence claiming it did.
_RED_LINE = re.compile(r"FAILED|FAIL:")


def _finding(row_id, reason):
    return {"id": row_id, "reason": reason}


def _command_path(command):
    """Pull the first path-shaped token out of a carried_by_check command,
    for example 'python3 scripts/test_x.py -v' -> 'scripts/test_x.py'.
    Returns None when no token looks like a path, which the caller reports
    as a finding rather than assuming the command needs no file."""
    for token in command.split():
        if token.startswith("-"):
            continue
        if "/" in token or token.endswith(".py"):
            return token
    return None


def _check_fold(row_id, unit, entry, existing_ids, root):
    """Rule 1: a CANCELLED-or-MERGED row must prove its work still exists
    somewhere else, not just say so. Every missing or hollow piece is its
    own finding, so a partial fold (a target named, no proof run) reads as
    partial, not as a single opaque failure."""
    findings = []
    merged_into = (entry or {}).get("merged_into") or unit.get("merged_into")
    if not merged_into:
        findings.append(_finding(row_id, "CANCELLED or MERGED with no merged_into"))
    elif merged_into not in existing_ids:
        findings.append(_finding(
            row_id, "merged_into %r does not name an existing row" % (merged_into,)))

    carried_by_check = (entry or {}).get("carried_by_check")
    if not carried_by_check:
        findings.append(_finding(row_id, "CANCELLED or MERGED with no carried_by_check"))
    else:
        path = _command_path(carried_by_check)
        if not path:
            findings.append(_finding(
                row_id, "carried_by_check %r names no test file" % (carried_by_check,)))
        elif not os.path.isfile(os.path.join(root, path)):
            findings.append(_finding(
                row_id, "carried_by_check names %r, which does not exist on disk" % (path,)))

    fold_proof = (entry or {}).get("fold_proof")
    if not fold_proof:
        findings.append(_finding(row_id, "CANCELLED or MERGED with no fold_proof"))
    elif not _RED_LINE.search(fold_proof):
        findings.append(_finding(
            row_id, "fold_proof carries no FAILED or FAIL: line, so it proves nothing went red"))

    return findings


def _check_deferred(row_id, entry):
    """Rule 2: AWAITING-HUMAN or DEFER must name who decides and what would
    change the decision. Neither is optional: a wait with no owner and no
    flip condition is a wait that never ends."""
    findings = []
    decided_by = (entry or {}).get("decided_by")
    flip_condition = (entry or {}).get("flip_condition")
    if not decided_by:
        findings.append(_finding(row_id, "AWAITING-HUMAN or DEFER with no decided_by"))
    if not flip_condition:
        findings.append(_finding(row_id, "AWAITING-HUMAN or DEFER with no flip_condition"))
    return findings


def check(wbs, status, root=None):
    """Every row's closure claim, checked against its own recorded proof.

    Returns a list of {"id": row id, "reason": text} findings. An empty
    list means every closure this run has to answer for is proven; it does
    NOT mean nothing was checked, because an empty or malformed wbs or
    status is itself reported as one NO-DATA finding rather than an empty
    list, per the estate's rule that NO-DATA is never read as a pass. This
    is the exact bug being fixed here (a check that is green because it
    checked nothing), so this function refuses to reproduce it about
    itself.
    """
    if not isinstance(wbs, dict) or not wbs.get("units"):
        return [_finding("PLAN", "NO-DATA: wbs has no units, cannot check any closure")]
    if not isinstance(status, dict) or "units" not in status:
        return [_finding("PLAN", "NO-DATA: status has no units, cannot check any closure")]

    root = ROOT if root is None else root
    units = wbs["units"]
    existing_ids = frozenset(u["id"] for u in units)
    status_units = status["units"]

    findings = []
    for unit in units:
        row_id = unit["id"]
        entry = status_units.get(row_id)
        state = entry.get("state") if entry else None
        disposition = unit.get("disposition")

        if state is not None and state not in INV.TASK_STATES:
            findings.append(_finding(row_id, "unknown state %r" % (state,)))
        if disposition is not None and disposition not in KNOWN_DISPOSITIONS:
            findings.append(_finding(row_id, "unknown disposition %r" % (disposition,)))

        if state == "DONE":
            # Already checked by gen_orch_board.tick_class; not re-checked here.
            continue

        if state == "CANCELLED" or disposition == "MERGED":
            findings.extend(_check_fold(row_id, unit, entry, existing_ids, root))

        if state == "AWAITING-HUMAN" or disposition == "DEFER":
            findings.extend(_check_deferred(row_id, entry))

    return findings


if __name__ == "__main__":
    import json
    import sys

    wbs_path = os.path.join(ROOT, "docs", "plan", "ORCH-1020-WBS.json")
    status_path = os.path.join(ROOT, "docs", "plan", "ORCH-1020-STATUS.json")
    try:
        with open(wbs_path, "r", encoding="utf-8") as fh:
            wbs_doc = json.load(fh)
        with open(status_path, "r", encoding="utf-8") as fh:
            status_doc = json.load(fh)
    except (OSError, ValueError) as exc:
        print("closure-integrity: NO-DATA, cannot read a source: %s" % exc, file=sys.stderr)
        sys.exit(2)
    results = check(wbs_doc, status_doc)
    if not results:
        print("closure-integrity: 0 findings")
        sys.exit(0)
    for f in results:
        print("closure-integrity: %s: %s" % (f["id"], f["reason"]))
    print("closure-integrity: %d finding(s)" % len(results))
    sys.exit(1)
