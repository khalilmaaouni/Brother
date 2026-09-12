"""Independent grader for pack_mdm_audit.py (M23 to M26). Orchestrator's cases."""
import copy, subprocess, sys

sys.path.insert(0, "products/brotherds")
bad = []
try:
    import pack_mdm_audit as P
    import mdm_eval
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
GATES = ["M23.report_reconciliation", "M24.assignment_uniqueness", "M25.pathway_precision", "M26.verifier_evidence"]


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
        bad.append("%s: no %s line" % (label, gate)); return
    if f.verdict != v:
        bad.append("%s: %s %s want %s (%s)" % (label, gate, f.verdict, v, f.detail))
    for w in words:
        if w.lower() not in str(f.detail).lower():
            bad.append("%s: %s detail lacks %r: %s" % (label, gate, w, f.detail))


def claim(audit=None, evaluation=None):
    md = {}
    if audit is not None:
        md["audit"] = audit
    if evaluation is not None:
        md["evaluation"] = evaluation
    return {"master_data": md}


if run({"id": "x"}) != {}:
    bad.append("no master_data must return []")
run({"master_data": {"evaluation": {"claimed_precision": 0.9}}})
run({"master_data": {"audit": "not a dict"}})
want(claim({}), GATES[0], "NO-DATA", ["reported"], "m23 absent")
REP = {"n_input": 100, "status_counts": {"MATCH": 70, "NO_MATCH": 30}, "pathway_counts": {"a": 40, "b": 30},
       "percents": {"matched": {"value": 70.0, "numerator": 70, "denominator": 100}}}
want(claim({"reported": REP}), GATES[0], "PASS", ["recomputes"], "m23 clean")
x = copy.deepcopy(REP); x["n_input"] = 101
want(claim({"reported": x}), GATES[0], "FAIL", ["sum"], "m23 status sum")
x = copy.deepcopy(REP); x["pathway_counts"]["b"] = 29
want(claim({"reported": x}), GATES[0], "FAIL", ["pathway"], "m23 pathway sum")
x = copy.deepcopy(REP); x["percents"]["matched"] = {"value": 76.72, "numerator": 38819, "denominator": 50597}
x["n_input"] = 100
want(claim({"reported": x}), GATES[0], "PASS", [], "m23 percent within 0.05")
x["percents"]["matched"]["value"] = 100.0
want(claim({"reported": x}), GATES[0], "FAIL", ["matched", "76.72"], "m23 percent wrong")
x = copy.deepcopy(REP); x["status_counts"] = [70, 30]
want(claim({"reported": x}), GATES[0], "FAIL", ["status_counts"], "m23 malformed")
COMP = {"n_rows": 100, "reconciliation": {"status_counts": {"MATCH": 70, "NO_MATCH": 30},
        "pathway_counts": {"a": 40, "b": 30}, "problems": [], "consistent": True},
        "collisions": {"reference_ids_with_multiple_sources": 0, "rows_in_collisions": 0, "top": []},
        "key_hubs": {"key": "key_phone", "state": "NO-DATA"}}
want(claim({"reported": REP, "computed": COMP}), GATES[0], "PASS", [], "m23 computed agrees")
c2 = copy.deepcopy(COMP); c2["n_rows"] = 99
want(claim({"reported": REP, "computed": c2}), GATES[0], "FAIL", ["results table has 99 rows"], "m23 computed rows")
c2 = copy.deepcopy(COMP); c2["reconciliation"]["status_counts"]["MATCH"] = 69
want(claim({"reported": REP, "computed": c2}), GATES[0], "FAIL", ["MATCH"], "m23 computed status")
c2 = copy.deepcopy(COMP); c2["reconciliation"]["consistent"] = False; c2["reconciliation"]["problems"] = ["duplicate source_id: 1 row(s)"]
want(claim({"reported": REP, "computed": c2}), GATES[0], "FAIL", ["inconsistent", "duplicate source_id"], "m23 inconsistent table")

# M24
want(claim({"reported": REP}), GATES[1], "NO-DATA", ["mdm-audit"], "m24 absent")
want(claim({"computed": COMP}), GATES[1], "PASS", ["NO-DATA"], "m24 clean, no key column")
c3 = copy.deepcopy(COMP); c3["collisions"] = {"reference_ids_with_multiple_sources": 2, "rows_in_collisions": 5, "top": [["R1", 3], ["R2", 2]]}
want(claim({"computed": c3}), GATES[1], "FAIL", ["more than one source", "2 reference"], "m24 collisions")
want(claim({"computed": c3, "collisions_reviewed": True}), GATES[1], "PASS", [], "m24 collisions reviewed")
want(claim({"computed": c3, "one_to_one": False}), GATES[1], "PASS", ["one_to_one"], "m24 one_to_one false")
c4 = copy.deepcopy(COMP); c4["key_hubs"] = {"key": "key_phone", "hub_min": 3, "hubs": 1, "rows_on_hubs": 4, "matched_rows_on_hubs": 4, "top": [["0311110000", 4]]}
want(claim({"computed": c4}), GATES[1], "FAIL", ["shared key", "4 matched"], "m24 hubs")
want(claim({"computed": c4, "hubs_reviewed": True}), GATES[1], "PASS", [], "m24 hubs reviewed")

# M25
want(claim({"computed": COMP, "precision_basis": "similarity"}), GATES[2], "FAIL", ["similarity"], "m25 similarity")
want(claim({"precision_basis": "review"}), GATES[2], "NO-DATA", ["pathway counts"], "m25 no counts")
CNT = {"key_phone": 400, "vector_auto": 150, "llm_verified": 200, "tiny": 10}
c5 = copy.deepcopy(COMP); c5["reconciliation"]["pathway_counts"] = CNT
REV = {"key_phone": {"population": 400, "sampled": 100, "positive": 97},
       "vector_auto": {"population": 150, "sampled": 50, "positive": 49}}
want(claim({"computed": c5, "pathway_review": REV}), GATES[2], "FAIL", ["llm_verified", "no labelled sample"], "m25 missing pathway")
REV2 = dict(REV, llm_verified={"population": 200, "sampled": 60, "positive": 48})
lo, hi = mdm_eval.wilson_interval(48, 60)
want(claim({"computed": c5, "pathway_review": REV2}), GATES[2], "PASS", ["llm_verified 48/60", "%.3f" % hi], "m25 all reviewed")
want(claim({"computed": c5, "pathway_review": REV2, "claimed_pathway_precision": {"llm_verified": 0.95}}), GATES[2], "FAIL",
     ["llm_verified", "%.3f" % hi], "m25 pathway overclaim")
want(claim({"computed": c5, "pathway_review": REV2, "claimed_pathway_precision": {"llm_verified": 0.85}}), GATES[2], "PASS", [], "m25 pathway claim ok")
est = mdm_eval.stratified_estimate([{"population": 400, "sampled": 100, "positive": 97},
                                    {"population": 150, "sampled": 50, "positive": 49},
                                    {"population": 200, "sampled": 60, "positive": 48}])
want(claim({"computed": c5, "pathway_review": REV2}, {"claimed_precision": round(est["hi"] + 0.01, 3)}), GATES[2], "FAIL",
     ["overall", "%.3f" % est["hi"]], "m25 overall overclaim")
want(claim({"computed": c5, "pathway_review": REV2}, {"claimed_precision": round(est["estimate"], 3)}), GATES[2], "PASS", [], "m25 overall ok")
bad_rev = dict(REV2, vector_auto={"population": 150, "sampled": 5, "positive": 6})
want(claim({"computed": c5, "pathway_review": bad_rev}), GATES[2], "FAIL", ["vector_auto", "positive"], "m25 malformed review")
want(claim({"reported": {"n_input": 760, "status_counts": {"MATCH": 760, "NO_MATCH": 0}, "pathway_counts": CNT}, "pathway_review": REV2}),
     GATES[2], "PASS", [], "m25 falls back to reported counts")

# M26
want(claim({}), GATES[3], "NO-DATA", ["verifier"], "m26 absent")
want(claim({"verifier": {"kind": "oracle"}}), GATES[3], "FAIL", ["oracle"], "m26 unknown kind")
want(claim({"verifier": {"kind": "llm", "confirmed_review": {"population": 900, "sampled": 20, "positive": 19},
                         "rejected_review": {"population": 300, "sampled": 40, "positive": 12}}}), GATES[3], "FAIL", ["30"], "m26 few confirmations")
want(claim({"verifier": {"kind": "llm", "confirmed_review": {"population": 900, "sampled": 40, "positive": 38}}}), GATES[3], "FAIL",
     ["rejections", "unknown number"], "m26 no rejected sample")
V = {"kind": "llm", "confirmed_review": {"population": 900, "sampled": 40, "positive": 38},
     "rejected_review": {"population": 300, "sampled": 40, "positive": 12}}
clo, chi = mdm_eval.wilson_interval(38, 40)
want(claim({"verifier": dict(V, claimed_precision=0.99)}), GATES[3], "FAIL", ["claimed", "%.3f" % chi], "m26 overclaim")
flo, fhi = mdm_eval.wilson_interval(12, 40)
want(claim({"verifier": dict(V, claimed_precision=0.95)}), GATES[3], "PASS",
     ["false omission", "0.300", "%.3f" % fhi, "miss rate NO-DATA"], "m26 pass without frame")
want(claim({"verifier": dict(V, known_match_frame={"sampled": 50, "rejected": 5})}), GATES[3], "PASS",
     ["miss rate", "0.100"], "m26 pass with frame")
want(claim({"verifier": {"kind": "human"}}), GATES[3], "NO-DATA", ["labelled"], "m26 human no sample")
want(claim({"verifier": dict(V, confirmed_review={"population": 9, "sampled": 4, "positive": 5})}), GATES[3], "FAIL",
     ["confirmed_review"], "m26 malformed")
p = subprocess.run([sys.executable, "products/brotherds/pack_mdm_audit.py"], capture_output=True, text=True)
if p.returncode != 0 or "SELFTEST PASS" not in p.stdout:
    bad.append("selftest: exit %d %s" % (p.returncode, (p.stdout + p.stderr)[-300:]))
src = open("products/brotherds/pack_mdm_audit.py", encoding="utf-8").read()
if "\u2014" in src or "\u2013" in src:
    bad.append("dash character in source")
# review round R4a
want(claim({"computed": {}}), GATES[1], "NO-DATA", ["collision"], "m24 computed without collisions")
pc = {("p%d" % i): 1 for i in range(1, 30)}
want(claim({"reported": {"pathway_counts": pc}}, {"claimed_precision": 0.99}), GATES[2], "FAIL", ["no labelled sample"], "m25 many tiny unreviewed pathways")
c6 = copy.deepcopy(COMP); c6["reconciliation"]["pathway_counts"] = {"a": 900, "t1": 30, "t2": 30}
want(claim({"computed": c6, "pathway_review": {"a": {"population": 900, "sampled": 90, "positive": 88}}}), GATES[2], "FAIL",
     ["t1", "t2"], "m25 unreviewed pathways combine above 5 percent")
ST = [{"stratum": "match:llm_verified:0.80_0.90", "population": 150, "sampled": 30, "positive": 20},
      {"stratum": "match:llm_verified:0.90_0.95", "population": 50, "sampled": 30, "positive": 28}]
REV3 = dict(REV2, llm_verified={"population": 200, "sampled": 60, "positive": 48, "strata": ST})
e3 = mdm_eval.stratified_estimate([{k: x[k] for k in ("population", "sampled", "positive")} for x in ST])
want(claim({"computed": c5, "pathway_review": REV3, "claimed_pathway_precision": {"llm_verified": round(e3["hi"] + 0.01, 3)}}), GATES[2], "FAIL",
     ["%.3f" % e3["hi"]], "m25 pathway interval from its strata")
want(claim({"computed": c5, "pathway_review": REV3, "claimed_pathway_precision": {"llm_verified": round(e3["estimate"], 3)}}), GATES[2], "PASS",
     [], "m25 pathway strata claim ok")
want(claim({"verifier": {"kind": "human", "rejected_review": {"population": 100, "sampled": 10, "positive": 2}, "claimed_precision": 0.99}}), GATES[3], "FAIL",
     ["confirmation sample"], "m26 claimed precision without confirmations")
want(claim({"verifier": {"kind": "human", "confirmed_review": {"population": 10, "sampled": 100, "positive": 90}}}), GATES[3], "FAIL",
     ["confirmed_review", "population"], "m26 sampled above population")
if bad:
    for b in bad[:40]:
        print("CHECK FAIL:", b)
    print("%d CHECK FAILURE(S)" % len(bad)); sys.exit(1)
print("CHECK PASS")
