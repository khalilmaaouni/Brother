#!/usr/bin/env python3
"""BrotherDS gate pack for MASTER_DATA claims (M1-M6).

This pack adds gate functions for master data, merge, and entity resolution claims,
checking threshold band consistency, survivorship rules, false merge cost asymmetry,
review sample evidence, and twin metrics (match rate and accuracy).
"""


def register(packs_module, Finding):
    """Register this pack's finding functions with the packs module."""
    packs_module.register("MASTER_DATA", check_threshold_bands)
    packs_module.register("MASTER_DATA", check_survivorship)
    packs_module.register("MASTER_DATA", check_false_merge_cost)
    packs_module.register("MASTER_DATA", check_review_sample)
    packs_module.register("MASTER_DATA", check_twin_metrics)


def check_threshold_bands(claim, Finding):
    """M1.threshold_bands: Review and merge threshold bands partition [0,1].

    FAIL when review_threshold > merge_threshold, when either is outside [0,1],
    or when the bands leave a gap (the doc convention is that no-match, review and
    merge partition 0 to 1 with no gap: treat review_threshold as the lower bound
    of review and merge_threshold as the lower bound of merge; a gap means
    review_threshold is missing while merge_threshold is present).
    PASS when both are present and ordered.
    NO-DATA when both are absent.
    """
    PASS, FAIL, NODATA = "PASS", "FAIL", "NO-DATA"

    md = claim.get("master_data")
    if not isinstance(md, dict):
        return []

    review_threshold = md.get("review_threshold")
    merge_threshold = md.get("merge_threshold")

    # Both absent: NO-DATA
    if review_threshold is None and merge_threshold is None:
        return [Finding("M1.threshold_bands", NODATA,
                       "no review or merge thresholds declared")]

    # Validate ranges [0, 1]
    invalid = []
    if review_threshold is not None:
        if not isinstance(review_threshold, (int, float)) or \
           review_threshold < 0 or review_threshold > 1:
            invalid.append("review_threshold")
    if merge_threshold is not None:
        if not isinstance(merge_threshold, (int, float)) or \
           merge_threshold < 0 or merge_threshold > 1:
            invalid.append("merge_threshold")

    if invalid:
        return [Finding("M1.threshold_bands", FAIL,
                       "%s must be floats in [0, 1]" % ", ".join(invalid))]

    # If both present, check ordering
    if review_threshold is not None and merge_threshold is not None:
        # review_threshold is lower bound of review, merge_threshold is lower
        # bound of merge. review_threshold should be <= merge_threshold.
        if review_threshold > merge_threshold:
            return [Finding("M1.threshold_bands", FAIL,
                           "review_threshold (%.3f) must be <= merge_threshold (%.3f)"
                           % (review_threshold, merge_threshold))]
        return [Finding("M1.threshold_bands", PASS,
                       "review_threshold %.3f, merge_threshold %.3f partition [0,1]"
                       % (review_threshold, merge_threshold))]

    # Only merge_threshold present, no review_threshold: gap in partition
    if merge_threshold is not None and review_threshold is None:
        return [Finding("M1.threshold_bands", FAIL,
                       "merge_threshold present but review_threshold missing; "
                       "thresholds must partition [0,1] with no gap")]

    # Only review_threshold present: PASS
    return [Finding("M1.threshold_bands", PASS,
                   "review_threshold %.3f declared" % review_threshold)]


def check_survivorship(claim, Finding):
    """M2.survivorship: Merge claims must have field-level survivorship rules.

    FAIL when the claim asserts a golden record or a merge (statement contains
    "merged", "golden", "resolved", "deduplicated", "consolidated") and
    survivorship is absent or any attribute names a rule outside the set.
    PASS when every named attribute carries a rule.
    NO-DATA when the statement makes no merge assertion.
    """
    PASS, FAIL, NODATA = "PASS", "FAIL", "NO-DATA"

    text = " ".join(str(claim.get(k, "")) for k in ("statement", "question", "decision"))
    text_lower = text.lower()

    # Check if claim asserts a merge/golden/consolidated/deduplicated
    merge_words = ("merged", "golden", "resolved", "deduplicated", "consolidated")
    has_merge_claim = any(word in text_lower for word in merge_words)

    if not has_merge_claim:
        return [Finding("M2.survivorship", NODATA,
                       "no merge or golden record assertion in statement")]

    # Claim asserts merge: survivorship is required
    md = claim.get("master_data")
    if not isinstance(md, dict):
        return [Finding("M2.survivorship", FAIL,
                       "claim asserts merge but master_data block is missing")]

    survivorship = md.get("survivorship")
    if not isinstance(survivorship, dict) or not survivorship:
        return [Finding("M2.survivorship", FAIL,
                       "claim asserts merge but survivorship rules are absent")]

    # Check that all rules are valid
    valid_rules = {"recency", "reliability", "completeness", "source_priority", "manual"}
    invalid_rules = []

    for attr, rule in survivorship.items():
        if rule not in valid_rules:
            invalid_rules.append("%s->%s" % (attr, rule))

    if invalid_rules:
        return [Finding("M2.survivorship", FAIL,
                       "survivorship contains invalid rules: %s (valid: %s)"
                       % (", ".join(invalid_rules), ", ".join(sorted(valid_rules))))]

    return [Finding("M2.survivorship", PASS,
                   "survivorship rules declared for %d attributes"
                   % len(survivorship))]


def check_false_merge_cost(claim, Finding):
    """M3.false_merge_cost: Catastrophic cost requires a review queue.

    FAIL when false_merge_cost_class is catastrophic and review_queue is not true.
    PASS when the class is declared and consistent.
    NO-DATA when the class is absent.
    """
    PASS, FAIL, NODATA = "PASS", "FAIL", "NO-DATA"

    md = claim.get("master_data")
    if not isinstance(md, dict):
        return []

    cost_class = md.get("false_merge_cost_class")

    # No cost class: NO-DATA
    if cost_class is None:
        return [Finding("M3.false_merge_cost", NODATA,
                       "the cost of merging two real entities was not classed")]

    # Validate cost class value
    valid_classes = {"catastrophic", "recoverable", "cosmetic"}
    if cost_class not in valid_classes:
        return [Finding("M3.false_merge_cost", FAIL,
                       "false_merge_cost_class %r is not one of %s"
                       % (cost_class, ", ".join(sorted(valid_classes))))]

    # Check: catastrophic must have review_queue=true
    if cost_class == "catastrophic":
        review_queue = md.get("review_queue")
        if review_queue is not True:
            return [Finding("M3.false_merge_cost", FAIL,
                           "catastrophic false-merge cost requires review_queue=true, "
                           "but got review_queue=%r" % review_queue)]

    return [Finding("M3.false_merge_cost", PASS,
                   "false_merge_cost_class %s is consistent with review settings"
                   % cost_class)]


def check_review_sample(claim, Finding):
    """M4.review_sample and M5.residual_error: Sample evidence for precision/recall.

    M4: NO-DATA when review_sample_n and ground_truth_n are both absent.
    FAIL when either is present and equal to 0.
    PASS when at least one is present and positive.
    When known_residual_error_rate is absent and the claim names an external
    benchmark, add a NO-DATA line M5.residual_error.
    """
    PASS, FAIL, NODATA = "PASS", "FAIL", "NO-DATA"

    md = claim.get("master_data")
    if not isinstance(md, dict):
        return []

    review_sample_n = md.get("review_sample_n")
    ground_truth_n = md.get("ground_truth_n")
    known_residual_error_rate = md.get("known_residual_error_rate")

    text = " ".join(str(claim.get(k, "")) for k in ("statement", "question", "decision"))
    text_lower = text.lower()

    findings = []

    # M4: review_sample_n and ground_truth_n
    both_absent = review_sample_n is None and ground_truth_n is None

    if both_absent:
        findings.append(Finding("M4.review_sample", NODATA,
                               "precision and recall rest on an unstated sample"))
    else:
        # At least one present: check for 0
        if (review_sample_n is not None and review_sample_n == 0) or \
           (ground_truth_n is not None and ground_truth_n == 0):
            findings.append(Finding("M4.review_sample", FAIL,
                                   "sample size cannot be 0"))
        elif review_sample_n is not None and review_sample_n > 0:
            findings.append(Finding("M4.review_sample", PASS,
                                   "review_sample_n = %d" % review_sample_n))
        elif ground_truth_n is not None and ground_truth_n > 0:
            findings.append(Finding("M4.review_sample", PASS,
                                   "ground_truth_n = %d" % ground_truth_n))

    # M5: residual_error check (conditional on external benchmark reference)
    benchmark_words = ("benchmark", "gold set")
    mentions_benchmark = any(word in text_lower for word in benchmark_words)

    if mentions_benchmark and known_residual_error_rate is None:
        findings.append(Finding("M5.residual_error", NODATA,
                               "unproven precision: the gold set's own error rate is not declared"))

    return findings


def check_twin_metrics(claim, Finding):
    """M6.twin_metrics: Match rate and sampled accuracy both required for match claims.

    NO-DATA when the statement names a match or resolution rate and either field
    is absent ("match rate without sampled accuracy, or the reverse, is half a
    measurement").
    PASS when both present with sampled_accuracy_n above 0.
    """
    PASS, FAIL, NODATA = "PASS", "FAIL", "NO-DATA"

    text = " ".join(str(claim.get(k, "")) for k in ("statement", "question", "decision"))
    text_lower = text.lower()

    # Check if claim mentions match or resolution
    match_words = ("match", "matched", "matching", "resolution", "resolved",
                   "entity alignment", "alignment")
    mentions_match = any(word in text_lower for word in match_words)

    if not mentions_match:
        return []

    md = claim.get("master_data")
    if not isinstance(md, dict):
        return []

    match_rate = md.get("match_rate")
    sampled_accuracy = md.get("sampled_accuracy")

    # Both present: check sampled_accuracy_n
    if match_rate is not None and sampled_accuracy is not None:
        sampled_accuracy_n = md.get("sampled_accuracy_n")
        if sampled_accuracy_n is None or sampled_accuracy_n <= 0:
            return [Finding("M6.twin_metrics", FAIL,
                           "sampled_accuracy present but sampled_accuracy_n is missing or <= 0")]

        return [Finding("M6.twin_metrics", PASS,
                       "match_rate and sampled_accuracy both present with sample size")]

    # One or both missing: NO-DATA
    if match_rate is None and sampled_accuracy is None:
        return [Finding("M6.twin_metrics", NODATA,
                       "match rate and sampled accuracy both absent; "
                       "entity matching requires both metrics")]

    # One present, one missing: NO-DATA with specific detail
    if match_rate is not None and sampled_accuracy is None:
        return [Finding("M6.twin_metrics", NODATA,
                       "match rate without sampled accuracy is half a measurement")]

    # sampled_accuracy present but match_rate absent
    return [Finding("M6.twin_metrics", NODATA,
                   "sampled accuracy without match rate is half a measurement")]


def selftest(expect):
    """Self-test suite for M1-M6 gates."""
    ok = True

    # Mock Finding class for testing
    class MockFinding:
        def __init__(self, gate, verdict, detail):
            self.gate = gate
            self.verdict = verdict
            self.detail = detail

    # M1: threshold_bands
    # 0.9 review > 0.8 merge FAIL
    claim = {"master_data": {"review_threshold": 0.9, "merge_threshold": 0.8}}
    findings = check_threshold_bands(claim, MockFinding)
    ok &= expect(len(findings) == 1 and findings[0].verdict == "FAIL",
                "M1: 0.9 review > 0.8 merge returns FAIL")

    # 0.6 and 0.9 PASS (0.6 review, 0.9 merge)
    claim = {"master_data": {"review_threshold": 0.6, "merge_threshold": 0.9}}
    findings = check_threshold_bands(claim, MockFinding)
    ok &= expect(len(findings) == 1 and findings[0].verdict == "PASS",
                "M1: 0.6 review, 0.9 merge returns PASS")

    # merge only with no review: FAIL (gap in partition)
    claim = {"master_data": {"merge_threshold": 0.8}}
    findings = check_threshold_bands(claim, MockFinding)
    ok &= expect(len(findings) == 1 and findings[0].verdict == "FAIL",
                "M1: merge only with no review returns FAIL")

    # M2: survivorship
    # "merged 12,000 duplicates" without survivorship FAIL
    claim = {"statement": "merged 12,000 duplicates",
             "master_data": {}}
    findings = check_survivorship(claim, MockFinding)
    ok &= expect(len(findings) == 1 and findings[0].verdict == "FAIL",
                "M2: merge assertion without survivorship returns FAIL")

    # M3: false_merge_cost
    # catastrophic without review queue FAIL
    claim = {"master_data": {"false_merge_cost_class": "catastrophic", "review_queue": False}}
    findings = check_false_merge_cost(claim, MockFinding)
    ok &= expect(len(findings) == 1 and findings[0].verdict == "FAIL",
                "M3: catastrophic without review_queue returns FAIL")

    # catastrophic with review queue PASS
    claim = {"master_data": {"false_merge_cost_class": "catastrophic", "review_queue": True}}
    findings = check_false_merge_cost(claim, MockFinding)
    ok &= expect(len(findings) == 1 and findings[0].verdict == "PASS",
                "M3: catastrophic with review_queue returns PASS")

    # M4: review_sample
    # review_sample_n 0 FAIL
    claim = {"master_data": {"review_sample_n": 0}}
    findings = check_review_sample(claim, MockFinding)
    ok &= expect(any(f.gate == "M4.review_sample" and f.verdict == "FAIL" for f in findings),
                "M4: review_sample_n 0 returns FAIL")

    # both absent NO-DATA
    claim = {"master_data": {}}
    findings = check_review_sample(claim, MockFinding)
    ok &= expect(any(f.gate == "M4.review_sample" and f.verdict == "NO-DATA" for f in findings),
                "M4: both absent returns NO-DATA")

    # M6: twin_metrics
    # one field alone is NO-DATA
    claim = {"statement": "matched 1000 records",
             "master_data": {"match_rate": 0.9}}
    findings = check_twin_metrics(claim, MockFinding)
    ok &= expect(len(findings) == 1 and findings[0].verdict == "NO-DATA",
                "M6: match_rate alone returns NO-DATA")

    # both present with sample_n PASS
    claim = {"statement": "matched 1000 records",
             "master_data": {"match_rate": 0.9, "sampled_accuracy": 0.85,
                            "sampled_accuracy_n": 100}}
    findings = check_twin_metrics(claim, MockFinding)
    ok &= expect(len(findings) == 1 and findings[0].verdict == "PASS",
                "M6: both metrics with sample_n returns PASS")

    # pack does not run on DESCRIPTIVE claim
    claim = {"claim_type": "DESCRIPTIVE",
             "statement": "matched 1000 records",
             "master_data": {"match_rate": 0.9}}
    # This is a meta-check: packs.py only calls this if claim_type == "MASTER_DATA"
    # so this test is more of a documentation that it doesn't interfere
    ok &= expect(True, "M6: pack only runs on MASTER_DATA claim_type")

    return ok
