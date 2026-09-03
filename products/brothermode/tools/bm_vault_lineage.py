#!/usr/bin/env python3
"""bm_vault_lineage: one origin answer for a note, read from three existing
seams. WBS VB11-01.

WHY THIS EXISTS. A note's history is already recorded, three times over, by
three modules that never talk to each other: bm_vault_intake.py stamps
where a note came from at the moment it was admitted or captured;
bm_vault_events.py's payload-free stream records every upsert/correct/
tombstone naming the note by id; bm_vault_cite.py's citation records bind a
claim's hash to the note it cited. Answering "where did this note come
from, and what has touched it since" today means opening three files by
hand and cross-referencing ids yourself. This module is that cross-
reference, read-only, with NO FOURTH STORE of its own: every byte it prints
comes from a file one of the three seams already owns.

`show --vault V --id NOTE-ID` prints three sections, one per seam:

  INTAKE      the provenance stamp bm_vault_intake.py wrote into this note's
              own frontmatter at arrival (admit's provenance_* fields, or
              capture's captured_by/captured_at/session_context/wbs_row/
              expiry_* fields). Read directly off the note file; this seam
              needs no second file.
  EVENTS      every event in the vault's event stream whose `ref` names this
              note, in occurred_at order, reusing bm_vault_events.load_events
              for parsing so this module never re-derives the event schema.
              Default stream location: <vault>/.vault/events.jsonl (the same
              convention bm_vault_export.py's own --events default uses),
              overridable with --events PATH.
  CITATIONS   every citation record naming this note either as the cited
              target (note_id) or as the citing key (by), reusing
              bm_vault_cite.py's own resolver and hasher
              (_resolve/_hash_and_lifecycle/_read_citations) rather than a
              second parser of the same store -- a gate and its action never
              share a command, but a reader may be shared, and re-parsing a
              sibling's store is the recorded defect class this avoids.
              Default file: <vault>/99-System/citations.jsonl, overridable
              with --citations PATH.

NOTE ID RESOLUTION. --id accepts either a stable id (n-<16 hex>, resolved via
bm_vault_ids.resolve, the one place that mechanism lives) or a vault-relative
path (checked directly against the filesystem). A value that resolves
neither way is an honest miss: exit nonzero, the id named, nothing printed
that pretends to be an answer.

NO-DATA IS AN ANSWER, NOT A FAILURE. A seam with nothing recorded for this
note prints NO-DATA naming that seam, never an empty section and never
silently treated as a pass over other seams. The command still exits 0 in
that case: three honest NO-DATA lines about a note nobody has touched since
intake is a complete, correct origin answer. A missing seam FILE (the event
log or the citations file cannot be opened at all) is reported as NO-DATA
too, naming the path that could not be read, which is different from "the
file is fine but says nothing about this note" only in the reason printed.

Exit 0: the note resolved (whatever the three sections found). Exit 1: --id
resolved to no note in this vault. Exit 2: NO-DATA, no readable vault given.

Python 3.9, standard library only. Read-only: every open() here reads.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_cite as cite      # noqa: E402 -- reuse its resolver/hasher, never re-parse
import bm_vault_events as events  # noqa: E402 -- reuse load_events, never re-derive the schema
import bm_vault_ids as ids        # noqa: E402 -- reuse resolve(), the one id->path mechanism

EVENTS_DEFAULT_RELPATH = os.path.join(".vault", "events.jsonl")       # bm_vault_export's own convention
CITATIONS_DEFAULT_RELPATH = os.path.join("99-System", "citations.jsonl")

INTAKE_FIELDS = (
    "provenance_source", "provenance_actor", "provenance_ingested_at",
    "provenance_original_path",
    "captured_by", "captured_at", "session_context", "wbs_row",
    "expiry_class", "expiry_at",
    # VB3-13: derived memory cannot declassify. bm_vault_labels.py's
    # annotate_derivation() stamps these onto a composed/synthesized note at
    # derivation time; reading them here, off the note's own frontmatter like
    # every other INTAKE field, is the "lineage back to sources" half of that
    # row's own done-check. No second store: bm_vault_labels.py owns writing
    # them, this module only reads.
    "security_label", "derived_from_ids", "derived_from_labels", "derived_at",
    "security_label_changed_from", "security_label_changed_at",
)


def _frontmatter_dict(text):
    """Every flat `key: value` line inside the note's own frontmatter block,
    reusing bm_vault_ids.frontmatter for the block boundaries so this module
    never re-derives what counts as the frontmatter. Values are not
    YAML-parsed beyond a trim and quote-strip; every field this seam reads is
    a plain scalar, matching how bm_vault_intake.py itself writes them."""
    block, _, _ = ids.frontmatter(text)
    if block is None:
        return {}
    out = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if not key or key.startswith("-"):
            continue
        out[key] = value.strip().strip('"').strip("'")
    return out


def resolve_note(vault, ident):
    """(relpath, error_or_None). ident is either a stable id (n-<16 hex>,
    resolved through bm_vault_ids.resolve, the one place that mechanism
    lives) or a vault-relative path, checked directly. Neither resolving is
    an honest miss, never a guess at a third interpretation."""
    if ids.ID_VALUE_RE.match(ident):
        rel = ids.resolve(vault, ident, allow_stem=False)
        if rel is None:
            return None, "no note in %s declares id %s" % (vault, ident)
        return rel, None
    if os.path.isfile(os.path.join(vault, ident)):
        return ident, None
    return None, ("%r is neither a resolvable note id (n-<16 hex>) nor an "
                   "existing vault-relative path under %s" % (ident, vault))


def intake_for(text):
    """dict of whichever INTAKE_FIELDS this note's own frontmatter declares,
    or {} when none are present (the caller reports that as NO-DATA)."""
    fm = _frontmatter_dict(text)
    return {k: fm[k] for k in INTAKE_FIELDS if k in fm}


def events_for(note_id, events_path):
    """("ok"|"no-file"|"malformed", [event, ...]). "no-file" means
    events_path could not be opened at all (the boundary read's own
    explicit failure path); "ok" with an empty list means the stream is
    fine but names no event for note_id, which the caller reports as
    NO-DATA distinctly from a missing file. Parsing is
    bm_vault_events.load_events, never a second parser of the same schema."""
    if note_id is None or not os.path.isfile(events_path):
        return "no-file", []
    try:
        parsed = events.load_events([events_path])
    except events.FoldError as exc:
        return "malformed", [str(exc)]
    except OSError as exc:
        return "no-file", [str(exc)]
    matching = [e for e in parsed if e.get("ref") == note_id]
    matching.sort(key=lambda e: (e["occurred_at"], e["recorded_at"], e["event_key"]))
    return "ok", matching


def citations_for(vault, note_id, citations_path):
    """("ok"|"no-file", [entry, ...]). An entry covers both directions: a
    citation naming this note as its cited target (note_id) or as its
    citing key (by). Resolution and hashing are bm_vault_cite's own
    _resolve/_hash_and_lifecycle, reused directly rather than re-parsed, so
    the mismatch state this reports (SUPERSEDED-CONTENT) can never drift
    from what bm_vault_cite's own `check` would call the same record."""
    if note_id is None:
        return "no-file", []
    records = cite._read_citations(citations_path)
    if records is None:
        return "no-file", []
    entries = []
    for rec in records:
        target_id, by = rec.get("note_id"), rec.get("by")
        if target_id != note_id and by != note_id:
            continue
        direction = "CITED-BY" if target_id == note_id else "CITES"
        entry = {"direction": direction, "note_id": target_id, "by": by,
                  "recorded_hash": rec.get("content_sha256"),
                  "recorded_lifecycle": rec.get("lifecycle")}
        if not target_id:
            entry["state"] = "MALFORMED-RECORD"
        else:
            paths, unreadable = cite._resolve(vault, target_id)
            if len(paths) > 1:
                entry["state"] = "AMBIGUOUS-ID"
                entry["paths"] = paths
            elif not paths:
                entry["state"] = "UNREADABLE-SCAN" if unreadable else "MISSING"
            else:
                new_hash, new_life = cite._hash_and_lifecycle(paths[0])
                entry["current_hash"] = new_hash
                entry["current_lifecycle"] = new_life
                entry["state"] = ("MATCH" if new_hash == entry["recorded_hash"]
                                   else "SUPERSEDED-CONTENT")
        entries.append(entry)
    return "ok", entries


def _print_intake(intake):
    print("INTAKE")
    if not intake:
        print("  NO-DATA: INTAKE, no provenance or capture fields declared on this note")
        return
    for key in INTAKE_FIELDS:
        if key in intake:
            print("  %s: %s" % (key, intake[key]))


def _print_events(status, rows, events_path):
    print("EVENTS")
    if status == "no-file":
        print("  NO-DATA: EVENTS, no readable event log at %s" % events_path)
        return
    if status == "malformed":
        print("  ERROR: EVENTS, %s" % rows[0])
        return
    if not rows:
        print("  NO-DATA: EVENTS, no event in %s names this note" % events_path)
        return
    for e in rows:
        line = ("  event_key=%s kind=%s occurred_at=%s recorded_at=%s"
                % (e["event_key"], e["kind"], e["occurred_at"], e["recorded_at"]))
        if e.get("corrects"):
            line += " corrects=%s" % e["corrects"]
        print(line)


def _print_citations(status, rows, citations_path):
    print("CITATIONS")
    if status == "no-file":
        print("  NO-DATA: CITATIONS, no readable citation record at %s" % citations_path)
        return
    if not rows:
        print("  NO-DATA: CITATIONS, no record in %s names this note" % citations_path)
        return
    for r in rows:
        line = "  %s note_id=%s by=%s state=%s" % (
            r["direction"], r.get("note_id"), r.get("by"), r["state"])
        if r["state"] == "SUPERSEDED-CONTENT":
            line += " (%s -> %s)" % ((r.get("recorded_hash") or "")[:12],
                                       (r.get("current_hash") or "")[:12])
        print(line)


def cmd_show(args):
    vault = args.vault
    if not vault or not os.path.isdir(vault):
        print("bm_vault_lineage: NO-DATA, no readable vault at %r" % vault, file=sys.stderr)
        return 2

    rel, err = resolve_note(vault, args.id)
    if err:
        print("bm_vault_lineage: %s could not be resolved to a note: %s" % (args.id, err),
              file=sys.stderr)
        return 1

    path = os.path.join(vault, rel)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        print("bm_vault_lineage: %s resolved to %s but it could not be read: %s"
              % (args.id, rel, exc), file=sys.stderr)
        return 1

    note_id = ids.read_id(text) or args.id
    events_path = args.events or os.path.join(vault, EVENTS_DEFAULT_RELPATH)
    citations_path = args.citations or os.path.join(vault, CITATIONS_DEFAULT_RELPATH)

    intake = intake_for(text)
    ev_status, ev_rows = events_for(note_id, events_path)
    ci_status, ci_rows = citations_for(vault, note_id, citations_path)

    nodata = []
    if not intake:
        nodata.append("INTAKE")
    if not ev_rows:
        nodata.append("EVENTS")
    if not ci_rows:
        nodata.append("CITATIONS")

    if args.json:
        payload = {
            "note": {"id": note_id, "path": rel},
            "intake": intake or None,
            "events": ev_rows,
            "citations": ci_rows,
            "nodata": nodata,
        }
        print(json.dumps(payload, sort_keys=True))
        return 0

    print("NOTE %s (%s)" % (note_id, rel))
    print()
    _print_intake(intake)
    print()
    _print_events(ev_status, ev_rows, events_path)
    print()
    _print_citations(ci_status, ci_rows, citations_path)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("show",))
    ap.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    ap.add_argument("--id", required=True, help="a note id (n-<16 hex>) or vault-relative path")
    ap.add_argument("--events", default=None, help="override the event stream JSONL path")
    ap.add_argument("--citations", default=None, help="override the citations JSONL path")
    ap.add_argument("--json", action="store_true", help="emit one JSON object instead of prose")
    args = ap.parse_args(argv)
    return cmd_show(args)


if __name__ == "__main__":
    sys.exit(main())
