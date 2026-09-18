#!/usr/bin/env python3
"""DOM-40.05: cancellation must terminate the whole owned descendant tree.

WHY THIS EXISTS. A subprocess a caller spawns and later cancels can leave
live work behind two different ways a plain kill misses: a child that only
forks (still parented to the tree, still in the original process group, a
plain os.kill(root_pid) leaves it running as an orphan) and a child that
DETACHES on purpose, by calling os.setsid() to open a new session and a new
process group. The second case is the one a naive killpg() cannot reach: the
detached process is no longer a member of the group killpg() signals, even
though at the moment of cancellation it is usually still discoverable by its
parent/child chain (ppid), because reparenting to the init/reaper process
only happens once its immediate parent has already exited.

WHAT THIS MODULE DOES NOT CLAIM. A descendant that BOTH detaches its session
AND is reparented to init before cancel_process_tree() takes its first
snapshot is a genuine blind spot: nothing routes it back to root_pid or to
root_pid's original process group any more, and this module has no cgroup or
kernel-level tracking to fall back on (standard library and `ps` only, see
the worker contract). That case is not hidden: every pid this call ever
targets or rediscovers is re-checked after the kill and survivors are
reported by pid; it never reports a tree contained just because the pids it
knew about at the start are gone.

THE RULE THIS MODULE FOLLOWS (worker contract rule 1, NO-DATA is never a
pass): if the process table cannot be read at all, cancel_process_tree()
still makes a best-effort kill of the one pid and one pgid it was actually
given, but it returns verified=False. A caller that reads verified=False as
"contained" is misusing this module; the field exists so it cannot.

Python 3.9 floor, standard library only, POSIX only (os.killpg, os.setsid
have no Windows equivalent used here). No network.
"""
import collections
import os
import signal
import subprocess
import time

ContainmentResult = collections.namedtuple(
    "ContainmentResult", "verified all_dead signalled alive error")


class EnumerationError(Exception):
    """The process table could not be read or parsed. Never treated as
    'nothing to enumerate', always as 'containment cannot be verified'."""


def spawn_contained(args, **kwargs):
    """subprocess.Popen(args, **kwargs), forced onto a fresh session so the
    returned process is its own process group leader (pgid == pid). This is
    the one thing a caller must do at spawn time for cancel_process_tree()
    to have anything to walk later: a child that inherits the caller's own
    process group cannot be told apart from the caller itself.

    Reuse this instead of calling subprocess.Popen directly for anything you
    intend to cancel later."""
    kwargs["start_new_session"] = True
    return subprocess.Popen(args, **kwargs)


def _read_process_table():
    """[(pid, ppid, pgid), ...] for every process ps can currently see.

    `=` after each column name (pid=,ppid=,pgid=) suppresses ps's header row
    on both the BSD ps this estate's macOS machines ship and GNU ps, so the
    parser never has to guess whether line one is data or a label."""
    try:
        completed = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,pgid="],
            capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EnumerationError("could not run ps: %s: %s" % (type(exc).__name__, exc))
    if completed.returncode != 0:
        raise EnumerationError("ps exited %s: %s" % (
            completed.returncode, (completed.stderr or "").strip()[:200]))
    rows = []
    for lineno, line in enumerate(completed.stdout.splitlines(), start=1):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) < 3:
            raise EnumerationError("ps row %d has fewer than 3 fields: %r" % (lineno, line))
        try:
            rows.append((int(parts[0]), int(parts[1]), int(parts[2])))
        except ValueError:
            raise EnumerationError("ps row %d is not numeric: %r" % (lineno, line))
    if not rows:
        raise EnumerationError("ps produced no process rows")
    return rows


def _descendants(root_pid, rows):
    """Every pid reachable from root_pid by following ppid edges, breadth
    first, plus root_pid itself. This is the parent/child half of coverage:
    it finds a child still parented normally, however many generations
    down, including one that changed its own process group but kept its
    parent (a setsid() call changes pgid, not ppid)."""
    children = collections.defaultdict(list)
    for pid, ppid, _pgid in rows:
        children[ppid].append(pid)
    found = set()
    queue = collections.deque([root_pid])
    while queue:
        pid = queue.popleft()
        if pid in found:
            continue
        found.add(pid)
        queue.extend(children.get(pid, ()))
    return found


def _pgid_members(pgid, rows):
    """Every pid currently sharing pgid. This is the process-group half of
    coverage: it finds a child that was reparented away (its immediate
    parent already exited) but never called setsid(), so it is still in the
    group root_pid started in even though the ppid chain no longer reaches
    it."""
    return {pid for pid, _ppid, row_pgid in rows if row_pgid == pgid}


def _pid_alive(pid):
    """Whether pid still exists, as far as os.kill(pid, 0) can tell. Matches
    claim_store.pid_alive: ProcessLookupError is the only proof of death, a
    permission error or any other OSError reads alive rather than dead,
    because neither one is evidence the process is gone."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _signal_pids(pids, sig):
    """Send sig to every pid, ignoring the expected race (it already exited
    between discovery and signalling) without ignoring anything else."""
    for pid in pids:
        if pid <= 0:
            continue
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            continue


def _signal_pgid(pgid, sig):
    if pgid <= 0:
        return
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return
    except PermissionError:
        # macOS answers EPERM, not ESRCH, when every remaining member of the
        # group is a zombie (exited, not yet reaped): measured 2026-09-18 as
        # 3 of 5 runs of test_execution_boundary at load 24. Tolerated here
        # because this module never trusts the signal step: cancel re-reads
        # the process table afterwards and reports every survivor by pid, so
        # a genuine permission refusal still surfaces as a survivor.
        return


def _reap(pid):
    """Clear a zombie direct child so a later os.kill(pid, 0) reports it
    gone rather than defunct-but-still-listed. ChildProcessError means pid
    is not our child (nothing to reap here, not an error)."""
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return
    except OSError:
        return


def _wait_until_dead(pids, deadline, interval):
    """Poll a bounded number of times (never an unbounded sleep loop) until
    every pid in pids is gone or deadline passes. Returns the pids still
    alive."""
    remaining = set(pids)
    while remaining and time.monotonic() < deadline:
        for pid in remaining:
            _reap(pid)
        remaining = {pid for pid in remaining if _pid_alive(pid)}
        if remaining:
            time.sleep(min(interval, max(0.0, deadline - time.monotonic())))
    return remaining


def cancel_process_tree(root_pid, root_pgid=None, grace=2.0, kill_grace=0.5, poll_interval=0.05):
    """Terminate root_pid and every descendant reachable at the moment of
    the call, including one that detached into its own session, and report
    what is confirmed dead rather than assuming it.

    root_pid must have been started with spawn_contained() (or an equivalent
    start_new_session=True Popen) so it is its own process group leader;
    root_pgid defaults to root_pid, matching that contract.

    Sequence: SIGTERM every discovered pid and the original process group,
    wait up to `grace` seconds, SIGKILL whatever is still alive plus the
    group again, wait up to `kill_grace`, then re-enumerate and check every
    pid this call ever touched, plus whatever is still reachable from
    root_pid or still in root_pgid at that point.

    Returns a ContainmentResult:
      verified   the process table was read successfully before and after
                 the kill; False means containment could not be checked, not
                 that it failed.
      all_dead   True only when verified is True and nothing tracked back to
                 root_pid is still alive. A caller must treat False (or
                 verified False) as "not contained", never as a pass.
      signalled  pids this call sent a signal to.
      alive      pids confirmed still alive after the attempt (empty when
                 all_dead is True).
      error      a short reason string, or None.
    """
    if root_pid <= 0:
        return ContainmentResult(False, False, [], [], "root_pid must be positive")
    if root_pid == os.getpid():
        return ContainmentResult(False, False, [], [],
                                  "refusing to signal the caller's own pid")
    if root_pgid is None:
        root_pgid = root_pid
    if root_pgid <= 0:
        return ContainmentResult(False, False, [], [], "root_pgid must be positive")
    try:
        if root_pgid == os.getpgid(0):
            return ContainmentResult(False, False, [], [],
                                      "refusing to signal the caller's own process group")
    except OSError:
        pass  # own pgid unreadable: proceed, the pid == os.getpid() guard above still holds

    try:
        before = _read_process_table()
    except EnumerationError as exc:
        # Cannot verify, but a cancellation was still requested: make the one
        # best-effort attempt this module can make without a process table,
        # and say plainly that nothing about it is confirmed.
        deadline = time.monotonic() + grace
        _signal_pids([root_pid], signal.SIGTERM)
        _signal_pgid(root_pgid, signal.SIGTERM)
        _wait_until_dead({root_pid}, deadline, poll_interval)
        _signal_pids([root_pid], signal.SIGKILL)
        _signal_pgid(root_pgid, signal.SIGKILL)
        return ContainmentResult(False, False, [root_pid], [],
                                  "process table unreadable, containment unverified: %s" % exc)

    targets = _descendants(root_pid, before) | _pgid_members(root_pgid, before)
    targets.discard(os.getpid())
    targets.add(root_pid)

    deadline = time.monotonic() + grace
    _signal_pids(targets, signal.SIGTERM)
    _signal_pgid(root_pgid, signal.SIGTERM)
    remaining = _wait_until_dead(targets, deadline, poll_interval)

    if remaining:
        kill_deadline = time.monotonic() + kill_grace
        _signal_pids(remaining, signal.SIGKILL)
        _signal_pgid(root_pgid, signal.SIGKILL)
        remaining = _wait_until_dead(remaining, kill_deadline, poll_interval)

    for pid in targets:
        _reap(pid)

    try:
        after = _read_process_table()
    except EnumerationError as exc:
        return ContainmentResult(False, False, sorted(targets), sorted(remaining),
                                  "post-kill process table unreadable: %s" % exc)

    # Whatever is reachable NOW (a process could have spawned a new
    # detached child in the window between snapshots) is checked too, not
    # only what was known at the start.
    still_reachable = _descendants(root_pid, after) | _pgid_members(root_pgid, after)
    check_pids = (targets | still_reachable) - {os.getpid()}
    alive = sorted(pid for pid in check_pids if _pid_alive(pid))

    return ContainmentResult(True, not alive, sorted(targets), alive,
                              None if not alive else "%d descendant(s) survived" % len(alive))


if __name__ == "__main__":
    import shlex
    import sys
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        pidfile = os.path.join(tmp, "grandchild.pid")
        detach_cmd = "import os, time; os.setsid(); time.sleep(30)"
        shell = (
            "%s -c %s & echo $! > %s; sleep 20"
            % (shlex.quote(sys.executable), shlex.quote(detach_cmd), shlex.quote(pidfile))
        )
        root = spawn_contained(["/bin/sh", "-c", shell])

        grandchild_pid = None
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if os.path.exists(pidfile):
                with open(pidfile, encoding="utf-8") as fh:
                    text = fh.read().strip()
                if text:
                    grandchild_pid = int(text)
                    break
            if root.poll() is not None:
                raise SystemExit("self-check: root shell exited before writing grandchild pid")
            time.sleep(0.05)
        if grandchild_pid is None:
            root.kill()
            root.wait()
            raise SystemExit("self-check: never saw the grandchild pid")

        result = cancel_process_tree(root.pid, grace=1.0, kill_grace=0.5)
        try:
            root.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            root.kill()
            root.wait()

        if not result.verified:
            raise SystemExit("self-check: containment unverified: %s" % result.error)
        if _pid_alive(grandchild_pid):
            raise SystemExit("self-check: detached grandchild %s is still alive: %r"
                              % (grandchild_pid, result))
        if grandchild_pid not in result.signalled:
            raise SystemExit("self-check: detached grandchild %s was never targeted: %r"
                              % (grandchild_pid, result))
        print("process_containment self-check passed: %r" % (result,))
