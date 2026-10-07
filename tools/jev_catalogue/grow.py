"""L6.3 growth and health for the Jev use case catalogue."""

import json
import os


class GrowError(ValueError):
    """Deliberate refusal for this module."""


def _require_str(value, name):
    if not isinstance(value, str):
        raise GrowError(name + " must be a str")
    if not value:
        raise GrowError(name + " must be non-empty")
    return value


def _parse_catalogue(catalogue_path):
    _require_str(catalogue_path, "catalogue_path")
    if not os.path.isfile(catalogue_path):
        return None, "catalogue file missing"
    try:
        with open(catalogue_path, "rb") as handle:
            raw = handle.read()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None, "catalogue unreadable or not utf-8"
    entries = []
    current = None
    for line in text.splitlines():
        if line.startswith("### "):
            rest = line[4:].strip()
            if rest and rest[0].isdigit():
                dot = rest.find(".")
                if dot > 0 and rest[:dot].isdigit():
                    if current is not None:
                        entries.append(current)
                    current = {"id": int(rest[:dot]), "title": rest[dot + 1:].strip()}
                    continue
        if current is not None:
            if line.startswith("Question: `"):
                start = len("Question: `")
                end = line.find("`", start)
                if end > start:
                    current["question_id"] = line[start:end]
            elif line.startswith("Ledger: `"):
                start = len("Ledger: `")
                end = line.find("`", start)
                if end > start:
                    current["holder_id"] = line[start:end]
    if current is not None:
        entries.append(current)
    return entries, ""


def _read_ledger(ledger_path):
    _require_str(ledger_path, "ledger_path")
    if not os.path.isfile(ledger_path):
        return None, "ledger file missing"
    try:
        with open(ledger_path, "rb") as handle:
            raw = handle.read()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None, "ledger unreadable or not utf-8"
    lines = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return None, "ledger line " + str(lineno) + " is not JSON"
        if not isinstance(obj, dict):
            return None, "ledger line " + str(lineno) + " is not an object"
        lines.append(obj)
    return lines, ""


def _verify_proof(entry, ledger_path):
    if not isinstance(entry, dict):
        return False, "entry must be a dict"
    holder_id = entry.get("holder_id")
    if not isinstance(holder_id, str) or not holder_id:
        return False, "entry missing holder_id"
    lines, err = _read_ledger(ledger_path)
    if lines is None:
        return False, err
    matched = []
    reservation_id = entry.get("reservation_id")
    for obj in lines:
        h = obj.get("holder_id")
        r = obj.get("reservation_id")
        if h == holder_id:
            matched.append(obj)
        elif isinstance(reservation_id, str) and reservation_id and r == reservation_id:
            matched.append(obj)
    if not matched:
        return False, "no ledger lines for holder id"
    has_reserve = False
    has_reconcile = False
    reconcile_reservations = set()
    for obj in matched:
        typ = obj.get("type") or obj.get("event") or obj.get("action") or ""
        if not isinstance(typ, str):
            return False, "ledger type is not a str"
        upper = typ.upper()
        if "RESERVE" in upper:
            has_reserve = True
            rr = obj.get("reservation_id")
            if isinstance(rr, str):
                reconcile_reservations.add(rr)
        if "RECONCILE" in upper:
            has_reconcile = True
    if has_reserve and has_reconcile:
        return True, "proof ok"
    if has_reserve and reconcile_reservations:
        for obj in lines:
            if obj.get("reservation_id") in reconcile_reservations:
                typ = obj.get("type") or obj.get("event") or obj.get("action") or ""
                if isinstance(typ, str) and "RECONCILE" in typ.upper():
                    return True, "proof ok"
    return False, "proof incomplete: reserve=" + str(has_reserve) + " reconcile=" + str(has_reconcile)


def next_catalogue_id(existing):
    if not isinstance(existing, list):
        raise GrowError("existing must be a list")
    max_id = 0
    for item in existing:
        if not isinstance(item, dict):
            raise GrowError("existing entries must be dicts")
        ident = item.get("id")
        if isinstance(ident, bool):
            raise GrowError("existing entry id must be an int")
        if isinstance(ident, int):
            if ident > max_id:
                max_id = ident
        elif isinstance(ident, str) and ident.isdigit():
            value = int(ident)
            if value > max_id:
                max_id = value
        else:
            raise GrowError("existing entry id must be an int")
    return str(max_id + 1)


def _format_entry(entry):
    lines = []
    lines.append("")
    lines.append("### " + str(entry["id"]) + ". " + entry.get("title", entry.get("question_id", "entry")))
    lines.append("")
    lines.append("Question: `" + entry["question_id"] + "`")
    lines.append("")
    lines.append("Ledger: `" + entry["holder_id"] + "`")
    lines.append("")
    return "\n".join(lines)


def append_entry(catalogue_path, entry, ledger_path):
    try:
        _require_str(catalogue_path, "catalogue_path")
        _require_str(ledger_path, "ledger_path")
        if not isinstance(entry, dict):
            return False, "entry must be a dict"
        holder_id = entry.get("holder_id")
        if not isinstance(holder_id, str) or not holder_id:
            return False, "entry holder_id must be a non-empty str"
        question_id = entry.get("question_id")
        if not isinstance(question_id, str) or not question_id:
            return False, "entry question_id must be a non-empty str"
        existing, err = _parse_catalogue(catalogue_path)
        if existing is None:
            return False, err
        qtype = entry.get("type")
        if not isinstance(qtype, str) or qtype != "noul":
            return False, "gate refuses question type: must be noul"
        for old in existing:
            if old.get("holder_id") == holder_id:
                return False, "duplicate holder id"
            if old.get("question_id") == question_id:
                return False, "duplicate question id"
        next_id = next_catalogue_id(existing)
        candidate = dict(entry)
        candidate["id"] = int(next_id)
        ok, reason = _verify_proof(candidate, ledger_path)
        if not ok:
            return False, reason
        block = _format_entry(candidate)
        with open(catalogue_path, "ab") as handle:
            handle.write(block.encode("utf-8"))
        return True, "appended " + next_id
    except GrowError as exc:
        return False, str(exc)
    except OSError as exc:
        return False, "write failed: " + str(exc)


def build_health(catalogue_path, ledger_path):
    _require_str(catalogue_path, "catalogue_path")
    _require_str(ledger_path, "ledger_path")
    existing, err = _parse_catalogue(catalogue_path)
    if existing is None:
        return {"total": 0, "proven": 0, "unproven": 0, "error": err, "entries": []}
    total = len(existing)
    proven = 0
    entries = []
    for item in existing:
        ok, reason = _verify_proof(item, ledger_path)
        if ok:
            proven += 1
        entries.append({"id": item.get("id"), "proven": ok, "reason": reason})
    return {"total": total, "proven": proven, "unproven": total - proven, "entries": entries}


def check_done(health):
    if not isinstance(health, dict):
        raise GrowError("health must be a dict")
    total = health.get("total")
    proven = health.get("proven")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise GrowError("health total must be a non-negative int")
    if isinstance(proven, bool) or not isinstance(proven, int) or proven < 0:
        raise GrowError("health proven must be a non-negative int")
    if proven > total:
        raise GrowError("proven cannot exceed total")
    if total >= 70 and proven == total:
        return True, "done: 70 or more entries, all proven"
    if total == 10 and proven == 10:
        return True, "seed phase: 10 of 10 proven"
    return False, "not done: " + str(proven) + " of " + str(total) + " proven"
