"""annotations_store: a correction made once is a fact from then on.

ANNOTATE TO REMEMBER (Dify Annotation Reply), part 2 of decision A
(docs/decisions/intake-speed-plan-v2-2026-09-06.json): scripts/intake_form.py
lets a reader save a note beside a mark; this module is where that note
survives past the one record it was made on. Every entry is kept at
docs/decisions/annotations.json, a plain JSON list, so a human can read or
edit the whole store in a text editor without this tool.

WHY THE MATCH IS OPTION AND CRITERION, NOT RECORD: a correction like "the
tokens mark for option A undercounts the drafter's own overhead" is a fact
about that option-and-criterion combination, not about the one JSON file it
happened to be typed into. The `record` field is kept on every entry for
provenance (which record first surfaced it), never used to narrow a match,
because narrowing by record is exactly what would make the same correction
get re-typed the next time a similar decision is drafted.

DEDUPE ON (option, criterion, note): the same fix noted twice, from two
different records or the same one twice, is one entry, not two. A dedupe
keyed only on those three fields treats two independently-typed but
identical corrections as the same fact, which is the point: the store holds
facts, not a log of who typed what when.

Python 3, standard library only. No network.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE_REL = os.path.join("docs", "decisions", "annotations.json")

NODATA = "NO-DATA"


def store_path(root):
    return os.path.join(root, STORE_REL)


def load_annotations(root):
    """The store, as a list. Never raises: a missing or unparsable store is
    the same as an empty one, since a store that has never been written to
    is not an error, only a project with nothing corrected yet."""
    path = store_path(root)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _load_for_rewrite(root):
    """(entries, error) for a caller about to rewrite the whole store. Unlike
    load_annotations, an unreadable or malformed store is an error here: a
    rewrite built on [] would erase every correction already in it."""
    path = store_path(root)
    if not os.path.isfile(path):
        return [], None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        data, why = None, str(exc)
    else:
        why = "it holds %s, not a list" % type(data).__name__
    if isinstance(data, list):
        return data, None
    return None, ("%s: the store %s could not be read (%s); refusing to rewrite "
                  "it, which would erase every correction in it. Fix or move "
                  "the file, then run this again." % (NODATA, path, why))


def save_annotations(root, entries):
    path = store_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=2)
        fh.write("\n")


def _next_id(entries):
    n = 0
    for e in entries:
        eid = str(e.get("id", ""))
        if eid.startswith("a") and eid[1:].isdigit():
            n = max(n, int(eid[1:]))
    return "a%d" % (n + 1)


def _dedupe_key(entry):
    return (entry.get("option"), entry.get("criterion"), entry.get("note"))


def annotation_for(annotations, option, criterion):
    """The most recently added entry matching (option, criterion), or None.
    Matched on those two fields alone; see the module docstring for why
    `record` never narrows this. 'Most recently added' is list order (the
    store and a record's own annotations list are both append-only), so a
    later correction is the one shown, not an earlier one it superseded."""
    found = None
    for a in annotations or []:
        if a.get("option") == option and a.get("criterion") == criterion:
            found = a
    return found


def add_from_record(root, record_path):
    """(added, skipped, error). Pulls RECORD.json's own `annotations` list
    (intake_form.py's shape: persona, time, option, criterion, note) into
    the project store, assigning an id and mapping `time` to the store's
    `at` field. A record with no annotations, or that cannot be read, adds
    nothing and is not an error: not every record has a correction in it."""
    try:
        with open(record_path, encoding="utf-8") as fh:
            record = json.load(fh)
    except (OSError, ValueError) as exc:
        return 0, 0, "%s: could not read %s: %s" % (NODATA, record_path, exc)
    incoming = record.get("annotations") or []
    if not incoming:
        return 0, 0, None

    entries, error = _load_for_rewrite(root)
    if error:
        return 0, 0, error
    have = {_dedupe_key(e) for e in entries}
    record_name = os.path.basename(record_path)
    added, skipped = 0, 0
    for a in incoming:
        candidate = {"option": a.get("option"), "criterion": a.get("criterion"),
                     "note": a.get("note")}
        key = _dedupe_key(candidate)
        if key in have:
            skipped += 1
            continue
        entry = {"id": _next_id(entries), "option": a.get("option"),
                  "criterion": a.get("criterion"), "note": a.get("note"),
                  "persona": a.get("persona", ""), "at": a.get("time", ""),
                  "record": record_name}
        entries.append(entry)
        have.add(key)
        added += 1
    if added:
        save_annotations(root, entries)
    return added, skipped, None


def remove(root, entry_id):
    """True if entry_id was present and removed, False if it was not
    found. Not finding an id to remove is a normal outcome (someone already
    removed it, or mistyped it), so this returns a bool rather than
    raising."""
    entries, error = _load_for_rewrite(root)
    if error:
        print(error, file=sys.stderr)
        return False
    kept = [e for e in entries if str(e.get("id")) != entry_id]
    if len(kept) == len(entries):
        return False
    save_annotations(root, kept)
    return True


def _print_table(entries):
    for e in entries:
        print("%-5s opt=%-10s crit=%-14s %s (%s, %s, from %s)"
              % (e.get("id", ""), e.get("option", ""), e.get("criterion", ""),
                 e.get("note", ""), e.get("persona", ""), e.get("at", ""),
                 e.get("record", NODATA)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="pull a record's annotations list into the store")
    p_add.add_argument("record", help="a decide.py/intake_form.py-shaped JSON file")

    p_list = sub.add_parser("list", help="print every stored correction")
    p_list.add_argument("--json", action="store_true")

    p_remove = sub.add_parser("remove", help="remove one entry by id")
    p_remove.add_argument("id")

    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.command == "add":
        added, skipped, error = add_from_record(ROOT, args.record)
        if error:
            print(error, file=sys.stderr)
            return 2
        print("added %d, skipped %d already-known" % (added, skipped))
        return 0

    if args.command == "list":
        entries = load_annotations(ROOT)
        if not entries:
            print("%s: the store is empty" % NODATA)
            return 2
        if args.json:
            print(json.dumps(entries, indent=2))
        else:
            _print_table(entries)
        return 0

    if args.command == "remove":
        if remove(ROOT, args.id):
            print("removed %s" % args.id)
            return 0
        print("%s: no entry %r in the store" % (NODATA, args.id), file=sys.stderr)
        return 2

    return 2


if __name__ == "__main__":
    sys.exit(main())
