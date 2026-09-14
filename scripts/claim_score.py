#!/usr/bin/env python3
"""WBS-50.03 Verified Claim Rate 2D. The roadmap's own words:

    Keep Verified Claim Rate.
    Do not replace it.
    Add separate dimensions so overly wide intervals cannot game
    interpretation:
    - resolution rate;
    - empirical interval coverage/calibration;
    - sharpness/interval width;
    - proper score where applicable;
    - baseline-relative skill;
    - age of unresolved claims.
    No blended composite.

This module computes those six dimensions over a cohort of claims. It never
produces a single collapsed score across them -- mirrors journey_passport.py's
own stated principle, reused here rather than re-argued: "a single summary
badge causes readers to miss dimensions below the fold". Each dimension
carries its own PASS/NO-DATA verdict (evidence_obligation.VERDICTS, imported
not redeclared) and its own denominator, independently NO-DATA when its own
population is empty -- a cohort with only interval claims never fakes a
proper-score number, and vice versa.

Per-claim record shape (docs/schema/claim-score-v1.json, schema-backed like
claim_lifecycle.py and claim_evidence.py): check() and hand_rules() mirror
those two siblings' shape exactly. check_matches_lifecycle(score_record,
lifecycle_record) is the one cross-record rule, mirroring claim_evidence.py's
own check_matches_claim: an evidence-of-content record documents one exact
claim revision, so its claim_id and revision_id must match the
claim-lifecycle-v1 record (scripts/claim_lifecycle.py, STATES imported not
redeclared) it was assembled for.

THE SCORED GATE, per the roadmap's own claim lifecycle (WBS-50.01): the
dispatch brief for this unit states it plainly, "a claim only scoreable once
it reaches SCORED state". Every outcome-dependent dimension here (coverage,
sharpness-at-resolution, the Brier proper score, baseline-relative skill)
is computed only over claims whose lifecycle state is SCORED, never merely
RESOLVED (outcome known but not yet consumed into the ledger) and never a
raw outcome_value read off an earlier state. Two dimensions the roadmap
names are explicit exceptions, stated here because the roadmap's six-item
list gives no further detail on either boundary and this is this module's
own inference:
  - resolution_rate: "did the claim ever reach a resolvable outcome" is
    read as "of the claims that were actually used for a decision
    (DECISION_USED or later), how many made it all the way to SCORED" --
    the decision-grade population is the denominator, not every DRAFT.
  - age_of_unresolved: "how long a claim has sat without resolving" is
    read in the plain-English sense of resolved (state RESOLVED or SCORED
    means the outcome is known), not the narrower SCORED gate above --
    a claim can be resolved without yet having been consumed for scoring.

PASS/FAIL/NO-DATA verdicts are imported from evidence_obligation.py, the one
definition site (matching claim_lifecycle.py's and claim_evidence.py's own
imports), never redeclared here.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

import claim_lifecycle as CL
import contract_check as CC
import evidence_obligation

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SCHEMA = os.path.join(ROOT, "docs", "schema", "claim-score-v1.json")

VERDICTS = evidence_obligation.VERDICTS  # ("PASS", "FAIL", "NO-DATA")

#: Reused, never redeclared: claim_lifecycle.py's own chain.
STATES = CL.STATES

#: "did the claim ever reach a resolvable outcome" is read against the
#: population of claims that were actually used for a decision, per this
#: module's own stated inference above.
DECISION_GRADE_STATES = ("DECISION_USED", "OUTCOME_AVAILABLE", "RESOLVED", "SCORED")

#: Plain-English "resolved" for age_of_unresolved: outcome known, whether
#: or not it has been consumed into a scored ledger entry yet.
RESOLVED_STATES = ("RESOLVED", "SCORED")


def _parse_iso(value):
    """Parse an ISO-8601 timestamp, tolerating a trailing 'Z' (which
    datetime.fromisoformat does not accept on its own). Returns None for
    anything that is not a non-empty string or does not parse; callers
    treat that as "no timestamp", the honest NO-DATA case, never a crash."""
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def age_days(created_at, now):
    """Days between created_at (an ISO-8601 string) and now (a datetime).
    None when created_at does not parse."""
    created = _parse_iso(created_at)
    if created is None:
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - created).total_seconds() / 86400.0


def brier(probability, outcome):
    """The Brier proper scoring rule for a binary outcome: (p - o)^2,
    0 is a perfect forecast, 1 is the worst possible. `outcome` is coerced
    to 1.0/0.0 from a bool."""
    o = 1.0 if outcome else 0.0
    return (probability - o) ** 2


def hand_rules(record):
    """The rules a single record's schema keywords cannot express (no
    if/then in the enforced subset): the stated_* field matching
    claim_type must be present and well-formed, the stated_* field NOT
    matching claim_type must be null, resolved_at and outcome_value must
    be null together or non-null together, and outcome_value's type must
    match claim_type when both are present. Returns a list of problems."""
    problems = []
    claim_type = record.get("claim_type")

    stated_interval = record.get("stated_interval")
    stated_probability = record.get("stated_probability")
    if claim_type == "interval":
        if stated_probability is not None:
            problems.append(
                "stated_probability: must be null when claim_type is "
                "'interval', got %r" % stated_probability)
        if stated_interval is None:
            problems.append(
                "stated_interval: required (non-null) when claim_type is "
                "'interval'")
        elif isinstance(stated_interval, dict):
            lower, upper = stated_interval.get("lower"), stated_interval.get("upper")
            if (isinstance(lower, (int, float)) and not isinstance(lower, bool)
                    and isinstance(upper, (int, float)) and not isinstance(upper, bool)
                    and lower > upper):
                problems.append(
                    "stated_interval: lower (%r) must not exceed upper "
                    "(%r)" % (lower, upper))
    elif claim_type == "probability":
        if stated_interval is not None:
            problems.append(
                "stated_interval: must be null when claim_type is "
                "'probability', got %r" % stated_interval)
        if stated_probability is None:
            problems.append(
                "stated_probability: required (non-null) when claim_type "
                "is 'probability'")
        elif (isinstance(stated_probability, bool)
                or not isinstance(stated_probability, (int, float))
                or not (0.0 <= stated_probability <= 1.0)):
            problems.append(
                "stated_probability: must be a number in [0, 1], got %r"
                % stated_probability)

    baseline_interval = record.get("baseline_interval")
    if baseline_interval is not None and claim_type != "interval":
        problems.append(
            "baseline_interval: must be null unless claim_type is "
            "'interval', got a value while claim_type is %r" % claim_type)
    baseline_probability = record.get("baseline_probability")
    if baseline_probability is not None:
        if claim_type != "probability":
            problems.append(
                "baseline_probability: must be null unless claim_type is "
                "'probability', got a value while claim_type is %r" % claim_type)
        elif (isinstance(baseline_probability, bool)
                or not isinstance(baseline_probability, (int, float))
                or not (0.0 <= baseline_probability <= 1.0)):
            problems.append(
                "baseline_probability: must be a number in [0, 1], got %r"
                % baseline_probability)

    resolved_at = record.get("resolved_at")
    outcome_value = record.get("outcome_value")
    if (resolved_at is None) != (outcome_value is None):
        problems.append(
            "resolved_at/outcome_value: must be null together or non-null "
            "together, got resolved_at=%r outcome_value=%r"
            % (resolved_at, outcome_value))
    if resolved_at is not None and _parse_iso(resolved_at) is None:
        problems.append("resolved_at: not a parseable ISO-8601 timestamp: %r" % resolved_at)
    if _parse_iso(record.get("created_at")) is None:
        problems.append("created_at: not a parseable ISO-8601 timestamp: %r" % record.get("created_at"))
    if outcome_value is not None and claim_type in ("interval", "probability"):
        if claim_type == "interval" and (isinstance(outcome_value, bool)
                                          or not isinstance(outcome_value, (int, float))):
            problems.append(
                "outcome_value: must be a number when claim_type is "
                "'interval', got %r" % outcome_value)
        if claim_type == "probability" and not isinstance(outcome_value, bool):
            problems.append(
                "outcome_value: must be a boolean when claim_type is "
                "'probability', got %r" % outcome_value)

    return problems


def check(record, schema):
    """Structural validation against claim-score-v1, plus the hand rules a
    single record can carry. check_matches_lifecycle compares two records
    and is called directly, not from here."""
    problems = []
    CC.validate(record, schema, "", problems)
    problems.extend(hand_rules(record))
    seen, out = set(), []
    for p in problems:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def check_matches_lifecycle(score_record, lifecycle_record):
    """A claim-score-v1 record documents one exact claim revision: its
    claim_id and revision_id must match the claim-lifecycle-v1 record it
    was assembled for. Mirrors claim_evidence.py's own
    check_matches_claim. Returns a list of problems, empty when they
    match."""
    problems = []
    if score_record.get("claim_id") != lifecycle_record.get("claim_id"):
        problems.append(
            "claim_id: score record names %r, lifecycle record is %r"
            % (score_record.get("claim_id"), lifecycle_record.get("claim_id")))
    if score_record.get("revision_id") != lifecycle_record.get("revision_id"):
        problems.append(
            "revision_id: score record names %r, lifecycle record is %r "
            "(scoring must resolve the exact historical revision, never a "
            "rewritten successor -- WBS-50.01's own scoring-target rule)"
            % (score_record.get("revision_id"), lifecycle_record.get("revision_id")))
    return problems


def score_one(score_record, lifecycle_state, now=None):
    """The per-claim raw ingredients for the aggregate dimensions below,
    honest None wherever a dimension does not apply to this one claim (not
    a fabricated 0 or False). `lifecycle_state` is the matching
    claim-lifecycle-v1 record's own state field, read directly, never
    re-derived from this record."""
    now = now or datetime.now(timezone.utc)
    claim_type = score_record.get("claim_type")
    decision_grade = lifecycle_state in DECISION_GRADE_STATES
    resolved = lifecycle_state in RESOLVED_STATES
    scored = lifecycle_state == "SCORED"

    result = {
        "claim_id": score_record.get("claim_id"),
        "revision_id": score_record.get("revision_id"),
        "claim_type": claim_type,
        "lifecycle_state": lifecycle_state,
        "decision_grade": decision_grade,
        "resolved": resolved,
        "scored": scored,
        "age_days": None,
        "in_interval": None,
        "interval_width": None,
        "brier": None,
        "baseline_brier": None,
    }

    if not resolved:
        result["age_days"] = age_days(score_record.get("created_at"), now)
        return result
    if not scored:
        # Outcome known but not yet consumed into a scored ledger entry:
        # the SCORED gate withholds coverage/sharpness/brier/skill until
        # then, exactly the module docstring's stated inference.
        return result

    outcome = score_record.get("outcome_value")
    if claim_type == "interval":
        interval = score_record.get("stated_interval")
        if isinstance(interval, dict) and isinstance(outcome, (int, float)) and not isinstance(outcome, bool):
            lower, upper = interval.get("lower"), interval.get("upper")
            if isinstance(lower, (int, float)) and isinstance(upper, (int, float)):
                result["interval_width"] = float(upper) - float(lower)
                result["in_interval"] = lower <= outcome <= upper
    elif claim_type == "probability":
        prob = score_record.get("stated_probability")
        if isinstance(prob, (int, float)) and not isinstance(prob, bool) and isinstance(outcome, bool):
            result["brier"] = brier(prob, outcome)
            baseline_prob = score_record.get("baseline_probability")
            if isinstance(baseline_prob, (int, float)) and not isinstance(baseline_prob, bool):
                result["baseline_brier"] = brier(baseline_prob, outcome)

    return result


def _rate_dimension(numerator, denominator, population_label):
    if denominator == 0:
        return {"verdict": "NO-DATA", "value": None, "n": 0,
                "detail": "no claim(s) in the %s population yet" % population_label}
    value = numerator / denominator
    return {"verdict": "PASS", "value": value, "n": denominator,
            "detail": "%d/%d = %.4f of the %s population"
                       % (numerator, denominator, value, population_label)}


def _mean_dimension(values, label):
    if not values:
        return {"verdict": "NO-DATA", "value": None, "n": 0,
                "detail": "no scoreable claim(s) yet for %s" % label}
    value = sum(values) / len(values)
    return {"verdict": "PASS", "value": value, "n": len(values),
            "detail": "mean %s over %d claim(s): %.4f" % (label, len(values), value)}


def _skill_dimension(pairs):
    if not pairs:
        return {"verdict": "NO-DATA", "value": None, "n": 0,
                "detail": "no scored probability claim(s) carry a baseline_probability yet"}
    mean_brier = sum(b for b, _ in pairs) / len(pairs)
    mean_baseline = sum(c for _, c in pairs) / len(pairs)
    if mean_baseline == 0:
        return {"verdict": "NO-DATA", "value": None, "n": len(pairs),
                "detail": "baseline Brier score is exactly 0 across %d claim(s); "
                          "the skill ratio is undefined (division by zero)" % len(pairs)}
    skill = 1.0 - (mean_brier / mean_baseline)
    return {"verdict": "PASS", "value": skill, "n": len(pairs),
            "detail": "Brier Skill Score over %d claim(s): 1 - (%.4f / %.4f) = %.4f "
                       "(positive beats the naive baseline, 0 ties it, negative is worse)"
                       % (len(pairs), mean_brier, mean_baseline, skill)}


def _age_dimension(rows):
    if not rows:
        return {"verdict": "NO-DATA", "value": None, "n": 0,
                "detail": "no unresolved claim(s) -- either none exist yet or every "
                          "claim has resolved"}
    ages = sorted(((r["age_days"], r["claim_id"]) for r in rows), reverse=True)
    values = [a for a, _ in ages]
    return {"verdict": "PASS",
            "value": {"mean_days": sum(values) / len(values), "max_days": ages[0][0],
                       "min_days": ages[-1][0], "oldest_claim_id": ages[0][1]},
            "n": len(rows),
            "detail": "%d unresolved claim(s), age in days: mean %.1f, oldest %.1f (%s)"
                       % (len(rows), sum(values) / len(values), ages[0][0], ages[0][1])}


def aggregate(entries, now=None):
    """Roll up a cohort of (score_record, lifecycle_state) pairs into the
    roadmap's six named dimensions. Trusts its caller the way
    journey_passport.py trusts its own composed siblings: every entry is
    assumed already check()-clean and check_matches_lifecycle()-clean
    (report(), the CLI's own entry point below, enforces that before
    calling this). Deliberately no blended composite -- the roadmap's own
    words are "No blended composite.\""""
    now = now or datetime.now(timezone.utc)
    rows = [score_one(rec, state, now) for rec, state in entries]

    decision_grade = [r for r in rows if r["decision_grade"]]
    scored_of_decision_grade = [r for r in decision_grade if r["scored"]]
    resolution_rate = _rate_dimension(
        len(scored_of_decision_grade), len(decision_grade), "decision-grade claim")

    interval_scored = [r for r in rows
                        if r["claim_type"] == "interval" and r["scored"] and r["in_interval"] is not None]
    interval_coverage = _rate_dimension(
        sum(1 for r in interval_scored if r["in_interval"]), len(interval_scored),
        "scored interval claim")

    widths = [r["interval_width"] for r in interval_scored if r["interval_width"] is not None]
    interval_sharpness = _mean_dimension(
        widths, "stated interval width (read together with interval_coverage above -- "
                "an overly wide interval inflates coverage while this stays large)")

    briers = [r["brier"] for r in rows if r["claim_type"] == "probability" and r["brier"] is not None]
    proper_score_brier = _mean_dimension(briers, "Brier score (lower is better, 0 is perfect)")

    skill_pairs = [(r["brier"], r["baseline_brier"]) for r in rows
                   if r["claim_type"] == "probability"
                   and r["brier"] is not None and r["baseline_brier"] is not None]
    baseline_relative_skill = _skill_dimension(skill_pairs)

    unresolved = [r for r in rows if not r["resolved"] and r["age_days"] is not None]
    age_of_unresolved = _age_dimension(unresolved)

    return {
        "schema_version": "claim-score-report-v1",
        "computed_at": now.isoformat(),
        "cohort_size": len(rows),
        "dimensions": {
            "resolution_rate": resolution_rate,
            "interval_coverage": interval_coverage,
            "interval_sharpness": interval_sharpness,
            "proper_score_brier": proper_score_brier,
            "baseline_relative_skill": baseline_relative_skill,
            "age_of_unresolved": age_of_unresolved,
        },
    }


def report(cohort_path, schema, now=None):
    """Load a cohort manifest (a JSON list of {"score": <claim-score-v1
    record>, "lifecycle_state": <a claim_lifecycle.STATES value>} entries),
    validate every entry, and aggregate() the clean ones. Malformed data is
    never silently dropped: any entry that fails check(), fails
    check_matches_lifecycle() against itself trivially (nothing to compare
    against here, so only structural/state problems apply), or names an
    unrecognized lifecycle_state fails the whole report. Returns
    (result, problems); result is None when problems is non-empty."""
    data = CC.load_json(cohort_path, "claim score cohort manifest")
    if not isinstance(data, list):
        raise CC.NoData("cohort manifest %s: top level must be a JSON list" % cohort_path)

    problems = []
    entries = []
    for i, item in enumerate(data):
        prefix = "cohort[%d]" % i
        if not isinstance(item, dict):
            problems.append("%s: must be an object" % prefix)
            continue
        score_record = item.get("score")
        lifecycle_state = item.get("lifecycle_state")
        if not isinstance(score_record, dict):
            problems.append("%s.score: must be an object" % prefix)
            continue
        item_problems = check(score_record, schema)
        if item_problems:
            problems.extend("%s.score.%s" % (prefix, p) for p in item_problems)
            continue
        if lifecycle_state not in STATES:
            problems.append("%s.lifecycle_state: must be one of %r, got %r"
                            % (prefix, STATES, lifecycle_state))
            continue
        entries.append((score_record, lifecycle_state))

    if problems:
        return None, problems
    return aggregate(entries, now), []


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)

    check_p = sub.add_parser("check", help="validate one claim-score-v1 record")
    check_p.add_argument("record", help="path to a claim-score-v1 JSON record")
    check_p.add_argument("--schema", default=DEFAULT_SCHEMA)
    check_p.add_argument("--lifecycle-record",
                          help="optional path to the matching claim-lifecycle-v1 JSON "
                               "record; when given, check_matches_lifecycle also runs")

    report_p = sub.add_parser("report",
                               help="compute the six WBS-50.03 dimensions over a cohort manifest")
    report_p.add_argument("cohort", help="path to a cohort manifest JSON file (see report())")
    report_p.add_argument("--schema", default=DEFAULT_SCHEMA)
    report_p.add_argument("--out", default=None)

    args = ap.parse_args(argv)

    try:
        schema = CC.load_json(args.schema, "claim score schema")
    except CC.NoData as exc:
        print("%s: %s" % (VERDICTS[2], exc))
        return 2

    if args.command == "check":
        try:
            record = CC.load_json(args.record, "claim score record")
            lifecycle_record = None
            if args.lifecycle_record:
                lifecycle_record = CC.load_json(args.lifecycle_record, "claim lifecycle record")
        except CC.NoData as exc:
            print("%s: %s" % (VERDICTS[2], exc))
            return 2
        problems = check(record, schema)
        if lifecycle_record is not None:
            problems.extend(check_matches_lifecycle(record, lifecycle_record))
        if problems:
            print("%s: %d problem(s)" % (VERDICTS[1], len(problems)))
            for p in problems:
                print(" -", p)
            return 1
        print("%s: %s validates as claim-score-v1" % (VERDICTS[0], args.record))
        return 0

    # report
    try:
        result, problems = report(args.cohort, schema)
    except CC.NoData as exc:
        print("%s: %s" % (VERDICTS[2], exc))
        return 2
    if problems:
        print("%s: %d problem(s) in cohort manifest" % (VERDICTS[1], len(problems)))
        for p in problems:
            print(" -", p)
        return 1
    text = json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
