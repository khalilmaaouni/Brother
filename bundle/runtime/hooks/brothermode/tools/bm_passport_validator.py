#!/usr/bin/env python3
"""Standalone validator for a change passport v1 document
(schema/change-passport.v1.json; docs/PASSPORT.md is the plain-language
guide; docs/NORTH-STAR-CHAIN.md, section "The change passport, in full", is
the spec authority).

WHY STANDALONE. A third party (BrotherSBE, a CI runner, anyone who never
installed this repository) needs to check a passport file without pulling in
this project's own tooling or a jsonschema library. So this file imports
NOTHING from this repository and NOTHING beyond the Python standard library.
It re-implements the handful of rules that actually matter by hand, rather
than deferring to the JSON Schema document, because a schema-only check
cannot express "field 4's items may be empty only when a justification is
given" without machinery this file is deliberately avoiding.

WHAT IT CHECKS, each one a NAMED reason on its own line when it fails:

  1. "schema" equals the literal string "change-passport/v1".
  2. The five consumer keys (whatWasDone, whoDidIt, whatWasRun,
     whatWasNotEstablished, whereItCameFrom) are present and ANSWERED: a
     non-empty list whose entries are non-blank strings. Matches the
     consuming side's own answered() (BrotherSBE tools/sbe_onepager.py):
     '' and None carry nothing, an empty list carries nothing.
  3. Field 4's law: details.notEstablished.items is non-empty, OR items is
     empty and details.notEstablished.noneClaimJustification is a non-blank
     string. A literal claim that nothing was left unestablished needs its
     own justification.
  4. change.baseCommit and change.headCommit are present and hex-shaped
     (7 to 40 hex characters); change.repo, change.projectId are non-blank
     strings; change.filesTouched is a list of strings (may be empty).
  5. details.who.accountableHuman is a non-blank string. Accountability is a
     name, never a role. details.who.sessions is a list of objects each
     carrying a non-blank "label" and a "claims" list of strings.
  6. Every entry in details.evidence carries an origin in _EVIDENCE_ORIGINS
     (schema 22: "local", "ci", "executed", "observed", "imported",
     "recovered", "synthetic" or "inferred") and a timestamp.
  7. generatedAt is a non-blank string shaped like the generator's own
     canonical form (seconds-precision UTC, a literal trailing "Z").
  8. details.method.name is a non-blank string.
  9. "sensitivity" equals "redacted" or "raw": the marker naming which
     export policy produced this document (cross-family review 2026-08-20,
     F1).
 10. CONSISTENCY: details.who.accountableHuman, when answered, appears
     (as a substring) in at least one whoDidIt line. whoDidIt is the only
     place the consumer ever reads the accountable name from, so a
     document whose two copies of that name disagree is not internally
     consistent, even though each one alone would pass rules 2 and 5.

Cross-family review 2026-08-20 (F7) found the standalone validator checking
only a subset of the schema's own required shape: a document missing
generatedAt, change.repo, change.filesTouched, details.method or a well
formed details.who.sessions passed with zero violations. Rules 4 (repo,
projectId, filesTouched), 5 (sessions), 7, 8, 9 and 10 above close that.

Exit codes: 0 and the line "VALID" when every check passes. 1 and one line
per violation when the document is well-formed JSON but fails a rule.
2 and one line naming the problem when the file cannot be read, is not
valid JSON, carries a duplicate object key, carries a non-finite numeric
constant (NaN/Infinity/-Infinity, which JSON does not define but Python's
json module accepts by default), or is over the 16 MiB size this validator
reads (F8: an unbounded read is an unbounded diagnostic surface for
whatever reads this file's own output next). A crash is never a pass: any
unexpected exception is caught and reported as a named error at exit 2.

Python 3.9, standard library only, no third-party schema library.

Usage: python3 tools/bm_passport_validator.py <file>
"""
import json
import os
import re
import sys

_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
_GENERATED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_SCHEMA_VALUE = "change-passport/v1"
_SENSITIVITY_VALUES = ("redacted", "raw")

#: Schema 22 (evidence origin/provenance). A hand-typed copy of
#: tools/bm_store.py's own EVIDENCE_ORIGINS, never an import of it: this
#: file's whole reason to exist is reading a passport with NOTHING beyond
#: the standard library (see the module docstring's WHY STANDALONE), so
#: the two lists are kept in sync by eye, the same way _SCHEMA_VALUE and
#: _SENSITIVITY_VALUES already stand alone rather than importing the
#: producer's own constants.
_EVIDENCE_ORIGINS = ("local", "ci", "executed", "observed", "imported",
                     "recovered", "synthetic", "inferred")

#: F8. A validator that reads an unlimited file is an unlimited attack
#: surface for whatever process pipes an arbitrary file into this one
#: (this producer caps a generated document far below this; a third party
#: document is not this producer's own output and gets no such guarantee).
_MAX_FILE_BYTES = 16 * 1024 * 1024

#: The five keys the consumer (BrotherSBE tools/sbe_passport.py) reads
#: directly off the deposit, in the chain's own order.
_CONSUMER_KEYS = (
    (1, "whatWasDone"),
    (2, "whoDidIt"),
    (3, "whatWasRun"),
    (4, "whatWasNotEstablished"),
    (5, "whereItCameFrom"),
)


def _answered_string(value):
    return isinstance(value, str) and value.strip() != ""


def _answered_list_of_strings(value):
    """True when `value` is a non-empty list whose every entry is a
    non-blank string. Mirrors the consuming side's answered() for a list:
    non-empty AND every element itself answered."""
    if not isinstance(value, list) or not value:
        return False
    return all(_answered_string(v) for v in value)


def validate(doc):
    """Return a list of violation strings. Empty list means valid. `doc`
    is assumed to already be parsed JSON (any type); every shape check is
    done here rather than assumed."""
    problems = []
    if not isinstance(doc, dict):
        return ["the document is not a JSON object"]

    # 1. schema key.
    schema_value = doc.get("schema")
    if schema_value != _SCHEMA_VALUE:
        problems.append(
            "schema must equal %r, got %r" % (_SCHEMA_VALUE, schema_value))

    # 2. the five consumer keys, each answered-non-empty.
    for number, key in _CONSUMER_KEYS:
        value = doc.get(key)
        if not _answered_list_of_strings(value):
            problems.append(
                "%s (field %d) must be a non-empty list of non-blank "
                "strings, got %r" % (key, number, value))

    # 7. generatedAt: non-blank, and shaped like the generator's own
    # canonical seconds-precision UTC form.
    generated_at = doc.get("generatedAt")
    if not _answered_string(generated_at):
        problems.append(
            "generatedAt is required and must be a non-blank string, "
            "got %r" % (generated_at,))
    elif not _GENERATED_AT_RE.match(generated_at.strip()):
        problems.append(
            "generatedAt is not an ISO 8601 UTC timestamp of the shape "
            "YYYY-MM-DDTHH:MM:SSZ: %r" % (generated_at,))

    # 9. the sensitivity marker (F1): which export policy produced this
    # document. Checked here, alongside the other whole-document markers,
    # rather than folded into any per-field rule below.
    sensitivity = doc.get("sensitivity")
    if sensitivity not in _SENSITIVITY_VALUES:
        problems.append(
            "sensitivity must be one of %r, got %r"
            % (_SENSITIVITY_VALUES, sensitivity))

    details = doc.get("details")
    details_is_object = isinstance(details, dict)
    if not details_is_object:
        problems.append("details must be an object, got %r" % (details,))

    # 3. field 4's law, read from details.notEstablished rather than from
    # the derived top-level whatWasNotEstablished list, because the law is
    # about the SOURCE record, not about whether the derived line happened
    # to be non-empty.
    not_established = details.get("notEstablished") if details_is_object else None
    if not isinstance(not_established, dict):
        problems.append(
            "details.notEstablished must be an object, got %r"
            % (not_established,))
    else:
        items = not_established.get("items")
        justification = not_established.get("noneClaimJustification")
        if not isinstance(items, list):
            problems.append(
                "details.notEstablished.items must be a list, got %r"
                % (items,))
        elif not items:
            if not _answered_string(justification):
                problems.append(
                    "details.notEstablished.items is empty and "
                    "noneClaimJustification is not a non-blank string "
                    "(%r); a literal claim that nothing was left "
                    "unestablished needs its own justification"
                    % (justification,))
        elif not all(_answered_string(v) for v in items):
            problems.append(
                "details.notEstablished.items contains a blank or "
                "non-string entry: %r" % (items,))

    # 4. change identity: baseCommit / headCommit present and hex-shaped;
    # repo and projectId non-blank; filesTouched a list of strings (may
    # be empty, per the schema, when the range touches nothing).
    change = doc.get("change")
    if not isinstance(change, dict):
        problems.append("change must be an object, got %r" % (change,))
    else:
        for field in ("baseCommit", "headCommit"):
            value = change.get(field)
            if not _answered_string(value):
                problems.append(
                    "change.%s is required and must be a non-blank "
                    "string, got %r" % (field, value))
            elif not _COMMIT_RE.match(value.strip()):
                problems.append(
                    "change.%s is not hex-shaped (7 to 40 hex characters): "
                    "%r" % (field, value))
        for field in ("repo", "projectId"):
            value = change.get(field)
            if not _answered_string(value):
                problems.append(
                    "change.%s is required and must be a non-blank "
                    "string, got %r" % (field, value))
        files_touched = change.get("filesTouched")
        if not isinstance(files_touched, list):
            problems.append(
                "change.filesTouched must be a list, got %r"
                % (files_touched,))
        elif not all(isinstance(f, str) and f for f in files_touched):
            problems.append(
                "change.filesTouched must be a list of non-blank "
                "strings, got %r" % (files_touched,))

    # 5. accountableHuman: a name, never a role, never blank. sessions is
    # a list of {label, claims}, each label non-blank and each claims a
    # list of strings (which may itself be empty: a session may hold no
    # named claim).
    who = details.get("who") if details_is_object else None
    accountable = None
    if not isinstance(who, dict):
        problems.append(
            "details.who must be an object, got %r" % (who,))
    else:
        accountable = who.get("accountableHuman")
        if not _answered_string(accountable):
            problems.append(
                "details.who.accountableHuman is required and must be a "
                "non-blank name, got %r" % (accountable,))
        sessions = who.get("sessions")
        if not isinstance(sessions, list):
            problems.append(
                "details.who.sessions must be a list, got %r" % (sessions,))
        else:
            for index, session in enumerate(sessions):
                if not isinstance(session, dict):
                    problems.append(
                        "details.who.sessions[%d] is not an object: %r"
                        % (index, session))
                    continue
                if not _answered_string(session.get("label")):
                    problems.append(
                        "details.who.sessions[%d].label is required and "
                        "must be a non-blank string, got %r"
                        % (index, session.get("label")))
                claims = session.get("claims")
                if not isinstance(claims, list) or not all(
                        isinstance(c, str) for c in claims):
                    problems.append(
                        "details.who.sessions[%d].claims must be a list "
                        "of strings, got %r" % (index, claims))

    # 8. method.name: a name, never blank.
    method = details.get("method") if details_is_object else None
    if not isinstance(method, dict):
        problems.append(
            "details.method must be an object, got %r" % (method,))
    elif not _answered_string(method.get("name")):
        problems.append(
            "details.method.name is required and must be a non-blank "
            "string, got %r" % (method.get("name"),))

    # 10. CONSISTENCY: the accountable name, when answered, must appear in
    # at least one whoDidIt line. Skipped when accountableHuman itself is
    # blank (rule 5 already names that on its own; a blank name trivially
    # "appears" in every line, which would be a check that always passes
    # for the wrong reason).
    if _answered_string(accountable):
        who_did_it = doc.get("whoDidIt")
        lines = who_did_it if isinstance(who_did_it, list) else []
        if not any(isinstance(line, str) and accountable in line
                   for line in lines):
            problems.append(
                "details.who.accountableHuman (%r) does not appear in any "
                "whoDidIt line; the two copies of the accountable name "
                "disagree" % (accountable,))

    # 6. every evidence entry carries origin in {local, ci} and a timestamp.
    evidence = details.get("evidence") if details_is_object else None
    if not isinstance(evidence, list):
        problems.append(
            "details.evidence must be a list, got %r" % (evidence,))
    else:
        for index, entry in enumerate(evidence):
            if not isinstance(entry, dict):
                problems.append(
                    "details.evidence[%d] is not an object: %r"
                    % (index, entry))
                continue
            origin = entry.get("origin")
            if origin not in _EVIDENCE_ORIGINS:
                problems.append(
                    "details.evidence[%d].origin must be one of %s, "
                    "got %r" % (index, ", ".join(_EVIDENCE_ORIGINS), origin))
            if not _answered_string(entry.get("timestamp")):
                problems.append(
                    "details.evidence[%d].timestamp is required and must "
                    "be a non-blank string, got %r"
                    % (index, entry.get("timestamp")))

    return problems


def _reject_duplicate_keys(pairs):
    """A json.loads object_pairs_hook (F8, cross-family review 2026-08-20).
    The stdlib decoder's default behaviour SILENTLY keeps the LAST value
    of a repeated key, which means this validator and any other JSON
    parser reading the same bytes (a browser, another language's json
    library, jq) can legally disagree about what the document even says:
    every one of them is free to keep the first, the last, or refuse
    outright, and the standard names no winner. A passport is meant to be
    read by a third party who never installed this repository; refusing
    outright is the only reading every parser can be TOLD to agree with."""
    seen = set()
    out = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError("duplicate key %r in one JSON object" % (key,))
        seen.add(key)
        out[key] = value
    return out


def _reject_non_finite(constant_text):
    """A json.loads parse_constant hook (F8). Python's json module accepts
    the bare tokens NaN, Infinity and -Infinity by default, which are not
    valid JSON under RFC 8259 at all; a parser that follows the RFC
    (the overwhelming majority outside Python) refuses the same bytes this
    validator would otherwise call VALID."""
    raise ValueError(
        "%s is not a JSON number; NaN/Infinity/-Infinity are not part of "
        "the JSON grammar" % (constant_text,))


def _load(path):
    """Return (doc, error_line_or_None). Never raises: every failure to
    read or parse becomes a plain-English line the caller prints at exit 2,
    because an unreadable file and an invalid document are different facts
    and only the second one is worth exit 1's "here is what to fix"."""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, "could not read %s (%s: %s)" % (path, type(e).__name__, e)
    if size > _MAX_FILE_BYTES:
        return None, (
            "%s is %d bytes, over the %d byte limit this validator reads; "
            "refused rather than parsed (F8: an unbounded read is an "
            "unbounded diagnostic surface)" % (path, size, _MAX_FILE_BYTES))
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as e:
        return None, "could not read %s (%s: %s)" % (path, type(e).__name__, e)
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys,
                          parse_constant=_reject_non_finite), None
    except ValueError as e:
        return None, "%s is not valid JSON (%s)" % (path, e)


def main(argv):
    if len(argv) != 1:
        # sys.argv[0], not a hardcoded "tools/bm_passport_validator.py":
        # this file is deliberately standalone (its own docstring: "imports
        # NOTHING from this repository"), so it cannot resolve the P17
        # shipping-command layout the way tools/bm_store.py's invocation()
        # does for every OTHER tool here. The command the reader actually
        # typed is the one honest answer available without that dependency,
        # and it is correct regardless of where this file was copied to.
        sys.stderr.write("usage: %s <file>\n" % (sys.argv[0] or __file__))
        return 2
    doc, error = _load(argv[0])
    if error is not None:
        sys.stdout.write(error + "\n")
        return 2

    problems = validate(doc)
    if problems:
        for line in problems:
            sys.stdout.write(line + "\n")
        return 1
    sys.stdout.write("VALID\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:
        sys.stdout.write("%s: %s\n" % (type(exc).__name__, exc))
        sys.exit(2)
