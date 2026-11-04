"""Recurring failure lessons for BrotherDS.

This module finds repeated gate failures across claims and writes lesson text.
It never writes anywhere itself; the caller files lessons through a separate intake door.
"""

import hashlib
import json
import re
import sys


GUIDANCE = {
    "M1.": "Order the threshold bands and write down why each band exists before you judge the results.",
    "M2.": "Write the survivorship rule for each attribute so the same population is compared every time.",
    "M3.": "Classify the cost of a false merge and route uncertain cases to a review queue.",
    "M4.": "Size the review sample from the error rate you need to detect and the margin you can tolerate.",
    "M5.": "Report residual error when you describe benchmark performance.",
    "M6.": "Report the match rate together with the sampled accuracy behind it.",
    "M7.": "Keep precision inside what the stratified review can actually support.",
    "M8.": "Sample below the merge threshold in review before you claim recall.",
    "M9.": "Recall cannot exceed the completeness of the blocking pairs you started from.",
    "M10.": "Measure gold subset precision on the basis you declared.",
    "M11.": "Review giant clusters caused by transitive closure before you trust the grouping.",
    "M12.": "For every field, make Fellegi-Sunter m above u.",
    "M13.": "Acknowledge major match-score drift and re-label the affected data.",
    "M14.": "Check quality dimensions against thresholds, and accuracy needs a reference method.",
    "M15.": "Record golden-record lineage with the rule and the source system.",
    "M16.": "Choose F-beta with beta below 1 when a false merge is catastrophic, and never use accuracy here.",
    "M17.": "Size the review for the margin the decision needs.",
    "M18.": "Use a probability sample weighted to the population.",
    "M19.": "A model's labels need a human-labelled overlap of at least 50 with kappa at least 0.8.",
    "M20.": "Calibrate match scores before you threshold them as probabilities.",
    "M21.": "Set the merge threshold from the declared error costs.",
    "M22.": "Declare and apply the normalization the data's script needs. For Japanese this includes width, kana, variant kanji, and corporate suffixes.",
    "G3.": "State an interval.",
    "G5.": "Give a derivation that can be recomputed.",
    "G9.": "Declare and verify the grain.",
    "G10.": "Name the metric definition.",
    "G11.": "Report precision and recall at the threshold.",
    "G13.": "Describe the labelled sample's frame.",
}


def recurring_failures(results, min_claims=2):
    """Return gates failed by at least min_claims distinct claims."""
    if min_claims < 1:
        raise ValueError("min_claims must be at least 1")
    if not results:
        return []

    gate_claims = {}
    gate_types = {}
    gate_first_detail = {}
    distinct_claim_ids = set()

    for result in results:
        if not isinstance(result, dict):
            raise ValueError("each result must be a dict with claim_id and findings")
        if "claim_id" not in result:
            raise ValueError("result lacks claim_id")
        if "findings" not in result or not isinstance(result["findings"], list):
            raise ValueError("findings must be a list")

        claim_id = result["claim_id"]
        if not isinstance(claim_id, str) or not claim_id:
            raise ValueError("claim_id must be a non-empty string: {!r}".format(claim_id))

        distinct_claim_ids.add(claim_id)

        claim_type = result.get("claim_type", "")
        if not isinstance(claim_type, str):
            claim_type = str(claim_type)

        for finding in result["findings"]:
            if not isinstance(finding, dict):
                continue
            gate = finding.get("gate")
            verdict = finding.get("verdict")
            if verdict != "FAIL":
                continue
            if gate is None:
                continue
            if not isinstance(gate, str) or not gate:
                raise ValueError("gate must be a non-empty string: {!r}".format(gate))

            if gate not in gate_claims:
                gate_claims[gate] = set()
                gate_types[gate] = set()
                gate_first_detail[gate] = finding.get("detail", "")

            gate_claims[gate].add(claim_id)
            gate_types[gate].add(claim_type)

    total = len(distinct_claim_ids)

    items = []
    for gate, claims in gate_claims.items():
        count = len(claims)
        if count < min_claims:
            continue
        items.append({
            "gate": gate,
            "claims": sorted(claims),
            "count": count,
            "share": count / total,
            "claim_types": sorted(gate_types[gate]),
            "example_detail": gate_first_detail[gate],
        })

    items.sort(key=lambda x: (-x["count"], x["gate"]))
    return items


def guidance_for(gate):
    """Return the longest matching guidance entry for a gate, or a default."""
    if not isinstance(gate, str):
        gate = str(gate)

    best = None
    for key in GUIDANCE:
        if gate.startswith(key):
            if best is None or len(key) > len(best):
                best = key

    if best is None:
        return "Review why this gate keeps failing and write down the fix."
    return GUIDANCE[best]


def lesson_text(item):
    """Return (title, body) lesson text for one recurring-failure item."""
    gate = item["gate"]
    count = item["count"]

    title = "Recurring FAIL {} in {} claims".format(gate, count)
    if len(title) > 80:
        title = title[:80]

    share = item.get("share", 0.0)
    try:
        share_pct = "{:.0f}%".format(share * 100.0)
    except Exception:
        share_pct = "0%"

    claims = item.get("claims", [])
    if len(claims) > 20:
        claim_line = "Claims: " + ", ".join(claims[:20]) + " and {} more".format(len(claims) - 20)
    else:
        claim_line = "Claims: " + ", ".join(claims)

    guidance = guidance_for(gate)

    detail = str(item.get("example_detail", ""))
    detail = re.sub(r"\d+", "N", detail)

    body_lines = [
        "Gate {} failed in {} of claims.".format(gate, share_pct),
        claim_line,
        "What to do next time: " + guidance,
        "Example finding: " + detail,
        "Proposed by BrotherDS from the claim ledger; a person decides whether it becomes a rule.",
    ]
    return title, "\n".join(body_lines)


def lesson_key(item):
    """Return a stable key for a recurring-failure item."""
    gate = item["gate"]
    claims = sorted(str(c) for c in item.get("claims", []))
    encoded = json.dumps(claims)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return "bds-recurring:" + gate + ":" + digest[:12]


def new_items(items, seen_keys):
    """Return items whose lesson_key is not in seen_keys, in input order."""
    if seen_keys is None:
        seen = set()
    else:
        seen = set(seen_keys)

    out = []
    for item in items:
        key = lesson_key(item)
        if key not in seen:
            out.append(item)
    return out


def selftest():
    """Run self-tests and return a list of failure messages."""
    failures = []

    def check(condition, message):
        if not condition:
            failures.append(message)

    def raises_value_error(fn):
        try:
            fn()
        except ValueError:
            return True
        except Exception:
            return False
        return False

    def raises_value_error_naming(fn, needle):
        try:
            fn()
        except ValueError as exc:
            return needle in str(exc)
        except Exception:
            return False
        return False

    # GUIDANCE coverage
    check(isinstance(GUIDANCE, dict), "GUIDANCE must be a dict")
    for i in range(1, 23):
        key = "M{}.".format(i)
        check(key in GUIDANCE, "GUIDANCE missing " + key)
        check(isinstance(GUIDANCE.get(key), str) and len(GUIDANCE.get(key, "")) > 0, "GUIDANCE empty for " + key)
    for key in ["G3.", "G5.", "G9.", "G10.", "G11.", "G13."]:
        check(key in GUIDANCE, "GUIDANCE missing " + key)
        check(isinstance(GUIDANCE.get(key), str) and len(GUIDANCE.get(key, "")) > 0, "GUIDANCE empty for " + key)
    check(len(GUIDANCE) >= 28, "GUIDANCE must cover all required prefixes")

    # guidance_for
    check(guidance_for("M1.x") == GUIDANCE["M1."], "guidance_for M1")
    check(guidance_for("M12.x") == GUIDANCE["M12."], "guidance_for M12")
    check(guidance_for("M12.x") != GUIDANCE["M1."], "M1 must not answer for M12")
    check(guidance_for("G3.x") == GUIDANCE["G3."], "guidance_for G3")
    check(guidance_for("G10.x") == GUIDANCE["G10."], "guidance_for G10")
    check(guidance_for("unknown") == "Review why this gate keeps failing and write down the fix.", "guidance_for unknown")

    # recurring_failures errors
    check(raises_value_error(lambda: recurring_failures([], 0)), "min_claims 0 raises")
    check(raises_value_error(lambda: recurring_failures([], -5)), "min_claims -5 raises")
    check(raises_value_error(lambda: recurring_failures([{"claim_type": "A", "findings": []}], 1)), "missing claim_id raises")
    check(raises_value_error(lambda: recurring_failures([{"claim_id": "c1", "findings": {}}], 1)), "findings not list raises")
    check(raises_value_error(lambda: recurring_failures([{"claim_id": "c1"}], 1)), "missing findings raises")
    check(raises_value_error(lambda: recurring_failures([None], 1)), "non-dict result raises")

    # Fix 2: non-string / empty gate and claim_id raise ValueError naming the value
    check(
        raises_value_error_naming(
            lambda: recurring_failures(
                [{"claim_id": "c1", "findings": [{"gate": 42, "verdict": "FAIL", "detail": "x"}]}],
                1,
            ),
            "42",
        ),
        "int gate raises ValueError naming 42",
    )
    check(
        raises_value_error(
            lambda: recurring_failures(
                [{"claim_id": "c1", "findings": [{"gate": [], "verdict": "FAIL", "detail": "x"}]}],
                1,
            )
        ),
        "list gate raises ValueError",
    )
    check(
        raises_value_error(
            lambda: recurring_failures(
                [{"claim_id": "c1", "findings": [{"gate": "", "verdict": "FAIL", "detail": "x"}]}],
                1,
            )
        ),
        "empty-string gate raises ValueError",
    )
    check(
        raises_value_error_naming(
            lambda: recurring_failures([{"claim_id": 42, "findings": []}], 1),
            "42",
        ),
        "int claim_id raises ValueError naming 42",
    )
    check(
        raises_value_error(
            lambda: recurring_failures([{"claim_id": "", "findings": []}], 1)
        ),
        "empty-string claim_id raises ValueError",
    )
    check(
        raises_value_error(
            lambda: recurring_failures([{"claim_id": [], "findings": []}], 1)
        ),
        "list claim_id raises ValueError",
    )

    # recurring_failures data
    results = [
        {"claim_id": "c1", "claim_type": "A", "findings": [
            {"gate": "M1.x", "verdict": "FAIL", "detail": "123"},
            {"gate": "M1.x", "verdict": "FAIL", "detail": "456"},
            {"gate": "M2.y", "verdict": "PASS", "detail": "no"},
        ]},
        {"claim_id": "c2", "claim_type": "B", "findings": [
            {"gate": "M1.x", "verdict": "FAIL", "detail": "789"},
            {"gate": "M3.z", "verdict": "FAIL", "detail": "abc123"},
        ]},
        {"claim_id": "c3", "claim_type": "A", "findings": [
            {"gate": "M1.x", "verdict": "NO-DATA", "detail": "x"},
            {"gate": "M2.y", "verdict": "FAIL", "detail": "def"},
        ]},
    ]
    out = recurring_failures(results, min_claims=2)
    check(len(out) == 1, "min 2 returns 1 item")
    item = out[0] if out else {}
    check(item.get("gate") == "M1.x", "gate is M1.x")
    check(item.get("claims") == ["c1", "c2"], "claims sorted and distinct")
    check(item.get("count") == 2, "count is 2")
    check(abs(item.get("share", 0) - 2 / 3) < 1e-9, "share is 2/3")
    check(item.get("claim_types") == ["A", "B"], "claim types sorted")
    check(item.get("example_detail") == "123", "first failing detail")

    out1 = recurring_failures(results, min_claims=1)
    check(len(out1) == 3, "min 1 returns 3 items")
    check([x["gate"] for x in out1] == ["M1.x", "M2.y", "M3.z"], "sort by count desc then gate asc")
    check(recurring_failures([], 1) == [], "empty results returns empty list")

    # Fix 1: share uses count of DISTINCT claim ids across results
    dup_results = [
        {"claim_id": "c1", "findings": [{"gate": "M1.x", "verdict": "FAIL", "detail": "1"}]},
        {"claim_id": "c1", "findings": [{"gate": "M1.x", "verdict": "FAIL", "detail": "2"}]},
        {"claim_id": "c2", "findings": [{"gate": "M1.x", "verdict": "FAIL", "detail": "3"}]},
    ]
    dup_out = recurring_failures(dup_results, min_claims=2)
    check(len(dup_out) == 1, "dup results: one item")
    dup_item = dup_out[0] if dup_out else {}
    check(dup_item.get("count") == 2, "dup results: distinct count is 2")
    check(abs(dup_item.get("share", 0) - 2 / 2) < 1e-9, "dup results: share is 2/2 not 2/3")

    dup_results2 = [
        {"claim_id": "c1", "findings": [{"gate": "M1.x", "verdict": "FAIL", "detail": "1"}]},
        {"claim_id": "c1", "findings": []},
        {"claim_id": "c2", "findings": [{"gate": "M1.x", "verdict": "FAIL", "detail": "2"}]},
        {"claim_id": "c3", "findings": []},
    ]
    dup_out2 = recurring_failures(dup_results2, min_claims=2)
    check(len(dup_out2) == 1, "dup results2: one item")
    check(abs(dup_out2[0].get("share", 0) - 2 / 3) < 1e-9, "dup results2: share is 2/3 over 3 distinct ids")

    # lesson_text
    title, body = lesson_text(item)
    check(title == "Recurring FAIL M1.x in 2 claims", "title correct")
    check(len(title) <= 80, "title at most 80 chars")
    check("Gate M1.x failed in 67% of claims." in body, "share percent no decimals")
    check("Claims: c1, c2" in body, "claims line correct")
    check("What to do next time: " + GUIDANCE["M1."] in body, "guidance line correct")
    check("Example finding: N" in body, "digits replaced in example")
    check("Proposed by BrotherDS from the claim ledger; a person decides whether it becomes a rule." in body, "footer line correct")

    item2 = dict(item)
    item2["example_detail"] = "abc123def45"
    _, body2 = lesson_text(item2)
    check("Example finding: abcNdefN" in body2, "multiple digit runs replaced")

    many = dict(item)
    many["claims"] = ["c{}".format(i) for i in range(1, 26)]
    _, body3 = lesson_text(many)
    claim_part = body3.split("Claims: ")[1].split("\n")[0]
    expected_start = ", ".join(["c{}".format(i) for i in range(1, 21)]) + " and 5 more"
    check(claim_part == expected_start, "claims truncated to 20 and N more")
    check("c21" not in claim_part, "c21 omitted from claims line")

    # lesson_key
    key1 = lesson_key(item)
    check(key1.startswith("bds-recurring:M1.x:"), "lesson_key prefix")
    check(len(key1) == len("bds-recurring:M1.x:") + 12, "lesson_key length")
    expected_hash = hashlib.sha256(json.dumps(["c1", "c2"]).encode("utf-8")).hexdigest()[:12]
    check(key1 == "bds-recurring:M1.x:" + expected_hash, "lesson_key hash uses json.dumps")
    item_reordered = dict(item)
    item_reordered["claims"] = ["c2", "c1"]
    check(lesson_key(item_reordered) == key1, "lesson_key stable across claim order")
    item_diff_gate = dict(item)
    item_diff_gate["gate"] = "M2.y"
    check(lesson_key(item_diff_gate) != key1, "lesson_key changes with gate")

    # Fix 3: comma in ids cannot collide
    item_comma1 = dict(item)
    item_comma1["claims"] = ["a,b", "c"]
    item_comma2 = dict(item)
    item_comma2["claims"] = ["a", "b,c"]
    check(
        lesson_key(item_comma1) != lesson_key(item_comma2),
        "lesson_key distinguishes ['a,b','c'] from ['a','b,c']",
    )
    expected_comma_hash = hashlib.sha256(json.dumps(["a,b", "c"]).encode("utf-8")).hexdigest()[:12]
    check(
        lesson_key(item_comma1) == "bds-recurring:M1.x:" + expected_comma_hash,
        "lesson_key hash for comma id matches json.dumps encoding",
    )

    # new_items
    items = [item]
    check(new_items(items, set()) == items, "new_items empty seen_keys")
    check(new_items(items, [key1]) == [], "new_items seen_keys list filters")
    check(new_items([], {key1}) == [], "new_items empty items")
    item_a = dict(item, gate="A", claims=["a"])
    item_b = dict(item, gate="B", claims=["b"])
    key_a = lesson_key(item_a)
    key_b = lesson_key(item_b)
    check(new_items([item_a, item_b], {key_a}) == [item_b], "new_items filters and keeps order")
    check(new_items([item_a, item_b], {key_b}) == [item_a], "new_items filters first item")

    return failures


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        failures = selftest()
        if failures:
            for msg in failures:
                print("SELFTEST FAIL: " + msg)
            sys.exit(1)
        print("SELFTEST PASS")
        sys.exit(0)
    else:
        print("usage: python3 lessons.py --selftest")
        sys.exit(1)
