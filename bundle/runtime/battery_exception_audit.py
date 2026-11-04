#!/usr/bin/env python3
"""battery_exception_audit: enforces the one rule docs/plan/BATTERY-EXPECTATIONS.json
already states in its own top comment and nothing before this script measured:
AN EXCEPTION PAST ITS REVIEW DATE TURNS BLOCKING, because a failure cemetery is
not allowed.

WHY THIS EXISTS. battery_verdict.py already treats a past-due review_by as
expired at classification time (see its _expired()), but only when a real
check_all.sh run is being judged. Nobody was reading the expectations file on
its own to ask "which of these five exceptions are about to rot", so a
reviewer's next look could be weeks after the review_by had already passed,
with nothing in between having said so. This script reads the expectations
file alone, no battery run required, and answers exactly that question.

MEASURED, 2026-09-18: of the five declared exceptions, three carry a
review_by within the next seven days (release-note-perturb and
release-closeout-sign-self on 2026-09-19, evad-score on 2026-09-18 itself,
today). None had yet passed. This is why the DUE_SOON_WINDOW_DAYS below is
seven, not one or thirty: a one-day window would have caught only evad-score
and given a reader of this audit no lead time on the other two; a
thirty-day window would permanently flag two of the five (their reviews are
genuinely twenty-eight days out at time of writing) and teach a reader to
ignore the warning. Seven days is a week's notice, long enough to act,
short enough that a WARN still means "soon" rather than "eventually".

FOUR OUTCOMES for a declared exception's review_by, never conflated because
each needs a different fix:
  EXPIRED             review_by has already passed. Fix: the owner either
                       fixes the underlying check so the exception is
                       deleted, or re-reviews it and writes a new, later
                       review_by. Never extend the old date in place; a
                       silently renewed date is exactly the failure
                       cemetery this rule exists to prevent.
  MISSING review_by    the entry never named one. Fix: same as EXPIRED
                       (the owner names a real review_by), because an
                       undated exception is permanent by accident, which is
                       indistinguishable from a cemetery entry that nobody
                       ever intended to be permanent.
  UNPARSEABLE          a review_by is present but is not a real ISO date
                       (typo, wrong format, a sentence instead of a date).
                       Fix: correct the string. Reported separately from
                       MISSING because "there is no date" and "there is a
                       date nobody can parse" are different bugs with
                       different authors: the first is an omission, the
                       second is usually a fat-fingered edit of a date that
                       used to be valid.
  DUE_SOON             review_by is real, in the future, but inside the
                       window above. WARNS, never fails: the point is to
                       give whoever reviews these exceptions a heads-up
                       before the expiry above bites, not to punish a date
                       that has not passed yet.
A review_by that equals "now" exactly is ruled DUE_SOON, not EXPIRED: the
review is due today, not overdue. This mirrors battery_verdict.py's own
_expired(), which uses a strict less-than against today for the same
reason, and this script keeps that convention rather than inventing a
second one for the same date field.

STALE DECLARATIONS. An exception can also go stale a third way: the check
it names stops existing in scripts/check_all.sh (renamed, deleted, folded
into another check) while the entry keeps naming it. This is dead weight
that hides the live exceptions among the still-real ones, so it is reported
too (AuditResult.stale), sourced from battery_verdict.load_check_all so this
script does not grow a second parser for check_all.sh's run_check lines.
When check_all.sh cannot be read, stale is None (not checked) rather than
an empty list (checked, found none): those two are different facts and
collapsing them would hide a broken cross-reference behind a clean report.

THE BAD STATE A GREEN RUN WOULD ALSO PASS: an audit that examined zero
exceptions (an empty "checks" object, or a bug that silently iterates
nothing, for example reading the wrong top-level key) prints EXACTLY the
same "nothing expired, nothing missing, nothing unparseable" shape as one
that examined five and found every one of them current. ok=True must never
be reachable by that first path. audit() asserts a POSITIVE examined count
before it may report ok=True; see the "examined > 0" term in its ok
computation. A caller who only checks "no problems in the lists" instead of
reading ok would still be exposed to this, which is exactly why ok exists
as its own field instead of leaving the caller to infer it.

UNKNOWN INPUT RAISES. A "class" this script does not recognise (typo, a
new class nobody taught this file about yet), a malformed entry, a missing
or unreadable expectations file, or a file whose "checks" key is not an
object all raise AuditError. None of these read as "zero exceptions
declared": that would be exactly the silent-empty-pass bad state above,
reached a different way. A missing or malformed file is not evidence of an
empty, healthy exceptions list, it is evidence that nobody can currently
tell what the exceptions list says.

CONTINGENCY. main() exits 0 (OK, everything examined is current, possibly
with due-soon warnings printed), 1 (FAIL: at least one exception is
EXPIRED, MISSING its review_by, or carries an UNPARSEABLE one; every one is
named with its class, its recorded reason, its date, and its
removal_condition when the entry declares one), or 2 (NO-DATA: the
expectations file itself could not be read or parsed; this is never
silently treated as zero exceptions and never as a pass). The rule's own
words say what an owner does about an EXPIRED entry: fix the check it
shelters, or re-justify the exception with a new review_by; never extend
the existing date without re-examining the reason it was granted. Who is
told: whoever reads this script's exit code and printed lines, which means
whoever runs scripts/check_all.sh, once the orchestrator registers this
check there (this script does not register itself, see the WBS unit that
produced it).

Python 3.9 floor, standard library only, no network, no new dependency.
This module imports one local sibling, battery_verdict, reusing its CLASSES
set (the vocabulary of recognised exception classes) and its
load_check_all() (the run_check line parser for scripts/check_all.sh),
because retyping either here would let this file and battery_verdict.py
answer different questions about the same declarations, exactly the trap
scripts/orchestrator_invariants.py's own docstring warns against.
"""
import argparse
import dataclasses
import datetime
import json
import os
import sys

import battery_verdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_EXPECTATIONS = battery_verdict.EXPECTATIONS_DEFAULT
DEFAULT_CHECK_ALL = battery_verdict.CHECK_ALL

# See the module docstring's MEASURED paragraph for why seven, not one or
# thirty.
DUE_SOON_WINDOW_DAYS = 7

KNOWN_CLASSES = battery_verdict.CLASSES


class AuditError(Exception):
    """The expectations file could not be read, parsed, or trusted enough to
    audit: an unreadable or non-JSON file, a non-object root, a missing or
    non-object "checks" key, a non-object entry, or an entry declaring a
    class outside KNOWN_CLASSES. Every one of these means "nobody can
    currently tell what the exceptions list says", which is never the same
    fact as "the exceptions list says there are none"."""


@dataclasses.dataclass
class AuditResult:
    """One audit's findings. `expired`, `missing_review_by`, `unparseable`
    and `due_soon` are each a list of dicts (see audit()'s docstring for the
    keys each list's dicts carry); `stale` is a list of check names, or None
    when scripts/check_all.sh could not be read to check against.
    `examined` is how many entries in "checks" this audit actually looked
    at: the positive-count guard against the bad state named in this
    module's docstring lives on this field. `ok` is True only when
    examined is positive and expired, missing_review_by and unparseable are
    all empty; due_soon does not affect it; due_soon is a warning, not a
    failure, per the module docstring's FOUR OUTCOMES."""
    expired: list
    due_soon: list
    missing_review_by: list
    unparseable: list
    stale: list
    examined: int
    ok: bool


def _coerce_today(now):
    """A real datetime.date, from `now` (already a date, or an ISO date
    string), or the real clock when now is None. This is the one place in
    this module the real clock may be read; audit()'s own comparisons never
    call datetime.date.today() themselves, only this helper does, so a test
    that always passes `now` explicitly never depends on the machine's
    clock (rule 5: never read the real clock inside a decision)."""
    if now is None:
        return datetime.date.today()
    if isinstance(now, datetime.date):
        return now
    return datetime.date.fromisoformat(str(now))


def audit(expectations_path=None, *, now=None, check_all_path=None,
          window_days=None):
    """Read `expectations_path` (default the real BATTERY-EXPECTATIONS.json)
    and return an AuditResult comparing every declared exception's
    review_by against `now` (a datetime.date, an ISO date string, or the
    real date when omitted).

    Each `expired` entry is a dict with "name", "class", "reason",
    "review_by" and "removal_condition" (rule 7: enough to act on without
    re-opening the file). Each `missing_review_by` entry carries "name",
    "class" and "reason". Each `unparseable` entry carries "name", "class",
    the raw "review_by" value and "error" (str of the ValueError
    date.fromisoformat raised). Each `due_soon` entry carries "name",
    "class", "review_by" and "days_until".

    Only the top-level "checks" object is audited. The sibling "critical"
    object in the same file is a different mechanism (which capabilities
    must appear and PASS in a real battery run, judged by
    battery_verdict._judge_critical) with no review_by field of its own;
    this script does not touch it.

    A literal duplicate key inside one JSON object already collapses to its
    last occurrence before json.loads returns, so there is no "duplicate
    check name" state left for this function to observe or report; two
    declarations for the same check living in two different files is a
    different problem (multiple expectations files), out of scope for a
    script that reads one path.

    Raises AuditError for anything that means "the file cannot be trusted
    enough to audit" (see AuditError's own docstring); never returns a
    result claiming zero exceptions were found unless "checks" really is
    an empty object, and even then `ok` reads False (see AuditResult's
    docstring on `examined`)."""
    path = expectations_path or DEFAULT_EXPECTATIONS
    window = DUE_SOON_WINDOW_DAYS if window_days is None else window_days

    try:
        today = _coerce_today(now)
    except ValueError as exc:
        raise AuditError("now=%r is not a real ISO date: %s" % (now, exc)) from exc

    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        raise AuditError("could not read expectations %s: %s" % (path, exc)) from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise AuditError("expectations %s is not valid JSON: %s" % (path, exc)) from exc
    if not isinstance(data, dict):
        raise AuditError("expectations %s root is not an object" % path)
    checks = data.get("checks")
    if not isinstance(checks, dict):
        raise AuditError(
            "expectations %s has no \"checks\" object (found %r)"
            % (path, type(checks).__name__))

    expired = []
    due_soon = []
    missing_review_by = []
    unparseable = []
    examined = 0

    for name, entry in checks.items():
        if not isinstance(entry, dict):
            raise AuditError(
                "%s: entry in %s is not an object (found %r)"
                % (name, path, type(entry).__name__))
        cls = entry.get("class")
        if cls not in KNOWN_CLASSES:
            raise AuditError(
                "%s: declares a class this audit does not recognise: %r "
                "(known: %s)" % (name, cls, sorted(KNOWN_CLASSES)))
        examined += 1
        reason = entry.get("reason") or ""
        removal_condition = entry.get("removal_condition") or ""
        review_by_raw = entry.get("review_by")

        if not review_by_raw:
            missing_review_by.append(
                {"name": name, "class": cls, "reason": reason})
            continue
        try:
            review_by = datetime.date.fromisoformat(str(review_by_raw))
        except ValueError as exc:
            unparseable.append({
                "name": name, "class": cls,
                "review_by": review_by_raw, "error": str(exc),
            })
            continue

        if review_by < today:
            expired.append({
                "name": name, "class": cls, "reason": reason,
                "review_by": review_by.isoformat(),
                "removal_condition": removal_condition,
            })
        elif (review_by - today).days <= window:
            due_soon.append({
                "name": name, "class": cls,
                "review_by": review_by.isoformat(),
                "days_until": (review_by - today).days,
            })

    cap = check_all_path or DEFAULT_CHECK_ALL
    try:
        commands = battery_verdict.load_check_all(cap)
    except OSError:
        stale = None
    else:
        stale = sorted(name for name in checks if name not in commands)

    ok = (examined > 0 and not expired and not missing_review_by
          and not unparseable)

    return AuditResult(
        expired=expired, due_soon=due_soon,
        missing_review_by=missing_review_by, unparseable=unparseable,
        stale=stale, examined=examined, ok=ok,
    )


def _print_report(result, expectations_path, check_all_path):
    for item in result.expired:
        print("EXPIRED %s (class=%s, review_by=%s): %s (removal_condition: %s)"
              % (item["name"], item["class"], item["review_by"],
                 item["reason"] or "no reason recorded",
                 item["removal_condition"] or "none declared"))
    for item in result.missing_review_by:
        print("MISSING-REVIEW-BY %s (class=%s): %s"
              % (item["name"], item["class"],
                 item["reason"] or "no reason recorded"))
    for item in result.unparseable:
        print("UNPARSEABLE %s (class=%s) review_by=%r: %s"
              % (item["name"], item["class"], item["review_by"], item["error"]))
    for item in result.due_soon:
        print("DUE-SOON %s (class=%s) review_by=%s, %d day(s) away"
              % (item["name"], item["class"], item["review_by"],
                 item["days_until"]))
    if result.stale is None:
        print("NO-DATA: could not read %s to check for stale check names"
              % check_all_path)
    else:
        for name in result.stale:
            print("STALE %s: declared in %s but not registered by any "
                  "run_check in %s" % (name, expectations_path, check_all_path))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--expectations", default=DEFAULT_EXPECTATIONS,
                     help="path to the declared-exceptions JSON")
    ap.add_argument("--check-all", default=DEFAULT_CHECK_ALL,
                     help="scripts/check_all.sh, read only to find stale "
                          "declarations for checks it no longer registers")
    ap.add_argument("--window-days", type=int, default=DUE_SOON_WINDOW_DAYS,
                     help="a review_by this many days out or fewer WARNS as "
                          "due soon (default %d)" % DUE_SOON_WINDOW_DAYS)
    ap.add_argument("--now", default=None,
                     help="ISO date to treat as today (tests only; default "
                          "the real clock)")
    args = ap.parse_args(argv)

    try:
        result = audit(args.expectations, now=args.now,
                        check_all_path=args.check_all,
                        window_days=args.window_days)
    except AuditError as exc:
        print("NO-DATA: %s" % exc)
        return 2

    _print_report(result, args.expectations, args.check_all)

    blocking = bool(result.expired or result.missing_review_by
                     or result.unparseable)
    if blocking:
        print("FAIL: %d examined, %d expired, %d missing review_by, "
              "%d unparseable" % (result.examined, len(result.expired),
                                   len(result.missing_review_by),
                                   len(result.unparseable)))
        return 1
    print("OK: %d exception(s) examined, %d due soon, none expired, "
          "missing or unparseable" % (result.examined, len(result.due_soon)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
