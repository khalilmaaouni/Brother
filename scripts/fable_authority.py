#!/usr/bin/env python3
"""W3 of the readiness board: who decides when the founder is away, and how.

WHY THIS EXISTS. A decision sat open on the board carrying AWAITING FOUNDER
with no fallback at all while four other streams kept running. Absence is
never guessed, it is measured: a stated window with no founder message, AND a
decision that is actually blocking progress. Either half alone is not
absence: a quiet founder with nothing blocked is not absence, and a blocked
decision with a present founder is not absence either. See check_absence().

THREE CLASSES, and only three, decide who rules once absence is real:

    GREEN   reversible and cheap. Fable decides and continues, no record.
    AMBER   reversible but wide. Fable decides at maximum effort and the
            decision is RECORDED, status PROVISIONAL-FABLE, carrying the
            OVERRULE SENTENCE: the exact words the founder would say to
            reverse it. A record missing that sentence is refused at write
            time (record_amber() returns None rather than writing a partial
            record), because the sentence is what turns catching up into a
            one word act instead of a review.
    RED     never auto-decided: irreversible actions, credentials, raising a
            spend ceiling, publishing publicly, deleting data, purchases, and
            changes to the standing laws. A RED decision is REFUSED and
            QUEUED for the founder, never acted on, whoever is absent.

THE CLASSIFIER is a keyword heuristic, not a model call, so it is
deterministic and testable. It will miss phrasings a person would catch.
# ponytail: keyword heuristic with a known ceiling; upgrade to a model
# classifier (or a bigger signal list) if a real decision text misses both
# lists and lands GREEN by default when it should not have.

BOTH LOGS ARE APPEND ONLY, matching this estate's other write-time records
(write_ledger.py, fence_expiry.py's registry is the one exception, mutated in
place because a claim must be able to close): a record that can be rewritten
cannot settle an argument about what Fable decided while nobody was there to
watch. They live under .sbe/fable-authority/, gitignored local machine state,
matching write_ledger's .sbe/write-ledger.jsonl.

Exit codes, --classify: 0 for GREEN or AMBER, 1 for RED (refused and
queued), 2 for NO-DATA (classify() returned something that is none of the
three known labels; this must never be silently treated as GREEN).
Exit codes, --record-amber: 0 written, 1 refused (not an AMBER decision, or
no overrule sentence given), 2 NO-DATA (cannot write the log).
Exit codes, --check-absence: 0 not absent, 1 absent, 2 NO-DATA (the given
timestamp cannot be parsed).
Exit codes, --selftest: 0 all three classes drove correctly, 1 otherwise.

Python 3.9 floor, standard library only, no network.

PRODUCER: this module is the sole producer of both its logs. record_amber()
(below) does the only `open(path, 'a', ...)` for amber-records.jsonl, reached
only once an overrule sentence is present. queue_red() does the only
`open(path, 'a', ...)` for red-queue.jsonl, reached automatically the moment
classify() returns RED, since a RED refusal needs no further input from
anyone to be queued.
"""
import argparse
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECORD_DIR = os.path.join(ROOT, '.sbe', 'fable-authority')
AMBER_LOG = os.path.join(RECORD_DIR, 'amber-records.jsonl')
RED_QUEUE = os.path.join(RECORD_DIR, 'red-queue.jsonl')

GREEN = 'GREEN'
AMBER = 'AMBER'
RED = 'RED'
NODATA = 'NO-DATA'

# Never delegated. Matched against the lowercased decision text; the first
# hit wins and names the reason. Order matters only in that RED is checked
# before AMBER, so a decision that is both wide and irreversible reads RED.
RED_SIGNALS = [
    ('irreversible', 'an irreversible action'),
    ('credential', 'touches credentials'),
    ('password', 'touches credentials'),
    ('api key', 'touches credentials'),
    ('secret', 'touches credentials'),
    ('token', 'touches a credential-shaped secret'),
    ('spend ceiling', 'raises a spend ceiling'),
    ('raise the ceiling', 'raises a spend ceiling'),
    ('budget', 'touches spend or a budget'),
    ('publish', 'publishes to a public surface'),
    ('publicly', 'publishes to a public surface'),
    # Added 2026-09-01 after a recon measured classify('export to the public
    # repository') returning GREEN. Export IS this estate's publication verb: it is
    # the only route from the private hub to the public repository, so a classifier
    # that reads 'publish' but not 'export' misses the exact act it exists to catch.
    ('export to the public', 'publishes to a public surface'),
    ('public repository', 'publishes to a public surface'),
    ('public repo', 'publishes to a public surface'),
    ('export_public', 'publishes to a public surface'),
    ('release to', 'releases to an outside party'),
    ('ship to', 'releases to an outside party'),
    ('delete', 'deletes data'),
    ('deletion', 'deletes data'),
    ('purge', 'deletes data'),
    ('drop table', 'deletes data'),
    ('drop database', 'deletes data'),
    ('rm -rf', 'deletes data'),
    ('force push', 'an irreversible action against shared history'),
    ('purchase', 'a purchase'),
    ('buy ', 'a purchase'),
    (' pay ', 'a purchase'),
    ('payment', 'a purchase'),
    ('subscribe', 'a purchase'),
    ('standing law', 'changes a standing law'),
    ('change the law', 'changes a standing law'),
    ('amend the rule', 'changes a standing law'),
    ('claude.md', 'changes a standing law'),
    # Added 2026-09-03 (row E49): classify() had no acceptance signal at all, so a
    # delegated 'accept this delivery' read GREEN even though the north star chain
    # (products/brothermode/docs/NORTH-STAR-CHAIN.md, HUMAN DECISION) names acceptance
    # as the node that is "UNCONDITIONAL and no automation may remove it, shorten it
    # to a rubber stamp, or pre-approve it." The matcher is substring (kw in text), so
    # a bare 'accept' is deliberately NOT used here: it is a substring of 'acceptable'
    # and of ordinary text like 'accept the risk of a retry', neither of which is an
    # acceptance decision. The phrases below catch the actual act without catching
    # either; see test_fable_authority.py's negative control for both.
    ('accept the delivery', 'accepts a delivery, the human decision node'),
    ('accept this delivery', 'accepts a delivery, the human decision node'),
    ('accepts the delivery', 'accepts a delivery, the human decision node'),
    ('acceptance', 'touches acceptance, the human decision node'),
    ('accepted', 'touches acceptance, the human decision node'),
    ('sign off', 'signs off on the human decision node'),
    ('signs off', 'signs off on the human decision node'),
]

# Reversible but wide. Checked only once no RED signal matched.
AMBER_SIGNALS = [
    ('restructure', 'restructures shared layout'),
    ('redesign', 'redesigns a shared surface'),
    ('migrate', 'migrates shared state'),
    ('cross-repo', 'spans more than one repository'),
    ('cross repo', 'spans more than one repository'),
    ('cross-project', 'spans more than one project'),
    ('production data', 'touches production data'),
    ('affects every', 'affects every consumer'),
    ('affects all', 'affects every consumer'),
    ('shared state', 'touches state other sessions read'),
    ('multiple sessions', 'touches state other sessions read'),
    ('rename the package', 'renames a shared public name'),
    ('change the api', 'changes a shared contract'),
    ('reprioritize', 'reorders the shared roadmap'),
    ('reorder the backlog', 'reorders the shared roadmap'),
    ('change the schedule', 'moves a shared deadline'),
    ('force-merge', 'merges without the normal review'),
]


def classify(decision):
    """(label, reason). Pure: no clock, no file, so both directions are
    driven in tests without touching a log. Defaults GREEN: reversible and
    cheap is the class that needs no signal to justify itself."""
    text = (decision or '').lower()
    for kw, why in RED_SIGNALS:
        if kw in text:
            return RED, 'RED: %s (matched %r), never delegated' % (why, kw)
    for kw, why in AMBER_SIGNALS:
        if kw in text:
            return AMBER, 'AMBER: %s (matched %r), reversible but wide' % (why, kw)
    return GREEN, 'GREEN: no RED or AMBER signal matched, reversible and cheap'


def check_absence(last_message_at, window_hours, blocked, now=None):
    """(is_absent, reason). Both halves must hold, driven each way:
    a quiet founder with nothing blocked is not absence, and a blocked
    decision with a present founder is not absence either."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if last_message_at.tzinfo is None:
        last_message_at = last_message_at.replace(tzinfo=datetime.timezone.utc)
    elapsed_hours = (now - last_message_at).total_seconds() / 3600.0
    silence = elapsed_hours > window_hours
    if silence and blocked:
        return True, ('no founder message in %.1fh (window %.1fh) and a decision '
                       'is blocking progress' % (elapsed_hours, window_hours))
    if silence and not blocked:
        return False, ('no founder message in %.1fh but nothing is blocked, so a '
                        'quiet founder alone is not absence' % elapsed_hours)
    if blocked:
        return False, ('a decision is blocked but the founder is still within the '
                        '%.1fh window, so this is not absence' % window_hours)
    return False, 'the founder is within the window and nothing is blocked'


def _now_stamp(now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now.strftime('%Y-%m-%dT%H:%M:%SZ')


def record_amber(decision, reason, cost_if_wrong, overrule, session='fable',
                  now=None, path=None):
    """Append one PROVISIONAL-FABLE record. Returns the entry, or None when
    refused: an AMBER record with no overrule sentence is never written,
    because the sentence is the entire point of the record."""
    if not overrule or not str(overrule).strip():
        return None
    if path is None:
        path = AMBER_LOG
    entry = {
        'decision': decision,
        'reason': reason,
        'cost_if_wrong': cost_if_wrong or '',
        'overrule_sentence': str(overrule).strip(),
        'status': 'PROVISIONAL-FABLE',
        'session': str(session),
        'at': _now_stamp(now),
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(entry, sort_keys=True) + '\n')
    return entry


def queue_red(decision, reason, session='fable', now=None, path=None):
    """Append one queued RED refusal. Never acted on: this only ever writes
    a record for the founder to answer, in one word, on return."""
    if path is None:
        path = RED_QUEUE
    entry = {
        'decision': decision,
        'reason': reason,
        'status': 'AWAITING FOUNDER',
        'session': str(session),
        'at': _now_stamp(now),
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(entry, sort_keys=True) + '\n')
    return entry


def decide(decision, overrule=None, cost_if_wrong=None, session='fable', now=None,
           amber_path=None, red_path=None):
    """Classify, then act. Returns (label, entry_or_None, reason).

    GREEN: no record, entry is None. AMBER: recorded only when an overrule
    sentence was given, otherwise entry is None (classified but not acted
    on: --classify alone must never silently record). RED: always queued,
    entry is never None, because a RED refusal needs no further input to be
    queued."""
    label, reason = classify(decision)
    if label == RED:
        entry = queue_red(decision, reason, session=session, now=now, path=red_path)
        return label, entry, reason
    if label == AMBER:
        entry = None
        if overrule:
            entry = record_amber(decision, reason, cost_if_wrong, overrule,
                                  session=session, now=now, path=amber_path)
        return label, entry, reason
    return label, None, reason


def selftest():
    """Drive all three classes once each, in memory paths, and say so.
    Never touches the real logs under .sbe/."""
    import shutil
    import tempfile
    d = tempfile.mkdtemp(prefix='fable-authority-selftest-')
    try:
        return _selftest_in(d)
    finally:
        # E100: check_all.sh runs --selftest on every battery run and this
        # directory was never removed. Cleanup reports rather than dies: a
        # finished proof must not fail on tidying up.
        try:
            shutil.rmtree(d)
        except OSError as exc:
            sys.stderr.write(
                'fable_authority: left behind %s: %s\n' % (d, exc))


def _selftest_in(d):
    """The three drives themselves, against logs under the caller's dir."""
    amber_path = os.path.join(d, 'amber.jsonl')
    red_path = os.path.join(d, 'red.jsonl')

    g_label, g_entry, _ = decide('rename a local variable for clarity',
                                  amber_path=amber_path, red_path=red_path)
    print('GREEN check: %-45s -> %s (no record: %s)'
          % ('rename a local variable for clarity', g_label, g_entry is None))

    a_label, a_entry, _ = decide(
        'restructure the module layout across the repo',
        overrule='Revert the restructure, put the layout back the way it was',
        amber_path=amber_path, red_path=red_path)
    print('AMBER check: %-43s -> %s (PROVISIONAL-FABLE recorded: %s)'
          % ('restructure the module layout across the repo', a_label,
             bool(a_entry and a_entry.get('status') == 'PROVISIONAL-FABLE')))

    r_label, r_entry, _ = decide('delete the remote branch',
                                  amber_path=amber_path, red_path=red_path)
    print('RED check: %-45s -> %s (refused and queued: %s)'
          % ('delete the remote branch', r_label,
             bool(r_entry and r_entry.get('status') == 'AWAITING FOUNDER')))

    ok = (g_label == GREEN and g_entry is None
          and a_label == AMBER and a_entry is not None
          and r_label == RED and r_entry is not None)
    print('SELFTEST %s: three classes driven, GREEN proceeds with no record, '
          'AMBER records PROVISIONAL-FABLE carrying its overrule sentence, '
          'RED is refused and queued' % ('OK' if ok else 'FAIL'))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0],
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--classify', metavar='DECISION',
                     help='classify one decision text and act: GREEN proceeds, '
                          'AMBER is reported (recorded only with --overrule), '
                          'RED is refused and queued automatically')
    ap.add_argument('--record-amber', metavar='DECISION',
                     help='classify and write a PROVISIONAL-FABLE record; '
                          'requires --overrule, refused without it')
    ap.add_argument('--overrule', help='the exact sentence that reverses this AMBER decision')
    ap.add_argument('--cost', default='', help='what it costs if this AMBER decision is wrong')
    ap.add_argument('--session', default='fable')
    ap.add_argument('--check-absence', action='store_true',
                     help='absence needs BOTH --last-message beyond --window-hours '
                          'AND --blocked')
    ap.add_argument('--last-message', metavar='ISO', help='the founder\'s last message time')
    ap.add_argument('--window-hours', type=float, default=24.0)
    ap.add_argument('--blocked', action='store_true', help='a decision is blocking progress')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--amber-log', help='override the amber log path, for tests')
    ap.add_argument('--red-queue', help='override the red queue path, for tests')
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    if args.check_absence:
        if not args.last_message:
            print('%s: --check-absence needs --last-message ISO' % NODATA, file=sys.stderr)
            return 2
        try:
            text = args.last_message
            if text.endswith('Z'):
                text = text[:-1] + '+00:00'
            last = datetime.datetime.fromisoformat(text)
        except ValueError as exc:
            print('%s: --last-message %r cannot be parsed: %s'
                  % (NODATA, args.last_message, exc), file=sys.stderr)
            return 2
        absent, reason = check_absence(last, args.window_hours, args.blocked)
        print('%s: %s' % ('ABSENT' if absent else 'NOT ABSENT', reason))
        return 1 if absent else 0

    if args.record_amber is not None:
        label, reason = classify(args.record_amber)
        if label != AMBER:
            print('REFUSED: %r classified %s, not AMBER; --record-amber only writes '
                  'AMBER records (%s)' % (args.record_amber, label, reason), file=sys.stderr)
            return 1
        entry = record_amber(args.record_amber, reason, args.cost, args.overrule,
                              session=args.session, path=args.amber_log)
        if entry is None:
            print('REFUSED: an AMBER record cannot be written without its overrule '
                  'sentence; pass --overrule "<exact words that reverse this>"',
                  file=sys.stderr)
            return 1
        print('PROVISIONAL-FABLE recorded: %s' % json.dumps(entry, sort_keys=True))
        return 0

    if args.classify is not None:
        label, reason = classify(args.classify)
        if label not in (GREEN, AMBER, RED):
            print('%s: classify() returned an unrecognized label %r for %r'
                  % (NODATA, label, args.classify), file=sys.stderr)
            return 2
        if label == RED:
            entry = queue_red(args.classify, reason, session=args.session, path=args.red_queue)
            print('RED: %s' % reason)
            print('REFUSED and queued for the founder: %s' % json.dumps(entry, sort_keys=True))
            return 1
        if label == AMBER:
            print('AMBER: %s' % reason)
            print('not recorded; use --record-amber --overrule "<...>" to write '
                  'the PROVISIONAL-FABLE record')
            return 0
        print('GREEN: %s' % reason)
        return 0

    ap.print_help(sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
