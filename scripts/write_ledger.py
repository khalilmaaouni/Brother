#!/usr/bin/env python3
"""W1 of the readiness board: attribution at write time, and rescue before removal.

WHY THIS EXISTS, measured on 2026-08-29 in this same repository. About 500 lines
of code and 17 passing tests were deleted from a shared working tree. They were
never committed, so only a .pyc survived. The mechanism: several sessions share
one tree, the scope reconciler reads whatever changes SURVIVED on disk and
cannot tell who wrote them, so it named undeclared files against whichever
session happened to stop next, repeatedly, and its FIRST offered recovery for an
undeclared new file was `rm`. A peer session was named ten times for files it
never wrote.

The root cause is that a surviving change carries no author. Everything else is
downstream of that single fact, so the fix is not a smarter reconciler, it is a
record made AT WRITE TIME that nothing after the fact has to reconstruct or
guess.

THE LEDGER is append-only on purpose, the same reason the spend guard's grants
and fence_expiry's registry are: a record that can be rewritten cannot settle an
argument about who wrote something. record() only ever appends a line; nothing
in this module opens the ledger file in a mode that can lose a prior line, and
a corrupt or half-written line is skipped on read rather than raised, so one bad
append can never block every attribution query that comes after it.

ATTRIBUTION has exactly THREE answers, and they must never collapse into two:
    MINE          the ledger attributes the path to the ASKING session
    THEIRS        the ledger attributes the path to a DIFFERENT session, named
    UNATTRIBUTED  no ledger line exists for the path at all
UNATTRIBUTED is the important one and it must NEVER read as MINE. A write that
bypassed the tool layer (an editor, a script outside the harness) leaves no
line, and treating that silence as ownership is exactly the mechanism that lost
500 lines: the reconciler could not prove ownership either way and guessed.

THE RECOVERY ORDERING is the second half of the fix. RESCUE (commit to a rescue
branch) is always available and always safe: it can never lose a byte, so it is
offered FIRST for every path, attributed or not. DECLARE (open a task or fence)
and BREAK_GLASS (a reviewed, expiring exception) come next. RESTORE_OR_REMOVE is
offered LAST, and ONLY when the ledger attributes the change to the ASKING
session. You may destroy your own work. You may not destroy work you cannot
prove is yours, and "the ledger has no line for it" is not proof it is yours,
it is the opposite.

THE DISTINCTION THAT COST 500 LINES, carried here as a comment because a
comment is what a tool relied on last time and it was not enough on its own:
reversible-to-HEAD and non-destructive are NOT the same property. `git restore`
on a tracked file whose new content was never committed is destruction wearing
the word "restore". That is why RESTORE_OR_REMOVE is gated on self-attribution
rather than on git's notion of what counts as reversible.

SCOPE NOTE: this module is the ledger and its query API only. Wiring an actual
PreToolUse hook to call record() on every write is machine configuration
(.claude/settings.json) and a founder decision; it is not made here. See the
module-level HOOK_REGISTRATION_LINE constant for the one line such a hook would
need.

The ledger lives at .sbe/write-ledger.jsonl, under this repository, matching the
other append-only state this estate already keeps there (durable-watch.jsonl).
.sbe/ is gitignored; this file is local, durable machine state, never committed.

Python 3.9 floor, standard library only, no network.

PRODUCER: this module is the sole producer of its own ledger. The write
happens inside record(), above, at the `with open(ledger_path, 'a', ...)`
plus `fh.write(json.dumps(entry, ...))` call (lines 114-115 of this file),
opened in append mode so a prior line can never be overwritten. No other
module in this repository imports write_ledger and calls record() (verified:
grep -rn "import write_ledger" scripts bundle/runtime finds nothing; the only
other file mentioning the string "write_ledger" is scripts/test_write_ledger.py,
which exercises this module's own API, and scripts/mutation_gate.py, whose
own unrelated `_write_ledger()` function writes a different, mutation-testing
ledger and never imports this module at all).
"""
import argparse
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(ROOT, '.sbe', 'write-ledger.jsonl')

MINE = 'MINE'
THEIRS = 'THEIRS'
UNATTRIBUTED = 'UNATTRIBUTED'

RESCUE = 'RESCUE'
DECLARE = 'DECLARE'
BREAK_GLASS = 'BREAK_GLASS'
RESTORE_OR_REMOVE = 'RESTORE_OR_REMOVE'

# The one line a founder would need to add to .claude/settings.json to wire this
# into a real PreToolUse hook. Never registered by this module; see SCOPE NOTE.
HOOK_REGISTRATION_LINE = (
    '"PreToolUse": [{"matcher": "Write|Edit|NotebookEdit", '
    '"hooks": [{"type": "command", "command": '
    '"python3 scripts/write_ledger.py record --path \\"$TOOL_INPUT_FILE_PATH\\" '
    '--session \\"$CLAUDE_SESSION_ID\\""}]}]'
)


def normalize(path):
    """A stable key for one file, independent of who asks or from where.

    Two sessions with different working directories must land on the same
    ledger key for the same file, or attribution silently fragments per-caller.
    Resolved relative to ROOT rather than the caller's cwd."""
    return os.path.relpath(os.path.abspath(path), ROOT).replace(os.sep, '/')


def _now_stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def record(path, session_id, timestamp=None, ledger_path=None):
    """Append one write to the ledger. APPEND ONLY: opens in mode 'a', so a
    prior line can never be lost by a later call, even a failing one."""
    if ledger_path is None:
        ledger_path = LEDGER
    entry = {
        'path': normalize(path),
        'session': str(session_id),
        'at': timestamp or _now_stamp(),
    }
    os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
    with open(ledger_path, 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(entry, sort_keys=True) + '\n')
    return entry


def load_entries(ledger_path=None):
    """Every line in the ledger, in append order. A corrupt or truncated line
    is SKIPPED, never raised: one bad append must not blind every attribution
    query that comes after it. A missing file is an empty ledger, not an
    error, since a fresh checkout has never had a write recorded."""
    if ledger_path is None:
        ledger_path = LEDGER
    if not os.path.exists(ledger_path):
        return []
    entries = []
    with open(ledger_path, 'r', encoding='utf-8') as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except ValueError:
                # Skipped so one bad append cannot blind attribution, but
                # said out loud: a dropped line is a dropped write record,
                # and this ledger is the only source of attribution truth.
                print("write_ledger: %s:%d is not valid JSON, skipping"
                      % (ledger_path, n), file=sys.stderr)
                continue
    return entries


def attribute(path, session_id, ledger_path=None):
    """Who wrote `path`, as the ASKING session sees it. Returns (verdict, why).

    The last matching line wins, because the ledger is append-only and append
    order IS write order; no line is ever edited in place, so "last appended"
    and "most recent write this ledger knows about" are the same thing."""
    target = normalize(path)
    last = None
    for entry in load_entries(ledger_path):
        if entry.get('path') == target:
            last = entry
    if last is None:
        return UNATTRIBUTED, 'no ledger line exists for %s' % target
    owner = last.get('session')
    if owner == str(session_id):
        return MINE, '%s is attributed to the asking session %r' % (target, str(session_id))
    return THEIRS, '%s is attributed to a different session %r' % (target, owner)


def options_for(verdict):
    """The recovery ordering, pure and driven off a verdict alone so both
    directions can be tested without touching a ledger file.

    RESCUE, DECLARE and BREAK_GLASS are always available in that order: none of
    them can destroy anything, so withholding them from an unattributed or
    other-owned path would only slow down the one recovery that is always
    safe. RESTORE_OR_REMOVE is appended LAST, and ONLY for MINE.

    reversible-to-HEAD and non-destructive are NOT the same property: `git
    restore` on a tracked file whose new content was never committed is
    destruction wearing the word "restore". That is the exact shape of the
    500-line loss this ledger exists to prevent, so REMOVE is gated on proven
    self-attribution, never on whether git considers the action reversible."""
    options = [RESCUE, DECLARE, BREAK_GLASS]
    if verdict == MINE:
        options.append(RESTORE_OR_REMOVE)
    return options


def recovery_options(path, session_id, ledger_path=None):
    """attribute() plus options_for(), the convenience a caller actually wants."""
    verdict, why = attribute(path, session_id, ledger_path)
    return options_for(verdict), verdict, why


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    p_record = sub.add_parser('record', help='append one write to the ledger')
    p_record.add_argument('--path', required=True)
    p_record.add_argument('--session', required=True)
    p_record.add_argument('--ledger', help='override the ledger path, for tests')

    p_attr = sub.add_parser('attribute', help='who wrote this path, as this session sees it')
    p_attr.add_argument('--path', required=True)
    p_attr.add_argument('--session', required=True)
    p_attr.add_argument('--ledger', help='override the ledger path, for tests')

    p_remove = sub.add_parser(
        'remove-check',
        help='may this session restore/remove this path (RESTORE_OR_REMOVE last, MINE only)')
    p_remove.add_argument('--path', required=True)
    p_remove.add_argument('--session', required=True)
    p_remove.add_argument('--ledger', help='override the ledger path, for tests')

    args = ap.parse_args(argv)

    if args.cmd == 'record':
        try:
            entry = record(args.path, args.session, ledger_path=args.ledger)
        except OSError as exc:
            print('write-ledger: NO-DATA, cannot append to the ledger: %s' % exc,
                  file=sys.stderr)
            return 2
        print('write-ledger: recorded %s for session %s at %s'
              % (entry['path'], entry['session'], entry['at']))
        return 0

    if args.cmd == 'attribute':
        verdict, why = attribute(args.path, args.session, ledger_path=args.ledger)
        print('%s: %s' % (verdict, why))
        return {'MINE': 0, 'THEIRS': 1, 'UNATTRIBUTED': 2}[verdict]

    if args.cmd == 'remove-check':
        options, verdict, why = recovery_options(args.path, args.session, ledger_path=args.ledger)
        ordered = ' -> '.join(options)
        if RESTORE_OR_REMOVE in options:
            print('ALLOWED (%s): %s. Recovery order: %s' % (verdict, why, ordered))
            return 0
        print('REFUSED (%s): %s. RESTORE_OR_REMOVE is never offered here. Recovery order: %s'
              % (verdict, why, ordered), file=sys.stderr)
        return 1

    return 2


if __name__ == '__main__':
    sys.exit(main())
