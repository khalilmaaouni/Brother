"""Independent grader for pack_mdm_decision.py (orchestrator's fixtures and verdicts)."""
import copy, subprocess, sys

sys.path.insert(0, "products/brotherds")
import pack_mdm_decision as P  # noqa: E402


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
if len(reg.fns) != 4:
    bad.append("expected 4 gates, got %d" % len(reg.fns))
ALL = ("M16.metric_choice", "M17.review_power", "M18.representative_gold", "M19.labeller_agreement")


def run(c):
    out = {}
    for fn in reg.fns:
        try:
            for f in fn(c, F):
                out[f.gate] = f
        except Exception as e:
            bad.append("crash %s: %r" % (getattr(fn, "__name__", fn), e))
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


GOOD = {"master_data": {"merge_threshold": 0.8, "false_merge_cost_class": "catastrophic", "evaluation": {
    "headline_metric": "f_beta", "beta": 0.5, "claimed_precision": 0.98, "required_margin": 0.02,
    "review_strata": [{"score_lo": 0.8, "score_hi": 1.0, "population": 9000, "sampled": 200, "positive": 196},
                      {"score_lo": 0.5, "score_hi": 0.8, "population": 9000, "sampled": 100, "positive": 5}],
    "gold_sample": {"frame": "probability", "weighted": True},
    "labeller": {"kind": "llm", "overlap_n": 120, "agreement_kappa": 0.86}}}}
# n_required = ceil(3.8416*0.98*0.02/0.0004) = ceil(188.24) = 189 <= 200: PASS
g = run(GOOD)
for gate in ALL:
    want(g, gate, "PASS", label="good")
if run({"claim_type": "MASTER_DATA"}):
    bad.append("no master_data must be silent")
nd = run({"master_data": {"merge_threshold": 0.8}})
for gate in ALL:
    want(nd, gate, "NO-DATA", label="empty")

def mut(fn):
    c = copy.deepcopy(GOOD); fn(c["master_data"]["evaluation"], c["master_data"]); return run(c)

want(mut(lambda e, m: e.update(headline_metric="accuracy")), "M16.metric_choice", "FAIL", ("non-match",), "acc")
want(mut(lambda e, m: e.update(headline_metric="f1")), "M16.metric_choice", "FAIL", ("beta",), "f1-cat")
r = mut(lambda e, m: (e.update(headline_metric="f1"), m.update(false_merge_cost_class="cosmetic")))
want(r, "M16.metric_choice", "PASS", label="f1-cosmetic")
want(mut(lambda e, m: e.update(beta=1.0)), "M16.metric_choice", "FAIL", label="beta1-cat")
want(mut(lambda e, m: e.pop("beta")), "M16.metric_choice", "FAIL", label="beta-missing")
want(mut(lambda e, m: e.update(headline_metric="auc")), "M16.metric_choice", "FAIL", ("auc",), "unknown")
want(mut(lambda e, m: e.update(required_margin=0.01)), "M17.review_power", "FAIL", ("753",), "power")
# ceil(3.8416*0.98*0.02/0.0001) = ceil(752.95) = 753
want(mut(lambda e, m: e.update(required_margin=0.7)), "M17.review_power", "FAIL", label="margin-range")
want(mut(lambda e, m: m.pop("merge_threshold")), "M17.review_power", "NO-DATA", label="no-threshold")
want(mut(lambda e, m: e.update(gold_sample={"frame": "benchmark", "weighted": True})),
     "M18.representative_gold", "FAIL", ("binette",), "bench")
want(mut(lambda e, m: e.update(gold_sample={"frame": "probability", "weighted": False})),
     "M18.representative_gold", "FAIL", ("weight",), "unweighted")
want(mut(lambda e, m: e.update(gold_sample={"frame": "census"})), "M18.representative_gold", "PASS", label="census")
want(mut(lambda e, m: e.update(gold_sample={"frame": "vibes"})), "M18.representative_gold", "FAIL", label="frame?")
want(mut(lambda e, m: e.update(labeller={"kind": "llm", "overlap_n": 20, "agreement_kappa": 0.9})),
     "M19.labeller_agreement", "FAIL", ("50",), "overlap")
want(mut(lambda e, m: e.update(labeller={"kind": "llm", "overlap_n": 100, "agreement_kappa": 0.7})),
     "M19.labeller_agreement", "FAIL", ("0.8",), "kappa")
want(mut(lambda e, m: e.update(labeller={"kind": "human"})), "M19.labeller_agreement", "PASS", label="human")
want(mut(lambda e, m: e.update(labeller={"kind": "oracle"})), "M19.labeller_agreement", "FAIL", label="kind?")
x = mut(lambda e, m: e.update(beta="big", required_margin="x", labeller={"kind": "llm", "overlap_n": "many"}))
want(x, "M16.metric_choice", "FAIL", label="malformed"); want(x, "M17.review_power", "FAIL", label="malformed")
want(x, "M19.labeller_agreement", "FAIL", label="malformed")

# R1 regressions
want(mut(lambda e, m: e.update(labeller={"kind": "llm", "overlap_n": 60, "agreement_kappa": 2.0})),
     "M19.labeller_agreement", "FAIL", ("kappa",), "kappa-range")
st = [{"score_lo": 0.7, "score_hi": 0.8, "population": 9000, "sampled": 5000, "positive": 4000}]
want(mut(lambda e, m: (e.update(review_strata=st), m.update(merge_threshold=0.75))),
     "M17.review_power", "NO-DATA", ("straddle",), "straddle")

p = "products/brotherds/pack_mdm_decision.py"
c2 = subprocess.run([sys.executable, p], capture_output=True, text=True)
if c2.returncode != 0 or "SELFTEST PASS" not in c2.stdout:
    bad.append("own selftest exit %d %r" % (c2.returncode, (c2.stdout + c2.stderr)[-300:]))
src = open(p, encoding="utf-8").read()
if "\u2014" in src or "\u2013" in src:
    bad.append("dash in file")
for z in bad:
    print("CHECK FAIL:", z)
print("CHECK PASS" if not bad else "%d CHECK FAILURE(S)" % len(bad))
sys.exit(1 if bad else 0)
