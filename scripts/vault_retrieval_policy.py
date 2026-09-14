#!/usr/bin/env python3
"""WBS-60.02 Vault convergence, context retrieval policy.

Roadmap text (BROTHER_1.0.17_CONVERGENCE_ROADMAP_2026-09-14.md, WBS-60.02,
"Context retrieval policy"):

    At unit start retrieve only:
    - top relevant symptom lessons;
    - component lessons;
    - domain lessons;
    - prior same failure if any.

    Never allow a Vault record to override current source/evidence.

    Context Capsule marks:

    `non_authoritative: true`

Two things this module enforces, matching the two paragraphs above:

  BOUNDED, CATEGORIZED RETRIEVAL. retrieve() selects only from the four
  named categories (symptom, component, domain, prior_failure), each
  capped at --limit-per-category, and never assigns one candidate to
  more than one category. It does not invent a relevance score: ranking
  within a category is whatever order the caller's own candidates
  already carry, this function only filters and caps.

  THE FLAG IS LOAD-BEARING. build_capsule() always sets
  non_authoritative: true on the capsule it returns, and check_capsule()
  refuses a capsule where that flag is missing or merely truthy (1,
  "true") rather than the literal boolean True -- "never allow a Vault
  record to override current source/evidence" is enforced as a field a
  downstream consumer can check, not only a promise in prose.

No dedicated docs/schema/*.json for this module: the roadmap names a
retrieval procedure and one required capsule field, not a record with its
own lifecycle, so this follows lesson_severity.py's shape (a plain
function plus CLI) rather than contract_check.py's schema-file pattern.

Python 3.9, standard library only, no network.
"""
import argparse
import json
import sys

CATEGORIES = ("symptom", "component", "domain", "prior_failure")

DEFAULT_LIMIT_PER_CATEGORY = 3


def retrieve(candidates, symptom=None, component=None, domain=None,
             prior_failure_id=None, limit_per_category=DEFAULT_LIMIT_PER_CATEGORY):
    """Select lessons from `candidates` (a list of dicts, each carrying an
    "id" and optionally symptom/component/domain/failure_id fields) into
    the four categories the roadmap names. Each candidate lands in at
    most one category, checked in the roadmap's own listed order
    (prior_failure first as "prior same failure", then symptom,
    component, domain), so a candidate matching several filters is not
    double counted. Returns a dict of category name -> list of selected
    items."""
    if limit_per_category < 0:
        raise ValueError("limit_per_category must be >= 0")
    selected = {cat: [] for cat in CATEGORIES}
    claimed_ids = set()
    for item in candidates:
        item_id = item.get("id")
        if item_id is not None and item_id in claimed_ids:
            continue
        if prior_failure_id is not None and item.get("failure_id") == prior_failure_id:
            cat = "prior_failure"
        elif symptom is not None and item.get("symptom") == symptom:
            cat = "symptom"
        elif component is not None and item.get("component") == component:
            cat = "component"
        elif domain is not None and item.get("domain") == domain:
            cat = "domain"
        else:
            continue
        if len(selected[cat]) >= limit_per_category:
            continue
        selected[cat].append(item)
        if item_id is not None:
            claimed_ids.add(item_id)
    return selected


def build_capsule(candidates, **retrieve_kwargs):
    """The Context Capsule fragment this policy hands to a unit: the
    categorized selection above, plus the load-bearing non_authoritative
    flag the roadmap names."""
    return {
        "non_authoritative": True,
        "categories": retrieve(candidates, **retrieve_kwargs),
    }


def check_capsule(capsule):
    """Problems, if any: non_authoritative must be present and exactly
    True (a capsule carrying non_authoritative: 1 or "true" is refused,
    since a caller comparing with `is True` would silently treat those
    as unmarked), and every category key must be one of CATEGORIES."""
    if not isinstance(capsule, dict):
        return ["capsule: must be an object, got %s" % type(capsule).__name__]
    problems = []
    if capsule.get("non_authoritative") is not True:
        problems.append(
            "non_authoritative: must be present and exactly true, got %r"
            % (capsule.get("non_authoritative"),))
    categories = capsule.get("categories")
    if not isinstance(categories, dict):
        problems.append("categories: must be an object")
    else:
        for key in categories:
            if key not in CATEGORIES:
                problems.append(
                    "categories: %r is not one of the roadmap's retrieval "
                    "categories %s" % (key, CATEGORIES))
    return problems


def run_selftest():
    failures = []

    fixture = [
        {"id": "a", "symptom": "flaky-test"},
        {"id": "b", "symptom": "flaky-test"},
        {"id": "c", "symptom": "flaky-test"},
        {"id": "d", "symptom": "flaky-test"},  # 4th: must be capped out
        {"id": "e", "component": "vault"},
        {"id": "f", "domain": "mobile"},
        {"id": "g", "failure_id": "F1"},
        {"id": "h"},  # matches nothing, must not appear anywhere
    ]

    sel = retrieve(fixture, symptom="flaky-test", component="vault",
                    domain="mobile", prior_failure_id="F1",
                    limit_per_category=3)
    if [it["id"] for it in sel["symptom"]] != ["a", "b", "c"]:
        failures.append("symptom category should cap at 3 in given order, got %r"
                         % ([it["id"] for it in sel["symptom"]],))
    if [it["id"] for it in sel["component"]] != ["e"]:
        failures.append("component category wrong: %r" % (sel["component"],))
    if [it["id"] for it in sel["domain"]] != ["f"]:
        failures.append("domain category wrong: %r" % (sel["domain"],))
    if [it["id"] for it in sel["prior_failure"]] != ["g"]:
        failures.append("prior_failure category wrong: %r" % (sel["prior_failure"],))
    all_ids = {it["id"] for items in sel.values() for it in items}
    if "h" in all_ids:
        failures.append("an item matching no category leaked into the capsule")

    capsule = build_capsule(fixture, symptom="flaky-test")
    if check_capsule(capsule) != []:
        failures.append("a capsule from build_capsule must itself pass check_capsule")
    if check_capsule({"categories": {}}) == []:
        failures.append("a capsule missing non_authoritative must be refused")
    if check_capsule({"non_authoritative": 1, "categories": {}}) == []:
        failures.append("non_authoritative: 1 (truthy, not True) must be refused")
    if check_capsule({"non_authoritative": True, "categories": {"bogus": []}}) == []:
        failures.append("an unknown category key must be refused")

    for msg in failures:
        print("selftest:", msg)
    return not failures


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--candidates", help="path to a JSON list of candidate lesson records")
    ap.add_argument("--symptom")
    ap.add_argument("--component")
    ap.add_argument("--domain")
    ap.add_argument("--prior-failure-id")
    ap.add_argument("--limit-per-category", type=int, default=DEFAULT_LIMIT_PER_CATEGORY)
    ap.add_argument("--selftest", action="store_true",
                     help="run the built-in self check and exit")
    args = ap.parse_args(argv)

    if args.selftest:
        if run_selftest():
            print("vault_retrieval_policy.py selftest: PASS")
            return 0
        print("vault_retrieval_policy.py selftest: FAIL", file=sys.stderr)
        return 1

    if not args.candidates:
        ap.error("--candidates is required unless --selftest")
    try:
        with open(args.candidates, encoding="utf-8") as fh:
            candidates = json.load(fh)
    except (OSError, ValueError) as exc:
        print("NO-DATA: could not read --candidates: %s" % exc, file=sys.stderr)
        return 2

    capsule = build_capsule(
        candidates, symptom=args.symptom, component=args.component,
        domain=args.domain, prior_failure_id=args.prior_failure_id,
        limit_per_category=args.limit_per_category,
    )
    print(json.dumps(capsule, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
