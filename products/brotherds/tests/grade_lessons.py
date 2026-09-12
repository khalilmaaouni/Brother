"""Independent grader for lessons.py."""
import subprocess, sys

sys.path.insert(0, "products/brotherds")
import lessons as L  # noqa: E402

bad = []


def f(g, v, d="d"):
    return {"gate": g, "verdict": v, "detail": d}


R = [
    {"claim_id": "C1", "claim_type": "MASTER_DATA", "findings": [f("M8.recall_evidence", "FAIL", "claimed 0.99 over 250"), f("M7.x", "PASS")]},
    {"claim_id": "C2", "claim_type": "MASTER_DATA", "findings": [f("M8.recall_evidence", "FAIL", "b"), f("M8.recall_evidence", "FAIL", "c"), f("M16.metric_choice", "FAIL")]},
    {"claim_id": "C3", "claim_type": "FORECAST", "findings": [f("M8.recall_evidence", "NO-DATA"), f("G3.uncertainty", "FAIL"), f("M16.metric_choice", "FAIL")]},
    {"claim_id": "C4", "claim_type": "MASTER_DATA", "findings": [f("M12.fs_parameters", "FAIL")]},
]
items = L.recurring_failures(R)
got = [(i["gate"], i["claims"], i["count"]) for i in items]
want = [("M16.metric_choice", ["C2", "C3"], 2), ("M8.recall_evidence", ["C1", "C2"], 2)]
if got != want:
    bad.append("recurring_failures order/content: %r" % got)
m8 = [i for i in items if i["gate"] == "M8.recall_evidence"][0]
if abs(m8["share"] - 0.5) > 1e-9 or m8["example_detail"] != "claimed 0.99 over 250" or m8["claim_types"] != ["MASTER_DATA"]:
    bad.append("item fields: %r" % m8)
if len(L.recurring_failures(R, min_claims=1)) != 4:
    bad.append("min_claims=1 should keep 4 gates")
if L.recurring_failures([]) != []:
    bad.append("empty input")
for fn, msg in ((lambda: L.recurring_failures(R, 0), "min_claims 0"),
                (lambda: L.recurring_failures([{"findings": []}]), "missing claim_id"),
                (lambda: L.recurring_failures([{"claim_id": "x", "findings": "no"}]), "findings not list")):
    try:
        fn(); bad.append("%s must raise ValueError" % msg)
    except ValueError:
        pass
for n in range(1, 23):
    if "M%d." % n not in L.GUIDANCE:
        bad.append("GUIDANCE lacks M%d." % n)
for g in ("G3.", "G5.", "G9.", "G10.", "G11.", "G13."):
    if g not in L.GUIDANCE:
        bad.append("GUIDANCE lacks " + g)
if L.guidance_for("M12.fs_parameters") != L.GUIDANCE["M12."]:
    bad.append("longest prefix: M12 must not answer with M1")
if "below" not in L.guidance_for("M8.recall_evidence").lower():
    bad.append("M8 guidance should mention sampling below the threshold")
if not L.guidance_for("Z9.unknown"):
    bad.append("unknown gate guidance empty")
t, b = L.lesson_text(m8)
if t != "Recurring FAIL M8.recall_evidence in 2 claims":
    bad.append("title %r" % t)
if "Claims: C1, C2" not in b or "What to do next time: " not in b or "a person decides" not in b:
    bad.append("body lines: %r" % b)
ex = [l for l in b.splitlines() if l.startswith("Example finding: ")]
if not ex or any(ch.isdigit() for ch in ex[0]):
    bad.append("example finding must have digits redacted: %r" % ex)
if "50%" not in b and "50 percent" not in b:
    bad.append("share as a percent missing: %r" % b)
k1 = L.lesson_key(m8)
m8r = dict(m8); m8r["claims"] = list(reversed(m8["claims"]))
if k1 != L.lesson_key(m8r) or not k1.startswith("bds-recurring:M8.recall_evidence:") or len(k1.split(":")[-1]) != 12:
    bad.append("lesson_key stability/format: %r" % k1)
if L.new_items(items, {k1}) != [i for i in items if i["gate"] != "M8.recall_evidence"]:
    bad.append("new_items filter")
many = {"gate": "M7.x", "claims": ["C%02d" % i for i in range(25)], "count": 25, "share": 1.0,
        "claim_types": ["MASTER_DATA"], "example_detail": "x"}
if "and 5 more" not in L.lesson_text(many)[1]:
    bad.append("claims list must truncate at 20 with 'and 5 more'")
# R3 regressions
dup = [{"claim_id": "c1", "claim_type": "M", "findings": [f("M1.x", "FAIL")]},
       {"claim_id": "c1", "claim_type": "M", "findings": [f("M1.x", "FAIL")]}]
dd = L.recurring_failures(dup, min_claims=1)
if not dd or abs(dd[0]["share"] - 1.0) > 1e-9:
    bad.append("share must be over distinct claim ids: %r" % [(i["count"], i["share"]) for i in dd])
try:
    L.recurring_failures([{"claim_id": "c1", "findings": [{"gate": 123, "verdict": "FAIL"}]}], 1)
    bad.append("a non-string gate must raise ValueError")
except ValueError:
    pass
except Exception as ex:
    bad.append("a non-string gate raised %s, want ValueError" % type(ex).__name__)
if L.lesson_key({"gate": "M1.x", "claims": ["a,b", "c"]}) == L.lesson_key({"gate": "M1.x", "claims": ["a", "b,c"]}):
    bad.append("lesson_key collides when claim ids contain commas")
c = subprocess.run([sys.executable, "products/brotherds/lessons.py", "--selftest"], capture_output=True, text=True)
if c.returncode != 0 or "SELFTEST PASS" not in c.stdout:
    bad.append("own selftest exit %d %r" % (c.returncode, (c.stdout + c.stderr)[-300:]))
src = open("products/brotherds/lessons.py", encoding="utf-8").read()
if "\u2014" in src or "\u2013" in src:
    bad.append("dash in file")
for z in bad:
    print("CHECK FAIL:", z)
print("CHECK PASS" if not bad else "%d CHECK FAILURE(S)" % len(bad))
sys.exit(1 if bad else 0)
