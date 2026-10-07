#!/usr/bin/env python3
"""rulebook_split: ACC8, the proposed standing rules file under 30,000 bytes
with EVERY bullet of the current file kept reachable, and the retention map
that scripts/donecheck_acc8_retention.py verifies.

HOW THE DRAFT IS BUILT. Every heading of the source stays in the draft. Each
bullet unit (rulebook_inventory.rule_units) goes one of four ways, decided by
its class first and the owner's keep list second:
  DUPLICATE        dropped; its row points at the identical earlier unit
  SUPERSEDED-TEXT  the sentences no other unit restates survive as one
                   bullet in the draft (rulebook_diet.surviving_line); a unit
                   with nothing surviving moves verbatim to the note instead
  ENFORCED-INDEX   replaced in the draft by rulebook_diet.pointer_line, which
                   keeps the status word and every proving sentence
  everything else  kept verbatim in the draft when its start line is in the
                   keep list, else moved verbatim to the note under the same
                   heading, and the draft carries a MOVED pointer under that
                   heading plus one line at the top naming the note's path
The note is a vault file; the draft names its absolute path, so a reader of
the draft reaches every moved sentence by that one pointer and the heading.

THE MAP. RETENTION.json carries one row per source unit: line, class, the
enforcement status the unit's text claims (rulebook_inventory.enforcement_status),
the original text, the destination ("draft" or the note's path) and the exact
text found there. --map-only locates each unit of an existing draft (and
note) without writing either, which is how the 7a1e16a66 class diet is
measured: its missing and status changed rows then fail the check.

PRIVACY. The draft, the note and the map all carry the owner's rules. They
are written under the private evidence directory or the vault, mode 600,
never under a repository path an export ships.

Python 3.9 compatible, standard library only. No em or en dashes in this file.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rulebook_diet as rd  # noqa: E402
import rulebook_inventory as ri  # noqa: E402

MAP_NAME = "RETENTION.json"
MAP_MD_NAME = "RETENTION.md"
TOP_POINTER = ("- MOVED RULES NOTE: %s holds, verbatim and under the same headings, every bullet "
               "marked MOVED below. Read that section before working in its area; nothing there "
               "is retired.")
SECTION_POINTER = "- MOVED (%s): %d bullet(s), verbatim in the moved rules note (top of file), under this heading."
DEFAULT_TRIGGER = "history and narrative, read when the rule above needs its origin"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def read_keep(path: Optional[str]) -> List[int]:
    if not path:
        return []
    with open(path, "r", encoding="utf-8") as handle:
        out = []
        for row in handle:
            row = row.split("#", 1)[0].strip()
            if row:
                out.append(int(row))
        return out


def read_triggers(path: Optional[str]) -> Dict[int, str]:
    """heading line number -> when to read the moved section (JSON object)."""
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    return {int(k): str(v) for k, v in raw.items()}


def split(text: str, keep: List[int], note_path: str, home: str, repo_root: str,
          triggers: Optional[Dict[int, str]] = None) -> Tuple[str, str, List[Dict[str, object]]]:
    """Returns (draft_text, note_text, rows). Pure: writes nothing. triggers
    maps a heading's line number to the lookup trigger written in its MOVED
    pointer ("read before any OpenRouter call"); a moved section with no
    trigger is marked as history."""
    triggers = triggers or {}
    units = ri.classify_all(text, home, repo_root)
    existing = ri.existing_cited_paths(units, home, repo_root)
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    by_start = {int(u["line"]): u for u in units}
    first_by_norm = {}  # type: Dict[str, Dict[str, object]]
    keep_set = set(keep)
    draft = []  # type: List[str]
    note_sections = []  # type: List[Tuple[str, List[str]]]
    rows = []  # type: List[Dict[str, object]]
    heading = ""
    heading_line = 0
    moved_here = []  # type: List[str]
    pending_pointer_at = None  # type: Optional[int]
    top_inserted = False

    def flush_section() -> None:
        nonlocal moved_here, pending_pointer_at
        if moved_here:
            trigger = triggers.get(heading_line, DEFAULT_TRIGGER)
            draft.insert(pending_pointer_at, SECTION_POINTER % (trigger, len(moved_here)))
            note_sections.append((heading, list(moved_here)))
        moved_here = []
        pending_pointer_at = None

    def row(unit: Dict[str, object], destination: Optional[str], dest_text: str, how: str) -> None:
        rows.append({
            "line": int(unit["line"]), "end_line": int(unit["end_line"]), "sha256": unit["sha256"],
            "heading": heading, "class": unit["class"], "how": how,
            "status": ri.enforcement_status(str(unit["text"])),
            "original": unit["text"], "destination": destination, "dest_text": dest_text,
        })

    number = 1
    while number <= len(lines):
        line = lines[number - 1]
        unit = by_start.get(number)
        if unit is None:
            if ri.HEADING_RE.match(line):
                flush_section()
                heading = line
                heading_line = number
                draft.append(line)
                pending_pointer_at = len(draft)
                if not top_inserted and line.startswith("# "):
                    draft.append(TOP_POINTER % note_path)
                    pending_pointer_at = len(draft)
                    top_inserted = True
            else:
                draft.append(line)
            number += 1
            continue
        cls = str(unit["class"])
        unit_text = str(unit["text"])
        norm = str(unit["norm"])
        if cls == "DUPLICATE":
            first = first_by_norm[norm]
            row(unit, first["_destination"], str(first["_dest_text"]), "duplicate of line %d" % int(first["line"]))
        elif cls == "ENFORCED-INDEX":
            pointer = rd.pointer_line(unit, existing)
            draft.append(pointer)
            unit["_destination"], unit["_dest_text"] = "draft", pointer + "\n"
            row(unit, "draft", pointer + "\n", "pointer with proving sentences")
        elif cls == "SUPERSEDED-TEXT" and rd.surviving_line(unit) is not None:
            kept = str(rd.surviving_line(unit))
            draft.append(kept)
            unit["_destination"], unit["_dest_text"] = "draft", kept + "\n"
            row(unit, "draft", kept + "\n", "surviving sentences kept")
        elif int(unit["line"]) in keep_set:
            draft.extend(lines[number - 1:int(unit["end_line"])])
            unit["_destination"], unit["_dest_text"] = "draft", unit_text
            row(unit, "draft", unit_text, "kept verbatim")
        else:
            moved_here.extend(lines[number - 1:int(unit["end_line"])])
            unit["_destination"], unit["_dest_text"] = note_path, unit_text
            row(unit, note_path, unit_text, "moved verbatim")
        if norm not in first_by_norm:
            first_by_norm[norm] = unit
        number = int(unit["end_line"]) + 1
    flush_section()
    draft_text = "\n".join(draft) + "\n"
    note = ["---", "type: reference", "status: standing",
            "created: " + datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "tags: [claude-md, diet, moved-rules]",
            "description: \"Standing rules moved verbatim out of ~/.claude/CLAUDE.md by the ACC8 diet, one section per original heading\"",
            "---", "",
            "# Standing rules moved out of CLAUDE.md (ACC8 diet)", "",
            "Every bullet below is verbatim from the rules file it left, under the heading it had there. "
            "The rules file points here by absolute path. Nothing here is retired; the retention map in the "
            "evidence directory lists each bullet's origin line.", ""]
    for head, body in note_sections:
        note.append(head)
        note.extend(body)
        note.append("")
    return draft_text, "\n".join(note) + "\n", rows


def locate(text: str, draft_text: str, note_path: Optional[str], home: str, repo_root: str
           ) -> List[Dict[str, object]]:
    """Rows for an existing draft: where each source unit is found, or None."""
    units = ri.classify_all(text, home, repo_root)
    existing = ri.existing_cited_paths(units, home, repo_root)
    draft_norm = _norm(draft_text)
    note_text = ri.read_rules(note_path) if note_path else None
    note_norm = _norm(note_text) if note_text else ""
    draft_lines = draft_text.split("\n")
    lines = text.split("\n")
    rows = []  # type: List[Dict[str, object]]
    heading = ""
    for unit in units:
        if unit["heading_line"]:
            heading = lines[int(unit["heading_line"]) - 1]
        norm = str(unit["norm"])
        destination = None  # type: Optional[str]
        dest_text = ""
        how = "not found"
        candidates = [str(unit["text"])]
        paths = ", ".join(p for p in unit["paths"] if p in existing)
        if paths and ri.ENFORCED_RE.search(str(unit["text"])):
            # a pointer an older diet may have written for this unit, whatever
            # this classifier now says: its status is then compared, not assumed
            for row in draft_lines:
                if row.startswith("- ENFORCEMENT: ENFORCED by " + paths):
                    candidates.append(row + "\n")
        if unit["class"] == "SUPERSEDED-TEXT" and rd.surviving_line(unit) is not None:
            candidates.append(str(rd.surviving_line(unit)) + "\n")
        for cand in candidates:
            if _norm(cand) in draft_norm:
                destination, dest_text, how = "draft", cand, "found in draft"
                break
            if note_norm and _norm(cand) in note_norm:
                destination, dest_text, how = note_path, cand, "found in note"
                break
        rows.append({
            "line": int(unit["line"]), "end_line": int(unit["end_line"]), "sha256": unit["sha256"],
            "heading": heading, "class": unit["class"], "how": how,
            "status": ri.enforcement_status(str(unit["text"])),
            "original": unit["text"], "destination": destination, "dest_text": dest_text,
        })
    return rows


def map_document(source_text: str, rows: List[Dict[str, object]]) -> Dict[str, object]:
    data = source_text.encode("utf-8")
    return {"schema": "acc8-retention/1", "source_bytes": len(data), "source_sha256": ri._sha256(data),
            "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "rows": rows}


def map_markdown(doc: Dict[str, object]) -> str:
    out = ["# ACC8 retention map", "",
           "Source: %d bytes, sha256 %s. One row per bullet unit of the current rules file." % (
               doc["source_bytes"], doc["source_sha256"]), ""]
    for row in doc["rows"]:
        out.append("## line %d (%s, status %s): %s" % (row["line"], row["class"], row["status"], row["how"]))
        out.append("- heading: " + str(row["heading"]))
        out.append("- original: " + str(row["original"]).rstrip("\n"))
        out.append("- destination: " + str(row["destination"]))
        out.append("- there as: " + str(row["dest_text"]).rstrip("\n"))
        out.append("")
    return "\n".join(out) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="split the rules file into a short draft, a vault note and a retention map")
    ap.add_argument("--rules", default=os.path.expanduser(ri.DEFAULT_RULES))
    ap.add_argument("--home", default=os.path.expanduser("~"))
    ap.add_argument("--repo", default=ri.ROOT)
    ap.add_argument("--keep", help="file of unit start lines kept verbatim in the draft, one per line")
    ap.add_argument("--triggers", help="JSON object: heading line number to the lookup trigger of its moved bullets")
    ap.add_argument("--note", help="absolute path of the vault note that receives the moved bullets")
    ap.add_argument("--draft", required=True, help="the draft rules file to write (or, with --map-only, to read)")
    ap.add_argument("--out", required=True, help="directory for RETENTION.json and RETENTION.md")
    ap.add_argument("--map-only", action="store_true", help="locate each unit in an existing draft and note; write nothing else")
    args = ap.parse_args(argv)
    text = ri.read_rules(args.rules)
    if text is None:
        print("NO-DATA: rules file unreadable or not UTF-8: %s" % args.rules)
        return 2
    if args.map_only:
        draft_text = ri.read_rules(args.draft)
        if draft_text is None:
            print("NO-DATA: draft unreadable or not UTF-8: %s" % args.draft)
            return 2
        rows = locate(text, draft_text, args.note, args.home, args.repo)
    else:
        if not args.note:
            print("NO-DATA: --note is required to build a split")
            return 2
        draft_text, note_text, rows = split(text, read_keep(args.keep), args.note, args.home, args.repo,
                                            read_triggers(args.triggers))
        os.makedirs(os.path.dirname(args.note), exist_ok=True)
        rd.write_private(args.note, note_text)
        rd.write_private(args.draft, draft_text)
    os.makedirs(args.out, exist_ok=True)
    doc = map_document(text, rows)
    rd.write_private(os.path.join(args.out, MAP_NAME), json.dumps(doc, indent=1, sort_keys=True) + "\n")
    rd.write_private(os.path.join(args.out, MAP_MD_NAME), map_markdown(doc))
    located = sum(1 for r in rows if r["destination"])
    print("source_bytes=%d draft_bytes=%d units=%d located=%d moved=%d" % (
        len(text.encode("utf-8")), len(draft_text.encode("utf-8")), len(rows), located,
        sum(1 for r in rows if r["destination"] not in (None, "draft"))))
    print("wrote %s and %s (mode 600)" % (os.path.join(args.out, MAP_NAME), os.path.join(args.out, MAP_MD_NAME)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
