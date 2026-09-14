#!/usr/bin/env python3
"""WBS-40.04 Matcher Boundary: the contract an external matcher's output
must satisfy before Brother will audit or act on it.

Brother does not become the matcher (the roadmap's own explicit non-goal
for this row): this module never scores, matches, or re-scores anything.
It defines the MatcherOutput shape and validates/audits a GIVEN output for
internal consistency and staleness only.

Required MatcherOutput fields (roadmap WBS-40.04):
  source_id                    the source-system record being matched
  candidate_id / reference_id  the candidate/reference record matched
                                against -- at least one of the two, not
                                necessarily both
  pathway                      the matching strategy/algorithm name (left
                                as a free string on purpose, the same
                                "the review policy owns its own taxonomy"
                                choice golden_master_contract.py's schema
                                makes for its own cost-class fields)
  score                        numeric match score
  model_version                the external matcher's own version string
                                (free, not this codebase's to define)
  normalizer_version           checked for real, not just presence: must
                                be one of the fingerprint(s)
                                current_normalizer_versions() says this
                                codebase's own normalization_trace.py
                                default chain actually declares right now
  blocker                      the blocking key that grouped these
                                candidates for comparison
  verifier_state                has this pairing been human-verified, and
                                how
  reference_snapshot           pointer/digest to the reference record
                                state at match time -- the audit anchor
                                reference_drifted below compares against

audit_matcher_output() never fails the whole audit over a reference_snapshot
mismatch: the reference record moving on since match time is expected, not
malformed. It is reported as its own reference_drifted flag and left for
the caller to act on.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evidence_obligation as EO  # noqa: E402 -- EV-3 verdict vocabulary, not a parallel one
import normalization_trace as NT  # noqa: E402 -- the real normalizer this module checks against

PASS = "PASS"
FAIL = "FAIL"
if not (PASS in EO.VERDICTS and FAIL in EO.VERDICTS):
    raise AssertionError("evidence_obligation.VERDICTS no longer carries PASS/FAIL")

REQUIRED_FIELDS = (
    "source_id", "pathway", "score", "model_version",
    "normalizer_version", "blocker", "verifier_state", "reference_snapshot",
)
# At least one of these two, not necessarily both.
CANDIDATE_ID_FIELDS = ("candidate_id", "reference_id")


def normalizer_version_fingerprint(chain=None):
    """The 'name=version' fingerprint a matcher output should declare as
    normalizer_version, built by reading normalization_trace's live
    transform chain directly (never hardcoded), joined in chain order.
    A version bump or an added/removed transform there changes what counts
    as current here automatically -- nothing to keep in sync by hand."""
    chain = chain if chain is not None else NT.default_chain()
    return "|".join("%s=%s" % (t.name, t.version) for t in chain)


def current_normalizer_versions():
    """The set of normalizer_version strings presently accepted as current.
    One entry today (the live default chain's fingerprint). A caller
    migrating to a new chain version can pass its own superset (old
    fingerprint plus new) to audit_matcher_output for a grace window
    instead of editing this function."""
    return {normalizer_version_fingerprint()}


def audit_matcher_output(output, current_normalizer_versions, current_reference_digest=None):
    """Audit one external matcher output for structural completeness and
    normalizer staleness. Read-only: never re-scores, never re-matches,
    only checks the shape of data trusted to have come from outside
    Brother.

    current_normalizer_versions: set/iterable of normalizer_version strings
    presently accepted as real and current (see current_normalizer_versions
    above for the live default).
    current_reference_digest: the reference record's digest as it stands
    right now, if the caller has one to compare against. Omit when no
    fresh digest is available -- reference_drifted then reports False
    rather than guessing.

    Returns a dict: verdict (PASS/FAIL), problems (list of str, empty on
    PASS), reference_drifted (bool, a separate flag from verdict -- see
    module docstring).
    """
    if not isinstance(output, dict):
        return {
            "verdict": FAIL,
            "problems": ["output is not a dict: %r" % (output,)],
            "reference_drifted": False,
        }

    problems = []
    for field in REQUIRED_FIELDS:
        if field not in output or output[field] in (None, ""):
            problems.append("missing required field: %s" % field)

    if not any(output.get(f) not in (None, "") for f in CANDIDATE_ID_FIELDS):
        problems.append("missing required field: one of %s" % (CANDIDATE_ID_FIELDS,))

    if "score" in output:
        score = output["score"]
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            problems.append("score %r is not numeric" % (score,))

    normalizer_version = output.get("normalizer_version")
    if normalizer_version and normalizer_version not in current_normalizer_versions:
        problems.append(
            "normalizer_version %r does not match any current normalizer "
            "fingerprint %s -- this matcher output is stale or was never "
            "run against this codebase's real normalizer" %
            (normalizer_version, sorted(current_normalizer_versions)))

    reference_drifted = False
    if current_reference_digest is not None and "reference_snapshot" in output:
        reference_drifted = output["reference_snapshot"] != current_reference_digest

    return {
        "verdict": FAIL if problems else PASS,
        "problems": problems,
        "reference_drifted": reference_drifted,
    }


def main(argv=None):
    import argparse
    import json

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("record", help="path to a JSON file holding one MatcherOutput")
    ap.add_argument(
        "--current-reference-digest", default=None,
        help="the reference record's current digest, to check reference_snapshot drift")
    args = ap.parse_args(argv)

    try:
        with open(args.record, "r", encoding="utf-8") as fh:
            output = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print("NO-DATA: could not read %s: %s" % (args.record, exc))
        return 2

    result = audit_matcher_output(
        output, current_normalizer_versions(), args.current_reference_digest)
    print("%s: %s" % (result["verdict"], args.record))
    for p in result["problems"]:
        print(" -", p)
    print("reference_drifted: %s" % result["reference_drifted"])
    return 0 if result["verdict"] == PASS else 1


if __name__ == "__main__":
    sys.exit(main())
