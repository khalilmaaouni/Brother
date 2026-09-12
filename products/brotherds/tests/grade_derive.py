"""Grader for mdm_derive.py: hand-computed expectations, run from the repository root."""
import csv, json, os, subprocess, sys, tempfile

P = "products/brotherds/mdm_derive.py"
sys.path.insert(0, "products/brotherds")
bad = []
try:
    import mdm_derive as D
except Exception as e:
    print("CHECK FAIL: import %r" % e); print("1 CHECK FAILURE(S)"); sys.exit(1)
d = tempfile.mkdtemp()


def write(name, header, rows):
    p = os.path.join(d, name)
    with open(p, "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(header); w.writerows(rows)
    return p


# pred {a,b,c},{d}; true {a,b},{c,d}: pairwise P 1/3 R 1/2, bcubed P 2/3 R 3/4 (hand computed)
c = write("c.csv", ["record_id", "predicted_cluster", "true_cluster", "note"],
          [["a", "p1", "t1", ""], ["b", "p1", "t1", ""], ["c", "p1", "t2", ""], ["d", "p2", "t2", "x"]])
r = D.clusters_from_csv(c)
if r["n_records"] != 4:
    bad.append("n_records %r" % r["n_records"])
if r["gold_clusters"] != {"pred": [["a", "b", "c"], ["d"]], "true": [["a", "b"], ["c", "d"]]}:
    bad.append("gold_clusters %r" % r["gold_clusters"])
if abs(r["pairwise"]["precision"] - 1 / 3) > 1e-9 or abs(r["pairwise"]["recall"] - 0.5) > 1e-9:
    bad.append("pairwise %r" % r["pairwise"])
if abs(r["bcubed"]["precision"] - 2 / 3) > 1e-9 or abs(r["bcubed"]["recall"] - 0.75) > 1e-9:
    bad.append("bcubed %r" % r["bcubed"])
dup = write("dup.csv", ["record_id", "predicted_cluster", "true_cluster"], [["a", "p1", "t1"], ["a", "p2", "t1"]])
try:
    D.clusters_from_csv(dup); bad.append("duplicate record_id must raise ValueError")
except ValueError:
    pass
# ref: 5 in bin 0, 5 in bin 9; cur: 9 in bin 0, 1 in bin 9 -> psi([.5,.5]->[.9,.1]) over 2 bins = 0.8789
ref = write("ref.csv", ["score"], [[0.05]] * 5 + [[0.95]] * 5)
cur = write("cur.csv", ["score"], [[0.05]] * 9 + [[1.0]] * 1)
dr = D.drift_from_csv(ref, cur, bins=2)
if abs(dr["psi"] - 0.8789) > 1e-3 or dr["band"] != "major":
    bad.append("psi/band %r %r" % (dr["psi"], dr["band"]))
if dr["score_drift"]["reference"] != [0.5, 0.5] or dr["score_drift"]["current"] != [0.9, 0.1]:
    bad.append("proportions %r" % dr["score_drift"])
if dr["score_drift"]["acknowledged"] is not False or dr["n_reference"] != 10 or dr["n_current"] != 10:
    bad.append("drift fields %r" % {k: dr[k] for k in ("n_reference", "n_current")})
oob = write("oob.csv", ["score"], [[1.5]])
try:
    D.drift_from_csv(ref, oob); bad.append("score outside [0,1] must raise ValueError")
except ValueError:
    pass
q = subprocess.run([sys.executable, P, "clusters", c], capture_output=True, text=True)
if q.returncode != 0 or json.loads(q.stdout)["n_records"] != 4:
    bad.append("CLI clusters exit %d %r" % (q.returncode, q.stdout[:120]))
q = subprocess.run([sys.executable, P, "drift", ref, oob], capture_output=True, text=True)
if q.returncode != 2 or not q.stdout.startswith("NO-DATA") or "Traceback" in q.stderr:
    bad.append("CLI bad drift input must print NO-DATA and exit 2: %d %r" % (q.returncode, q.stdout[:120]))
q = subprocess.run([sys.executable, P, "--selftest"], capture_output=True, text=True)
if q.returncode != 0 or "SELFTEST PASS" not in q.stdout:
    bad.append("own selftest exit %d %r" % (q.returncode, (q.stdout + q.stderr)[-300:]))
src = open(P, encoding="utf-8").read()
if "\u2014" in src or "\u2013" in src:
    bad.append("dash in file")
if "def bcubed" in src or "def psi(" in src:
    bad.append("re-implements mdm_eval")
for z in bad:
    print("CHECK FAIL:", z)
print("CHECK PASS" if not bad else "%d CHECK FAILURE(S)" % len(bad))
sys.exit(1 if bad else 0)
