"""End to end demonstration of the master data claim audit loop.

This example drives the whole open source audit loop over a synthetic
matching run whose truth is known, prints every estimate next to that
truth, and then judges one hurried claim and one honest claim with the
shipped audit, coverage and identifier claim packs.

Run:
    python3 mdm_audit_demo.py
    python3 mdm_audit_demo.py --json
    python3 mdm_audit_demo.py --write-examples
    python3 mdm_audit_demo.py --coverage
    python3 mdm_audit_demo.py --selftest
"""

import argparse
import csv
import json
import math
import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _path in (_PARENT, _HERE):
    if _path and _path not in sys.path:
        sys.path.insert(0, _path)

import mdm_archetype  # noqa: E402
import mdm_audit  # noqa: E402
import mdm_eval  # noqa: E402
import pack_mdm_audit  # noqa: E402
import pack_mdm_coverage  # noqa: E402
import pack_mdm_identity  # noqa: E402


# ---------------------------------------------------------------------------
# Tiny local registry and Finding, matching the pack register API.
# ---------------------------------------------------------------------------

class _Registry(object):
    """Registry built for one judging run."""

    def __init__(self):
        self.entries = []

    def register(self, claim_type, fn):
        self.entries.append((claim_type, fn))


class _Finding(object):
    """One gate result."""

    def __init__(self, gate, verdict, detail=""):
        self.gate = gate
        self.verdict = verdict
        self.detail = detail


def _build_registry():
    registry = _Registry()
    pack_mdm_audit.register(registry, _Finding)
    pack_mdm_coverage.register(registry, _Finding)
    pack_mdm_identity.register(registry, _Finding)
    return registry


def _normalise_finding(item):
    gate = getattr(item, "gate", None)
    verdict = getattr(item, "verdict", None)
    detail = getattr(item, "detail", "")
    if gate is None and isinstance(item, (list, tuple)) and len(item) >= 2:
        gate = item[0]
        verdict = item[1]
        detail = item[2] if len(item) > 2 else ""
    if gate is None:
        return None
    return (gate, verdict, detail)


def _judge(claim, registry):
    """Call every registered fn(claim, Finding) and collect gate results."""
    collected = {}
    for _claim_type, fn in registry.entries:
        try:
            res = fn(claim, _Finding)
        except Exception as exc:  # pragma: no cover - defensive
            collected["M99.pack_error"] = (
                "NO-DATA",
                "pack raised %s: %s" % (type(exc).__name__, exc),
            )
            continue
        if res is None:
            continue
        if isinstance(res, _Finding):
            items = [res]
        elif isinstance(res, (list, tuple)):
            if (
                len(res) == 3
                and isinstance(res[0], str)
                and res[1] in ("PASS", "FAIL", "NO-DATA")
            ):
                items = [res]
            else:
                items = list(res)
        else:
            items = [res]
        for item in items:
            parsed = _normalise_finding(item)
            if parsed is None:
                continue
            gate, verdict, detail = parsed
            collected[gate] = (verdict, detail)
    verdicts = dict((g, v) for g, (v, _d) in collected.items())
    details = dict((g, d) for g, (_v, d) in collected.items())
    return verdicts, details


def _gate_sort_key(gate):
    head = gate.split(".", 1)[0]
    digits = "".join(ch for ch in head if ch.isdigit())
    return (int(digits) if digits else 10 ** 6, gate)


def _gate_by_prefix(vmap, prefix):
    for gate in vmap:
        if gate == prefix or gate.startswith(prefix + "."):
            return vmap[gate]
    return None


# ---------------------------------------------------------------------------
# The audit loop.
# ---------------------------------------------------------------------------

def _audit_cli(arguments):
    command = [sys.executable, os.path.join(_PARENT, "mdm_audit.py")] + arguments
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise ValueError("audit replay command failed: " + (result.stdout + result.stderr).strip())
    return json.loads(result.stdout)


def run(seed=11, margin=0.10, workdir=None, plan_seed=7):
    """Execute the exported CSV audit and bound replay, retaining explicit workdirs."""
    if workdir is None:
        with tempfile.TemporaryDirectory(prefix="mdm_audit_demo_") as temporary:
            return _run(seed, margin, temporary, plan_seed)
    os.makedirs(workdir, exist_ok=True)
    return _run(seed, margin, workdir, plan_seed)


def _run(seed, margin, workdir, plan_seed):
    gen = mdm_archetype.generate(seed)
    results_path, _truth_path = mdm_archetype.write_csvs(workdir, gen)
    plan_path = os.path.join(workdir, "plan.csv")
    snapshot_path = os.path.join(workdir, "audit.json")
    labelled_path = os.path.join(workdir, "plan-labelled.csv")
    audit = _audit_cli(["audit", results_path, "--plan", plan_path,
                        "--margin", str(margin), "--seed", str(plan_seed)])
    with open(snapshot_path, "w", encoding="utf-8") as stream:
        json.dump(audit, stream, indent=1, sort_keys=True)
        stream.write("\n")
    with open(plan_path, encoding="utf-8", newline="") as stream:
        plan = list(csv.DictReader(stream))
    labelled = mdm_archetype.label_plan(plan, gen["truth"])
    mdm_audit.write_plan(labelled, labelled_path)
    ev = _audit_cli(["evaluate", labelled_path, "--results", results_path,
                     "--audit", snapshot_path])
    rows = mdm_audit.read_results(results_path)

    # ----- overall precision over the reviewed MATCH strata ---------------
    # R1: stratified estimate over the labelled MATCH strata of the plan.
    match_strata = []
    for entry in (ev.get("strata") or []):
        name = str(entry.get("stratum", ""))
        labelled_n = int(entry.get("labelled", 0) or 0)
        if name.startswith("match:") and labelled_n >= 1:
            match_strata.append({
                "stratum": name,
                "population": int(entry.get("population", 0) or 0),
                "sampled": labelled_n,
                "positive": int(entry.get("positive", 0) or 0),
            })
    precision_raw = mdm_eval.stratified_estimate(match_strata)
    precision = {
        "estimate": float(precision_raw["estimate"]),
        "lo": float(precision_raw["lo"]),
        "hi": float(precision_raw["hi"]),
    }

    # ----- per pathway Wilson intervals (kept as they were) ---------------
    pw = ev.get("pathway_review", {}) or {}
    precision_by_pathway = {}
    for pathway in sorted(pw):
        v = pw[pathway]
        sampled = int(v.get("sampled", 0) or 0)
        positive = int(v.get("positive", 0) or 0)
        if sampled >= 1:
            lo, hi = mdm_eval.wilson_interval(positive, sampled)
            precision_by_pathway[pathway] = {
                "estimate": positive / float(sampled),
                "lo": float(lo),
                "hi": float(hi),
            }

    # ----- unmatched review, missed share and recall ----------------------
    ur = ev.get("unmatched_review", {}) or {}
    U = int(ur.get("population", 0) or 0)
    unmatched_sampled = int(ur.get("sampled", 0) or 0)
    matchable_missed = int(ur.get("matchable_missed", 0) or 0)
    reference_absent = int(ur.get("reference_absent", 0) or 0)

    if ur.get("coverage_state") == "NO-DATA":
        raise ValueError("NO-DATA: unmatched population is not fully reviewed by stratum")
    weighted = ur.get("matchable_missed_weighted")
    if weighted is not None:
        q = float(weighted.get("estimate", 0.0) or 0.0)
        q_lo = float(weighted.get("lo", 0.0) or 0.0)
        q_hi = float(weighted.get("hi", 0.0) or 0.0)
    elif unmatched_sampled > 0:
        q = matchable_missed / float(unmatched_sampled)
        q_lo, q_hi = mdm_eval.wilson_interval(matchable_missed, unmatched_sampled)
        q_lo = float(q_lo)
        q_hi = float(q_hi)
    else:
        q = 0.0
        q_lo = 0.0
        q_hi = 0.0

    if ur.get("reference_absent_weighted") is not None:
        coverage_gap_share = float(ur["reference_absent_weighted"]["estimate"])
    elif unmatched_sampled > 0:
        coverage_gap_share = reference_absent / float(unmatched_sampled)
    else:
        coverage_gap_share = 0.0

    status_counts = dict(audit["reconciliation"]["status_counts"])
    M = int(status_counts.get("MATCH", 0) or 0)

    p_hat = float(precision["estimate"])
    p_lo = float(precision["lo"])

    denom_est = p_hat * M + U * q
    recall_estimate = (p_hat * M / float(denom_est)) if denom_est > 0 else 0.0

    denom_lower = M + U * q_hi
    recall_range_lower = (p_lo * M / float(denom_lower)) if denom_lower > 0 else 0.0
    denom_upper = M + U * q_lo
    recall_range_upper = (M / float(denom_upper)) if denom_upper > 0 else 0.0
    recall_range = {
        "lower": float(recall_range_lower),
        "upper": float(recall_range_upper),
    }
    recall_bound = float(recall_range_upper)

    missed_share = {"estimate": q, "lo": q_lo, "hi": q_hi}

    # ----- shared report fragments ----------------------------------------
    n_rows = int(audit["n_rows"])
    match_n = int(status_counts.get("MATCH", 0) or 0)
    if n_rows > 0:
        matched_value = round(100.0 * match_n / float(n_rows), 2)
    else:
        matched_value = 0.0
    matched_percent = {
        "matched": {
            "value": matched_value,
            "numerator": match_n,
            "denominator": n_rows,
        }
    }

    segments = []
    for seg in (audit.get("segments") or []):
        segments.append({
            "segment": seg.get("segment", ""),
            "n": int(seg.get("n", 0) or 0),
            "matched": int(seg.get("matched", 0) or 0),
        })

    pathway_counts = dict(audit["reconciliation"]["pathway_counts"])
    pathway_counts_over = dict(pathway_counts)
    pathway_counts_over["key_phone"] = int(pathway_counts_over.get("key_phone", 0) or 0) + 9

    # ----- the hurried claim ----------------------------------------------
    overclaim = {
        "id": "demo-overclaim",
        "claim_type": "MASTER_DATA",
        "statement": "the matching run matched most outlets with high quality",
        "master_data": {
            "evaluation": {
                "claimed_precision": 0.99,
                "claimed_recall": 0.95,
            },
            "audit": {
                "reported": {
                    "n_input": n_rows + 17,
                    "status_counts": status_counts,
                    "pathway_counts": pathway_counts_over,
                    "percents": matched_percent,
                },
                "computed": audit,
                "precision_basis": "similarity",
                "verifier": {"kind": "llm"},
                "segments": segments,
                "segment_disclosure": False,
            },
        },
    }

    # ----- the honest claim -----------------------------------------------
    audited_precision = round(p_hat, 3)
    audited_recall = math.floor(recall_range["lower"] * 100) / 100

    hierarchy_edges = []
    for row in rows:
        hierarchy_edges.append([
            row.get("source_id", ""),
            row.get("segment", "") or "",
        ])
    chains = gen.get("chains") or []
    for chain in chains:
        if isinstance(chain, dict):
            cid = chain.get("id") or chain.get("chain_id") or chain.get("name") or ""
        else:
            cid = chain
        hierarchy_edges.append([cid, "GROUP"])

    both_match = 0
    model_only = 0
    human_only = 0
    both_nonmatch = 0
    for row in labelled:
        ver = (row.get("verifier") or "").strip().upper()
        raw_label = row.get("label")
        lab = "" if raw_label is None else str(raw_label).strip()
        if ver == "CONFIRMED":
            if lab == "1":
                both_match += 1
            elif lab == "0":
                model_only += 1
        elif ver == "REJECTED":
            if lab == "1":
                human_only += 1
            elif lab == "0":
                both_nonmatch += 1

    verifier_block = dict(ev.get("verifier") or {})
    verifier_block["kind"] = "llm"

    audited = {
        "id": "demo-audited",
        "claim_type": "MASTER_DATA",
        "statement": (
            "the matching run was reviewed and the reported quality "
            "matches the reviewed evidence"
        ),
        "master_data": {
            "evaluation": {
                "claimed_precision": audited_precision,
                "claimed_recall": audited_recall,
            },
            "audit": {
                "reported": {
                    "n_input": n_rows,
                    "status_counts": status_counts,
                    "pathway_counts": pathway_counts,
                    "percents": matched_percent,
                },
                "computed": audit,
                "precision_basis": "review",
                "plan_seed": plan_seed,
                "provenance_binding": ev["provenance_binding"],
                "pathway_review": ev.get("pathway_review", {}),
                "collisions_reviewed": True,
                "hubs_reviewed": True,
                "verifier": verifier_block,
                "segments": segments,
                "segment_disclosure": True,
                "unmatched_review": ev.get("unmatched_review", {}),
                "hierarchy": {
                    "edges": hierarchy_edges,
                    "retired": [],
                    "linkages": [],
                    "synthesized": ["GROUP"],
                },
                "labeller_confusion": {
                    "both_match": both_match,
                    "model_only": model_only,
                    "human_only": human_only,
                    "both_nonmatch": both_nonmatch,
                },
            },
        },
    }

    # ----- judge both claims ----------------------------------------------
    registry = _build_registry()
    over_verdicts, over_details = _judge(overclaim, registry)
    aud_verdicts, aud_details = _judge(audited, registry)

    return {
        "seed": seed,
        "plan_seed": plan_seed,
        "summary": gen["summary"],
        "audit": audit,
        "evaluation": ev,
        "provenance_binding": ev["provenance_binding"],
        "estimates": {
            "precision": precision,
            "precision_by_pathway": precision_by_pathway,
            "recall_estimate": float(recall_estimate),
            "recall_bound": float(recall_bound),
            "recall_range": recall_range,
            "coverage_gap_share": float(coverage_gap_share),
            "missed_share": missed_share,
        },
        "claims": {
            "overclaim": overclaim,
            "audited": audited,
        },
        "verdicts": {
            "overclaim": over_verdicts,
            "audited": aud_verdicts,
        },
        "details": {
            "overclaim": over_details,
            "audited": aud_details,
        },
    }


def coverage(seeds=range(1, 41), margin=0.10):
    """Run the loop over independent review draws and summarise coverage.

    Each seed gets its own plan seed (1000 + s) so the review draws are
    independent replications rather than repeats of one sampling pattern.
    """
    n = 0
    hits = 0
    recall_hits = 0
    errors = []
    for s in seeds:
        rep = run(seed=s, margin=margin, plan_seed=1000 + s)
        prec = (rep.get("estimates") or {}).get("precision") or {}
        truth = (rep.get("summary") or {}).get("true_precision")
        if truth is None:
            continue
        n += 1
        lo = prec.get("lo")
        hi = prec.get("hi")
        est = prec.get("estimate")
        if lo is not None and hi is not None and lo <= truth <= hi:
            hits += 1
        if est is not None:
            errors.append(float(est) - float(truth))

        rr = (rep.get("estimates") or {}).get("recall_range") or {}
        true_recall = (rep.get("summary") or {}).get("true_recall")
        rr_lo = rr.get("lower")
        rr_hi = rr.get("upper")
        if (
            true_recall is not None
            and rr_lo is not None
            and rr_hi is not None
            and rr_lo <= true_recall <= rr_hi
        ):
            recall_hits += 1

    mean_error = (sum(errors) / float(n)) if n else 0.0
    return {
        "n": int(n),
        "hits": int(hits),
        "coverage": (hits / float(n)) if n else 0.0,
        "mean_error": float(mean_error),
        "recall_range_coverage": (recall_hits / float(n)) if n else 0.0,
    }


# ---------------------------------------------------------------------------
# Rendering.
# ---------------------------------------------------------------------------

def _fmt(value):
    if value is None:
        return "-"
    try:
        return "%.4f" % float(value)
    except (TypeError, ValueError):
        return str(value)


def render(report):
    """Return the human readable report."""
    summary = report.get("summary") or {}
    estimates = report.get("estimates") or {}
    audit = report.get("audit") or {}

    lines = []
    lines.append("MASTER DATA AUDIT DEMO")
    lines.append(
        "seed %s, source records %s, audit rows %s"
        % (report.get("seed"), summary.get("n_source"), audit.get("n_rows"))
    )
    lines.append("")

    header = "%-40s %-34s %s" % ("estimate", "interval", "truth")
    lines.append(header)
    lines.append("-" * len(header))

    precision = estimates.get("precision") or {}
    lines.append("%-40s %-34s %s" % (
        "overall precision",
        "%s [%s, %s]" % (
            _fmt(precision.get("estimate")),
            _fmt(precision.get("lo")),
            _fmt(precision.get("hi")),
        ),
        _fmt(summary.get("true_precision")),
    ))

    true_by_pathway = summary.get("true_precision_by_pathway") or {}
    by_pathway = estimates.get("precision_by_pathway") or {}
    for pathway in sorted(by_pathway):
        item = by_pathway[pathway]
        lines.append("%-40s %-34s %s" % (
            "precision, pathway %s" % pathway,
            "%s [%s, %s]" % (
                _fmt(item.get("estimate")),
                _fmt(item.get("lo")),
                _fmt(item.get("hi")),
            ),
            _fmt(true_by_pathway.get(pathway)),
        ))

    lines.append("%-40s %-34s %s" % (
        "recall, estimate",
        "%s [-, -]" % _fmt(estimates.get("recall_estimate")),
        _fmt(summary.get("true_recall")),
    ))
    lines.append("%-40s %-34s %s" % (
        "recall, bound",
        "%s [-, -]" % _fmt(estimates.get("recall_bound")),
        _fmt(summary.get("true_recall")),
    ))

    rr = estimates.get("recall_range") or {}
    lines.append("%-40s %-34s %s" % (
        "recall, conservative range",
        "[%s, %s]" % (_fmt(rr.get("lower")), _fmt(rr.get("upper"))),
        _fmt(summary.get("true_recall")),
    ))

    missed = estimates.get("missed_share") or {}
    lines.append("%-40s %-34s %s" % (
        "unmatched, missed share",
        "%s [%s, %s]" % (
            _fmt(missed.get("estimate")),
            _fmt(missed.get("lo")),
            _fmt(missed.get("hi")),
        ),
        "-",
    ))
    lines.append("%-40s %-34s %s" % (
        "unmatched, coverage gap share",
        "%s [-, -]" % _fmt(estimates.get("coverage_gap_share")),
        "-",
    ))

    lines.append("")

    over_map = (report.get("verdicts") or {}).get("overclaim") or {}
    aud_map = (report.get("verdicts") or {}).get("audited") or {}
    gates = sorted(set(over_map) | set(aud_map), key=_gate_sort_key)
    for gate in gates:
        over = over_map.get(gate, "NO-DATA")
        aud = aud_map.get(gate, "NO-DATA")
        lines.append("%-32s hurried: %-7s honest: %s" % (gate, over, aud))

    binding = report.get("provenance_binding") or {}
    lines.append("")
    lines.append("Executed export and plan replay: %s; source authenticity: %s" %
                 (binding.get("state", "NO-DATA"), binding.get("source_authenticity", "NO-DATA")))
    lines.append("Replay checks export bytes and plan membership; labels are not verified.")
    lines.append("")
    lines.append(
        "Coverage of the precision interval over 40 independent review "
        "draws is available with --coverage."
    )
    lines.append(
        "A person decides release and acceptance; the tool only says what "
        "the evidence supports."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Example claim files.
# ---------------------------------------------------------------------------

def _write_examples(report, output_dir=None):
    claims = report.get("claims") or {}
    pairs = [
        ("example-mdm-audit-overclaim.json", claims.get("overclaim")),
        ("example-mdm-audit-repaired.json", claims.get("audited")),
    ]
    destination = _HERE if output_dir is None else output_dir
    os.makedirs(destination, exist_ok=True)
    written = []
    for name, payload in pairs:
        path = os.path.join(destination, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False))
            fh.write("\n")
        written.append(path)
    return written


# ---------------------------------------------------------------------------
# Selftest.
# ---------------------------------------------------------------------------

def _selftest():
    failures = []

    def check(condition, message):
        if not condition:
            failures.append(message)

    report = run(seed=11)

    over_map = (report.get("verdicts") or {}).get("overclaim") or {}
    aud_map = (report.get("verdicts") or {}).get("audited") or {}

    for prefix in ("M23", "M24", "M25", "M26", "M27"):
        verdict = _gate_by_prefix(over_map, prefix)
        check(verdict == "FAIL", "overclaim %s expected FAIL got %r" % (prefix, verdict))

    for prefix in ("M23", "M24", "M25", "M26", "M27", "M28", "M29"):
        verdict = _gate_by_prefix(aud_map, prefix)
        check(
            verdict != "FAIL",
            "audited %s should not FAIL, got %r" % (prefix, verdict),
        )

    check(_gate_by_prefix(aud_map, "M31") == "NO-DATA", "absent identifiers remain NO-DATA")
    check(_gate_by_prefix(aud_map, "M32") == "PASS", "supplied provenance matches replay inputs")
    check(report["provenance_binding"]["state"] == "PASS", "executed replay passed")
    check(report["provenance_binding"]["source_authenticity"] == "NO-DATA", "replay does not authenticate source")

    cov = coverage(range(1, 21))
    check(
        cov.get("coverage", 0.0) >= 0.8,
        "coverage over 20 draws was %r, expected at least 0.8" % cov.get("coverage"),
    )
    check(
        abs(cov.get("mean_error", 1.0)) <= 0.02,
        "mean_error over 20 draws was %r, expected within 0.02" % cov.get("mean_error"),
    )
    check(cov.get("n", 0) == 20, "coverage should have run 20 seeds, got %r" % cov.get("n"))

    claims = report.get("claims") or {}
    check(
        (claims.get("overclaim") or {}).get("id") == "demo-overclaim",
        "overclaim id mismatch",
    )
    check(
        (claims.get("audited") or {}).get("id") == "demo-audited",
        "audited id mismatch",
    )
    check(
        (claims.get("overclaim") or {}).get("master_data", {})
        .get("evaluation", {}).get("claimed_precision") == 0.99,
        "overclaim claimed precision should be 0.99",
    )

    audit = report.get("audit") or {}
    check(
        (audit.get("reconciliation") or {}).get("consistent") is True,
        "audit reconciliation should be consistent on synthetic data",
    )
    check(int(audit.get("n_rows", 0) or 0) > 0, "audit should have rows")

    est = report.get("estimates") or {}
    p_est = (est.get("precision") or {}).get("estimate")
    check(
        p_est is not None and 0.0 <= p_est <= 1.0,
        "precision estimate out of range: %r" % (p_est,),
    )
    check(
        0.0 <= est.get("recall_estimate", -1.0) <= 1.0,
        "recall estimate out of range",
    )
    check(
        0.0 <= est.get("recall_bound", -1.0) <= 1.0,
        "recall bound out of range",
    )

    rr = est.get("recall_range") or {}
    check(
        isinstance(rr, dict) and "lower" in rr and "upper" in rr,
        "estimates should have recall_range with lower and upper",
    )
    check(
        est.get("recall_bound") == rr.get("upper"),
        "recall_bound should equal recall_range upper",
    )

    status_counts = (audit.get("reconciliation") or {}).get("status_counts") or {}
    M = int(status_counts.get("MATCH", 0) or 0)
    ur = ((report.get("evaluation") or {}).get("unmatched_review") or {})
    U = int(ur.get("population", 0) or 0)
    q_est = (est.get("missed_share") or {}).get("estimate")
    q_lo = (est.get("missed_share") or {}).get("lo")
    q_hi = (est.get("missed_share") or {}).get("hi")
    p_lo = (est.get("precision") or {}).get("lo")
    if all(v is not None for v in (q_est, q_lo, q_hi, p_est, p_lo)):
        expected_lower = p_lo * M / (M + U * q_hi) if (M + U * q_hi) > 0 else 0.0
        expected_upper = M / (M + U * q_lo) if (M + U * q_lo) > 0 else 0.0
        check(
            abs(rr.get("lower", -1) - expected_lower) < 1e-12,
            "recall_range lower formula mismatch",
        )
        check(
            abs(rr.get("upper", -1) - expected_upper) < 1e-12,
            "recall_range upper formula mismatch",
        )
        expected_recall = (
            p_est * M / (p_est * M + U * q_est)
            if (p_est * M + U * q_est) > 0
            else 0.0
        )
        check(
            abs(est.get("recall_estimate", -1) - expected_recall) < 1e-12,
            "recall_estimate formula mismatch",
        )

    aud_claim = claims.get("audited") or {}
    claimed_recall = (
        (aud_claim.get("master_data") or {}).get("evaluation") or {}
    ).get("claimed_recall")
    expected_claimed_recall = math.floor(rr.get("lower", 0.0) * 100) / 100
    check(
        claimed_recall == expected_claimed_recall,
        "audited claimed_recall should match recall_range lower floor",
    )

    check(
        "recall_range_coverage" in cov,
        "coverage should report recall_range_coverage",
    )
    rr_cov = cov.get("recall_range_coverage")
    check(
        isinstance(rr_cov, float) and 0.0 <= rr_cov <= 1.0,
        "recall_range_coverage should be a float in [0, 1], got %r" % (rr_cov,),
    )

    aud_audit = ((claims.get("audited") or {}).get("master_data") or {}).get("audit") or {}
    check(aud_audit.get("segment_disclosure") is True, "audited segment disclosure")
    check(aud_audit.get("precision_basis") == "review", "audited precision basis")
    check(
        ((claims.get("overclaim") or {}).get("master_data") or {})
        .get("audit", {}).get("precision_basis") == "similarity",
        "overclaim precision basis",
    )

    if failures:
        for message in failures:
            sys.stderr.write("FAIL: %s\n" % message)
        return 1
    print("SELFTEST PASS")
    return 0


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Master data claim audit end to end demonstration.",
    )
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--plan-seed", type=int, default=7, dest="plan_seed")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--write-examples", action="store_true", dest="write_examples")
    parser.add_argument("--output-dir", help="destination for examples and their replay inputs")
    parser.add_argument("--coverage", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()

    if args.coverage:
        print(json.dumps(coverage(), indent=1, sort_keys=True))
        return 0

    if args.output_dir and not args.write_examples:
        parser.error("--output-dir requires --write-examples")
    report = run(seed=args.seed, plan_seed=args.plan_seed,
                 workdir=args.output_dir if args.write_examples else None)

    if args.write_examples:
        for path in _write_examples(report, output_dir=args.output_dir):
            sys.stderr.write("wrote %s\n" % path)

    if args.as_json:
        print(json.dumps(report, indent=1, sort_keys=True))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
