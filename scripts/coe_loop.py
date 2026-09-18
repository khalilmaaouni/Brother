#!/usr/bin/env python3
"""COE-06 of the Council of Experts subsystem: the red and green loop driver.

WHY THIS EXISTS. COE-02 (coe_nominate.py) decides who sits on the council.
COE-04 (coe_score.py) asks each seat one criterion at a time. COE-05
(coe_arbitrate.py) turns one score matrix into one verdict under A1 to A5.
None of those three runs more than once: they answer "what does the council
say about this methodology, right now." This module is the thing that keeps
asking, keeps letting red attack, keeps letting green repair, and above all
decides HONESTLY when to stop. COE-SUBSYSTEM-WBS.md names the danger this
module exists to prevent in its own closing section: "the council becomes
ceremony. Seats are nominated, scores come back at 9, nobody's mind changes,
and the process adds cost and latency while certifying whatever was going to
be built anyway." A loop that calls PASS after one quiet round IS that
ceremony with extra steps. Termination is therefore the whole unit; scoring
and arbitration are composed, never rebuilt (worker contract: "compose them,
do not build a second scheduler, a second claim store, or a second
integration engine" applies here to scoring and arbitration too).

THE ONE ENTRY POINT: run_loop(problem, methodology, *, criteria, ask, attack,
repair, max_rounds=6, nominate=None, score=None, arbitrate=None) -> LoopResult.

THE ROUND, exactly as COE-RUBRIC-AND-COUNCIL.md section 4 and
COE-SUBSYSTEM-WBS.md's COE-06 row both state it:
  1. Green proposes a methodology. The council scores it (coe_score). The
     methodology's score is the MINIMUM criterion (coe_arbitrate, A1).
  2. Red attacks the lowest-scoring criteria first, with EXPLOITS.
  3. Each surviving (accepted) exploit lowers that criterion to what red
     demonstrated. It never raises one.
  4. Green either repairs the methodology or withdraws it for an alternative.
  5. Re-score. Repeat.

`ask` is coe_score.score_council's own per-(seat, criterion) callable,
passed straight through: ask(seat_id, criterion, prompt) -> answer. It is
not this module's business what a seat says; scoring isolation is COE-04's
contract, not re-implemented here.

`attack` is RED TEAM for one round: attack(methodology, verdict, round_num)
-> an iterable (possibly empty) of exploit candidates, where `verdict` is
the coe_arbitrate.Verdict this round's scoring produced (so red can read
`verdict.per_criterion` and attack the lowest-scoring criteria first, exactly
as the standing rule requires). An exploit candidate is EITHER a concrete
exploit or a rejected non-finding; see _validate_exploit_candidate() below
for the exact shape a candidate must carry to count. Returning a bare
string, rather than a list, is a documented protocol violation of `attack`
itself (see EDGE LIST) and raises; a list CONTAINING a string is the
ordinary "vague concern, not a finding" case and that string is rejected as
malformed, never as a raise.

`repair` is GREEN TEAM's response after a round with the accepted (valid,
surviving) exploits from that round: repair(methodology, accepted_exploits,
round_num) -> a new methodology string to try next, or None to WITHDRAW the
methodology outright, ending the loop as REJECTED. Any other return type is
a protocol violation and raises (see EDGE LIST).

TERMINATION, which is the unit, so it is stated four times: in
COE-RUBRIC-AND-COUNCIL.md section 4, in COE-SUBSYSTEM-WBS.md's COE-06 row,
in the worker brief, and here, because a fifth restatement in code comments
is cheaper than a sixth incident:

  PASS requires TWO CONSECUTIVE ROUNDS with no surviving exploit against any
  criterion scoring below 9 (CONSECUTIVE_CLEAN_ROUNDS_REQUIRED = 2, not 1: a
  single quiet round is more often red running out of ideas than the control
  being sound). "No surviving exploit below 9" is computed AFTER accepted
  exploits are folded into the round's resolved per-criterion scores, so a
  round where the raw council score was already below 9, with no exploit
  needed to show it, is exactly as unclean as a round where red had to work
  for it.

  CEILING after CEILING_ROUNDS (3) consecutive rounds where one criterion's
  resolved score cannot reach 9 (whether by a low number, NO-DATA, or
  MISSING: all three fail the ">= 9" test the same way, since none of them
  is evidence the control is sound). Recorded with the capping criterion and
  the reason; this is an ACCEPTABLE outcome, distinct from PASS by
  construction, not by convention: LoopResult.outcome is a single string
  field, so a caller comparing outcome == PASS cannot mistake a CEILING for
  one, and no code path in this module ever sets outcome to PASS except the
  two-consecutive-clean-round branch.

  THE STANDARD CANNOT CHANGE MID-ROUND. `criteria` is snapshotted once, as
  a frozenset, before round 1, and re-checked against the live `criteria`
  object at the top of every round and immediately after `repair` returns
  (the "start" and "end" of the round, per the standing rule's own words).
  This only detects a mutation of the SAME object the caller passed in (a
  list a test's own `attack` or `repair` closure appends to); a caller that
  hands in a brand-new object with different contents on some later call
  cannot be caught this way, because run_loop only ever receives `criteria`
  once, at the top of the call, not once per round. That is not a gap: this
  module has exactly one `criteria` argument for the whole loop by design
  (COE-SUBSYSTEM-WBS.md's own wording, "the standard cannot be edited during
  a round"), so a caller wanting to change the standard between separate
  invocations does so by calling run_loop again, deliberately, which is a
  different, visible act from a criterion silently drifting inside one call.

  A RED FINDING THAT IS NOT A CONCRETE EXPLOIT IS NOT A FINDING. Validated
  by _validate_exploit_candidate() before anything touches a score; a
  rejected candidate is recorded (for audit, in the round's
  "exploits_rejected" list) but never folds into resolved_per_criterion.

  UNANIMITY AT 10 (coe_arbitrate.OUTCOME_EXTRA_ROUND_REQUIRED) NEVER PASSES.
  coe_arbitrate.arbitrate() already computes this (A5); this module honours
  it by forcing the round "not clean" whenever the raw verdict says so,
  regardless of what red does or does not find that round, which resets the
  two-consecutive-clean-round streak. It does not re-derive A5's condition;
  a second computation of "all tens" here, beside coe_arbitrate's own, would
  itself be the duplicate-truth failure this estate's architect seat exists
  to catch.

CONTINGENCY, so a caller never has to guess which direction a return means:
  PASS       ship it. Two clean rounds is the strongest signal this process
             produces; there is nothing further for this module to check.
  CEILING    do not ship as-is. LoopResult.capping_criterion names the one
             criterion that would not move; LoopResult.final_score is that
             criterion's last resolved numeric value, or None when it never
             resolved to a number at all (NO-DATA or MISSING every round).
             A caller's recovery is an OWNER DECISION (COE-07's own row: "a
             selection row with a recorded ceiling opens its build row only
             with a named owner decision"), never a silent retry of this
             exact call, which would reproduce the identical ceiling for the
             identical reason.
  REJECTED   green itself gave up on this methodology. LoopResult.rounds
             holds every round that ran; a caller trying again does so with
             a genuinely different methodology, not a retry.
  ABANDONED  the loop ran out of rounds (including max_rounds=0, and
             including a nomination that could not seat a council at all,
             before any round ran) without reaching PASS, CEILING or
             REJECTED. This is NEVER read as a pass, and it is distinct from
             CEILING: CEILING means a specific criterion was measured stuck
             for three rounds running; ABANDONED means the budget ran out
             before the loop could even establish that (round mechanics were
             still converging, or diverging, when the money ran out). A
             caller's recovery is raising max_rounds (spend more) or, for
             the pre-round nomination failure, fixing the problem's declared
             attributes and calling again (see coe_nominate.NominationRefused's
             own contingency).
  LoopError  (raised, not returned) something in the MACHINERY broke, not
             the methodology: attack or repair raised, attack or repair
             returned a shape this module never asked for, arbitrate
             returned an outcome string this module does not recognise, or
             the standard changed mid-round. This is deliberately never
             folded into ABANDONED: ABANDONED means "the process ran
             correctly to its budget and still had no verdict," LoopError
             means "the process itself is broken and produced no evidence
             at all," and conflating the two would let a broken test harness
             or a crashing attacker read, on paper, exactly like an honest
             loop that simply could not converge. WHO IS TOLD: the caller
             that invoked run_loop; nothing in this module logs or notifies
             on its own (COE-07's own row, not this one, wires a gate's
             refusals into a countable log).
             A NOMINATION failure is the one exception folded into ABANDONED
             rather than raised as LoopError: coe_nominate.NominationRefused
             (and any other exception nominate() raises) is treated as a
             legitimate, expected, ALREADY-DOCUMENTED business outcome
             (COE-02's own contingency section names exactly how a caller
             recovers from it), not a machinery break, so it is folded into
             ABANDONED with the underlying reason preserved in
             LoopResult.reason, at zero rounds. A failure of `score` or
             `arbitrate` DURING a round, by contrast, happens after rounds
             have already run and is raised as LoopError: a scoring or
             arbitration crash mid-loop is not a documented, expected
             business outcome the way a pre-loop nomination refusal is, and
             ABANDONED at that point would misreport a machinery break as an
             honest budget exhaustion.

THE BAD STATE A GREEN RUN WOULD ALSO PASS, NAMED SO IT IS NEVER SHIPPED
ACCIDENTALLY: stub `ask`, `attack` and `repair` functions this cooperative
would make every test in this file green while proving the loop does
nothing: `ask` always returns 10, `attack` always returns an empty list (red
never finds anything), `repair` is never even exercised because nothing
ever needs repairing. Under exactly those stubs, coe_arbitrate.arbitrate()
does NOT return PASS on round 1: a raw matrix of all 10s is
OUTCOME_EXTRA_ROUND_REQUIRED (A5), which this module reads as forced "not
clean." Because the stub `ask` and `attack` never change their answer round
to round, the SAME unanimous-10 matrix recurs every round, so
OUTCOME_EXTRA_ROUND_REQUIRED recurs every round too, and the loop can never
accumulate two consecutive clean rounds: it runs out its full max_rounds and
reports ABANDONED, never PASS. test_all_tens_stub_never_passes below is
exactly this scenario and asserts the outcome is ABANDONED, not PASS,
which is the sharpest test in this file: a fully cooperative, always-agreeing
stub set is precisely the "ceremony" this whole subsystem exists to refuse,
and the mechanism refuses it without any special-case code written for that
purpose, purely as a consequence of composing coe_arbitrate's own A5 with
this module's two-consecutive-clean-round rule.

EDGE LIST, walked explicitly (docs/plan/ORCH-1020-WORKER-CONTRACT.md's
standing law):
  - max_rounds=0: handled, returns ABANDONED with zero rounds and no
    nomination attempted (there is nothing to nominate a council for if no
    round can ever run).
  - a repair that makes the score WORSE: not special-cased. The next
    round's scoring and stuck-criterion counters treat "worse" exactly like
    "unchanged" or "still bad": not clean, resets the clean-round streak,
    advances the stuck-criterion counter toward CEILING. No code needs to
    know a score got worse versus merely staying bad for the mechanism to
    stay honest.
  - a repair that changes nothing at all: DECIDED, not special-cased, and
    the decision is stated because the brief calls it "the interesting
    one." This module treats `methodology` as opaque (mirroring coe_score's
    own stated position: "methodology is opaque to this module... it never
    inspects it beyond that"), so detecting "the text did not change" would
    mean this module reaching into a payload it has no standard for
    interpreting, which is exactly the layering violation the architect
    seat exists to catch. It does not need to: if `ask` and `attack` are
    themselves pure functions of (seat, criterion, methodology) and
    (methodology, verdict) respectively, an unchanged methodology
    reproduces an identical verdict and identical exploits next round,
    which the stuck-criterion counter already treats as one more stuck
    round toward CEILING. A no-op repair is not silently rewarded; it is
    left to the same honest counter that catches a repair that never
    improves anything at all. test_no_op_repair_reaches_ceiling below is
    this scenario.
  - an attack function that raises: raised onward as LoopError, never
    absorbed as a clean or an unclean round (see CONTINGENCY: a crashed red
    team is a machinery break, not a business outcome either direction
    could honestly represent).
  - an ask function that raises mid-round: NOT this module's problem to
    solve twice. coe_score.score_council() already catches an `ask`
    exception per (seat, criterion) and records it as a failure cell, which
    coe_arbitrate.arbitrate() already turns into a MISSING criterion, which
    already caps this module's per-round "clean" check below 9 the same
    way any other missing or NO-DATA criterion does. No new code here
    reproduces that handling; test_ask_failure_prevents_pass exercises the
    composed behaviour to prove it still holds through this module.
  - a nomination that refuses (high risk, one family): handled, folded into
    ABANDONED at zero rounds; see CONTINGENCY above for why this one
    nomination failure is not a LoopError.
  - an empty criteria list: raises LoopError immediately, before nominating
    anyone: there is no standard to run a loop against.
  - an exploit targeting a criterion not in the standard: handled by
    _validate_exploit_candidate(), rejected as malformed (recorded in
    "exploits_rejected"), never raised and never lowers a score: the
    criterion it names simply is not part of what this run is judging.

Python 3.9 floor, standard library only (dataclasses and typing ship with
the interpreter), no network, no clock, no subprocess, no sleep. This
module does not schedule, dispatch, merge, or mark any build unit done; it
runs one problem's red-and-green rounds and returns.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

import coe_arbitrate
import coe_nominate
import coe_score

OUTCOME_PASS = "PASS"
OUTCOME_CEILING = "CEILING"
OUTCOME_REJECTED = "REJECTED"
OUTCOME_ABANDONED = "ABANDONED"
OUTCOMES = frozenset(
    {OUTCOME_PASS, OUTCOME_CEILING, OUTCOME_REJECTED, OUTCOME_ABANDONED}
)

#: Two, not one. See the module docstring's TERMINATION section for why a
#: single clean round is not evidence: it is red running out of ideas as
#: often as it is a sound control.
CONSECUTIVE_CLEAN_ROUNDS_REQUIRED = 2

#: Three consecutive rounds where a criterion cannot reach 9 records a
#: ceiling. Per COE-RUBRIC-AND-COUNCIL.md section 4 and the worker brief,
#: both stated as "three rounds," not "three rounds after the first."
CEILING_ROUNDS = 3

_EXPLOIT_BASIS_KEYS = ("input", "sequence", "incentive")


class LoopError(Exception):
    """The loop's own machinery broke: `attack` or `repair` raised or
    returned a shape this module never asked for, `arbitrate` returned an
    outcome string outside coe_arbitrate.OUTCOMES, the criteria set changed
    inside a round, `max_rounds` was not a valid non-negative int, or
    `criteria` was empty. Never raised because a methodology failed under
    attack: that is PASS/CEILING/REJECTED/ABANDONED territory, handled by
    a returned LoopResult, not an exception. See the module docstring's
    CONTINGENCY section for the one deliberate exception to this split
    (a pre-loop nomination refusal, which is folded into ABANDONED, not
    raised here).
    """


@dataclass(frozen=True)
class LoopResult(object):
    """One run_loop() call's result.

    outcome            one of OUTCOMES (PASS, CEILING, REJECTED, ABANDONED).
    rounds             tuple of per-round history dicts, in round order,
                       every round that actually ran (including the round
                       that produced the terminal outcome). Never empty
                       except for ABANDONED-at-zero-budget or a nomination
                       refusal, both of which name that explicitly in
                       `reason`.
    final_score        the resolved numeric score that decided the outcome:
                       the (still >= 9) minimum on PASS, the stuck
                       criterion's last numeric value on CEILING (or None
                       if it never resolved to a number at all), and always
                       None on REJECTED and ABANDONED, since neither is a
                       score, they are the absence of one.
    capping_criterion  the criterion PASS's or CEILING's final_score came
                       from; None on REJECTED and ABANDONED.
    exploits_accepted  tuple of every VALID exploit accepted across the
                       whole run (not just the final round), each carrying
                       its round number, criterion, basis, detail,
                       control_says, demonstrated_score and the new_score
                       it produced, per the standing rule: "the history
                       records the exploit beside the new score."
    reason             one plain-language sentence naming why this outcome,
                       never a bare enum value: see CONTINGENCY above for
                       what each outcome means to a caller deciding what to
                       do next.
    """

    outcome: str
    rounds: Tuple[Dict[str, Any], ...]
    final_score: Optional[int]
    capping_criterion: Optional[str]
    exploits_accepted: Tuple[Dict[str, Any], ...]
    reason: str


def _seat_ids_of(council):
    """The seat id strings arbitrate()'s `seats=` argument needs.

    Duck-typed exactly like coe_score.py's own `_seat_ids()`: accepts
    anything with a `.seats` attribute (coe_nominate.Council's own shape,
    the real default) or a bare iterable of seat id strings (what an
    injected test double may hand back instead). Raises LoopError, never
    silently scores against nobody, when the council carries zero seats:
    coe_nominate.nominate() already guarantees a non-empty Council or a
    raised NominationRefused for the real default, so this can only
    actually fire here against an injected `nominate` stub that returns an
    empty council directly, which is exactly the kind of machinery mistake
    LoopError exists to name rather than silently miscount as ABANDONED.
    """
    seats = list(getattr(council, "seats", council))
    if not seats:
        raise LoopError(
            "nominated council carries no seats; cannot score a methodology "
            "against nobody"
        )
    return seats


def _validate_exploit_candidate(candidate, criteria_set):
    """(True, record) for a concrete exploit; (False, reason) otherwise.

    A concrete exploit, per the standing rule (COE-RUBRIC-AND-COUNCIL.md
    section 4): "an input, a sequence, or an actor's incentive that defeats
    the control while it reports success... a red finding that cannot be
    stated as 'here is what I would do, and here is what the control would
    say' is not a finding." Mechanically, that requires a mapping carrying:
    a `criterion` that is actually part of the standard being judged; at
    least one of `input`, `sequence` or `incentive` as a non-empty string
    (the WHAT); `control_says`, a non-empty string naming what the control
    would wrongly report; and `demonstrated_score`, the int in [0, 10] red
    showed the criterion actually deserves. Anything else, including a bare
    string, a dict missing any of these, or a criterion this run is not
    judging, is a doubt or a concern, not a finding, and never touches a
    score.
    """
    if not isinstance(candidate, dict):
        return False, (
            "exploit is not a mapping (a vague finding, not a concrete "
            "exploit): %r" % (candidate,)
        )

    criterion = candidate.get("criterion")
    if not isinstance(criterion, str) or not criterion:
        return False, "exploit names no valid criterion: %r" % (candidate,)
    if criterion not in criteria_set:
        return False, (
            "exploit targets criterion %r, which is not in the standard "
            "this run is judging against" % (criterion,)
        )

    basis_present = [
        key for key in _EXPLOIT_BASIS_KEYS
        if isinstance(candidate.get(key), str) and candidate.get(key)
    ]
    if not basis_present:
        return False, (
            "exploit states no input, sequence or incentive: a doubt or a "
            "concern is not a finding: %r" % (candidate,)
        )

    control_says = candidate.get("control_says")
    if not isinstance(control_says, str) or not control_says:
        return False, (
            "exploit does not state what the control would (wrongly) say: "
            "%r" % (candidate,)
        )

    demonstrated = candidate.get("demonstrated_score")
    if isinstance(demonstrated, bool) or not isinstance(demonstrated, int):
        return False, (
            "exploit's demonstrated_score is not an int: %r" % (candidate,)
        )
    if not (coe_arbitrate.MIN_SCORE <= demonstrated <= coe_arbitrate.MAX_SCORE):
        return False, (
            "exploit's demonstrated_score %r is outside %d to %d"
            % (demonstrated, coe_arbitrate.MIN_SCORE, coe_arbitrate.MAX_SCORE)
        )

    basis_key = basis_present[0]
    return True, {
        "criterion": criterion,
        "basis": basis_key,
        "detail": candidate[basis_key],
        "control_says": control_says,
        "demonstrated_score": demonstrated,
    }


def _apply_exploits(per_criterion, accepted_exploits):
    """(resolved_per_criterion, applied_records) for one round.

    Each accepted exploit can only LOWER the criterion it targets, never
    raise it: `new_value = min(current, demonstrated_score)`, so a red
    finding that (mistakenly, or generously) demonstrates a score no worse
    than what the council already gave has no effect, and is still recorded
    as accepted (it was a real, concrete finding) with new_score equal to
    the unchanged current value. A criterion the council could not resolve
    to a number at all (NO-DATA or MISSING) is left exactly as it is: it is
    already capped below PASS by coe_arbitrate's own A4 and missing-criteria
    rule, and an exploit cannot sink a value that was never a number to
    begin with.
    """
    resolved = dict(per_criterion)
    applied = []
    for exploit in accepted_exploits:
        criterion = exploit["criterion"]
        current = resolved.get(criterion)
        if isinstance(current, int) and not isinstance(current, bool):
            new_value = min(current, exploit["demonstrated_score"])
            resolved[criterion] = new_value
        else:
            new_value = current
        applied.append(dict(exploit, new_score=new_value))
    return resolved, applied


def _is_resolved_pass_value(value):
    """True only for an int (never bool) at or above the pass threshold.

    NO-DATA and MISSING both fail this the same way a low number does:
    neither is evidence the control is sound (coe_arbitrate's own A4).
    """
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= coe_arbitrate.PASS_THRESHOLD
    )


def run_loop(problem, methodology, *, criteria, ask, attack, repair,
             max_rounds=6, nominate=None, score=None, arbitrate=None):
    """Run the red-and-green loop for one problem and methodology.

    Returns a LoopResult; raises LoopError when the loop's own machinery
    (never the methodology under review) breaks. See the module docstring's
    CONTINGENCY section for exactly what each outcome and the exception
    mean to a caller, and the EDGE LIST section for every boundary this
    function was checked against.

    `criteria` must be a reusable, non-empty sequence (a list or tuple, not
    a one-shot iterator): this function re-reads it at the start of every
    round and again immediately after `repair` returns, comparing it to a
    frozenset snapshot taken before round 1, specifically so it can catch
    "the standard changed mid-round" (see TERMINATION above) rather than
    only being told about it after the fact.

    `nominate`, `score` and `arbitrate` default to coe_nominate.nominate,
    coe_score.score_council and coe_arbitrate.arbitrate respectively, and
    are injectable only so a test can observe or replace them without
    touching disk; this function never reimplements what any of the three
    already decides.
    """
    nominate_fn = nominate if nominate is not None else coe_nominate.nominate
    score_fn = score if score is not None else coe_score.score_council
    arbitrate_fn = arbitrate if arbitrate is not None else coe_arbitrate.arbitrate

    if isinstance(max_rounds, bool) or not isinstance(max_rounds, int) or max_rounds < 0:
        raise LoopError(
            "max_rounds must be a non-negative int, got %r" % (max_rounds,)
        )

    criteria_list = list(criteria)
    if not criteria_list:
        raise LoopError(
            "criteria is empty: there is no standard to run this loop against"
        )
    criteria_frozen = frozenset(criteria_list)

    if max_rounds == 0:
        return LoopResult(
            outcome=OUTCOME_ABANDONED,
            rounds=(),
            final_score=None,
            capping_criterion=None,
            exploits_accepted=(),
            reason=(
                "max_rounds is 0: no round was permitted to run, so no "
                "verdict could be reached"
            ),
        )

    try:
        council = nominate_fn(problem)
    except Exception as exc:  # nomination refusal is a documented business
        # outcome (coe_nominate.NominationRefused's own contingency section
        # names how a caller recovers), never a machinery break; see the
        # module docstring's CONTINGENCY section for why this is the one
        # exception folded into ABANDONED rather than raised as LoopError.
        return LoopResult(
            outcome=OUTCOME_ABANDONED,
            rounds=(),
            final_score=None,
            capping_criterion=None,
            exploits_accepted=(),
            reason=(
                "nomination could not produce a council for this problem: "
                "%s: %s" % (type(exc).__name__, exc)
            ),
        )

    seat_ids = _seat_ids_of(council)

    current_methodology = methodology
    rounds = []
    all_accepted_exploits = []
    consecutive_clean = 0
    stuck_rounds = {criterion: 0 for criterion in criteria_list}

    for round_num in range(1, max_rounds + 1):
        if frozenset(criteria) != criteria_frozen:
            raise LoopError(
                "the standard changed before round %d could start (the "
                "criteria this loop began with differ from criteria now): "
                "the standard cannot change mid-round" % round_num
            )

        try:
            matrix = score_fn(
                council, current_methodology, ask=ask, criteria=criteria_list
            )
        except Exception as exc:
            raise LoopError(
                "scoring failed in round %d: %s: %s"
                % (round_num, type(exc).__name__, exc)
            ) from exc

        try:
            verdict = arbitrate_fn(matrix, seats=seat_ids, criteria=criteria_list)
        except Exception as exc:
            raise LoopError(
                "arbitration failed in round %d: %s: %s"
                % (round_num, type(exc).__name__, exc)
            ) from exc

        if verdict.outcome not in coe_arbitrate.OUTCOMES:
            raise LoopError(
                "arbitrate returned an unrecognised outcome %r in round "
                "%d: refusing to guess what it means" % (verdict.outcome, round_num)
            )

        try:
            candidates = attack(current_methodology, verdict, round_num)
        except Exception as exc:
            raise LoopError(
                "red team's attack function raised in round %d: %s: %s"
                % (round_num, type(exc).__name__, exc)
            ) from exc

        if isinstance(candidates, (str, bytes)) or not hasattr(candidates, "__iter__"):
            raise LoopError(
                "attack must return an iterable of exploit candidates "
                "(possibly empty), got %r in round %d" % (candidates, round_num)
            )

        accepted = []
        rejected = []
        for candidate in candidates:
            ok, record_or_reason = _validate_exploit_candidate(
                candidate, criteria_frozen
            )
            if ok:
                accepted.append(dict(record_or_reason, round=round_num))
            else:
                rejected.append({"round": round_num, "reason": record_or_reason})

        resolved_per_criterion, applied = _apply_exploits(
            verdict.per_criterion, accepted
        )
        all_accepted_exploits.extend(applied)

        # A5, honoured not re-derived: coe_arbitrate already decided this
        # matrix is unanimous at 10 and mandates an extra round. Forcing
        # "not clean" here, regardless of what red found this round, is
        # what stops that extra round from silently counting toward PASS.
        forced_not_clean = verdict.outcome == coe_arbitrate.OUTCOME_EXTRA_ROUND_REQUIRED

        for criterion in criteria_list:
            value = resolved_per_criterion.get(criterion, coe_arbitrate.MISSING)
            if _is_resolved_pass_value(value):
                stuck_rounds[criterion] = 0
            else:
                stuck_rounds[criterion] += 1

        clean = (
            not forced_not_clean
            and all(_is_resolved_pass_value(v) for v in resolved_per_criterion.values())
        )

        round_record = {
            "round": round_num,
            "methodology": current_methodology,
            "base_outcome": verdict.outcome,
            "base_per_criterion": dict(verdict.per_criterion),
            "resolved_per_criterion": resolved_per_criterion,
            "exploits_accepted": applied,
            "exploits_rejected": rejected,
            "forced_not_clean": forced_not_clean,
            "stuck_rounds": dict(stuck_rounds),
            "clean": clean,
        }
        rounds.append(round_record)

        ceiling_criteria = sorted(
            c for c in criteria_list if stuck_rounds[c] >= CEILING_ROUNDS
        )
        if ceiling_criteria:
            capping = ceiling_criteria[0]
            capping_value = resolved_per_criterion.get(capping)
            final_score = (
                capping_value
                if isinstance(capping_value, int) and not isinstance(capping_value, bool)
                else None
            )
            return LoopResult(
                outcome=OUTCOME_CEILING,
                rounds=tuple(rounds),
                final_score=final_score,
                capping_criterion=capping,
                exploits_accepted=tuple(all_accepted_exploits),
                reason=(
                    "criterion %r could not reach %d for %d consecutive "
                    "rounds; recorded as a ceiling, never reported as a pass"
                    % (capping, coe_arbitrate.PASS_THRESHOLD, CEILING_ROUNDS)
                ),
            )

        if clean:
            consecutive_clean += 1
        else:
            consecutive_clean = 0

        if consecutive_clean >= CONSECUTIVE_CLEAN_ROUNDS_REQUIRED:
            capping = min(
                resolved_per_criterion, key=lambda c: resolved_per_criterion[c]
            )
            return LoopResult(
                outcome=OUTCOME_PASS,
                rounds=tuple(rounds),
                final_score=resolved_per_criterion[capping],
                capping_criterion=capping,
                exploits_accepted=tuple(all_accepted_exploits),
                reason=(
                    "%d consecutive rounds produced no surviving exploit "
                    "against any criterion scoring below %d"
                    % (CONSECUTIVE_CLEAN_ROUNDS_REQUIRED, coe_arbitrate.PASS_THRESHOLD)
                ),
            )

        if round_num == max_rounds:
            return LoopResult(
                outcome=OUTCOME_ABANDONED,
                rounds=tuple(rounds),
                final_score=None,
                capping_criterion=None,
                exploits_accepted=tuple(all_accepted_exploits),
                reason=(
                    "max_rounds (%d) was reached without two consecutive "
                    "clean rounds and without a criterion stuck long enough "
                    "to record a ceiling; reported as abandoned, never as a "
                    "pass" % max_rounds
                ),
            )

        try:
            next_methodology = repair(current_methodology, accepted, round_num)
        except Exception as exc:
            raise LoopError(
                "green team's repair function raised in round %d: %s: %s"
                % (round_num, type(exc).__name__, exc)
            ) from exc

        if next_methodology is None:
            return LoopResult(
                outcome=OUTCOME_REJECTED,
                rounds=tuple(rounds),
                final_score=None,
                capping_criterion=None,
                exploits_accepted=tuple(all_accepted_exploits),
                reason=(
                    "green withdrew the methodology after round %d rather "
                    "than repairing it" % round_num
                ),
            )

        if not isinstance(next_methodology, str):
            raise LoopError(
                "repair must return a new methodology string or None "
                "(withdrawal), got %r in round %d" % (next_methodology, round_num)
            )

        if frozenset(criteria) != criteria_frozen:
            raise LoopError(
                "the standard changed during round %d, discovered right "
                "after repair ran: the standard cannot change mid-round"
                % round_num
            )

        current_methodology = next_methodology

    # Unreachable: every iteration above returns, either before max_rounds
    # (PASS, CEILING, REJECTED) or exactly at it (ABANDONED). Kept as a
    # defensive raise rather than an implicit None, in case that invariant
    # is ever broken by a future edit: a silent None reads as "no result at
    # all," which for this module must never be mistaken for any of the
    # four honest outcomes above.
    raise LoopError("run_loop fell through its round loop without returning")


if __name__ == "__main__":
    # Not a test suite (see test_coe_loop.py for that); a two-second sanity
    # check that the module at least imports and runs one trivial loop to
    # completion, the way a `__main__` self-check is expected to for any
    # non-trivial branch or loop that would otherwise ship untested.
    def _demo_ask(seat_id, criterion, prompt):
        return 10

    def _demo_attack(methodology, verdict, round_num):
        return []

    def _demo_repair(methodology, accepted_exploits, round_num):
        return methodology

    class _DemoCouncil(object):
        seats = ("solo",)

    result = run_loop(
        {"id": "demo"},
        "demo methodology",
        criteria=["C1", "C2"],
        ask=_demo_ask,
        attack=_demo_attack,
        repair=_demo_repair,
        max_rounds=3,
        nominate=lambda problem: _DemoCouncil(),
    )
    assert result.outcome == OUTCOME_ABANDONED, result.outcome
    print("coe_loop demo: all-tens stub abandons rather than passing, as documented")
