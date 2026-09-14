#!/usr/bin/env python3
"""WBS-20.04 Assurance Graph: link every externally meaningful 1.0.17 claim
to the real evidence that backs it, per the roadmap's own edge shape (R7):

  CLAIM -> failure mode -> control -> check -> fixture/evidence
                              \\-> environment
                         \\-> residual limitation
  CLAIM -> last verified revision

Per docs/schema/assurance-graph-v1.json's own description this is a VIEW,
mirroring scripts/merge_passport.py's discipline (WBS-40.07): compose_claim()
never accepts a raw claim. Every failure_mode's check verdict must already
have been produced by calling a real sibling control's own function --
scope_audit.audit(), native_evidence.validate_record(), or
merge_passport.compose_passport() for the three pilot claims below -- never
recomputed or asserted here. Per docs/decisions/evidence-vocabulary-2026-09-13
.json's EV-3 ruling, the PASS/FAIL/NO-DATA triple is imported from
scripts/evidence_obligation.py, not redeclared.

SCOPE, stated plainly (roadmap wording checked in the commit message this
file ships with). WBS-20.04's own text is:

  "Tomorrow: graph schema + pilot for three release claims"

with the three pilot claims named verbatim. It does not specify a wire
format for existing checks, a storage layer, or a CLI beyond that. Built
here, as the minimal defensible reading of "graph schema + pilot":

  - docs/schema/assurance-graph-v1.json: the graph record shape.
  - compose_claim(): the general VIEW function any claim (not only the
    three pilot ones) can be composed through, once failure-mode checks
    exist for it.
  - check_record(): schema validation, reusing contract_check.py's
    enforced keyword subset like every other contract checker in this
    repo (golden_master_contract.py is the closest sibling, mirrored
    directly).
  - Three pilot check functions, one per named pilot claim, each calling
    exactly one real sibling control and reading its actual verdict --
    never asserting the claim held.

INFERENCES made where the roadmap text alone does not decide the shape
(named here rather than hidden, matching tonight's WBS-50.01-50.05
discipline):

  - The roadmap's diagram nests "environment" under "control" and
    "residual limitation" under "failure mode" (both one level above
    "check"). This module places both alongside "check" on the
    failure_mode entry instead: the diagram is illustrative prose, not a
    parseable grammar, and every other reasonable reading of the same
    picture places them there too, since a check is what actually runs in
    an environment and a residual limitation is what that specific check
    (not the failure mode in the abstract) fails to establish.
  - "Test count becomes secondary to claim coverage and discriminating
    evidence" (R7) is read as a design principle for how this graph is
    USED later (weighting coverage over raw test counts), not as a field
    this schema must carry; no field was invented to hold it.
  - No persistence/storage location is named for composed claim records
    in the roadmap text; none is built. compose_claim() returns a dict a
    caller can write to disk, matching every other compose_* module in
    this repo (merge_passport.compose_passport does the same).
  - "By release, cover every externally marketed 1.0.17 claim" names a
    release-time obligation, not a tomorrow deliverable; no attempt was
    made here to enumerate every 1.0.17 claim, only the three the roadmap
    names for this row.
"""
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from evidence_obligation import VERDICTS, exit_code_for_verdict  # noqa: E402
import contract_check as CC  # noqa: E402
import scope_audit  # noqa: E402
import native_evidence  # noqa: E402
import merge_passport  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SCHEMA = os.path.join(ROOT, "docs", "schema", "assurance-graph-v1.json")

NO_DATA = "NO-DATA"


def _worst(verdicts):
    """Combine several VERDICTS into one: FAIL beats NO-DATA beats PASS.
    Mirrors merge_passport._worst exactly (same shared triple, same rule)."""
    verdicts = list(verdicts)
    if not verdicts:
        return NO_DATA
    if "FAIL" in verdicts:
        return "FAIL"
    if NO_DATA in verdicts:
        return NO_DATA
    return "PASS"


def _git_head(repo=None):
    """The revision this record's checks were last run against, or NO-DATA
    when it cannot be determined. Never raises: a broken git call is
    evidence of NO-DATA, not a crash."""
    repo = repo or ROOT
    try:
        out = subprocess.run(
            ["git", "-C", repo, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return NO_DATA
    if out.returncode != 0:
        return NO_DATA
    sha = out.stdout.strip()
    return sha if sha else NO_DATA


def compose_claim(claim_id, claim_text, failure_modes, last_verified_revision=None, repo=None):
    """Compose an Assurance Graph claim record from failure modes whose
    check verdicts were already produced elsewhere. Each entry of
    failure_modes is:

      {"failure_mode": str, "control": str,
       "check": {"name": str, "command_ref": str, "fixture_ref": str,
                 "evidence_ref": str, "verdict": one of VERDICTS,
                 "verdict_reason": str},
       "environment": str, "residual_limitation": str}

    Returns a dict matching assurance-graph-v1's schema. Raises ValueError
    on a missing/invalid check verdict rather than silently defaulting --
    the same refusal discipline merge_passport.py uses for the shared
    triple.
    """
    if not failure_modes:
        raise ValueError("a claim with no failure modes has nothing checked against it")

    normalized = []
    for fm in failure_modes:
        check = fm.get("check") or {}
        verdict = check.get("verdict")
        if verdict not in VERDICTS:
            raise ValueError(
                "failure mode %r check verdict %r outside the shared triple"
                % (fm.get("failure_mode"), verdict))
        reason = check.get("verdict_reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(
                "failure mode %r check has no verdict_reason" % fm.get("failure_mode"))
        normalized.append({
            "failure_mode": fm.get("failure_mode", ""),
            "control": fm.get("control", ""),
            "check": {
                "name": check.get("name", ""),
                "command_ref": check.get("command_ref", ""),
                "fixture_ref": check.get("fixture_ref", ""),
                "evidence_ref": check.get("evidence_ref", ""),
                "verdict": verdict,
                "verdict_reason": reason,
            },
            "environment": fm.get("environment", ""),
            "residual_limitation": fm.get("residual_limitation", ""),
        })

    overall = _worst(fm["check"]["verdict"] for fm in normalized)
    if overall not in VERDICTS:
        raise AssertionError("assurance graph overall verdict %r outside the shared triple" % overall)

    return {
        "schema_version": "assurance-graph-v1",
        "claim_id": claim_id,
        "claim_text": claim_text,
        "failure_modes": normalized,
        "verdict": overall,
        "verdict_reason": "worst-of %d failure mode check verdict(s)" % len(normalized),
        "last_verified_revision": last_verified_revision or _git_head(repo),
    }


def check_record(record, schema=None):
    """Validate a composed claim record against assurance-graph-v1's schema.
    Reuses contract_check.validate for the same enforced keyword subset
    every other contract checker in this repo uses (mirrors
    golden_master_contract.check) -- never a second structural checker."""
    schema = schema or CC.load_json(DEFAULT_SCHEMA, "assurance graph schema")
    problems = []
    CC.validate(record, schema, "", problems)
    return problems


# ---------------------------------------------------------------------------
# Pilot claims (WBS-20.04's own three named pilot claims, verbatim from the
# roadmap). Each function below calls exactly one real sibling control and
# reads ITS actual verdict; none of them assert the claim held.
# ---------------------------------------------------------------------------

def check_undeclared_writes_not_silent(unit, before, after=None, cwd=None, runner=None):
    """Pilot claim: 'undeclared writes do not silently integrate.' Calls
    scope_audit.audit() -- the real control this estate's merge path already
    runs through -- and reads the claim as held only when an undeclared
    write actually occurred AND was caught (QUARANTINE), never assumed. A
    CLEAN result means this particular fixture had nothing undeclared to
    catch: by scope_audit's own logic that is not evidence either way for
    this claim, so it reads NO-DATA rather than a silent PASS or FAIL."""
    verdict, detail = scope_audit.audit(unit, before, after=after, cwd=cwd, runner=runner)
    if verdict == scope_audit.QUARANTINE:
        return "PASS", "an undeclared write was held for review, not merged: " + detail["reason"]
    if verdict == scope_audit.CLEAN:
        return NO_DATA, "no undeclared write occurred in this fixture, so the claim is untested here: " + detail["reason"]
    if verdict == scope_audit.NO_DATA:
        return NO_DATA, detail["reason"]
    return "FAIL", "scope_audit returned an unrecognized verdict %r: %s" % (verdict, detail["reason"])


def check_native_evidence_not_stale(evidence_path, repo, xcrun="xcrun"):
    """Pilot claim: 'native test evidence cannot reuse stale result
    bundles.' Ground truth for whether the recorded candidate is actually
    stale is computed independently, by calling native_evidence's OWN
    repo_snapshot()/same_snapshot() against the record's candidate_after --
    the identical mechanism validate_record() uses internally -- rather
    than pattern-matching validate_record()'s failure text. That ground
    truth is then compared against validate_record()'s real verdict:

      genuinely stale, and validate_record() rejects it -> PASS (caught)
      genuinely stale, and validate_record() accepts it -> FAIL (a bug:
        stale evidence silently reused; unreachable today, since
        validate_record() uses this exact same comparison internally, but
        this check would catch a regression that broke it)
      not stale at all -> NO-DATA (nothing to catch in this fixture, so
        the claim is untested here, never a silent PASS)
    """
    doc, error = native_evidence.read_json(evidence_path)
    if error:
        return NO_DATA, "cannot read evidence record: %s" % error
    after = doc.get("candidate_after")
    if not native_evidence.valid_snapshot(after):
        return NO_DATA, "record has no valid candidate_after snapshot to compare against"
    current = native_evidence.repo_snapshot(repo)
    if current.get("error"):
        return NO_DATA, "current candidate state is unavailable: %s" % current["error"]
    ground_truth_stale = not native_evidence.same_snapshot(after, current)
    if not ground_truth_stale:
        return NO_DATA, "the recorded candidate matches the current repo state; nothing stale to catch in this fixture"
    verdict, detail = native_evidence.validate_record(doc, repo=repo, xcrun=xcrun)
    reasons = "; ".join(detail)
    if verdict == native_evidence.PASS:
        return "FAIL", "a genuinely stale candidate was accepted, not caught: " + reasons
    if verdict == native_evidence.NODATA:
        return NO_DATA, "cannot determine whether the stale candidate was rejected: " + reasons
    return "PASS", "a stale candidate was rejected, not reused: " + reasons


def check_mdm_precision_not_score_only(candidate_generation, risk_assessment=None):
    """Pilot claim: 'an MDM precision claim cannot rely only on similarity
    scores.' Calls merge_passport.compose_passport() -- the real control --
    and reads the claim as held only when a candidate carrying nothing but
    a similarity score cannot reach a validated risk field without risk
    evidence beyond that score, never assumed."""
    passport = merge_passport.compose_passport(
        master_id="assurance-graph-pilot-mdm-precision",
        golden_master_contract_record=None,
        candidate_generation=candidate_generation,
        risk_assessment=risk_assessment,
    )
    risk = passport["field_verdicts"]["risk"]
    if risk_assessment is None:
        if risk != "PASS":
            return "PASS", (
                "a candidate carrying only %s could not validate a risk field without "
                "risk evidence beyond it (risk field verdict: %s)"
                % (sorted(candidate_generation or {}), risk))
        return "FAIL", "a candidate with no risk evidence beyond its score still validated the risk field"
    if risk == "PASS":
        return "PASS", "risk evidence beyond the similarity score is present and validates: " + passport["risk"]["verdict_reason"]
    return "FAIL", "risk evidence was supplied but did not validate: " + passport["risk"]["verdict_reason"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="validate a claim record against assurance-graph-v1")
    check_parser.add_argument("record", help="path to the assurance graph claim JSON record")
    check_parser.add_argument("--schema", default=DEFAULT_SCHEMA)

    args = parser.parse_args(argv)

    if args.command == "check":
        try:
            record = CC.load_json(args.record, "assurance graph claim record")
            schema = CC.load_json(args.schema, "assurance graph schema")
        except CC.NoData as exc:
            print("NO-DATA: %s" % exc)
            return 2
        problems = check_record(record, schema)
        if problems:
            print("FAIL: %d problem(s)" % len(problems))
            for p in problems:
                print(" -", p)
            return 1
        overall = record.get("verdict", NO_DATA)
        print("%s validates as assurance-graph-v1, claim verdict: %s" % (args.record, overall))
        return exit_code_for_verdict(overall)

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    sys.exit(main())
