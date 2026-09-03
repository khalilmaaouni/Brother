#!/usr/bin/env python3
"""bm_recurrence: count whether vault retrieval (bm_vault.py) actually changed a work unit's
plan or action, instead of firing and being ignored.

REBUILT 2026-08-29 EVENING FROM A RECOVERED SPECIFICATION. The first version of this file, and
its 17 tests, were DELETED from a shared working tree hours after being written: absent from
disk, from git ls-files, and from every commit on every branch, because they were never
committed. Only tools/__pycache__/bm_recurrence.cpython-313.pyc survived, and reading the
marshalled code object out of it returned this docstring and all nine function signatures. So
this is a rebuild from a complete design, not a redesign, and the specification is held at two
durable paths (docs/plan/RECOVERED-bm_recurrence-spec-2026-08-29.md in the Brother repository
and a copy under BrotherArchive) so it cannot be lost a second time.

WHY THIS EXISTS. bm_vault.py, bm_vault_graph.py and vault_recall_hook.py already retrieve past
lessons and demonstrably fire. Nothing measures whether a fired retrieval was ever USED. A stack
that fires but is never counted cannot back a claim that memory helped; this file is the counter,
not more memory. It is deliberately independent of bm_vault.py: it does not call it and does not
parse its output, it only records the receipt a human or agent files after using it.

THE RECEIPT (one row per work unit, upserted by unit_id):
  unit_id             a name for the work unit (a ticket id, a branch, a session tag)
  surfaced            ids of knowledge objects retrieval returned (e.g. a vault note slug)
  applied             ids from `surfaced` that demonstrably changed the plan or action taken
  declined            ids from `surfaced` seen and deliberately not applied
  reason              free text: why the declined ids were declined
  before_first_write  true/false: did the recall happen before the first mutation of this unit

THE RATE, and the definition that makes or breaks the number:
  denominator = work units where at least one APPLICABLE prior lesson existed
  numerator   = units where a lesson was surfaced BEFORE the first write AND appears in `applied`
  units with no applicable lesson are NOT in the denominator at all.

Below MIN_DENOMINATOR the report prints NO-DATA rather than a percentage, because a rate over two
units is noise wearing a number.

APPLICABILITY CEILING, read before trusting the rate. Whether a surfaced lesson was actually
relevant to a work unit is a semantic judgement about the work, not something this recorder can
compute from ids alone. The proxy used here: a unit counts as having at least one APPLICABLE
prior lesson if and only if its receipt moves at least one surfaced id into `applied` or
`declined` (enforced: both must be subsets of `surfaced`, you cannot apply or decline what was
never returned, and a non-empty `declined` requires a non-empty `reason`). Moving an id into
either list is an explicit, recorded judgement call by whoever files the receipt. This is
GAMEABLE both ways: pad `declined` with noise ids to inflate the denominator, or never file a
`declined` entry so an irrelevant unit never enters the denominator at all. Nothing here catches
that. Trustworthy applicability needs an INDEPENDENT JUDGE: a second reviewer, or a separate LLM
judge shown only the work unit's actual diff and the surfaced lesson text, blind to where the
recorder put the id.

Python 3.9 floor, standard library only, no network.
"""
import argparse
import json
import os
import sqlite3
import sys

#: Below this many units in the denominator the report refuses to print a
#: percentage. A rate over two units is noise wearing a number.
MIN_DENOMINATOR = 5


def resolve_root(start):
    """Walk up for a .brothermode or .git marker, mirroring bm_store.py and bm_gate.py so this
    tool's receipts live under the same estate as everything else, never in a temp directory.
    This estate has already lost a deliverable whose only home was a scratch path."""
    path = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(path, '.brothermode')) or \
           os.path.exists(os.path.join(path, '.git')):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return os.path.abspath(start)
        path = parent


def default_db_path():
    override = os.environ.get('BROTHERMODE_RECURRENCE_DB')
    if override:
        return override
    root = resolve_root(os.getcwd())
    return os.path.join(root, '.brothermode', 'recurrence.sqlite3')


def _connect(db_path):
    directory = os.path.dirname(os.path.abspath(db_path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    conn = sqlite3.connect(db_path)
    conn.execute(
        'CREATE TABLE IF NOT EXISTS receipts ('
        ' unit_id TEXT PRIMARY KEY,'
        ' surfaced TEXT NOT NULL,'
        ' applied TEXT NOT NULL,'
        ' declined TEXT NOT NULL,'
        ' reason TEXT NOT NULL,'
        ' before_first_write INTEGER NOT NULL,'
        ' judge TEXT NOT NULL DEFAULT "",'
        ' worker TEXT NOT NULL DEFAULT "",'
        ' witness TEXT NOT NULL DEFAULT "")')
    # Migration for a store created before V9: add the columns if missing.
    # A judge distinct from the worker and a witness of surfaced-before-write
    # are what stop the numerator being self-filed; an old row lacks both and
    # is therefore NO-DATA for the numerator, never counted.
    for col in ('judge', 'worker', 'witness'):
        try:
            conn.execute('ALTER TABLE receipts ADD COLUMN %s TEXT NOT NULL DEFAULT ""' % col)
        except sqlite3.OperationalError:
            pass  # sbe: allow-silent column already present on this store
    return conn


def record_receipt(unit_id, surfaced, applied, declined, reason,
                   before_first_write, db_path=None, judge='', worker='', witness=''):
    """Write (or overwrite) the one receipt for unit_id. Raises ValueError on a contract
    violation. Returns nothing on success.

    THE CONTRACT IS ENFORCED RATHER THAN DOCUMENTED. You cannot apply or decline an id that was
    never surfaced, because that would let a receipt claim a lesson helped when retrieval never
    returned it. And a non-empty `declined` requires a reason, because 'seen and not used' with
    no reason is indistinguishable from padding to inflate the denominator."""
    if not str(unit_id or '').strip():
        raise ValueError('unit_id is required: a receipt with no work unit counts nothing')
    surfaced = list(surfaced or [])
    applied = list(applied or [])
    declined = list(declined or [])
    ssurf = set(surfaced)
    stray_applied = sorted(set(applied) - ssurf)
    if stray_applied:
        raise ValueError('applied ids were never surfaced: %s. You cannot apply what retrieval '
                         'did not return.' % ', '.join(stray_applied))
    stray_declined = sorted(set(declined) - ssurf)
    if stray_declined:
        raise ValueError('declined ids were never surfaced: %s. You cannot decline what '
                         'retrieval did not return.' % ', '.join(stray_declined))
    both = sorted(set(applied) & set(declined))
    if both:
        raise ValueError('ids are both applied and declined: %s. A judgement is one or the '
                         'other.' % ', '.join(both))
    if declined and not str(reason or '').strip():
        raise ValueError('a non-empty declined list requires a reason: "seen and not used" with '
                         'no reason cannot be told apart from padding the denominator')
    conn = _connect(db_path or default_db_path())
    try:
        conn.execute(
            'INSERT INTO receipts (unit_id, surfaced, applied, declined, reason, '
            'before_first_write, judge, worker, witness) VALUES (?,?,?,?,?,?,?,?,?) '
            'ON CONFLICT(unit_id) DO UPDATE SET surfaced=excluded.surfaced, '
            'applied=excluded.applied, declined=excluded.declined, reason=excluded.reason, '
            'before_first_write=excluded.before_first_write, judge=excluded.judge, '
            'worker=excluded.worker, witness=excluded.witness',
            (unit_id, json.dumps(surfaced), json.dumps(applied), json.dumps(declined),
             reason or '', 1 if before_first_write else 0,
             str(judge or ''), str(worker or ''), str(witness or '')))
        conn.commit()
    finally:
        conn.close()


def compute_report(db_path=None):
    """Read every receipt and compute the rate. Returns a dict: denominator, numerator, rate
    (None when denominator < MIN_DENOMINATOR), total_units (all receipts, including those with
    no applicable lesson, so the two numbers can never be confused)."""
    conn = _connect(db_path or default_db_path())
    try:
        rows = conn.execute('SELECT unit_id, surfaced, applied, declined, reason, '
                            'before_first_write, judge, worker, witness FROM receipts').fetchall()
    finally:
        conn.close()
    total = len(rows)
    denominator = 0
    numerator = 0
    excluded_self_filed = []
    excluded_no_witness = []
    for uid, surfaced, applied, declined, _reason, before, judge, worker, witness in rows:
        applied = json.loads(applied)
        declined = json.loads(declined)
        if not applied and not declined:
            continue                      # no applicable lesson: not in the denominator at all
        denominator += 1
        if not (applied and before):
            continue                      # not a candidate for the numerator
        # A candidate only counts when an INDEPENDENT judge made the call and a
        # witness proves the ordering. Self-filed or unwitnessed is NO-DATA for
        # the numerator, named here, never silently folded into either total.
        judge = str(judge or '').strip()
        worker = str(worker or '').strip()
        witness = str(witness or '').strip()
        if not judge or not worker or judge == worker:
            excluded_self_filed.append(uid)
            continue
        if not witness:
            excluded_no_witness.append(uid)
            continue
        numerator += 1
    rate = None
    if denominator >= MIN_DENOMINATOR:
        rate = 100.0 * numerator / denominator
    return {'denominator': denominator, 'numerator': numerator,
            'rate': rate, 'total_units': total,
            'excluded_self_filed': excluded_self_filed,
            'excluded_no_witness': excluded_no_witness}


def _bool_arg(s):
    text = str(s).strip().lower()
    if text in ('true', 'yes', 'y', '1'):
        return True
    if text in ('false', 'no', 'n', '0'):
        return False
    raise argparse.ArgumentTypeError(
        'expected true or false, got %r. This vocabulary is explicit because a truthy string '
        'like "n" silently reading as True is a defect this estate has already shipped once.' % s)


def cli_record(args):
    try:
        record_receipt(args.unit, args.surfaced, args.applied, args.declined,
                       args.reason, args.before_first_write, args.db,
                       judge=args.judge, worker=args.worker, witness=args.witness)
    except ValueError as exc:
        print('bm_recurrence: REFUSED: %s' % exc, file=sys.stderr)
        return 1
    print('bm_recurrence: receipt recorded for %s' % args.unit)
    return 0


def cli_report(args):
    r = compute_report(args.db)
    for uid in r.get('excluded_self_filed', []):
        print('EXCLUDED (self-filed, judge==worker or missing): %s is a candidate but its '
              'applied call was not made by an independent judge, so it does not count toward '
              'the numerator' % uid)
    for uid in r.get('excluded_no_witness', []):
        print('EXCLUDED (no ordering witness): %s claims surfaced-before-write with no witness '
              'proving it, so it is NO-DATA for the numerator, never counted' % uid)
    if r['rate'] is None:
        print('NO-DATA: %d applicable work unit(s) recorded, need at least %d for a rate '
              '(denominator=%d, %d total receipt(s))'
              % (r['denominator'], MIN_DENOMINATOR, r['denominator'], r['total_units']))
        return 0
    # THE NAME IS THE MEASUREMENT. This printed "recurrence rate" until 2026-08-29,
    # which claimed the failure did not happen again. The counter never observed a
    # later outcome, so it could not know that. What it counts is that a lesson was
    # surfaced and marked applied BEFORE the first write, which is memory reaching
    # the work in time to matter, not the failure being prevented. A number labelled
    # stronger than its own denominator is the overclaim these tools exist to refuse.
    print('pre-action memory application rate: %.1f%% (%d/%d applicable units '
          'surfaced-and-applied before first write) denominator=%d'
          % (r['rate'], r['numerator'], r['denominator'], r['denominator']))
    print('NO-DATA: prevented-recurrence is NOT measured here. That needs a later '
          'observation per unit (did the known failure signature recur) and a '
          'memory-off control arm to subtract from. Neither exists yet, so no '
          'prevention claim may cite this number.')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--db', help='receipt store path (default: the estate root .brothermode)')
    sub = ap.add_subparsers(dest='cmd')
    rec = sub.add_parser('record')
    rec.add_argument('--unit', required=True)
    rec.add_argument('--surfaced', action='append', default=[])
    rec.add_argument('--applied', action='append', default=[])
    rec.add_argument('--declined', action='append', default=[])
    rec.add_argument('--reason', default='')
    rec.add_argument('--before-first-write', dest='before_first_write',
                     type=_bool_arg, required=True)
    rec.add_argument('--judge', default='', help='who judged the applied call, '
                     'distinct from --worker; a numerator unit needs both and they must differ')
    rec.add_argument('--worker', default='', help='who did the work')
    rec.add_argument('--witness', default='', help='the ordering witness: how surfaced-'
                     'before-write was established (a timestamp pair or a commit ordering)')
    sub.add_parser('report')
    args = ap.parse_args(argv)
    if args.cmd == 'record':
        return cli_record(args)
    if args.cmd == 'report':
        return cli_report(args)
    ap.print_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())
