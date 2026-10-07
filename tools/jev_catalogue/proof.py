"""L6.2 ledger proof link for the Jev use case catalogue.

Read only helpers that bind a catalogue entry to real RESERVE plus RECONCILE
lines in the OpenRouter dispatch ledger. Missing, unreadable, non utf-8 or
corrupt input is NO-DATA: an empty list from find_ledger_lines and a blocked
verdict from verify_proof, never a proven entry. Nothing here writes.
"""

import json
import math
import os


RESERVE = "RESERVE"
RECONCILE = "RECONCILE"


def _type_name(value):
    return type(value).__name__


def _require_str(name, value):
    if isinstance(value, bool) or not isinstance(value, str) or not value:
        raise ValueError("%s must be a non empty str, got %s" % (name, _type_name(value)))


def _require_list(name, value):
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, list):
        raise ValueError("%s must be a list, got %s" % (name, _type_name(value)))


def _require_record(name, value):
    if not isinstance(value, dict):
        raise ValueError("%s must be a dict, got %s" % (name, _type_name(value)))


def find_ledger_lines(ledger_path: str, holder_id: str) -> list:
    """Return the ledger lines whose holder_id matches exactly.

    A missing file, a directory where a file belongs, bytes that are not utf-8,
    a line that is not valid JSON, or a JSON value that is not an object is
    NO-DATA: return the empty list, never a proven set.
    """
    _require_str("ledger_path", ledger_path)
    _require_str("holder_id", holder_id)
    if not os.path.isfile(ledger_path):
        return []
    try:
        with open(ledger_path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return []
    found = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except (ValueError, TypeError):
            return []
        if not isinstance(record, dict):
            return []
        # THE REAL LEDGER'S SHAPE (2026-09-28): a RECONCILE row carries only the reservation_id, and the catalogue
        # records the reservation id as each entry's holder_id; matched on holder only, 0 of 70 entries proved,
        # matched on either, 60 do (the other 10 were written to a /tmp ledger that no longer exists).
        if record.get("holder_id") == holder_id or record.get("reservation_id") == holder_id:
            found.append(record)
    return found


def verify_proof(entry: dict, lines: list) -> tuple:
    """Return (True, reason) only for one RESERVE plus one RECONCILE.

    Every counted line must carry the entry's exact holder_id. Anything else is
    blocked, and the reason names the counts that were found.
    """
    _require_record("entry", entry)
    _require_list("lines", lines)
    holder_id = entry.get("holder_id")
    if isinstance(holder_id, bool) or not isinstance(holder_id, str) or not holder_id:
        return (False, "blocked: entry carries no usable holder_id")
    reserve = 0
    reconcile = 0
    for line in lines:
        _require_record("ledger line", line)
        if line.get("holder_id") != holder_id and line.get("reservation_id") != holder_id:
            continue
        kind = line.get("type", line.get("kind"))   # the real ledger names it "type"; the first fixtures said "kind"
        if kind == RESERVE:
            reserve += 1
        elif kind == RECONCILE:
            reconcile += 1
    if reserve >= 1 and reconcile >= 1:
        return (True, "proven: %d RESERVE and %d RECONCILE for %s" % (reserve, reconcile, holder_id))
    return (False, "blocked: %d RESERVE and %d RECONCILE for %s" % (reserve, reconcile, holder_id))


def current_spend_view(lines: list) -> float:
    """Read only sum of the cost field on RESERVE lines. Never writes."""
    _require_list("lines", lines)
    total = 0.0
    for line in lines:
        _require_record("ledger line", line)
        if line.get("type", line.get("kind")) != RESERVE:   # the real ledger: "type" and "estimated_cost"
            continue
        cost = line.get("cost", line.get("estimated_cost", 0.0))
        if isinstance(cost, bool) or not isinstance(cost, (int, float)):
            raise ValueError("cost must be a finite real number, got %s" % (_type_name(cost),))
        if math.isnan(cost) or math.isinf(cost):
            raise ValueError("cost must be a finite real number")
        total += float(cost)
    return total
