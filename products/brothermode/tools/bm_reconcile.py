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

Python 3.9, standard library only besides `subprocess` (git, read only).
No em or en dashes anywhere in this file or its output.

Usage:
  python3 tools/bm_reconcile.py [sweep] [--root PATH] [--now ISO8601]
    [--stale-seconds N] [--pid-hint SESSION_ID=PID] [--json]
"""

import datetime
import importlib.util
import json
import os
import subprocess
import sys

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


def _row(cls, kind, subject, reason, next_action=""):
    return {"class": cls, "kind": kind, "subject": subject or "",
            "reason": reason, "next_action": next_action or ""}


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
    """The real repository root git itself would use, or None when `root`
    is not inside a git repository (or git is unavailable). A push-state
    finding is meaningless outside a git repo, so callers use this to
    decide whether to say anything at all."""
    result = _run_git(root, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def classify_push_state(root):
    """Case 14: 'external push or release state unobservable.' A
    BrotherMode root with no git repo at all has nothing to say here (most
    of this suite's own fixtures are exactly that, and it is a legitimate,
    ordinary state, not a fault); one WITH a repo but no upstream tracking
    branch cannot answer whether the work has shipped anywhere, which is
    NO-DATA, named."""
    if _git_toplevel(root) is None:
        return []
    result = _run_git(root, "rev-parse", "--abbrev-ref",
                      "--symbolic-full-name", "@{u}")
    if result.returncode == 127:
        return [_row(NO_DATA, "push-state", root,
                     "external push/release state is unobservable: git "
                     "is not available on PATH", "install git, or check "
                     "the remote by hand")]
    if result.returncode != 0:
        return [_row(NO_DATA, "push-state", root,
                     "external push/release state is unobservable: no "
                     "upstream tracking branch is configured for the "
                     "current branch",
                     "configure one with `git push -u origin <branch>`, "
                     "or check the remote by hand")]
    return [_row(VALID, "push-state", root,
                 "tracks %s" % result.stdout.strip(), "")]


# ---------------------------------------------------------------------------
# records / claims / transitions / provisional_records: reuse bm_stall's
# sweep() for the dead-owner half, add the settle-then-edit comparison
# sweep() does not do.
# ---------------------------------------------------------------------------

def classify_records(bs, st, store, root, now, stale_after_seconds,
                     pid_hints):
    findings = st.sweep(bs, store, now=now,
                        stale_after_seconds=stale_after_seconds,
                        pid_hints=pid_hints)
    by_uuid = {}
    for f in findings:
        by_uuid.setdefault(f["lifecycle_uuid"], []).append(f)

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

        if st.DUPLICATE_FENCE_LINES in kinds:
            f = next(f for f in fs if f["kind"] == st.DUPLICATE_FENCE_LINES)
            rows.append(_row(CONFLICT, "record", subject, f["message"],
                             f["actions"][0]["command"] if f["actions"]
                             else ""))
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
                             if f["actions"] else ""))
            continue
        if kinds & {st.STALE_FENCE, st.DEAD_WATCHDOG}:
            f = next(f for f in fs
                    if f["kind"] in (st.STALE_FENCE, st.DEAD_WATCHDOG))
            rows.append(_row(STALE, "record", subject, f["message"],
                             f["actions"][0]["command"] if f["actions"]
                             else ""))
            continue

        if r["state"] == "complete":
            rows.append(_classify_settled_record(bs, st, store, root, r,
                                                  subject))
            continue

        # active with no bm_stall finding, or parked/adopted: nothing
        # observed contradicts the persisted state.
        rows.append(_row(VALID, "record", subject,
                         "state=%s, no drift observed" % r["state"], ""))
    return rows


def _classify_settled_record(bs, st, store, root, r, subject):
    """Case 5: a completed fence whose claimed files were edited AFTER
    completion. `records.updated_at` is the settlement timestamp (the last
    write `transition()` made, per tools/bm_store.py:12268); a claimed
    path's own mtime newer than that means the tree moved on after the
    record said it was finished."""
    settled_at = bs.parse_iso_stamp(r["updated_at"])
    if settled_at is None:
        return _row(NO_DATA, "record", subject,
                   "completed but its own updated_at %r does not parse "
                   "as this store's timestamp format" % r["updated_at"], "")
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
                "before trusting it, or re-open the work")
    return _row(VALID, "record", subject,
               "complete, no later edit observed on its claimed paths", "")


# ---------------------------------------------------------------------------
# The Full-Auto controller chain: cases 3, 9, 10. Two helpers, one per
# controller_units.status this pass has anything to say about, kept
# separate rather than one branching function: an open dispatch and a
# settled unit are different questions (is anything happening versus was
# the outcome actually checked) with independent fixtures in
# tools/test_bm_reconcile.py, and reading one never requires the other.
# ---------------------------------------------------------------------------

def classify_controller_units(bs, store):
    rows = []
    units = bs._exec(
        store, "SELECT unit_id, run_id, status FROM controller_units "
              "ORDER BY unit_id").fetchall()
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
            rows.append(row)
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
        "attempt) once you know")


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
            "checkpoint before trusting that this unit is really green")
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
            "verdict no longer describes this unit's current result")
    return _row(
        VALID, "controller-unit", unit_id,
        "finished, attempt %d passed review and no later attempt exists"
        % newest_reviewed, "")


# ---------------------------------------------------------------------------
# Store-internal consistency: reuse bm_store.verify() wholesale (case 11)
# rather than re-deriving what it already checks.
# ---------------------------------------------------------------------------

def classify_store_integrity(bs, root):
    problems = bs.verify(root)
    return [_row(CONFLICT, "store-integrity", "", p,
                 "run `%s verify` for detail; a targeted fix (or, for a "
                 "quarantined store, `%s init --acknowledge-quarantine`) "
                 "may be needed depending on the problem"
                 % (bs._cmd(), bs._cmd()))
            for p in problems]


# ---------------------------------------------------------------------------
# The whole pass.
# ---------------------------------------------------------------------------

def reconcile(bs, st, root, now=None, stale_after_seconds=None,
             pid_hints=None):
    """Runs the whole read-only pass and returns a list of row dicts.
    Never raises: a store this cannot open yields exactly one NO-DATA row
    naming why (case 13), and every other failure surfaces the same way
    at the CLI layer (see main() below), never as a crash.

    Deterministic, idempotent, bounded by construction (Z2.5): this
    function writes nothing, so calling it twice against an unchanged
    store reads the exact same reality both times. See
    docs/RECOVERY-TRUTH.md's Z2.5 section for why no pass-count loop was
    needed this pass."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    stale_after_seconds = (stale_after_seconds if stale_after_seconds
                           is not None else st.DEFAULT_STALE_AFTER_SECONDS)
    pid_hints = pid_hints or {}
    try:
        store = bs.ReadOnlyStore(root)
    except Exception as exc:
        return [_row(
            NO_DATA, "store", root,
            "the store could not be read: %s: %s" % (type(exc).__name__, exc),
            "inspect it by hand; a writable command is the only thing "
            "that may quarantine a damaged store")]
    try:
        rows = []
        rows.extend(classify_records(bs, st, store, root, now,
                                     stale_after_seconds, pid_hints))
        rows.extend(classify_controller_units(bs, store))
        rows.extend(classify_store_integrity(bs, root))
        rows.extend(classify_push_state(root))
        return rows
    finally:
        store.close()


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
    if verb != "sweep":
        return None, None, "bm_reconcile: unknown verb: %s" % verb
    kv = {"root": None, "now": None, "stale_seconds": None, "json": False,
          "pid_hints": {}}
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
        lines.append("%s | %s | %s | %s"
                     % (r["class"], subject, r["reason"],
                        r["next_action"] or "(no action proposed)"))
    return "\n".join(lines)


def _run(argv):
    verb, kv, err = _parse_argv(argv)
    if err:
        _err(err)
        return 2
    bs, load_err = _load_bm_store()
    if bs is None:
        _out("NO-DATA: could not load bm_store.py (%s)" % load_err)
        return 2
    st, load_err = _load_bm_stall()
    if st is None:
        _out("NO-DATA: could not load bm_stall.py (%s)" % load_err)
        return 2
    root, reason = resolve_project_root(bs, kv["root"])
    if root is None:
        _out("NO-DATA: %s" % reason)
        return 2
    now = datetime.datetime.now(datetime.timezone.utc)
    if kv["now"]:
        parsed_now = bs.parse_iso_stamp(kv["now"])
        if parsed_now is None:
            _out("NO-DATA: --now %r is not in the store's own timestamp "
                "format (%s)" % (kv["now"], bs._ISO_STAMP_FORMAT))
            return 2
        now = parsed_now
    stale_seconds = (kv["stale_seconds"] if kv["stale_seconds"] is not None
                     else st.DEFAULT_STALE_AFTER_SECONDS)
    rows = reconcile(bs, st, root, now=now, stale_after_seconds=stale_seconds,
                     pid_hints=kv["pid_hints"])
    if kv["json"]:
        _out(json.dumps({"rows": rows}, indent=2, sort_keys=True))
    else:
        _out(_render_text(rows))
    return 1 if any(r["class"] != VALID for r in rows) else 0


def main(argv):
    try:
        return _run(argv)
    except Exception as exc:
        _out("NO-DATA: reconcile: %s: %s" % (type(exc).__name__, exc))
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
