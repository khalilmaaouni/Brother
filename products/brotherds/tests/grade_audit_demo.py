"""Grader for examples/mdm_audit_demo.py: the loop end to end, checked against ground truth."""
import json, math, os, subprocess, sys, tempfile

sys.path.insert(0, "products/brotherds/examples")
sys.path.insert(0, "products/brotherds")
bad = []
try:
    import mdm_audit_demo as D
    import mdm_eval
except Exception as e:
    print("CHECK FAIL: import: %r" % e); print("1 CHECK FAILURE(S)"); sys.exit(1)

rep = D.run(seed=11)
try:
    json.dumps(rep)
except Exception as e:
    bad.append("report is not JSON-serializable: %r" % e)
ov, au = rep["verdicts"]["overclaim"], rep["verdicts"]["audited"]
for g in ["M23.report_reconciliation", "M24.assignment_uniqueness", "M25.pathway_precision", "M26.verifier_evidence", "M27.segment_disparity"]:
    if ov.get(g) != "FAIL":
        bad.append("overclaim %s is %r, want FAIL (%s)" % (g, ov.get(g), rep["details"]["overclaim"].get(g)))
if ov.get("M28.coverage_split") != "NO-DATA":
    bad.append("overclaim M28 is %r, want NO-DATA (no unmatched sample)" % ov.get("M28.coverage_split"))
for g in ["M23.report_reconciliation", "M24.assignment_uniqueness", "M25.pathway_precision", "M26.verifier_evidence",
          "M27.segment_disparity", "M28.coverage_split", "M29.hierarchy_integrity"]:
    if au.get(g) != "PASS":
        bad.append("audited %s is %r, want PASS (%s)" % (g, au.get(g), rep["details"]["audited"].get(g)))
# M30 must equal the grader's own kappa bound on the claim's confusion counts
lc = rep["claims"]["audited"]["master_data"]["audit"]["labeller_confusion"]
a, b, c, d = lc["both_match"], lc["model_only"], lc["human_only"], lc["both_nonmatch"]
n = a + b + c + d
if n >= 50:
    po = (a + d) / n; pe = ((a + b) * (a + c) + (c + d) * (b + d)) / n ** 2
    k = (po - pe) / (1 - pe); lo = k - 1.96 * math.sqrt(po * (1 - po) / (n * (1 - pe) ** 2))
    want30 = "PASS" if lo >= 0.6 else "FAIL"
    if au.get("M30.agreement_bound") != want30:
        bad.append("audited M30 %r, grader says %s (kappa %.3f lower %.3f)" % (au.get("M30.agreement_bound"), want30, k, lo))
# confusion counts are the labelled verifier rows
ev = rep["evaluation"]
cr, rr = ev["verifier"]["confirmed_review"], ev["verifier"]["rejected_review"]
if (a + b, a, c + d, c) != (cr["sampled"], cr["positive"], rr["sampled"], rr["positive"]):
    bad.append("labeller_confusion does not match the labelled verifier samples")
# estimates against truth
s, est = rep["summary"], rep["estimates"]
pr = est["precision"]
# A nominal 95 percent interval is judged over independent replications below,
# rather than requiring one fixed seed to cover the truth.
strata = [{"population": x["population"], "sampled": x["labelled"], "positive": x["positive"]}
          for x in ev["strata"] if x["stratum"].startswith("match:") and x["labelled"] >= 1]
ref = mdm_eval.stratified_estimate(strata)
if abs(pr["estimate"] - ref["estimate"]) > 1e-9 or abs(pr["hi"] - ref["hi"]) > 1e-9:
    bad.append("precision estimate differs from stratified_estimate over the labelled MATCH strata")
cov = D.coverage(seeds=range(1, 41))
if not (cov.get("n") == 40 and cov.get("coverage", 0) >= 0.85 and abs(cov.get("mean_error", 1)) <= 0.01):
    bad.append("coverage study over 40 independent seeds: %r (want coverage >= 0.85, |mean_error| <= 0.01)" % (cov,))
if not est["recall_bound"] >= est["recall_estimate"] - 1e-12:
    bad.append("recall estimate above its own bound")
ac = rep["claims"]["audited"]["master_data"]["evaluation"]
if ac["claimed_recall"] > est["recall_bound"] + 1e-12:
    bad.append("audited claimed recall above the bound")
if abs(est["recall_estimate"] - s["true_recall"]) > 0.15:
    bad.append("recall estimate %.3f far from truth %.3f" % (est["recall_estimate"], s["true_recall"]))
oc = rep["claims"]["overclaim"]["master_data"]
if oc["audit"]["reported"]["n_input"] != rep["audit"]["n_rows"] + 17:
    bad.append("overclaim n_input must be n_rows + 17")
txt = D.render(rep)
if "A person decides release and acceptance" not in txt or "M30.agreement_bound" not in txt:
    bad.append("render lacks the gate lines or the closing line")
mod = "products/brotherds/examples/mdm_audit_demo.py"
p = subprocess.run([sys.executable, mod, "--json"], capture_output=True, text=True)
try:
    eq_seed = json.loads(p.stdout)["seed"]
    if eq_seed != 11:
        bad.append("cli default seed %r" % eq_seed)
except Exception as e:
    bad.append("cli --json not JSON: %r %r" % (e, (p.stdout + p.stderr)[-300:]))
p = subprocess.run([sys.executable, mod, "--selftest"], capture_output=True, text=True)
if p.returncode != 0 or "SELFTEST PASS" not in p.stdout:
    bad.append("selftest: exit %d %s" % (p.returncode, (p.stdout + p.stderr)[-300:]))
with tempfile.TemporaryDirectory(prefix="audit-demo-examples-") as output_dir:
    p = subprocess.run([sys.executable, mod, "--write-examples", "--output-dir", output_dir],
                       capture_output=True, text=True)
    ex = os.path.join(output_dir, "example-mdm-audit-repaired.json")
    if p.returncode != 0 or not os.path.exists(ex):
        bad.append("--write-examples did not write the requested output directory")
src = open(mod, encoding="utf-8").read()
if "\u2014" in src or "\u2013" in src:
    bad.append("dash character in source")
# Recall is a conservative range; the independent replication check below evaluates coverage.
rr = est.get("recall_range") or {}
if not (rr.get("lower", 2) <= rr.get("upper", -1)):
    bad.append("estimates.recall_range must hold lower <= upper, got %r" % (rr,))
if abs(ac["claimed_recall"] - math.floor(rr.get("lower", 0) * 100) / 100) > 1e-12:
    bad.append("the audited claim must state the floor of the conservative lower recall, got %r vs %r" % (ac["claimed_recall"], rr))
if ac["claimed_recall"] > s["true_recall"]:
    bad.append("the audited claim overstates recall: %r above the truth %r" % (ac["claimed_recall"], s["true_recall"]))
if cov.get("recall_range_coverage", 0) < 0.85:
    bad.append("recall range coverage over 40 independent seeds %r, want >= 0.85" % (cov.get("recall_range_coverage"),))
if bad:
    for x in bad[:40]:
        print("CHECK FAIL:", x)
    print("%d CHECK FAILURE(S)" % len(bad)); sys.exit(1)
print("CHECK PASS")
