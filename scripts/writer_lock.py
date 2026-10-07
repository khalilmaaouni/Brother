#!/usr/bin/env python3
"""The exclusive writer lock: only one process may hold the tree, the branch and the plan at a time.

usage: python3 scripts/writer_lock.py status | break-if-dead
As a library: with writer_lock.held() as lock:  ...   (lock is None when somebody else holds it)

WHY. Both council seats that returned DO NOT CLOCK on 2026-09-21 named the same first precondition, and they were
right: brother_pass runs a writer action with subprocess.run and never checks whether another pass is already
landing. Two ticks of a timer that overlap would apply two builds to one tree, revert each other's paths, and
commit a mixture neither of them verified. Nothing in the loop prevented it, because until tonight a human was
always the thing invoking it, one at a time.

HOW. An O_CREAT|O_EXCL file, which is atomic on every filesystem this runs on, carrying the holder's pid and when
it started. A lock whose holder is gone is DEAD and can be broken, because a crashed landing must not stop the
night; a lock whose holder is alive is never broken, however old, because the holder may simply be slow and
stealing the tree from a running landing is worse than waiting."""
import contextlib
import errno
import json
import os
import sys
import time

LOCK = os.path.expanduser("~/.claude/evidence/brother-writer.lock")


def read(path=LOCK):
    """The holder, or None when the lock is absent. A corrupt lock file reads as held by an unknown holder, never
    as free: a file that exists means somebody meant to hold it."""
    try:
        with open(path, encoding="utf-8") as fh:
            row = json.load(fh)
        return row if isinstance(row, dict) else {"pid": None, "corrupt": True}
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"pid": None, "corrupt": True}


def alive(pid, probe=None):
    """Whether the holder is still running. An unknown pid counts as ALIVE, because breaking a lock we cannot
    reason about is the dangerous direction."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return True
    try:
        (probe or os.kill)(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # it exists and belongs to someone else
    except OSError:
        return True


def acquire(path=LOCK, pid=None, now=None, what=""):
    """True when this process now holds the lock. Never blocks and never steals: a caller that loses simply does
    something else this pass, which is what a work conserving loop should do anyway."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    row = json.dumps({"pid": pid or os.getpid(), "since": now or time.time(), "what": what})
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            return False
        raise
    with os.fdopen(fd, "w") as fh:
        fh.write(row)
    return True


def release(path=LOCK, pid=None):
    """Release only OUR lock. Releasing somebody else's is how two writers end up believing they are alone."""
    row = read(path)
    if row is None:
        return False
    if row.get("pid") not in (pid or os.getpid(), None):
        return False
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def break_if_dead(path=LOCK, probe=None):
    """(broken, reason). A lock whose holder is gone is removed; a live holder is left alone however long it has
    held, because a slow landing is not a stuck one and stealing the tree from it is worse than waiting."""
    row = read(path)
    if row is None:
        return False, "no lock is held"
    pid = row.get("pid")
    if alive(pid, probe):
        return False, "held by pid %s, which is still running" % pid
    try:
        os.remove(path)
    except OSError as exc:
        return False, "the dead holder's lock could not be removed: %s" % exc
    return True, "broke the lock of pid %s, which is gone" % pid


@contextlib.contextmanager
def held(path=LOCK, what="", probe=None):
    """Yield the lock row when we hold it, or None when somebody else does. A dead holder's lock is broken first,
    so one crashed landing cannot stop the night."""
    break_if_dead(path, probe)
    got = acquire(path, what=what)
    try:
        yield (read(path) if got else None)
    finally:
        if got:
            release(path)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "status"
    if cmd == "status":
        row = read()
        if row is None:
            print("FREE    no writer holds the tree")
            return 0
        held_by = row.get("pid")
        print("HELD    by pid %s since %s, doing %s | holder %s"
              % (held_by, row.get("since"), row.get("what") or "unstated",
                 "alive" if alive(held_by) else "GONE, the lock can be broken"))
        return 1
    if cmd == "break-if-dead":
        broke, why = break_if_dead()
        print(("BROKE   " if broke else "KEPT    ") + why)
        return 0 if broke else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
