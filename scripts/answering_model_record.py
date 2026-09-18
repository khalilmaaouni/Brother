#!/usr/bin/env python3
"""ORCH-29 of the 1.0.20 control plane: which model actually answered, and
whether that substitution is one the caller allowed.

WHY THIS EXISTS. A routing API (an OpenRouter style bridge, a fallback
chain) can be asked for one model and answer with a different one, silently,
because its own chain moved on after a timeout, an overload, or an empty
first answer. Refusing every substitution outright turns a deliberate,
useful fallback into a paid failure; accepting every substitution silently
turns "I asked model A" into "something answered" with no way to tell them
apart later. This module answers the narrower question a caller actually
has: is THIS substitution one I said I would accept.

THE FIELD IS STRUCTURAL, NEVER PROSE. The response object a chat completion
call returns already carries the model that answered as a plain JSON field
(payload["model"] on the OpenAI and OpenRouter response shape; see
~/.claude/bin/or_ask.py, whose own stderr line "[usage] ... model=%s" is
rendered FROM this same field via `payload.get("model")`, never the other
way around). Parsing that rendered stderr line back out with a regular
expression would be building a second, fragile reader for a value already
sitting in the parsed JSON object. This module only ever reads the
structured field: extract_answering_model() takes the parsed response dict,
never a log line.

FAIL CLOSED ON THE UNKNOWN, TWICE. First: a response carrying no usable
model field is NO-DATA, never read as "the requested model answered", and
never read as a FAIL either, because neither claim is measured. Second: a
requested model with no entry in the caller's policy has no permitted
substitutes at all. A caller who never stated a fallback policy for a model
gets no free pass on whatever happened to answer; only an exact match
counts until the caller says otherwise.

Python 3.9 floor, standard library only, no network, no file I/O, no logging.
"""

from collections import namedtuple

__all__ = [
    "PASS",
    "FAIL",
    "NO_DATA",
    "Verdict",
    "extract_answering_model",
    "evaluate",
]

#: The three and only three verdict strings this module ever produces.
PASS = "PASS"
FAIL = "FAIL"
NO_DATA = "NO-DATA"

Verdict = namedtuple(
    "Verdict", ["requested_model", "answering_model", "verdict", "reason"]
)


def extract_answering_model(payload):
    """The model id that actually answered, read from the structured
    response object, or None when nothing usable is there.

    `payload` is the parsed JSON response object (a dict), never a rendered
    log line. Returns `payload["model"]` stripped of surrounding whitespace
    when it is present and is a non-empty string after stripping. Returns
    None in every other case: the key is missing, the value is None, the
    value is an empty or whitespace-only string, or the value is not a
    string at all (a number, a list, a dict). None means "no usable
    answering model was recorded"; a caller must never treat it as a
    synonym for the requested model having answered.

    Raises TypeError if `payload` itself is not a dict: a boundary call
    handed something other than a parsed response object is a caller
    defect, not a NO-DATA case.
    """
    if not isinstance(payload, dict):
        raise TypeError(
            "payload must be a dict (the parsed JSON response object), got %r"
            % (type(payload).__name__,)
        )

    value = payload.get("model")
    if not isinstance(value, str):
        return None

    value = value.strip()
    if not value:
        return None

    return value


def _is_permitted_substitute(answering, requested, policy):
    """True only when `policy` names `answering` as an acceptable
    substitute for `requested`.

    A missing entry, a None entry, a non-iterable entry, or an entry whose
    items are not strings all mean "not permitted": an unrecognised or
    malformed policy shape is never read as permissive. A bare string entry
    is treated as one substitute id, never iterated as a sequence of
    characters (a common trap: `"a" in "abc"` is True for the wrong reason).
    Comparison is exact and case sensitive, matching extract_answering_model,
    which never folds case either.
    """
    if policy is None:
        return False

    allowed = policy.get(requested)
    if allowed is None:
        return False

    if isinstance(allowed, str):
        allowed = (allowed,)

    try:
        candidates = list(allowed)
    except TypeError:
        return False

    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip() == answering:
            return True

    return False


def evaluate(requested_model, payload, policy=None):
    """Judge whether an answer may be treated as coming from the model the
    caller asked for.

    `requested_model` is the model id string the caller asked for; leading
    and trailing whitespace is ignored when it is compared. `payload` is
    the parsed JSON response object. `policy` is either None or a dict
    mapping a requested model id to an iterable of model ids that are an
    acceptable substitute for it; a requested model with no entry has no
    acceptable substitutes (fail closed, see module docstring).

    Returns a Verdict namedtuple: requested_model, answering_model, verdict,
    reason. verdict is always exactly one of PASS, FAIL, NO_DATA:

        NO-DATA   extract_answering_model(payload) found nothing usable.
                  answering_model is None. Never a pass, never silently a
                  fail either: the caller must decide what NO-DATA means
                  for its own obligation, the way evidence_obligation.py
                  already does for every other verdict in this estate.
        PASS      the answering model equals the requested model, or the
                  requested model's policy entry names it as permitted.
        FAIL      a different, known model answered and the requested
                  model's policy (or its absence) does not permit it.

    Raises ValueError if `requested_model` is not a non-empty string (after
    stripping), and TypeError if `payload` is not a dict or `policy` is
    neither None nor a dict: all three are boundary inputs from a caller,
    never guessed at.
    """
    if not isinstance(requested_model, str) or not requested_model.strip():
        raise ValueError(
            "requested_model must be a non-empty string naming the model "
            "the caller asked for, got %r" % (requested_model,)
        )

    if not isinstance(payload, dict):
        raise TypeError(
            "payload must be a dict (the parsed JSON response object), got %r"
            % (type(payload).__name__,)
        )

    if policy is not None and not isinstance(policy, dict):
        raise TypeError(
            "policy must be a dict or None, got %r" % (type(policy).__name__,)
        )

    requested = requested_model.strip()
    answering = extract_answering_model(payload)

    if answering is None:
        return Verdict(
            requested_model=requested,
            answering_model=None,
            verdict=NO_DATA,
            reason=(
                "the response carried no usable structured 'model' field, "
                "so the answering model is unknown; this is never read as "
                "the requested model having answered"
            ),
        )

    if answering == requested:
        return Verdict(
            requested_model=requested,
            answering_model=answering,
            verdict=PASS,
            reason="the requested model answered",
        )

    if _is_permitted_substitute(answering, requested, policy):
        return Verdict(
            requested_model=requested,
            answering_model=answering,
            verdict=PASS,
            reason="%r substituted for %r, permitted by policy"
            % (answering, requested),
        )

    return Verdict(
        requested_model=requested,
        answering_model=answering,
        verdict=FAIL,
        reason="%r substituted for %r, not permitted by the caller's policy"
        % (answering, requested),
    )
