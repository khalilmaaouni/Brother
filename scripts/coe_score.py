#!/usr/bin/env python3
"""COE-04 of the Council of Experts subsystem: independent scoring.

WHY THIS EXISTS. COE-02 (scripts/coe_nominate.py) decides WHO sits on the
council for one problem. This module decides WHAT each seat says, one
criterion at a time, and hands the result to COE-05 (scripts/
coe_arbitrate.py) as the matrix that module's own docstring says arbitrate()
expects: an iterable of {"seat", "criterion", "score"} cells. The one
property that makes any of this worth building is named in
docs/plan/COE-SUBSYSTEM-WBS.md's own words: "a council nominated by vibe is
a council that agrees with whoever nominated it," and the scoring stage is
where that vibe would actually leak in, seat by seat, if a later seat could
see an earlier seat's answer before giving its own. ISOLATION is this
module's whole job; arbitration is COE-05's, and this module never resolves
disagreement, never applies A1 through A5, and never decides PASS or REJECT.

THE ONE ENTRY POINT: score_council(council, methodology, *, ask, criteria)
-> ScoreMatrix (a list subclass; pass it straight to coe_arbitrate.arbitrate()
as its `matrix` argument, and it works, because arbitrate() only ever calls
list(matrix) and reads each element as a plain {"seat","criterion","score"}
mapping).

`council` is anything exposing a `.seats` attribute that is an iterable of
seat id strings, in nomination order (coe_nominate.Council's own shape), OR
a bare iterable of seat id strings. This module does not import
coe_nominate: nomination has already happened by the time scoring starts,
and this module only needs the seat ids it produced, not the machinery that
produced them. Importing coe_nominate here for a type check nobody asked for
would itself be the duplicate-truth failure the architect seat exists to
catch (COE-SUBSYSTEM-WBS.md section 1, D2's own note about the registry).

`methodology` is opaque to this module: whatever text or structure describes
the control under review, handed verbatim into every prompt this module
builds. This module never inspects it beyond that.

`ask` is the injected callable that actually puts one question to one seat:

    ask(seat_id: str, criterion: str, prompt: str) -> answer

called exactly once per (seat, criterion) pair this module decides to ask.
`prompt` is built by this module from `methodology` and `criterion` alone
(see _build_prompt below): it never contains another seat's id, score, or
reasoning, which is what makes isolation a property of the CALL SHAPE rather
than a promise about what some model does with what it is told. `answer` is
interpreted by _interpret_answer() below, never by this module's callers:
an int in [0, 10] (bool excluded) is a score, the exact string "NO-DATA" is
a permitted non-score, and everything else (prose, None, a float, a dict, an
out-of-range or non-integer number) is a FAILURE for that one cell, not a
crash and not an inferred score. `ask` may also raise: caught per call, same
outcome as a FAILURE.

`criteria` is the closed set of criterion names to ask every seat about
(mirrors coe_arbitrate.arbitrate()'s own `criteria` parameter, which is
REQUIRED there for exactly the same reason it is required here: a default
would stop tracking the moment the standard changes).

WHAT A CALLER DOES WITH THE RETURN VALUE. score_council() always returns a
ScoreMatrix, or raises ScoringError before asking anyone anything (bad
`council`/`criteria` shape). It never raises because some seat's answer was
bad: that is what FAILURE cells are for. The returned ScoreMatrix carries
`.failures`, a list of {"seat", "criterion", "reason"} records for every
(seat, criterion) pair that was asked but produced no cell, purely for
audit and debugging; arbitrate() never reads `.failures` and does not need
to, because a cell missing from the matrix is exactly how arbitrate()
already expects a recusal or a dead seat to look (its own docstring: "a seat
named here that never appears in the matrix simply contributed nothing").

CONTINGENCY. A missing cell fails DOWNWARD, never upward: a criterion no
surviving seat could score for becomes MISSING inside arbitrate(), which
caps the verdict at CEILING or REJECT, never at PASS (coe_arbitrate.py's own
A4 and its closed-criteria-set rule). So a partial matrix, produced when one
seat dies, times out, or answers badly on some cells, is not an error this
module needs to raise: it is the correct, legitimate output, because the
alternative (crashing the whole run over one seat's one bad answer) would
throw away every other seat's real, isolated, independent work over a single
failure that arbitrate() is already designed to absorb. A caller holding a
ScoreMatrix with entries in `.failures` decides, same as any other CEILING,
whether to renominate a replacement seat for the affected criterion or
accept the ceiling; retrying score_council() with the identical `ask` is
only useful if the failure was transient (a timeout), never if it was a
malformed answer, since a malformed answer is deterministic in what it
means (a bad seat, not a bad die roll).

THE BAD STATE A GREEN RUN WOULD ALSO PASS, NAMED SO IT IS NEVER SHIPPED
ACCIDENTALLY: a stub `ask` that returns the SAME score for every seat and
every criterion makes isolation trivially true, because there is nothing
distinct to leak. scripts/test_coe_score.py's isolation and order tests
therefore give every seat a DISTINCT score per criterion and assert those
exact distinct values survive into the matrix attributed to the correct
seat; a stub that collapsed every seat to one shared value would make every
assertion in this file pass while proving nothing.

EDGE LIST, walked explicitly (docs/plan/ORCH-1020-WORKER-CONTRACT.md's
standing law):
  - an empty council (zero seats): refused before any `ask` call, raises
    ScoringError. A council of nobody cannot produce a matrix arbitrate()
    could resolve to anything but the same refusal arbitrate() itself gives
    an empty `seats`.
  - a council of exactly one seat: handled, the ordinary path; nothing here
    requires more than one seat (that would be D4's job, at nomination
    time, already past by the time scoring runs).
  - a seat appearing twice in `council.seats`: DECIDED HERE, deduplicated,
    first occurrence kept, in iteration order. This module asks each unique
    seat id exactly once per criterion; asking the same seat id twice for
    the same criterion could only ever produce either an identical cell
    (redundant work) or two DIFFERENT cells for the same (seat, criterion)
    key, and the latter is exactly the shape coe_arbitrate.py's own
    _validate_matrix() raises ArbitrationError on ("duplicate matrix entry
    for seat ... criterion ..."). Deduplicating at the council boundary,
    before a single `ask` call is made, is cheaper and more honest than
    generating a matrix that is guaranteed to crash arbitrate() and cannot
    represent "this seat said two different things" in any case, since a
    seat is one actor and one actor does not hold two simultaneous opinions.
  - a criteria list that is empty: refused before any `ask` call, raises
    ScoringError. There is nothing to ask about.
  - `ask` raising on the first call: caught for that one (seat, criterion)
    pair only, recorded in `.failures`, and every other pair (including the
    rest of that same seat's criteria) is still asked. See EdgeList tests.
  - `ask` returning None: falls through _interpret_answer()'s final branch
    (not NO-DATA, not a valid int) and becomes a FAILURE, never a score of
    0 or a silently dropped question.
  - `ask` returning a score for a criterion nobody asked about: cannot
    happen through the shape this module accepts. Every `ask` call is for
    exactly one named criterion, and its return value is interpreted as a
    single scalar (int or the string "NO-DATA"); a caller-supplied `ask`
    that tries to smuggle a different criterion in, for example by
    returning a dict such as {"criterion": "C9", "score": 7} when this
    module asked about C5, is simply not a valid scalar answer, and is
    refused as a FAILURE by _interpret_answer()'s final branch exactly like
    any other malformed answer. This module never inspects a returned
    structure for a "which criterion is this really about" field: doing so
    would mean trusting the seat's own claim about what it answered, which
    is the same "asking politely" this whole subsystem exists to refuse.
  - a seat returning scores for only some criteria: handled by construction
    of the seat x criteria loop: each (seat, criterion) pair is asked and
    resolved independently, so a seat whose answer fails or is malformed
    for one criterion still gets asked, and still gets a real cell, for
    every other criterion.
  - the same seat scoring the same criterion twice with different values:
    cannot arise from this module's own call structure (each unique seat is
    asked each criterion exactly once; see the duplicate-seat bullet
    above), which is the decision: refused by construction rather than
    accepted and reconciled, because reconciling two different answers from
    one actor is an arbitration-shaped question (closer to A2) that this
    module, which does no cross-seat reasoning at all, has no standard to
    resolve it against.

DETERMINISM AND ORDER INDEPENDENCE. This module iterates `council.seats` in
whatever order the caller supplies, and `criteria` in whatever order the
caller supplies, but the CONTENTS of the returned ScoreMatrix (as a set of
(seat, criterion, score) triples) do not depend on that order: nothing in
this module carries state between one (seat, criterion) call and the next,
each call's `prompt` is built fresh from only `methodology` and `criterion`,
and no seat's call can observe an earlier call's answer because this module
never puts one in the arguments it hands to `ask`. scripts/test_coe_score.py
proves this by scoring the same council in reverse seat order and asserting
the resulting matrices are equal as sets, which is the strongest evidence
available that a seat's answer is a function of (seat, criterion,
methodology) alone, since an anchored, order-sensitive implementation would
disagree with itself the moment the order changed.

Python 3.9 floor, standard library only, no network, no clock, no I/O. This
module does not schedule, dispatch, nominate, or arbitrate; it turns one
council and one methodology into one matrix and returns.
"""

NO_DATA = "NO-DATA"
MIN_SCORE = 0
MAX_SCORE = 10


class ScoringError(ValueError):
    """`council` or `criteria` was not a shape this module can score at
    all: no seats, no criteria, or a `council` with no usable seat list.
    Never raised because of what a seat answered; see the module docstring's
    CONTINGENCY section for why a bad ANSWER is a FAILURE cell, not this
    exception.
    """


class ScoreMatrix(list):
    """The cells score_council() produced, in the exact shape
    coe_arbitrate.arbitrate()'s own `matrix` parameter expects (a list is
    already what arbitrate() turns its `matrix` argument into internally,
    so subclassing list rather than inventing a container is the whole
    point: no second matrix type, one thing arbitrate() already reads).

    `.failures` is a list of {"seat", "criterion", "reason"} records for
    every (seat, criterion) pair that was asked but produced no cell here.
    Purely for audit: arbitrate() never reads it and does not need to.
    """

    def __init__(self, cells):
        super().__init__(cells)
        self.failures = []


def _seat_ids(council):
    """The unique seat ids in `council`, in first-seen order.

    Accepts anything with a `.seats` attribute (coe_nominate.Council's own
    shape) or a bare iterable of seat id strings, duck-typed so this module
    never has to import coe_nominate just to read a list of strings off it.
    """
    raw = getattr(council, "seats", council)
    seen = set()
    ordered = []
    for seat_id in raw:
        if not isinstance(seat_id, str) or not seat_id:
            raise ScoringError(
                "council contains a non-string or empty seat id: %r" % (seat_id,)
            )
        if seat_id in seen:
            continue
        seen.add(seat_id)
        ordered.append(seat_id)
    return ordered


def _validate_criteria(criteria):
    criteria_list = list(criteria)
    for criterion in criteria_list:
        if not isinstance(criterion, str) or not criterion:
            raise ScoringError(
                "criteria contains a non-string or empty entry: %r" % (criterion,)
            )
    return criteria_list


def _build_prompt(seat_id, criterion, methodology):
    """The exact text handed to `ask` for one (seat, criterion) question.

    Deliberately built from only `criterion` and `methodology`: never from
    any other seat's id, score, or reasoning, and never from this seat's
    own prior answers (there are none; each criterion is asked once). This
    is what makes ISOLATION a property of what this function cannot
    contain, not a promise about what happens downstream of it.
    """
    return (
        "You are seat %r, scoring one methodology against exactly one "
        "criterion. Do not reference any other seat or scorer; none of "
        "their answers are available to you and none exist yet in this "
        "process.\n\n"
        "CRITERION: %s\n\n"
        "METHODOLOGY UNDER REVIEW:\n%s\n\n"
        "Respond with either an integer from 0 to 10, or the exact string "
        "NO-DATA if you cannot score this criterion. Do not respond with "
        "prose; a prose answer is scored as a failure, never interpreted."
        % (seat_id, criterion, methodology)
    )


def _interpret_answer(answer):
    """(ok, value) for a usable answer, or (fail, reason) for anything else.

    Exactly three usable shapes: the literal string "NO-DATA", or a
    non-bool int in [0, 10]. Every other shape, including prose, None, a
    float, a bool, an out-of-range or non-integer number, or any structured
    value, is a failure with a reason naming why, never an inferred score.
    """
    if isinstance(answer, str):
        if answer == NO_DATA:
            return True, NO_DATA
        return False, "seat returned prose, not a score: %r" % (answer,)
    if isinstance(answer, bool):
        return False, "seat returned a bool, never coerced to a score: %r" % (answer,)
    if isinstance(answer, int):
        if MIN_SCORE <= answer <= MAX_SCORE:
            return True, answer
        return False, "score %r is outside %d to %d" % (answer, MIN_SCORE, MAX_SCORE)
    return False, "seat returned a non-score value of type %s: %r" % (
        type(answer).__name__,
        answer,
    )


def score_council(council, methodology, *, ask, criteria):
    """Score every seat in `council` against every criterion in `criteria`,
    independently, and return the resulting ScoreMatrix.

    Raises ScoringError before calling `ask` at all when `council` holds no
    seats or `criteria` is empty. Never raises because of what `ask`
    returns or raises for one (seat, criterion) pair: see the module
    docstring's CONTINGENCY section for why that is a FAILURE cell instead.
    """
    seat_ids = _seat_ids(council)
    if not seat_ids:
        raise ScoringError("council holds no seats; cannot score against nobody")

    criteria_list = _validate_criteria(criteria)
    if not criteria_list:
        raise ScoringError("criteria is empty; nothing to score against")

    cells = []
    failures = []

    for seat_id in seat_ids:
        for criterion in criteria_list:
            prompt = _build_prompt(seat_id, criterion, methodology)
            try:
                answer = ask(seat_id, criterion, prompt)
            except Exception as exc:  # noqa: BLE001 - a seat's own failure, recorded not raised
                failures.append(
                    {
                        "seat": seat_id,
                        "criterion": criterion,
                        "reason": "ask raised %s: %s" % (type(exc).__name__, exc),
                    }
                )
                continue

            ok, value = _interpret_answer(answer)
            if not ok:
                failures.append(
                    {"seat": seat_id, "criterion": criterion, "reason": value}
                )
                continue

            cells.append({"seat": seat_id, "criterion": criterion, "score": value})

    matrix = ScoreMatrix(cells)
    matrix.failures = failures
    return matrix
