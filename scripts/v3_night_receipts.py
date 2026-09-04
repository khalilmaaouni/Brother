#!/usr/bin/env python3
"""v3_night_receipts: the five real work units of the night of 2026-09-03/04, with their
applicability judged by a script rather than by whoever files the receipt.

WHAT THIS ADDS TO v3_receipts.py, which stays exactly as it is. That earlier pass recorded
three real token-shield units and stopped at denominator 3, saying NO-DATA rather than padding
to five, and left `judge` empty because it had no independent judge. This file supplies both
missing halves for five units of this estate's own night:

  THE UNITS are merged pull requests on the private hub, taken from `git log --first-parent`
  on main since 2026-09-03 21:00. Each one's complete diff sits beside this file under
  docs/plan/runs/v3-2026-09-04/diffs/, and the verdicts below are reproducible from those
  diffs alone with scripts/v3_judge.py.

  THE JUDGE is scripts/v3_judge.py, which sees the diff and the lesson text and nothing else:
  not this file, not the branch name, not the commit message, not the roadmap. Every applied
  and declined id below is that script's own verdict, transcribed whole. Nothing was dropped
  for reading badly: u5 carries a DECLINED this recorder believes may be a false negative, and
  it is recorded anyway, because picking which of a judge's verdicts to keep is the self-
  grading bm_recurrence.py's applicability ceiling warns about.

  THE ORDERING WITNESS, for the two units that have one, is a replay of the machine's own
  PreToolUse repeat guard (~/.claude/hooks/repeat_guard.py) over that unit's session log in
  ~/.claude/repeat-guard/. The guard prints a recorded lesson before a tool runs and its log
  keeps every approach in order, so "lesson surfaced at record i, first write to this unit's
  files at record j, i < j" is a fact about a real event, replayable by anyone.

WHY THE NUMERATOR IS ZERO AND WHY THAT IS THE POINT. Two of the five (u1, u2) had a lesson
surfaced BEFORE their first write, witnessed. The judge found neither diff followed it. The
other three were reconciled after the fact, so before_first_write is False and they can never
reach the numerator. So the rate is 0.0 percent over a denominator of 5: memory fired and the
work did not visibly follow it. That is the honest reading of a pre-action APPLICATION rate,
and it is not a prevention claim of any kind.

Python 3 standard library only, no network. Writes only to the --db path given on the command
line; never resolves or touches the estate's own .brothermode store.
"""
import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import v3_receipts as V  # noqa: E402
import v3_judge as J  # noqa: E402

DIFFS = HERE.parent / 'docs' / 'plan' / 'runs' / 'v3-2026-09-04' / 'diffs'

#: The git author of every branch-side commit in all five units, read from
#: `git log -1 --format=%an <%ae>` on each merge's second parent.
WORKER = 'Khalil Maaouni <khalilmaaouni@users.noreply.github.com>'
#: The judge is a script, so it is named as one. It is not the worker and cannot be.
JUDGE = 'script:scripts/v3_judge.py'

#: One entry per unit: the merge it came from, its diff, the lesson ids put to the judge, and
#: the ordering witness where one exists. applied/declined are NOT written here: they are read
#: back from the judge at run time, so this file cannot disagree with the judge.
UNITS = [
    {
        'unit_id': 'brother-hub:wbs/p2b-bundle-data-dirs',
        'merge': 'ba6e685b (PR 147)',
        'diff': 'u1-p2b-bundle-data-dirs.diff',
        'lessons': ['repeat-guard:grep-c', 'repeat-guard:def-space', 'repeat-guard:pipe-tail'],
        'before_first_write': True,
        'witness': ('repeat-guard session 8e2f3733: lesson repeat-guard:def-space surfaced at '
                    'record i=3, first write to scripts/bundle_runtime.py at record i=35'),
    },
    {
        'unit_id': 'brother-hub:wbs/product-battery-triage',
        'merge': '8084472f (PR 120)',
        'diff': 'u2-product-battery-triage.diff',
        'lessons': ['repeat-guard:pipe-tail', 'repeat-guard:def-space',
                    'repeat-guard:open-paren', 'repeat-guard:unittest'],
        'before_first_write': True,
        'witness': ('repeat-guard session 019ba53b: lesson repeat-guard:unittest surfaced at '
                    'record i=17, first write to products/brothersbe/tools/sbe_gate.py at '
                    'record i=25'),
    },
    {
        'unit_id': 'brother-hub:wbs/p9-receipt-identity',
        'merge': 'eb834e5d (PR 137)',
        'diff': 'u3-p9-receipt-identity.diff',
        'lessons': ['repeat-guard:shlex-split', 'repeat-guard:certify'],
        'before_first_write': False,
        'witness': '',
    },
    {
        'unit_id': 'brother-hub:wbs/e76-repairs',
        'merge': '488cdacd (PR 145)',
        'diff': 'u4-e76-repairs.diff',
        'lessons': ['repeat-guard:checksums'],
        'before_first_write': False,
        'witness': '',
    },
    {
        'unit_id': 'brother-hub:wbs/p11-vault-note-types',
        'merge': 'ba15bab9 (PR 142)',
        'diff': 'u5-p11-vault-note-types.diff',
        'lessons': ['repeat-guard:shlex-split', 'repeat-guard:done-check'],
        'before_first_write': False,
        'witness': '',
    },
]


def judged(unit, diffs_dir=None):
    """The unit as bm_recurrence.py wants it, with applied and declined filled in by
    v3_judge.py rather than by hand. A lesson the judge calls NOT-APPLICABLE is left out of
    both lists, which is what keeps the unit honestly out of the denominator when no lesson
    bears on it at all."""
    path = Path(diffs_dir or DIFFS) / unit['diff']
    diff_text = J.read_diff(str(path))
    surfaced, applied, declined, reasons = [], [], [], []
    for lesson_id in unit['lessons']:
        verdict, evidence = J.judge(diff_text, lesson_id)
        surfaced.append(lesson_id)
        if verdict == J.APPLIED:
            applied.append(lesson_id)
        elif verdict == J.DECLINED:
            declined.append(lesson_id)
            reasons.append('%s: the judge saw the lesson\'s subject in the diff and no sign of '
                           'its discipline (%s)' % (lesson_id, evidence))
    return {
        'unit_id': unit['unit_id'],
        'surfaced': surfaced,
        'applied': applied,
        'declined': declined,
        'reason': '; '.join(reasons),
        'before_first_write': unit['before_first_write'],
        'judge': JUDGE,
        'worker': WORKER,
        'witness': unit['witness'],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--db', required=True,
                    help='scratch receipt store path; must not be the estate .brothermode '
                         'store (pass a path outside it)')
    ap.add_argument('--diffs', default=None, help='directory holding the unit diffs')
    args = ap.parse_args(argv)
    if V._refuse_estate_db(args.db):
        print('v3_night_receipts: REFUSED: --db resolves under .brothermode, which is the live '
              'estate store; point this at a scratch path instead', file=sys.stderr)
        return 2
    for unit in UNITS:
        record = judged(unit, args.diffs)
        print('%-42s applied=%s declined=%s'
              % (record['unit_id'], record['applied'] or '[]', record['declined'] or '[]'))
        try:
            V.record_unit(record, args.db)
        except V.NoApplicableLesson as exc:
            print('v3_night_receipts: NO APPLICABLE LESSON: %s' % exc)
    print()
    print(V.report(args.db))
    return 0


if __name__ == '__main__':
    sys.exit(main())
