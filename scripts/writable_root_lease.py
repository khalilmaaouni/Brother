"""writable_root_lease: one exclusive lease per writable root, and one more
at the repository a git worktree shares with its siblings.

WHY THIS EXISTS. On the night of 2026-09-18, five agents worked inside one
worktree kept apart only by hand-written file lists in their briefs: nothing
enforced that "owns" list, it was just prose in a prompt. A directory-only
lease closes that, but a directory-only lease is not enough by itself,
because git worktrees do not each own a private repository: `git worktree
add` gives every linked worktree its own working tree and its own per-
worktree admin directory, but refs/heads/*, the reflog and the stash all
live in the ONE git directory the worktrees share (`git rev-parse
--git-common-dir`). Two agents in two different worktrees of the same
repository can both believe they hold an exclusive lease (their directories
never collide) and still race each other's `git stash` or `git branch -f`,
because that race happens at the common repository, not at either
directory. So a write rooted at a git worktree needs TWO leases, not one:
the root itself, and the common repository behind it.

WHAT THIS MODULE IS NOT. It is not a second claim store. Every actual lease
(exclusivity, TTL expiry, dead-pid reclaim, corrupt-store blocking,
same-owner re-acquire after restart) is claim_store.py's, unchanged and
imported. This module answers exactly one question claim_store cannot
answer on its own: which keys does a write rooted at THIS path need to
hold, and where does the store for those keys live so every worktree of one
repository can see the same one. Composed with claim_store, never
reimplemented beside it.

ALL OR NOTHING. acquire() takes every target a root implies or none of
them: a root inside a git worktree that got the root lease but lost the
race for the common-repository lease releases the root lease again before
returning, rather than leaving a caller holding half of what it asked for
and free to write anyway.

Python 3, standard library only, and git (already a hard dependency of the
repository this module coordinates writes into).
"""
import os
import subprocess

import claim_store

NODATA = "NO-DATA"

#: Lease key prefixes, so the two kinds of target can never collide with
#: each other even if a root path and a common git dir path were ever
#: textually identical (they cannot be in practice, but the prefix makes
#: that a property of the key, not an assumption about paths).
ROOT_PREFIX = "root:"
COMMON_PREFIX = "common:"

#: Where the shared lease store lives by default when a root sits inside a
#: git worktree: inside the COMMON git directory, so every worktree of the
#: same repository resolves to the same file without being told about each
#: other. See default_store_path().
LEASE_STORE_FILENAME = "writable-root-leases.json"


def _git(args, cwd, runner=None):
    """Mirrors worktree_lane.py's own `_git`: a real `git` subprocess by
    default, or an injected `runner` for tests, with any failure to even
    start git turned into a returncode 1 the caller can read like any other
    failed git call, never an uncaught exception."""
    runner = runner or (lambda cmd, **kw: subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd, timeout=120))
    try:
        return runner(["git"] + list(args))
    except Exception as exc:  # noqa: BLE001
        class _Fail:  # a shape the caller can read without a special case
            returncode, stdout, stderr = 1, "", str(exc)
        return _Fail()


def common_git_dir(root, runner=None):
    """Absolute path to the git directory `root` shares with every other
    worktree of the same repository, or None when `root` is not inside a
    git repository, or git could not be run at all.

    Uses `git rev-parse --git-common-dir`, the exact fact worktree_lane.py
    already resolves this same way for refs/heads/*.lock: for a linked
    worktree this differs from `--git-dir` (which points at the private
    per-worktree admin directory); for the primary worktree, or a plain
    non-worktree repository, the two are the same directory. Either way,
    what this returns is the one directory every worktree of the repository
    agrees is shared, which is exactly where refs and the stash live.

    Resolved through os.path.realpath, not just normpath: git itself
    stores worktree admin paths through the OS's canonical form (on macOS,
    /private/var rather than the /var symlink a caller's own tempdir may
    still be spelled with), so two worktrees of one repository must be
    compared after the same symlink resolution or they read as two
    different repositories."""
    proc = _git(["rev-parse", "--git-common-dir"], root, runner)
    if proc.returncode != 0:
        return None
    out = (proc.stdout or "").strip()
    if not out:
        return None
    if not os.path.isabs(out):
        out = os.path.join(root, out)
    return os.path.realpath(out)


def default_store_path(root, runner=None):
    """A lease store path every worktree of `root`'s repository resolves to
    identically: <common-git-dir>/writable-root-leases.json. None when
    `root` has no git repository behind it at all, since there is then no
    shared location this module can name on the caller's behalf; such a
    caller must choose and pass its own `store` path to acquire()."""
    common = common_git_dir(root, runner=runner)
    if common is None:
        return None
    return os.path.join(common, LEASE_STORE_FILENAME)


def targets(root, runner=None):
    """The sorted, deduplicated lease keys a write rooted at `root` must
    hold: the root's own absolute path always, plus the shared repository
    behind it when one exists. Sorted so two callers whose target sets
    overlap always acquire in the same order; acquiring in a consistent
    order is what keeps two composed claim_store.acquire() calls from
    deadlocking against each other, since claim_store's own per-unit lock
    only ever guards one key at a time."""
    root_abs = os.path.realpath(root)
    keys = {ROOT_PREFIX + root_abs}
    common = common_git_dir(root_abs, runner=runner)
    if common is not None:
        keys.add(COMMON_PREFIX + common)
    return sorted(keys)


def _release_many(store, claims, owner, clock=None):
    """Release every (key -> claim) this process itself just took, used
    both by acquire()'s own rollback and by the public release() below.
    Returns the (key, why) pairs for any release that itself failed, so a
    rollback failure is reported rather than swallowed."""
    problems = []
    for key, claim in claims.items():
        held, why = claim_store.release(store, key, owner, state="not-taken",
                                        clock=clock,
                                        attempt=claim.get("attempt"))
        if held is None:
            problems.append((key, why))
    return problems


def acquire(store, root, owner, work_id="", ttl=None, clock=None, runner=None):
    """Take an exclusive lease on every target `root` implies, or none.

    (claims, problem): on success, claims is {target_key: claim} for every
    key targets(root) named, and problem is "". On failure, claims is {}
    (any target this call itself acquired before hitting the failure is
    released again first) and problem says which target could not be
    leased and why, exactly as claim_store.acquire() phrases it (naming the
    other owner and how long they hold it, or the store's own read
    failure for a corrupt lease file, which is never read as free).
    """
    keys = targets(root, runner=runner)
    acquired = {}
    for key in keys:
        claim, problem = claim_store.acquire(store, key, owner,
                                             work_id=work_id, ttl=ttl,
                                             clock=clock)
        if claim is None:
            rollback_problems = _release_many(store, acquired, owner,
                                              clock=clock)
            reason = "could not lease %s for %s: %s" % (key, root, problem)
            if rollback_problems:
                reason += ("; also could not release %s already taken this "
                          "call: %s"
                          % (", ".join(k for k, _w in rollback_problems),
                             "; ".join("%s (%s)" % (k, w)
                                      for k, w in rollback_problems)))
            return {}, reason
        acquired[key] = claim
    return acquired, ""


def release(store, root, owner, state="done", clock=None, runner=None):
    """Release every target `root` implies. Never raises: a target already
    released, or owned by someone else, is reported and skipped rather than
    stopping the rest from releasing.

    (released, problems): released is the list of target keys actually
    released; problems is [(key, why)] for every target that did not."""
    keys = targets(root, runner=runner)
    released, problems = [], []
    for key in keys:
        held, why = claim_store.release(store, key, owner, state=state,
                                        clock=clock)
        if held is None:
            problems.append((key, why))
        else:
            released.append(key)
    return released, problems


def renew(store, root, owner, ttl=None, clock=None, runner=None):
    """Push every target `root` implies out by another lease. Same
    (renewed, problems) shape as release(), for the same reason: one stale
    target must not stop the others from being kept alive."""
    keys = targets(root, runner=runner)
    renewed, problems = [], []
    for key in keys:
        held, why = claim_store.renew(store, key, owner, ttl=ttl, clock=clock)
        if held is None:
            problems.append((key, why))
        else:
            renewed.append(key)
    return renewed, problems


def reconcile(store, clock=None):
    """(findings, problem): delegates straight to claim_store.reconcile.
    Every key this module ever leases lives in the one store claim_store
    already knows how to read (or refuse to guess at, when it is corrupt),
    so a second reconciler here would only be a second copy of the same
    logic."""
    return claim_store.reconcile(store, clock=clock)
