"""Independent grader for pack_mdm_calibration.py (orchestrator fixtures)."""
import copy, subprocess, sys

sys.path.insert(0, "products/brotherds")
import pack_mdm_calibration as P  # noqa: E402


class F(object):
    def __init__(self, g, v, d):
        self.gate, self.verdict, self.detail = g, v, d


class Reg(object):
    def __init__(self):
        self.fns = []
    def register(self, ct, fn):
        assert ct == "MASTER_DATA"
        self.fns.append(fn)


reg = Reg(); P.register(reg, F); bad = []
if len(reg.fns) != 2:
    bad.append("expected 2 gates, got %d" % len(reg.fns))


def run(c):
    out = {}
    for fn in reg.fns:
        try:
            for f in fn(c, F):
                out[f.gate] = f
        except Exception as e:
            bad.append("crash: %r" % e)
    return out


def want(res, gate, v, words=(), label=""):
    f = res.get(gate)
    if f is None:
        bad.append("%s: %s no line" % (label, gate)); return
    if f.verdict != v:
        bad.append("%s: %s %s want %s (%s)" % (label, gate, f.verdict, v, f.detail))
    for w in words:
        if w.lower() not in f.detail.lower():
            bad.append("%s: %s lacks %r: %s" % (label, gate, w, f.detail))


S = [{"score_lo": 0.9, "score_hi": 1.0, "population": 5000, "sampled": 200, "positive": 190, "mean_score": 0.95},
     {"score_lo": 0.8, "score_hi": 0.9, "population": 3000, "sampled": 200, "positive": 170, "mean_score": 0.85},
     {"score_lo": 0.6, "score_hi": 0.8, "population": 9000, "sampled": 200, "positive": 138, "mean_score": 0.70}]
GOOD = {"master_data": {"merge_threshold": 0.8, "evaluation": {
    "review_strata": S, "scores_are_probabilities": True,
    "error_costs": {"false_merge": 4, "missed_match": 1}}}}
# t* = 4/5 = 0.8; ECE = (1/3)(0 + 0 + |0.69-0.70|) = 0.00333
g = run(GOOD)
want(g, "M20.score_calibration", "PASS", ("0.003",), "good")
want(g, "M21.threshold_cost", "PASS", label="good")
if run({"claim_type": "MASTER_DATA"}):
    bad.append("no master_data must be silent")
nd = run({"master_data": {"merge_threshold": 0.8}})
want(nd, "M20.score_calibration", "NO-DATA", label="empty"); want(nd, "M21.threshold_cost", "NO-DATA", label="empty")


def mut(fn):
    c = copy.deepcopy(GOOD); fn(c["master_data"]["evaluation"], c["master_data"]); return run(c)


r = mut(lambda e, m: e["review_strata"][1].update(positive=124))   # observed 0.62, predicted 0.85
want(r, "M20.score_calibration", "FAIL", ("0.80", "0.850", "0.620"), "miscal")
want(mut(lambda e, m: e.update(scores_are_probabilities=False)), "M20.score_calibration", "NO-DATA", ("probabilities",), "notprob")
want(mut(lambda e, m: e.update(scores_are_probabilities=False)), "M21.threshold_cost", "NO-DATA", label="notprob21")
want(mut(lambda e, m: [s.pop("mean_score") for s in e["review_strata"][:2]]), "M20.score_calibration", "NO-DATA", label="few")
want(mut(lambda e, m: e["review_strata"][0].update(mean_score=1.4)), "M20.score_calibration", "FAIL", label="range")
want(mut(lambda e, m: e["review_strata"][0].update(positive=500)), "M20.score_calibration", "FAIL", label="counts")
want(mut(lambda e, m: m.update(merge_threshold=0.6)), "M21.threshold_cost", "FAIL", ("0.800",), "eager")
want(mut(lambda e, m: m.update(merge_threshold=0.95)), "M21.threshold_cost", "PASS", ("conservative",), "cons")
want(mut(lambda e, m: e.update(error_costs={"false_merge": 0, "missed_match": 1})), "M21.threshold_cost", "FAIL", label="cost0")
want(mut(lambda e, m: e.update(error_costs={"false_merge": "high", "missed_match": 1})), "M21.threshold_cost", "FAIL", label="costs")
want(mut(lambda e, m: e.pop("error_costs")), "M21.threshold_cost", "NO-DATA", label="nocost")
# R3 regressions: a gate must never PASS on a value that is not a finite number
nan = float("nan")
want(mut(lambda e, m: e["review_strata"][0].update(mean_score=nan)), "M20.score_calibration", "FAIL", label="nan mean")
want(mut(lambda e, m: m.update(merge_threshold=nan)), "M21.threshold_cost", "FAIL", label="nan threshold")
want(mut(lambda e, m: e.update(error_costs={"false_merge": nan, "missed_match": 1})), "M21.threshold_cost", "FAIL", label="nan cost")
want(mut(lambda e, m: e.update(error_costs={"false_merge": float("inf"), "missed_match": 1})), "M21.threshold_cost", "FAIL", label="inf cost")
want(mut(lambda e, m: e["review_strata"][0].update(score_lo=0.95, score_hi=0.2)), "M20.score_calibration", "FAIL", label="reversed band")
p = "products/brotherds/pack_mdm_calibration.py"
c2 = subprocess.run([sys.executable, p], capture_output=True, text=True)
if c2.returncode != 0 or "SELFTEST PASS" not in c2.stdout:
    bad.append("own selftest exit %d %r" % (c2.returncode, (c2.stdout + c2.stderr)[-300:]))
if "\u2014" in open(p, encoding="utf-8").read() or "\u2013" in open(p, encoding="utf-8").read():
    bad.append("dash in file")
for z in bad:
    print("CHECK FAIL:", z)
print("CHECK PASS" if not bad else "%d CHECK FAILURE(S)" % len(bad))
sys.exit(1 if bad else 0)
