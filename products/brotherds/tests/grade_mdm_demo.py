"""Independent grader for examples/mdm_demo.py: runs it twice, checks determinism, verdicts, time."""
import re, subprocess, sys, time

p = "products/brotherds/examples/mdm_demo.py"
bad = []
outs = []
for i in range(2):
    t0 = time.time()
    r = subprocess.run([sys.executable, p], capture_output=True, text=True, timeout=120)
    dt = time.time() - t0
    outs.append(r.stdout)
    if r.returncode != 0:
        bad.append("run %d exit %d: %s" % (i, r.returncode, (r.stdout + r.stderr)[-1500:]))
        break
    if dt > 30:
        bad.append("run %d took %.1fs > 30s" % (i, dt))
if len(outs) == 2 and outs[0] != outs[1]:
    bad.append("two runs differ (not deterministic)")
o = outs[0] if outs else ""
verdicts = re.findall(r"^\s*VERDICT (\S+)", o, re.M)
if len(verdicts) != 2:
    bad.append("expected 2 VERDICT lines, got %s" % verdicts)
elif verdicts[1] != "FAIL" or verdicts[0] == "FAIL":
    bad.append("verdicts %s, want honest not FAIL then hurried FAIL" % verdicts)
for g in ("M7.precision_evidence", "M8.recall_evidence", "M16.metric_choice", "M19.labeller_agreement"):
    if o.count(g) < 2:
        bad.append("gate %s not printed for both claims" % g)
if "/tmp" in o or "/var/folders" in o or "/Users/" in o:
    bad.append("output leaks a filesystem path")
src = open(p, encoding="utf-8").read()
if "\u2014" in src or "\u2013" in src:
    bad.append("dash in file")
if bad and o:
    print("DEMO OUTPUT (tail):\n" + o[-2500:])
for z in bad:
    print("CHECK FAIL:", z)
print("CHECK PASS" if not bad else "%d CHECK FAILURE(S)" % len(bad))
sys.exit(1 if bad else 0)
