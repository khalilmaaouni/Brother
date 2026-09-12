"""Independent grader for examples/mdm_archetype.py. Recomputes the truth-side numbers itself."""
import csv, json, os, subprocess, sys, tempfile
from collections import Counter

sys.path.insert(0, "products/brotherds/examples")
sys.path.insert(0, "products/brotherds")
bad = []
try:
    import mdm_archetype as G
except Exception as e:
    print("CHECK FAIL: import: %r" % e); print("1 CHECK FAILURE(S)"); sys.exit(1)


def eq(a, b, msg):
    if a != b:
        bad.append("%s: got %r expected %r" % (msg, a, b))


g = G.generate()
g2 = G.generate()
eq(g["results"] == g2["results"] and g["truth"] == g2["truth"], True, "deterministic for the same seed")
g3 = G.generate(seed=12)
if g3["results"] == g["results"]:
    bad.append("a different seed produced identical results")
res, truth = g["results"], g["truth"]
eq(list(res[0].keys()) if res else None, G.COLUMNS, "row keys equal COLUMNS in order")
eq(len(res), len(truth), "one truth entry per source row")
ids = [r["source_id"] for r in res]
eq(len(set(ids)), len(ids), "source ids unique")
eq(ids[0], "S000001", "first id")
for r in res:
    if not all(isinstance(v, str) for v in r.values()):
        bad.append("non-string value in %s" % r["source_id"]); break
    if r["status"] == "MATCH" and not r["reference_id"]:
        bad.append("MATCH without reference in %s" % r["source_id"]); break
    if r["status"] == "NO_MATCH" and r["reference_id"]:
        bad.append("NO_MATCH with reference in %s" % r["source_id"]); break
    if r["score"] and (len(r["score"].split(".")[-1]) != 3 or not 0.0 <= float(r["score"]) <= 1.0):
        bad.append("score format %r in %s" % (r["score"], r["source_id"])); break
    if r["pathway"] not in ("key_phone", "vector_auto", "llm_verified", "rejected_by_verifier", "no_candidate"):
        bad.append("unknown pathway %r" % r["pathway"]); break
    want_mv = "m-2026-09" if r["verifier"] in ("CONFIRMED", "REJECTED") else ""
    if r["model_version"] != want_mv or r["reference_snapshot"] != "ref-2026-08":
        bad.append("provenance columns wrong in %s" % r["source_id"]); break
segs = sorted(set(r["segment"] for r in res))
eq(segs[0], "C01", "first chain"); eq(len(segs), 24, "24 chains")
# headquarters matches are wrong and collide
hq = [r for r in res if r["reference_id"].startswith("RHQ")]
if not hq:
    bad.append("no headquarters matches generated")
if any(r["segment"] not in ("C04", "C05") for r in hq):
    bad.append("headquarters matches outside the hub chains")
if any(truth[r["source_id"]] == r["reference_id"] for r in hq):
    bad.append("a headquarters match equals the truth")
phones = Counter(r["key_phone"] for r in res)
if sum(1 for c in phones.values() if c >= 3) < 2:
    bad.append("expected at least two shared phone hubs")
# coverage gap chains
cov = {}
for r in res:
    c = cov.setdefault(r["segment"], [0, 0]); c[1] += 1; c[0] += bool(truth[r["source_id"]])
gap = sum(cov[s][0] for s in ("C01", "C02", "C03")) / max(1, sum(cov[s][1] for s in ("C01", "C02", "C03")))
rest = sum(v[0] for k, v in cov.items() if k not in ("C01", "C02", "C03")) / max(1, sum(v[1] for k, v in cov.items() if k not in ("C01", "C02", "C03")))
if not (gap < 0.55 and rest > 0.88):
    bad.append("coverage gap chains %.3f vs others %.3f not separated" % (gap, rest))
# recompute the summary
m = [r for r in res if r["status"] == "MATCH"]
correct = [r for r in m if r["reference_id"] and r["reference_id"] == truth[r["source_id"]]]
correct_ids = set(r["source_id"] for r in correct)
covered = sum(1 for v in truth.values() if v)
bypw = {}
for r in m:
    t = bypw.setdefault(r["pathway"], [0, 0]); t[1] += 1; t[0] += r["source_id"] in correct_ids
refc = Counter(r["reference_id"] for r in m)
exp = {"n_source": len(res), "n_match": len(m), "n_no_match": len(res) - len(m), "covered": covered,
       "coverage": covered / len(res), "true_precision": len(correct) / len(m),
       "true_precision_by_pathway": {k: v[0] / v[1] for k, v in bypw.items()},
       "true_recall": len(correct) / covered, "missed_covered": covered - len(correct),
       "reference_ids_with_multiple_sources": sum(1 for c in refc.values() if c >= 2)}
s = g["summary"]
for k, v in exp.items():
    if k == "true_precision_by_pathway":
        if set(s.get(k, {})) != set(v) or any(abs(s[k][p] - v[p]) > 1e-12 for p in v):
            bad.append("summary %s %r expected %r" % (k, s.get(k), v))
    elif isinstance(v, float):
        if abs(float(s.get(k, -9)) - v) > 1e-12:
            bad.append("summary %s %r expected %r" % (k, s.get(k), v))
    elif s.get(k) != v:
        bad.append("summary %s %r expected %r" % (k, s.get(k), v))
tp = exp["true_precision_by_pathway"]
if not (tp.get("vector_auto", 0) > 0.9 and tp.get("llm_verified", 1) < 0.95 and tp.get("key_phone", 1) < 0.95):
    bad.append("pathway precisions not in the designed shape: %r" % tp)
if not any(r["pathway"] == "rejected_by_verifier" and truth[r["source_id"]] for r in res):
    bad.append("no true match was rejected by the verifier")
# label_plan
plan = [{"stratum": "match:key_phone:ge_0.95", "source_id": hq[0]["source_id"], "reference_id": hq[0]["reference_id"], "label": ""},
        {"stratum": "match:vector_auto:ge_0.95", "source_id": correct[0]["source_id"], "reference_id": correct[0]["reference_id"], "label": ""}]
unm_cov = [r for r in res if r["status"] == "NO_MATCH" and truth[r["source_id"]]][0]
unm_gap = [r for r in res if r["status"] == "NO_MATCH" and not truth[r["source_id"]]][0]
plan += [{"stratum": "unmatched:x", "source_id": unm_cov["source_id"], "reference_id": "", "label": ""},
         {"stratum": "unmatched:x", "source_id": unm_gap["source_id"], "reference_id": "", "label": ""}]
lab = G.label_plan(plan, truth)
eq([q["label"] for q in lab], ["0", "1", "1", "0"], "label_plan labels")
eq([q["label"] for q in plan], ["", "", "", ""], "label_plan leaves input untouched")
# CSV round trip and CLI
tmp = tempfile.mkdtemp()
p1, p2 = G.write_csvs(os.path.join(tmp, "o"), g)
with open(p1, encoding="utf-8") as f:
    back = list(csv.DictReader(f))
eq(back == res, True, "results.csv round trip")
eq(open(p2, encoding="utf-8").readline().strip(), "source_id,true_reference_id", "truth header")
p = subprocess.run([sys.executable, "products/brotherds/examples/mdm_archetype.py", "--out", os.path.join(tmp, "cli")], capture_output=True, text=True)
try:
    eq(json.loads(p.stdout)["n_source"], len(res), "cli summary")
except Exception as e:
    bad.append("cli output not JSON: %r %r" % (e, p.stdout[:200]))
p = subprocess.run([sys.executable, "products/brotherds/examples/mdm_archetype.py", "--selftest"], capture_output=True, text=True)
eq((p.returncode, "SELFTEST PASS" in p.stdout), (0, True), "selftest")
# optional integration: when the audit library is present, the table must reconcile
try:
    import mdm_audit
    a = mdm_audit.audit(res)
    eq(a["reconciliation"]["consistent"], True, "audit finds the generated table consistent")
    if a["collisions"]["reference_ids_with_multiple_sources"] < 2:
        bad.append("audit should see headquarters collisions")
except Exception as e:  # the audit library is another unit; its state must not fail this one
    print('note: optional audit integration skipped: %r' % (e,))
src = open("products/brotherds/examples/mdm_archetype.py", encoding="utf-8").read()
if "\u2014" in src or "\u2013" in src:
    bad.append("dash character in source")
if bad:
    for b in bad[:40]:
        print("CHECK FAIL:", b)
    print("%d CHECK FAILURE(S)" % len(bad)); sys.exit(1)
print("CHECK PASS")
