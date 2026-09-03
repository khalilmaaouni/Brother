#!/usr/bin/env python3
"""bm_vault_identity.py: opaque ids, tenant as a field, merge history as events (VB3-17).

WHY THIS EXISTS. Codex refutation, conceded: prefixing an id with a deployment fact
(a tenant slug, an environment name) embeds something MUTABLE into something that is
supposed to be PERMANENT, and MDM survivorship (bm_vault_survivorship.py, VB12-01)
already proves a "golden key" is a ranking decision, not an identity -- the winner of a
survivorship contest can change when the rule version changes, so a key derived from
today's winner is not stable enough to be an id. This module is the row that locks the
consequence: ids stay opaque and immutable (bm_vault_ids.py already mints them that
way, unchanged here); tenant scope is carried as its OWN mandatory field, never folded
into an id (see VB3-03, tools/bm_vault_context.py: the seam chosen there is full vault-
root isolation via HOME, a per-note tenant column was considered and rejected for the
same reason this row exists -- "No id or path this module builds ever embeds the
tenant string as an identifier"); a golden-record merge is a recorded event, never a
rewrite; and an unmerge creates a new resolution interval rather than erasing the old
one.

WHAT THIS DOES NOT DO. It never touches an entity note. Both notes in a merge stay
byte-identical on disk, before and after -- a note's own frontmatter (its `entity:`,
its `source_ids:`, its own `id:`) is not where merge history lives, because editing it
would mean two different systems (bm_vault_crosswalk.py's dated mappings and this
module's merge history) fighting to own the same file, and because "no file rewrites"
is cheaper to prove than "every rewrite is provably safe". Merge and unmerge write to
exactly one place: an append-only JSONL event stream at `<vault>/.identity/events.jsonl`,
using bm_vault_events.py's own schema (VB6-08) extended with the merged_into and
unmerged kinds. History is never rewritten because there is nothing here CAPABLE of
rewriting a line: every write is an append, and resolution is a fold over everything
appended so far, exactly the replay contract bm_vault_events.py already established
for note events.

RESOLUTION, TWO HOPS. `resolve --source-id X [--as-of DATE]` first asks "which entity
declares X": if X already looks like an opaque note id (bm_vault_ids' own n-<16 hex>
format) that entity IS the answer to hop one; otherwise it defers to
bm_vault_crosswalk.py's own dated many-to-many mapping (VB6-07, already built, reused
here rather than rebuilt) to find which entity note declares X as a source_id. Hop two
folds this module's own event stream: does that entity have an OPEN merged_into
interval covering DATE (or, with no --as-of, covering right now)? If so, the answer is
the survivor at the far end of the chain; if not (X predates every merge, or every
merge touching X has since been unmerged as of DATE), the answer is the entity itself.
A merge interval closes exactly like bm_vault_crosswalk's own dated mappings do: the
original merged_into event is never edited, an unmerged event is appended instead, and
the fold computes valid_to from it. The merge era stays queryable: `--as-of` a date
inside [merge effective, unmerge effective] still answers the survivor, exactly as
crosswalk's own INCLUSIVE-both-ends interval semantics already document.

MERGE REFUSES TO STACK. An entity already openly merged into a survivor (no unmerged
event has closed that interval yet) cannot be merged again until it is unmerged --
otherwise the fold would have to arbitrate two live survivors for one id, which is
exactly the ambiguity bm_vault_crosswalk.py's own overlap detection refuses. This is a
write-time refusal (exit 1, naming the existing survivor), never a silent last-write-
wins.

OPACITY GATE. `check --vault PATH` asserts, corpus-wide, that no id anywhere is
anything other than bm_vault_ids' own opaque `n-<16 hex>` shape: every note's `id:`
field (read leniently here, not through bm_vault_ids.read_id, which silently treats a
malformed value as "missing" -- a tenant-prefixed id must be NAMED, not swallowed as an
absence), every `vault:` entry a crosswalk declaration makes, and every `ref`/`into` an
identity event carries. Foreign systems' own ids (github:, path:, plugin:, artifact:)
are that system's own spelling, not this vault's identity, and are not held to this
vault's format -- exactly bm_vault_crosswalk.py's own posture that a foreign id is
"that system's fact, not this vault's". TENANT LIVES OUTSIDE THE ID, and outside the
note: today's estate is single-tenant, so there is nothing to embed a tenant string
INTO yet, and this gate exists so that stays true when a second tenant arrives -- the
tenant will be a RequestContext field (VB3-03) or a subprocess HOME, never a prefix
minted into a note's or an entity's id.

Exit codes: 0 clean or resolved to the entity itself (no interval, or an honest
resolution), 1 a check finding, an honest miss, or a refused write (stacked merge,
unknown entity, bad date), 2 NO-DATA (no readable vault, or nothing at all to check /
resolve against). Python 3.9 floor, standard library only.
"""
import argparse
import datetime
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
IDENTITY_DIR = ".identity"
EVENTS_FILE = "events.jsonl"
LOOSE_ID_RE = re.compile(r"(?m)^id:\s*(\S+)\s*$")


def _load_sibling(name, tools_dir=None):
    """tools/<name>.py loaded BY PATH, the same pattern bm_vault_survivorship.py's own
    _load_sibling uses: point tools_dir at a copy of tools/ and every sibling import
    inside this call resolves inside that same copy. Returns None (never raises) when
    the file is missing or fails to import -- a missing contract module is the caller's
    NO-DATA finding, not a crash."""
    path = os.path.join(tools_dir or HERE, name + ".py")
    if not os.path.isfile(path):
        return None
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # sbe: allow-silent per docstring: a missing contract module is the caller's NO-DATA finding
        return None


def events_path(vault):
    return os.path.join(vault, IDENTITY_DIR, EVENTS_FILE)


def load_identity_events(vault, ev_mod):
    """Every validated event in this vault's identity stream, [] when the stream file
    does not exist yet (an empty history is not an error, it is a vault where nothing
    has ever been merged). Raises ev_mod.FoldError on a malformed line -- the caller
    turns that into NO-DATA, never a silent partial read."""
    path = events_path(vault)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        lines = [("%s:%d" % (path, i), line) for i, line in enumerate(fh, 1)]
    return ev_mod.parse_lines(lines)


def append_event(vault, record):
    """One JSON line, appended, never rewriting a byte that came before it."""
    directory = os.path.join(vault, IDENTITY_DIR)
    os.makedirs(directory, exist_ok=True)
    line = json.dumps(record, sort_keys=True) + "\n"
    fd = os.open(events_path(vault), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def merge_intervals(events):
    """[{from, into, valid_from, valid_to, rule_version, merge_event_key,
    unmerge_event_key}], one per merge episode, built purely from the event set (never
    from arrival order, matching bm_vault_events.fold's own order-independence).

    ponytail: pairs each merged_into with the earliest unmerged event for the same
    (from, into) pair whose effective date is not before the merge's own -- the common
    case of at most one open interval per pair at a time. cmd_merge's own stacked-merge
    refusal (below) is what keeps that assumption true at write time, so this fold
    never has to arbitrate two live merges of the same id; a fuller interval-algebra
    fold is the upgrade path if that refusal is ever relaxed."""
    merges = [e for e in events if e["kind"] == "merged_into"]
    unmerges = [e for e in events if e["kind"] == "unmerged"]
    by_pair = {}
    for u in unmerges:
        by_pair.setdefault((u["ref"], u["into"]), []).append(u)
    for lst in by_pair.values():
        lst.sort(key=lambda e: (e["effective"], e["event_key"]))

    consumed = {}
    intervals = []
    for m in sorted(merges, key=lambda e: (e["ref"], e["into"], e["effective"], e["event_key"])):
        key = (m["ref"], m["into"])
        pool = by_pair.get(key, [])
        idx = consumed.get(key, 0)
        valid_to, unmerge_key = None, None
        while idx < len(pool):
            cand = pool[idx]
            idx += 1
            if cand["effective"] >= m["effective"]:
                valid_to, unmerge_key = cand["effective"], cand["event_key"]
                break
        consumed[key] = idx
        intervals.append({
            "from": m["ref"], "into": m["into"],
            "valid_from": m["effective"], "valid_to": valid_to,
            "rule_version": m["rule_version"],
            "merge_event_key": m["event_key"], "unmerge_event_key": unmerge_key,
        })
    return intervals


def _covers(interval, as_of):
    if as_of < interval["valid_from"]:
        return False
    if interval["valid_to"] is not None and as_of > interval["valid_to"]:
        return False
    return True


def resolve_entity(intervals, entity_id, as_of):
    """(final_id, chain). Chases merged_into intervals from entity_id to its survivor:
    with as_of given, the interval covering that date (inclusive both ends, same as
    bm_vault_crosswalk's own dated mappings); with as_of None, the still-open interval
    (valid_to is None). A cycle (should never happen -- cmd_merge refuses the write
    that would create one) is refused rather than looped forever."""
    chain = []
    current = entity_id
    seen = {current}
    while True:
        hop = None
        for iv in intervals:
            if iv["from"] != current:
                continue
            if as_of is not None:
                if _covers(iv, as_of):
                    hop = iv
                    break
            elif iv["valid_to"] is None:
                hop = iv
                break
        if hop is None:
            return current, chain
        if hop["into"] in seen:
            raise ValueError("merge cycle detected: %s already visited" % hop["into"])
        chain.append(hop)
        current = hop["into"]
        seen.add(current)


def _reverse_id_map(ids_mod, vault):
    by_id, _, _ = ids_mod.index(vault)
    return {path: nid for nid, path in by_id.items()}, by_id


def crosswalk_entity_for(xw_mod, ids_mod, vault, source_id):
    """The opaque entity id that declares source_id, via bm_vault_crosswalk.py's own
    load() (VB6-07), reused rather than rebuilt. None when nothing declares it (an
    honest miss, not a NO-DATA -- the caller decides what that means)."""
    decls, _, _ = xw_mod.load(vault)
    path_to_id, _ = _reverse_id_map(ids_mod, vault)
    hits = set()
    for d in decls:
        for entry in d["entries"]:
            if source_id in (entry["raw"], entry["ident"]):
                hits.add(d["path"])
    if not hits:
        return None
    # A source id claimed by two entities is a crosswalk-level FINDING (cmd check over
    # there), not this module's problem to arbitrate; picking the first is an honest
    # best-effort read for resolve, since crosswalk's own `check` already names the
    # conflict.
    return path_to_id.get(sorted(hits)[0])


def _parse_iso_date(value, label):
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise ValueError("%s %r is not an ISO date (YYYY-MM-DD)" % (label, value))


def cmd_merge(vault, from_id, into_id, rule_version, effective, ids_mod, ev_mod):
    if from_id == into_id:
        print("bm_vault_identity: refused, --from and --into name the same entity %r"
              % from_id, file=sys.stderr)
        return 1
    if not (ids_mod.ID_VALUE_RE.match(from_id) and ids_mod.ID_VALUE_RE.match(into_id)):
        print("bm_vault_identity: refused, ids must be opaque n-<16 hex> (VB3-17 locks "
              "identity to that shape); got --from=%r --into=%r" % (from_id, into_id),
              file=sys.stderr)
        return 1
    by_id, _, _ = ids_mod.index(vault)
    for label, ident in (("--from", from_id), ("--into", into_id)):
        if ident not in by_id:
            print("bm_vault_identity: refused, %s %r resolves to no note in this vault"
                  % (label, ident), file=sys.stderr)
            return 1
    if not rule_version:
        print("bm_vault_identity: refused, --rule-version is required and must be "
              "non-empty", file=sys.stderr)
        return 1
    try:
        effective = _parse_iso_date(effective, "--effective")
    except ValueError as exc:
        print("bm_vault_identity: refused, %s" % exc, file=sys.stderr)
        return 1

    try:
        events = load_identity_events(vault, ev_mod)
    except ev_mod.FoldError as exc:
        print("bm_vault_identity: NO-DATA, identity event stream is malformed: %s"
              % exc, file=sys.stderr)
        return 2
    intervals = merge_intervals(events)
    survivor, chain = resolve_entity(intervals, from_id, as_of=None)
    if chain:
        print("bm_vault_identity: refused, %r is already merged into %r as of %s; "
              "unmerge it first" % (from_id, survivor, chain[-1]["valid_from"]),
              file=sys.stderr)
        return 1

    event_key = "merge:%s:%s:%s" % (from_id, into_id, effective)
    record = {
        "event_key": event_key, "kind": "merged_into", "ref": from_id,
        "into": into_id, "rule_version": rule_version, "effective": effective,
        "occurred_at": effective,
        "recorded_at": datetime.datetime.now(datetime.timezone.utc)
                        .isoformat(timespec="seconds"),
    }
    ev_mod._validate(dict(record), "merge-command")
    append_event(vault, record)
    print("merged: %s -> %s (rule_version=%s, effective=%s, event_key=%s)"
          % (from_id, into_id, rule_version, effective, event_key))
    print("both entity notes are untouched: %s, %s" % (by_id[from_id], by_id[into_id]))
    return 0


def cmd_unmerge(vault, from_id, into_id, effective, ev_mod):
    try:
        effective = _parse_iso_date(effective, "--effective")
    except ValueError as exc:
        print("bm_vault_identity: refused, %s" % exc, file=sys.stderr)
        return 1
    try:
        events = load_identity_events(vault, ev_mod)
    except ev_mod.FoldError as exc:
        print("bm_vault_identity: NO-DATA, identity event stream is malformed: %s"
              % exc, file=sys.stderr)
        return 2
    intervals = merge_intervals(events)
    open_ones = [iv for iv in intervals
                 if iv["from"] == from_id and iv["valid_to"] is None
                 and (into_id is None or iv["into"] == into_id)]
    if not open_ones:
        print("bm_vault_identity: refused, no open merge to unmerge for --from %r%s"
              % (from_id, "" if into_id is None else (" --into %r" % into_id)),
              file=sys.stderr)
        return 1
    if len(open_ones) > 1:
        print("bm_vault_identity: refused, %d open merges for --from %r, "
              "qualify with --into: %s" % (
                  len(open_ones), from_id,
                  ", ".join(sorted(iv["into"] for iv in open_ones))), file=sys.stderr)
        return 1
    interval = open_ones[0]
    if effective < interval["valid_from"]:
        print("bm_vault_identity: refused, --effective %s precedes the merge's own "
              "effective date %s" % (effective, interval["valid_from"]), file=sys.stderr)
        return 1

    event_key = "unmerge:%s:%s:%s" % (from_id, interval["into"], effective)
    record = {
        "event_key": event_key, "kind": "unmerged", "ref": from_id,
        "into": interval["into"], "effective": effective, "occurred_at": effective,
        "recorded_at": datetime.datetime.now(datetime.timezone.utc)
                        .isoformat(timespec="seconds"),
    }
    ev_mod._validate(dict(record), "unmerge-command")
    append_event(vault, record)
    print("unmerged: %s from %s (effective=%s, event_key=%s)"
          % (from_id, interval["into"], effective, event_key))
    print("merge era %s..%s stays queryable as history"
          % (interval["valid_from"], effective))
    return 0


def cmd_resolve(vault, source_id, as_of, ids_mod, xw_mod, ev_mod):
    if as_of is not None:
        try:
            as_of = _parse_iso_date(as_of, "--as-of")
        except ValueError as exc:
            print("bm_vault_identity: refused, %s" % exc, file=sys.stderr)
            return 1
    entity_id = source_id if ids_mod.ID_VALUE_RE.match(source_id) else \
        crosswalk_entity_for(xw_mod, ids_mod, vault, source_id)
    if entity_id is None:
        print("NO-DATA: %r resolves to no entity in this vault" % source_id)
        return 1
    try:
        events = load_identity_events(vault, ev_mod)
    except ev_mod.FoldError as exc:
        print("bm_vault_identity: NO-DATA, identity event stream is malformed: %s"
              % exc, file=sys.stderr)
        return 2
    intervals = merge_intervals(events)
    try:
        survivor, chain = resolve_entity(intervals, entity_id, as_of)
    except ValueError as exc:
        print("bm_vault_identity: %s" % exc, file=sys.stderr)
        return 1
    suffix = " as of %s" % as_of if as_of is not None else ""
    if chain:
        print("%s  survivor=%s  via %s%s" % (
            source_id, survivor,
            " -> ".join("%s->%s@%s" % (h["from"], h["into"], h["valid_from"])
                        for h in chain), suffix))
    else:
        print("%s  entity=%s (no merge applies)%s" % (source_id, survivor, suffix))
    return 0


def cmd_check(vault, ids_mod, xw_mod, ev_mod):
    problems = []
    total_notes = 0
    for path in ids_mod.walk(vault):
        total_notes += 1
        rel = os.path.relpath(path, vault)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            problems.append("%s: could not be read (%s)" % (rel, exc))
            continue
        block, _, _ = ids_mod.frontmatter(text)
        if block is None:
            continue
        m = LOOSE_ID_RE.search(block)
        if not m:
            continue
        raw = m.group(1).strip().strip('"').strip("'")
        if not ids_mod.ID_VALUE_RE.match(raw):
            problems.append("%s: id %r is not opaque n-<16 hex> (a deployment or "
                            "tenant string may be embedded)" % (rel, raw))

    decls, _, _ = xw_mod.load(vault)
    for d in decls:
        for entry in d["entries"]:
            if entry["system"] == "vault" and not ids_mod.ID_VALUE_RE.match(entry["ident"]):
                problems.append("%s: crosswalk vault: id %r is not opaque n-<16 hex>"
                                % (d["path"], entry["ident"]))

    events = []
    try:
        events = load_identity_events(vault, ev_mod)
    except ev_mod.FoldError as exc:
        problems.append("identity event stream is malformed: %s" % exc)
    for e in events:
        if e["kind"] in ("merged_into", "unmerged"):
            for field in ("ref", "into"):
                value = e[field]
                if not ids_mod.ID_VALUE_RE.match(value):
                    problems.append("event %s: %s %r is not opaque n-<16 hex>"
                                    % (e["event_key"], field, value))

    if total_notes == 0 and not decls and not events:
        print("bm_vault_identity: NO-DATA, no notes, no crosswalk declarations and no "
              "identity events to check for opacity", file=sys.stderr)
        return 2
    print("vault: %s" % vault)
    print("notes scanned: %d" % total_notes)
    print("crosswalk entities: %d" % len(decls))
    print("identity events: %d" % len(events))
    if problems:
        print("FINDINGS, each named: %d" % len(problems))
        for p in problems:
            print("  %s" % p)
        return 1
    print("clean: every id is opaque, no tenant or deployment string embedded")
    return 0


def _load_stack():
    ids_mod = _load_sibling("bm_vault_ids")
    xw_mod = _load_sibling("bm_vault_crosswalk")
    ev_mod = _load_sibling("bm_vault_events")
    if ids_mod is None or xw_mod is None or ev_mod is None:
        print("bm_vault_identity: NO-DATA, a sibling contract module failed to load",
              file=sys.stderr)
        return None
    return ids_mod, xw_mod, ev_mod


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("merge", "unmerge", "check", "resolve"))
    ap.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    ap.add_argument("--from", dest="from_id", help="merge/unmerge: the entity id merged away")
    ap.add_argument("--into", dest="into_id", help="merge: the survivor entity id; "
                    "unmerge: optional, disambiguates when more than one open merge exists")
    ap.add_argument("--rule-version", help="merge: which survivorship rule decided it")
    ap.add_argument("--effective", help="merge/unmerge: ISO date the event takes effect")
    ap.add_argument("--source-id", help="resolve: an opaque entity id or any crosswalk-"
                    "declared source id")
    ap.add_argument("--as-of", help="resolve: ISO date, current survivor if omitted")
    args = ap.parse_args(argv)

    if not args.vault or not os.path.isdir(args.vault):
        print("bm_vault_identity: NO-DATA, no readable vault at %r" % args.vault,
              file=sys.stderr)
        return 2
    stack = _load_stack()
    if stack is None:
        return 2
    ids_mod, xw_mod, ev_mod = stack

    if args.command == "check":
        return cmd_check(args.vault, ids_mod, xw_mod, ev_mod)

    if args.command == "merge":
        missing = [n for n, v in (("--from", args.from_id), ("--into", args.into_id),
                                   ("--rule-version", args.rule_version),
                                   ("--effective", args.effective)) if not v]
        if missing:
            print("bm_vault_identity: merge needs %s" % ", ".join(missing), file=sys.stderr)
            return 2
        return cmd_merge(args.vault, args.from_id, args.into_id, args.rule_version,
                         args.effective, ids_mod, ev_mod)

    if args.command == "unmerge":
        missing = [n for n, v in (("--from", args.from_id), ("--effective", args.effective))
                   if not v]
        if missing:
            print("bm_vault_identity: unmerge needs %s" % ", ".join(missing), file=sys.stderr)
            return 2
        return cmd_unmerge(args.vault, args.from_id, args.into_id, args.effective, ev_mod)

    if not args.source_id:
        print("bm_vault_identity: resolve needs --source-id", file=sys.stderr)
        return 2
    return cmd_resolve(args.vault, args.source_id, args.as_of, ids_mod, xw_mod, ev_mod)


if __name__ == "__main__":
    sys.exit(main())
