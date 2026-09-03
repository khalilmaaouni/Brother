#!/usr/bin/env python3
"""BrotherSBE battery marker: one file that says a test battery is measuring
this tree RIGHT NOW, so nothing else quietly edits the tree mid-run.

WHY THIS EXISTS (incident 2026-08-17, and the vault failure class
"measuring-a-tree-you-are-still-editing")
  One session started the sibling repo's full battery as a baseline capture.
  A second session, unaware, edited a tracked documentation page nineteen
  minutes into the run, which invalidated the baseline; the second session
  only discovered the running battery through pgrep. Nothing announced the
  run. The same gap existed here: evals/run_evals.py re-invokes the tools as
  subprocesses per case, so an edit to tools/ mid-run changes what later
  cases measure, and tools/sbe_gate.py reads whatever tree it is pointed at.

THE MECHANISM (ported from BrotherModeUp tools/test_all.py, PARITY.md row)
  The battery runner acquires a marker file at start and releases it on exit.
  The marker is keyed to the MEASURED TREE (the realpath of the root the
  battery examines), not to the machine: a battery in one worktree neither
  guards nor blocks a sibling worktree, which measures its own copy. It
  lives in the system temp directory rather than in the repo, on purpose: a
  marker inside the tree would itself be the dirty-tree condition batteries
  refuse over. The sibling keys its lock by the tools directory instead of
  the measured root because its runner only ever measures its own checkout;
  that divergence is recorded in PARITY.md, next to the mechanism row.

  Crash safety: acquisition is O_CREAT|O_EXCL, staleness is decided by the
  holder pid's liveness FIRST (a process that answers os.kill(pid, 0) is
  running, however long it has run), and age only decides where a pid
  cannot be probed. Release never removes a file whose bytes are not the
  ones this process wrote: a stolen lock belongs to another run by then.

THE READERS (tools/sbe_fence_hook.py and tools/sbe_bash_write_guard.py)
  Both PreToolUse hooks call battery_conflict() below before allowing a
  write. The verdict mapping follows this repo's own law: a live, readable
  marker plus a git-tracked target is a refusal (a finding); a stale marker
  is reported and ignored; a marker that cannot be read is NO-DATA, reported
  loudly and never a block, exactly as evals/test_no_data_class.py holds for
  every checker that ships. Writer and readers share THIS file so the format
  has one owner and no second parser can drift against it.

Python 3.9, standard library only, cross-platform. No em or en dashes
anywhere in this file, its comments, or its output.
"""
import errno
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import time

#: Set (to anything but "" or "0") to disable the hook-side battery fence.
#: The hooks say so on stderr on every write while it is set, so the bypass
#: is never silent. The RUNNER side (acquire/release) ignores this switch:
#: a battery keeps announcing itself even when nobody is listening.
DISABLE_ENV = "BROTHERSBE_BATTERY_FENCE_OFF"

#: How long an acquirer waits for a live holder before refusing, by default.
#: run_evals.py uses this; sbe_gate.py passes a short wait instead, because a
#: seconds-long gate check stalling for minutes behind a battery is worse
#: than an honest busy refusal naming the holder.
DEFAULT_TIMEOUT = float(os.environ.get("BROTHERSBE_BATTERY_TIMEOUT", "900"))

#: AGE DECIDES ONLY WHERE A PID CANNOT BE PROBED (the sibling's 2026-07-29
#: fix, kept): on POSIX the holder's liveness wins, however long the run has
#: been going. This threshold applies on Windows and to an unparseable
#: holder record. 24 hours is longer than any run that is not already
#: abandoned.
STALE_SECONDS = 86400.0

# Paths this process actually holds, mapped to the exact bytes it wrote. A
# release must never remove a marker this process did not take: if its own
# marker was stolen while it ran, deleting the thief's file lets a THIRD
# run in.
_HELD = {}


class BatteryMarkerBusy(Exception):
    """Another battery holds the marker and did not release it in time."""


def marker_path(root):
    """The marker file for the tree rooted at `root`. Keyed by realpath so a
    relative and an absolute spelling of the same tree meet the same marker,
    and two different worktrees never share one."""
    key = os.path.realpath(root)
    tag = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return os.path.join(tempfile.gettempdir(),
                        "brothersbe-battery-%s.lock" % tag)


def _read_marker(path):
    """(fields, raw_text, error). fields is the whitespace split of the file,
    raw_text the stripped content for display, error a string only when the
    file EXISTS but could not be read, which is the NO-DATA state callers
    must report rather than swallow."""
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except (IOError, OSError) as e:
        if not os.path.exists(path):
            return None, "", ""
        return None, "", "%s: %s" % (type(e).__name__, e)
    return raw.split(), raw.strip() or "(holder record not yet written)", ""


def marker_state(path):
    """("absent" | "live" | "stale" | "unreadable", holder_text).

    LIVENESS IS CHECKED FIRST, AND IT WINS: a holder pid that answers
    os.kill(pid, 0) is running, and a running battery is not stale no matter
    how long it has been running. An unparseable or empty file is a marker
    being taken RIGHT NOW, not a dead one, so age decides for it. EPERM
    from the probe means the pid exists under another user, which is alive."""
    fields, raw, err = _read_marker(path)
    if err:
        return "unreadable", err
    if fields is None:
        return "absent", ""
    try:
        pid = int(fields[0])
        stamp = float(fields[1])
    except (IndexError, ValueError):
        try:
            old = (time.time() - os.path.getmtime(path)) > STALE_SECONDS
        except OSError:
            return "absent", ""
        return ("stale" if old else "live"), raw
    if os.name == "posix" and pid > 0:
        try:
            os.kill(pid, 0)
        except OSError as exc:
            if exc.errno == errno.ESRCH:
                return "stale", raw
            return "live", raw
        return "live", raw
    return ("stale" if (time.time() - stamp) > STALE_SECONDS else "live"), raw


def head_sha(root):
    """The measured tree's HEAD, short, or "unknown". Fail-soft on purpose:
    the marker must still announce a battery in a tree where git cannot
    answer, and a missing sha is displayed as exactly that."""
    try:
        r = subprocess.run(
            ["git", "-C", root, "rev-parse", "--short=12", "HEAD"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=30,
            env={k: v for k, v in os.environ.items()
                 if not k.startswith("GIT_")})
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else "unknown"


def session_id():
    """The session that started this battery, for ATTRIBUTION in refusal
    messages, never for the decision: a same-session write mid-battery
    invalidates the run exactly as a foreign one does, so the fence does not
    exempt anybody. Env order matches tools/bm_handover.py in the sibling."""
    for var in ("BROTHERSBE_FENCE_SESSION", "CLAUDE_SESSION_ID"):
        v = os.environ.get(var, "").strip()
        if v:
            return v
    return "unrecorded"


def owner_text(tool, root):
    return "%s sha %s session %s" % (tool, head_sha(root), session_id())


def acquire(root, tool, timeout=None, quiet=False):
    """Take the battery marker for the tree at `root`. Returns the marker
    path as the handle. Raises BatteryMarkerBusy when a live holder keeps it
    past the deadline. Stale markers from crashed runs are announced and
    removed, never silently absorbed."""
    path = marker_path(root)
    owner = owner_text(tool, root)
    deadline = time.time() + (DEFAULT_TIMEOUT if timeout is None else timeout)
    announced = False
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
            state, holder = marker_state(path)
            if state == "stale":
                if not quiet:
                    sys.stderr.write(
                        "%s: removing a stale battery marker from a crashed "
                        "run: %s\n" % (tool, holder))
                try:
                    os.remove(path)
                except OSError:
                    pass  # sbe: allow-silent a stale marker another process removed first is the outcome this cleanup wants; the acquire loop above retries and its own busy or unreadable branches do the reporting
                continue
            if state == "absent":
                continue
            if state == "unreadable" and not quiet and not announced:
                sys.stderr.write(
                    "%s: the battery marker at %s exists but could not be "
                    "read (%s). Treating it as live and waiting, because an "
                    "unreadable marker is NO-DATA, not permission.\n"
                    % (tool, path, holder))
            if time.time() >= deadline:
                raise BatteryMarkerBusy(
                    "another battery holds %s (%s). Two batteries over one "
                    "tree measure each other's churn, so this run refuses "
                    "rather than joining it. Wait for the holder to finish, "
                    "or delete that file if you are sure it is dead."
                    % (path, holder or "unreadable holder record"))
            if not announced and not quiet:
                sys.stderr.write(
                    "%s: waiting for another battery to finish: %s\n"
                    % (tool, holder))
                announced = True
            time.sleep(0.25)
            continue
        token = "%d %.3f %s\n" % (os.getpid(), time.time(), owner)
        with os.fdopen(fd, "w") as fh:
            fh.write(token)
        _HELD[path] = token
        return path


def release(handle, quiet=False):
    """Give up a marker THIS process took. Never removes a file whose bytes
    are not the ones this process wrote: a stolen marker now belongs to
    another run, and removing it would let a third one in unannounced."""
    if not handle:
        return
    token = _HELD.pop(handle, None)
    if token is None:
        if not quiet:
            sys.stderr.write(
                "sbe_gatelock: NOT removing %s: this process never took it.\n"
                % handle)
        return
    try:
        with io.open(handle, encoding="utf-8", errors="replace") as fh:
            current = fh.read()
    except (IOError, OSError):
        return
    if current != token:
        if not quiet:
            sys.stderr.write(
                "sbe_gatelock: NOT removing %s: it is held by another run "
                "now (%s), so this run's marker was taken from it while it "
                "ran. Report this: two batteries may have overlapped.\n"
                % (handle, current.strip()))
        return
    try:
        os.remove(handle)
    except OSError:
        pass  # sbe: allow-silent the marker being already gone IS the released state this function exists to reach; nothing downstream reads a removal receipt, and the steal check above already reported the one case that matters


def is_tracked(root, rel):
    """True, False, or None when git could not answer (no repo here, no git
    on PATH, a timeout). None is NO-DATA and the caller must report it; it is
    never the same answer as False."""
    try:
        r = subprocess.run(
            ["git", "-C", root, "ls-files", "--error-unmatch", "--", rel],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=30,
            env={k: v for k, v in os.environ.items()
                 if not k.startswith("GIT_")})
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode == 0:
        return True
    # git prints "error: pathspec ... did not match" for an untracked path at
    # exit 1; 128 with "not a git repository" (or any other complaint) means
    # the question itself could not be asked.
    if r.returncode == 1:
        return False
    return None


def _start_of(holder):
    """The human-readable start time out of a holder line, or ""."""
    fields = holder.split()
    try:
        stamp = float(fields[1])
    except (IndexError, ValueError):
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stamp))


def battery_conflict(root, rel_targets):
    """(deny_reason_or_None, notes). The one decision both hooks share.

    Deny only for a LIVE, READABLE marker over a target git TRACKS in this
    tree. Everything the fence cannot establish is reported on stderr and
    allowed: NO-DATA is never a pass and never a block, the same three-state
    honesty every checker in this repo keeps."""
    notes = []
    if os.environ.get(DISABLE_ENV, "").strip() not in ("", "0"):
        notes.append(
            "battery fence: %s is set, so the battery marker was NOT checked "
            "and this write is allowed. Unset it to restore the fence."
            % DISABLE_ENV)
        return None, notes
    path = marker_path(root)
    if not os.path.exists(path):
        return None, notes
    state, holder = marker_state(path)
    if state == "absent":
        return None, notes
    if state == "stale":
        notes.append(
            "battery fence: a battery marker exists at %s but its holder is "
            "provably gone (%s). It is ignored; the next battery run cleans "
            "it, or delete it by hand." % (path, holder))
        return None, notes
    if state == "unreadable":
        notes.append(
            "battery fence: a battery marker exists at %s but could not be "
            "read (%s). NO-DATA: this write is allowed and the battery state "
            "was NOT checked. Read the file by hand before trusting any run "
            "in flight." % (path, holder))
        return None, notes
    for rel in rel_targets:
        tracked = is_tracked(root, rel)
        if tracked is None:
            notes.append(
                "battery fence: a battery is live here but git could not say "
                "whether %s is tracked, so the fence could not judge it. "
                "NO-DATA: this write is allowed unchecked. A tracked-file "
                "edit would invalidate the run named in %s." % (rel, path))
            continue
        if not tracked:
            continue
        started = _start_of(holder)
        return (
            "BrotherSBE battery fence: a test battery is measuring this tree "
            "right now, and %s is tracked by git here, so writing it would "
            "invalidate the run's baseline. This is the recorded failure "
            "class of measuring a tree you are still editing: the run keeps "
            "going and its verdicts stop meaning anything.\n"
            "The battery: %s%s, marker %s.\n"
            "What proceeds, and nothing else does:\n"
            "  1. Wait for the battery to finish; the marker disappears when "
            "it does.\n"
            "  2. Write an untracked scratch path instead; only tracked "
            "files are fenced.\n"
            "  3. If you are certain the run is dead, note that a LIVE "
            "marker means its pid answered a liveness probe just now; a "
            "crashed run reads as stale and is ignored automatically.\n"
            "  4. A deliberate override is %s=1, which is announced on "
            "stderr on every write while it is set."
            % (rel, holder,
               (", started %s" % started) if started else "",
               path, DISABLE_ENV)), notes
    return None, notes


if __name__ == "__main__":
    # Diagnostics only, on stderr, matching the sibling hooks' subcommand
    # style: stdout stays empty because nothing here is a hook decision.
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    p = marker_path(root)
    state, holder = marker_state(p)
    sys.stderr.write("marker %s\nstate %s\nholder %s\n"
                     % (p, state, holder or "(none)"))
    sys.exit(0)
