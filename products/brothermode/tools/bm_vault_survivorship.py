#!/usr/bin/env python3
"""bm_vault_survivorship: which claim wins when two notes truly collide.

WHY THIS EXISTS. bm_vault_triage.py (VB6-05) tells CONTRADICTION apart from
SCOPED: a same-scope collision versus two claims about different things.
Once triage says two claims are a real CONTRADICTION, nothing in the estate
decided which one to trust. This module is that decision, report-only: it
never edits a note, never mints a contradicts edge, never touches the vault.

THE TABLE MAPS ONTO bm_vault_authority.LEVELS, never a second vocabulary.
Plain names for the same three ranks: founder_ruling is source_of_record,
validated note is derived, transcript-derived is casual. The default order,
most to least trusted, is exactly bm_vault_authority.LEVELS read top down
(that module's own ascending rank, reversed): source_of_record, derived,
casual. PER_ATTRIBUTE_ORDER lets one attribute name its own order, but every
entry in that order must still be one of bm_vault_authority.LEVELS; naming
anything else is a finding (ValueError), never a silently invented rank,
same posture bm_vault_authority.read_authority takes on an unknown value.

AN OVERRIDE RECORD outranks the table entirely. An operator can record,
dated and attributed (--by is required, same as bm_vault_promotions.py's own
promote command refusing an unattributed write), which note wins for one
attribute (optionally narrowed to one claim subject). Overrides are appended
to a JSON-lines file OUTSIDE the vault (default next to the answer ledger,
~/.claude/bm_vault_survivorship_overrides.jsonl), never inside a note and
never deleted: a later override for the same attribute and subject becomes
the ACTIVE one by timestamp, but every prior record stays on disk, exactly
bm_vault_ledger.py's own report-only-on-erasure posture for the same reason,
an override that could vanish is not a record. Dry run unless --apply,
bm_vault_promotions.py's own pattern for any command that writes.

  resolve-conflict --vault V [--attribute NAME] [--store PATH]
      runs bm_vault_triage.scan(vault), takes every CONTRADICTION it found
      (never a SCOPED pair, those are not a real collision), and reports the
      winner per the table or a matching override, and why, in rank names.
      Exit 0 clean (no contradictions), 1 at least one was resolved (or left
      UNRESOLVED at a tie), 2 NO-DATA (bad vault, missing sibling module).

  override --attribute NAME --winner PATH --by WHO [--subject TEXT] [--apply]
      records a dated override. Dry run prints what would be written and
      returns 0 without touching disk; --apply appends one JSON line.

Python 3.9, standard library only. No vault writes, ever.
"""
import argparse
import datetime
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# C3: the config directory is resolved by brother_paths, the one seam
# that knows which coding client is running (docs/codex/HOOKS-MAPPING.md).
# Loaded from beside this file because tools/ is not a package.
sys.path.insert(0, HERE)
import brother_paths  # noqa: E402
OVERRIDE_PATH = os.path.join(brother_paths.config_dir(),
                              "bm_vault_survivorship_overrides.jsonl")

# Per-attribute exceptions to the default order. Empty on purpose: every
# attribute uses default_order() until an operator names one here. Any order
# named must be a permutation of (a subset of) bm_vault_authority.LEVELS,
# checked in order_for, never a second vocabulary.
PER_ATTRIBUTE_ORDER = {}


def _load_sibling(name, tools_dir=None):
    """tools/<name>.py loaded BY PATH from tools_dir (default: this file's
    own directory), the same pattern bm_vault_lint.py's _load_sibling and
    bm_vault.py's dynamic imports use. Loading by path, not by package name,
    is what lets a copy of tools/ in a different directory be read as a
    self-contained unit: point tools_dir at the copy and every sibling
    import inside this call resolves inside that same copy.

    Returns the module, or None when the file is absent or fails to import:
    a missing contract module is a NO-DATA finding for the caller, never a
    crash and never a silently skipped rule."""
    path = os.path.join(tools_dir or HERE, name + ".py")
    if not os.path.isfile(path):
        return None
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # sbe: allow-silent optional module load failure, caller turns None into a NO-DATA finding per docstring
        return None


def default_order(auth_mod):
    """Most to least trusted, read from auth_mod.LEVELS (ascending),
    reversed. This IS founder_ruling > validated note > transcript-derived,
    spelled in bm_vault_authority's own three names."""
    return tuple(reversed(auth_mod.LEVELS))


def order_for(attribute, auth_mod):
    """The trusted-source order for one attribute: PER_ATTRIBUTE_ORDER's
    entry if it named one, default_order(auth_mod) otherwise. Raises
    ValueError naming the bad value(s) when a per-attribute row names
    anything outside auth_mod.LEVELS: an unknown rank is a finding, never a
    silent guess, matching bm_vault_authority.read_authority's own refusal
    to rank an unknown value."""
    order = PER_ATTRIBUTE_ORDER.get(attribute)
    if order is None:
        return default_order(auth_mod)
    levels = set(auth_mod.LEVELS)
    bad = [v for v in order if v not in levels]
    if bad:
        raise ValueError(
            "attribute %r names unranked authority(ies) %r, not in %s"
            % (attribute, bad, "/".join(auth_mod.LEVELS)))
    return tuple(order)


def load_overrides(store=OVERRIDE_PATH):
    """[override record], oldest first, or [] when the store does not exist
    yet. Every record ever appended comes back: nothing is filtered here,
    active_override picks the winner among them."""
    if not os.path.exists(store):
        return []
    records = []
    with open(store, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def active_override(attribute, subject, records):
    """The most recent (by ts) recorded override matching this attribute,
    and either no subject was named on the record (it covers every subject)
    or it matches this one exactly. None when nothing matches. Every prior
    record for the same key is still in `records`, untouched; this is only
    which one governs right now."""
    matches = [(i, r) for i, r in enumerate(records) if r.get("attribute") == attribute
               and (r.get("subject") is None or r.get("subject") == subject)]
    if not matches:
        return None
    # Break a same-second ts tie by append order (records is oldest first),
    # so two overrides written inside one wall-clock second still resolve to
    # the LAST one recorded, never an arbitrary tie winner.
    return max(matches, key=lambda pair: (pair[1].get("ts", ""), pair[0]))[1]


def note_authority(path_abs, auth_mod):
    try:
        with open(path_abs, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        return None, "unreadable (%s)" % exc
    return auth_mod.read_authority(text)


def resolve(vault, a, b, attribute, auth_mod, overrides):
    """(winner, loser, reason). winner/loser are triage claim records (or
    both None on a tie the table cannot break). a and b must already be a
    same-scope CONTRADICTION, per triage's own classify: this function never
    checks that itself, the caller (cmd_resolve_conflict) only ever passes
    pairs triage already classified that way."""
    override = active_override(attribute, a.get("subject"), overrides)
    if override is not None:
        winner_path = override.get("winner")
        winner, loser = (a, b) if a["path"] == winner_path else (b, a)
        return winner, loser, "override recorded by %s on %s outranks the table" % (
            override.get("by"), override.get("ts"))

    order = order_for(attribute, auth_mod)
    level_a, problem_a = note_authority(os.path.join(vault, a["path"]), auth_mod)
    level_b, problem_b = note_authority(os.path.join(vault, b["path"]), auth_mod)
    if problem_a:
        return b, a, "%s: %s, ranks unrankable; %s wins on authority" % (
            a["path"], problem_a, b["path"])
    if problem_b:
        return a, b, "%s: %s, ranks unrankable; %s wins on authority" % (
            b["path"], problem_b, a["path"])
    rank_a, rank_b = order.index(level_a), order.index(level_b)
    if rank_a < rank_b:
        return a, b, "%s outranks %s in %s" % (level_a, level_b, order)
    if rank_b < rank_a:
        return b, a, "%s outranks %s in %s" % (level_b, level_a, order)
    return None, None, "tie at %s, table cannot decide" % level_a


def cmd_resolve_conflict(vault, attribute, store):
    auth_mod = _load_sibling("bm_vault_authority")
    triage_mod = _load_sibling("bm_vault_triage")
    if auth_mod is None or triage_mod is None:
        print("bm_vault_survivorship: NO-DATA, a sibling contract module "
              "failed to load", file=sys.stderr)
        return 2
    pairs, scoped, contradictions, unreadable = triage_mod.scan(vault)
    overrides = load_overrides(store)
    print("vault: %s" % vault)
    print("attribute: %s" % attribute)
    if unreadable:
        print("unreadable files skipped: %d" % unreadable)
    print("scoped (not a real collision, skipped): %d" % len(scoped))
    print("contradictions to resolve: %d" % len(contradictions))
    if not contradictions:
        print("clean: no same-scope collisions to resolve")
        return 0
    for a, b in contradictions:
        winner, loser, reason = resolve(vault, a, b, attribute, auth_mod, overrides)
        if winner is None:
            print("UNRESOLVED %s <%s> vs %s <%s>: %s" % (
                a["path"], a["text"], b["path"], b["text"], reason))
        else:
            print("WINNER %s <%s> over %s <%s>: %s" % (
                winner["path"], winner["text"], loser["path"], loser["text"], reason))
    return 1


def cmd_override(attribute, winner, by, subject, apply_changes, store):
    if not by:
        print("bm_vault_survivorship: recording an override needs --by; a "
              "survivorship call with no accountable name is worse than none",
              file=sys.stderr)
        return 2
    record = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "attribute": attribute,
        "subject": subject,
        "winner": winner,
        "by": by,
    }
    line = json.dumps(record, sort_keys=True)
    print(("would record: %s" if not apply_changes else "recorded: %s") % line)
    if not apply_changes:
        print("dry run: nothing was written. Re-run with --apply to write.")
        return 0
    os.makedirs(os.path.dirname(store), exist_ok=True)
    fd = os.open(store, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, (line + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("resolve-conflict", "override"))
    ap.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    ap.add_argument("--attribute", default="value",
                     help="which table row to apply, defaults to the generic 'value' row")
    ap.add_argument("--winner", help="override: vault-relative path of the winning note")
    ap.add_argument("--subject", default=None,
                     help="override: restrict to one claim subject, omitted covers all")
    ap.add_argument("--by", help="override: who is recording it")
    ap.add_argument("--apply", action="store_true",
                     help="override: actually write, default is dry run")
    ap.add_argument("--store", default=OVERRIDE_PATH,
                     help="override records file, never the vault")
    args = ap.parse_args(argv)

    if args.command == "resolve-conflict":
        if not args.vault or not os.path.isdir(args.vault):
            print("bm_vault_survivorship: NO-DATA, no readable vault at %r" % args.vault,
                  file=sys.stderr)
            return 2
        return cmd_resolve_conflict(args.vault, args.attribute, args.store)

    if not args.winner:
        print("bm_vault_survivorship: recording an override needs --winner",
              file=sys.stderr)
        return 2
    return cmd_override(args.attribute, args.winner, args.by, args.subject,
                         args.apply, args.store)


if __name__ == "__main__":
    sys.exit(main())
