#!/usr/bin/env python3
"""Who owns this worktree, and did anything change that we did not do.

usage (repo root):
  python3 -B scripts/worktree_sentry.py claim <pid>    claim the worktree for a process
  python3 -B scripts/worktree_sentry.py check          FREE / HELD by a live pid / STALE
  python3 -B scripts/worktree_sentry.py release [<pid>] | release --force
  python3 -B scripts/worktree_sentry.py snapshot       print a fingerprint of the tree right now
  python3 -B scripts/worktree_sentry.py verify <fp>    did anything change since that fingerprint
Exit 0 free or unchanged, 1 held or changed, 2 when the tree cannot be read (NO-DATA, never a pass).

WHY, and this is the failure the owner scored 0 of 5. On 2026-09-21 a SECOND Claude session edited this
worktree while the loop was running. The landing stage refused to commit changes it could not attribute and
exited BLOCKED, which was correct, and the owner then waited 1h42m to be told. The loop lease added the same
day protects the loop from a second DRIVER. It does nothing about a second SESSION, which is what actually
happened.

TWO MECHANISMS, because one of them is advisory and the other is not:

  THE CLAIM is cooperative. A session records that it is writing here, with a pid and a heartbeat, and a second
  session that checks first will see it. It is honest about what it is: a courtesy between well behaved
  sessions, not a lock. Nothing stops a session that never checks.

  THE FINGERPRINT is not cooperative and needs nobody's agreement. It is the tree's own state: every tracked
  path's mode, hash and name, plus every untracked path, reduced to one sha256. Snapshot before a pass, verify
  after. A change nobody in this pass made is a FOREIGN WRITE, detected deterministically, with no reliance on
  the other party having asked permission. That is the airtight half.

FAIL DIRECTION: an unreadable tree is NO-DATA and exit 2, never "unchanged". A fingerprint that cannot be taken
must never read as agreement that nothing happened."""
import argparse
import hashlib
import os
import subprocess
import sys
import time


def _landing_tree():
    """THE TREE THIS SENTRY WATCHES IS THE LANDING TREE, never the directory this file sits in (U3, B5-08). The loop runs
    this file from the frozen candidate, whose parent is no checkout: counting parents from __file__ named the candidate,
    git refused it, the snapshot came back empty, and loop_pass.sh skips its foreign write check on an empty snapshot.
    BROTHER_LAUNCH_WORKTREE (the driver exports it for every child) names the tree; else the checkout the cwd is in.
    FAIL DIRECTION: no checkout at all is None, and a named tree git cannot read fails git; every git read below answers
    both NO-DATA (exit 2), never a verdict about some other tree."""
    named = os.environ.get("BROTHER_LAUNCH_WORKTREE")
    if named:
        return os.path.realpath(named)
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    top = r.stdout.strip()
    return os.path.realpath(top) if r.returncode == 0 and top and os.path.isdir(top) else None


ROOT = _landing_tree()
# Same seam as loop_guard.sh's LEASE, and for the same measured reason: a test that
# cannot name its own claim file spends the operator's real one.
MARK = os.environ.get("BROTHER_WORKTREE_CLAIM") or os.path.expanduser(
    "~/.claude/brother-or-dispatch-state/worktree.claim")
TTL = int(os.environ.get("BROTHER_WORKTREE_TTL", "900"))   # reported, NOT an eviction: see claim()
SKEW = 120      # the same future stamp tolerance loop_heartbeat.py and loop_guard.sh allow


def _git(*args):
    if not ROOT:
        raise OSError("the landing tree cannot be named: no BROTHER_LAUNCH_WORKTREE, and the cwd is in no checkout")
    r = subprocess.run(["git", "-C", ROOT] + list(args), capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise OSError((r.stderr or "git failed").strip())
    return r.stdout


def fingerprint():
    """One sha256 over the tree's own state.

    `git status --porcelain` alone is NOT enough: it says WHICH paths differ, never what they now contain, so
    an edit that replaces a file's content without changing its status is invisible to it. The index listing
    carries each tracked path's mode and blob hash, so a content change moves the fingerprint even when the
    porcelain line does not."""
    try:
        tracked = _git("ls-files", "-s")            # mode, blob sha, stage, path
        dirty = _git("status", "--porcelain", "-uall")
        head = _git("rev-parse", "HEAD")
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError("the tree could not be read (%s)" % exc)
    h = hashlib.sha256()
    for part in (head, tracked, dirty):
        h.update(part.encode("utf-8", "replace"))
    return h.hexdigest()[:32]


def _read():
    try:
        with open(MARK, encoding="utf-8") as fh:
            d = dict(l.strip().split("=", 1) for l in fh if "=" in l)
        return d.get("pid"), float(d.get("at", 0)), d.get("start", "")
    except (OSError, ValueError):
        return None, 0.0, ""


def _alive(pid):
    """ALIVE or DEAD, and every unknown answer is ALIVE.

    os.kill(pid, 0) raises for two completely different reasons and the old code collapsed them into one
    False answer: ProcessLookupError means the process is GONE, while PermissionError means it is very much
    ALIVE and simply owned by another uid. Measured 2026-09-21 on this machine, pid 1 raises PermissionError
    and so read as dead, which is how a claim held by a live process under another uid became takeable. Only a
    positive "no such process" is death here, because refusing a free tree costs one relaunch while stealing a
    live one costs two writers on one worktree."""
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except (ValueError, TypeError):
        return True                 # an unreadable pid is never declared dead
    except OSError:
        return True                 # EPERM and anything else: alive, or unknown, which reads the same


def _proc_start(pid):
    """The holder's start time, or "" when it cannot be read. See _holder_live."""
    try:
        r = subprocess.run(["ps", "-p", str(int(pid)), "-o", "lstart="], capture_output=True, text=True, timeout=20)
        # One canonical spelling, shared with loop_guard.sh proc_start: see the note there.
        return " ".join(r.stdout.split()) if r.returncode == 0 else ""
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return ""


def _holder_live(pid, start):
    """A PID THAT EXISTS IS NOT YOUR CLAIMANT, raised by adversarial review 2026-09-22.

    Gating eviction on liveness alone means a claimant that genuinely died, whose pid the OS then RECYCLED
    onto an unrelated process, is protected forever. The recorded start time is what ties the claim to the
    process: a recycled pid is a different process and started at a different moment. Costs, stated: one
    extra `ps` per decision, one second granularity so a same-second recycle is still not distinguished,
    and a claim written before this field existed degrades to bare pid existence. Every unknown fails
    toward ALIVE, which refuses, matching loop_guard.sh holder_live()."""
    if not _alive(pid):
        return False
    if not start:
        return True                 # nothing recorded: pid existence is all we have
    now_start = _proc_start(pid)
    if not now_start:
        return True                 # ps could not answer: unknown reads ALIVE
    return now_start == start


def claim(pid):
    """A LIVE CLAIMANT IS NEVER EVICTED, HOWEVER OLD.

    loop_until.sh claims this worktree ONCE at startup and never again, so with a 900s TTL the claim was
    honoured for fifteen minutes and free for the rest of the night, which is the same defect the loop lease
    had: a TTL cannot tell a slow holder from a stuck one, and a single build is allowed 3000s here. The claim
    is now taken over only from a positively DEAD claimant, matching scripts/writer_lock.py and the loop lease.
    A crashed session therefore still frees the tree, which is the recovery this must not cost.

    LIVENESS IS ASKED FIRST AND A FUTURE STAMP NEVER OVERRIDES IT, corrected 2026-09-22. A future stamp used
    to take precedence, so moving this machine's clock backwards by more than SKEW made an honest live
    claimant look planted and the claim was taken from a running session: measured that day, a claim naming a
    live pid stamped an hour ahead returned CLAIMED to a stranger. The two causes of a future stamp, a clock
    that moved and a file somebody planted, are indistinguishable on disk, so the decision is made on the one
    checkable fact, which is whether the named process is running. Poison recovery is kept where it is safe: a
    future stamp naming a DEAD pid is still taken over, loudly. A future stamp naming a LIVE pid is refused and
    the operator is told, because a clock fault must never put two writers on one worktree."""
    holder, at, start = _read()
    poisoned = at - time.time() > SKEW
    if holder and holder != str(pid) and _holder_live(holder, start):
        if poisoned:
            return 1, ("REFUSED: this worktree is claimed by LIVE pid %s and the claim is stamped %ds in the "
                       "FUTURE; the clock moved backwards or the file was planted, and a live claimant is not "
                       "evicted either way" % (holder, at - time.time()))
        return 1, "REFUSED: this worktree is claimed by live pid %s, %ds ago" % (holder, time.time() - at)
    if holder and poisoned:
        sys.stderr.write("INVALID: the claim is stamped %ds in the FUTURE and names DEAD pid %s; no honest "
                         "claimant writes a future stamp, so it is being taken\n" % (at - time.time(), holder))
    os.makedirs(os.path.dirname(MARK), exist_ok=True)
    with open(MARK, "w", encoding="utf-8") as fh:
        fh.write("pid=%s\nat=%s\nstart=%s\nroot=%s\n" % (pid, time.time(), _proc_start(pid), ROOT))
    return 0, "CLAIMED this worktree for pid %s" % pid


def check():
    holder, at, start = _read()
    if not holder:
        return 0, "FREE: nobody claims this worktree"
    age = int(time.time() - at)
    # LIVENESS FIRST, in the same order claim() decides. Measured 2026-09-22: a LIVE claimant whose stamp read
    # as future was reported STALE and free here, which is a verdict about the clock dressed up as a verdict
    # about the tree.
    if not _holder_live(holder, start):
        if age < -SKEW:
            return 0, "STALE: the claim names dead pid %s and is stamped %ds in the FUTURE, which no honest claimant writes" % (holder, -age)
        return 0, "STALE: the claim names dead pid %s (%ds old); treat as free" % (holder, age)
    if age < -SKEW:
        return 1, ("HELD by LIVE pid %s, stamped %ds in the FUTURE; the clock moved backwards or the file was "
                   "planted, and a live claimant is not evictable either way" % (holder, -age))
    # HELD whatever the age: check must report the policy claim() enforces, or it tells a reader the tree is
    # free while every claim() call refuses it.
    if age >= TTL:
        return 1, "HELD by live pid %s, %ds ago, past the %ds ttl but ALIVE, so it is not evictable" % (holder, age, TTL)
    return 1, "HELD by live pid %s, %ds ago" % (holder, age)


def selftest():
    import tempfile
    cases = []
    fp1 = fingerprint()
    cases.append(("a fingerprint is stable when nothing changes", fp1 == fingerprint()))
    cases.append(("a fingerprint is a short hex digest", len(fp1) == 32 and all(c in "0123456789abcdef" for c in fp1)))
    # A FOREIGN WRITE moves it. Written into the tree, measured, removed.
    probe = os.path.join(ROOT, ".sentry-probe-tmp")
    with open(probe, "w", encoding="utf-8") as fh:
        fh.write("foreign write\n")
    moved = fingerprint() != fp1
    os.remove(probe)
    cases.append(("a FOREIGN untracked write moves the fingerprint", moved))
    cases.append(("removing it restores the fingerprint", fingerprint() == fp1))
    rc, msg = check()
    cases.append(("check returns one of three verdicts", any(k in msg for k in ("FREE", "HELD", "STALE"))))
    cases.append(("a dead pid is never HELD", not _alive("999999")))
    # pid 1 is real, live, and not ours: os.kill raises PermissionError for it, and reading that as death is
    # exactly how a live claimant's tree became takeable.
    cases.append(("a live pid owned by another uid reads ALIVE", _alive("1")))
    # A RECYCLED PID IS NOT THE CLAIMANT. pid 1 is live, so bare liveness says "held"; a start time that
    # does not match it says the pid was reused, which is the only thing that frees such a claim.
    cases.append(("a live pid with a NON MATCHING start reads DEAD", not _holder_live("1", "Thu Jan  1 00:00:00 1970")))
    cases.append(("a live pid with a MATCHING start reads ALIVE", _holder_live("1", _proc_start("1"))))
    cases.append(("a live pid with NO recorded start still reads ALIVE", _holder_live("1", "")))
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("action", choices=["claim", "check", "release", "snapshot", "verify", "selftest"])
    ap.add_argument("arg", nargs="?")
    # A DECLARED FLAG, not a positional that happens to start with two dashes: argparse rejects
    # the latter, so break glass would have been unreachable from the command line.
    ap.add_argument("--force", action="store_true", help="release a claim whose holder is genuinely stuck")
    a = ap.parse_args(argv)
    try:
        if a.action == "selftest":
            return selftest()
        if a.action == "claim":
            rc, msg = claim(a.arg or os.getpid())
            print(msg); return rc
        if a.action == "check":
            rc, msg = check()
            print(msg); return rc
        if a.action == "release":
            # RELEASE NAMES THE PID IT ACTS FOR, matching loop_guard.sh. It was an unconditional
            # remove, so any session freed any other session's claim in one command, which is the
            # same unauthenticated-release defect that was closed in the lease and left open here.
            # What it proves is modest and worth saying: a caller that passes a pid only shows it
            # could read the file. Same uid processes cannot be told apart by a filesystem, so this
            # is a courtesy between well behaved sessions, like the claim itself, not a lock.
            holder, _at, _s = _read()
            if a.force:
                sys.stderr.write("BREAK-GLASS: forcing the release of the worktree claim held by pid %s\n"
                                 % (holder or "unknown"))
            elif holder:
                pid = a.arg or os.getppid()
                if holder != str(pid):
                    print("REFUSED: this worktree is claimed by pid %s, not by %s; use "
                          "'release --force' if it is genuinely stuck" % (holder, pid))
                    return 1
            if os.path.exists(MARK):
                os.remove(MARK)
            print("released"); return 0
        if a.action == "snapshot":
            print(fingerprint()); return 0
        if a.action == "verify":
            if not a.arg:
                print("NO-DATA: verify needs the fingerprint to compare against"); return 2
            now = fingerprint()
            if now == a.arg:
                print("UNCHANGED %s" % now); return 0
            print("FOREIGN WRITE: the tree changed from %s to %s and this pass did not do it" % (a.arg, now))
            return 1
    except OSError as exc:
        print("NO-DATA: %s; this is not a clean verdict" % exc)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
