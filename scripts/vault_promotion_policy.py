#!/usr/bin/env python3
"""WBS-60.04 Vault convergence, Promotion policy.

Roadmap text (BROTHER_1.0.17_CONVERGENCE_ROADMAP_2026-09-14.md, WBS-60.04,
"Promotion policy"):

    Preferred:
    - accepted human decision;
    - resolved claim;
    - repeated measured failure;
    - reproducible fault-lab case.

    Avoid promoting:
    - one model's speculation;
    - unreviewed design preference;
    - temporary workaround;
    - synthetic fixture result presented as real-world behavior.

This module turns those two named lists into a decision: given the
grounds a candidate is being promoted on, decide(grounds) returns PROMOTE,
REFUSE or NO-DATA plus a reason.

INFERENCE THIS SESSION MADE (named here, not silently assumed): the
roadmap states a preferred list and an avoid list but never says what to
do when a candidate carries grounds from BOTH lists at once. This module
treats the avoid list as an absolute refusal, not a tiebreaker: any
avoid-ground present refuses promotion even if a preferred ground is also
present. Reasoning: "avoid promoting" reads as a should-not, and a
should-not that a strong enough preferred ground can outvote is not
actually an avoid list, it is a weighted preference list, which is not
what the roadmap wrote.
"""
import argparse
import json
import sys

PROMOTE, REFUSE, NO_DATA = "PROMOTE", "REFUSE", "NO-DATA"

PREFERRED_GROUNDS = (
    "accepted_human_decision",
    "resolved_claim",
    "repeated_measured_failure",
    "reproducible_fault_lab_case",
)

AVOID_GROUNDS = (
    "model_speculation",
    "unreviewed_design_preference",
    "temporary_workaround",
    "synthetic_fixture_presented_as_real",
)

KNOWN_GROUNDS = frozenset(PREFERRED_GROUNDS) | frozenset(AVOID_GROUNDS)


def decide(grounds):
    """(verdict, reason) for a candidate promoted on `grounds` (a list of
    strings). NO-DATA when any ground is not one this policy recognizes,
    since a decision reached on an unrecognized ground is not actually a
    decision under this policy. REFUSE when any avoid-ground is present,
    regardless of whether a preferred ground is also present (see the
    module docstring). PROMOTE when at least one preferred ground is
    present and no avoid-ground is. REFUSE when grounds is empty or
    names only unrecognized-but-absent cases (no ground at all is not a
    reason to promote)."""
    grounds = list(grounds or [])
    unknown = sorted(set(g for g in grounds if g not in KNOWN_GROUNDS))
    if unknown:
        return NO_DATA, ("unrecognized ground(s) %s; this policy only "
                          "recognizes %s" % (unknown, sorted(KNOWN_GROUNDS)))
    avoid_hit = [g for g in grounds if g in AVOID_GROUNDS]
    if avoid_hit:
        return REFUSE, ("carries avoid-ground(s) %s; the avoid list is a "
                         "refusal, not a tiebreaker, even alongside a "
                         "preferred ground" % avoid_hit)
    preferred_hit = [g for g in grounds if g in PREFERRED_GROUNDS]
    if preferred_hit:
        return PROMOTE, "supported by preferred ground(s) %s" % preferred_hit
    return REFUSE, ("no preferred ground named; promotion needs at least "
                     "one of %s" % (PREFERRED_GROUNDS,))


def run_selftest():
    failures = []

    def expect(name, grounds, want_verdict):
        verdict, _reason = decide(grounds)
        if verdict != want_verdict:
            failures.append("%s: expected %s, got %s" % (name, want_verdict, verdict))

    expect("a single preferred ground promotes",
           ["resolved_claim"], PROMOTE)
    expect("all four preferred grounds promote",
           list(PREFERRED_GROUNDS), PROMOTE)
    expect("a single avoid ground refuses",
           ["model_speculation"], REFUSE)
    expect("no grounds at all refuses",
           [], REFUSE)
    expect("preferred and avoid together refuses (avoid wins)",
           ["resolved_claim", "model_speculation"], REFUSE)
    expect("an unrecognized ground is NO-DATA",
           ["it_felt_right"], NO_DATA)
    expect("mixed known and unknown grounds is NO-DATA even with a preferred ground present",
           ["resolved_claim", "it_felt_right"], NO_DATA)

    for msg in failures:
        print("selftest:", msg)
    return not failures


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--grounds", help='JSON list of ground strings, e.g. \'["resolved_claim"]\'')
    ap.add_argument("--selftest", action="store_true",
                     help="run the built-in self check and exit")
    args = ap.parse_args(argv)

    if args.selftest:
        if run_selftest():
            print("vault_promotion_policy.py selftest: PASS")
            return 0
        print("vault_promotion_policy.py selftest: FAIL", file=sys.stderr)
        return 1

    if not args.grounds:
        ap.error("--grounds is required unless --selftest")
    try:
        grounds = json.loads(args.grounds)
    except ValueError as exc:
        print("NO-DATA: could not read --grounds: %s" % exc, file=sys.stderr)
        return 2
    if not isinstance(grounds, list):
        print("NO-DATA: --grounds must be a JSON list", file=sys.stderr)
        return 2

    verdict, reason = decide(grounds)
    print("%s: %s" % (verdict, reason))
    return {PROMOTE: 0, REFUSE: 1, NO_DATA: 2}[verdict]


if __name__ == "__main__":
    sys.exit(main())
