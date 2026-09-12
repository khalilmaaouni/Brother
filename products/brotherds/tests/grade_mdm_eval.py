"""Independent grader for mdm_eval.py: reference values computed by the orchestrator,
never by the model under test. Run from the worktree root."""
import csv, importlib.util, os, subprocess, sys, tempfile

p = "products/brotherds/mdm_eval.py"
spec = importlib.util.spec_from_file_location("mdm_eval", p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
bad = []


def eq(got, want, msg, tol=1e-4):
    if isinstance(want, tuple):
        ok = all(abs(g - w) <= tol for g, w in zip(got, want))
    elif want is None or isinstance(want, (bool, str)):
        ok = got == want
    else:
        ok = got is not None and abs(got - want) <= tol
    if not ok:
        bad.append("%s: got %r want %r" % (msg, got, want))


def raises(fn, msg):
    try:
        fn()
    except ValueError:
        return
    except Exception as e:  # wrong type is a failure too
        bad.append("%s: raised %s, want ValueError" % (msg, type(e).__name__))
        return
    bad.append("%s: did not raise" % msg)


eq(m.wilson_interval(0, 10), (0.0, 0.2775), "wilson 0/10")
eq(m.wilson_interval(5, 10), (0.2366, 0.7634), "wilson 5/10")
eq(m.wilson_interval(10, 10)[1], 1.0, "wilson 10/10 hi")
raises(lambda: m.wilson_interval(1, 0), "wilson n=0")
raises(lambda: m.wilson_interval(11, 10), "wilson k>n")
eq(m.clopper_pearson(0, 10)[1], 0.3085, "cp 0/10 hi")
eq(m.clopper_pearson(0, 10)[0], 0.0, "cp 0/10 lo")
eq(m.clopper_pearson(10, 10)[1], 1.0, "cp 10/10 hi")
eq(m.clopper_pearson(10, 10)[0], 0.6915, "cp 10/10 lo")
pr, tr = [["a", "b", "c"], ["d"]], [["a", "b"], ["c", "d"]]
pw = m.pairwise_metrics(pr, tr)
eq(pw["precision"], 0.3333, "pairwise P"); eq(pw["recall"], 0.5, "pairwise R")
eq(pw["tp"], 1, "tp"); eq(pw["fp"], 2, "fp"); eq(pw["fn"], 1, "fn")
eq(m.pairwise_metrics([["a"], ["b"]], [["a"], ["b"]])["precision"], None, "pairwise P none")
b3 = m.bcubed(pr, tr)
eq(b3["precision"], 0.6667, "bcubed P"); eq(b3["recall"], 0.75, "bcubed R")
eq(b3["f1"], 2 * 0.66667 * 0.75 / (0.66667 + 0.75), "bcubed F1", 1e-3)
raises(lambda: m.bcubed([["a", "x"]], [["a"]]), "bcubed mismatched ids")
bl = m.blocking_metrics(100, 495, 50, 45)
eq(bl["possible_pairs"], 4950, "possible"); eq(bl["reduction_ratio"], 0.9, "RR")
eq(bl["pair_completeness"], 0.9, "PC"); eq(bl["pairs_quality"], 0.0909, "PQ")
raises(lambda: m.blocking_metrics(100, 10, 50, 45), "true_in_cand > cand")
eq(m.psi([0.5, 0.5], [0.9, 0.1]), 0.8789, "psi")
eq(m.psi([1, 1], [1, 1]), 0.0, "psi identical, unnormalised")
eq(m.psi_band(0.05), "stable", "band"); eq(m.psi_band(0.2), "moderate", "band2"); eq(m.psi_band(0.3), "major", "band3")
raises(lambda: m.psi([1.0], [1.0]), "psi one bin")
fw = m.fs_weights(0.9, 0.01)
eq(fw["agree"], 6.4919, "fs agree"); eq(fw["disagree"], -3.3074, "fs disagree")
raises(lambda: m.fs_weights(1.0, 0.1), "fs m=1")
st = m.stratified_estimate([{"population": 800, "sampled": 100, "positive": 90},
                            {"population": 200, "sampled": 100, "positive": 50}])
eq(st["estimate"], 0.8 * 0.9 + 0.2 * 0.5, "strat est")
var = 0.64 * 0.9 * 0.1 / 100 * (1 - 100 / 800) + 0.04 * 0.25 / 100 * (1 - 100 / 200)
eq(st["se"], var ** 0.5, "strat se")
raises(lambda: m.stratified_estimate([{"population": 10, "sampled": 0, "positive": 0}]), "strat empty")
above_only = [{"score_lo": 0.8, "score_hi": 1.0, "population": 1000, "sampled": 100, "positive": 95}]
rr = m.review_recall(above_only, 0.8)
eq(rr["recall"], None, "recall none when above-only"); eq(rr["covers_below_threshold"], False, "covers flag")
both = above_only + [{"score_lo": 0.5, "score_hi": 0.8, "population": 2000, "sampled": 100, "positive": 5}]
rr2 = m.review_recall(both, 0.8)
eq(rr2["matches_above"], 950.0, "m above"); eq(rr2["matches_total"], 1050.0, "m total")
eq(rr2["recall"], 950 / 1050, "recall")
cs = m.cluster_size_profile([1] * 90 + [2] * 8 + [300, 5])
eq(cs["max_size"], 300, "max"); eq(cs["giant_clusters"], 1, "giant"); eq(cs["n_records"], 90 + 16 + 305, "n rec")
raises(lambda: m.cluster_size_profile([]), "sizes empty")

# R1 regressions (Muse review, reproduced by the orchestrator 2026-09-12)
s1 = m.stratified_estimate([{"population": 100, "sampled": 10, "positive": 10}])
if not (s1["lo"] < 0.99 and abs(s1["lo"] - 0.7225) < 2e-3 and s1["hi"] == 1.0):
    bad.append("stratified p=1 must give a Wilson-type interval (lo ~0.7225, hi 1.0), got %r" % ((s1["lo"], s1["hi"]),))
raises(lambda: m.stratified_estimate([{"population": 10, "sampled": 20, "positive": 19}]), "strat sampled>population")
raises(lambda: m.stratified_estimate([{"population": 100, "sampled": 10, "positive": 11}]), "strat positive>sampled")
raises(lambda: m.bcubed([["a", "b"], ["b", "c"]], [["a", "b", "c"]]), "bcubed overlapping clusters")
raises(lambda: m.pairwise_metrics([["a", "b"], ["b", "c"]], [["a", "b", "c"]]), "pairwise overlapping clusters")
raises(lambda: m.blocking_metrics(10, 50, 5, 5), "blocking candidates > possible pairs")
raises(lambda: m.psi([-0.5, 1.5], [0.5, 0.5]), "psi negative proportion")
raises(lambda: m.psi([0, 0], [0.5, 0.5]), "psi zero-sum reference")
raises(lambda: m.psi([float("nan"), 1], [0.5, 0.5]), "psi nan")
try:
    cpl = m.clopper_pearson(2000, 4000)
    eq(cpl, (0.4845, 0.5155), "cp 2000/4000 (large n must not overflow)", tol=1.5e-3)
except Exception as ex:
    bad.append("clopper_pearson(2000, 4000) raised %s" % type(ex).__name__)

d = tempfile.mkdtemp()
f = os.path.join(d, "r.csv")
with open(f, "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["score", "label"])
    for i in range(20):
        w.writerow(["0.95", "match" if i < 19 else "non-match"])
    for i in range(20):
        w.writerow(["0.55", "match" if i < 2 else "non-match"])
ev = m.evaluate_review_csv(f, 0.8)
eq(ev["n_rows"], 40, "csv rows")
eq(ev["precision_above"]["estimate"], 0.95, "csv precision")
eq(ev["recall"]["recall"], 19 / 21, "csv recall")
g = os.path.join(d, "bad.csv")
with open(g, "w") as fh:
    fh.write("score,label\n0.9,maybe\n")
raises(lambda: m.evaluate_review_csv(g, 0.8), "csv bad label")
c = subprocess.run([sys.executable, p, "review", g, "--merge-threshold", "0.8"], capture_output=True, text=True)
if c.returncode != 2 or not c.stdout.startswith("NO-DATA") or "Traceback" in c.stderr:
    bad.append("CLI bad input must print NO-DATA and exit 2: exit %d out %r" % (c.returncode, c.stdout[:120]))
c = subprocess.run([sys.executable, p, "--selftest"], capture_output=True, text=True)
if c.returncode != 0 or "SELFTEST PASS" not in c.stdout:
    bad.append("own selftest: exit %d tail %r" % (c.returncode, (c.stdout + c.stderr)[-300:]))
src = open(p, encoding="utf-8").read()
if "\u2014" in src or "\u2013" in src:
    bad.append("file contains an em or en dash")
for mod in ("numpy", "scipy", "pandas"):
    if "import " + mod in src:
        bad.append("third-party import: " + mod)

for x in bad:
    print("CHECK FAIL:", x)
print("CHECK PASS" if not bad else "%d CHECK FAILURE(S)" % len(bad))
sys.exit(1 if bad else 0)
