"""Brother Core: machine-local dispatch semaphore and claim-before-
dispatch (unit OR-4).

Replaces unbounded parallel dispatch, where each Brother session
assumes it owns the whole machine, with a real flock-based slot
directory bounding concurrent OpenRouter calls machine-wide, plus a
claim-before-dispatch record so two concurrent sessions can never both
work the same task. Prevents the retry storm Muse named: a timeout
under real resource pressure triggering a double-budget retry that
doubles load on an already-choked machine.

Uses the same real primitives already proven elsewhere in this
codebase: a real flock for the bounded slot count (mirroring the
approach), and O_CREAT|O_EXCL for the claim record (the exact atomic
primitive proven in unit D's leases.py, real 5-process race included).
"""

import fcntl
import json
import math
import os
import re
import time


class NoSlotAvailable(Exception):
    """Raised when acquire_slot times out waiting for a free slot."""


class TaskAlreadyClaimed(Exception):
    """Raised when a task hash is already claimed by a different
    holder, and the claim has not been released."""


def state_root():
    """The shared admission pool, including calls outside the bridge."""
    return (os.environ.get("BROTHER_OR_STATE_ROOT")
            or os.path.expanduser("~/.claude/brother-or-dispatch-state"))


def effective_slots(root, requested):
    """A caller can narrow the shared policy, never widen it.

    The limit belongs to one machine-local policy file, not each fanout's
    worker count. Missing policy uses four slots. Malformed policy refuses
    admission, so a typo cannot turn a bounded run into an unbounded one.
    """
    if isinstance(requested, bool) or not isinstance(requested, int) or requested < 1:
        raise ValueError("requested dispatch slots must be a positive integer")
    path = os.path.join(root, "dispatch-policy.json")
    try:
        with open(path, encoding="utf-8") as handle:
            policy = json.load(handle)
    except FileNotFoundError:
        policy = {"max_concurrent_calls": 4}
    if not isinstance(policy, dict):
        raise ValueError("dispatch policy must be an object")
    limit = policy.get("max_concurrent_calls")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("dispatch policy max_concurrent_calls must be a positive integer")
    return min(requested, limit)


def _checked_dir(root, name: str) -> str:
    """REQ-06: refuse a slots or claims directory that is itself a symlink
    or whose realpath leaves the state root. Called before os.makedirs or any
    open under it, so a directory-level symlink never routes a slot file or a
    claim outside the state root. O_NOFOLLOW on a per-file path can never
    catch this: the escape already happened one directory up."""
    if not isinstance(root, str) or not root:
        raise ValueError("root must be a non-empty string")
    candidate = os.path.join(root, name)
    if os.path.islink(candidate):
        raise ValueError("%s is a symlink and is refused: %r" % (name, candidate))
    real_root = os.path.realpath(root)
    real_candidate = os.path.realpath(candidate)
    if real_candidate != real_root and not real_candidate.startswith(real_root + os.sep):
        raise ValueError("%s escapes the state root: %r" % (name, candidate))
    return candidate


def _slot_dir(root):
    return os.path.join(root, "or-dispatch-slots")


def _claims_dir(root):
    return os.path.join(root, "or-dispatch-claims")


def acquire_slot(root, max_slots, holder_id, timeout_seconds=60,
                  poll_interval=0.05):
    """Blocks (polling) until one of max_slots numbered slot files can
    be exclusively flocked, or raises NoSlotAvailable after
    timeout_seconds. Returns an open file handle; the caller must call
    release_slot(handle) when done (the OS also releases the flock
    automatically if the process exits without calling it, so a crash
    cannot leave a slot permanently stuck)."""
    if not isinstance(root, str) or not root:
        raise ValueError("root must be a non-empty string")
    max_slots = effective_slots(root, max_slots)
    for label, value in (("timeout_seconds", timeout_seconds), ("poll_interval", poll_interval)):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0
                or (label == "poll_interval" and value == 0)):
            raise ValueError("%s must be finite and nonnegative (poll interval positive)" % label)
    slot_dir = _checked_dir(root, "or-dispatch-slots")
    os.makedirs(slot_dir, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    while True:
        for i in range(max_slots):
            path = os.path.join(slot_dir, "slot-%d" % i)
            # O_NOFOLLOW: a symlink planted at a slot path was followed and
            # its target overwritten with the holder line (proven 2026-09-20).
            # A slot that is a symlink is not a slot: skip it, never follow it.
            try:
                fh = os.fdopen(os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600), "a")
            except OSError:
                continue
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                fh.close()
                continue
            fh.seek(0)
            fh.truncate()
            fh.write("%s %f\n" % (holder_id, time.time()))
            fh.flush()
            return fh
        if time.monotonic() >= deadline:
            raise NoSlotAvailable(
                "no slot free among %d after %ss" % (max_slots, timeout_seconds)
            )
        time.sleep(poll_interval)


def release_slot(file_handle):
    fcntl.flock(file_handle.fileno(), fcntl.LOCK_UN)
    file_handle.close()


_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _claim_path(root, task_hash):
    """task_hash becomes a file name, so it is a trust boundary: a value
    like '../../x' used to write outside the claims directory."""
    if not isinstance(task_hash, str) or not _SAFE_NAME_RE.match(task_hash) \
            or ".." in task_hash:
        raise ValueError("task_hash is not a safe file name: %r" % (task_hash,))
    _checked_dir(root, "or-dispatch-claims")
    return os.path.join(_claims_dir(root), task_hash)


def _claim_owner(path, task_hash):
    """The holder named in a claim file. An empty, truncated or unreadable
    claim (a crash mid-write) is held by SOMEBODY we cannot name: it blocks
    as TaskAlreadyClaimed and is never read as free. One reader for claim and
    release, so the two can never disagree about who owns a task."""
    try:
        with open(path) as f:
            words = f.read().split()
    except OSError as exc:
        raise TaskAlreadyClaimed("claim for %r is unreadable (%s)" % (task_hash, exc))
    if not words:
        raise TaskAlreadyClaimed("claim for %r is empty or truncated; owner unknown" % (task_hash,))
    return words[0]


def claim_task(root, task_hash, holder_id, now=None):
    """Claims task_hash for holder_id, atomically via O_CREAT|O_EXCL.
    Raises TaskAlreadyClaimed if a different holder already claimed it.
    Re-claiming with the identical holder_id is idempotent, not a
    collision (mirrors leases.py's own re-acquire-by-same-identity
    rule)."""
    if not isinstance(holder_id, str) or not holder_id or holder_id.split() != [holder_id]:
        # the claim file is "holder time": whitespace in a holder forges one
        raise ValueError("holder_id must be one non-empty token: %r" % (holder_id,))
    now = now if now is not None else time.time()
    path = _claim_path(root, task_hash)
    os.makedirs(_checked_dir(root, "or-dispatch-claims"), exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w") as f:
            f.write("%s %f\n" % (holder_id, now))
        return True
    except FileExistsError:
        existing_holder = _claim_owner(path, task_hash)
        if existing_holder == holder_id:
            return True
        raise TaskAlreadyClaimed(
            "task %r already claimed by %r" % (task_hash, existing_holder)
        )


class EmptyModelAnswer(Exception):
    """The one failure worth a costlier retry: the model answered nothing."""


def release_task(root, task_hash, holder_id):
    """Release a claim THIS holder owns. Idempotent for a task nobody holds.
    Refuses (TaskAlreadyClaimed) when another holder owns it: proven
    2026-09-20, a release with no owner check let holder B delete holder A's
    live claim and take the task. An unreadable claim file is not released
    either: unknown ownership is not permission."""
    path = _claim_path(root, task_hash)
    if not os.path.exists(path):
        return
    owner = _claim_owner(path, task_hash)
    if owner != holder_id:
        raise TaskAlreadyClaimed("task %r is held by %r, not by %r" % (task_hash, owner, holder_id))
    os.remove(path)


def should_retry_at_double_budget(exception):
    """DEFAULT DENY. Only EmptyModelAnswer earns a double-budget retry. An
    OS-level failure must not double load on a choked machine, and an
    UNKNOWN failure must not either: the old default (True for anything not
    listed) doubled the budget on exactly the failures nobody had thought
    about, which is failing open on spend."""
    return isinstance(exception, EmptyModelAnswer)
