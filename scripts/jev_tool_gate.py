#!/usr/bin/env python3
"""jev_tool_gate: J1.a of unit J1, the tool call gate.

WHY THIS EXISTS. A proposed tool call must be refused by arithmetic and
policy checks BEFORE any model is consulted (R1), and a consulted model's
answer may only ever make the outcome STRICTER, never looser (H1). This
module is the deterministic half of that seam: precheck() decides,
gate_verdict() maps one probability to exactly one of three outcomes, and
gate_tool_call() wires the two together behind the existing outside
content gate and an injected ask path.

THE THREE OUTCOMES, AND THE DIRECTION THEY MAY MOVE.

  approve   the arithmetic and policy checks passed and nothing stricter
            came back from the recorded second opinion.
  block     the arithmetic or policy checks refused. Nothing is consulted
            after a refusal (R1), so no second opinion can move a block.
  review    everything else, including every NO-DATA: an abstain, a
            timeout, an answer that raises, a missing answer, a crossing
            threshold pair, a probability that is not a finite number
            inside 0..1. Unknown is stricter (H2).

THE SEAM SHIPS OFF. mode "off" (the default) never consults and never
lets a recorded answer change the outcome. mode "shadow" consults and
records the answer while the outcome stays exactly what "off" would have
returned. mode "review-only" may DOWNGRADE a passing precheck to
"review" or "block" and may never upgrade anything (H1).

NOTHING PRIVATE LEAVES THE MACHINE. The state this module would send is
built from the policy text, the proposed call and the order records, and
it is passed through scripts/coe_outside_gate.py first; a refusal there
means the model is never reached (H3). That module is imported at load
time: if it is absent this module does not load at all, rather than send
ungated content.

Python 3.9 floor, standard library only, no network, no file of its own.
"""
import json
import math
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coe_outside_gate as _outside_gate  # noqa: E402  (sibling module, reused never copied)

#: The two published thresholds: at or above APPROVE_AT the call is
#: approved, at or below BLOCK_AT it is blocked, and anything strictly
#: between them is a review.
APPROVE_AT = 0.9
BLOCK_AT = 0.1

OUTCOME_APPROVE = "approve"
OUTCOME_BLOCK = "block"
OUTCOME_REVIEW = "review"

MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_REVIEW_ONLY = "review-only"

_MODES = (MODE_OFF, MODE_SHADOW, MODE_REVIEW_ONLY)

#: Strictness ordering: a second opinion may only ever move an outcome up
#: this scale, never down (H1).
_SEVERITY = {OUTCOME_APPROVE: 0, OUTCOME_REVIEW: 1, OUTCOME_BLOCK: 2}

#: How long gate_tool_call() waits for the injected ask path before it
#: treats the call as NO-DATA. The ask path keeps its own, shorter
#: deadline inside scripts/jev_seam.py; this is the outer bound that
#: keeps a hung or dead ask from stalling the caller. A module constant
#: only so a test can shorten it.
DEFAULT_ASK_DEADLINE_S = 3.0
ASK_DEADLINE_S = DEFAULT_ASK_DEADLINE_S


def _finite_number(value):
    """True only for a real, finite int or float.

    False for a bool: Python's bool IS an int, so True would otherwise
    pass as the amount 1. False for anything that is not int or float at
    all. False for NaN and the infinities: NaN compares False against
    every bound, so a NaN amount would otherwise slip past both the
    negative check and the over-balance check.
    """
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return False
    return True


def _valid_probability(value):
    """True only for a finite number inside the closed interval 0..1."""
    if not _finite_number(value):
        return False
    return 0.0 <= value <= 1.0


def precheck(call, records):
    """(ok, reason). The arithmetic and policy check that runs BEFORE any
    model is consulted (R1).

    False when the record the call names is absent, the amount is not a
    finite number, is negative, or exceeds that record's balance. Pure:
    no model, no I/O, and it never raises for a bad caller.
    """
    if not isinstance(call, dict):
        return (False, "the proposed call is not a mapping")
    if not isinstance(records, dict):
        return (False, "the order records are not a mapping")
    if not call:
        return (False, "the proposed call is empty")
    record_id = call.get("record")
    if not isinstance(record_id, str) or not record_id:
        return (False, "the proposed call names no record")
    if record_id not in records:
        return (False, "the record %r the call names is absent" % (record_id,))
    record = records[record_id]
    if not isinstance(record, dict):
        return (False, "the record %r is not a mapping" % (record_id,))
    amount = call.get("amount")
    if not _finite_number(amount):
        return (False, "the amount is not a finite number")
    if amount < 0:
        return (False, "the amount is negative")
    balance = record.get("balance")
    if not _finite_number(balance):
        return (False, "the record %r carries no usable balance" % (record_id,))
    if amount > balance:
        return (False, "the amount %s exceeds the record balance %s" % (amount, balance))
    return (True, "the amount %s is within the record balance %s" % (amount, balance))


def gate_verdict(p_allowed, approve_at=APPROVE_AT, block_at=BLOCK_AT):
    """One probability to exactly one outcome (R2). Pure, never raises.

    None, NaN, an infinity, anything that is not a number, a number
    outside 0..1, a threshold pair that crosses (block_at >= approve_at),
    and a threshold outside 0..1 all return "review": an unusable or
    corrupt input is never read as either extreme (H2).
    """
    if not _valid_probability(p_allowed):
        return OUTCOME_REVIEW
    if not _valid_probability(approve_at) or not _valid_probability(block_at):
        return OUTCOME_REVIEW
    if block_at >= approve_at:
        return OUTCOME_REVIEW
    if p_allowed >= approve_at:
        return OUTCOME_APPROVE
    if p_allowed <= block_at:
        return OUTCOME_BLOCK
    return OUTCOME_REVIEW


def _stricter(first, second):
    """Whichever of two outcomes is stricter; never the looser one (H1)."""
    if _SEVERITY[first] >= _SEVERITY[second]:
        return first
    return second


def _state_text(policy_text, call, records):
    """The exact text that would leave the machine, or None when it cannot
    be built. Built from the policy text, the proposed call and the order
    records and nothing else."""
    if not isinstance(policy_text, str) or not policy_text:
        return None
    try:
        return json.dumps(
            {"policy": policy_text, "call": call, "records": records},
            sort_keys=True,
            default=repr,
        )
    except (TypeError, ValueError):
        return None


def _ask_with_deadline(ask, state, deadline_s):
    """("answered" | "error" | "timeout", value). Never raises and never
    blocks longer than deadline_s."""
    box = {}

    def _worker():
        try:
            box["value"] = ask(state)
        except Exception:  # noqa: BLE001 -- a raising ask is NO-DATA, never a crash
            box["error"] = True

    worker = threading.Thread(target=_worker, daemon=True)
    try:
        worker.start()
    except Exception:  # noqa: BLE001 -- a thread that will not start is NO-DATA
        return ("error", None)
    worker.join(deadline_s)
    if worker.is_alive():
        return ("timeout", None)
    if "error" in box:
        return ("error", None)
    return ("answered", box.get("value"))


def _deadline():
    """The bounded wait _ask_with_deadline() uses. A corrupt configured
    value falls back to the default rather than to "wait forever"."""
    value = ASK_DEADLINE_S
    if not _finite_number(value) or value < 0:
        return DEFAULT_ASK_DEADLINE_S
    return float(value)


def _second_opinion_note(status, probability):
    """One human sentence naming why the second opinion was or was not
    used. Built only from the status and the recorded number, never from
    the state that was sent."""
    if status == "timeout":
        return "the second opinion did not return within the deadline: NO-DATA"
    if status == "error":
        return "the second opinion raised and was not used: NO-DATA"
    if probability is None:
        return "the second opinion was not a usable probability: NO-DATA"
    return "the second opinion scored %r" % (probability,)


def _no_consult(mode, reason):
    """The result when the model was never reached.

    mode "shadow" keeps the deterministic answer exactly as "off" would
    have returned it; every other consulting mode is stricter and says so
    (H2).
    """
    if mode == MODE_SHADOW:
        return {"outcome": OUTCOME_APPROVE, "reason": reason,
                "probability": None, "consulted": False}
    return {"outcome": OUTCOME_REVIEW, "reason": reason,
            "probability": None, "consulted": False}


def gate_tool_call(call, records, policy_text, ask=None, mode="off"):
    """See the module docstring. Returns a dict with exactly the keys
    "outcome", "reason", "probability" and "consulted", and never raises
    for a bad caller: every refusal is one of those four keys."""
    ok, reason = precheck(call, records)
    if not ok:
        return {"outcome": OUTCOME_BLOCK, "reason": reason,
                "probability": None, "consulted": False}

    if not isinstance(mode, str) or mode not in _MODES:
        return {"outcome": OUTCOME_REVIEW,
                "reason": "unrecognised mode %r: NO-DATA, refusing to guess" % (mode,),
                "probability": None, "consulted": False}

    if mode == MODE_OFF:
        return {"outcome": OUTCOME_APPROVE, "reason": reason,
                "probability": None, "consulted": False}

    state = _state_text(policy_text, call, records)
    if state is None:
        return _no_consult(mode, "the policy text or the state is unusable: NO-DATA")

    passed = _outside_gate.check(state)
    if not passed.allowed:
        return _no_consult(mode, "the outside content gate refused: %s" % passed.reason)

    if not callable(ask):
        return _no_consult(mode, "no ask path is available: NO-DATA")

    status, answer = _ask_with_deadline(ask, state, _deadline())
    probability = answer if _valid_probability(answer) else None

    if mode == MODE_SHADOW:
        return {"outcome": OUTCOME_APPROVE,
                "reason": "shadow, outcome unchanged: %s"
                          % _second_opinion_note(status, probability),
                "probability": probability, "consulted": True}

    verdict = gate_verdict(probability)
    outcome = _stricter(OUTCOME_APPROVE, verdict)
    return {"outcome": outcome,
            "reason": "%s: %s" % (_second_opinion_note(status, probability), outcome),
            "probability": probability, "consulted": True}
