#!/usr/bin/env python3
"""bm_vault_heat_temporal: heat-scored promotion and bi-temporal fields (row V8,
docs/plan/READINESS-ROADMAP-2026-08-29.json).

NAMING NOTE, read before wondering why this is not bm_vault_temporal.py. V8's
own "owns" list names bm_vault_promote.py and bm_vault_temporal.py, but both
names are already taken by shipped, unrelated work found while building this
row: bm_vault_temporal.py is D09's five field valid_from, valid_to,
observed_at, ingested_at, verified_at contract, and bm_vault_promote.py is
WBS 9's distillation cadence nudge. Neither does what this row asks (a heat
counter earned by a mechanical count, plus a four field valid_from, valid_to,
recorded_at, superseded_by as_of query), and overwriting either would destroy
already shipped work with no relation to V8. This module ships under its own
name instead of colliding with them.

WHAT THIS SHIPS, the two mechanisms the outside research scored highest
(207 and 214 of 235, this row's own why_now), both additive, nothing that
edits an existing note's body:

  1. A HEAT COUNTER. Advisory recall promotion earned by a mechanical count of
     how many times a note was actually shown to a session, never by a
     model's opinion. record_recall() increments one note's count by one;
     promote(threshold) returns the note ids whose count has reached the
     threshold, a plain integer comparison. One JSON file, {note_id: count},
     written next to bm_vault.py's own index: the same directory
     bm_vault_audit.py's own AUDIT_PATH already resolves against, found by
     the identical dynamic import of bm_vault.py this file also uses, so a
     vault's local, non content state stays in one place.

  2. BI-TEMPORAL FIELDS. Four frontmatter fields: valid_from, valid_to,
     recorded_at, superseded_by. An absent field means: recorded_at equals
     created, valid_from equals created, valid_to open (still valid).
     as_of(notes, when) returns exactly the note ids true at `when`:
     valid_from at or before `when`, valid_to absent or after `when`, and not
     superseded by a note (found through superseded_by, resolved against the
     same input set) whose own recorded_at is at or before `when`.
     superseded_by is spelled with an underscore to match the field the
     scripts/vault_correct.py lane (row V13, out of this row's scope) writes
     onto a corrected note.

Exit 0 clean, 1 on a bad --date, 2 NO-DATA (no vault, or no readable heat
file where the caller asked for one that must already exist). Python 3.9,
standard library only, no network.

No em or en dashes anywhere in this file.
"""
import argparse
import datetime
import importlib.util
import json
import os
import re
import sys

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_bm_vault():
    """Dynamic import by path, the same defensive pattern bm_vault_audit.py's
    own _load_bm_vault already uses: a bare `import bm_vault` only resolves
    by accident of sys.path, and this file sets up none of its own. Used for
    one constant, INDEX_PATH, so the heat file can never drift from the
    directory the index itself already lives in."""
    spec = importlib.util.spec_from_file_location(
        "bm_vault", os.path.join(_TOOLS_DIR, "bm_vault.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HEAT_PATH = os.path.join(os.path.dirname(_load_bm_vault().INDEX_PATH), "bm_vault_heat.json")

FIELDS = ("valid_from", "valid_to", "recorded_at", "superseded_by")
SKIP_DIRS = {".git", ".trash", ".obsidian"}
_FIELD_RES = {f: re.compile(r"^%s:\s*(.+?)\s*$" % f, re.M) for f in FIELDS}
_CREATED_RE = re.compile(r"^created:\s*(\S+)\s*$", re.M)


def _frontmatter(text):
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end != -1 else ""


def _strip_quotes(raw):
    return raw.strip().strip('"').strip("'")


def _parse_date(raw):
    """A date or None. Quotes tolerated, garbage refused rather than coerced,
    the same stance bm_vault_temporal.py's own _parse_date already takes."""
    try:
        return datetime.date.fromisoformat(_strip_quotes(raw))
    except (ValueError, AttributeError):
        return None


def parse_note(text, created=None):
    """One note's bi-temporal record: {"valid_from", "valid_to", "recorded_at",
    "superseded_by"}, plus problems: [(field, reason)] for a date that does
    not parse. An absent field falls back per the module contract: recorded_at
    and valid_from default to `created` (the note's own created: date, passed
    in by the caller since this module never re-parses that field itself);
    valid_to defaults to None (open, still valid); superseded_by defaults to
    None and is never date-coerced, it names another note's id.

    A malformed date is left out of the record (None), never guessed into
    looking current: the same "reported, never silently included" stance
    bm_vault_temporal.py's own parse() already takes for its five fields."""
    block = _frontmatter(text)
    problems = []
    raw = {}
    for f in FIELDS:
        m = _FIELD_RES[f].search(block)
        if m:
            raw[f] = _strip_quotes(m.group(1))
    record = {"superseded_by": raw.get("superseded_by") or None}
    for f in ("valid_from", "valid_to", "recorded_at"):
        if f not in raw:
            record[f] = created if f != "valid_to" else None
            continue
        d = _parse_date(raw[f])
        if d is None:
            problems.append((f, "unparseable date %r" % raw[f]))
            record[f] = None
        else:
            record[f] = d
    return record, problems


def as_of(notes, when):
    """notes: {note_id: record}. Returns the sorted ids true at `when`:
    valid_from at or before `when`, valid_to absent or after `when`, and not
    superseded by a note (looked up in this same `notes` dict through its own
    superseded_by field) whose own recorded_at is at or before `when`. A note
    missing valid_from, or one whose superseded_by target is absent from
    `notes` or itself carries no recorded_at, is judged on what it does
    carry: never crashes, never silently included by a guess."""
    result = []
    for note_id, rec in notes.items():
        vf = rec.get("valid_from")
        if vf is None or vf > when:
            continue
        vt = rec.get("valid_to")
        if vt is not None and vt <= when:
            continue
        sup = rec.get("superseded_by")
        if sup and sup in notes:
            sup_recorded = notes[sup].get("recorded_at")
            if sup_recorded is not None and sup_recorded <= when:
                continue
        result.append(note_id)
    return sorted(result)


def _walk_notes(vault):
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(filenames):
            if fn.endswith(".md"):
                yield os.path.join(dirpath, fn)


def scan_vault(vault):
    """{note_id: record} for every markdown note under `vault`, note_id being
    the file's stem, the same identity scripts/vault_correct.py's own
    find_note() already uses for a wikilink target. A note that cannot be
    read is skipped and named on stderr, never silently dropped; a note with
    a malformed date field is included with that field as None and the
    reason printed, never guessed into looking current."""
    notes = {}
    for path in _walk_notes(vault):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            sys.stderr.write("bm_vault_heat_temporal: cannot read %s (%s); excluded\n"
                             % (path, exc))
            continue
        block = _frontmatter(text)
        m = _CREATED_RE.search(block)
        created = _parse_date(m.group(1)) if m else None
        record, problems = parse_note(text, created=created)
        for field, reason in problems:
            sys.stderr.write("bm_vault_heat_temporal: %s: %s: %s\n" % (path, field, reason))
        note_id = os.path.splitext(os.path.basename(path))[0]
        notes[note_id] = record
    return notes


def _load_heat(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        sys.stderr.write("bm_vault_heat_temporal: cannot read %s (%s); starting empty\n"
                         % (path, e))
        return {}


def _save_heat(path, counts):
    """Write-then-rename: a crash mid-write leaves the old file intact
    rather than a half-written JSON no reader can parse."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(counts, f, indent=1, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def record_recall(note_id, path=None):
    """Increment note_id's heat counter by one and return the new count. The
    ONLY writer of the heat file; every reader elsewhere treats it as
    read-only. This is the mechanical counter the done-check asks never be a
    model's opinion: one call, one integer, no judgment."""
    path = path or HEAT_PATH
    counts = _load_heat(path)
    counts[note_id] = counts.get(note_id, 0) + 1
    _save_heat(path, counts)
    return counts[note_id]


def promote(threshold, path=None):
    """Sorted note ids whose heat counter has reached `threshold`. A plain
    integer comparison, never a model's opinion."""
    path = path or HEAT_PATH
    counts = _load_heat(path)
    return sorted(nid for nid, n in counts.items() if n >= threshold)


def cmd_heat(args):
    path = args.path or HEAT_PATH
    before = _load_heat(path)
    print("heat counters before: %d note(s) tracked" % len(before))
    for note_id in sorted(before):
        print("  %s: %d" % (note_id, before[note_id]))
    if args.record:
        record_recall(args.record, path)
    after = _load_heat(path)
    print("heat counters after: %d note(s) tracked" % len(after))
    for note_id in sorted(after):
        print("  %s: %d" % (note_id, after[note_id]))
    promoted = promote(args.threshold, path)
    print("promoted at threshold %d: %d note(s)" % (args.threshold, len(promoted)))
    for note_id in promoted:
        print("  %s" % note_id)
    return 0


def cmd_as_of(args):
    vault = args.vault or os.environ.get("BM_VAULT_ROOT") or os.environ.get("BROTHERMODE_VAULT")
    if not vault or not os.path.isdir(vault):
        sys.stderr.write("bm_vault_heat_temporal: NO-DATA, no readable vault at %r\n" % vault)
        return 2
    when = _parse_date(args.date)
    if when is None:
        sys.stderr.write("bm_vault_heat_temporal: as-of needs --date YYYY-MM-DD\n")
        return 1
    notes = scan_vault(vault)
    today = datetime.date.today()
    then_ids = as_of(notes, when)
    now_ids = as_of(notes, today)
    print("as of %s versus today %s: %d note(s) tracked" % (when, today, len(notes)))
    print("true as of %s: %d" % (when, len(then_ids)))
    for nid in then_ids:
        print("  %s" % nid)
    print("true today: %d" % len(now_ids))
    for nid in now_ids:
        print("  %s" % nid)
    return 0


def _build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    ph = sub.add_parser("heat", help="heat counters before/after, plus promotion at --threshold")
    ph.add_argument("--path", default=None)
    ph.add_argument("--record", default=None,
                     help="note id to increment before printing the after counts")
    ph.add_argument("--threshold", type=int, default=3)
    pa = sub.add_parser("as-of", help="notes true as of --date versus today")
    pa.add_argument("--vault", default=None)
    pa.add_argument("--date", required=True)
    return p


def main(argv=None):
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.cmd == "heat":
        return cmd_heat(args)
    return cmd_as_of(args)


if __name__ == "__main__":
    sys.exit(main())
