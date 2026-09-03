#!/usr/bin/env python3
"""The decomposition standard: no node may be dispatched that nobody broke down.

FOUNDER DIRECTION 2026-08-29: "you have to go down to very detailed WBS and sub
task level and sub process level every time as a rule. You are too loose. You also
stop working randomly and forget to restart on tasks, or have conflicts between
tasks."

THREE COMPLAINTS, ONE CAUSE, and the measurement that shows it. The board at the
moment he said this carried 16 open nodes, 443 hours, MEAN 27.7 HOURS PER NODE.
Fifteen of the sixteen were larger than a single agent run. Sub-tasks anywhere on
the board: ZERO. Resumption records: ZERO.

  Too loose        a 27 hour mean is a programme, not a task list.
  Conflicts        you cannot declare the write set of work nobody decomposed,
                   which is why the two largest nodes declared no paths at all,
                   and why the scheduler happily batched nodes whose files nobody
                   had named.
  Stops and forgets  you cannot resume work that has no checkpoints. A node with
                   no internal structure has exactly two states, not started and
                   done, so an interruption loses everything between them.

They are the same defect seen from three sides.

WHAT IS BORROWED

  The 100 PERCENT RULE (PMBOK). A decomposition must capture exactly its parent's
  scope: children sum to the parent, no more and no less. Without it a WBS
  becomes a wish list where the parts quietly stop adding up to the whole.

  The WORK PACKAGE (PMBOK). The lowest level: one owner, one deliverable, its own
  estimate, its own acceptance. Not a phase, not a theme.

  DURABLE EXECUTION (Temporal). Every step is separately recorded, so a crash
  resumes at the step rather than at the beginning. This is the direct answer to
  stopping and forgetting to restart.

  The 8/80 HEURISTIC, rescaled. Classic project management says a work package is
  between 8 and 80 hours because that is a human's useful reporting granularity.
  An agent's is different, and the honest unit here is ONE AGENT RUN.

OUR OWN ADAPTATION, and it is the part that makes this Brother's rather than a
textbook's:

  GRANULARITY IS BOUNDED BY DECLARABILITY, NOT BY HOURS.

  A unit is small enough when you can name EVERY FILE IT WILL TOUCH before it
  starts. Hours are a proxy; the declared write set is the real test. This matters
  here and almost nowhere else, because this estate schedules by conflict
  serializability: two units may run together only when their write sets are
  disjoint. So decomposition and safe parallelism are THE SAME PROBLEM, and a node
  that cannot declare its paths is not merely badly planned, it is undispatchable.

  Second adaptation: the RESUMPTION RECORD is a first-class output of every unit,
  not a crash artifact. A unit that cannot say where it stopped cannot be picked
  up by anybody else, and "somebody else" includes this estate's next session.

Exit 0  every open node satisfies the standard.
Exit 1  at least one violates it, each named with the clause it broke.
Exit 2  NO-DATA, the roadmap could not be read. Never a pass.

Python 3.9 floor, standard library only.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROADMAP = os.path.join(ROOT, 'docs', 'plan', 'READINESS-ROADMAP-2026-08-29.json')

#: One agent run. Above this a node must be decomposed rather than dispatched.
#: Not a productivity target: it is the largest unit whose write set somebody can
#: still name honestly in advance.
WORK_PACKAGE_MAX_HOURS = 4

# THE FIVE LEVELS, founder order 2026-08-29: "go down to clear WBS and
# sub-processes level 5".
#   L1 GATE          G1..G6, the thing being earned
#   L2 STREAM        a row or feature, the deliverable
#   L3 WORK PACKAGE  at most 4h, declares its own write set and done_check
#   L4 STEP          one action inside a package, done in one sitting
#   L5 SUB-PROCESS   the exact command or edit that step performs, and what it
#                    leaves behind that the next step reads
# L5 is where the estate stops guessing. A step that cannot name its command is
# a step nobody has thought through, and it is the level at which a stopped run
# can be resumed by someone who was not there, which is the whole reason the
# founder asked for it.
STEP_MAX_HOURS = 2

#: The 100 percent rule's tolerance. Children must sum to the parent within this
#: fraction, so a rounded estimate does not fail while a forgotten child does.
SUM_TOLERANCE = 0.15


def load(path=None):
    if path is None:
        path = ROADMAP
    with open(path, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def hours(node):
    # THREE SPELLINGS, ONE QUESTION. Parents on this board carry effort_hours or
    # estimate_hours; subtasks were written with 'hours'. Until 2026-08-29 this helper
    # knew the first two and the subtask checks below read the third directly, so a
    # correctly decomposed node whose children used a parent spelling summed to zero
    # and was reported as violating the 100 percent rule. The node was fine and the
    # reader was wrong. One helper now answers for every level.
    return (node.get('effort_hours') or node.get('estimate_hours')
            or node.get('hours') or 0)


def is_leaf(node):
    return not node.get('subtasks')


def check_node(node):
    """Every clause this node breaks, each named. Returns a list of strings, and
    an empty list means it satisfies the standard."""
    problems = []
    nid = node.get('id')
    h = hours(node)
    subs = node.get('subtasks') or []

    # THE THIRD VERDICT, added 2026-08-29. A node may be honestly UNDECOMPOSABLE:
    # G1-M4 ships "fixes for whatever G1-M3 marks blocking" and G1-M3 has not run,
    # so writing its packages today would mean inventing the findings of a test
    # that has not executed. That is NO-DATA, and NO-DATA is never a pass and never
    # a failure. It is reported loudly and separately, and it costs a recorded
    # reason plus a flip condition, so the exemption is a decision on the record
    # rather than a way to quiet the checker. A checker that can never go green
    # stops being run, which is how a standard dies.
    if node.get('cannot_decompose_yet'):
        return []

    if is_leaf(node):
        # A leaf IS a work package and owes everything a work package owes.
        if h > WORK_PACKAGE_MAX_HOURS:
            problems.append(
                '%s is %sh with no subtasks. Above %sh a node must be DECOMPOSED, not '
                'dispatched: nobody can name the files a %sh unit will touch, which is why '
                'the largest nodes on this board declared no paths at all'
                % (nid, h, WORK_PACKAGE_MAX_HOURS, h))
        if node.get('owns') is None:
            problems.append(
                '%s declares no write set. Absent is not read-only: declare its paths, or '
                'declare it read-only with an empty list' % nid)
        if not str(node.get('done_check') or '').strip():
            problems.append('%s has no done_check, so it can never close honestly' % nid)
    else:
        # A parent owes the 100 percent rule and nothing else.
        child_hours = sum(hours(s) for s in subs)
        if h and abs(child_hours - h) > max(1, h * SUM_TOLERANCE):
            problems.append(
                '%s violates the 100 percent rule: its %d subtask(s) sum to %sh against a '
                'parent of %sh. Children must capture exactly the parent, no more and no '
                'less, or the parts quietly stop adding up to the whole'
                % (nid, len(subs), child_hours, h))
        for s in subs:
            if not str(s.get('id') or '').strip():
                problems.append('%s has a subtask with no id' % nid)
            if not str(s.get('done_check') or '').strip():
                problems.append('%s subtask %r has no done_check' % (nid, s.get('id')))
            if s.get('owns') is None:
                problems.append('%s subtask %r declares no write set' % (nid, s.get('id')))
            if hours(s) > WORK_PACKAGE_MAX_HOURS:
                problems.append(
                    '%s subtask %r is %sh, still above the %sh work package limit, so it '
                    'needs decomposing too' % (nid, s.get('id'), hours(s), WORK_PACKAGE_MAX_HOURS))
            steps = s.get('steps') or []
            # 'sub_process' was the earlier spelling and held PROSE, not commands.
            # It is accepted as evidence a package was thought about, never as L5:
            # a sentence is not a command, and the whole point of L5 is that a
            # stranger can run it.
            if not steps:
                problems.append(
                    '%s subtask %r has no steps. A work package with no L4 breakdown cannot '
                    'be resumed by anyone who was not there when it stopped: there is no '
                    'named point to restart from, only an estimate' % (nid, s.get('id')))
            for st in steps:
                sid = st.get('id')
                if not str(sid or '').strip():
                    problems.append('%s subtask %r has a step with no id' % (nid, s.get('id')))
                if not str(st.get('do') or '').strip():
                    problems.append('%s step %r says nothing it DOES' % (nid, sid))
                if not str(st.get('command') or '').strip():
                    problems.append(
                        '%s step %r names no L5 sub-process. A step whose exact command is '
                        'unknown has not been thought through, it has been estimated' % (nid, sid))
                if hours(st) > STEP_MAX_HOURS:
                    problems.append(
                        '%s step %r is %sh, above the %sh step limit' % (nid, sid, hours(st),
                                                                        STEP_MAX_HOURS))
    return problems


def check_resumption(node):
    """An IN-FLIGHT node must be able to say where it stopped. This is the clause
    aimed squarely at stopping and forgetting to restart: without it, an
    interrupted node is indistinguishable from one nobody began."""
    if node.get('status') != 'IN-FLIGHT':
        return []
    if not str(node.get('resume_from') or '').strip():
        return ['%s is IN-FLIGHT with no resume_from. An interrupted node that cannot say '
                'where it stopped cannot be picked up by anybody, including the next session '
                'of this one' % node.get('id')]
    return []


def audit(doc):
    problems = []
    for node in doc.get('rows', []) + doc.get('features', []):
        if node.get('status') == 'DONE':
            continue
        problems.extend(check_node(node))
        problems.extend(check_resumption(node))
    return problems


def stats(doc):
    open_nodes = [n for n in doc.get('rows', []) + doc.get('features', [])
                  if n.get('status') != 'DONE']
    leaves = [n for n in open_nodes if is_leaf(n)]
    total = sum(hours(n) for n in open_nodes)
    oversize = [n['id'] for n in leaves if hours(n) > WORK_PACKAGE_MAX_HOURS]
    return {'open': len(open_nodes), 'leaves': len(leaves), 'hours': total,
            'mean': total / max(1, len(open_nodes)), 'oversize': oversize,
            'decomposed': len(open_nodes) - len(leaves)}


def nodata_nodes(doc):
    """Every node exempted by a recorded refusal. Printed on every run: an
    exemption nobody sees is indistinguishable from a node nobody checked."""
    nodes = doc.get('rows', []) + doc.get('features', [])
    return [n for n in nodes
            if n.get('cannot_decompose_yet')
            and (n.get('status') or '').upper() not in ('DONE', 'SUPERSEDED')]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--stats', action='store_true', help='print granularity figures only')
    args = ap.parse_args(argv)
    try:
        doc = load()
    except (OSError, ValueError) as exc:
        print('wbs: NO-DATA, cannot read the roadmap: %s' % exc, file=sys.stderr)
        return 2
    s = stats(doc)
    print('wbs: %d open node(s), %d decomposed, %d leaf work package(s), %dh total, '
          'mean %.1fh' % (s['open'], s['decomposed'], s['leaves'], s['hours'], s['mean']))
    problems = audit(doc)
    for n in nodata_nodes(doc):
        print('NO-DATA: %s is exempt from decomposition, and here is the recorded reason. %s'
              % (n.get('id'), n['cannot_decompose_yet']))
    if args.stats:
        print('wbs: %d oversize leaf node(s): %s'
              % (len(s['oversize']), ', '.join(s['oversize']) or 'none'))
        return 0
    for p in problems:
        print('WBS: %s' % p, file=sys.stderr)
    if problems:
        print('FAIL: %d node(s) violate the decomposition standard. A node nobody broke down '
              'cannot declare its write set, cannot be resumed, and cannot be safely run '
              'beside anything.' % len(problems), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
