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

import claim_store
import journal

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


def branch_for(unit_id):
    """The exact lane branch name acquire() builds for `unit_id`, exposed so
    a caller outside this module (brother_run.py's resume settle path,
    RESUME-FIX F2) can ask the same question acquire() answers internally
    -- "what branch would this unit's lane be on" -- without a second copy
    of the sanitizer living in two files."""
    safe = "".join(c if c.isalnum() or c in "-_" else "-"
                   for c in str(unit_id))[:48]
    return BRANCH_PREFIX + (safe or "unit")


def _crash_orphaned_claim(unit_id):
    """True only when THIS run's own journal recorded, BEFORE the current
    attempt ever claimed `unit_id`, that its PRIOR claim was still
    state=="claimed" and dead (the owning pid gone, or its lease expired):
    the one shape no ordinary flow ever leaves behind. release() (called
    at the end of every ordinary dispatch, PASS or FAIL alike) always
    moves state away from "claimed" to something else ("done", "failed",
    ...) before the process that held the claim exits normally; a claim
    still reading "claimed" was never released at all, which only a kill
    (or an equivalent hard crash) between claim and release produces.

    Read from the JOURNAL (brother_run.py's own "claim.orphaned_by_kill"
    event, written by _settle_units_already_delivered before the round
    loop makes any new claim) rather than from claims.json's CURRENT
    state, because by the time this module's acquire() runs,
    claim_store.acquire() (not owned here, called earlier in the same
    round by loop_bridge) has already overwritten the old dead claim with
    a fresh one for the new attempt -- state "claimed" again, but live and
    owned by the very process asking the question. Reading claims.json
    here would always see that fresh claim and could never tell a genuine
    kill from an ordinary release-then-reclaim.

    REPAIR ROUND 3, R1: a plain any() over the whole journal used to LATCH
    for the rest of the run -- one kill early on made every LATER round for
    the same unit (a clean PASS, a FAIL, or a QUARANTINE that released the
    lane the ordinary way) read as crash-orphaned too, because the old
    "claim.orphaned_by_kill" event never went away. That reopened the exact
    quarantine-reuse regression this file's docstring already tells the
    story of (measured live 2026-09-09: a quarantined lane read back as
    delivered on its very next ordinary retry) -- only one kill later than
    the first fix closed it for.

    The fix walks the journal IN ORDER (journal.read()'s own contract) and
    tracks the signal rather than testing for its mere presence:
    claim_store.release()'s own "claim.released" event -- written at the
    end of every ordinary dispatch, PASS, FAIL or QUARANTINE alike -- clears
    the latch for this unit_id the moment it is seen. A later
    "claim.orphaned_by_kill" for a genuinely new kill sets it again. So the
    signal is scoped to the attempt it was written for: proven true only
    when the LAST thing this unit's journal trail says about it is an
    unreleased kill, never merely that a kill happened at some point
    earlier in the run.

    False on any doubt (no run directory known, no journal, no matching
    event, or the latest matching event is a release): reuse only ever
    happens on affirmative proof, never on an absence of evidence against
    it."""
    run_dir = journal.run_dir_from_env()
    if not run_dir:
        return False
    events = journal.read(run_dir)
    orphaned = False
    for e in (events or []):
        if e.get("unit_id") != str(unit_id):
            continue
        etype = e.get("type")
        if etype == "claim.orphaned_by_kill":
            orphaned = True
        elif etype == "claim.released":
            orphaned = False
    return orphaned


def _lane_holds_a_commit(repo, branch, runner=None):
    """True when `branch` carries at least one commit beyond its own fork
    point with HEAD -- the unit's own prior write this whole reuse exists
    to preserve. An EMPTY lane (the worker was killed before it ever wrote
    or committed anything, still sitting exactly at the revision it was
    created from) has nothing worth preserving, and reusing it anyway
    turned out to be actively harmful (found live, 2026-09-09: a worker
    killed mid-sleep, before touching a single file, then failed its check
    on every retry once its empty lane was reused instead of a truly fresh
    one). False on any git error: never a guess in the direction that
    risks a stale checkout."""
    base_proc = _git(["merge-base", branch, "HEAD"], repo, runner)
    if base_proc.returncode != 0:
        return False
    base = (base_proc.stdout or "").strip()
    if not base:
        return False
    count_proc = _git(["rev-list", "--count", "%s..%s" % (base, branch)],
                      repo, runner)
    if count_proc.returncode != 0:
        return False
    try:
        return int((count_proc.stdout or "0").strip()) > 0
    except ValueError:
        return False


def _path_from_status_line(line):
    """The path a `git status --porcelain` line names, path only (a rename
    line's NEW path is what now exists). Mirrors model_worker.py's own
    collect_artifacts parser -- porcelain format XY<space>path, or
    "XY path -> newpath" on rename -- so this file does not invent a
    second reading of the same format."""
    path_part = line[3:] if len(line) > 3 else line.strip()
    if " -> " in path_part:
        path_part = path_part.split(" -> ", 1)[1]
    return path_part.strip().strip('"')


def _within_owned_paths(path, owned_paths):
    """True only when `path` sits at or under one of `owned_paths` (repo-
    relative, the same shape a row's own "owns" list already carries
    elsewhere in this estate). None or an empty list is never an
    exemption: a caller that names nothing owns nothing extra, so every
    byte of dirt counts against reuse."""
    if not owned_paths or not path:
        return False
    norm = path.replace(os.sep, "/")
    for owned in owned_paths:
        owned_norm = str(owned).replace(os.sep, "/").rstrip("/")
        if not owned_norm:
            continue
        if norm == owned_norm or norm.startswith(owned_norm + "/"):
            return True
    return False


def _retain_dirty_lane(repo, path, branch, runner=None):
    """Frees `branch` for a fresh checkout by renaming the STALE worktree's
    OWN branch aside, in place -- a pure ref update that writes not one
    byte in the working tree, so the uncommitted dirt this whole path
    exists to protect is never touched. Returns the new branch name, or
    None when the rename itself failed (a git error), which the caller
    treats as a hard refusal of the unit rather than risk
    _clear_stale_lane's own --force removal discarding work nobody has
    looked at."""
    retained = "%s-retained-%d" % (branch, int(time.time() * 1000))
    proc = _git(["branch", "-m", retained], path, runner)
    if proc.returncode != 0:
        return None
    return retained


def _reuse_lane_if_proven(repo, branch, unit_id, runner=None, owned_paths=None):
    """(path_or_None, note, refuse, retained_branch). RESUME-FIX F3: a dead claim's own
    lane, still on disk after a kill, is reused rather than destroyed and
    recreated empty -- IF it can be corroborated to still be this unit's
    own lane, to have been orphaned by an actual kill, and to actually
    hold the write worth preserving, and (REPAIR ROUND 3, R4) to hold
    nothing MORE than that write. This is corroboration, not proof: the
    journal record checked at signal 3 is written by the very process
    that goes on to decide reuse, so a bug in this same module could in
    principle write a false one. What follows is every check this estate
    can actually make without guessing, not a claim that no other
    explanation could produce the same trail:

      1. the branch name (already 1:1 with unit_id, by construction: this
         module never creates lane/<x> for any unit but the one that
         sanitizes to <x>);
      2. a worktree still registered to that branch on disk (_stale_lane,
         which reads git's own admin directories, the same way
         orphan_report() already does, rather than trusting the branch
         name alone);
      3. THIS run's own journal recording that this exact unit_id acquired
         this exact branch before -- the corroborating fact signal 1 alone
         cannot give, because sanitizing collapses some distinct ids to
         the same 48-character prefix;
      4. THIS run's own claim store showing the unit's claim is still
         state=="claimed" and dead (_crash_orphaned_claim, above) -- a
         crash-in-flight, never a unit that finished a round the ordinary
         way (a clean PASS, a FAIL, a QUARANTINE) and was released.
         Without this fourth check the ordinary ROUND-TO-ROUND retry of a
         ordinarily-failed unit reused its OWN prior, deliberately-kept
         lane (QUARANTINE preserves a lane "for a person to look at",
         never for a fresh claim to silently inherit), which read a
         quarantined write as delivered on the retry;
      5. the lane branch actually carries a commit beyond its own fork
         point (_lane_holds_a_commit, above) -- a kill that lands before
         the worker ever writes anything leaves an EMPTY lane, which has
         nothing to preserve and, measured live, is actively worse to
         reuse than to recreate;
      6. the working tree holds nothing UNCOMMITTED beyond that same
         commit, or any uncommitted dirt is confined to `owned_paths`
         (the unit's own declared write scope, when the caller has one to
         give -- None by default, which allows no exemption at all). A
         kill that lands AFTER a real commit but WHILE the worker was
         still writing (a half-write) leaves exactly that shape: signal 5
         is satisfied by the real commit, and reusing the lane as-is would
         hand the next attempt an unreviewed half-write mixed in with its
         own. When this signal fails the lane is never destroyed either
         (dirty() reading a `git status --porcelain` on it is exactly the
         evidence release() already refuses to discard without being
         told): _retain_dirty_lane renames its branch aside so `branch`
         is free for a genuinely fresh worktree, and the dirty one is
         left on disk, named in the note, for a person to look at.

    Any one of the six missing falls back to the existing clear-and-
    recreate path (_clear_stale_lane), UNLESS signal 6 fails AND the
    retaining rename itself fails, in which case `refuse` carries the
    NO-DATA reason the caller must return as the whole unit's own
    refusal, rather than let the ordinary clear path force-remove a lane
    still holding unreviewed work. In every other case `refuse` is None
    and the caller reads only the first two fields, unchanged."""
    stale = _stale_lane(repo, branch, runner)
    if stale is None:
        return None, None, None, None  # nothing stale at all; the ordinary fresh path applies
    if not stale.get("path") or not os.path.isdir(stale["path"]):
        return None, ("a stale lane %s exists at %s but no worktree is "
                      "registered for it on disk, so it cannot be proven to "
                      "still hold a prior write; not reused"
                      % (branch, stale["sha"][:9])), None, None
    run_dir = journal.run_dir_from_env()
    events = journal.read(run_dir) if run_dir else None
    matched = any(
        e.get("type") == "lane.acquired" and e.get("unit_id") == str(unit_id)
        and (e.get("payload") or {}).get("branch") == branch
        for e in (events or []))
    if not matched:
        return None, ("a stale lane %s exists at %s but this run's own "
                      "journal has no record of unit %s ever acquiring it, "
                      "so it cannot be proven to be this unit's own lane; "
                      "not reused" % (branch, stale["sha"][:9], unit_id)), None, None
    if not _crash_orphaned_claim(unit_id):
        return None, ("a stale lane %s exists at %s but this run's own "
                      "claim store does not show unit %s still claimed and "
                      "dead, so this lane was not orphaned by a kill (it "
                      "finished an ordinary round and was released "
                      "normally); not reused" % (branch, stale["sha"][:9],
                                                 unit_id)), None, None
    if not _lane_holds_a_commit(repo, branch, runner):
        return None, ("a stale lane %s exists at %s but carries no commit "
                      "beyond its own fork point, so it holds nothing worth "
                      "preserving; not reused" % (branch, stale["sha"][:9])), None, None
    dirt = dirty(stale["path"], runner)
    if dirt is None:
        return None, ("a stale lane %s exists at %s but its working tree "
                      "could not be read, so it cannot be proven clean "
                      "enough to reuse; not reused" % (branch, stale["sha"][:9])), None, None
    extra = [d for d in dirt
            if not _within_owned_paths(_path_from_status_line(d), owned_paths)]
    if extra:
        retained_branch = _retain_dirty_lane(repo, stale["path"], branch, runner)
        if retained_branch is None:
            return None, None, (
                "%s: a stale lane %s exists at %s with %d uncommitted "
                "change(s) beyond its own committed write, not confined to "
                "the unit's own owned paths, and could not be retained (its "
                "branch could not be renamed aside); the unit is refused "
                "rather than risk discarding work nobody has looked at"
                % (NODATA, branch, stale["sha"][:9], len(extra))), None
        return None, ("a stale lane %s exists at %s but its working tree "
                      "carries %d uncommitted change(s) beyond its own "
                      "committed write, not confined to the unit's own "
                      "owned paths (%s); RETAINED at %s on %s rather than "
                      "discarded, and a fresh lane is used instead"
                      % (branch, stale["sha"][:9], len(extra),
                         "; ".join(extra[:5]), stale["path"],
                         retained_branch)), None, retained_branch
    return stale["path"], ("reusing lane %s from an earlier attempt in this "
                           "run at %s, corroborated by its branch, a "
                           "registered worktree, this run's own journal, a "
                           "claim still claimed and dead, a real commit "
                           "beyond its fork point, and a working tree with "
                           "no dirt beyond that commit"
                           % (branch, stale["sha"][:9])), None, None


def acquire(repo, unit_id, root=None, runner=None, owned_paths=None):
    """(path, branch, problem). A private tree for one unit, or a stated reason.

    Never raises and never returns the canonical tree as a consolation: a caller
    that got no lane must reduce concurrency, not write where everyone else is.

    REPAIR ROUND 3, R3: the temp base used to be made with
    tempfile.mkdtemp() up front, before the reuse decision below, so every
    reuse (which never touches `path` -- it overwrites it with the
    existing lane's own path) still left a fresh, empty brother-lane-*
    directory on disk with nothing ever created inside it and nobody to
    remove it. It is now made only on the branch that actually needs a
    place to put a fresh worktree, after reuse has been refused.

    `owned_paths`, REPAIR ROUND 3, R4: the unit's own declared write scope
    (a row's "owns" list, when the caller has one), passed straight
    through to _reuse_lane_if_proven's sixth signal. None (the default,
    and every existing caller of this function today) allows no
    exemption at all: a reused lane must then be perfectly clean beyond
    its own committed write.
    """
    if not os.path.isdir(os.path.join(repo, ".git")) and not os.path.isfile(
            os.path.join(repo, ".git")):
        return None, None, "%s is not a git repository, so no lane was created" % repo
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in str(unit_id))[:48]
    branch = branch_for(unit_id)
    with _GIT_LOCK:
        reused_path, reuse_note, refuse, retained_branch = _reuse_lane_if_proven(
            repo, branch, unit_id, runner, owned_paths)
        if refuse:
            return None, None, refuse
        if reuse_note:
            print("worktree_lane: %s" % reuse_note, file=sys.stderr)
        reused = bool(reused_path)
        if reused_path:
            path = reused_path
        else:
            cleared, note = _clear_stale_lane(repo, branch, runner)
            if not cleared:
                return None, None, note
            if note:
                print("worktree_lane: %s" % note, file=sys.stderr)
            base = root or tempfile.mkdtemp(prefix="brother-lane-")
            path = os.path.join(base, safe or "unit")
            proc = _git(["worktree", "add", "-q", "--detach", path, "HEAD"], repo, runner)
            if proc.returncode != 0:
                return None, None, ("git worktree add failed: %s"
                                    % (proc.stderr or proc.stdout or "").strip()[:200])
            made = _git(["checkout", "-q", "-b", branch], path, runner)
            if made.returncode != 0:
                branch = None  # a lane without its own branch is still isolated
    _write_breadcrumb(path, str(unit_id), branch, runner)
    # E59: the private tree this unit's worker is about to write in. No run
    # directory reaches this module (it takes a repository and a unit id), so
    # it reads the one brother_run exports; a lane taken outside a run
    # journals nothing.
    run_dir = journal.run_dir_from_env()
    journal.append(run_dir, "lane.acquired",
                   parent_ids=journal.previous(run_dir), unit_id=unit_id,
                   payload={"branch": branch,
                            "own_branch": branch is not None,
                            "reused": reused,
                            "retained_branch": retained_branch})
    return path, branch, ""


def _admin_dir_for_worktree(path, runner=None):
    """git's own per-worktree admin directory (.git/worktrees/<name>/) for
    the worktree checked out at `path`, or None when it cannot be resolved
    (not a git worktree, or the git call failed). The one resolver every
    reader and writer of LANE_SIDECAR_NAME shares, so the admin directory is
    never computed two different ways."""
    proc = _git(["rev-parse", "--git-dir"], path, runner)
    if proc.returncode != 0:
        return None
    admin_dir = (proc.stdout or "").strip()
    if not admin_dir:
        return None
    if not os.path.isabs(admin_dir):
        admin_dir = os.path.normpath(os.path.join(path, admin_dir))
    return admin_dir


def _write_breadcrumb(path, unit_id, branch, runner=None):
    """Best-effort. A lane without a breadcrumb still isolates; orphan_report()
    falls back to the branch name for it, which is why this never raises.

    Also carries `created_at`, written once here and never again: the one
    stable, platform-independent creation time this estate has for a lane.
    cleanup_lane() used to read st_birthtime/st_ctime off the worktree
    directory itself, which is fine on macOS but wrong on Linux, where
    st_ctime is the last METADATA change, not creation, and a coarse kernel
    clock can stamp it equal to a file written moments later. See
    worktree_lane.lane_created_at(), below, which reads this back."""
    admin_dir = _admin_dir_for_worktree(path, runner)
    if not admin_dir:
        return
    try:
        with open(os.path.join(admin_dir, LANE_SIDECAR_NAME), "w",
                  encoding="utf-8") as fh:
            json.dump({"unit_id": unit_id, "branch": branch,
                      "created_at": time.time()}, fh)
    except OSError as exc:
        # the lane still works without its breadcrumb, but the orphan report
        # will read it as UNKNOWN, so the loss is said out loud rather than
        # discovered at the next crash
        print("worktree_lane: could not write breadcrumb for %s: %s"
              % (unit_id, exc), file=sys.stderr)


def lane_created_at(path, runner=None):
    """The stable creation time for the lane worktree at `path`, or None
    when nothing usable was found. Must be called BEFORE the worktree is
    removed: every source this reads lives inside `path` and is gone the
    moment `git worktree remove` succeeds.

    Read in this order: (1) the breadcrumb sidecar's own `created_at`,
    written once by _write_breadcrumb() at acquire() time and stable on
    every platform; (2) st_birthtime, the true filesystem creation time,
    where the platform has one (not Linux); (3) the mtime of the
    worktree's own `.git` file -- a linked worktree's `.git` is a small
    file git writes once at `worktree add` and never touches again, so its
    mtime is a creation time even on a platform with neither of the above.

    Linux has no st_birthtime, so falling back straight to st_ctime (the
    last metadata change) used to be the only portable option, and it is
    wrong: on the Linux CI runner a lane's worktree ctime and its
    transcript folder's newest file mtime were measured equal to the
    fraction of a second (the kernel's clock is coarser than macOS's), so
    the age gate in integrate._retire_transcript_folder refused forever.
    The sidecar closes that by giving every platform the same answer."""
    admin_dir = _admin_dir_for_worktree(path, runner)
    if admin_dir:
        try:
            with open(os.path.join(admin_dir, LANE_SIDECAR_NAME),
                      encoding="utf-8") as fh:
                created_at = json.load(fh).get("created_at")
            if (isinstance(created_at, (int, float))
                    and not isinstance(created_at, bool)):
                return float(created_at)
        except (OSError, ValueError):
            pass
    try:
        birthtime = getattr(os.stat(path), "st_birthtime", None)
    except OSError:
        birthtime = None
    if birthtime:
        return birthtime
    try:
        return os.stat(os.path.join(path, ".git")).st_mtime
    except OSError:  # sbe: allow-silent documented contract (see docstring): the last of three fallback sources returns None "when nothing usable was found," and integrate._retire_transcript_folder already refuses to retire on a None lane_created_at rather than guessing, so no record is lost by staying silent here
        return None


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


def _clear_stale_branch_lock(repo, branch, runner=None):
    """Remove a stale `refs/heads/<branch>.lock` left by a crashed run, or
    None when there is nothing to remove.

    THE CRASH THIS CLOSES, reproduced 2026-09-14: a run SIGKILLed while its
    git was between `checkout -b`'s lock-create and the rename that writes
    the ref leaves the lock file with no ref behind it. git never removes a
    ref lock on its own (it has no owner recorded in the file to prove
    staleness by), so the next `git checkout -b <branch>` in a fresh lane
    dies with "cannot lock ref '<branch>': ... File exists", acquire() reads
    that as `made.returncode != 0`, sets branch=None, and the unit is
    refused forever with "its own checkout failed": the exact flake this
    estate's resume tests were seeing on CI, where a slow runner hits the
    sub-millisecond kill window often enough to matter.

    SAFE TO REMOVE HERE. Removing a lock a LIVE git holds would be a
    corruption path, so this is only ever done for a `lane/<unit>` branch
    (the caller already restricts itself to BRANCH_PREFIX), and only from
    acquire(), which is the one and only creator of that branch, runs under
    this module's own _GIT_LOCK, and is reached only after claim_store has
    granted this process the unit's exclusive claim across every process on
    the host. So no other live git can be mid-creation of THIS exact lane
    branch: a `lane/<unit>.lock` found here is always a dead run's residue.
    The scope is a single named file under the common git dir; it never
    touches index.lock, packed-refs.lock, or any ref but this one.

    Branch refs live in the COMMON git dir (shared across linked
    worktrees), never a per-worktree one, so the lock is resolved against
    `git rev-parse --git-common-dir` rather than any lane's own admin dir."""
    common = _git(["rev-parse", "--git-common-dir"], repo, runner)
    if common.returncode != 0:
        return None  # not resolvable; the ordinary checkout path will report it
    common_dir = (common.stdout or "").strip()
    if not common_dir:
        return None
    if not os.path.isabs(common_dir):
        common_dir = os.path.normpath(os.path.join(repo, common_dir))
    lock_path = os.path.join(common_dir, "refs", "heads", branch + ".lock")
    if not os.path.isfile(lock_path):
        return None
    try:
        os.remove(lock_path)
    except OSError as exc:
        # Never fatal: the ordinary `git checkout -b` below will still fail
        # loudly on the lock and the unit is refused with its own reason,
        # exactly as before this sweep existed. Said out loud so the cause
        # is on the record rather than discovered at the next crash.
        print("worktree_lane: a stale ref lock for %s exists at %s and could "
              "not be removed (%s); the fresh checkout will report it"
              % (branch, lock_path, exc), file=sys.stderr)
        return None
    return ("a stale ref lock for %s (left by an earlier run killed mid "
            "checkout) was removed before creating a fresh lane" % branch)


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
    # A SIGKILLed run whose git was mid `checkout -b` for this exact branch
    # leaves a stale `refs/heads/<branch>.lock` with NO ref written: git
    # creates the lock, then dies before the rename that would produce the
    # ref. That lock is invisible to _stale_lane below (rev-parse --verify
    # of the ref exits 1, so `stale` reads None and this function used to
    # return "nothing to clear"), yet acquire()'s own `git checkout -b`
    # then fails with "cannot lock ref ... File exists", so the unit is
    # refused forever with "its own checkout failed" on every retry. Swept
    # here, before _stale_lane, because the lock outlives the ref it never
    # became.
    lock_note = _clear_stale_branch_lock(repo, branch, runner)
    stale = _stale_lane(repo, branch, runner)
    if stale is None:
        return True, lock_note
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
    depends on another module's internals to do its own reporting. Note the
    LIVENESS question is not part of that independence: it comes from
    claim_store.dead_reason(), because two subsystems answering dead-or-alive
    differently is the defect, not the coupling."""
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
      ABANDONED the lane is still on disk but its claim is no longer live
                (the lease expired, or the owning pid died on this host,
                exactly as claim_store.live() decides it), or was released,
                and nobody has cleared the lane.
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
        # ONE DEFINITION OF LIVENESS. This used to be its own
        # `expires_at > now` arithmetic, which called a claim still leased
        # while claim_store.reconcile(), which also treats a dead owning pid
        # as not live, called the SAME claim abandoned two log lines earlier.
        # Asking claim_store makes the two reads incapable of disagreeing.
        dead = claim_store.dead_reason(claim, now)
        if state == "claimed" and dead is None:
            findings.append(dict(base, classification=OWNED, owner=owner,
                detail="claimed by %s, still leased" % owner))
        else:
            why = ("%s while still claimed" % dead if state == "claimed"
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
