#!/usr/bin/env python3
"""DOM-10.03: which evidence a change of a given type must carry.

WHY THIS EXISTS. scripts/evidence_obligation.py (EV-3, the frozen
definition site) already answers "how strong must the proof be" with
three levels, OPTIONAL, REQUIRED_FOR_MERGE, REQUIRED_FOR_RELEASE, but it
answers that question the same way for every check regardless of what
kind of change the check is guarding. scripts/claim_discriminativeness.py
(DOM-10.02) separately ruled that "the command exited 0" is never, by
itself, proof a HIGH RISK claim's property would fail if it were false;
that module names the discriminating evidence kinds that DO answer the
question. This module is the row that connects the two: for each known
CHANGE TYPE, which of those discriminating kinds must be present before
a change of that type is accepted. A change whose required kinds are
absent is refused, whatever its check's own exit code says, because a
passing exit code with no discriminating evidence is exactly the failure
DOM-10.02 exists to catch, now enforced one level up, at the change
itself rather than at one claim inside it.

THE DECIDING PROPERTY: each change type carries its own required
evidence, and generic tests-passed evidence is refused. A change of an
UNKNOWN type is never accepted under the weakest obligation by default;
it is refused outright (evaluate_change() and required_evidence() both
raise ValueError), matching orchestrator_invariants.py's rule that an
unrecognised input is never read as the safe case. A change of a KNOWN
type whose registry entry names no required evidence kinds (a planning,
research, documentation, review or architecture change: none of these
carry a running, falsifiable claim) is always accepted, whatever
evidence it carries or does not carry.

VOCABULARY REUSE, none of it retyped here:
  - the obligation levels come from evidence_obligation.LEVELS (EV-3);
  - the evidence kinds come from claim_discriminativeness.EVIDENCE_KINDS
    (DOM-10.02), the same set that module already refuses to accept a
    bare exit code in place of;
  - the change-type vocabulary comes from orchestrator_invariants.
    TASK_CLASSES, the same thirteen values a WBS unit's own task_class
    field already carries, so a change type here is never a fifth
    vocabulary invented for this one row.
_REGISTRY below is the only new fact this module adds: which subset of
those already-defined evidence kinds each already-defined change type
requires, and at which already-defined obligation level.

CLI exit codes: 0 ACCEPTED, 1 BLOCKED (an unknown change type, or a known
type missing required evidence), 2 NO-DATA (the input file is missing,
unreadable, not JSON, or not the right shape).

Python 3.9 floor, standard library only, no network.
"""
import argparse
import json
import sys

import claim_discriminativeness
import evidence_obligation
import orchestrator_invariants

# Reused, never retyped: see VOCABULARY REUSE above.
CHANGE_TYPES = orchestrator_invariants.TASK_CLASSES
EVIDENCE_KINDS = claim_discriminativeness.EVIDENCE_KINDS
OBLIGATION_LEVELS = frozenset(evidence_obligation.LEVELS)

# change_type -> (obligation_level, frozenset of required evidence kinds).
#
# Empty required kinds means this change type carries no running,
# falsifiable claim for DOM-10.02's evidence kinds to discriminate: a
# planning note, a piece of research, a documentation edit, a review
# verdict or an architecture decision is judged by its own review or
# decision record, not by a code-level perturbation. Every change type
# that DOES touch running code carries at least one required kind, and
# REQUIRED_FOR_MERGE unless the proof needs a real packaged artifact,
# dependency resolution or live run the merge-time gate does not have
# (packaging, integration-prep, benchmark), which is the same reasoning
# scripts/gate_obligations.json already gives virgin-unit-proof and
# closing-ceremony-real for binding at release rather than merge.
_REGISTRY = {
    "planning": ("OPTIONAL", frozenset()),
    "architecture": ("OPTIONAL", frozenset()),
    "research": ("OPTIONAL", frozenset()),
    "documentation": ("OPTIONAL", frozenset()),
    "review": ("OPTIONAL", frozenset()),
    "implementation": ("REQUIRED_FOR_MERGE", frozenset(("mutation", "red_before"))),
    "test": ("REQUIRED_FOR_MERGE", frozenset(("red_before",))),
    "repair": ("REQUIRED_FOR_MERGE", frozenset(("red_before", "mutation"))),
    "verification": ("REQUIRED_FOR_MERGE", frozenset(("red_before",))),
    "fault-injection": ("REQUIRED_FOR_MERGE", frozenset(("induced_failure",))),
    "packaging": ("REQUIRED_FOR_RELEASE", frozenset(("dependency_perturbation",))),
    "benchmark": ("REQUIRED_FOR_RELEASE", frozenset(("induced_failure",))),
    "integration-prep": (
        "REQUIRED_FOR_RELEASE",
        frozenset(("dependency_perturbation", "configuration_perturbation")),
    ),
}


def _validate_registry():
    """Raise at import time if the registry disagrees with the
    vocabularies it is built from, rather than let a typo ship as a
    silently wrong obligation."""
    if frozenset(_REGISTRY) != CHANGE_TYPES:
        missing = CHANGE_TYPES - frozenset(_REGISTRY)
        extra = frozenset(_REGISTRY) - CHANGE_TYPES
        raise AssertionError(
            "registry does not cover exactly CHANGE_TYPES: missing %s, extra %s"
            % (sorted(missing), sorted(extra))
        )
    for change_type, entry in _REGISTRY.items():
        level, required = entry
        if level not in OBLIGATION_LEVELS:
            raise AssertionError(
                "change type %r carries unknown obligation level %r" % (change_type, level)
            )
        unknown_kinds = required - EVIDENCE_KINDS
        if unknown_kinds:
            raise AssertionError(
                "change type %r requires unknown evidence kinds: %s"
                % (change_type, sorted(unknown_kinds))
            )


_validate_registry()


def required_evidence(change_type):
    """(obligation_level, frozenset_of_required_evidence_kinds) for a
    known change type.

    Raises ValueError for any change type not in the registry, including
    an unhashable value: a caller mistyping the change type must see
    that immediately, never have it read as the weakest (OPTIONAL)
    obligation by default.
    """
    try:
        return _REGISTRY[change_type]
    except (KeyError, TypeError):
        raise ValueError("unknown change type: %r" % (change_type,))


def evidence_kinds_present(evidence_items):
    """The subset of EVIDENCE_KINDS actually present in a list of
    evidence dicts (matching claim_discriminativeness's own item shape,
    each carrying a "kind" key).

    Never raises on a malformed item: an item that is not a dict, or
    whose kind is missing or not in EVIDENCE_KINDS, is bad data, not a
    caller defect, so it simply contributes nothing to the result (same
    posture as claim_discriminativeness.classify_evidence).
    """
    present = set()
    if not evidence_items:
        return frozenset(present)
    for item in evidence_items:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        if kind in EVIDENCE_KINDS:
            present.add(kind)
    return frozenset(present)


def evaluate_change(change_type, evidence_items):
    """(accepted: bool, reason: str) for a change of this type carrying
    this evidence.

    Raises ValueError for an unknown change type (see required_evidence):
    an unrecognised type is refused outright, never silently accepted
    under whatever obligation happens to be weakest.

    True when the change type requires no evidence, or when every
    required evidence kind is present. False, naming the missing kinds,
    otherwise: evidence that only asserts a passing exit code, with no
    named discriminating kind, never satisfies a non-empty requirement.
    """
    obligation, required = required_evidence(change_type)
    if not required:
        return True, "change type %r (%s) carries no evidence obligation" % (
            change_type, obligation,
        )
    present = evidence_kinds_present(evidence_items)
    missing = required - present
    if missing:
        return False, (
            "change type %r is %s and requires evidence kinds %s: missing %s "
            "(a passing exit code alone, with no discriminating evidence, "
            "does not satisfy this)"
            % (change_type, obligation, sorted(required), sorted(missing))
        )
    return True, "all required evidence kinds present for %r: %s" % (
        change_type, sorted(required),
    )


def _load_payload(path):
    """(payload, None) on success, (None, reason) on any read or parse
    failure. Every failure path is named explicitly; there is no bare
    except."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle), None
    except FileNotFoundError as exc:
        return None, "file not found: %s" % exc
    except IsADirectoryError as exc:
        return None, "path is a directory: %s" % exc
    except OSError as exc:
        return None, "could not read file: %s" % exc
    except json.JSONDecodeError as exc:
        return None, "not valid JSON: %s" % exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", help='JSON file shaped {"change_type": str, "evidence": [...]}')
    args = parser.parse_args(argv)

    payload, failure = _load_payload(args.path)
    if failure is not None:
        print("NO-DATA: %s" % failure, file=sys.stderr)
        return 2

    if not isinstance(payload, dict):
        print("NO-DATA: top level JSON value must be an object", file=sys.stderr)
        return 2

    change_type = payload.get("change_type")
    evidence = payload.get("evidence")
    if not isinstance(change_type, str):
        print("NO-DATA: change_type must be a string", file=sys.stderr)
        return 2
    if not isinstance(evidence, list):
        print("NO-DATA: evidence must be a list", file=sys.stderr)
        return 2

    try:
        accepted, reason = evaluate_change(change_type, evidence)
    except ValueError as exc:
        print("BLOCKED: %s" % exc)
        return 1

    if accepted:
        print("ACCEPTED: %s" % reason)
        return 0
    print("BLOCKED: %s" % reason)
    return 1


if __name__ == "__main__":
    sys.exit(main())
