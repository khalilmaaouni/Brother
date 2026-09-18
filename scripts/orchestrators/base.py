#!/usr/bin/env python3
"""ORCH-06 of the 1.0.20 orchestration control plane: the adapter contract
an outside model is invoked through to play an orchestrating role.

WHY THIS EXISTS. A night run can hand the orchestrating decision itself
(what to DISPATCH, PARK, or QUEUE-HUMAN) to an outside model instead of a
human or a fixed rule. That model answers in prose by default. Reading its
prose to guess a dispatch is exactly the failure this build's steering
document forbids: "a model that seems to want to dispatch is not a
dispatch." This module is the one seam every such invocation goes through,
so "seems to want to" can never reach the loop: the model is asked for one
JSON action record, that record is validated against
docs/schema/orchestrator-event-v1.json through orchestrator_protocol.
validate(), and anything else it returns (prose, broken JSON, an action
outside scripts/orchestrator_invariants.ACTIONS) is a classified FAILURE,
never a best-effort interpretation.

THE CENTRAL RULE: prose in, structured record out, or a named failure.
There is no fourth outcome. invoke() below either returns an ActionResult
whose .action is a schema-valid record, or one whose .ok is False and
whose .failure_class is a real member of orchestrator_invariants.
FAILURE_CLASSES. It never guesses, never partially trusts a document, and
never retries the same call twice unchanged: see RETRY-ONCE below.

EMPTY IS A FAILURE, always, regardless of exit code. This estate has the
recorded incident (SR-1, docs/decisions/swarm-model-resilience-2026-09-10.
json): a bridge exiting 0 with an empty body was read as success. This
module classifies an empty answer as "empty" before it ever looks at
whatever exit status the caller's own transport reported.

RETRY-ONCE, NEVER IDENTICALLY. invoke() allows exactly one retry
(MAX_ATTEMPTS below), and only when the first attempt's failure is not an
"authority" class failure (a policy refusal is never retried, it goes
straight to a human). The retry always differs from the first attempt: a
larger time budget and a prompt note naming what happened last time. What
"differs" means is checked mechanically by check_retry_allowed(), a pure
function kept separate from invoke() so a test can drive the third-attempt
and identical-attempt refusals directly, without a real subprocess.

THE MODEL THAT ANSWERS IS CHECKED, NOT ASSUMED. When a concrete adapter's
transport reports which model actually produced an answer, invoke()
compares it against the model this adapter was built to call
(self.requested_model). A mismatch is a failure (see _classify below), not
a footnote the caller has to notice for itself: this estate has the
recorded incident of a lane crediting findings to a model that did not
produce them, because the identity was printed somewhere nobody read.

NO FULL-ACCESS BYPASS. assert_safe_invocation() is the one place an
argv is checked for a sandbox flag that would drop sandboxing, or for a
merge/push token that would let this adapter act on the canonical
repository directly. Every concrete adapter under this package calls it
on every invocation it builds, before that invocation ever runs.

HEALTH IS MEASURED, NEVER INFERRED FROM THE LAST ANSWER LOOKING GOOD.
health() defaults to UNAVAILABLE and stays there unless a concrete
adapter can actually measure something (a process, a heartbeat file, a
binary that exists on disk). A subclass that returns HEALTHY without
measuring anything is the exact failure this default exists to refuse by
omission: nothing here ever upgrades UNAVAILABLE to HEALTHY on its own.

Python 3.9 floor, standard library only, no network. Imports exactly
scripts/orchestrator_invariants.py (FAILURE_CLASSES, ACTIONS,
HEALTH_STATES, classify_failure) and scripts/orchestrator_protocol.py
(validate against orchestrator-event-v1), both frozen definition sites
this module reads from rather than restates.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(os.path.dirname(__file__))))
import orchestrator_invariants  # noqa: E402
import orchestrator_protocol  # noqa: E402

#: The schema every attempt's parsed answer is checked against. Never a
#: second, hand-rolled shape check: orchestrator_protocol.validate() reads
#: this file's own oneOf branches, so a field added there is enforced here
#: with no code change.
ACTION_SCHEMA = "orchestrator-event-v1"

#: Exactly one retry. A caller wanting a third try is asking the wrong
#: function; see check_retry_allowed().
MAX_ATTEMPTS = 2

#: The retry's time budget is this multiple of the first attempt's, never
#: the same number: part of what "never identically" means mechanically.
RETRY_BUDGET_MULTIPLIER = 2.0

#: Fragments that must never appear in an invocation this package builds.
#: Checked as substrings of the whole joined argv, not exact tokens, so a
#: flag spelled "--sandbox=danger-full-access" is caught the same as
#: "--sandbox danger-full-access". This is deliberately a short, literal
#: list, not a permissive default: an argv containing none of these still
#: is not thereby proven safe, it is only not proven unsafe by THIS check.
FORBIDDEN_INVOCATION_FRAGMENTS = (
    "danger-full-access",
    "merge",
    "push",
    "--force",
)


class AdapterRefused(Exception):
    """Raised when this package itself refuses to proceed: a caller error
    (a non-positive deadline, a third attempt, a retry identical to a
    prior one), or an invocation an adapter built that would bypass
    sandboxing or touch the canonical repository. Never raised for an
    ordinary model failure (prose, empty answer, timeout): those are an
    ActionResult with ok=False, because they are the expected, classified
    outcome of asking an outside model something, not a defect in the
    caller.
    """


def assert_safe_invocation(argv):
    """Raise AdapterRefused if `argv` (a list of command-line tokens)
    contains any of FORBIDDEN_INVOCATION_FRAGMENTS. Returns None (not a
    boolean) on a clean argv: this is a gate, not a predicate, so a
    caller cannot accidentally ignore a False return and proceed anyway.

    A pure function over a list of strings, never itself launching a
    process, so both a hand-written test argv and a concrete adapter's
    real argv-builder can be checked identically, with nothing spawned.
    """
    joined = " ".join(str(token) for token in argv)
    for fragment in FORBIDDEN_INVOCATION_FRAGMENTS:
        if fragment in joined:
            raise AdapterRefused(
                "invocation would bypass sandboxing or touch canonical "
                "history: %r contains forbidden fragment %r" % (argv, fragment))


def check_retry_allowed(attempt_number, signature, prior_signatures):
    """Raise AdapterRefused unless this attempt may proceed.

    attempt_number: 1 for the first attempt, 2 for the retry. Anything
    past MAX_ATTEMPTS is refused outright: this is the "never a third
    attempt" rule, and it is checked here rather than only implied by a
    loop bound, so a test can drive it directly with no adapter at all.

    signature: whatever distinguishes this attempt from a prior one (this
    module uses (budget_s, prior_note), a concrete adapter may pass a
    richer tuple). prior_signatures: the signatures already used in this
    invocation. A signature equal to one already used is refused: "retry
    once" only means something if the retry is required to differ.

    Returns None on a clean attempt; never returns False, for the same
    reason assert_safe_invocation never does.
    """
    if attempt_number > MAX_ATTEMPTS:
        raise AdapterRefused(
            "attempt %d exceeds the %d-attempt limit: retry once, never "
            "again" % (attempt_number, MAX_ATTEMPTS))
    if signature in prior_signatures:
        raise AdapterRefused(
            "a retry must differ from every prior attempt in this "
            "invocation, got identical signature %r" % (signature,))


class ActionResult:
    """The outcome of one invoke() call, win or lose, never partially
    filled.

    ok: True only when `action` holds a document that validated clean
        against ACTION_SCHEMA. False for every classified failure.
    action: the validated action-envelope dict when ok is True, else
        None. Never a partially-parsed or best-effort document: the whole
        point of this class is that "close enough" does not exist here.
    failure_class: a member of orchestrator_invariants.FAILURE_CLASSES
        when ok is False, else None. Checked at construction time via
        orchestrator_invariants.classify_failure(), which raises
        ValueError on a name outside that frozenset: this class can never
        be built holding an unrecognised failure label, on purpose.
    raw: the model's own raw text this attempt produced, kept even on
        failure (possibly the empty string), for audit.
    model_reported: the model identity the invocation reported answering,
        or None when the transport reported none.
    """

    def __init__(self, ok, action, failure_class, raw, model_reported):
        ok = bool(ok)
        if ok:
            if failure_class is not None:
                raise AdapterRefused(
                    "an ok ActionResult carries no failure_class, got %r"
                    % (failure_class,))
            if action is None:
                raise AdapterRefused(
                    "an ok ActionResult must carry the validated action")
        else:
            if action is not None:
                raise AdapterRefused(
                    "a failed ActionResult carries no action, got %r"
                    % (action,))
            if failure_class is None:
                raise AdapterRefused(
                    "a failed ActionResult must name a failure_class")
            # Raises ValueError on a name outside FAILURE_CLASSES: an
            # unrecognised failure stops the caller cold, it is never
            # read as the safe default "unknown" (requirement 4).
            orchestrator_invariants.classify_failure(failure_class)
        self.ok = ok
        self.action = action
        self.failure_class = failure_class
        self.raw = raw
        self.model_reported = model_reported

    def __repr__(self):
        if self.ok:
            return "ActionResult(ok=True, action=%r)" % (self.action,)
        return "ActionResult(ok=False, failure_class=%r)" % (self.failure_class,)


class OrchestratorAdapter:
    """The contract both orchestrator families satisfy: an advisory
    family that only recommends, and an execution family (see
    scripts/orchestrators/execution.py) whose action envelopes the loop
    may actually apply. Owns every behaviour that must not diverge
    between families: the deadline, the empty-is-failure rule, the
    retry-once-never-identically rule, failure classification, the
    answering-model check, and the UNAVAILABLE-by-default health default.
    A concrete family implements only `_run_attempt` (one bounded call to
    its own transport) and, where it can actually measure something,
    `health`.
    """

    #: Set by a concrete subclass to name its family, for logging and for
    #: docs/schema/orchestrator-event-v1.json's own "orchestrator" field.
    #: None here on purpose: an adapter with no declared name is a
    #: programming error in the subclass, never silently tolerated.
    name = None

    def __init__(self, requested_model=None):
        #: The model identity this adapter was built to call, or None
        #: when no check is wanted. None is a deliberate opt-out, not a
        #: default meaning "assume it matched": see _classify below.
        self.requested_model = requested_model

    def _run_attempt(self, capsule, *, budget_s, attempt_number, prior_note):
        """Subclass hook: run exactly ONE bounded call to this adapter's
        outside model and report what happened. Must return a dict with
        at least these keys:

          raw: str, the model's raw text (empty string if none at all).
          timed_out: bool, True when the call was killed for exceeding
              budget_s. The deadline itself must be enforced by the
              subclass in Python (see execution.py's docstring for why:
              this machine has no `timeout` command), never assumed.
          model_reported: str or None, the model identity the transport
              says actually answered, when it says anything at all.

        May also include:
          failure_class: a member of orchestrator_invariants.
              FAILURE_CLASSES, set only when the subclass has concrete
              evidence of a specific cause (a launch OSError is
              "tool_unavailable", a detected provider rate-limit message
              is "rate_limit"). Never set as a default or a guess.

        Must never raise for an ordinary model failure: an empty answer,
        a timeout, prose, or malformed JSON are all reported through the
        dict above, not as exceptions. Raising here is reserved for a
        genuine programming error in the subclass itself.
        """
        raise NotImplementedError(
            "%s must implement _run_attempt" % (type(self).__name__,))

    def health(self):
        """A member of orchestrator_invariants.HEALTH_STATES, measured
        from process or heartbeat state, never inferred from the content
        or success of the last answer this adapter returned. The base
        default is UNAVAILABLE and stays UNAVAILABLE for any subclass
        that does not override this with an actual measurement: a
        subclass that returns HEALTHY here without checking anything is
        exactly the failure this method exists to refuse by never doing
        that itself."""
        return "UNAVAILABLE"

    def _classify(self, raw_attempt):
        """One ActionResult from a single _run_attempt() return value.
        The only place prose, malformed JSON, an out-of-vocabulary
        action, an empty answer, a timeout, or a model mismatch turn into
        a named failure_class rather than a guess."""
        raw = raw_attempt.get("raw") or ""
        model_reported = raw_attempt.get("model_reported")
        if raw_attempt.get("timed_out"):
            return ActionResult(False, None, "timeout", raw, model_reported)
        override = raw_attempt.get("failure_class")
        if override:
            # The subclass has concrete evidence of a specific cause
            # (see _run_attempt's docstring); never a default classifier,
            # so trust it ahead of the generic checks below. Still routed
            # through ActionResult's own classify_failure() check, so a
            # subclass naming a class outside FAILURE_CLASSES still
            # raises rather than being accepted.
            return ActionResult(False, None, override, raw, model_reported)
        if not raw.strip():
            # SR-1's recorded incident: an exit 0 with an empty body read
            # as success. Empty is always a failure, checked before this
            # method looks at anything else about the answer.
            return ActionResult(False, None, "empty", raw, model_reported)
        if (self.requested_model and model_reported
                and model_reported != self.requested_model):
            # The answering model is checked, not assumed. missing_evidence
            # is the closest fit in the frozen taxonomy: an answer whose
            # own claimed authorship cannot be trusted is an answer this
            # estate has no valid evidence of who actually produced,
            # which is a condition about the run's environment, not about
            # whether the JSON inside it happens to be well formed.
            return ActionResult(
                False, None, "missing_evidence", raw, model_reported)
        try:
            parsed = json.loads(raw)
        except ValueError:
            # Prose, or JSON that does not even parse. worker_crash is
            # the closest fit: the invocation produced nothing this
            # estate's control plane can use as a decision, the same
            # practical outcome as the worker process itself crashing.
            return ActionResult(False, None, "worker_crash", raw, model_reported)
        if not isinstance(parsed, dict):
            return ActionResult(False, None, "worker_crash", raw, model_reported)
        problems = orchestrator_protocol.validate(parsed, ACTION_SCHEMA)
        if problems:
            # Covers both a structurally-off document AND an action
            # value outside orchestrator_invariants.ACTIONS: the schema's
            # own enum branches already enforce that vocabulary, so a
            # document naming an unrecognised action fails validate()
            # here rather than needing a second, separate check.
            return ActionResult(False, None, "worker_crash", raw, model_reported)
        if parsed.get("action") not in orchestrator_invariants.ACTIONS:
            # Belt and suspenders: never trust schema-shape alone for
            # this estate's own frozen action vocabulary.
            return ActionResult(False, None, "worker_crash", raw, model_reported)
        return ActionResult(True, parsed, None, raw, model_reported)

    def invoke(self, capsule, *, deadline_s, now=None):
        """One outside-model call that returns either a schema-valid
        action record or a classified failure, retrying at most once,
        never identically, and never retrying a policy refusal.

        capsule: the prepared context (scripts/context_capsule.py's
            build_capsule() output, or an equivalent mapping). Never
            walked for more: this method and _run_attempt only ever read
            what the capsule already carries, they do not go looking for
            more history themselves.
        deadline_s: the first attempt's time budget, seconds, > 0.
        now: the epoch time this call was decided, or None to use the
            real clock. A value, not a callable, matching this estate's
            existing now=None convention (orchestrator_authority.py,
            fable_authority.py): a test drives a fixed value, real
            callers pass None and get time.time().
        """
        if deadline_s is None or deadline_s <= 0:
            raise AdapterRefused(
                "deadline_s must be a positive number, got %r" % (deadline_s,))
        #: Read once per call, from an injectable seam rather than a bare
        #: time.time() sprinkled inline, so a test can pin it and an
        #: audit trail can later record exactly when this decision was
        #: made without guessing from narrative flow (this estate's own
        #: NO FABRICATION rule for timestamps).
        self.last_invoked_at = self._now(now)
        prior_signatures = []
        attempt_number = 1
        budget_s = float(deadline_s)
        prior_note = None
        result = None
        while True:
            signature = (budget_s, prior_note)
            check_retry_allowed(attempt_number, signature, prior_signatures)
            prior_signatures.append(signature)
            raw_attempt = self._run_attempt(
                capsule, budget_s=budget_s, attempt_number=attempt_number,
                prior_note=prior_note)
            result = self._classify(raw_attempt)
            if result.ok:
                return result
            # An unrecognised failure_class already raised inside
            # _classify/ActionResult; only real classes reach here.
            bucket = orchestrator_invariants.classify_failure(result.failure_class)
            if bucket == "authority":
                # A policy refusal is never retried: asking the same
                # question again hopes for a different rule, which this
                # estate treats as a human's decision, not a retry.
                return result
            if attempt_number >= MAX_ATTEMPTS:
                return result
            attempt_number += 1
            budget_s = budget_s * RETRY_BUDGET_MULTIPLIER
            prior_note = "attempt %d failed: %s" % (
                attempt_number - 1, result.failure_class)

    @staticmethod
    def _now(now):
        if now is not None:
            return float(now)
        import time
        return time.time()
