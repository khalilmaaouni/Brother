#!/usr/bin/env python3
"""bm_vault_contract: the founder-approved per-class metadata contract (VB10-02).

WHY A NEW MODULE, NOT AN EXTENSION OF bm_vault_lint.py. Lint's own table is
BASE_REQUIRED = (id, type, status, created), the same four fields for every
type, plus ONE per-type addition (failure needs symptom). The approved table
below does not fit that shape: entity needs no status and no created at all,
capture needs neither status, created, NOR owner. Layering the approved table
onto lint's additive model would either lie about entity/capture (claim they
need fields the founder's table never asked for) or break lint's own passing
calibration for every other type. So this is its own module with its own
table, self-contained (its own tiny frontmatter reader, mirroring the
technique bm_vault_lifecycle.py and bm_vault_lint.py already use rather than
importing lint's private helpers and risking a load-order cycle).

THE TABLE, verbatim, founder-approved 2026-08-30:
  decision  id, owner, status, created, description
  failure   id, owner, symptom, verified-by, created
  reference id, owner, description, created
  entity    id, owner, description
  capture   id, captured_by, captured_at, expiry_class, promotion=candidate
  session-log                                    EXEMPT (immutable history)
A type not named above (finding, overview, index) is outside this contract's
scope: it is neither checked nor exempted, because the approved table never
named it.

PROGRESSIVE, NOT RETROACTIVE. The contract binds notes MINTED on or after its
adoption date; the 843-note legacy corpus is debt to be counted, never a
sudden pile of blocking errors. A note is "new" when it is staged for commit
(--staged) or when its own created: date is on or after ADOPTED (--all).
NEW-note violations classify ERROR; legacy violations classify QUEUE.

FLAG STYLE, NEVER BLOCKING HERE. `check` always exits 0 on a readable vault
(2 only for NO-DATA: no notes, or --staged on a non-git vault). It reports
counts; it does not gate anything. Blocking is bm_vault_tiers.py's job (on
its own branch) once it imports classify() below as its seam.

OWNER GRAIN: the domain folder (10-Projects/<domain>, or a bare top-level
folder like 40-Failures), read from tools/bm_vault_contract's OWNERS_RELPATH
map (99-System/owners.json, beside access-policy.json, same convention as
bm_vault_policy.POLICY_RELPATH). A note's own owner: field always overrides.
Steward resolves the same way, independently (may equal owner, never
defaulted to it). No owners.json on disk is NO-DATA, never a guess.

Python 3.9, standard library only, no network, writes nothing.
"""
import argparse
import datetime
import importlib.util
import json
import os
import re
import subprocess
import sys

DEFAULT_VAULT = os.environ.get("BROTHERMODE_VAULT") or os.path.expanduser("~/Documents/Kay Vault")
SKIP_DIRS = {".git", ".trash", ".obsidian"}
HERE = os.path.dirname(os.path.abspath(__file__))

OWNERS_RELPATH = os.path.join("99-System", "owners.json")
ADOPTED = datetime.date(2026, 8, 30)  # founder approval date of THIS table

# id -> required field tuple. "type" itself is the discriminant, not a value
# checked for presence beyond being readable at all.
CONTRACT = {
    "decision": ("id", "owner", "status", "created", "description"),
    "failure": ("id", "owner", "symptom", "verified-by", "created"),
    "reference": ("id", "owner", "description", "created"),
    "entity": ("id", "owner", "description"),
    "capture": ("id", "captured_by", "captured_at", "expiry_class", "promotion"),
}
# Fields whose PRESENCE is not enough: the value itself is constrained. A
# capture is raw and unvalidated by definition, so its lifecycle field must
# read the entry state, never anything past it. Sourced from
# bm_vault_lifecycle.STATES[0] at runtime (never a second hardcoded literal);
# the literal below is only the fallback when that module cannot be loaded.
REQUIRED_VALUES = {"capture": {"promotion": "candidate"}}
EXEMPT_TYPES = frozenset({"session-log"})

FRONT_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):(.*)$")


def _load_sibling(name):
    """tools/<name>.py loaded BY PATH, guarded, the same technique
    bm_vault_lint.py already uses so the vocabulary never depends on the
    caller's sys.path. None when absent or broken; callers turn that into a
    named NO-DATA finding rather than a silent pass."""
    path = os.path.join(HERE, name + ".py")
    if not os.path.isfile(path):
        return None
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # sbe: allow-silent optional sibling module load failure, caller turns None into a named NO-DATA finding per docstring
        return None


def _promotion_candidate_value():
    mod = _load_sibling("bm_vault_lifecycle")
    if mod is not None and getattr(mod, "STATES", None):
        return mod.STATES[0]
    return REQUIRED_VALUES["capture"]["promotion"]


def _vault_root(cli_vault):
    if cli_vault:
        return cli_vault
    env = os.environ.get("BM_VAULT_ROOT")
    if env:
        return env
    return DEFAULT_VAULT


def frontmatter_span(text):
    """(block, end) exactly like the sibling bm_vault_* readers: block is
    text[3:end] with no leading newline required, so `---id: ...` on one
    line parses the same as the newline-separated form."""
    if not text.startswith("---"):
        return None, -1
    end = text.find("\n---", 3)
    if end == -1:
        return None, -1
    return text[3:end], end


def _field_map(block):
    """key -> value for every top-level `key: value` line, continuation
    lines folded into the preceding key. First declaration wins, mirroring
    bm_vault_lint._field_map."""
    fields = []
    for line in block.split("\n"):
        m = FRONT_KEY.match(line)
        if m:
            fields.append([m.group(1), m.group(2).strip()])
        elif fields:
            fields[-1][1] = (fields[-1][1] + "\n" + line).strip()
    out = {}
    for k, v in fields:
        if k not in out:
            out[k] = v
    return out


def _strip_quotes(raw):
    return raw.strip().strip('"').strip("'")


def _walk_md(vault):
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(filenames):
            if fn.endswith(".md"):
                yield os.path.join(dirpath, fn)


def _load_notes(vault):
    """[(relpath, text)] or None when the vault has no markdown at all."""
    if not os.path.isdir(vault):
        return None
    notes = []
    for path in _walk_md(vault):
        rel = os.path.relpath(path, vault)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                notes.append((rel, fh.read()))
        except OSError as exc:
            sys.stderr.write("bm_vault_contract: cannot read %s: %s\n" % (rel, exc))
    return notes if notes else None


def _staged_relpaths(vault):
    """[relpath, ...] of staged .md files, or None when the vault is not a
    readable git worktree (NO-DATA, never a silent empty list)."""
    try:
        out = subprocess.run(
            ["git", "-C", vault, "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return [p for p in out.stdout.splitlines() if p.endswith(".md")]


# ---------------------------------------------------------------------------
# The contract itself, table-driven so a test can hand it a mutated copy.
# ---------------------------------------------------------------------------

def missing_fields(note_type, fmap, contract=None):
    """Required fields absent or blank for note_type, per `contract` (defaults
    to the module CONTRACT). [] for an exempt or out-of-scope type: this
    contract never invents a rule for a class the approved table did not
    name."""
    table = CONTRACT if contract is None else contract
    if note_type in EXEMPT_TYPES or note_type not in table:
        return []
    return [f for f in table[note_type] if not fmap.get(f, "").strip()]


def value_violations(note_type, fmap, required_values=None, candidate_value=None):
    """[(field, expected, actual)] for fields whose value is constrained
    beyond presence. Skips a field missing_fields() already named absent."""
    table = REQUIRED_VALUES if required_values is None else required_values
    if note_type not in table:
        return []
    expected_map = dict(table[note_type])
    if note_type == "capture" and "promotion" in expected_map and candidate_value is not None:
        expected_map["promotion"] = candidate_value
    out = []
    for field, expected in expected_map.items():
        raw = fmap.get(field)
        if raw is None or not raw.strip():
            continue  # missing_fields already names the absence
        actual = _strip_quotes(raw)
        if actual != expected:
            out.append((field, expected, actual))
    return out


def classify_note(rel, text, is_new, contract=None, candidate_value=None):
    """[finding, ...] for one note. finding = {"kind": "ERROR"|"QUEUE",
    "path", "class", "field", "detail"}. [] for a clean or out-of-scope
    note."""
    block, _end = frontmatter_span(text)
    if block is None:
        return []
    fmap = _field_map(block)
    note_type = fmap.get("type", "").strip()
    kind = "ERROR" if is_new else "QUEUE"
    findings = []
    for field in missing_fields(note_type, fmap, contract):
        findings.append({"kind": kind, "path": rel, "class": note_type, "field": field,
                          "detail": "missing required field %r" % field})
    for field, expected, actual in value_violations(note_type, fmap, candidate_value=candidate_value):
        findings.append({"kind": kind, "path": rel, "class": note_type, "field": field,
                          "detail": "%r must be %r, got %r" % (field, expected, actual)})
    return findings


def _is_new_by_date(fmap):
    raw = fmap.get("created", "").strip().strip('"').strip("'")
    try:
        return datetime.date.fromisoformat(raw) >= ADOPTED
    except ValueError:
        return False  # unparseable created: is legacy debt, not a new-note claim


def classify(notes, staged_rels=None, candidate_value=None):
    """notes: [(relpath, text)]. staged_rels: set of relpaths to force ERROR
    (the --staged mode), or None to classify by created: date (--all).
    Returns {"error": [...], "queue": [...], "by_class": {cls: {"error": n,
    "queue": n}}}: the per-class gap census the row's item 1(b) asks for."""
    error, queue = [], []
    by_class = {}
    for rel, text in notes:
        block, _end = frontmatter_span(text)
        fmap = _field_map(block) if block is not None else {}
        if staged_rels is not None:
            is_new = rel in staged_rels
        else:
            is_new = _is_new_by_date(fmap)
        findings = classify_note(rel, text, is_new, candidate_value=candidate_value)
        for f in findings:
            bucket = error if f["kind"] == "ERROR" else queue
            bucket.append(f)
            slot = by_class.setdefault(f["class"], {"error": 0, "queue": 0})
            slot["error" if f["kind"] == "ERROR" else "queue"] += 1
    return {"error": error, "queue": queue, "by_class": by_class}


# ---------------------------------------------------------------------------
# Owner / steward resolution.
# ---------------------------------------------------------------------------

def domain_of(relpath):
    """The owner grain for a vault-relative path: 10-Projects/<domain>, or
    the bare top-level folder otherwise (40-Failures, 30-Entities, ...)."""
    parts = relpath.replace(os.sep, "/").split("/")
    if not parts:
        return None
    if parts[0] == "10-Projects" and len(parts) > 1:
        return "10-Projects/" + parts[1]
    return parts[0]


def load_owners_map(vault, override=None):
    """(map | None, error | None). None, None is the opt-in absent case
    (NO-DATA downstream, never a guess). None, "reason" is a present but
    broken file, which must never silently read as "no map"."""
    path = override or (os.path.join(vault, OWNERS_RELPATH) if vault else None)
    if not path or not os.path.isfile(path):
        return None, None
    try:
        with open(path, encoding="utf-8") as fh:
            loaded = json.load(fh)
    except (OSError, ValueError) as e:
        return None, "unreadable owners map %s: %s" % (path, e)
    if not isinstance(loaded, dict) or not isinstance(loaded.get("domains", {}), dict):
        return None, "owners map %s must be {\"domains\": {domain: {\"owner\": ..}}}" % path
    return loaded, None


def _resolve(field, relpath, fmap, owners_map):
    """(value|None, source). source in "note", "domain:<name>", "no-data"."""
    raw = fmap.get(field)
    if raw is not None and raw.strip():
        return _strip_quotes(raw), "note"
    if owners_map is None:
        return None, "no-data"
    domain = domain_of(relpath)
    entry = owners_map.get("domains", {}).get(domain, {})
    value = entry.get(field if field != "steward" else "steward")
    if value:
        return value, "domain:%s" % domain
    return None, "no-data"


def resolve_owner(relpath, fmap, owners_map):
    return _resolve("owner", relpath, fmap, owners_map)


def resolve_steward(relpath, fmap, owners_map):
    return _resolve("steward", relpath, fmap, owners_map)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _emit_json(tool, verdict, counts, findings):
    print(json.dumps({"tool": tool, "verdict": verdict, "counts": counts,
                       "findings": findings, "schema_version": 1}, indent=2, sort_keys=True))


def cmd_check(vault, staged, json_out):
    notes = _load_notes(vault)
    if notes is None:
        msg = "NO-DATA: no markdown files found under %s" % vault
        if json_out:
            _emit_json("bm_vault_contract.check", "NO-DATA", {},
                       [{"kind": "no_data", "path": None, "detail": msg}])
        else:
            print(msg)
        return 2
    staged_rels = None
    if staged:
        staged_rels = _staged_relpaths(vault)
        if staged_rels is None:
            msg = "NO-DATA: --staged needs a readable git worktree at %s" % vault
            if json_out:
                _emit_json("bm_vault_contract.check", "NO-DATA", {},
                           [{"kind": "no_data", "path": None, "detail": msg}])
            else:
                print(msg)
            return 2
        notes = [(rel, text) for rel, text in notes if rel in set(staged_rels)]
    result = classify(notes, staged_rels=set(staged_rels) if staged_rels is not None else None,
                       candidate_value=_promotion_candidate_value())
    counts = {"note_count": len(notes), "error_count": len(result["error"]),
              "queue_count": len(result["queue"]), "by_class": result["by_class"]}
    if json_out:
        _emit_json("bm_vault_contract.check", "PASS", counts, result["error"] + result["queue"])
        return 0
    print("mode: %s" % ("staged" if staged else "all"))
    for f in result["error"]:
        print("ERROR %s: %s" % (f["path"], f["detail"]))
    for f in result["queue"]:
        print("QUEUE %s: %s" % (f["path"], f["detail"]))
    print("%d note(s), %d ERROR, %d QUEUE" % (len(notes), len(result["error"]), len(result["queue"])))
    for cls in sorted(result["by_class"]):
        c = result["by_class"][cls]
        print("  %s: %d error, %d queue" % (cls, c["error"], c["queue"]))
    print("flag style: this verdict never blocks; bm_vault_tiers.py decides that")
    return 0


def cmd_resolve(vault, relpath, json_out):
    path = os.path.join(vault, relpath)
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as e:
        print("NO-DATA: cannot read %s: %s" % (path, e))
        return 2
    block, _end = frontmatter_span(text)
    fmap = _field_map(block) if block is not None else {}
    owners_map, err = load_owners_map(vault)
    if err:
        print("NO-DATA: %s" % err)
        return 2
    owner, owner_src = resolve_owner(relpath, fmap, owners_map)
    steward, steward_src = resolve_steward(relpath, fmap, owners_map)
    if json_out:
        print(json.dumps({"path": relpath, "owner": owner, "owner_source": owner_src,
                           "steward": steward, "steward_source": steward_src}, sort_keys=True))
    else:
        print("owner: %s (source: %s)" % (owner or "NO-DATA", owner_src))
        print("steward: %s (source: %s)" % (steward or "NO-DATA", steward_src))
    return 0


def _build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    pc = sub.add_parser("check", help="flag ERROR (new-note) and QUEUE (legacy) violations")
    group = pc.add_mutually_exclusive_group(required=True)
    group.add_argument("--staged", action="store_true")
    group.add_argument("--all", action="store_true")
    pc.add_argument("--vault", default=None)
    pc.add_argument("--json", action="store_true")
    pr = sub.add_parser("resolve", help="print a note's effective owner and steward")
    pr.add_argument("path", help="vault-relative path to the note")
    pr.add_argument("--vault", default=None)
    pr.add_argument("--json", action="store_true")
    return p


def main(argv=None):
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    vault = _vault_root(args.vault)
    if args.cmd == "resolve":
        return cmd_resolve(vault, args.path, args.json)
    return cmd_check(vault, args.staged, args.json)


if __name__ == "__main__":
    sys.exit(main())
