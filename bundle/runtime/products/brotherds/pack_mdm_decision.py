"""Decision design gates for MDM master data claims.

References:
- Hand and Christen 2018, A note on using the F-measure for evaluating record
  linkage algorithms: the F-measure silently weights precision and recall; the
  problem owner must choose beta.
- Binette et al. 2022/2023: representative estimates of entity-resolution
  performance need probability samples weighted back to the population;
  unweighted benchmark metrics are biased.
- Cochran 1977: sample size for a proportion n = z^2 p (1 - p) / e^2.
- Cohen 1960: kappa for agreement between two labellers.
- Landis and Koch 1977: kappa interpretation bands.
"""

import math
import sys


def _master_data(claim):
    if not isinstance(claim, dict):
        return None
    md = claim.get("master_data")
    if not isinstance(md, dict):
        return None
    return md


def _is_number(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


def _is_intlike(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value) and value.is_integer()
    return False


def _fmt_frac(value):
    return f"{float(value):.3f}"


def _fmt_count(value):
    return f"{int(value):,}"


def _claimed_precision(claim, md):
    evaluation = md.get("evaluation")
    if isinstance(evaluation, dict):
        value = evaluation.get("claimed_precision")
        if value is not None:
            return value
    if isinstance(claim, dict):
        match = claim.get("match")
        if isinstance(match, dict):
            value = match.get("precision")
            if value is not None:
                return value
    return None


def _gate_m16(claim, Finding):
    gate = "M16.metric_choice"
    md = _master_data(claim)
    if md is None:
        return []
    if "evaluation" not in md:
        return [Finding(gate, "NO-DATA", "headline_metric is absent")]
    evaluation = md.get("evaluation")
    if not isinstance(evaluation, dict):
        return [Finding(gate, "FAIL", "evaluation is not an object")]
    metric = evaluation.get("headline_metric")
    if metric is None:
        return [Finding(gate, "NO-DATA", "headline_metric is absent")]
    if not isinstance(metric, str):
        return [Finding(gate, "FAIL", "headline_metric is not a string")]
    cost = md.get("false_merge_cost_class")
    if metric == "accuracy":
        return [Finding(
            gate,
            "FAIL",
            "accuracy on record pairs is dominated by true non-matches (Hand and Christen 2018); use precision, recall, or F-beta",
        )]
    if metric == "f1":
        if cost == "catastrophic":
            return [Finding(
                gate,
                "FAIL",
                "F1 weights a false merge and a missed match equally (Hand and Christen 2018); declare f_beta with beta < 1, or headline precision",
            )]
        return [Finding(gate, "PASS", "metric f1, beta 1.000 (Hand and Christen 2018)")]
    if metric == "f_beta":
        beta = evaluation.get("beta")
        if not _is_number(beta):
            return [Finding(gate, "FAIL", "f_beta requires beta as a number > 0")]
        if beta <= 0:
            return [Finding(gate, "FAIL", "f_beta requires beta > 0")]
        if cost == "catastrophic" and beta >= 1:
            return [Finding(
                gate,
                "FAIL",
                f"catastrophic false merges require beta < 1 (Hand and Christen 2018), got beta {_fmt_frac(beta)}",
            )]
        return [Finding(gate, "PASS", f"metric f_beta, beta {_fmt_frac(beta)} (Hand and Christen 2018)")]
    if metric in ("precision", "recall"):
        beta = evaluation.get("beta")
        if _is_number(beta):
            return [Finding(
                gate,
                "PASS",
                f"metric {metric}, beta {_fmt_frac(beta)} (Hand and Christen 2018)",
            )]
        return [Finding(
            gate,
            "PASS",
            f"metric {metric}, beta not applicable (Hand and Christen 2018)",
        )]
    return [Finding(gate, "FAIL", f"unknown headline_metric {metric!r}")]


def _gate_m17(claim, Finding):
    gate = "M17.review_power"
    md = _master_data(claim)
    if md is None:
        return []
    merge_threshold = md.get("merge_threshold")
    if merge_threshold is None:
        return [Finding(gate, "NO-DATA", "merge_threshold is absent")]
    if not _is_number(merge_threshold):
        return [Finding(gate, "FAIL", "merge_threshold is not a number")]
    if "evaluation" not in md:
        return [Finding(
            gate,
            "NO-DATA",
            "required_margin, claimed precision, and review_strata are absent",
        )]
    evaluation = md.get("evaluation")
    if not isinstance(evaluation, dict):
        return [Finding(gate, "FAIL", "evaluation is not an object")]
    margin = evaluation.get("required_margin")
    if margin is None:
        return [Finding(gate, "NO-DATA", "required_margin is absent")]
    if not _is_number(margin):
        return [Finding(gate, "FAIL", "required_margin is not a number")]
    if not (0 < margin < 0.5):
        return [Finding(
            gate,
            "FAIL",
            f"required_margin {_fmt_frac(margin)} is outside (0, 0.5)",
        )]
    claimed = _claimed_precision(claim, md)
    if claimed is None:
        return [Finding(gate, "NO-DATA", "claimed precision is absent")]
    if not _is_number(claimed):
        return [Finding(gate, "FAIL", "claimed_precision is not a number")]
    if not (0 <= claimed <= 1):
        return [Finding(
            gate,
            "FAIL",
            f"claimed_precision {_fmt_frac(claimed)} is outside [0, 1]",
        )]
    strata = evaluation.get("review_strata")
    if strata is None:
        return [Finding(gate, "NO-DATA", "review_strata is absent")]
    if not isinstance(strata, list):
        return [Finding(gate, "FAIL", "review_strata is not a list")]
    n_have = 0
    for i, stratum in enumerate(strata):
        if not isinstance(stratum, dict):
            return [Finding(gate, "FAIL", f"review_strata[{i}] is not an object")]
        score_lo = stratum.get("score_lo")
        if score_lo is None:
            return [Finding(gate, "FAIL", f"review_strata[{i}].score_lo is absent")]
        if not _is_number(score_lo):
            return [Finding(gate, "FAIL", f"review_strata[{i}].score_lo is not a number")]
        sampled = stratum.get("sampled")
        if sampled is None or not _is_intlike(sampled) or sampled < 0:
            return [Finding(gate, "FAIL", f"review_strata[{i}].sampled is not a non-negative integer")]
        population = stratum.get("population")
        if population is None or not _is_intlike(population) or population < 0:
            return [Finding(gate, "FAIL", f"review_strata[{i}].population is not a non-negative integer")]
        score_hi = stratum.get("score_hi")
        if score_hi is not None and _is_number(score_hi):
            if score_lo < merge_threshold < score_hi:
                return [Finding(
                    gate,
                    "NO-DATA",
                    f"stratum {i} band [{_fmt_frac(score_lo)}, {_fmt_frac(score_hi)}] straddles merge_threshold {_fmt_frac(merge_threshold)}; strata must align to the threshold before a sample size can be counted",
                )]
        if score_lo >= merge_threshold:
            n_have += int(sampled)
    p = float(claimed)
    if p == 0.0 or p == 1.0:
        p = 0.5
    n_required = math.ceil((1.96 ** 2) * p * (1.0 - p) / (float(margin) ** 2))
    detail = (
        f"review holds {_fmt_count(n_have)} pairs above the threshold, "
        f"the decision's margin {_fmt_frac(margin)} needs {_fmt_count(n_required)} "
        f"(Cochran 1977)"
    )
    if n_have < n_required:
        return [Finding(gate, "FAIL", detail)]
    return [Finding(gate, "PASS", detail)]


def _gate_m18(claim, Finding):
    gate = "M18.representative_gold"
    md = _master_data(claim)
    if md is None:
        return []
    if "evaluation" not in md:
        return [Finding(gate, "NO-DATA", "gold_sample is absent")]
    evaluation = md.get("evaluation")
    if not isinstance(evaluation, dict):
        return [Finding(gate, "FAIL", "evaluation is not an object")]
    gold = evaluation.get("gold_sample")
    if gold is None:
        return [Finding(gate, "NO-DATA", "gold_sample is absent")]
    if not isinstance(gold, dict):
        return [Finding(gate, "FAIL", "gold_sample is not an object")]
    frame = gold.get("frame")
    if frame is None:
        return [Finding(gate, "FAIL", "gold_sample.frame is absent")]
    if not isinstance(frame, str):
        return [Finding(gate, "FAIL", "gold_sample.frame is not a string")]
    claimed = _claimed_precision(claim, md)
    if frame in ("convenience", "benchmark"):
        if claimed is not None:
            return [Finding(
                gate,
                "FAIL",
                f"gold frame {frame} cannot stand for the population (Binette et al. 2022/2023)",
            )]
        return [Finding(
            gate,
            "PASS",
            f"gold frame {frame} but no claimed precision is present (Binette et al. 2022/2023)",
        )]
    if frame == "probability":
        weighted = gold.get("weighted")
        if weighted is not True:
            return [Finding(
                gate,
                "FAIL",
                "probability samples must be weighted back to the population (Binette et al. 2022/2023)",
            )]
        return [Finding(
            gate,
            "PASS",
            "gold frame probability, weighted True (Binette et al. 2022/2023)",
        )]
    if frame == "census":
        return [Finding(gate, "PASS", "gold frame census (Binette et al. 2022/2023)")]
    return [Finding(gate, "FAIL", f"unknown gold_sample.frame {frame!r}")]


def _gate_m19(claim, Finding):
    gate = "M19.labeller_agreement"
    md = _master_data(claim)
    if md is None:
        return []
    if "evaluation" not in md:
        return [Finding(gate, "NO-DATA", "labeller is absent")]
    evaluation = md.get("evaluation")
    if not isinstance(evaluation, dict):
        return [Finding(gate, "FAIL", "evaluation is not an object")]
    labeller = evaluation.get("labeller")
    if labeller is None:
        return [Finding(gate, "NO-DATA", "labeller is absent")]
    if not isinstance(labeller, dict):
        return [Finding(gate, "FAIL", "labeller is not an object")]
    kind = labeller.get("kind")
    if kind is None:
        return [Finding(gate, "FAIL", "labeller.kind is absent")]
    if not isinstance(kind, str):
        return [Finding(gate, "FAIL", "labeller.kind is not a string")]
    if kind == "human":
        return [Finding(gate, "PASS", "labeller kind human (Cohen 1960)")]
    if kind in ("llm", "model"):
        overlap = labeller.get("overlap_n")
        if overlap is None:
            return [Finding(
                gate,
                "FAIL",
                "model labels need a human-labelled overlap_n of at least 50 (Cohen 1960)",
            )]
        if not _is_intlike(overlap):
            return [Finding(gate, "FAIL", "overlap_n is not an integer")]
        overlap_i = int(overlap)
        if overlap_i < 50:
            return [Finding(
                gate,
                "FAIL",
                f"model labels need a human-labelled overlap of at least 50 pairs, got {_fmt_count(overlap_i)} (Cohen 1960)",
            )]
        kappa = labeller.get("agreement_kappa")
        if kappa is None:
            return [Finding(
                gate,
                "FAIL",
                "model labels need agreement_kappa of at least 0.8 (Cohen 1960; Landis and Koch 1977)",
            )]
        if not _is_number(kappa):
            return [Finding(gate, "FAIL", "agreement_kappa is not a number")]
        if kappa < -1.0 or kappa > 1.0:
            return [Finding(gate, "FAIL", f"agreement_kappa {_fmt_frac(kappa)} is outside [-1, 1]")]
        if kappa < 0.8:
            return [Finding(
                gate,
                "FAIL",
                f"agreement_kappa {_fmt_frac(kappa)} is below 0.8; merge decisions need almost perfect agreement (Landis and Koch 1977, Cohen 1960)",
            )]
        return [Finding(
            gate,
            "PASS",
            f"overlap {_fmt_count(overlap_i)} pairs, kappa {_fmt_frac(kappa)} (Cohen 1960; Landis and Koch 1977)",
        )]
    return [Finding(gate, "FAIL", f"unknown labeller.kind {kind!r}")]


def register(packs_module, Finding):
    packs_module.register("MASTER_DATA", _gate_m16)
    packs_module.register("MASTER_DATA", _gate_m17)
    packs_module.register("MASTER_DATA", _gate_m18)
    packs_module.register("MASTER_DATA", _gate_m19)


def selftest(expect):
    ok = True

    class Finding:
        def __init__(self, gate, verdict, detail):
            self.gate = gate
            self.verdict = verdict
            self.detail = detail

    def m16(claim):
        return _gate_m16(claim, Finding)

    def m17(claim):
        return _gate_m17(claim, Finding)

    def m18(claim):
        return _gate_m18(claim, Finding)

    def m19(claim):
        return _gate_m19(claim, Finding)

    def v(res):
        return res[0].verdict if res else None

    def d(res):
        return res[0].detail if res else ""

    ok &= expect(m16({}) == [], "M16 no master_data returns []")
    ok &= expect(v(m16({"master_data": {}})) == "NO-DATA", "M16 missing metric NO-DATA")
    ok &= expect(v(m16({"master_data": {"evaluation": {"headline_metric": "accuracy"}}})) == "FAIL", "M16 accuracy FAIL")
    ok &= expect("non-matches" in d(m16({"master_data": {"evaluation": {"headline_metric": "accuracy"}}})), "M16 accuracy detail names non-matches")
    ok &= expect(v(m16({"master_data": {"false_merge_cost_class": "catastrophic", "evaluation": {"headline_metric": "f1"}}})) == "FAIL", "M16 f1 catastrophic FAIL")
    ok &= expect(v(m16({"master_data": {"false_merge_cost_class": "recoverable", "evaluation": {"headline_metric": "f1"}}})) == "PASS", "M16 f1 recoverable PASS")
    ok &= expect(v(m16({"master_data": {"evaluation": {"headline_metric": "f_beta"}}})) == "FAIL", "M16 f_beta missing beta FAIL")
    ok &= expect(v(m16({"master_data": {"evaluation": {"headline_metric": "f_beta", "beta": 0}}})) == "FAIL", "M16 f_beta beta 0 FAIL")
    ok &= expect(v(m16({"master_data": {"evaluation": {"headline_metric": "f_beta", "beta": "x"}}})) == "FAIL", "M16 f_beta beta string FAIL")
    ok &= expect(v(m16({"master_data": {"false_merge_cost_class": "catastrophic", "evaluation": {"headline_metric": "f_beta", "beta": 1}}})) == "FAIL", "M16 f_beta catastrophic beta 1 FAIL")
    ok &= expect(v(m16({"master_data": {"false_merge_cost_class": "catastrophic", "evaluation": {"headline_metric": "f_beta", "beta": 0.5}}})) == "PASS", "M16 f_beta catastrophic beta 0.5 PASS")
    ok &= expect("0.500" in d(m16({"master_data": {"false_merge_cost_class": "catastrophic", "evaluation": {"headline_metric": "f_beta", "beta": 0.5}}})), "M16 beta formatted 3 decimals")
    ok &= expect(v(m16({"master_data": {"evaluation": {"headline_metric": "precision"}}})) == "PASS", "M16 precision PASS")
    ok &= expect(v(m16({"master_data": {"evaluation": {"headline_metric": "bogus"}}})) == "FAIL", "M16 unknown metric FAIL")
    ok &= expect(v(m16({"master_data": {"evaluation": {"headline_metric": 123}}})) == "FAIL", "M16 non-string metric FAIL")

    ok &= expect(m17({}) == [], "M17 no master_data returns []")
    ok &= expect(v(m17({"master_data": {}})) == "NO-DATA", "M17 missing merge_threshold NO-DATA")
    ok &= expect(v(m17({"master_data": {"merge_threshold": 0.5}})) == "NO-DATA", "M17 missing evaluation NO-DATA")
    ok &= expect(v(m17({"master_data": {"merge_threshold": 0.5, "evaluation": {}}})) == "NO-DATA", "M17 missing margin NO-DATA")
    ok &= expect(v(m17({"master_data": {"merge_threshold": 0.5, "evaluation": {"required_margin": 0.6}}})) == "FAIL", "M17 margin 0.6 FAIL")
    ok &= expect(v(m17({"master_data": {"merge_threshold": 0.5, "evaluation": {"required_margin": 0.01}}})) == "NO-DATA", "M17 missing claimed precision NO-DATA")
    ok &= expect(v(m17({"master_data": {"merge_threshold": 0.5, "evaluation": {"required_margin": 0.01, "claimed_precision": 0.9}}})) == "NO-DATA", "M17 missing strata NO-DATA")
    insufficient = {
        "master_data": {
            "merge_threshold": 0.5,
            "evaluation": {
                "required_margin": 0.01,
                "claimed_precision": 0.9,
                "review_strata": [{"score_lo": 0.6, "sampled": 100, "population": 1000}],
            },
        }
    }
    ok &= expect(v(m17(insufficient)) == "FAIL", "M17 insufficient review FAIL")
    ok &= expect("100" in d(m17(insufficient)) and "3,458" in d(m17(insufficient)), "M17 detail has counts")
    sufficient = {
        "master_data": {
            "merge_threshold": 0.5,
            "evaluation": {
                "required_margin": 0.01,
                "claimed_precision": 0.9,
                "review_strata": [{"score_lo": 0.6, "sampled": 4000, "population": 10000}],
            },
        }
    }
    ok &= expect(v(m17(sufficient)) == "PASS", "M17 sufficient review PASS")
    below = {
        "master_data": {
            "merge_threshold": 0.5,
            "evaluation": {
                "required_margin": 0.01,
                "claimed_precision": 0.9,
                "review_strata": [{"score_lo": 0.4, "sampled": 4000, "population": 10000}],
            },
        }
    }
    ok &= expect(v(m17(below)) == "FAIL", "M17 strata below threshold FAIL")
    ok &= expect("0 pairs" in d(m17(below)), "M17 below threshold detail has 0 pairs")
    fallback = {
        "match": {"precision": 0.9},
        "master_data": {
            "merge_threshold": 0.5,
            "evaluation": {
                "required_margin": 0.01,
                "review_strata": [{"score_lo": 0.6, "sampled": 4000, "population": 10000}],
            },
        },
    }
    ok &= expect(v(m17(fallback)) == "PASS", "M17 match.precision fallback PASS")
    ok &= expect(v(m17({"master_data": {"merge_threshold": 0.5, "evaluation": {"required_margin": 0.5}}})) == "FAIL", "M17 margin 0.5 FAIL")
    zero_p = {
        "master_data": {
            "merge_threshold": 0.5,
            "evaluation": {
                "required_margin": 0.1,
                "claimed_precision": 0,
                "review_strata": [{"score_lo": 0.6, "sampled": 100, "population": 1000}],
            },
        }
    }
    ok &= expect(v(m17(zero_p)) == "PASS", "M17 p=0 uses 0.5 and PASS")
    straddle = {
        "master_data": {
            "merge_threshold": 0.5,
            "evaluation": {
                "required_margin": 0.01,
                "claimed_precision": 0.9,
                "review_strata": [{"score_lo": 0.4, "score_hi": 0.6, "sampled": 100, "population": 1000}],
            },
        }
    }
    ok &= expect(v(m17(straddle)) == "NO-DATA", "M17 straddle NO-DATA")
    ok &= expect("straddle" in d(m17(straddle)), "M17 straddle detail")
    malformed_sampled = {
        "master_data": {
            "merge_threshold": 0.5,
            "evaluation": {
                "required_margin": 0.01,
                "claimed_precision": 0.9,
                "review_strata": [{"score_lo": 0.6, "sampled": -1, "population": 1000}],
            },
        }
    }
    ok &= expect(v(m17(malformed_sampled)) == "FAIL", "M17 malformed sampled FAIL")
    malformed_population = {
        "master_data": {
            "merge_threshold": 0.5,
            "evaluation": {
                "required_margin": 0.01,
                "claimed_precision": 0.9,
                "review_strata": [{"score_lo": 0.6, "sampled": 100, "population": -5}],
            },
        }
    }
    ok &= expect(v(m17(malformed_population)) == "FAIL", "M17 malformed population FAIL")
    missing_population = {
        "master_data": {
            "merge_threshold": 0.5,
            "evaluation": {
                "required_margin": 0.01,
                "claimed_precision": 0.9,
                "review_strata": [{"score_lo": 0.6, "sampled": 100}],
            },
        }
    }
    ok &= expect(v(m17(missing_population)) == "FAIL", "M17 missing population FAIL")

    ok &= expect(m18({}) == [], "M18 no master_data returns []")
    ok &= expect(v(m18({"master_data": {}})) == "NO-DATA", "M18 missing gold_sample NO-DATA")
    conv = {
        "master_data": {
            "evaluation": {
                "claimed_precision": 0.9,
                "gold_sample": {"frame": "convenience", "weighted": False},
            }
        }
    }
    ok &= expect(v(m18(conv)) == "FAIL", "M18 convenience with precision FAIL")
    ok &= expect("Binette" in d(m18(conv)), "M18 convenience detail cites Binette")
    bench = {
        "master_data": {
            "evaluation": {
                "claimed_precision": 0.9,
                "gold_sample": {"frame": "benchmark", "weighted": False},
            }
        }
    }
    ok &= expect(v(m18(bench)) == "FAIL", "M18 benchmark with precision FAIL")
    prob_false = {
        "master_data": {
            "evaluation": {
                "gold_sample": {"frame": "probability", "weighted": False},
            }
        }
    }
    ok &= expect(v(m18(prob_false)) == "FAIL", "M18 probability unweighted FAIL")
    prob_true = {
        "master_data": {
            "evaluation": {
                "gold_sample": {"frame": "probability", "weighted": True},
            }
        }
    }
    ok &= expect(v(m18(prob_true)) == "PASS", "M18 probability weighted PASS")
    census = {
        "master_data": {
            "evaluation": {
                "gold_sample": {"frame": "census", "weighted": False},
            }
        }
    }
    ok &= expect(v(m18(census)) == "PASS", "M18 census PASS")
    unknown_frame = {
        "master_data": {
            "evaluation": {
                "gold_sample": {"frame": "mystery", "weighted": True},
            }
        }
    }
    ok &= expect(v(m18(unknown_frame)) == "FAIL", "M18 unknown frame FAIL")

    ok &= expect(m19({}) == [], "M19 no master_data returns []")
    ok &= expect(v(m19({"master_data": {}})) == "NO-DATA", "M19 missing labeller NO-DATA")
    human = {"master_data": {"evaluation": {"labeller": {"kind": "human"}}}}
    ok &= expect(v(m19(human)) == "PASS", "M19 human PASS")
    llm_missing = {"master_data": {"evaluation": {"labeller": {"kind": "llm"}}}}
    ok &= expect(v(m19(llm_missing)) == "FAIL", "M19 llm missing overlap FAIL")
    llm_low = {"master_data": {"evaluation": {"labeller": {"kind": "llm", "overlap_n": 49, "agreement_kappa": 0.9}}}}
    ok &= expect(v(m19(llm_low)) == "FAIL", "M19 llm overlap below 50 FAIL")
    llm_no_kappa = {"master_data": {"evaluation": {"labeller": {"kind": "llm", "overlap_n": 50}}}}
    ok &= expect(v(m19(llm_no_kappa)) == "FAIL", "M19 llm missing kappa FAIL")
    llm_low_kappa = {"master_data": {"evaluation": {"labeller": {"kind": "llm", "overlap_n": 50, "agreement_kappa": 0.79}}}}
    ok &= expect(v(m19(llm_low_kappa)) == "FAIL", "M19 llm low kappa FAIL")
    llm_ok = {"master_data": {"evaluation": {"labeller": {"kind": "llm", "overlap_n": 50, "agreement_kappa": 0.8}}}}
    ok &= expect(v(m19(llm_ok)) == "PASS", "M19 llm acceptable PASS")
    llm_kappa_high = {"master_data": {"evaluation": {"labeller": {"kind": "llm", "overlap_n": 50, "agreement_kappa": 1.5}}}}
    ok &= expect(v(m19(llm_kappa_high)) == "FAIL", "M19 kappa > 1 FAIL")
    ok &= expect("kappa" in d(m19(llm_kappa_high)) and "outside" in d(m19(llm_kappa_high)), "M19 kappa outside detail")
    llm_kappa_low = {"master_data": {"evaluation": {"labeller": {"kind": "llm", "overlap_n": 50, "agreement_kappa": -1.5}}}}
    ok &= expect(v(m19(llm_kappa_low)) == "FAIL", "M19 kappa < -1 FAIL")
    ok &= expect("kappa" in d(m19(llm_kappa_low)) and "outside" in d(m19(llm_kappa_low)), "M19 kappa outside detail low")
    model_ok = {"master_data": {"evaluation": {"labeller": {"kind": "model", "overlap_n": 1000, "agreement_kappa": 0.9}}}}
    ok &= expect(v(m19(model_ok)) == "PASS", "M19 model acceptable PASS")
    ok &= expect("1,000" in d(m19(model_ok)), "M19 count formatted with thousands separator")
    unknown_kind = {"master_data": {"evaluation": {"labeller": {"kind": "robot"}}}}
    ok &= expect(v(m19(unknown_kind)) == "FAIL", "M19 unknown kind FAIL")

    calls = []

    class Packs:
        def register(self, pack, fn):
            calls.append((pack, fn.__name__))

    register(Packs(), Finding)
    ok &= expect(len(calls) == 4, "register calls four gates")
    ok &= expect(calls[0][0] == "MASTER_DATA", "register uses MASTER_DATA pack")
    ok &= expect(calls[0][1] == "_gate_m16", "register first M16")
    ok &= expect(calls[1][1] == "_gate_m17", "register second M17")
    ok &= expect(calls[2][1] == "_gate_m18", "register third M18")
    ok &= expect(calls[3][1] == "_gate_m19", "register fourth M19")

    return ok


if __name__ == "__main__":
    def expect(cond, msg):
        if not cond:
            print("SELFTEST FAIL: " + msg)
        return bool(cond)

    ok = selftest(expect)
    if ok:
        print("SELFTEST PASS")
        sys.exit(0)
    sys.exit(1)
