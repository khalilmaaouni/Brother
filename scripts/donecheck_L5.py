#!/usr/bin/env python3
'''Check the L5 parent unit: all six L5a-L5f siblings are DONE with a matching closer receipt, and each current done_check is a shape the closer will run.

usage: python3 scripts/donecheck_L5.py [--root DIR] [--rerun]
exit 0 PASS, 1 FAIL, 2 NO-DATA.
'''
import argparse
import json
import os
import shlex
import sys

SIBLINGS = ('L5a', 'L5b', 'L5c', 'L5d', 'L5e', 'L5f')  # WBS :424 names L5a-L5f.

_ALLOWED_HEAD = ('python3', '/usr/bin/python3')
_FORBIDDEN = '|;`$><' + chr(10) + chr(92)


def screen_done_check(cmd):
    '''Return [[argv], ...] when cmd is a shape the closer will run, else None.

    This is the same screen as scripts/close_unit.py screen_done_check. It is copied here because the build
    safety screen refuses importing close_unit from this file. The source of truth stays close_unit.py; if that
    screen changes, this copy must be updated with it.
    '''
    if not isinstance(cmd, str) or not cmd.strip():
        return None
    if any(ch in cmd for ch in _FORBIDDEN) or '&&&' in cmd:
        return None
    parts = [p.strip() for p in cmd.split('&&')]
    out = []
    for part in parts:
        if not part:
            return None
        try:
            argv = shlex.split(part)
        except ValueError:
            return None
        if not argv or argv[0] not in _ALLOWED_HEAD:
            return None
        for tok in argv[1:]:
            if tok.startswith(('/', '~')) or '..' in tok.split('/'):
                return None
            if tok == '-c' or tok.startswith('-c'):
                return None
        out.append(argv)
    return out


def _has_receipt(unit):
    '''True only when the unit evidence carries the closer's own receipt for its current done_check.'''
    evidence = unit.get('evidence')
    command = unit.get('done_check')
    if not isinstance(evidence, str) or not isinstance(command, str):
        return False
    return 'UNIT DONE ' in evidence and ('`%s` printed:' % command) in evidence


def _load_plan(root):
    path = os.path.join(root, 'docs', 'plan', 'BROTHER-1.1.0-LAUNCH-WBS.json')
    try:
        with open(path, 'rb') as handle:
            raw = handle.read()
    except OSError as exc:
        return None, 'NO-DATA: plan unreadable (%s)' % exc
    try:
        return json.loads(raw.decode('utf-8')), None
    except (ValueError, UnicodeDecodeError) as exc:
        return None, 'NO-DATA: plan corrupt (%s)' % exc


def _refuse(message):
    print('NO-DATA: ' + message)
    return 2


def main(argv=None):
    if not isinstance(argv, (type(None), list, tuple)):
        return _refuse('arguments must be a list of strings')
    raw = list(sys.argv[1:] if argv is None else argv)
    for item in raw:
        if not isinstance(item, str):
            return _refuse('arguments must be strings')

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--root', default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    parser.add_argument('--rerun', action='store_true')
    args = parser.parse_args(raw)
    if not isinstance(args.root, str):
        return _refuse('root must be a string')
    root = os.path.abspath(args.root)

    if args.rerun:
        launch = os.environ.get('BROTHER_LAUNCH_WORKTREE')
        if launch:
            try:
                if os.path.realpath(launch) == os.path.realpath(root):
                    print('NO-DATA: rerun refused in the launch tree')
                    return 2
            except OSError:
                pass
        print('NO-DATA: rerun is not available without a command runner')
        return 2

    plan, error = _load_plan(root)
    if error:
        print(error)
        return 2
    units = plan.get('units') if isinstance(plan, dict) else None
    if not isinstance(units, list):
        print('NO-DATA: plan has no units list')
        return 2

    by_id = {}
    for unit in units:
        if isinstance(unit, dict) and isinstance(unit.get('id'), str):
            by_id.setdefault(unit['id'], []).append(unit)

    failures = []
    nodata = []
    done_count = 0
    for sibling in SIBLINGS:
        records = by_id.get(sibling)
        if not records:
            nodata.append('%s is absent from the plan' % sibling)
            continue
        if len(records) > 1:
            nodata.append('%s appears %d times in the plan' % (sibling, len(records)))
            continue
        unit = records[0]
        command = unit.get('done_check')
        if screen_done_check(command) is None:
            failures.append('%s current done_check is not a shape the closer will run: %r' % (sibling, command))
            continue
        state = unit.get('state')
        if state != 'DONE':
            failures.append('%s state is %s' % (sibling, state if isinstance(state, str) else type(state).__name__))
            continue
        if not _has_receipt(unit):
            nodata.append('%s is DONE without the closer receipt for its current done_check' % sibling)
            continue
        done_count += 1

    if nodata:
        print('NO-DATA: ' + '; '.join(nodata))
        return 2
    if failures:
        print('FAIL: ' + '; '.join(failures))
        return 1
    if done_count != len(SIBLINGS):
        print('NO-DATA: counted %d of %d siblings' % (done_count, len(SIBLINGS)))
        return 2
    print('PASS: %d of %d L5 siblings closed by their own done check' % (done_count, len(SIBLINGS)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
