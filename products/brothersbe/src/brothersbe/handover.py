"""LT-301: explicit human handover. `12-handover.json` makes ownership
transfer complete only after a human receiver explicitly accepts it.

WHY THIS EXISTS. A completion message in chat is not a record: nobody can
`sbe status` it, nobody can prove later who accepted what, and "I told them"
is not evidence. This module writes ONE artifact, `12-handover.json`, inside
the dossier being handed off, bound to the commit it was prepared at, and it
stays the outgoing owner's until a named human receiver runs `acknowledge`.
Rejecting keeps ownership with the outgoing owner too, with the reason on
record. Nothing here changes who owns a task in `.sbe/tasks.json`: this
module only ever READS the task registry, never writes it, so "no task owner
is changed until acknowledgment succeeds" is true by construction rather than
by a check that could be bypassed.

WHAT `prepare` DERIVES, and does not ask for. Everything the engine already
knows comes from readers this project already ships, reused rather than
retyped:
  - the task registry (`brothersbe.tasks.load_registry`) for who holds which
    plan task open, closed clean, or never opened;
  - the dossier's own `08-plan.json` for the task graph itself;
  - the evidence store (`brothersbe.status._scan_evidence`, the same reader
    `sbe status --team` uses) for which receipts are current, stale or
    unavailable;
  - `09-convergence.json`, `10-approval.json` and `11-review.json`, read and
    judged for staleness the same way `brothersbe.status.build_team_report`
    already judges them: bound to a commit, and a bound commit that is not
    the current HEAD is stale, never silently re-read as current;
  - `working_tree_dirty` (`brothersbe.evidence`) and the task registry's own
    declared worktrees, for which trees carry uncommitted state.
Only two things cannot be read off any of that: who the outgoing owner and
the intended receiver ARE (`--outgoing`/`--receiver`), and a rejection's
reason (`--reject --reason`). Those, and only those, are asked for.

IDENTITY COMPARISON is not reinvented here. `tools/sbe_gate.py` already closed
seven forgeries of "is this the same person" for the self-approval guard on
the money gate (a trailing period, a reordered name, an initial, a
plus-address, a gmail dot-fold, a homoglyph, an invisible character): see
`sbe_gate._identity_parts`, `_canonical_email` and `_names_overlap`. This
module imports that machinery rather than writing a second, weaker fold, for
"receiver equals outgoing owner" and "receiver equals a registered agent
identity" alike.

THE CR-09 SHAPE THIS MIRRORS: a handover record NEVER JUDGES ITSELF. `status`
stays exactly what `prepare` wrote ("prepared") until `acknowledge` or
`reject` changes it; staleness (bound commit versus current HEAD) is worked
out fresh at every read, in `show` and again, as a hard refusal, inside
`acknowledge`. A prepared record is a frozen snapshot: if a task closes or
opens after `prepare` ran with no new commit, the record still shows what was
true when it was written, exactly the way an old `11-review.json` is never
silently rewritten to match a tree that moved on without a new review.

CONCURRENCY: two `acknowledge` (or `reject`) calls racing the same dossier are
serialized by an `fcntl.flock` sidecar (`12-handover.json.lock`), the same
pattern `brothersbe.tasks._registry_lock` already uses for the task registry,
held across the whole read-check-write. The first writer's atomic rename
(temp file, then `os.replace`) lands; the second reads the now-updated
`status` and is refused, never a lost update and never a torn file.

Python floor is 3.9: no match statements, no `X | Y` annotations. Standard
library only.
"""
import argparse
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import time

try:
    import fcntl
except ImportError:          # every non-POSIX platform (Windows: msvcrt below)
    fcntl = None
try:
    import msvcrt
except ImportError:          # every POSIX platform (fcntl above covers it)
    msvcrt = None            # with neither, refuse to write unlocked rather
                             # than reopen the race the lock exists to close

from . import SCHEMA_VERSION
from . import cwd as cwd_mod
from . import lifecycle
from . import tasks as tasks_mod
from . import status as status_mod
from . import evidence as evidence_mod
from .impact import _git  # noqa: E402  (the same private git runner status.py reuses)
from . import content_binding  # noqa: E402

# `tools/` is not a package; this mounts it onto sys.path through the one
# shared mechanism every other converged module uses, the same way
# `brothersbe.tasks` reaches `sbe_fence_hook.paths_overlap`. Mirrored here so
# the identity comparison this module runs is the SAME code the approval
# gate's self-approval guard runs, not a second copy of its folding rules.
from ._toolspath import mount
mount()
import sbe_gate  # noqa: E402

HANDOVER_REL = "12-handover.json"
_LOCK_REL = "12-handover.json.lock"

#: Every field the schema carries, verbatim, and nothing else: a record
#: written by this module carries exactly these keys, in this order when
#: serialized (json.dumps with sort_keys re-sorts them alphabetically on
#: disk, which is fine; this tuple is the CONTRACT, not the byte order).
SCHEMA_FIELDS = ("schemaVersion", "headSha", "change", "preparedBy", "preparedAt",
                 "outgoingOwner", "intendedReceiver", "status", "done", "inFlight",
                 "notStarted", "openQuestions", "decisions", "evidence", "activeTasks",
                 "worktrees", "requiredAccess", "nextAction", "acknowledgment")

STATUSES = ("prepared", "acknowledged", "rejected")

#: How long `_handover_lock` waits for the exclusive lock before giving up.
#: Bounded, so a stuck holder cannot wedge every later writer forever; see
#: `tasks._registry_lock`, whose timeout this mirrors.
_LOCK_TIMEOUT_S = 30.0


class HandoverUnusable(Exception):
    """The handover file or its lock cannot support a decision right now: no
    fcntl on this platform, the lock sidecar could not be opened, or another
    writer held it past the timeout. Never silently proceeds unlocked."""


def _iso(epoch):
    """ISO 8601 in UTC, to the second, with an explicit Z: the same spelling
    the task registry and the evidence receipts already use."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _real(path):
    """The symlink-resolved absolute form of `path`.

    `tasks.repo_root_of` resolves the repository root through `git
    rev-parse --show-toplevel`, which git itself returns fully resolved.
    On macOS, `tempfile.mkdtemp()` (and every fixture in this suite) hands
    back a path under `/var/folders/...`, a symlink to
    `/private/var/folders/...`; comparing that unresolved form against the
    resolved root with `os.path.relpath` walked back out through eight `..`
    segments instead of landing on the dossier's own relative path, and the
    same mismatch would make two spellings of one worktree register as two
    entries. Every path this module derives or compares is realpath'd once,
    here, so root and dossier are always the same spelling of the same
    directory.
    """
    return os.path.realpath(os.path.abspath(path))


def handover_path(dossier):
    return os.path.join(_real(dossier), HANDOVER_REL)


def read_handover(dossier):
    """("missing", None) | ("malformed", <why, as text>) | ("ok", <dict>).

    Mirrors `status._read_review_record`'s three-state law exactly: absence
    is NO-DATA (nobody has prepared a handover, which is a fact, not an
    accusation, and never a block on anything per the NO-DATA law), and a
    file present but unparseable, not an object, or missing one of its own
    required fields is a broken claim, worse than silence, and is never
    guessed through.
    """
    path = handover_path(dossier)
    if not os.path.exists(path):
        return "missing", None
    try:
        data = json.loads(io.open(path, encoding="utf-8").read())
    except (ValueError, OSError) as exc:
        return "malformed", "does not parse: %s" % exc
    if not isinstance(data, dict):
        return "malformed", "the top-level JSON value is not an object"
    missing = [k for k in SCHEMA_FIELDS if k not in data]
    if missing:
        return "malformed", "missing required field(s): %s" % ", ".join(missing)
    if data.get("status") not in STATUSES:
        return "malformed", ("status %r is not one of %s"
                             % (data.get("status"), ", ".join(STATUSES)))
    return "ok", data


def _atomic_write_json(path, data):
    """Temp file in the same directory, then `os.replace`: the file is never
    half-written. Mirrors `tasks.save_registry` and `cli._record_review`
    exactly, so this store's write path is not a third invention of the same
    one line."""
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix="12-handover.", suffix=".tmp", dir=directory)
    try:
        with io.open(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def _handover_lock(dossier):
    """Exclusive advisory lock serializing every read-check-write of ONE
    dossier's `12-handover.json`, held from the read through the rewrite, so
    two concurrent `acknowledge` (or `reject`, or `prepare`) calls racing the
    same file can no longer both read `status: prepared` and both win.

    Mirrors `tasks._registry_lock`: an `fcntl.flock` on a sidecar file beside
    the thing being protected, spun for up to `_LOCK_TIMEOUT_S` before giving
    up. A timeout or a missing `fcntl` raises `HandoverUnusable` rather than
    proceeding unlocked, for the same reason `tasks._registry_lock` does:
    proceeding unlocked would reopen exactly the race this exists to close.
    """
    if fcntl is None and msvcrt is None:
        raise HandoverUnusable(
            "this platform has neither fcntl nor msvcrt, so the handover file cannot be "
            "locked for a safe read-check-write; nothing was recorded rather than writing "
            "unprotected against a concurrent acknowledge or reject")
    directory = os.path.abspath(dossier)
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        raise HandoverUnusable("%s cannot be created (%s); nothing was recorded" % (directory, exc))
    lock_path = os.path.join(directory, _LOCK_REL)
    try:
        fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o644)
    except OSError as exc:
        raise HandoverUnusable(
            "the lock sidecar %s could not be opened (%s); nothing was recorded rather than "
            "writing unprotected against a concurrent acknowledge or reject" % (lock_path, exc))
    try:
        held = False
        deadline = time.time() + _LOCK_TIMEOUT_S
        while True:
            try:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    # Same byte-range shape as decisions.py and tasks.py (pad
                    # to one byte, anchor at offset 0, lock [0, 1)): msvcrt
                    # regions anchor at the CURRENT position, so every opener
                    # must target the same range or two writers never contend.
                    if os.fstat(fd).st_size < 1:
                        os.write(fd, b"0")
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                held = True
                break
            except OSError:
                if time.time() >= deadline:
                    break
                time.sleep(0.02)
        if not held:
            raise HandoverUnusable(
                "%s stayed locked by another writer past %.0fs; nothing was recorded rather "
                "than writing unprotected against it" % (handover_path(dossier), _LOCK_TIMEOUT_S))
        yield
    finally:
        # No explicit LOCK_UN: POSIX releases every lock held through a
        # descriptor the instant that descriptor is closed, and this fd is
        # never reused past this point. Mirrors tasks._registry_lock exactly.
        os.close(fd)


# ---------------------------------------------------------------------------
# Identity comparison: reused from tools/sbe_gate.py, not reinvented.
# ---------------------------------------------------------------------------

def _identity_matches(a, b):
    """True when `a` and `b` read as the same person: the SAME comparison
    `sbe_gate.py`'s self-approval guard runs on the money gate (skeleton-
    reduced email canonicalization, including the gmail dot-fold, plus
    Unicode-aware name-word overlap), applied here to a `--outgoing`/
    `--receiver` pair instead of an Approved-by trailer against a commit's
    author. Two prongs, mirroring `sbe_gate.py`'s own `_same`: an email match
    in canonical form, or a name-word overlap (which also catches the
    "welded by an invisible character" case through the spaceless join).
    """
    a_emails, a_names = sbe_gate._identity_parts(a or "")
    b_emails, b_names = sbe_gate._identity_parts(b or "")
    if a_emails & b_emails:
        return True
    if any(sbe_gate._names_overlap(frozenset(x), frozenset(y))
           for x in a_names for y in b_names):
        return True
    joined_a = {"".join(t) for t in a_names}
    joined_b = {"".join(t) for t in b_names}
    return bool(joined_a & joined_b)


def _agent_identities(root):
    """Every `agent` string the task registry has ever recorded, open or
    closed: an agent identity may never act as the human receiver, and that
    is true of an agent whose task has since closed, not only an open one.
    A registry this module cannot read yields no agents to compare against,
    never a crash: NO-DATA here is silence, not a block."""
    try:
        data = tasks_mod.load_registry(root)
    except tasks_mod.RegistryUnusable:
        return []
    return sorted({t.get("agent") for t in data.get("tasks", []) if t.get("agent")})


# ---------------------------------------------------------------------------
# Derivation: done / inFlight / notStarted / activeTasks, from the plan and
# the task registry, exactly as `status.build_team_report` reads them.
# ---------------------------------------------------------------------------

def _classify_tasks(root, plan):
    """(done, inFlight, notStarted, activeTasks, registryProblem).

    One plan task lands in exactly one of the three buckets, decided by its
    CURRENT registry state: an open record makes it inFlight regardless of
    an earlier closed-clean record for the same id (the live state wins); no
    open record but a closed-and-not-forced record makes it done, mirroring
    `status._closed_clean`'s own "closed FORCED is a disposition, never a
    completion" rule; anything else is notStarted. `activeTasks` is the
    ownership detail for every inFlight task: id, agent, role, worktree and
    baseCommit, so a receiver can see WHO to talk to, not just WHAT is open.
    """
    plan_tasks = plan.get("tasks", []) if isinstance(plan, dict) else []
    registry_problem = None
    registry_tasks = []
    try:
        registry_tasks = tasks_mod.load_registry(root).get("tasks", [])
    except tasks_mod.RegistryUnusable as exc:
        registry_problem = str(exc)

    done, in_flight, not_started, active = [], [], [], []
    for task in plan_tasks:
        if not isinstance(task, dict):
            continue
        tid = task.get("id")
        title = task.get("title") or tid
        records = [r for r in registry_tasks if isinstance(r, dict) and r.get("id") == tid]
        open_rec = next((r for r in records if r.get("status") == "open"), None)
        closed_clean = any(r.get("status") == "closed" and not r.get("forced") for r in records)
        if open_rec is not None:
            in_flight.append({"id": tid, "title": title, "agent": open_rec.get("agent")})
            active.append({"id": tid, "agent": open_rec.get("agent"),
                           "role": open_rec.get("role"), "worktree": open_rec.get("worktree"),
                           "baseCommit": open_rec.get("baseCommit")})
        elif closed_clean:
            done.append({"id": tid, "title": title})
        else:
            not_started.append({"id": tid, "title": title,
                                "dependsOn": list(task.get("dependsOn") or [])})
    return done, in_flight, not_started, active, registry_problem


# ---------------------------------------------------------------------------
# Worktrees: the root tree plus every distinct worktree an active task
# declares, each checked for dirtiness through `evidence.working_tree_dirty`
# (the same reader `sbe evidence` binds a receipt's own `workingTreeDirty`
# field from) and named through `tasks._porcelain_paths` (the same porcelain
# read `sbe task close`'s postcondition and `sbe work remove`'s dirty-
# worktree refusal already use), never a third copy of `git status
# --porcelain`.
# ---------------------------------------------------------------------------

def _worktree_entry(path, exempt_rel=frozenset()):
    """One worktree's dirty state, `exempt_rel` (repo-root-relative POSIX
    paths) filtered out of both the file list and the dirty verdict itself:
    `12-handover.json` and its lock sidecar are always dirty exactly because
    THIS run is about to write or just wrote them, and reporting a handover
    as blocked by its own creation would be the same self-reference
    `tasks.py` already exempts `.sbe/tasks.json`/`.sbe/tasks.json.lock` from,
    by exact name, for exactly the same reason (see that module's docstring).
    If filtering leaves no changed path, the tree reads as clean, not merely
    unlisted.
    """
    dirty, detail = evidence_mod.working_tree_dirty(path)
    files = None
    if dirty:
        try:
            files = sorted(f for f in tasks_mod._porcelain_paths(path) if f not in exempt_rel)
        except tasks_mod.DiffUnavailable:
            files = None
        if files is not None:
            # `_porcelain_paths` (`-uall`, every untracked file named) is more
            # precise than `working_tree_dirty`'s own count (plain `git status
            # --porcelain`, which can collapse a wholly-untracked directory
            # into one line); once it succeeds, the detail text is rebuilt
            # from the SAME list `files` carries, so the two never disagree.
            detail = "%d uncommitted path(s)" % len(files)
            if not files:
                dirty = False
                detail = "clean, aside from the handover file itself"
    return {"path": path, "dirty": dirty, "detail": detail, "files": files or []}


def _worktrees(root, active_tasks, dossier):
    root_abs = _real(root)
    dossier_rel = os.path.relpath(_real(dossier), root_abs).replace(os.sep, "/")
    exempt = frozenset((dossier_rel + "/" + HANDOVER_REL, dossier_rel + "/" + _LOCK_REL))
    entries = [_worktree_entry(root_abs, exempt)]
    seen = {root_abs}
    for t in active_tasks:
        wt = t.get("worktree")
        if not wt:
            continue
        wt_abs = _real(wt)
        if wt_abs in seen:
            continue
        seen.add(wt_abs)
        if os.path.isdir(wt_abs):
            entries.append(_worktree_entry(wt_abs, exempt))
        else:
            entries.append({"path": wt, "dirty": None,
                            "detail": "declared worktree does not exist", "files": []})
    return entries


# ---------------------------------------------------------------------------
# Evidence: receipts (via status._scan_evidence, the same reader
# `sbe status --team` uses), plus convergence, approval and review, each
# judged for staleness the same commit-bound way `status.build_team_report`
# already judges them. Everything the spec calls "evidence" this module can
# derive lands in the ONE `evidence` array the schema carries, tagged by
# `kind` so a reader can tell a stale receipt from a stale approval.
# ---------------------------------------------------------------------------

_STALE_PHRASES = ("is no longer checked out", "the code changed after the evidence was made",
                  "the evidence did not see")

_RECEIPT_PATH_RE = re.compile(r"^receipt (\S+) fails verify")


def _receipt_status(finding_text):
    return "stale" if any(p in finding_text for p in _STALE_PHRASES) else "unavailable"


def _receipt_entries(root):
    """(entries, note). One `receipt-store` summary entry always, naming
    presence or absence, then one entry per receipt: current (clean, PASS,
    commit-bound and unmoved), stale (bound to a commit that moved, or a
    covered file that changed since), or unavailable (every other broken or
    failing reason `evidence.verify` names)."""
    evidence_dir = tasks_mod.evidence_dir(root)
    scan = status_mod._scan_evidence(root, evidence_dir)
    entries = [{"kind": "receipt-store", "path": None,
               "status": "current" if scan["inspected"] else "absent",
               "detail": scan["note"]}]
    for item in scan["clean"]:
        entries.append({"kind": "receipt", "path": item.get("path"), "status": "current",
                        "detail": item["finding"]})
    for item in scan["failing"]:
        entries.append({"kind": "receipt", "path": item.get("path"), "status": "unavailable",
                        "detail": item["finding"]})
    for item in scan["broken"]:
        m = _RECEIPT_PATH_RE.match(item["finding"])
        entries.append({"kind": "receipt", "path": m.group(1) if m else None,
                        "status": _receipt_status(item["finding"]), "detail": item["finding"]})
    return entries, scan["note"]


def _bound_entry(kind, path_name, record, head, final_field, final_ok):
    """One evidence entry for a commit-bound dossier record (convergence or
    approval): absent when the file does not exist, stale when its bound
    commit is not the current HEAD, unavailable when it is current but its
    own verdict field is not the passing one, current otherwise. Mirrors
    `status.build_team_report`'s own convergence/approval staleness compare
    (`bound != head`) exactly, rather than re-deriving a second notion of
    staleness."""
    if record is None:
        return {"kind": kind, "path": path_name, "status": "absent",
                "detail": "no %s recorded" % path_name}
    bound = record.get("headSha") if "headSha" in record else record.get("head")
    if head and bound and bound != head:
        return {"kind": kind, "path": path_name, "status": "stale",
                "detail": "%s binds to %s but the repository head is %s"
                          % (path_name, str(bound)[:12], head[:12])}
    final = record.get(final_field)
    if final not in final_ok:
        return {"kind": kind, "path": path_name, "status": "unavailable",
                "detail": "%s recorded %s %r" % (path_name, final_field, final)}
    return {"kind": kind, "path": path_name, "status": "current",
            "detail": "%s is current and %s" % (path_name, final)}


def _review_entry(dossier, head):
    path = os.path.join(dossier, "11-review.json")
    state, payload = status_mod._read_review_record(path)
    if state == "missing":
        return {"kind": "review", "path": "11-review.json", "status": "absent",
                "detail": "no review record is saved for this change"}
    if state == "malformed":
        return {"kind": "review", "path": "11-review.json", "status": "unavailable",
                "detail": "11-review.json cannot be trusted: %s" % payload}
    bound = payload.get("headSha")
    if head and bound and bound != head:
        return {"kind": "review", "path": "11-review.json", "status": "stale",
                "detail": "the review record binds to %s but the repository head is %s"
                          % (bound[:12], head[:12])}
    return {"kind": "review", "path": "11-review.json", "status": "current",
            "detail": "reviewed by %s: %s" % (payload.get("reviewer"), payload.get("result"))}


def _evidence_entries(dossier, root, head):
    entries = []
    receipts, _note = _receipt_entries(root)
    entries.extend(receipts)
    conv = status_mod._read_json_or_none(os.path.join(dossier, "09-convergence.json"))
    entries.append(_bound_entry("convergence", "09-convergence.json", conv, head,
                                "final", ("PASS",)))
    appr = status_mod._read_json_or_none(os.path.join(dossier, "10-approval.json"))
    entries.append(_bound_entry("approval", "10-approval.json", appr, head,
                                "final", ("PASS",)))
    entries.append(_review_entry(dossier, head))
    return entries


# ---------------------------------------------------------------------------
# nextAction: LOOP B: this used to be a private priority chain (`for kind in
# ("convergence", "approval", "review"): ...`, then a separate dirty-tree
# check, then receipts, then in-flight, then not-started), the FOURTH
# hand-rolled next-action ladder `lifecycle.py`'s own module docstring names
# (`status.build_report`'s old `_next_action`, `build_team_report`'s raw
# severity-number pick, and `/brothersbe:next`'s prose ladder were the other
# three). It now builds a list of candidates the identical way `status.py`'s
# own `_next_action` does -- one `lifecycle.candidate(rung, reason)` per
# outstanding fact, calling `status.py`'s OWN approval/task/review candidate
# builder (`_change_ladder_candidates`) directly rather than re-deriving any
# of those three judgements a second, weaker way -- and reduces through the
# ONE canonical `lifecycle.reduce_next_action`. On every fact `status.py`
# ALSO computes (approval, convergence, task readiness, review), this module
# can no longer disagree with `sbe status` or `sbe status --team` about which
# one is most urgent for the same commit. It can still, correctly, report a
# MORE urgent action than either surface would: `lifecycle.RUNG_UNCOMMITTED_
# STATE` (a dirty working tree) and the task-registry-unusable candidate
# below both name facts neither `status.py` surface ever inspects at all
# (a plain `sbe status` never checks working-tree cleanliness; the
# unparseable-registry case is the narrower claim, verified by an
# independent refuter on one fixture and true of the PLAIN surface only,
# since `sbe status --team` does surface an unusable registry through its
# own report -- see `_handover_candidates`'s own docstring below), so
# agreement is a claim about the facts both sides share, never a claim that
# the two surfaces read the identical set of facts for the same commit.
# ---------------------------------------------------------------------------

def _handover_candidates(root, dossier, head, evidence_entries, worktrees, receiver, all_done):
    """The candidate list `_derive_next_action` reduces, most of it built by
    calling functions `status.py` already owns rather than copied here:

      - convergence, at `lifecycle.RUNG_STALE_RECORD` (bound to a commit that
        moved, always eligible) or `lifecycle.RUNG_CONVERGENCE_FAILURE` (its
        own `final` is not PASS, always eligible; OR absent, but ONLY once
        `all_done` -- mirroring `status.py`'s own `_superseded_by_shared_
        ladder`'s identical gate on its severity-6 finding: an unattempted
        convergence report is not yet a problem while a task is still open
        or ready, the SAME BLOCKER A2 reasoning this module's approval
        candidate below already follows, applied to the one other fact this
        module reads unconditionally from `evidence_entries` rather than
        importing a status.py candidate builder for) -- read off
        `evidence_entries`' own `kind == "convergence"` entry (`_bound_entry`
        above already judges it the SAME commit-bound way `status.
        build_team_report`'s own severity-4/6 findings do; there is no
        separate importable convergence-candidate function in `status.py` to
        call instead, since team's own check is inlined there, not factored
        out).
      - approval's NO-DATA/absent verdict is deliberately NOT read here
        unconditionally the way the deleted private ladder read it: it
        comes ONLY from `status_mod._change_ladder_candidates` below, gated
        on every plan task being closed clean (`status_mod._approval_
        ladder_candidate`, called from inside that function). An
        UNCONDITIONAL absent-approval check here would reintroduce, for
        this module alone, the exact "nag about approval before any task
        work has even started" divergence BLOCKER A2 closed for
        `status.py` (see `_task_and_approval_candidates`'s own docstring).
        ROUND 2: a recorded approval FAILURE, or a STALE approval, is a
        different fact from "not attempted yet" and IS read unconditionally
        -- `status_mod._approval_ladder_candidate` (the SAME function,
        called a second time here rather than copied) is called directly,
        outside the `all_done` gate, and its result kept only when its own
        `verdict` is not `"NO-DATA"`, mirroring `status_mod._superseded_by_
        shared_ladder`'s identical rule for team's own severity-5 finding
        exactly ("a FAIL at any of the three ... stays eligible regardless
        of task state," that function's own docstring). Before this round,
        a genuine recorded FAIL never reached this module's `nextAction`
        while any task was still open, because `_task_and_approval_
        candidates` gates the WHOLE `_approval_ladder_candidate` call on
        `all_done`, FAIL branch included (status.py:886-889) -- a
        suppression `build_team_report` never has, since it builds its own
        severity-5 finding directly, outside that gate, and only filters
        the NO-DATA flavor back out at selection time. Calling the same
        function twice when `all_done` is True and approval genuinely
        failed adds two IDENTICAL candidates at the identical rung (same
        function, same arguments): never two competing wordings for one
        fact, just a redundant, harmless second copy the reducer's own
        first-wins tie-break discards.
      - a task registry that cannot be parsed (`.sbe/tasks.json` exists but
        is malformed) is its OWN candidate, at `lifecycle.RUNG_ACTIVE_
        CONFLICT` -- ROUND 2: `status_mod._change_ladder_candidates`
        swallows `tasks_mod.RegistryUnusable` and returns `[]`
        (status.py:931-934; see that function's own docstring for why --
        `build_report`'s ACTIVE CONFLICTS section names the identical
        failure as a display-only NO-DATA note there, `_section_line` over
        an empty list, never as a next-action candidate on that surface
        either). `_classify_tasks` already builds a `task-registry:
        unavailable` evidence entry for exactly this state (read back
        below, from `evidence_entries`); before this round it was disclosed
        but never turned into a candidate, so a dossier whose registry
        cannot be read fell straight through every task/approval/review
        check to the `RUNG_FINISH` fallback while its OWN record still
        listed every plan task as "not started" and the registry as
        unreadable side by side -- a self-contradicting record whose
        `nextAction` told the receiver to simply review and acknowledge it.
      - a dirty worktree, at `lifecycle.RUNG_UNCOMMITTED_STATE`: no fact in
        `status.py`'s own ladder corresponds to this (a plain `sbe status`
        never inspects working-tree cleanliness), so it is built here, at
        the rung `lifecycle.py` reserves for it.
      - a stale or unavailable evidence receipt, at
        `lifecycle.RUNG_MISSING_EVIDENCE`.
      - task-active, task-ready, review-not-cleared and gated approval (once
        every task is closed clean), via `status_mod._change_ladder_
        candidates` called directly: the SAME candidates `build_report` and
        `build_team_report`'s severity-10 finding are both derived from,
        imported rather than re-derived from this module's own `in_flight`/
        `not_started` lists the way the deleted private ladder did (which
        picked `in_flight[0]`/`not_started[0]` with no dependency check at
        all -- `_change_ladder_candidates`'s own ready-task selection skips
        a task whose dependencies are not all closed clean, a check the
        deleted ladder never made).
      - the fallback, at `lifecycle.RUNG_FINISH`: review this handover,
        naming `receiver`, so the list handed to `reduce_next_action` is
        never empty even when nothing above fired.
    """
    candidates = []

    # ROUND 2 / MAJOR: an unparseable task registry is its own candidate,
    # never a silent empty list. See this function's own docstring above.
    registry_hit = next((e for e in evidence_entries if e["kind"] == "task-registry"), None)
    if registry_hit is not None:
        candidates.append(lifecycle.candidate(
            lifecycle.RUNG_ACTIVE_CONFLICT,
            "resolve the task registry: %s" % registry_hit["detail"]))

    conv = next((e for e in evidence_entries if e["kind"] == "convergence"), None)
    if conv is not None and conv["status"] == "stale":
        candidates.append(lifecycle.candidate(
            lifecycle.RUNG_STALE_RECORD, "resolve convergence: %s" % conv["detail"]))
    elif conv is not None and conv["status"] == "unavailable":
        candidates.append(lifecycle.candidate(
            lifecycle.RUNG_CONVERGENCE_FAILURE, "resolve convergence: %s" % conv["detail"]))
    elif conv is not None and conv["status"] == "absent" and all_done:
        candidates.append(lifecycle.candidate(
            lifecycle.RUNG_CONVERGENCE_FAILURE, "resolve convergence: %s" % conv["detail"]))

    # ROUND 2 / MAJOR: a recorded approval FAILURE or a STALE approval must
    # never be suppressed by the `all_done` gate: reuse `status_mod._
    # approval_ladder_candidate` directly (never copied) and keep its result
    # whenever it is not the NO-DATA/absent verdict, mirroring `status_mod.
    # _superseded_by_shared_ladder`'s identical "a FAIL stays eligible
    # regardless of task state" rule for team's own severity-5 finding. See
    # this function's own docstring above.
    approval_finding = status_mod._approval_ladder_candidate(dossier, head, "")
    if approval_finding and approval_finding[0]["verdict"] != "NO-DATA":
        candidates.append(approval_finding[0])

    dirty = [w for w in worktrees if w.get("dirty")]
    if dirty:
        w = dirty[0]
        named = (", ".join(w["files"][:5]) + (", ..." if len(w["files"]) > 5 else "")) \
            if w["files"] else w["detail"]
        candidates.append(lifecycle.candidate(
            lifecycle.RUNG_UNCOMMITTED_STATE,
            "commit or stash the uncommitted state in %s: %s" % (w["path"], named)))

    receipt_hit = next((e for e in evidence_entries
                        if e["kind"] == "receipt" and e["status"] != "current"), None)
    if receipt_hit:
        candidates.append(lifecycle.candidate(
            lifecycle.RUNG_MISSING_EVIDENCE, "resolve evidence: %s" % receipt_hit["detail"]))

    candidates.extend(status_mod._change_ladder_candidates(root, dossier, head, ""))

    candidates.append(lifecycle.candidate(
        lifecycle.RUNG_FINISH,
        "%s: review this handover with sbe handover show and acknowledge or reject it"
        % receiver))
    return candidates


def _derive_next_action(root, dossier, head, evidence_entries, worktrees, receiver, all_done):
    candidates = _handover_candidates(
        root, dossier, head, evidence_entries, worktrees, receiver, all_done)
    return lifecycle.reduce_next_action(candidates)["reason"]


# ---------------------------------------------------------------------------
# preparedBy: who actually ran the command, derived from git identity in the
# repository (the same `user.name`/`user.email` a commit's own author line
# would read), never asked for on the command line. Falls back to the
# outgoing owner when git carries no configured identity, because that is
# the strongest signal left, not a guess: an unconfigured `git config` is a
# real absence, not a value this module invented.
# ---------------------------------------------------------------------------

def _prepared_by(root, outgoing):
    _c1, name, _e1 = _git(["config", "user.name"], root)
    _c2, email, _e2 = _git(["config", "user.email"], root)
    name = name.strip()
    email = email.strip()
    if name and email:
        return "%s <%s>" % (name, email)
    return name or email or outgoing


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _refuse_dir(dossier):
    return not os.path.isdir(os.path.abspath(dossier))


def cmd_prepare(args, exit_ok, exit_failed, exit_usage):
    dossier = _real(cwd_mod.resolve_relative(args.dossier, args.cwd))
    if _refuse_dir(dossier):
        sys.stderr.write("sbe handover prepare: %r is not a directory. A mistyped dossier "
                         "path must not silently write a handover for the wrong change.\n"
                         % dossier)
        return exit_usage
    outgoing = (args.outgoing or "").strip()
    receiver = (args.receiver or "").strip()
    if not outgoing or not receiver:
        sys.stderr.write("sbe handover prepare: --outgoing and --receiver are both required "
                         "and may not be blank.\n")
        return exit_usage
    if _identity_matches(outgoing, receiver):
        sys.stderr.write(
            "sbe handover prepare: refused. --receiver %r reads as the same identity as "
            "--outgoing %r (case, punctuation, an initial, a gmail dot or a homoglyph does "
            "not make a second person). A handover to yourself is not a handover; a second "
            "person must accept ownership.\n" % (receiver, outgoing))
        return exit_usage
    try:
        root = tasks_mod.repo_root_of(dossier)
    except tasks_mod.RegistryUnusable as exc:
        sys.stderr.write("sbe handover prepare: %s\n" % exc)
        return exit_usage

    with _handover_lock(dossier):
        state, existing = read_handover(dossier)
        head = status_mod._git_head(root)
        if not head:
            sys.stderr.write("sbe handover prepare: HEAD could not be resolved in %s; a "
                             "handover must bind to a real commit, so nothing was written.\n"
                             % root)
            return exit_failed
        if state == "malformed":
            sys.stderr.write(
                "sbe handover prepare: refused. %s already exists and cannot be trusted "
                "(%s). It was left in place rather than silently overwritten; move or "
                "remove it by hand, then re-run prepare.\n" % (handover_path(dossier), existing))
            return exit_failed
        if state == "ok":
            existing_status = existing.get("status")
            existing_head = existing.get("headSha")
            if existing_status == "acknowledged":
                sys.stderr.write(
                    "sbe handover prepare: refused. %s was already ACKNOWLEDGED by %r at "
                    "%s; preparing again would silently discard a completed acceptance "
                    "record. Acknowledged handovers are never overwritten by prepare.\n"
                    % (handover_path(dossier),
                       (existing.get("acknowledgment") or {}).get("receiver"),
                       (existing.get("acknowledgment") or {}).get("at")))
                return exit_failed
            if existing_status == "prepared" and existing_head != head:
                sys.stderr.write(
                    "sbe handover prepare: refused. %s is bound to %s and still awaiting a "
                    "receiver; preparing again at %s would silently replace it. Reject it "
                    "first (sbe handover reject) if it should no longer stand, or re-run "
                    "prepare once the receiver has acted.\n"
                    % (handover_path(dossier), str(existing_head)[:12], head[:12]))
                return exit_failed
            # existing_status == "rejected", or "prepared" at the SAME head:
            # a fresh prepare is the ordinary next step, not a silent overwrite.

        plan = status_mod._read_json_or_none(os.path.join(dossier, "08-plan.json"))
        done, in_flight, not_started, active, registry_problem = _classify_tasks(root, plan)
        worktrees = _worktrees(root, active, dossier)
        evidence_entries = _evidence_entries(dossier, root, head)
        if registry_problem:
            evidence_entries.insert(0, {"kind": "task-registry", "path": ".sbe/tasks.json",
                                        "status": "unavailable", "detail": registry_problem})
        # LOOP B: the SAME "every plan task closed clean" predicate
        # `status.py`'s own `_superseded_by_shared_ladder` gates its
        # severity-6 (convergence) and severity-5 (approval) NO-DATA
        # candidacy on, read here off the buckets `_classify_tasks` already
        # produced rather than re-derived a second way.
        all_done = bool(plan and plan.get("tasks")) and not in_flight and not not_started
        next_action = _derive_next_action(root, dossier, head, evidence_entries, worktrees,
                                          receiver, all_done)
        record = {
            "schemaVersion": SCHEMA_VERSION,
            "headSha": head,
            "change": os.path.relpath(dossier, root).replace(os.sep, "/"),
            "preparedBy": _prepared_by(root, outgoing),
            "preparedAt": _iso(time.time()),
            "outgoingOwner": outgoing,
            "intendedReceiver": receiver,
            "status": "prepared",
            "done": done,
            "inFlight": in_flight,
            "notStarted": not_started,
            "openQuestions": [],
            "decisions": [],
            "evidence": evidence_entries,
            "activeTasks": active,
            "worktrees": worktrees,
            "requiredAccess": [],
            "nextAction": next_action,
            "acknowledgment": None,
        }
        _atomic_write_json(handover_path(dossier), record)
    sys.stdout.write(
        "sbe handover prepare: written, bound to head %s: %d done, %d in flight, %d not "
        "started, %d evidence item(s). From %s to %s. Ownership remains with %s until %s "
        "acknowledges.\n  %s\n"
        % (head[:12], len(done), len(in_flight), len(not_started), len(evidence_entries),
           outgoing, receiver, outgoing, receiver, handover_path(dossier)))
    return exit_ok


def _binding_is_stale(dossier, root, bound, head):
    """True when a handover bound to `bound` no longer speaks for the tree at
    `head` -- the one shared rule from `content_binding.content_unchanged`,
    called here with the SAME exemption `_worktrees` already computes for the
    dirty-tree check (`12-handover.json` and its lock, by exact name, for the
    same self-reference reason): the record binds to a DIGEST of the tracked
    content it covers, everything in the repository except its own file and
    lock (see `docs/adr/2026-08-12-handover-across-two-machines.md` and the
    founder decision in `docs/plans/2026-08-12-overnight-team-version-and-
    roadmap.md` section 2.1 that this implements). Committing the record
    alone never changes that digest, so it survives; a commit that bundles
    the record with anything else changes a tracked path outside the
    exemption, so it still stales the record exactly as it always did."""
    if bound == head:
        return False
    if not bound:
        return True
    root_abs = _real(root)
    dossier_rel = os.path.relpath(_real(dossier), root_abs).replace(os.sep, "/")
    exempt = frozenset((dossier_rel + "/" + HANDOVER_REL, dossier_rel + "/" + _LOCK_REL))
    return not content_binding.content_unchanged(root_abs, bound, head, exempt)


def _state_summary(dossier, root, record):
    """(stale, head) for an already-parsed handover record: `stale` is True
    when the current HEAD differs from the record's bound `headSha` by more
    than the record's own introducing commit, False when it matches (or only
    the record's own commit(s) sit between them), and None when HEAD could
    not be resolved at all (the repository is unreachable from here) --
    NO-DATA about staleness, never a guess in either direction."""
    try:
        head = status_mod._git_head(root)
    except Exception:
        head = None
    if not head:
        return None, None
    return _binding_is_stale(dossier, root, record.get("headSha"), head), head


def cmd_show(args, exit_ok, exit_failed, exit_usage):
    dossier = _real(cwd_mod.resolve_relative(args.dossier, args.cwd))
    if _refuse_dir(dossier):
        sys.stderr.write("sbe handover show: %r is not a directory.\n" % dossier)
        return exit_usage
    state, payload = read_handover(dossier)
    if state == "missing":
        if args.json:
            sys.stdout.write(json.dumps(
                {"dossier": dossier, "path": handover_path(dossier), "state": "missing",
                 "stale": None, "record": None}, indent=2, sort_keys=True) + "\n")
        else:
            sys.stdout.write(
                "sbe handover show: NO-DATA. No %s exists for %s. Nobody has prepared a "
                "handover for this change; absence is a fact, not an accusation, and never "
                "a block when ownership is not changing.\n" % (HANDOVER_REL, dossier))
        return exit_ok
    if state == "malformed":
        if args.json:
            sys.stdout.write(json.dumps(
                {"dossier": dossier, "path": handover_path(dossier), "state": "malformed",
                 "stale": None, "record": None, "detail": payload},
                indent=2, sort_keys=True) + "\n")
        else:
            sys.stdout.write("sbe handover show: FAIL. %s cannot be trusted: %s\n"
                             % (handover_path(dossier), payload))
        return exit_failed

    try:
        root = tasks_mod.repo_root_of(dossier)
    except tasks_mod.RegistryUnusable:
        root = dossier
    stale, head = _state_summary(dossier, root, payload)
    if args.json:
        sys.stdout.write(json.dumps(
            {"dossier": dossier, "path": handover_path(dossier), "state": "ok",
             "stale": stale, "currentHead": head, "record": payload},
            indent=2, sort_keys=True) + "\n")
        return exit_ok
    stale_note = ""
    if stale is True:
        stale_note = " STALE: bound to %s, HEAD is now %s." % (
            str(payload.get("headSha"))[:12], str(head)[:12])
    elif stale is None:
        stale_note = " (staleness unknown: HEAD could not be resolved here)"
    ack = payload.get("acknowledgment")
    lines = [
        "sbe handover show: %s" % payload.get("change"),
        "  status       %s%s" % (payload.get("status"), stale_note),
        "  from -> to   %s -> %s" % (payload.get("outgoingOwner"), payload.get("intendedReceiver")),
        "  head         %s" % str(payload.get("headSha"))[:12],
        "  done         %d" % len(payload.get("done") or []),
        "  in flight    %d" % len(payload.get("inFlight") or []),
        "  not started  %d" % len(payload.get("notStarted") or []),
        "  evidence     %d item(s)" % len(payload.get("evidence") or []),
        "  next action  %s" % payload.get("nextAction"),
    ]
    if ack:
        lines.append("  acknowledgment %s by %s at %s (head %s)%s"
                     % (ack.get("outcome"), ack.get("receiver"), ack.get("at"),
                        str(ack.get("headSha"))[:12],
                        (": %s" % ack.get("reason")) if ack.get("reason") else ""))
    sys.stdout.write("\n".join(lines) + "\n")
    return exit_ok


def _cmd_respond(args, outcome, exit_ok, exit_failed, exit_usage):
    dossier = _real(cwd_mod.resolve_relative(args.dossier, args.cwd))
    if _refuse_dir(dossier):
        sys.stderr.write("sbe handover %s: %r is not a directory.\n"
                         % ("acknowledge" if outcome == "accepted" else "reject", dossier))
        return exit_usage
    receiver = (args.receiver or "").strip()
    if not receiver:
        sys.stderr.write("sbe handover: --receiver is required and may not be blank.\n")
        return exit_usage
    if outcome == "rejected" and not (args.reason or "").strip():
        sys.stderr.write("sbe handover reject: --reason is required and may not be blank; a "
                         "rejection with no reason is an off switch, not a decision.\n")
        return exit_usage
    try:
        root = tasks_mod.repo_root_of(dossier)
    except tasks_mod.RegistryUnusable as exc:
        sys.stderr.write("sbe handover: %s\n" % exc)
        return exit_usage

    verb = "acknowledge" if outcome == "accepted" else "reject"
    with _handover_lock(dossier):
        state, data = read_handover(dossier)
        if state == "missing":
            sys.stderr.write(
                "sbe handover %s: refused. No %s exists for %s; run sbe handover prepare "
                "first.\n" % (verb, HANDOVER_REL, dossier))
            return exit_failed
        if state == "malformed":
            sys.stderr.write("sbe handover %s: refused. %s cannot be trusted: %s\n"
                             % (verb, handover_path(dossier), data))
            return exit_failed
        if data.get("status") != "prepared":
            ack = data.get("acknowledgment") or {}
            sys.stderr.write(
                "sbe handover %s: refused. This handover already carries status %r "
                "(recorded outcome %r by %r at %s); a second acknowledge or reject is "
                "refused. Prepare a fresh handover to try again.\n"
                % (verb, data.get("status"), ack.get("outcome"), ack.get("receiver"),
                   ack.get("at")))
            return exit_failed
        outgoing = data.get("outgoingOwner") or ""
        if _identity_matches(receiver, outgoing):
            sys.stderr.write(
                "sbe handover %s: refused. --receiver %r reads as the same identity as the "
                "outgoing owner %r; the outgoing owner cannot %s their own handover. A "
                "second person must act.\n" % (verb, receiver, outgoing, verb))
            return exit_usage
        matched_agent = next((a for a in _agent_identities(root)
                              if _identity_matches(receiver, a)), None)
        if matched_agent is not None:
            sys.stderr.write(
                "sbe handover %s: refused. --receiver %r reads as the same identity as "
                "agent %r recorded in the task registry; an agent identity may never "
                "acknowledge or reject as the human receiver.\n" % (verb, receiver, matched_agent))
            return exit_usage
        head = status_mod._git_head(root)
        if not head:
            sys.stderr.write("sbe handover %s: refused. HEAD could not be resolved in %s.\n"
                             % (verb, root))
            return exit_failed
        bound = data.get("headSha")
        if _binding_is_stale(dossier, root, bound, head):
            sys.stderr.write(
                "sbe handover %s: refused. This handover is STALE: bound to %s but HEAD is "
                "now %s. Re-run sbe handover prepare against the current head before %sing "
                "it.\n" % (verb, str(bound)[:12], head[:12], verb))
            return exit_failed
        if outcome == "accepted":
            required = data.get("requiredAccess") or []
            if required:
                sys.stderr.write(
                    "sbe handover acknowledge: refused. Required access is still "
                    "outstanding and acceptance is refused until it is granted: %s\n"
                    % ", ".join(str(r) for r in required))
                return exit_failed
        ack = {"outcome": outcome, "receiver": receiver, "at": _iso(time.time()), "headSha": head}
        if outcome == "rejected":
            ack["reason"] = args.reason.strip()
        data["acknowledgment"] = ack
        data["status"] = "acknowledged" if outcome == "accepted" else "rejected"
        _atomic_write_json(handover_path(dossier), data)
    sys.stdout.write(
        "sbe handover %s: %s by %s at head %s.%s\n"
        % (verb, outcome, receiver, head[:12],
           (" Ownership remains with %s." % outgoing) if outcome == "rejected"
           else " Ownership has moved to %s." % receiver))
    return exit_ok


def cmd_acknowledge(args, exit_ok, exit_failed, exit_usage):
    return _cmd_respond(args, "accepted", exit_ok, exit_failed, exit_usage)


def cmd_reject(args, exit_ok, exit_failed, exit_usage):
    return _cmd_respond(args, "rejected", exit_ok, exit_failed, exit_usage)


def _parser():
    parser = argparse.ArgumentParser(
        prog="sbe handover",
        description="Explicit human handover: ownership transfer is complete only after a "
                    "named human receiver acknowledges. See docs/CLI.md.")
    sub = parser.add_subparsers(dest="sub")

    pp = sub.add_parser("prepare", help="derive and write 12-handover.json for one dossier")
    pp.add_argument("dossier")
    pp.add_argument("--outgoing", required=True, help="the identity handing off ownership")
    pp.add_argument("--receiver", required=True,
                    help="the identity or role meant to receive it")
    cwd_mod.add_cwd_argument(
        pp, help_text="anchor a relative dossier path here instead of the current "
                      "directory; an already-absolute dossier path is unaffected")

    sh = sub.add_parser("show", help="print the handover recorded for one dossier")
    sh.add_argument("dossier")
    sh.add_argument("--json", action="store_true", help="machine-readable output")
    cwd_mod.add_cwd_argument(sh)

    ak = sub.add_parser("acknowledge", help="the receiver accepts ownership")
    ak.add_argument("dossier")
    ak.add_argument("--receiver", required=True, help="the accepting identity")
    cwd_mod.add_cwd_argument(ak)

    rj = sub.add_parser("reject",
                        help="the receiver declines; ownership stays with the outgoing owner")
    rj.add_argument("dossier")
    rj.add_argument("--receiver", required=True, help="the declining identity")
    rj.add_argument("--reason", required=True, help="why, recorded on the handover")
    cwd_mod.add_cwd_argument(rj)
    return parser


def main(rest, exit_ok=0, exit_failed=1, exit_usage=2):
    """The `sbe handover` surface. Exit codes come from the caller so this
    module never disagrees with the CLI's documented table."""
    parser = _parser()
    if not rest:
        parser.print_help(sys.stderr)
        return exit_usage
    try:
        args = parser.parse_args(list(rest))
    except SystemExit as exc:
        # argparse already answered: -h/--help printed the usage the caller
        # asked for and raised 0, and help is not an error. Anything nonzero
        # is a refused flag or argument.
        return exit_ok if exc.code in (0, None) else exit_usage
    handlers = {"prepare": cmd_prepare, "show": cmd_show,
               "acknowledge": cmd_acknowledge, "reject": cmd_reject}
    handler = handlers.get(getattr(args, "sub", None))
    if handler is None:
        parser.print_help(sys.stderr)
        return exit_usage
    try:
        return handler(args, exit_ok, exit_failed, exit_usage)
    except HandoverUnusable as exc:
        sys.stderr.write("sbe handover: %s\n" % exc)
        return exit_usage
