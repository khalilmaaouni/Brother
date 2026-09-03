#!/usr/bin/env python3
"""bm_vault_lint: the frontmatter schema linter, the plugin survey's top port.

WHY THIS EXISTS. bm_vault_graph.py already gates links and the status:/type:
enums. Nothing enforces the WIDER contract every other bm_vault_* module
assumes exists: required fields per type, the id format, ISO dates in the five
bi-temporal fields plus created:, the authority and lifecycle vocabularies, and
no field declared twice. A headless linter closes that gap before the graph
gate ever sees the drift.

  check --vault V              every note's frontmatter against the contract.
                                Exit 0 clean, 1 findings, 2 NO-DATA (the vault
                                itself is unreadable, e.g. no .md files at all).
  fix --vault V [--apply]      normalizes ONLY whitelisted mechanical shapes:
                                field ordering to the house order, trailing
                                whitespace inside frontmatter lines, and
                                quoting style for verified-by. Dry by default;
                                --apply writes atomically (temp file, then
                                os.replace). Never invents a missing value,
                                never touches body text.

VOCABULARIES ARE NEVER DUPLICATED. authority's three levels come from
bm_vault_authority.LEVELS, the lifecycle states from bm_vault_lifecycle.STATES,
the id shape from bm_vault_ids.ID_VALUE_RE, and the five bi-temporal field
names from bm_vault_temporal.FIELDS, all loaded BY PATH (the technique the
sibling tools already use, so the answer never depends on the caller's
sys.path). The status:/type: enums are deliberately NOT rechecked here:
bm_vault_graph.py already owns that rule, and duplicating it would let the two
gates drift apart from each other, exactly the failure this file exists to
prevent.

A RULE THAT CANNOT RUN IS NEVER A SILENT PASS. When a contract module is
missing or fails to import, the rule that depends on it emits one NO-DATA
finding naming the rule and the module, and every other rule still runs. A
missing contract module can therefore never make `check` print "clean".

Python 3.9, standard library only, no network.
"""
import argparse
import datetime
import importlib.util
import json
import os
import re
import sys

DEFAULT_VAULT = os.environ.get("BROTHERMODE_VAULT") or os.path.expanduser("~/Documents/Kay Vault")
SKIP_DIRS = {".git", ".trash", ".obsidian"}
HERE = os.path.dirname(os.path.abspath(__file__))

FRONT_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):(.*)$")
BASE_REQUIRED = ("id", "type", "status", "created")
# The one per-type extra the plugin survey named. Every other type adds
# nothing beyond BASE_REQUIRED; inventing more here is a rule nobody asked for.
EXTRA_REQUIRED_BY_TYPE = {"failure": ("symptom",)}
# The house order fix normalizes toward: id first, then type, authority,
# project, created, status, the five temporal fields (bm_vault_temporal's own
# order), tags, then everything else in its original relative order.
HOUSE_ORDER_HEAD = ("id", "type", "authority", "project", "created", "status")
HOUSE_ORDER_TAIL = ("tags",)
_FALLBACK_TEMPORAL_FIELDS = ("valid_from", "valid_to", "observed_at",
                              "ingested_at", "verified_at")
_FALLBACK_ID_RE = re.compile(r"^n-[0-9a-f]{16}$")


def _load_sibling(name):
    """tools/<name>.py loaded BY PATH, guarded. Returns the module, or None
    when the file is absent or fails to import: a missing contract module
    must never crash the lint or silently skip the rule built on it, so the
    caller turns None into a named NO-DATA finding instead."""
    path = os.path.join(HERE, name + ".py")
    if not os.path.isfile(path):
        return None
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # sbe: allow-silent broken contract module, per docstring caller turns None into a named NO-DATA
        return None


def _vault_root(cli_vault):
    if cli_vault:
        return cli_vault
    env = os.environ.get("BM_VAULT_ROOT")
    if env:
        return env
    return DEFAULT_VAULT


def _walk_md(vault):
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(filenames):
            if fn.endswith(".md"):
                yield os.path.join(dirpath, fn)


def _load_notes(vault):
    """[(relpath, text)], or None when the vault has no markdown at all. A
    single unreadable file is skipped and warned about, never a crash and
    never silently dropped from the count."""
    if not os.path.isdir(vault):
        return None
    notes = []
    for path in _walk_md(vault):
        rel = os.path.relpath(path, vault)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                notes.append((rel, fh.read()))
        except OSError as exc:
            sys.stderr.write("bm_vault_lint: cannot read %s: %s\n" % (rel, exc))
    return notes if notes else None


def frontmatter_span(text):
    """(block, end) where block = text[3:end], the same convention every
    sibling bm_vault_* module uses, or (None, -1) when there is no closing
    fence. block does not include a required leading newline: a note whose
    frontmatter opens `---id: ...` on one line (observed in this vault) is
    real and must parse the same as the newline-separated form."""
    if not text.startswith("---"):
        return None, -1
    end = text.find("\n---", 3)
    if end == -1:
        return None, -1
    return text[3:end], end


def _parse_date(raw):
    try:
        return datetime.date.fromisoformat(raw.strip().strip('"').strip("'"))
    except ValueError:  # sbe: allow-silent unparseable date reader degrades to None, callers treat that as no date
        return None


def _iter_fields(block):
    """[(key, value, line_no_in_block)] for every top-level `key: value` line
    in a frontmatter block. A continuation line (indented, or not matching
    `key:` at column 0, e.g. a multi-line list under one key) is folded into
    the PRECEDING field's value with a newline, so a value that spans several
    lines is still seen once, under its own key, rather than misread as a
    second declaration of whatever key happens to come next."""
    fields = []
    for line in block.split("\n"):
        m = FRONT_KEY.match(line)
        if m:
            fields.append([m.group(1), m.group(2).strip()])
        elif fields:
            fields[-1][1] = (fields[-1][1] + "\n" + line).strip()
    return [(k, v) for k, v in fields]


def _field_map(fields):
    """key -> first value, for rules that only care about presence/format.
    Duplicate detection reads `fields` directly instead, on purpose."""
    seen = {}
    for k, v in fields:
        if k not in seen:
            seen[k] = v
    return seen


class Rules(object):
    """One method per rule. Each returns a list of (relpath, message)
    findings for the notes it was given. A rule that cannot run because a
    contract module is missing returns a single findings entry naming
    itself, via `_no_data`, rather than an empty (falsely clean) list."""

    def __init__(self):
        self.ids_mod = _load_sibling("bm_vault_ids")
        self.temporal_mod = _load_sibling("bm_vault_temporal")
        self.authority_mod = _load_sibling("bm_vault_authority")
        self.lifecycle_mod = _load_sibling("bm_vault_lifecycle")

    @staticmethod
    def _no_data(rule, module_name):
        return [("(vault-wide)", "NO-DATA: rule %s could not run, %s.py "
                                  "not found or not importable" % (rule, module_name))]

    def required_fields(self, notes):
        out = []
        for rel, text in notes:
            block, end = frontmatter_span(text)
            if block is None:
                out.append((rel, "no frontmatter block"))
                continue
            fmap = _field_map(_iter_fields(block))
            for key in BASE_REQUIRED:
                if not fmap.get(key, "").strip():
                    out.append((rel, "missing required field %r" % key))
            note_type = fmap.get("type", "").strip()
            for extra in EXTRA_REQUIRED_BY_TYPE.get(note_type, ()):
                if not fmap.get(extra, "").strip():
                    out.append((rel, "type %r requires %r, missing" % (note_type, extra)))
        return out

    def id_format(self, notes):
        if self.ids_mod is None:
            return self._no_data("id_format", "bm_vault_ids")
        id_re = self.ids_mod.ID_VALUE_RE
        out = []
        for rel, text in notes:
            block, _end = frontmatter_span(text)
            if block is None:
                continue
            fmap = _field_map(_iter_fields(block))
            raw = fmap.get("id")
            if raw is None:
                continue  # required_fields already names the absence
            value = raw.strip().strip('"').strip("'")
            if not id_re.match(value):
                out.append((rel, "id %r is not n-<16 hex chars>" % value))
        return out

    def date_format(self, notes):
        if self.temporal_mod is None:
            return self._no_data("date_format", "bm_vault_temporal")
        fields = ("created",) + tuple(self.temporal_mod.FIELDS)
        out = []
        for rel, text in notes:
            block, _end = frontmatter_span(text)
            if block is None:
                continue
            fmap = _field_map(_iter_fields(block))
            for key in fields:
                raw = fmap.get(key)
                if raw is None or not raw.strip():
                    continue
                if _parse_date(raw) is None:
                    out.append((rel, "%s: %r is not an ISO YYYY-MM-DD date" % (key, raw.strip())))
        return out

    def authority_vocab(self, notes):
        if self.authority_mod is None:
            return self._no_data("authority_vocab", "bm_vault_authority")
        levels = set(self.authority_mod.LEVELS)
        out = []
        for rel, text in notes:
            block, _end = frontmatter_span(text)
            if block is None:
                continue
            fmap = _field_map(_iter_fields(block))
            raw = fmap.get("authority")
            if raw is None:
                continue  # absent ranks as casual by the contract's own rule
            value = raw.strip().strip('"').strip("'")
            if value not in levels:
                out.append((rel, "authority %r not in %s" % (value, "/".join(levels))))
        return out

    def lifecycle_vocab(self, notes):
        if self.lifecycle_mod is None:
            return self._no_data("lifecycle_vocab", "bm_vault_lifecycle")
        states = set(self.lifecycle_mod.STATES)
        out = []
        for rel, text in notes:
            block, _end = frontmatter_span(text)
            if block is None:
                continue
            fmap = _field_map(_iter_fields(block))
            raw = fmap.get("promotion")
            if raw is None:
                continue  # absent reads as legacy by the contract's own rule
            value = raw.strip().strip('"').strip("'")
            if value not in states:
                out.append((rel, "promotion %r not in %s" % (value, "/".join(states))))
        return out

    def duplicate_fields(self, notes):
        out = []
        for rel, text in notes:
            block, _end = frontmatter_span(text)
            if block is None:
                continue
            counts = {}
            for k, _v in _iter_fields(block):
                counts[k] = counts.get(k, 0) + 1
            for k, n in sorted(counts.items()):
                if n > 1:
                    out.append((rel, "field %r declared %d times" % (k, n)))
        return out


RULE_NAMES = ("required_fields", "id_format", "date_format", "authority_vocab",
              "lifecycle_vocab", "duplicate_fields")


def run_rules(notes):
    """rule_name -> [(relpath, message)]."""
    rules = Rules()
    return {name: getattr(rules, name)(notes) for name in RULE_NAMES}


def _emit_json(tool, verdict, counts, findings):
    """The one shared --json envelope across every bm_vault_* reporting tool
    (VB7-02): {tool, verdict, counts, findings, schema_version}. verdict is
    always "PASS", "FAIL" or "NO-DATA" and always matches the process exit
    code the caller returns; counts/findings never change the exit code,
    only its format."""
    print(json.dumps({
        "tool": tool,
        "verdict": verdict,
        "counts": counts,
        "findings": findings,
        "schema_version": 1,
    }, indent=2, sort_keys=True))


def cmd_check(vault, json_out=False):
    notes = _load_notes(vault)
    if notes is None:
        msg = "NO-DATA: no markdown files found under %s" % vault
        if json_out:
            _emit_json("bm_vault_lint.check", "NO-DATA", {},
                       [{"kind": "no_data", "path": None, "detail": msg}])
        else:
            print(msg)
        return 2
    by_rule = run_rules(notes)
    total = 0
    findings = []
    counts = {"note_count": len(notes)}
    for name in RULE_NAMES:
        rule_findings = by_rule[name]
        counts[name] = len(rule_findings)
        for rel, msg in rule_findings:
            findings.append({"kind": name, "path": rel, "detail": msg})
        total += len(rule_findings)
    counts["violation_count"] = total
    if json_out:
        verdict = "FAIL" if total else "PASS"
        _emit_json("bm_vault_lint.check", verdict, counts, findings)
        return 1 if total else 0
    for name in RULE_NAMES:
        rule_findings = by_rule[name]
        print("%s: %d finding(s)" % (name, len(rule_findings)))
        for rel, msg in rule_findings:
            print("  %s: %s" % (rel, msg))
    if total:
        print("%d violation(s) across %d rule(s)" % (total, len(RULE_NAMES)))
        return 1
    print("OK: %d notes, 0 findings across %d rule(s)" % (len(notes), len(RULE_NAMES)))
    return 0


# ---------------------------------------------------------------------------
# fix: mechanical, whitelisted normalization only. Reorders whole field
# groups (a key plus every continuation line under it) rather than
# reformatting values, and never touches anything outside the frontmatter
# span, which is what keeps a Dataview inline field or an obsidian-tasks
# emoji marker in the body byte-identical across a fix --apply.
# ---------------------------------------------------------------------------

_SINGLE_QUOTED = re.compile(r"^'(.*)'$", re.S)


def _group_lines(block):
    """[(key_or_None, [raw_line, ...])]. key is None for the leading
    preamble group (lines before the first top-level key, e.g. the empty
    first line a `---\\nid: ...` block starts with); every other group is
    exactly one field and its continuation lines, in original order."""
    groups = []
    for line in block.split("\n"):
        m = FRONT_KEY.match(line)
        if m or not groups:
            groups.append([m.group(1) if m else None, [line]])
        else:
            groups[-1][1].append(line)
    return groups


def _reorder(groups, temporal_fields):
    house = list(HOUSE_ORDER_HEAD) + list(temporal_fields) + list(HOUSE_ORDER_TAIL)
    by_key = {g[0]: g for g in groups if g[0] is not None}
    ordered = [g for g in groups if g[0] is None]  # preamble, always first
    for key in house:
        if key in by_key:
            ordered.append(by_key.pop(key))
    # everything else keeps its original relative order
    for g in groups:
        if g[0] is not None and g[0] in by_key:
            ordered.append(g)
            del by_key[g[0]]
    return ordered


def _rstrip_lines(groups):
    return [[key, [ln.rstrip(" \t") for ln in lines]] for key, lines in groups]


def _normalize_verified_by_quoting(groups):
    out = []
    for key, lines in groups:
        if key == "verified-by" and len(lines) == 1:
            m = FRONT_KEY.match(lines[0])
            value = m.group(2).strip()
            qm = _SINGLE_QUOTED.match(value)
            if qm:
                inner = qm.group(1).replace("\\'", "'").replace('"', '\\"')
                lines = ["verified-by: \"%s\"" % inner]
        out.append([key, lines])
    return out


def normalize_frontmatter(text, temporal_fields):
    """(new_text, changed). Body (everything from the closing `---` fence
    onward) is never touched."""
    block, end = frontmatter_span(text)
    if block is None:
        return text, False
    groups = _group_lines(block)
    groups = _rstrip_lines(groups)
    groups = _normalize_verified_by_quoting(groups)
    groups = _reorder(groups, temporal_fields)
    new_block = "\n".join(ln for _key, lines in groups for ln in lines)
    if new_block == block:
        return text, False
    return text[:3] + new_block + text[end:], True


def cmd_fix(vault, apply_changes):
    notes = _load_notes(vault)
    if notes is None:
        print("NO-DATA: no markdown files found under %s" % vault)
        return 2
    temporal_mod = _load_sibling("bm_vault_temporal")
    temporal_fields = (tuple(temporal_mod.FIELDS) if temporal_mod is not None
                        else _FALLBACK_TEMPORAL_FIELDS)
    changed = 0
    for rel, text in notes:
        new_text, did_change = normalize_frontmatter(text, temporal_fields)
        if not did_change:
            continue
        changed += 1
        print("%s %s" % ("normalized" if apply_changes else "would normalize", rel))
        if apply_changes:
            path = os.path.join(vault, rel)
            tmp = path + ".bm-vault-lint.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(new_text)
            os.replace(tmp, path)
    print("%s %d note(s)" % ("normalized" if apply_changes else "would normalize", changed))
    if not apply_changes:
        print("dry run: nothing was written. Re-run with --apply to write.")
    return 0


def _build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    pc = sub.add_parser("check", help="exit non-zero on any contract violation")
    pc.add_argument("--vault", default=None)
    pc.add_argument("--json", action="store_true")
    pf = sub.add_parser("fix", help="normalize whitelisted mechanical shapes")
    pf.add_argument("--vault", default=None)
    pf.add_argument("--apply", action="store_true", help="actually write; dry run otherwise")
    return p


def main(argv=None):
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    vault = _vault_root(args.vault)
    if args.cmd == "fix":
        return cmd_fix(vault, args.apply)
    return cmd_check(vault, getattr(args, "json", False))


if __name__ == "__main__":
    sys.exit(main())
