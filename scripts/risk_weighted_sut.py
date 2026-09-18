#!/usr/bin/env python3
"""Risk-weighted Safe Unwatched Time (SUT): row DOM-20.02, an EXTEND of S10.

WHY THIS EXISTS. scripts/safe_unwatched_time.py answers one question well:
how long did this run go, as a whole, before some claim it made stopped
being true. That single number hides a real difference this estate's own
WBS already carries on every unit: a "medium" risk row (README wording, a
docstring) and a "critical" one (a schema migration, a merge gate) do not
deserve the same trust for the same thirty minutes unwatched. Pooling them
into one figure lets a long, calm stretch of low-stakes edits hide a short,
early break in the one row that actually mattered. This module answers the
weighted question instead: for EACH risk class this run actually touched,
how long did that class's own claims stay true.

WHAT IT REUSES, NOT REBUILDS. Every timestamp, claim, receipt and break rule
comes from safe_unwatched_time.py (imported below, never copied): the four
break conditions, their names, read_journal, read_claims, read_receipts and
the NO-DATA exit codes are that module's contract and stay that module's
contract. This file adds exactly one thing on top: it ATTRIBUTES each break
to the unit that caused it (safe_unwatched_time.find_breaks() deliberately
drops that attribution, because the whole-run figure never needed it) and
groups units by the risk_class their own claim evidence carries.

WHERE risk_class COMES FROM. Not a new field. scripts/integrate.py (ORCH-02)
already carries a unit's risk_class from its WBS row into the evidence dict
it hands back on every verdict, passed or failed, and claim_store.release()
already stores that whole evidence dict verbatim as claim["evidence"]. So
claims.json[unit_id]["evidence"]["risk_class"] is the one place this run
already recorded the answer; this module reads it, it never re-derives it
from docs/plan/*WBS.json (that file is a plan, not a record of what a given
run actually claimed) and never invents a class for a unit that lacks one.

THE "unknown" BUCKET. A unit whose claim carries no evidence, no risk_class
inside it, or an empty one, is real work this run did with no declared
stakes. That is not the same fact as "this class of work stayed safe for N
minutes", so it is never folded into whichever named class happens to sort
first or read as least severe: it gets its own bucket, named "unknown", and
that bucket ALWAYS reports NO-DATA, never a computed number. A number next
to "unknown" would silently imply a class was named when none was.

THE ONE BREAK KIND THAT CANNOT BE PINNED TO A UNIT. receipt.issued is a
run-wide aggregate (receipt_door.py issues one event per CALL, covering
every unit's receipt at once; see that module's own comment on E59). An
unproven receipt cannot be pinned to the one unit that left it unproven, so
this module cannot rule any class out. An unattributed receipt break is
therefore applied to EVERY class this run touched, never dropped and never
guessed onto one class. This is a stated limitation, not a silent gap: the
per-class report says so through the same "broken by" word
safe_unwatched_time.py already uses (UNPROVEN), same as it would for the
whole-run figure.

Exit 0  every class reported a real figure.
Exit 2  the run directory does not exist or could not be read.
Exit 3  NO-DATA touched the result: either nothing in the run could be
        measured at all (safe_unwatched_time.py's own two whole-run NO-DATA
        cases: no timestamped journal event, or no receipt), or at least one
        unit's claim carried no risk_class and so the "unknown" bucket is
        present. NO-DATA is never a pass and is never silently dropped from
        the exit code just because other classes came back clean.

Python 3.9 floor, standard library only, no network.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import safe_unwatched_time as sut  # noqa: E402

#: The bucket for a unit whose claim carries no identifiable risk_class.
#: Deliberately not a class that could ever be typed by a real WBS row (no
#: WBS unit is named "unknown"), so a collision can never happen by
#: accident.
UNKNOWN_RISK_CLASS = "unknown"

#: The sentinel key used only when the WHOLE run has nothing to classify at
#: all (safe_unwatched_time.py's own whole-run NO-DATA, or a run that names
#: no unit id anywhere). Not a risk class either, for the same reason as
#: UNKNOWN_RISK_CLASS above.
GLOBAL_NODATA_KEY = "*"


def classify_units(claims, events):
    """{unit_id: risk_class} for every unit id this run's record names.

    A unit id can appear only in claims.json (a claim was written but the
    journal line that named it was lost or predates this event kind), only
    in the journal (evidence.verified, integrate.refused, unit.done and
    claim.acquired/released all stamp unit_id), or in both. Every source is
    read so a unit never silently escapes classification because one of the
    two records happens to be missing it. The value is UNKNOWN_RISK_CLASS
    unless the unit has a claim, that claim has an "evidence" dict, and that
    dict's "risk_class" is a non-empty string once stripped.

    Raises ValueError when a unit's OWN declared risk_class string, after
    stripping, equals UNKNOWN_RISK_CLASS exactly: risk_class is free text
    from whoever wrote the WBS row (docs/plan/ORCH-1020-WBS.json carries
    "critical", "high", "medium" today and nothing stops a future row
    naming itself "unknown"), and a real class silently colliding with the
    "no class was ever declared" bucket would erase exactly the distinction
    this row exists to preserve: a unit that DID declare its stakes would
    read as one that never named them. A Muse review of this row's own
    generalized design (2026-09-18) named this collision; it is refused
    here rather than absorbed under a permissive default.
    """
    ids = set(claims.keys())
    for event in events:
        unit_id = event.get("unit_id")
        if isinstance(unit_id, str) and unit_id:
            ids.add(unit_id)

    out = {}
    for unit_id in ids:
        risk_class = None
        claim = claims.get(unit_id)
        if isinstance(claim, dict):
            evidence = claim.get("evidence")
            if isinstance(evidence, dict):
                value = evidence.get("risk_class")
                if isinstance(value, str) and value.strip():
                    risk_class = value.strip()
        if risk_class == UNKNOWN_RISK_CLASS:
            raise ValueError(
                "unit %s declares its own risk_class as %r, which collides "
                "with the reserved no-class-declared bucket name; rename "
                "the WBS row's risk_class or this instrument cannot tell a "
                "declared class from an absent one"
                % (unit_id, UNKNOWN_RISK_CLASS))
        out[unit_id] = risk_class or UNKNOWN_RISK_CLASS
    return out


def find_breaks_with_unit(events, claims):
    """Every break, as (when, kind, detail, unit_id), oldest first.

    Mirrors safe_unwatched_time.find_breaks() exactly (same four break
    kinds, same two sources: the journal and the claim store), and adds the
    one field that reader drops on purpose: which unit the break belongs
    to. unit_id is None for exactly one case, a receipt.issued break: that
    event carries no unit_id at all (see the module docstring), so callers
    here must treat None as "applies to every class", never as "applies to
    no class".
    """
    breaks = []
    for event in events:
        kind = event.get("type")
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        unit_id = event.get("unit_id")
        unit_id = unit_id if isinstance(unit_id, str) and unit_id else None

        if kind == sut.EV_EVIDENCE_VERIFIED:
            code = payload.get("check_exit")
            if isinstance(code, int) and not isinstance(code, bool) and code != 0:
                breaks.append((event["_at"], sut.REFUTED,
                               "check_exit %d on %s" % (
                                   code, unit_id or "a unit"), unit_id))
        elif kind == sut.EV_INTEGRATE_REFUSED:
            reason = str(payload.get("reason") or "").strip()
            breaks.append((event["_at"], sut._refusal_kind(reason),
                           reason[:160] or "integration refused, no reason given",
                           unit_id))
        elif kind == sut.EV_RECEIPT_ISSUED:
            left = payload.get("unproven")
            if isinstance(left, int) and not isinstance(left, bool) and left > 0:
                # Never attributable: see the module docstring.
                breaks.append((event["_at"], sut.UNPROVEN,
                               "%d receipt(s) unproven" % left, None))

    for unit_id, claim in sorted(claims.items()):
        if not isinstance(claim, dict):
            continue
        if str(claim.get("state") or "") not in ("done", "released", "closed"):
            continue
        when = (sut._epoch(claim.get("released_at"))
                or sut._epoch(claim.get("claimed_at")))
        if when is None:
            continue
        evidence = claim.get("evidence")
        evidence = evidence if isinstance(evidence, dict) else {}
        # A claims.json entry keyed by an empty string is corrupt data, not
        # a real unit id. Attributing its break to that literal "" would
        # make it invisible to every class's own unit_ids set (never equal
        # "" itself) and the break would silently vanish instead of ending
        # any span, which is exactly the false-green shape this instrument
        # exists to refuse. Treated as unattributable instead, the same as
        # a receipt-wide break: it ends every class's span rather than
        # none of them.
        attributed_to = unit_id if unit_id else None
        label = unit_id or "a unit"
        code = evidence.get("exit_code")
        if isinstance(code, bool) or not isinstance(code, int):
            breaks.append((when, sut.NO_CHECK,
                           "%s closed with no exit code in its evidence"
                           % label, attributed_to))
        elif code != 0:
            breaks.append((when, sut.REFUTED,
                           "%s closed on exit %d" % (label, code),
                           attributed_to))

    breaks.sort(key=lambda b: b[0])
    return breaks


def _class_report(risk_class, unit_ids, breaks, started, ended, events,
                  receipts, unproven, skipped):
    """One report dict for one named (never "unknown") risk class.

    Shaped exactly like safe_unwatched_time.measure()'s own report dict, so
    a caller that already knows how to read that shape reads this one too.
    started/ended/receipts/unproven/skipped_lines are the whole run's, not
    recomputed per class: the wall clock a person could have been asleep
    for is one clock, shared by every class in the same run. What varies
    per class is only where the FIRST break belonging to that class falls,
    and how many of that class's own units closed before it.
    """
    class_breaks = [b for b in breaks if b[3] is None or b[3] in unit_ids]
    if class_breaks:
        when, kind, detail, _ = class_breaks[0]
        closed_at, broken_by, break_detail = min(when, ended), kind, detail
    else:
        closed_at, broken_by, break_detail = ended, "none", ""

    units = sum(1 for e in events
                if e.get("type") == sut.EV_UNIT_DONE
                and e.get("unit_id") in unit_ids
                and e["_at"] <= closed_at)
    return {
        "nodata": "",
        "minutes": (closed_at - started).total_seconds() / 60.0,
        "units": units,
        "broken_by": broken_by,
        "detail": break_detail,
        "started": started.isoformat(),
        "ended": closed_at.isoformat(),
        "receipts": receipts,
        "unproven": unproven,
        "skipped_lines": skipped,
    }


def measure(run_dir):
    """({risk_class: report}, worst_exit_code). No printing.

    Every named risk class this run's claims actually carry gets its own
    report, shaped like safe_unwatched_time.measure()'s own report dict.
    "unknown" is present only when at least one unit in this run carries no
    risk_class, and its report is always the NO-DATA shape ({"nodata":
    reason}), never a computed figure: see the module docstring for why.

    worst_exit_code is 2 when run_dir is not a directory, 3 when nothing at
    all could be classified (safe_unwatched_time's own whole-run NO-DATA,
    or a run naming no unit id anywhere) or when the "unknown" bucket is
    present, else 0.
    """
    if not os.path.isdir(run_dir):
        return ({GLOBAL_NODATA_KEY: {
            "nodata": "%s is not a directory" % run_dir}}, 2)

    events, skipped = sut.read_journal(run_dir)
    claims = sut.read_claims(run_dir)
    receipts, unproven = sut.read_receipts(run_dir, events)

    if not events:
        return ({GLOBAL_NODATA_KEY: {
            "nodata": "%s holds no journal event carrying a timestamp, so "
                      "no risk class in it can be measured" % run_dir}}, 3)
    if receipts == 0:
        return ({GLOBAL_NODATA_KEY: {
            "nodata": "%s holds no receipt, so nothing says what this run "
                      "claimed for any risk class" % run_dir}}, 3)

    unit_classes = classify_units(claims, events)
    if not unit_classes:
        return ({GLOBAL_NODATA_KEY: {
            "nodata": "%s names no unit id in its journal or its claims, so "
                      "nothing can be weighted by risk class" % run_dir}}, 3)

    started = events[0]["_at"]
    ended = events[-1]["_at"]
    # Mirrors safe_unwatched_time.measure(): a break stamped before this
    # run's first event belongs to an earlier run and cannot shorten a span
    # it precedes.
    breaks = [b for b in find_breaks_with_unit(events, claims)
             if b[0] >= started]

    classes_present = set(unit_classes.values())
    per_class = {}
    for risk_class in sorted(classes_present):
        unit_ids = {u for u, c in unit_classes.items() if c == risk_class}
        if risk_class == UNKNOWN_RISK_CLASS:
            per_class[risk_class] = {
                "nodata": "unit(s) %s carry no risk_class in their claim "
                          "evidence, so their unwatched time cannot be "
                          "weighted against a named class"
                          % ", ".join(sorted(unit_ids))}
            continue
        per_class[risk_class] = _class_report(
            risk_class, unit_ids, breaks, started, ended, events,
            receipts, unproven, skipped)

    worst = 3 if UNKNOWN_RISK_CLASS in per_class else 0
    return per_class, worst


def report_line(risk_class, report):
    """The one line this instrument prints per risk class."""
    if report.get("nodata"):
        return ("safe unwatched time, risk class %s: %s, %s"
                % (risk_class, sut.NODATA, report["nodata"]))
    return ("safe unwatched time, risk class %s: %.1f min over %d units, "
            "broken by %s" % (risk_class, report["minutes"],
                              report["units"], report["broken_by"]))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dir", help="a run directory holding journal.jsonl")
    args = ap.parse_args(argv)

    per_class, code = measure(args.run_dir)
    for risk_class in sorted(per_class):
        report = per_class[risk_class]
        print(report_line(risk_class, report))
        if not report.get("nodata") and report["detail"]:
            print("  break: %s" % report["detail"])
    return code


if __name__ == "__main__":
    sys.exit(main())
