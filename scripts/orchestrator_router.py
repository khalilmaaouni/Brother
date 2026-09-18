"""ORCH-05 of the 1.0.20 orchestration control plane: the deterministic router.

WHAT THIS MODULE IS. Given one task and the observed state of the world (who
is healthy, who already holds a live lease on this task, who has already
failed it), route() names which orchestrator role should probably take the
task next, why, who the second choice is, and whether a cross-role review is
required.

WHAT THIS MODULE IS NOT, AND WHY THAT DISTINCTION IS THE WHOLE POINT. A
Recommendation is advice, never authority. Only orchestrator_authority's
lease (acquire, renew, takeover, handoff) grants the right to act on a
scope, and only after passing check() against a held epoch. This module
therefore:

  - never touches the authority store, never calls acquire/takeover/handoff,
    and takes no store_path or run_id argument to do so with;
  - returns a plain namedtuple with no method that could be mistaken for
    one that grants, checks, or spends authority (no .apply(), no .grant(),
    no .authorize()); a caller holding a Recommendation still has nothing
    but a name and a reason, exactly as if it had asked a person in the
    hallway;
  - reports an already-held live lease as "already owned" rather than ever
    recommending that a caller take it over. Deciding a lease may be
    reclaimed is orchestrator_authority.takeover()'s job, gated on the
    lease's own TTL, not this module's.

A caller that reads Recommendation.orchestrator and acts on the scope
without a lease held under check() has skipped the one control this whole
build exists to keep in place (no dual authority, no worker without a
claim). This module cannot stop that caller; it can only refuse to hand it
anything that looks like permission.

TWO ORCHESTRATOR ROLES, NAMED NEUTRALLY. Matching orchestrator_authority.py's
own convention (its own tests pass "primary" and "secondary" as the literal
`orchestrator` value of a lease), this module uses exactly those two role
names, never a vendor or model name:

  primary   the semantic role: the one that plans, researches, reviews and
            judges. Preferred first for planning, architecture, research,
            verification and review.
  secondary the execution role: the one already holding the working tree
            and running the loop. Preferred first for implementation,
            test, repair, fault-injection, packaging and integration-prep.

A NOTE ON A WORD COLLISION IN THE BRIEF THIS MODULE WAS BUILT FROM: the
worker contract for this unit describes the rules in terms of "the primary"
meaning "whichever role is this task's first choice", not "the role
literally named primary". For a semantic-class task those are the same
role; for an execution-class task they are opposites (the first choice is
secondary). To keep the two senses from colliding in code, this module
never uses a bare variable named `primary` for "first choice": internally
that concept is always `preferred` and `fallback`, and `primary`/`secondary`
appear only as the literal role-name strings that get returned to the
caller.

DOCUMENTATION AND BENCHMARK, THE "EITHER" CLASSES. The brief is explicit
that shrugging is not an option: each of these two classes is given a firm
default and a stated reason (see _PREFERENCE_REASON), not a coin flip and
not a silent tilt towards one role for every "either" class alike.

THE BAD STATE A GREEN RUN WOULD ALSO PASS: a router that recommends the same
orchestrator for every task class, which would satisfy any test that only
checks "route() returned something". test_orchestrator_router.py's
AtLeastTwoRolesAppear guards against exactly this by sweeping every real
TASK_CLASSES member and asserting both role names appear, then checking each
class against the table by name.

DEGRADED, THE JUDGEMENT CALL THE BRIEF ASKS FOR (rule 4): a DEGRADED
orchestrator still takes work. HEALTH_STATES treats DEGRADED as a distinct
state from UNAVAILABLE and STOPPED on purpose: an orchestrator whose loop is
slower or running under a partial capability has not stopped being able to
act, and routing every task away from a merely degraded role would turn two
simultaneously-degraded orchestrators (a plausible shared-cause event, for
example resource pressure) into a run with nothing dispatchable at all. Only
UNAVAILABLE and STOPPED, the two states this module's own HEALTH_STATES
vocabulary defines as "cannot act", cause a fallback to the other role. This
mirrors invariant "no idle capacity while safe READY work exists": refusing
degraded capacity that could still safely take work is its own defect.
DRAINING is left on the "still takes work" side of that same line for the
same reason: HEALTH_STATES does not name it as a stop-taking-work state
either, and this module adds no rule the brief did not ask for.

Determinism (rule 9): every branch below is a pure function of its
arguments. The only clock input is the injectable `now`, used solely to
re-check a passed-in lease's own expires_at rather than trust the caller's
claim that it is still live (this module's one piece of "no stale
authority" defense in depth); it defaults to time.time() only when the
caller does not supply one, exactly like orchestrator_authority's own `now`.

Python 3.9 floor, standard library only, no network.
"""

import time
from collections import namedtuple

import orchestrator_authority
import orchestrator_invariants

#: The two orchestrator roles this run recognises. See the module docstring
#: for why these two names and no others.
ROLES = frozenset(("primary", "secondary"))

#: risk_class is not part of orchestrator_invariants.py's frozen vocabulary
#: (that module holds task classes, task states, failure classes and
#: verdict/obligation levels, not risk levels); it is defined here directly
#: from docs/schema/orchestrator-task-v1.json's own enum, the one other
#: place it is spelled. If a second module ever needs this list, it should
#: move to orchestrator_invariants.py rather than be retyped a third time.
RISK_CLASSES = frozenset(("low", "medium", "high", "critical"))

#: Risk classes that require a cross-role review of whoever is recommended
#: (rule 7). Low and medium do not.
_CROSS_REVIEW_RISK = frozenset(("high", "critical"))

#: Health states in which a role cannot take work at all (rule 3). DEGRADED
#: is deliberately absent from this set: see the module docstring.
_BLOCKED_HEALTH = frozenset(("UNAVAILABLE", "STOPPED"))


def _other(role):
    """The role that is not `role`. Raises on anything outside ROLES so a
    typo'd role name fails immediately rather than quietly comparing equal
    to nothing and returning the wrong side."""
    if role not in ROLES:
        raise ValueError("unknown orchestrator role: %r" % (role,))
    return "secondary" if role == "primary" else "primary"


# THE DEFAULT PREFERENCE TABLE, one entry per real TASK_CLASSES member.
# Built directly against the frozenset rather than restating class names by
# hand elsewhere: _CLASS_TABLE_IS_COMPLETE below re-derives this same
# frozenset from this table's own keys and asserts the two are equal, so a
# task class added to orchestrator_invariants.py without a matching row
# here fails at import time instead of silently falling through to some
# default.
_TASK_CLASS_PREFERENCE = {
    "planning": "primary",
    "architecture": "primary",
    "research": "primary",
    "verification": "primary",
    "review": "primary",
    "implementation": "secondary",
    "test": "secondary",
    "repair": "secondary",
    "fault-injection": "secondary",
    "packaging": "secondary",
    "integration-prep": "secondary",
    # "either" classes (see the module docstring): each gets a firm default
    # and a reason, never a shrug.
    "documentation": "primary",
    "benchmark": "secondary",
}

_PREFERENCE_REASON = {
    "planning": "planning prefers the semantic role (primary), which plans and holds context",
    "architecture": "architecture prefers the semantic role (primary), which judges structure before code exists",
    "research": "research prefers the semantic role (primary), which gathers and weighs evidence",
    "verification": "verification prefers the semantic role (primary): judging whether evidence satisfies a claim is a semantic act",
    "review": "review prefers the semantic role (primary): a review is itself a judgement, not an execution step",
    "implementation": "implementation prefers the execution role (secondary), which already holds the working tree",
    "test": "test prefers the execution role (secondary), which runs the suite it is closest to",
    "repair": "repair prefers the execution role (secondary), which made or is nearest to the failing change",
    "fault-injection": "fault-injection prefers the execution role (secondary), which can run the injected fault against a live tree",
    "packaging": "packaging prefers the execution role (secondary), which assembles what it already built",
    "integration-prep": "integration-prep prefers the execution role (secondary), which is closest to the tree being prepared",
    "documentation": (
        "documentation could be either role's work; this table defaults it to "
        "the semantic role (primary) because the role documenting a decision "
        "should be the one that reasoned it out, not the execution role asked "
        "to originate a WHY it never held"
    ),
    "benchmark": (
        "benchmark could be either role's work; this table defaults it to the "
        "execution role (secondary) because a benchmark measures the working "
        "tree the execution role already holds, and routing it through the "
        "semantic role first would add a hop with no judgement in it"
    ),
}

_CLASS_TABLE_IS_COMPLETE = frozenset(_TASK_CLASS_PREFERENCE) == orchestrator_invariants.TASK_CLASSES
if not _CLASS_TABLE_IS_COMPLETE:
    _missing = orchestrator_invariants.TASK_CLASSES - frozenset(_TASK_CLASS_PREFERENCE)
    _extra = frozenset(_TASK_CLASS_PREFERENCE) - orchestrator_invariants.TASK_CLASSES
    raise RuntimeError(
        "orchestrator_router's preference table has drifted from "
        "orchestrator_invariants.TASK_CLASSES: missing %r, extra %r"
        % (sorted(_missing), sorted(_extra)))
assert frozenset(_PREFERENCE_REASON) == orchestrator_invariants.TASK_CLASSES, (
    "every task class needs a stated reason, not just a preferred role")


#: What route() returns. This carries no authority: see the module
#: docstring. `orchestrator` and `second_choice` are role names from ROLES,
#: or None when no recommendation can be made. `cross_review` is a role
#: name (the opposite of `orchestrator`) or None.
Recommendation = namedtuple(
    "Recommendation",
    "task_id orchestrator reason second_choice cross_review",
)


def _validate_task(task):
    """Returns (task_id, task_class, risk_class, allow_cross_takeover) or
    raises ValueError. Unknown task_class is the one refusal the brief
    names explicitly (rule 2): it is never defaulted to "implementation",
    it stops the router."""
    if not isinstance(task, dict):
        raise ValueError("task must be a dict shaped like orchestrator-task-v1.json, got %r" % (type(task),))
    task_id = task.get("id")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("task is missing a non-empty string id")
    task_class = task.get("task_class")
    if task_class not in orchestrator_invariants.TASK_CLASSES:
        raise ValueError("unknown task class: %r" % (task_class,))
    risk_class = task.get("risk_class")
    if risk_class not in RISK_CLASSES:
        raise ValueError("unknown risk class: %r" % (risk_class,))
    allow_cross = task.get("allow_cross_orchestrator_takeover", True)
    if not isinstance(allow_cross, bool):
        raise ValueError(
            "allow_cross_orchestrator_takeover must be a bool when present, got %r"
            % (allow_cross,))
    return task_id, task_class, risk_class, allow_cross


def _validate_health(health):
    """health must report BOTH roles. A role missing from the mapping is
    not read as any particular state (not HEALTHY, not UNAVAILABLE): it is
    a caller defect, refused rather than defaulted."""
    if not isinstance(health, dict):
        raise ValueError("health must be a dict mapping both orchestrator roles to a HEALTH_STATES member")
    for role in ROLES:
        if role not in health:
            raise ValueError(
                "health is missing role %r; both roles must be reported" % (role,))
    for role, state in health.items():
        if role not in ROLES:
            raise ValueError("unknown orchestrator role in health: %r" % (role,))
        if state not in orchestrator_invariants.HEALTH_STATES:
            raise ValueError("unknown health state for %r: %r" % (role, state))


def _attempt_role(item):
    """One attempt-history entry as a role name. Accepts either a bare role
    string or a dict carrying one under "orchestrator", so a caller can
    pass either its own attempt records or a plain list of role names.
    Raises on anything else, or on a role outside ROLES: an attempt record
    naming a role this module has never heard of is a defect to surface,
    never a name to silently ignore."""
    if isinstance(item, str):
        role = item
    elif isinstance(item, dict):
        role = item.get("orchestrator")
    else:
        raise ValueError(
            "attempt history entries must be a role string or a dict with "
            "an 'orchestrator' key, got %r" % (item,))
    if role not in ROLES:
        raise ValueError("unknown orchestrator role in attempt history: %r" % (role,))
    return role


def _failed_twice(attempts, role):
    """True once `role` has two or more entries in `attempts` (rule 8).
    None or an empty history means no failures recorded yet."""
    if not attempts:
        return False
    return sum(1 for item in attempts if _attempt_role(item) == role) >= 2


def _lease_field(leases, name):
    """One field off `leases`, which may be an orchestrator_authority.Lease
    namedtuple, an equivalent dict, or None. Duck-typed on purpose: this
    module never parses the authority store itself (see the module
    docstring), so it accepts whatever shape the caller's own
    orchestrator_authority.current() call handed back."""
    if leases is None:
        return None
    if isinstance(leases, dict):
        return leases.get(name)
    return getattr(leases, name, None)


def _live_lease_holder(leases, now):
    """The role that actively holds `leases` as of `now`, or None. Re-checks
    expires_at against `now` rather than trusting that a caller-supplied
    lease is still live: the caller may have called
    orchestrator_authority.current() some time before calling route(), and
    trusting that snapshot forever is exactly the kind of stale-authority
    belief this whole build exists to refuse. This check can only turn a
    claimed-live lease into "not live" here, never the reverse, so it
    cannot manufacture a conflict that was not already real."""
    if leases is None:
        return None
    role = _lease_field(leases, "orchestrator")
    if role not in ROLES:
        raise ValueError("unknown orchestrator role holding a lease: %r" % (role,))
    expires_at = _lease_field(leases, "expires_at")
    if expires_at is None or float(expires_at) <= now:
        return None
    return role


def _why_unusable(role, health):
    """One clause explaining why `role` cannot take the task right now,
    given it already failed the usability check. `health[role]` is always
    present because _validate_health requires both roles."""
    return "health is %s" % (health[role],)


def route(task, *, leases, health, attempts=None, now=None):
    """Recommend an orchestrator role for `task`. See the module docstring
    for what the return value is and is not.

    task    a dict shaped like docs/schema/orchestrator-task-v1.json. Only
            id, task_class, risk_class and the optional
            allow_cross_orchestrator_takeover are read.
    leases  the live lease for this task's own scope, as returned by
            orchestrator_authority.current(store_path, run_id, task["id"]):
            a Lease namedtuple, an equivalent dict, or None when the scope
            is free. This module never reads the authority store itself.
    health  a dict mapping BOTH role names in ROLES to a HEALTH_STATES
            member. A role missing from this mapping is refused, not
            defaulted (see _validate_health).
    attempts
            optional iterable of past attempts on this task, each either a
            role name string or a dict with an "orchestrator" key. Used
            only to count failures per role (rule 8); this module does not
            care about outcomes it is not told about, so pass only the
            failed attempts.
    now     optional float epoch-seconds, defaulting to time.time(). Used
            solely to re-check a passed-in lease's own liveness; see
            _live_lease_holder.

    Raises ValueError on an unknown task class, risk class, health state,
    orchestrator role, or a malformed task/health/attempts shape. Never
    returns a default recommendation for input it does not recognise.
    """
    task_id, task_class, risk_class, allow_cross = _validate_task(task)
    _validate_health(health)
    now = time.time() if now is None else float(now)

    preferred = _TASK_CLASS_PREFERENCE[task_class]
    fallback = _other(preferred)
    preferred_reason = _PREFERENCE_REASON[task_class]

    preferred_usable = (
        health[preferred] not in _BLOCKED_HEALTH
        and not _failed_twice(attempts, preferred)
    )
    fallback_usable = (
        health[fallback] not in _BLOCKED_HEALTH
        and not _failed_twice(attempts, fallback)
    )

    candidate = None
    reason = None

    if preferred_usable:
        candidate = preferred
        reason = preferred_reason
    elif allow_cross and fallback_usable:
        candidate = fallback
        # rule 3 / rule 8: name why the preferred role was skipped, whether
        # that is health or two prior failures, or both.
        skip_clauses = []
        if health[preferred] in _BLOCKED_HEALTH:
            skip_clauses.append(_why_unusable(preferred, health))
        if _failed_twice(attempts, preferred):
            skip_clauses.append("it has already failed this task twice")
        reason = (
            "%s; but %s is unavailable to take it (%s), so the router falls "
            "back to %s" % (preferred_reason, preferred, " and ".join(skip_clauses), fallback)
        )
    elif not allow_cross and not preferred_usable:
        # rule 6: the fallback exists but this task forbids naming it.
        skip_clauses = []
        if health[preferred] in _BLOCKED_HEALTH:
            skip_clauses.append(_why_unusable(preferred, health))
        if _failed_twice(attempts, preferred):
            skip_clauses.append("it has already failed this task twice")
        reason = (
            "%s cannot take task %s (%s), and this task sets "
            "allow_cross_orchestrator_takeover to false, so no recommendation "
            "is made rather than naming the forbidden fallback role"
            % (preferred, task_id, " and ".join(skip_clauses))
        )
    else:
        # Neither role is usable at all (this only happens when allow_cross
        # is true but the fallback is also unusable).
        preferred_clauses = []
        if health[preferred] in _BLOCKED_HEALTH:
            preferred_clauses.append(_why_unusable(preferred, health))
        if _failed_twice(attempts, preferred):
            preferred_clauses.append("it has already failed this task twice")
        fallback_clauses = []
        if health[fallback] in _BLOCKED_HEALTH:
            fallback_clauses.append(_why_unusable(fallback, health))
        if _failed_twice(attempts, fallback):
            fallback_clauses.append("it has already failed this task twice")
        reason = (
            "neither %s (%s) nor %s (%s) can take task %s right now"
            % (preferred, " and ".join(preferred_clauses), fallback,
               " and ".join(fallback_clauses), task_id)
        )

    if candidate is not None:
        # rule 5: a live lease held by anyone other than the candidate we
        # would otherwise recommend blocks the recommendation entirely.
        # A live lease already held BY the candidate is not a conflict, it
        # is just confirming who already has it.
        holder = _live_lease_holder(leases, now)
        if holder is not None and holder != candidate:
            return Recommendation(
                task_id=task_id,
                orchestrator=None,
                reason=(
                    "task %s is already owned by %s at epoch %s; the router "
                    "never recommends taking a live lease, only "
                    "orchestrator_authority.takeover() may reclaim one, and "
                    "only after its TTL has expired"
                    % (task_id, holder, _lease_field(leases, "epoch"))
                ),
                second_choice=None,
                cross_review=None,
            )

    if candidate is None:
        return Recommendation(
            task_id=task_id, orchestrator=None, reason=reason,
            second_choice=None, cross_review=None,
        )

    cross_review = _other(candidate) if risk_class in _CROSS_REVIEW_RISK else None
    return Recommendation(
        task_id=task_id, orchestrator=candidate, reason=reason,
        second_choice=_other(candidate), cross_review=cross_review,
    )
