"""Master data audit gate pack for M23 through M26: the gates apply only when claim["master_data"]["audit"] is a dict, and fn returns [] when master_data is not a dict, audit is missing, or audit is not a dict."""
import os
import sys
import math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mdm_eval
import mdm_audit

GATE_M23 = "M23.report_reconciliation"
GATE_M24 = "M24.assignment_uniqueness"
GATE_M25 = "M25.pathway_precision"
GATE_M26 = "M26.verifier_evidence"


def _is_nonneg_int(x):
    return isinstance(x, int) and not isinstance(x, bool) and x >= 0


def _is_nonneg_int_dict(d):
    if not isinstance(d, dict):
        return False
    for v in d.values():
        if not _is_nonneg_int(v):
            return False
    return True


def _rate(x):
    return (isinstance(x, (int, float)) and not isinstance(x, bool)
            and math.isfinite(float(x)) and 0.0 <= float(x) <= 1.0)


def _gate_m23(A, E, Finding):
    if "reported" not in A or A.get("reported") is None:
        return Finding(
            GATE_M23,
            "NO-DATA",
            "no reported figures: a match report must state its counts to be checked",
        )
    reported = A.get("reported")
    if not isinstance(reported, dict):
        return Finding(GATE_M23, "FAIL", "reported")

    problems = []
    checks = 0

    n_input = reported.get("n_input")
    if not _is_nonneg_int(n_input):
        problems.append("n_input")
    else:
        checks += 1

    status_counts = reported.get("status_counts")
    if not _is_nonneg_int_dict(status_counts):
        problems.append("status_counts")
    else:
        checks += 1
        if _is_nonneg_int(n_input):
            total_status = sum(status_counts.values())
            if total_status != n_input:
                problems.append(
                    "status counts sum to %s, not n_input %s" % (total_status, n_input)
                )
            else:
                checks += 1

    pathway_counts = reported.get("pathway_counts")
    if pathway_counts is not None:
        if not _is_nonneg_int_dict(pathway_counts):
            problems.append("pathway_counts")
        else:
            checks += 1
            if _is_nonneg_int_dict(status_counts):
                match_count = status_counts.get("MATCH", 0)
                total_pathways = sum(pathway_counts.values())
                if total_pathways != match_count:
                    problems.append(
                        "pathway counts sum to %s, not the MATCH count %s"
                        % (total_pathways, match_count)
                    )
                else:
                    checks += 1

    percents = reported.get("percents")
    if percents is not None:
        if not isinstance(percents, dict):
            problems.append("percents")
        else:
            for name, entry in percents.items():
                if not isinstance(entry, dict) or not all(
                    k in entry for k in ("value", "numerator", "denominator")
                ):
                    problems.append("percent %s" % name)
                    continue
                value = entry.get("value")
                numerator = entry.get("numerator")
                denominator = entry.get("denominator")
                if (not _rate(value / 100.0) or
                        not _is_nonneg_int(numerator) or
                        not _is_nonneg_int(denominator)):
                    problems.append("percent %s" % name)
                    continue
                if denominator == 0:
                    problems.append("percent %s" % name)
                    continue
                checks += 1
                expected = 100.0 * numerator / denominator
                if abs(value - expected) > 0.05:
                    problems.append(
                        "percent %s says %s but %s/%s is %.2f"
                        % (name, value, numerator, denominator, expected)
                    )
                else:
                    checks += 1

    computed = A.get("computed")
    if computed is not None:
        if not isinstance(computed, dict):
            problems.append("computed")
        else:
            if "n_rows" in computed and _is_nonneg_int(n_input):
                if computed.get("n_rows") != n_input:
                    problems.append(
                        "the results table has %s rows, the report says %s"
                        % (computed.get("n_rows"), n_input)
                    )
                else:
                    checks += 1

            reconciliation = computed.get("reconciliation")
            if not isinstance(reconciliation, dict):
                reconciliation = {}

            computed_status = reconciliation.get("status_counts")
            if isinstance(computed_status, dict) and isinstance(status_counts, dict):
                keys = set(computed_status.keys()) | set(status_counts.keys())
                for key in sorted(keys):
                    left = computed_status.get(key, 0)
                    right = status_counts.get(key, 0)
                    if left != right:
                        problems.append(
                            "results table has %s %s, the report says %s"
                            % (left, key, right)
                        )
                    else:
                        checks += 1

            computed_pathways = reconciliation.get("pathway_counts")
            if isinstance(computed_pathways, dict) and isinstance(pathway_counts, dict):
                keys = set(computed_pathways.keys()) | set(pathway_counts.keys())
                for key in sorted(keys):
                    left = computed_pathways.get(key, 0)
                    right = pathway_counts.get(key, 0)
                    if left != right:
                        problems.append(
                            "results table has %s %s, the report says %s"
                            % (left, key, right)
                        )
                    else:
                        checks += 1

            if reconciliation.get("consistent") is False:
                inner = reconciliation.get("problems") or []
                if not isinstance(inner, list):
                    inner = []
                first_three = [str(x) for x in inner[:3]]
                problems.append(
                    "the results table itself is inconsistent: "
                    + "; ".join(first_three)
                )
            elif "consistent" in reconciliation:
                checks += 1

    if problems:
        return Finding(
            GATE_M23,
            "FAIL",
            "%d problem(s): %s" % (len(problems), "; ".join(problems)),
        )
    return Finding(
        GATE_M23,
        "PASS",
        "every count sums and every percent recomputes (%d check(s))" % checks,
    )


def _gate_m24(A, E, Finding):
    computed = A.get("computed")
    if computed is None:
        return Finding(
            GATE_M24,
            "NO-DATA",
            "no computed audit: run mdm-audit on the results table",
        )
    if not isinstance(computed, dict):
        return Finding(GATE_M24, "FAIL", "computed")

    problems = []
    one_to_one = A.get("one_to_one", True)

    collisions = computed.get("collisions")
    if collisions is None:
        return Finding(
            GATE_M24,
            "NO-DATA",
            "no collision counts in the computed audit",
        )
    if not isinstance(collisions, dict):
        problems.append("collisions")
    else:
        multiple = collisions.get("reference_ids_with_multiple_sources", 0)
        if not _is_nonneg_int(multiple):
            problems.append("collisions.reference_ids_with_multiple_sources")
            multiple = 0
        if one_to_one and multiple > 0 and not A.get("collisions_reviewed"):
            rows = collisions.get("rows_in_collisions", 0)
            problems.append(
                "%s reference record(s) received more than one source record (%s rows); a location-anchored master allows one; review them or declare one_to_one false"
                % (multiple, rows)
            )

    key_hubs = computed.get("key_hubs")
    if key_hubs is None:
        key_hubs = {}
    if not isinstance(key_hubs, dict):
        problems.append("key_hubs")
    else:
        matched = key_hubs.get("matched_rows_on_hubs", 0)
        if not _is_nonneg_int(matched):
            problems.append("key_hubs.matched_rows_on_hubs")
            matched = 0
        if matched > 0 and not A.get("hubs_reviewed"):
            hub_min = key_hubs.get("hub_min", 0)
            hubs = key_hubs.get("hubs", 0)
            problems.append(
                "%s matched row(s) rest on a shared key used by at least %s source records (%s hub(s)); a shared key is not proof of identity; review them"
                % (matched, hub_min, hubs)
            )

    if problems:
        return Finding(GATE_M24, "FAIL", "; ".join(problems))

    if one_to_one:
        base = "one reference record per source"
    else:
        base = "one_to_one declared false"
    if isinstance(key_hubs, dict) and key_hubs.get("state") == "NO-DATA":
        base = base + "; shared-key check NO-DATA: no key column"
    return Finding(GATE_M24, "PASS", base)


def _gate_m25(A, E, Finding):
    if A.get("precision_basis") == "similarity":
        return Finding(
            GATE_M25,
            "FAIL",
            "a similarity score distribution is not a precision: label a sample of each pathway",
        )

    counts = None
    computed = A.get("computed")
    if isinstance(computed, dict):
        reconciliation = computed.get("reconciliation")
        if isinstance(reconciliation, dict):
            candidate = reconciliation.get("pathway_counts")
            if candidate is not None:
                if not _is_nonneg_int_dict(candidate):
                    return Finding(GATE_M25, "FAIL", "pathway_counts")
                counts = candidate

    if counts is None:
        reported = A.get("reported")
        if isinstance(reported, dict):
            candidate = reported.get("pathway_counts")
            if candidate is not None:
                if not _is_nonneg_int_dict(candidate):
                    return Finding(GATE_M25, "FAIL", "pathway_counts")
                counts = candidate

    if counts is None:
        return Finding(GATE_M25, "NO-DATA", "no pathway counts")

    total = sum(counts.values())
    if total == 0:
        return Finding(GATE_M25, "NO-DATA", "no pathway counts")

    review = A.get("pathway_review")
    if review is None:
        review = {}
    if not isinstance(review, dict):
        return Finding(GATE_M25, "FAIL", "pathway_review")

    for pathway, entry in review.items():
        if pathway not in counts:
            return Finding(GATE_M25, "FAIL", "pathway %s is absent from audited pathway_counts" % pathway)
        if not isinstance(entry, dict):
            return Finding(GATE_M25, "FAIL", "pathway %s entry" % pathway)
        for field in ("population", "sampled", "positive"):
            if field not in entry:
                return Finding(GATE_M25, "FAIL", "pathway %s %s" % (pathway, field))
            if not _is_nonneg_int(entry[field]):
                return Finding(GATE_M25, "FAIL", "pathway %s %s" % (pathway, field))
        if entry["positive"] > entry["sampled"]:
            return Finding(GATE_M25, "FAIL", "pathway %s positive" % pathway)
        if entry["sampled"] > entry["population"]:
            return Finding(GATE_M25, "FAIL", "pathway %s sampled" % pathway)

        strata = entry.get("strata")
        if strata is not None:
            if not isinstance(strata, list):
                return Finding(GATE_M25, "FAIL", "pathway %s strata" % pathway)
            for stratum in strata:
                if not isinstance(stratum, dict):
                    return Finding(GATE_M25, "FAIL", "pathway %s strata" % pathway)
                if "stratum" not in stratum:
                    return Finding(
                        GATE_M25, "FAIL", "pathway %s stratum" % pathway
                    )
                for field in ("population", "sampled", "positive"):
                    if field not in stratum:
                        return Finding(
                            GATE_M25,
                            "FAIL",
                            "pathway %s stratum %s" % (pathway, field),
                        )
                    if not _is_nonneg_int(stratum[field]):
                        return Finding(
                            GATE_M25,
                            "FAIL",
                            "pathway %s stratum %s" % (pathway, field),
                        )
                if stratum["positive"] > stratum["sampled"]:
                    return Finding(
                        GATE_M25, "FAIL", "pathway %s stratum positive" % pathway
                    )
                if stratum["sampled"] > stratum["population"]:
                    return Finding(
                        GATE_M25, "FAIL", "pathway %s stratum population" % pathway
                    )

    incomplete = []
    for pathway, entry in review.items():
        try:
            coverage = mdm_audit.review_coverage(entry, ("positive",), counts.get(pathway))
        except ValueError as exc:
            return Finding(GATE_M25, "FAIL", "pathway %s: %s" % (pathway, exc))
        if coverage and coverage["coverage_state"] == "NO-DATA":
            incomplete.append(pathway)
    if incomplete:
        return Finding(GATE_M25, "NO-DATA", "unlabelled stratum population in pathway(s): "
                       + ", ".join(sorted(incomplete)))

    overall_strata = []
    for pathway, entry in review.items():
        if pathway not in counts:
            continue
        strata = entry.get("strata")
        if isinstance(strata, list) and len(strata) > 0:
            for stratum in strata:
                if stratum.get("sampled", 0) >= 1:
                    overall_strata.append(
                        {
                            "population": stratum["population"],
                            "sampled": stratum["sampled"],
                            "positive": stratum["positive"],
                        }
                    )
        elif entry.get("sampled", 0) >= 1:
            overall_strata.append(
                {
                    "population": counts[pathway],
                    "sampled": entry["sampled"],
                    "positive": entry["positive"],
                }
            )

    overall_claimed = E.get("claimed_precision")
    overall_hi = None
    if overall_claimed is not None:
        if not _rate(overall_claimed):
            return Finding(GATE_M25, "FAIL", "evaluation.claimed_precision")
        if not overall_strata:
            return Finding(
                GATE_M25,
                "FAIL",
                "overall claimed precision has no labelled sample behind it",
            )
        estimate = mdm_eval.stratified_estimate(overall_strata)
        overall_hi = estimate["hi"]

    missing = []
    for pathway, count in counts.items():
        entry = review.get(pathway)
        if not isinstance(entry, dict):
            missing.append(pathway)
            continue
        has_sample = entry.get("sampled", 0) >= 1
        strata = entry.get("strata")
        if isinstance(strata, list):
            for stratum in strata:
                if stratum.get("sampled", 0) >= 1:
                    has_sample = True
        if not has_sample:
            missing.append(pathway)

    if missing:
        share = sum(counts[p] for p in missing) / float(total)
        if share >= 0.05:
            names = ", ".join(sorted(missing))
            return Finding(
                GATE_M25,
                "FAIL",
                "pathway(s) carrying %.3f of matches have no labelled sample: %s"
                % (share, names),
            )

    problems = []

    claimed = A.get("claimed_pathway_precision")
    if claimed is None:
        claimed = {}
    if not isinstance(claimed, dict):
        return Finding(GATE_M25, "FAIL", "claimed_pathway_precision")

    intervals = {}
    for pathway, entry in review.items():
        strata = entry.get("strata")
        if isinstance(strata, list) and len(strata) > 0:
            valid_strata = [
                stratum for stratum in strata if stratum.get("sampled", 0) >= 1
            ]
            if valid_strata:
                estimate = mdm_eval.stratified_estimate(valid_strata)
                intervals[pathway] = (estimate["lo"], estimate["hi"])
        elif entry.get("sampled", 0) >= 1:
            lo, hi = mdm_eval.wilson_interval(entry["positive"], entry["sampled"])
            intervals[pathway] = (lo, hi)

    for pathway, value in claimed.items():
        if not _rate(value):
            return Finding(
                GATE_M25, "FAIL", "claimed_pathway_precision %s" % pathway
            )
        if pathway not in intervals:
            problems.append(
                "claimed precision for %s has no labelled sample" % pathway
            )
        else:
            lo, hi = intervals[pathway]
            if value > hi + 1e-12:
                problems.append(
                    "claimed precision for %s is %.3f but its sample supports at most %.3f"
                    % (pathway, value, hi)
                )

    if overall_claimed is not None and overall_hi is not None:
        if overall_claimed > overall_hi + 1e-12:
            problems.append(
                "overall claimed precision %.3f exceeds %.3f, the most the pathway samples support"
                % (overall_claimed, overall_hi)
            )

    if problems:
        return Finding(GATE_M25, "FAIL", "; ".join(problems))

    parts = []
    for pathway in sorted(intervals.keys()):
        entry = review[pathway]
        lo, hi = intervals[pathway]
        parts.append(
            "%s %s/%s [%.3f, %.3f]"
            % (pathway, entry["positive"], entry["sampled"], lo, hi)
        )
    return Finding(GATE_M25, "PASS", "; ".join(parts))


def _gate_m26(A, E, Finding):
    verifier = A.get("verifier")
    if verifier is None:
        return Finding(GATE_M26, "NO-DATA", "no verifier declared")
    if not isinstance(verifier, dict):
        return Finding(GATE_M26, "FAIL", "verifier")

    kind = verifier.get("kind")
    if kind not in ("llm", "model", "human", "rule"):
        return Finding(GATE_M26, "FAIL", "unknown verifier kind %s" % kind)

    def valid_review(name):
        obj = verifier.get(name)
        if obj is None:
            return None, None
        if not isinstance(obj, dict):
            return None, name
        for field in ("population", "sampled", "positive"):
            if field not in obj:
                return None, name
            if not _is_nonneg_int(obj[field]):
                return None, name
        if obj["positive"] > obj["sampled"]:
            return None, name
        if obj["sampled"] > obj["population"]:
            return None, "%s population" % name
        return obj, None

    confirmed, error = valid_review("confirmed_review")
    if error is not None:
        return Finding(GATE_M26, "FAIL", error)

    rejected, error = valid_review("rejected_review")
    if error is not None:
        return Finding(GATE_M26, "FAIL", error)

    claimed = verifier.get("claimed_precision")
    if claimed is not None:
        if not _rate(claimed):
            return Finding(GATE_M26, "FAIL", "claimed_precision")

    if (
        kind in ("human", "rule")
        and confirmed is None
        and rejected is None
        and claimed is None
    ):
        return Finding(
            GATE_M26, "NO-DATA", "no labelled sample of the verifier's decisions"
        )

    problems = []

    if kind in ("llm", "model"):
        if confirmed is None or confirmed["sampled"] < 30:
            problems.append(
                "an llm or model verifier needs at least 30 human-labelled confirmations to state its precision"
            )
        if rejected is None or rejected["sampled"] < 30:
            if rejected is not None:
                discarded = rejected["population"]
            else:
                discarded = "an unknown number of"
            problems.append(
                "its rejections were never sampled enough (need 30); %s record(s) were discarded unexamined"
                % discarded
            )

    details = []

    if rejected is not None and rejected.get("sampled", 0) >= 1:
        false_omission = rejected["positive"] / float(rejected["sampled"])
        lo, hi = mdm_eval.wilson_interval(rejected["positive"], rejected["sampled"])
        details.append(
            "false omission rate %.3f [%.3f, %.3f] (a sample of rejections estimates P(true match | rejected))"
            % (false_omission, lo, hi)
        )

    frame = verifier.get("known_match_frame")
    if isinstance(frame, dict):
        frame_sampled = frame.get("sampled")
        frame_rejected = frame.get("rejected")
        if (
            _is_nonneg_int(frame_sampled)
            and _is_nonneg_int(frame_rejected)
            and frame_sampled >= 1
            and frame_rejected <= frame_sampled
        ):
            miss_rate = frame_rejected / float(frame_sampled)
            lo, hi = mdm_eval.wilson_interval(frame_rejected, frame_sampled)
            details.append(
                "miss rate %s/%s = %.3f [%.3f, %.3f]"
                % (frame_rejected, frame_sampled, miss_rate, lo, hi)
            )
        else:
            details.append(
                "miss rate NO-DATA: it needs a frame of known true matches, not a sample of rejections"
            )
    else:
        details.append(
            "miss rate NO-DATA: it needs a frame of known true matches, not a sample of rejections"
        )

    if claimed is not None:
        if confirmed is None or confirmed.get("sampled", 0) < 1:
            problems.append(
                "claimed verifier precision has no labelled confirmation sample"
            )
        else:
            lo, hi = mdm_eval.wilson_interval(
                confirmed["positive"], confirmed["sampled"]
            )
            if claimed > hi + 1e-12:
                problems.append(
                    "claimed verifier precision %.3f exceeds %.3f" % (claimed, hi)
                )

    if problems:
        return Finding(GATE_M26, "FAIL", "; ".join(problems + details))

    if confirmed is not None and confirmed.get("sampled", 0) >= 1:
        precision = confirmed["positive"] / float(confirmed["sampled"])
        lo, hi = mdm_eval.wilson_interval(confirmed["positive"], confirmed["sampled"])
        base = "confirmation precision %.3f [%.3f, %.3f]" % (precision, lo, hi)
    else:
        base = "confirmation precision NO-DATA"

    if details:
        return Finding(GATE_M26, "PASS", base + "; " + "; ".join(details))
    return Finding(GATE_M26, "PASS", base)


def _run_gate(gate_name, gate_fn, A, E, Finding):
    try:
        return gate_fn(A, E, Finding)
    except Exception as exc:
        return Finding(gate_name, "FAIL", "malformed input: %s" % exc)


def fn(claim, Finding):
    if not isinstance(claim, dict):
        return []
    master_data = claim.get("master_data")
    if not isinstance(master_data, dict):
        return []

    if "audit" not in master_data:
        return []
    audit = master_data.get("audit")
    if not isinstance(audit, dict):
        return []

    evaluation = master_data.get("evaluation")
    if not isinstance(evaluation, dict):
        evaluation = {}

    return [
        _run_gate(GATE_M23, _gate_m23, audit, evaluation, Finding),
        _run_gate(GATE_M24, _gate_m24, audit, evaluation, Finding),
        _run_gate(GATE_M25, _gate_m25, audit, evaluation, Finding),
        _run_gate(GATE_M26, _gate_m26, audit, evaluation, Finding),
    ]


def register(packs_module, Finding):
    packs_module.register("MASTER_DATA", fn)


def selftest(expect):
    ok = True

    class Finding:
        def __init__(self, gate, verdict, detail):
            self.gate = gate
            self.verdict = verdict
            self.detail = detail

    def run(claim):
        return fn(claim, Finding)

    def md_claim(audit, evaluation=None):
        master_data = {"audit": audit}
        if evaluation is not None:
            master_data["evaluation"] = evaluation
        return {"master_data": master_data}

    ok &= expect(run({}) == [], "no master_data returns empty list")
    ok &= expect(run({"master_data": None}) == [], "master_data None returns empty list")
    ok &= expect(
        run({"master_data": {}}) == [],
        "master_data without audit returns empty list",
    )
    ok &= expect(
        run({"master_data": {"evaluation": {}}}) == [],
        "master_data without audit but with evaluation returns empty list",
    )
    ok &= expect(
        run({"master_data": {"audit": None}}) == [],
        "audit None returns empty list",
    )
    ok &= expect(
        run({"master_data": {"audit": []}}) == [],
        "audit not a dict returns empty list",
    )
    ok &= expect(len(run(md_claim({}))) == 4, "four findings for master_data")
    ok &= expect(
        [f.gate for f in run(md_claim({}))]
        == [GATE_M23, GATE_M24, GATE_M25, GATE_M26],
        "gate order",
    )

    class Packs:
        def __init__(self):
            self.calls = []

        def register(self, name, func):
            self.calls.append((name, func))

    packs = Packs()
    register(packs, Finding)
    ok &= expect(
        len(packs.calls) == 1
        and packs.calls[0][0] == "MASTER_DATA"
        and packs.calls[0][1] is fn,
        "register exactly once",
    )

    base_m23 = {
        "reported": {
            "n_input": 10,
            "status_counts": {"MATCH": 7, "NO_MATCH": 3},
            "pathway_counts": {"key_phone": 4, "vector_auto": 3},
            "percents": {
                "match_rate": {"value": 70.0, "numerator": 7, "denominator": 10}
            },
        }
    }
    res = run(md_claim(base_m23))
    ok &= expect(res[0].verdict == "PASS", "M23 valid PASS")
    ok &= expect("every count sums" in res[0].detail, "M23 PASS detail")

    res = run(md_claim({}))
    ok &= expect(res[0].verdict == "NO-DATA", "M23 no reported NO-DATA")

    res = run(
        md_claim({"reported": {"n_input": -1, "status_counts": {"MATCH": 0}}})
    )
    ok &= expect(
        res[0].verdict == "FAIL" and "n_input" in res[0].detail,
        "M23 bad n_input",
    )

    res = run(md_claim({"reported": {"n_input": 5, "status_counts": {"MATCH": 3}}}))
    ok &= expect("status counts sum" in res[0].detail, "M23 status sum")

    res = run(
        md_claim(
            {
                "reported": {
                    "n_input": 10,
                    "status_counts": {"MATCH": 7, "NO_MATCH": 3},
                    "pathway_counts": {"key_phone": 4, "vector_auto": 2},
                }
            }
        )
    )
    ok &= expect("pathway counts sum" in res[0].detail, "M23 pathway sum")

    res = run(
        md_claim(
            {
                "reported": {
                    "n_input": 10,
                    "status_counts": {"MATCH": 7, "NO_MATCH": 3},
                    "percents": {
                        "x": {"value": 50, "numerator": 7, "denominator": 10}
                    },
                }
            }
        )
    )
    ok &= expect("percent x says" in res[0].detail, "M23 percent mismatch")

    res = run(
        md_claim(
            {
                "reported": base_m23["reported"],
                "computed": {
                    "n_rows": 9,
                    "reconciliation": {
                        "status_counts": {"MATCH": 7, "NO_MATCH": 3},
                        "pathway_counts": {"key_phone": 4, "vector_auto": 3},
                        "consistent": True,
                    },
                },
            }
        )
    )
    ok &= expect("results table has 9 rows" in res[0].detail, "M23 computed n_rows")

    res = run(
        md_claim(
            {
                "reported": base_m23["reported"],
                "computed": {
                    "n_rows": 10,
                    "reconciliation": {
                        "status_counts": {"MATCH": 6, "NO_MATCH": 4},
                        "pathway_counts": {"key_phone": 4, "vector_auto": 3},
                        "consistent": True,
                    },
                },
            }
        )
    )
    ok &= expect("results table has 6 MATCH" in res[0].detail, "M23 computed status")

    res = run(
        md_claim(
            {
                "reported": base_m23["reported"],
                "computed": {
                    "n_rows": 10,
                    "reconciliation": {
                        "status_counts": {"MATCH": 7, "NO_MATCH": 3},
                        "pathway_counts": {"key_phone": 4, "vector_auto": 3},
                        "consistent": False,
                        "problems": ["a", "b", "c", "d"],
                    },
                },
            }
        )
    )
    ok &= expect("inconsistent: a; b; c" in res[0].detail, "M23 computed inconsistent")

    res = run(
        md_claim(
            {
                "computed": {
                    "collisions": {
                        "reference_ids_with_multiple_sources": 0,
                        "rows_in_collisions": 0,
                    },
                    "key_hubs": {"matched_rows_on_hubs": 0},
                }
            }
        )
    )
    ok &= expect(res[1].verdict == "PASS", "M24 PASS")

    res = run(md_claim({}))
    ok &= expect(res[1].verdict == "NO-DATA", "M24 no computed NO-DATA")

    res = run(
        md_claim(
            {
                "computed": {
                    "collisions": {
                        "reference_ids_with_multiple_sources": 2,
                        "rows_in_collisions": 5,
                    },
                    "key_hubs": {},
                }
            }
        )
    )
    ok &= expect("2 reference record(s)" in res[1].detail, "M24 collisions")

    res = run(
        md_claim(
            {
                "computed": {
                    "collisions": {
                        "reference_ids_with_multiple_sources": 2,
                        "rows_in_collisions": 5,
                    },
                    "key_hubs": {},
                },
                "collisions_reviewed": True,
            }
        )
    )
    ok &= expect(res[1].verdict == "PASS", "M24 collisions reviewed")

    res = run(
        md_claim(
            {
                "computed": {
                    "collisions": {},
                    "key_hubs": {
                        "matched_rows_on_hubs": 3,
                        "hub_min": 3,
                        "hubs": 1,
                    },
                }
            }
        )
    )
    ok &= expect("3 matched row(s)" in res[1].detail, "M24 hubs")

    res = run(
        md_claim(
            {
                "computed": {
                    "collisions": {},
                    "key_hubs": {
                        "matched_rows_on_hubs": 3,
                        "hub_min": 3,
                        "hubs": 1,
                    },
                },
                "hubs_reviewed": True,
            }
        )
    )
    ok &= expect(res[1].verdict == "PASS", "M24 hubs reviewed")

    res = run(
        md_claim(
            {
                "computed": {
                    "collisions": {},
                    "key_hubs": {"state": "NO-DATA"},
                }
            }
        )
    )
    ok &= expect(
        "shared-key check NO-DATA" in res[1].detail,
        "M24 key_hubs NO-DATA detail",
    )

    res = run(md_claim({"computed": {"key_hubs": {"state": "NO-DATA"}}}))
    ok &= expect(
        res[1].verdict == "NO-DATA"
        and "no collision counts in the computed audit" in res[1].detail,
        "M24 missing collisions NO-DATA",
    )

    res = run(md_claim({"precision_basis": "similarity"}))
    ok &= expect(
        res[2].verdict == "FAIL" and "similarity score" in res[2].detail,
        "M25 similarity FAIL",
    )

    res = run(md_claim({}))
    ok &= expect(res[2].verdict == "NO-DATA", "M25 no counts NO-DATA")

    res = run(
        md_claim({"reported": {"pathway_counts": {"a": 10, "b": 10}}, "pathway_review": {}})
    )
    ok &= expect("pathway(s) carrying 1.000" in res[2].detail, "M25 material missing")

    res = run(
        md_claim(
            {
                "reported": {"pathway_counts": {"a": 4, "b": 4, "c": 92}},
                "pathway_review": {
                    "c": {"population": 92, "sampled": 10, "positive": 9}
                },
            }
        )
    )
    ok &= expect(
        res[2].verdict == "FAIL"
        and "pathway(s) carrying 0.080 of matches have no labelled sample: a, b"
        in res[2].detail,
        "M25 combined material missing",
    )

    res = run(
        md_claim(
            {
                "reported": {"pathway_counts": {"a": 1, "b": 99}},
                "pathway_review": {"b": {"population": 99, "sampled": 10, "positive": 9}},
                "claimed_pathway_precision": {"a": 0.9},
            }
        )
    )
    ok &= expect(
        "claimed precision for a has no labelled sample" in res[2].detail,
        "M25 claimed no review",
    )

    res = run(
        md_claim(
            {
                "reported": {"pathway_counts": {"a": 100}},
                "pathway_review": {"a": {"population": 100, "sampled": 10, "positive": 1}},
                "claimed_pathway_precision": {"a": 0.9},
            }
        )
    )
    ok &= expect("claimed precision for a is 0.900" in res[2].detail, "M25 claimed exceeds")

    res = run(
        md_claim(
            {
                "reported": {"pathway_counts": {"a": 100}},
                "pathway_review": {"a": {"population": 100, "sampled": 10, "positive": 1}},
            },
            {"claimed_precision": 0.9},
        )
    )
    ok &= expect("overall claimed precision 0.900 exceeds" in res[2].detail, "M25 overall claimed")

    res = run(
        md_claim(
            {
                "reported": {"pathway_counts": {"a": 100}},
                "pathway_review": {"a": {"population": 100, "sampled": 0, "positive": 0}},
            },
            {"claimed_precision": 0.9},
        )
    )
    ok &= expect(
        res[2].verdict == "FAIL"
        and "overall claimed precision has no labelled sample behind it" in res[2].detail,
        "M25 overall claimed no sample",
    )

    strata_a = [
        {"stratum": "s1", "population": 50, "sampled": 5, "positive": 5},
        {"stratum": "s2", "population": 50, "sampled": 5, "positive": 4},
    ]
    est_a = mdm_eval.stratified_estimate(strata_a)

    res = run(
        md_claim(
            {
                "reported": {"pathway_counts": {"a": 100}},
                "pathway_review": {
                    "a": {
                        "population": 100,
                        "sampled": 10,
                        "positive": 9,
                        "strata": strata_a,
                    }
                },
            }
        )
    )
    ok &= expect(
        res[2].verdict == "PASS"
        and ("a 9/10 [%.3f, %.3f]" % (est_a["lo"], est_a["hi"])) in res[2].detail,
        "M25 strata interval detail",
    )

    res = run(
        md_claim(
            {
                "reported": {"pathway_counts": {"a": 100}},
                "pathway_review": {
                    "a": {
                        "population": 100,
                        "sampled": 10,
                        "positive": 9,
                        "strata": strata_a,
                    }
                },
            },
            {"claimed_precision": est_a["hi"] + 0.001},
        )
    )
    ok &= expect(
        res[2].verdict == "FAIL"
        and (
            "overall claimed precision %.3f exceeds %.3f"
            % (est_a["hi"] + 0.001, est_a["hi"])
        )
        in res[2].detail,
        "M25 overall uses all strata",
    )

    res = run(
        md_claim(
            {
                "reported": {"pathway_counts": {"a": 100}},
                "pathway_review": {"a": {"population": 100, "sampled": 10, "positive": 9}},
            }
        )
    )
    ok &= expect(res[2].verdict == "PASS" and "a 9/10" in res[2].detail, "M25 PASS listing")

    res = run(md_claim({}))
    ok &= expect(res[3].verdict == "NO-DATA", "M26 no verifier NO-DATA")

    res = run(md_claim({"verifier": {"kind": "robot"}}))
    ok &= expect(
        res[3].verdict == "FAIL" and "unknown verifier kind robot" in res[3].detail,
        "M26 unknown kind",
    )

    res = run(
        md_claim(
            {
                "verifier": {
                    "kind": "llm",
                    "rejected_review": {"population": 100, "sampled": 30, "positive": 1},
                }
            }
        )
    )
    ok &= expect("at least 30" in res[3].detail, "M26 llm cr missing")

    res = run(
        md_claim(
            {
                "verifier": {
                    "kind": "llm",
                    "confirmed_review": {"population": 100, "sampled": 30, "positive": 29},
                }
            }
        )
    )
    ok &= expect("its rejections were never sampled enough" in res[3].detail, "M26 llm rr missing")

    res = run(md_claim({"verifier": {"kind": "human"}}))
    ok &= expect(res[3].verdict == "NO-DATA", "M26 human neither NO-DATA")

    res = run(
        md_claim({"verifier": {"kind": "human", "claimed_precision": 0.9}})
    )
    ok &= expect(
        res[3].verdict == "FAIL"
        and "claimed verifier precision has no labelled confirmation sample"
        in res[3].detail,
        "M26 claimed no confirmation sample",
    )

    res = run(
        md_claim(
            {
                "verifier": {
                    "kind": "human",
                    "confirmed_review": {"population": 10, "sampled": 11, "positive": 5},
                }
            }
        )
    )
    ok &= expect(
        res[3].verdict == "FAIL"
        and "confirmed_review population" in res[3].detail,
        "M26 sampled exceeds population",
    )

    res = run(
        md_claim(
            {
                "verifier": {
                    "kind": "human",
                    "confirmed_review": {"population": 100, "sampled": 40, "positive": 38},
                }
            }
        )
    )
    ok &= expect(
        res[3].verdict == "PASS" and "confirmation precision" in res[3].detail,
        "M26 human cr PASS",
    )

    res = run(
        md_claim(
            {
                "verifier": {
                    "kind": "human",
                    "confirmed_review": {"population": 100, "sampled": 40, "positive": 38},
                    "rejected_review": {"population": 50, "sampled": 10, "positive": 2},
                }
            }
        )
    )
    ok &= expect("false omission rate" in res[3].detail, "M26 false omission detail")

    res = run(
        md_claim(
            {
                "verifier": {
                    "kind": "human",
                    "confirmed_review": {"population": 100, "sampled": 40, "positive": 38},
                    "rejected_review": {"population": 50, "sampled": 10, "positive": 2},
                    "known_match_frame": {"sampled": 100, "rejected": 5},
                }
            }
        )
    )
    ok &= expect("miss rate 5/100" in res[3].detail, "M26 miss rate frame")

    res = run(
        md_claim(
            {
                "verifier": {
                    "kind": "human",
                    "confirmed_review": {"population": 100, "sampled": 40, "positive": 38},
                }
            }
        )
    )
    ok &= expect("miss rate NO-DATA" in res[3].detail, "M26 miss rate NO-DATA")

    res = run(
        md_claim(
            {
                "verifier": {
                    "kind": "human",
                    "confirmed_review": {"population": 100, "sampled": 40, "positive": 20},
                    "claimed_precision": 0.9,
                }
            }
        )
    )
    ok &= expect("claimed verifier precision 0.900" in res[3].detail, "M26 claimed exceeds")

    return ok


if __name__ == "__main__":
    failures = []

    def expect(cond, msg):
        if cond:
            print("ok:", msg)
        else:
            print("FAIL:", msg)
            failures.append(msg)
        return cond

    ok = selftest(expect)
    if ok:
        print("SELFTEST PASS")
        sys.exit(0)
    else:
        for failure in failures:
            print("FAIL:", failure)
        sys.exit(1)
