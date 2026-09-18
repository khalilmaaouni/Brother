#!/usr/bin/env python3
"""ORCH-00 of the 1.0.20 orchestration control plane: the frozen vocabulary.

WHY THIS EXISTS. Every later row of this build (the dispatcher, the claim
store, the verifier, the merge gate) needs the same words for the same
things: what a task can be, what a verdict means, which failures retry and
which ones stop the line. If each row restates its own copy of these lists in
prose, the rows drift apart the first time one of them is edited and the
others are not. This module is the one place those words are spelled, so
every later row IMPORTS from here instead of re-typing them. See
docs/decisions/ORCH-1020-INVARIANTS-2026-09-18.md for why each entry exists
and what it would cost to be wrong.

THE CENTRAL RULE THIS MODULE ENFORCES: NO-DATA is never a pass, under any
obligation except OPTIONAL. A missing measurement is not evidence of
success, it is the absence of evidence, and the one thing worse than a
FAILing check is a NO-DATA one that gets read as green. See may_proceed().

THE OTHER RULE THIS MODULE ENFORCES: an unrecognised input is never read as
the safe case. may_proceed(), is_terminal() and classify_failure() all raise
ValueError on a name outside their known set, on purpose: silently treating
an unknown verdict as FAIL, an unknown state as non-terminal, or an unknown
failure as transient-and-retryable is exactly the kind of default that lets
a real defect pass through disguised as ordinary operation.

Python 3.9 floor, standard library only, no network. This module imports
exactly one local module, scripts/evidence_obligation.py, because that
module is already the enforced definition site for the verdict and
evidence-obligation vocabularies (docs/decisions/evidence-vocabulary-
2026-09-13.json, rule EV-3: "DEFINED ONCE and imported by every other
vocabulary", naming evidence_obligation.py because it is the module wired
into the enforced merge gate, scripts/required_fast.sh). Retyping those two
lists here instead of importing them let this module and that gate answer
opposite questions about the same NO-DATA case; see may_proceed() below and
docs/decisions/ORCH-1020-INVARIANTS-2026-09-18.md for the incident that
forced this. Every other name in this module (the invariants, task classes,
task states, failure taxonomy) has no other definition site and is typed
here directly.
"""

import evidence_obligation

SCHEMA_VERSION = "orchestrator-v1"

# Priority order matters: this is the hierarchy a conflict is resolved by,
# not an alphabetical or historical list. A reordering here is a real
# defect, which is why test_orchestrator_invariants.py asserts the exact
# tuple, in this exact order, rather than just its membership.
INVARIANTS = (
    "no false green",
    "no dual authority",
    "no stale authority",
    "no worker without a claim",
    "no concurrent writers with overlapping scope",
    "no direct agent merge to canonical",
    "no mandatory NO-DATA treated as success",
    "no RED action automated",
    "no transcript-only state",
    "no idle capacity while safe READY work exists",
)

TASK_CLASSES = frozenset((
    "planning", "architecture", "research", "implementation", "test",
    "verification", "review", "repair", "fault-injection", "documentation",
    "packaging", "benchmark", "integration-prep",
))

# Derived from evidence_obligation.py, never retyped: that module is the
# frozen definition site (EV-3). Three levels, matching the enforced gate,
# which refuses a fourth (validate_obligations raises "unknown level" on
# anything outside evidence_obligation.LEVELS).
EVIDENCE_OBLIGATIONS = frozenset(evidence_obligation.LEVELS)

# Derived from evidence_obligation.py, never retyped: see EVIDENCE_OBLIGATIONS
# above.
VERDICTS = frozenset(evidence_obligation.VERDICTS)

TASK_STATES = frozenset((
    "PLANNED", "READY", "ORCHESTRATOR-CLAIMED", "WORKER-CLAIMED", "RUNNING",
    "WORKER-PASS", "WORKER-FAIL", "VERIFYING", "REVIEW", "REPAIRABLE",
    "NEEDS-REPLAN", "INTEGRATING", "INTEGRATED", "NEEDS-REPAIR-ON-NEW-BASE",
    "CANONICAL-VERIFY", "DONE", "PARKED", "EXHAUSTED", "AWAITING-HUMAN",
    "CANCELLED",
))

TERMINAL_STATES = frozenset((
    "DONE", "PARKED", "EXHAUSTED", "AWAITING-HUMAN", "CANCELLED",
))

FAILURE_CLASSES = frozenset((
    "rate_limit", "overloaded", "timeout", "empty", "worker_crash",
    "scope_violation", "test_failure", "integration_conflict",
    "canonical_regression", "missing_evidence", "tool_unavailable",
    "resource_pressure", "policy_refusal", "unknown",
))

# Retry with a fresh attempt: the environment misbehaved, not the work.
TRANSIENT_FAILURES = frozenset((
    "rate_limit", "overloaded", "timeout", "tool_unavailable",
))

# The work itself is wrong: retrying the same attempt unchanged wastes a
# turn and hides the real defect. These go to repair or replan, never a
# bare retry.
SEMANTIC_FAILURES = frozenset((
    "test_failure", "canonical_regression", "scope_violation",
    "integration_conflict",
))

# Not a technical failure at all: something with the standing rules of this
# estate refused the action. Never retried automatically; it goes to a
# human, because retrying a policy refusal is asking the same question
# again in the hope of a different rule.
AUTHORITY_FAILURES = frozenset(("policy_refusal",))

ACTIONS = frozenset((
    "NOOP", "DISPATCH", "REQUEST-REVIEW", "REQUEST-REPAIR", "REQUEST-REPLAN",
    "PROPOSE-DECOMPOSITION", "HANDOFF", "PARK", "QUEUE-HUMAN", "CLOSEOUT",
))

HEALTH_STATES = frozenset((
    "STARTING", "HEALTHY", "THINKING", "WAITING-WORKER", "WAITING-REVIEW",
    "DRAINING", "DEGRADED", "UNAVAILABLE", "STOPPED",
))


STAGES = ("merge", "release")


def may_proceed(verdict, obligation, stage):
    """True only when this verdict satisfies this obligation at this stage.

    stage must be "merge" or "release". It exists because evidence_obligation.
    transition() (the enforced gate) does not answer this question with only
    a verdict and an obligation: a REQUIRED_FOR_RELEASE obligation binds only
    at release, so NO-DATA against it is allowed to proceed at "merge" and
    blocked at "release". This function must return the same answer the gate
    does, and the drift test below proves it on a live obligations file.

    PASS always proceeds and FAIL never does, at either stage and under
    every obligation: that is what evidence_obligation.transition() does
    unconditionally at its FAIL and PASS branches, before it ever looks at
    obligation or stage.

    NO-DATA proceeds only under OPTIONAL, or under REQUIRED_FOR_RELEASE at
    stage "merge" (an obligation that only binds at release must not block
    a merge). NO-DATA against REQUIRED_FOR_MERGE is blocked at both stages,
    because that obligation binds at the point NO-DATA was produced.

    Raises ValueError on a verdict, obligation or stage this module does
    not know, rather than returning a default: a caller mistyping any of
    the three must see that immediately, not have it read as an unwritten
    False.
    """
    if verdict not in VERDICTS:
        raise ValueError("unknown verdict: %r" % (verdict,))
    if obligation not in EVIDENCE_OBLIGATIONS:
        raise ValueError("unknown evidence obligation: %r" % (obligation,))
    if stage not in STAGES:
        raise ValueError("unknown stage: %r" % (stage,))
    if verdict == "FAIL":
        return False
    if verdict == "PASS":
        return True
    # verdict == "NO-DATA"
    if obligation == "OPTIONAL":
        return True
    if obligation == "REQUIRED_FOR_RELEASE" and stage == "merge":
        return True
    return False


def is_terminal(state):
    """True when this task state is one this task will not leave on its
    own. Raises ValueError on an unknown state: a caller must never read
    a typo or a not-yet-added state as "still in flight" (False) just
    because it failed to match TERMINAL_STATES.
    """
    if state not in TASK_STATES:
        raise ValueError("unknown task state: %r" % (state,))
    return state in TERMINAL_STATES


def classify_failure(name):
    """'transient', 'semantic', 'authority' or 'other' for a known failure
    class. Raises ValueError on a name outside FAILURE_CLASSES: an
    unrecognised failure must stop the dispatcher's attention, never be
    silently read as transient and handed a free retry.

    The literal string "unknown" is itself a member of FAILURE_CLASSES: a
    worker may legitimately report its own failure as "unknown" (it does
    not know why it failed), and that is a normal, classified "other"
    result. This is not the same case as passing this function a name it
    has never heard of, which is the ValueError case above: "unknown" the
    reported class is data, an unrecognised name is a defect in the
    caller.
    """
    if name not in FAILURE_CLASSES:
        raise ValueError("unknown failure class: %r" % (name,))
    if name in TRANSIENT_FAILURES:
        return "transient"
    if name in SEMANTIC_FAILURES:
        return "semantic"
    if name in AUTHORITY_FAILURES:
        return "authority"
    return "other"
