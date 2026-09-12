"""Independent grader for pack_mdm_coverage.py (M27 to M30). Orchestrator's own formulas."""
import copy, math, random, re, statistics, subprocess, sys

sys.path.insert(0, "products/brotherds")
bad = []
try:
    import pack_mdm_coverage as P
except Exception as e:
    print("CHECK FAIL: import: %r" % e); print("1 CHECK FAILURE(S)"); sys.exit(1)


class F(object):
    def __init__(self, g, v, d):
        self.gate, self.verdict, self.detail = g, v, d


class R(object):
    def __init__(self):
        self.fns = []

    def register(self, ct, fn):
        assert ct == "MASTER_DATA"
        self.fns.append(fn)


r = R()
P.register(r, F)
if len(r.fns) != 1:
    bad.append("register must register exactly one function, got %d" % len(r.fns))
GATES = ["M27.segment_disparity", "M28.coverage_split", "M29.hierarchy_integrity", "M30.agreement_bound"]


def run(c):
    out = {}
    for fn in r.fns:
        try:
            res = fn(c, F)
        except Exception as e:
            bad.append("gate function raised: %r" % e)
            return {}
        applies = isinstance(c.get("master_data"), dict) and isinstance(c["master_data"].get("audit"), dict)
        if applies and [f.gate for f in res] != GATES:
            bad.append("expected exactly the four gates in order, got %r" % [f.gate for f in res])
        if not applies and res:
            bad.append("a claim without master_data.audit must get [] (the audit gates do not apply), got %r" % [f.gate for f in res])
        if False:
            bad.append("expected exactly the four gates in order, got %r" % [f.gate for f in res])
        for f in res:
            out[f.gate] = f
    return out


def want(c, gate, v, words=(), label=""):
    f = run(c).get(gate)
    if f is None:
        bad.append("%s: no %s line" % (label, gate)); return None
    if f.verdict != v:
        bad.append("%s: %s %s want %s (%s)" % (label, gate, f.verdict, v, f.detail))
    for w in words:
        if w.lower() not in str(f.detail).lower():
            bad.append("%s: %s detail lacks %r: %s" % (label, gate, w, f.detail))
    return f


def claim(audit=None, evaluation=None):
    md = {}
    if audit is not None:
        md["audit"] = audit
    if evaluation is not None:
        md["evaluation"] = evaluation
    return {"master_data": md}


def wilson(k, n, z=1.96):
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, c - h), min(1.0, c + h)


def ref_flags(segs, gap=0.10, min_n=30):
    elig = [s for s in segs if s["n"] >= min_n]
    k = len(elig)
    z = statistics.NormalDist().inv_cdf(1 - 0.05 / (2 * k))
    tm = sum(s["matched"] for s in segs); tn = sum(s["n"] for s in segs)
    below, above = [], []
    for s in elig:
        m1, n1 = s["matched"], s["n"]; m2, n2 = tm - m1, tn - n1
        if n2 == 0:
            continue
        p1, p2 = m1 / n1, m2 / n2
        l1, u1 = wilson(m1, n1, z); l2, u2 = wilson(m2, n2, z)
        d = p1 - p2
        lo = d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
        hi = d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
        if hi < -gap:
            below.append(s["segment"])
        if lo > gap:
            above.append(s["segment"])
    return sorted(below), sorted(above), z


def names(detail, key):
    m = re.search(key + r": ([^;]*)", detail)
    if not m:
        return None
    t = m.group(1).strip()
    return [] if t == "none" else sorted(x.strip() for x in t.split(","))


if run({"id": "x"}) != {}:
    bad.append("no master_data must return []")
run({"master_data": {"evaluation": {"claimed_precision": 0.9}}})
run({"master_data": {"audit": "not a dict"}})
# M27
want(claim({}), GATES[0], "NO-DATA", ["segment"], "m27 absent")
SEG = [{"segment": "big1", "n": 1000, "matched": 900}, {"segment": "big2", "n": 1000, "matched": 880},
       {"segment": "bad", "n": 200, "matched": 20}, {"segment": "small", "n": 10, "matched": 1}]
f = want(claim({"segments": SEG}), GATES[0], "FAIL", ["headline", "below: bad", "small segment"], "m27 hidden")
want(claim({"segments": SEG, "segment_disclosure": True}), GATES[0], "PASS", ["below: bad"], "m27 disclosed")
want(claim({"segments": [SEG[0], SEG[3]]}), GATES[0], "NO-DATA", ["fewer than two"], "m27 one eligible")
x = copy.deepcopy(SEG); x[1]["matched"] = 1001
want(claim({"segments": x}), GATES[0], "FAIL", ["big2", "matched"], "m27 malformed")
want(claim({"computed": {"segments": [dict(s, rate=0, lo=0, hi=0) for s in SEG]}}), GATES[0], "FAIL", ["below: bad"], "m27 from computed")
rng = random.Random(912)
for trial in range(25):
    segs = []
    for i in range(rng.randint(3, 9)):
        n = rng.choice([20, 40, 80, 150, 400, 900])
        rate = rng.choice([0.2, 0.5, 0.7, 0.75, 0.8, 0.85, 0.9])
        segs.append({"segment": "s%02d" % i, "n": n, "matched": sum(rng.random() < rate for _ in range(n))})
    if len([s for s in segs if s["n"] >= 30]) < 2:
        continue
    eb, ea, z = ref_flags(segs)
    fnd = run(claim({"segments": segs, "segment_disclosure": True})).get(GATES[0])
    if fnd is None:
        bad.append("trial %d: no M27" % trial); continue
    if names(fnd.detail, "below") != eb or names(fnd.detail, "above") != ea:
        bad.append("trial %d: below/above %r/%r want %r/%r (%s)" % (trial, names(fnd.detail, "below"), names(fnd.detail, "above"), eb, ea, fnd.detail))
    if ("%.3f" % z) not in fnd.detail:
        bad.append("trial %d: Bonferroni z %.3f not in detail" % (trial, z))

# M28
want(claim({}), GATES[1], "NO-DATA", ["unmatched"], "m28 absent")
U = {"population": 3000, "sampled": 100, "reference_absent": 80, "matchable_missed": 20}
want(claim({"unmatched_review": dict(U, reference_absent=70)}), GATES[1], "FAIL", ["labelled"], "m28 unlabelled rows")
want(claim({"unmatched_review": dict(U, sampled=0, reference_absent=0, matchable_missed=0)}), GATES[1], "NO-DATA", ["empty"], "m28 empty")
qlo, qhi = wilson(20, 100)
bound = 7000 / (7000 + 3000 * qlo)
comp = {"reconciliation": {"status_counts": {"MATCH": 7000, "NO_MATCH": 3000}}}
want(claim({"unmatched_review": U, "computed": comp}, {"claimed_recall": 0.97}), GATES[1], "FAIL", ["0.970", "%.3f" % bound], "m28 overclaim")
want(claim({"unmatched_review": U, "computed": comp}, {"claimed_recall": 0.90}), GATES[1], "PASS", ["%.3f" % bound, "reference absent 0.800"], "m28 ok")
want(claim({"unmatched_review": U, "reported": {"status_counts": {"MATCH": 7000}}}, {"claimed_recall": 0.97}), GATES[1], "FAIL", [], "m28 reported fallback")
want(claim({"unmatched_review": U}, {"claimed_recall": 0.9}), GATES[1], "NO-DATA", ["matched count"], "m28 no M")
want(claim({"unmatched_review": U}), GATES[1], "PASS", ["matchable but missed 0.200"], "m28 split only")
want(claim({"unmatched_review": dict(U, sampled="100")}), GATES[1], "FAIL", ["sampled"], "m28 malformed")

# M29
want(claim({}), GATES[2], "NO-DATA", ["hierarchy"], "m29 absent")
H = {"edges": [["a", "p"], ["b", "p"], ["p", "g"]], "retired": [], "linkages": [["x", "a"]], "synthesized": ["g"]}
want(claim({"hierarchy": H}), GATES[2], "PASS", ["4 node", "3 edge", "0.250"], "m29 clean")
want(claim({"hierarchy": dict(H, edges=H["edges"] + [["a", "q"]])}), GATES[2], "FAIL", ["more than one parent", "p, q"], "m29 multi parent")
want(claim({"hierarchy": dict(H, edges=[["a", "b"], ["b", "c"], ["c", "a"]])}), GATES[2], "FAIL", ["cycle", "a, b, c"], "m29 cycle")
want(claim({"hierarchy": dict(H, retired=["a"], linkages=[["x", "a"], ["y", "b"]])}), GATES[2], "FAIL", ["1 linkage", "retired"], "m29 retired")
want(claim({"hierarchy": dict(H, edges=[["a"]])}), GATES[2], "FAIL", ["edges"], "m29 malformed")

# M30
want(claim({}), GATES[3], "NO-DATA", ["confusion"], "m30 absent")
want(claim({"labeller_confusion": {"both_match": 10, "model_only": 5, "human_only": 5, "both_nonmatch": 10}}), GATES[3], "FAIL", ["50"], "m30 small n")


def kap(a, b, c, d):
    n = a + b + c + d
    po = (a + d) / n; pe = ((a + b) * (a + c) + (c + d) * (b + d)) / n ** 2
    k = (po - pe) / (1 - pe); se = math.sqrt(po * (1 - po) / (n * (1 - pe) ** 2))
    return k, k - 1.645 * se


k, lo = kap(40, 5, 5, 50)
want(claim({"labeller_confusion": {"both_match": 40, "model_only": 5, "human_only": 5, "both_nonmatch": 50}}), GATES[3], "PASS",
     ["%.3f" % k, "%.3f" % lo, "0.889", "0.909"], "m30 pass")
k, lo = kap(20, 10, 10, 20)
want(claim({"labeller_confusion": {"both_match": 20, "model_only": 10, "human_only": 10, "both_nonmatch": 20}}), GATES[3], "FAIL",
     ["lower", "%.3f" % lo], "m30 low bound")
want(claim({"labeller_confusion": {"both_match": 60, "model_only": 0, "human_only": 0, "both_nonmatch": 0}}), GATES[3], "FAIL", ["undefined"], "m30 constant")
want(claim({"labeller_confusion": {"both_match": 40, "model_only": 5, "human_only": 5, "both_nonmatch": 50}, "min_kappa_lower": 0.7}), GATES[3], "FAIL", [], "m30 stricter bar")
want(claim({"labeller_confusion": {"both_match": -1, "model_only": 5, "human_only": 5, "both_nonmatch": 50}}), GATES[3], "FAIL", ["both_match"], "m30 malformed")
p = subprocess.run([sys.executable, "products/brotherds/pack_mdm_coverage.py"], capture_output=True, text=True)
if p.returncode != 0 or "SELFTEST PASS" not in p.stdout:
    bad.append("selftest: exit %d %s" % (p.returncode, (p.stdout + p.stderr)[-300:]))
src = open("products/brotherds/pack_mdm_coverage.py", encoding="utf-8").read()
if "\u2014" in src or "\u2013" in src:
    bad.append("dash character in source")
# review round R4a
want(claim({"labeller_confusion": {"both_match": 410, "model_only": 90, "human_only": 90, "both_nonmatch": 410}}), GATES[3], "PASS",
     ["one-sided"], "m30 one-sided 95 percent bound")
want(claim({"unmatched_review": {"population": 100, "sampled": 10, "reference_absent": 10, "matchable_missed": 0},
            "computed": {"reconciliation": {"status_counts": {"MATCH": 0}}}}, {"claimed_recall": 0.9}), GATES[1], "FAIL", ["0.900"], "m28 zero matches")
import mdm_eval as _E
UW = {"population": 1100, "sampled": 20, "reference_absent": 10, "matchable_missed": 10,
      "strata": [{"stratum": "unmatched:A", "population": 1000, "sampled": 10, "matchable_missed": 0, "reference_absent": 10},
                 {"stratum": "unmatched:B", "population": 100, "sampled": 10, "matchable_missed": 10, "reference_absent": 0}]}
qw = _E.stratified_estimate([{"population": 1000, "sampled": 10, "positive": 0}, {"population": 100, "sampled": 10, "positive": 10}])
bw = 500 / (500 + 1100 * qw["lo"])
cw = {"reconciliation": {"status_counts": {"MATCH": 500}}}
want(claim({"unmatched_review": UW, "computed": cw}, {"claimed_recall": round(bw + 0.01, 3)}), GATES[1], "FAIL", ["%.3f" % bw], "m28 weighted strata bound")
want(claim({"unmatched_review": UW, "computed": cw}, {"claimed_recall": round(bw - 0.01, 3)}), GATES[1], "PASS", ["weighted"], "m28 weighted strata pass")
f8 = run(claim({"segments": [{"segment": "A", "n": 0, "matched": 0}, {"segment": "B", "n": 100, "matched": 50},
                             {"segment": "C", "n": 100, "matched": 60}], "min_segment_n": 0})).get(GATES[0])
if f8 is None or "malformed" in f8.detail or "division" in f8.detail:
    bad.append("m27: a zero-size segment with min_segment_n 0 must be ignored cleanly, got %r" % (f8 and f8.detail))
if bad:
    for b in bad[:40]:
        print("CHECK FAIL:", b)
    print("%d CHECK FAILURE(S)" % len(bad)); sys.exit(1)
print("CHECK PASS")
