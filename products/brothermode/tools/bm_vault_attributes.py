#!/usr/bin/env python3
"""bm_vault_attributes: attribute-management discipline over the per-class
metadata contract (VB10-02, tools/bm_vault_contract.py). WBS row VB13-03.

THE THREE EXTENSIONS, founder-approved 2026-08-30:

1. INHERITANCE. CLASS_ATTRIBUTES declares an attribute set per class plus an
   optional parent. resolve_attributes() walks the parent chain root first
   and merges each level's attributes in; a child re-declaring an attribute
   name its parent already declared is marked as an OVERRIDE in the returned
   listing (source class, overridden class), automatically, by comparing keys
   during the merge. Nothing is a silent redefinition: an override is always
   visible in the listing a caller reads.

2. PER-CHANNEL REQUIREDNESS. CHANNEL_REQUIRED extends the class contract with
   one more declared table: (class, attribute, channel) -> required. A
   combination CHANNEL_REQUIRED does not name falls back to the attribute's
   own class-level "required" flag. This is a plain presence check, never
   tiered by new-versus-legacy: a record is missing what a channel demands or
   it is not, the same way bm_vault_contract's own per-class table is a plain
   presence check before the classifier ever tiers it.

3. GOVERNED CODE LISTS. Units and enums are dated interval tables, same shape
   as bm_vault_crosswalk's own valid_from/valid_to mappings (imported by path
   and reused directly, never re-derived): a value's validity is "does any
   declared interval for this value cover this date". classify_code_value()
   reuses bm_vault_contract's own gate-on-new-queue-on-legacy posture
   (ADOPTED, _is_new_by_date): an out-of-list or out-of-interval value is
   ERROR on a note new since ADOPTED, QUEUE on an older one. Nothing is
   deleted to retire a value; retiring closes its open interval.

REUSE, NOT REIMPLEMENTATION. bm_vault_contract.py and bm_vault_crosswalk.py
are loaded BY PATH (the technique tools/bm_vault_tiers.py's own _load_by_path
already uses for bm_vault_graph.py) so this module's answers are computed by
the SAME frontmatter reader, the SAME new/legacy threshold, and the SAME
dated-interval covers/overlaps arithmetic as their own suites already
calibrate, rather than a second copy of any of the three that could drift.

FLAG STYLE for `check`, mirroring bm_vault_contract's own cmd_check: it
reports ERROR/QUEUE counts and channel-missing counts, and it never itself
blocks a commit (that is bm_vault_tiers.py's job, on its own branch).

Python 3.9, standard library only, no network, no writes except the explicit
`codes add-value` / `codes retire-value` commands, which write only the
declared code-lists file.
"""
import argparse
import datetime
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_VAULT = os.environ.get("BROTHERMODE_VAULT") or os.path.expanduser("~/Documents/Kay Vault")

CODELISTS_RELPATH = os.path.join("99-System", "code-lists.json")


def _load_by_path(name, path):
    """Same by-path import technique bm_vault_tiers.py's own _load_by_path
    uses: neither dependency here is optional (both are required for this
    module's own logic), so a load failure raises rather than degrading."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _contract():
    return _load_by_path("bm_vault_contract", os.path.join(HERE, "bm_vault_contract.py"))


def _crosswalk():
    return _load_by_path("bm_vault_crosswalk", os.path.join(HERE, "bm_vault_crosswalk.py"))


# ---------------------------------------------------------------------------
# 1. INHERITANCE: the declared class/attribute table.
# ---------------------------------------------------------------------------
# Generic fixture hierarchy, never a real client's actual attribute set: a
# base "item" class, a "product" child that overrides item's optional status
# into a required one and adds its own attributes (two of them governed by a
# code list below), and a "kit" grandchild that adds one more attribute on
# top of product's already-merged set. This is the shape the founder-approved
# row describes (parent-child, override visible, code-list-bound attributes),
# not a specific customer's schema.
CLASS_ATTRIBUTES = {
    "item": {
        "parent": None,
        "attributes": {
            "id": {"required": True},
            "name": {"required": True},
            "status": {"required": False},
        },
    },
    "product": {
        "parent": "item",
        "attributes": {
            "status": {"required": True},  # OVERRIDE: item leaves it optional
            "sku": {"required": True},
            "unit": {"required": True, "code_list": "unit"},
            "pack_type": {"required": False, "code_list": "enum:pack_type"},
        },
    },
    "kit": {
        "parent": "product",
        "attributes": {
            "component_count": {"required": True},
        },
    },
}

# 2. PER-CHANNEL REQUIREDNESS, one more declared table extending the class
# contract above. A (class, attribute, channel) combination this table does
# not name falls back to the attribute's own class-level "required" flag.
CHANNEL_REQUIRED = {
    "product": {
        "pack_type": {"web": True, "wholesale": False},
    },
}


def resolve_attributes(cls, table=None):
    """{attr: {required, code_list, source, override, overrides}} for `cls`,
    walking its parent chain root-first and merging each level in. None for
    a class the table never named (out of scope, never an invented rule).
    Raises ValueError on a cyclical or dangling parent chain: a table that
    cannot be resolved is a defect in the table, never a silent partial
    answer."""
    table = CLASS_ATTRIBUTES if table is None else table
    if cls not in table:
        return None
    chain, seen, node = [], set(), cls
    while node is not None:
        if node in seen:
            raise ValueError("cyclical parent chain reaching %r" % node)
        seen.add(node)
        if node not in table:
            raise ValueError("class %r declares unknown parent %r" % (chain[-1], node))
        chain.append(node)
        node = table[node].get("parent")
    chain.reverse()  # root first, so a child's redeclaration is the override
    resolved = {}
    for level_cls in chain:
        for attr, rule in table[level_cls]["attributes"].items():
            prior = resolved.get(attr)
            entry = dict(rule)
            entry["source"] = level_cls
            entry["override"] = prior is not None
            entry["overrides"] = prior["source"] if prior is not None else None
            resolved[attr] = entry
    return resolved


def missing_for_channel(cls, fmap, channel, table=None, channel_table=None):
    """[attr, ...] required for `channel` but absent or blank in `fmap`.
    None when `cls` is out of scope. A plain presence check, never tiered:
    the goal's own wording is "refuses when missing", not "queues"."""
    resolved = resolve_attributes(cls, table)
    if resolved is None:
        return None
    channel_table = CHANNEL_REQUIRED if channel_table is None else channel_table
    missing = []
    for attr in sorted(resolved):
        per_attr = channel_table.get(cls, {}).get(attr, {})
        required = per_attr[channel] if channel in per_attr else bool(resolved[attr].get("required"))
        if required and not fmap.get(attr, "").strip():
            missing.append(attr)
    return missing


# ---------------------------------------------------------------------------
# 3. GOVERNED CODE LISTS: unit/enum values as dated intervals, same shape as
# bm_vault_crosswalk's own source-ID mappings, reused via that module's own
# _covers() rather than a second copy of the interval arithmetic.
# ---------------------------------------------------------------------------

def _parse_iso_or_none(raw):
    if not raw:
        return None
    return datetime.date.fromisoformat(raw)


def load_code_lists(vault, override=None):
    """(data | None, error | None), same three-way contract as
    bm_vault_contract.load_owners_map: (None, None) is the opt-in absent
    case (no code-lists file declared, NO-DATA downstream, never a guess);
    (None, "reason") is a present but broken file, never silently "no
    lists"."""
    path = override or (os.path.join(vault, CODELISTS_RELPATH) if vault else None)
    if not path or not os.path.isfile(path):
        return None, None
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as e:
        return None, "unreadable code lists %s: %s" % (path, e)
    if not isinstance(raw, dict) or not isinstance(raw.get("lists", {}), dict):
        return None, ("code lists %s must be "
                       "{\"lists\": {list_name: {value: [interval, ...]}}}" % path)
    lists = {}
    try:
        for list_name, values in raw["lists"].items():
            lists[list_name] = {}
            for value, intervals in values.items():
                lists[list_name][value] = [
                    {"valid_from": _parse_iso_or_none(iv.get("valid_from")),
                     "valid_to": _parse_iso_or_none(iv.get("valid_to"))}
                    for iv in intervals
                ]
    except (AttributeError, TypeError, ValueError) as e:
        return None, "malformed code lists %s: %s" % (path, e)
    return lists, None


def save_code_lists(vault, lists_data):
    path = os.path.join(vault, CODELISTS_RELPATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    serializable = {"lists": {
        list_name: {
            value: [{"valid_from": iv["valid_from"].isoformat() if iv["valid_from"] else None,
                     "valid_to": iv["valid_to"].isoformat() if iv["valid_to"] else None}
                    for iv in intervals]
            for value, intervals in values.items()
        }
        for list_name, values in lists_data.items()
    }}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(serializable, fh, indent=2, sort_keys=True)
        fh.write("\n")


def value_covers(lists_data, list_name, value, as_of):
    """True/False/None: None means the list or the value is not governed at
    all (never declared); False means it is governed but no interval covers
    `as_of` (retired, not yet opened, or simply never valid then); True means
    a declared interval covers the date. Reuses bm_vault_crosswalk's own
    _covers(entry, as_of), not a second copy of the inclusive-both-ends
    arithmetic."""
    values = (lists_data or {}).get(list_name)
    if values is None or value not in values:
        return None
    cw = _crosswalk()
    return any(cw._covers(iv, as_of) for iv in values[value])


def classify_code_value(lists_data, list_name, value, as_of, is_new):
    """None (clean) or a finding {"kind": ERROR|QUEUE, "list", "value",
    "detail"}. Reuses bm_vault_contract's own ERROR-if-new/QUEUE-if-legacy
    posture: the exact ternary tools/bm_vault_contract.py's classify_note
    already applies to a missing required field applies here to an
    out-of-governance value, so the two mechanisms tier identically."""
    covers = value_covers(lists_data, list_name, value, as_of)
    if covers:
        return None
    kind = "ERROR" if is_new else "QUEUE"
    reason = "not a governed value" if covers is None else "outside its valid interval"
    return {"kind": kind, "list": list_name, "value": value,
            "detail": "%r is %s in %r as of %s" % (value, reason, list_name, as_of)}


def add_value(lists_data, list_name, value, valid_from):
    """(ok, message). Refuses a duplicate OPEN interval for one value: an
    estate progressively opens and closes intervals, it never lets a value
    hold two open-ended validities at once, which is exactly what the goal's
    own duplicate-add test names."""
    intervals = lists_data.setdefault(list_name, {}).setdefault(value, [])
    if any(iv["valid_to"] is None for iv in intervals):
        return False, ("value %r in %r already has an open interval; retire it "
                        "before opening another" % (value, list_name))
    intervals.append({"valid_from": valid_from, "valid_to": None})
    return True, "opened %r in %r from %s" % (value, list_name, valid_from)


def retire_value(lists_data, list_name, value, valid_to):
    """(ok, message). Closes the value's open interval; never deletes it or
    any prior closed interval, so a note dated inside the now-closed span
    still resolves correctly."""
    intervals = lists_data.get(list_name, {}).get(value)
    if not intervals:
        return False, "value %r is not declared in %r" % (value, list_name)
    open_ivs = [iv for iv in intervals if iv["valid_to"] is None]
    if not open_ivs:
        return False, "value %r in %r has no open interval to retire" % (value, list_name)
    if len(open_ivs) > 1:
        return False, ("value %r in %r has %d open intervals; ambiguous retire"
                        % (value, list_name, len(open_ivs)))
    open_ivs[0]["valid_to"] = valid_to
    return True, "retired %r in %r at %s" % (value, list_name, valid_to)


def _created_date(fmap):
    raw = fmap.get("created", "").strip().strip('"').strip("'")
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:  # sbe: allow-silent unparseable date falls back to today() at the one caller, per its own guard
        return None


def check_code_values(cls, fmap, lists_data, is_new, table=None, as_of=None):
    """[finding, ...] for every code-list-bound attribute present on `fmap`.
    Missing attributes are bm_vault_contract's own job, not this one's: a
    blank value has nothing to look up in a list."""
    resolved = resolve_attributes(cls, table)
    if resolved is None or lists_data is None:
        return []
    if as_of is None:
        as_of = _created_date(fmap) or datetime.date.today()
    findings = []
    for attr in sorted(resolved):
        list_name = resolved[attr].get("code_list")
        if not list_name:
            continue
        raw = fmap.get(attr, "").strip()
        if not raw:
            continue
        f = classify_code_value(lists_data, list_name, raw, as_of, is_new)
        if f:
            f["field"] = attr
            findings.append(f)
    return findings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _vault_root(cli_vault):
    if cli_vault:
        return cli_vault
    env = os.environ.get("BM_VAULT_ROOT")
    if env:
        return env
    return DEFAULT_VAULT


def cmd_classes(cls, json_out):
    classes = [cls] if cls else sorted(CLASS_ATTRIBUTES)
    out = {}
    for c in classes:
        try:
            out[c] = resolve_attributes(c)
        except ValueError as e:
            print("NO-DATA: %s" % e)
            return 2
    if json_out:
        def _ser(v):
            return v  # already JSON-safe (str/bool/None)
        print(json.dumps({c: {a: _ser(r) for a, r in attrs.items()} for c, attrs in out.items()},
                          indent=2, sort_keys=True))
        return 0
    for c in classes:
        print("class: %s" % c)
        for attr in sorted(out[c]):
            r = out[c][attr]
            tag = (" OVERRIDE of %s" % r["overrides"]) if r["override"] else ""
            cl = (" code_list=%s" % r["code_list"]) if r.get("code_list") else ""
            print("  %-16s required=%-5s source=%s%s%s" % (attr, r["required"], r["source"], tag, cl))
    return 0


def cmd_check_channel(vault, cls, channel, relpath, json_out):
    ct = _contract()
    path = os.path.join(vault, relpath)
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as e:
        print("NO-DATA: cannot read %s: %s" % (path, e))
        return 2
    block, _end = ct.frontmatter_span(text)
    fmap = ct._field_map(block) if block is not None else {}
    missing = missing_for_channel(cls, fmap, channel)
    if missing is None:
        print("NO-DATA: class %r is not declared" % cls)
        return 2
    if json_out:
        print(json.dumps({"class": cls, "channel": channel, "path": relpath,
                           "missing": missing}, sort_keys=True))
    else:
        if missing:
            print("REFUSED: %s missing for channel %s: %s" % (relpath, channel, ", ".join(missing)))
        else:
            print("PASS: %s satisfies channel %s" % (relpath, channel))
    return 1 if missing else 0


def cmd_codes_check(vault, all_mode, staged, json_out):
    ct = _contract()
    lists_data, err = load_code_lists(vault)
    if err:
        print("NO-DATA: %s" % err)
        return 2
    notes = ct._load_notes(vault)
    if notes is None:
        print("NO-DATA: no markdown files found under %s" % vault)
        return 2
    staged_rels = None
    if staged:
        staged_rels = ct._staged_relpaths(vault)
        if staged_rels is None:
            print("NO-DATA: --staged needs a readable git worktree at %s" % vault)
            return 2
        staged_rels = set(staged_rels)
        notes = [(rel, text) for rel, text in notes if rel in staged_rels]
    errors, queue = [], []
    for rel, text in notes:
        block, _end = ct.frontmatter_span(text)
        if block is None:
            continue
        fmap = ct._field_map(block)
        cls = fmap.get("class", "").strip()
        if cls not in CLASS_ATTRIBUTES:
            continue
        is_new = (rel in staged_rels) if staged_rels is not None else ct._is_new_by_date(fmap)
        for f in check_code_values(cls, fmap, lists_data, is_new):
            f["path"] = rel
            (errors if f["kind"] == "ERROR" else queue).append(f)
    if json_out:
        print(json.dumps({"tool": "bm_vault_attributes.codes_check", "verdict": "PASS",
                           "error_count": len(errors), "queue_count": len(queue),
                           "findings": errors + queue}, indent=2, sort_keys=True))
        return 0
    for f in errors:
        print("ERROR %s: %s" % (f["path"], f["detail"]))
    for f in queue:
        print("QUEUE %s: %s" % (f["path"], f["detail"]))
    print("%d note(s), %d ERROR, %d QUEUE" % (len(notes), len(errors), len(queue)))
    print("flag style: this verdict never blocks; bm_vault_tiers.py decides that")
    return 0


def cmd_codes_mutate(vault, mode, list_name, value, date_str):
    lists_data, err = load_code_lists(vault)
    if err:
        print("NO-DATA: %s" % err)
        return 2
    lists_data = lists_data or {}
    try:
        d = datetime.date.fromisoformat(date_str)
    except ValueError:
        print("NO-DATA: %r is not an ISO date" % date_str)
        return 2
    if mode == "add-value":
        ok, msg = add_value(lists_data, list_name, value, d)
    else:
        ok, msg = retire_value(lists_data, list_name, value, d)
    print(("ADDED " if mode == "add-value" and ok else
           "RETIRED " if ok else "REFUSED ") + msg)
    if not ok:
        return 1
    save_code_lists(vault, lists_data)
    return 0


def _build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    pcl = sub.add_parser("classes", help="print resolved attribute sets, overrides marked")
    pcl.add_argument("--class", dest="cls", default=None)
    pcl.add_argument("--json", action="store_true")

    pcc = sub.add_parser("check-channel", help="does one note satisfy one channel's requiredness")
    pcc.add_argument("--class", dest="cls", required=True)
    pcc.add_argument("--channel", required=True)
    pcc.add_argument("path", help="vault-relative path to the note")
    pcc.add_argument("--vault", default=None)
    pcc.add_argument("--json", action="store_true")

    pcodes = sub.add_parser("codes", help="governed unit/enum code lists")
    codesub = pcodes.add_subparsers(dest="codes_cmd", required=True)
    pchk = codesub.add_parser("check", help="census ERROR/QUEUE code-list violations")
    group = pchk.add_mutually_exclusive_group(required=True)
    group.add_argument("--staged", action="store_true")
    group.add_argument("--all", action="store_true")
    pchk.add_argument("--vault", default=None)
    pchk.add_argument("--json", action="store_true")
    padd = codesub.add_parser("add-value")
    padd.add_argument("--list", dest="list_name", required=True)
    padd.add_argument("--value", required=True)
    padd.add_argument("--valid-from", required=True)
    padd.add_argument("--vault", default=None)
    pret = codesub.add_parser("retire-value")
    pret.add_argument("--list", dest="list_name", required=True)
    pret.add_argument("--value", required=True)
    pret.add_argument("--valid-to", required=True)
    pret.add_argument("--vault", default=None)
    return p


def main(argv=None):
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.cmd == "classes":
        return cmd_classes(args.cls, args.json)
    if args.cmd == "check-channel":
        return cmd_check_channel(_vault_root(args.vault), args.cls, args.channel, args.path, args.json)
    if args.cmd == "codes":
        vault = _vault_root(args.vault)
        if args.codes_cmd == "check":
            return cmd_codes_check(vault, args.all, args.staged, args.json)
        if args.codes_cmd == "add-value":
            return cmd_codes_mutate(vault, "add-value", args.list_name, args.value, args.valid_from)
        return cmd_codes_mutate(vault, "retire-value", args.list_name, args.value, args.valid_to)
    return 2


if __name__ == "__main__":
    sys.exit(main())
