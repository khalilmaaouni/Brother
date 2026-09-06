#!/usr/bin/env python3
"""bm_vault_census_ext: hierarchy and attribute census extensions. WBS VB13-06.

WHY A NEW MODULE. bm_vault_shapes.py (VB12-02) owns hierarchy_edges parsing, interval
math and dangling-parent detection; bm_vault_contract.py (VB10-02) owns the per-class
metadata contract and owner/steward resolution; bm_vault_route.py (VB10-03) owns
per-owner defect routing with folder dedupe. None of the three modules is itself "the
census": bm_vault_retention.py's own `census` command reconciles a different domain
(index rows), so there is no existing census module this row's dimensions belong on.
This module adds seven new REPORTED dimensions over hierarchy and attribute data,
every one flag-style (never blocks, never gates a commit), and funnels its findings
through bm_vault_route's own route_findings()/render_report() so a new dimension gets
owner attribution and folder dedupe for free rather than a second implementation of
either.

CONSUMPTION, NEVER RE-DERIVATION. Every dimension below reads its source module's own
structured return or a function it already exports, never a second parser of prose it
prints:
  hierarchy_orphan             bm_vault_shapes.load()'s own (hdecls, entity_stems)
  hierarchy_multi_parent       bm_vault_shapes._overlap_findings(), called exactly as
                                cmd_check calls it -- shapes already refuses this shape
                                at write time; this dimension is the LEGACY-data lens
                                on the same rule, using the same function
  hierarchy_expired_referenced hdecls plus shapes._covers(), new aggregation only
  hierarchy_walk_failure       hdecls, entity_stems plus shapes._covers(), new walk
  attribute_completeness       bm_vault_contract.classify() (per-class, structured)
  attribute_placeholder        bm_vault_contract.frontmatter_span()/_field_map(),
                                new pattern table (neither shapes nor contract declares
                                one, so this module owns the placeholder vocabulary)
  attribute_stale_verification bm_vault_staleness.walk()/read_capture_expiry()/
                                classify(), the exact building blocks bm_vault_route's
                                own collect_rot() already calls: this dimension is that
                                same per-class verified_at horizon, grouped by class
                                rather than left as one flat list

HIERARCHY DIMENSION DEFINITIONS, made concrete because the WBS row states them in one
line each:
  orphan nodes            an edge whose `parent` names no entity note in the vault
                           (the exact DANGLING check bm_vault_shapes.cmd_check already
                           runs, exposed here as its own counted dimension)
  undeclared multi-parent two open (overlapping) intervals for one (child, hierarchy
                           name) pair -- shapes refuses this at its own write-time
                           discipline, but nothing stops a legacy note from carrying
                           it, so this dimension is the read-only census of it
  expired-but-referenced  an edge whose valid_to has passed AND whose named parent is
                           still the parent in some OTHER edge (same hierarchy name)
                           that covers today. A benign like-for-like successor to the
                           same parent also counts: this dimension flags "this edge
                           closed" plus "that parent is still live", not "the reorg
                           looks wrong", so a clean successor edge to the same parent
                           is reported too, by design, never silently excluded.
  walk-test failure       walking a child's CURRENT parent chain (the edge covering
                           today, or -- when none covers today -- the edge with the
                           latest valid_from, a documented heuristic for "best guess of
                           the last known state") hits a cycle, or a parent that
                           resolves to no entity note, before running out of edges.
                           Reaching a node with no further edge in that hierarchy is a
                           SUCCESS (that node is the root), not a failure.

FLAG STYLE, NEVER BLOCKING. This module never classifies a finding ERROR vs QUEUE by
note age: that new-vs-legacy gate is bm_vault_tiers.py's job on new records at the
door, and duplicating it here would be a second gate reading the same clock. Every
finding from every dimension here is equally a flag, queued for its owner, exactly
like bm_vault_shapes and bm_vault_route already treat their own findings.

NO-DATA IS PER DIMENSION, NEVER A SILENT PASS. Each of the seven dimensions (the
per-class and per-hierarchy ones counted per key) reports its own domain size; a
domain size of zero is NO-DATA for that one dimension, never folded into "0 findings,
clean". The whole command is NO-DATA (exit 2) only when EVERY dimension's domain is
empty, mirroring bm_vault_shapes's own "any one class alone may be empty" doctrine.

Exit 0: at least one dimension had data and none of it produced a finding. Exit 1: at
least one finding was routed to at least one owner. Exit 2: NO-DATA, an unreadable
vault, a required sibling (bm_vault_contract or bm_vault_route) failed to load, or
every dimension's domain was empty.

Python 3.9 floor, standard library only, writes nothing anywhere.
"""
import argparse
import datetime
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

PLACEHOLDER_FIELD_SUFFIXES = ("phone", "tel", "telephone")
_DIGITS_RE = re.compile(r"\D+")
_PLACEHOLDER_DIGIT_SEQUENCES = frozenset({
    "1234567890", "0123456789", "12345678901", "01234567890",
})


def _load_sibling(name, tools_dir=None):
    """tools/<name>.py loaded BY PATH, the same technique bm_vault_contract.py and
    bm_vault_route.py already use, so this module's vocabulary never depends on the
    caller's sys.path. None when absent or broken: callers turn that into a named
    NO-DATA reason, never a crash."""
    path = os.path.join(tools_dir or HERE, name + ".py")
    if not os.path.isfile(path):
        return None
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # sbe: allow-silent broken sibling module, per docstring callers turn None into a named NO-DATA
        return None


def _finding(source, path, detail):
    return {"source": source, "path": path, "detail": detail}


# ---------------------------------------------------------------------------
# Hierarchy dimensions, all reading bm_vault_shapes's own structured records.
# ---------------------------------------------------------------------------

def hierarchy_orphans(hdecls, entity_stems):
    """(domain_size, findings): domain_size is every declared edge; a finding is an
    edge whose parent resolves to no entity note, the exact condition
    bm_vault_shapes.cmd_check already flags as DANGLING, counted here as its own
    census dimension."""
    total = sum(len(d["entries"]) for d in hdecls)
    findings = []
    for d in hdecls:
        for e in d["entries"]:
            if e["parent"] not in entity_stems:
                findings.append(_finding(
                    "hierarchy_orphan", d["path"],
                    "child %r cites parent %r under hierarchy %r, which resolves to "
                    "no entity note in this vault" % (d["entity"], e["parent"], e["name"])))
    return total, findings


def hierarchy_multi_parent(shapes_mod, hdecls):
    """(domain_size, findings): calls shapes's own overlap check verbatim, the same
    function cmd_check calls, never a re-derived comparison."""
    total = sum(len(d["entries"]) for d in hdecls)
    raw = shapes_mod._overlap_findings(
        hdecls, lambda d, e: (d["entity"], e["name"]), "hierarchy edge")
    findings = [_finding("hierarchy_multi_parent", path, detail) for path, detail in raw]
    return total, findings


def hierarchy_expired_referenced(shapes_mod, hdecls, today):
    """(domain_size, findings): domain_size is every edge that has closed (a valid_to
    at all); a finding is a closed edge whose parent is still the parent named by some
    OTHER edge, in the same hierarchy name, that covers today."""
    closed = [(d, e) for d in hdecls for e in d["entries"] if e["valid_to"] is not None]
    total = len(closed)
    active_parents_by_name = {}
    for d in hdecls:
        for e in d["entries"]:
            if shapes_mod._covers(e, today):
                active_parents_by_name.setdefault(e["name"], set()).add(e["parent"])
    findings = []
    for d, e in closed:
        if e["valid_to"] >= today:
            continue  # closes today or later: not yet expired as of today
        if e["parent"] in active_parents_by_name.get(e["name"], set()):
            findings.append(_finding(
                "hierarchy_expired_referenced", d["path"],
                "edge %r for %r expired %s but parent %r is still an active %r "
                "parent elsewhere" % (e["full_raw"], d["entity"], e["valid_to"],
                                       e["parent"], e["name"])))
    return total, findings


def _current_parent_edge(entries, shapes_mod, today):
    """The edge to treat as "current" for the walk test: the one covering today, or
    -- when none does -- the one with the latest valid_from (a documented heuristic
    for "best guess of the last known state", never a claim of certainty)."""
    covering = [e for e in entries if shapes_mod._covers(e, today)]
    if covering:
        return covering[0]
    return max(entries, key=lambda e: e["valid_from"] or datetime.date.min)


def hierarchy_walk_failures(shapes_mod, hdecls, entity_stems, today):
    """{hierarchy_name: (domain_size, findings)}. domain_size is every child that
    declares at least one edge under that name. A finding is a child whose current
    parent chain hits a cycle or a dangling parent before running out of edges;
    running out of edges (a node with no further parent under this name) is SUCCESS,
    that node is the root."""
    names = sorted({e["name"] for d in hdecls for e in d["entries"]})
    per_name = {}
    for name in names:
        current_parent = {}
        for d in hdecls:
            entries = [e for e in d["entries"] if e["name"] == name]
            if not entries:
                continue
            pick = _current_parent_edge(entries, shapes_mod, today)
            current_parent[d["entity"]] = (pick["parent"], d["path"])
        findings = []
        for entity, (_parent, path) in current_parent.items():
            visited = set()
            current = entity
            failure = None
            while True:
                if current in visited:
                    failure = "cycles back to %r" % current
                    break
                visited.add(current)
                nxt = current_parent.get(current)
                if nxt is None:
                    break  # no further edge under this name: current is the root
                parent, _p = nxt
                if parent not in entity_stems:
                    failure = "hits dangling parent %r before reaching a root" % parent
                    break
                current = parent
            if failure:
                findings.append(_finding(
                    "hierarchy_walk_failure", path,
                    "%r in hierarchy %r is not traversable to a root: %s"
                    % (entity, name, failure)))
        per_name[name] = (len(current_parent), findings)
    return per_name


# ---------------------------------------------------------------------------
# Attribute dimensions, reading bm_vault_contract's own frontmatter helpers.
# ---------------------------------------------------------------------------

def attribute_completeness(contract_mod, notes):
    """{class: (domain_size, findings)} for every class the contract table names.
    domain_size is every note of that class in the vault; a finding is one of
    contract.classify()'s own ERROR/QUEUE entries for that class, consumed as-is."""
    type_counts = {}
    for _rel, text in notes:
        block, _end = contract_mod.frontmatter_span(text)
        fmap = contract_mod._field_map(block) if block is not None else {}
        t = fmap.get("type", "").strip()
        type_counts[t] = type_counts.get(t, 0) + 1
    result = contract_mod.classify(
        notes, candidate_value=contract_mod._promotion_candidate_value())
    by_class_findings = {}
    for f in result["error"] + result["queue"]:
        by_class_findings.setdefault(f["class"], []).append(_finding(
            "attribute_completeness", f["path"], "%s %s: %s" % (f["kind"], f["class"], f["detail"])))
    return {cls: (type_counts.get(cls, 0), by_class_findings.get(cls, []))
            for cls in sorted(contract_mod.CONTRACT)}


def _is_placeholder_value(value):
    digits = _DIGITS_RE.sub("", value)
    if len(digits) < 7:
        return False
    if len(set(digits)) == 1:
        return True
    return digits in _PLACEHOLDER_DIGIT_SEQUENCES


def attribute_placeholders(contract_mod, notes):
    """(domain_size, findings): domain_size is every (note, field) pair examined
    (a field whose name ends in a declared placeholder-checked suffix, e.g. phone);
    a finding is one whose value matches a declared placeholder shape (all-same-digit,
    or a bare ascending/descending run), e.g. a zeroed phone number."""
    domain_size = 0
    findings = []
    for rel, text in notes:
        block, _end = contract_mod.frontmatter_span(text)
        if block is None:
            continue
        fmap = contract_mod._field_map(block)
        for field, raw in fmap.items():
            if not any(field.lower().endswith(suf) for suf in PLACEHOLDER_FIELD_SUFFIXES):
                continue
            value = contract_mod._strip_quotes(raw)
            if not value:
                continue
            domain_size += 1
            if _is_placeholder_value(value):
                findings.append(_finding(
                    "attribute_placeholder", rel,
                    "field %r holds a placeholder-shaped value %r" % (field, value)))
    return domain_size, findings


def attribute_stale_verifications(staleness_mod, vault, today=None):
    """{note_type: (domain_size, findings)}. Reuses staleness_mod's own walk()/
    read_capture_expiry()/classify() verbatim, the exact building blocks
    bm_vault_route.collect_rot() already calls, grouped by class here instead of left
    as one flat list. domain_size counts only notes with a dated claim to age (state
    fresh or stale); unverified_no_clock and examined_no_date carry no age to check."""
    today = today or datetime.date.today()
    per_class = {}
    for path in staleness_mod.walk(vault):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:  # sbe: allow-silent unreadable note excluded from the census, same posture as the rest of this walk
            continue
        rel = os.path.relpath(path, vault)
        is_capture, _expires = staleness_mod.read_capture_expiry(text)
        if is_capture:
            continue
        note_type = staleness_mod._note_type(text)
        state, verified, age, _problem = staleness_mod.classify(text, today)
        if state not in ("stale", "fresh"):
            continue
        total, findings = per_class.get(note_type, (0, []))
        total += 1
        if state == "stale":
            findings = findings + [_finding(
                "attribute_stale_verification", rel,
                "verified_at %s is stale (%d days old)" % (verified, age))]
        per_class[note_type] = (total, findings)
    return per_class


# ---------------------------------------------------------------------------
# Assembly: seven dimensions, NO-DATA per dimension, routed through bm_vault_route.
# ---------------------------------------------------------------------------

def collect_dimensions(vault, tools_dir=None, today=None):
    """(dims, no_data_siblings, contract_mod, route_mod). dims: name -> either
    {"domain_size", "findings"} (a simple dimension) or {"per_key": {key: {...}}}
    (completeness is per class, walk_failure is per hierarchy name, stale
    verification is per class). A sibling that fails to load names itself in
    no_data_siblings and every dimension it would have fed reports domain_size 0
    rather than being silently skipped."""
    today = today or datetime.date.today()
    shapes_mod = _load_sibling("bm_vault_shapes", tools_dir)
    contract_mod = _load_sibling("bm_vault_contract", tools_dir)
    route_mod = _load_sibling("bm_vault_route", tools_dir)
    staleness_mod = _load_sibling("bm_vault_staleness", tools_dir)

    no_data_siblings = [n for n, m in (
        ("bm_vault_shapes", shapes_mod), ("bm_vault_contract", contract_mod),
        ("bm_vault_route", route_mod), ("bm_vault_staleness", staleness_mod)) if m is None]

    dims = {}
    if shapes_mod is not None:
        hdecls, _pdecls, _mdecls, entity_stems, _findings = shapes_mod.load(vault)
        total, f = hierarchy_orphans(hdecls, entity_stems)
        dims["hierarchy_orphan"] = {"domain_size": total, "findings": f}
        total, f = hierarchy_multi_parent(shapes_mod, hdecls)
        dims["hierarchy_multi_parent"] = {"domain_size": total, "findings": f}
        total, f = hierarchy_expired_referenced(shapes_mod, hdecls, today)
        dims["hierarchy_expired_referenced"] = {"domain_size": total, "findings": f}
        dims["hierarchy_walk_failure"] = {"per_key": {
            name: {"domain_size": ds, "findings": f}
            for name, (ds, f) in hierarchy_walk_failures(
                shapes_mod, hdecls, entity_stems, today).items()}}
    else:
        for name in ("hierarchy_orphan", "hierarchy_multi_parent",
                      "hierarchy_expired_referenced"):
            dims[name] = {"domain_size": 0, "findings": []}
        dims["hierarchy_walk_failure"] = {"per_key": {}}

    if contract_mod is not None:
        notes = contract_mod._load_notes(vault) or []
        dims["attribute_completeness"] = {"per_key": {
            cls: {"domain_size": ds, "findings": f}
            for cls, (ds, f) in attribute_completeness(contract_mod, notes).items()}}
        total, f = attribute_placeholders(contract_mod, notes)
        dims["attribute_placeholder"] = {"domain_size": total, "findings": f}
    else:
        dims["attribute_completeness"] = {"per_key": {}}
        dims["attribute_placeholder"] = {"domain_size": 0, "findings": []}

    if staleness_mod is not None:
        dims["attribute_stale_verification"] = {"per_key": {
            cls: {"domain_size": ds, "findings": f}
            for cls, (ds, f) in attribute_stale_verifications(
                staleness_mod, vault, today).items()}}
    else:
        dims["attribute_stale_verification"] = {"per_key": {}}

    return dims, no_data_siblings, contract_mod, route_mod


def _all_findings(dims):
    out = []
    for d in dims.values():
        if "findings" in d:
            out.extend(d["findings"])
        else:
            for sub in d["per_key"].values():
                out.extend(sub["findings"])
    return out


def _any_domain_populated(dims):
    for d in dims.values():
        if "findings" in d:
            if d["domain_size"] > 0:
                return True
        elif any(sub["domain_size"] > 0 for sub in d["per_key"].values()):
            return True
    return False


def _render_dimensions(dims):
    lines = []
    for name in sorted(dims):
        d = dims[name]
        if "findings" in d:
            if d["domain_size"] == 0:
                lines.append("  %s: NO-DATA, nothing declared" % name)
            else:
                lines.append("  %s: domain %d, %d finding(s)"
                              % (name, d["domain_size"], len(d["findings"])))
        else:
            if not d["per_key"]:
                lines.append("  %s: NO-DATA, nothing declared" % name)
                continue
            for key in sorted(d["per_key"]):
                sub = d["per_key"][key]
                if sub["domain_size"] == 0:
                    lines.append("  %s[%s]: NO-DATA, nothing declared" % (name, key))
                else:
                    lines.append("  %s[%s]: domain %d, %d finding(s)"
                                  % (name, key, sub["domain_size"], len(sub["findings"])))
    return lines


def cmd_check(vault, tools_dir, default_owner, json_out, today=None):
    if not vault or not os.path.isdir(vault):
        msg = "bm_vault_census_ext: NO-DATA, no readable vault at %r" % vault
        print(msg, file=sys.stderr)
        return 2

    dims, no_data_siblings, contract_mod, route_mod = collect_dimensions(vault, tools_dir, today)
    if contract_mod is None or route_mod is None:
        msg = ("bm_vault_census_ext: NO-DATA, a required sibling failed to load (%s), "
               "owner routing has no seam without both" % ", ".join(
                   n for n in ("bm_vault_contract", "bm_vault_route") if n in no_data_siblings))
        print(msg, file=sys.stderr)
        return 2

    owners_map, err = contract_mod.load_owners_map(vault)
    if err:
        print("bm_vault_census_ext: NO-DATA, %s" % err, file=sys.stderr)
        return 2

    findings = _all_findings(dims)
    routed = route_mod.route_findings(vault, findings, owners_map, contract_mod, default_owner)
    populated = _any_domain_populated(dims)

    if json_out:
        print(json.dumps({
            "vault": vault, "no_data_siblings": sorted(no_data_siblings),
            "dimensions": dims, "routed": routed,
        }, indent=2, sort_keys=True, default=str))
    else:
        print("vault: %s" % vault)
        if no_data_siblings:
            print("NO-DATA siblings (failed to load, skipped): %s" % ", ".join(sorted(no_data_siblings)))
        print("dimensions:")
        for line in _render_dimensions(dims):
            print(line)
        if not routed:
            print("clean: no findings from any dimension")
        else:
            for owner in sorted(routed):
                print(route_mod.render_report(owner, routed[owner]))

    if not populated:
        return 2
    return 1 if routed else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("check",))
    ap.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    ap.add_argument("--default-owner", default="unassigned",
                     help="named lane for a finding that resolves to no owner at all")
    ap.add_argument("--as-of", help="ISO date to treat as 'today' (default: the real today)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    today = None
    if args.as_of:
        try:
            today = datetime.date.fromisoformat(args.as_of)
        except ValueError:
            print("bm_vault_census_ext: --as-of %r is not an ISO date" % args.as_of, file=sys.stderr)
            return 2
    return cmd_check(args.vault, None, args.default_owner, args.json, today)


if __name__ == "__main__":
    sys.exit(main())
