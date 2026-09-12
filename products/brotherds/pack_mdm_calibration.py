"""Calibration and cost-threshold gates for master data management claims.

Background: a match score is only safe to threshold as a probability when it is
calibrated, that is, among pairs scored about 0.9, about 90 percent are true
matches (reliability diagrams, expected calibration error, Guo et al. 2017;
Fellegi-Sunter posterior match probabilities as produced by probabilistic
linkage tools). Given calibrated probabilities and per-error costs, the
Bayes-optimal rule merges a pair when P(match) > c_fm / (c_fm + c_mm), where
c_fm is the cost of a false merge and c_mm the cost of a missed match (standard
decision theory; Hand and Christen 2018 argue the trade-off belongs to the
problem owner).

This module registers two gates:
- M20.score_calibration
- M21.threshold_cost

It follows the pack contract: missing evidence is NO-DATA, malformed input is
a FAIL naming the field, and no input should cause a crash.
"""

import math
import sys

import mdm_eval


def _is_number(value):
    """Return True for int or float values, excluding bool."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_finite_number(value):
    """Return True for finite int or float values, excluding bool, NaN, and inf."""
    return _is_number(value) and math.isfinite(value)


def score_calibration(claim, Finding):
    """Gate M20.score_calibration."""
    if not isinstance(claim, dict):
        return []
    master_data = claim.get("master_data")
    if not isinstance(master_data, dict):
        return []
    if "evaluation" not in master_data:
        return [
            Finding(
                "M20.score_calibration",
                "NO-DATA",
                "scores are not declared as probabilities, so calibration cannot be judged",
            )
        ]
    evaluation = master_data.get("evaluation")
    if not isinstance(evaluation, dict):
        return [
            Finding(
                "M20.score_calibration",
                "FAIL",
                "master_data.evaluation must be an object",
            )
        ]
    if evaluation.get("scores_are_probabilities") is not True:
        return [
            Finding(
                "M20.score_calibration",
                "NO-DATA",
                "scores are not declared as probabilities, so calibration cannot be judged",
            )
        ]
    review_strata = evaluation.get("review_strata")
    if review_strata is None:
        return [
            Finding(
                "M20.score_calibration",
                "NO-DATA",
                "fewer than 3 strata carry mean_score, so calibration cannot be judged",
            )
        ]
    if not isinstance(review_strata, list):
        return [
            Finding(
                "M20.score_calibration",
                "FAIL",
                "master_data.evaluation.review_strata must be a list",
            )
        ]

    strata_with_mean = []
    for i, stratum in enumerate(review_strata):
        if not isinstance(stratum, dict):
            return [
                Finding(
                    "M20.score_calibration",
                    "FAIL",
                    f"master_data.evaluation.review_strata[{i}] must be an object",
                )
            ]
        if "mean_score" not in stratum:
            continue
        mean_score = stratum.get("mean_score")
        if not _is_finite_number(mean_score):
            return [
                Finding(
                    "M20.score_calibration",
                    "FAIL",
                    f"master_data.evaluation.review_strata[{i}].mean_score must be a finite number",
                )
            ]
        if mean_score < 0.0 or mean_score > 1.0:
            return [
                Finding(
                    "M20.score_calibration",
                    "FAIL",
                    f"master_data.evaluation.review_strata[{i}].mean_score must be in [0,1]",
                )
            ]
        sampled = stratum.get("sampled")
        positive = stratum.get("positive")
        if not isinstance(sampled, int) or isinstance(sampled, bool) or sampled < 1:
            return [
                Finding(
                    "M20.score_calibration",
                    "FAIL",
                    f"master_data.evaluation.review_strata[{i}].sampled must be an integer >= 1",
                )
            ]
        if (
            not isinstance(positive, int)
            or isinstance(positive, bool)
            or positive < 0
            or positive > sampled
        ):
            return [
                Finding(
                    "M20.score_calibration",
                    "FAIL",
                    f"master_data.evaluation.review_strata[{i}].positive must be an integer between 0 and sampled",
                )
            ]
        score_lo = stratum.get("score_lo")
        score_hi = stratum.get("score_hi")
        if not _is_finite_number(score_lo):
            return [
                Finding(
                    "M20.score_calibration",
                    "FAIL",
                    f"master_data.evaluation.review_strata[{i}].score_lo must be a finite number",
                )
            ]
        if not _is_finite_number(score_hi):
            return [
                Finding(
                    "M20.score_calibration",
                    "FAIL",
                    f"master_data.evaluation.review_strata[{i}].score_hi must be a finite number",
                )
            ]
        if score_lo < 0.0 or score_lo > 1.0:
            return [
                Finding(
                    "M20.score_calibration",
                    "FAIL",
                    f"master_data.evaluation.review_strata[{i}].score_lo must be in [0,1]",
                )
            ]
        if score_hi < 0.0 or score_hi > 1.0:
            return [
                Finding(
                    "M20.score_calibration",
                    "FAIL",
                    f"master_data.evaluation.review_strata[{i}].score_hi must be in [0,1]",
                )
            ]
        if score_lo > score_hi:
            return [
                Finding(
                    "M20.score_calibration",
                    "FAIL",
                    f"master_data.evaluation.review_strata[{i}] has score_lo greater than score_hi",
                )
            ]
        strata_with_mean.append((i, stratum, mean_score, sampled, positive, score_lo, score_hi))

    if len(strata_with_mean) < 3:
        return [
            Finding(
                "M20.score_calibration",
                "NO-DATA",
                "fewer than 3 strata carry mean_score, so calibration cannot be judged",
            )
        ]

    total_sampled = 0
    for item in strata_with_mean:
        total_sampled += item[3]

    ece = 0.0
    miscalibrated = []
    for i, stratum, mean_score, sampled, positive, score_lo, score_hi in strata_with_mean:
        observed = positive / float(sampled)
        lo, hi = mdm_eval.wilson_interval(positive, sampled)
        ece += (sampled / float(total_sampled)) * abs(observed - mean_score)
        if mean_score < lo or mean_score > hi:
            miscalibrated.append((i, stratum, mean_score, observed, lo, hi, score_lo, score_hi))

    if miscalibrated:
        parts = []
        for i, stratum, mean_score, observed, lo, hi, score_lo, score_hi in miscalibrated:
            band = f"{score_lo:.3f} to {score_hi:.3f}"
            parts.append(
                f"{band}: predicted {mean_score:.3f}, observed {observed:.3f} [{lo:.3f}, {hi:.3f}]"
            )
        detail = "miscalibrated: " + "; ".join(parts) + f"; ECE {ece:.3f}"
        return [Finding("M20.score_calibration", "FAIL", detail)]

    detail = f"calibration passes; ECE {ece:.3f}"
    return [Finding("M20.score_calibration", "PASS", detail)]


def threshold_cost(claim, Finding):
    """Gate M21.threshold_cost."""
    if not isinstance(claim, dict):
        return []
    master_data = claim.get("master_data")
    if not isinstance(master_data, dict):
        return []
    if "evaluation" not in master_data:
        return [
            Finding(
                "M21.threshold_cost",
                "NO-DATA",
                "a cost-derived threshold only applies to calibrated probabilities",
            )
        ]
    evaluation = master_data.get("evaluation")
    if not isinstance(evaluation, dict):
        return [
            Finding(
                "M21.threshold_cost",
                "FAIL",
                "master_data.evaluation must be an object",
            )
        ]
    if evaluation.get("scores_are_probabilities") is not True:
        return [
            Finding(
                "M21.threshold_cost",
                "NO-DATA",
                "a cost-derived threshold only applies to calibrated probabilities",
            )
        ]

    error_costs = evaluation.get("error_costs")
    merge_threshold = master_data.get("merge_threshold")
    if error_costs is None or merge_threshold is None:
        missing = []
        if error_costs is None:
            missing.append("error_costs")
        if merge_threshold is None:
            missing.append("merge_threshold")
        return [
            Finding(
                "M21.threshold_cost",
                "NO-DATA",
                "missing evidence: " + ", ".join(missing),
            )
        ]
    if not isinstance(error_costs, dict):
        return [
            Finding(
                "M21.threshold_cost",
                "FAIL",
                "master_data.evaluation.error_costs must be an object",
            )
        ]

    false_merge = error_costs.get("false_merge")
    missed_match = error_costs.get("missed_match")
    if not _is_finite_number(false_merge):
        return [
            Finding(
                "M21.threshold_cost",
                "FAIL",
                "master_data.evaluation.error_costs.false_merge must be a finite number",
            )
        ]
    if false_merge <= 0:
        return [
            Finding(
                "M21.threshold_cost",
                "FAIL",
                "master_data.evaluation.error_costs.false_merge must be a positive number",
            )
        ]
    if not _is_finite_number(missed_match):
        return [
            Finding(
                "M21.threshold_cost",
                "FAIL",
                "master_data.evaluation.error_costs.missed_match must be a finite number",
            )
        ]
    if missed_match <= 0:
        return [
            Finding(
                "M21.threshold_cost",
                "FAIL",
                "master_data.evaluation.error_costs.missed_match must be a positive number",
            )
        ]
    if not _is_finite_number(merge_threshold):
        return [
            Finding(
                "M21.threshold_cost",
                "FAIL",
                "master_data.merge_threshold must be a finite number",
            )
        ]
    if merge_threshold < 0.0 or merge_threshold > 1.0:
        return [
            Finding(
                "M21.threshold_cost",
                "FAIL",
                "master_data.merge_threshold must be in [0,1]",
            )
        ]

    t_star = false_merge / (false_merge + missed_match)
    if merge_threshold < t_star - 0.02:
        detail = (
            f"merge_threshold {merge_threshold:.3f} is below cost-implied threshold {t_star:.3f}; "
            f"the costs imply merging only above {t_star:.3f}"
        )
        return [Finding("M21.threshold_cost", "FAIL", detail)]

    detail = f"merge_threshold {merge_threshold:.3f} meets cost-implied threshold {t_star:.3f}"
    if merge_threshold > t_star + 0.10:
        detail += "; threshold is more conservative than the costs require"
    return [Finding("M21.threshold_cost", "PASS", detail)]


def register(packs_module, Finding):
    """Register M20 and M21 with the pack module."""
    packs_module.register("MASTER_DATA", score_calibration)
    packs_module.register("MASTER_DATA", threshold_cost)


def selftest(expect):
    """Run self-test and return True if all assertions pass."""
    ok = True

    class Finding:
        def __init__(self, gate, verdict, detail):
            self.gate = gate
            self.verdict = verdict
            self.detail = detail

    def base_claim():
        return {
            "master_data": {
                "merge_threshold": 0.5,
                "evaluation": {
                    "scores_are_probabilities": True,
                    "review_strata": [],
                    "error_costs": {"false_merge": 1, "missed_match": 1},
                },
            }
        }

    ok &= expect(score_calibration({}, Finding) == [], "M20 returns [] when master_data absent")
    ok &= expect(threshold_cost({}, Finding) == [], "M21 returns [] when master_data absent")
    ok &= expect(
        score_calibration({"master_data": []}, Finding) == [],
        "M20 returns [] when master_data not dict",
    )
    ok &= expect(
        threshold_cost({"master_data": []}, Finding) == [],
        "M21 returns [] when master_data not dict",
    )

    res = score_calibration({"master_data": {}}, Finding)
    ok &= expect(len(res) == 1, "M20 returns one finding when evaluation missing")
    ok &= expect(res[0].verdict == "NO-DATA", "M20 NO-DATA when evaluation missing")
    ok &= expect(
        "not declared as probabilities" in res[0].detail,
        "M20 detail when evaluation missing",
    )

    res = score_calibration({"master_data": {"evaluation": []}}, Finding)
    ok &= expect(res[0].verdict == "FAIL", "M20 FAIL when evaluation not dict")
    ok &= expect("evaluation" in res[0].detail, "M20 detail names evaluation")

    claim = base_claim()
    claim["master_data"]["evaluation"]["scores_are_probabilities"] = False
    res = score_calibration(claim, Finding)
    ok &= expect(res[0].verdict == "NO-DATA", "M20 NO-DATA when scores_are_probabilities False")
    ok &= expect(
        "not declared as probabilities" in res[0].detail,
        "M20 detail scores not probabilities",
    )

    claim = base_claim()
    claim["master_data"]["evaluation"]["review_strata"] = [
        {
            "score_lo": 0.0,
            "score_hi": 0.5,
            "population": 100,
            "sampled": 10,
            "positive": 5,
            "mean_score": 0.5,
        }
    ]
    res = score_calibration(claim, Finding)
    ok &= expect(res[0].verdict == "NO-DATA", "M20 NO-DATA fewer than 3 strata")
    ok &= expect("fewer than 3" in res[0].detail, "M20 detail fewer than 3")

    claim = base_claim()
    claim["master_data"]["evaluation"]["review_strata"] = {}
    res = score_calibration(claim, Finding)
    ok &= expect(res[0].verdict == "FAIL", "M20 FAIL review_strata not list")
    ok &= expect("review_strata" in res[0].detail, "M20 detail review_strata")

    claim = base_claim()
    claim["master_data"]["evaluation"]["review_strata"] = [1, 2, 3]
    res = score_calibration(claim, Finding)
    ok &= expect(res[0].verdict == "FAIL", "M20 FAIL stratum not dict")
    ok &= expect("review_strata[0]" in res[0].detail, "M20 detail stratum index")

    claim = base_claim()
    claim["master_data"]["evaluation"]["review_strata"] = [
        {
            "score_lo": 0.0,
            "score_hi": 0.3,
            "population": 100,
            "sampled": 10,
            "positive": 5,
            "mean_score": 1.5,
        },
        {
            "score_lo": 0.3,
            "score_hi": 0.6,
            "population": 100,
            "sampled": 10,
            "positive": 5,
            "mean_score": 0.5,
        },
        {
            "score_lo": 0.6,
            "score_hi": 1.0,
            "population": 100,
            "sampled": 10,
            "positive": 5,
            "mean_score": 0.5,
        },
    ]
    res = score_calibration(claim, Finding)
    ok &= expect(res[0].verdict == "FAIL", "M20 FAIL invalid mean_score")
    ok &= expect("mean_score" in res[0].detail, "M20 detail mean_score")

    claim = base_claim()
    claim["master_data"]["evaluation"]["review_strata"] = [
        {"score_lo": 0.0, "score_hi": 0.3, "sampled": 10, "positive": 5, "mean_score": float("nan")},
        {"score_lo": 0.3, "score_hi": 0.6, "sampled": 10, "positive": 5, "mean_score": 0.5},
        {"score_lo": 0.6, "score_hi": 1.0, "sampled": 10, "positive": 5, "mean_score": 0.5},
    ]
    res = score_calibration(claim, Finding)
    ok &= expect(res[0].verdict == "FAIL", "M20 FAIL mean_score NaN")
    ok &= expect("mean_score" in res[0].detail, "M20 detail mean_score NaN")

    claim = base_claim()
    claim["master_data"]["evaluation"]["review_strata"] = [
        {"score_lo": 0.0, "score_hi": 0.3, "sampled": 10, "positive": 5, "mean_score": float("inf")},
        {"score_lo": 0.3, "score_hi": 0.6, "sampled": 10, "positive": 5, "mean_score": 0.5},
        {"score_lo": 0.6, "score_hi": 1.0, "sampled": 10, "positive": 5, "mean_score": 0.5},
    ]
    res = score_calibration(claim, Finding)
    ok &= expect(res[0].verdict == "FAIL", "M20 FAIL mean_score inf")
    ok &= expect("mean_score" in res[0].detail, "M20 detail mean_score inf")

    claim = base_claim()
    claim["master_data"]["evaluation"]["review_strata"] = [
        {"score_lo": 0.0, "score_hi": 0.3, "sampled": 10, "positive": 5, "mean_score": True},
        {"score_lo": 0.3, "score_hi": 0.6, "sampled": 10, "positive": 5, "mean_score": 0.5},
        {"score_lo": 0.6, "score_hi": 1.0, "sampled": 10, "positive": 5, "mean_score": 0.5},
    ]
    res = score_calibration(claim, Finding)
    ok &= expect(res[0].verdict == "FAIL", "M20 FAIL mean_score bool")
    ok &= expect("mean_score" in res[0].detail, "M20 detail mean_score bool")

    claim = base_claim()
    claim["master_data"]["evaluation"]["review_strata"] = [
        {"score_lo": 0.5, "score_hi": 0.3, "sampled": 10, "positive": 5, "mean_score": 0.5},
        {"score_lo": 0.3, "score_hi": 0.6, "sampled": 10, "positive": 5, "mean_score": 0.5},
        {"score_lo": 0.6, "score_hi": 1.0, "sampled": 10, "positive": 5, "mean_score": 0.5},
    ]
    res = score_calibration(claim, Finding)
    ok &= expect(res[0].verdict == "FAIL", "M20 FAIL score_lo > score_hi")
    ok &= expect("review_strata[0]" in res[0].detail, "M20 detail stratum index for lo>hi")

    claim = base_claim()
    claim["master_data"]["evaluation"]["review_strata"] = [
        {"score_lo": -0.1, "score_hi": 0.3, "sampled": 10, "positive": 5, "mean_score": 0.2},
        {"score_lo": 0.3, "score_hi": 0.6, "sampled": 10, "positive": 5, "mean_score": 0.5},
        {"score_lo": 0.6, "score_hi": 1.0, "sampled": 10, "positive": 5, "mean_score": 0.5},
    ]
    res = score_calibration(claim, Finding)
    ok &= expect(res[0].verdict == "FAIL", "M20 FAIL score_lo below 0")
    ok &= expect("score_lo" in res[0].detail, "M20 detail score_lo below 0")

    claim = base_claim()
    claim["master_data"]["evaluation"]["review_strata"] = [
        {"score_lo": 0.0, "score_hi": 1.2, "sampled": 10, "positive": 5, "mean_score": 0.2},
        {"score_lo": 0.3, "score_hi": 0.6, "sampled": 10, "positive": 5, "mean_score": 0.5},
        {"score_lo": 0.6, "score_hi": 1.0, "sampled": 10, "positive": 5, "mean_score": 0.5},
    ]
    res = score_calibration(claim, Finding)
    ok &= expect(res[0].verdict == "FAIL", "M20 FAIL score_hi above 1")
    ok &= expect("score_hi" in res[0].detail, "M20 detail score_hi above 1")

    claim = base_claim()
    claim["master_data"]["evaluation"]["review_strata"] = [
        {"score_lo": float("nan"), "score_hi": 0.3, "sampled": 10, "positive": 5, "mean_score": 0.2},
        {"score_lo": 0.3, "score_hi": 0.6, "sampled": 10, "positive": 5, "mean_score": 0.5},
        {"score_lo": 0.6, "score_hi": 1.0, "sampled": 10, "positive": 5, "mean_score": 0.5},
    ]
    res = score_calibration(claim, Finding)
    ok &= expect(res[0].verdict == "FAIL", "M20 FAIL score_lo NaN")
    ok &= expect("score_lo" in res[0].detail, "M20 detail score_lo NaN")

    claim = base_claim()
    claim["master_data"]["evaluation"]["review_strata"] = [
        {"score_lo": 0.0, "score_hi": float("inf"), "sampled": 10, "positive": 5, "mean_score": 0.2},
        {"score_lo": 0.3, "score_hi": 0.6, "sampled": 10, "positive": 5, "mean_score": 0.5},
        {"score_lo": 0.6, "score_hi": 1.0, "sampled": 10, "positive": 5, "mean_score": 0.5},
    ]
    res = score_calibration(claim, Finding)
    ok &= expect(res[0].verdict == "FAIL", "M20 FAIL score_hi inf")
    ok &= expect("score_hi" in res[0].detail, "M20 detail score_hi inf")

    claim = base_claim()
    claim["master_data"]["evaluation"]["review_strata"] = [
        {
            "score_lo": 0.0,
            "score_hi": 0.3,
            "population": 100,
            "sampled": 0,
            "positive": 0,
            "mean_score": 0.1,
        },
        {
            "score_lo": 0.3,
            "score_hi": 0.6,
            "population": 100,
            "sampled": 10,
            "positive": 5,
            "mean_score": 0.5,
        },
        {
            "score_lo": 0.6,
            "score_hi": 1.0,
            "population": 100,
            "sampled": 10,
            "positive": 5,
            "mean_score": 0.5,
        },
    ]
    res = score_calibration(claim, Finding)
    ok &= expect(res[0].verdict == "FAIL", "M20 FAIL sampled=0")
    ok &= expect("sampled" in res[0].detail, "M20 detail sampled")

    claim = base_claim()
    claim["master_data"]["evaluation"]["review_strata"] = [
        {
            "score_lo": 0.0,
            "score_hi": 0.3,
            "population": 100,
            "sampled": 10,
            "positive": 11,
            "mean_score": 0.1,
        },
        {
            "score_lo": 0.3,
            "score_hi": 0.6,
            "population": 100,
            "sampled": 10,
            "positive": 5,
            "mean_score": 0.5,
        },
        {
            "score_lo": 0.6,
            "score_hi": 1.0,
            "population": 100,
            "sampled": 10,
            "positive": 5,
            "mean_score": 0.5,
        },
    ]
    res = score_calibration(claim, Finding)
    ok &= expect(res[0].verdict == "FAIL", "M20 FAIL positive > sampled")
    ok &= expect("positive" in res[0].detail, "M20 detail positive")

    claim = base_claim()
    claim["master_data"]["evaluation"]["review_strata"] = [
        {
            "score_lo": 0.0,
            "score_hi": 0.3,
            "population": 100,
            "sampled": 10,
            "positive": 5,
            "mean_score": 0.5,
        },
        {
            "score_lo": 0.3,
            "score_hi": 0.6,
            "population": 100,
            "sampled": 20,
            "positive": 10,
            "mean_score": 0.5,
        },
        {
            "score_lo": 0.6,
            "score_hi": 1.0,
            "population": 100,
            "sampled": 30,
            "positive": 15,
            "mean_score": 0.5,
        },
    ]
    res = score_calibration(claim, Finding)
    ok &= expect(res[0].verdict == "PASS", "M20 PASS calibrated")
    ok &= expect("ECE 0.000" in res[0].detail, "M20 PASS ECE detail")
    ok &= expect(res[0].gate == "M20.score_calibration", "M20 gate name")

    claim = base_claim()
    claim["master_data"]["evaluation"]["review_strata"] = [
        {
            "score_lo": 0.0,
            "score_hi": 0.3,
            "population": 100,
            "sampled": 10,
            "positive": 5,
            "mean_score": 0.5,
        },
        {
            "score_lo": 0.3,
            "score_hi": 0.6,
            "population": 100,
            "sampled": 20,
            "positive": 10,
            "mean_score": 0.5,
        },
        {
            "score_lo": 0.8,
            "score_hi": 0.9,
            "population": 100,
            "sampled": 30,
            "positive": 3,
            "mean_score": 0.9,
        },
    ]
    res = score_calibration(claim, Finding)
    ok &= expect(res[0].verdict == "FAIL", "M20 FAIL miscalibrated")
    ok &= expect("miscalibrated" in res[0].detail, "M20 detail miscalibrated")
    ok &= expect("0.800 to 0.900" in res[0].detail, "M20 detail band")
    ok &= expect("predicted 0.900" in res[0].detail, "M20 detail predicted")
    ok &= expect("observed 0.100" in res[0].detail, "M20 detail observed")
    ok &= expect("ECE 0.400" in res[0].detail, "M20 detail ECE computed")

    claim = base_claim()
    claim["master_data"]["evaluation"]["scores_are_probabilities"] = False
    res = threshold_cost(claim, Finding)
    ok &= expect(res[0].verdict == "NO-DATA", "M21 NO-DATA scores not probabilities")
    ok &= expect(
        "cost-derived threshold only applies" in res[0].detail,
        "M21 detail scores not probabilities",
    )

    claim = base_claim()
    del claim["master_data"]["evaluation"]["error_costs"]
    res = threshold_cost(claim, Finding)
    ok &= expect(res[0].verdict == "NO-DATA", "M21 NO-DATA error_costs absent")
    ok &= expect("error_costs" in res[0].detail, "M21 detail error_costs absent")

    claim = base_claim()
    del claim["master_data"]["merge_threshold"]
    res = threshold_cost(claim, Finding)
    ok &= expect(res[0].verdict == "NO-DATA", "M21 NO-DATA merge_threshold absent")
    ok &= expect("merge_threshold" in res[0].detail, "M21 detail merge_threshold absent")

    claim = base_claim()
    claim["master_data"]["evaluation"]["error_costs"]["false_merge"] = 0
    res = threshold_cost(claim, Finding)
    ok &= expect(res[0].verdict == "FAIL", "M21 FAIL false_merge zero")
    ok &= expect("false_merge" in res[0].detail, "M21 detail false_merge")

    claim = base_claim()
    claim["master_data"]["evaluation"]["error_costs"]["false_merge"] = float("nan")
    res = threshold_cost(claim, Finding)
    ok &= expect(res[0].verdict == "FAIL", "M21 FAIL false_merge NaN")
    ok &= expect("false_merge" in res[0].detail, "M21 detail false_merge NaN")

    claim = base_claim()
    claim["master_data"]["evaluation"]["error_costs"]["false_merge"] = float("inf")
    res = threshold_cost(claim, Finding)
    ok &= expect(res[0].verdict == "FAIL", "M21 FAIL false_merge inf")
    ok &= expect("false_merge" in res[0].detail, "M21 detail false_merge inf")

    claim = base_claim()
    claim["master_data"]["evaluation"]["error_costs"]["false_merge"] = True
    res = threshold_cost(claim, Finding)
    ok &= expect(res[0].verdict == "FAIL", "M21 FAIL false_merge bool")
    ok &= expect("false_merge" in res[0].detail, "M21 detail false_merge bool")

    claim = base_claim()
    claim["master_data"]["evaluation"]["error_costs"]["missed_match"] = "high"
    res = threshold_cost(claim, Finding)
    ok &= expect(res[0].verdict == "FAIL", "M21 FAIL missed_match non-numeric")
    ok &= expect("missed_match" in res[0].detail, "M21 detail missed_match")

    claim = base_claim()
    claim["master_data"]["evaluation"]["error_costs"]["missed_match"] = float("nan")
    res = threshold_cost(claim, Finding)
    ok &= expect(res[0].verdict == "FAIL", "M21 FAIL missed_match NaN")
    ok &= expect("missed_match" in res[0].detail, "M21 detail missed_match NaN")

    claim = base_claim()
    claim["master_data"]["merge_threshold"] = "0.5"
    res = threshold_cost(claim, Finding)
    ok &= expect(res[0].verdict == "FAIL", "M21 FAIL merge_threshold non-numeric")
    ok &= expect("merge_threshold" in res[0].detail, "M21 detail merge_threshold non-numeric")

    claim = base_claim()
    claim["master_data"]["merge_threshold"] = 1.5
    res = threshold_cost(claim, Finding)
    ok &= expect(res[0].verdict == "FAIL", "M21 FAIL merge_threshold out of range")
    ok &= expect("merge_threshold" in res[0].detail, "M21 detail merge_threshold out of range")

    claim = base_claim()
    claim["master_data"]["merge_threshold"] = float("nan")
    res = threshold_cost(claim, Finding)
    ok &= expect(res[0].verdict == "FAIL", "M21 FAIL merge_threshold NaN")
    ok &= expect("merge_threshold" in res[0].detail, "M21 detail merge_threshold NaN")

    claim = base_claim()
    claim["master_data"]["merge_threshold"] = float("inf")
    res = threshold_cost(claim, Finding)
    ok &= expect(res[0].verdict == "FAIL", "M21 FAIL merge_threshold inf")
    ok &= expect("merge_threshold" in res[0].detail, "M21 detail merge_threshold inf")

    claim = base_claim()
    claim["master_data"]["merge_threshold"] = True
    res = threshold_cost(claim, Finding)
    ok &= expect(res[0].verdict == "FAIL", "M21 FAIL merge_threshold bool")
    ok &= expect("merge_threshold" in res[0].detail, "M21 detail merge_threshold bool")

    claim = base_claim()
    claim["master_data"]["evaluation"]["error_costs"] = {"false_merge": 3, "missed_match": 1}
    claim["master_data"]["merge_threshold"] = 0.5
    res = threshold_cost(claim, Finding)
    ok &= expect(res[0].verdict == "FAIL", "M21 FAIL below cost threshold")
    ok &= expect("0.500" in res[0].detail, "M21 detail merge_threshold value")
    ok &= expect("0.750" in res[0].detail, "M21 detail t_star value")
    ok &= expect(
        "costs imply merging only above" in res[0].detail,
        "M21 detail cost implication",
    )

    claim = base_claim()
    claim["master_data"]["evaluation"]["error_costs"] = {"false_merge": 1, "missed_match": 1}
    claim["master_data"]["merge_threshold"] = 0.5
    res = threshold_cost(claim, Finding)
    ok &= expect(res[0].verdict == "PASS", "M21 PASS at threshold")
    ok &= expect(res[0].gate == "M21.threshold_cost", "M21 gate name")

    claim = base_claim()
    claim["master_data"]["evaluation"]["error_costs"] = {"false_merge": 1, "missed_match": 1}
    claim["master_data"]["merge_threshold"] = 0.48
    res = threshold_cost(claim, Finding)
    ok &= expect(res[0].verdict == "PASS", "M21 PASS at lower tolerance")

    claim = base_claim()
    claim["master_data"]["evaluation"]["error_costs"] = {"false_merge": 1, "missed_match": 1}
    claim["master_data"]["merge_threshold"] = 0.479
    res = threshold_cost(claim, Finding)
    ok &= expect(res[0].verdict == "FAIL", "M21 FAIL just below lower tolerance")

    claim = base_claim()
    claim["master_data"]["evaluation"]["error_costs"] = {"false_merge": 1, "missed_match": 1}
    claim["master_data"]["merge_threshold"] = 0.61
    res = threshold_cost(claim, Finding)
    ok &= expect(res[0].verdict == "PASS", "M21 PASS conservative")
    ok &= expect("more conservative" in res[0].detail, "M21 detail conservative")

    class Packs:
        def __init__(self):
            self.calls = []

        def register(self, pack, fn):
            self.calls.append((pack, fn))

    packs = Packs()
    register(packs, Finding)
    ok &= expect(len(packs.calls) == 2, "register makes two calls")
    ok &= expect(packs.calls[0][0] == "MASTER_DATA", "register first pack name")
    ok &= expect(packs.calls[1][0] == "MASTER_DATA", "register second pack name")
    ok &= expect(
        packs.calls[0][1].__name__ == "score_calibration",
        "register first fn name",
    )
    ok &= expect(
        packs.calls[1][1].__name__ == "threshold_cost",
        "register second fn name",
    )

    return ok


if __name__ == "__main__":
    def expect(cond, msg):
        if not cond:
            print(f"SELFTEST FAIL: {msg}")
        return cond

    if selftest(expect):
        print("SELFTEST PASS")
        sys.exit(0)
    else:
        sys.exit(1)
