#!/usr/bin/env python3
"""Quality bar and batch landing plans for P0.5.

This module implements the P0.5 quality gate and batch landing planner.
Standard library only, Python 3.9 floor.

quality_gate_check(probe_clean) returns True only for a clean probe bool.
A False probe result is a refusal value: the quality gate blocks, and the
caller must rerun the probe. Anything whose EXACT type is not bool (None,
an empty string, a corrupt probe log object, a number, a hostile object) is
refused with ValueError. The refusal is decided by TYPE alone, so no
comparison, repr, str, bool or hash is ever run on the value itself, and a
hostile value cannot crash the guard.

batch_landing_plan(pending_count) returns a queue-ordered string plan.
Batches are sized to the work in progress limit of 2 units. A pending count
of 0 reports the batch already done without reland. Anything whose EXACT
type is not int (bool, float, str, None, an int subclass with hostile
comparisons) is refused with ValueError, again decided by TYPE alone before
any comparison runs, so a hostile __lt__ can never reach a comparison.
"""

WIP_LIMIT = 2
WORKERS_PER_LANE = 3
GRADER_CAP = 5
LANDING_SLOT_MIN_MINUTES = 5
LANDING_SLOT_MAX_MINUTES = 8
PROBE_TIMEOUT_SECONDS = 600
MAX_ROUNDS = 6
CLEAN_ROUNDS_REQUIRED = 2
CEILING_ROUNDS = 3


def quality_gate_check(probe_clean):
    """Return True only when the probe result is a clean bool True.

    A False probe result is a refusal value: the quality gate blocks and
    the caller reruns the probe. Any value whose exact type is not bool is
    hostile, empty, corrupt or missing input and raises ValueError, never
    guessed at, never silently accepted, and never inspected by running a
    comparison, repr, str, bool or hash on it.
    """
    if type(probe_clean) is not bool:
        raise ValueError(
            "quality_gate_check requires a bool probe result by exact "
            "type; empty, corrupt, missing or hostile probe input is "
            "refused"
        )
    return probe_clean


def batch_landing_plan(pending_count):
    """Return a queue-ordered landing plan for pending_count units.

    The plan is a string. Batches are sized to the work in progress limit
    (2 units) so one landing lane is never widened beyond its constraint.
    A pending_count of 0 reports the batch already done without reland.
    Any value whose exact type is not int is refused by TYPE before any
    comparison runs, so an int subclass with a hostile __lt__ cannot crash
    this function.
    """
    if type(pending_count) is not int:
        raise ValueError(
            "batch_landing_plan requires an int pending_count by exact "
            "type; missing, corrupt or hostile queue input is refused"
        )
    if pending_count < 0:
        raise ValueError(
            "batch_landing_plan refuses negative pending_count %d"
            % pending_count
        )
    if pending_count == 0:
        return "done: no pending batch to land; no reland needed"
    batches = []
    remaining = pending_count
    index = 1
    while remaining > 0:
        size = WIP_LIMIT if remaining >= WIP_LIMIT else remaining
        batches.append(
            "batch %d: %d unit%s" % (index, size, "" if size == 1 else "s")
        )
        remaining -= size
        index += 1
    return (
        "queue order " + "; ".join(batches)
        + " (landing slot 5 to 8 minutes; next build starts during landing)"
    )
