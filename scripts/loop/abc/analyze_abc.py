#!/usr/bin/env python3
"""The A/B/C table, computed only from recorded files (endpoint.json + blind-map.json, or-meter.jsonl, claude-*.json,
start/end files, c-turns.tsv, INTERVENTIONS.md, prices.json). A missing input prints NO-DATA in its cell, never a zero.
usage: analyze_abc.py [--unblind]    writes ab/RESULT.json and prints the table."""
import json, math, os, random, re, statistics, sys, datetime
AB = os.path.dirname(os.path.abspath(__file__))
P = json.load(open(os.path.join(AB, "prices.json")))["usd_per_million"]
def load(n, default=None):
    try: return json.load(open(os.path.join(AB, n)))
    except (OSError, ValueError): return default
def ts(n):
    try: return datetime.datetime.strptime(open(os.path.join(AB, n)).read().strip()[:19], "%Y-%m-%d %H:%M:%S")
    except (OSError, ValueError): return None
def usd(by_model):
    tot, unpriced = 0.0, []
    for m, v in (by_model or {}).items():
        p = P.get(m)
        if not p: unpriced.append(m); continue
        tot += (v["input"] * p["input"] + v["output"] * p["output"] + v["cache_write_5m"] * p["cache_write_5m"]
                + v["cache_write_1h"] * p["cache_write_1h"] + v["cache_read"] * p["cache_read"]) / 1e6
    return tot, unpriced
def or_delta(a):
    rows = [json.loads(l) for l in open(os.path.join(AB, "or-meter.jsonl"))] if os.path.exists(os.path.join(AB, "or-meter.jsonl")) else []
    s = [r for r in rows if r["label"] == "start-%s" % a]; e = [r for r in rows if r["label"] == "end-%s" % a]
    return (e[-1]["usage"] - s[-1]["usage"]) if s and e else None
def killed_usd(cw):
    tot = 0.0
    for m, c in ((cw or {}).get("calls") or {}).get("by_model", {}).items() if isinstance((cw or {}).get("calls"), dict) else []:
        per = c.get("ESTIMATE_per_missing_call_mean_of_recorded")
        if per and c["calls_without_usage"]: tot += usd({m: dict(per, messages=0)})[0] * c["calls_without_usage"]
    return tot
def mcnemar_exact(b, c):
    n = b + c
    if n == 0: return 1.0
    k = min(b, c); return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)
rows = load("endpoint.json", []); bm = load("blind-map.json", {}); inv = {v: k for k, v in bm.items()}
unblind = "--unblind" in sys.argv
arms = sorted({r["arm"] for r in rows})
out = {}
for L in arms:
    a = inv.get(L)
    R = [r for r in rows if r["arm"] == L]; S = [r for r in R if not r.get("extra")]
    st, en = ts("start-%s.txt" % a), ts("end-%s.txt" % a)
    hours = (en - st).total_seconds() / 3600 if st and en else None
    deliv = [r for r in R if r["verdict"] == "DELIVERED"]; sd = [r for r in S if r["verdict"] == "DELIVERED"]
    cw = load("claude-%s.json" % a); orch = load("claude-orch-%s.json" % a); mach = load("claude-machine-%s.json" % a)
    c_usd, unpriced = usd((cw or {}).get("by_model")); k_usd = killed_usd(cw)
    o_usd = usd((orch or {}).get("by_model"))[0] if orch else None
    m_usd = usd((mach or {}).get("by_model"))[0] if mach else None
    orr = or_delta(a)
    total = (orr or 0) + c_usd + k_usd if orr is not None and cw else None
    mut = [r["mutations"] for r in deliv if isinstance(r.get("mutations"), dict)]
    kill = (sum(x["total"] - x["survived"] for x in mut), sum(x["total"] for x in mut))
    mins = [r["minutes_to_deliver"] for r in deliv if r.get("minutes_to_deliver") is not None]
    # bootstrap over the sample sub units: cost per delivered, 2000 resamples, seeded
    boot = []
    if total and S:
        rng = random.Random("abc-boot-" + L); share = total / len(S)
        for _ in range(2000):
            pick = [rng.choice(S) for _ in S]; d = sum(1 for r in pick if r["verdict"] == "DELIVERED")
            if d: boot.append(share * len(pick) / d)
    boot.sort()
    o = {"arm": a if unblind else L, "hours": round(hours, 3) if hours else "NO-DATA",
         "sample_delivered": "%d/%d" % (len(sd), len(S)), "extras_delivered": len(deliv) - len(sd),
         "no_data_cells": sum(1 for r in R if r["verdict"] == "NO-DATA"),
         "median_minutes_to_deliver": statistics.median(mins) if mins else "NO-DATA",
         "delivered_per_hour": round(len(deliv) / hours, 2) if hours else "NO-DATA",
         "openrouter_usd_meter": round(orr, 4) if orr is not None else "NO-DATA",
         "claude_usd_recorded": round(c_usd, 4) if cw else "NO-DATA", "claude_usd_killed_ESTIMATE": round(k_usd, 4),
         "unpriced_models": unpriced, "orchestrator_usd": round(o_usd, 4) if o_usd is not None else "NO-DATA",
         "machine_claude_usd_excl_orch": round(m_usd, 4) if m_usd is not None else "NO-DATA",
         "total_usd_excl_orch": round(total, 4) if total is not None else "NO-DATA",
         "usd_per_delivered": round(total / len(deliv), 4) if total is not None and deliv else "NO-DATA",
         "usd_per_delivered_CI90": [round(boot[int(0.05 * len(boot))], 3), round(boot[int(0.95 * len(boot)) - 1], 3)] if len(boot) > 20 else "NO-DATA",
         "mutation_kill": "%d/%d" % kill if kill[1] else "NO-DATA"}
    if a == "c":
        turns = [l.rstrip("\n").split("\t") for l in open(os.path.join(AB, "c-turns.tsv")) if l.count("\t") == 4] if os.path.exists(os.path.join(AB, "c-turns.tsv")) else []
        o["turns"] = len(turns)
        for h in (2, 5):   # human review minutes per turn, applied to every turn before each delivery
            adj = []
            for r in deliv:
                k = 0
                for i, t in enumerate(turns):
                    k = i + 1
                    if t[0] == r["sub"] and t[1] == "commit": break
                if r.get("minutes_to_deliver") is not None: adj.append(r["minutes_to_deliver"] + h * k)
            within = sum(1 for x in adj if hours and x <= hours * 60)
            o["human_%dmin_per_turn_delivered_in_slot" % h] = within
    try:
        o["interventions"] = sum(1 for l in open(os.path.join(AB, "INTERVENTIONS.md")) if l.startswith("|") and ("| %s |" % a) in l)
    except OSError:
        o["interventions"] = "NO-DATA"
    out[L] = o
pairs = {}
subs = sorted({r["sub"] for r in rows if not r.get("extra")})
for i, x in enumerate(arms):
    for y in arms[i + 1:]:
        dx = {r["sub"]: r["verdict"] == "DELIVERED" for r in rows if r["arm"] == x and not r.get("extra")}
        dy = {r["sub"]: r["verdict"] == "DELIVERED" for r in rows if r["arm"] == y and not r.get("extra")}
        b = sum(1 for s in subs if dx.get(s) and not dy.get(s)); c = sum(1 for s in subs if dy.get(s) and not dx.get(s))
        pairs["%s vs %s" % ((inv.get(x) if unblind else x), (inv.get(y) if unblind else y))] = {"only_first": b, "only_second": c, "mcnemar_exact_p": round(mcnemar_exact(b, c), 4)}
res = {"arms": out, "paired_on_sample": pairs, "blind": not unblind}
json.dump(res, open(os.path.join(AB, "RESULT%s.json" % ("-unblind" if unblind else "")), "w"), indent=1)
print(json.dumps(res, indent=1))
