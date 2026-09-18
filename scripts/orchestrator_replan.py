#!/usr/bin/env python3
"""ORCH-12 of the 1.0.20 orchestration control plane: retry versus re-plan.

WHAT ALREADY EXISTS, READ FIRST, NOT REBUILT HERE.

scripts/attempt_ledger.py already refuses a third attempt at one TECHNIQUE
CLASS: it counts prior "failed" rows recorded against a (problem, class)
pair and REFUSEs once that count reaches its strikes limit (default 2),
quoting a vault lesson, the failing check's own output, or a prior passed
attempt elsewhere as the research a human would otherwise have to go and
do. Its unit of comparison is a STRING THE WORKER DECLARES ("the class").
It never looks at what an attempt actually touched, so a worker that
mislabels attempt 2 with a new class name, or never declares one, defeats
it silently. It also has no opinion on whether a failure should be
retried at all; it is consulted only once something is already about to
be retried. This module does not reimplement any of that: it does not
count strikes against a declared class and it does not write a ledger.

scripts/brother_run.py's `_apply_w2_retry` and `_retry_backoff_seconds`
already ARE the transient retry engine: on a rate_limit failure a unit is
parked and its just-spent attempt is refunded (a rate limit never counts
against a unit's real attempt budget); on overloaded/timeout/empty it
blocks the drain for a growing, capped, jittered backoff before the next
round's claim. Rule 1 below defers to that mechanism by name. This module
does not compute a backoff duration and does not sleep.

WHAT THIS MODULE ADDS, which neither of the above provides:

1. One shared answer to "what does this failure mean for the next
   attempt", driven off orchestrator_invariants.classify_failure, so every
   caller reads the same verdict instead of re-deriving one from the raw
   failure class string.
2. A MECHANICAL, evidence-based test for "attempt N was effectively the
   same as attempt N minus 1", built from a comparable FINGERPRINT of an
   attempt (see fingerprint() below): the files it wrote, the deciding
   check it ran, the strategy label it declared, and the base revision it
   started from. attempt_ledger.py has no equivalent to this: it compares
   a self-reported class string, never what an attempt actually did, so a
   worker's own prose claim that it "tried something different" is not
   evidence here the way it would pass unnoticed there.

Python 3.9 floor, standard library only, no network, no filesystem writes:
this module is a pure decision function over a failure class and the
caller's own attempt history. Persisting that history, and persisting a
re-plan record, stays the caller's job (the plan document, the claim
store), exactly as the worker contract says: this unit composes, it does
not build a second scheduler or a second store.
"""

import collections

import orchestrator_invariants as inv

# The five outcomes a decision() call can return. RETRY and RETRY_CHANGED
# both mean "try again"; the difference is whether the next attempt is
# allowed to repeat the last one. REPLAN and PARK both mean "do not retry
# as-is", the difference is who acts: REPLAN asks for a new approach from
# the same lane, PARK hands the unit to a human because no approach this
# lane can choose is the right axis (an authority refusal). EXHAUSTED means
# a budget, not a rule, stopped it.
RETRY = "RETRY"
RETRY_CHANGED = "RETRY_CHANGED"
REPLAN = "REPLAN"
PARK = "PARK"
EXHAUSTED = "EXHAUSTED"

VERDICTS = frozenset((RETRY, RETRY_CHANGED, REPLAN, PARK, EXHAUSTED))

# The fields a fingerprint is built from. Deliberately exhaustive here (not
# "whatever keys the caller happened to pass") so a caller cannot silently
# widen or narrow what counts as "the approach" per call; see fingerprint()
# for why each one is in and everything else is out.
_FINGERPRINT_FIELDS = ("files_written", "check", "strategy", "base_revision")


Decision = collections.namedtuple(
    "Decision", ("verdict", "reason", "required_change"))


def classify(failure_class):
    """'transient', 'semantic', 'authority' or 'other': a direct delegate
    to orchestrator_invariants.classify_failure. Never restate the buckets
    here; import them. Raises ValueError on a name outside
    orchestrator_invariants.FAILURE_CLASSES, exactly as that function
    does: an unrecognised failure class is a defect in the caller, not a
    case for this module to guess at.
    """
    return inv.classify_failure(failure_class)


def fingerprint(attempt):
    """A comparable fingerprint of one attempt: the sorted tuple of files
    it wrote, the deciding check it ran, the strategy label it declared,
    and the base revision it started from, as a hashable tuple two
    attempts can be compared by equality.

    DELIBERATELY IGNORED: a timestamp, a run id, an attempt number, a
    duration, raw log or output text, or any other field that is unique to
    one EXECUTION rather than to the APPROACH taken. Extra keys on the
    attempt dict beyond _FINGERPRINT_FIELDS are silently not read here,
    which is what makes them ignored rather than merely unused.

    THE BAD STATE THIS GUARDS AGAINST: a fingerprint that includes any
    field that is unique by construction (a timestamp, a uuid run id, an
    attempt counter) makes every attempt look different from every other
    one, forever. That silently turns rule 4 (no identical third attempt)
    off, and no green test run would ever surface it: "no two attempts are
    ever identical" reads exactly like "the worker keeps trying something
    genuinely new" until a human notices attempt 11 was attempt 1 with a
    different clock reading. test_fingerprint_ignores_volatile_fields
    below is the regression guard: it fails the moment fingerprint() is
    edited to read a volatile field.

    THE OPPOSITE BAD STATE, also guarded here: a fingerprint coarser than
    this (for instance, keyed only on the failure_class, or only on
    outcome) would make every attempt at a given failure look identical
    even when the worker genuinely changed strategy, files and check, so
    rule 4 would fire on every attempt and block all legitimate retries.
    test_genuinely_different_attempts_are_allowed below is the guard for
    that direction: two attempts differing only in `strategy` must compare
    unequal.
    """
    if not isinstance(attempt, dict):
        raise ValueError("attempt must be a dict, got %r" % (type(attempt),))
    files = attempt.get("files_written") or ()
    return (
        tuple(sorted(str(f) for f in files)),
        str(attempt.get("check") or ""),
        str(attempt.get("strategy") or ""),
        str(attempt.get("base_revision") or ""),
    )


def same_approach(attempt_a, attempt_b):
    """True when two attempts fingerprint identically. The only comparison
    rule 4 is allowed to use; never a prose judgement of the two attempts'
    descriptions."""
    return fingerprint(attempt_a) == fingerprint(attempt_b)


def _semantic_attempts(attempt_history):
    """Every entry of attempt_history whose own failure_class classifies
    as semantic or other, in order. "Other" (worker_crash, missing_evidence,
    resource_pressure, and the literal class named "unknown") is folded in
    here rather than given a fourth bucket: none of those are an
    environment retrying itself (transient) and none are a policy refusal
    (authority), so blindly retrying one unchanged is exactly the
    hammering rule 6 exists to stop. They consume the same budget and the
    same identical-attempt check as a semantic failure."""
    out = []
    for entry in attempt_history:
        kind = classify(entry["failure_class"])
        if kind in ("semantic", "other"):
            out.append(entry)
    return out


def _transient_attempts(attempt_history):
    return [e for e in attempt_history if classify(e["failure_class"]) == "transient"]


def decision(unit_id, failure_class, attempt_history, *, budgets):
    """The verdict for what happens after `unit_id`'s most recent attempt
    failed with `failure_class`.

    attempt_history is every attempt made so far for this unit, oldest
    first, each a dict carrying at least "failure_class" and the four
    fingerprint fields (files_written, check, strategy, base_revision);
    the LAST entry is the attempt that just produced `failure_class` (the
    two must agree; a caller passing a history whose last entry disagrees
    with `failure_class` has a bookkeeping bug and gets a ValueError
    rather than a silently wrong verdict).

    budgets is a dict which may carry "max_semantic_attempts" and/or
    "max_transient_retries". A budget key that is absent never exhausts;
    a budget key that is present caps the count of attempts in that
    class recorded in attempt_history (rule 1: a transient failure never
    contributes to the semantic count, only to the transient one, because
    no real work began).

    Returns a Decision(verdict, reason, required_change). required_change
    is a description of what the NEXT attempt must differ in, or None
    when there is no next attempt to constrain (RETRY needs no change;
    PARK and EXHAUSTED end this unit's own retry loop rather than shape
    one).

    Raises ValueError on an empty attempt_history, on a last entry that
    disagrees with `failure_class`, or on a failure_class classify() does
    not recognise (delegated, not re-checked here): an unrecognised or
    inconsistent input stops the caller's attention, it is never read as
    the safe case.
    """
    attempt_history = list(attempt_history or [])
    if not attempt_history:
        raise ValueError(
            "attempt_history must include the attempt that just failed")
    latest = attempt_history[-1]
    if latest.get("failure_class") != failure_class:
        raise ValueError(
            "attempt_history[-1]'s failure_class %r does not match the "
            "failure_class argument %r" % (latest.get("failure_class"), failure_class))

    kind = classify(failure_class)  # raises ValueError on an unknown class

    if kind == "authority":
        # Rule 3: an authority refusal never retries as a technical
        # failure, whatever the budgets say. Retrying a refusal is asking
        # the same question again hoping for a different rule.
        return Decision(
            PARK,
            "failure class %r is a policy refusal (authority); it is "
            "handed to a human rather than retried, changed or not" % (failure_class,),
            None)

    if kind == "transient":
        # Rule 1: transient failures retry after backoff without changing
        # the approach, and do not touch the semantic budget: no work
        # actually began, so counting it against the unit's semantic
        # budget would exhaust units that were never really tried. The
        # backoff itself is brother_run.py's _retry_backoff_seconds; this
        # module only says RETRY, it does not sleep or compute a duration.
        max_transient = budgets.get("max_transient_retries")
        used = len(_transient_attempts(attempt_history))
        if max_transient is not None and used >= max_transient:
            return Decision(
                EXHAUSTED,
                "transient retry budget exhausted: %d attempt(s) classified "
                "transient against a budget of %d (max_transient_retries)"
                % (used, max_transient),
                None)
        return Decision(
            RETRY,
            "failure class %r is transient; retry after backoff with the "
            "same approach, this attempt does not count against the "
            "semantic attempt budget" % (failure_class,),
            None)

    # kind is "semantic" or "other" from here down; both require a changed
    # approach and both are counted against the same semantic budget, see
    # _semantic_attempts().
    semantic_so_far = _semantic_attempts(attempt_history)
    used = len(semantic_so_far)
    max_semantic = budgets.get("max_semantic_attempts")
    if max_semantic is not None and used >= max_semantic:
        return Decision(
            EXHAUSTED,
            "semantic attempt budget exhausted: %d attempt(s) classified "
            "semantic or other against a budget of %d (max_semantic_attempts)"
            % (used, max_semantic),
            None)

    # Rule 4: attempt N cannot be identical to attempt N minus 1. Compare
    # the latest semantic-or-other attempt against the one immediately
    # before it in that same sub-sequence (not against every prior
    # attempt: an approach that was tried, abandoned for a different one,
    # then genuinely returned to is a re-plan decision to make explicitly,
    # not something this mechanical check should silently allow or block).
    if len(semantic_so_far) >= 2:
        previous = semantic_so_far[-2]
        if same_approach(previous, latest):
            return Decision(
                REPLAN,
                "the attempt that just failed (%r) fingerprints identically "
                "to the previous semantic attempt (%r): same files written, "
                "same deciding check, same declared strategy, same base "
                "revision. A worker's own claim of a changed approach is "
                "not evidence; the fingerprint is. This stops here rather "
                "than spending a third identical attempt"
                % (fingerprint(latest), fingerprint(previous)),
                "a recorded re-plan: name the approach being abandoned and "
                "why, before any further attempt at this unit")

    return Decision(
        RETRY_CHANGED,
        "failure class %r requires a changed approach before the next "
        "attempt (%d semantic/other attempt(s) so far, budget %r)"
        % (failure_class, used, max_semantic),
        "the next attempt must carry this failure's evidence and change at "
        "least one of: the files it writes, the deciding check it runs, "
        "the declared strategy label, or the base revision it starts from")
