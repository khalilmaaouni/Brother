#!/usr/bin/env python3
"""donecheck_acc8_retention: the closing check of ACC8. Reads the current
rules file, the proposed draft and RETENTION.json, and answers PASS, FAIL or
NO-DATA on its own reading, never on the map's word.

WHAT BINDS A ROW TO ITS SOURCE. Every unit of the current file is recomputed
here (rulebook_inventory.classify_all on the real source text). A row is
matched to a unit by line and by the sha256 of the unit's text as recomputed,
not as the map states it. The texts a destination may carry for that unit
are recomputed from the unit too, never read from the map: the unit itself
(an exact copy, whitespace folded), the ENFORCED-INDEX pointer
rulebook_diet.pointer_line produces for it, or the surviving sentences
rulebook_diet.surviving_line produces for it. The map's dest_text must equal
one of those, and that text must be present at the destination. A map that
names any other text (the reviewer's counterexample: source "Never publish
private records", draft "Say hello", a row pointing at the draft) fails on
"not the source unit or an allowed transformation of it".

FAILS when: a unit has no row, two rows, or a row with no destination; a row
names a unit the source does not have; the row's text is not an allowed
transformation of the unit; a destination file cannot be read; the text is
not at its destination; a moved row's destination path is not named in the
draft or its heading is absent there; the enforcement status the unit claims
differs from the status the allowed text claims; the draft is at or above
the byte limit.
NO-DATA (exit 2) when: the rules file, the draft or the map is unreadable,
the map is not the schema or carries a malformed row, or the map was built
from a different source (sha256 mismatch), because then nothing it says is
about this file.

Exit 0 PASS, 1 FAIL, 2 NO-DATA. Python 3.9 compatible, standard library
only. No em or en dashes in this file.
"""
import argparse
import json
import os
import re
import sys
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rulebook_diet as rd  # noqa: E402
import rulebook_inventory as ri  # noqa: E402

LIMIT = 30000


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


ROW_KEYS = ("line", "sha256", "destination", "dest_text", "heading")


def allowed_texts(unit: Dict[str, object], existing: List[str]) -> List[str]:
    """What a destination may carry for this unit, recomputed from the unit."""
    out = [str(unit["text"])]
    if unit["class"] == "ENFORCED-INDEX":
        out.append(rd.pointer_line(unit, existing) + "\n")
    if unit["class"] == "SUPERSEDED-TEXT":
        kept = rd.surviving_line(unit)
        if kept is not None:
            out.append(kept + "\n")
    return out


def _rows_by_key(rows: object) -> Dict[tuple, List[Dict[str, object]]]:
    """Rows grouped by (line, sha256). Raises ValueError on a malformed row."""
    if not isinstance(rows, list):
        raise ValueError("rows is not a list")
    grouped = {}  # type: Dict[tuple, List[Dict[str, object]]]
    for row in rows:
        if not isinstance(row, dict) or any(k not in row for k in ROW_KEYS):
            raise ValueError("malformed row")
        if not isinstance(row["line"], int) or isinstance(row["line"], bool) or not isinstance(row["sha256"], str):
            raise ValueError("malformed row")
        grouped.setdefault((row["line"], row["sha256"]), []).append(row)
    return grouped


def check(source: str, draft: str, doc: Dict[str, object], limit: int, home: str, repo_root: str) -> List[str]:
    failures = []  # type: List[str]
    units = ri.classify_all(source, home, repo_root)
    existing = ri.existing_cited_paths(units, home, repo_root)
    grouped = _rows_by_key(doc["rows"])
    known = set((int(u["line"]), str(u["sha256"])) for u in units)
    for key in sorted(grouped):
        if key not in known:
            failures.append("line %d: row names a unit the source does not have" % key[0])
    draft_norm = _norm(draft)
    cache = {"draft": draft_norm}  # type: Dict[str, Optional[str]]
    for unit in units:
        key = (int(unit["line"]), str(unit["sha256"]))
        tag = "line %d" % unit["line"]
        rows = grouped.get(key, [])
        if not rows:
            failures.append("%s: no row in the map" % tag)
            continue
        if len(rows) > 1:
            failures.append("%s: %d rows for one unit" % (tag, len(rows)))
            continue
        row = rows[0]
        destination = row.get("destination")
        dest_text = str(row.get("dest_text") or "")
        if not destination or not dest_text.strip():
            failures.append("%s: no destination" % tag)
            continue
        destination = str(destination)
        allowed = [t for t in allowed_texts(unit, existing) if _norm(t) == _norm(dest_text)]
        if not allowed:
            failures.append("%s: map text is not the source unit or an allowed transformation of it" % tag)
            continue
        text = allowed[0]
        if destination not in cache:
            read = ri.read_rules(destination)
            cache[destination] = _norm(read) if read is not None else None
        dest_norm = cache[destination]
        if dest_norm is None:
            failures.append("%s: destination unreadable: %s" % (tag, destination))
            continue
        if _norm(text) not in dest_norm:
            failures.append("%s: text not found at %s" % (tag, destination))
            continue
        if destination != "draft":
            if _norm(destination) not in draft_norm:
                failures.append("%s: draft names no pointer to %s" % (tag, destination))
            heading = str(row.get("heading") or "")
            if heading and _norm(heading) not in dest_norm:
                failures.append("%s: heading absent from %s" % (tag, destination))
        before = ri.enforcement_status(str(unit["text"]))
        after = ri.enforcement_status(text)
        if before != after:
            failures.append("%s: enforcement status changed %s to %s" % (tag, before, after))
    size = len(draft.encode("utf-8"))
    if size >= limit:
        failures.append("draft is %d bytes, limit %d" % (size, limit))
    return failures


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="verify every rule of the current file survives the proposed draft")
    ap.add_argument("--source", default=os.path.expanduser(ri.DEFAULT_RULES))
    ap.add_argument("--draft", required=True)
    ap.add_argument("--map", required=True, help="RETENTION.json")
    ap.add_argument("--limit", type=int, default=LIMIT)
    ap.add_argument("--home", default=os.path.expanduser("~"))
    ap.add_argument("--repo", default=ri.ROOT)
    args = ap.parse_args(argv)
    source = ri.read_rules(args.source)
    draft = ri.read_rules(args.draft)
    raw = ri.read_rules(args.map)
    for name, value in (("source", source), ("draft", draft), ("map", raw)):
        if value is None:
            print("NO-DATA: %s unreadable or not UTF-8" % name)
            return 2
    try:
        doc = json.loads(str(raw))
        if doc.get("schema") != "acc8-retention/1" or not isinstance(doc.get("rows"), list):
            raise ValueError("schema")
    except (ValueError, AttributeError):
        print("NO-DATA: map is not an acc8-retention/1 document")
        return 2
    if doc.get("source_sha256") != ri._sha256(str(source).encode("utf-8")):
        print("NO-DATA: map was built from a different source (sha256 mismatch)")
        return 2
    try:
        failures = check(str(source), str(draft), doc, args.limit, args.home, args.repo)
    except ValueError as exc:
        print("NO-DATA: map malformed (%s)" % exc)
        return 2
    units = len(ri.rule_units(str(source)))
    size = len(str(draft).encode("utf-8"))
    if failures:
        for item in failures:
            print("FAIL: " + item)
        print("FAIL: %d problem(s) over %d units, draft %d bytes" % (len(failures), units, size))
        return 1
    print("PASS: %d of %d units reach a destination with their enforcement status, draft %d bytes under %d" % (
        units, units, size, args.limit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
