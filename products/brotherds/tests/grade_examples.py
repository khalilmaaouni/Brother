"""Independent grader for the MDM examples: runs the real tool on them."""
import json, re, subprocess, sys

B = "products/brotherds/bds.py"
X = "products/brotherds/examples/"
bad = []


def check(path):
    r = subprocess.run([sys.executable, B, "check", path], capture_output=True, text=True)
    lines = {}
    for ln in r.stdout.splitlines():
        m = re.match(r"\s+(PASS|FAIL|NO-DATA)\s+(\S+)\s", ln)
        if m:
            lines[m.group(2)] = m.group(1)
    verdict = re.search(r"^VERDICT (\S+)", r.stdout, re.M)
    return lines, verdict.group(1) if verdict else None, r


ev = subprocess.run([sys.executable, B, "mdm-eval", "review", X + "review-sample-mdm.csv",
                     "--merge-threshold", "0.8"], capture_output=True, text=True)
try:
    strata = json.loads(ev.stdout)["strata"]
except Exception as e:
    strata = None
    bad.append("mdm-eval did not print JSON with strata: exit %d %r" % (ev.returncode, ev.stdout[:200]))
try:
    good = json.load(open(X + "example-mdm-evaluated.json", encoding="utf-8"))
    gs = good["master_data"]["evaluation"]["review_strata"]
    if strata is not None:
        norm = lambda s: sorted((round(x["score_lo"], 6), round(x["score_hi"], 6), x["population"],
                                 x["sampled"], x["positive"]) for x in s)
        if norm(gs) != norm(strata):
            bad.append("example review_strata differ from recomputed: example %s recomputed %s"
                       % (norm(gs), norm(strata)))
except Exception as e:
    bad.append("evaluated example unreadable: %r" % e)

g, gv, r = check(X + "example-mdm-evaluated.json")
fails = [k for k, v in g.items() if v == "FAIL"]
if fails:
    bad.append("evaluated example has FAIL lines: %s" % fails)
for k, v in g.items():
    if re.match(r"M\d+\.", k) and v != "PASS":
        bad.append("evaluated example: %s is %s, want PASS" % (k, v))
for need in ("M7.precision_evidence", "M8.recall_evidence", "M9.blocking_ceiling", "M10.cluster_metrics",
             "M11.overmerge", "M12.fs_parameters", "M13.score_drift", "M14.quality_dimensions",
             "M15.golden_lineage", "M16.metric_choice", "M17.review_power", "M18.representative_gold",
             "M19.labeller_agreement", "M20.score_calibration", "M21.threshold_cost", "M22.normalization"):
    if need not in g:
        bad.append("evaluated example printed no %s line" % need)

o, ov, r2 = check(X + "example-mdm-overclaim.json")
for need in ("M7.precision_evidence", "M8.recall_evidence", "M9.blocking_ceiling", "M11.overmerge",
             "M13.score_drift", "M16.metric_choice", "M18.representative_gold", "M19.labeller_agreement"):
    if o.get(need) != "FAIL":
        bad.append("overclaim example: %s is %s, want FAIL" % (need, o.get(need)))
if ov != "FAIL":
    bad.append("overclaim VERDICT %s want FAIL" % ov)
for p in ("example-mdm-evaluated.json", "example-mdm-overclaim.json", "review-sample-mdm.csv"):
    t = open(X + p, encoding="utf-8").read()
    if "\u2014" in t or "\u2013" in t:
        bad.append("dash in " + p)
rows = open(X + "review-sample-mdm.csv").read().strip().splitlines()
if len(rows) != 401:
    bad.append("csv has %d lines, want 401" % len(rows))
if bad:
    print("TOOL OUTPUT, evaluated example:\n" + "\n".join(l for l in r.stdout.splitlines() if " M" in l or "FAIL" in l))
for z in bad:
    print("CHECK FAIL:", z)
print("CHECK PASS" if not bad else "%d CHECK FAILURE(S)" % len(bad))
sys.exit(1 if bad else 0)
