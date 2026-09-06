#!/usr/bin/env python3
"""bm_vault_closure: a maintained closure table (node-to-descendant paths)
over the hierarchy edges tools/bm_vault_shapes.py owns (VB13-02), for
ragged rollups that a chain walk alone cannot answer efficiently or safely.

WHY THIS EXISTS. bm_vault_shapes.py's own resolver, and
bm_vault_hierarchy_req.py's direct/transitive query modes, both walk the
edge graph live, one hop at a time, every time. That is correct but it is
not a rollup: a rollup needs every (ancestor, descendant) pair reachable as
of a date, in one read, so a fact table keyed on the descendant can be
summed under the ancestor. Walking live per query cannot express "count
every leaf once" when the same leaf reaches the same ancestor by more than
one path, because nothing accumulates the paths to compare them against
each other. The closure table is that accumulation, materialized once and
verified rather than trusted forever.

WHY NOT A PATHSTRING (e.g. storing "/root/div/region/store" on each note).
A pathstring only ever names ONE path, so it cannot express a node that
sits under two concurrent named hierarchies at once (the very polyhierarchy
case this module exists for) without inventing a second column per
hierarchy, and a reorg becomes a rewrite of every descendant's pathstring
rather than one dated edge closing and one opening. The dimensional
modeling literature (Kimball's ragged-hierarchy "helper table" pattern,
which this module implements) rejects pathstrings for exactly these two
reasons: restructure cost and the inability to express shared ownership. A
closure table pays a small storage cost per (ancestor, descendant, path)
row instead, and a rebuild is the only response a reorg ever needs.

THE SHAPE. One row per (ancestor, descendant) pair PER PATH that reaches it:
{"ancestor": stem, "descendant": stem, "hierarchy": name, "depth": int}.
A descendant that reaches the same ancestor through two different named
hierarchies (a declared polyhierarchy: the same child carries edges under
two hierarchy names, e.g. legal and trade, that happen to reconverge on a
shared ancestor) gets TWO rows for that (ancestor, descendant) pair, one
per hierarchy path. That is deliberate: it is the raw evidence a
path-filtered rollup dedupes against, and a query that skips the dedupe
step is shown to double count by exactly the number of extra paths.

DERIVED, NEVER HAND-EDITED. `rebuild --as-of DATE` recomputes every row
from the vault's current hierarchy_edges declarations and OVERWRITES the
stored copy atomically; `verify` recomputes fresh from the same as-of date
and diffs against what is stored, reporting DRIFT (never silently serving
stale rows) when the two disagree. `rollup --ancestor NAME` reads the
STORED closure and reports both the raw path-row count and the
path-filtered (distinct descendant) count for that ancestor, so a caller
sees the double count it is refusing to act on.

THE COMMIT-TIME CONTRACT. Hooking this into bm_vault_hierarchy_req.py's
`approve` step is deliberately NOT done here: `approve` writes hierarchy
edges to notes on disk, and a closure rebuild reads the WHOLE vault back
off disk under a fresh as-of, which is a different-shaped operation
(potentially over every entity, not just the request's own children) than
a single approval commit. Coupling them would make every approval pay a
full-vault rebuild whether or not anyone reads a rollup that day. The
contract instead: an approved reorg's closure is stale until the next
`rebuild --as-of <the reorg's effective date>` runs, and `verify` is the
guard that catches anyone who forgot, by reporting DRIFT rather than
quietly answering with the old tree.

REUSE, NAMED. Every hop of the upward walk is
bm_vault_hierarchy_req._up_transitive (which itself is built on
bm_vault_shapes._covers, an as-of interval check reused from
bm_vault_crosswalk.py) -- this module never re-derives what "this edge
covers this date" means or re-walks a parent chain by hand. Entity and
edge discovery is bm_vault_shapes.load and bm_vault_hierarchy_req's own
_entries_map. The stored table's atomic write is
bm_vault_promotions._atomic_write, called directly, same as
bm_vault_hierarchy_req.cmd_approve already does for note writes.

Exit 0 clean/rebuilt/rolled-up/verified-no-drift. Exit 1 DRIFT, a cycle
finding, or an honest miss. Exit 2 NO-DATA (missing vault, no hierarchy
edges declared anywhere, no edge covers the given as-of, or no stored
closure to verify/rollup against). Python 3.9 floor, standard library only.
"""
import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# C3: the config directory is resolved by brother_paths, the one seam
# that knows which coding client is running (docs/codex/HOOKS-MAPPING.md).
# Loaded from beside this file because tools/ is not a package.
import brother_paths  # noqa: E402
import bm_vault_hierarchy_req as hr  # noqa: E402 -- reuse the up-walk, never re-derive it
import bm_vault_promotions as promo  # noqa: E402 -- reuse its atomic write
import bm_vault_shapes as vs         # noqa: E402 -- reuse load(), _covers, entity discovery

STORE_PATH = brother_paths.config_path("bm_vault_closure.json")


def _row_key(row):
    return (row["ancestor"], row["descendant"], row["hierarchy"], row["depth"])


def build_rows(hdecls, as_of):
    """([row, ...], [finding, ...]). One row per (ancestor, descendant,
    hierarchy) path reachable as of `as_of`, walking every hierarchy name
    each entity declares via bm_vault_hierarchy_req._up_transitive -- never
    a second implementation of the upward walk. A cycle in any one named
    hierarchy is a finding, named per (entity, hierarchy), and that
    hierarchy's chain is excluded from the rows rather than raising."""
    emap = hr._entries_map(hdecls)
    rows, findings = [], []
    for entity in sorted(emap):
        names = sorted({e["name"] for e in emap[entity]})
        for name in names:
            chain, err = hr._up_transitive(emap, entity, name, as_of)
            if err:
                findings.append("%s / %s: %s" % (entity, name, err))
                continue
            for depth, ancestor in enumerate(chain, start=1):
                rows.append({"ancestor": ancestor, "descendant": entity,
                             "hierarchy": name, "depth": depth})
    return rows, findings


def _any_edge_covers(hdecls, as_of):
    for d in hdecls:
        for e in d["entries"]:
            if vs._covers(e, as_of):
                return True
    return False


def _load_store():
    if not os.path.exists(STORE_PATH):
        return None
    with open(STORE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _write_store(vault, as_of, rows, findings):
    dirname = os.path.dirname(STORE_PATH)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    payload = {
        "vault": os.path.abspath(vault),
        "as_of": as_of.isoformat(),
        "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "row_count": len(rows),
        "findings": findings,
        "rows": rows,
    }
    promo._atomic_write(STORE_PATH, json.dumps(payload, sort_keys=True, indent=2) + "\n")


def cmd_rebuild(vault, as_of):
    hdecls, _, _, _, _ = vs.load(vault)
    if not hdecls:
        print("bm_vault_closure: NO-DATA, no note declares hierarchy_edges anywhere "
              "in this vault, there is nothing to build a closure over", file=sys.stderr)
        return 2
    if not _any_edge_covers(hdecls, as_of):
        print("bm_vault_closure: NO-DATA, no hierarchy edge covers %s" % as_of, file=sys.stderr)
        return 2
    rows, findings = build_rows(hdecls, as_of)
    if findings:
        print("REFUSED: %d cycle finding(s), nothing stored" % len(findings))
        for f in findings:
            print("  %s" % f)
        return 1
    _write_store(vault, as_of, rows, findings)
    print("closure rebuilt as of %s: %d row(s) over %s"
          % (as_of, len(rows), os.path.abspath(vault)))
    return 0


def cmd_verify(vault):
    stored = _load_store()
    if stored is None:
        print("bm_vault_closure: NO-DATA, no stored closure at %r; run rebuild first"
              % STORE_PATH, file=sys.stderr)
        return 2
    as_of = datetime.date.fromisoformat(stored["as_of"])
    hdecls, _, _, _, _ = vs.load(vault)
    fresh_rows, findings = build_rows(hdecls, as_of)
    if findings:
        print("REFUSED: %d cycle finding(s) in the current vault state, cannot verify"
              % len(findings))
        for f in findings:
            print("  %s" % f)
        return 1

    stored_set = {_row_key(r) for r in stored["rows"]}
    fresh_set = {_row_key(r) for r in fresh_rows}
    missing = sorted(stored_set - fresh_set)   # stored, no longer true
    extra = sorted(fresh_set - stored_set)      # true now, never stored

    print("bm_vault_closure: verifying as of %s against %s" % (as_of, STORE_PATH))
    if not missing and not extra:
        print("clean: stored closure matches the current vault state, %d row(s)"
              % len(stored_set))
        return 0

    print("DRIFT: stored closure no longer matches the current vault state")
    print("  stale in store (no longer a valid path): %d" % len(missing))
    for ancestor, descendant, name, depth in missing:
        print("    %s -%s-> %s  (depth %d, in store, not now)" % (descendant, name, ancestor, depth))
    print("  missing from store (a valid path today): %d" % len(extra))
    for ancestor, descendant, name, depth in extra:
        print("    %s -%s-> %s  (depth %d, now, not in store)" % (descendant, name, ancestor, depth))
    print("run rebuild --as-of %s to bring the store back in line" % as_of)
    return 1


def cmd_rollup(ancestor):
    """Reads the STORED closure only, path-filtered: reports the raw
    path-row count for `ancestor` alongside the distinct-descendant
    (path-filtered) count, so a caller sees a polyhierarchy double count
    named rather than silently summed twice."""
    stored = _load_store()
    if stored is None:
        print("bm_vault_closure: NO-DATA, no stored closure at %r; run rebuild first"
              % STORE_PATH, file=sys.stderr)
        return 2
    raw = [r for r in stored["rows"] if r["ancestor"] == ancestor]
    if not raw:
        print("NO-DATA: %r has no descendant in the stored closure (as of %s)"
              % (ancestor, stored["as_of"]))
        return 2
    distinct = sorted({r["descendant"] for r in raw})
    print("ancestor %s  as of %s" % (ancestor, stored["as_of"]))
    print("  raw path rows: %d" % len(raw))
    print("  path-filtered distinct descendants: %d" % len(distinct))
    for d in distinct:
        paths = sorted(r["hierarchy"] for r in raw if r["descendant"] == d)
        print("    %s  (%d path(s): %s)" % (d, len(paths), ", ".join(paths)))
    return 0


def _parse_as_of(raw):
    try:
        return datetime.date.fromisoformat(raw), None
    except ValueError:  # sbe: allow-silent not silent: returns (None, message), the message is the usage error _parse_as_of's caller prints
        return None, "bm_vault_closure: --as-of %r is not an ISO date" % raw


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("rebuild", "verify", "rollup"))
    ap.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    ap.add_argument("--as-of", help="ISO date; required for rebuild")
    ap.add_argument("--ancestor", help="ancestor entity stem; required for rollup")
    args = ap.parse_args(argv)

    if args.command == "rollup":
        if not args.ancestor:
            print("bm_vault_closure: rollup needs --ancestor", file=sys.stderr)
            return 2
        return cmd_rollup(args.ancestor)

    if not args.vault or not os.path.isdir(args.vault):
        print("bm_vault_closure: NO-DATA, no readable vault at %r" % args.vault, file=sys.stderr)
        return 2

    if args.command == "verify":
        return cmd_verify(args.vault)

    if not args.as_of:
        print("bm_vault_closure: rebuild needs --as-of", file=sys.stderr)
        return 2
    as_of, err = _parse_as_of(args.as_of)
    if err:
        print(err, file=sys.stderr)
        return 2
    return cmd_rebuild(args.vault, as_of)


if __name__ == "__main__":
    sys.exit(main())
