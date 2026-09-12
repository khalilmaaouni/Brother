"""Independent grader for forecast_score.py (orchestrator's reference values)."""
import importlib.util, subprocess, sys

p = "products/brotherds/forecast_score.py"
s = importlib.util.spec_from_file_location("fs", p)
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)
bad = []


def near(a, b, msg, tol=1e-4):
    if not isinstance(a, (int, float)) or abs(a - b) > tol:
        bad.append("%s: got %r want %r" % (msg, a, b))


def raises(fn, msg):
    try:
        fn()
    except ValueError:
        return
    except Exception as e:
        bad.append("%s: raised %s not ValueError" % (msg, type(e).__name__)); return
    bad.append("%s: did not raise" % msg)


Q = {"0.1": 80, "0.5": 100, "0.9": 130}
a = m.wis(Q, 100); near(a["wis"], 3.3333, "wis y=100"); near(a["dispersion"], 3.3333, "disp")
b = m.wis(Q, 200)
near(b["wis"], 83.3333, "wis y=200"); near(b["underprediction"], 80.0, "under"); near(b["overprediction"], 0.0, "over")
near(b["dispersion"] + b["underprediction"] + b["overprediction"], b["wis"], "decomposition sums", 1e-9)
c = m.wis(Q, 50)  # below: over = (0.5*50 + 0.1*10*30)/1.5 = 55/1.5
near(c["overprediction"], 55 / 1.5, "over y=50"); near(c["wis"], 5 / 1.5 + 55 / 1.5, "wis y=50")
Q2 = {"0.05": 70, "0.1": 80, "0.25": 90, "0.5": 100, "0.75": 110, "0.9": 130, "0.97": 150}
d = m.wis(Q2, 100)
if d["n_intervals"] != 2 or d["ignored"] != ["0.05", "0.97"]:
    bad.append("pairs: n %r ignored %r" % (d["n_intervals"], d["ignored"]))
# K=2: alphas .2 (80,130) and .5 (90,110); y=100 inside both: (0.1*50 + 0.25*20)/2.5 = 4.0
near(d["wis"], 4.0, "wis two intervals")
near(m.wis(Q, 100)["relative_wis"], 0.033333, "relative")
if not str(m.wis({"0.1": -1, "0.5": 0, "0.9": 1}, 0)["relative_wis"]).startswith("NO-DATA"):
    bad.append("relative_wis with zero median must be NO-DATA")
raises(lambda: m.wis({"0.1": 80, "0.9": 130}, 1), "no median")
raises(lambda: m.wis({"0.1": 90, "0.5": 80, "0.9": 130}, 1), "non-monotone")
raises(lambda: m.wis({"x": 1, "0.5": 2}, 1), "bad key")
raises(lambda: m.wis({"1.5": 1, "0.5": 2}, 1), "prob out of range")
near(m.interval_score(80, 130, 200, 0.2), 750.0, "IS")
raises(lambda: m.interval_score(130, 80, 1, 0.2), "IS lo>hi")
near(m.pinball_loss(0.9, 130, 200), 63.0, "pinball")
raises(lambda: m.pinball_loss(1.0, 1, 1), "pinball tau")
if m.band_hit(Q, 100) is not True or m.band_hit(Q, 200) is not False or m.band_hit({"0.5": 1}, 1) is not None:
    bad.append("band_hit")
if m.coverage([True] * 8 + [False] * 2)["verdict"] != "calibrated":
    bad.append("coverage calibrated")
if m.coverage([True] * 5 + [False] * 15)["verdict"] != "over-confident":
    bad.append("coverage over-confident")
if m.coverage([True] * 200)["verdict"] != "under-confident":
    bad.append("coverage under-confident")
if not m.coverage([True] * 4)["verdict"].startswith("NO-DATA"):
    bad.append("coverage small n")
cz = m.coverage([])
if cz["coverage"] is not None or cz["n"] != 0:
    bad.append("coverage empty")
# R1 regressions
raises(lambda: m.coverage([True, None, False]), "coverage non-boolean hit")
try:
    if m.band_hit({"0.1": "80", "0.9": "130"}, 100) is not True:
        bad.append("band_hit must convert numeric strings")
except Exception as ex:
    bad.append("band_hit on numeric strings raised %s" % type(ex).__name__)
raises(lambda: m.band_hit({"0.1": "x", "0.9": "130"}, 100), "band_hit non-numeric")
r = subprocess.run([sys.executable, p, "wis", "{bad", "3"], capture_output=True, text=True)
if r.returncode != 2 or not r.stdout.startswith("NO-DATA") or "Traceback" in r.stderr:
    bad.append("CLI bad input: exit %d %r" % (r.returncode, r.stdout[:100]))
r = subprocess.run([sys.executable, p, "--selftest"], capture_output=True, text=True)
if r.returncode != 0 or "SELFTEST PASS" not in r.stdout:
    bad.append("own selftest exit %d %r" % (r.returncode, (r.stdout + r.stderr)[-300:]))
if "\u2014" in open(p, encoding="utf-8").read() or "\u2013" in open(p, encoding="utf-8").read():
    bad.append("dash in file")
for x in bad:
    print("CHECK FAIL:", x)
print("CHECK PASS" if not bad else "%d CHECK FAILURE(S)" % len(bad))
sys.exit(1 if bad else 0)
