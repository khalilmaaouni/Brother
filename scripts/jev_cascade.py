#!/usr/bin/env python3
"""JEV-03 of the 1.0.20 orchestration control plane: the confidence-gated
cascade that decides ACT, ESCALATE or NO-DATA for one Jev decision.

WHY THIS EXISTS. Measured in this session's own eval: at confidence 0.9 and
above Jev was right 92 to 100 percent of the time, but confident on only 35
to 54 percent of items. A caller that acts on every answer trusts the 46 to
65 percent of items where Jev itself was not sure; a caller that never acts
automatically throws away the cheap, fast win Jev exists to provide. This
module is the gate between those two failures: it ACTs only where
scripts/jev_calibration.py (JEV-02) has MEASURED, from real recorded
outcomes, that confidence at or above some threshold meets the target
precision this risk class requires, with enough samples for that measurement
to mean something. Everything else goes to a stronger reviewer instead of
either extreme.

REUSES, NEVER REBUILDS. jev_calibration.threshold() is the one place a
calibrated confidence threshold is computed; this module never re-derives
precision or a Wilson bound itself, it only reads threshold()'s answer and
decides what to do with it. See CalibrationHandle below for how a caller
points this module at a ledger.

THE THREE OUTCOMES.
  ACT       the decision's own confidence clears the calibrated threshold
            for its family, qtype (optionally scoped to its framing) and
            risk class. The caller may act automatically.
  ESCALATE  sent to the next reviewing lane, named explicitly
            ("muse" first, "opus" second: see CASCADE_LANES) so the caller
            knows exactly where to route it next. This is the outcome for
            everything that is not yet safe to act on automatically,
            including cases where no calibration data exists at all: see
            THE ESCALATE-ON-UNKNOWN RULE below.
  NO-DATA   routing itself could not produce a next step: today this is
            exactly one case, an already-exhausted escalation ladder (a
            decision already reviewed by both muse and opus that still
            does not clear the bar has nowhere further to go inside this
            cascade and needs a human).

THE ESCALATE-ON-UNKNOWN RULE, cited from this unit's own deciding property
(docs/plan/ORCH-1020-WBS.json, JEV-03): "never ACT without a calibrated
threshold for the decision's family and risk class; unknown family or risk
class escalates". This is a deliberate, narrow exception to the general
estate rule that an unrecognised enum value stops the machine (an
unrecognised risk_class or a family jev_calibration has never recorded is
NOT the same defect class as an unrecognised qtype or a malformed decision
object: a new family name or an unfamiliar risk_class label is an ordinary,
expected runtime fact as this product grows, and the safe response to "no
evidence yet" is more scrutiny, not a crash and not silent automatic
action). What still raises, because it is a structural contract violation
of the decision object itself rather than an open-ended business fact: a
decision that is not a dict, that carries no non-empty string id, whose
qtype is not one of jev_calibration.QTYPES (a closed, fixed vocabulary this
whole system defines), whose confidence is not a finite number in [0, 1],
or whose escalation_rung is not one of the ladder's own recognised
positions (0, 1 or 2). See route() for the exact order these are checked
in.

CRITICAL NEVER ACTS, BY POLICY, NOT BY MEASUREMENT. RISK_TARGET_PRECISION
maps "critical" to None on purpose: no Wilson lower bound, however high,
is read as license to act automatically at critical stakes, because a
calibration report describes how the model performed on similar items in
the past, not that THIS specific wrong action is safe. route() checks
risk_class == "critical" before it ever calls jev_calibration, since the
answer does not depend on the calibration data at all.

THREE STATED LIMITATIONS, named here rather than left implicit (found by
an adversarial review of the first draft of this design, docs/plan
research pipeline, before any of this module's real code was written).
None of these are fixed by this unit, because fixing them means changing
jev_calibration.py's ledger shape or adding a state store this WBS unit
does not own; they are contract obligations on the CALLER instead.

  1. framing is OPTIONAL on a decision. When omitted, threshold() pools
     across every framing recorded for the family and qtype (jev_
     calibration.py's own documented behaviour for framing=None). A caller
     that reworded a question and wants the new wording's own, separate
     calibration evidence (never inheriting an old wording's threshold)
     MUST supply framing on every decision it routes. Omitting it is a
     real choice, not an oversight, and it trades framing-precision for
     simplicity when a caller knows its wording is stable.
  2. An escalated re-review should be given its OWN id when it is written
     back to the ledger (for example "<original id>:rung1"), never the
     original id with only escalation_rung bumped: jev_calibration.py
     treats two ledger lines sharing one id as a duplicate decision and
     excludes BOTH from every report. This module does not write to the
     ledger itself (route() only reads it via threshold()), so it cannot
     enforce this; it is stated here for whoever does write the escalated
     review back.
  3. Keeping the same family/qtype/framing across every rung of the
     ladder (see the module docstring's ESCALATED QUESTION IDENTITY
     reasoning, chosen so the calibration ledger stays joinable at all)
     means a family's report() numbers mix rung-0 answers with rung-1 and
     rung-2 reviewer answers. A per-rung precision breakdown would need a
     rung dimension in the ledger schema, which is out of this unit's
     owned files.

ESCALATED QUESTION IDENTITY: an escalated decision keeps the SAME family,
qtype and framing as the one it reviews (only the model/rung, answer and
confidence change). Renaming any of the three on escalation would make a
later report() unable to tell the original and the escalated answer were
ever the same underlying question, which defeats the point of measuring
calibration per family/qtype/framing at all.

Python 3.9 floor, standard library only, no network, no third party
dependency.
"""

import os
import sys
from collections import namedtuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jev_calibration  # noqa: E402

#: Fixed once, here, not accepted as a per-call argument. If a caller could
#: pass a different family/qtype -> precision table on every call, "medium"
#: could mean 0.95 in one call and 0.80 in the next, which would make the
#: risk class label meaningless across calls and impossible to audit later
#: from the ledger alone. A caller that genuinely needs a different policy
#: edits this table (a deliberate, reviewed code change), not a runtime
#: argument.
#:
#: "critical": None is not a placeholder, it is the policy: critical risk
#: NEVER auto-acts, at any confidence, at any calibrated precision. See the
#: module docstring's CRITICAL NEVER ACTS section.
RISK_TARGET_PRECISION = {
    "low": 0.90,
    "medium": 0.95,
    "high": 0.98,
    "critical": None,
}

#: The minimum number of joined (decision, outcome) pairs a confidence band
#: must carry before its Wilson lower bound is trusted for routing. A
#: policy knob, not a derived number: raise it if early operation shows
#: small-sample bands clearing target precision by chance.
MIN_SAMPLES = 20

#: The two-rung escalation ladder, in order. A decision's escalation_rung
#: (0 by default) is its current position: rung 0 has never been reviewed,
#: rung 1 has already been reviewed by CASCADE_LANES[0], rung 2 by
#: CASCADE_LANES[1]. route() never sends a decision already on rung 1 back
#: to CASCADE_LANES[0]: see _escalate_or_exhausted(). route() itself holds
#: no state between calls: a decision's escalation_rung is trusted exactly
#: as given (missing defaults to 0, "never reviewed"). Carrying the right
#: rung forward on a re-submitted decision is the CALLER's obligation, the
#: same way jev_calibration.py trusts every field a caller appends to its
#: ledger: this module has no separate store to check a rung against.
CASCADE_LANES = ("muse", "opus")

#: Bundles the two paths jev_calibration.threshold() needs plus an optional
#: bands override, so route() takes one "calibration" argument instead of
#: two raw paths. `bands` defaults to jev_calibration.DEFAULT_BANDS.
CalibrationHandle = namedtuple("CalibrationHandle", ["decisions_path", "outcomes_path", "bands"])
CalibrationHandle.__new__.__defaults__ = (jev_calibration.DEFAULT_BANDS,)


def _act(decision, risk_class, threshold_value):
    return {
        "outcome": "ACT",
        "id": decision["id"],
        "risk_class": risk_class,
        "family": decision["family"],
        "qtype": decision["qtype"],
        "confidence": decision["confidence"],
        "threshold": threshold_value,
        "reason": None,
    }


def _escalate_or_exhausted(decision_id, rung, reason):
    """rung is the decision's CURRENT position (0, 1 or 2). Returns the
    ESCALATE record naming the next lane and rung, or NO-DATA naming
    escalation_exhausted once rung is already past the last lane. A
    decision on rung 1 (already reviewed by CASCADE_LANES[0]) always moves
    to CASCADE_LANES[1], never back to CASCADE_LANES[0]."""
    if rung >= len(CASCADE_LANES):
        return {"outcome": "NO-DATA", "id": decision_id, "reason": "escalation_exhausted"}
    return {
        "outcome": "ESCALATE",
        "id": decision_id,
        "next_lane": CASCADE_LANES[rung],
        "next_rung": rung + 1,
        "reason": reason,
    }


def route(decision, risk_class, calibration):
    """Decide ACT, ESCALATE or NO-DATA for one decision at one risk class.

    decision is a plain dict carrying at least: id (non-empty str), family
    (non-empty str), qtype (one of jev_calibration.QTYPES), confidence (a
    finite number in [0, 1]). Optional: framing (str, passed through to
    jev_calibration.threshold() to scope the calibration lookup to this
    exact wording; omitted or None pools across framings for the family
    and qtype), escalation_rung (int, 0, 1 or 2; defaults to 0, meaning
    "never yet reviewed").

    calibration is a CalibrationHandle pointing at the ledger's decisions
    and outcomes files (and, optionally, a non-default bands tuple).

    Raises ValueError for a structurally invalid decision object (see the
    module docstring's ESCALATE-ON-UNKNOWN RULE for exactly what does and
    does not raise). Never raises for an unrecognised risk_class or an
    unrecognised family/qtype: those escalate.
    """
    if not isinstance(decision, dict):
        raise ValueError("decision must be a dict, got %r" % (decision,))

    decision_id = decision.get("id")
    if not isinstance(decision_id, str) or not decision_id.strip():
        raise ValueError("decision.id must be a non-empty string, got %r" % (decision_id,))

    family = decision.get("family")
    if not isinstance(family, str) or not family.strip():
        raise ValueError("decision.family must be a non-empty string, got %r" % (family,))

    qtype = decision.get("qtype")
    if qtype not in jev_calibration.QTYPES:
        raise ValueError(
            "decision.qtype must be one of %s, got %r" % (sorted(jev_calibration.QTYPES), qtype)
        )

    confidence = decision.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError("decision.confidence must be a number, got %r" % (confidence,))
    if not (0.0 <= confidence <= 1.0):
        raise ValueError("decision.confidence must be within [0, 1], got %r" % (confidence,))

    rung = decision.get("escalation_rung", 0)
    if isinstance(rung, bool) or not isinstance(rung, int) or rung not in (0, 1, 2):
        raise ValueError("decision.escalation_rung must be 0, 1 or 2, got %r" % (rung,))

    if not isinstance(calibration, CalibrationHandle):
        raise ValueError("calibration must be a CalibrationHandle, got %r" % (calibration,))

    framing = decision.get("framing")
    if framing is not None and (not isinstance(framing, str) or not framing.strip()):
        raise ValueError("decision.framing must be a non-empty string when present, got %r" % (framing,))

    # An unrecognised or malformed risk_class escalates rather than raises:
    # see the module docstring's ESCALATE-ON-UNKNOWN RULE.
    if not isinstance(risk_class, str) or risk_class not in RISK_TARGET_PRECISION:
        return _escalate_or_exhausted(decision_id, rung, "unknown_risk_class")

    # Critical never acts, regardless of confidence or calibration: checked
    # before any calibration call, since the answer never depends on it.
    if risk_class == "critical":
        return _escalate_or_exhausted(decision_id, rung, "critical_never_acts")

    target_precision = RISK_TARGET_PRECISION[risk_class]

    res = jev_calibration.threshold(
        calibration.decisions_path, calibration.outcomes_path,
        family, qtype, target_precision, MIN_SAMPLES,
        framing=framing, bands=calibration.bands,
    )

    if res["threshold"] is None:
        # Covers both "this family/qtype has never been calibrated" and
        # "it has been calibrated, but no band reached this risk class's
        # target precision with enough samples". Both are "no evidence
        # this is safe to act on automatically yet", so both escalate.
        return _escalate_or_exhausted(decision_id, rung, "calibration_no_data:%s" % res["reason"])

    if confidence < res["threshold"]:
        return _escalate_or_exhausted(decision_id, rung, "below_calibrated_threshold")

    return _act(decision, risk_class, res["threshold"])


def _main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "selftest":
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            dp = os.path.join(d, "decisions.jsonl")
            op = os.path.join(d, "outcomes.jsonl")
            for i in range(40):
                did = "s%d" % i
                jev_calibration.append_decision(dp, {
                    "id": did, "family": "f", "qtype": "noul", "framing": "h",
                    "answer": True, "prob": 0.95, "confidence": 0.95, "model": "m",
                    "cost": 0.0, "at": "2026-09-18T00:00:00Z",
                })
                jev_calibration.append_outcome(op, {"id": did, "correct": True, "source": "t", "at": "2026-09-18T00:01:00Z"})
            handle = CalibrationHandle(dp, op)
            result = route(
                {"id": "new1", "family": "f", "qtype": "noul", "confidence": 0.95},
                "low", handle,
            )
            assert result["outcome"] == "ACT", result
            print("selftest OK")
        return 0
    print("usage: jev_cascade.py selftest", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_main())
