#!/usr/bin/env python3
"""bm_vault_attribute_provenance: per-attribute provenance with verification status.
WBS VB13-04.

NAMED attribute_provenance, DELIBERATELY NOT bm_vault_provenance.py. That name is
already taken by the D07 claim-level checker (a note-BODY `claim: ... [evidence:
...]` scanner, found by reading it before writing this: it resolves locators, it
records nothing, and it has nothing to do with a value's source or verification
state). Two tools whose names differ by nothing is exactly the trap
tools/bm_vault_promotions.py's own docstring already named ("two tools whose names
differ by a suffix is how the estate once ended up with two writer locks in two
formats"); this module gets its own unambiguous name rather than colliding with a
shipped one.

WHY A NEW STORE, NOT A FRONTMATTER FIELD. tools/bm_vault_intake.py already writes
`provenance_source` / `provenance_actor` / `provenance_ingested_at` into a note's
own frontmatter, but that is ONE record for the WHOLE note at admission time. This
row asks for provenance PER ATTRIBUTE: a note has many fields, each one can be set
by a different source, at a different time, with a different confidence, and can
be independently verified. Splicing a growing, versioned record per field into one
note's frontmatter would mean reserialising that block on every write and racing
every other tool that also splices it (bm_vault_promotions.py, bm_vault_ids.py). So
this module keeps its own JSON store OUTSIDE the vault, the SAME shape
tools/bm_vault_curate.py already established for exactly this reason (its own
docstring: "this tool does not hold the vault fence"): default
~/.claude/bm_vault_attribute_provenance.json, one JSON object, overridable with
--store. The vault itself is never written by this module.

REUSE, NAMED. Note resolution reuses tools/bm_vault_promotions.py's own `_resolve`
(id first, then a vault-relative path), the same helper tools/bm_vault_enrich.py
already reuses rather than re-implementing id lookup a third time. Date/time
stamping reuses tools/bm_vault_intake.py's `_today`/`_now_iso`. The human-promotion
gate reuses bm_vault_promotions.py's own --by CONTRACT (a state change naming a
human is refused without --by, its own verbatim rule: "a promotion that is not
recorded did not happen") rather than re-deciding when a promoter must be named;
the two modules promote DIFFERENT things (a whole note's lifecycle state there, one
attribute's verification status here), so calling into that module directly would
promote the wrong object, and the rule it enforces is copied here as the identical
CONTRACT it already is, never re-derived from scratch.

THE RECORD. One JSON object per (note, attribute) WRITE, versioned rather than
overwritten (see VERSIONING below):
  note                  vault-relative path, resolved through bm_vault_promotions
  attribute             the field name this record is about, e.g. "owner"
  source                who or what set the value: a human name, or the machine
                        actor that drafted it (see CALLING CONTRACT below)
  set_at                the date the value was set (ISO date; --at, default today)
  confidence            optional numeric, None when not given
  verification_status   one of "unverified", "machine-checked", "human-verified"
  checked_by            the checking mechanism; REQUIRED when status is
                        machine-checked (a verification claim naming no mechanism
                        verifies nothing)
  promoted_by           the human who promoted; REQUIRED when status is
                        human-verified (the --by contract above)
  version               1-based, per (note, attribute); see VERSIONING
  recorded_at           UTC timestamp this record was appended to the store

VERSIONING. A second `set` on the same (note, attribute) never overwrites the
first: it appends a new record with version = previous max + 1. Every version
stays in the store (nothing is ever deleted here), and `get` reads the highest
version, so "latest wins on read" is a property of the reader, never a property
of the store destroying history to get there. `history` returns every version.
# ponytail: no cross-process lock on the read-modify-write cycle (the same
# ceiling bm_vault_curate.py's own JSON store already carries); two concurrent
# `set` calls on the same pair could compute the same next version and one
# write would then hide behind the other's atomic replace. Add a lock if two
# writers are ever expected to race on the same (note, attribute) at once.

CALLING CONTRACT FOR PRODUCERS (not wired in this change; this row asks this
module to EXPOSE the record and the query, and names the census extension as the
consumer "later"). The intake path (tools/bm_vault_intake.py) would call `set`
with source naming the human or system that supplied the value and status
"unverified". The enrichment lane (tools/bm_vault_enrich.py, PR 135) would call
`set` with source "machine:<drafting_model>" (the SAME drafting_model string its
own note frontmatter already carries, per its docstring's drafter_kind /
drafting_model fields) and status "unverified" until something checks it: this
hooks into the SAME identifying string enrichment already emits, not a second,
divergent copy of it.

THE QUERY THE CENSUS CAN USE LATER. `by_status(records, status, min_age_days,
as_of)` returns every record in `status` whose `set_at` is at least
`min_age_days` old as of `as_of` (default today). This is read-only and reads
this module's OWN store; tools/bm_vault_census_ext.py is left completely
untouched by this change, exactly as directed.

Exit 0 clean. Exit 1 a refusal (bad status, a machine-checked record with no
--checked-by, a human-verified record with no --by): named, nothing written.
Exit 2 NO-DATA: unreadable vault, IDENT resolves to no note, an unreadable
store file, or a query that matches nothing. Python 3.9, standard library
only, no network.
"""
import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_promotions as promo   # noqa: E402
import bm_vault_intake as intake      # noqa: E402

DEFAULT_VAULT = os.environ.get("BROTHERMODE_VAULT") or os.path.expanduser("~/Documents/Kay Vault")
DEFAULT_STORE = os.path.expanduser("~/.claude/bm_vault_attribute_provenance.json")

STATUSES = ("unverified", "machine-checked", "human-verified")


def _vault_root(cli_vault):
    if cli_vault:
        return cli_vault
    env = os.environ.get("BM_VAULT_ROOT")
    if env:
        return env
    return DEFAULT_VAULT


def _now_iso():
    return intake._now_iso()


def _today():
    return intake._today()


# ---------------------------------------------------------------- the store

def load_store(path):
    """The store dict, or a fresh empty one when no file exists yet. Raises
    RuntimeError (never a silent empty store) when the file exists but is not
    readable JSON: a corrupt store must not read as "nothing has ever been
    recorded"."""
    if not os.path.isfile(path):
        return {"generated": None, "vault": None, "records": []}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        raise RuntimeError("cannot read provenance store %s: %s" % (path, e))
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        raise RuntimeError("provenance store %s is not the expected shape "
                            "(a dict with a 'records' list)" % path)
    return data


def save_store(path, data):
    """Atomic write: a temp file in the same directory, then os.replace over
    the target, the same technique bm_vault_promotions.py and
    bm_vault_curate.py already use so a reader never sees a half-written
    store."""
    dirname = os.path.dirname(path) or "."
    os.makedirs(dirname, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def _pair_records(records, note, attribute):
    return [r for r in records if r.get("note") == note and r.get("attribute") == attribute]


def latest_record(records, note, attribute):
    """The highest-version record for (note, attribute), or None."""
    pair = _pair_records(records, note, attribute)
    if not pair:
        return None
    return max(pair, key=lambda r: r.get("version", 0))


def history(records, note, attribute):
    """Every version for (note, attribute), oldest first."""
    return sorted(_pair_records(records, note, attribute), key=lambda r: r.get("version", 0))


def by_status(records, status, min_age_days=0, as_of=None):
    """Records in `status` whose set_at is at least min_age_days old as of
    `as_of` (default today). A record whose set_at cannot be parsed is
    skipped, never counted as either old or new by a guess."""
    ref = as_of or datetime.date.today()
    out = []
    for r in records:
        if r.get("verification_status") != status:
            continue
        raw = r.get("set_at", "")
        try:
            set_date = datetime.date.fromisoformat(raw)
        except ValueError:  # sbe: allow-silent unparseable set_at skipped per docstring, never counted as either old or new by a guess
            continue
        if (ref - set_date).days >= min_age_days:
            out.append(r)
    return sorted(out, key=lambda r: r["set_at"])


# ---------------------------------------------------------- writing a record

def set_record(vault, store_path, note_ident, attribute, source, status,
               confidence=None, checked_by=None, by=None, at=None):
    """(ok, message, record_or_None). Appends exactly one versioned record
    when ok is True; writes nothing at all when ok is False."""
    if status not in STATUSES:
        return False, ("REFUSED: --status must be one of %s, got %r"
                        % ("/".join(STATUSES), status)), None
    if not attribute or not attribute.strip():
        return False, "REFUSED: --attribute is empty", None
    if not source or not source.strip():
        return False, ("REFUSED: no source given; a provenance record with no "
                        "source attributes to nothing"), None
    if status == "machine-checked" and not (checked_by and checked_by.strip()):
        return False, ("REFUSED: machine-checked needs --checked-by naming the "
                        "checking mechanism; an unnamed check verifies nothing"), None
    if status == "human-verified" and not (by and by.strip()):
        return False, ("REFUSED: human-verified needs --by; a promotion that is "
                        "not recorded did not happen"), None
    if not vault or not os.path.isdir(vault):
        return False, "NO-DATA: no readable vault at %r" % vault, None

    target = promo._resolve(vault, note_ident)
    if target is None:
        return False, "NO-DATA: %r resolves to no note" % note_ident, None

    try:
        data = load_store(store_path)
    except RuntimeError as e:
        return False, "NO-DATA: %s" % e, None

    existing = _pair_records(data["records"], target, attribute)
    version = (max((r.get("version", 0) for r in existing), default=0)) + 1
    record = {
        "note": target,
        "attribute": attribute,
        "source": source,
        "set_at": at or _today(),
        "confidence": confidence,
        "verification_status": status,
        "checked_by": checked_by,
        "promoted_by": by,
        "version": version,
        "recorded_at": _now_iso(),
    }
    data["records"].append(record)
    data["generated"] = _now_iso()
    data["vault"] = vault
    save_store(store_path, data)
    return True, ("RECORDED note=%s attribute=%s version=%d status=%s source=%s"
                  % (target, attribute, version, status, source)), record


# -------------------------------------------------------------------- CLI

def cmd_set(args):
    ok, message, _record = set_record(
        args.vault, args.store, args.note, args.attribute, args.source, args.status,
        confidence=args.confidence, checked_by=args.checked_by, by=args.by, at=args.at)
    print(message)
    if ok:
        return 0
    return 2 if message.startswith("NO-DATA") else 1


def _resolve_note_or_none(vault, ident):
    if not vault or not os.path.isdir(vault):
        print("bm_vault_attribute_provenance: NO-DATA, no readable vault at %r" % vault)
        return None
    target = promo._resolve(vault, ident)
    if target is None:
        print("bm_vault_attribute_provenance: NO-DATA, %r resolves to no note" % ident)
        return None
    return target


def cmd_get(args):
    target = _resolve_note_or_none(args.vault, args.note)
    if target is None:
        return 2
    try:
        data = load_store(args.store)
    except RuntimeError as e:
        print("NO-DATA: %s" % e)
        return 2
    record = latest_record(data["records"], target, args.attribute)
    if record is None:
        print("NO-DATA: no provenance recorded for %s / %s" % (target, args.attribute))
        return 2
    if args.json:
        print(json.dumps(record, sort_keys=True))
    else:
        for k in sorted(record):
            print("  %s: %s" % (k, record[k]))
    return 0


def cmd_history(args):
    target = _resolve_note_or_none(args.vault, args.note)
    if target is None:
        return 2
    try:
        data = load_store(args.store)
    except RuntimeError as e:
        print("NO-DATA: %s" % e)
        return 2
    rows = history(data["records"], target, args.attribute)
    if not rows:
        print("NO-DATA: no provenance recorded for %s / %s" % (target, args.attribute))
        return 2
    print("provenance history for %s / %s (%d version(s)):" % (target, args.attribute, len(rows)))
    for r in rows:
        print("  v%d [%s] source=%s set_at=%s checked_by=%s promoted_by=%s"
              % (r["version"], r["verification_status"], r["source"], r["set_at"],
                 r.get("checked_by"), r.get("promoted_by")))
    return 0


def cmd_by_status(args):
    try:
        data = load_store(args.store)
    except RuntimeError as e:
        print("NO-DATA: %s" % e)
        return 2
    if not data["records"]:
        print("NO-DATA: provenance store %s is empty" % args.store)
        return 2
    as_of = datetime.date.fromisoformat(args.as_of) if args.as_of else None
    rows = by_status(data["records"], args.status, min_age_days=args.min_age_days, as_of=as_of)
    if not rows:
        print("NO-DATA: no %s record(s) at least %d day(s) old"
              % (args.status, args.min_age_days))
        return 2
    if args.json:
        print(json.dumps(rows, sort_keys=True))
        return 0
    print("%s record(s) at least %d day(s) old: %d" % (args.status, args.min_age_days, len(rows)))
    for r in rows:
        print("  %s / %s  v%d  set_at=%s  source=%s"
              % (r["note"], r["attribute"], r["version"], r["set_at"], r["source"]))
    return 0


def _build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("set", help="record one provenance version for (note, attribute)")
    ps.add_argument("--vault", default=None)
    ps.add_argument("--store", default=DEFAULT_STORE)
    ps.add_argument("--note", required=True, help="note id or vault-relative path")
    ps.add_argument("--attribute", required=True)
    ps.add_argument("--source", required=True, help="who or what set the value")
    ps.add_argument("--status", required=True, choices=STATUSES)
    ps.add_argument("--confidence", type=float, default=None)
    ps.add_argument("--checked-by", dest="checked_by", default=None,
                     help="the checking mechanism; required for machine-checked")
    ps.add_argument("--by", default=None, help="the promoter; required for human-verified")
    ps.add_argument("--at", default=None, help="ISO date the value was set; default today")

    pg = sub.add_parser("get", help="the latest provenance record for (note, attribute)")
    pg.add_argument("--vault", default=None)
    pg.add_argument("--store", default=DEFAULT_STORE)
    pg.add_argument("--note", required=True)
    pg.add_argument("--attribute", required=True)
    pg.add_argument("--json", action="store_true")

    ph = sub.add_parser("history", help="every provenance version for (note, attribute)")
    ph.add_argument("--vault", default=None)
    ph.add_argument("--store", default=DEFAULT_STORE)
    ph.add_argument("--note", required=True)
    ph.add_argument("--attribute", required=True)

    pb = sub.add_parser("by-status", help="records in one status, at least N days old")
    pb.add_argument("--store", default=DEFAULT_STORE)
    pb.add_argument("--status", required=True, choices=STATUSES)
    pb.add_argument("--min-age-days", dest="min_age_days", type=int, default=0)
    pb.add_argument("--as-of", dest="as_of", default=None, help="ISO date; default today")
    pb.add_argument("--json", action="store_true")

    return p


def main(argv=None):
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.cmd in ("set", "get", "history"):
        args.vault = _vault_root(args.vault)
    return {"set": cmd_set, "get": cmd_get, "history": cmd_history,
            "by-status": cmd_by_status}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
