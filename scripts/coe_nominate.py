"""COE-02 of the Council of Experts subsystem: deterministic nomination.

WHY THIS EXISTS. COE-01 (scripts/coe_registry.py) declares WHO could sit on
a council. This module decides WHO ACTUALLY DOES, for one problem, and is
the unit docs/plan/COE-SUBSYSTEM-WBS.md names as deciding whether the whole
subsystem is real: "a council nominated by judgement is a council that
agrees with whoever nominated it." Four properties make nomination
mechanical instead of a preference dressed up as one, and each is a test in
scripts/test_coe_nominate.py:

  D1 DETERMINISM      the same problem nominates the same council, always.
  D2 STRUCTURE         a seat is seated because the problem carries an
                        attribute that seat owns, never because it was
                        merely available, and the record names the
                        attribute.
  D3 NO SELF-REVIEW    a seat may not score work it authored.
  D4 FAMILY DIVERSITY  a high or critical risk problem needs at least two
                        distinct model families among the nominated seats,
                        or nomination REFUSES rather than proceeding with a
                        council that shares one blind spot.

THE ONE ENTRY POINT CALLERS USE: nominate(problem, seats=None, author=None,
outside_gate_terms=None) -> Council. Everything else in this module exists
to make that one call auditable and refusable.

HOW "MODEL FAMILY" IS DEFINED, AND WHY THAT IS A DECISION WORTH STATING.
A council's family is read verbatim from the nominated seat's own `model`
field in the registry (docs/plan/COE-SEATS.json): "opus", "sonnet", or "an
outside model" today. This is deliberate, not an oversight. The registry
(COE-01, which this unit may not edit) declares no separate "family" field,
only "model" and "outside". Inventing a second taxonomy on top of a field
that already exists would itself be a duplicate-truth problem, exactly what
the architect seat (owns_attributes: "duplicate-truth") exists to catch.
COE-SUBSYSTEM-WBS.md's own wording ("the outside lane exists PARTLY to
supply that diversity") says outside seats are one source of a second
family, not the only one, which is consistent with reading "opus" and
"sonnet" as two distinct families here: two Claude models at different
tiers can and do disagree, and nothing in the founder's order or the WBS
collapses every seat that happens to share a vendor into one family. If a
future need arises to group more coarsely (or more finely) than the literal
model string, that is a registry schema change (COE-01 adds a declared
"family" field), never a private mapping invented inside this module.

THE OUTSIDE GATE, CALLED, NEVER REIMPLEMENTED. An outside seat is only ever
nominated when the problem declares content_leaves_machine and the
problem's content passes scripts/coe_outside_gate.check(). This module
calls check(), never guard(): nominate() has no call to actually wire to
the scan (that is COE-04's job, scoring, not this unit's), it only needs
the verdict to decide membership. The scan runs once per problem, lazily,
the first time an outside seat is a candidate, and the same verdict is
reused for every outside seat that problem considers afterward: the content
leaving the machine is one fact about the PROBLEM, not a fact that changes
per seat asking, so scanning it twice would not make the second scan more
correct, only slower.

THE D4-VERSUS-GATE COLLISION, NAMED IN THE BRIEF, RESOLVED HERE. A high or
critical risk problem whose only path to a second model family is an
outside seat, when that seat's content fails the gate, cannot be rescued by
either rule bending: the gate does not warn and proceed (COE-03's own
contract), and D4 does not drop to one family because the second one was
unavailable for a good reason. The nomination REFUSES. NominationRefused
carries the refusals list that occurred before the refusal, so a caller can
see the outside seat's gate verdict as the reason its family was missing,
never a bare "could not satisfy D4."

EDGE LIST, WALKED EXPLICITLY (docs/plan/ORCH-1020-WORKER-CONTRACT.md's
standing law):
  - a problem with no attributes: handled, refused immediately with a
    reason naming that no attributes were declared, before any seat is
    even looked at (see the empty-attributes check in nominate()).
  - a problem with exactly one attribute: handled, the ordinary path.
  - a problem whose every attribute maps to the same single seat: handled,
    that seat is nominated once, and rationale records the FIRST attribute
    (in the problem's own attribute order) that matched it, never a list
    of every attribute it happens to own.
  - an attribute not in coe_registry.KNOWN_ATTRIBUTES: handled, refused by
    _validate_problem() before any nomination logic runs, naming the bad
    attribute, the same way coe_registry.seats_owning() refuses a caller
    mistake rather than silently returning an empty answer indistinguishable
    from "no seat currently owns this".
  - an unknown risk_class: handled, refused by _validate_problem().
  - author naming a seat that is not in the registry: handled as a no-op,
    deliberately, not an error. No seat's id will ever equal an id that
    names nobody, so nothing is excluded; this module does not validate
    `author` against the registry because a caller's own identity string
    is not required to correspond to a known seat (a human reviewer, a
    foreign identity), and treating an unrecognised author as an error
    would force every non-seat author to be blessed by this module, which
    is not this module's job.
  - author being None: handled, the default; D3 excludes nothing, since no
    seat id ever equals None.
  - a registry that loads zero seats: handled. coe_registry.load_seats()
    itself already guarantees a non-empty list or a raised RegistryError
    (see that module's own docstring), so this can only actually happen
    here when a caller passes `seats=[]` directly; nominate() checks for
    it anyway, as defence in depth, and refuses rather than silently
    nominating nobody.
  - a high-risk problem where the ONLY second family is an outside seat
    and the gate refuses it: handled, see "THE D4-VERSUS-GATE COLLISION"
    above. Refuses the whole nomination.
  - the same problem nominated twice by different callers: handled by
    construction. nominate() holds no mutable module-level state and reads
    no clock, so two callers (or the same caller twice) invoking it with
    an identical `problem` (and identical `seats`/`author` overrides, or
    both omitted) get an identical Council, every time.
  - a seat whose model is unavailable: OUT OF SCOPE, named rather than
    silently skipped, exactly as coe_registry.py declares validating a
    seat's model against a live catalog out of its own scope. Nomination
    is a pure function of the registry's DECLARED attributes; whether the
    declared model can actually be reached at dispatch time is scripts/
    model_worker.py's and the outside-model fallback chain's problem
    (CLAUDE.md's "OUTSIDE MODEL CALLS: FOUR FIXES"), never nomination's.

CONTINGENCY. Every unresolvable nomination RAISES NominationRefused; this
module never returns a partial or empty Council, because a partial council
that scores anyway is a council with a hole in it (the worker contract's
own words). A caller catching NominationRefused recovers by one of: adding
or fixing the problem's declared attributes, naming a different author,
supplying content that passes the outside gate (or dropping the outside
seat from contention by not declaring content_leaves_machine), or widening
the registry with a new seat that supplies the missing coverage or family.
Retrying the identical call is never a recovery: nomination is a pure
function of its inputs, so the identical inputs produce the identical
refusal (see D1's contingency in scripts/test_coe_nominate.py). The
refusal itself is silent to nobody: NominationRefused's message and
`.refusals` name exactly what was missing and, where relevant, which seat
was considered and why it did not count.

DETERMINISM, MECHANICALLY, NOT BY ASSERTION. This module never iterates a
`set` or a `dict` to decide the ORDER of anything it returns. Membership
sets (`nominated_set`, `refused_ids` below) exist only to answer "have I
already handled this seat id", never to produce output order; the actual
seat order comes from iterating `problem["attributes"]` (a list, in the
caller's own order) and, within one attribute, `all_seats` (a list, in
coe_registry's file order), both of which are ordinary list iteration and
carry no hash-randomization dependence. `families` is built through a set
only to de-duplicate, then immediately `sorted()`, so its output order is
alphabetical and independent of the set's internal (hash-seed-dependent)
iteration order. See scripts/test_coe_nominate.py's determinism tests for
why a same-process repeated-call loop alone cannot prove this and what this
module's test suite does instead.

Python 3.9 floor, standard library only, no network calls other than the
one coe_outside_gate.check() may make through its own configuration file
read (no network either; see that module).
"""

import coe_outside_gate
import coe_registry

#: The problem's declared severity. This vocabulary lives here, not in
#: coe_registry.KNOWN_ATTRIBUTES: risk_class is a property of the PROBLEM
#: being scored, never a property of a SEAT, and COE-01 is deliberately
#: scoped to seat-owned attributes only.
KNOWN_RISK_CLASSES = frozenset(("low", "medium", "high", "critical"))

#: Risk classes at which D4 (family diversity) applies. Below this floor a
#: single-family council is accepted: COE-SUBSYSTEM-WBS.md section 1 ties
#: the requirement explicitly to "risk class high or critical", never to
#: every problem.
RISK_CLASSES_REQUIRING_FAMILY_DIVERSITY = frozenset(("high", "critical"))

#: D4's floor, per COE-SUBSYSTEM-WBS.md's own wording: "at least two
#: distinct model families".
MIN_FAMILIES_FOR_HIGH_RISK = 2

#: The required keys of a `problem` dict. See _validate_problem().
_REQUIRED_PROBLEM_KEYS = ("id", "attributes", "risk_class",
                           "content_leaves_machine")


class NominationRefused(Exception):
    """The nomination could not produce a usable council. Covers a
    malformed problem (missing key, wrong type, unknown attribute, unknown
    risk_class), a registry holding zero seats, a problem whose attributes
    matched no seat that could actually be nominated (including the
    empty-attributes case), and a high or critical risk problem that could
    not reach two distinct model families, whether because no second
    family was ever a candidate or because the only candidate that would
    have supplied one was itself refused (an author match or an outside
    gate refusal).

    `.reason` is the same text as str(exc). `.refusals` is the list of
    refusal records accumulated before the refusal was raised (each a
    dict with "seat", "attribute" and "reason" keys, the same shape as
    Council.refusals): empty when the refusal happened before any seat was
    even considered (a malformed problem, an empty registry, no declared
    attributes). A caller inspecting a NominationRefused raised for a D4
    failure finds the seat that would have supplied the missing family,
    and exactly why it did not, in `.refusals`.
    """

    def __init__(self, reason, refusals=None):
        self.reason = reason
        self.refusals = list(refusals) if refusals else []
        super(NominationRefused, self).__init__(reason)


class Council(object):
    """One nomination's result. Immutable by convention (matching
    coe_outside_gate.GateResult): construct a new one rather than mutating
    an existing result.

    seats      tuple of seat id strings, in nomination order (the order in
               which the problem's own `attributes` list first caused each
               seat to be seated). D1 compares this as an ordered sequence,
               never as a set: the same MEMBERSHIP nominated in a different
               ORDER is still a determinism failure.
    rationale  dict of seat id -> the single attribute that caused that
               seat to be seated (the FIRST attribute, in the problem's own
               attribute order, that matched it; not every attribute the
               seat happens to also own). An audit trail names one cause.
    families   tuple of the distinct seat "model" values present among
               `seats`, sorted alphabetically, so this field never depends
               on set iteration order either.
    refusals   tuple of dicts, each {"seat", "attribute", "reason"}: every
               seat that was a candidate (it owned an attribute the problem
               declared) and was rejected, and why. A seat that owns none
               of the problem's declared attributes never appears here: it
               was never a candidate to reject.
    """

    __slots__ = ("seats", "rationale", "families", "refusals")

    def __init__(self, seats, rationale, families, refusals):
        self.seats = tuple(seats)
        self.rationale = dict(rationale)
        self.families = tuple(families)
        self.refusals = tuple(refusals)

    def __repr__(self):
        return (
            "Council(seats=%r, rationale=%r, families=%r, refusals=%r)"
            % (self.seats, self.rationale, self.families, self.refusals)
        )

    def __eq__(self, other):
        if not isinstance(other, Council):
            return NotImplemented
        return (
            self.seats == other.seats
            and self.rationale == other.rationale
            and self.families == other.families
            and self.refusals == other.refusals
        )


def _fail(reason, refusals=None):
    raise NominationRefused(reason, refusals)


def _validate_problem(problem):
    """`problem`, checked against the shape nominate() requires.

    Raises NominationRefused (never returns a patched-up substitute, never
    silently ignores a bad field) on any shape defect. This is a
    malformed-input refusal rather than a business-rule refusal, but this
    module deliberately exposes exactly one exception type (see the module
    docstring's CONTINGENCY section), so it is raised the same way every
    other unresolvable nomination is: the caller's recovery is the same
    shape either way, read `.reason`.
    """
    if not isinstance(problem, dict):
        _fail("problem must be a dict, got %s" % type(problem).__name__)

    for key in _REQUIRED_PROBLEM_KEYS:
        if key not in problem:
            _fail("problem is missing required key %r" % (key,))

    attributes = problem["attributes"]
    if not isinstance(attributes, list):
        _fail("problem[\"attributes\"] must be a list, got %s"
              % type(attributes).__name__)
    for attribute in attributes:
        if attribute not in coe_registry.KNOWN_ATTRIBUTES:
            _fail(
                "problem declares unknown attribute %r (not in "
                "coe_registry.KNOWN_ATTRIBUTES)" % (attribute,)
            )

    risk_class = problem["risk_class"]
    if risk_class not in KNOWN_RISK_CLASSES:
        _fail(
            "problem declares unknown risk_class %r (must be one of %s)"
            % (risk_class, sorted(KNOWN_RISK_CLASSES))
        )

    if not isinstance(problem["content_leaves_machine"], bool):
        _fail(
            "problem[\"content_leaves_machine\"] must be a bool, got %s"
            % type(problem["content_leaves_machine"]).__name__
        )


def nominate(problem, *, seats=None, author=None, outside_gate_terms=None):
    """The Council nominated for `problem`.

    `problem` is a dict carrying at least: id, attributes (a list drawn
    from coe_registry.KNOWN_ATTRIBUTES), risk_class (low, medium, high,
    critical), content_leaves_machine (bool), and optionally content (the
    text an outside seat would actually see, required only when an
    outside seat is a candidate; see coe_outside_gate for its shape).

    `seats`, when given, replaces coe_registry.load_seats() as the seat
    pool: this is how a test (or a caller with its own already-loaded
    registry) supplies seats without touching disk. It is trusted as
    already valid per docs/schema/coe-seat-v1.json; this function does not
    re-validate seat shape, which is COE-01's job (re-checking it here
    would be the exact duplicate-truth failure the architect seat exists
    to catch). When omitted (None), coe_registry.load_seats() is called,
    which enforces that validation itself.

    `author`, when given, is a seat id that is never nominated (D3); a
    value that names no seat in the pool is a documented no-op, not an
    error (see the module docstring's edge list).

    `outside_gate_terms`, when given, is passed through verbatim to
    coe_outside_gate.check() exactly as that module's own tests pass
    `terms=`; None (the default) means the real production term list is
    read from disk exactly as check() does by default. This exists purely
    so a test can supply a fake term set without touching the real
    ~/.claude/coe-outside-gate-terms.json, the same way seats= exists so a
    test need not touch the real registry file.

    Returns a Council. Raises NominationRefused, never a partial or empty
    Council, when the problem is malformed, the seat pool is empty, no
    seat could actually be nominated, or (for risk_class high or critical)
    the nominated seats cannot reach two distinct model families.
    """
    _validate_problem(problem)

    all_seats = seats if seats is not None else coe_registry.load_seats()
    if not all_seats:
        _fail("seat pool holds zero seats; cannot nominate a council "
              "against nobody")

    attributes = problem["attributes"]
    risk_class = problem["risk_class"]
    content_leaves_machine = problem["content_leaves_machine"]
    content = problem.get("content")

    if not attributes:
        _fail(
            "problem %r declares no attributes; nomination requires at "
            "least one attribute to match a seat against"
            % (problem.get("id"),)
        )

    seats_by_id = {}
    for seat_obj in all_seats:
        seats_by_id[seat_obj["id"]] = seat_obj

    nominated_ids = []      # order-preserving: this becomes Council.seats
    nominated_set = set()   # membership only, never iterated for order
    rationale = {}
    refusals = []
    refused_ids = set()     # membership only, never iterated for order

    # The outside gate's verdict is a fact about the PROBLEM's content, not
    # about which seat is asking, so it is computed at most once and reused
    # for every outside seat this problem considers. A one-element list
    # stands in for a nonlocal cache cell (Python 2/3 compatible without
    # the `nonlocal` keyword); see coe_outside_gate's own module docstring
    # for why the scan and the decision it gates must never be pulled apart
    # into two separately-callable steps.
    gate_result_cache = []

    def _outside_gate_result():
        if not gate_result_cache:
            if content is None:
                verdict = coe_outside_gate.GateResult(
                    False, 0,
                    "problem declares content_leaves_machine but supplies "
                    "no content to scan; refusing rather than treating "
                    "absent content as clean",
                    [],
                )
            else:
                verdict = coe_outside_gate.check(
                    content, terms=outside_gate_terms)
            gate_result_cache.append(verdict)
        return gate_result_cache[0]

    for attribute in attributes:
        # List iteration over all_seats, in coe_registry's own file order
        # (or the caller-supplied seats= order): never a set, never a
        # dict's arbitrary iteration, so this loop's visiting order is the
        # same every time this exact `all_seats` is passed in.
        owners = [s for s in all_seats if attribute in s["owns_attributes"]]
        for seat_obj in owners:
            seat_id = seat_obj["id"]
            if seat_id in nominated_set or seat_id in refused_ids:
                continue

            if seat_id == author:
                refusals.append({
                    "seat": seat_id,
                    "attribute": attribute,
                    "reason": (
                        "seat is the author of the work under review; a "
                        "seat may not score its own methodology (D3)"
                    ),
                })
                refused_ids.add(seat_id)
                continue

            if seat_obj["outside"]:
                if not content_leaves_machine:
                    refusals.append({
                        "seat": seat_id,
                        "attribute": attribute,
                        "reason": (
                            "seat is outside and problem does not declare "
                            "content_leaves_machine; an outside seat may "
                            "only be nominated when its content leaves "
                            "the machine and passes the outside gate"
                        ),
                    })
                    refused_ids.add(seat_id)
                    continue
                gate_result = _outside_gate_result()
                if not gate_result.allowed:
                    refusals.append({
                        "seat": seat_id,
                        "attribute": attribute,
                        "reason": "outside gate refused: %s"
                                  % gate_result.reason,
                    })
                    refused_ids.add(seat_id)
                    continue

            nominated_ids.append(seat_id)
            nominated_set.add(seat_id)
            rationale[seat_id] = attribute

    if not nominated_ids:
        _fail(
            "problem %r matched no seat that could be nominated (every "
            "candidate attribute either has no owning seat, or every "
            "owning seat was refused); see refusals" % (problem.get("id"),),
            refusals,
        )

    # A set only to de-duplicate; sorted() immediately after makes the
    # output order alphabetical and independent of the set's own
    # (hash-seed-dependent) iteration order. See the module docstring's
    # DETERMINISM section.
    families = sorted(set(seats_by_id[sid]["model"] for sid in nominated_ids))

    if (risk_class in RISK_CLASSES_REQUIRING_FAMILY_DIVERSITY
            and len(families) < MIN_FAMILIES_FOR_HIGH_RISK):
        _fail(
            "problem %r is risk_class %r and requires at least %d "
            "distinct model families among nominated seats (D4); only %d "
            "found (%s). Nominated: %s. Refused: %s"
            % (
                problem.get("id"), risk_class, MIN_FAMILIES_FOR_HIGH_RISK,
                len(families), families, nominated_ids,
                [r["seat"] for r in refusals],
            ),
            refusals,
        )

    return Council(nominated_ids, rationale, families, refusals)
