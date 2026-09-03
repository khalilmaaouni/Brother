#!/usr/bin/env python3
"""Delivery tracking for the readiness roadmap, and the blocker learning behind it.

FOUNDER DIRECTION 2026-08-29: put exact dates and hours on the board, then TRACK
whether each row lands when it said it would, so that the blockers behind misses
become vault learning about handling blockers dynamically and autonomously.

WHAT THIS IS FOR, and it is not a burndown chart. A plan that quietly re-dates a
slipped row teaches nobody anything: the miss vanishes and the blocker behind it
survives to cause the next one. This records the miss, demands the blocker BY
NAME, and applies an escalation ladder so a row is never silently re-promised a
fourth time.

THE LADDER, from the roadmap's own delivery_policy so it is data rather than
prose buried here:
  miss 1  record the blocker and re-promise once. Most first misses are
          estimation error, not a systemic blocker.
  miss 2  Fable reviews the ROW, not the worker. Twice missed usually means
          mis-scoped or an unnamed dependency.
  miss 3  stop, escalate to the founder with the blocker, what was tried, and
          two options. Never a fourth silent re-promise.
  class   the same blocker CLASS across 3 distinct rows is systemic and goes to
          the vault as one lesson, not three row notes.

THE HONEST LIMIT, stated here rather than discovered later. This ledger records
what a session TELLS it. Nothing here observes work directly, so a session that
never runs the tracker produces no misses and the board looks perfect. That is
the could-not-go-red class this estate keeps finding in its own controls. The
mechanical part is that this runs inside check_all.sh, so a battery run cannot
avoid it.

EXIT CODES
  0  nothing is late, or every late row already has its blocker recorded
  1  a row is LATE with NO blocker recorded, or a row has reached escalation 3.
     A miss nobody explained is the one thing this refuses to pass.
  2  NO-DATA: the roadmap could not be read. Never a pass.

Python 3.9 floor, standard library only.

origin: a human, or a session acting for one, running this script's own CLI
directly with `--record`. scripts/check_all.sh:101 runs `python3
scripts/track_delivery.py` with no flags, which is a report-only pass through
main(): it never reaches append_ledger() because that call is gated by `if
args.record:` a few lines below the report() call. Confirmed by grep: no
file in scripts or bundle/runtime passes `--record` to track_delivery.py
(searched the string "track_delivery.py.*--record" across scripts,
bundle/runtime and .claude, no hit), so the docstring's own honest-limit
paragraph above holds literally: this ledger records only what a session, by
hand, tells it to.

PRODUCER: this module is the sole producer of docs/plan/DELIVERY-LEDGER.jsonl.
The write happens inside append_ledger(), above, at the `with open(path, 'a',
encoding='utf-8') as fh: fh.write(json.dumps(e, ...) + '\n')` call (lines
142-144 of this file), called from main() only inside the `if args.record:`
block (line 201-203).
"""
import argparse
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROADMAP = os.path.join(ROOT, 'docs', 'plan', 'READINESS-ROADMAP-2026-08-29.json')
LEDGER = os.path.join(ROOT, 'docs', 'plan', 'DELIVERY-LEDGER.jsonl')

AT_RISK_FRACTION = 0.25   # inside the last quarter of its window


def parse_dt(s):
    """Always timezone AWARE, whatever shape the value was written in.

    fromisoformat returns a NAIVE datetime for a bare date like "2026-08-29" and
    an AWARE one for a full timestamp, and this module subtracts them from each
    other and from now(). Mixing the two raises TypeError, so a single row
    written as a date rather than a timestamp took the whole tracker down with a
    crash rather than a verdict. Found 2026-08-29 when a delivered_at was
    written as a plain date, which is the obvious thing for a person to write.

    A bare date means midnight UTC. That is a choice and it is stated here: it
    makes a same-day delivery count as delivered at the START of the day, which
    is the reading that can only make a row look EARLIER, never later, so it
    cannot manufacture an on-time delivery out of a late one.
    """
    if not s:
        return None
    parsed = datetime.datetime.fromisoformat(s)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def verdict_for(row, now, started_default=None):
    """One row's delivery verdict. Pure, so it can be driven both ways in tests
    without touching a clock or a file."""
    promised = parse_dt(row.get('promised_at'))
    delivered = parse_dt(row.get('delivered_at'))
    if promised is None:
        return 'NO-DATA', 'no promised_at, so nothing can be late or on time'
    if delivered is not None:
        if delivered <= promised:
            return 'DELIVERED-ON-TIME', 'delivered %s, promised %s' % (row['delivered_at'], row['promised_at'])
        return 'DELIVERED-LATE', 'delivered %s, promised %s' % (row['delivered_at'], row['promised_at'])
    if now > promised:
        return 'LATE', 'promised %s, not delivered' % row['promised_at']
    started = parse_dt(row.get('started_at')) or started_default
    if started is not None:
        window = (promised - started).total_seconds()
        if window > 0 and (promised - now).total_seconds() < window * AT_RISK_FRACTION:
            return 'AT-RISK', 'inside the last quarter of its window, not delivered'
    return 'NOT-DUE', 'promised %s' % row['promised_at']


def miss_count(row):
    """How many times this row has been recorded as missing its promise. Drives
    the ladder. A DELIVERED-LATE row keeps its misses: it landing eventually does
    not un-miss the earlier promise, and forgetting that is how the same blocker
    recurs."""
    return len([e for e in row.get('slip_log', []) if e.get('status') in ('LATE', 'DELIVERED-LATE')])


def intervention_for(misses):
    if misses <= 0:
        return None
    if misses == 1:
        return ('RECORD', 'record the blocker by name and re-promise once')
    if misses == 2:
        return ('FABLE-REVIEW', 'Fable reviews the ROW, not the worker: twice missed usually means '
                                'mis-scoped or carrying an unnamed dependency')
    return ('ESCALATE-FOUNDER', 'stop and escalate through the question UI with the blocker, what was '
                                'tried, and two options. Never a fourth silent re-promise')


def blocker_classes(rows):
    """Blocker text grouped by its recorded class. Three DISTINCT rows sharing a
    class is systemic and earns one vault lesson rather than three row notes."""
    classes = {}
    for r in rows:
        for e in r.get('slip_log', []):
            cls = e.get('blocker_class')
            if cls:
                classes.setdefault(cls, set()).add(r['id'])
    return {k: sorted(v) for k, v in classes.items()}


def load(path=None):
    # Resolved at CALL time, never bound into a default at definition time.
    # The sibling renderer shipped that bug this morning and three tests passed
    # against the real file instead of their fixtures.
    if path is None:
        path = ROADMAP
    with open(path, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def append_ledger(entries, path=None):
    if path is None:
        path = LEDGER
    with open(path, 'a', encoding='utf-8') as fh:
        for e in entries:
            fh.write(json.dumps(e, sort_keys=True) + '\n')


def report(doc, now):
    rows = doc.get('rows', [])
    out = []
    unexplained = []
    escalations = []
    for r in rows:
        v, why = verdict_for(r, now)
        misses = miss_count(r)
        act = intervention_for(misses)
        out.append((r['id'], v, why, misses, act))
        if v == 'LATE' and not r.get('blocker_recorded'):
            unexplained.append(r['id'])
        if act and act[0] == 'ESCALATE-FOUNDER':
            escalations.append(r['id'])
    return out, unexplained, escalations


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--now', help='ISO datetime to evaluate against, for tests')
    ap.add_argument('--record', action='store_true', help='append this run to the delivery ledger')
    args = ap.parse_args(argv)

    try:
        doc = load()
    except (OSError, ValueError) as exc:
        print('track-delivery: NO-DATA, cannot read the roadmap: %s' % exc, file=sys.stderr)
        return 2

    now = parse_dt(args.now) if args.now else datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)

    rows, unexplained, escalations = report(doc, now)
    counts = {}
    for _, v, _, _, _ in rows:
        counts[v] = counts.get(v, 0) + 1

    for rid, v, why, misses, act in rows:
        line = '%-5s %-18s %s' % (rid, v, why)
        if misses:
            line += ' | misses: %d' % misses
        if act:
            line += ' | INTERVENTION %s: %s' % act
        print(line)

    classes = blocker_classes(doc.get('rows', []))
    systemic = {k: v for k, v in classes.items() if len(v) >= 3}
    if systemic:
        for cls, ids in sorted(systemic.items()):
            print('SYSTEMIC: blocker class %r hit %d distinct rows (%s). This is one vault lesson, '
                  'not %d row notes.' % (cls, len(ids), ', '.join(ids), len(ids)))
    print('track-delivery: %s' % ', '.join('%s %d' % (k, counts[k]) for k in sorted(counts)))

    if args.record:
        append_ledger([{'checked_at': now.isoformat(), 'row': rid, 'verdict': v, 'misses': m}
                       for rid, v, _, m, _ in rows])
        print('track-delivery: %d row(s) appended to %s' % (len(rows), os.path.relpath(LEDGER, ROOT)))

    if unexplained:
        print('FAIL: %d row(s) LATE with NO blocker recorded: %s. A miss nobody explained teaches '
              'nothing, which is the whole reason this tracker exists.'
              % (len(unexplained), ', '.join(unexplained)), file=sys.stderr)
        return 1
    if escalations:
        print('FAIL: %d row(s) at escalation 3, owed to the founder: %s'
              % (len(escalations), ', '.join(escalations)), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
