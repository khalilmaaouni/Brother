"""Independent grader for mdm_audit.py. Every expected number is hand-built here."""
import csv, hashlib, io, json, math, os, random, subprocess, sys, tempfile

sys.path.insert(0, "products/brotherds")
bad = []
try:
    import mdm_audit as A
except Exception as e:
    print("CHECK FAIL: import: %r" % e); print("1 CHECK FAILURE(S)"); sys.exit(1)


def eq(a, b, msg):
    if a != b:
        bad.append("%s: got %r expected %r" % (msg, a, b))


def close(a, b, msg, tol=1e-9):
    try:
        if abs(float(a) - float(b)) > tol:
            bad.append("%s: got %r expected %r" % (msg, a, b))
    except Exception as e:
        bad.append("%s: %r" % (msg, e))


def wilson(k, n, z=1.96):
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, c - h), min(1.0, c + h)


COLS = ["source_id", "reference_id", "pathway", "status", "score", "verifier", "segment",
        "key_phone", "hit_a", "hit_b", "reference_snapshot", "model_version"]
rows = []


def add(i, ref, pw, st, sc, ver, seg, ph, ha, hb, mv=""):
    rows.append(dict(zip(COLS, ["S%03d" % i, ref, pw, st, sc, ver, seg, ph, ha, hb, "", mv])))


for i in range(1, 31):
    ph = "03-1111-0000" if i <= 4 else "03-1111-%04d" % i
    add(i, "R%03d" % i, "key_phone", "MATCH", "0.97", "SKIPPED", "A", ph, "1", "1" if i <= 10 else "0")
add(31, "R001", "key_phone", "MATCH", "0.97", "SKIPPED", "A", "03-1111-0031", "1", "0")
for i in range(32, 52):
    add(i, "R%03d" % (69 + i), "vector_auto", "MATCH", "0.96", "", "B", "", "0", "1")
for i in range(52, 72):
    add(i, "R%03d" % (149 + i), "llm_verified", "match", "0.85", "CONFIRMED", "B", "", "0", "1", "m-1")
for i in range(72, 82):
    add(i, "", "rejected_by_verifier", "NO_MATCH", "0.82", "REJECTED", "C", "", "0", "0")
for i in range(82, 92):
    add(i, "", "no_candidate", "NO_MATCH", "", "", "C", "", "0", "0")

tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "results.csv")
with open(path, "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=COLS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
digest = hashlib.sha256(open(path, "rb").read()).hexdigest()

try:
    got = A.audit_path(path)
except Exception as e:
    print("CHECK FAIL: audit_path raised %r" % e); print("1 CHECK FAILURE(S)"); sys.exit(1)
eq(got.get("n_rows"), 91, "n_rows")
rec = got.get("reconciliation", {})
eq(rec.get("status_counts"), {"MATCH": 71, "NO_MATCH": 20}, "status_counts")
eq(rec.get("pathway_counts"), {"key_phone": 31, "vector_auto": 20, "llm_verified": 20}, "pathway_counts")
eq(rec.get("no_match_reasons"), {"rejected_by_verifier": 10, "no_candidate": 10}, "no_match_reasons")
eq(rec.get("verifier_counts"), {"SKIPPED": 31, "CONFIRMED": 20, "REJECTED": 10}, "verifier_counts")
eq((rec.get("problems"), rec.get("consistent")), ([], True), "clean table consistent")
col = got.get("collisions", {})
eq((col.get("reference_ids_with_multiple_sources"), col.get("rows_in_collisions"), col.get("top")),
   (1, 2, [["R001", 2]]), "collisions")
kh = got.get("key_hubs", {})
eq((kh.get("hubs"), kh.get("rows_on_hubs"), kh.get("matched_rows_on_hubs"), kh.get("top"), kh.get("hub_min")),
   (1, 4, 4, [["0311110000", 4]], 3), "key_hubs")
pb = got.get("pathway_band", {})
eq(pb.get("key_phone", {}).get("ge_0.95"), 31, "band key_phone")
eq(pb.get("llm_verified", {}).get("0.80_0.90"), 20, "band llm")
eq(sum(pb.get("vector_auto", {}).values()), 20, "band vector total")
segs = got.get("segments", [])
eq([(s.get("segment"), s.get("n"), s.get("matched")) for s in segs], [("B", 40, 40), ("A", 31, 31), ("C", 20, 0)], "segments")
if len(segs) == 3:
    lo, hi = wilson(0, 20)
    close(segs[2]["lo"], lo, "segment C lo"); close(segs[2]["hi"], hi, "segment C hi")
    lo, hi = wilson(31, 31)
    close(segs[1]["lo"], lo, "segment A lo")
cr = got.get("capture_recapture", {})
eq((cr.get("state"), cr.get("n1"), cr.get("n2"), cr.get("m")), ("EXPLORATORY", 31, 50, 10), "capture_recapture counts")
close(cr.get("n_hat", -1), 32 * 51 / 11 - 1, "chapman n_hat")
note = str(cr.get("note", "")).lower()
if "overstate" not in note or "never a gate" not in note:
    bad.append("capture_recapture note lacks 'overstate' or 'never a gate'")
eq(got.get("provenance"), {"present": ["model_version"], "missing": ["reference_snapshot", "normalizer_version"]}, "provenance")
eq(got.get("input_sha256"), digest, "input_sha256")
eq(A.audit(rows).get("input_sha256"), None, "input_sha256 null for rows")

# broken table
brk = [dict(r) for r in rows[:5]]
brk.append(dict(rows[0]))
brk.append(dict(rows[5], source_id="X1", reference_id=""))
brk.append(dict(rows[80], source_id="X2", reference_id="R999"))
brk.append(dict(rows[6], source_id="X3", status="MAYBE"))
brk.append(dict(rows[7], source_id="X4", score="1.5"))
brk.append(dict(rows[8], source_id="X5", score="abc"))
probs = A.audit(brk)["reconciliation"]
eq(probs.get("consistent"), False, "broken table inconsistent")
joined = " | ".join(probs.get("problems", []))
for sub in ["duplicate source_id", "MATCH row without reference_id", "NO_MATCH row with reference_id",
            "unknown status", "score out of range", "status counts do not sum to n_rows"]:
    if sub not in joined:
        bad.append("broken table: problem %r not reported (got %r)" % (sub, joined[:300]))

# missing column
p2 = os.path.join(tmp, "nocol.csv")
with open(p2, "w", encoding="utf-8") as f:
    f.write("source_id,reference_id,pathway,score\nS1,R1,x,0.9\n")
try:
    A.read_results(p2)
    bad.append("read_results accepted a table without status")
except ValueError as e:
    if "status" not in str(e):
        bad.append("missing column message does not name status: %r" % e)

# review plan: margin 0.3 gives n = min(pop, max(10, 11))
def band(s):
    if s == "":
        return "no_score"
    s = float(s)
    return "lt_0.80" if s < 0.80 else "0.80_0.90" if s < 0.90 else "0.90_0.95" if s < 0.95 else "ge_0.95"


strata = {}
for r in rows:
    st = r["status"].upper()
    name = ("match:%s:%s" % (r["pathway"], band(r["score"]))) if st == "MATCH" else "unmatched:%s" % r["pathway"]
    strata.setdefault(name, []).append(r)
expect = []
for name in sorted(strata):
    srt = sorted(strata[name], key=lambda r: r["source_id"])
    n = min(len(srt), max(10, min(400, math.ceil(1.96 ** 2 * 0.25 / 0.3 ** 2))))
    ch = sorted(random.Random("%s:%s" % (7, name)).sample(srt, n), key=lambda r: r["source_id"])
    expect += [(name, r["source_id"], str(len(srt)), str(n)) for r in ch]
plan = A.review_plan(A.read_results(path), margin=0.3, seed=7)
eq([(p.get("stratum"), p.get("source_id"), str(p.get("stratum_population")), str(p.get("stratum_sampled"))) for p in plan],
   expect, "review plan selection")
eq([p.get("plan_id") for p in plan[:2]], ["P0001", "P0002"], "plan ids")
eq(len(plan), 53, "plan size")
plan2 = A.review_plan(A.read_results(path), margin=0.3, seed=7)
eq(plan, plan2, "plan deterministic")
planf = os.path.join(tmp, "plan.csv")
A.write_plan(plan, planf)
hdr = open(planf, encoding="utf-8").readline().strip()
eq(hdr, "plan_id,stratum,source_id,reference_id,pathway,score,verifier,stratum_population,stratum_sampled,label", "plan header")

# labelling
wrong = {"S001", "S002", "S003", "S004", "S031"}
lab = []
for p in plan:
    q = dict(p)
    sid = q["source_id"]
    if q["stratum"].startswith("match:"):
        q["label"] = "0" if sid in wrong else "1"
    elif q["stratum"] == "unmatched:rejected_by_verifier":
        q["label"] = "1" if sid in {"S072", "S073", "S074"} else "0"
    else:
        q["label"] = "1" if sid == "S082" else "0"
    lab.append(q)
lab[-1]["label"] = ""
ev = A.evaluate_plan(lab, A.read_results(path))
eq(ev.get("unlabelled"), 1, "unlabelled")
kp = [q for q in lab if q["stratum"].startswith("match:key_phone") and q["label"] != ""]
kpr = ev.get("pathway_review", {}).get("key_phone", {})
eq({k: kpr.get(k) for k in ("population", "sampled", "positive")},
   {"population": 31, "sampled": len(kp), "positive": sum(q["label"] == "1" for q in kp)}, "pathway_review key_phone")
eq([(x.get("stratum"), x.get("population")) for x in kpr.get("strata", [])], [("match:key_phone:ge_0.95", 31)], "pathway_review key_phone strata")
cf = [q for q in lab if q["verifier"] == "CONFIRMED" and q["label"] != ""]
eq(ev.get("verifier", {}).get("confirmed_review"),
   {"population": 20, "sampled": len(cf), "positive": sum(q["label"] == "1" for q in cf)}, "confirmed_review")
rj = [q for q in lab if q["verifier"] == "REJECTED" and q["label"] != ""]
eq(ev.get("verifier", {}).get("rejected_review"),
   {"population": 10, "sampled": len(rj), "positive": sum(q["label"] == "1" for q in rj)}, "rejected_review")
um = [q for q in lab if q["stratum"].startswith("unmatched:") and q["label"] != ""]
eq({k: ev.get("unmatched_review", {}).get(k) for k in ("population", "sampled", "matchable_missed", "reference_absent")},
   {"population": 20, "sampled": len(um), "matchable_missed": sum(q["label"] == "1" for q in um),
    "reference_absent": sum(q["label"] == "0" for q in um)}, "unmatched_review")
lab2 = [dict(q) for q in lab]
lab2[0]["label"] = "2"
try:
    A.evaluate_plan(lab2)
    bad.append("evaluate_plan accepted label 2")
except ValueError as e:
    if lab2[0]["plan_id"] not in str(e):
        bad.append("bad label message lacks plan_id: %r" % e)

# CLI
mod = "products/brotherds/mdm_audit.py"
p = subprocess.run([sys.executable, mod, "audit", path, "--plan", os.path.join(tmp, "p.csv"), "--margin", "0.3"],
                   capture_output=True, text=True)
try:
    eq(json.loads(p.stdout)["reconciliation"]["status_counts"]["MATCH"], 71, "cli audit")
except Exception as e:
    bad.append("cli audit output not JSON: %r %r" % (e, p.stdout[:200]))
eq(open(os.path.join(tmp, "p.csv"), encoding="utf-8").read(), open(planf, encoding="utf-8").read(), "cli plan equals write_plan")
p = subprocess.run([sys.executable, mod, "audit", p2], capture_output=True, text=True)
eq((p.returncode, p.stdout.startswith("NO-DATA")), (2, True), "cli missing column NO-DATA exit 2")
p = subprocess.run([sys.executable, os.path.abspath(mod), "--selftest"], capture_output=True, text=True, cwd=tmp)
eq((p.returncode, "SELFTEST PASS" in p.stdout), (0, True), "selftest from another cwd")
src = open(mod, encoding="utf-8").read()
if "\u2014" in src or "\u2013" in src:
    bad.append("dash character in source")
if "def wilson" in src.lower() and "mdm_eval.wilson_interval" not in src:
    bad.append("reimplemented wilson instead of mdm_eval.wilson_interval")
# review round R4a (2026-09-12): independent strata, weighted unmatched share, margin guard
import mdm_eval as _E
uw = [dict(plan_id="U%04d" % i, stratum="unmatched:A", stratum_population="1000", stratum_sampled="10", label="0",
           verifier="", pathway="A", source_id="a%d" % i, reference_id="", score="") for i in range(10)]
uw += [dict(plan_id="V%04d" % i, stratum="unmatched:B", stratum_population="100", stratum_sampled="10", label="1",
            verifier="", pathway="B", source_id="b%d" % i, reference_id="", score="") for i in range(10)]
ur = A.evaluate_plan(uw)["unmatched_review"]
ref = _E.stratified_estimate([{"population": 1000, "sampled": 10, "positive": 0}, {"population": 100, "sampled": 10, "positive": 10}])
w = ur.get("matchable_missed_weighted") or {}
if abs(w.get("estimate", -1) - ref["estimate"]) > 1e-9 or abs(w.get("lo", -1) - ref["lo"]) > 1e-9 or abs(w.get("hi", -1) - ref["hi"]) > 1e-9:
    bad.append("unmatched_review matchable_missed_weighted %r, want the stratified estimate %r" % (w, ref))
eq([(x.get("stratum"), x.get("population"), x.get("sampled"), x.get("matchable_missed")) for x in ur.get("strata", [])],
   [("unmatched:A", 1000, 10, 0), ("unmatched:B", 100, 10, 10)], "unmatched_review strata")
for m in (0.0, -0.1):
    try:
        A.review_plan(A.read_results(path), margin=m)
        bad.append("review_plan accepted margin %r" % m)
    except ValueError as exc:
        refused = str(exc)  # expected: the refusal is the behaviour under test
    except Exception as e:
        bad.append("review_plan margin %r raised %r, want ValueError" % (m, e))
if bad:
    for b in bad[:40]:
        print("CHECK FAIL:", b)
    print("%d CHECK FAILURE(S)" % len(bad)); sys.exit(1)
print("CHECK PASS")
