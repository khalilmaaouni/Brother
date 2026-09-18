#!/usr/bin/env python3
"""budget_floor: the minimum token budget for one model call, split into the
three figures that must never be collapsed into one number.

WHY THIS EXISTS, ORCH-28. The original design derived a floor from payload
size alone. That institutionalises one exceptional spend (a huge one-off
input) as the minimum price of every call, forever, including the ordinary
short ones that follow it. Two calls with the same tiny input can need wildly
different budgets depending on how long the answer is expected to run and how
many retries the caller is willing to fund, so this module keeps three
figures separate and never lets one stand in for another:

    INPUT CAPACITY     how much of the model's own context window this
                        call's input actually uses. A ceiling the call must
                        fit under, not a price.
    ANSWER FLOOR        how many tokens the expected answer needs, padded for
                        reasoning overhead when the model has any.
    ATTEMPT CEILING     the most this one logical attempt (first try plus
                        every doubled retry) may ever spend in total. A cap
                        the caller chose, not something this module invents.

REASONING OVERHEAD is real and already measured, not guessed here: see
~/.claude/bin/or_ask.py, EFFORT_MAX_FLOOR and its neighbouring comment (read
2026-09-18): "a low --max with --effort set lets hidden reasoning tokens
spend the whole budget before any visible content is written, which reads as
an empty answer". That file also retries once at double the budget on an
empty answer whose finish_reason is "length". This module generalises both
findings into floor() and retry_budget() below, without importing or_ask.py
(it lives outside this repository, in ~/.claude/bin, and is not part of the
package this unit owns) and without inventing new numbers for models or
context windows or_ask.py never measured.

NO MODEL CATALOG LIVES HERE ON PURPOSE. A model's context window and whether
it reasons before answering are facts about that model, not about a budget
calculation, and guessing them would be exactly the kind of invented value
the estate's fabrication rule forbids. The caller supplies a `profile` for
the model it is actually calling (see floor()); an unrecognised or missing
profile refuses rather than defaulting to some other model's numbers.

CONCURRENCY. Every function here is pure: no module-level mutable state, no
file, no clock. Two callers computing a floor for the same or different
calls at the same time never interact, so there is nothing here for a
concurrent-second-actor edge to corrupt.

OUT OF SCOPE, named rather than silently skipped: recording which model
actually answered (ORCH-29), effort above the estate's cap citing its
authorisation (ORCH-30), and pinning the bridge's default model (ORCH-31)
are separate units and separate files. Whether the SAME caller already spent
part of an attempt ceiling on a PRIOR call is also out of scope: this module
computes one number for one call given the figures it is handed, it does not
remember prior calls, so "already done" / "partially done" attempt state is
the caller's ledger to keep, not this module's.

Python 3.9 floor, standard library only, no network, no file I/O.
"""
import math


class UnknownModel(ValueError):
    """Raised when no usable profile was supplied for the named model.
    Never caught internally and turned into a default: an unrecognised model
    stops the machine (worker contract rule 2), it does not borrow another
    model's context window or reasoning behaviour."""


class InvalidBudgetInput(ValueError):
    """Raised for a non-numeric, negative, boolean-typed, or otherwise
    out-of-contract figure (input_tokens, expected_answer_tokens,
    attempt_ceiling_tokens), and for an attempt_ceiling_tokens too small to
    fund even one try."""


class InputCapacityExceeded(ValueError):
    """Raised when the input plus the reserved answer floor would not fit
    in the model's own context window. This is a capacity refusal, not a
    price: no floor is computed for a call that cannot physically run."""


class BudgetExhausted(ValueError):
    """Raised by retry_budget() when the previous attempt already reached
    (or was never below) the attempt ceiling, so no retry budget exists that
    is both larger than the previous one and within the ceiling."""


# Coarse, stdlib-only estimate (no tokenizer dependency: rule 3, standard
# library only). 4 characters per token is the same order-of-magnitude
# approximation OpenAI and Anthropic both publish for English prose; callers
# who already have a real token count should pass it directly to floor()
# instead of going through this helper.
CHARS_PER_TOKEN = 4


def _require_nonneg_int(value, name):
    # bool is a subclass of int in Python; True/False silently becoming 1/0
    # here would be exactly the kind of unrecognised-value default rule 2
    # forbids, so it is excluded explicitly rather than accidentally accepted.
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidBudgetInput(
            "%s must be a plain int, got %r" % (name, value))
    if value < 0:
        raise InvalidBudgetInput("%s must not be negative, got %d" % (name, value))
    return value


def _require_pos_int(value, name):
    _require_nonneg_int(value, name)
    if value == 0:
        raise InvalidBudgetInput("%s must be greater than zero, got 0" % name)
    return value


def estimate_input_tokens(char_count):
    """A coarse token estimate from a character count. char_count == 0 is a
    valid, meaningful input (an empty payload), and returns 0, not a raised
    error: zero-length input is a real edge this function must pass through
    cleanly, never treat as missing."""
    _require_nonneg_int(char_count, "char_count")
    if char_count == 0:
        return 0
    return math.ceil(char_count / CHARS_PER_TOKEN)


def floor(model, profile, input_tokens, expected_answer_tokens, attempt_ceiling_tokens):
    """Compute the three-figure budget floor for one model call.

    model: a string naming the model, used only for the error message on
        refusal; never looked up in any catalog this module owns.
    profile: a dict the CALLER supplies for this exact model, shaped
        {"context_tokens": int > 0, "reasoning": bool}. None, a non-dict, or
        a dict missing either key is refused as UnknownModel: this module
        never guesses a model's context window or reasoning behaviour from
        its name.
    input_tokens: tokens this call's own input actually measures right now.
        Never a remembered peak from a previous, larger call: passing a
        stale large figure here would recreate exactly the single-exceptional-
        spend-becomes-the-floor bug ORCH-28 exists to remove.
    expected_answer_tokens: how long the caller expects the answer to run.
        Must be a positive int; a zero-token answer is not a completion call.
    attempt_ceiling_tokens: the most this whole attempt (first try plus every
        retry) may ever spend. Chosen by the caller, never invented here.

    Returns a dict: input_floor, answer_floor, reasoning_reserve,
    attempt_ceiling, first_try_max. first_try_max is what the caller should
    pass as its first --max / max_tokens. It never exceeds
    attempt_ceiling_tokens: when the answer floor alone (plus any reasoning
    reserve) would exceed the ceiling, this function refuses with
    InvalidBudgetInput rather than silently capping the try at a budget too
    small to actually run it.

    Raises UnknownModel, InvalidBudgetInput, or InputCapacityExceeded. Never
    returns a partial or best-guess result: a call this function cannot
    validate is refused, not started.
    """
    if not isinstance(profile, dict) or "context_tokens" not in profile or "reasoning" not in profile:
        raise UnknownModel(
            "no usable budget profile for model %r; the caller must supply "
            "one (context_tokens, reasoning) rather than have one guessed" % (model,))
    context_tokens = profile["context_tokens"]
    reasoning = profile["reasoning"]
    _require_pos_int(context_tokens, "profile['context_tokens']")
    if not isinstance(reasoning, bool):
        raise InvalidBudgetInput(
            "profile['reasoning'] must be a bool, got %r" % (reasoning,))

    input_floor = _require_nonneg_int(input_tokens, "input_tokens")
    answer_floor = _require_pos_int(expected_answer_tokens, "expected_answer_tokens")
    attempt_ceiling = _require_pos_int(attempt_ceiling_tokens, "attempt_ceiling_tokens")

    # Reasoning overhead: a reasoning model can spend the whole visible
    # budget on hidden reasoning before writing any answer at all (measured
    # in or_ask.py, cited in the module docstring above), so its answer
    # floor is doubled rather than trusted at face value.
    reasoning_reserve = answer_floor if reasoning else 0
    needed_for_one_try = answer_floor + reasoning_reserve

    if needed_for_one_try > attempt_ceiling:
        raise InvalidBudgetInput(
            "attempt_ceiling_tokens %d is below %d, the minimum needed to "
            "make even one try (answer_floor %d + reasoning_reserve %d)"
            % (attempt_ceiling, needed_for_one_try, answer_floor, reasoning_reserve))

    if input_floor + needed_for_one_try > context_tokens:
        raise InputCapacityExceeded(
            "input_tokens %d plus the %d needed for one try exceeds this "
            "model's context_tokens %d; this call cannot fit, at any price"
            % (input_floor, needed_for_one_try, context_tokens))

    # needed_for_one_try <= attempt_ceiling is already guaranteed by the
    # check above (it raises otherwise), so first_try_max is exactly what
    # one try needs, never a smaller silently-capped number: capping would
    # hide the fact that the try can't actually run at that budget.
    first_try_max = needed_for_one_try

    return {
        "input_floor": input_floor,
        "answer_floor": answer_floor,
        "reasoning_reserve": reasoning_reserve,
        "attempt_ceiling": attempt_ceiling,
        "first_try_max": first_try_max,
    }


def retry_budget(previous_max_tokens, attempt_ceiling_tokens):
    """The budget for the next retry after an empty or truncated answer:
    double the previous try, capped at attempt_ceiling_tokens, and never
    equal to previous_max_tokens (a retry at the identical budget would
    reproduce the identical empty answer, per or_ask.py's own comment on
    this exact failure mode).

    Raises BudgetExhausted when previous_max_tokens has already reached (or
    somehow exceeds) attempt_ceiling_tokens: there is no room left for a
    retry that is both larger and within the ceiling, so the caller must
    stop, not silently retry at the same or a smaller budget.
    """
    previous_max_tokens = _require_pos_int(previous_max_tokens, "previous_max_tokens")
    attempt_ceiling_tokens = _require_pos_int(attempt_ceiling_tokens, "attempt_ceiling_tokens")

    if previous_max_tokens >= attempt_ceiling_tokens:
        raise BudgetExhausted(
            "previous_max_tokens %d has already reached the attempt "
            "ceiling %d; no larger retry budget is available"
            % (previous_max_tokens, attempt_ceiling_tokens))

    doubled = previous_max_tokens * 2
    next_max = min(doubled, attempt_ceiling_tokens)
    if next_max == previous_max_tokens:
        # Unreachable given the strict '>=' check above (min() can only tie
        # previous_max_tokens if the ceiling itself equals it, already
        # excluded), kept as a guard so this invariant is never silently
        # broken by a future edit to the arithmetic above it.
        raise BudgetExhausted(
            "computed retry budget %d is identical to the previous try; "
            "refusing rather than repeating the same call" % (next_max,))
    return next_max
