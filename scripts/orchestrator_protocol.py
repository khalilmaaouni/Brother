#!/usr/bin/env python3
"""ORCH-01 of the 1.0.20 orchestration control plane: the night-run-v1
contract and its validator.

WHY THIS EXISTS. A night run is launched from one manifest
(docs/schema/night-run-v1.json) and drives units described by one task
shape (docs/schema/orchestrator-task-v1.json) that report through one event
shape (docs/schema/orchestrator-event-v1.json). If a manifest with no hard
stop, a task with a typo'd task_class, or an event carrying a stray field
could load anyway, every later row of this build (the dispatcher, the claim
store, the verifier) would be reading a document it never actually agreed
to. This module is the one place that refuses those documents, generically,
by reading the schema files rather than hard coding their shape a second
time here: a field added to a schema is checked without a code change here,
so the schema and this checker cannot drift apart on structure.

THIS MIRRORS scripts/contract_check.py, this estate's existing hand rolled
validator for outcome-contract-v1 and its siblings, over the same core
keyword subset (type, required, properties, enum, const, additionalProperties,
items, minItems). It is a new module rather than an extension of that one
because this contract needs three keywords contract_check.py's subset does
not implement (minimum, pattern, oneOf, the last one for
orchestrator-event-v1's two shapes), and contract_check.py is owned by a
different unit of this build, not this one's to change.

THE CENTRAL RULE THIS MODULE ENFORCES: an unrecognised value is never read
as the safe or common case. enum membership is checked exactly as spelled
in the schema file (which is asserted, in
scripts/test_orchestrator_protocol.py, to equal
scripts/orchestrator_invariants.py's own TASK_CLASSES, EVIDENCE_OBLIGATIONS
and ACTIONS sets), so an unknown task_class is refused rather than read as
"implementation", and an unknown evidence_obligation is refused rather than
read as OPTIONAL. additionalProperties is false on every object shape here,
so a mistyped field name is refused as an unexpected field rather than
silently accepted as a new one.

THE OTHER RULE THIS MODULE ENFORCES: a schema file that is missing or is
not valid JSON raises ProtocolError. It is never read as "no constraints"
(an empty schema would pass every document) and never silently skipped; the
vocabulary this whole module enforces comes from that file, so a broken
schema file must stop the caller cold. A DOCUMENT's own problems, in
contrast, are never raised as exceptions: they are the ordinary, expected
return value of validate(), a list of human-readable strings, empty when
the document is clean.

Python 3.9 floor, standard library only, no network, no jsonschema
dependency: a document is checked by hand against the schema's own
declared keywords, never against a second, separately maintained copy of
the same rules.
"""

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_DIR = os.path.normpath(os.path.join(HERE, "..", "docs", "schema"))

#: The manifest schema validate_manifest() checks against. Named once here
#: so a caller of validate_manifest() never has to know the schema's file
#: name, only that it validates the night run manifest shape.
NIGHT_RUN_SCHEMA = "night-run-v1"


class ProtocolError(Exception):
    """A schema file could not be found or could not be parsed as JSON.
    Never raised for a verdict about a document's own content: that is
    validate()'s return value (a possibly empty list of problem strings),
    not an exception. A document failing validation is an ordinary,
    expected outcome; a missing or broken schema file is not, because it
    means this module's own vocabulary cannot be read at all."""


def load_schema(name):
    """The parsed schema named `name` (its docs/schema/ basename, with or
    without a trailing .json) as a Python object.

    Raises ProtocolError when the file does not exist or does not parse
    as JSON. Never returns None, never returns an empty dict as a stand
    in for "could not read it": an empty dict would pass every document
    as valid, which is exactly the false-green this function exists to
    prevent.
    """
    filename = name if name.endswith(".json") else name + ".json"
    path = os.path.join(SCHEMA_DIR, filename)
    if not os.path.isfile(path):
        raise ProtocolError("schema not found: %s" % path)
    try:
        with open(path, encoding="utf-8") as handle:
            schema = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ProtocolError(
            "schema is not valid JSON: %s: %s" % (path, exc))
    if not isinstance(schema, dict):
        raise ProtocolError("schema %s is not a JSON object" % path)
    return schema


def _type_ok(value, type_spec):
    types = type_spec if isinstance(type_spec, list) else [type_spec]
    for one in types:
        if one == "object" and isinstance(value, dict):
            return True
        if one == "array" and isinstance(value, list):
            return True
        if one == "string" and isinstance(value, str):
            return True
        if one == "boolean" and isinstance(value, bool):
            return True
        if (one == "integer" and isinstance(value, int)
                and not isinstance(value, bool)):
            return True
        if (one == "number" and isinstance(value, (int, float))
                and not isinstance(value, bool)):
            return True
        if one == "null" and value is None:
            return True
    return False


def _child(path, key):
    return "%s.%s" % (path, key) if path else str(key)


def _validate_node(instance, schema, path, problems):
    """Recursive structural check over the keyword subset this module
    enforces: const, enum, oneOf, type, pattern, minLength, minimum,
    required, properties, additionalProperties, items, minItems. Appends
    "field: message" strings to `problems` and keeps checking rather
    than stopping at the first hit, so one run names every problem in
    the document, not only the first one it happens to reach.
    """
    if "const" in schema:
        if instance != schema["const"]:
            problems.append("%s: must equal %r, got %r"
                             % (path or "document", schema["const"], instance))
            return
    if "enum" in schema:
        if instance not in schema["enum"]:
            problems.append("%s: must be one of %r, got %r"
                             % (path or "document", schema["enum"], instance))
            return
    if "oneOf" in schema:
        branch_problems = []
        for branch in schema["oneOf"]:
            this_branch = []
            _validate_node(instance, branch, path, this_branch)
            branch_problems.append(this_branch)
        matches = [p for p in branch_problems if not p]
        if not matches:
            detail = "; ".join(
                "shape %d (%s)" % (i, branch.get("title", "untitled"))
                for i, branch in enumerate(schema["oneOf"]))
            problems.append(
                "%s: matches none of the possible shapes: %s"
                % (path or "document", detail))
        elif len(matches) > 1:
            problems.append(
                "%s: matches more than one possible shape, which oneOf "
                "forbids" % (path or "document"))
        return
    if "type" in schema:
        if not _type_ok(instance, schema["type"]):
            problems.append("%s: must be of type %r, got %s"
                             % (path or "document", schema["type"],
                                type(instance).__name__))
            return
    if isinstance(instance, str):
        if "pattern" in schema and re.match(schema["pattern"], instance) is None:
            problems.append("%s: must match pattern %r, got %r"
                             % (path or "document", schema["pattern"], instance))
        if "minLength" in schema and len(instance) < schema["minLength"]:
            problems.append("%s: must be at least %d character(s) long, got %d"
                             % (path or "document", schema["minLength"], len(instance)))
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            problems.append("%s: must be >= %r, got %r"
                             % (path or "document", schema["minimum"], instance))
    if isinstance(instance, dict):
        for required_key in schema.get("required", []):
            if required_key not in instance:
                problems.append("%s: missing required field"
                                 % _child(path, required_key))
        declared = schema.get("properties", {})
        for key, value in instance.items():
            if key in declared:
                _validate_node(value, declared[key], _child(path, key), problems)
            elif schema.get("additionalProperties") is False:
                problems.append(
                    "%s: unexpected field, not declared in the schema"
                    % _child(path, key))
            elif isinstance(schema.get("additionalProperties"), dict):
                _validate_node(value, schema["additionalProperties"],
                                _child(path, key), problems)
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            problems.append(
                "%s: must have at least %d item(s), has %d"
                % (path or "document", schema["minItems"], len(instance)))
        if "items" in schema:
            for index, item in enumerate(instance):
                _validate_node(item, schema["items"],
                                "%s[%d]" % (path or "document", index), problems)


def validate(document, schema_name):
    """Every problem found validating `document` against the schema named
    `schema_name` (see load_schema), as a list of human-readable
    strings. Empty exactly when the document is valid.

    Raises ProtocolError if the schema itself cannot be loaded: a broken
    schema file is this module's own vocabulary failing to load, not a
    fact about the document, so it is never folded into the returned
    problem list.
    """
    schema = load_schema(schema_name)
    problems = []
    _validate_node(document, schema, "", problems)
    return problems


def validate_manifest(path):
    """(ok, errors) for the night-run-v1 manifest at `path` on disk.

    ok is True only when the file exists, parses as JSON, is a JSON
    object, and validates clean against docs/schema/night-run-v1.json.
    A manifest that is missing, unreadable, not valid JSON, or not a
    JSON object is reported as (False, [<message>]): this is the
    explicit failure path this function owns for the DOCUMENT being
    checked. A missing or broken SCHEMA file is a different and worse
    problem (this module's own vocabulary is unreadable) and is left to
    raise ProtocolError out of validate() instead of being folded into
    this function's error list.
    """
    if not os.path.isfile(path):
        return False, ["manifest not found: %s" % path]
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as exc:
        return False, ["manifest is not valid JSON: %s: %s" % (path, exc)]
    if not isinstance(document, dict):
        return False, ["manifest %s is not a JSON object" % path]
    errors = validate(document, NIGHT_RUN_SCHEMA)
    return (len(errors) == 0, errors)
