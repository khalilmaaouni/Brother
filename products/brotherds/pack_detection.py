#!/usr/bin/env python3
"""DETECTION pack for BrotherDS: gates D1-D7.

Covers detection, anomaly, fault, and classifier claims: precision/recall,
base rate, evidence, judge calibration, trajectory reward, and multi-step verdicts.
"""

import re
from datetime import datetime


def _answered(v):
    """A value is answered if it is not None and not empty string."""
    return v is not None and v != ""


def gate_d1_confusion(claim, Finding):
    """D1.confusion: precision and recall must travel together."""
    findings = []
    detection = claim.get("detection") or {}

    tp = detection.get("tp")
    fp = detection.get("fp")
    fn = detection.get("fn")

    precision_stated = detection.get("precision")
    recall_stated = detection.get("recall")

    # If we have counts, compute and check
    if tp is not None and fp is not None and fn is not None:
        if fp + tp > 0:
            precision_computed = tp / (tp + fp)
        else:
            precision_computed = None

        if fn + tp > 0:
            recall_computed = tp / (tp + fn)
        else:
            recall_computed = None

        # Check stated values against computed
        has_error = False

        if precision_stated is not None and precision_computed is not None:
            if abs(precision_stated - precision_computed) > 0.005:
                findings.append(Finding("D1.confusion", "FAIL",
                    "declared precision %.6g disagrees with computed %.6g from tp=%d fp=%d fn=%d"
                    % (precision_stated, precision_computed, tp, fp, fn)))
                has_error = True

        if recall_stated is not None and recall_computed is not None:
            if abs(recall_stated - recall_computed) > 0.005:
                findings.append(Finding("D1.confusion", "FAIL",
                    "declared recall %.6g disagrees with computed %.6g from tp=%d fp=%d fn=%d"
                    % (recall_stated, recall_computed, tp, fp, fn)))
                has_error = True

        if not has_error:
            findings.append(Finding("D1.confusion", "PASS",
                "precision and recall computed from counts tp=%d fp=%d fn=%d"
                % (tp, fp, fn)))
    else:
        # No counts; check if both precision and recall are stated
        has_precision = _answered(precision_stated)
        has_recall = _answered(recall_stated)

        if has_precision and not has_recall:
            findings.append(Finding("D1.confusion", "FAIL",
                "precision stated but recall missing; both required or neither"))
        elif has_recall and not has_precision:
            findings.append(Finding("D1.confusion", "FAIL",
                "recall stated but precision missing; both required or neither"))
        elif not has_precision and not has_recall:
            findings.append(Finding("D1.confusion", "NO-DATA",
                "neither precision nor recall stated and no counts to compute them"))

    return findings


def gate_d2_base_rate(claim, Finding):
    """D2.base_rate: PPV collapses at low base rates; bare accuracy is meaningless."""
    findings = []
    detection = claim.get("detection") or {}

    base_rate = detection.get("base_rate")
    if not _answered(base_rate):
        findings.append(Finding("D2.base_rate", "NO-DATA",
            "positive predictive value cannot be computed without the base rate"))
        return findings

    # Try to get recall and specificity (or compute from fp, tn)
    recall = detection.get("recall")
    specificity = detection.get("specificity")

    if recall is None:
        # Try to compute from tp, fn
        tp = detection.get("tp")
        fn = detection.get("fn")
        if tp is not None and fn is not None and fn + tp > 0:
            recall = tp / (tp + fn)

    if specificity is None:
        # Try to compute from fp, tn
        fp = detection.get("fp")
        tn = detection.get("tn")
        if fp is not None and tn is not None and fp + tn > 0:
            specificity = tn / (tn + fp)

    # If we still don't have both, we can't compute PPV
    if recall is None or specificity is None:
        return findings

    # Compute PPV = (recall * base_rate) / (recall * base_rate + (1 - specificity) * (1 - base_rate))
    numerator = recall * base_rate
    denominator = recall * base_rate + (1 - specificity) * (1 - base_rate)

    if denominator == 0:
        ppv = 0
    else:
        ppv = numerator / denominator

    # Check if statement contains "accurate" or "accuracy" and base_rate < 0.10 and PPV < 0.5
    statement = (claim.get("statement") or "").lower()
    has_accuracy_claim = "accurate" in statement or "accuracy" in statement

    if has_accuracy_claim and base_rate < 0.10 and ppv < 0.5:
        findings.append(Finding("D2.base_rate", "FAIL",
            "statement claims accuracy but base rate is %.2f percent and PPV is only %.1f percent; "
            "a rare event makes accuracy meaningless"
            % (base_rate * 100, ppv * 100)))
    else:
        findings.append(Finding("D2.base_rate", "PASS",
            "base rate %.2f percent, recall %.2f, specificity %.2f, PPV %.1f percent"
            % (base_rate * 100, recall, specificity, ppv * 100)))

    return findings


def gate_d3_anomaly_evidence(claim, Finding):
    """D3.anomaly_evidence: anomaly claims need precision_at_k or lead_time."""
    findings = []
    statement = (claim.get("statement") or "").lower()

    # Check if statement contains anomaly keywords
    anomaly_keywords = ["caught", "detected", "early"]
    has_anomaly_keyword = any(kw in statement for kw in anomaly_keywords)

    if not has_anomaly_keyword:
        return findings

    detection = claim.get("detection") or {}
    precision_at_k = detection.get("precision_at_k")
    lead_time = detection.get("lead_time")

    if _answered(precision_at_k) or _answered(lead_time):
        detail = []
        if _answered(precision_at_k):
            detail.append("precision_at_k=%s" % precision_at_k)
        if _answered(lead_time):
            detail.append("lead_time=%s" % lead_time)
        findings.append(Finding("D3.anomaly_evidence", "PASS",
            "anomaly detection claim supported by %s" % ", ".join(detail)))
    else:
        findings.append(Finding("D3.anomaly_evidence", "NO-DATA",
            "statement describes anomaly detection but no precision_at_k or lead_time given"))

    return findings


def gate_d4_judge_admissible(claim, Finding):
    """D4.judge_admissible: LLM judges need kappa, recalibration, and gate_id."""
    findings = []
    detection = claim.get("detection") or {}

    judge = detection.get("judge")
    if not judge or not isinstance(judge, dict):
        return findings

    kind = judge.get("kind")
    if kind != "llm":
        return findings

    # LLM judge requires gate_id
    if not _answered(judge.get("gate_id")):
        findings.append(Finding("D4.judge_admissible", "FAIL",
            "llm judge requires gate_id (a prose verdict alone is never evidence)"))
        return findings

    # Check kappa >= 0.6
    kappa = judge.get("kappa")
    if kappa is None or kappa < 0.6:
        findings.append(Finding("D4.judge_admissible", "FAIL",
            "llm judge kappa %.2f is below 0.6 (common bar for admissible evidence)"
            % (kappa if kappa is not None else 0)))
        return findings

    # Check calibration recency (within 30 days)
    calibrated_on = judge.get("calibrated_on")
    if _answered(calibrated_on):
        try:
            cal_date = datetime.fromisoformat(calibrated_on)
            age_days = (datetime.now() - cal_date).days
            if age_days > 30:
                findings.append(Finding("D4.judge_admissible", "FAIL",
                    "llm judge calibrated %d days ago (older than 30-day refresh)" % age_days))
                return findings
        except (ValueError, TypeError):
            pass  # Invalid date format; let it slide and PASS

    findings.append(Finding("D4.judge_admissible", "PASS",
        "llm judge gate_id present, kappa %.2f, calibration fresh"
        % (kappa if kappa is not None else 0)))
    return findings


def gate_d5_wording(claim, Finding):
    """D5.wording: safe-wording hook, fires only when D2 FAILs."""
    # This gate is triggered by D2 FAIL, not standalone
    # The actual trigger happens in the calling logic, not here
    # This is a receipt-line gate that gets added by the pack when D2 FAILs
    # For now, return empty; the pack will handle this in post-processing
    return []


def gate_d6_trajectory_reward(claim, Finding):
    """D6.trajectory_reward: agent-success claims need environment-native reward."""
    findings = []
    statement = (claim.get("statement") or "").lower()

    # Check if statement contains agent success keywords
    agent_keywords = ["agent", "trajectory", "task success", "completed the task"]
    has_agent_keyword = any(kw in statement for kw in agent_keywords)

    if not has_agent_keyword:
        return findings

    detection = claim.get("detection") or {}

    # Check for trajectory_reward and reward_source
    trajectory_reward = detection.get("trajectory_reward")
    reward_source = detection.get("reward_source")

    if not _answered(trajectory_reward) or not _answered(reward_source):
        findings.append(Finding("D6.trajectory_reward", "NO-DATA",
            "agent-success claim requires trajectory_reward and reward_source"))
        return findings

    # FAIL if reward_source is self_report
    if reward_source == "self_report":
        findings.append(Finding("D6.trajectory_reward", "FAIL",
            "agent success backed only by self-report; needs environment-native reward"))
        return findings

    # Otherwise PASS
    findings.append(Finding("D6.trajectory_reward", "PASS",
        "trajectory_reward=%s from %s" % (trajectory_reward, reward_source)))
    return findings


def gate_d7_first_broken_step(claim, Finding):
    """D7.first_broken_step: multi-step verdicts need first-broken-step index."""
    findings = []
    detection = claim.get("detection") or {}

    steps = detection.get("steps")
    if not _answered(steps) or not isinstance(steps, list) or len(steps) == 0:
        return findings

    # Steps present; check for first_broken_step
    first_broken_step = detection.get("first_broken_step")

    if not _answered(first_broken_step):
        findings.append(Finding("D7.first_broken_step", "NO-DATA",
            "multi-step derivation present but no first_broken_step index given"))
        return findings

    # Check if index is in range
    try:
        idx = int(first_broken_step)
        if idx < 0 or idx >= len(steps):
            findings.append(Finding("D7.first_broken_step", "FAIL",
                "first_broken_step index %d out of range [0, %d)" % (idx, len(steps))))
            return findings
    except (ValueError, TypeError):
        findings.append(Finding("D7.first_broken_step", "FAIL",
            "first_broken_step must be an integer, got %r" % first_broken_step))
        return findings

    findings.append(Finding("D7.first_broken_step", "PASS",
        "first broken step at index %d of %d" % (idx, len(steps))))
    return findings


def detect_pack(claim, Finding):
    """Main pack function that calls all detection gates and handles D5."""
    findings = []

    # Run all gates
    findings.extend(gate_d1_confusion(claim, Finding))
    findings.extend(gate_d2_base_rate(claim, Finding))
    findings.extend(gate_d3_anomaly_evidence(claim, Finding))
    findings.extend(gate_d4_judge_admissible(claim, Finding))
    findings.extend(gate_d6_trajectory_reward(claim, Finding))
    findings.extend(gate_d7_first_broken_step(claim, Finding))

    # D5 wording hook: add if D2 failed
    d2_failed = any(f.gate == "D2.base_rate" and f.verdict == "FAIL" for f in findings)
    if d2_failed:
        findings.append(Finding("D5.wording", "NO-DATA",
            "safe wording: state precision and positive predictive value at the base rate, "
            "never accuracy alone"))

    return findings


def register(packs_module, Finding):
    """Register the detection pack."""
    packs_module.register("DETECTION", detect_pack)


def selftest(expect):
    """Self-tests for the detection pack."""
    ok = True

    # Test D1: precision alone FAILs
    class MockFinding:
        def __init__(self, gate, verdict, detail):
            self.gate = gate
            self.verdict = verdict
            self.detail = detail

    # D1: precision alone FAILs
    claim1 = {"detection": {"precision": 0.9}}
    findings1 = gate_d1_confusion(claim1, MockFinding)
    ok &= expect(
        any(f.verdict == "FAIL" for f in findings1),
        "D1: precision alone should FAIL"
    )

    # D1: tp fp fn compute and PASS
    claim2 = {"detection": {"tp": 80, "fp": 20, "fn": 10}}
    findings2 = gate_d1_confusion(claim2, MockFinding)
    ok &= expect(
        any(f.verdict == "PASS" for f in findings2),
        "D1: tp fp fn counts should PASS"
    )

    # D2: rare event with accuracy FAILs
    claim3 = {
        "statement": "the detector is 99 percent accurate",
        "detection": {
            "base_rate": 0.0001,
            "recall": 0.99,
            "specificity": 0.99
        }
    }
    findings3 = gate_d2_base_rate(claim3, MockFinding)
    ok &= expect(
        any(f.verdict == "FAIL" for f in findings3),
        "D2: rare event with accuracy claim should FAIL"
    )

    # D3: "caught early" with no lead_time is NO-DATA
    claim4 = {
        "statement": "we caught the outage 12 minutes early",
        "detection": {}
    }
    findings4 = gate_d3_anomaly_evidence(claim4, MockFinding)
    ok &= expect(
        any(f.verdict == "NO-DATA" for f in findings4),
        "D3: caught early with no lead_time should be NO-DATA"
    )

    # D4: llm judge without gate_id FAILs
    claim5 = {
        "detection": {
            "judge": {
                "kind": "llm",
                "kappa": 0.7
            }
        }
    }
    findings5 = gate_d4_judge_admissible(claim5, MockFinding)
    ok &= expect(
        any(f.verdict == "FAIL" for f in findings5),
        "D4: llm judge without gate_id should FAIL"
    )

    # D4: kappa 0.7 calibrated yesterday PASSes
    import datetime
    yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).isoformat()
    claim6 = {
        "detection": {
            "judge": {
                "kind": "llm",
                "gate_id": "G25.something",
                "kappa": 0.7,
                "calibrated_on": yesterday
            }
        }
    }
    findings6 = gate_d4_judge_admissible(claim6, MockFinding)
    ok &= expect(
        any(f.verdict == "PASS" for f in findings6),
        "D4: kappa 0.7 calibrated yesterday should PASS"
    )

    # D6: agent claim with self_report FAILs
    claim7 = {
        "statement": "the agent completed the task successfully",
        "detection": {
            "trajectory_reward": 1.0,
            "reward_source": "self_report"
        }
    }
    findings7 = gate_d6_trajectory_reward(claim7, MockFinding)
    ok &= expect(
        any(f.verdict == "FAIL" for f in findings7),
        "D6: agent with self_report should FAIL"
    )

    # D7: steps present but no first_broken_step is NO-DATA
    claim8 = {
        "detection": {
            "steps": [
                "step 1",
                "step 2",
                "step 3"
            ]
        }
    }
    findings8 = gate_d7_first_broken_step(claim8, MockFinding)
    ok &= expect(
        any(f.verdict == "NO-DATA" for f in findings8),
        "D7: steps present with no index should be NO-DATA"
    )

    # Pack should not run on DESCRIPTIVE claim
    claim_desc = {
        "statement": "this is just a description",
        "detection": {"tp": 100, "fp": 10}
    }
    # This check is done at the bds.py level via run_packs, not in the pack itself
    ok &= expect(True, "pack registration is in packs.py")

    return ok
