#!/usr/bin/env python3
"""contract_check: does a record follow the outcome contract, the one project
record for the door, Intake V2 and Daybook (2026-09-08 debate judgment,
docs/schema/outcome-contract-v1.json).

THE SCHEMA FILE STAYS THE SINGLE SOURCE. This checker reads
docs/schema/outcome-contract-v1.json and enforces it generically over a
small keyword subset (type, required, properties, enum, const, items,
minItems, additionalProperties), so no field name or type is hard coded
here a second time. A field added to the schema is checked without a code
change; the schema and this file cannot drift apart on structure because
there is only one place structure is written down.

RULES THE SCHEMA'S KEYWORDS CANNOT SAY, enforced by hand instead, each
documented in the schema's own description so a reader of either file
finds the same account (amended 2026-09-08 after the Opus adversarial
review, findings F1 to F12):

  QUESTION AND LANGUAGE NON-EMPTY. The enforced subset carries no
  minLength, so either could pass type: string as "". Checked directly.

  TICKET REQUIRED WHEN AUDITED. ticket must be non-null when audit.required
  is true. A plain conditional; the enforced subset has no if/then.

  THE QUESTIONS CAP (the A-prime rule, refined by F12). At most one open
  question once state is at or past the schema's own x-question-pivot
  (contracted); none once state is delivered or later. A cross-field
  count, not a structural shape, and the pivot state is read from the
  schema instead of a literal in this file (F5), so a schema whose enum
  drops the pivot FAILs cleanly instead of crashing with a traceback.

  SUCCESS_CHECKS REQUIRED ONCE CONTRACTED. A draft may carry zero
  success_checks (the one open question a draft asks is often exactly
  "how do you check this repository", so there is nothing to demand
  yet); once state is at or past the same x-question-pivot the
  questions cap reads, at least one entry is required. Reusing that
  pivot rather than a second hard coded "contracted" literal keeps the
  schema the single place either rule's boundary is spelled.

  PROVENANCE COVERS EVERY PROJECT FIELD, BOTH WAYS (F7). Every key present
  in `project` (other than provenance itself) must have an entry in
  project.provenance, and every provenance entry must name a real project
  field. additionalProperties on provenance only checks the VALUES already
  there; it cannot say every project key must appear, nor catch a
  provenance entry naming a field that does not exist.

  MUST_ANSWER CITED ONCE DELIVERED (F1). Once state is delivered or later,
  every must_answer entry needs a non-empty answer and a receipt_id that
  names a real entry in receipts.

  RECEIPT REF GRAMMAR (F4). Every receipts[].ref must start with one of
  the three prefixes scripts/receipt_check.py resolves: file:, evidence:,
  or url:. The enforced subset has no pattern keyword.

  SUCCESS CHECK VALUE WHEN CITED (F9). A success_checks entry whose expect
  is contains or file-exists needs a non-empty value.

Exit contract, mirroring scripts/split_check.py and this estate's other
gates:
  0  PASS      the record satisfies the schema and the hand rules
  1  FAIL      one or more problems, each printed on its own line
  2  NO-DATA   the record or the schema could not be read as JSON

NO-DATA IS NOT A PASS: a record this script could not open or parse is
"could not look", never "looked and found nothing wrong".

Python 3, standard library only. No network.
"""
import argparse
import json
import os
import sys

NODATA = "NO-DATA"
HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_FILENAME = "outcome-contract-v1.json"
DEFAULT_SCHEMA = os.path.normpath(
    os.path.join(HERE, "..", "docs", "schema", SCHEMA_FILENAME))
#: The same schema as an installed runtime carries it: bundle/runtime has no
#: docs/ tree above it, so scripts/bundle_runtime.py copies the schema FLAT
#: beside this module (DATA_FILES there), and default_schema() below finds it
#: there when the checkout path is not present. Without this, an installed
#: `brother-run --contract` would read NO-DATA on every record.
INSTALLED_SCHEMA = os.path.join(HERE, SCHEMA_FILENAME)


def default_schema():
    """The schema to read when nobody names one: the checkout's own
    docs/schema copy (the single source), or the flat copy an installed
    runtime ships beside this module. The checkout path is returned
    unchanged when neither exists, so the NO-DATA names the canonical
    location rather than the packaging fallback."""
    for candidate in (DEFAULT_SCHEMA, INSTALLED_SCHEMA):
        if os.path.isfile(candidate):
            return candidate
    return DEFAULT_SCHEMA

#: The A-prime rule's own state ordering: "contracted or later" means at or
#: past this index, read from the schema's own enum rather than repeated
#: as a literal list wherever the rule is checked. Used only when a
#: schema read is missing the node entirely (a NO-DATA schema read).
STATE_ORDER_FALLBACK = [
    "draft", "contracted", "planned", "in-flight", "delivered", "superseded",
]

#: Used only when a schema read carries no x-question-pivot key at all
#: (never for the shipped, up to date schema).
QUESTION_PIVOT_DEFAULT = "contracted"

#: The receipt resolver grammar scripts/receipt_check.py understands
#: (F4). The one place this checker spells the prefixes.
RECEIPT_REF_PREFIXES = ("file:", "evidence:", "url:")


class NoData(Exception):
    """A file could not be found or parsed. Never raised for a verdict
    about the record's content; that is a return value, not an exception."""


def load_json(path, label):
    if not os.path.isfile(path):
        raise NoData("%s not found: %s" % (label, path))
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise NoData("%s could not be read as JSON: %s: %s" % (label, path, exc))


def _child(path, key):
    return "%s.%s" % (path, key) if path else str(key)


def _type_ok(value, type_spec):
    types = type_spec if isinstance(type_spec, list) else [type_spec]
    for t in types:
        if t == "object" and isinstance(value, dict):
            return True
        if t == "array" and isinstance(value, list):
            return True
        if t == "string" and isinstance(value, str):
            return True
        if t == "boolean" and isinstance(value, bool):
            return True
        if t == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if t == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if t == "null" and value is None:
            return True
    return False


def validate(instance, schema, path, problems):
    """Recursive structural check over the enforced keyword subset. Appends
    "field: message" strings to problems, never stopping at the first, so
    one run names every structural problem rather than one per attempt."""
    if "const" in schema:
        if instance != schema["const"]:
            problems.append("%s: must equal %r, got %r"
                            % (path or "record", schema["const"], instance))
            return
    if "enum" in schema:
        if instance not in schema["enum"]:
            problems.append("%s: must be one of %r, got %r"
                            % (path or "record", schema["enum"], instance))
            return
    if "type" in schema:
        if not _type_ok(instance, schema["type"]):
            problems.append("%s: must be of type %r, got %s"
                            % (path or "record", schema["type"],
                               type(instance).__name__))
            return
    if isinstance(instance, dict):
        for req in schema.get("required", []):
            if req not in instance:
                problems.append("%s: missing required field"
                                % _child(path, req))
        props = schema.get("properties", {})
        for key, value in instance.items():
            if key in props:
                validate(value, props[key], _child(path, key), problems)
            elif schema.get("additionalProperties") is False:
                problems.append("%s: unexpected field" % _child(path, key))
            elif isinstance(schema.get("additionalProperties"), dict):
                validate(value, schema["additionalProperties"],
                        _child(path, key), problems)
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            problems.append("%s: must have at least %d item(s), has %d"
                            % (path or "record", schema["minItems"], len(instance)))
        if "items" in schema:
            for i, item in enumerate(instance):
                validate(item, schema["items"], "%s[%d]" % (path, i), problems)


def _state_order(schema):
    """The state enum read from the schema itself, falling back to the
    fixed list above only when the schema is missing that node (a NO-DATA
    schema read, never a normal run)."""
    try:
        return list(schema["properties"]["state"]["enum"])
    except (KeyError, TypeError):
        return list(STATE_ORDER_FALLBACK)


def _safe_index(order, value, problems):
    """order.index(value), guarded (F5): appends a "state: ..." problem
    and returns None instead of raising when the schema's own state enum
    no longer carries the named pivot, so a schema edit that drops or
    renames a pivot state FAILs cleanly instead of crashing the checker
    with a traceback."""
    try:
        return order.index(value)
    except ValueError:
        problems.append("state: the schema enum lacks the pivot state %r"
                        % value)
        return None


def hand_rules(record, schema):
    """The rules the schema's own keywords cannot express, listed in the
    module docstring and in the schema's description field. Returns every
    problem found, never stopping at the first."""
    problems = []

    for field in ("language", "question"):
        value = record.get(field)
        if isinstance(value, str) and not value.strip():
            problems.append("%s: must not be empty after stripping "
                            "whitespace" % field)

    audit = record.get("audit")
    if isinstance(audit, dict) and audit.get("required") is True:
        if record.get("ticket") is None:
            problems.append("ticket: required because audit.required is "
                            "true, but ticket is null")

    order = _state_order(schema)
    state = record.get("state")
    questions = record.get("questions")
    must_answer = record.get("must_answer")
    receipts = record.get("receipts")
    success_checks = record.get("success_checks")

    question_pivot = schema.get("x-question-pivot", QUESTION_PIVOT_DEFAULT)
    contracted_idx = _safe_index(order, question_pivot, problems)
    delivered_idx = _safe_index(order, "delivered", problems)
    cur_idx = order.index(state) if state in order else None

    # THE QUESTIONS CAP (A-prime, refined by F12): none once delivered or
    # later, at most one once at or past the pivot state, unlimited before.
    if isinstance(questions, list) and cur_idx is not None:
        if (delivered_idx is not None and cur_idx >= delivered_idx
                and len(questions) > 0):
            problems.append("questions: no open questions are allowed "
                            "once state is '%s' or later, found %d"
                            % (state, len(questions)))
        elif (contracted_idx is not None and cur_idx >= contracted_idx
              and len(questions) > 1):
            problems.append("questions: at most one blocking question is "
                            "allowed once state is '%s' (the A-prime rule), "
                            "found %d" % (state, len(questions)))

    # SUCCESS_CHECKS REQUIRED ONCE CONTRACTED. A draft may carry zero
    # entries; at or past the same pivot the questions cap reads, at
    # least one is required.
    if (isinstance(success_checks, list) and cur_idx is not None
            and contracted_idx is not None and cur_idx >= contracted_idx
            and len(success_checks) == 0):
        problems.append("success_checks: at least one check is required "
                        "once state is '%s' or later, found 0"
                        % question_pivot)

    # MUST_ANSWER CITED ONCE DELIVERED (F1).
    if (isinstance(must_answer, list) and cur_idx is not None
            and delivered_idx is not None and cur_idx >= delivered_idx):
        receipt_ids = set()
        if isinstance(receipts, list):
            for r in receipts:
                if isinstance(r, dict) and isinstance(r.get("id"), str):
                    receipt_ids.add(r["id"])
        for entry in must_answer:
            if not isinstance(entry, dict):
                continue
            field_name = entry.get("field", "?")
            answer = entry.get("answer")
            if not (isinstance(answer, str) and answer.strip()):
                problems.append("must_answer.%s: answer is required once "
                                "state is 'delivered' or later"
                                % field_name)
            receipt_id = entry.get("receipt_id")
            if not receipt_id:
                problems.append("must_answer.%s: receipt_id is required "
                                "once state is 'delivered' or later"
                                % field_name)
            elif receipt_id not in receipt_ids:
                problems.append("must_answer.%s: receipt_id %r names no "
                                "entry in receipts" % (field_name, receipt_id))

    # RECEIPT REF GRAMMAR (F4).
    if isinstance(receipts, list):
        for r in receipts:
            if not isinstance(r, dict):
                continue
            ref = r.get("ref")
            rid = r.get("id", "?")
            if isinstance(ref, str) and not ref.startswith(RECEIPT_REF_PREFIXES):
                problems.append("receipts.%s.ref: must start with one of "
                                "%r, got %r" % (rid, RECEIPT_REF_PREFIXES, ref))

    # SUCCESS CHECK VALUE WHEN CITED (F9).
    if isinstance(success_checks, list):
        for sc in success_checks:
            if not isinstance(sc, dict):
                continue
            expect = sc.get("expect")
            if expect in ("contains", "file-exists"):
                value = sc.get("value")
                if not (isinstance(value, str) and value.strip()):
                    problems.append("success_checks.%s: value is required "
                                    "when expect is %r"
                                    % (sc.get("id", "?"), expect))

    # PROVENANCE COVERS EVERY PROJECT FIELD, BOTH WAYS (F7).
    project = record.get("project")
    if isinstance(project, dict):
        provenance = project.get("provenance")
        if isinstance(provenance, dict):
            for key in project:
                if key == "provenance":
                    continue
                if key not in provenance:
                    problems.append("project.provenance: missing "
                                    "provenance entry for project field "
                                    "'%s'" % key)
            for key in provenance:
                if key not in project:
                    problems.append("project.provenance: entry for '%s', "
                                    "which is not a field of project"
                                    % key)

    return problems


def check(record, schema):
    """Every problem found, structural then hand rules, deduplicated but
    order preserved."""
    problems = []
    validate(record, schema, "", problems)
    problems.extend(hand_rules(record, schema))
    seen = set()
    out = []
    for p in problems:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("record", help="path to the outcome-contract JSON record")
    # Resolved at CALL time, never bound as an argparse default at import
    # time: an installed copy and a checkout answer default_schema()
    # differently, and a default captured at definition time could not.
    ap.add_argument("--schema", default=None,
                    help="path to the outcome-contract schema "
                         "(default: docs/schema/outcome-contract-v1.json, or "
                         "the copy beside this module in an installed "
                         "runtime)")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    try:
        schema = load_json(args.schema or default_schema(), "schema")
        record = load_json(args.record, "record")
    except NoData as exc:
        print("contract_check: %s: %s" % (NODATA, exc))
        return 2

    if not isinstance(record, dict):
        print("contract_check: %s: %s is not a JSON object"
              % (NODATA, args.record))
        return 2
    if not isinstance(schema, dict):
        print("contract_check: %s: %s is not a JSON object"
              % (NODATA, args.schema or default_schema()))
        return 2

    problems = check(record, schema)
    if problems:
        for p in problems:
            print("contract_check: FAIL: %s" % p)
        print("contract_check: FAIL %d problem(s) in %s"
              % (len(problems), args.record))
        return 1
    print("contract_check: PASS %s" % args.record)
    return 0


if __name__ == "__main__":
    sys.exit(main())
