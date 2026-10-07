#!/usr/bin/env python3
"""Owner order 2026-09-23 18:1x: "Make sure brother.loop supports all these models". Every registry model that can build
gets the same small JSON job through the REAL fan out (the deployed launch tree's or_fanout, same parse step as a build);
Jev, which only decides, gets a typed question through the bridge. PASS = the answer came back parsed and correct.
The prompt does not forbid code fences on purpose: each family's own answering habit is what is being tested.
usage: model_conformance.py <launch tree>   writes ab/conformance/ and prints one line per model."""
import json, os, subprocess, sys, time
tree = sys.argv[1]; AB = os.path.dirname(os.path.abspath(__file__)); D = os.path.join(AB, "conformance"); os.makedirs(D, exist_ok=True)
sys.path.insert(0, os.path.dirname(AB)); import model_router as R   # the router's view: it fills in kinds; model_router.py lives one directory up, in scripts/loop itself
reg = R.registry()
builders = [m for m, v in reg.items() if "build" in (v.get("kinds") or set())]
prompt = os.path.join(D, "prompt.md")
open(prompt, "w").write("Compute 7 plus 5. Answer with a JSON object with exactly two keys: \"sum\" (the number) and \"ok\" (true).\n")
jobs = [{"id": "conf-%s" % m, "model": m, "prompt_file": prompt, "out": os.path.join(D, "out", "%s.json" % m),
         "expect": "json", "kind": "build", "sensitivity": "public"} for m in builders]
jf = os.path.join(D, "jobs.json"); json.dump(jobs, open(jf, "w"), indent=1)
t0 = time.time()
r = subprocess.run([sys.executable, "-B", "-m", "plugin.runtime.brother.core.or_fanout", jf, "--workers", str(len(jobs)), "--timeout", "420",
                    "--retries", "0", "--results", os.path.join(D, "results.json")], cwd=tree, capture_output=True, text=True, timeout=1500)
open(os.path.join(D, "fanout.log"), "w").write(r.stdout + r.stderr)
try:
    res = json.load(open(os.path.join(D, "results.json")))
    res = res if isinstance(res, list) else res.get("results", [])
except (OSError, ValueError):
    res = []
by = {x.get("model") or x.get("id", "").replace("conf-", ""): x for x in res}
rows = []
for m in builders:
    x = by.get(m, {}); out = os.path.join(D, "out", "%s.json" % m); verdict, why = "FAIL", x.get("error") or "no result row"
    if os.path.isfile(out):
        try:
            a = json.load(open(out)); verdict, why = ("PASS", "parsed, sum 12") if a.get("sum") == 12 and a.get("ok") is True else ("FAIL", "parsed but wrong: %s" % a)
        except ValueError as exc:
            why = "output not JSON: %s" % exc
    rej = out + ".rejected.txt"
    rows.append({"model": m, "id": reg[m].get("id"), "transport": reg[m].get("transport"), "verdict": verdict, "why": str(why)[:140],
                 "seconds": x.get("seconds"), "rejected_saved": os.path.isfile(rej)})
# Jev: a decide model, typed input only
jq = json.dumps({"state": {"fact": "7 plus 5"}, "questions": {"ok": {"type": "noul", "instructions": "Answer true if 7 plus 5 is 12."}}})
try:
    R.assert_may_send("jev", "public", "decide", reg)   # the transport allowlist (BROTHER_TRANSPORTS) and the privacy gate, before the bridge
    j = subprocess.run([sys.executable, os.path.expanduser("~/.claude/bin/or_ask.py"), "--model", "jev", "--timeout", "120"], input=jq, capture_output=True, text=True, timeout=300)
    jv, jwhy = ("PASS" if j.returncode == 0 and j.stdout.strip() else "FAIL"), (j.stdout.strip() or j.stderr.strip())[:140]
except R.Refused as exc:
    jv, jwhy = "NO-DATA", ("not sent: %s" % exc)[:140]
rows.append({"model": "jev", "id": reg.get("jev", {}).get("id"), "transport": "bridge (decide)", "verdict": jv,
             "why": jwhy, "seconds": None, "rejected_saved": None})
json.dump({"at": time.strftime("%F %T"), "wall_seconds": round(time.time() - t0), "rows": rows}, open(os.path.join(D, "CONFORMANCE.json"), "w"), indent=1)
for x in rows:
    print("%-5s %-9s %-26s %-16s %6s s  %s" % (x["verdict"], x["model"], x["id"], x["transport"], x["seconds"], x["why"]))
print("%d of %d PASS" % (sum(1 for x in rows if x["verdict"] == "PASS"), len(rows)))
