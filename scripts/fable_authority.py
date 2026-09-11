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
            classify() never returns this on its own: there is no
            GREEN_SIGNALS list, so nothing currently earns GREEN except an
            unmatched decision, and unmatched text defaults to AMBER (see
            below), not GREEN. The label and its handling stay live in
            decide()/record_amber()/main() for the day a real positive
            signal list justifies it.
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
deterministic and testable. It will miss phrasings a person would catch;
unmatched text now defaults AMBER, not GREEN (fixed 2026-09-10: GREEN's
silence meant a RED-worthy decision whose phrasing missed both lists was
misclassified as freely actionable with no record at all).
# ponytail: keyword heuristic with a known ceiling; upgrade to a model
# classifier (or a bigger signal list) if a real decision text misses both
# lists and lands AMBER by default when it should have read RED.

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

import autonomy_dial
import fence_expiry

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _record_dir(root=None):
    """Where this module's three logs actually live (F6). A linked
    worktree has no .sbe of its own, exactly the worktree-blindness
    fence_expiry._registry_root's own docstring names for two prior
    instances (integrate._Lock, record_drift); newly load-bearing here
    because a grant written by a human in one worktree must be found by
    land_queue.resolve_authority running in another. Reuses
    fence_expiry's own resolver rather than a second implementation of
    the same "walk to the primary checkout" logic (steering 12: no second
    source of truth), so this file and fence_expiry can never disagree
    about which checkout holds the live registry."""
    return os.path.join(fence_expiry._registry_root(root or ROOT), '.sbe', 'fable-authority')


RECORD_DIR = _record_dir()
AMBER_LOG = os.path.join(RECORD_DIR, 'amber-records.jsonl')
RED_QUEUE = os.path.join(RECORD_DIR, 'red-queue.jsonl')
DELEGATIONS_LOG = os.path.join(RECORD_DIR, 'delegations.jsonl')

# F1: the one spelling of the merge-delegation action word. land_queue.py
# imports this (falling back to the same literal if fable_authority.py is
# ever absent from a worktree) instead of hardcoding its own, which is
# exactly how the two lanes disagreed on "merge" vs "merge_or_release".
MERGE_ACTION = 'merge'

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
    driven in tests without touching a log. Defaults AMBER, not GREEN: no
    signal matching either list is not evidence that a decision IS
    reversible and cheap, only that the keyword heuristic missed it. GREEN
    silently proceeds with no record at all, so a RED-worthy decision
    whose phrasing missed both lists would be misclassified as freely
    actionable; AMBER still lets an autonomous session act, but forces a
    recorded, overrule-sentence-bearing PROVISIONAL-FABLE entry instead of
    silent, unlogged action. This does not widen or narrow RED_SIGNALS or
    AMBER_SIGNALS themselves; GREEN remains reachable only by adding an
    actual positive signal for it, which this change deliberately does
    not do."""
    text = (decision or '').lower()
    for kw, why in RED_SIGNALS:
        if kw in text:
            return RED, 'RED: %s (matched %r), never delegated' % (why, kw)
    for kw, why in AMBER_SIGNALS:
        if kw in text:
            return AMBER, 'AMBER: %s (matched %r), reversible but wide' % (why, kw)
    return AMBER, ('AMBER: no RED or AMBER signal matched; unmatched text is '
                    'no longer treated as reversible and cheap by default, '
                    'it is recorded instead of silently proceeding')


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


def grant_merge(repository, base, action, risk_ceiling, until, merge_method,
                 granted_by, words, now=None, path=None):
    """Append one scoped merge delegation (design-P3.md section 3, steering
    9.7). Returns (True, entry) or (False, reason), mirroring
    accept_delivery.record's refusal style.

    THE LINE THIS FUNCTION NEVER CROSSES: it records a human's own grant.
    It never infers one from evidence, memory, or a prior acceptance, and it
    is reached only through this module's own `grant-merge` CLI subcommand,
    never from a model's own decision. A session that finds no live
    delegation via delegation_for() reports READY-FOR-HUMAN and stops; it
    never calls this function to manufacture the grant it is missing.

    until is refused when it cannot be parsed or already lies in the past
    (fence_expiry.parse_expiry is the one expiry reader, matching the spend
    guard's own rule that a grant with no future `until` is no grant)."""
    missing = [flag for flag, val in (
        ('repository', repository), ('base', base), ('action', action),
        ('risk_ceiling', risk_ceiling), ('until', until),
        ('merge_method', merge_method), ('granted_by', granted_by),
        ('words', words))
        if not (val or '').strip()]
    if missing:
        return False, ('grant-merge requires %s (no field is ever '
                        'defaulted or inferred)' % ', '.join(missing))
    if risk_ceiling not in autonomy_dial.ORDER:
        return False, ('risk_ceiling %r is not one of %s'
                        % (risk_ceiling, autonomy_dial.ORDER))
    now = now or datetime.datetime.now(datetime.timezone.utc)
    when = fence_expiry.parse_expiry(until)
    if when is None:
        return False, 'until %r cannot be parsed as a date' % until
    if when <= now:
        return False, ('until %r is in the past (now %s); a grant needs a '
                        'future expiry, never a lapsed one'
                        % (until, _now_stamp(now)))
    if path is None:
        path = DELEGATIONS_LOG
    entry = {
        'repository': repository.strip(),
        'base': base.strip(),
        'action': action.strip(),
        'risk_ceiling': risk_ceiling.strip(),
        'until': until.strip(),
        'merge_method': merge_method.strip(),
        'granted_by': granted_by.strip(),
        'words': words.strip(),
        'granted_at': _now_stamp(now),
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(entry, sort_keys=True) + '\n')
    return True, entry



def revoke_merge(repository, base, action, granted_by, words, now=None, path=None):
    """F4: the only way to end a live grant early. `grant_merge` refuses
    any `until` at or before now (a human cannot grant an already-lapsed
    window), so ending one early by writing a plain grant is impossible
    by construction; this is the second, deliberate writer. It appends a
    line for the SAME (repository, base, action) whose `until` already
    equals its own `granted_at`, i.e. already expired the instant it is
    written. delegation_for's last-matching-line-wins scan then reads
    this line, not the live grant before it, and its own expiry check
    (F4 exercises the same path steering 9.7 already required: NO-EXPIRY
    and EXPIRED both collapse to no grant). Returns (True, entry) or
    (False, reason), matching grant_merge's own refusal style."""
    missing = [flag for flag, val in (
        ('repository', repository), ('base', base), ('action', action),
        ('granted_by', granted_by), ('words', words))
        if not (val or '').strip()]
    if missing:
        return False, ('revoke-merge requires %s' % ', '.join(missing))
    now = now or datetime.datetime.now(datetime.timezone.utc)
    stamp = _now_stamp(now)
    if path is None:
        path = DELEGATIONS_LOG
    entry = {
        'repository': repository.strip(),
        'base': base.strip(),
        'action': action.strip(),
        'risk_ceiling': None,
        'until': stamp,
        'merge_method': None,
        'granted_by': granted_by.strip(),
        'words': words.strip(),
        'granted_at': stamp,
        'revoked': True,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(entry, sort_keys=True) + '\n')
    return True, entry


def delegation_for(repository, base, action, now=None, path=None):
    """The live grant matching (repository, base, action), or None.

    None covers three cases a caller must treat identically (steering 9.7:
    authority NO-DATA is READY-FOR-HUMAN, never FAIL and never MERGED): no
    delegations file yet, no matching line, and a matching line whose
    `until` is expired or unparsable. fence_expiry.parse_expiry is the sole
    expiry reader, so this file and fence_expiry never disagree about a
    date; an unparsable or absent `until` reads NO-EXPIRY, and NO-EXPIRY is
    no grant, exactly as fence_expiry treats an open claim with none.

    Last matching line wins: delegations.jsonl is append-only, so a later
    grant supersedes an earlier one for the same (repository, base, action)
    without needing to edit or delete the earlier line."""
    if path is None:
        path = DELEGATIONS_LOG
    if not os.path.isfile(path):
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    found = None
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:  # sbe: allow-silent one corrupt JSONL event cannot establish authority, so it is skipped while later events remain readable
                continue
            if (entry.get('repository') == repository
                    and entry.get('base') == base
                    and entry.get('action') == action):
                found = entry
    if found is None:
        return None
    when = fence_expiry.parse_expiry(found.get('until'))
    if when is None or when <= now:
        return None
    return found



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
    """The three drives themselves, against logs under the caller's dir.
    GREEN is no longer one of them: classify() has no GREEN_SIGNALS list,
    so nothing reaches GREEN through it any more (fixed 2026-09-10). The
    first drive below is the fix itself: text matching neither RED nor
    AMBER now classifies AMBER, not GREEN, and (with no overrule given)
    is classified but not silently recorded either, exactly like any
    other AMBER decision."""
    amber_path = os.path.join(d, 'amber.jsonl')
    red_path = os.path.join(d, 'red.jsonl')

    u_label, u_entry, _ = decide('rename a local variable for clarity',
                                  amber_path=amber_path, red_path=red_path)
    print('UNMATCHED check: %-40s -> %s (no record without an overrule: %s)'
          % ('rename a local variable for clarity', u_label, u_entry is None))

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

    ok = (u_label == AMBER and u_entry is None
          and a_label == AMBER and a_entry is not None
          and r_label == RED and r_entry is not None)
    print('SELFTEST %s: unmatched text now classifies AMBER (not GREEN) and stays '
          'unrecorded without an overrule, a matched AMBER signal records '
          'PROVISIONAL-FABLE carrying its overrule sentence, RED is refused '
          'and queued' % ('OK' if ok else 'FAIL'))
    return 0 if ok else 1


def _grant_merge_cli(argv):
    """`grant-merge`: the one writer of delegations.jsonl (steering 9.7: a
    model never writes its own delegation). Human-run only; nothing in this
    repository calls into it (mirrors accept_delivery.py's own claim, made
    the same way: grep -rl grant_merge scripts finds no importer besides
    this file and its test)."""
    ap = argparse.ArgumentParser(
        prog='fable_authority.py grant-merge',
        description='Write one scoped merge delegation. Refuses a missing '
                     'or past --until so a human writes it and a session '
                     'never fabricates one.')
    ap.add_argument('--repository', required=True)
    ap.add_argument('--base', required=True)
    ap.add_argument('--action', required=True)
    ap.add_argument('--risk-ceiling', required=True, choices=autonomy_dial.ORDER)
    ap.add_argument('--until', required=True,
                     help='ISO date/datetime; refused unless it is in the future')
    ap.add_argument('--merge-method', required=True)
    ap.add_argument('--granted-by', required=True)
    ap.add_argument('--words', required=True, help="the granter's own words, verbatim")
    ap.add_argument('--delegations-log', default=None,
                     help='override the delegations log path, for tests')
    args = ap.parse_args(argv)

    ok, result = grant_merge(args.repository, args.base, args.action,
                              args.risk_ceiling, args.until, args.merge_method,
                              args.granted_by, args.words, path=args.delegations_log)
    if not ok:
        print('REFUSED: %s' % result, file=sys.stderr)
        return 1
    print('GRANTED: %s' % json.dumps(result, sort_keys=True))
    return 0


def _revoke_merge_cli(argv):
    """`revoke-merge` (F4): the only writer that can end a live grant
    early, matching grant_merge's own rule that a session never writes
    its own delegation nor its own revocation; nothing in this repository
    calls into it besides this dispatch and its test."""
    ap = argparse.ArgumentParser(
        prog='fable_authority.py revoke-merge',
        description='Supersede any live merge delegation for one '
                     '(repository, base, action) with an already-expired '
                     'line, so delegation_for stops finding a grant.')
    ap.add_argument('--repository', required=True)
    ap.add_argument('--base', required=True)
    ap.add_argument('--action', required=True)
    ap.add_argument('--granted-by', required=True, help='who is revoking, verbatim')
    ap.add_argument('--words', required=True, help="the revoker's own words, verbatim")
    ap.add_argument('--delegations-log', default=None,
                     help='override the delegations log path, for tests')
    args = ap.parse_args(argv)

    ok, result = revoke_merge(args.repository, args.base, args.action,
                               args.granted_by, args.words, path=args.delegations_log)
    if not ok:
        print('REFUSED: %s' % result, file=sys.stderr)
        return 1
    print('REVOKED: %s' % json.dumps(result, sort_keys=True))
    return 0


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if argv[:1] == ['grant-merge']:
        return _grant_merge_cli(argv[1:])
    if argv[:1] == ['revoke-merge']:
        return _revoke_merge_cli(argv[1:])
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
