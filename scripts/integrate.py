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
import subprocess
import sys
import time

NODATA = "NO-DATA"
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
LOCK_NAME = ".integration.lock"
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


class _Lock(object):
    """O_EXCL across processes, same reasoning as the claim store's."""

    def __init__(self, repo, timeout=LOCK_TIMEOUT):
        self.path = os.path.join(_git_admin_dir(repo), LOCK_NAME)
        self.timeout, self.fd = timeout, None

    def __enter__(self):
        deadline = time.time() + self.timeout
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                if time.time() >= deadline:
                    raise TimeoutError(
                        "the integration lane at %s has been held for over "
                        "%.0fs. Truth is serial: waiting is correct and "
                        "proceeding is not" % (self.path, self.timeout))
                time.sleep(0.05)

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
            try:
                os.unlink(self.path)
            except OSError as e:
                # Never raise out of __exit__, which would replace whatever
                # exception the with-block was already raising. But truth is
                # serial here, and a lock left held blocks every lane behind
                # it, so a failed release is named on stderr rather than only
                # surfacing minutes later as an unexplained TimeoutError.
                print("integrate: could not release lock %s: %s"
                      % (self.path, e), file=sys.stderr)
        return False


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

        if passed:
            return {"verdict": INTEGRATED, "unit": unit_id, "canonical": after,
                    "reason": "applied to %s and its own check passed ON "
                              "canonical at %s" % (before[:9], (after or "")[:9]),
                    "check_detail": detail, "evidence": evidence}

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
    reaches main or a branch a human made.

    Returns (removed, detail) and prints one line so the run's own output
    (which is what this estate calls the run log) carries it."""
    if not branch or not branch.startswith(LANE_BRANCH_PREFIX):
        return False, ("%s: %r is not a lane branch, so nothing was touched"
                       % (NODATA, branch))
    path = _lane_worktree_path(repo, branch, runner)
    problems = []
    if path:
        proc = _git(["worktree", "remove", "--force", path], repo, runner)
        if proc.returncode != 0:
            problems.append("worktree remove failed for %s: %s" % (
                path, (proc.stderr or proc.stdout or "").strip()[:160]))
        _git(["worktree", "prune"], repo, runner)
    exists = _git(["rev-parse", "--verify", "--quiet", "refs/heads/" + branch],
                 repo, runner)
    if exists.returncode == 0:
        branch_del = _git(["branch", "-D", branch], repo, runner)
        if branch_del.returncode != 0:
            problems.append("branch -D failed for %s: %s" % (
                branch, (branch_del.stderr or branch_del.stdout or "").strip()[:160]))
    removed = not problems
    detail = ("lane %s removed" % branch if removed else
              "lane %s at %s NOT fully removed: %s"
              % (branch, path or "(no worktree)", "; ".join(problems)))
    print("  lane-cleanup %-10s %-7s %s"
          % (unit_id, "removed" if removed else "kept", detail))
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
    return out
