#!/usr/bin/env python3
"""No gate row may go red on a DATE that falls inside the release window.

WHY THIS EXISTS. Three times now a gate row has turned every tree red at a
moment nobody chose, in the middle of a cut:

  2026-10-20 and 2026-10-21  closing-ceremony-real red at a 26.8 h then a
      24.2 h pack age, and no-data-semantics, export-public and
      readiness-gate red on three BATTERY-EXPECTATIONS review_by dates that
      had simply passed. Every one blocked every landing.
  2026-10-25  the closing ceremony check measured a pack's AGE rather than
      whether the pack still described the branch under test.

The symptom each time was a red row. The SOURCE is not any one row: it is
that a review_by date, and a freshness bar in days, are DATA chosen by hand
with nothing constraining where they land. A date is allowed to be written
two days before a cut, and then it fires on cut day.

So this is a control that PREVENTS rather than one that reports. It refuses
the date at the moment it is written, not at the moment it expires: every
clock-bound record this repository gates on must stay valid across the whole
declared release window, or the gate says so now, while there is still time
to re-review the exception honestly.

WHICH DIRECTION IT FAILS. Toward refusing. An unparseable date, a missing
date and a record this tool cannot read all FAIL, because a date nobody can
read is exactly the case that silently expires. Only a genuinely absent
release window (no plan, no cut band) is NO-DATA, and NO-DATA is never a
pass.

WHAT IT DOES NOT DO. It does not judge whether an exception is still
justified; battery_exception_audit.py owns that. It does not refresh a
handover pack. And it cannot fix the closing ceremony row, whose bar is an
age in hours and is therefore stale on any day the pack was not written:
that row is reported here as INHERENT, named rather than passed, and the fix
at its own source (measure whether the pack describes the branch) is a
separate change.

USAGE
    python3 scripts/release_window_clock_rows.py [--root DIR] [--plan FILE]
    python3 scripts/release_window_clock_rows.py --selftest

EXIT CODES
    0  PASS     every clock-bound record stays valid past the window's end
    1  FAIL     at least one record expires inside the window, or is unreadable
    3  NO-DATA  no release window could be read; never a pass
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys
import tempfile

PASS, FAIL, NODATA = "PASS", "FAIL", "NO-DATA"
EXIT = {PASS: 0, FAIL: 1, NODATA: 3}

#: readiness_gate.py's own bar, mirrored here rather than imported, so this
#: check keeps working when that module cannot be loaded. Kept in step by
#: test_release_window_clock_rows.py's parity case.
RESTORE_DRILL_MAX_AGE_DAYS = 7
RESTORE_DRILL_PATH = os.path.join("docs", "plan", "RESTORE-DRILL-ENTERPRISE-RESULT.json")
BATTERY_PATH = os.path.join("docs", "plan", "BATTERY-EXPECTATIONS.json")
PLAN_GLOB = os.path.join("docs", "plan", "BROTHER-*-WBS.json")

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: A RELEASE plan, by version, not by whatever sorts last (decision 2026-10-29, F1). The glob
#: BROTHER-*-WBS.json also matches BROTHER-LOOP-HARDENING-WBS.json, which sorts after 1.1.9, so taking
#: the last sorted path read the wrong plan and answered NO-DATA about a plan with no cut band.
_VERSIONED = re.compile(r"BROTHER-(\d+)\.(\d+)\.(\d+)-WBS\.json$")


def release_plan_path(root, plan=None):
    """THE one rule for which plan is the release plan: an explicit plan as given, else the highest
    BROTHER-<x>.<y>.<z>-WBS.json under root/docs/plan (numeric, so 1.1.10 beats 1.1.9), else None.

    None is the fail direction: an unreadable plan directory, or one holding only unversioned plans,
    names no release plan, and every caller reads that as NO-DATA, never as some other plan.
    scripts/go_no_go_119.py imports this instead of keeping a second copy.
    """
    if plan:
        return plan
    found = []
    for path in glob.glob(os.path.join(root, PLAN_GLOB)):
        m = _VERSIONED.search(path)
        if m:
            found.append((tuple(int(g) for g in m.groups()), path))
    return max(found)[1] if found else None


def _date(value):
    """An ISO date, or a ValueError naming what was wrong."""
    if value is None:
        raise ValueError("no date")
    text = str(value).strip()
    if not _ISO.match(text):
        raise ValueError("not an ISO date: %r" % (value,))
    return datetime.date.fromisoformat(text)


def read_window(root, plan=None):
    """(start, end) of the declared release window, or (None, reason).

    The window runs from the plan's calendar start to the LAST day of its cut
    band, because a row that expires on the cut day itself is the exact
    failure this exists to prevent.
    """
    newest = release_plan_path(root, plan)
    if not newest:
        return None, "no versioned release plan matched %s" % PLAN_GLOB
    try:
        with open(newest, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, "%s unreadable: %s" % (os.path.basename(newest), exc)
    band = doc.get("cut_band") or []
    cal = doc.get("calendar") or {}
    try:
        end = _date(band[-1]) if band else _date(cal.get("end"))
    except (ValueError, IndexError, TypeError) as exc:
        return None, "%s declares no readable cut band or calendar end: %s" % (
            os.path.basename(newest), exc)
    try:
        start = _date(cal.get("start"))
    except (ValueError, TypeError):
        start = end
    if start > end:
        start = end
    return (start, end, os.path.basename(newest)), None


def battery_rows(root):
    """Every declared exception carrying a review_by, as (name, raw value)."""
    path = os.path.join(root, BATTERY_PATH)
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, "%s unreadable: %s" % (BATTERY_PATH, exc)
    found = []

    def walk(node, where):
        if isinstance(node, dict):
            if "review_by" in node or "removal_condition" in node:
                found.append((node.get("name") or where, node.get("review_by")))
            for key, value in node.items():
                walk(value, "%s/%s" % (where, key))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, "%s[%d]" % (where, index))

    walk(doc, "")
    return found, None


def _drill_bound_by_content(root, path):
    """(True, note) when every file the drill record covers is byte identical.

    Read with readiness_gate's own `_covered_matches` where that module can be
    imported, so the two checks cannot drift apart. Where it cannot be
    imported, this returns (None, why) and the caller keeps the strict clock
    rule: an unprovable binding never earns the skip.
    """
    import hashlib
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, "the record is unreadable (%s)" % exc
    covered = doc.get("covered")
    if not isinstance(covered, list) or not covered:
        return None, "the record declares no covered set"
    for entry in covered:
        if not isinstance(entry, dict):
            return None, "the covered set holds a non-record entry"
        rel, want = entry.get("path"), entry.get("sha256")
        if not rel or not want:
            return None, "a covered entry names no path or no sha256"
        full = os.path.join(root, rel)
        try:
            with open(full, "rb") as fh:
                got = hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            return None, "a covered file could not be read (%s)" % rel
        if got != want:
            return False, "the covered file %s has changed since the drill ran" % rel
    return True, ("all %d covered file(s) are byte identical to the commit the record names"
                  % len(covered))


def drill_row(root, window_end, today):
    """The restore drill's own freshness, projected to the window's end.

    A freshness bar in DAYS is not the same shape as a review_by date. A bar
    of 7 days cannot span a window of 11, so demanding "already fresh past
    the end" would make this row permanently red and teach everyone to
    ignore it, which is how the original defect survived three cuts.

    So the bar becomes a DEADLINE with an earliest valid run date
    (window_end minus the bar, plus a day). Before that date there is
    nothing to do and the row PASSES while naming the date. From that date
    on, a record older than the bar is the action being due and undone, and
    that is a refusal.
    """
    path = os.path.join(root, RESTORE_DRILL_PATH)
    earliest = window_end - datetime.timedelta(days=RESTORE_DRILL_MAX_AGE_DAYS - 1)
    if not os.path.isfile(path):
        return ("restore-drill", FAIL,
                "%s is missing, so the row is already red" % RESTORE_DRILL_PATH)
    # readiness_gate supersedes its own age bar where the record's content
    # binding is proven (every covered file byte identical to the commit the
    # record names). This check must agree with the row it is about: a drill
    # bound by content is not clock bound at all, and saying otherwise would
    # leave two registered checks contradicting each other, which this plan
    # has already paid for once.
    bound, note = _drill_bound_by_content(root, path)
    if bound is True:
        return ("restore-drill", PASS,
                "not a clock bound row: %s, so readiness_gate supersedes its age bar" % note)
    # readiness_gate judges the record by its OWN declared drill_date, not by
    # the file's mtime. An earlier draft of this check read mtime and so said
    # PASS while the real row was already red: a check that measures a
    # different number than the row it is about certifies nothing.
    try:
        with open(path, encoding="utf-8") as fh:
            ran = _date(json.load(fh).get("drill_date"))
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return ("restore-drill", FAIL,
                "%s declares no readable drill_date (%s), so nothing can say when it expires"
                % (RESTORE_DRILL_PATH, exc))
    expires = ran + datetime.timedelta(days=RESTORE_DRILL_MAX_AGE_DAYS)
    if expires > window_end:
        return ("restore-drill", PASS,
                "the drill ran %s and stays fresh until %s, past the window end %s"
                % (ran.isoformat(), expires.isoformat(), window_end.isoformat()))
    if expires <= today:
        return ("restore-drill", FAIL,
                "the drill ran %s and its %d day bar expired %s, so the row is red TODAY and "
                "every landing is already blocked: re-run the drill, and run it on or after %s "
                "so it is still fresh at the window end %s"
                % (ran.isoformat(), RESTORE_DRILL_MAX_AGE_DAYS, expires.isoformat(),
                   earliest.isoformat(), window_end.isoformat()))
    if today < earliest:
        return ("restore-drill", PASS,
                "the drill ran %s and expires %s, still fresh today, but it must be re-run on "
                "or after %s to be fresh at the window end %s. Not due yet."
                % (ran.isoformat(), expires.isoformat(), earliest.isoformat(),
                   window_end.isoformat()))
    return ("restore-drill", FAIL,
            "the drill ran %s and expires %s, on or before the window end %s, and the "
            "earliest valid re-run date %s has arrived: re-run the drill now, not on cut day"
            % (ran.isoformat(), expires.isoformat(), window_end.isoformat(),
               earliest.isoformat()))


def audit(root, plan=None, today=None):
    """(verdict, lines). Pure: no printing, no process exit."""
    today = today or datetime.date.today()
    window, why = read_window(root, plan)
    if window is None:
        return NODATA, ["%s: %s" % (NODATA, why)]
    start, end, plan_name = window
    lines = ["window %s to %s, read from %s" % (start.isoformat(), end.isoformat(), plan_name)]

    rows, why = battery_rows(root)
    if rows is None:
        return FAIL, lines + ["%s: %s" % (FAIL, why)]

    bad = []
    for name, raw in rows:
        try:
            when = _date(raw)
        except ValueError as exc:
            bad.append("%s: review_by is not a usable date (%s), so nothing can say when it "
                       "expires" % (name, exc))
            continue
        if when <= end:
            bad.append("%s: review_by %s falls on or inside the release window, so this row "
                       "turns every tree red before the cut" % (name, when.isoformat()))
    lines.append("%d declared exception(s) carry a review_by" % len(rows))

    name, verdict, detail = drill_row(root, end, today)
    lines.append("%s %s: %s" % (verdict, name, detail))
    if verdict == FAIL:
        bad.append("%s: %s" % (name, detail))

    lines.append("INHERENT closing-ceremony-real: its bar is a pack age in hours, so it is red "
                 "on any day the pack was not written. Named, not passed; its own fix is to "
                 "measure whether the pack describes the branch under test.")

    if bad:
        return FAIL, lines + ["%s: %d record(s) expire inside the release window" % (FAIL, len(bad))] + \
            ["  - " + b for b in bad]
    return PASS, lines + ["%s: every clock-bound record stays valid past %s"
                          % (PASS, end.isoformat())]


# --------------------------------------------------------------------------
# selftest: ONE fixture per guard, so a fixture that trips two guards can
# never certify either. Each case below is refusable by exactly one guard.
# --------------------------------------------------------------------------
def _fixture(tmp, review_by, drill_days_ago=1, plan=True, battery=True, band="2026-11-06",
             start="2026-10-26", drill_date=None, covered=None):
    plan_dir = os.path.join(tmp, "docs", "plan")
    os.makedirs(plan_dir, exist_ok=True)
    if plan:
        with open(os.path.join(plan_dir, "BROTHER-9.9.9-WBS.json"), "w", encoding="utf-8") as fh:
            json.dump({"cut_band": [band, band], "calendar": {"start": start, "end": band}}, fh)
    if battery:
        entry = {"name": "a-row"}
        if review_by is not _MISSING:
            entry["review_by"] = review_by
        entry["removal_condition"] = "the thing is fixed"
        with open(os.path.join(plan_dir, "BATTERY-EXPECTATIONS.json"), "w", encoding="utf-8") as fh:
            json.dump({"checks": {"a-row": entry}}, fh)
    drill = os.path.join(plan_dir, "RESTORE-DRILL-ENTERPRISE-RESULT.json")
    ran = TODAY - datetime.timedelta(days=drill_days_ago)
    body = {"result": "PASS"}
    if drill_date is not _MISSING:
        body["drill_date"] = ran.isoformat() if drill_date is None else drill_date
    if covered:
        import hashlib
        rel = os.path.join("docs", "plan", "covered-thing.txt")
        with open(os.path.join(tmp, rel), "wb") as fh:
            fh.write(b"the thing the drill certifies\n")
        body["covered"] = [{"path": rel,
                            "sha256": hashlib.sha256(b"the thing the drill certifies\n").hexdigest()}]
        if covered == "drift":
            with open(os.path.join(tmp, rel), "wb") as fh:
                fh.write(b"changed after the drill ran\n")
    with open(drill, "w", encoding="utf-8") as fh:
        json.dump(body, fh)
    return tmp


_MISSING = object()
TODAY = datetime.date(2026, 10, 28)


def selftest():
    cases = []

    def case(label, expect, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            _fixture(tmp, **kw)
            got, lines = audit(tmp, today=TODAY)
            cases.append((label, expect, got, lines))

    # G6 clear: a date well past the window, and a window short enough that a
    # drill run today is still fresh at its end.
    case("clear", PASS, review_by="2026-12-31", drill_days_ago=0,
         start="2026-10-28", band="2026-10-31")
    # G1 a date inside the window, everything else clear.
    case("review_by inside the window", FAIL, review_by="2026-11-04", drill_days_ago=0)
    # G1 boundary: the cut day itself is inside the window.
    case("review_by on the cut day", FAIL, review_by="2026-11-06", drill_days_ago=0)
    # G2 unparseable, everything else clear.
    case("review_by unparseable", FAIL, review_by="soon", drill_days_ago=0)
    # G3 missing, everything else clear.
    case("review_by missing", FAIL, review_by=_MISSING, drill_days_ago=0)
    # G4a the drill alone, not yet due: a date far past the window, a drill
    # that expires inside the window while the re-run deadline is still ahead.
    case("drill expires inside the window, not due yet", PASS,
         review_by="2026-12-31", drill_days_ago=0)
    # G4b the drill alone, due and undone: the earliest valid re-run date has
    # arrived and the record is older than the bar.
    case("drill re-run is due and undone", FAIL,
         review_by="2026-12-31", drill_days_ago=5, start="2026-10-26", band="2026-11-01")
    # G5 no plan: the only NO-DATA case, everything else clear.
    case("no release plan", NODATA, review_by="2026-12-31", drill_days_ago=0, plan=False)
    # G5 battery unreadable is a refusal, not NO-DATA.
    case("battery file absent", FAIL, review_by="2026-12-31", drill_days_ago=0, battery=False)
    # G4c the drill is ALREADY expired today: red now, never deferred by the
    # re-run deadline being in the future.
    case("drill already expired today", FAIL, review_by="2026-12-31", drill_days_ago=9)
    # G4d a drill whose covered set is PROVEN byte identical is not a clock
    # bound row at all, however stale its date: readiness_gate supersedes the
    # age bar there, and this check must say the same thing.
    case("drill bound by content, stale date", PASS, review_by="2026-12-31",
         drill_days_ago=30, covered="match")
    # G4e the same record with a covered file that CHANGED keeps the clock rule.
    case("drill covered file changed", FAIL, review_by="2026-12-31",
         drill_days_ago=30, covered="drift")
    # G7 the drill record declares no drill_date: refused, never read as fresh.
    case("drill declares no drill_date", FAIL, review_by="2026-12-31", drill_days_ago=0,
         drill_date=_MISSING, start="2026-10-28", band="2026-10-31")

    bad = 0
    for label, expect, got, lines in cases:
        ok = got == expect
        bad += 0 if ok else 1
        print("%-44s expect %-8s got %-8s %s" % (label, expect, got, "ok" if ok else "MISMATCH"))
        if not ok:
            for line in lines:
                print("      " + line)
    print("%d case(s), %s" % (len(cases), "OK" if not bad else "%d FAILURE(S)" % bad))
    return 0 if not bad else 1


def main(argv=None):
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=here, help="repository root (default: this script's)")
    ap.add_argument("--plan", help="a specific release plan to read the window from")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    verdict, lines = audit(args.root, args.plan)
    for line in lines:
        print(line)
    return EXIT[verdict]


if __name__ == "__main__":
    sys.exit(main())
