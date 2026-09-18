#!/usr/bin/env python3
"""DOM-10.02: judges a claim's evidence for discriminativeness.

WHY THIS EXISTS. Of 471 recorded failures on this estate, the largest
single class at 195 is something green that was not evidence: a check
that exited 0 got read as proof, when exit 0 only shows the check did
not fail today, never that it WOULD fail if the claimed property were
false. This module answers that question mechanically instead of by
narrative confidence.

THE DECIDING PROPERTY (the row this module exists to enforce): a HIGH
RISK claim's evidence must be able to answer "would this check FAIL if
the claimed property were false?" A green exit code alone never answers
that question, so a HIGH RISK claim resting on exit code alone is
NO-DATA, never PROVEN. See judge_claim() and classify_evidence() below.

VOCABULARY REUSE (per the estate's evidence-vocabulary rule, EV-3 in
evidence_obligation.py, the frozen definition site): the raw outcome of
actually running a claim's own check is one of evidence_obligation.
VERDICTS (PASS, FAIL, NO-DATA), imported here, never redeclared. This
module answers a different question (is the evidence discriminating,
not did the check pass), so it needs its own vocabulary, PROVEN,
REFUTED, NO-DATA, STALE, but it reuses the literal string "NO-DATA" for
the one meaning both vocabularies share: no measurement was taken.
orchestrator_invariants.py's rule that an unrecognized input is never
read as the safe case is followed here too: judge_claim() raises
ValueError on an unknown risk class or unknown raw result instead of
defaulting one in.

A CLAIM is a plain dict:
  check      str, required. What was run.
  risk       "LOW" or "HIGH", required. See RISK_CLASSES.
  property   str, required. The claimed property, named in words.
  result     one of evidence_obligation.VERDICTS, required. What running
             the check actually produced.
  id         str, optional. Needed only to detect evidence borrowed
             across claims (see judge_claims).
  revision   str, optional. The state this claim is about; compared
             against each evidence item's own "as_of" to catch staleness.
  evidence   list of dicts, optional. Each item:
               kind        one of EVIDENCE_KINDS
               note        str, non-empty, what was exercised
               property    str, non-empty, the property THIS evidence
                            exercised (must match the claim's property)
               id          str, optional, for cross-claim borrow detection
               claim_id    str, optional, which claim this was captured for
               as_of       str, optional, the revision this evidence is
                            good for
               red_cause   str, optional; "syntax_error" invalidates a
                            "red_before" entry (a broken-syntax red is not
                            the property failing, same reason the mutation
                            gate row does not count one)

Python 3 stdlib only, no network.
"""
import argparse
import json
import sys

import evidence_obligation

# This module's own verdicts. Distinct from evidence_obligation.VERDICTS
# (which judges a check's own exit code) because this module judges a
# different property: whether the evidence behind that exit code could
# have told PASS apart from a false claimed property. NO-DATA is the one
# member both vocabularies share, on purpose (see module docstring): in
# either vocabulary it always means "no measurement was taken".
PROVEN = "PROVEN"
REFUTED = "REFUTED"
NO_DATA = "NO-DATA"
STALE = "STALE"
VERDICTS = (PROVEN, REFUTED, NO_DATA, STALE)

assert NO_DATA in evidence_obligation.VERDICTS, (
    "NO-DATA must stay the literal both vocabularies share")

RISK_CLASSES = ("LOW", "HIGH")

# The only evidence kinds this module accepts as discriminating. Each one
# is a real perturbation whose failure would show the claimed property is
# false. "the command exited 0" is deliberately not a member of this set;
# that omission is the row DOM-10.02 exists to enforce.
EVIDENCE_KINDS = frozenset((
    "red_before",
    "implementation_revert",
    "mutation",
    "negative_fixture",
    "dependency_perturbation",
    "configuration_perturbation",
    "permission_withdrawal",
    "malformed_input",
    "induced_failure",
))


def _same_property(claimed, offered):
    """Exact match after trimming and casefolding.

    ponytail: a fuzzy or substring match would forgive close paraphrases,
    but it would also forgive the exact failure this unit exists to catch,
    evidence that quietly names a nearby but different property. Upgrade
    path: a reviewed synonym table, if exact match proves too strict.
    """
    return claimed.strip().casefold() == offered.strip().casefold()


def classify_evidence(claim, item, borrowed_ids=frozenset()):
    """One evidence item's standing against one claim.

    Returns "valid", "stale", or "invalid: <reason>". Never raises: an
    evidence item malformed in some new way is bad data, not a program
    defect, and gets sorted into "invalid" like any other bad item.
    """
    if not isinstance(item, dict):
        return "invalid: evidence item is not an object"

    kind = item.get("kind")
    if kind not in EVIDENCE_KINDS:
        return "invalid: kind %r is not a discriminating evidence kind" % (kind,)

    note = item.get("note")
    if not isinstance(note, str) or not note.strip():
        return "invalid: evidence is empty, no note of what was exercised"

    offered_property = item.get("property")
    if not isinstance(offered_property, str) or not offered_property.strip():
        return "invalid: evidence does not name the property it exercised"

    claimed_property = claim.get("property", "")
    if not _same_property(claimed_property, offered_property):
        return "invalid: evidence exercised a different property than the one claimed"

    claim_id = claim.get("id")
    owner = item.get("claim_id")
    if owner is not None and claim_id is not None and owner != claim_id:
        return "invalid: evidence was captured for a different claim (borrowed)"
    evidence_id = item.get("id")
    if evidence_id is not None and evidence_id in borrowed_ids:
        return "invalid: evidence id is shared by more than one claim (borrowed)"

    if kind == "red_before" and item.get("red_cause") == "syntax_error":
        return ("invalid: the red-before came from a syntax error, not the "
                "claimed property failing (same reason the mutation gate "
                "row does not count a broken-syntax red)")

    revision = claim.get("revision")
    as_of = item.get("as_of")
    if revision is not None and as_of is not None and as_of != revision:
        return "stale"

    return "valid"


def _find_borrowed_ids(claims):
    """Evidence ids used by more than one claim, regardless of whether
    either evidence item bothered to name a claim_id: an omission is not
    a license to reuse the same evidence for a second claim."""
    owners = {}
    for claim in claims:
        for item in claim.get("evidence") or []:
            if not isinstance(item, dict):
                continue
            eid = item.get("id")
            if eid is None:
                continue
            owners.setdefault(eid, set()).add(claim.get("id"))
    return frozenset(eid for eid, claim_ids in owners.items() if len(claim_ids) > 1)


def judge_claim(claim, borrowed_ids=frozenset()):
    """Judge one claim. Returns (verdict, reason).

    Raises ValueError when the claim is missing a required field, or
    carries a risk class or raw result this module does not know: per
    orchestrator_invariants.py's rule, an input outside the known set is
    never read as the safe case.
    """
    if not isinstance(claim, dict):
        raise ValueError("claim must be an object")
    for field in ("check", "risk", "property", "result"):
        if field not in claim:
            raise ValueError("claim missing required field %r" % (field,))

    risk = claim["risk"]
    if risk not in RISK_CLASSES:
        raise ValueError("unknown risk class: %r" % (risk,))
    result = claim["result"]
    if result not in evidence_obligation.VERDICTS:
        raise ValueError("unknown raw result: %r" % (result,))

    if result == "NO-DATA":
        return NO_DATA, "the check itself produced no data"
    if result == "FAIL":
        return REFUTED, ("the check reported failure, which itself "
                          "contradicts the claimed property")

    # result == "PASS" from here: this is the only case where the
    # deciding property is actually in question, because PASS is the
    # verdict a FALSE claimed property can produce by accident.
    evidence = claim.get("evidence") or []

    if risk == "LOW":
        # DECISION (edge: "a low risk claim with only an exit code").
        # Justification: the deciding property exists to stop a HIGH RISK
        # claim, one that is unsafe or costly if wrong, from resting on
        # exit code alone. A LOW risk claim is cheap to be wrong about by
        # definition, so its exit code plus the standing ability to rerun
        # it is proportionate evidence. This is the one branch where PASS
        # alone still means PROVEN.
        return PROVEN, "low risk claim: exit code accepted by policy"

    # risk == "HIGH": exit code alone is never enough here (the row this
    # module exists to enforce). At least one evidence item must be a
    # discriminating kind that names this claim's own property.
    classifications = [classify_evidence(claim, item, borrowed_ids) for item in evidence]
    valid_kinds = sorted({
        item["kind"] for item, c in zip(evidence, classifications) if c == "valid"
    })
    if valid_kinds:
        return PROVEN, ("discriminating evidence (%s) exercises the "
                         "claimed property" % ", ".join(valid_kinds))
    if "stale" in classifications:
        return STALE, ("evidence once discriminated the property but is "
                        "not as_of the claim's current revision")
    return NO_DATA, ("high risk claim has no discriminating evidence; an "
                      "exit code alone is not proof (DOM-10.02)")


def judge_claims(claims):
    """Judge a batch of claims. The only thing a batch view adds over
    judging each claim alone: an evidence id shared by more than one claim
    is borrowing, even when neither item names a claim_id. Returns a dict
    keyed by each claim's own id (falling back to its check name)."""
    borrowed_ids = _find_borrowed_ids(claims)
    out = {}
    for claim in claims:
        key = claim.get("id", claim.get("check"))
        out[key] = judge_claim(claim, borrowed_ids)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("claims_file", help="path to a JSON array of claim objects")
    args = ap.parse_args(argv)

    try:
        with open(args.claims_file, "r", encoding="utf-8") as handle:
            claims = json.load(handle)
    except FileNotFoundError:
        print("NO-DATA: missing %s" % args.claims_file, file=sys.stderr)
        return 2
    except Exception as exc:
        print("NO-DATA: cannot read %s: %s" % (args.claims_file, exc), file=sys.stderr)
        return 2
    if not isinstance(claims, list):
        print("NO-DATA: %s is not a JSON array of claims" % args.claims_file, file=sys.stderr)
        return 2

    try:
        results = judge_claims(claims)
    except ValueError as exc:
        print("NO-DATA: %s" % exc, file=sys.stderr)
        return 2

    blocked = False
    for key, (verdict, reason) in results.items():
        print("%s\t%s\t%s" % (key, verdict, reason))
        if verdict in (REFUTED, NO_DATA, STALE):
            blocked = True
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
