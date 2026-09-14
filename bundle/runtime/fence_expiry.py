#!/usr/bin/env python3
"""W4 of the orchestration watchdog: no claim may outlive the session that made it.

WHY THIS EXISTS, measured rather than imagined. On 2026-08-29 two fences in this
repository carried `expiry: null` and were held by agents that appeared in no
session list, one of them for over thirteen hours. Between them they held five of
the most contended files in the tree and blocked two sessions until a human
reaped them by hand, after two separate sessions had each independently run
ListAgents to convince themselves the owners were really gone.

A claim with no expiry cannot retire itself. That is the whole defect, and it is
not a new one here: the spend guard has refused any grant lacking a future
`until` since 2026-08-17, and that rule is what stopped a raised ceiling
outliving its window. This is the same rule pointed at fences.

THE ADAPTATION, and it matters. The spend guard IGNORES an expired grant and
falls back to a baseline. A fence has no baseline to fall back to, so this REAPS:
it closes the claim and records who held it, what they held, and when it lapsed.

    REAPING CLEARS THE CLAIM. IT NEVER TOUCHES THE CONTENT.

That single property is what makes reaping safe to automate at all, and it is
asserted by a test rather than promised in a comment. The failure this estate
suffered the same day, roughly 500 lines deleted from a shared tree, came from a
tool that offered to remove FILES. This one only ever edits a registry.

Exit 0  every open claim carries a future expiry, nothing to do.
Exit 1  at least one open claim has NO expiry at all, which is refused: it can
        never retire and must be given one or closed.
Exit 2  NO-DATA, the registry could not be read. Never a pass.

With --reap, expired claims are closed and the run still exits 0, because reaping
an expired claim is the system working rather than a finding.

Python 3.9 floor, standard library only.

origin: a human (or the overnight watchdog acting on the founder's behalf)
running this script's own CLI (main(), line 145) directly with --reap.
scripts/check_all.sh calls this script at line 107
(`run_check "fence-expiry" python3 scripts/fence_expiry.py`) but never passes
--reap, so that call only reports LIVE/EXPIRED/NO-EXPIRY and never writes;
verified: grep -rln fence_expiry scripts finds no caller (besides
test_fence_expiry.py, which targets a temp registry) that passes --reap.

PRODUCER: this module is the sole producer of its own reap records. main()
(line 145), only when called with --reap and only when expired claims exist,
calls reap() (line 123) to close them in memory, then does the actual
open(REGISTRY, 'w', encoding='utf-8') plus json.dump(doc, fh, indent=2) at
lines 174-175.
"""
import argparse
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _registry_root(root=None):
    """The checkout that actually holds the live .sbe registry.

    A linked worktree has no .sbe of its own, and resolving relative to
    __file__ made this checker read a registry that does not exist there
    (third instance of the worktree-blindness class found 2026-08-30, after
    integrate._Lock and record_drift). The estate's one live registry sits
    beside the primary checkout's .git, which git names via the common dir."""
    root = root or ROOT
    if os.path.isfile(os.path.join(root, '.sbe', 'tasks.json')):
        return root
    try:
        import subprocess
        p = subprocess.run(['git', '-C', root, 'rev-parse', '--git-common-dir'],
                           capture_output=True, text=True, timeout=10)
        if p.returncode == 0 and p.stdout.strip():
            d = p.stdout.strip()
            if not os.path.isabs(d):
                d = os.path.join(root, d)
            primary = os.path.dirname(os.path.realpath(d))
            if os.path.isfile(os.path.join(primary, '.sbe', 'tasks.json')):
                return primary
    except Exception:  # sbe: allow-silent the caller reports NO-DATA on a missing registry
        pass
    return root


REGISTRY = os.path.join(_registry_root(), '.sbe', 'tasks.json')


def parse_expiry(value):
    """An expiry in any of the shapes this registry actually contains. Returns
    None when absent or unparseable, and the CALLER decides what that means:
    absent is a refusal, unparseable is also a refusal, and neither is silently
    treated as 'far away'."""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        dt = datetime.datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.datetime.strptime(text, '%Y-%m-%d')
        except ValueError:  # sbe: allow-silent documented sentinel above; classify() treats None as NO-EXPIRY, never as far-away
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def classify(task, now):
    """One claim's verdict. Pure, so both directions can be driven in a test
    without a clock or a file."""
    if task.get('status') != 'open':
        return 'CLOSED', 'not an open claim'
    raw = task.get('expiry')
    when = parse_expiry(raw)
    if when is None:
        if raw:
            return 'NO-EXPIRY', 'expiry %r cannot be parsed, so it cannot retire' % raw
        return 'NO-EXPIRY', 'no expiry at all, so this claim can never retire itself'
    if when <= now:
        return 'EXPIRED', 'lapsed at %s' % raw
    return 'LIVE', 'expires %s' % raw


def load(path=None):
    # Resolved at CALL time. A default bound at definition time cannot be
    # overridden, which is a bug this repository shipped once already today.
    if path is None:
        path = REGISTRY
    with open(path, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def reap(doc, now):
    """Close every EXPIRED claim, recording why. Returns the ids reaped.

    TOUCHES ONLY THE REGISTRY. No file named in ownedPaths is read, moved or
    removed, and test_only_the_registry_is_touched asserts it."""
    reaped = []
    stamp = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    for task in doc.get('tasks', []):
        verdict, why = classify(task, now)
        if verdict != 'EXPIRED':
            continue
        task['status'] = 'closed'
        task['closedAt'] = stamp
        task['reapedBy'] = 'fence_expiry'
        task['reapReason'] = (
            'REAPED automatically: %s. The owner was %r and it held %s. Reaping clears the CLAIM '
            'and never the content; every one of those paths is untouched.'
            % (why, task.get('agent'), ', '.join(task.get('ownedPaths') or []) or '(no paths)'))
        reaped.append(task.get('id'))
    return reaped


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--reap', action='store_true', help='close expired claims and record why')
    ap.add_argument('--now', help='ISO datetime to evaluate against, for tests')
    args = ap.parse_args(argv)

    try:
        doc = load()
    except (OSError, ValueError) as exc:
        print('fence-expiry: NO-DATA, cannot read the claim registry: %s' % exc, file=sys.stderr)
        return 2

    now = (datetime.datetime.fromisoformat(args.now) if args.now
           else datetime.datetime.now(datetime.timezone.utc))
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)

    verdicts = [(t.get('id'), classify(t, now)) for t in doc.get('tasks', [])]
    live = [i for i, (v, _) in verdicts if v == 'LIVE']
    expired = [(i, w) for i, (v, w) in verdicts if v == 'EXPIRED']
    missing = [(i, w) for i, (v, w) in verdicts if v == 'NO-EXPIRY']

    for i, w in missing:
        print('NO-EXPIRY %-38s %s' % (i, w))
    for i, w in expired:
        print('EXPIRED   %-38s %s' % (i, w))

    if args.reap and expired:
        ids = reap(doc, now)
        with open(REGISTRY, 'w', encoding='utf-8') as fh:
            json.dump(doc, fh, indent=2)
        print('fence-expiry: reaped %d expired claim(s): %s. Claims cleared, content untouched.'
              % (len(ids), ', '.join(ids)))
        expired = []

    print('fence-expiry: %d live, %d expired, %d with no expiry'
          % (len(live), len(expired), len(missing)))

    if missing:
        print('FAIL: %d open claim(s) carry no usable expiry, so they can never retire themselves '
              'and will hold their paths until a human intervenes: %s'
              % (len(missing), ', '.join(i for i, _ in missing)), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
