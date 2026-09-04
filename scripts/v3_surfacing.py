#!/usr/bin/env python3
"""v3_surfacing: replay the machine's PreToolUse repeat guard to find which recorded lessons it
surfaced BEFORE a work unit's first write.

THE WITNESS THIS PRODUCES is what turns "the recall happened before the first mutation" from a
claim into a fact anyone can re-derive. ~/.claude/hooks/repeat_guard.py matches a lesson's
trigger against what is about to run and prints it; ~/.claude/repeat-guard/<session>.jsonl keeps
every approach in order, one record per tool call. So for a session and a unit's file list:

  first write  the first record whose approach edits or writes one of the unit's own files
  surfaced     every lesson whose trigger matches a record BEFORE that one

and "lesson L surfaced at record i, first write at record j, i < j" follows from the log alone.
The match is deliberately the guard's own lexical rule, re-implemented in six lines here rather
than imported, so this tool keeps working if the guard moves; where they could disagree, the
guard is the authority and this is a reconstruction, which is why the output says so.

CONSERVATIVE BY CONSTRUCTION. The stored approach text is truncated (the guard stores about 200
characters), and a lesson matched against a Write's file CONTENT cannot be re-derived from the
log at all, because the content was never stored. So this tool UNDER-counts surfacings and never
over-counts them. NO-DATA, never a guess, when the session log is not on this machine.

Python 3, standard library only, no network, reads only the guard's own state directory.
"""
import argparse
import json
import os
import sys

STATE = os.path.join(os.path.expanduser('~'), '.claude', 'repeat-guard')
WRITE_TOOLS = ('edit ', 'write ', 'notebookedit ')


def load_lessons(state=None):
    """[(trigger, note)] from the guard's own store, or NO-DATA by exception if unreadable."""
    path = os.path.join(state or STATE, 'lessons.jsonl')
    out = []
    try:
        with open(path, encoding='utf-8') as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue  # sbe: allow-silent one malformed lesson line is not the store
                trigger = str(rec.get('trigger', '')).strip()
                if trigger:
                    out.append((trigger, str(rec.get('note', ''))))
    except OSError as exc:
        raise SystemExit('v3_surfacing: NO-DATA: cannot read %s: %s' % (path, exc))
    return out


def load_session(session_id, state=None):
    """Every recorded approach for one session, in order."""
    path = os.path.join(state or STATE, session_id + '.jsonl')
    out = []
    try:
        with open(path, encoding='utf-8') as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue  # sbe: allow-silent a truncated tail record is not the session
    except OSError as exc:
        raise SystemExit('v3_surfacing: NO-DATA: no session log at %s (%s). This tool reports '
                         'nothing rather than guessing an ordering.' % (path, exc))
    return out


def replay(records, paths, lessons):
    """(first_write_index, [(index, trigger)]) for one unit's file list."""
    lowered = [p.lower() for p in paths]
    first_write = None
    for i, rec in enumerate(records):
        approach = str(rec.get('approach', ''))
        low = approach.lower()
        if low.startswith(WRITE_TOOLS) and any(p in low for p in lowered):
            first_write = i
            break
    surfaced = []
    seen = set()
    for i, rec in enumerate(records[:first_write if first_write is not None else 0]):
        low = str(rec.get('approach', '')).lower()
        for trigger, _note in lessons:
            if trigger.lower() in low and trigger not in seen:
                seen.add(trigger)
                surfaced.append((i, trigger))
    return first_write, surfaced


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--session', required=True, help='repeat-guard session id (the log basename)')
    ap.add_argument('--file', action='append', default=[], required=True,
                    help="a repo-relative path belonging to the unit (repeatable)")
    ap.add_argument('--state', default=None, help='guard state directory (default ~/.claude/repeat-guard)')
    args = ap.parse_args(argv)
    lessons = load_lessons(args.state)
    records = load_session(args.session, args.state)
    first_write, surfaced = replay(records, args.file, lessons)
    if first_write is None:
        print('NO-DATA: this session never wrote any of those files, so no ordering is '
              'established (%d record(s) read)' % len(records))
        return 0
    print('session %s: %d records, first write to the unit files at record i=%d'
          % (args.session, len(records), first_write))
    for i, trigger in surfaced:
        print('  surfaced before that write: record i=%d trigger %r' % (i, trigger))
    if not surfaced:
        print('  NO-DATA: no recorded lesson matched anything this session ran before that write')
    return 0


if __name__ == '__main__':
    sys.exit(main())
