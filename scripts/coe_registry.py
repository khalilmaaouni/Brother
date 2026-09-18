#!/usr/bin/env python3
"""COE-01 of the Council of Experts subsystem: the seat registry and the
problem attribute schema.

WHY THIS EXISTS. The Council of Experts is nominated per problem rather
than fixed (founder order, 2026-09-18: "the mixture of experts and sub
agents... will be nominated by the orchestrator to be part of the COE").
A dynamic council whose membership lives inside the nominator's code is
not actually dynamic: adding a seat would mean editing the code that
decides who gets nominated, which is the coupling docs/plan/COE-SUBSYSTEM-
WBS.md section 2 exists to forbid. This module is the one place a seat is
declared, as DATA (docs/plan/COE-SEATS.json), so scripts/coe_nominate.py
(COE-02, not this unit) can be written once and never touched again to add
a ninth seat.

THE CENTRAL RULE THIS MODULE ENFORCES: an unknown problem attribute is
refused, never ignored. A seat claiming to own an attribute outside
KNOWN_ATTRIBUTES fails the WHOLE REGISTRY to load, and the raised error
names both the seat and the attribute. The alternative, silently dropping
the unrecognised attribute, would produce a seat that owns nothing and is
therefore nominated for nothing, with nobody told (COE-SUBSYSTEM-WBS.md
rule D2). The same intolerance applies to every other malformed shape a
permissive loader would let through: a required field missing, a criterion
id outside C1 to C12, two seats sharing one id, a registry file that is not
a JSON list. See load_seats() below for the exhaustive list.

CONTINGENCY: how this fails, and what a caller does about it. Every
failure path in this module raises RegistryError; none of them returns an
empty list, an empty dict, or None as a stand in for "could not tell you".
A registry that is missing, unreadable, corrupt, or internally invalid
(duplicate ids, an unknown attribute, a bad criterion id) is a fact about
this module's own vocabulary, not a fact about the problem being solved,
and it must stop whatever called it cold, the same way scripts/
orchestrator_protocol.py's ProtocolError stops a caller on a broken schema
file. A caller (COE-02's nominate(), a test, an interactive check) must
never catch RegistryError and proceed with a smaller or empty council: a
council that silently shrank to nobody because its own data could not be
read is exactly the failure mode this module exists to make impossible.
The one exception, and it is a deliberate one, not an oversight: a VALID,
readable registry that legitimately holds no seat matching some KNOWN
attribute is not a failure at all, it is real data, and seats_owning()
returns an empty list for it (see rule e in scripts/test_coe_registry.py
and the docstring on seats_owning() below). Empty and error mean opposite
things here, and conflating them is how a council silently shrinks to
nobody without anyone raising an alarm.

REUSE, NOT REIMPLEMENTATION: schema validation is done entirely by
scripts/orchestrator_protocol.py's validate(), the estate's one hand
rolled JSON Schema subset checker. This module never re-parses a JSON
Schema keyword itself; it only interprets orchestrator_protocol.validate()'s
returned problem list and layers the two registry-level checks that keyword
matching alone cannot express: the closed KNOWN_ATTRIBUTES set (a seat's
authoritative_on and effort ARE schema enums, checked there; owns_attributes
is deliberately not, so a new attribute can be taught to KNOWN_ATTRIBUTES
here without a schema version bump) and duplicate seat ids (JSON Schema has
no cross-item uniqueness keyword in the subset orchestrator_protocol.py
implements).

NO NETWORK, NO OUTSIDE MODEL CALL. This module reads two local files
(docs/schema/coe-seat-v1.json, docs/plan/COE-SEATS.json) and nothing else.
It imports only the standard library plus orchestrator_protocol, which is
itself standard library only (see that module's own docstring). A seat
records whether IT runs outside this vendor (its "outside" field); that is
a fact this module stores, never a call this module makes. The gate that
decides whether an outside seat's content may actually be sent anywhere is
COE-03 (scripts/coe_outside_gate.py), not this module: a registry that
phoned out itself could not be trusted by the gate that decides whether
phoning out is allowed.

EDGE LIST, walked explicitly (docs/plan/ORCH-1020-WORKER-CONTRACT.md's
standing law):
  - empty registry file (zero bytes, or a syntactically valid but empty
    JSON list): handled. A zero byte file fails json.load with a
    ValueError, caught and re-raised as RegistryError. An empty JSON list
    parses cleanly but is refused as a registry with no seats: a council
    that can never have a member is not a degenerate but valid case here,
    it is exactly as unusable as a missing file.
  - a registry holding exactly one seat: handled, and is the minimum
    valid case. Nothing in this module requires more than one seat; COE-04
    and COE-05 (not this unit) are what would make a one seat council
    scientifically weak, not this loader.
  - duplicate seat ids: handled. Checked after every seat individually
    passes schema validation, so the error always names the specific
    duplicated id rather than a generic schema complaint.
  - a seat whose model field is empty: handled by the schema itself
    (coe-seat-v1.json's model property carries minLength 1), so it comes
    back through the same path as any other missing-or-empty required
    field, not a special case in this module.
  - a corrupt or truncated JSON file: handled, raises RegistryError naming
    the path and the underlying ValueError.
  - a registry file that is valid JSON but not a list (for example a bare
    object, matching the WORK BREAKDOWN's own JSON files which are
    objects, not lists): handled, refused before any seat is even looked
    at.
  - out of scope, and named here rather than silently skipped: concurrent
    writers to docs/plan/COE-SEATS.json (this module only reads; a claim
    on that path, if one is ever needed, belongs to scripts/claim_store.py,
    not to a seat loader), and validating that a seat's declared "model"
    string names a model that actually exists and is reachable (that is
    COE-02's or COE-03's problem at nomination and dispatch time, not this
    module's: this registry is a static declaration, not a live catalog
    check, exactly as orchestrator-task-v1.json's worker_profile string is
    not validated against a live model catalog either).

Python 3.9 floor, standard library only, no network.
"""

import json
import os

import orchestrator_protocol

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REGISTRY_PATH = os.path.normpath(
    os.path.join(HERE, "..", "docs", "plan", "COE-SEATS.json"))

#: The schema name passed to orchestrator_protocol.load_schema()/validate().
#: Named once here so a caller never has to know the schema file's basename,
#: only that this module validates against the seat shape.
SCHEMA_NAME = "coe-seat-v1"

#: The closed set of problem attributes a seat may declare it owns, taken
#: from docs/plan/COE-SUBSYSTEM-WBS.md section 2's seat table (the "owns
#: attributes" column). This is the estate's one definition site for this
#: vocabulary; docs/schema/coe-seat-v1.json deliberately does NOT enum
#: constrain owns_attributes against this set (see that schema's own
#: description), so a ninth attribute can be taught to a future seat by
#: editing this frozenset alone, with no schema version bump. An attribute
#: outside this set fails load_seats() for the whole registry (rule a).
KNOWN_ATTRIBUTES = frozenset((
    "duplicate-truth", "boundaries", "component-reuse",
    "test-integrity", "theatre-resistance",
    "false-refusal", "liveness", "stalling",
    "observability", "measured-cost",
    "enforcement", "ownership", "flip-condition",
    "privacy", "trust-boundary", "leakage",
    "independent-family", "structural-critique",
    "high-volume-clustering", "counting",
))

#: This estate's effort tiers (CLAUDE.md's model consumption cap; the
#: --effort values scripts/or_ask.py's bridge accepts). Must equal
#: docs/schema/coe-seat-v1.json's effort enum exactly;
#: scripts/test_coe_registry.py asserts this so the two cannot drift apart
#: silently, mirroring scripts/test_orchestrator_protocol.py's
#: TestEnumsMatchInvariants pattern for orchestrator-task-v1.json.
KNOWN_EFFORTS = frozenset(("minimal", "low", "medium", "high", "xhigh"))

#: The twelve scoring criteria (docs/plan/COE-SUBSYSTEM-WBS.md section 1),
#: fixed by the standard and never edited by a seat. Must equal
#: docs/schema/coe-seat-v1.json's authoritative_on items enum exactly; see
#: KNOWN_EFFORTS above for why this is asserted by a test rather than
#: assumed.
KNOWN_CRITERIA = frozenset("C%d" % n for n in range(1, 13))


class RegistryError(Exception):
    """The registry file, or one seat inside it, could not be loaded as a
    valid Council of Experts seat. Covers a missing or unreadable file, a
    file that is not valid JSON, a file that is valid JSON but not a list,
    an empty list, a seat failing schema validation (a required field
    missing, an unknown effort, an unknown criterion id), a seat owning an
    attribute outside KNOWN_ATTRIBUTES, and two seats sharing one id.

    Never raised for a verdict this module considers ordinary, expected
    data: seats_owning() returning an empty list for a KNOWN attribute
    that no currently registered seat owns is not an error, it is the
    correct answer, and it is returned, not raised (see seats_owning()).
    """


def _fail(message):
    raise RegistryError(message)


def load_seats(path=None):
    """Every seat in the registry at `path` (defaults to
    docs/plan/COE-SEATS.json), each validated against docs/schema/
    coe-seat-v1.json via orchestrator_protocol.validate() and against this
    module's own KNOWN_ATTRIBUTES closed set and duplicate-id check.

    Returns a list of seat dicts, in file order, never empty: a registry
    file holding zero seats is refused (see the module docstring's edge
    list) rather than returned as an empty list, because an empty list
    here is indistinguishable from "the registry could not be read" to
    any caller that does not go looking for the difference, and this
    module's whole purpose is to make that difference impossible to miss.

    Raises RegistryError, never returns a partial or default result, on:
    a missing file, a file that is not valid JSON, a file that is valid
    JSON but not a list, an empty list, any seat failing schema
    validation, any seat owning an attribute outside KNOWN_ATTRIBUTES, or
    two seats sharing one id.
    """
    registry_path = path if path is not None else DEFAULT_REGISTRY_PATH
    if not os.path.isfile(registry_path):
        _fail("registry not found: %s" % registry_path)
    try:
        with open(registry_path, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as exc:
        _fail("registry is not valid JSON: %s: %s" % (registry_path, exc))
    if not isinstance(document, list):
        _fail("registry %s is not a JSON list of seats, got %s"
              % (registry_path, type(document).__name__))
    if len(document) == 0:
        _fail("registry %s holds no seats" % registry_path)

    seen_ids = set()
    seats = []
    for index, raw_seat in enumerate(document):
        problems = orchestrator_protocol.validate(raw_seat, SCHEMA_NAME)
        if problems:
            seat_id = raw_seat.get("id") if isinstance(raw_seat, dict) else None
            _fail("registry %s, seat %s (index %d) failed validation: %s"
                  % (registry_path, seat_id or "<no id>", index,
                     "; ".join(problems)))

        seat_id = raw_seat["id"]
        if seat_id in seen_ids:
            _fail("registry %s: duplicate seat id %r"
                  % (registry_path, seat_id))
        seen_ids.add(seat_id)

        for attribute in raw_seat["owns_attributes"]:
            if attribute not in KNOWN_ATTRIBUTES:
                _fail(
                    "registry %s, seat %r owns unknown attribute %r "
                    "(not in coe_registry.KNOWN_ATTRIBUTES)"
                    % (registry_path, seat_id, attribute))

        seats.append(raw_seat)

    return seats


def seat(seat_id, path=None):
    """The one seat in the registry whose id equals `seat_id`.

    Raises RegistryError when no seat has that id. Never returns None: a
    caller doing `if seat(x):` on a None result would silently treat "no
    such seat" as "a falsy seat", nominate nobody, and still report a
    council.
    """
    for one_seat in load_seats(path):
        if one_seat["id"] == seat_id:
            return one_seat
    _fail("unknown seat id: %r" % (seat_id,))


def seats_owning(attribute, path=None):
    """Every seat in the registry whose owns_attributes includes
    `attribute`, in file order.

    Returns an empty list when `attribute` is a member of KNOWN_ATTRIBUTES
    but no currently registered seat owns it: this is ordinary, expected
    data (a real gap in coverage, worth nominating a new seat for, but not
    a defect in this call), not an error.

    Raises RegistryError when `attribute` is not even in KNOWN_ATTRIBUTES:
    that is a caller mistake (a typo, or an attribute this registry has
    never heard of), and must never be silently answered with the same
    empty list a genuine coverage gap would produce. Conflating the two is
    exactly how a council silently shrinks to nobody without anyone
    noticing (rule e).
    """
    if attribute not in KNOWN_ATTRIBUTES:
        _fail("unknown problem attribute: %r (not in coe_registry."
              "KNOWN_ATTRIBUTES)" % (attribute,))
    return [one_seat for one_seat in load_seats(path)
            if attribute in one_seat["owns_attributes"]]


def _selftest():
    """Minimal smoke check, run directly (`python3 scripts/coe_registry.py`)
    without the full unittest suite: loads the real registry and prints a
    one line summary. Not a substitute for scripts/test_coe_registry.py,
    which is the actual done check; this exists only so a human staring at
    this file can eyeball that it still loads before running anything
    heavier.
    """
    loaded = load_seats()
    outside_ids = sorted(s["id"] for s in loaded if s["outside"])
    print("coe_registry: %d seats loaded, outside=%s"
          % (len(loaded), outside_ids))


if __name__ == "__main__":
    _selftest()
