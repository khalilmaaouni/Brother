#!/usr/bin/env python3
"""Safe Unwatched Time (SUT) for one run directory, read off the run's own records.

Row S10. The definition, the four break conditions and everything this
deliberately does NOT measure are in benchmarks/SAFE-UNWATCHED-TIME.md; this
file is the instrument that applies it, and the document is the contract.

In one sentence: the longest span a run went with no human present during which
every claim it made stayed true, measured from the journal's timestamps, the
claim store's evidence and the receipts.

WHY IT READS THE RECORDS AND NOT A REPORT. A delivery report carries
wall_clock_seconds, which is the run's LENGTH, not its safe length. The two are
the same number only when nothing broke, and the whole point of the metric is
the case where something did. So the span is rebuilt event by event and closed
at the first break, in timestamp order.

WHY A CAUGHT BREAK STILL CLOSES THE SPAN. An integration this engine refused
because a unit wrote a path it never declared is the system working, and it is
the reason this number is measurable at all. It still ends the span, because
the span measures how long the run went without needing a person, and a
quarantine is a thing a person then adjudicates.

Exit 0  a figure was computed, broken or not. This is a METRIC REPORTER, not a
        gate: a break is a measurement, and a run that broke at minute 3 has a
        real SUT of 3 minutes, not a failure of this tool.
Exit 2  the run directory does not exist or could not be read.
Exit 3  NO-DATA. The run left no timestamps, or left no receipts. NO-DATA is
        never a pass and never a zero: a run nobody recorded is not a run that
        was safe for zero minutes, it is a run nothing can be said about.

Python 3.9 floor, standard library only.
"""
import argparse
import datetime
import json
import os
import sys

NODATA = "NO-DATA"

# The journal vocabulary this reader gives meaning to, spelled once. Every
# other event type in a journal is timeline only: it moves the clock and can
# close the span at the end, but it can never break it.
EV_RUN_OPENED = "run.opened"
#: A resume is NOT a break. Row E73 proved a killed run coming back from disk
#: with nothing lost, and a recovery the engine performed itself is continuity.
EV_RUN_RESUMED = "run.resumed"
#: The unit closed. Counted toward the units figure when it lands in the span.
EV_UNIT_DONE = "unit.done"
#: Carries check_exit, the unit's own verdict on its own claim.
EV_EVIDENCE_VERIFIED = "evidence.verified"
#: Carries the engine's refusal reason, which is where a scope break shows up.
EV_INTEGRATE_REFUSED = "integrate.refused"
#: Carries receipts and unproven, so an absent proof can be told from a
#: present one.
EV_RECEIPT_ISSUED = "receipt.issued"

#: The four break kinds of benchmarks/SAFE-UNWATCHED-TIME.md, in the document's
#: own order. The strings are what the report prints after "broken by".
REFUTED = "a claim refuted by its own check"
UNPROVEN = "a receipt left unproven where a pass was claimed"
SCOPE = "a write outside declared scope"
NO_CHECK = "a unit closed with no check recorded"


def _parse_at(value):
    """A journal `at` string to an aware datetime, or None.

    Naive stamps are read as UTC rather than rejected: the journal has always
    written an offset, but a hand-built or older record must not crash the
    instrument, and a naive stamp compared against an aware one raises."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        stamp = datetime.datetime.fromisoformat(value.strip())
    except ValueError:  # sbe: allow-silent read_journal, the only caller, already counts this None as a skipped line and reports the count
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    return stamp


def _epoch(value):
    """A claims.json epoch float to an aware datetime, or None.

    claims.json stamps are seconds since the epoch, the journal's are ISO
    strings, and the two have to be comparable because a break can be recorded
    in either store."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value <= 0:
        return None
    try:
        return datetime.datetime.fromtimestamp(value, datetime.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def read_journal(run_dir):
    """Every parseable journal event with a timestamp, oldest first.

    A malformed line is SKIPPED rather than fatal, and the count of skipped
    lines comes back beside the events, because a truncated final line is what
    a killed run leaves behind and refusing to measure a crashed run would
    refuse exactly the runs this metric exists for."""
    path = os.path.join(run_dir, "journal.jsonl")
    events, skipped = [], 0
    if not os.path.isfile(path):
        return events, skipped
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                skipped += 1
                continue
            if not isinstance(event, dict):
                skipped += 1
                continue
            stamp = _parse_at(event.get("at"))
            if stamp is None:
                skipped += 1
                continue
            event["_at"] = stamp
            events.append(event)
    events.sort(key=lambda e: e["_at"])
    return events, skipped


def read_claims(run_dir):
    """The claim store as a dict, or {} when there is none to read."""
    path = os.path.join(run_dir, "claims.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            claims = json.load(fh)
    except (OSError, ValueError):
        return {}
    return claims if isinstance(claims, dict) else {}


def read_receipts(run_dir, events):
    """(count, unproven) across every receipt this run left, or (0, 0).

    Two sources, because brother_run.py writes the file (RECEIPT_DIRNAME and
    RECEIPT_FILENAME under the run directory) while the journal carries the
    receipt.issued event, and a run directory published under docs/plan/runs
    has historically carried the event without the file. Whichever exists is
    read; when both do the file wins, since it is the run's own serialization
    rather than a summary of it."""
    path = os.path.join(run_dir, "receipt", "receipt.json")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                body = json.load(fh)
        except (OSError, ValueError):
            body = None
        if isinstance(body, dict) and isinstance(body.get("receipts"), list):
            rows = body["receipts"]
            left = sum(1 for r in rows if isinstance(r, dict)
                       and str(r.get("state") or "") in ("unproven", "refused"))
            return len(rows), left
    count = unproven = 0
    for event in events:
        if event.get("type") != EV_RECEIPT_ISSUED:
            continue
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        issued = payload.get("receipts")
        left = payload.get("unproven")
        if isinstance(issued, int) and not isinstance(issued, bool):
            count = max(count, issued)
        if isinstance(left, int) and not isinstance(left, bool):
            unproven = max(unproven, left)
    return count, unproven


def _refusal_kind(reason):
    """Which break an integrate.refused reason is, from the engine's own words.

    Structural fields would be better than a reason string and do not exist:
    the payload carries canonical, reason and verdict, and the verdict is
    always REFUSED, so the kind is only in the prose. Read by keyword, and
    anything unrecognized falls to SCOPE rather than being dropped, because an
    integration this engine refused is a break of SOME kind and silently
    dropping the unfamiliar one is how a metric flatters itself."""
    text = str(reason or "").lower()
    if "quarantine" in text or "never declared" in text:
        return SCOPE
    if "did not pass" in text or "verdict=fail" in text or "failed" in text:
        return REFUTED
    return SCOPE


def find_breaks(events, claims):
    """Every break in the record as (when, kind, detail), oldest first.

    Both stores are walked, because a break can be recorded in either: the
    journal carries the engine's verdicts, the claim store carries the evidence
    a unit closed on."""
    breaks = []
    for event in events:
        kind = event.get("type")
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        if kind == EV_EVIDENCE_VERIFIED:
            code = payload.get("check_exit")
            if isinstance(code, int) and not isinstance(code, bool) and code != 0:
                breaks.append((event["_at"], REFUTED, "check_exit %d on %s" % (
                    code, event.get("unit_id") or "a unit")))
        elif kind == EV_INTEGRATE_REFUSED:
            reason = str(payload.get("reason") or "").strip()
            breaks.append((event["_at"], _refusal_kind(reason),
                           reason[:160] or "integration refused, no reason given"))
        elif kind == EV_RECEIPT_ISSUED:
            left = payload.get("unproven")
            if isinstance(left, int) and not isinstance(left, bool) and left > 0:
                breaks.append((event["_at"], UNPROVEN,
                               "%d receipt(s) unproven" % left))

    for unit_id, claim in sorted(claims.items()):
        if not isinstance(claim, dict):
            continue
        if str(claim.get("state") or "") not in ("done", "released", "closed"):
            continue
        when = _epoch(claim.get("released_at")) or _epoch(claim.get("claimed_at"))
        if when is None:
            continue
        evidence = claim.get("evidence")
        evidence = evidence if isinstance(evidence, dict) else {}
        code = evidence.get("exit_code")
        if isinstance(code, bool) or not isinstance(code, int):
            breaks.append((when, NO_CHECK,
                           "%s closed with no exit code in its evidence" % unit_id))
        elif code != 0:
            breaks.append((when, REFUTED,
                           "%s closed on exit %d" % (unit_id, code)))

    breaks.sort(key=lambda b: b[0])
    return breaks


def measure(run_dir):
    """(report dict, exit code). The whole metric, with no printing.

    report carries minutes, units, broken_by, detail, started, ended, receipts,
    unproven and skipped_lines. On NO-DATA every figure is absent and `nodata`
    holds the sentence naming what was missing."""
    if not os.path.isdir(run_dir):
        return {"nodata": "%s is not a directory" % run_dir}, 2

    events, skipped = read_journal(run_dir)
    claims = read_claims(run_dir)
    receipts, unproven = read_receipts(run_dir, events)

    if not events:
        return {"nodata": "%s holds no journal event carrying a timestamp, so "
                          "the span has no endpoints" % run_dir}, 3
    if receipts == 0:
        return {"nodata": "%s holds no receipt, neither a receipt.json file "
                          "nor a receipt.issued event, so nothing says what "
                          "this run claimed" % run_dir}, 3

    started = events[0]["_at"]
    ended = events[-1]["_at"]
    # A break stamped before this run's first event belongs to an earlier run
    # (a claim carried over) and cannot shorten a span it precedes.
    breaks = [b for b in find_breaks(events, claims) if b[0] >= started]

    if breaks:
        when, kind, detail = breaks[0]
        closed_at, broken_by, break_detail = min(when, ended), kind, detail
    else:
        closed_at, broken_by, break_detail = ended, "none", ""

    units = sum(1 for e in events
                if e.get("type") == EV_UNIT_DONE and e["_at"] <= closed_at)
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
    }, 0


def report_line(report):
    """The one line row S10 asks for. Exactly this shape, everywhere."""
    if report.get("nodata"):
        return "safe unwatched time: %s, %s" % (NODATA, report["nodata"])
    return ("safe unwatched time: %.1f min over %d units, broken by %s"
            % (report["minutes"], report["units"], report["broken_by"]))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dir", help="a run directory holding journal.jsonl")
    args = ap.parse_args(argv)

    report, code = measure(args.run_dir)
    print(report_line(report))
    if not report.get("nodata"):
        # The bound, printed every time rather than only in the document: no
        # event kind records an operator input scoped to a run, so the span
        # above assumes the whole run was unattended and is an UPPER BOUND.
        print("  span %s to %s, %d receipt(s), %d unproven, upper bound: no "
              "operator-input event kind exists yet"
              % (report["started"], report["ended"], report["receipts"],
                 report["unproven"]))
        if report["detail"]:
            print("  break: %s" % report["detail"])
        if report["skipped_lines"]:
            print("  %d journal line(s) unparseable and skipped"
                  % report["skipped_lines"])
    return code


if __name__ == "__main__":
    sys.exit(main())
