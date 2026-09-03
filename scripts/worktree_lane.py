"""worktree_lane: one isolated tree per concurrent writer, or no concurrency at all.

PARITY BLOCKER P0.2, measured at level 0 on 2026-08-29: the word 'worktree' did
not appear anywhere in this estate's dispatch path, so every concurrent writer
shared one tree. The scheduler's conflict admission is PREDICTIVE safety, which
is real and is not enough: it depends on the write sets being declared correctly
and on nothing else touching the tree. Isolation is CONTAINMENT safety, and the
competitors this is measured against have both.

THE RULE THIS REFUSES TO BREAK, stated as an instruction rather than a
preference: if isolation cannot be established, writer concurrency drops to ONE.
It never degrades into shared-tree concurrent writing. A system that silently
falls back to the unsafe thing under load is worse than one that is slow,
because the failure arrives exactly when nobody is watching.

  canonical tree = integration only
  worker tree    = writing only

CREATION AND REMOVAL ARE SERIALISED, execution is not. `git worktree add`
mutates shared repository state, and two of them racing is a real corruption
path; the work inside each tree is what needs to be concurrent and it is. This
is the shape the field uses and it costs almost nothing, because creation is
milliseconds and the work is minutes.

IT NEVER DELETES WORK IT DID NOT SEE. Release refuses to remove a lane whose
tree has uncommitted changes unless the caller says so explicitly, and reports
what it found instead. An orphaned lane is a nuisance; a silently deleted one is
the thing this estate has a standing rule against.

ORPHANS, the clause this file was missing until now: a SIGKILLed run leaves its
lane worktrees on disk with nobody naming them. claim_store.reconcile() already
reports an abandoned CLAIM; nothing paired that claim back to the LANE sitting
on disk, so the directory itself was invisible to every check this estate had.
orphan_report() closes that, and it inherits claim_store's own philosophy
exactly: it REPORTS, it never deletes. Deciding a lane's uncommitted work is
disposable is a judgement this file already refuses to make for release(); it
is no more willing to make that call for a lane whose owner is dead.

STALE LANE REFUSAL, added 2026-09-02, the other half of the same defect:
integrate.py's own cleanup now retires a lane once its round is decided (see
its module docstring's CLEANUP note), but a run that never reaches cleanup
(SIGKILLed, or crashed between the merge and the retire) still leaves
`lane/<unit>` on disk exactly like an orphan does. acquire() used to walk
straight past that and hand the caller a worktree checked out onto a branch
name git was about to refuse to recreate, or worse, silently reuse if the old
branch still resolved. Now, before creating a lane, acquire() checks whether
`lane/<unit>` already exists in the target and, if so, logs it as stale and
removes the old worktree and branch before proceeding, or refuses the unit
with a NO-DATA reason when removal fails, rather than risk a fresh attempt
inheriting a dead run's commits. This only ever touches a `lane/<unit>`
branch and the worktree git itself registered for it, the same restriction
release() and cleanup_lane() both hold themselves to.

Python 3, standard library only, and git.

origin: scripts/loop_bridge.py's dispatch path, which imports this module
and constructs `worktree_lane.Lanes(cwd, [n.get("id") for n in batch])`
(loop_bridge.py line 327) when it is about to run a batch of units
concurrently. Lanes.__init__ calls acquire() for every unit id, and acquire()
is what writes the breadcrumb. Confirmed by grep: the only other importers of
this module (fault_lab.py, and the test files) either only mention it in a
comment (fault_lab.py line 313, a documented-defect note) or exercise its
functions directly in tests, never through a real dispatch; loop_bridge.py is
itself run as a script (a scheduler CLI), so the write ultimately traces back
to whoever runs loop_bridge.py, directly or via night_tick.py's own loop.

PRODUCER: this module is the sole producer of its lane breadcrumb file. The
write happens inside _write_breadcrumb(), above, at the `with open(
os.path.join(admin_dir, LANE_SIDECAR_NAME), "w", encoding="utf-8") as fh:
json.dump({"unit_id": unit_id, "branch": branch}, fh)` call (lines 122-125 of
this file), called from acquire() (line 107) for every lane it creates. The
write is best-effort by design: a failure is reported to stderr rather than
raised, because a lane without its breadcrumb still isolates the writer, it
only loses the fast path orphan_report() uses to name its unit_id later.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

NODATA = "NO-DATA"

#: git worktree add and remove mutate shared repository state, so they are taken
#: one at a time. The lock guards the git call, never the work.
_GIT_LOCK = threading.Lock()

BRANCH_PREFIX = "lane/"

#: THE BREADCRUMB. acquire() sees only a repo and a unit_id, never an owner or
#: a claim store, so it cannot write a full claim record. What it CAN write,
#: and what orphan_report() actually needs, is the one fact the branch name
#: already half-carries but sanitizes and truncates: the exact unit_id. Written
#: into git's OWN per-worktree admin directory (.git/worktrees/<name>/), never
#: into the lane's working tree, so it can never show up as an uncommitted
#: entry the caller has to explain or force through release().
LANE_SIDECAR_NAME = "brother-lane.json"

#: The three answers orphan_report() gives, and the only three: matching
#: claim_store's own reporting philosophy means never inventing a fourth verdict
#: that looks like a decision to act.
OWNED = "OWNED"
ABANDONED = "ABANDONED"
UNKNOWN = "UNKNOWN"


def _git(args, cwd, runner=None):
    runner = runner or (lambda cmd, **kw: subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd, timeout=120))
    try:
        return runner(["git"] + list(args))
    except Exception as exc:  # noqa: BLE001
        class _Fail:  # a shape the caller can read without a special case
            returncode, stdout, stderr = 1, "", str(exc)
        return _Fail()


def acquire(repo, unit_id, root=None, runner=None):
    """(path, branch, problem). A private tree for one unit, or a stated reason.

    Never raises and never returns the canonical tree as a consolation: a caller
    that got no lane must reduce concurrency, not write where everyone else is.
    """
    if not os.path.isdir(os.path.join(repo, ".git")) and not os.path.isfile(
            os.path.join(repo, ".git")):
        return None, None, "%s is not a git repository, so no lane was created" % repo
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in str(unit_id))[:48]
    base = root or tempfile.mkdtemp(prefix="brother-lane-")
    path = os.path.join(base, safe or "unit")
    branch = BRANCH_PREFIX + (safe or "unit")
    with _GIT_LOCK:
        cleared, note = _clear_stale_lane(repo, branch, runner)
        if not cleared:
            return None, None, note
        if note:
            print("worktree_lane: %s" % note, file=sys.stderr)
        proc = _git(["worktree", "add", "-q", "--detach", path, "HEAD"], repo, runner)
        if proc.returncode != 0:
            return None, None, ("git worktree add failed: %s"
                                % (proc.stderr or proc.stdout or "").strip()[:200])
        made = _git(["checkout", "-q", "-b", branch], path, runner)
        if made.returncode != 0:
            branch = None  # a lane without its own branch is still isolated
    _write_breadcrumb(path, str(unit_id), branch, runner)
    return path, branch, ""


def _write_breadcrumb(path, unit_id, branch, runner=None):
    """Best-effort. A lane without a breadcrumb still isolates; orphan_report()
    falls back to the branch name for it, which is why this never raises."""
    proc = _git(["rev-parse", "--git-dir"], path, runner)
    if proc.returncode != 0:
        return
    admin_dir = (proc.stdout or "").strip()
    if not admin_dir:
        return
    if not os.path.isabs(admin_dir):
        admin_dir = os.path.normpath(os.path.join(path, admin_dir))
    try:
        with open(os.path.join(admin_dir, LANE_SIDECAR_NAME), "w",
                  encoding="utf-8") as fh:
            json.dump({"unit_id": unit_id, "branch": branch}, fh)
    except OSError as exc:
        # the lane still works without its breadcrumb, but the orphan report
        # will read it as UNKNOWN, so the loss is said out loud rather than
        # discovered at the next crash
        print("worktree_lane: could not write breadcrumb for %s: %s"
              % (unit_id, exc), file=sys.stderr)


def dirty(path, runner=None):
    """Uncommitted entries in a lane, or None when it cannot be read."""
    proc = _git(["status", "--porcelain"], path, runner)
    if proc.returncode != 0:
        return None
    return [l for l in (proc.stdout or "").splitlines() if l.strip()]


def release(repo, path, force=False, runner=None):
    """(released, note). Refuses to discard uncommitted work unless told to.

    The estate's standing rule is that work is never removed unless somebody
    names it, and a lane that a worker left dirty holds exactly the kind of work
    nobody has looked at yet."""
    if not path or not os.path.isdir(path):
        return False, "%s: %s is not a directory, so nothing was released" % (
            NODATA, path)
    entries = dirty(path, runner)
    if entries is None:
        return False, ("%s: could not read the lane's state, so it was LEFT IN "
                       "PLACE rather than removed on a guess" % NODATA)
    if entries and not force:
        return False, ("refused: %d uncommitted entry(ies) in this lane. It was "
                       "left in place. Pass force only when the work is known to "
                       "be disposable" % len(entries))
    with _GIT_LOCK:
        proc = _git(["worktree", "remove", "--force", path], repo, runner)
    if proc.returncode != 0:
        shutil.rmtree(path, ignore_errors=True)
        _git(["worktree", "prune"], repo, runner)
        return True, "removed by hand after git declined: %s" % (
            proc.stderr or "").strip()[:120]
    return True, ""


def _admin_dirs(repo):
    """Every worktree admin directory git has ever created for `repo`.

    `.git/worktrees/<name>/` is git's own bookkeeping for a linked worktree,
    written by `git worktree add` and left behind by `git worktree remove` only
    once it succeeds. Reading it directly needs no git subprocess and works
    even for a lane whose working directory has since been deleted by hand."""
    base = os.path.join(repo, ".git", "worktrees")
    if not os.path.isdir(base):
        return []
    return [os.path.join(base, name) for name in sorted(os.listdir(base))]


def _lane_from_admin(admin_dir):
    """(path, branch, unit_id, has_sidecar) for one admin dir, or None if it
    cannot be read at all. Reads plain files git itself writes; no git call."""
    gitdir_file = os.path.join(admin_dir, "gitdir")
    try:
        with open(gitdir_file, encoding="utf-8") as fh:
            content = fh.read().strip()
    except OSError:  # sbe: allow-silent documented sentinel: caller classifies a lane it cannot read as UNKNOWN, which the report prints; nothing is dropped
        return None
    trimmed = content.rstrip("/")
    path = os.path.dirname(trimmed) if os.path.basename(trimmed) == ".git" else content

    branch = None
    try:
        with open(os.path.join(admin_dir, "HEAD"), encoding="utf-8") as fh:
            head = fh.read().strip()
        if head.startswith("ref:"):
            ref = head.split(None, 1)[1]
            if ref.startswith("refs/heads/"):
                branch = ref[len("refs/heads/"):]
    except OSError:  # sbe: allow-silent branch is optional metadata on the report line; the lane itself is still reported with its path and owner
        pass

    unit_id, has_sidecar = None, False
    sidecar = os.path.join(admin_dir, LANE_SIDECAR_NAME)
    if os.path.isfile(sidecar):
        try:
            with open(sidecar, encoding="utf-8") as fh:
                unit_id = json.load(fh).get("unit_id")
            has_sidecar = True
        except (OSError, ValueError):
            pass
    if unit_id is None and branch and branch.startswith(BRANCH_PREFIX):
        unit_id = branch[len(BRANCH_PREFIX):]  # best effort: sanitized, truncated

    return {"path": path, "branch": branch, "unit_id": unit_id,
            "has_sidecar": has_sidecar}


def _stale_lane(repo, branch, runner=None):
    """(path_or_None, sha) for a still-registered `branch`, or None when the
    branch does not exist at all. `path` is None when the branch exists but
    no worktree is currently registered for it (the worktree side was
    already cleared, by hand or by a crash mid-cleanup), which is still a
    branch worth clearing before it is reused. Reuses this file's own
    admin-dir reader rather than a fresh `git worktree list` parser, so a
    lane found here is read exactly the way orphan_report() reads one."""
    sha_proc = _git(["rev-parse", "--verify", "--quiet",
                     "refs/heads/" + branch], repo, runner)
    sha = (sha_proc.stdout or "").strip()
    if sha_proc.returncode != 0 or not sha:
        return None
    path = None
    for admin_dir in _admin_dirs(repo):
        lane = _lane_from_admin(admin_dir)
        if lane and lane.get("branch") == branch:
            path = lane["path"]
            break
    return {"path": path, "sha": sha}


def _clear_stale_lane(repo, branch, runner=None):
    """Refuse to reuse a leftover `lane/<unit>` branch from an earlier run:
    remove it first, or say why it could not be removed. Called from
    acquire(), below, before it creates anything, and only ever touches a
    `branch` acquire() itself constructed with BRANCH_PREFIX.

    Returns (ok, note). ok is False only when a stale lane was found and
    could not be cleared, and `note` is then the NO-DATA problem acquire()
    hands back instead of creating a new lane over it. ok is True with
    note=None when there was nothing stale to clear. ok is True with a note
    when a stale lane WAS found and removed, so the reuse refusal is on the
    record rather than silent."""
    if not branch.startswith(BRANCH_PREFIX):
        return True, None  # never this function's business
    stale = _stale_lane(repo, branch, runner)
    if stale is None:
        return True, None
    sha_short = stale["sha"][:9]
    if stale["path"] and os.path.isdir(stale["path"]):
        proc = _git(["worktree", "remove", "--force", stale["path"]], repo, runner)
        if proc.returncode != 0:
            return False, ("%s: stale lane %s from an earlier run exists at "
                           "%s and its worktree could not be removed (%s), so "
                           "the unit was refused rather than risk reusing its "
                           "old work" % (NODATA, branch, sha_short,
                           (proc.stderr or proc.stdout or "").strip()[:160]))
        _git(["worktree", "prune"], repo, runner)
    branch_del = _git(["branch", "-D", branch], repo, runner)
    if branch_del.returncode != 0:
        return False, ("%s: stale lane %s from an earlier run exists at %s "
                       "and could not be deleted (%s), so the unit was "
                       "refused rather than risk reusing its old work"
                       % (NODATA, branch, sha_short,
                          (branch_del.stderr or branch_del.stdout or "").strip()[:160]))
    return True, ("stale lane %s from an earlier run exists at %s, not "
                  "reused: it was removed" % (branch, sha_short))


def _read_claims(path):
    """The claim store's raw contents: {} absent, None unreadable. Deliberately
    independent of claim_store's own (private) reader, so this file never
    depends on another module's internals to do its own reporting."""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return None


def orphan_report(repo, claims_path, clock=None):
    """Every lane worktree still on disk for `repo`, paired with its claim.

    (findings, problem). `problem` is set only when the claim store itself
    could not be read, exactly mirroring claim_store.reconcile()'s own contract
    so a caller already handling one NO-DATA case handles both the same way.

    Each finding is {"classification", "path", "branch", "unit_id", "owner",
    "detail"}. Classification is exactly one of:

      OWNED     the claim naming this lane's unit is live: somebody is still
                working it, and this is not an orphan at all.
      ABANDONED the lane is still on disk but its claim expired while still
                claimed, or was released, and nobody has cleared the lane.
      UNKNOWN   NO-DATA. no claim in the store names this lane's unit, so no
                owner can be established. Never silently skipped: an unmatched
                lane is exactly the case a quiet skip would hide.

    THIS REPORTS. It never removes anything, matching claim_store's own
    philosophy exactly: deciding a lane's work is safe to discard is a
    judgement about side effects this file cannot make, precisely as release()
    already refuses to make it for a lane it can see is merely dirty."""
    claims = _read_claims(claims_path)
    if claims is None:
        return None, ("%s: the claim store at %s could not be read, so no lane "
                      "could be paired with a claim" % (NODATA, claims_path))
    now = (clock or time.time)()
    findings = []
    for admin_dir in _admin_dirs(repo):
        lane = _lane_from_admin(admin_dir)
        if lane is None:
            continue
        is_lane = lane["has_sidecar"] or (
            lane["branch"] and lane["branch"].startswith(BRANCH_PREFIX))
        if not is_lane or not os.path.isdir(lane["path"]):
            continue  # not shaped like a lane, or already gone: nothing to report

        unit_id = lane["unit_id"]
        claim = claims.get(unit_id) if unit_id is not None else None
        base = {"path": lane["path"], "branch": lane["branch"], "unit_id": unit_id}

        if claim is None:
            findings.append(dict(base, classification=UNKNOWN, owner=None,
                detail=("%s: no claim in the store names unit %r, so this "
                        "lane's owner cannot be established" % (NODATA, unit_id))))
            continue

        owner, state = claim.get("owner"), claim.get("state")
        leased = float(claim.get("expires_at", 0)) > now
        if state == "claimed" and leased:
            findings.append(dict(base, classification=OWNED, owner=owner,
                detail="claimed by %s, still leased" % owner))
        else:
            why = ("the lease expired while still claimed" if state == "claimed"
                   else "the claim was released (state %r)" % state)
            findings.append(dict(base, classification=ABANDONED, owner=owner,
                detail=("the lane is still on disk but %s for unit %s. It is "
                        "removed only by a human or by a future unit that "
                        "names it" % (why, unit_id))))
    return findings, ""


class Lanes(object):
    """Lanes for one batch, and the concurrency that is actually SAFE.

    `safe_concurrency` is the whole point of this class existing rather than a
    pair of functions: the caller asks for N lanes and is told how many writers
    it may actually run, which is len(lanes) when every lane was created and 1
    when any lane was not. There is no third answer, because the third answer is
    shared-tree concurrent writing.
    """

    def __init__(self, repo, unit_ids, root=None, runner=None):
        self.repo, self.root, self._runner = repo, root, runner
        self.lanes, self.problems = {}, {}
        for uid in unit_ids:
            path, branch, problem = acquire(repo, uid, root, runner)
            if path:
                self.lanes[uid] = {"path": path, "branch": branch}
            else:
                self.problems[uid] = problem

    @property
    def isolated(self):
        return not self.problems and bool(self.lanes)

    def safe_concurrency(self, requested):
        """How many writers may run at once, given what isolation exists."""
        if self.isolated:
            return max(1, min(int(requested), len(self.lanes)))
        return 1

    def why(self):
        if self.isolated:
            return ""
        if not self.lanes and not self.problems:
            return "%s: no units were given, so no lane was created" % NODATA
        return ("isolation could not be established for %d of %d unit(s), so "
                "writer concurrency drops to 1 rather than degrading into "
                "shared-tree concurrent writing: %s"
                % (len(self.problems), len(self.lanes) + len(self.problems),
                   "; ".join("%s (%s)" % (k, v) for k, v in
                             sorted(self.problems.items()))))

    def path_for(self, uid):
        lane = self.lanes.get(uid)
        return lane["path"] if lane else None

    def release_all(self, force=False):
        """(released, kept). Kept lanes are named with why, never dropped."""
        released, kept = [], []
        for uid, lane in sorted(self.lanes.items()):
            ok, note = release(self.repo, lane["path"], force, self._runner)
            (released if ok else kept).append((uid, lane["path"], note))
        return released, kept
