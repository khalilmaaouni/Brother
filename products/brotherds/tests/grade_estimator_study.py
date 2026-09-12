"""Grader for examples/estimator_study.py: runs a short study twice (determinism),
checks the output shape, and records the verdict the study reaches about the
tool's own precision interval. The coverage bound is the finding, not a style rule:
an interval that under-covers means M7 passes claims it should not."""
import re, subprocess, sys, time

P = "products/brotherds/examples/estimator_study.py"
bad = []
runs = []
for _ in range(2):
    t0 = time.time()
    r = subprocess.run([sys.executable, P, "--reps", "60"], capture_output=True, text=True, timeout=300)
    runs.append((r, time.time() - t0))
r, dt = runs[0]
out = r.stdout
if r.returncode != 0:
    bad.append("exit %d: %s" % (r.returncode, (out + r.stderr)[-800:]))
if runs[0][0].stdout != runs[1][0].stdout:
    bad.append("two runs differ (not deterministic)")
for key in ("replications 60", "precision interval coverage", "precision interval mean width",
            "precision mean error", "end-to-end recall mean error", "review-only recall mean error", "verdict "):
    if key not in out:
        bad.append("missing line: %s" % key)
m = re.search(r"precision interval coverage ([0-9.]+)", out)
cov = float(m.group(1)) if m else None
if cov is None or not 0.0 <= cov <= 1.0:
    bad.append("coverage not a fraction: %r" % cov)
e2e = re.search(r"end-to-end recall mean error (-?[0-9.]+)", out)
rev = re.search(r"review-only recall mean error (-?[0-9.]+)", out)
if e2e and rev and not abs(float(e2e.group(1))) < abs(float(rev.group(1))):
    bad.append("the blocking factor must reduce recall bias: end-to-end %s vs review-only %s"
               % (e2e.group(1), rev.group(1)))
q = subprocess.run([sys.executable, P, "--reps", "x"], capture_output=True, text=True)
if q.returncode != 2 or not q.stdout.startswith("NO-DATA"):
    bad.append("bad --reps must print NO-DATA and exit 2")
src = open(P, encoding="utf-8").read()
if "\u2014" in src or "\u2013" in src:
    bad.append("dash in file")
if "def stratified_estimate" in src or "def review_recall" in src:
    bad.append("re-implements mdm_eval")
if dt > 120:
    bad.append("60 replications took %.0fs" % dt)
print(out.strip())
for z in bad:
    print("CHECK FAIL:", z)
print("CHECK PASS" if not bad else "%d CHECK FAILURE(S)" % len(bad))
sys.exit(1 if bad else 0)
