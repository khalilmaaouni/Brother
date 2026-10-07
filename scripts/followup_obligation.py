#!/usr/bin/env python3
"""followup_obligation: a follow-up obligation (a trigger, its owner, its
expiry and the authorization behind it) persisted as rows on the run's
existing journal, and folded back from it.

journal.py stays the one writer and reader: this module calls journal.append
and journal.read and never opens journal.jsonl itself. An obligation's
identity is obligation_id(trigger), a sha256 over TRIGGER_KEYS only, so a
decorative extra key never creates a second obligation and a restarted
session re-persisting its own triggers takes the idempotent path.

A REMINDER IS NEVER A GRANT TO SEND. An authorization whose scope names any
of FORBIDDEN_SCOPES is refused at validation, and a row planted with one is
read back as malformed rather than folded.

A LINE THAT WOULD BE TRUNCATED IS NOT WRITTEN. journal._line shrinks a
payload over the atomicity bound into a NO-DATA stub; an obligation row so
shrunk could never be read back, so persist() refuses it (returns None)
instead of appending a row the fold would only count as malformed.

Python 3.9, standard library only. No network.
"""
import datetime
import hashlib
import json
import os
import sys
from typing import Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import journal  # noqa: E402

OBLIGATION = "followup.obligation"
NOTIFIED = "followup.notified"
CANCELLED = "followup.cancelled"
COMPLETED = "followup.completed"
TRIGGER_KEYS = ("kind", "subject", "due_at", "owner")
FORBIDDEN_SCOPES = ("external-send", "third-party-send", "publish")
AUTHORIZATION_KEYS = ("granted_by", "scope", "action_ref")


def _nonempty_str(value):
    return isinstance(value, str) and bool(value.strip())


def obligation_id(trigger: dict) -> str:
    """sha256 hex over json.dumps({k: trigger[k] for k in TRIGGER_KEYS},
    sort_keys=True). Pure; a trigger missing a key raises ValueError naming
    it, and a trigger that is not a dict or does not serialize raises
    ValueError too."""
    if not isinstance(trigger, dict):
        raise ValueError("trigger must be a dict, got %s"
                         % type(trigger).__name__)
    for key in TRIGGER_KEYS:
        if key not in trigger:
            raise ValueError("trigger is missing %s" % key)
    try:
        text = json.dumps({k: trigger[k] for k in TRIGGER_KEYS},
                          sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("trigger does not serialize (%s)" % exc)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _utc_time(name, value):
    """The aware datetime `value` names, or ValueError: ISO 8601 with a UTC
    offset only; a trailing Z is read as +00:00 (Python 3.9's fromisoformat
    does not accept it)."""
    if not _nonempty_str(value):
        raise ValueError("%s must be a non-empty ISO 8601 string" % name)
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.datetime.fromisoformat(text)
    except ValueError:
        raise ValueError("%s is not ISO 8601: %r" % (name, value))
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("%s has no UTC offset: %r" % (name, value))
    return moment


def validate_trigger(trigger: dict, owner: str, expires_at: str,
                     authorization: dict) -> None:
    """Raises ValueError naming the first problem: a TRIGGER_KEYS key missing
    or empty; due_at or expires_at not ISO 8601 with a UTC offset; expires_at
    before due_at; owner empty; authorization missing granted_by, scope (a
    list of str) or action_ref; any scope item in FORBIDDEN_SCOPES (a reminder
    is never a grant to send outside the machine)."""
    if not isinstance(trigger, dict):
        raise ValueError("trigger must be a dict, got %s"
                         % type(trigger).__name__)
    for key in TRIGGER_KEYS:
        if key not in trigger:
            raise ValueError("trigger is missing %s" % key)
        if not _nonempty_str(trigger[key]):
            raise ValueError("trigger %s is empty or not a string" % key)
    obligation_id(trigger)
    due = _utc_time("due_at", trigger["due_at"])
    expires = _utc_time("expires_at", expires_at)
    if expires < due:
        raise ValueError("expires_at %s is before due_at %s"
                         % (expires_at, trigger["due_at"]))
    if not _nonempty_str(owner):
        raise ValueError("owner is empty or not a string")
    if not isinstance(authorization, dict):
        raise ValueError("authorization must be a dict, got %s"
                         % type(authorization).__name__)
    for key in AUTHORIZATION_KEYS:
        if key not in authorization:
            raise ValueError("authorization is missing %s" % key)
    if not _nonempty_str(authorization["granted_by"]):
        raise ValueError("authorization granted_by is empty or not a string")
    scope = authorization["scope"]
    if not isinstance(scope, list) or not all(
            isinstance(item, str) for item in scope):
        raise ValueError("authorization scope must be a list of str")
    if not _nonempty_str(authorization["action_ref"]):
        raise ValueError("authorization action_ref is empty or not a string")
    for item in scope:
        # Case and padding folded so "External-Send " cannot slip past.
        if item.strip().lower() in FORBIDDEN_SCOPES:
            raise ValueError("authorization scope %r is forbidden: a "
                             "reminder is never a grant to send" % item)


def _fits_one_line(run_dir, payload):
    """True when journal._line would keep `payload` whole for this run. The
    probe event carries the longest identity values append() can write."""
    probe = {
        "event_id": "0" * 32,
        "parent_ids": [],
        "run_id": os.path.basename(os.path.normpath(run_dir)),
        "session_id": None,
        "unit_id": None,
        "at": "0000-00-00T00:00:00.000000+00:00",
        "type": OBLIGATION,
        "payload": payload,
    }
    try:
        line = journal._line(probe)
        return json.loads(line.decode("utf-8")).get("payload") == payload
    except (TypeError, ValueError):
        return False


def persist(run_dir: str, trigger: dict, owner: str, expires_at: str,
            authorization: dict) -> Optional[str]:
    """validate_trigger, then read_obligations: when an OBLIGATION row with
    this obligation_id exists its event_id is returned and NOTHING is
    appended (idempotent); else one journal row typed OBLIGATION with payload
    {obligation_id, trigger, owner, expires_at, authorization} is appended
    and its event_id returned. None when run_dir is empty, the payload would
    not fit one atomic journal line, or the journal refuses the append.
    Never raises past validation."""
    validate_trigger(trigger, owner, expires_at, authorization)
    run_dir = str(run_dir or "").strip()
    if not run_dir:
        return None
    oid = obligation_id(trigger)
    folded = read_obligations(run_dir)
    if folded is not None:
        known = folded.get(oid)
        if isinstance(known, dict):
            return known["event_id"]
    payload = {
        "obligation_id": oid,
        "trigger": trigger,
        "owner": owner,
        "expires_at": expires_at,
        "authorization": authorization,
    }
    if not _fits_one_line(run_dir, payload):
        sys.stderr.write("followup_obligation: obligation %s is too large for "
                         "one atomic journal line; not written\n" % oid)
        return None
    # A root event: an obligation has no causal parent in this run, and a
    # parent id would spend 35 of the line's few hundred bytes.
    return journal.append(run_dir, OBLIGATION, payload=payload)


def _obligation_from(payload):
    """The record an OBLIGATION payload folds to, or None when malformed."""
    if not isinstance(payload, dict):
        return None
    try:
        validate_trigger(payload.get("trigger"), payload.get("owner"),
                         payload.get("expires_at"),
                         payload.get("authorization"))
        oid = obligation_id(payload["trigger"])
    except ValueError:
        return None
    if payload.get("obligation_id") != oid:
        return None
    return oid


def read_obligations(run_dir: str) -> Optional[dict]:
    """obligation_id -> {'event_id', 'trigger', 'owner', 'expires_at',
    'authorization', 'notified': [event ids], 'cancelled': bool,
    'completed': bool, 'duplicates': int}, folded from OBLIGATION, NOTIFIED,
    CANCELLED and COMPLETED rows in file order. The first OBLIGATION row for
    an id wins; later duplicates are counted on the record and in the
    top-level 'duplicates' int. A row with a malformed payload (including a
    NOTIFIED, CANCELLED or COMPLETED row naming no folded obligation) is
    skipped and counted in the top-level 'malformed' int. None when the run
    has no journal (journal.read returned None)."""
    rows = journal.read(run_dir)
    if rows is None:
        return None
    out = {"duplicates": 0, "malformed": 0}
    marks = (NOTIFIED, CANCELLED, COMPLETED)
    for row in rows:
        if not isinstance(row, dict):
            continue
        kind = row.get("type")
        if kind != OBLIGATION and kind not in marks:
            continue
        payload = row.get("payload")
        event_id = row.get("event_id")
        if not _nonempty_str(event_id):
            out["malformed"] += 1
            continue
        if kind == OBLIGATION:
            oid = _obligation_from(payload)
            if oid is None:
                out["malformed"] += 1
                continue
            if oid in out:
                out[oid]["duplicates"] += 1
                out["duplicates"] += 1
                continue
            out[oid] = {
                "event_id": event_id,
                "trigger": payload["trigger"],
                "owner": payload["owner"],
                "expires_at": payload["expires_at"],
                "authorization": payload["authorization"],
                "notified": [],
                "cancelled": False,
                "completed": False,
                "duplicates": 0,
            }
            continue
        oid = payload.get("obligation_id") if isinstance(payload, dict) \
            else None
        record = out.get(oid) if isinstance(oid, str) else None
        if not isinstance(record, dict):
            out["malformed"] += 1
            continue
        if kind == NOTIFIED:
            record["notified"].append(event_id)
        elif kind == CANCELLED:
            record["cancelled"] = True
        else:
            record["completed"] = True
    return out


SKIP_REASONS = ("cancelled", "completed", "notified", "expired", "not-due",
                "quiet", "malformed")
MARK_TYPES = (CANCELLED, COMPLETED)


def _moment(value):
    """The aware datetime `value` names, or None when it does not parse."""
    try:
        return _utc_time("time", value)
    except ValueError:
        return None


def _quiet_windows(quiet):
    """[(start, end)] from `quiet`, or None when any part is malformed: a
    window is a two item list or tuple of ISO 8601 times, start before end."""
    if quiet is None:
        return []
    if not isinstance(quiet, (list, tuple)):
        return None
    windows = []
    for pair in quiet:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            return None
        start, end = _moment(pair[0]), _moment(pair[1])
        if start is None or end is None or not start < end:
            return None
        windows.append((start, end))
    return windows


def reconcile(state: dict, now_utc: str,
              quiet: Optional[list] = None) -> Tuple[str, str]:
    """('notify' | 'skip', reason) for ONE folded obligation from
    read_obligations. skip reasons, tested in this order, first wins:
    'cancelled', 'completed', 'notified' (any notified event id exists),
    'expired' (expires_at at or before now_utc), 'not-due' (due_at after
    now_utc), 'quiet' (now_utc inside any [start, end) pair in quiet, ISO 8601
    UTC), 'malformed' (a date that does not parse, including now_utc itself).
    'notify' with 'due' otherwise. Pure.

    UNKNOWN IS NEVER DUE. A state that is not a dict, flags that are not
    bools, a notified that is not a list, a trigger that no longer validates
    or a quiet window that does not parse is skip 'malformed', never notify;
    an empty state is not an obligation."""
    if not isinstance(state, dict):
        return ("skip", "malformed")
    flags = {}
    for name in ("cancelled", "completed"):
        value = state.get(name, False)
        if not isinstance(value, bool):
            return ("skip", "malformed")
        flags[name] = value
    if flags["cancelled"]:
        return ("skip", "cancelled")
    if flags["completed"]:
        return ("skip", "completed")
    notified = state.get("notified", [])
    if not isinstance(notified, list):
        return ("skip", "malformed")
    if len(notified) > 0:
        return ("skip", "notified")
    now = _moment(now_utc)
    expires = _moment(state.get("expires_at"))
    if now is None or expires is None:
        return ("skip", "malformed")
    if expires <= now:
        return ("skip", "expired")
    trigger = state.get("trigger")
    due = _moment(trigger.get("due_at")) if isinstance(trigger, dict) \
        else None
    if due is None:
        return ("skip", "malformed")
    if due > now:
        return ("skip", "not-due")
    windows = _quiet_windows(quiet)
    if windows is None:
        return ("skip", "malformed")
    for start, end in windows:
        if start <= now < end:
            return ("skip", "quiet")
    try:
        validate_trigger(trigger, state.get("owner"), state.get("expires_at"),
                         state.get("authorization"))
    except ValueError:
        return ("skip", "malformed")
    return ("notify", "due")


def _folded_record(folded, oid):
    """The folded record for `oid`, or None (no journal, not a str id, or no
    OBLIGATION row; the fold's own int counters are never a record)."""
    if not isinstance(folded, dict) or not isinstance(oid, str):
        return None
    record = folded.get(oid)
    return record if isinstance(record, dict) else None


def mark(run_dir: str, obligation_id: str, row_type: str,
         by: str) -> Optional[str]:
    """Appends one CANCELLED or COMPLETED row {obligation_id, by}; any other
    row_type raises ValueError; an id with no OBLIGATION row is refused with
    None and one stderr line. Returns the event_id or None. `by` that is not
    a non-empty str raises ValueError too."""
    if not isinstance(row_type, str) or row_type not in MARK_TYPES:
        raise ValueError("row_type must be %s or %s, got %r"
                         % (CANCELLED, COMPLETED, row_type))
    if not _nonempty_str(by):
        raise ValueError("by is empty or not a string")
    run_dir = str(run_dir or "").strip()
    record = _folded_record(read_obligations(run_dir) if run_dir else None,
                            obligation_id)
    if record is None:
        sys.stderr.write("followup_obligation: no obligation %r in %r; "
                         "%s not written\n"
                         % (obligation_id, run_dir, row_type))
        return None
    payload = {"obligation_id": obligation_id, "by": by}
    if not _fits_one_line(run_dir, payload):
        sys.stderr.write("followup_obligation: %s row for %s is too large "
                         "for one atomic journal line; not written\n"
                         % (row_type, obligation_id))
        return None
    # A root event, as persist's: the probe above measured a line with no
    # parent, and a parent id would spend bytes the probe did not count.
    return journal.append(run_dir, row_type, payload=payload)


def notify(run_dir: str, obligation_id: str, now_utc: str,
           quiet: Optional[list] = None) -> Tuple[str, str]:
    """read_obligations, reconcile; on 'notify' append one NOTIFIED row
    {obligation_id, at: now_utc} and return ('notified', event_id); on 'skip'
    return ('skip', reason) and append nothing. A second call for the same id
    returns ('skip', 'notified'). It renders and sends nothing: the text is
    the caller's (RL4.c prints it).

    No journal or an unknown id reconciles as the empty state, skip
    'malformed'. A NOTIFIED row the journal refuses to write is
    ('skip', 'unwritten'): a notification nobody recorded must not be sent,
    or the next pass would send it again."""
    run_dir = str(run_dir or "").strip()
    record = _folded_record(read_obligations(run_dir) if run_dir else None,
                            obligation_id)
    verdict, reason = reconcile(record if record is not None else {},
                                now_utc, quiet)
    if verdict != "notify":
        return ("skip", reason)
    event_id = journal.append(
        run_dir, NOTIFIED,
        payload={"obligation_id": obligation_id, "at": now_utc})
    if event_id is None:
        return ("skip", "unwritten")
    return ("notified", event_id)


LINE_LIMIT = 400
ELLIPSIS = "..."


def _one_line(value):
    """`value` with every line break and control character turned into a
    space, so a field can never start a second printed line."""
    return "".join(" " if (ch.isspace() and ch != " ") or not ch.isprintable()
                   else ch for ch in value)


def render_line(obligation_id: str, folded: dict) -> str:
    """'FOLLOW-UP <id[:12]> due <due_at> owner <owner>: <kind> <subject>
    (authorized by <granted_by>, scope <scope>, action <action_ref>)'. One
    line, no newline, under LINE_LIMIT characters: the subject is cut first
    with an ellipsis of three dots, and a line still too long from its other
    fields is cut at the end the same way. scope prints its items joined by
    commas, 'none' when empty.

    `folded` is one record from read_obligations; one that is not a dict, or
    whose trigger, owner or authorization does not validate, raises
    ValueError, as does an obligation_id that is not a non-empty str."""
    if not _nonempty_str(obligation_id):
        raise ValueError("obligation_id is empty or not a string")
    if not isinstance(folded, dict):
        raise ValueError("folded must be a dict, got %s"
                         % type(folded).__name__)
    trigger = folded.get("trigger")
    owner = folded.get("owner")
    authorization = folded.get("authorization")
    validate_trigger(trigger, owner, folded.get("expires_at"), authorization)
    scope = ",".join(authorization["scope"]) or "none"
    head = "FOLLOW-UP %s due %s owner %s: %s " % (
        obligation_id[:12], trigger["due_at"], owner, trigger["kind"])
    tail = " (authorized by %s, scope %s, action %s)" % (
        authorization["granted_by"], scope, authorization["action_ref"])
    head, tail = _one_line(head), _one_line(tail)
    subject = _one_line(trigger["subject"])
    room = LINE_LIMIT - 1 - len(head) - len(tail)
    if len(subject) > room:
        subject = subject[:max(0, room - len(ELLIPSIS))] + ELLIPSIS
    line = head + subject + tail
    if len(line) >= LINE_LIMIT:
        line = line[:LINE_LIMIT - 1 - len(ELLIPSIS)] + ELLIPSIS
    return line
