#!/usr/bin/env python3
"""bm_reconcile.py: startup reconciliation (Z2.3 through Z2.6, BROTHER_ZOO_
HARVEST_ACCELERATION_PLAN_2026-08-23.md section 9).

WHAT THIS DOES
  A single read-only pass that compares what BrotherMode's store PERSISTS
  against what is OBSERVABLE right now (git, the filesystem, process and
  controller-run liveness) and classifies every relevant record into one
  of five words: VALID, RECOVERABLE, STALE, CONFLICT, NO-DATA. See
  docs/RECOVERY-TRUTH.md for the full inventory of the state fields this
  reads and the reasoning behind every classification rule below; this
  file is the implementation, that document is the design record.

WHAT THIS DOES NOT DO (Z2.4: report only, this pass)
  No repair is ever performed. Every non-VALID row carries a `next_action`
  string, the same discipline `bm_stall.py`'s SD5 findings use: propose the
  exact command, never run it.

REUSE, NOT REIMPLEMENTATION
  The fence/dead-owner half of this (a stale claim, a dead provisional
  record) is `bm_stall.sweep()`'s own output, relabelled, not re-derived:
  this module never computes owner liveness itself. The store-integrity
  half (an internally inconsistent store) is `bm_store.verify()`'s own
  output, relabelled the same way. Z2's own closing bullet says "SBE does
  not gain a duplicate lifecycle reconciler"; the same discipline applies
  here, one project over.

EFFECT CLASS: mostly pure_read, ONE exception
  Every store read goes through `bm_store.ReadOnlyStore`, never `Store`
  (opening a writable Store is itself a write; see `bm_stall.py`'s own
  EFFECT CLASS note). Unlike `bm_store.py` and `bm_stall.py`, this file
  DOES spawn `git` subprocesses, read-only ones only (`rev-parse`, never
  anything that mutates), the same posture `tools/bm_autosave.py` and
  `tools/bm_sessionstart.py` already take: the git facts Z2.3 needs (is
  there an upstream remote at all) exist nowhere in the store.

SCOPE OF WHAT GETS A ROW
  `records` (the fence/ownership ledger): every row gets a classification,
  including a healthy one, because Z2.3 says "classify EACH relevant
  record." `controller_units`: only a unit whose current status is an
  OPEN dispatch, or a settled one, gets a row; every other status
  (pending, ready, claimed, under review, failed, blocked, skipped) has
  nothing this pass's fault cases speak to, and emitting a row with
  nothing to say would be noise, not signal.

BE SILENT-SAFE, mirroring bm_stall.py's own contract: an unreadable store
degrades to one NO-DATA row naming why, never a crash and never a false
"nothing wrong."

OWNER, ROUTE AND LINEAGE (2026-09-17). Every row also carries WHO the
finding belongs to and WHERE it goes: `owner_session` (records.session_id,
or a git author for a commit finding), `owner_confidence` (derived: the label
this sweep recomputed from its own token file matches; declared: any
other id, a `bm1-` or `cli-` string a caller typed, never provable; name-match: no
session id, only records.owner; unresolvable: nothing), `owner_liveness`
(bm_stall.owner_liveness's own verdict, never recomputed here), `route`
(mine: the owner label equals the label derived from THIS session's own
token file, read at token_path(root, --session-id) and never created;
owner: a LIVE other session, left alone; founder: DEAD, UNKNOWN or
unresolvable), `decision_class` (1 when route is mine, 2 otherwise, per
the delegation order's class 2 item 7: another session's fence is a
coordination decision), `lineage` (the store's own history for the
subject, ordered by time: transitions, handovers, the provisional row,
autosave receipts, and Brother-Run / Brother-Harness commit trailers where
a commit carries one that names the owner), `fingerprint` (sha256 of
kind, anchor and owner, the dedup key `file` uses) and `observed_ref`
(the git HEAD the facts were read at, or "" outside a repository).

THE ONE WRITE: `file` (2026-09-17). `sweep` stays read-only. `file` runs
the same pass and then persists each non-VALID row as a store note (kind
alert, severity warning, anchored to the record or the file it is about),
deduplicated by fingerprint inside one BEGIN IMMEDIATE transaction, read
then insert, so two filers racing produce one note. It never resolves a
note, never touches records, and a fingerprint already filed (resolved or
not) is never filed twice.

Python 3.9, standard library only besides `subprocess` (git, read only).
No em or en dashes anywhere in this file or its output.

Usage:
  python3 tools/bm_reconcile.py [sweep|file] [--root PATH] [--now ISO8601]
    [--stale-seconds N] [--pid-hint SESSION_ID=PID] [--session-id ID]
    [--json]
"""

import datetime
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))

VALID = "VALID"
RECOVERABLE = "RECOVERABLE"
STALE = "STALE"
CONFLICT = "CONFLICT"
NO_DATA = "NO-DATA"

#: The two controller_units.status values this pass has anything to say
#: about (tools/bm_store.py:3918's own CONTROLLER_UNIT_STATES tuple holds
#: the full ten; see docs/RECOVERY-TRUTH.md section 4 for the other
#: eight). Named here, once, rather than spelled inline at every compare,
#: so the two literal spellings this module depends on live in one place.
_UNIT_STATUS_OPEN_DISPATCH = "DISPATCHED"
_UNIT_STATUS_SETTLED = "DONE"

#: The controller_dispatches.status values this pass reads (tools/
#: bm_store.py:3933's own CONTROLLER_DISPATCH_STATUSES tuple holds all
#: five). An attempt still OPEN has no outcome yet; one that PASSED
#: REVIEW is the only status this module treats as a real verifier
#: verdict (REJECTED and CANCELLED are terminal but are not evidence of a
#: passing check, so they never satisfy the "was this verified" test
#: below).
_DISPATCH_STATUS_OPEN = "DISPATCHED"
_DISPATCH_STATUS_PASSED_REVIEW = "VERIFIED"


def _load(name):
    """Load a sibling tools/ module by PATH, the same technique
    tools/bm_stall.py:_load uses: this file may be invoked from an
    arbitrary working directory, so a plain `import bm_store` would depend
    on whatever sys.path the caller happened to have."""
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_bm_store():
    try:
        return _load("bm_store"), None
    except Exception as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)


def _load_bm_stall():
    try:
        return _load("bm_stall"), None
    except Exception as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)


def _load_bm_fence_hook():
    try:
        return _load("bm_fence_hook"), None
    except Exception as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)


#: owner_confidence values. See the module docstring.
CONF_DERIVED = "derived"
CONF_DECLARED = "declared"
CONF_NAME_MATCH = "name-match"
CONF_UNRESOLVABLE = "unresolvable"

#: route values.
ROUTE_MINE = "mine"
ROUTE_OWNER = "owner"
ROUTE_FOUNDER = "founder"

#: The fence hook's own public-label prefix (bm_fence_hook.LABEL_PREFIX),
#: read from that module when it loads; this literal is the fallback for
#: the confidence split only, never for deriving a label.
_LABEL_PREFIX_FALLBACK = "bm1-"


def fingerprint(kind, anchor, owner_session, cls="", category=""):
    """The identity of one finding: what kind of subject, which one, whose,
    what verdict and which reason category. The category is a fixed word
    per rule (stale-fence, settled-then-edited, no-upstream, ...), never
    the timestamped reason text, so the id is stable across runs and
    commits and changes exactly when the finding itself changes (checker
    finding 2, 2026-09-17: without class and category, a later distinct
    finding on the same record and owner was never filed)."""
    return hashlib.sha256(("%s|%s|%s|%s|%s" % (kind, anchor, owner_session
                                               or "", cls, category))
                          .encode("utf-8")).hexdigest()[:16]


def _row(cls, kind, subject, reason, next_action="", anchor="",
         owner_session="", owner_confidence=CONF_UNRESOLVABLE,
         owner_liveness="", route=ROUTE_FOUNDER, lineage=None,
         observed_ref="", category=""):
    """One finding. Every row carries the owner/route/lineage fields so a
    reader (and `file`) never has to special-case a kind; a kind with no
    owner to speak of (push-state, store-integrity) says so through
    owner_confidence=unresolvable and route=founder rather than by
    omitting the keys."""
    return {"class": cls, "kind": kind, "subject": subject or "",
            "reason": reason, "next_action": next_action or "",
            "anchor": anchor or "", "category": category or "",
            "fingerprint": fingerprint(kind, anchor, owner_session, cls,
                                       category),
            "owner_session": owner_session or "",
            "owner_confidence": owner_confidence,
            "owner_liveness": owner_liveness or "",
            "route": route,
            "decision_class": 1 if route == ROUTE_MINE else 2,
            "lineage": list(lineage or []),
            "observed_ref": observed_ref or ""}


def own_label(fh, root, session_id):
    """The label THIS session can prove it owns: derived from the token at
    token_path(root, session_id), read only. Never calls ensure_token (that
    creates the token, a write); a missing, unreadable or malformed token
    means "" and therefore nothing routes mine. A `cli-` style id with no
    token bridge falls out here naturally: no token file, no label."""
    if not session_id or fh is None:
        return ""
    try:
        path = fh.token_path(root, session_id)
        with io.open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except (OSError, ValueError):  # sbe: allow-silent no token means not mine, the documented fail-closed answer
        return ""
    if not fh._valid_token_text(text):
        return ""
    return fh.label_for_token(text.strip())


def owner_fields(record, my_label, liveness, label_prefix=None):
    """(owner_session, owner_confidence, route) for one records row.
    derived ONLY when the record's session_id equals the label THIS sweep
    recomputed from the one token file it read (the calling session's
    own); a bm1- prefix on its own proves nothing, anyone can type one,
    so every other id, bm1- or cli- alike, is declared. route=mine on the
    same equality; a declared id can never be mine, whatever it says.
    `label_prefix` is kept for callers and no longer decides anything."""
    sid = (record.get("session_id") or "").strip()
    owner = (record.get("owner") or "").strip()
    if sid and my_label and sid == my_label:
        conf = CONF_DERIVED
    elif sid:
        conf = CONF_DECLARED
    elif owner:
        conf = CONF_NAME_MATCH
    else:
        conf = CONF_UNRESOLVABLE
    owner_session = sid or (owner if conf == CONF_NAME_MATCH else "")
    if sid and my_label and sid == my_label:
        route = ROUTE_MINE
    elif sid and liveness == "LIVE":
        route = ROUTE_OWNER
    else:
        route = ROUTE_FOUNDER
    return owner_session, conf, route


def _lineage_sort_key(item):
    return (item.get("at") or "", item.get("kind") or "", str(item.get("id")))


def record_lineage(bs, store, lifecycle_uuid, session_ids, trailer_commits):
    """The store's own history for one record, oldest first. Every entry
    is {kind, id, at} plus the columns that name who acted. Commit trailers
    join only when a commit's Brother-Harness value equals one of the
    record's own session ids (the harness id the trailer records IS a
    session id), so an unrelated lane's commit never lands here."""
    items = []
    for t in bs._exec(store, "SELECT id, from_state, to_state, session_id, at "
                             "FROM transitions WHERE lifecycle_uuid=? "
                             "ORDER BY id", (lifecycle_uuid,)).fetchall():
        items.append({"kind": "transition", "id": str(t["id"]), "at": t["at"],
                      "from_state": t["from_state"] or "",
                      "to_state": t["to_state"],
                      "session_id": t["session_id"] or ""})
        if t["session_id"]:
            session_ids.add(t["session_id"])
    for h in bs._exec(store, "SELECT handover_uuid, from_session_id, "
                             "to_session_id, created_at, delivered_at FROM "
                             "handovers WHERE lifecycle_uuid=? ORDER BY "
                             "created_at", (lifecycle_uuid,)).fetchall():
        items.append({"kind": "handover", "id": h["handover_uuid"],
                      "at": h["created_at"],
                      "from_session_id": h["from_session_id"] or "",
                      "to_session_id": h["to_session_id"] or "",
                      "delivered_at": h["delivered_at"] or ""})
        for s in (h["from_session_id"], h["to_session_id"]):
            if s:
                session_ids.add(s)
    p = bs._exec(store, "SELECT created_session_id, created_at, promoted_at, "
                        "cancelled_at FROM provisional_records WHERE "
                        "lifecycle_uuid=?", (lifecycle_uuid,)).fetchone()
    if p is not None:
        items.append({"kind": "provisional", "id": lifecycle_uuid,
                      "at": p["created_at"],
                      "session_id": p["created_session_id"] or "",
                      "promoted_at": p["promoted_at"] or "",
                      "cancelled_at": p["cancelled_at"] or ""})
        if p["created_session_id"]:
            session_ids.add(p["created_session_id"])
    for sid in sorted(session_ids):
        for a in bs._exec(store, "SELECT id, worktree_id, snapshot_sha, "
                                 "created_at FROM autosave_receipts WHERE "
                                 "session_id=? ORDER BY id",
                          (sid,)).fetchall():
            items.append({"kind": "autosave", "id": str(a["id"]),
                          "at": a["created_at"], "session_id": sid,
                          "snapshot_sha": a["snapshot_sha"],
                          "worktree_id": a["worktree_id"]})
        for c in trailer_commits:
            if c["harness"] == sid:
                items.append({"kind": "commit", "id": c["sha"], "at": c["at"],
                              "session_id": sid, "brother_run": c["run"],
                              "brother_harness": c["harness"]})
    items.sort(key=_lineage_sort_key)
    return items


# ---------------------------------------------------------------------------
# git, read-only. The one place this module differs from bm_store.py and
# bm_stall.py's own no-subprocess posture; see the module docstring.
# ---------------------------------------------------------------------------

def _run_git(root, *args):
    """Mirrors tools/bm_autosave.py's own _run_git: OSError (no git on
    PATH) degrades to a CompletedProcess with returncode 127 rather than
    raising, so every caller's return-code check works uniformly."""
    try:
        return subprocess.run(["git", "-C", root] + list(args),
                              capture_output=True, text=True, timeout=10)
    except OSError as exc:
        return subprocess.CompletedProcess(args, 127, "", str(exc))
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(args, 124, "", str(exc))


def _git_toplevel(root):
    """The real repository root git itself would use, or None when git is
    unavailable, or "" when `root` is not inside a git repository. A
    push-state finding is meaningless outside a git repo, so callers use
    this to decide whether to say anything at all."""
    result = _run_git(root, "rev-parse", "--show-toplevel")
    if result.returncode == 127:
        return None
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def observed_ref(root):
    """The git HEAD the facts were read at, "" outside a repository or
    without git: a finding is a claim about one tree, and this names it."""
    result = _run_git(root, "rev-parse", "HEAD")
    return result.stdout.strip() if result.returncode == 0 else ""


#: How far back the trailer scan looks. Bounded on purpose: this runs at
#: every session start, and a lineage is about recent hands, not archaeology.
_TRAILER_SCAN_DEPTH = 400


def trailer_commits(root):
    """Recent commits on HEAD carrying a Brother-Run or Brother-Harness
    trailer, newest first, each {sha, at, run, harness}. One git call per
    pass; [] outside a repository. Only commits that carry at least one of
    the two trailers are kept."""
    result = _run_git(root, "log", "-n", str(_TRAILER_SCAN_DEPTH),
                      "--format=%H%x1f%aI%x1f%(trailers:key=Brother-Run,"
                      "valueonly,separator=%x20)%x1f%(trailers:key="
                      "Brother-Harness,valueonly,separator=%x20)", "HEAD")
    if result.returncode != 0:
        return []
    out = []
    for line in result.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 4:
            continue
        sha, at, run, harness = (p.strip() for p in parts)
        if run or harness:
            out.append({"sha": sha, "at": at, "run": run, "harness": harness})
    return out


def classify_push_state(root):
    """Case 14: 'external push or release state unobservable.' A
    BrotherMode root with no git repo at all has nothing to say here (most
    of this suite's own fixtures are exactly that, and it is a legitimate,
    ordinary state, not a fault); one WITH a repo but no upstream tracking
    branch cannot answer whether the work has shipped anywhere, which is
    NO-DATA, named."""
    top = _git_toplevel(root)
    if top is None:
        return [_row(NO_DATA, "push-state", root,
                     "external push/release state is unobservable: git "
                     "is not available on PATH", "install git, or check "
                     "the remote by hand", category="no-git")]
    if not top:
        return []
    result = _run_git(root, "rev-parse", "--abbrev-ref",
                      "--symbolic-full-name", "@{u}")
    if result.returncode == 127:
        return [_row(NO_DATA, "push-state", root,
                     "external push/release state is unobservable: git "
                     "is not available on PATH", "install git, or check "
                     "the remote by hand", category="no-git")]
    if result.returncode != 0:
        return [_row(NO_DATA, "push-state", root,
                     "external push/release state is unobservable: no "
                     "upstream tracking branch is configured for the "
                     "current branch",
                     "configure one with `git push -u origin <branch>`, "
                     "or check the remote by hand", category="no-upstream")]
    return [_row(VALID, "push-state", root,
                 "tracks %s" % result.stdout.strip(), "", category="tracks")]


#: Which installed hook reads which tracked path's output. Explicit and
#: small on purpose: this is never derived from ~/.claude/settings.json at
#: runtime (a hook that is wired but not shipped is exactly the drift this
#: finding is for, and parsing the live settings would hide it). Keys are
#: the hook as the founder names it; values are the repository paths whose
#: commits that hook silently depends on. Today: intake_gate.py refuses a
#: decision unless scripts/decide.py stamped a screen for this session, so
#: a checkout whose decide.py is on no remote branch has a hook depending
#: on code nobody else can see.
HOOK_READS = {
    "~/.claude/hooks/intake_gate.py": ("scripts/decide.py",),
}


def _patch_ids(top, path, *revs):
    """{patch_id: sha} for every commit selected by `revs` that touches
    `path`, with the diff limited to that path: `git log -p` piped into
    `git patch-id --stable`, the same equivalence `git cherry` and `git
    log --cherry-pick` use. Path-limited on purpose: what the hook depends
    on is the change to the file it reads, not the rest of the commit."""
    log = _run_git(top, "log", "-p", "--format=%H", "--no-color",
                   *revs, "--", path)
    if log.returncode != 0 or not log.stdout.strip():
        return {}
    try:
        ids = subprocess.run(["git", "-C", top, "patch-id", "--stable"],
                             input=log.stdout, capture_output=True,
                             text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if ids.returncode != 0:
        return {}
    out = {}
    for line in ids.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2:
            out[parts[0]] = parts[1]
    return out


def unpushed_commits_touching(top, path):
    """Commits reachable from HEAD that touch `path` (a path relative to
    the repository TOPLEVEL, checker finding 4) whose change to it is on
    NO remote branch, newest first, each {sha, author, at}. `HEAD --not
    --remotes` is the set `git branch -r --contains <sha>` being empty
    describes; a commit in that set whose path-limited patch-id also
    exists on a remote (a squash, a cherry-pick, a rebase: same change,
    other sha) is NOT unpushed (checker finding 3), the same test `git
    cherry` applies. A repository with no remote at all has nothing to
    exclude, so every commit counts as unpushed there, which is the true
    answer."""
    result = _run_git(top, "log", "--format=%H%x1f%an%x1f%aI", "HEAD",
                      "--not", "--remotes", "--", path)
    if result.returncode != 0:
        return []
    candidates = []
    for line in result.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3:
            candidates.append({"sha": parts[0], "author": parts[1],
                               "at": parts[2]})
    if not candidates:
        return []
    upstream = _patch_ids(top, path, "--remotes", "--not", "HEAD")
    if not upstream:
        return candidates
    local = _patch_ids(top, path, "HEAD", "--not", "--remotes")
    equivalent = {sha for pid, sha in local.items() if pid in upstream}
    return [c for c in candidates if c["sha"] not in equivalent]


def classify_unpushed_hook_deps(root, ref=""):
    """CONFLICT when a commit on the current checkout that touches a path
    an installed hook reads is on no remote branch: the hook's behaviour
    on this machine depends on code no other checkout can reach. Owner is
    the commit's git author (declared: a name git recorded, never proved);
    route founder, since pushing is his hand."""
    top = _git_toplevel(root)
    if not top:
        return []
    rows = []
    for hook, paths in sorted(HOOK_READS.items()):
        for path in paths:
            commits = unpushed_commits_touching(top, path)
            if not commits:
                continue
            newest = commits[0]
            rows.append(_row(
                CONFLICT, "unpushed-hook-dependency", path,
                "%s reads what %s writes, and %d commit(s) on this checkout "
                "touching %s are on no remote branch (newest %s by %s at "
                "%s): the installed hook depends on code no other checkout "
                "can see"
                % (hook, path, len(commits), path, newest["sha"][:12],
                   newest["author"], newest["at"]),
                "push the branch carrying %s (git branch -r --contains %s "
                "is empty today and no remote commit carries the same "
                "patch to this path), or cherry-pick it onto a pushed "
                "branch, before relying on %s" % (newest["sha"][:12],
                                          newest["sha"][:12], hook),
                anchor="file:" + path, owner_session=newest["author"],
                owner_confidence=CONF_DECLARED, owner_liveness="UNKNOWN",
                route=ROUTE_FOUNDER,
                lineage=[{"kind": "commit", "id": c["sha"], "at": c["at"],
                          "author": c["author"]}
                         for c in sorted(commits, key=lambda c: c["at"])],
                observed_ref=ref, category="unpushed-commit"))
    return rows


# ---------------------------------------------------------------------------
# records / claims / transitions / provisional_records: reuse bm_stall's
# sweep() for the dead-owner half, add the settle-then-edit comparison
# sweep() does not do.
# ---------------------------------------------------------------------------

def classify_records(bs, st, store, root, now, stale_after_seconds,
                     pid_hints, my_label="", label_prefix=None, ref="",
                     trailers=None):
    findings = st.sweep(bs, store, now=now,
                        stale_after_seconds=stale_after_seconds,
                        pid_hints=pid_hints)
    by_uuid = {}
    for f in findings:
        by_uuid.setdefault(f["lifecycle_uuid"], []).append(f)
    label_prefix = label_prefix or _LABEL_PREFIX_FALLBACK
    trailers = trailers or []
    terminal_states = st._controller_terminal_states(bs)

    rows = []
    records = bs._exec(
        store, "SELECT lifecycle_uuid, name, state, session_id, owner, "
              "version, created_at, updated_at FROM records "
              "ORDER BY name, lifecycle_uuid").fetchall()
    for r in records:
        r = dict(r)
        u = r["lifecycle_uuid"]
        subject = "%s (%s)" % (r["name"], u[:8])
        fs = by_uuid.get(u, [])
        kinds = {f["kind"] for f in fs}

        # Owner, liveness, route and lineage: the same SD1 oracle sweep()
        # itself used, asked once more per record so a LIVE or UNKNOWN
        # owner (which sweep() stays silent about) still gets a verdict
        # on its row.
        signals = st._owner_signals(bs, store, r, terminal_states,
                                    pid_hints, now)
        liveness, _why = st.owner_liveness(signals, stale_after_seconds)
        owner_session, conf, route = owner_fields(r, my_label, liveness,
                                                  label_prefix)
        sids = {owner_session} if owner_session else set()
        lineage = record_lineage(bs, store, u, sids, trailers)
        who = dict(anchor="record:" + u, owner_session=owner_session,
                   owner_confidence=conf, owner_liveness=liveness,
                   route=route, lineage=lineage, observed_ref=ref)

        if st.DUPLICATE_FENCE_LINES in kinds:
            f = next(f for f in fs if f["kind"] == st.DUPLICATE_FENCE_LINES)
            rows.append(_row(CONFLICT, "record", subject, f["message"],
                             f["actions"][0]["command"] if f["actions"]
                             else "", category=f["kind"], **who))
            continue
        # DEAD_OWNER_PROVISIONAL is checked BEFORE the plain stale-fence
        # kinds on purpose: sweep() emits BOTH findings for the same
        # lifecycle_uuid when a dead owner's record happens to be a
        # provisional one (a STALE_FENCE/DEAD_WATCHDOG finding for the
        # fence itself, plus DEAD_OWNER_PROVISIONAL for the provisional
        # row), and the more specific, safely-actionable verdict
        # (RECOVERABLE: cancel it, one mechanical fix, no human choice)
        # is the more useful of the two to surface, not the generic one.
        if st.DEAD_OWNER_PROVISIONAL in kinds:
            f = next(f for f in fs if f["kind"] == st.DEAD_OWNER_PROVISIONAL)
            rows.append(_row(RECOVERABLE, "record", subject, f["message"],
                             "; or ".join(a["command"] for a in f["actions"])
                             if f["actions"] else "", category=f["kind"],
                             **who))
            continue
        if kinds & {st.STALE_FENCE, st.DEAD_WATCHDOG}:
            f = next(f for f in fs
                    if f["kind"] in (st.STALE_FENCE, st.DEAD_WATCHDOG))
            rows.append(_row(STALE, "record", subject, f["message"],
                             f["actions"][0]["command"] if f["actions"]
                             else "", category=f["kind"], **who))
            continue

        if r["state"] == "complete":
            rows.append(_classify_settled_record(bs, st, store, root, r,
                                                  subject, who))
            continue

        # active with no bm_stall finding, or parked/adopted: nothing
        # observed contradicts the persisted state.
        rows.append(_row(VALID, "record", subject,
                         "state=%s, no drift observed" % r["state"], "",
                         category="no-drift", **who))
    return rows


def _classify_settled_record(bs, st, store, root, r, subject, who=None):
    who = who or {}
    """Case 5: a completed fence whose claimed files were edited AFTER
    completion. `records.updated_at` is the settlement timestamp (the last
    write `transition()` made, per tools/bm_store.py:12268); a claimed
    path's own mtime newer than that means the tree moved on after the
    record said it was finished."""
    settled_at = bs.parse_iso_stamp(r["updated_at"])
    if settled_at is None:
        return _row(NO_DATA, "record", subject,
                   "completed but its own updated_at %r does not parse "
                   "as this store's timestamp format" % r["updated_at"], "",
                   category="updated-at-unparsable", **who)
    for path in st._claims_for(bs, store, r["lifecycle_uuid"]):
        full = os.path.join(root, path)
        try:
            mtime = datetime.datetime.fromtimestamp(
                os.path.getmtime(full), tz=datetime.timezone.utc)
        except OSError:  # sbe: allow-silent a missing/unreadable claimed path has no mtime to compare; other claimed paths still checked
            continue
        # Truncated to whole seconds before comparing: the store's own
        # timestamp format (tools/bm_store.py's _ISO_STAMP_FORMAT) carries
        # no fractional seconds, so an mtime a few hundred milliseconds
        # into the SAME wall-clock second as settlement is not evidence of
        # "afterward," only of a comparison finer than the store can make.
        if mtime.replace(microsecond=0) > settled_at:
            return _row(
                STALE, "record", subject,
                "completed at %s but %s was modified at %s, afterward"
                % (r["updated_at"], path, mtime.isoformat()),
                "re-verify this claim's evidence against the current tree "
                "before trusting it, or re-open the work",
                category="settled-then-edited", **who)
    return _row(VALID, "record", subject,
               "complete, no later edit observed on its claimed paths", "",
               category="settled-clean", **who)


# ---------------------------------------------------------------------------
# The Full-Auto controller chain: cases 3, 9, 10. Two helpers, one per
# controller_units.status this pass has anything to say about, kept
# separate rather than one branching function: an open dispatch and a
# settled unit are different questions (is anything happening versus was
# the outcome actually checked) with independent fixtures in
# tools/test_bm_reconcile.py, and reading one never requires the other.
# ---------------------------------------------------------------------------

def _stamp(row, anchor, ref):
    """Anchor a row built without owner facts (a unit, a push-state or
    integrity finding) and recompute its fingerprint over that anchor."""
    row["anchor"] = anchor or ""
    row["observed_ref"] = ref or ""
    row["fingerprint"] = fingerprint(row["kind"], row["anchor"],
                                     row["owner_session"], row["class"],
                                     row["category"])
    return row


def classify_controller_units(bs, store, ref=""):
    rows = []
    units = bs._exec(
        store, "SELECT unit_id, run_id, status, fence_uuid FROM "
              "controller_units ORDER BY unit_id").fetchall()
    for u in units:
        dispatches = bs._exec(
            store, "SELECT attempt, status, created_at, resulted_at FROM "
                  "controller_dispatches WHERE unit_id=? ORDER BY attempt",
            (u["unit_id"],)).fetchall()
        if u["status"] == _UNIT_STATUS_OPEN_DISPATCH:
            row = _classify_open_dispatch(u["unit_id"], dispatches)
        elif u["status"] == _UNIT_STATUS_SETTLED:
            row = _classify_settled_unit(u["unit_id"], dispatches)
        else:
            row = None
        if row is not None:
            # A unit's fence row is the record a note can anchor to; a
            # unit with no fence yet has no anchor and `file` says so.
            anchor = "record:" + u["fence_uuid"] if u["fence_uuid"] else ""
            rows.append(_stamp(row, anchor, ref))
    return rows


def _classify_open_dispatch(unit_id, dispatches):
    """Case 3: a dispatch attempt was started and nothing has been heard
    back since. Only the newest attempt matters here (an older attempt
    still showing the open status would mean a later attempt superseded
    it, which is a different, already-closed chapter for this unit)."""
    last = dispatches[-1] if dispatches else None
    if last is None or last["status"] != _DISPATCH_STATUS_OPEN:
        return None
    return _row(
        NO_DATA, "controller-unit", unit_id,
        "dispatch attempt %d was started but no outcome (a result or a "
        "review of one) has been recorded for it" % last["attempt"],
        "check whether the dispatched worker is still running; if it "
        "crashed, record what actually happened (or re-dispatch a new "
        "attempt) once you know", category="open-dispatch-no-outcome")


def _classify_settled_unit(unit_id, dispatches):
    """Cases 9 and 10: a unit whose status claims the work is finished.
    Finished-with-no-review-ever (case 9) and finished-but-the-review-
    predates-a-later-attempt (case 10) are the two ways that claim can be
    hollow; anything else about a settled unit is unremarkable."""
    reviewed = [d for d in dispatches
               if d["status"] == _DISPATCH_STATUS_PASSED_REVIEW]
    if not reviewed:
        return _row(
            NO_DATA, "controller-unit", unit_id,
            "this unit's status says the work is finished, but no "
            "dispatch attempt for it was ever reviewed and passed: the "
            "checker's own output is absent",
            "run the unit's done_check and verifier by hand against its "
            "checkpoint before trusting that this unit is really green",
            category="settled-never-reviewed")
    newest_reviewed = max(d["attempt"] for d in reviewed)
    later = [d for d in dispatches if d["attempt"] > newest_reviewed]
    if later:
        return _row(
            STALE, "controller-unit", unit_id,
            "attempt %d passed review, but attempt %d exists for the "
            "same unit after it: the passing verdict predates this "
            "unit's own final edit"
            % (newest_reviewed, max(d["attempt"] for d in later)),
            "re-run the checker against the latest attempt; the recorded "
            "verdict no longer describes this unit's current result",
            category="review-predates-later-attempt")
    return _row(
        VALID, "controller-unit", unit_id,
        "finished, attempt %d passed review and no later attempt exists"
        % newest_reviewed, "", category="settled-reviewed")


# ---------------------------------------------------------------------------
# Store-internal consistency: reuse bm_store.verify() wholesale (case 11)
# rather than re-deriving what it already checks.
# ---------------------------------------------------------------------------

def classify_store_integrity(bs, root, ref=""):
    problems = bs.verify(root)
    return [_stamp(_row(CONFLICT, "store-integrity", "", p,
                        "run `%s verify` for detail; a targeted fix (or, "
                        "for a quarantined store, `%s init "
                        "--acknowledge-quarantine`) may be needed depending "
                        "on the problem" % (bs._cmd(), bs._cmd()),
                        category="verify-problem"),
                   "file:" + os.path.join(bs.STORE_DIRNAME, "store.sqlite3"),
                   ref)
            for p in problems]


# ---------------------------------------------------------------------------
# The whole pass.
# ---------------------------------------------------------------------------

def reconcile(bs, st, root, now=None, stale_after_seconds=None,
             pid_hints=None, session_id="", fh=None):
    """Runs the whole read-only pass and returns a list of row dicts.
    Never raises: a store this cannot open yields exactly one NO-DATA row
    naming why (case 13) for the store half, and every other failure
    surfaces the same way at the CLI layer (see main() below), never as a
    crash. The git half (push state, unpushed hook dependencies) needs no
    store and runs either way: a linked worktree with no store of its own
    still has a checkout worth classifying.

    `session_id` is the harness id of the session asking (the hook
    payload's session_id); the label derived from its token file, if one
    exists, is the only thing that ever routes a row `mine`. `fh` is the
    loaded bm_fence_hook module (None when it will not load: nothing is
    mine then, which is the safe answer).

    Deterministic, idempotent, bounded by construction (Z2.5): this
    function writes nothing, so calling it twice against an unchanged
    store reads the exact same reality both times. See
    docs/RECOVERY-TRUTH.md's Z2.5 section for why no pass-count loop was
    needed this pass."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    stale_after_seconds = (stale_after_seconds if stale_after_seconds
                           is not None else st.DEFAULT_STALE_AFTER_SECONDS)
    pid_hints = pid_hints or {}
    ref = observed_ref(root)
    my_label = own_label(fh, root, session_id)
    label_prefix = getattr(fh, "LABEL_PREFIX", _LABEL_PREFIX_FALLBACK)
    rows = []
    try:
        store = bs.ReadOnlyStore(root)
    except Exception as exc:
        rows.append(_stamp(_row(
            NO_DATA, "store", root,
            "the store could not be read: %s: %s" % (type(exc).__name__, exc),
            "inspect it by hand; a writable command is the only thing "
            "that may quarantine a damaged store", category="store-unreadable"),
            "", ref))
        store = None
    if store is not None:
        try:
            trailers = trailer_commits(root) if ref else []
            rows.extend(classify_records(bs, st, store, root, now,
                                         stale_after_seconds, pid_hints,
                                         my_label=my_label,
                                         label_prefix=label_prefix, ref=ref,
                                         trailers=trailers))
            rows.extend(classify_controller_units(bs, store, ref=ref))
            rows.extend(classify_store_integrity(bs, root, ref=ref))
        finally:
            store.close()
    rows.extend(_stamp(r, "file:.", ref) for r in classify_push_state(root))
    rows.extend(classify_unpushed_hook_deps(root, ref=ref))
    return rows


# ---------------------------------------------------------------------------
# CLI, mirroring tools/bm_stall.py's own _parse_argv/_run/main shape.
# ---------------------------------------------------------------------------

def _out(msg=""):
    sys.stdout.write("%s\n" % msg)


def _err(msg):
    sys.stderr.write("%s\n" % msg)


def resolve_project_root(bs, explicit_root):
    if explicit_root:
        candidate = os.path.realpath(os.path.expanduser(explicit_root))
        if not os.path.isdir(candidate):
            return None, "no such directory: %s" % candidate
        return candidate, None
    root, _source = bs.resolve_root()
    if not root:
        return None, ("nothing anchors a BrotherMode project here (no "
                      "BROTHERMODE_ROOT, no marker directory, no git repo "
                      "found); pass --root explicitly")
    return root, None


def _parse_pid_hint(text):
    if "=" not in text:
        raise ValueError("--pid-hint needs SESSION_ID=PID, got %r" % text)
    session_id, pid_text = text.split("=", 1)
    return session_id, int(pid_text)


def _parse_argv(argv):
    args = list(argv)
    verb = "sweep"
    if args and not args[0].startswith("--"):
        verb = args.pop(0)
    if verb not in _VERBS:
        return None, None, "bm_reconcile: unknown verb: %s" % verb
    kv = {"root": None, "now": None, "stale_seconds": None, "json": False,
          "pid_hints": {}, "session_id": ""}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--root":
            if i + 1 >= len(args):
                return None, None, "bm_reconcile: --root requires a value"
            kv["root"] = args[i + 1]; i += 2
        elif arg == "--now":
            if i + 1 >= len(args):
                return None, None, "bm_reconcile: --now requires a value"
            kv["now"] = args[i + 1]; i += 2
        elif arg == "--stale-seconds":
            if i + 1 >= len(args):
                return None, None, ("bm_reconcile: --stale-seconds "
                                    "requires a value")
            try:
                kv["stale_seconds"] = int(args[i + 1])
            except ValueError:  # sbe: allow-silent not silent: returns a named error message, matches only because the tuple starts with None
                return None, None, ("bm_reconcile: --stale-seconds must be "
                                    "an integer, got %r" % args[i + 1])
            i += 2
        elif arg == "--pid-hint":
            if i + 1 >= len(args):
                return None, None, "bm_reconcile: --pid-hint requires a value"
            try:
                session_id, pid = _parse_pid_hint(args[i + 1])
            except ValueError as exc:
                return None, None, "bm_reconcile: %s" % exc
            kv["pid_hints"][session_id] = pid; i += 2
        elif arg == "--session-id":
            if i + 1 >= len(args):
                return None, None, ("bm_reconcile: --session-id requires "
                                    "a value")
            kv["session_id"] = args[i + 1].strip(); i += 2
        elif arg == "--json":
            kv["json"] = True; i += 1
        else:
            return None, None, "bm_reconcile: unknown argument: %s" % arg
    return verb, kv, None


def _render_text(rows):
    if not rows:
        return ("bm_reconcile: 0 row(s). Nothing observable contradicts "
                "what the store persists right now.")
    lines = ["bm_reconcile: %d row(s)" % len(rows)]
    for r in rows:
        subject = "%s %s" % (r["kind"], r["subject"]) if r["subject"] \
            else r["kind"]
        lines.append("%s | %s | %s | %s | route %s (owner %s, %s, %s)"
                     % (r["class"], subject, r["reason"],
                        r["next_action"] or "(no action proposed)",
                        r["route"], r["owner_session"] or "(none)",
                        r["owner_confidence"],
                        r["owner_liveness"] or "liveness n/a"))
    return "\n".join(lines)


def _pass(kv):
    """Everything both verbs share: load the modules, resolve the root and
    the clock, run the read-only pass. Returns (rows, ctx, exit_code);
    rows is None when something refused, with the reason already printed."""
    bs, load_err = _load_bm_store()
    if bs is None:
        _out("NO-DATA: could not load bm_store.py (%s)" % load_err)
        return None, None, 2
    st, load_err = _load_bm_stall()
    if st is None:
        _out("NO-DATA: could not load bm_stall.py (%s)" % load_err)
        return None, None, 2
    # The fence hook is optional here: without it nothing can be mine,
    # which is the fail-closed answer, not a refusal.
    fh, _fh_err = _load_bm_fence_hook()
    root, reason = resolve_project_root(bs, kv["root"])
    if root is None:
        _out("NO-DATA: %s" % reason)
        return None, None, 2
    now = datetime.datetime.now(datetime.timezone.utc)
    if kv["now"]:
        parsed_now = bs.parse_iso_stamp(kv["now"])
        if parsed_now is None:
            _out("NO-DATA: --now %r is not in the store's own timestamp "
                "format (%s)" % (kv["now"], bs._ISO_STAMP_FORMAT))
            return None, None, 2
        now = parsed_now
    stale_seconds = (kv["stale_seconds"] if kv["stale_seconds"] is not None
                     else st.DEFAULT_STALE_AFTER_SECONDS)
    rows = reconcile(bs, st, root, now=now, stale_after_seconds=stale_seconds,
                     pid_hints=kv["pid_hints"], session_id=kv["session_id"],
                     fh=fh)
    ctx = {"bs": bs, "st": st, "fh": fh, "root": root,
           "my_label": own_label(fh, root, kv["session_id"]),
           "session_id": kv["session_id"]}
    return rows, ctx, 0


def cmd_sweep(kv):
    rows, _ctx, code = _pass(kv)
    if rows is None:
        return code
    if kv["json"]:
        _out(json.dumps({"rows": rows}, indent=2, sort_keys=True))
    else:
        _out(_render_text(rows))
    return 1 if any(r["class"] != VALID for r in rows) else 0


# ---------------------------------------------------------------------------
# file: the ONE write. See the module docstring's THE ONE WRITE section.
# ---------------------------------------------------------------------------

#: What a filed finding is, in the notes table's own vocabulary. An alert
#: (the kind a gate pack renders beside the thing it is about) at severity
#: warning: visible everywhere a note is, and it refuses nothing; only a
#: critical alert refuses an approval, and turning a radar reading into a
#: gate would be a second decision this verb was not given.
NOTE_KIND = "alert"
NOTE_SEVERITY = "warning"
NOTE_AUTHOR = "bm_reconcile"
NOTE_AUTHOR_KIND = "assistant"


def note_anchor(bs, root, anchor):
    """(anchor_type, anchor_key, why_not) for a row's anchor string. A
    record anchor is the full lifecycle uuid the pass itself read minutes
    ago; a file anchor goes through canonicalize_path exactly as add_note
    sends one, so the stored key equals what any other note about the same
    file stores. Anything else has no anchor a note can hold."""
    kind, _sep, key = (anchor or "").partition(":")
    if kind == "record" and key:
        return "record", key, ""
    if kind == "file" and key:
        try:
            return "file", bs.canonicalize_path(root, key), ""
        except Exception as exc:
            return None, "", "file anchor %r refused: %s" % (key, exc)
    return None, "", "no anchor a note can hold (%r)" % (anchor or "")


def already_filed(bs, store, fp):
    """True when an OPEN note carries this fingerprint. A resolved note is
    an answered question; the same finding observed again after that is a
    new occurrence and files again (checker finding 2, 2026-09-17: the
    earlier any-note rule silently dropped every recurrence). The
    fingerprint is hex, so it carries no LIKE wildcard; the key it sits
    under is the JSON key sort_keys puts it at."""
    needle = '%"fingerprint": "' + fp + '"%'
    row = bs._exec(store, "SELECT 1 FROM notes WHERE kind=? AND resolved_at "
                          "IS NULL AND body LIKE ? LIMIT 1",
                   (NOTE_KIND, needle)).fetchone()
    return row is not None


def file_findings(bs, store, root, rows, filer):
    """Persist every non-VALID row as a note, once per fingerprint. Each
    row is its own BEGIN IMMEDIATE (store._transaction): the read and the
    insert sit under one write lock, so two filers racing on the same
    fingerprint produce one note and one "already filed", never two
    notes. Returns (filed, already, unfileable) where unfileable is a
    list of (kind, subject, why) this verb could not anchor; it says so
    rather than inventing an anchor."""
    filed = 0
    already = 0
    unfileable = []
    for r in rows:
        if r["class"] == VALID:
            continue
        atype, key, why = note_anchor(bs, root, r["anchor"])
        if atype is None:
            unfileable.append((r["kind"], r["subject"], why))
            continue
        body = json.dumps(r, sort_keys=True)
        with store._transaction():
            if already_filed(bs, store, r["fingerprint"]):
                already += 1
                continue
            # The same INSERT add_note makes, inlined so it shares this
            # transaction with the read above (add_note opens its own).
            bs._exec(store,
                     "INSERT INTO notes (note_uuid, kind, severity, author, "
                     "author_kind, anchor_type, anchor_key, anchor_line, "
                     "body, session_id, created_at, anchor_line_hash) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (uuid.uuid4().hex, NOTE_KIND, NOTE_SEVERITY, NOTE_AUTHOR,
                      NOTE_AUTHOR_KIND, atype, key, None,
                      bs.redact_text(body), filer or "", bs.now_iso(), ""))
        filed += 1
    return filed, already, unfileable


def cmd_file(kv):
    rows, ctx, code = _pass(kv)
    if rows is None:
        return code
    bs = ctx["bs"]
    filer = ctx["my_label"] or ctx["session_id"]
    try:
        store = bs.Store(ctx["root"], create=False)
    except Exception as exc:
        _out("NO-DATA: file: the store could not be opened for writing: "
             "%s: %s" % (type(exc).__name__, exc))
        return 2
    try:
        filed, already, unfileable = file_findings(bs, store, ctx["root"],
                                                  rows, filer)
    finally:
        store.close()
    if kv["json"]:
        _out(json.dumps({"filed": filed, "already_filed": already,
                         "unfileable": [{"kind": k, "subject": s, "why": w}
                                        for k, s, w in unfileable],
                         "rows": rows}, indent=2, sort_keys=True))
    else:
        _out("bm_reconcile file: %d filed, %d already filed, %d unfileable"
             % (filed, already, len(unfileable)))
        for k, s, w in unfileable:
            _out("  unfileable | %s %s | %s" % (k, s, w))
    return 0


_VERBS = {
    "sweep": cmd_sweep,
    "file": cmd_file,
}


def _run(argv):
    verb, kv, err = _parse_argv(argv)
    if err:
        _err(err)
        return 2
    return _VERBS[verb](kv)


def main(argv):
    try:
        return _run(argv)
    except Exception as exc:
        _out("NO-DATA: reconcile: %s: %s" % (type(exc).__name__, exc))
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
