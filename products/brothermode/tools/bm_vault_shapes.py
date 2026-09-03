#!/usr/bin/env python3
"""bm_vault_shapes: three entity shapes on top of the crosswalk and entity layer (VB12-02).

WHY THIS EXISTS. The entity layer (bm_vault_entity) gave the vault THINGS to talk
about; the crosswalk (bm_vault_crosswalk) gave a dated, typed way for one thing to
be named differently across systems. Neither can say a thing sits BELOW another
thing for a while, that a thing is PLACED somewhere under a model that can change,
or that a thing in one namespace is the SAME thing as an id in someone else's feed.
Three founder-approved MDM recommendations, one module, all riding the same
dated-interval discipline crosswalk already proved: a fact never mutates in place,
it closes with a valid_to and a new dated fact opens beside it, history intact.

THE SHAPE ON DISK. All three record classes are frontmatter fields on ordinary
entity notes (a note declaring `entity: <type>`), never a second store:

  1. HIERARCHY EDGES, declared on the CHILD note:
       hierarchy_edges: [name=legal;parent=parent-corp;valid_from=2020-01-01;valid_to=2022-01-01,
                          name=trade;parent=trade-hq;valid_from=2020-01-01]
     Each entry is one directed edge from this note to a named parent, over an
     interval. The `name` is the hierarchy: legal and trade are two CONCURRENT
     hierarchies over the same entity set, told apart only by this name. A reorg
     is never an edit in place: the old entry gets its valid_to set (closing it)
     and a new entry with the new parent and a fresh valid_from is appended. Both
     stay on disk. `resolve-hierarchy --as-of DATE` walks the parent chain a
     hierarchy's edges form at that date.

  2. PLACEMENT RECORDS, declared on the ASSET note (the asset is never re-keyed):
       placements: [location=site-a;model=full-op;valid_from=2020-01-01;valid_to=2021-06-01,
                     location=site-a;model=semi-op;valid_from=2021-06-01]
     Each entry links this asset to a customer-location entity under an
     ownership model (MODELS below), over an interval. A conversion between
     models is a DATED DECISION: the old placement closes (valid_to set) and a
     new one opens; the asset's own id, type and note never change.

  3. THE COMPETITOR NAMESPACE plus its mapping table, declared on the competitor
     note (entity: competitor, plus a non-empty internal_id: field, its own
     namespace so an internal id never collides with our own entity ids):
       syndicated_mappings: [external=synd-001;source=vendor-a;valid_from=2020-01-01;valid_to=2021-01-01,
                              external=synd-002;source=vendor-b;valid_from=2021-01-02]
     Each entry maps this competitor's internal id to an external syndicated-data
     id from a named source, over an interval, `resolve-competitor --as-of DATE`
     answers which external id and source held on that date.

GRAMMAR, all three fields. Same `key=value;key=value` entry syntax as crosswalk's
source_ids, split on top-level commas (quoted values may embed a comma). Every
entry accepts valid_from/valid_to (ISO date, inclusive both ends, empty or absent
means unbounded on that side) and recorded_at (free-form, carried through, never
used for resolution). An entry missing a required field, naming an unknown field,
carrying a malformed date, or valid_from after valid_to is a FINDING named per
entry, never silently dropped or silently accepted. A note declaring any of the
three fields without an `entity:` field is a FINDING: these are facts about
things, not about documents.

OVERLAP IS INTERVAL-AWARE, mirroring the crosswalk's duplicate-claim rule, both
ends inclusive so two intervals that merely touch at one shared day DO overlap:
  - hierarchy_edges: two entries for the SAME (child, hierarchy name) whose
    intervals overlap is a FINDING -- a child cannot have two parents at once
    inside one hierarchy. Two different hierarchy names never conflict with
    each other, that is the whole point of naming them.
  - placements: two entries on the SAME asset whose intervals overlap is a
    FINDING -- an asset cannot be in two places, or under two models, at once.
  - syndicated_mappings: two entries on the SAME (competitor, source) whose
    intervals overlap is a FINDING -- one source cannot report two external ids
    for one competitor at once. Two different sources tracking the same
    competitor concurrently is not a conflict.

DANGLING REFERENCES. A hierarchy_edges `parent` or a placements `location` that
names no entity note anywhere in the vault is a FINDING, named per entry, same
doctrine as the crosswalk's DANGLING vault: reference.

ZERO DECLARATIONS ACROSS ALL THREE CLASSES IS NO-DATA, never a pass, same
doctrine as the crosswalk and the entity layer: nothing to verify, reporting
that as clean is the absence-reads-as-success shape this estate's benchmark
caught three times in one evening. Any one class alone may be empty without
that; NO-DATA only fires when hierarchy, placement and mapping declarations are
ALL absent and there are no findings either.

RESOLUTION IS HONEST. `resolve-hierarchy`/`resolve-placement`/`resolve-competitor`
answer NO-DATA when no interval covers the given date (including a date before
the first interval, or past the last valid_to), AMBIGUOUS when more than one
interval covers it (a check finding that resolve refuses to silently pick a
side on too), and a plain miss when the named entity does not exist at all.

Exit 0 clean or resolved, 1 findings or an honest miss/AMBIGUOUS, 2 NO-DATA.
Python 3.9 floor, standard library only, writes nothing anywhere.
"""
import argparse
import datetime
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_crosswalk as xw  # noqa: E402 -- reuse its date parsing and interval math

MODELS = ("full-op", "semi-op")
ENTITY_RE = xw.ENTITY_RE
ID_RE = xw.ID_RE
SKIP_DIRS = xw.SKIP_DIRS

HIERARCHY_FIELD = "hierarchy_edges"
PLACEMENT_FIELD = "placements"
MAPPING_FIELD = "syndicated_mappings"
INTERNAL_ID_RE = re.compile(r"(?m)^internal_id:\s*(.*)$")
FIELD_RE = {
    HIERARCHY_FIELD: re.compile(r"(?m)^hierarchy_edges:\s*(.*)$"),
    PLACEMENT_FIELD: re.compile(r"(?m)^placements:\s*(.*)$"),
    MAPPING_FIELD: re.compile(r"(?m)^syndicated_mappings:\s*(.*)$"),
}

_covers = xw._covers
_overlaps = xw._overlaps
_parse_date = xw._parse_date
_split_top_level = xw._split_top_level
_frontmatter = xw._frontmatter


def _parse_kv_entries(value):
    """[(dict of raw key->value, full_raw)] for a `key=value;key=value,...`
    field, same one-line-list-or-bare-comma-separated shape as the crosswalk's
    source_ids. Splitting into raw key/value pairs only; domain-specific
    validation (required fields present, dates, enums) is each caller's job so
    the error text can name what THAT grammar actually requires."""
    body = value.strip()
    if body.startswith("[") and body.endswith("]"):
        body = body[1:-1]
    out = []
    for full_raw in (p.strip().strip('"').strip("'") for p in _split_top_level(body)):
        if not full_raw:
            continue
        raw = {}
        for part in full_raw.split(";"):
            key, sep, val = part.partition("=")
            if sep:
                raw[key.strip()] = val.strip()
        out.append((raw, full_raw))
    return out


def _finish_entry(raw, full_raw, required, problems):
    """Validates a raw key/value dict against `required` field names, parses
    valid_from/valid_to/recorded_at, and returns the finished entry dict or
    None (with `problems` appended) on any violation."""
    known = set(required) | {"valid_from", "valid_to", "recorded_at"}
    unknown = sorted(set(raw) - known)
    if unknown:
        problems.append("entry %r has unknown field %r" % (full_raw, unknown[0]))
        return None
    missing = [f for f in required if not raw.get(f, "").strip()]
    if missing:
        problems.append("entry %r is missing required field %r" % (full_raw, missing[0]))
        return None
    before = len(problems)
    valid_from = _parse_date(raw.get("valid_from", ""), "valid_from", full_raw, problems)
    valid_to = _parse_date(raw.get("valid_to", ""), "valid_to", full_raw, problems)
    if len(problems) > before:
        return None
    if valid_from is not None and valid_to is not None and valid_from > valid_to:
        problems.append("entry %r has valid_from %s after valid_to %s"
                         % (full_raw, valid_from, valid_to))
        return None
    entry = {k: raw.get(k, "") for k in required}
    entry["valid_from"] = valid_from
    entry["valid_to"] = valid_to
    entry["recorded_at"] = raw.get("recorded_at", "")
    entry["full_raw"] = full_raw
    return entry


def parse_hierarchy_edges(value):
    entries, problems = [], []
    for raw, full_raw in _parse_kv_entries(value):
        entry = _finish_entry(raw, full_raw, ("name", "parent"), problems)
        if entry is not None:
            entries.append(entry)
    return entries, problems


def parse_placements(value):
    entries, problems = [], []
    for raw, full_raw in _parse_kv_entries(value):
        model = raw.get("model", "")
        if model and model not in MODELS:
            problems.append("entry %r names unknown model %r, not in %s"
                             % (full_raw, model, "/".join(MODELS)))
            continue
        entry = _finish_entry(raw, full_raw, ("location", "model"), problems)
        if entry is not None:
            entries.append(entry)
    return entries, problems


def parse_syndicated_mappings(value):
    entries, problems = [], []
    for raw, full_raw in _parse_kv_entries(value):
        entry = _finish_entry(raw, full_raw, ("external", "source"), problems)
        if entry is not None:
            entries.append(entry)
    return entries, problems


def walk(vault):
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(filenames):
            if fn.endswith(".md"):
                yield os.path.join(dirpath, fn)


def load(vault):
    """(hierarchy_decls, placement_decls, mapping_decls, entity_stems, findings).

    Each *_decls entry: {"entity": stem, "path": rel, "entries": [...]}.
    entity_stems: every stem in the vault that declares `entity:`, for the
    dangling-reference check. findings: (rel_path, problem) pairs collected
    while parsing, before any cross-entry check runs."""
    hierarchy_decls, placement_decls, mapping_decls = [], [], []
    entity_stems, findings = set(), []
    internal_ids = {}
    for path in walk(vault):
        rel = os.path.relpath(path, vault)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            findings.append((rel, "could not be read (%s)" % exc))
            continue
        front = _frontmatter(text)
        em = ENTITY_RE.search(front)
        # Keyed on the basename stem, like bm_vault_entity.py, not the
        # crosswalk's full relative-path stem: parent/location/competitor
        # references here are human-typed short names, not foreign-system
        # ids where path-qualification guards against a same-name collision.
        stem = os.path.splitext(os.path.basename(path))[0]
        if em:
            entity_stems.add(stem)
            im = INTERNAL_ID_RE.search(front)
            if im and im.group(1).strip():
                internal_ids[stem] = im.group(1).strip().strip('"')

        for field, regex, parser, bucket in (
            (HIERARCHY_FIELD, FIELD_RE[HIERARCHY_FIELD], parse_hierarchy_edges, hierarchy_decls),
            (PLACEMENT_FIELD, FIELD_RE[PLACEMENT_FIELD], parse_placements, placement_decls),
            (MAPPING_FIELD, FIELD_RE[MAPPING_FIELD], parse_syndicated_mappings, mapping_decls),
        ):
            m = regex.search(front)
            if not m:
                continue
            if not em:
                findings.append((rel, "declares %s without entity:, these are facts "
                                      "about things, not about documents" % field))
                continue
            entries, problems = parser(m.group(1))
            for problem in problems:
                findings.append((rel, problem))
            bucket.append({"entity": stem, "path": rel, "entries": entries})
            if field == MAPPING_FIELD and stem not in internal_ids:
                findings.append((rel, "declares syndicated_mappings without a non-empty "
                                      "internal_id:, the competitor namespace needs one"))

    return hierarchy_decls, placement_decls, mapping_decls, entity_stems, findings


def _overlap_findings(decls, group_key, label):
    """FINDINGS for two entries in the same group (per group_key(decl, entry))
    whose intervals overlap, mirroring the crosswalk's duplicate-claim check."""
    findings = []
    seen = {}
    for d in decls:
        for entry in d["entries"]:
            key = group_key(d, entry)
            for other_path, other_entry in seen.get(key, []):
                if _overlaps(entry, other_entry):
                    findings.append((d["path"], "%s %r overlaps %s already declared at %s"
                                     % (label, entry["full_raw"], label, other_path)))
            seen.setdefault(key, []).append((d["path"], entry))
    return findings


def cmd_check(vault, hierarchy_name=None):
    hdecls, pdecls, mdecls, entity_stems, findings = load(vault)
    if not hdecls and not pdecls and not mdecls and not findings:
        print("bm_vault_shapes: NO-DATA, no note declares hierarchy_edges, placements "
              "or syndicated_mappings, there is nothing to verify", file=sys.stderr)
        return 2

    if hierarchy_name is not None:
        hdecls = [{"entity": d["entity"], "path": d["path"],
                   "entries": [e for e in d["entries"] if e["name"] == hierarchy_name]}
                  for d in hdecls]
        hdecls = [d for d in hdecls if d["entries"]]

    findings += _overlap_findings(hdecls, lambda d, e: (d["entity"], e["name"]), "hierarchy edge")
    findings += _overlap_findings(pdecls, lambda d, e: d["entity"], "placement")
    findings += _overlap_findings(mdecls, lambda d, e: (d["entity"], e["source"]), "syndicated mapping")

    for d in hdecls:
        for e in d["entries"]:
            if e["parent"] not in entity_stems:
                findings.append((d["path"], "DANGLING parent %r resolves to no entity "
                                            "note in this vault" % e["parent"]))
    for d in pdecls:
        for e in d["entries"]:
            if e["location"] not in entity_stems:
                findings.append((d["path"], "DANGLING location %r resolves to no entity "
                                            "note in this vault" % e["location"]))

    print("vault: %s" % vault)
    print("entities with hierarchy edges: %d (%d edge(s))"
          % (len(hdecls), sum(len(d["entries"]) for d in hdecls)))
    print("assets with placements: %d (%d record(s))"
          % (len(pdecls), sum(len(d["entries"]) for d in pdecls)))
    print("competitors with syndicated mappings: %d (%d mapping(s))"
          % (len(mdecls), sum(len(d["entries"]) for d in mdecls)))
    if findings:
        print("FINDINGS, each named, never silently skipped: %d" % len(findings))
        for rel, problem in findings:
            print("  %s: %s" % (rel, problem))
    return 1 if findings else 0


def _resolve_as_of(decls, entity, as_of):
    """[entry, ...] covering as_of for the named entity, or None if the
    entity declares no matching bucket at all (a plain miss, not NO-DATA)."""
    for d in decls:
        if d["entity"] == entity:
            return [e for e in d["entries"] if _covers(e, as_of)]
    return None


def cmd_resolve_hierarchy(vault, entity, hierarchy_name, as_of):
    hdecls, _, _, entity_stems, _ = load(vault)
    if entity not in entity_stems:
        print("NO-DATA: no entity named %r in this vault" % entity)
        return 2
    chain = []
    seen = set()
    current = entity
    while True:
        hits = _resolve_as_of(hdecls, current, as_of)
        hits = [] if hits is None else [e for e in hits if e["name"] == hierarchy_name]
        if not hits:
            break
        if len(hits) > 1:
            print("AMBIGUOUS: %r has %d concurrent %r hierarchy parents as of %s"
                  % (current, len(hits), hierarchy_name, as_of))
            return 1
        parent = hits[0]["parent"]
        if parent in seen:
            print("FINDING: cycle in %r hierarchy at %r -> %r" % (hierarchy_name, current, parent))
            return 1
        seen.add(current)
        chain.append(parent)
        current = parent
    if not chain:
        print("NO-DATA: %r has no %r hierarchy edge covering %s"
              % (entity, hierarchy_name, as_of))
        return 1
    print("%s -%s-> %s  as of %s" % (entity, hierarchy_name, " -> ".join(chain), as_of))
    return 0


def cmd_resolve_placement(vault, asset, as_of):
    _, pdecls, _, entity_stems, _ = load(vault)
    if asset not in entity_stems:
        print("NO-DATA: no entity named %r in this vault" % asset)
        return 2
    hits = _resolve_as_of(pdecls, asset, as_of)
    if not hits:
        print("NO-DATA: %r has no placement covering %s" % (asset, as_of))
        return 1
    if len(hits) > 1:
        print("AMBIGUOUS: %r has %d concurrent placements as of %s" % (asset, len(hits), as_of))
        return 1
    e = hits[0]
    print("%s  location=%s  model=%s  as of %s" % (asset, e["location"], e["model"], as_of))
    return 0


def cmd_resolve_competitor(vault, competitor, as_of):
    _, _, mdecls, entity_stems, _ = load(vault)
    if competitor not in entity_stems:
        print("NO-DATA: no entity named %r in this vault" % competitor)
        return 2
    hits = _resolve_as_of(mdecls, competitor, as_of)
    if not hits:
        print("NO-DATA: %r has no syndicated mapping covering %s" % (competitor, as_of))
        return 1
    if len(hits) > 1:
        print("AMBIGUOUS: %r has %d concurrent syndicated mappings as of %s"
              % (competitor, len(hits), as_of))
        return 1
    e = hits[0]
    print("%s  external=%s  source=%s  as of %s" % (competitor, e["external"], e["source"], as_of))
    return 0


def _parse_as_of(raw):
    try:
        return datetime.date.fromisoformat(raw), None
    except ValueError:  # sbe: allow-silent the error string is the return value, caller prints and refuses on it
        return None, "bm_vault_shapes: --as-of %r is not an ISO date" % raw


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=(
        "check", "resolve-hierarchy", "resolve-placement", "resolve-competitor"))
    ap.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    ap.add_argument("--hierarchy", help="hierarchy name, e.g. legal or trade")
    ap.add_argument("--entity", help="child entity, for resolve-hierarchy")
    ap.add_argument("--asset", help="asset entity, for resolve-placement")
    ap.add_argument("--competitor", help="competitor entity, for resolve-competitor")
    ap.add_argument("--as-of", help="ISO date to resolve against")
    args = ap.parse_args(argv)
    if not args.vault or not os.path.isdir(args.vault):
        print("bm_vault_shapes: NO-DATA, no readable vault at %r" % args.vault, file=sys.stderr)
        return 2
    if args.command == "check":
        return cmd_check(args.vault, args.hierarchy)

    if not args.as_of:
        print("bm_vault_shapes: %s needs --as-of" % args.command, file=sys.stderr)
        return 2
    as_of, err = _parse_as_of(args.as_of)
    if err:
        print(err, file=sys.stderr)
        return 2

    if args.command == "resolve-hierarchy":
        if not args.entity or not args.hierarchy:
            print("bm_vault_shapes: resolve-hierarchy needs --entity and --hierarchy",
                  file=sys.stderr)
            return 2
        return cmd_resolve_hierarchy(args.vault, args.entity, args.hierarchy, as_of)
    if args.command == "resolve-placement":
        if not args.asset:
            print("bm_vault_shapes: resolve-placement needs --asset", file=sys.stderr)
            return 2
        return cmd_resolve_placement(args.vault, args.asset, as_of)
    if not args.competitor:
        print("bm_vault_shapes: resolve-competitor needs --competitor", file=sys.stderr)
        return 2
    return cmd_resolve_competitor(args.vault, args.competitor, as_of)


if __name__ == "__main__":
    sys.exit(main())
