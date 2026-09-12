"""Independent grader for pack_mdm_science.py. Fixtures and expected verdicts are the
orchestrator's, never the model's. Run from the worktree root."""
import copy, subprocess, sys

sys.path.insert(0, "products/brotherds")
import pack_mdm_science as P  # noqa: E402


class F(object):
    def __init__(self, gate, verdict, detail):
        self.gate, self.verdict, self.detail = gate, verdict, detail


class Reg(object):
    def __init__(self):
        self.fns = []
    def register(self, ct, fn):
        assert ct == "MASTER_DATA", ct
        self.fns.append(fn)


reg = Reg()
P.register(reg, F)
bad = []
if len(reg.fns) != 9:
    bad.append("expected 9 registered gates, got %d" % len(reg.fns))
ALL = ("M7.precision_evidence", "M8.recall_evidence", "M9.blocking_ceiling", "M10.cluster_metrics",
       "M11.overmerge", "M12.fs_parameters", "M13.score_drift", "M14.quality_dimensions",
       "M15.golden_lineage")


def run(claim):
    out = {}
    for fn in reg.fns:
        try:
            for f in fn(claim, F):
                out[f.gate] = f
        except Exception as e:
            bad.append("gate %s crashed: %r" % (getattr(fn, "__name__", fn), e))
    return out


def want(res, gate, verdict, words=(), label=""):
    f = res.get(gate)
    if f is None:
        bad.append("%s: %s produced no line" % (label, gate)); return
    if f.verdict != verdict:
        bad.append("%s: %s verdict %s want %s (%s)" % (label, gate, f.verdict, verdict, f.detail))
    for w in words:
        if w.lower() not in f.detail.lower():
            bad.append("%s: %s detail lacks %r: %s" % (label, gate, w, f.detail))


GOOD = {"claim_type": "MASTER_DATA", "master_data": {"merge_threshold": 0.8, "evaluation": {
    "claimed_precision": 0.95, "claimed_recall": 0.85, "metric_basis": "pairwise",
    "review_strata": [
        {"score_lo": 0.9, "score_hi": 1.0, "population": 5000, "sampled": 200, "positive": 196},
        {"score_lo": 0.8, "score_hi": 0.9, "population": 3000, "sampled": 200, "positive": 176},
        {"score_lo": 0.5, "score_hi": 0.8, "population": 20000, "sampled": 200, "positive": 12}],
    "blocking": {"n_records": 100000, "n_candidate_pairs": 2000000, "n_true_pairs": 10000,
                 "n_true_pairs_in_candidates": 9700},
    "gold_clusters": {"pred": [["a", "b"], ["c"], ["d", "e"]], "true": [["a", "b"], ["c"], ["d", "e"]]},
    "cluster_sizes": [1] * 900 + [2] * 90 + [3] * 10, "giant_clusters_reviewed": False,
    "fs_parameters": [{"field": "email", "m": 0.95, "u": 0.001}, {"field": "city", "m": 0.9, "u": 0.1}],
    "score_drift": {"reference": [0.2, 0.3, 0.5], "current": [0.21, 0.29, 0.5], "acknowledged": False},
    "quality": {"completeness": {"value": 0.98, "threshold": 0.95},
                "accuracy": {"value": 0.97, "threshold": 0.95, "method": "sampled against the source system"}},
    "golden_record": [{"attribute": "email", "rule": "recency", "source_system": "crm", "conflict_rate": 0.04}]}}}

g = run(GOOD)
for gate in ALL:
    want(g, gate, "PASS", label="good")
if run({"claim_type": "MASTER_DATA"}):
    bad.append("no master_data must produce no lines")
nd = run({"master_data": {"merge_threshold": 0.8}})
for gate in ALL:
    want(nd, gate, "NO-DATA", label="empty")

b = copy.deepcopy(GOOD); e = b["master_data"]["evaluation"]
e["claimed_precision"] = 0.999
e["review_strata"] = e["review_strata"][:2]
e["claimed_recall"] = 0.99
e["cluster_sizes"] = e["cluster_sizes"] + [400]
e["fs_parameters"].append({"field": "gender", "m": 0.5, "u": 0.5})
e["score_drift"] = {"reference": [0.5, 0.5], "current": [0.9, 0.1], "acknowledged": False}
e["quality"]["colour"] = {"value": 1.0, "threshold": 0.5}
e["quality"]["accuracy"] = {"value": 0.97, "threshold": 0.95}
e["golden_record"].append({"attribute": "phone", "rule": "recency"})
e["gold_clusters"] = {"pred": [["a", "b", "c", "d", "e"]], "true": [["a", "b"], ["c"], ["d", "e"]]}
r = run(b)
want(r, "M7.precision_evidence", "FAIL", label="bad")
want(r, "M8.recall_evidence", "FAIL", ("cannot estimate recall", "below the merge threshold"), label="bad")
want(r, "M9.blocking_ceiling", "FAIL", ("blocking recall",), label="bad")
want(r, "M10.cluster_metrics", "FAIL", ("pairwise", "bcubed"), label="bad")
want(r, "M11.overmerge", "FAIL", ("400",), label="bad")
want(r, "M12.fs_parameters", "FAIL", ("gender",), label="bad")
want(r, "M13.score_drift", "FAIL", ("major",), label="bad")
want(r, "M14.quality_dimensions", "FAIL", ("colour", "accuracy"), label="bad")
want(r, "M15.golden_lineage", "FAIL", ("phone",), label="bad")

a = copy.deepcopy(b); ea = a["master_data"]["evaluation"]
ea["giant_clusters_reviewed"] = True; ea["score_drift"]["acknowledged"] = True
ra = run(a)
want(ra, "M11.overmerge", "PASS", label="ack"); want(ra, "M13.score_drift", "PASS", label="ack")

c = copy.deepcopy(GOOD); c["master_data"]["evaluation"]["claimed_recall"] = 0.97
want(run(c), "M8.recall_evidence", "FAIL", ("0.05",), label="recall-over")

m = copy.deepcopy(GOOD); ev = m["master_data"]["evaluation"]
del ev["claimed_precision"]; del ev["claimed_recall"]
m["match"] = {"precision": 0.999, "recall": 0.5}
want(run(m), "M7.precision_evidence", "FAIL", label="match-block-precision")

x = copy.deepcopy(GOOD); ex = x["master_data"]["evaluation"]
ex["blocking"] = {"n_records": 10, "n_candidate_pairs": 5, "n_true_pairs": 3, "n_true_pairs_in_candidates": 9}
ex["score_drift"] = {"reference": [1.0], "current": [1.0]}
ex["gold_clusters"] = {"pred": [["a", "zz"]], "true": [["a"]]}
ex["fs_parameters"] = [{"field": "x", "m": "high", "u": 0.1}]
rx = run(x)
for gate in ("M9.blocking_ceiling", "M13.score_drift", "M10.cluster_metrics", "M12.fs_parameters"):
    want(rx, gate, "FAIL", label="malformed")

# R2 regressions (Muse review, reproduced by the orchestrator)
s1 = copy.deepcopy(GOOD); s1["master_data"]["evaluation"].update(metric_basis="bcubed", claimed_precision=0.9,
    gold_clusters={"pred": [["a"], ["b"]], "true": [["a"], ["b"]]})
want(run(s1), "M10.cluster_metrics", "PASS", label="bcubed-singletons")
s2 = copy.deepcopy(GOOD); s2["master_data"]["evaluation"].update(metric_basis="pairwise", claimed_precision=0.9,
    gold_clusters={"pred": [["a"], ["b"]], "true": [["a"], ["b"]]})
want(run(s2), "M10.cluster_metrics", "NO-DATA", ("pairs",), "pairwise-no-pairs")
want(g, "M7.precision_evidence", "PASS", ("n=400",), "m7-n")
s3 = copy.deepcopy(GOOD); s3["master_data"]["merge_threshold"] = 0.85
want(run(s3), "M7.precision_evidence", "NO-DATA", ("straddle",), "m7-straddle")
want(run(s3), "M8.recall_evidence", "NO-DATA", ("straddle",), "m8-straddle")
# F3: a review of candidate pairs measures recall AMONG candidates; end-to-end
# recall multiplies by blocking pair completeness (demo 2026-09-12: review 0.401,
# truth 0.320, product 0.354). 0.863 * 0.97 = 0.837; +0.05 = 0.887 < 0.90.
f3 = copy.deepcopy(GOOD); f3["master_data"]["evaluation"]["claimed_recall"] = 0.90
want(run(f3), "M8.recall_evidence", "FAIL", ("pair completeness",), "end-to-end recall")
want(g, "M8.recall_evidence", "PASS", ("pair completeness",), "end-to-end detail")
c2 = subprocess.run([sys.executable, "products/brotherds/pack_mdm_science.py"], capture_output=True, text=True)
if c2.returncode != 0 or "SELFTEST PASS" not in c2.stdout:
    bad.append("own selftest: exit %d tail %r" % (c2.returncode, (c2.stdout + c2.stderr)[-300:]))
src = open("products/brotherds/pack_mdm_science.py", encoding="utf-8").read()
if "\u2014" in src or "\u2013" in src:
    bad.append("file contains an em or en dash")
if "def wilson_interval" in src or "def bcubed" in src:
    bad.append("re-implements mdm_eval instead of importing it")

for z in bad:
    print("CHECK FAIL:", z)
print("CHECK PASS" if not bad else "%d CHECK FAILURE(S)" % len(bad))
sys.exit(1 if bad else 0)
