#!/usr/bin/env python3
"""COE-05 of the Council of Experts subsystem: turn a score matrix into one verdict.

WHY THIS EXISTS. A council of expert seats scores a proposed methodology
against twelve criteria, seats down, criteria across. Averaging that matrix
into one number is how a control that is excellent on eleven criteria and
weak on one gets called a 9.4 and ships anyway; that exact failure mode is
recorded in this estate under the largest single class of false-green
findings. This module refuses to average. It applies five fixed rules
(A1 to A5, named after the rows in COE-SUBSYSTEM-WBS.md section 3) to turn a
matrix into exactly one outcome, and it refuses rather than guesses on any
input it cannot resolve under those rules.

THE FIVE RULES.

  A1. The score is the minimum criterion score, never an average.
  A2. Within a criterion, disagreement resolves downward: the lowest score
      stands unless the higher scorer has answered the low scorer's specific
      objection, because a high score is a claim of ABSENCE and absence is
      the harder claim to defend, so the burden sits with the optimist.
  A3. The authoritative seat's score on its own criterion is not outvoted by
      a majority, including when the authoritative seat's own answer is
      NO-DATA: authority means nobody else's number substitutes for its
      silence either.
  A4. NO-DATA is never coerced to a number and never ignored. A criterion no
      seat could score caps the verdict below PASS.
  A5. Unanimous 10 across every seat and every criterion is itself a finding
      (a control nobody could fault has usually not been understood), not a
      passing result. A single 9 anywhere in an otherwise-perfect matrix does
      not trigger this: the rule is specific to total unanimity, not a
      general suspicion of high scores.

THREE DISTINCT RESOLVED STATES, never conflated. A criterion resolves to
exactly one of: an int in [0, 10] (measured), the string "NO-DATA" (a seat
was asked and could not score it), or the string "MISSING" (no cell in the
matrix even asked; nobody was seated for it, or a seat that should have
answered never returned). Conflating MISSING with NO-DATA hides the
difference between "we looked and could not tell" and "we never looked",
and conflating either with 0 turns an absence of evidence into a measured
failure. This distinction was added after the first build of this module
shipped with the criteria universe implicitly derived from the matrix
itself, which meant a matrix scoring three of twelve criteria at a perfect
10 could reach PASS with the other nine simply never asked: exactly the
false-green shape this council exists to catch, reproduced inside the
mechanism meant to catch it. A seat timing out, returning malformed output,
or a nomination that silently seats nobody for a criterion are all expected
operating conditions that produce MISSING, not exceptional ones.

INPUT SHAPE, chosen so a duplicate score cell is representable and therefore
catchable rather than silently overwritten by dict-key collision:

  matrix: an iterable of cell records, each a mapping with exactly the keys
      "seat", "criterion", "score". "score" is either the exact string
      "NO-DATA" or an int in [0, 10] (bool is rejected: it is a subclass of
      int in Python and accepting it would silently score True as 1).
  seats: an iterable of the seat ids nominated for this problem. A seat
      named in the matrix that is not in this set is unknown input and
      raises; a seat named here that never appears in the matrix simply
      contributed nothing (a recusal), which is allowed.
  criteria: REQUIRED, an iterable of the closed set of criterion names the
      methodology is being judged against (today, C1 through C12). Required
      rather than defaulted on purpose: a default of C1-C12 would be
      convenient today and would silently stop tracking the moment the
      standard changes, exactly the failure this estate has already
      recorded once as a module constant bound into a default argument,
      where a later override of the constant had no effect because the
      default had already captured the old value at definition time. A
      matrix cell naming a criterion outside this set raises: the matrix and
      the standard disagree about what is being judged, and silently
      dropping the extra is how a standard drifts. A criterion in this set
      that no cell in the matrix mentions resolves to "MISSING", not
      "NO-DATA" and not 0, and its presence makes PASS unreachable.
  authoritative_map: a mapping of criterion -> seat id, the seat authoritative
      on that criterion per A3. A criterion or seat named here that the
      matrix cannot support (the seat never scored that exact criterion) is
      unresolvable authority and raises, rather than silently falling back to
      majority resolution, which would make the authority rule advisory.
  objections: optional iterable of raise records, each a mapping with keys
      "id" (unique string), "criterion", "seat" (the higher scorer whose
      raise is being justified), "objection" (the low scorer's concern, free
      text), and optionally "answer" (the high scorer's rebuttal, free
      text). A raise is permitted only when "answer" is present and contains
      the record's own "id" as a substring: a mechanical, deterministic
      stand-in for "the answer references the objection" that a caller can
      satisfy by literally citing the record it is responding to. An
      objection is a pending, unanswered raise: recorded, but it does not by
      itself lift the score.

CONTINGENCY. Every malformed input raises ArbitrationError; there is no
partial verdict and no permissive default for an unrecognised seat,
criterion, score value, or objection. arbitrate() either returns a complete
Verdict or it raises before computing anything. A caller that catches
ArbitrationError fixes the matrix (or the seats, criteria, map, or
objections that produced the bad reference) and calls again; it never
retries the same input expecting a different answer, and it never reads the
exception as license to treat the criterion as passed, failed, or scored
zero.

Python 3.9 floor, standard library only, no network, no clock, no I/O. This
module does not schedule, dispatch, merge, or mark anything done; it scores
one already-assembled matrix and returns.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union

NO_DATA = "NO-DATA"
MISSING = "MISSING"
MIN_SCORE = 0
MAX_SCORE = 10
PASS_THRESHOLD = 9

OUTCOME_PASS = "PASS"
OUTCOME_REJECT = "REJECT"
OUTCOME_CEILING = "CEILING"
OUTCOME_EXTRA_ROUND_REQUIRED = "EXTRA_ROUND_REQUIRED"
OUTCOMES = frozenset(
    {OUTCOME_PASS, OUTCOME_REJECT, OUTCOME_CEILING, OUTCOME_EXTRA_ROUND_REQUIRED}
)

Score = Union[int, str]


class ArbitrationError(ValueError):
    """A matrix, seat list, authority map, or objection set that this module
    refuses to arbitrate. Raised instead of any permissive default; see the
    CONTINGENCY section of the module docstring for what a caller does next.
    """


@dataclass(frozen=True)
class Verdict:
    outcome: str
    score: Optional[int]
    capping_criterion: Optional[str]
    per_criterion: Dict[str, Score]
    reason: str
    raises_permitted: Dict[str, dict] = field(default_factory=dict)
    missing_criteria: List[str] = field(default_factory=list)


def _is_valid_score_value(value) -> bool:
    if value == NO_DATA and isinstance(value, str):
        return True
    if isinstance(value, bool):
        return False
    if not isinstance(value, int):
        return False
    return MIN_SCORE <= value <= MAX_SCORE


def _validate_matrix(matrix, seats, criteria_set):
    """Walk the raw cell list once, raising on the first structural defect.

    Returns rows: Dict[seat, Dict[criterion, score]], with the collision
    check (duplicate seat+criterion cell) done before folding into that dict,
    since a plain dict-of-dicts fold would otherwise let the second cell
    silently overwrite the first.
    """
    cells = list(matrix)
    if not cells:
        raise ArbitrationError("empty matrix: nothing to arbitrate")

    seen = set()
    rows: Dict[str, Dict[str, Score]] = {}
    for cell in cells:
        if not isinstance(cell, dict):
            raise ArbitrationError(f"matrix cell is not a mapping: {cell!r}")
        missing = {"seat", "criterion", "score"} - set(cell.keys())
        if missing:
            raise ArbitrationError(f"matrix cell missing keys {sorted(missing)}: {cell!r}")
        seat = cell["seat"]
        criterion = cell["criterion"]
        score = cell["score"]
        if not isinstance(seat, str) or not seat:
            raise ArbitrationError(f"matrix cell has a non-string or empty seat: {cell!r}")
        if not isinstance(criterion, str) or not criterion:
            raise ArbitrationError(f"matrix cell has a non-string or empty criterion: {cell!r}")
        if seat not in seats:
            raise ArbitrationError(
                f"matrix scores seat {seat!r}, which is not in the nominated seats {sorted(seats)}"
            )
        if criterion not in criteria_set:
            raise ArbitrationError(
                f"matrix scores criterion {criterion!r}, which is not in the declared "
                f"criteria set {sorted(criteria_set)}: the matrix and the standard "
                f"disagree about what is being judged"
            )
        if not _is_valid_score_value(score):
            raise ArbitrationError(
                f"seat {seat!r} criterion {criterion!r}: score {score!r} is neither "
                f"'{NO_DATA}' nor an int in [{MIN_SCORE}, {MAX_SCORE}]"
            )
        key = (seat, criterion)
        if key in seen:
            raise ArbitrationError(
                f"duplicate matrix entry for seat {seat!r} criterion {criterion!r}"
            )
        seen.add(key)
        rows.setdefault(seat, {})[criterion] = score
    return rows


def _validate_seats(seats):
    seat_list = list(seats)
    if not seat_list:
        raise ArbitrationError("seats is empty: no council to arbitrate for")
    for seat in seat_list:
        if not isinstance(seat, str) or not seat:
            raise ArbitrationError(f"seats contains a non-string or empty entry: {seat!r}")
    return frozenset(seat_list)


def _validate_criteria(criteria):
    criteria_list = list(criteria)
    if not criteria_list:
        raise ArbitrationError("criteria is empty: no standard to judge against")
    for criterion in criteria_list:
        if not isinstance(criterion, str) or not criterion:
            raise ArbitrationError(f"criteria contains a non-string or empty entry: {criterion!r}")
    return frozenset(criteria_list)


def _validate_authoritative_map(authoritative_map, rows, seats):
    if authoritative_map is None:
        return {}
    if not isinstance(authoritative_map, dict):
        raise ArbitrationError("authoritative_map must be a mapping of criterion to seat")
    for criterion, seat in authoritative_map.items():
        if not isinstance(criterion, str) or not criterion:
            raise ArbitrationError(f"authoritative_map has a bad criterion key: {criterion!r}")
        if seat not in seats:
            raise ArbitrationError(
                f"authoritative_map names seat {seat!r} for criterion {criterion!r}, "
                f"which is not in the nominated seats"
            )
        if criterion not in rows.get(seat, {}):
            raise ArbitrationError(
                f"authoritative_map names seat {seat!r} as authoritative on "
                f"criterion {criterion!r}, but that seat never scored that criterion "
                f"(no seat scoring it at all is not the same as an authority backing "
                f"its own silence: score it as NO-DATA explicitly if that is meant)"
            )
    return dict(authoritative_map)


def _validate_objections(objections, rows, seats):
    if objections is None:
        return []
    all_criteria = {criterion for row in rows.values() for criterion in row}
    result = []
    seen_ids = set()
    for obj in objections:
        if not isinstance(obj, dict):
            raise ArbitrationError(f"objection is not a mapping: {obj!r}")
        required = {"id", "criterion", "seat", "objection"}
        missing = required - set(obj.keys())
        if missing:
            raise ArbitrationError(f"objection missing keys {sorted(missing)}: {obj!r}")
        obj_id = obj["id"]
        criterion = obj["criterion"]
        seat = obj["seat"]
        if not isinstance(obj_id, str) or not obj_id:
            raise ArbitrationError(f"objection has a non-string or empty id: {obj!r}")
        if obj_id in seen_ids:
            raise ArbitrationError(f"duplicate objection id {obj_id!r}")
        seen_ids.add(obj_id)
        if criterion not in all_criteria:
            raise ArbitrationError(
                f"objection {obj_id!r} references criterion {criterion!r}, "
                f"which no seat scored"
            )
        if seat not in seats:
            raise ArbitrationError(
                f"objection {obj_id!r} names seat {seat!r}, which is not in the nominated seats"
            )
        answer = obj.get("answer")
        if answer is not None and not isinstance(answer, str):
            raise ArbitrationError(f"objection {obj_id!r} has a non-string answer: {answer!r}")
        result.append(
            {
                "id": obj_id,
                "criterion": criterion,
                "seat": seat,
                "objection": obj["objection"],
                "answer": answer,
            }
        )
    return result


def _resolve_criterion(criterion, rows, authoritative_map, objections):
    """Resolve one criterion to (value, raise_record_or_None).

    value is an int in [0, 10] or NO_DATA. raise_record is the objection
    record that justified a raise above the low score, or None if no raise
    was permitted (either none was attempted, or none carried a valid
    answer).
    """
    authoritative_seat = authoritative_map.get(criterion)
    if authoritative_seat is not None:
        # A3: the authoritative seat's answer stands on its own criterion,
        # numeric or NO-DATA, regardless of every other seat's score.
        return rows[authoritative_seat][criterion], None

    numeric = {
        seat: row[criterion]
        for seat, row in rows.items()
        if criterion in row and row[criterion] != NO_DATA
    }
    if not numeric:
        # A4: nobody produced a number for this criterion. Capped, not a
        # zero and not ignored.
        return NO_DATA, None

    base_seat = min(numeric, key=lambda s: numeric[s])
    base = numeric[base_seat]

    best_raise = None
    best_value = base
    for obj in objections:
        if obj["criterion"] != criterion:
            continue
        seat = obj["seat"]
        if seat not in numeric:
            continue
        candidate = numeric[seat]
        if candidate <= base:
            continue
        answer = obj["answer"]
        if not answer or obj["id"] not in answer:
            # A2: an answer that does not reference the objection it is
            # answering does not permit the raise.
            continue
        if candidate > best_value:
            best_value = candidate
            best_raise = obj

    return best_value, best_raise


def arbitrate(matrix, *, seats, criteria, authoritative_map=None, objections=None) -> Verdict:
    """Apply A1 through A5 to one score matrix and return exactly one Verdict.

    `criteria` is required: the closed set the methodology is judged against.
    Without it, a matrix scoring 3 of 12 criteria at a perfect 10 would be
    indistinguishable from a complete one, and PASS would be reachable while
    nine criteria were simply never asked. See the module docstring's THREE
    DISTINCT RESOLVED STATES section.

    Raises ArbitrationError on any structurally invalid input; see the
    module docstring's CONTINGENCY section. Never returns a partial result.
    """
    seat_set = _validate_seats(seats)
    criteria_set = _validate_criteria(criteria)
    rows = _validate_matrix(matrix, seat_set, criteria_set)
    auth_map = _validate_authoritative_map(authoritative_map, rows, seat_set)
    obj_list = _validate_objections(objections, rows, seat_set)

    scored_criteria = {criterion for row in rows.values() for criterion in row}
    missing_criteria = sorted(criteria_set - scored_criteria)

    per_criterion: Dict[str, Score] = {}
    raises_permitted: Dict[str, dict] = {}
    for criterion in sorted(criteria_set):
        if criterion in missing_criteria:
            # No cell in the matrix even asked: distinct from NO-DATA (a
            # seat looked and could not score it) and distinct from 0 (a
            # measured failure). See THREE DISTINCT RESOLVED STATES.
            per_criterion[criterion] = MISSING
            continue
        value, raise_record = _resolve_criterion(criterion, rows, auth_map, obj_list)
        per_criterion[criterion] = value
        if raise_record is not None:
            raises_permitted[criterion] = {
                "id": raise_record["id"],
                "seat": raise_record["seat"],
                "objection": raise_record["objection"],
                "answer": raise_record["answer"],
            }

    if missing_criteria:
        return Verdict(
            outcome=OUTCOME_CEILING,
            score=None,
            capping_criterion=missing_criteria[0],
            per_criterion=per_criterion,
            reason=(
                f"{len(missing_criteria)} of {len(criteria_set)} declared criteria were "
                f"never scored by any seat, not even as NO-DATA: {missing_criteria}. "
                f"PASS is unreachable while any criterion is missing, because a matrix "
                f"that never asked is not evidence that the answer would have been good"
            ),
            raises_permitted=raises_permitted,
            missing_criteria=missing_criteria,
        )

    no_data_criteria = [c for c, v in per_criterion.items() if v == NO_DATA]
    if no_data_criteria:
        capping = sorted(no_data_criteria)[0]
        return Verdict(
            outcome=OUTCOME_CEILING,
            score=None,
            capping_criterion=capping,
            per_criterion=per_criterion,
            reason=(
                f"criterion {capping!r} was scored by no seat (NO-DATA is never read as "
                f"a pass or a zero); this caps the verdict below PASS until a seat that "
                f"can score it is nominated"
            ),
            raises_permitted=raises_permitted,
        )

    numeric_values = {c: v for c, v in per_criterion.items() if v != NO_DATA}

    # A5: unanimous 10 across every seat, on every criterion, with no
    # authority override and no NO-DATA anywhere, is a finding, not a pass.
    all_raw_scores = [
        score
        for row in rows.values()
        for score in row.values()
        if score != NO_DATA
    ]
    unanimous_ten = bool(all_raw_scores) and all(s == MAX_SCORE for s in all_raw_scores)
    if unanimous_ten and all(v == MAX_SCORE for v in numeric_values.values()):
        return Verdict(
            outcome=OUTCOME_EXTRA_ROUND_REQUIRED,
            score=MAX_SCORE,
            capping_criterion=None,
            per_criterion=per_criterion,
            reason=(
                "every seat scored every criterion 10: a control nobody could fault has "
                "usually not been understood, so this mandates one extra red round "
                "instead of passing"
            ),
            raises_permitted=raises_permitted,
        )

    # A1: the score is the minimum, never an average.
    capping_criterion = min(numeric_values, key=lambda c: numeric_values[c])
    score = numeric_values[capping_criterion]

    if score >= PASS_THRESHOLD:
        outcome = OUTCOME_PASS
        reason = f"every criterion resolved to {PASS_THRESHOLD} or better; minimum is {capping_criterion!r} at {score}"
    else:
        outcome = OUTCOME_REJECT
        reason = (
            f"the minimum criterion score caps the verdict: {capping_criterion!r} "
            f"resolved to {score}, below the pass threshold of {PASS_THRESHOLD}, "
            f"regardless of how high the other criteria scored"
        )

    return Verdict(
        outcome=outcome,
        score=score,
        capping_criterion=capping_criterion,
        per_criterion=per_criterion,
        reason=reason,
        raises_permitted=raises_permitted,
    )
