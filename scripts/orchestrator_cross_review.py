#!/usr/bin/env python3
"""ORCH-11 of the 1.0.20 orchestration control plane: the cross-family
review gate.

WHY THIS EXISTS. High-risk work must not be accepted on the authoring
family's own narrative. If the same model family writes the code and
reviews the code, the review adds confidence without adding information,
which is worse than no review at all because it costs time and produces a
false sense of having checked.

THE ONE PROPERTY THAT MATTERS MOST, AND IT IS A FRESHNESS PROPERTY, NOT A
FAIRNESS ONE. A review verdict binds to an exact revision: a reviewer
looked at a specific tree. If the worker then pushes another commit, that
verdict describes code that no longer exists and must become STALE and
unreusable. This estate has 47 recorded notes in the stale-evidence class
and 7 confirmed cases of work thrown away against a tree that had moved
underneath it, so this is the measured failure, not a theoretical one. See
is_valid() and gate() below: staleness is checked mechanically, on every
gate() call, never assumed from "it was fine a minute ago."

HOW "MODEL FAMILY" IS DEFINED, AND WHY THAT IS NOT RESTATED HERE. This
module does not own a family taxonomy and does not build a second one.
scripts/coe_nominate.py already answers this question for the Council of
Experts subsystem: a family is read verbatim from whatever string a caller
supplies (that module reads it off a seat's own "model" field; this module
never touches seat records at all and simply takes author_family and
available_families as opaque, caller-supplied strings). Two strings are the
same family if and only if they are equal; nothing here groups, normalises
or aliases them, for the same reason coe_nominate's own docstring gives:
inventing a private mapping on top of a value that already means something
would itself be a duplicate-truth problem. This module DOES reuse one
concrete thing from coe_nominate: the risk classes that require a second
family at all (coe_nominate.RISK_CLASSES_REQUIRING_FAMILY_DIVERSITY, "high"
and "critical" today), because that is the exact same threshold COE-02's
own D4 rule already enforces for the same reason, and a second, private
copy of "high or critical" here would drift from it the first time either
is edited alone.

WHAT THIS MODULE DOES NOT REUSE FROM coe_nominate. Seat registries, the
outside-content gate, and Council/NominationRefused are COE-02's own
machinery for picking WHO sits on a panel from a declared seat pool. This
gate answers a narrower question, after some other part of the system
(worker dispatch) has already decided who authored the work and which
families are on shift: is a DIFFERENT family available, is the review that
family gave still true of the code on disk, and does the answer allow the
work through. No seat pool, no registry file, no outside-content scan.

THE FIVE RULES, EACH A TEST IN scripts/test_orchestrator_cross_review.py:

  1. A NEW COMMIT INVALIDATES THE VERDICT (is_valid, gate). The freshness
     check this rule needs.
  2. THE REVIEWER IS NEVER THE AUTHOR'S FAMILY (request). A request that
     cannot find a different family raises CrossReviewRefused; it never
     quietly falls back to the author.
  3. REQUIRED PLUS UNAVAILABLE EQUALS NO-DATA AND BLOCKS (gate). When
     `review` is None (request() could not be satisfied, so nothing was
     ever recorded) and `obligation` binds at this gate's stage, gate()
     reports verdict NO-DATA and may_proceed False. This module never
     writes its own verdict-versus-obligation table for this: it calls
     orchestrator_invariants.may_proceed(), the one place that table is
     allowed to live, so this gate and the merge gate can never quietly
     disagree about what NO-DATA against REQUIRED_FOR_MERGE means.
  4. OPTIONAL PLUS UNAVAILABLE PROCEEDS WITH EXPLICIT UNCERTAINTY (gate).
     Decision.uncertain is a field, not a sentence buried in `reason`, set
     True exactly when the verdict fed to may_proceed was NO-DATA (missing
     review, stale review, or a reviewer that itself answered NO-DATA), so
     a caller can count how often this happened without parsing prose.
  5. A REVIEW IS READ ONLY (module-level). Nothing in this file edits,
     merges, or marks a unit done; test_orchestrator_cross_review.py
     asserts this module exposes no such name.
  6. A VERDICT FOR A REVISION NOBODY ASKED ABOUT IS REFUSED (record).
     record() raises CrossReviewRefused when its `revision` keyword does
     not exactly equal the ReviewRequest's own `.revision`.

SHORT SHA VERSUS FULL SHA: EXACT EQUALITY, NEVER A PREFIX MATCH. is_valid()
and record()'s revision check both compare with `==` on the whole string,
never `startswith`. A prefix match would let a short SHA "fresh" a review
against any longer revision that happens to start with the same characters,
which is exactly the kind of coincidental agreement this gate exists to
refuse; the cost of a needless re-review (minutes) is far smaller than the
cost of accepting a stale review because two spellings of "close enough"
lined up (the defect this whole unit exists to catch). A caller that wants
a short SHA compared against a full one must normalise both to the same
length before calling in; this module will not guess which one is right.

CONTINGENCY. Every function here either returns its documented value or
raises CrossReviewRefused (the one exception type this module exposes,
matching coe_nominate's own "exactly one exception type" choice): there is
no third, half-finished outcome. Where a freshness comparison would
otherwise be ambiguous or unverifiable (an empty or non-string
current_revision, a missing review), the comparison resolves to STALE
(is_valid returns False), never to fresh: an ambiguous "yes" here is the
one path back into the defect this unit exists to catch, and an ambiguous
"no" only costs a redundant review. A caller that receives
CrossReviewRefused, or a Decision with may_proceed False, recovers by
routing the task to AWAITING-HUMAN or REQUEST-REVIEW (see
orchestrator_invariants.ACTIONS and TASK_STATES); this module never retries
anything itself and never picks that recovery path for the caller.

THE BAD STATE A GREEN RUN WOULD ALSO PASS. A freshness check hard-coded to
return False always would make every staleness test in this suite pass
(a review at a moved-on revision is correctly rejected) while silently
blocking every legitimate, still-current review too, which would make this
gate useless rather than safe: a task that could never clear review is
functionally the same dead end as a task whose stale reviews are always
accepted, just with the failure hidden behind an always-red gate instead of
an always-green one. TestFreshReviewGatesThrough below is the guard: a
review recorded at the CURRENT revision must be valid AND must gate
through, so this exact tautology cannot pass unnoticed.

EDGE LIST, WALKED EXPLICITLY.
  - empty available_families: request() refuses (see rule 2's docstring).
  - exactly one available family, and it is the author's: request()
    refuses (rule 2); the module never drops the requirement instead.
  - a revision that is an empty string: request() and record() both refuse
    an empty-string `revision` at construction time (a revision nobody
    could ever have been asking about is not a value worth carrying
    forward to fail more confusingly later). is_valid() and gate() go the
    other way on purpose: THEIR `current_revision` comes from the live
    world (a caller reading a checkout), not from this module's own
    inputs, so an empty or malformed current_revision there is treated as
    the ambiguous case above (STALE), never raised, matching the
    contingency rule that a freshness question always answers, it never
    throws.
  - two reviews for the same request: OUT OF SCOPE, stated plainly. This
    module holds no ledger of requests already answered; it is a pure set
    of functions over the objects a caller passes in, not a second claim
    store. Refusing a duplicate review needs a durable record of "already
    answered," which is exactly scripts/claim_store.py's job (see the
    worker contract: "compose them, do not build a second claim store").
    A caller wiring this gate into the real loop is expected to route
    record() through that store, once, per request.
  - a review recorded before its request existed: cannot happen through
    this module's own API (record() takes the ReviewRequest object as its
    first argument, so no Review can be built without one already
    existing), and rule 6's revision check refuses the one way a caller
    could still fake it (constructing a request-shaped stand-in whose
    revision does not match what was actually asked).
  - an unknown verdict string: record() refuses (validated against
    orchestrator_invariants.VERDICTS before a Review is built).
  - an unknown obligation: gate() refuses; it is caught from
    orchestrator_invariants.may_proceed()'s own ValueError and re-raised
    as CrossReviewRefused so this module still exposes exactly one
    exception type.
  - a task with no risk_class, or an unrecognised one: required_for()
    refuses rather than guessing whether cross-review applies (unknown
    input raises, never a permissive default).
  - a revision differing only by case or length: see the SHORT SHA section
    above. Exact equality; never fresh unless the strings are identical.

Python 3.9 floor, standard library only, no network, no subprocess. Imports
exactly scripts/orchestrator_invariants.py (VERDICTS, EVIDENCE_OBLIGATIONS,
may_proceed) and scripts/coe_nominate.py (RISK_CLASSES_REQUIRING_FAMILY_
DIVERSITY, KNOWN_RISK_CLASSES only, both frozen definition sites this
module reads from rather than restates.
"""

import orchestrator_invariants
import coe_nominate

#: The stage this gate evaluates at. gate()'s own signature (fixed by the
#: worker brief: task, review, *, current_revision, obligation, no stage
#: argument) has no room for a caller-supplied stage, and this gate exists
#: to decide whether high-risk work may be MERGED, never whether it may be
#: RELEASED (that is a separate, later gate's question over the same
#: evidence). Hard-coding "merge" here, rather than silently defaulting a
#: missing parameter to it, keeps that choice visible at the one call site
#: it is made rather than buried in a signature nobody reads twice.
_GATE_STAGE = "merge"


class CrossReviewRefused(Exception):
    """The one exception this module raises, for every reason it refuses
    to proceed: a malformed or empty revision, an available_families pool
    that holds no family but the author's, a verdict or obligation outside
    the frozen vocabularies, or a recorded verdict naming a revision the
    matching request never asked about. Mirrors coe_nominate.
    NominationRefused's own choice to expose exactly one exception type:
    a caller's recovery is the same shape regardless of which check
    failed, so there is nothing to gain from a taxonomy of exception
    subclasses here and a real cost (a caller forced to catch several
    types, or catching Exception broadly instead) if one were added.
    """


class ReviewRequest(object):
    """One request for a cross-family review, bound to the exact revision
    it was asked about. Immutable by convention, matching this estate's
    other result objects (coe_outside_gate.GateResult, coe_nominate.
    Council): construct a new one rather than mutating an existing
    request.

    task_id             whatever `task.get("id")` returned, or None when
                         the task carried no "id" key. Carried for audit
                         only; nothing in this module keys off it.
    author_family       the family that authored the work under review.
    reviewer_family      the family request() chose: the alphabetically
                         first distinct family among available_families,
                         excluding author_family. Deterministic on purpose
                         (same inputs, same choice, always), so a caller
                         auditing a dispatch never has to ask "which one
                         would it have picked this time."
    revision             the exact revision (a full identifier, this
                         module never guesses which prefix length is
                         "close enough") the reviewer is being asked to
                         look at.
    available_families   tuple of every distinct family request() actually
                         considered, sorted, for audit: proof of what the
                         choice was made from, not only what it landed on.
    """

    __slots__ = ("task_id", "author_family", "reviewer_family", "revision",
                 "available_families")

    def __init__(self, task_id, author_family, reviewer_family, revision,
                 available_families):
        self.task_id = task_id
        self.author_family = author_family
        self.reviewer_family = reviewer_family
        self.revision = revision
        self.available_families = tuple(available_families)

    def __repr__(self):
        return (
            "ReviewRequest(task_id=%r, author_family=%r, reviewer_family=%r,"
            " revision=%r)"
            % (self.task_id, self.author_family, self.reviewer_family,
               self.revision)
        )

    def __eq__(self, other):
        if not isinstance(other, ReviewRequest):
            return NotImplemented
        return (
            self.task_id == other.task_id
            and self.author_family == other.author_family
            and self.reviewer_family == other.reviewer_family
            and self.revision == other.revision
            and self.available_families == other.available_families
        )


class Review(object):
    """One reviewer family's answer to one ReviewRequest, at one revision.
    Read only: nothing on this object, and no function in this module,
    edits a file, merges anything, or marks any unit done (rule 5). A
    Review is evidence a gate reads, never an action a gate takes.

    findings and evidence are copied into a tuple and a dict respectively
    at construction time, so a caller mutating the collection it passed in
    afterward cannot reach back and change a Review already recorded.
    """

    __slots__ = ("task_id", "author_family", "reviewer_family", "revision",
                 "verdict", "findings", "evidence")

    def __init__(self, task_id, author_family, reviewer_family, revision,
                 verdict, findings, evidence):
        self.task_id = task_id
        self.author_family = author_family
        self.reviewer_family = reviewer_family
        self.revision = revision
        self.verdict = verdict
        self.findings = tuple(findings) if findings else ()
        self.evidence = dict(evidence) if evidence else None

    def __repr__(self):
        return (
            "Review(task_id=%r, reviewer_family=%r, revision=%r, verdict=%r)"
            % (self.task_id, self.reviewer_family, self.revision,
               self.verdict)
        )

    def __eq__(self, other):
        if not isinstance(other, Review):
            return NotImplemented
        return (
            self.task_id == other.task_id
            and self.author_family == other.author_family
            and self.reviewer_family == other.reviewer_family
            and self.revision == other.revision
            and self.verdict == other.verdict
            and self.findings == other.findings
            and self.evidence == other.evidence
        )


class Decision(object):
    """gate()'s answer: the verdict this gate is acting on, whether the
    work may proceed, and whether that verdict rests on real, fresh
    evidence or on an explicit absence of it.

    verdict        one of orchestrator_invariants.VERDICTS: the review's
                   own verdict when a fresh Review was available, else
                   "NO-DATA" (no review at all, or a stale one, carries no
                   usable evidence either way).
    may_proceed    bool, exactly what orchestrator_invariants.may_proceed(
                   verdict, obligation, "merge") returned; this class
                   never recomputes that table itself.
    uncertain      bool, True exactly when verdict is "NO-DATA": the field
                   rule 4 asks for, so a caller can count how often work
                   proceeded (or was blocked) on an absence of evidence
                   without parsing `reason`.
    fresh          bool, True only when a Review was supplied AND
                   is_valid() returned True for it. False whenever `review`
                   was None, or is_valid() said stale.
    task_id        audit only, `task.get("id")` or None.
    reviewer_family audit only: the family whose verdict this Decision is
                   built from, or None when there was none to build from.
    reason         human-readable, built only from the fields above.
    """

    __slots__ = ("verdict", "may_proceed", "uncertain", "fresh", "task_id",
                 "reviewer_family", "reason")

    def __init__(self, verdict, may_proceed, uncertain, fresh, task_id,
                 reviewer_family, reason):
        self.verdict = verdict
        self.may_proceed = bool(may_proceed)
        self.uncertain = bool(uncertain)
        self.fresh = bool(fresh)
        self.task_id = task_id
        self.reviewer_family = reviewer_family
        self.reason = reason

    def __repr__(self):
        return (
            "Decision(verdict=%r, may_proceed=%r, uncertain=%r, fresh=%r)"
            % (self.verdict, self.may_proceed, self.uncertain, self.fresh)
        )


def _require_nonempty_str(value, label):
    if not isinstance(value, str) or not value:
        raise CrossReviewRefused(
            "%s must be a non-empty string, got %r" % (label, value))


def required_for(task):
    """True when `task["risk_class"]` is one of the risk classes
    coe_nominate already requires a second model family for (D4: "high" or
    "critical" today, coe_nominate.RISK_CLASSES_REQUIRING_FAMILY_
    DIVERSITY). False for "low" or "medium". Raises CrossReviewRefused on a
    task with no risk_class, or one outside coe_nominate.KNOWN_RISK_CLASSES:
    an unclassified task is not "does not need review", it is a caller
    error this function refuses to guess past.
    """
    if not isinstance(task, dict):
        raise CrossReviewRefused(
            "task must be a dict, got %s" % type(task).__name__)
    if "risk_class" not in task:
        raise CrossReviewRefused("task is missing required key 'risk_class'")
    risk_class = task["risk_class"]
    if risk_class not in coe_nominate.KNOWN_RISK_CLASSES:
        raise CrossReviewRefused(
            "task declares unknown risk_class %r (must be one of %s)"
            % (risk_class, sorted(coe_nominate.KNOWN_RISK_CLASSES)))
    return risk_class in coe_nominate.RISK_CLASSES_REQUIRING_FAMILY_DIVERSITY


def request(task, *, author_family, available_families, revision):
    """A ReviewRequest naming a reviewer family that is not author_family,
    bound to `revision`. Deterministic: the same three inputs always pick
    the same reviewer (the alphabetically first distinct family other than
    the author's), never a set's own iteration order.

    Raises CrossReviewRefused when: author_family is not a non-empty
    string; revision is not a non-empty string (rule: an empty revision is
    never a value worth carrying forward, see the module docstring's edge
    list); available_families is a bare string (the classic Python trap
    of iterating a string as a sequence of one-character "families"),
    is empty, or contains no family other than author_family. This
    function never falls back to nominating the author (rule 2): a caller
    that cannot supply a second family gets a refusal to act on (route the
    task to AWAITING-HUMAN, or treat it as NO-DATA through gate() with
    review=None), never a review that would silently defeat the point of
    asking for one.
    """
    _require_nonempty_str(author_family, "author_family")
    _require_nonempty_str(revision, "revision")
    if isinstance(available_families, str):
        raise CrossReviewRefused(
            "available_families must be a collection of family strings, "
            "not a bare string (which iterates as one family per "
            "character): got %r" % (available_families,))
    try:
        candidates = set(available_families)
    except TypeError:
        raise CrossReviewRefused(
            "available_families must be iterable, got %s"
            % type(available_families).__name__)
    if not candidates:
        raise CrossReviewRefused(
            "available_families is empty; no reviewer family exists to "
            "request")
    for family in candidates:
        _require_nonempty_str(family, "each entry in available_families")
    others = sorted(candidates - {author_family})
    if not others:
        raise CrossReviewRefused(
            "the only available family is %r, the author's own; a cross "
            "family review may never fall back to the author (rule 2)"
            % (author_family,))
    reviewer_family = others[0]
    task_id = task.get("id") if isinstance(task, dict) else None
    return ReviewRequest(
        task_id=task_id, author_family=author_family,
        reviewer_family=reviewer_family, revision=revision,
        available_families=sorted(candidates))


def record(review_request, verdict, *, revision, findings=None, evidence=None):
    """A Review answering `review_request`, at `revision`.

    Raises CrossReviewRefused when: `review_request` is not a
    ReviewRequest; `verdict` is not a member of orchestrator_invariants.
    VERDICTS; or `revision` does not exactly equal `review_request.
    revision` (rule 6: a verdict for a revision nobody asked about is the
    subtlest version of the whole failure class this unit exists to catch,
    a reviewer answering about a different tree). Exact string equality
    only, never a prefix match: see the module docstring's SHORT SHA
    section.
    """
    if not isinstance(review_request, ReviewRequest):
        raise CrossReviewRefused(
            "review_request must be a ReviewRequest, got %s"
            % type(review_request).__name__)
    if verdict not in orchestrator_invariants.VERDICTS:
        raise CrossReviewRefused(
            "unknown verdict %r (must be one of %s)"
            % (verdict, sorted(orchestrator_invariants.VERDICTS)))
    if revision != review_request.revision:
        raise CrossReviewRefused(
            "record() given revision %r but the request asked about %r; "
            "a verdict for a revision nobody asked about is refused"
            % (revision, review_request.revision))
    return Review(
        task_id=review_request.task_id,
        author_family=review_request.author_family,
        reviewer_family=review_request.reviewer_family,
        revision=revision, verdict=verdict, findings=findings,
        evidence=evidence)


def is_valid(review, *, current_revision):
    """True only when `review` is a Review AND `current_revision` exactly
    equals `review.revision`.

    Never raises. An ambiguous or unverifiable `current_revision` (not a
    string, or an empty one) resolves to False, not an exception: this is
    a freshness QUESTION, always answered, and the contingency rule this
    module states once, in its docstring, is that ambiguity here always
    resolves to STALE, because a needless re-review costs minutes and a
    wrongly-accepted stale review costs the defect this unit exists to
    catch. `review=None` (no review was ever recorded) is likewise simply
    False, never an error: gate() relies on exactly this to treat "no
    review" and "a stale review" the same way, as no usable evidence.

    Exact string equality only, case-sensitive, never a prefix match: a
    short SHA and the full SHA of the very same commit compare unequal
    here on purpose (see the module docstring's SHORT SHA section). A
    caller holding a short SHA must normalise it to the full revision
    before calling in; this function will not guess.
    """
    if review is None:
        return False
    if not isinstance(current_revision, str) or not current_revision:
        return False
    return current_revision == review.revision


def gate(task, review, *, current_revision, obligation):
    """The one answer this unit produces: given a task, the review (or
    None) standing for it, the revision the work is actually at right
    now, and the evidence obligation that binds this gate, a Decision
    combining the review's verdict, its freshness, and
    orchestrator_invariants.may_proceed()'s own table, never a second copy
    of that table.

    `obligation` must be a member of orchestrator_invariants.
    EVIDENCE_OBLIGATIONS. This function evaluates at the "merge" stage
    (see _GATE_STAGE): this gate exists to decide whether high-risk work
    may be merged, never whether it may be released.

    Effective verdict fed to may_proceed():
      - review is None: "NO-DATA" (nothing was ever recorded; rule 3).
      - review is not None but is_valid() is False: "NO-DATA" (a stale
        review carries no more evidence than no review at all; rule 1's
        whole point is that a stale PASS must never act like a PASS).
      - review is not None and is_valid() is True: review.verdict itself,
        whatever it is (a reviewer may legitimately answer PASS, FAIL, or
        its own NO-DATA).

    Decision.uncertain is True exactly when the effective verdict above is
    "NO-DATA" (rule 4: explicit uncertainty is a field, not prose).

    Raises CrossReviewRefused on an unrecognised `obligation` (caught from
    orchestrator_invariants.may_proceed()'s own ValueError and re-raised,
    so this module still exposes exactly one exception type) or an
    unrecognised `review.verdict` on an otherwise-fresh review (the same
    re-raise, covering a Review built by some path other than record(),
    since record() itself already refuses this at construction time).
    """
    task_id = task.get("id") if isinstance(task, dict) else None
    fresh = is_valid(review, current_revision=current_revision)
    if review is not None and fresh:
        effective_verdict = review.verdict
        reviewer_family = review.reviewer_family
    else:
        effective_verdict = "NO-DATA"
        reviewer_family = review.reviewer_family if review is not None else None
    try:
        proceeds = orchestrator_invariants.may_proceed(
            effective_verdict, obligation, _GATE_STAGE)
    except ValueError as exc:
        raise CrossReviewRefused(str(exc))
    uncertain = effective_verdict == "NO-DATA"
    if review is None:
        basis = "no review was ever recorded for this task"
    elif not fresh:
        basis = (
            "review at revision %r is stale against current revision %r"
            % (review.revision, current_revision))
    else:
        basis = "review at the current revision %r" % (current_revision,)
    reason = (
        "verdict=%s obligation=%s stage=%s proceeds=%s (%s)"
        % (effective_verdict, obligation, _GATE_STAGE, proceeds, basis))
    return Decision(
        verdict=effective_verdict, may_proceed=proceeds, uncertain=uncertain,
        fresh=fresh, task_id=task_id, reviewer_family=reviewer_family,
        reason=reason)
