#!/usr/bin/env python3
"""v3_receipts: records V3 pre-action-memory receipts for real work units, honestly.

Wraps bm_recurrence.py's own CLI (subprocess only, never its internals, never edits it) so
every receipt goes through the one contract bm_recurrence.py enforces. Adds exactly one more
refusal on top of that contract: a unit with no genuinely surfaced lesson gets NO receipt at
all, not even an empty one. bm_recurrence.py already excludes such a unit from the
denominator (applied and declined both empty means the row is skipped by compute_report),
but writing a row for it invites the next reader to mistake "a row exists" for "a judgement
was made." Refusing to write it is the honest version of the same outcome: the unit is named
in the output as having no applicable lesson, and nothing is written to the store for it.

THE THREE UNITS below are this V3 pass's actual, bounded judgement calls: three token-shield
branches were read diff-first, then the Kay Vault (40-Failures, 50-Reference) was searched
for a lesson that genuinely bears on each one's actual work, not for a lesson that merely
sounds related. Full reasoning per unit, including the runner-up lessons that were considered
and set aside, is in docs/plan/V3-DENOMINATOR-2026-09-03.md.

before_first_write is False for all three: this vault search happened AFTER each unit's work
was already committed on the token-shield project. This is a retroactive reconciliation
pass, not a pre-action recall, and recording True would be exactly the fabrication this row
exists to refuse. judge is deliberately left empty: an independent judge is a separate step,
not this script's own author.

Python 3 standard library only, no network. Writes only to the --db path given on the
command line; never resolves or touches the estate's own .brothermode store.
"""
import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BM_RECURRENCE = HERE.parent / 'products' / 'brothermode' / 'tools' / 'bm_recurrence.py'

# The three token-shield units this pass judged, honestly, against their real diffs
# (git -C SaveClaudeTokens diff origin/main..<branch>) and a real vault search. worker is
# each branch's actual git commit author (khalilmaaouni@users.noreply.github.com on all
# three), not an invented id.
UNITS = [
    {
        'unit_id': 'token-shield:docs/reconcile-backlog-2026-09-03',
        'surfaced': ['a-backlog-row-is-a-claim-about-the-tree'],
        'applied': ['a-backlog-row-is-a-claim-about-the-tree'],
        'declined': [],
        'reason': '',
        'before_first_write': False,
        'worker': 'khalilmaaouni@users.noreply.github.com',
    },
    {
        'unit_id': 'token-shield:docs/fix-contributing-suite-2026-09-03',
        'surfaced': ['website-claims-outliving-the-app'],
        'applied': ['website-claims-outliving-the-app'],
        'declined': [],
        'reason': '',
        'before_first_write': False,
        'worker': 'khalilmaaouni@users.noreply.github.com',
    },
    {
        'unit_id': 'token-shield:docs/readme-suite-pointer-2026-09-03',
        'surfaced': ['website-claims-outliving-the-app'],
        'applied': ['website-claims-outliving-the-app'],
        'declined': [],
        'reason': '',
        'before_first_write': False,
        'worker': 'khalilmaaouni@users.noreply.github.com',
    },
]


class NoApplicableLesson(ValueError):
    """Raised when a unit has nothing surfaced: refuse the receipt, don't write a no-op row."""


def record_unit(unit, db_path, python=None, bm_recurrence_path=BM_RECURRENCE):
    """Record one receipt via bm_recurrence.py's own CLI (subprocess). Refuses, before ever
    invoking the CLI, when unit['surfaced'] is empty: a unit with no applicable lesson gets
    no row at all, rather than an empty one a later reader could mistake for a judgement.
    Returns the CLI's stdout on success; raises NoApplicableLesson or RuntimeError on
    refusal."""
    surfaced = list(unit.get('surfaced') or [])
    if not surfaced:
        raise NoApplicableLesson(
            '%s surfaced no applicable lesson, so no receipt is recorded for it (it stays '
            'honestly out of the denominator)' % unit.get('unit_id', '<unnamed>'))
    cmd = [python or sys.executable, str(bm_recurrence_path), '--db', str(db_path),
           'record', '--unit', unit['unit_id']]
    for s in surfaced:
        cmd += ['--surfaced', s]
    for a in unit.get('applied') or []:
        cmd += ['--applied', a]
    for d in unit.get('declined') or []:
        cmd += ['--declined', d]
    cmd += ['--reason', unit.get('reason', '')]
    cmd += ['--before-first-write', 'true' if unit.get('before_first_write') else 'false']
    if unit.get('judge'):
        cmd += ['--judge', unit['judge']]
    if unit.get('worker'):
        cmd += ['--worker', unit['worker']]
    if unit.get('witness'):
        cmd += ['--witness', unit['witness']]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError('bm_recurrence refused %s: %s'
                            % (unit['unit_id'], result.stderr.strip()))
    return result.stdout.strip()


def report(db_path, python=None, bm_recurrence_path=BM_RECURRENCE):
    """Print bm_recurrence.py's own report for db_path, via its CLI."""
    cmd = [python or sys.executable, str(bm_recurrence_path), '--db', str(db_path), 'report']
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError('bm_recurrence report failed: %s' % result.stderr.strip())
    return result.stdout


def _refuse_estate_db(db_path):
    """Never let this tool point at the live estate store, even by accident."""
    return '.brothermode' in str(Path(db_path).resolve())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--db', required=True,
                    help='scratch receipt store path; must not be the estate .brothermode '
                         'store (pass a path under /tmp)')
    args = ap.parse_args(argv)
    if _refuse_estate_db(args.db):
        print('v3_receipts: REFUSED: --db resolves under .brothermode, which is the live '
              'estate store; point this at a scratch path instead', file=sys.stderr)
        return 2
    for unit in UNITS:
        try:
            out = record_unit(unit, args.db)
            print('v3_receipts: %s' % out)
        except NoApplicableLesson as exc:
            print('v3_receipts: NO APPLICABLE LESSON: %s' % exc)
    print()
    print(report(args.db))
    return 0


if __name__ == '__main__':
    sys.exit(main())
