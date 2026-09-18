"""integrate: workers parallel, truth serial.

PARITY BLOCKER P0.4 and P0.5 together, the heaviest unbuilt piece on the board
and the place the strongest competitor is furthest ahead. Until now nothing in
this estate applied a worker's result against the CURRENT canonical revision,
and nothing revalidated a branch that was green on an older base. A unit could
pass in its own lane, merge cleanly, and break canonical, and the first anybody
knew was the next unrelated failure.

THE RULE, and every design choice below falls out of it: a clean merge is not
semantic compatibility. Git proves two changes do not touch the same lines. It
proves nothing about whether they can be true at the same time, and the only
thing that proves that is running the unit's own check ON CANONICAL, AFTER the
apply, at the revision everybody else will actually live with.

THE SEQUENCE, per unit, strictly one at a time:

  lane result -> scope gate -> apply to canonical tip -> VERIFY ON CANONICAL
    green: commit stands, canonical advances, the unit is closed at that revision
    red:   the apply is unwound, the unit goes back for repair ON THE NEW BASE

INTEGRATION CAPACITY IS ONE, by design and not by limitation. Serial truth is
the whole point: two integrations racing recreate exactly the ambiguity this
exists to remove. The lock is cross-process for the same reason the claim
store's is.

WHY THE UNWIND IS SAFE HERE and nowhere else in this estate: canonical is
integration-only ground. Workers write in lanes, so a canonical tree that is
dirty is already a rule violation and integration REFUSES it rather than
working around it. The unwind resets to a tip recorded at entry, under the
lock, with nothing else permitted to write. An unwind that cannot prove the
tree was clean does not run.

FAILURE HERE IS INFORMATION, never discarded work: a red revalidation reports
NEEDS-REPAIR-ON-NEW-BASE with the base named, and a conflict reports CONFLICT
and aborts the merge. Either way the full account (reason, check output, the
canonical revision) is in the returned verdict before anything below touches
the tree, and the caller persists that verdict; integrate_one() itself never
deletes anything.

CLEANUP, added 2026-09-02, amending the line this used to end on ("nothing is
deleted by this module, ever"): integrate(), the batch wrapper below, now
retires a unit's lane right after its round is decided, whatever the verdict.
THE DEFECT THIS CLOSES, observed live: nothing ever removed a finished lane,
so `lane/<unit>` and its worktree survived the run, and a second run of the
same unit found the stale branch and reused it, silently inheriting a dead
attempt's commits into what should have been a fresh one. This is safe for
every verdict, not only INTEGRATED: NEEDS-REPAIR-ON-NEW-BASE leaves the unit
SCHEDULED for a genuinely FRESH claim and a fresh lane off the new canonical
tip next round (see brother_run.py's own comment on that classification), so
nothing a retry needs was still sitting in the old lane to lose; the facts of
why it failed are already captured in the verdict, not in the tree. A CONFLICT
lane is retired the same way, for the same reason. cleanup_lane() only ever
removes a worktree git itself registered for a `lane/<unit>` branch and that
branch itself, never main or a branch a human made, and a removal failure is
reported and printed rather than allowed to change the verdict already
decided: cleanup must never fail a finished proof.

THE MERGE SAYS A MACHINE MADE IT (E45, run 5 critic 1, section 5,
2026-09-03: an auditor reading the history found no trailer, no marker and no
run id, so an engine merge read exactly like a person's). Every integration
merge carries its own message: a summary line naming the engine and the unit,
then a Brother-Run and a Brother-Harness trailer. It goes in the MESSAGE and
never in the author field, because the author is whoever ran the engine and
forging that is a different and worse thing than labelling the commit.

Python 3, standard library only, and git.
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile

import brother_paths
import claim_store
import fault_barrier
import journal
import worktree_lane

NODATA = "NO-DATA"


def _fault_barrier(name):
    """REPAIR C1: the shared, gated implementation now lives in
    fault_barrier.py, one function in one module instead of three
    verbatim copies. See that module's docstring for the three gates
    that keep it inert in production (barrier name match, the stub
    worker seam already active, STARTED/RELEASE under system temp)
    and the bounded timeout that raises instead of hanging."""
    fault_barrier.wait(name)
INTEGRATED = "INTEGRATED"
CONFLICT = "CONFLICT"
NEEDS_REPAIR = "NEEDS-REPAIR-ON-NEW-BASE"
REFUSED = "REFUSED"
#: The unit's lane is ALREADY in canonical. Distinct from INTEGRATED because
#: "I merged this just now" and "this was already here before I looked" are
#: different facts, and collapsing them is what hid a real defect for a week.
ALREADY_INTEGRATED = "ALREADY-INTEGRATED"

#: Mirrors worktree_lane.BRANCH_PREFIX. Duplicated rather than imported,
#: matching this module's existing choice to keep its own `_git` wrapper
#: instead of depending on worktree_lane's.
LANE_BRANCH_PREFIX = "lane/"

#: FINDING 4 follow-up (lane-root gate tightening): the literal
#: worktree_lane.acquire() passes to tempfile.mkdtemp() for every lane it
#: creates (scripts/worktree_lane.py, the `base = root or
#: tempfile.mkdtemp(prefix="brother-lane-")` line). Not exported by that
#: module (no module-level name for it there), so it is copied here rather
#: than guessed at, with this comment naming its source. A real lane
#: worktree's path is always base/<sanitized-unit-id>, so the directory
#: this prefixes is the worktree's PARENT under the lane root, never the
#: worktree's own leaf name.
LANE_WORKTREE_PARENT_PREFIX = "brother-lane-"

#: The environment the engine can export so a merge trailer names the run it
#: belongs to. Read only when the caller passes nothing: a parameter beats an
#: environment variable, and neither is ever guessed. Nothing in this estate
#: exports them yet, so an ordinary run stamps NO-DATA for the run id, which
#: is the honest reading and still names the engine and the harness.
RUN_ID_ENV_VAR = "BROTHER_RUN_ID"
HARNESS_ENV_VAR = "BROTHER_HARNESS_REVISION"

#: The trailer keys, git's own "Key: value" shape, so `git interpret-trailers
#: --parse` reads them back rather than a grep having to.
RUN_TRAILER = "Brother-Run"
HARNESS_TRAILER = "Brother-Harness"


def _merge_message(unit_id, lane_branch, run_id=None, harness_revision=None):
    """The integration merge's own message: one summary line naming the
    engine and what it merged, a blank line, then the two trailers. A value
    the caller did not pass is read from the environment and otherwise reads
    NO-DATA, spelled out rather than omitted, because a missing trailer says
    nothing about whether anybody ever knew the run."""
    # A trailer is ONE line: a value carrying a newline would end the
    # trailer block at that break and git would stop reading it as trailers.
    # _harness_revision's own NO-DATA string quotes git's stderr, which can
    # be multi line, so whitespace is collapsed rather than trusted.
    run = " ".join(str(run_id or os.environ.get(RUN_ID_ENV_VAR)
                       or "").split()) or NODATA
    rev = " ".join(str(harness_revision
                       or os.environ.get(HARNESS_ENV_VAR) or "").split()) or NODATA
    return ("Brother integrated %s from %s\n\n%s: %s\n%s: %s"
            % (unit_id, lane_branch, RUN_TRAILER, run, HARNESS_TRAILER, rev))


#: One integration at a time, cross-process. Capacity is 1 by DESIGN: serial
#: truth is the feature, and the directive says do not optimise this yet.
#: _LOCK_STEM is the value actually handed to claim_store._Lock, which always
#: appends ".lock" itself; LOCK_NAME (the literal file name callers and tests
#: have always used) is derived from it so the two can never drift apart.
_LOCK_STEM = ".integration"
LOCK_NAME = _LOCK_STEM + ".lock"
LOCK_TIMEOUT = 300.0


def _git(args, cwd, runner=None):
    runner = runner or (lambda cmd, **kw: subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd, timeout=300))
    try:
        return runner(["git"] + list(args))
    except Exception as exc:  # noqa: BLE001
        class _F:
            returncode, stdout, stderr = 1, "", str(exc)
        return _F()


def _git_admin_dir(repo):
    """The directory that can hold the lock file, valid in a LINKED WORKTREE
    as well as a primary checkout. In a linked worktree `.git` is a FILE, so
    joining repo/.git/<name> dies with NotADirectoryError (found live
    2026-08-30 when the loop first integrated from a worktree). The common
    dir is also the SEMANTICALLY right place: truth is serial per repository,
    so two integrators in different worktrees of one repo must share one lock."""
    try:
        p = subprocess.run(["git", "-C", repo, "rev-parse", "--git-common-dir"],
                           capture_output=True, text=True, timeout=10)
        if p.returncode == 0 and p.stdout.strip():
            d = p.stdout.strip()
            if not os.path.isabs(d):
                d = os.path.join(repo, d)
            if os.path.isdir(d):
                return os.path.realpath(d)
    except Exception:  # sbe: allow-silent falls back to the primary layout below
        pass
    return os.path.realpath(os.path.join(repo, ".git"))


class _Lock(claim_store._Lock):
    """O_EXCL across processes, same reasoning as the claim store's.

    CONT-0, boundary during_integration: a real SIGKILL mid integrate_one
    (fault_lab.scenario_boundary("during_integration")) left this file
    behind forever, because SIGKILL cannot run __exit__, and nothing here
    ever asked whether the owning pid was still alive. A follow-up acquire
    then waited out the full LOCK_TIMEOUT (300s) instead of reclaiming at
    once. claim_store.py's own _Lock already solved exactly this for the
    claim store's lock file.

    REPAIR (consolidation round): this class used to carry its own verbatim
    copy of claim_store._Lock's O_EXCL dance and _reclaim_if_dead (the two
    docstrings even said "mirrors ... exactly"), which is exactly the kind
    of second copy that drifts. This is now a thin subclass instead: it
    keeps this module's own path (the integration lock always lives at
    _git_admin_dir(repo)/LOCK_NAME, never inside a run directory), and its
    own wording, by overriding claim_store._Lock's class attributes rather
    than its methods.

    M-4: JOURNAL_ON_RECLAIM was False here only because nothing told this
    lock which run directory to write a claim.reclaimed event into, not
    because an integration reclaim is any less worth recording than the
    claim store's own. _journal_run_dir is overridden below to read
    journal.run_dir_from_env(), the same resolver every other event this
    module writes already reads (see integrate_one's own "integrate.merged"
    write); a reclaim journals when a run exported that variable and stays
    silent in the journal (stderr still gets its line either way) when none
    did, exactly as journal.append() already treats an empty run_dir."""

    LABEL = "integrate"
    TIMEOUT_REASON = ("the integration lane at %s has been held for over "
                       "%.0fs. Truth is serial: waiting is correct and "
                       "proceeding is not")
    JOURNAL_ON_RECLAIM = True
    POLL_INTERVAL = 0.05

    def __init__(self, repo, timeout=LOCK_TIMEOUT):
        # claim_store._Lock.__init__ appends ".lock" to whatever path it is
        # given, and _LOCK_STEM + ".lock" == LOCK_NAME, so the file this
        # creates on disk is unchanged: _git_admin_dir(repo)/LOCK_NAME,
        # exactly as before this class became a subclass.
        super().__init__(os.path.join(_git_admin_dir(repo), _LOCK_STEM),
                         timeout=timeout)

    def _journal_run_dir(self):
        # M-4: this lock's own path is never a run directory (it lives at
        # _git_admin_dir(repo)/LOCK_NAME), so claim_store._Lock's default
        # (dirname of the lock's own path) would name the wrong directory.
        # journal.run_dir_from_env() is the resolver this module's other
        # writers already use.
        return journal.run_dir_from_env()


STOP_FILE = ".brother-stop"


def _stop_reason(repo, stop_name=STOP_FILE):
    """The stop file's message, or None when integration may proceed.

    A human halts autonomous integration by creating `<repo>/.brother-stop`,
    whose contents (if any) are quoted back in the refusal so whoever stopped it
    can say why in the same act. An unreadable stop file still STOPS: the file's
    presence is the signal and its text is only the explanation, so a read error
    must never read as permission to merge.
    """
    path = os.path.join(repo, stop_name)
    if not os.path.exists(path):
        return None
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            note = fh.read().strip()
    except OSError as exc:
        note = "(the stop file could not be read: %s)" % exc
    return ("integration is STOPPED by %s. Remove that file to resume. %s"
            % (path, note or "It carries no note."))


def _already_integrated(repo, lane_branch, runner=None):
    """True when this lane's own merge is ALREADY IN canonical's history.

    CORRECTED 2026-09-02, and the bug it fixes was mine, introduced the previous
    night. This asked `merge-base --is-ancestor lane HEAD`, which is TRIVIALLY
    TRUE for a lane that has committed nothing: brother_run creates lane/<unit>
    at canonical's tip when it claims the unit, so before the worker commits, the
    lane IS the tip, and every commit is its own ancestor. A resume landing in
    that window therefore reported ALREADY-INTEGRATED for a unit nobody had
    integrated, dispatched no worker, recorded no evidence, and the verifier then
    correctly refused it. The retry hit the same short-circuit until the budget
    was gone. Diagnosed from two natural failures whose logs were still on disk,
    and the causation proven rather than inferred: a rig that waits for the lane
    branch to exist before killing failed 0 of 3, each with lane == HEAD.

    The exact question is not "is the lane an ancestor" but "was this lane
    MERGED". integrate_one always merges with --no-ff, so a real integration
    leaves a merge commit in canonical whose SECOND PARENT is the lane tip. So
    the signal is: does the lane tip appear as the SECOND parent of any commit
    reachable from HEAD? A lane that has committed nothing is never anybody's
    second parent, and a lane merged long ago still is one, which is the pair
    the ancestry test could not tell apart. SECOND, not any: an empty lane's
    tip IS the fork base, and the base becomes the FIRST parent of the first
    sibling merge that lands after it, so "any parent" read True for a unit
    nobody had merged, released it done with no evidence, and the verifier
    refused it (found 2026-09-03 by E41's zero-change fixture, a no-op unit
    beside a sibling in one round).

    A git error still answers False, which sends the caller down the ordinary
    merge path where a genuine already-up-to-date merge is harmless. Answering
    True on an unreadable repository is the dangerous direction, because it
    silently skips an integration that never happened.
    """
    tip = _git(["rev-parse", lane_branch], repo, runner)
    if tip.returncode != 0:
        return False
    lane_sha = (tip.stdout or "").strip()
    if not lane_sha:
        return False
    walk = _git(["rev-list", "--parents", "HEAD"], repo, runner)
    if walk.returncode != 0:
        return False
    for line in (walk.stdout or "").splitlines():
        parts = line.split()
        # parts[0] is the commit, parts[1] its first parent; a --no-ff merge
        # of a lane puts the lane tip at parts[2].
        if lane_sha in parts[2:]:
            return True
    return False


def dirty_paths(repo, runner=None):
    """Every path `git status --porcelain` reports for `repo` that makes the
    tree dirty by THIS module's rule, in git's own order; [] when the tree is
    clean; None when git status could not run (never guessed clean).

    The one rule, shared with _clean() below and with brother_run.py's own
    refusal before anything is claimed, so the engine refuses at the door
    exactly what integration would refuse minutes later:

    Interpreter bytecode does not make the ground dirty: running a unit's
    own check ON canonical (which this module itself does, by design) can
    leave __pycache__ behind, and refusing the NEXT unit for that side
    effect starved a correct integration on the first live product-path
    run (2026-08-30, readme after tests). Same class and same rule as
    scope_audit._generated_noise: only bytecode, nothing that can carry
    content."""
    proc = _git(["status", "--porcelain"], repo, runner)
    if proc.returncode != 0:
        return None
    out = []
    for line in (proc.stdout or "").splitlines():
        if not line.strip():
            continue
        path = line[3:].strip()
        parts = path.split("/")
        if "__pycache__" in parts or path.endswith((".pyc", ".pyo")):
            continue
        out.append(path)
    return out


def _clean(repo, runner=None):
    paths = dirty_paths(repo, runner)
    return None if paths is None else not paths


def _tip(repo, runner=None):
    proc = _git(["rev-parse", "HEAD"], repo, runner)
    return (proc.stdout or "").strip() if proc.returncode == 0 else None


def _tail_lines(text, max_lines=50):
    """(text, truncated). Truncation is to the LAST max_lines lines, never to
    zero: a check that printed nothing keeps saying so, rather than an empty
    record that could be mistaken for an untruncated empty check."""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text, False
    return "\n".join(lines[-max_lines:]), True


def _run_check(check, cwd, runner=None):
    """(exit_code, detail, truncated). exit_code is None when the check never
    RAN at all (no done_check declared, or the runner itself raised): that is
    a different fact than a check that ran and returned nonzero, and a caller
    that folds the two together cannot tell "unverifiable" from "verified and
    failing"."""
    if not str(check or "").strip():
        return None, ("the unit carries no done_check, so nothing can prove it "
                      "still holds on canonical. That is %s and it blocks, "
                      "because an unverifiable integration is the failure this "
                      "whole module exists to prevent" % NODATA), False
    runner = runner or (lambda cmd, **kw: subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd, shell=True, timeout=600))
    try:
        proc = runner(check)
    except Exception as exc:  # noqa: BLE001
        return None, "the check could not run: %s" % exc, False
    detail, truncated = _tail_lines(
        ((proc.stdout or "") + (proc.stderr or "")).strip())
    return proc.returncode, detail, truncated


def _changed_between(repo, before, after, runner=None):
    """The paths canonical's tree changed from `before` to `after`: [] when
    the two are one revision, None when git cannot read the range (an
    unreadable range is not a measured empty one, and receipt_door reads
    the two differently)."""
    if not before or not after:
        return None
    if before == after:
        return []
    proc = _git(["diff", "--name-only", before, after], repo, runner)
    if proc.returncode != 0:
        return None
    return [p for p in (proc.stdout or "").splitlines() if p.strip()]


#: bm_store.py loaded by path once and cached as (module_or_None, why_not),
#: same technique scripts/attempt_hook.py already uses for scripts/setup.py:
#: tools/ is not a package, this file can run from either the hub dev
#: checkout or the installed bundle, and a plain `import bm_store` would
#: resolve against sys.path and could pick up a different checkout. A load
#: failure is cached too, so a missing or broken bm_store.py prints once per
#: process, not once per unit integrated.
_BM_STORE_CACHE = []


def _load_bm_store():
    here = os.path.dirname(os.path.abspath(__file__))
    for candidate in (
        # hub dev layout: scripts/integrate.py beside products/brothermode/tools/
        os.path.join(os.path.dirname(here), "products", "brothermode",
                     "tools", "bm_store.py"),
        # installed bundle layout: bundle/runtime/integrate.py beside
        # bundle/runtime/hooks/brothermode/tools/bm_store.py
        os.path.join(here, "hooks", "brothermode", "tools", "bm_store.py"),
    ):
        if not os.path.isfile(candidate):
            continue
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "bm_store_for_integrate", candidate)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _BM_STORE_CACHE.append((mod, None))
            return _BM_STORE_CACHE[0]
        except Exception as e:  # noqa: BLE001
            _BM_STORE_CACHE.append((None, "%s: %s" % (type(e).__name__, e)))
            return _BM_STORE_CACHE[0]
    _BM_STORE_CACHE.append((None, "no bm_store.py found beside %s" % here))
    return _BM_STORE_CACHE[0]


def _interactive_fence_conflict(repo, before, lane_branch, runner=None):
    """(conflict_or_None, note). Closes the bypass found live 2026-09-14:
    a path claimed through bm_store.py's interactive fence (the store
    products/brothermode/tools/bm_fence_hook.py checks in front of every
    Cursor/Claude Code edit) was invisible to claim_store.py's own per-unit
    claims, so the SAME path could be landed through brother_run's
    autonomous engine while an interactive session still held it. The two
    stores keep tracking different things (a unit id there, a set of paths
    here) and stay two stores; this is the one place autonomous integration
    now also asks the interactive store, right before it would advance
    canonical, using the exact same query and the exact same
    bm_store.paths_overlap comparison bm_fence_hook.py's active_claims()
    already runs for the interactive side, so the two fences can never read
    a path two different ways.

    `conflict` names the path, the claim's name, lifecycle_uuid and
    session_id. `note` is a one-line reason the check could not be made
    (no bm_store.py, no store at this root, store unreadable): NOT a
    conflict, matching bm_fence_hook.py's own fail-open direction for a
    store that is absent or unreadable, because the overwhelming majority
    of repositories (including every existing integrate.py test fixture)
    never initialize an interactive fence at all, and a transient sqlite
    hiccup must not halt the whole autonomous loop over a check that is
    ADDITIONAL to, not a replacement for, canonical's own merge and
    revalidation gates below."""
    bs, why = _load_bm_store()
    if bs is None:
        return None, "bm_store.py unavailable (%s); interactive fence not checked" % why
    try:
        root, _source = bs.resolve_root(repo)
    except Exception as e:  # noqa: BLE001
        return None, "interactive fence root could not be resolved (%s: %s)" % (
            type(e).__name__, e)
    if root is None:
        return None, None  # no BrotherMode project anchored at this repo at all
    store_file = bs.store_path(root)
    if not os.path.isfile(store_file):
        return None, None  # nobody ever ran `bm_store.py init` here; no fence to check
    touched = _changed_between(repo, before, lane_branch, runner)
    if not touched:
        return None, None  # nothing to compare, or git could not read the range
    try:
        store = bs.ReadOnlyStore(root)
    except Exception as e:  # noqa: BLE001
        return None, ("interactive fence store at %s could not be opened "
                      "read-only (%s: %s)" % (store_file, type(e).__name__, e))
    try:
        rows = bs._exec(store,
            "SELECT c.path AS path, r.name AS name, "
            "r.lifecycle_uuid AS lifecycle_uuid, r.session_id AS session_id "
            "FROM claims c JOIN records r ON r.lifecycle_uuid = c.lifecycle_uuid "
            "WHERE r.state='active'").fetchall()
    except Exception as e:  # noqa: BLE001
        return None, ("interactive fence store at %s could not be read "
                      "(%s: %s)" % (store_file, type(e).__name__, e))
    finally:
        store.close()
    if not rows:
        return None, None
    for path in touched:
        try:
            candidate = bs.canonicalize_path(root, path)
        except Exception:  # noqa: BLE001
            continue  # outside the fenced project; nothing to compare against
        for row in rows:
            if bs.paths_overlap(candidate, row["path"]):
                return {"path": path, "claim_path": row["path"],
                       "name": row["name"], "lifecycle_uuid": row["lifecycle_uuid"],
                       "session_id": row["session_id"]}, None
    return None, None


def integrate_one(repo, lane_branch, unit, runner=None, check_runner=None,
                  run_id=None, harness_revision=None):
    """One unit's lane into canonical, or a named reason why not.

    Returns a dict whose `verdict` is INTEGRATED, CONFLICT, NEEDS_REPAIR,
    REFUSED or NO-DATA, and whose `canonical` names the revision the caller can
    rely on afterwards, whichever way it went.

    `run_id` and `harness_revision` are what the merge commit's trailers say
    this merge belongs to (E45); left None they fall back to the engine's own
    environment and then to NO-DATA, so the trailers are always there."""
    unit_id = str(unit.get("id") or unit.get("unit_id") or "?")

    with _Lock(repo):
        # THE STOP CONTROL, added 2026-09-01. A recon looked for a way to halt
        # autonomous integration and found none: no stop flag, no cancel, and no
        # merge action class in the authority contract, so the only brake on the
        # one function in this estate that advances canonical was to kill the
        # process and hope it was not mid-merge. A file a human can create with
        # one command is the smallest honest brake, and it is read HERE, inside
        # the lock and before the tree is touched, so a stop that arrives during
        # a run takes effect at the next unit rather than corrupting this one.
        stopped = _stop_reason(repo)
        if stopped is not None:
            return {"verdict": REFUSED, "unit": unit_id, "canonical": _tip(repo, runner),
                    "reason": stopped}

        # THE RECOVERY RESOLVER, added 2026-09-01. It answers the one question a
        # crashed run cannot answer from its own state: did this lane's merge
        # already happen? The 2026-08-31 crash measurement recorded the cost of
        # not asking. The resume re-claimed a unit that was already integrated,
        # ran a worker for it again, and the re-merge was a no-op, so the record
        # read clean. It was clean by LUCK: the model happened to write nothing,
        # and a model writing a different valid implementation would have
        # advanced canonical twice for one unit.
        #
        # git answers this exactly and cheaply, and stays correct through the
        # unwind path because that path resets hard rather than reverting: if
        # the lane tip is an ancestor of HEAD, its content is in. Reporting it as
        # its own verdict rather than letting `merge` return already-up-to-date
        # is the whole point, because a silent no-op is indistinguishable from
        # work that was just done.
        if _already_integrated(repo, lane_branch, runner):
            return {"verdict": ALREADY_INTEGRATED, "unit": unit_id,
                    "canonical": _tip(repo, runner),
                    "reason": "lane %s is already an ancestor of canonical, so this "
                              "unit was integrated before this call. Nothing was "
                              "merged and no worker should be dispatched for it "
                              "again." % lane_branch}
        clean = _clean(repo, runner)
        if clean is None:
            return {"verdict": NODATA, "unit": unit_id, "canonical": None,
                    "reason": "the canonical tree's state could not be read, and "
                              "an unwind that cannot prove the tree was clean "
                              "does not run"}
        if not clean:
            return {"verdict": REFUSED, "unit": unit_id, "canonical": _tip(repo, runner),
                    "reason": "the canonical tree is dirty. Canonical is "
                              "integration-only ground, so a dirty tree is "
                              "already a rule violation and integrating over it "
                              "would bury somebody's uncommitted work"}
        before = _tip(repo, runner)
        if not before:
            return {"verdict": NODATA, "unit": unit_id, "canonical": None,
                    "reason": "canonical has no readable tip"}

        # THE INTERACTIVE FENCE CHECK, added to close the bypass found live
        # 2026-09-14 (WBS-70.04's canary): a path an interactive session held
        # through bm_store.py's fence was invisible to this engine's own
        # claim_store.py, which tracks unit ids, never paths. Asked HERE,
        # before the merge touches anything, so a conflict refuses exactly
        # like the dirty-tree and stop-file checks above rather than landing
        # a merge that then has to be unwound.
        fence_conflict, fence_note = _interactive_fence_conflict(
            repo, before, lane_branch, runner)
        if fence_conflict is not None:
            return {"verdict": REFUSED, "unit": unit_id, "canonical": before,
                    "reason": "%s is inside the active BrotherMode interactive "
                              "fence '%s' (lifecycle %s, session %s); the "
                              "autonomous engine refuses to land a write there "
                              "until that fence releases it"
                              % (fence_conflict["path"], fence_conflict["name"],
                                 fence_conflict["lifecycle_uuid"],
                                 fence_conflict["session_id"] or "(none)")}
        if fence_note:
            sys.stderr.write("integrate: %s\n" % fence_note)

        # THE APPLY. --no-ff so the integration is its own commit and the
        # unwind is one reset to a recorded tip. The message is written here
        # rather than left to git's default (E45), so the commit itself says
        # a machine made it and which run to read.
        merged = _git(["merge", "--no-ff", "-m",
                       _merge_message(unit_id, lane_branch, run_id,
                                      harness_revision), lane_branch],
                      repo, runner)
        if merged.returncode != 0:
            _git(["merge", "--abort"], repo, runner)
            return {"verdict": CONFLICT, "unit": unit_id, "canonical": before,
                    "reason": "the lane does not apply to the current canonical "
                              "revision: %s"
                              % (merged.stderr or merged.stdout or "").strip()[:200]}

        # THE REVALIDATION, on canonical, at the revision everybody else will
        # live with. Branch-local green bought admission to this step, nothing
        # more.
        _fault_barrier("during_integration")
        code, detail, truncated = _run_check(unit.get("done_check"), repo,
                                             check_runner)
        after = _tip(repo, runner)
        passed = None if code is None else (code == 0)
        # THE UNIT'S OWN FILES (E41, run 5 critic 3, 2026-09-03): what
        # canonical's tree changed between the tip this lane was merged onto
        # and the tip the merge produced, which is exactly this lane's
        # contribution and never a sibling's landed earlier in the round.
        # Read HERE, the one place both tips are known, because the lane is
        # retired right after this call. A lane that committed nothing
        # merges as a no-op (no merge commit, `after` == `before`) and reads
        # [], the zero-change fact receipt_door refuses to credit; None when
        # git could not read the range, which is not the same fact.
        files_changed = _changed_between(repo, before, after, runner)

        # THE EVIDENCE, row E1: what a delivery record must carry so it proves
        # its own delivery rather than asserting it. Captured here, at the one
        # place the check actually ran, and threaded outward through
        # loop_bridge's claim release into brother_run's own independent
        # verification: the command, its captured exit code, its output (never
        # discarded, only truncated to the tail with the truncation named),
        # the canonical revision the check actually ran against, and the
        # files this unit's own merge changed there.
        evidence = {"check_command": str(unit.get("done_check") or ""),
                   "exit_code": code, "output": detail,
                   "output_truncated": truncated,
                   "canonical_rev": after if passed else None,
                   "files_changed": files_changed}
        # ORCH-02: the fourth and fifth of the five real drop points this
        # run's advisory pass found. This dict and the INTEGRATED result
        # below it were both a fixed shape carrying none of the routing
        # metadata a unit can carry (task_class, worker_profile,
        # review_profile, risk_class, evidence_obligation, the two retry
        # counts, leaf_worker_only), so a reviewer or a merge gate reading
        # the integration record could not see what the unit was even
        # supposed to be. Carried through only when the unit actually has
        # the field: a unit without this metadata still produces the
        # exact evidence and result shape this module produced before.
        for field in ("task_class", "worker_profile", "review_profile",
                     "risk_class", "evidence_obligation",
                     "max_outer_attempts", "max_repair_attempts",
                     "leaf_worker_only"):
            if field in unit:
                evidence[field] = unit[field]

        if passed:
            # E59: the ONE place canonical actually advanced for this unit.
            # This module holds no run directory (it takes a repository and a
            # lane branch), so it reads the one brother_run exports, exactly
            # as it already reads RUN_ID_ENV_VAR for the merge trailers; run
            # it outside a run and nothing is written.
            run_dir = journal.run_dir_from_env()
            journal.append(run_dir, "integrate.merged",
                           parent_ids=journal.previous(run_dir),
                           unit_id=unit_id,
                           payload={"canonical": (after or "")[:12],
                                    "onto": (before or "")[:12],
                                    "files_changed": (len(files_changed)
                                                      if files_changed
                                                      is not None else None)})
            result = {"verdict": INTEGRATED, "unit": unit_id, "canonical": after,
                     "reason": "applied to %s and its own check passed ON "
                               "canonical at %s" % (before[:9], (after or "")[:9]),
                     "check_detail": detail, "evidence": evidence}
            # ORCH-02: the result itself, not only the nested evidence dict,
            # so a caller reading the integration record directly (never
            # descending into evidence) still sees what the unit carried.
            for field in ("task_class", "worker_profile", "review_profile",
                         "risk_class", "evidence_obligation",
                         "max_outer_attempts", "max_repair_attempts",
                         "leaf_worker_only"):
                if field in unit:
                    result[field] = unit[field]
            return result

        # THE UNWIND. Safe because the tree was proven clean at entry, the lock
        # is held, and canonical is integration-only ground.
        _git(["reset", "--hard", before], repo, runner)
        reason = ("its own check FAILED on the current canonical base %s, "
                  "although the lane was green on the base it forked from. A "
                  "clean merge is not semantic compatibility. The apply was "
                  "unwound, canonical stands at %s, and the unit goes back for "
                  "repair ON THIS base, not its old one" % (before[:9], before[:9])
                  if passed is False else
                  "%s: %s. The apply was unwound rather than integrated "
                  "unverified" % (NODATA, detail))
        return {"verdict": NEEDS_REPAIR if passed is False else NODATA,
                "unit": unit_id, "canonical": before, "reason": reason,
                "check_detail": detail, "evidence": evidence}


def _lane_worktree_path(repo, branch, runner=None):
    """The linked worktree path git has registered for `branch`, or None.

    Reads `git worktree list --porcelain` directly rather than depending on
    worktree_lane's own (private) admin-dir reader, mirroring this module's
    existing choice to keep its own `_git` wrapper instead of importing
    another module's."""
    proc = _git(["worktree", "list", "--porcelain"], repo, runner)
    if proc.returncode != 0:
        return None
    path, target = None, "branch refs/heads/" + branch
    for line in (proc.stdout or "").splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):].strip()
        elif line.strip() == target:
            return path
    return None


def _lane_worktree_path_by_slug(repo, unit_id, runner=None):
    """A linked worktree registered under `repo`, found by DIRECTORY NAME
    rather than by the branch it is checked out to.

    REPAIR C6 (2026-09-09 adversarial review of lane/continuity, round 2):
    worktree_lane.acquire() derives both the lane's directory name and its
    branch name from the same sanitized unit_id (path =
    os.path.join(base, safe), branch = BRANCH_PREFIX + safe) but creates
    the worktree FIRST, detached, and only afterwards tries `checkout -q
    -b branch` in it; when that checkout fails, acquire() returns
    branch=None while the worktree itself is left behind, real, on disk,
    detached, and still registered with git. _lane_worktree_path's own
    branch-keyed lookup above finds nothing for a detached worktree (its
    "branch refs/heads/..." line is never printed for one), so without
    this a caller that only has the SANITIZED branch name loop_bridge.py
    computes unconditionally (never acquire()'s real return) could not
    tell "this lane's worktree is genuinely gone" from "this lane's
    worktree exists but was never checked out to its own branch". This
    reads the same `worktree list --porcelain` output _lane_worktree_path
    reads, matching each entry's directory basename against the slug
    worktree_lane.branch_for(unit_id) itself would produce.

    REPAIR (consolidation round): this used to take the already-computed
    `branch` string and slice off LANE_BRANCH_PREFIX to get the slug, which
    only reproduced acquire()'s real directory name because nothing tied
    the two together -- a second, silent copy of the naming rule. This now
    asks worktree_lane.branch_for, the one place that rule lives, directly."""
    expected_branch = worktree_lane.branch_for(unit_id)
    slug = expected_branch[len(LANE_BRANCH_PREFIX):]
    if not slug:
        return None
    proc = _git(["worktree", "list", "--porcelain"], repo, runner)
    if proc.returncode != 0:
        return None
    for line in (proc.stdout or "").splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):].strip()
            if os.path.basename(path) == slug:
                return path
    return None


#: CONT-0, U4: the coding client's own config-dir env vars, read directly
#: here (this module's own convention: RUN_ID_ENV_VAR and HARNESS_ENV_VAR
#: above are already read the same way, never through another module).
#: REPAIR C2 (2026-09-09 adversarial review): the config dir now comes from
#: brother_paths.config_dir(), this estate's own resolver (BROTHER_CONFIG_DIR,
#: then CLAUDE_CONFIG_DIR, then the per-client default), never a bare
#: environment read in this module. Safety no longer depends on the resolver
#: staying dormant: the gates in _retire_transcript_folder below (a
#: registered lane branch, the worktree confirmed gone, real non-symlinked
#: projects/, the target folder resolving directly inside it, an EXACT slug
#: match, and a symlink-free walk before deletion) are what make it safe for
#: this to resolve the REAL config dir in an ordinary run, exactly as every
#: other consumer of brother_paths.config_dir() already does.


def _transcript_slug(path):
    """The exact directory name the `claude` CLI's own session-transcript
    convention derives from an absolute path: every character that is not
    a letter, digit, underscore or hyphen becomes a hyphen, one for one,
    never collapsed (observed real form: .../projects/-Users-example-
    user-Brother/... from /Users/example-user/Brother, where both
    '/' and '.' became a single '-' each)."""
    # realpath, not abspath: tempfile.mkdtemp() (and this platform's own
    # /var -> /private/var symlink) can hand back a path that resolves to
    # a different string than the one the worktree listing reports for
    # the same tree (test_brother_run.py's own CHECK_LOGGER comment names
    # this exact class of mismatch), so both ends of this comparison are
    # normalized through the same resolver rather than trusted as already
    # equal.
    resolved = os.path.realpath(path)
    return "".join(ch if ch.isalnum() or ch in "_-" else "-"
                  for ch in resolved)


def _refuse_transcript_retirement(path, reason):
    print("integrate: not retiring a transcript folder for %s: %s"
          % (path, reason), file=sys.stderr)


def _retire_transcript_folder(path, branch, runner=None, lane_created_at=None):
    """CONT-0, U4, option (a) ONLY: after a lane's worktree is gone, remove
    the session-transcript folder under the config dir's projects/ whose
    slug is derived from THAT EXACT worktree path, and only when that
    worktree path no longer exists on disk. Never any other folder: the
    slug is derived from `path` alone, never a prefix match, so a folder
    for a different path that happens to share a prefix is untouched.

    REPAIR C2 (2026-09-09 adversarial review): ALL of the following must
    hold, checked in this order, each independently refusable and each
    refusal printed as the caller's next action with nothing touched:

      1. `branch` carries LANE_BRANCH_PREFIX. This is this module's own,
         and this codebase's only, notion of a "registered lane path":
         cleanup_lane() already resolves `path` from git's own worktree
         list for exactly this branch, so re-checking the prefix here
         makes the guarantee this function's own, not merely inherited
         from its one caller.
      2. `path` no longer exists on disk (the ordinary, silent case on
         every call before a lane's teardown actually removes it).
      3. brother_paths.config_dir() names a directory, and realpath(that)/
         projects exists, is a real directory, and is not itself a
         symlink.
      4. the target folder -- os.path.join(that projects dir, the EXACT
         transcript slug of `path`) -- is not itself a symlink, and
         resolves, by realpath, DIRECTLY inside that projects directory:
         no symlink hop, no escape.

    Only past all four is anything read further: the removal walks the
    folder first and refuses the whole deletion if ANY entry inside it, at
    any depth, is a symlink -- this function only ever deletes ordinary
    files and directories it can already account for, never something a
    symlink could redirect elsewhere.

    Option (b), relocating transcripts instead of deleting the orphaned
    ones, is refused: a second store is exactly what CONT-0's own ABORT
    clause forbids.

    `runner` is unused; kept for the same call shape as this module's other
    helpers. Best-effort and silent past one stderr note past the gates:
    transcript hygiene must never fail a finished lane teardown, the same
    rule this module's cleanup_lane() already holds for the worktree and
    branch removal it follows.

    REPAIR C7 (2026-09-09 adversarial review of lane/continuity, round 2):
    two more gates, past the symlink walk and before anything is deleted.
    (1) The folder's file count and total byte size are printed ON THEIR
    OWN LINE, before the deletion, not only after it: the second reviewer
    found this function used to print what it destroyed only once it was
    already gone, with no size or entry count either way. (2) `path`'s own
    creation time, captured by the caller BEFORE the worktree that made it
    was removed (`lane_created_at`), must NOT be newer than the folder's
    own newest file: a folder whose newest file is STRICTLY OLDER than the
    lane that supposedly produced it is a PRE-EXISTING folder that merely
    shares this lane's slug, never removed. Same-tick equality is
    tolerated (REPAIR L1, 2026-09-10): a coarse platform clock can stamp
    the lane's creation and the folder's newest write at the identical
    timestamp, and that is not evidence of a pre-existing folder. A
    missing lane_created_at (no creation time was recorded at all)
    refuses the same way, rather than guessing that an unrecorded lane is
    old enough."""
    if not branch or not branch.startswith(LANE_BRANCH_PREFIX):
        return _refuse_transcript_retirement(
            path, "%r is not a registered lane branch" % (branch,))
    if not path or os.path.exists(path):
        return  # the worktree is still there; not this function's business

    # FINDING 4 (2026-09-10 security review): the four gates below prove
    # the TARGET folder is safe to remove, but nothing proved `path` itself
    # was ever a lane worktree_lane.py created -- only that its branch name
    # carries the lane prefix. Any registered git worktree checked out to a
    # `lane/`-prefixed branch would otherwise reach the slug lookup below,
    # and real_projects/_transcript_slug(path) for an attacker- or
    # bug-chosen path could collide with a live project's own transcript
    # folder. worktree_lane.acquire() creates every lane under
    # tempfile.gettempdir() (via tempfile.mkdtemp(prefix="brother-lane-"))
    # unless a caller supplies its own root, which no real caller in this
    # estate does; that is this tool's own lane root, read here from the
    # same stdlib call rather than a hardcoded path. A worktree path
    # outside it is refused, exactly like the other gates in this
    # function.
    lane_root = os.path.realpath(tempfile.gettempdir())
    resolved_path = os.path.realpath(path)
    if (resolved_path == lane_root
            or os.path.commonpath([lane_root, resolved_path]) != lane_root):
        return _refuse_transcript_retirement(
            path, "%s does not sit under %s, the lane root "
            "worktree_lane.acquire() creates lanes under; refusing to "
            "treat it as a registered lane worktree" % (path, lane_root))

    # Tightening: containment under the system temp root alone is not
    # enough -- a worktree registered to a `lane/`-prefixed branch but sitting
    # anywhere else under gettempdir() (a stray temp directory a bug or an
    # attacker made) would still pass the check above. Every real lane's
    # path is <a brother-lane-* directory>/<sanitized-unit-id>, so the path
    # component immediately under lane_root must carry that prefix.
    rel = os.path.relpath(resolved_path, lane_root)
    lane_dir_name = rel.split(os.sep, 1)[0]
    if not lane_dir_name.startswith(LANE_WORKTREE_PARENT_PREFIX):
        return _refuse_transcript_retirement(
            path, "%s does not sit inside a %s* directory under %s, the "
            "prefix worktree_lane.acquire() uses for every lane it "
            "creates; refusing to treat it as a registered lane worktree"
            % (path, LANE_WORKTREE_PARENT_PREFIX, lane_root))

    config_dir = brother_paths.config_dir()
    if not config_dir:
        return _refuse_transcript_retirement(
            path, "%s: brother_paths.config_dir() named no directory"
            % NODATA)
    projects_dir = os.path.join(config_dir, "projects")
    if os.path.islink(projects_dir):
        return _refuse_transcript_retirement(
            path, "%s is a symlink; refusing to treat it as the real "
            "transcript store" % projects_dir)
    real_projects = os.path.realpath(projects_dir)
    if not os.path.isdir(real_projects):
        return  # no projects/ directory at all: nothing to retire

    folder = os.path.join(real_projects, _transcript_slug(path))
    if os.path.islink(folder):
        return _refuse_transcript_retirement(
            path, "%s is a symlink; refusing to delete through it" % folder)
    if not os.path.isdir(folder):
        return
    if os.path.dirname(os.path.realpath(folder)) != real_projects:
        return _refuse_transcript_retirement(
            path, "%s resolves outside %s; refusing" % (folder, real_projects))

    file_count, total_bytes, newest_mtime = 0, 0, None
    for root, dirnames, filenames in os.walk(folder, followlinks=False):
        for name in dirnames + filenames:
            full = os.path.join(root, name)
            if os.path.islink(full):
                return _refuse_transcript_retirement(
                    path, "%s contains a symlink at %s; refusing to delete "
                    "through it" % (folder, full))
        for name in filenames:
            full = os.path.join(root, name)
            try:
                fst = os.stat(full)
            except OSError as exc:
                # Silently dropping this entry would let it hide from
                # file_count, total_bytes and the newest_mtime gate below --
                # exactly the "refuse on any doubt" contract this function
                # otherwise holds (REPAIR C10). If the file that vanished or
                # went unreadable was in fact the newest one, an incomplete
                # newest_mtime could pass the age gate and shutil.rmtree a
                # folder that still holds something recent. Refuse instead
                # of guessing.
                return _refuse_transcript_retirement(
                    path, "%s could not be stat'd (%s) while accounting for "
                    "%s; refusing to retire it without a complete count of "
                    "its files" % (full, exc, folder))
            file_count += 1
            total_bytes += fst.st_size
            if newest_mtime is None or fst.st_mtime > newest_mtime:
                newest_mtime = fst.st_mtime

    if lane_created_at is None:
        return _refuse_transcript_retirement(
            path, "no creation time was recorded for this lane's "
            "worktree, so %s was left alone rather than retired on a "
            "guess" % folder)
    # REPAIR L1: same-tick equality is now TOLERATED. A folder's newest
    # file legitimately lands at the same coarse timestamp as the lane's
    # own creation on a platform whose clock cannot tell the two apart
    # (measured on the Linux CI runner: both 1788996795.3214097), and that
    # is not evidence of a pre-existing folder. Only a newest file
    # STRICTLY OLDER than the lane's recorded creation time is refused.
    if newest_mtime is None or newest_mtime < lane_created_at:
        return _refuse_transcript_retirement(
            path, "%s's newest file (mtime %r) is older than this "
            "lane's own worktree creation time (%r), so it looks like a "
            "pre-existing folder that merely shares this lane's slug, and "
            "was left alone rather than retired on a guess"
            % (folder, newest_mtime, lane_created_at))

    # REPAIR C9 (2026-09-09 adversarial review, round 3): this announcement
    # used to print ABOVE the two gates just above, so a refused retirement
    # (no creation time, or a folder older than the lane) still printed
    # "retiring transcript folder ..." before the run log's own refusal
    # line said the opposite -- a run log that announced a removal that
    # never happened. It now prints only once both gates have already
    # passed, immediately before the deletion it describes.
    print("integrate: retiring transcript folder %s (%d file(s), %d "
         "byte(s))" % (folder, file_count, total_bytes), file=sys.stderr)

    try:
        shutil.rmtree(folder)
        print("integrate: retired transcript folder %s (worktree %s is gone)"
              % (folder, path), file=sys.stderr)
    except OSError as exc:
        print("integrate: could not retire transcript folder %s: %s"
              % (folder, exc), file=sys.stderr)


def _lane_already_torn_down_per_journal(branch, unit_id):
    """True only when THIS run's own journal proves BOTH that `unit_id`
    once acquired `branch` (a "lane.acquired" event, written by
    worktree_lane.acquire) AND that a later cleanup of that same branch
    actually completed (a "lane.cleaned" event with removed=True) -- the
    two marks a genuine prior teardown leaves behind. False on any doubt
    (no run directory, no journal, either mark missing): a branch that
    merely carries LANE_BRANCH_PREFIX and happens to have no worktree or
    ref left is never assumed to be a finished teardown on that shape
    alone (REPAIR C10, 2026-09-09 adversarial review round 3, driven with
    a bare lane/<x> branch name that worktree_lane.acquire never created:
    cleanup_lane used to call this "already fully removed by an earlier
    attempt" and journal removed=True for a lane that never existed)."""
    run_dir = journal.run_dir_from_env()
    if not run_dir:
        return False
    events = journal.read(run_dir) or []
    mine = [e for e in events if e.get("unit_id") == str(unit_id)
           and (e.get("payload") or {}).get("branch") == branch]
    acquired = any(e.get("type") == "lane.acquired" for e in mine)
    torn_down = any(e.get("type") == "lane.cleaned"
                    and (e.get("payload") or {}).get("removed") for e in mine)
    return acquired and torn_down


def cleanup_lane(repo, branch, unit_id, runner=None):
    """Retire one unit's lane once this round has decided its fate.

    Called from integrate(), below, right after it records a unit's verdict,
    whatever that verdict was: see the module docstring's CLEANUP section for
    why this is safe for every verdict, not only INTEGRATED. A removal
    failure is reported and printed here and never allowed to change the
    verdict integrate() already recorded, per this estate's rule that
    cleanup must never fail a finished proof.

    Removes only a worktree git itself registered for `branch`, and `branch`
    itself, and only when `branch` carries the lane prefix: this never
    reaches main or a branch a human made. The worktree PATH is never taken
    on trust either: it is always RESOLVED from the verified lane branch via
    _lane_worktree_path (git's own `worktree list`), never accepted as an
    argument, so there is no route by which a non-lane worktree could reach
    the removal below.

    CONT-0, U3: this runs OUTSIDE integrate_one's own lock (integrate()
    calls this only after integrate_one has already released it), so
    canonical's HEAD can move between any earlier check and the actual
    deletion. The branch's containment in canonical is therefore verified
    AT DELETION TIME, immediately before the branch is actually deleted,
    never only from an earlier check that could be stale by then.

    CONT-0, U3, the reviewer's case: a kill between the two deletion steps
    (worktree removed, branch not yet deleted, or the reverse) must read as
    ONE next action on a retry, never as "no worktree, cannot prove
    cleanliness" (which used to be this function's only reading of a
    half-torn-down lane) and never as a second attempt at whichever half
    already succeeded.

    Returns (removed, detail) and prints one line so the run's own output
    (which is what this estate calls the run log) carries it."""
    def retain(detail):
        print("  lane-cleanup %-10s %-7s %s" % (unit_id, "kept", detail))
        run_dir = journal.run_dir_from_env()
        journal.append(run_dir, "lane.cleaned",
                       parent_ids=journal.previous(run_dir), unit_id=unit_id,
                       payload={"branch": branch, "removed": False})
        return False, detail

    def done(detail, resumed):
        print("  lane-cleanup %-10s %-7s %s" % (unit_id, "removed", detail))
        run_dir = journal.run_dir_from_env()
        journal.append(run_dir, "lane.cleaned",
                       parent_ids=journal.previous(run_dir), unit_id=unit_id,
                       payload={"branch": branch, "removed": True,
                                "resumed_teardown": resumed})
        return True, detail

    if not branch or not branch.startswith(LANE_BRANCH_PREFIX):
        return False, ("%s: %r is not a lane branch, so nothing was touched"
                       % (NODATA, branch))

    path = _lane_worktree_path(repo, branch, runner)
    branch_exists = _git(["rev-parse", "--verify", "--quiet",
                         "refs/heads/" + branch], repo, runner).returncode == 0

    if not path and not branch_exists:
        # REPAIR C6 (2026-09-09 adversarial review, round 2): before
        # believing "nothing is left", check by DIRECTORY NAME too. A
        # worktree that never got its own branch (worktree_lane.acquire's
        # `checkout -b` failed, branch=None) is invisible to the
        # branch-keyed lookup above and the branch genuinely never
        # existed, so the two Nones used to read as "already fully
        # removed" while the directory, its git registration and any
        # uncommitted worker file inside it all remained, undiscovered.
        stray = _lane_worktree_path_by_slug(repo, unit_id, runner)
        if stray:
            return retain(
                "retained lane %s: a worktree is still registered at %s "
                "(it was never checked out to its own branch, so the "
                "branch-keyed lookup could not see it; found by directory "
                "name instead) and was left in place rather than reported "
                "removed on a guess. Next action: reconcile or "
                "force-remove %s by hand" % (branch, stray, stray))
        # THE REVIEWER'S CASE, already finished: an earlier attempt (killed
        # right after its last step, or simply run twice) already removed
        # both halves. Read as the SAME terminal fact every time, never as
        # a fresh deletion: a retry must never claim credit twice for one
        # teardown -- but ONLY when this run's own journal actually proves
        # a teardown happened at all (REPAIR C10, round 3): a branch that
        # was never acquired in the first place has no acquire mark and no
        # completed-teardown mark either, and reporting it "removed" would
        # be exactly the same guess this function refuses everywhere else.
        if not _lane_already_torn_down_per_journal(branch, unit_id):
            detail = ("%s: no record of this lane; nothing was removed"
                      % NODATA)
            print("  lane-cleanup %-10s %-7s %s" % (unit_id, "no-data", detail))
            run_dir = journal.run_dir_from_env()
            journal.append(run_dir, "lane.cleaned",
                           parent_ids=journal.previous(run_dir), unit_id=unit_id,
                           payload={"branch": branch, "removed": False})
            return False, detail
        return done("lane %s already fully removed by an earlier attempt"
                   % branch, resumed=True)

    if not path and branch_exists:
        # THE REVIEWER'S CASE, mid-teardown: an earlier attempt was killed
        # after the worktree was removed but before the branch was
        # deleted. There is no working tree left to check for uncommitted
        # entries -- that half is already gone -- so only the branch's own
        # safety needs proving, AT DELETION TIME, before finishing the one
        # step that did not complete. No second `worktree remove` is
        # attempted: there is nothing left to remove.
        contained = _git(["merge-base", "--is-ancestor", branch, "HEAD"],
                         repo, runner)
        if contained.returncode != 0:
            return retain("retained lane %s: its worktree is already gone "
                          "but its branch is not (yet) contained in "
                          "canonical, so the branch was preserved rather "
                          "than finishing the deletion on a guess" % branch)
        branch_del = _git(["branch", "-d", branch], repo, runner)
        if branch_del.returncode != 0:
            return retain("retained lane %s: its worktree is already gone "
                          "but deleting its now-orphaned branch failed: %s"
                          % (branch, (branch_del.stderr or
                                     branch_del.stdout or "").strip()[:160]))
        return done("lane %s removed (finishing an interrupted teardown: "
                   "its worktree was already gone)" % branch, resumed=True)

    # THE ORDINARY CASE: both halves still present.
    problems = []
    dirty = _git(["-C", path, "status", "--porcelain"], repo, runner)
    if dirty.returncode != 0:
        return retain("retained lane %s at %s: its cleanliness could not be "
                      "read, so removal was refused" % (branch, path))
    entries = [line for line in (dirty.stdout or "").splitlines() if line.strip()]
    if entries:
        return retain("retained lane %s at %s: %d uncommitted entry(ies) need "
                      "reconciliation before removal" % (branch, path, len(entries)))
    contained = _git(["merge-base", "--is-ancestor", branch, "HEAD"], repo,
                     runner)
    if contained.returncode != 0:
        return retain("retained lane %s at %s: its branch is not contained in "
                      "canonical, so its committed work was preserved" %
                      (branch, path))
    # REPAIR C7 (2026-09-09 adversarial review of lane/continuity, round
    # 2): read BEFORE the worktree is removed, because both the worktree
    # itself and the breadcrumb worktree_lane.py wrote for it at acquire
    # time (LANE_SIDECAR_NAME, under git's own per-worktree admin
    # directory) are gone the moment `worktree remove` succeeds --
    # `_retire_transcript_folder` runs strictly after that, so this is the
    # only point left where the lane's own creation time can still be
    # read at all. st_birthtime (true creation time, stable across later
    # writes into the lane) is preferred; st_ctime is the portable
    # fallback where birthtime is not available.
    # REPAIR L1 (2026-09-10, Linux CI runner): this used to fall back to
    # st_ctime (the worktree directory's own last metadata change) when
    # st_birthtime was unavailable. Linux has no st_birthtime and its
    # kernel clock is coarse enough that this worktree's ctime and the
    # transcript folder's newest file mtime came out EQUAL on the CI
    # runner, so the age gate below refused forever. worktree_lane.
    # lane_created_at() gives every platform the same stable answer: the
    # breadcrumb sidecar's own created_at, written once at acquire() time,
    # ahead of any stat-based fallback. Read here, not reimplemented,
    # because this is the one place left that can still read it at all --
    # both the sidecar and the worktree's admin directory are gone the
    # moment `worktree remove` below succeeds.
    lane_created_at = worktree_lane.lane_created_at(path, runner)
    proc = _git(["worktree", "remove", path], repo, runner)
    if proc.returncode != 0:
        problems.append("worktree remove failed for %s: %s" % (
            path, (proc.stderr or proc.stdout or "").strip()[:160]))
    _git(["worktree", "prune"], repo, runner)
    if proc.returncode == 0:
        # CONT-0, U4: the worktree is confirmed gone HERE, with `path`
        # still in hand -- the one place this function still knows the
        # exact path a stale transcript folder would be keyed on. The
        # "already gone" recovery branches above cannot do this: by the
        # time they run, git has already forgotten the path, and this
        # function never guesses one.
        _retire_transcript_folder(path, branch, runner,
                                  lane_created_at=lane_created_at)
    exists = _git(["rev-parse", "--verify", "--quiet", "refs/heads/" + branch],
                 repo, runner)
    if exists.returncode == 0:
        # AT DELETION TIME: re-verified immediately before the mutation,
        # never trusted from the check above, which can be stale by now
        # (this function holds no lock of its own).
        contained_now = _git(["merge-base", "--is-ancestor", branch, "HEAD"],
                             repo, runner)
        if contained_now.returncode != 0:
            problems.append("branch %s is no longer contained in canonical "
                            "at deletion time (canonical moved since the "
                            "earlier check); its worktree is gone but the "
                            "branch was preserved rather than deleted on a "
                            "stale check" % branch)
        else:
            branch_del = _git(["branch", "-d", branch], repo, runner)
            if branch_del.returncode != 0:
                problems.append("branch -d failed for %s: %s" % (
                    branch, (branch_del.stderr or branch_del.stdout
                            or "").strip()[:160]))
    removed = not problems
    detail = ("lane %s removed" % branch if removed else
              "lane %s at %s NOT fully removed: %s"
              % (branch, path or "(no worktree)", "; ".join(problems)))
    print("  lane-cleanup %-10s %-7s %s"
          % (unit_id, "removed" if removed else "kept", detail))
    run_dir = journal.run_dir_from_env()
    journal.append(run_dir, "lane.cleaned",
                   parent_ids=journal.previous(run_dir), unit_id=unit_id,
                   payload={"branch": branch, "removed": removed})
    return removed, detail


def integrate(repo, results, lanes, units, runner=None, check_runner=None,
              run_id=None, harness_revision=None):
    """Every integrable result, one at a time, each against the revision the
    previous one produced. Order is the batch's own.

    `results` are dispatch records (carrying `integrable` from the scope gate),
    `lanes` maps unit id to its lane branch, `units` maps unit id to the unit.
    A result the scope gate did not clear is reported, never integrated.

    A result whose OWN lane check never reached PASS is also reported, never
    merged (E7, measured 2026-08-31): the batch this loop runs against is
    already dependency-satisfied and write-set disjoint (graph_loop's own
    admission rule), so a sibling unit's lane cannot supply anything this
    unit's lane was missing. A check that just failed on this exact code has
    no reason to be re-run unchanged after a merge; attempting it anyway
    still spends a real git merge, the unit's done_check, and an unwind, for
    a result that was never going to differ. This costs nothing a PASS-ing
    result still needs: the check-on-canonical gate below is unchanged for
    every unit that actually has a chance of standing."""
    out = []
    for rec in results:
        uid = str(rec.get("id"))
        # Read once, used both to decide the outcome below and, at the end
        # of this iteration, to retire this unit's lane whichever way it
        # went: see cleanup_lane() and the module docstring's CLEANUP note.
        branch = lanes.get(uid)
        if not rec.get("integrable", False):
            out.append({"verdict": REFUSED, "unit": uid,
                        "canonical": _tip(repo, runner),
                        "reason": rec.get("integration_block")
                                  or "the scope gate did not clear this unit"})
            if branch:
                cleanup_lane(repo, branch, uid, runner)
            continue
        if "verdict" in rec and rec.get("verdict") != "PASS":
            out.append({"verdict": REFUSED, "unit": uid,
                        "canonical": _tip(repo, runner),
                        "reason": "the unit's own check did not pass in its "
                                  "lane (verdict=%s); nothing was merged, "
                                  "because the same check on the same code "
                                  "would fail again on canonical"
                                  % rec.get("verdict")})
            if branch:
                cleanup_lane(repo, branch, uid, runner)
            continue
        if not branch:
            out.append({"verdict": NODATA, "unit": uid,
                        "canonical": _tip(repo, runner),
                        "reason": "no lane branch is recorded for this unit, so "
                                  "there is nothing to integrate from"})
            continue
        out.append(integrate_one(repo, branch, units.get(uid, {"id": uid}),
                                 runner, check_runner, run_id,
                                 harness_revision))
        cleanup_lane(repo, branch, uid, runner)
    # E59: one event per verdict this round recorded that is NOT a merge, in
    # one place rather than at each of the four points above, because every
    # one of them ends in this same list and a reader wants the verdict word
    # and the unit, not the branch of the code that produced it. The merge
    # itself is journalled by integrate_one, where the two tips are known.
    # The reason is cut short here: it is already whole in the verdict this
    # returns, in the delivery report and in the unit's own receipt.
    run_dir = journal.run_dir_from_env()
    for row in out:
        if row.get("verdict") == INTEGRATED:
            continue
        # ALREADY_INTEGRATED IS NOT A REFUSAL: the recovery resolver found
        # this lane's content already in canonical, which is a fact about a
        # resumed run, not a verdict against the unit. It gets its own type
        # rather than being filed under a word that would misread.
        journal.append(run_dir,
                       "integrate.already_integrated"
                       if row.get("verdict") == ALREADY_INTEGRATED
                       else "integrate.refused",
                       parent_ids=journal.previous(run_dir),
                       unit_id=row.get("unit"),
                       payload={"verdict": row.get("verdict"),
                                "canonical": (row.get("canonical") or "")[:12],
                                "reason": str(row.get("reason") or "")[:100]})
    return out
