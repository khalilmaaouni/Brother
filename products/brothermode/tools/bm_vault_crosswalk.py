#!/usr/bin/env python3
"""The crosswalk contract: many source-IDs, one entity.

WHY THIS EXISTS. Benchmark row D06, NO-DATA until 2026-08-30: the same THING
is named differently in every system that touches it. The BrotherModeUp
repository is `khalilmaaouni/BrotherModeUp` to GitHub, `~/Documents/BrotherModeUp`
to this machine, and `n-826b7bfac74243bc` to the vault's id layer. Nothing
recorded that those strings denote one entity, so a question about the entity
could only pull records keyed by whichever name the asker happened to hold.
The entity layer (D14, bm_vault_entity) gave the vault THINGS to talk about;
this module records their names in other systems.

THE SHAPE ON DISK. An entity note (a note declaring `entity:`) additionally
declares system-qualified source-IDs:

  source_ids: [github:khalilmaaouni/BrotherModeUp, vault:n-826b7bfac74243bc, path:~/Documents/BrotherModeUp]

The system prefix comes from SYSTEMS and everything after the first colon is
the ID verbatim in that system's own spelling. A crosswalk that normalises the
foreign ID stops matching the foreign system's records, which defeats it.

WHAT COUNTS AND WHAT IS A FINDING, both directions calibrated:
  - source_ids on a note with no entity: declaration is a FINDING. A crosswalk
    maps IDs to things, and hanging one on a document is how a token would game
    the capability without building it.
  - an unknown system prefix or an entry with no colon is a FINDING, never a
    silent skip: dropping it hides a real name, keeping it invents a system.
  - a vault: entry whose id resolves to no note in the vault is a DANGLING
    reference, a FINDING named per entry. Only vault: ids are existence-checked:
    a path: or github: entry names something in ANOTHER namespace, whose
    existence is that system's fact, not this vault's.
  - one source-ID claimed by two entities is a FINDING: a crosswalk where a
    lookup returns two things resolves nothing.

ZERO DECLARATIONS IS NO-DATA, never a pass, same doctrine as the entity layer:
an empty crosswalk has nothing to verify and reporting it clean is the
absence-reads-as-success shape this estate's benchmark caught three times in
one evening.

RESOLUTION IS EXACT. `resolve --source-id X` matches X against declared
entries: the full system-qualified form, or the bare value when the caller
does not know the system, and a bare value that different entities claim under
different systems refuses to pick one and names both. A miss is an honest
miss at exit 1, never a fuzzy guess.

DATED, TYPED MAPPINGS (VB6-07). A customer is many-to-many over time:
corporate groups, renames, reused legacy ids, billing-versus-contracting
parties. A plain `system:id` entry cannot say WHEN a mapping held or WHAT
KIND it is, and a reused id bleeds across the boundary where it changed
hands. An entry may carry trailing `;field=value` metadata after the id:

  vault:n-...;valid_from=2020-01-01;valid_to=2022-06-01;relationship=renamed_from;recorded_at=2026-08-30

Fields, all optional: `valid_from`/`valid_to` (ISO date, empty or omitted
means unbounded on that side), `relationship` (one of RELATIONSHIPS, default
`same_as`), `recorded_at` (free-form, when the mapping was recorded, not
used for resolution). An entry with no metadata is an OPEN-INTERVAL same_as
mapping, exactly as before: this keeps every undated mapping already on disk
readable, unchanged. An unknown field, a malformed date, an unknown
relationship, or valid_from after valid_to is a FINDING named per entry,
never silently ignored or silently accepted.

`resolve --as-of DATE` restricts matches to entries whose interval covers
DATE. The interval is INCLUSIVE on both ends: a mapping is valid ON its
valid_from date and ON its valid_to date, not strictly between them. A
mapping outside its interval never answers, so a reused id maps to
different entities before and after its boundary and never bleeds across it.
Without --as-of, every entry answers regardless of interval (the old,
undated behaviour). Two open same_as mappings for one id at one date is
AMBIGUOUS, reported by name, never a silent pick.

An ended mapping is never deleted: ending a mapping sets its valid_to, the
entry (and the note declaring it) stays on disk, and it still resolves
correctly for any date before that boundary.

DUPLICATE-CLAIM DETECTION IS INTERVAL-AWARE. The same system:id claimed by
two entities is only a FINDING when their intervals OVERLAP; two entities
legitimately reusing one legacy id across non-overlapping time are not a
conflict, that is exactly the case this row exists to record. Because both
ends are inclusive, two intervals that merely TOUCH at one shared day (one
ending and the other beginning on the same date) DO overlap: that day is
valid under both, so the boundary itself is a conflict, never a clean
handoff.

Exit 0 clean or resolved, 1 findings or an honest miss, 2 NO-DATA. Python 3.9
floor, standard library only, writes nothing anywhere.
"""
import argparse
import datetime
import os
import re
import sys

SYSTEMS = ("github", "path", "vault", "plugin", "artifact")
RELATIONSHIPS = ("same_as", "billing_party", "contracting_party", "branch_of",
                  "renamed_from", "reused_id")
META_FIELDS = ("valid_from", "valid_to", "relationship", "recorded_at")
SOURCE_IDS_RE = re.compile(r"(?m)^source_ids:\s*(.*)$")
ENTITY_RE = re.compile(r"(?m)^entity:\s*(\S+)\s*$")
ID_RE = re.compile(r"(?m)^id:\s*(n-[0-9a-f]{16})\s*$")
SKIP_DIRS = {".git", ".trash", ".obsidian"}
MIN_DATE = datetime.date.min
MAX_DATE = datetime.date.max


def _parse_date(value, label, raw, problems):
    """Return a datetime.date for a non-empty ISO value, else None.

    A malformed date is a problem named per entry, never a silent drop.
    """
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        problems.append("entry %r has invalid %s date %r" % (raw, label, value))
        return None


def _frontmatter(text):
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end != -1 else ""


def _split_top_level(body):
    """Split on commas, but never inside a quoted entry.

    A quoted value may itself contain a comma, e.g. "path:/tmp/My Docs, Old".
    Splitting on every comma before the quotes are honoured tears such an
    entry in half: the id gets silently truncated while a stray fragment
    trips the no-colon finding. Track quote state so an in-quote comma is
    never a separator.
    """
    parts, current, quote = [], [], None
    for ch in body:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            current.append(ch)
        elif ch == ",":
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def parse_source_ids(value):
    """[entry-dict] plus [problem] from a source_ids: value.

    Accepts the one-line list form `[a, b]` or a bare comma-separated value.
    Each raw item is `system:id` optionally followed by `;field=value`
    metadata (valid_from, valid_to, relationship, recorded_at); an entry
    with no metadata is an open-interval same_as mapping. An entry with no
    colon, an unknown system, an unknown field, a malformed date, valid_from
    after valid_to, or an unknown relationship lands in problems, verbatim,
    so the finding names exactly what was typed.

    Each surviving entry is a dict: system, ident, raw (the id part only,
    e.g. "github:a/b", used for exact-match resolution the way it always
    was), full_raw (the whole entry as typed, for display), valid_from,
    valid_to (datetime.date or None, None means unbounded on that side),
    relationship, recorded_at.
    """
    entries, problems = [], []
    body = value.strip()
    if body.startswith("[") and body.endswith("]"):
        body = body[1:-1]
    for full_raw in (p.strip().strip('"').strip("'") for p in _split_top_level(body)):
        if not full_raw:
            continue
        id_part, *meta_parts = full_raw.split(";")
        system, sep, ident = id_part.partition(":")
        if not sep or not ident:
            problems.append("entry %r has no system: prefix" % full_raw)
            continue
        if system not in SYSTEMS:
            problems.append("entry %r names unknown system %r, not in %s"
                            % (full_raw, system, "/".join(SYSTEMS)))
            continue
        meta = {}
        bad = False
        for part in meta_parts:
            key, msep, val = part.partition("=")
            key = key.strip()
            val = val.strip()
            if not msep or key not in META_FIELDS:
                problems.append("entry %r has unknown field %r, not in %s"
                                % (full_raw, part, "/".join(META_FIELDS)))
                bad = True
                continue
            meta[key] = val
        if bad:
            continue
        relationship = meta.get("relationship") or "same_as"
        if relationship not in RELATIONSHIPS:
            problems.append("entry %r names unknown relationship %r, not in %s"
                            % (full_raw, relationship, "/".join(RELATIONSHIPS)))
            continue
        problems_before = len(problems)
        valid_from = _parse_date(meta.get("valid_from", ""), "valid_from", full_raw, problems)
        valid_to = _parse_date(meta.get("valid_to", ""), "valid_to", full_raw, problems)
        if len(problems) > problems_before:
            continue
        if valid_from is not None and valid_to is not None and valid_from > valid_to:
            problems.append("entry %r has valid_from %s after valid_to %s"
                            % (full_raw, valid_from, valid_to))
            continue
        entries.append({
            "system": system, "ident": ident, "raw": id_part, "full_raw": full_raw,
            "valid_from": valid_from, "valid_to": valid_to,
            "relationship": relationship, "recorded_at": meta.get("recorded_at", ""),
        })
    return entries, problems


def _covers(entry, as_of):
    vf = entry["valid_from"] or MIN_DATE
    vt = entry["valid_to"] or MAX_DATE
    return vf <= as_of <= vt


def _overlaps(a, b):
    a_from, a_to = a["valid_from"] or MIN_DATE, a["valid_to"] or MAX_DATE
    b_from, b_to = b["valid_from"] or MIN_DATE, b["valid_to"] or MAX_DATE
    return a_from <= b_to and b_from <= a_to


def walk(vault):
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(filenames):
            if fn.endswith(".md"):
                yield os.path.join(dirpath, fn)


def load(vault):
    """The crosswalk as declared on disk.

    Returns (decls, note_ids, findings) where decls is
    [{entity, entity_type, path, entries}], note_ids is every stable note id
    in the vault (for the dangling check), and findings are (rel_path, problem)
    pairs. Reads every note once; dangling resolution happens in check, after
    the full id set is known.
    """
    decls, note_ids, findings = [], set(), []
    for path in walk(vault):
        rel = os.path.relpath(path, vault)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            findings.append((rel, "could not be read (%s)" % exc))
            continue
        front = _frontmatter(text)
        m = ID_RE.search(front)
        if m:
            note_ids.add(m.group(1))
        sm = SOURCE_IDS_RE.search(front)
        if not sm:
            continue
        em = ENTITY_RE.search(front)
        entries, problems = parse_source_ids(sm.group(1))
        for problem in problems:
            findings.append((rel, problem))
        if not em:
            findings.append((rel, "declares source_ids without entity:, a crosswalk "
                                  "maps IDs to things, not to documents"))
            continue
        decls.append({
            # Keyed on the vault-relative stem, not the basename: two notes
            # named the same in different folders are two different entities,
            # and a basename key silently collapsed them into one, hiding a
            # real duplicate-claim or AMBIGUOUS-resolve case.
            "entity": os.path.splitext(rel.replace(os.sep, "/"))[0],
            "entity_type": em.group(1).strip().strip('"'),
            "path": rel,
            "entries": entries,
        })
    return decls, note_ids, findings


def cmd_check(vault):
    decls, note_ids, findings = load(vault)
    claimed = {}  # (system, ident) -> [(entity, path, entry), ...]
    systems = {}
    for d in decls:
        for entry in d["entries"]:
            system, ident = entry["system"], entry["ident"]
            systems[system] = systems.get(system, 0) + 1
            key = (system, ident)
            for other_entity, other_path, other_entry in claimed.get(key, []):
                if other_entity != d["entity"] and _overlaps(entry, other_entry):
                    findings.append((d["path"],
                                     "source-ID %r already claimed by entity %r "
                                     "for an overlapping interval" % (entry["raw"], other_entity)))
            claimed.setdefault(key, []).append((d["entity"], d["path"], entry))
            if system == "vault" and ident not in note_ids:
                findings.append((d["path"], "DANGLING vault reference %r resolves to no "
                                            "note id in this vault" % entry["raw"]))
    if not decls and not findings:
        print("bm_vault_crosswalk: NO-DATA, no note declares source_ids, there is no "
              "crosswalk to verify", file=sys.stderr)
        return 2
    print("vault: %s" % vault)
    print("entities declaring a crosswalk: %d" % len(decls))
    print("source-IDs declared: %d across %d system(s)"
          % (sum(len(d["entries"]) for d in decls), len(systems)))
    for system in SYSTEMS:
        if system in systems:
            print("  %-9s %d" % (system, systems[system]))
    multi = sum(1 for d in decls if len({e["system"] for e in d["entries"]}) >= 2)
    print("entities crossing 2+ systems: %d" % multi)
    if findings:
        print("FINDINGS, each named, never silently skipped: %d" % len(findings))
        for rel, problem in findings:
            print("  %s: %s" % (rel, problem))
    return 1 if findings else 0


def cmd_resolve(vault, source_id, as_of=None):
    decls, _, _ = load(vault)
    if not decls:
        print("bm_vault_crosswalk: NO-DATA, no note declares source_ids, there is no "
              "crosswalk to resolve against", file=sys.stderr)
        return 2
    hits = []
    for d in decls:
        for entry in d["entries"]:
            if source_id == entry["raw"] or source_id == entry["ident"]:
                if as_of is None or _covers(entry, as_of):
                    hits.append((d["entity"], d["entity_type"], entry))
    if not hits:
        if as_of is not None:
            print("NO-DATA: %r is declared by no entity in this vault as of %s"
                  % (source_id, as_of))
        else:
            print("NO-DATA: %r is declared by no entity in this vault" % source_id)
        return 1
    entities = {h[0] for h in hits}
    if len(entities) > 1:
        print("AMBIGUOUS: %r is claimed by %d entities: %s. Qualify it with its system."
              % (source_id, len(entities),
                 ", ".join(sorted("%s (via %s)" % (e, h["full_raw"]) for e, _, h in hits))))
        return 1
    entity, etype, entry = hits[0]
    suffix = " as of %s" % as_of if as_of is not None else ""
    print("%s  entity=%s  relationship=%s  via %s%s"
          % (entity, etype, entry["relationship"], entry["full_raw"], suffix))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("check", "resolve"))
    ap.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    ap.add_argument("--source-id", help="for resolve: the foreign ID to look up")
    ap.add_argument("--as-of", help="for resolve: ISO date, restrict to mappings "
                                    "whose interval covers this date")
    args = ap.parse_args(argv)
    if not args.vault or not os.path.isdir(args.vault):
        print("bm_vault_crosswalk: NO-DATA, no readable vault at %r" % args.vault,
              file=sys.stderr)
        return 2
    if args.command == "check":
        return cmd_check(args.vault)
    if not args.source_id:
        print("bm_vault_crosswalk: resolve needs --source-id", file=sys.stderr)
        return 2
    as_of = None
    if args.as_of:
        try:
            as_of = datetime.date.fromisoformat(args.as_of)
        except ValueError:
            print("bm_vault_crosswalk: --as-of %r is not an ISO date" % args.as_of,
                  file=sys.stderr)
            return 2
    return cmd_resolve(args.vault, args.source_id, as_of)


if __name__ == "__main__":
    sys.exit(main())
