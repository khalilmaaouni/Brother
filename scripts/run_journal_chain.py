#!/usr/bin/env python3
"""run_journal_chain: tamper-evident run journal for DOM-10.05.

THE DECIDING PROPERTY. Each event's hash covers its own fields AND the
hash of the event immediately before it (prev_hash). Modifying, reordering
or removing any earlier event changes a hash somewhere downstream of it,
so verify() finds the first place the chain no longer adds up and names
its index; a clean walk to the end hands back the final hash, the one a
receipt is allowed to carry.

WHAT THIS IS NOT: TAMPER EVIDENT, NOT TAMPER PROOF. Anyone who can rewrite
run_journal_chain.jsonl can recompute every hash in it from scratch and
produce a new, internally-consistent chain that verify() will call clean.
This module detects modification by anything that does NOT recompute the
whole chain (a hand edit, a partial write, a line moved or deleted without
the rest of the file being redone) -- which is the honest, buildable claim.
It is not a signature, it proves nothing about WHO wrote an event, and it
cannot stop someone with write access to the file from replacing it whole.

WHAT THE HASH COVERS, EXACTLY (see _event_hash): prev_hash, timestamp,
actor, host, unit, action, result -- the event's own seven fields, nothing
else. It does NOT cover the event's line number in the file, any sibling
event beyond the one linked predecessor, or anything outside the file
(who is running this process, the real wall clock, disk metadata). A field
added outside this list would silently ride along unchecked; extend the
COVERED tuple in _event_hash if one is ever added.

WHY APPEND REWRITES THE WHOLE FILE. append() is asked for atomically via
temp-file-then-replace, never an in-place write: tempfile.mkstemp beside
the target, the full event list (old plus the one new line) written to it,
fsync, then os.replace over the real path. A crash mid-write leaves either
the untouched old file or the complete new one, never a half-written line
-- unlike scripts/journal.py's O_APPEND approach (a single line is POSIX-
atomic there because it never needs to read what came before); this
journal's chain requires reading the prior event's hash before a new one
can be computed, so the choice here is read-modify-write, not append-only
at the OS level, and each write's atomicity comes from the rename instead.

EDGES THIS IS BUILT AND TESTED AGAINST: an empty journal, a single event, a
truncated last line (a crash mid-write by something other than this
module, since this module's own writes are atomic), a modified middle
event, two events swapped, an event removed, a duplicated event, a file
with and without a trailing newline (both read identically), and a
non-JSON line in the middle. See test_run_journal_chain.py.

Python 3, standard library only. No network.
"""
import datetime
import hashlib
import json
import os
import tempfile

NODATA = "NO-DATA"

#: The prev_hash of the very first event in a chain: there is no real
#: predecessor to link to, so this fixed, obviously-not-a-real-hash value
#: stands in, the same role a genesis block's parent hash plays elsewhere.
GENESIS_HASH = "0" * 64

#: The exact fields _event_hash covers, in the order they are hashed. Named
#: once so the docstring above, _event_hash, and verify()'s "missing
#: field(s)" check all read from the same list rather than three copies of
#: it drifting apart.
COVERED_FIELDS = ("prev_hash", "timestamp", "actor", "host", "unit",
                  "action", "result")


class JournalError(Exception):
    """Raised by append() when the existing file cannot be safely
    extended (unreadable, or its last line will not parse). See append()'s
    own docstring for why this refuses rather than chaining onto a
    predecessor it cannot read."""


def _event_hash(prev_hash, timestamp, actor, host, unit, action, result):
    """sha256 hex digest over exactly COVERED_FIELDS, canonical JSON
    (sorted keys, no incidental whitespace) so the same field values always
    hash the same way regardless of dict ordering. Covering prev_hash is
    what makes reordering or removing an earlier event detectable here --
    drop it from this payload and every event still hashes correctly in
    isolation while the chain stops proving anything about order, which is
    exactly the mutation this module's own done-check is proved against."""
    payload = {
        "prev_hash": prev_hash, "timestamp": timestamp, "actor": actor,
        "host": host, "unit": unit, "action": action, "result": result,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _read_lines(path):
    """The journal's lines as raw text (JSON not yet parsed), or None when
    the file exists but could not be opened/read at all -- the NO-DATA
    case, distinct from "no file yet" which is a legitimately empty chain.
    A trailing newline and no trailing newline read identically: the file
    is split on "\\n" and exactly one trailing empty element (the artifact
    of a final "\\n") is dropped, never a truncated last line's own
    content, which is not empty and must survive to be caught by verify()."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    if text == "":
        return []
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _atomic_write(path, text):
    """Write `text` to `path` via tempfile.mkstemp beside it, fsync,
    os.replace -- never open(path, "w") in place. Mirrors
    scripts/continuity.py's write_capsule() pattern: the file that exists
    at `path` at any moment is always either the complete old version or
    the complete new one."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".run_journal_chain-",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def append(path, actor, host, unit, action, result, timestamp=None):
    """Append one event to the journal at `path`, atomically (see
    _atomic_write). Returns the event dict just written, including its
    computed "hash".

    Raises JournalError rather than writing anything when the existing
    file cannot be read, or its content does not fully parse as one JSON
    object per line: chaining a new, correctly-computed event onto a
    predecessor this process cannot read would produce a file that LOOKS
    newly broken at this append, when the corruption actually predates it.
    Fail closed here; call verify() first to learn what is wrong before
    deciding whether to start a fresh journal instead."""
    lines = _read_lines(path)
    if lines is None:
        raise JournalError("%s: %s could not be read" % (NODATA, path))
    events = []
    for i, line in enumerate(lines):
        try:
            events.append(json.loads(line))
        except ValueError as exc:
            raise JournalError(
                "refusing to append to %s: line %d is not valid JSON (%s); "
                "the existing chain is already broken" % (path, i, exc))
    prev_hash = events[-1]["hash"] if events else GENESIS_HASH
    ts = timestamp if timestamp is not None else (
        datetime.datetime.now(datetime.timezone.utc).isoformat())
    event_hash = _event_hash(prev_hash, ts, actor, host, unit, action, result)
    event = {
        "prev_hash": prev_hash, "timestamp": ts, "actor": actor,
        "host": host, "unit": unit, "action": action, "result": result,
        "hash": event_hash,
    }
    text = "".join(json.dumps(e, sort_keys=True) + "\n"
                   for e in events + [event])
    _atomic_write(path, text)
    return event


def verify(path):
    """Walk the journal at `path` from its first event, recomputing and
    checking each event's hash and its link to the one before it. Returns
    one of:

      {"verdict": "clean", "count": N, "final_hash": H} -- every event
          checks out in order; H is what receipt() hands back.
      {"verdict": "broken", "index": I, "problem": "..."} -- the first
          event (0-based, file order) where something did not add up.
      {"verdict": NO-DATA, "problem": "..."} -- the file could not be
          read at all (permission, I/O error). Never reported as clean:
          a journal nobody can read has proven nothing.

    An empty journal (no file, or a zero-byte file) verifies clean with
    count 0 and final_hash GENESIS_HASH -- a real, valid state, not a
    failure to read one."""
    lines = _read_lines(path)
    if lines is None:
        return {"verdict": NODATA, "problem": "%s could not be read" % path}
    if not lines:
        return {"verdict": "clean", "count": 0, "final_hash": GENESIS_HASH}
    prev_hash = GENESIS_HASH
    for i, line in enumerate(lines):
        try:
            event = json.loads(line)
        except ValueError as exc:
            return {"verdict": "broken", "index": i,
                    "problem": "line %d is not valid JSON (%s), a truncated "
                               "or otherwise corrupted write" % (i, exc)}
        if not isinstance(event, dict):
            return {"verdict": "broken", "index": i,
                    "problem": "line %d did not decode to a JSON object" % i}
        missing = [k for k in COVERED_FIELDS + ("hash",) if k not in event]
        if missing:
            return {"verdict": "broken", "index": i,
                    "problem": "line %d is missing field(s): %s"
                               % (i, ", ".join(missing))}
        if event["prev_hash"] != prev_hash:
            return {"verdict": "broken", "index": i,
                    "problem": "line %d's prev_hash does not match the "
                               "hash of the event before it (an earlier "
                               "event was modified, reordered, removed, "
                               "duplicated, or inserted)" % i}
        recomputed = _event_hash(*(event[k] for k in COVERED_FIELDS))
        if recomputed != event["hash"]:
            return {"verdict": "broken", "index": i,
                    "problem": "line %d's hash does not match its own "
                               "content (a field was edited after it was "
                               "written)" % i}
        prev_hash = event["hash"]
    return {"verdict": "clean", "count": len(lines), "final_hash": prev_hash}


def receipt(path):
    """(hash_or_None, problem): the final chain hash a receipt may carry,
    or (None, a one-line reason) when the chain is not clean -- a receipt
    must never carry the hash of a broken or unreadable chain, so this
    refuses instead of returning verify()'s partial answer."""
    result = verify(path)
    if result["verdict"] != "clean":
        return None, "%s: %s" % (result["verdict"], result.get("problem", ""))
    return result["final_hash"], ""
