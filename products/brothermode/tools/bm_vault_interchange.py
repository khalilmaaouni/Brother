#!/usr/bin/env python3
"""bm_vault_interchange: the interchange contract, WBS row VB3-16.

WHY THIS EXISTS. The row's own sentence, conceded from a Codex refutation: a
star schema flattens bi-temporal intervals, many-to-many evidence and
contradiction graphs, and one imported Python module cannot be the contract
for Azure, Snowflake and Databricks consumers that never run this
interpreter. The fix is not a new database, it is a LANGUAGE-NEUTRAL
description of the shapes this vault already emits: JSON Schema (draft
2020-12) files under schemas/interchange/, each field carrying a stable
x-field-id (see schemas/interchange/field_registry.json, the ledger this
module and its suite both read as the single source of truth), so a
consumer in any language can validate a row without ever importing this
tool. Iceberg-compatible table shapes are the first target; the star
projection this module can also emit stays a LOSSY PROJECTION, labeled as
one in its own output, never presented as the source of truth.

THE FIVE SCHEMAS, schemas/interchange/*.schema.json, each mirroring an
existing sibling module's REAL record shape (never re-decided here):
  assertion.schema.json                tools/bm_vault_assertions.py's
                                        99-System/assertions.jsonl record
  event.schema.json                    tools/bm_vault_events.py's payload-
                                        free event record, all five kinds
  export_assertions_row.schema.json    tools/bm_vault_export.py's
                                        assertions.jsonl bundle row (VB8-04)
  export_events_live_row.schema.json   tools/bm_vault_export.py's
                                        events.jsonl bundle row, status=live
  export_events_tombstoned_row.schema.json  the sibling row, status=tombstoned
The two export_events_*_row schemas are separate files rather than one
schema with optional fields because bm_vault_export.build_events() actually
emits two DIFFERENT field sets discriminated by status (see fold()'s own
"live" vs "tombstoned" shapes) -- one schema could only describe that with
if/then/else, which this module's validator does not implement (see below).

THE VALIDATOR IS A SELF-WRITTEN, HONEST SUBSET of JSON Schema draft 2020-12,
stdlib only, no dependency added: it understands "type" (string/integer/
boolean/number/object), "required", "enum", "pattern", "maxLength" and
"additionalProperties": false. It does NOT implement $ref, if/then/else,
oneOf/anyOf/allOf, "format", numeric bounds, or array/nested-object schemas
-- none of the five schemas above need them, and reaching for a dependency
to cover cases this contract does not use would be exactly the overclaim
CLAUDE.md's research discipline forbids. Every schema file's own docstring-
equivalent "description" field states which cross-field rules (e.g.
bm_vault_events.py's corrects-iff-kind=correct) are therefore left to the
module that owns the record, not this schema: a record can pass this
schema and still be refused by its owning module, which is a NARROWER
schema than the code it describes, never a wider one.

ICEBERG-COMPATIBLE SHAPE, stated once here rather than duplicated in every
schema file: this is a SHAPE contract, not a metadata writer -- no Iceberg
catalog, snapshot or manifest is produced by this module. Every column in
the two export-row schemas maps to an Iceberg primitive as follows:
  string columns (ids, hashes, free text, opaque locators)   -> Iceberg string
  the five temporal columns (valid_from/valid_to/observed_at/
    ingested_at/verified_at), ISO date or "" today            -> Iceberg date
    (a real writer normalizes "" to a SQL NULL; this contract keeps them
    string so an empty vs missing distinction survives until that writer
    decides how to represent "unmigrated", which is a data decision, not
    a shape one)
  occurred_at/recorded_at (ISO date or full ISO 8601 datetime) -> Iceberg
    timestamptz when the datetime form is used, timestamp otherwise; this
    contract does not force one representation, matching what
    bm_vault_events.py itself accepts today (see event.schema.json)
  authority/lifecycle/sensitivity/kind/status                 -> Iceberg
    string with an enum constraint enforced at the writer, not the file
    format (Iceberg has no native enum type)
Governance columns (tenant, authority, lifecycle, sensitivity,
evidence_locator) are REQUIRED in both export-row schemas, matching
bm_vault_export.py's own "always present" contract for that table.

MIGRATION SEMANTICS (`evolve --from V1 --to V2`), evolve_check() below:
  ADDITIVE, forward-compatible: a field in V2 absent from V1 is accepted
    when it is not in V2's "required" list, or (if required) carries a
    "default" -- a new REQUIRED field with no default would fail every V1
    record on migration, so that case is refused, not silently accepted.
  REMOVAL, refused: a field present in V1 and absent from V2 is named as
    REMOVED and refused outright.
  TYPE CHANGE, refused: a field present in both with a different "type" (or
    an enum that drops a value the source relied on) is named and refused.
  RENAME: expressed only as an ADDITIVE new field-id/name pair alongside
    the OLD name kept present (optionally marked "x-deprecated": true) --
    there is no dedicated "rename" operation, because a bare rename (old
    name gone, new name present) is indistinguishable from a REMOVAL plus
    an unrelated ADDITION and is refused as a REMOVAL, which is the honest
    reading: nothing here can prove intent, only shape.
  ID REUSE, refused: evolve_check also refuses when a field's x-field-id
    changes across the same name, or when an id that named one field in V1
    is reassigned to a DIFFERENT field name in V2 -- this is the "field ids
    never reused" rule enforced across a live evolution, not just within
    one schema snapshot.
Running evolve TWICE, once forward (--from V1 --to V2) and once backward
(--from V2 --to V1), is how this module answers "does it migrate back, or
is it refused honestly": an additive V1->V2 change is typically NOT safe
backward under a schema with "additionalProperties": false (V1 has no slot
for the new field), and the backward call reports that refusal by name
(the field is REMOVED from V1's perspective) rather than silently passing.

THE STAR PROJECTION (`star --assertions FILE`), build_star() below: for
every (subject, predicate) pair with more than one row, keeps exactly the
one row bm_vault_authority.rank_key() ranks highest (ties broken by the
lowest assertion id, for determinism) and drops the rest. This is LOSSY on
three axes, named in the command's own first output line, never silently:
  bi-temporal intervals    every row keeps only the WINNER's own
                            valid_from/valid_to; a competing row's distinct
                            window is gone, not merged, not unioned
  many-to-many evidence     N assertions for one (subject, predicate)
                            collapse to 1 star row; the other N-1 rows'
                            evidence_locator/source rows are dropped
  contradiction graphs     when the dropped rows disagree with the winner
                            on value, that disagreement (the CONFLICT
                            bm_vault_assertions.truth exposes explicitly) is
                            gone from the star entirely -- there is no
                            column here for "N competitors existed"

Exit codes: validate 0 PASS, 1 REFUSE (first violation named), 2 NO-DATA
(missing file/schema, unknown --kind, unreadable JSON). evolve 0 FORWARD-
COMPATIBLE, 1 REFUSED (reasons named), 2 NO-DATA (missing/unreadable schema
file). star 0 always (an empty input is a clean empty projection), 2
NO-DATA (missing assertions file). check-registry 0 clean, 1 findings
(duplicate or missing x-field-id), 2 NO-DATA (missing registry file).

Python 3.9 floor, standard library only. No em or en dashes anywhere in
this file, its comments, or its output.
"""
import argparse
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
SCHEMAS_DIR = os.path.join(REPO_ROOT, "schemas", "interchange")
FIELD_REGISTRY_PATH = os.path.join(SCHEMAS_DIR, "field_registry.json")

sys.path.insert(0, HERE)
import bm_vault_authority as authority   # noqa: E402

KIND_SCHEMA_FILES = {
    "assertion": "assertion.schema.json",
    "event": "event.schema.json",
    "export_assertion": "export_assertions_row.schema.json",
}
# export_event dispatches to one of these two by the row's own "status"
# field, see resolve_schema_for_kind() below -- the two export event row
# shapes are genuinely different field sets, not one schema with optional
# fields (see the module docstring).
EXPORT_EVENT_SCHEMA_FILES = {
    "live": "export_events_live_row.schema.json",
    "tombstoned": "export_events_tombstoned_row.schema.json",
}
KNOWN_KINDS = tuple(sorted(KIND_SCHEMA_FILES) + ["export_event"])


class InterchangeError(Exception):
    """Raised for a NO-DATA condition a CLI command turns into exit 2."""


def load_schema(filename):
    """The parsed schema dict for one schemas/interchange/<filename>.
    Raises InterchangeError (never a bare exception) when the file is
    missing or not valid JSON, so every call site gets one NO-DATA shape."""
    path = os.path.join(SCHEMAS_DIR, filename)
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except OSError as exc:
        raise InterchangeError("no readable schema at %r (%s)" % (path, exc))
    except ValueError as exc:
        raise InterchangeError("schema %r is not valid JSON (%s)" % (path, exc))


def load_field_registry():
    """The field_registry.json ledger, {"next_id": int, "fields": [...]}.
    Raises InterchangeError when the file is missing or malformed."""
    try:
        with open(FIELD_REGISTRY_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except OSError as exc:
        raise InterchangeError("no readable field registry at %r (%s)"
                               % (FIELD_REGISTRY_PATH, exc))
    except ValueError as exc:
        raise InterchangeError("field registry %r is not valid JSON (%s)"
                               % (FIELD_REGISTRY_PATH, exc))


def iter_schema_field_ids():
    """{(schema_basename, field_name): field_id} across every
    schemas/interchange/*.schema.json file on disk -- the ACTUAL x-field-id
    values shipped, as opposed to field_registry.json's own declared
    ledger. The registry test (tools/test_bm_vault_interchange.py) compares
    the two: every id here must exist in the registry under the same name,
    and no bare integer may repeat under two DIFFERENT names anywhere in
    this set (that would be a reused id)."""
    found = {}
    for path in sorted(glob.glob(os.path.join(SCHEMAS_DIR, "*.schema.json"))):
        base = os.path.basename(path)
        with open(path, encoding="utf-8") as fh:
            schema = json.load(fh)
        for name, prop in (schema.get("properties") or {}).items():
            if "x-field-id" in prop:
                found[(base, name)] = prop["x-field-id"]
    return found


# ------------------------------------------------------------ validator ----

_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
}


def _validate_value(name, value, prop_schema):
    """[problem, ...] for one field's value against its property schema.
    Honest subset: type, enum, pattern, maxLength -- see the module
    docstring's VALIDATOR section for exactly what is and is not covered."""
    problems = []
    expected_type = prop_schema.get("type")
    check = _TYPE_CHECKS.get(expected_type)
    if check is not None and not check(value):
        problems.append("field %r must be of type %r, got %s"
                        % (name, expected_type, type(value).__name__))
        return problems
    if isinstance(value, str):
        max_len = prop_schema.get("maxLength")
        if max_len is not None and len(value) > max_len:
            problems.append("field %r exceeds maxLength %d (got %d characters)"
                            % (name, max_len, len(value)))
        pattern = prop_schema.get("pattern")
        if pattern is not None and re.match(pattern, value) is None:
            problems.append("field %r value %r does not match pattern %r"
                            % (name, value, pattern))
    if "enum" in prop_schema and value not in prop_schema["enum"]:
        problems.append("field %r value %r is not in enum %s"
                        % (name, value, prop_schema["enum"]))
    return problems


def validate_record(record, schema):
    """[problem, ...] (empty means valid) for one JSON record against one
    schema dict. Only "type": "object" schemas are supported at the top
    level -- every schema this contract ships is exactly that shape."""
    if schema.get("type") != "object":
        return ["schema does not declare type: object at the top level"]
    if not isinstance(record, dict):
        return ["record is not a JSON object"]
    properties = schema.get("properties") or {}
    problems = []
    for required_field in schema.get("required", []):
        if required_field not in record:
            problems.append("missing required field %r" % required_field)
    if schema.get("additionalProperties") is False:
        extra = sorted(set(record) - set(properties))
        if extra:
            problems.append("unknown field(s) %s" % extra)
    for name, value in record.items():
        prop_schema = properties.get(name)
        if prop_schema is not None:
            problems.extend(_validate_value(name, value, prop_schema))
    return problems


def resolve_schema_for_kind(kind, record):
    """(schema_dict_or_None, schema_name_or_error_message). export_event
    dispatches on the record's own "status" field, since a live row and a
    tombstoned row are genuinely different shapes (see the module
    docstring); every other kind maps to exactly one schema file."""
    if kind == "export_event":
        status = record.get("status") if isinstance(record, dict) else None
        filename = EXPORT_EVENT_SCHEMA_FILES.get(status)
        if filename is None:
            return None, ("export_event row has status %r, expected one of %s"
                          % (status, sorted(EXPORT_EVENT_SCHEMA_FILES)))
        return load_schema(filename), filename
    filename = KIND_SCHEMA_FILES.get(kind)
    if filename is None:
        return None, "unknown kind %r" % kind
    return load_schema(filename), filename


# -------------------------------------------------------------- evolve -----

def evolve_check(v1, v2):
    """(ok, message). See the module docstring's MIGRATION SEMANTICS
    section for the full rule set this implements: additive-with-default is
    forward-compatible, removal and type change are refused by name, and a
    field id must never move to a different field name across the two
    schema snapshots. v1/v2 are parsed schema dicts (top-level "properties"
    and "required" keys), not file paths -- the CLI wrapper reads the files."""
    v1_props = v1.get("properties") or {}
    v2_props = v2.get("properties") or {}
    v2_required = set(v2.get("required") or [])
    problems = []

    v1_ids = {name: p.get("x-field-id") for name, p in v1_props.items()}
    v2_ids = {name: p.get("x-field-id") for name, p in v2_props.items()}

    for name, fid in v1_ids.items():
        if name in v2_ids and v2_ids[name] != fid:
            problems.append("field %r changed x-field-id from %s to %s"
                            % (name, fid, v2_ids[name]))

    id_to_name_v2 = {}
    for name, fid in v2_ids.items():
        if fid in id_to_name_v2 and id_to_name_v2[fid] != name:
            problems.append("x-field-id %s names both %r and %r in the target schema"
                            % (fid, id_to_name_v2[fid], name))
        else:
            id_to_name_v2[fid] = name
    for name, fid in v1_ids.items():
        target_name = id_to_name_v2.get(fid)
        if target_name is not None and target_name != name:
            problems.append("x-field-id %s (field %r in the source schema) is "
                            "reassigned to %r in the target schema, never a "
                            "reuse" % (fid, name, target_name))

    for name, v1_field in v1_props.items():
        if name not in v2_props:
            problems.append("REMOVED field %r (present in the source schema, "
                            "absent from the target)" % name)
            continue
        v2_field = v2_props[name]
        if v1_field.get("type") != v2_field.get("type"):
            problems.append("TYPE CHANGE on field %r (%r -> %r)"
                            % (name, v1_field.get("type"), v2_field.get("type")))
            continue
        v1_enum = v1_field.get("enum")
        if v1_enum is not None:
            dropped = sorted(set(v1_enum) - set(v2_field.get("enum") or []))
            if dropped:
                problems.append("enum on field %r drops value(s) %s"
                                % (name, dropped))

    for name, v2_field in v2_props.items():
        if name in v1_props:
            continue
        if name in v2_required and "default" not in v2_field:
            problems.append("ADDED required field %r with no default: existing "
                            "records cannot migrate forward" % name)

    if problems:
        return False, "; ".join(problems)
    return True, "forward-compatible"


# ---------------------------------------------------------------- star -----

def _load_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def build_star(assertion_rows):
    """(star_rows, dropped_evidence_count, dropped_contradiction_count). See
    the module docstring's STAR PROJECTION section for exactly what each
    count means and why it is lossy on three separate axes."""
    groups = {}
    for r in assertion_rows:
        groups.setdefault((r.get("subject"), r.get("predicate")), []).append(r)

    star_rows = []
    dropped_evidence = 0
    dropped_contradictions = 0
    for (subject, predicate) in sorted(groups, key=lambda k: (k[0] or "", k[1] or "")):
        rows = groups[(subject, predicate)]
        if len(rows) > 1:
            dropped_evidence += len(rows) - 1
            distinct_values = {r.get("value") for r in rows}
            if len(distinct_values) > 1:
                dropped_contradictions += len(distinct_values) - 1

        ranked = [(authority.rank_key(r["authority"], 0), r)
                 for r in rows if r.get("authority") in authority.LEVELS]
        if ranked:
            best_rank = max(rank for rank, _ in ranked)
            tied = sorted((r for rank, r in ranked if rank == best_rank),
                         key=lambda r: r.get("id", ""))
            winner = tied[0]
        else:
            winner = sorted(rows, key=lambda r: r.get("id", ""))[0]

        star_rows.append({
            "subject": subject,
            "predicate": predicate,
            "value": winner.get("value"),
            "authority": winner.get("authority"),
            "lifecycle": winner.get("lifecycle"),
            "valid_from": winner.get("valid_from", ""),
            "valid_to": winner.get("valid_to", ""),
            "winning_id": winner.get("id"),
        })
    return star_rows, dropped_evidence, dropped_contradictions


# ----------------------------------------------------------------- CLI -----

def cmd_validate(args):
    if args.kind not in KNOWN_KINDS:
        print("bm_vault_interchange: NO-DATA, unknown --kind %r "
             "(must be one of %s)" % (args.kind, ", ".join(KNOWN_KINDS)), file=sys.stderr)
        return 2
    if not args.file or not os.path.isfile(args.file):
        print("bm_vault_interchange: NO-DATA, no readable file at %r" % args.file,
             file=sys.stderr)
        return 2
    try:
        with open(args.file, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError as exc:
        print("bm_vault_interchange: NO-DATA, could not read %r (%s)"
             % (args.file, exc), file=sys.stderr)
        return 2

    rows = 0
    for lineno, raw in enumerate(lines, 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            record = json.loads(raw)
        except ValueError as exc:
            print("bm_vault_interchange: REFUSE %s:%d: invalid JSON (%s)"
                 % (args.file, lineno, exc), file=sys.stderr)
            return 1
        try:
            schema, schema_name = resolve_schema_for_kind(args.kind, record)
        except InterchangeError as exc:
            print("bm_vault_interchange: NO-DATA, %s" % exc, file=sys.stderr)
            return 2
        if schema is None:
            print("bm_vault_interchange: REFUSE %s:%d: %s"
                 % (args.file, lineno, schema_name), file=sys.stderr)
            return 1
        problems = validate_record(record, schema)
        if problems:
            print("bm_vault_interchange: REFUSE %s:%d (kind=%s, schema=%s): %s"
                 % (args.file, lineno, args.kind, schema_name, "; ".join(problems)),
                 file=sys.stderr)
            return 1
        rows += 1
    print("PASS kind=%s file=%s rows=%d" % (args.kind, args.file, rows))
    return 0


def cmd_evolve(args):
    try:
        v1 = load_schema_from_path(args.from_)
        v2 = load_schema_from_path(args.to)
    except InterchangeError as exc:
        print("bm_vault_interchange: NO-DATA, %s" % exc, file=sys.stderr)
        return 2
    ok, message = evolve_check(v1, v2)
    if ok:
        print("FORWARD-COMPATIBLE from=%s to=%s: %s" % (args.from_, args.to, message))
        return 0
    print("bm_vault_interchange: REFUSED from=%s to=%s: %s"
         % (args.from_, args.to, message), file=sys.stderr)
    return 1


def load_schema_from_path(path):
    """Like load_schema(), but for an arbitrary path (evolve compares any
    two schema snapshots, not only the ones this repo currently ships)."""
    if not path or not os.path.isfile(path):
        raise InterchangeError("no readable schema file at %r" % path)
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as exc:
        raise InterchangeError("schema %r is not valid JSON (%s)" % (path, exc))


def cmd_star(args):
    if not args.assertions or not os.path.isfile(args.assertions):
        print("bm_vault_interchange: NO-DATA, no readable assertions file at %r"
             % args.assertions, file=sys.stderr)
        return 2
    rows = _load_jsonl(args.assertions)
    star_rows, dropped_evidence, dropped_contradictions = build_star(rows)
    print("STAR PROJECTION (LOSSY): flattens bi-temporal intervals to the "
         "winning assertion's own valid_from/valid_to per (subject, "
         "predicate); collapses many-to-many evidence (%d row(s) dropped) "
         "to one row per pair; drops %d contradiction edge(s) between "
         "competing values entirely." % (dropped_evidence, dropped_contradictions))
    for row in star_rows:
        print(json.dumps(row, sort_keys=True))
    return 0


def cmd_check_registry(args):
    try:
        registry = load_field_registry()
    except InterchangeError as exc:
        print("bm_vault_interchange: NO-DATA, %s" % exc, file=sys.stderr)
        return 2
    registered = {f["name"]: f["id"] for f in registry.get("fields", [])}
    findings = []
    seen_ids = {}
    for (schema_file, field_name), field_id in sorted(iter_schema_field_ids().items()):
        if registered.get(field_name) != field_id:
            findings.append("%s field %r carries x-field-id %s, registry says %s"
                            % (schema_file, field_name, field_id,
                               registered.get(field_name, "UNREGISTERED")))
        if field_id in seen_ids and seen_ids[field_id] != field_name:
            findings.append("x-field-id %s is shared by %r and %r across schema "
                            "files, and is not a rename (registry names one)"
                            % (field_id, seen_ids[field_id], field_name))
        seen_ids[field_id] = field_name
    if findings:
        print("FINDINGS: %d" % len(findings))
        for f in findings:
            print("  %s" % f)
        return 1
    print("REGISTRY CLEAN: %d field id(s), all unique and matching field_registry.json"
         % len(seen_ids))
    return 0


def _build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    pv = sub.add_parser("validate", help="validate a JSONL fixture against one schema")
    pv.add_argument("--kind", required=True, choices=KNOWN_KINDS)
    pv.add_argument("file")

    pe = sub.add_parser("evolve", help="check whether one schema snapshot migrates "
                                       "forward to another")
    pe.add_argument("--from", dest="from_", required=True)
    pe.add_argument("--to", required=True)

    ps = sub.add_parser("star", help="emit the lossy flat star projection")
    ps.add_argument("--assertions", required=True)

    sub.add_parser("check-registry", help="verify every shipped x-field-id is "
                                          "unique and matches field_registry.json")
    return p


def main(argv=None):
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "validate":
        return cmd_validate(args)
    if args.command == "evolve":
        return cmd_evolve(args)
    if args.command == "star":
        return cmd_star(args)
    if args.command == "check-registry":
        return cmd_check_registry(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
