#!/usr/bin/env python3
"""WBS-40.11 MDM Canary Smoke: prove the ten WBS-40 (Golden Master / MDM)
sibling modules that landed tonight actually connect, end to end, on one
synthetic canary entity-resolution journey. The WBS-40 mirror of
scripts/canary_pipeline_smoke.py (WBS-30.10): same idea, applied to the
Golden Master / MDM pipeline instead of the mobile pipeline.

This is NOT an eleventh domain module. It builds a fake, generic golden
master journey (master_id="mdm-canary-smoke", two synthetic source
systems, no real client data anywhere) and threads it through the REAL
functions of every sibling module in the roadmap's own pipeline order:

  golden_master_contract.check()          (WBS-40.01)
    -> master_source_snapshot.build_snapshot()   (WBS-40.02, two synthetic
       .xlsx fixtures, same fixture pattern test_master_source_snapshot.py
       uses)
    -> normalization_trace.trace()               (WBS-40.03, real default
       chain, real corporate-form/long-vowel transforms)
    -> matcher_boundary.audit_matcher_output()    (WBS-40.04, normalizer
       version fingerprint read live off the real chain, never hardcoded)
    -> risk_review_orchestrator.route_batch()     (WBS-40.05, routed on the
       contract's own real cost class and threshold fields)
    -> clerical_review_plan.build_sampling_plan() (WBS-40.06, sampling only
       the real CLERICAL_REVIEW bucket route_batch produced)
    -> survivorship_lineage.resolve_field()       (WBS-40.09, real per-field
       winner decisions over the same two synthetic source systems)
    -> reversibility_gate.build_reversible_action()/evaluate_merge_candidate()
       (WBS-40.08, a real rollback cycle run twice for idempotence)
    -> merge_passport.compose_passport()          (WBS-40.07, the terminal
       composer; every field either a real sibling record or a real sibling
       function's real output)
    -> publish_reconciliation.verify_reconciliation() (WBS-40.10, downstream
       of but never re-reading the passport, per its own documented
       boundary: authorizing a publish and reconciling one are two distinct
       questions)

Every stage's REAL output is fed directly into the next real function's
real input, no reshaping step sits between them, except where a shape
genuinely does not fit: see THE FINDING below (a real adjacent-stage
mismatch this run surfaced between survivorship_lineage's real per-field
decision shape and merge_passport's own survivorship_decision shape),
fixed in merge_passport.py itself, not papered over here.

Python 3.9, standard library only. No network. No file under this
repository's docs/schema or any client's own estate is read; every fixture
is synthetic/generic and a real client's data is never referenced.
"""
import datetime
import hashlib
import json
import os
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import contract_check as CC  # noqa: E402
import golden_master_contract as GMC  # noqa: E402
import master_source_snapshot as MSS  # noqa: E402
import normalization_trace as NT  # noqa: E402
import matcher_boundary as MB  # noqa: E402
import risk_review_orchestrator as RRO  # noqa: E402
import clerical_review_plan as CRP  # noqa: E402
import survivorship_lineage as SL  # noqa: E402
import reversibility_gate as RG  # noqa: E402
import merge_passport as MP  # noqa: E402
import publish_reconciliation as PR  # noqa: E402

MASTER_ID = "mdm-canary-smoke"
SOURCE_A = "synthetic-crm"
SOURCE_B = "synthetic-pos"

# A fake, schema-valid outcome-contract-v1 record, same shape
# scripts/test_golden_master_contract.py's VALID_OUTCOME fixture uses.
SYNTHETIC_OUTCOME = {
    "schema_version": "outcome-contract-v1", "project": {
        "project_id": "mdm-canary-smoke", "name": "mdm-canary-smoke",
        "provenance": {"project_id": "ask", "name": "ask"}},
    "language": "en", "question": "does the synthetic golden master canary journey work",
    "success_checks": [{"id": "s", "command": "true", "expect": "exit-0"}],
    "must_answer": [], "affected_products": ["brother"], "ticket": None,
    "audit": {"required": False, "manifest": None}, "persona": "developer",
    "state": "contracted", "receipts": [], "questions": [], "history": [],
    "decision": None,
}

# A fake, schema-valid golden-master-contract-v1 record. Generic customer/
# location entity, two fake source systems, nothing resembling a real
# client's own data anywhere. false_merge_cost_class is deliberately
# MEDIUM (not in risk_review_orchestrator.HIGH_RISK_COST_CLASSES) so this
# canary run can exercise all three real routing buckets on score alone,
# rather than every candidate being forced to CLERICAL_REVIEW.
SYNTHETIC_MASTER = {
    "schema_version": "golden-master-contract-v1", "master_id": MASTER_ID,
    "entity_type": "customer/location",
    "business_purpose": "resolve duplicate customer/location master records "
                         "across two synthetic source systems, for testing only",
    "source_systems": [SOURCE_A, SOURCE_B], "downstream_consumers": ["synthetic-dwh"],
    "critical_attributes": ["customer_name", "phone"], "cardinality_constraints": [],
    "false_merge_cost_class": "MEDIUM", "missed_match_cost_class": "LOW",
    "review_policy": "clerical review for the ambiguous middle band",
    "merge_threshold": 0.9, "review_threshold": 0.5,
    "survivorship_policy_ref": "docs/decisions/five-layer-optimisation-1.0.17-2026-09-13.html",
    "rollback_required": True, "quality_claim_ids": [],
    "locale_profiles": ["ja-JP"], "publish_authority": "HUMAN",
}

# Two synthetic company-name spellings that should converge to the same
# blocking key once normalization_trace's real corporate-form transform
# strips both the leading and trailing legal-form variants.
SOURCE_A_NAME = "株式会社テストキャナリー"
SOURCE_B_NAME = "テストキャナリー株式会社"
SOURCE_A_PHONE = "03-1234-5678"
SOURCE_B_PHONE = ""  # deliberately empty: exercises most_complete_value_wins for real

WORKBOOK_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Records" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>
"""
RELS_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>
"""


def _sheet_xml(row_count):
    rows = "".join(
        '<row r="%d"><c r="A%d" t="s"><v>0</v></c></row>' % (i, i)
        for i in range(1, row_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<dimension ref="A1:A%d"/><sheetData>%s</sheetData></worksheet>' % (row_count, rows)
    ).encode("utf-8")


def _make_synthetic_xlsx(path, row_count):
    """A minimal, structurally-valid synthetic .xlsx: one sheet, a handful
    of blank-content rows. Same pattern test_master_source_snapshot.py's
    make_fixture_xlsx uses (workbook.xml + rels + one worksheet part),
    trimmed to one sheet since this smoke needs a real file to hash and
    count rows on, not a shared-strings exercise."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("xl/workbook.xml", WORKBOOK_XML)
        zf.writestr("xl/_rels/workbook.xml.rels", RELS_XML)
        zf.writestr("xl/worksheets/sheet1.xml", _sheet_xml(row_count))


def run_pipeline():
    """Thread the synthetic canary golden-master journey through every real
    WBS-40 sibling function, in the roadmap's own pipeline order. Returns
    (passport, reconciliation, tmp)."""
    tmp = tempfile.mkdtemp(prefix="mdm-canary-smoke-")

    # Stage 1: Golden Master Contract (WBS-40.01), layered on a real
    # outcome contract via outcome_contract_ref.
    outcome_path = os.path.join(tmp, "outcome.json")
    with open(outcome_path, "w", encoding="utf-8") as fh:
        json.dump(SYNTHETIC_OUTCOME, fh)
    contract = dict(SYNTHETIC_MASTER, outcome_contract_ref=outcome_path)
    schema = CC.load_json(GMC.DEFAULT_SCHEMA, "golden master contract schema")
    problems = GMC.check(contract, schema)
    assert problems == [], "synthetic golden master contract must validate: %s" % problems

    # Stage 2: Source Snapshot (WBS-40.02) -- two REAL structural-only .xlsx
    # snapshots, one per synthetic source system named on the contract.
    path_a = os.path.join(tmp, "synthetic-crm-export.xlsx")
    path_b = os.path.join(tmp, "synthetic-pos-export.xlsx")
    _make_synthetic_xlsx(path_a, row_count=4)
    _make_synthetic_xlsx(path_b, row_count=6)
    snapshot_a = MSS.build_snapshot(path_a, source_system_id=SOURCE_A)
    snapshot_b = MSS.build_snapshot(path_b, source_system_id=SOURCE_B)
    assert snapshot_a["verdict"] == "PASS", snapshot_a["verdict_reason"]
    assert snapshot_b["verdict"] == "PASS", snapshot_b["verdict_reason"]

    # Stage 3: Normalization Trace (WBS-40.03) -- real default chain, both
    # source spellings of the same company name and phone number.
    trace_name_a = NT.trace(SOURCE_A_NAME, NT.default_chain(), "ja-JP")
    trace_name_b = NT.trace(SOURCE_B_NAME, NT.default_chain(), "ja-JP")
    # SOURCE_B_PHONE is deliberately blank (exercised for real at Stage 7's
    # most_complete_value_wins comparison below); not traced here, since an
    # empty raw value has no matching key worth auditing as a normalization
    # step and normalization_trace.py itself needs a non-empty value to
    # demonstrate a real transform running end to end.
    trace_phone_a = NT.trace(SOURCE_A_PHONE, NT.default_chain(), "ja-JP")
    # Real proof the normalizer actually connects blocking: two differently
    # spelled source names (legal form leading vs. trailing) converge on the
    # exact same matching key once the real corporate-form transform runs.
    assert trace_name_a["matching_key"] == trace_name_b["matching_key"] == "テストキャナリー", (
        trace_name_a["matching_key"], trace_name_b["matching_key"])

    # Stage 4: Matcher Boundary (WBS-40.04) -- MatcherOutput records whose
    # normalizer_version is the REAL live fingerprint of the same chain
    # Stage 3 just ran, read off normalization_trace directly, never
    # hardcoded.
    normalizer_version = MB.normalizer_version_fingerprint()
    current_versions = MB.current_normalizer_versions()

    def matcher_output(candidate_id, score):
        return {
            "source_id": SOURCE_A,
            "candidate_id": candidate_id,
            "reference_id": SOURCE_B,
            "pathway": "blocking+ml",
            "score": score,
            "model_version": "generic-matcher-v1",
            "normalizer_version": normalizer_version,
            "blocker": trace_name_a["matching_key"],
            "verifier_state": "unverified",
            "reference_snapshot": "sha256:" + hashlib.sha256(b"synthetic-reference-state").hexdigest(),
        }

    matcher_outputs = {
        "cand-accept": matcher_output("cand-accept", 0.95),
        "cand-review": matcher_output("cand-review", 0.70),
        "cand-reject": matcher_output("cand-reject", 0.30),
    }
    for cid, output in matcher_outputs.items():
        audit = MB.audit_matcher_output(output, current_versions)
        assert audit["verdict"] == MB.PASS, "%s: %s" % (cid, audit["problems"])

    # Stage 5: Risk-Directed Review Orchestrator (WBS-40.05) -- routed on
    # the contract's OWN real cost-class and threshold fields (roadmap's
    # documented connection), plus one deliberately malformed candidate
    # (an injected, unrecognized cost class) to prove REFUSED is a real,
    # non-silent bucket, not just the happy path.
    routing_candidates = [
        {
            "candidate_id": cid,
            "score": output["score"],
            "false_merge_cost_class": contract["false_merge_cost_class"],
            "missed_match_cost_class": contract["missed_match_cost_class"],
            "merge_threshold": contract["merge_threshold"],
            "review_threshold": contract["review_threshold"],
        }
        for cid, output in matcher_outputs.items()
    ]
    routing_candidates.append({
        "candidate_id": "cand-poisoned",
        "score": 0.99,
        "false_merge_cost_class": "suspicious-injected-value",
        "missed_match_cost_class": contract["missed_match_cost_class"],
        "merge_threshold": contract["merge_threshold"],
        "review_threshold": contract["review_threshold"],
    })
    decisions, summary = RRO.route_batch(routing_candidates)
    by_id = {d["candidate_id"]: d for d in decisions}
    assert by_id["cand-accept"]["routing"] == "AUTO_ACCEPT", by_id["cand-accept"]
    assert by_id["cand-review"]["routing"] == "CLERICAL_REVIEW", by_id["cand-review"]
    assert by_id["cand-reject"]["routing"] == "AUTO_REJECT", by_id["cand-reject"]
    assert by_id["cand-poisoned"]["routing"] == "NO-DATA", by_id["cand-poisoned"]
    assert summary["CLERICAL_REVIEW"] == 1 and summary["NO-DATA"] == 1, summary

    # Stage 6: Clerical Review Sampling Plan (WBS-40.06) -- samples ONLY
    # the real CLERICAL_REVIEW bucket route_batch just produced, merged
    # with the matcher's own pathway field as the module's own docstring
    # says a caller must (risk_review_orchestrator never reads pathway
    # itself but does not drop it either).
    clerical_bucket = [
        dict(matcher_outputs["cand-review"], **by_id["cand-review"],
             false_merge_cost_class=contract["false_merge_cost_class"],
             missed_match_cost_class=contract["missed_match_cost_class"])
    ]
    sampling_plan = CRP.build_sampling_plan(clerical_bucket, seed=42)
    assert sampling_plan["population"] == 1
    assert sampling_plan["strata"][0]["sampled_candidate_ids"] == ["cand-review"], sampling_plan
    assert sampling_plan["precision"] == "NO-DATA"  # structural refusal, never a guess

    # Stage 7: Survivorship Lineage (WBS-40.09) -- real per-field winner
    # decisions over the same two synthetic source systems named on the
    # contract and the snapshots above.
    name_decision, name_lineage = SL.resolve_field(
        "customer_name",
        {
            SOURCE_A: {"value": SOURCE_A_NAME, "timestamp": "2026-01-05T00:00:00Z"},
            SOURCE_B: {"value": SOURCE_B_NAME, "timestamp": "2026-03-10T00:00:00Z"},
        },
        SL.MOST_RECENT_SOURCE_WINS,
    )
    phone_decision, phone_lineage = SL.resolve_field(
        "phone",
        {
            SOURCE_A: {"value": SOURCE_A_PHONE},
            SOURCE_B: {"value": SOURCE_B_PHONE},
        },
        SL.MOST_COMPLETE_VALUE_WINS,
    )
    assert name_decision["winner_source_id"] == SOURCE_B  # more recent timestamp
    assert phone_decision["winner_source_id"] == SOURCE_A  # only non-empty value
    survivorship_decisions = {"customer_name": name_decision, "phone": phone_decision}

    # Stage 8: Reversibility Gate (WBS-40.08) -- a real merge -> rollback ->
    # verify cycle, run twice for idempotence, consuming Stage 7's real
    # decisions exactly as reversibility_gate.py's own docstring says it
    # must ("this gate does not decide winners ... it only consumes a
    # decision already made").
    pre_merge_records = {
        SOURCE_A: {"customer_name": SOURCE_A_NAME, "phone": SOURCE_A_PHONE},
        SOURCE_B: {"customer_name": SOURCE_B_NAME, "phone": SOURCE_B_PHONE},
    }
    reversibility_candidate = RG.build_reversible_action(
        affected_entity_set=[SOURCE_A, SOURCE_B],
        pre_merge_records=pre_merge_records,
        survivorship_decisions=survivorship_decisions,
        downstream_propagation_status="not_propagated",
    )
    rg_verdict, rg_reason = RG.evaluate_merge_candidate(reversibility_candidate)
    assert rg_verdict == "PASS", rg_reason

    # Stage 9: Merge Passport (WBS-40.07) -- the terminal composer. Every
    # field either a real sibling record (contract, snapshots, traces,
    # reversibility candidate) or real sibling function output.
    #
    # THE FINDING: survivorship_decisions above is Stage 7's REAL output,
    # {field: {"value": ..., "winner_source_id": ...}} -- exactly the shape
    # reversibility_gate.py already consumes (proven at Stage 8). Fed
    # straight into merge_passport.compose_passport()'s survivorship_decision
    # parameter with no reshaping, this is a genuine adjacent-stage shape
    # mismatch: merge_passport.py's own _compose_survivorship originally
    # only understood a flat {"attributes": [{"value", "source_system"}]}
    # view-layer shape, written before WBS-40.09 (survivorship_lineage.py)
    # existed to produce real evidence for this field. Fed directly, the
    # real per-field decision dict has no top-level "attributes" key, which
    # merge_passport's own _compose_struct correctly reports as FAIL rather
    # than a silent PASS or a crash -- exactly the same shape of finding
    # scripts/canary_pipeline_smoke.py (WBS-30.10) surfaced between
    # release_state_tracker and journey_passport. Fixed here in
    # merge_passport.py itself (_normalize_survivorship_decision), which
    # now accepts survivorship_lineage's real per-field shape in addition
    # to the original flat evidence shape, so a real WBS-40.09 decision
    # composes cleanly instead of a false FAIL.
    passport = MP.compose_passport(
        master_id=MASTER_ID,
        golden_master_contract_record=contract,
        source_snapshots=[snapshot_a, snapshot_b],
        normalization_traces=[trace_name_a, trace_name_b, trace_phone_a],
        candidate_generation={
            "pathway": matcher_outputs["cand-accept"]["pathway"],
            "model": matcher_outputs["cand-accept"]["model_version"],
            "score": matcher_outputs["cand-accept"]["score"],
        },
        risk_assessment={
            "false_merge_cost": contract["false_merge_cost_class"],
            "shared_key_hub": False,
            "cluster_size_after": 2,
            "bridge_edge": False,
        },
        review_record={
            "required": True,
            "result": "clerical review sample cand-review approved (synthetic)",
        },
        reversibility_candidate=reversibility_candidate,
        survivorship_decision=survivorship_decisions,
        source_entities=[SOURCE_A],
        reference_entities=[SOURCE_B],
        # evidence: no sibling module produces this field yet, left
        # unsupplied so completeness reports it as honest NO-DATA, never a
        # fabricated PASS.
    )

    # Stage 10: Publish Reconciliation (WBS-40.10) -- downstream of, but per
    # its own documented boundary never re-reading, the passport above.
    # Caller-supplied post-publish evidence, six of seven checks real and
    # consistent, duplication_recurrence left honestly unsupplied (this
    # canary run tracks no prior resolved-duplicate population to check
    # recurrence against).
    reconciliation = PR.verify_reconciliation(
        master_id=MASTER_ID,
        counts_evidence={"attempted": 1, "published": 1, "rejected": 0},
        rejected_entities_evidence=[],
        downstream_reconciliation_evidence={
            "synthetic-dwh": {"confirmed": True, "receipt_id": "rcpt-001"},
        },
        schema_expectation_evidence={
            "expected_schema": "golden-master-publish-v1",
            "observed_schema": "golden-master-publish-v1",
        },
        orphan_references_evidence={"checked_references": ["ref-1", "ref-2"], "orphans": []},
        duplication_recurrence_evidence=None,
        publish_timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )

    return passport, reconciliation, tmp


def main():
    passport, reconciliation, tmp = run_pipeline()
    print(json.dumps({"merge_passport": passport, "publish_reconciliation": reconciliation},
                      indent=2, sort_keys=True))
    print("---", file=sys.stderr)
    print("merge_passport.verdict: %s" % passport["verdict"], file=sys.stderr)
    print("publish_reconciliation: %s" % reconciliation["completeness"]["headline"], file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
