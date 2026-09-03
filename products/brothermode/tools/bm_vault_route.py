#!/usr/bin/env python3
"""bm_vault_route: per-owner defect routing across the estate's own findings. WBS VB10-03.

WHY THIS EXISTS. The estate already runs six kinds of check over the vault (the founder's
own words: "doctor, census, rot, triage, governance, posture"), and every one of them prints
its own report to whoever happened to run it. Nothing ever asked "whose problem is this" and
grouped the answer by person. This module is that grouping, report-only: it never edits a
note, never mints a `contradicts:` edge, never touches the vault.

THE SIX SOURCES, each an ALREADY-SHIPPED tool read by its own real output, never re-derived
with a second parser of the same data:

  census      bm_vault_contract.classify() (VB10-02's per-class metadata contract). Its own
              structured return already carries "path" per finding; consumed as-is.
  triage      bm_vault_triage.scan() (VB6-05's scope-first contradiction triage). Its own
              structured (a, b) contradiction pairs, each side already carrying "path".
  rot         bm_vault_staleness's own classify()/walk()/read_capture_expiry() (VB4-03/
              VB2-06's bi-temporal staleness contract) -- the exact per-note loop its own
              cmd_check runs, called here for the STRUCTURED verdict tuple instead of
              scraping cmd_check's printed lines, which is the tool's own better seam.
  doctor      bm_vault_lint.run_rules() over its own _load_notes() (the frontmatter schema
              linter). Structured {rule_name: [(path, message)]}, consumed as-is.
  governance  bm_vault_tiers.py's own WARN review queue: 99-System/telemetry/vault-review-
              queue.md, path named by bm_vault_tiers.QUEUE_RELPATH (read off that module,
              never a second literal), one line per finding in the format append_queue()
              itself documents and writes ("<stamp> WARN <class> <path-or-dash> <detail>").
              This is the estate's own already-aggregated cross-check queue, a stable
              documented line format, read as-is, never grepped from a check's prose.
  posture     bm_vault_posture.storage_state(vault) (VB8-01's encryption posture). Vault-
              wide, not note-scoped, so a "plaintext" verdict always lands in the default-
              owner lane: nothing in that finding names a path to resolve an owner from.

A sibling module missing or failing to load is NO-DATA for that one source (named, printed,
skipped), never a crash and never silently folded into "no findings".

OWNER AND STEWARD RESOLUTION IS NOT REIMPLEMENTED HERE. bm_vault_contract.py (VB10-02)
already built exactly what this row asks for: domain_of() (the owner grain, a top-level
folder or 10-Projects/<domain>), load_owners_map() (99-System/owners.json, "the way siblings
store declared config", beside access-policy.json), resolve_owner()/resolve_steward() (a
note's own owner:/steward: frontmatter field wins, the domain table otherwise, NO-DATA
never a guess). This module's owner_of() is a thin call onto those four functions; adding a
second copy of that table or that logic here is exactly the drift the survivorship module's
own docstring warns against (bm_vault_authority.LEVELS, one owner, never a second
vocabulary). Steward is resolved the SAME way, independently: it may equal the owner, it is
never defaulted to it.

DEDUPE BY FOLDER, the Monte Carlo lineage-grouping lesson named in the WBS row: notifying an
owner once per finding when five findings are really "this folder has a problem" trains the
owner to ignore the channel. Every finding is grouped first by owner (or DEFAULT_OWNER, see
below), then by the SAME domain-folder grain the owner table itself uses (domain_of(), or
the literal string "UNSCOPED" for a finding naming no path at all, e.g. posture). Two or more
findings sharing a folder collapse into ONE reported line carrying a count; the underlying
findings are never dropped, only the REPORTED line is deduped.

UNOWNED FINDINGS route to DEFAULT_OWNER ("unassigned" unless --default-owner names another),
never dropped: a finding this module could not attribute to a real owner is exactly the kind
of finding a silent drop would make invisible, so it lands in a named lane instead.

Exit 0: a readable vault with no findings from any loadable source. Exit 1: at least one
finding was routed to at least one owner (including the default lane). Exit 2: NO-DATA, an
unreadable vault or a malformed owners map (bm_vault_contract's own load_owners_map error).

Python 3.9, standard library only, no network, writes nothing.
"""
import argparse
import datetime
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OWNER = "unassigned"

# The line format bm_vault_tiers.append_queue() itself documents and writes:
# "%s WARN %s %s%s\n" % (stamp, cls, path_or_dash, detail_with_leading_space).
GOVERNANCE_LINE_RE = re.compile(r"^(\S+)\s+WARN\s+(\S+)\s+(\S+)(?:\s+(.*))?$")


def _load_sibling(name, tools_dir=None):
    """tools/<name>.py loaded BY PATH, the same technique bm_vault_survivorship.py and
    bm_vault_contract.py already use, so the vocabulary never depends on the caller's
    sys.path and a copy of tools/ elsewhere can be read as a self-contained unit. None
    when the file is absent or fails to import: a missing sibling is a NO-DATA finding
    for that one source, never a crash."""
    path = os.path.join(tools_dir or HERE, name + ".py")
    if not os.path.isfile(path):
        return None
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # sbe: allow-silent documented above: None is a NO-DATA finding for that one source when the sibling module is absent or fails to import
        return None


def _finding(source, path, detail):
    return {"source": source, "path": path, "detail": detail}


# ---------------------------------------------------------------------------
# The six sources. Each takes the already-loaded sibling module (never loads
# it itself), so collect_all controls NO-DATA in exactly one place.
# ---------------------------------------------------------------------------

def collect_census(vault, contract_mod):
    notes = contract_mod._load_notes(vault)
    if notes is None:
        return []
    result = contract_mod.classify(
        notes, candidate_value=contract_mod._promotion_candidate_value())
    return [_finding("census", f["path"], "%s: %s" % (f["kind"], f["detail"]))
            for f in result["error"] + result["queue"]]


def collect_triage(vault, triage_mod):
    _pairs, _scoped, contradictions, _unreadable = triage_mod.scan(vault)
    out = []
    for a, b in contradictions:
        out.append(_finding("triage", a["path"],
                             "contradiction with %s on %r" % (b["path"], a["subject"])))
        out.append(_finding("triage", b["path"],
                             "contradiction with %s on %r" % (a["path"], b["subject"])))
    return out


def collect_rot(vault, staleness_mod, today=None):
    """Reuses staleness_mod's own walk()/read_capture_expiry()/classify(), the exact
    building blocks its cmd_check calls, so the verdict here is the tool's own, never a
    second implementation of the freshness comparison."""
    today = today or datetime.date.today()
    out = []
    for path in staleness_mod.walk(vault):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:  # sbe: allow-silent vault walk reused from staleness_mod skips a file it cannot read, same convention as its own walk() callers
            continue
        rel = os.path.relpath(path, vault)
        is_capture, _expires = staleness_mod.read_capture_expiry(text)
        if is_capture:
            continue
        state, verified, age, _problem = staleness_mod.classify(text, today)
        if state == "stale":
            out.append(_finding("rot", rel, "stale since %s (%d days)" % (verified, age)))
    return out


def collect_doctor(vault, lint_mod):
    notes = lint_mod._load_notes(vault)
    if notes is None:
        return []
    by_rule = lint_mod.run_rules(notes)
    out = []
    for rule_name in lint_mod.RULE_NAMES:
        for rel, msg in by_rule[rule_name]:
            out.append(_finding("doctor", rel, "%s: %s" % (rule_name, msg)))
    return out


def collect_governance(vault, tiers_mod):
    queue_path = os.path.join(vault, tiers_mod.QUEUE_RELPATH)
    if not os.path.isfile(queue_path):
        return []
    out = []
    with open(queue_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = GOVERNANCE_LINE_RE.match(line.strip())
            if not m:
                continue  # frontmatter / blank lines in the queue file, not a finding
            _stamp, cls, path, detail = m.groups()
            out.append(_finding("governance", None if path == "-" else path,
                                 "%s%s" % (cls, (": " + detail) if detail else "")))
    return out


def collect_posture(vault, posture_mod):
    verdict, detail = posture_mod.storage_state(vault)
    if verdict == "plaintext":
        return [_finding("posture", None, "vault storage is plaintext: %s" % detail)]
    return []


# name -> (sibling module name, collector). Declared once so collect_all and any caller
# naming a --skip source read the identical list.
SOURCES = (
    ("census", "bm_vault_contract", collect_census),
    ("triage", "bm_vault_triage", collect_triage),
    ("rot", "bm_vault_staleness", collect_rot),
    ("doctor", "bm_vault_lint", collect_doctor),
    ("governance", "bm_vault_tiers", collect_governance),
    ("posture", "bm_vault_posture", collect_posture),
)


def collect_all(vault, tools_dir=None):
    """([finding, ...], [no_data_source_name, ...]). A sibling that fails to load names
    itself in the second list rather than being silently treated as "no findings"."""
    findings = []
    no_data = []
    for name, modname, collector in SOURCES:
        mod = _load_sibling(modname, tools_dir)
        if mod is None:
            no_data.append(name)
            continue
        findings.extend(collector(vault, mod))
    return findings, no_data


# ---------------------------------------------------------------------------
# Owner/steward resolution (delegated, see the module docstring) and routing.
# ---------------------------------------------------------------------------

def _note_fmap(vault, path, contract_mod):
    """The note's own frontmatter map, {} for an unreadable file or one with no
    frontmatter block: resolve_owner/resolve_steward already treat an absent field as
    "check the domain table", never a reason to invent a value here."""
    try:
        with open(os.path.join(vault, path), encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return {}
    block, _end = contract_mod.frontmatter_span(text)
    return contract_mod._field_map(block) if block is not None else {}


def owner_of(vault, path, owners_map, contract_mod):
    """(owner|None, steward|None) for one finding's path, None for a pathless finding
    (posture). Pure delegation onto bm_vault_contract's own resolve_owner/resolve_steward:
    a note-level owner:/steward: field wins, the domain folder table otherwise, None when
    neither names one. The caller turns a None owner into DEFAULT_OWNER; this function
    never guesses one itself."""
    if path is None:
        return None, None
    fmap = _note_fmap(vault, path, contract_mod)
    owner, _src = contract_mod.resolve_owner(path, fmap, owners_map)
    steward, _src2 = contract_mod.resolve_steward(path, fmap, owners_map)
    return owner, steward


def route_findings(vault, findings, owners_map, contract_mod, default_owner=DEFAULT_OWNER):
    """owner -> [{"folder", "count", "findings": [...]}, ...], each owner's list sorted by
    folder name. folder is contract_mod.domain_of(path), or the literal "UNSCOPED" for a
    pathless finding. Two or more findings sharing (owner, folder) collapse into one group
    with count = len(findings); the findings themselves are always still there, listed."""
    by_owner = {}
    for f in findings:
        owner, _steward = owner_of(vault, f["path"], owners_map, contract_mod)
        owner = owner or default_owner
        folder = contract_mod.domain_of(f["path"]) if f["path"] else "UNSCOPED"
        groups = by_owner.setdefault(owner, {})
        group = groups.setdefault(folder, {"folder": folder, "count": 0, "findings": []})
        group["count"] += 1
        group["findings"].append(f)
    return {owner: sorted(groups.values(), key=lambda g: g["folder"])
            for owner, groups in by_owner.items()}


def render_report(owner, groups):
    lines = ["=== owner: %s ===" % owner]
    for g in groups:
        lines.append("%s: %d finding(s)" % (g["folder"], g["count"]))
        for f in g["findings"]:
            where = (f["path"] + ": ") if f["path"] else ""
            lines.append("  [%s] %s%s" % (f["source"], where, f["detail"]))
    return "\n".join(lines)


def cmd_route(vault, owners_override, default_owner, json_out, tools_dir=None):
    if not vault or not os.path.isdir(vault):
        print("bm_vault_route: NO-DATA, no readable vault at %r" % vault, file=sys.stderr)
        return 2
    contract_mod = _load_sibling("bm_vault_contract", tools_dir)
    if contract_mod is None:
        print("bm_vault_route: NO-DATA, bm_vault_contract failed to load, owner/steward "
              "resolution has no seam without it", file=sys.stderr)
        return 2
    owners_map, err = contract_mod.load_owners_map(vault, owners_override)
    if err:
        print("bm_vault_route: NO-DATA, %s" % err, file=sys.stderr)
        return 2
    findings, no_data = collect_all(vault, tools_dir)
    routed = route_findings(vault, findings, owners_map, contract_mod, default_owner)
    if json_out:
        print(json.dumps({"vault": vault, "no_data_sources": sorted(no_data),
                           "routed": routed}, indent=2, sort_keys=True))
        return 1 if routed else 0
    print("vault: %s" % vault)
    if no_data:
        print("NO-DATA sources (sibling failed to load, skipped): %s" % ", ".join(sorted(no_data)))
    if not routed:
        print("clean: no findings from any loadable source")
        return 0
    for owner in sorted(routed):
        print(render_report(owner, routed[owner]))
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("route",))
    ap.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    ap.add_argument("--owners", default=None,
                     help="override path to the owners map, same convention as "
                          "bm_vault_contract's own --registry-style overrides")
    ap.add_argument("--default-owner", default=DEFAULT_OWNER,
                     help="named lane for a finding that resolves to no owner at all "
                          "(default: %(default)s)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    return cmd_route(args.vault, args.owners, args.default_owner, args.json)


if __name__ == "__main__":
    sys.exit(main())
