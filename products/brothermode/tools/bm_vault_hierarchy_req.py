#!/usr/bin/env python3
"""bm_vault_hierarchy_req: named hierarchy query modes, and a request flow for
changing hierarchy_edges (VB13-01), on top of tools/bm_vault_shapes.py (VB12-02).

WHY THIS EXISTS. bm_vault_shapes.py already gives the vault dated hierarchy
edges and an as-of resolver, but its resolver only ever walks UP and only
ever walks the FULL chain: it cannot answer "who is the immediate parent"
without also silently walking every ancestor above that, and it cannot
answer a children question at all. Per the W3C hierarchy vocabulary (a
`broader`/`narrower` link is a direct edge; only closure over those edges is
a rollup path), a chain of direct links is not the same claim as a closure,
and an answer that does not say which one it gave invites exactly that
misread. This module names the two modes explicitly in every answer:
  direct       one hop only: the immediate parent (up) or immediate
               children (down) at the given date.
  transitive   the full closure: the whole ancestor chain to the top (up),
               or every descendant reachable at that date (down).
Neither mode has a default; a query missing --mode is refused, never
silently resolved as one or the other.

THE REQUEST FLOW. A hierarchy change is never a direct edit of a note's
frontmatter. It is a REQUEST: one or more ITEMS, each either
  add    a new hierarch_edges entry (child, hierarchy, parent, valid_from,
         optional valid_to)
  close  sets valid_to on an existing OPEN entry (child, hierarchy, valid_to)
A move is not a third op: it is a close item and an add item in the same
request, exactly as the brief that ordered this module named it. A request
is validated against bm_vault_shapes' own contract (grammar, overlap,
dangling parent) BEFORE it is ever stored; an invalid request is refused
WHOLE, nothing stored, nothing touched. A stored request is PENDING until a
human decision lands:
  preview   renders the affected hierarchy chain as it reads BEFORE and
            AFTER the request's earliest effective date, from the request
            alone, never touching the request store or any vault note.
  approve   requires --by (mirrors bm_vault_promotions.py's own promote
            step: an approval that is not recorded did not happen).
            Re-validates against the CURRENT vault state (which may have
            moved since the request was created) and, only if every item
            still holds, writes every affected note's hierarchy_edges field
            in one pass: all items land or none do, because validation
            happens in full before the first byte is written.
  reject    records the refusal with its reason. --by is optional here
            (mirrors the same sibling contract: only approve, the write
            path, needs a named human on record).
A rejected request is never deleted: it stays in the store, queryable by
`show`, with its reason and (if the deciding human named themselves) who
declined it.

STORAGE. One append-only JSON-lines file, same shape and same posture as
tools/bm_vault_ledger.py's own store: two row kinds, "request" (the items as
submitted) and "decision" (approved/rejected, referencing the request by
id). Never rewritten in place, never a second writer disagreeing about the
format; `show` and `preview` only ever read it. A request already carrying a
decision refuses a second one.

REUSE, NAMED. Interval math (an as-of covers check, an overlap check, an
ISO-date parse) all come from bm_vault_shapes.py (`_covers`, `_overlaps`,
`_parse_date`), which itself reuses bm_vault_crosswalk.py for the same
reason: this module never re-derives what "two intervals overlap" or
"this date falls inside this interval" means. Entity and frontmatter
discovery (`walk`, `ENTITY_RE`, `_frontmatter`) also come from
bm_vault_shapes.py. The frontmatter-block splice for the actual on-disk
write reuses bm_vault_ids.frontmatter (block, start, end) exactly as
bm_vault_promotions.py's apply_promotion does, and the atomic write itself
IS bm_vault_promotions._atomic_write, called directly rather than
reimplemented. What this module adds that neither sibling has: the
DOWNWARD walk (children/descendants), a direct-vs-transitive distinction on
the upward walk, and the request/decision store.

Exit 0 clean/resolved/created/approved/rejected/shown. Exit 1 findings, a
refused request, an honest miss/AMBIGUOUS, or a decision already recorded.
Exit 2 NO-DATA (missing vault, missing request, missing required flag).
Python 3.9 floor, standard library only.
"""
import argparse
import datetime
import json
import os
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# C3: the config directory is resolved by brother_paths, the one seam
# that knows which coding client is running (docs/codex/HOOKS-MAPPING.md).
# Loaded from beside this file because tools/ is not a package.
import brother_paths  # noqa: E402
import bm_vault_ids as ids           # noqa: E402 -- frontmatter block splice
import bm_vault_promotions as promo  # noqa: E402 -- reuse its atomic write
import bm_vault_shapes as vs         # noqa: E402 -- reuse interval math, entity discovery

STORE_PATH = brother_paths.config_path(
    "bm_vault_hierarchy_requests.jsonl")


# ---------------------------------------------------------------- storage --

def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _new_id():
    return uuid.uuid4().hex[:12]


def _append(row):
    """Append one JSON row, same O_APPEND atomic-append contract as
    bm_vault_ledger.py's own writer. References STORE_PATH by name at call
    time (never as a default argument) so a test that monkeypatches the
    module constant is honored."""
    dirname = os.path.dirname(STORE_PATH)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    line = (json.dumps(row, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(STORE_PATH, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def _read_rows():
    if not os.path.exists(STORE_PATH):
        return []
    rows = []
    with open(STORE_PATH, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as e:
                sys.stderr.write("bm_vault_hierarchy_req: skipping malformed line %d (%s)\n" % (i, e))
    return rows


def _find_request(req_id):
    for row in _read_rows():
        if row.get("kind") == "request" and row.get("id") == req_id:
            return row
    return None


def _decision_for(req_id):
    hits = [r for r in _read_rows() if r.get("kind") == "decision" and r.get("request_id") == req_id]
    return hits[-1] if hits else None


# ------------------------------------------------------------- item parse --

def _parse_item(raw):
    kv = {}
    for part in raw.split(";"):
        key, sep, val = part.partition("=")
        if sep:
            kv[key.strip()] = val.strip()
    return kv


def _build_item(raw, problems):
    """One request item dict from a raw `op=...;child=...;...` string, or
    None (with `problems` appended) on any grammar violation. Dates are
    parsed with bm_vault_shapes._parse_date, the same parser the shapes
    contract itself uses, never a second implementation of ISO-date parsing."""
    kv = _parse_item(raw)
    op = kv.get("op")
    if op not in ("add", "close"):
        problems.append("item %r: op must be add or close" % raw)
        return None
    child = kv.get("child", "").strip()
    hierarchy_name = kv.get("hierarchy", "").strip()
    if not child or not hierarchy_name:
        problems.append("item %r: needs child and hierarchy" % raw)
        return None
    item = {"raw": raw, "op": op, "child": child, "hierarchy": hierarchy_name}
    if op == "add":
        parent = kv.get("parent", "").strip()
        if not parent:
            problems.append("item %r: add needs parent" % raw)
            return None
        if not kv.get("valid_from", "").strip():
            problems.append("item %r: add needs valid_from" % raw)
            return None
        valid_from = vs._parse_date(kv.get("valid_from", ""), "valid_from", raw, problems)
        valid_to = vs._parse_date(kv.get("valid_to", ""), "valid_to", raw, problems) \
            if kv.get("valid_to", "").strip() else None
        if valid_from is None or (kv.get("valid_to", "").strip() and valid_to is None):
            return None
        item["parent"] = parent
        item["valid_from"] = valid_from
        item["valid_to"] = valid_to
    else:
        if not kv.get("valid_to", "").strip():
            problems.append("item %r: close needs valid_to" % raw)
            return None
        valid_to = vs._parse_date(kv.get("valid_to", ""), "valid_to", raw, problems)
        if valid_to is None:
            return None
        item["valid_to"] = valid_to
    return item


def _serialize_item(item):
    out = dict(item)
    for k in ("valid_from", "valid_to"):
        if isinstance(out.get(k), datetime.date):
            out[k] = out[k].isoformat()
    return out


def _deserialize_item(item):
    out = dict(item)
    for k in ("valid_from", "valid_to"):
        if out.get(k):
            out[k] = datetime.date.fromisoformat(out[k])
        else:
            out[k] = None
    return out


# -------------------------------------------------------- contract reuse --

def _entries_map(hdecls):
    return {d["entity"]: list(d["entries"]) for d in hdecls}


def _load_working(vault):
    """(working, entity_stems): working is {entity: [entry, ...]}, a plain
    copy of the CURRENT vault state (via bm_vault_shapes.load, never
    re-derived), ready to be mutated in place by a request's items without
    touching the vault itself."""
    hdecls, _, _, entity_stems, _ = vs.load(vault)
    return _entries_map(hdecls), entity_stems


def _apply_items_virtual(working, items, problems):
    """Mutates `working` in place applying each item; appends to `problems`
    (never raises) for any item that cannot be resolved against the current
    working state. Order follows the request's own item order, so a move
    (close then add) sees its own close take effect before its add lands."""
    for it in items:
        entries = working.setdefault(it["child"], [])
        if it["op"] == "close":
            open_entries = [e for e in entries
                             if e["name"] == it["hierarchy"] and e.get("valid_to") is None]
            if len(open_entries) != 1:
                problems.append("close item %r: found %d open edge(s) for %s/%s, need exactly 1"
                                 % (it["raw"], len(open_entries), it["child"], it["hierarchy"]))
                continue
            open_entries[0]["valid_to"] = it["valid_to"]
        else:
            entries.append({"name": it["hierarchy"], "parent": it["parent"],
                             "valid_from": it["valid_from"], "valid_to": it["valid_to"],
                             "recorded_at": "", "full_raw": ""})


def _overlap_problems(working):
    """FINDINGS for two entries of the same (child, hierarchy name) whose
    intervals overlap, using bm_vault_shapes._overlaps directly -- the same
    interval-aware, both-ends-inclusive check the shapes contract itself
    runs, never a second implementation of it."""
    problems = []
    for child, entries in working.items():
        by_name = {}
        for e in entries:
            by_name.setdefault(e["name"], []).append(e)
        for name, group in by_name.items():
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    if vs._overlaps(group[i], group[j]):
                        problems.append("%s/%s: an entry overlaps another already in the request"
                                         % (child, name))
    return problems


def validate_request(vault, items):
    """(ok, problems, working). Checks every item's child resolves to a real
    entity, every add's parent resolves to a real entity, then applies all
    items virtually and runs the shapes overlap check over the result.
    `working` is always returned so a caller (preview) can render the
    AFTER tree even when the request is invalid."""
    working, entity_stems = _load_working(vault)
    problems = []
    for it in items:
        if it["child"] not in entity_stems:
            problems.append("item %r: child %r resolves to no entity note in this vault"
                             % (it["raw"], it["child"]))
        if it["op"] == "add" and it["parent"] not in entity_stems:
            problems.append("item %r: parent %r resolves to no entity note in this vault"
                             % (it["raw"], it["parent"]))
    if problems:
        return False, problems, working
    _apply_items_virtual(working, items, problems)
    problems += _overlap_problems(working)
    return (not problems), problems, working


# ----------------------------------------------------------- query modes --

def _up_direct(emap, entity, hierarchy_name, as_of):
    """(parent, error). error is None on a clean single hit, else a
    NO-DATA/AMBIGUOUS message naming the mode's own vocabulary."""
    entries = emap.get(entity, [])
    hits = [e for e in entries if e["name"] == hierarchy_name and vs._covers(e, as_of)]
    if not hits:
        return None, "NO-DATA: %r has no %r hierarchy edge covering %s" % (entity, hierarchy_name, as_of)
    if len(hits) > 1:
        return None, "AMBIGUOUS: %r has %d concurrent %r hierarchy parents as of %s" \
            % (entity, len(hits), hierarchy_name, as_of)
    return hits[0]["parent"], None


def _up_transitive(emap, entity, hierarchy_name, as_of):
    """(chain, error): the full ancestor chain to the top, one hop at a time
    via _up_direct (never a second interval walk), cycle-guarded."""
    chain, seen, current = [], set(), entity
    while True:
        parent, err = _up_direct(emap, current, hierarchy_name, as_of)
        if parent is None:
            break
        if parent in seen:
            return None, "FINDING: cycle in %r hierarchy at %r -> %r" % (hierarchy_name, current, parent)
        seen.add(current)
        chain.append(parent)
        current = parent
    return chain, None


def _down_direct(emap, entity, hierarchy_name, as_of):
    children = set()
    for child, entries in emap.items():
        for e in entries:
            if e["name"] == hierarchy_name and e["parent"] == entity and vs._covers(e, as_of):
                children.add(child)
                break
    return sorted(children)


def _down_transitive(emap, entity, hierarchy_name, as_of):
    result, seen, frontier = [], {entity}, [entity]
    while frontier:
        nxt = []
        for node in frontier:
            for child in _down_direct(emap, node, hierarchy_name, as_of):
                if child not in seen:
                    seen.add(child)
                    nxt.append(child)
                    result.append(child)
        frontier = nxt
    return result


def cmd_query(vault, entity, hierarchy_name, as_of, mode, direction="up"):
    if mode not in ("direct", "transitive"):
        print("bm_vault_hierarchy_req: query needs --mode direct or transitive; "
              "an unlabeled hierarchy answer invites a chain-vs-closure misread")
        return 2
    hdecls, _, _, entity_stems, _ = vs.load(vault)
    if entity not in entity_stems:
        print("mode: %s  NO-DATA: no entity named %r in this vault" % (mode, entity))
        return 2
    emap = _entries_map(hdecls)

    if direction == "up":
        if mode == "direct":
            parent, err = _up_direct(emap, entity, hierarchy_name, as_of)
            if err:
                print("mode: direct  %s" % err)
                return 1
            print("mode: direct  %s -%s-> %s  as of %s" % (entity, hierarchy_name, parent, as_of))
            return 0
        chain, err = _up_transitive(emap, entity, hierarchy_name, as_of)
        if err:
            print("mode: transitive  %s" % err)
            return 1
        if not chain:
            print("mode: transitive  NO-DATA: %r has no %r hierarchy edge covering %s"
                  % (entity, hierarchy_name, as_of))
            return 1
        print("mode: transitive  %s -%s-> %s  as of %s"
              % (entity, hierarchy_name, " -> ".join(chain), as_of))
        return 0

    if mode == "direct":
        children = _down_direct(emap, entity, hierarchy_name, as_of)
        if not children:
            print("mode: direct  NO-DATA: %r has no %r children covering %s"
                  % (entity, hierarchy_name, as_of))
            return 1
        print("mode: direct  %s  children=%s  as of %s" % (entity, ", ".join(children), as_of))
        return 0
    descendants = _down_transitive(emap, entity, hierarchy_name, as_of)
    if not descendants:
        print("mode: transitive  NO-DATA: %r has no %r descendants covering %s"
              % (entity, hierarchy_name, as_of))
        return 1
    print("mode: transitive  %s  descendants=%s  as of %s"
          % (entity, ", ".join(descendants), as_of))
    return 0


# -------------------------------------------------------------- requests --

def cmd_create(vault, item_strs):
    problems, items = [], []
    for raw in item_strs:
        item = _build_item(raw, problems)
        if item is not None:
            items.append(item)
    if problems:
        print("REFUSED: request has %d malformed item(s), nothing stored" % len(problems))
        for p in problems:
            print("  %s" % p)
        return 1
    if not items:
        print("bm_vault_hierarchy_req: create needs at least one --item", file=sys.stderr)
        return 2
    ok, problems, _ = validate_request(vault, items)
    if not ok:
        print("REFUSED: request violates the shapes contract, nothing stored")
        for p in problems:
            print("  %s" % p)
        return 1
    req_id = _new_id()
    _append({"kind": "request", "id": req_id, "ts": _now(), "vault": os.path.abspath(vault),
             "items": [_serialize_item(it) for it in items]})
    print("request %s created: %d item(s), pending" % (req_id, len(items)))
    return 0


def _print_chain(emap, child, hierarchy_name, as_of):
    chain, err = _up_transitive(emap, child, hierarchy_name, as_of)
    if err:
        print("    %s" % err)
    elif not chain:
        print("    NO-DATA: %s has no %s hierarchy edge covering %s" % (child, hierarchy_name, as_of))
    else:
        print("    %s -%s-> %s" % (child, hierarchy_name, " -> ".join(chain)))


def cmd_preview(vault, req_id):
    """Renders the affected (child, hierarchy) chains as they read BEFORE
    and AFTER the request's earliest effective date, from the request
    alone. Reads the store and the vault; writes neither -- STORE_PATH is
    never opened for writing here, so a byte-for-byte comparison of the
    store file before and after this call always holds."""
    row = _find_request(req_id)
    if row is None:
        print("NO-DATA: no request %r" % req_id)
        return 2
    items = [_deserialize_item(it) for it in row["items"]]
    dates = [d for it in items for d in (it.get("valid_from"), it.get("valid_to")) if d is not None]
    if not dates:
        print("NO-DATA: request %s names no dated item, nothing to preview" % req_id)
        return 2
    effective = min(dates)
    before_date = effective - datetime.timedelta(days=1)

    ok, problems, working = validate_request(vault, items)
    hdecls_before, _, _, _, _ = vs.load(vault)
    emap_before = _entries_map(hdecls_before)

    print("request %s preview: as-of before %s and after %s" % (req_id, before_date, effective))
    for child, hierarchy_name in sorted({(it["child"], it["hierarchy"]) for it in items}):
        print("  %s / %s" % (child, hierarchy_name))
        print("  BEFORE (mode: transitive) as of %s:" % before_date)
        _print_chain(emap_before, child, hierarchy_name, before_date)
        print("  AFTER  (mode: transitive) as of %s:" % effective)
        _print_chain(working, child, hierarchy_name, effective)
    if not ok:
        print("NOTE: this request currently violates the contract and would be refused if approved:")
        for p in problems:
            print("  %s" % p)
    return 0


def _splice_hierarchy_edges(text, entries):
    """The note text with its hierarchy_edges frontmatter line replaced (or
    appended if the note declared none yet), or None if there is no
    frontmatter block to splice into. Mirrors
    bm_vault_promotions.apply_promotion's splice-not-reserialize approach so
    every other frontmatter line and its order survive untouched."""
    block, start, end = ids.frontmatter(text)
    if block is None:
        return None
    rendered = []
    for e in entries:
        parts = ["name=%s" % e["name"], "parent=%s" % e["parent"]]
        if e.get("valid_from"):
            parts.append("valid_from=%s" % e["valid_from"].isoformat())
        if e.get("valid_to"):
            parts.append("valid_to=%s" % e["valid_to"].isoformat())
        if e.get("recorded_at"):
            parts.append("recorded_at=%s" % e["recorded_at"])
        rendered.append(";".join(parts))
    new_line = "hierarchy_edges: [%s]" % ", ".join(rendered)
    field_re = vs.FIELD_RE[vs.HIERARCHY_FIELD]
    if field_re.search(block):
        new_block = field_re.sub(lambda m: new_line, block, count=1)
    else:
        new_block = block.rstrip("\n") + "\n" + new_line
    return text[:start] + new_block + text[end:]


def _find_entity_path(vault, stem):
    """(rel_path, text) for the entity note whose basename stem is `stem`,
    or (None, None). Reuses bm_vault_shapes.walk, .ENTITY_RE and
    ._frontmatter -- the same discovery bm_vault_shapes.load itself runs,
    never a second implementation of it."""
    for path in vs.walk(vault):
        if os.path.splitext(os.path.basename(path))[0] != stem:
            continue
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        if vs.ENTITY_RE.search(vs._frontmatter(text)):
            return os.path.relpath(path, vault), text
    return None, None


def cmd_approve(vault, req_id, by, at):
    if not by:
        print("bm_vault_hierarchy_req: approve needs --by; an approval that is not "
              "recorded did not happen", file=sys.stderr)
        return 2
    row = _find_request(req_id)
    if row is None:
        print("NO-DATA: no request %r" % req_id)
        return 2
    if _decision_for(req_id) is not None:
        print("REFUSED: request %s already has a decision recorded" % req_id)
        return 1
    items = [_deserialize_item(it) for it in row["items"]]
    ok, problems, working = validate_request(vault, items)
    if not ok:
        print("REFUSED: request %s violates the shapes contract, nothing applied" % req_id)
        for p in problems:
            print("  %s" % p)
        return 1

    writes = []
    for child in sorted({it["child"] for it in items}):
        path, text = _find_entity_path(vault, child)
        if path is None:
            print("REFUSED: request %s names child %r with no entity note; nothing applied"
                  % (req_id, child))
            return 1
        new_text = _splice_hierarchy_edges(text, working.get(child, []))
        if new_text is None:
            print("REFUSED: %s has no frontmatter block to record hierarchy edges into; "
                  "nothing applied" % path)
            return 1
        writes.append((os.path.join(vault, path), new_text))

    for full_path, new_text in writes:
        promo._atomic_write(full_path, new_text)
    _append({"kind": "decision", "id": _new_id(), "request_id": req_id, "ts": _now(),
             "decision": "approved", "by": by, "at": at})
    print("request %s approved by %s: %d file(s) updated" % (req_id, by, len(writes)))
    return 0


def cmd_reject(req_id, reason, by, at):
    row = _find_request(req_id)
    if row is None:
        print("NO-DATA: no request %r" % req_id)
        return 2
    if not reason:
        print("bm_vault_hierarchy_req: reject needs --reason; a rejection with no reason "
              "cannot be audited later", file=sys.stderr)
        return 2
    if _decision_for(req_id) is not None:
        print("REFUSED: request %s already has a decision recorded" % req_id)
        return 1
    _append({"kind": "decision", "id": _new_id(), "request_id": req_id, "ts": _now(),
             "decision": "rejected", "by": by or "", "reason": reason, "at": at})
    print("request %s rejected: %s" % (req_id, reason))
    return 0


def cmd_show(req_id):
    row = _find_request(req_id)
    if row is None:
        print("NO-DATA: no request %r" % req_id)
        return 2
    print("request %s: %d item(s)" % (req_id, len(row["items"])))
    for it in row["items"]:
        print("  %s" % it.get("raw", it))
    decision = _decision_for(req_id)
    if decision is None:
        print("status: pending")
        return 0
    print("status: %s" % decision["decision"])
    print("approver: %s" % (decision.get("by") or "(none)"))
    if decision.get("reason"):
        print("reason: %s" % decision["reason"])
    return 0


# -------------------------------------------------------------------- CLI --

def _parse_as_of(raw):
    try:
        return datetime.date.fromisoformat(raw), None
    except ValueError:  # sbe: allow-silent not silent: returns (None, message), the message is the usage error _parse_as_of's caller prints
        return None, "bm_vault_hierarchy_req: --as-of %r is not an ISO date" % raw


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("query", "create", "preview", "approve", "reject", "show"))
    ap.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    ap.add_argument("--entity", help="child entity, for query")
    ap.add_argument("--hierarchy", help="hierarchy name, for query")
    ap.add_argument("--as-of", help="ISO date, for query")
    ap.add_argument("--mode", choices=("direct", "transitive"), default=None,
                     help="query needs this named explicitly; there is no default")
    ap.add_argument("--direction", choices=("up", "down"), default="up",
                     help="up: parent(s) [default]; down: child(ren)")
    ap.add_argument("--item", action="append", default=[],
                     help="op=add|close;child=...;hierarchy=...;... , repeatable, for create")
    ap.add_argument("--id", dest="req_id", help="request id, for preview/approve/reject/show")
    ap.add_argument("--by", help="who is deciding; required for approve")
    ap.add_argument("--reason", help="why a request is rejected; required for reject")
    ap.add_argument("--at", default=None, help="ISO date recorded on the decision; defaults to today")
    args = ap.parse_args(argv)

    if args.command in ("query", "create", "preview", "approve"):
        if not args.vault or not os.path.isdir(args.vault):
            print("bm_vault_hierarchy_req: NO-DATA, no readable vault at %r" % args.vault,
                  file=sys.stderr)
            return 2

    at = args.at or datetime.date.today().isoformat()

    if args.command == "query":
        if not args.entity or not args.hierarchy or not args.as_of:
            print("bm_vault_hierarchy_req: query needs --entity, --hierarchy and --as-of",
                  file=sys.stderr)
            return 2
        as_of, err = _parse_as_of(args.as_of)
        if err:
            print(err, file=sys.stderr)
            return 2
        return cmd_query(args.vault, args.entity, args.hierarchy, as_of, args.mode, args.direction)

    if args.command == "create":
        return cmd_create(args.vault, args.item)

    if args.command == "preview":
        if not args.req_id:
            print("bm_vault_hierarchy_req: preview needs --id", file=sys.stderr)
            return 2
        return cmd_preview(args.vault, args.req_id)

    if args.command == "approve":
        if not args.req_id:
            print("bm_vault_hierarchy_req: approve needs --id", file=sys.stderr)
            return 2
        return cmd_approve(args.vault, args.req_id, args.by, at)

    if args.command == "reject":
        if not args.req_id:
            print("bm_vault_hierarchy_req: reject needs --id", file=sys.stderr)
            return 2
        return cmd_reject(args.req_id, args.reason, args.by, at)

    if not args.req_id:
        print("bm_vault_hierarchy_req: show needs --id", file=sys.stderr)
        return 2
    return cmd_show(args.req_id)


if __name__ == "__main__":
    sys.exit(main())
