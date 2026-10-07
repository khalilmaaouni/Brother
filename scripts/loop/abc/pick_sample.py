#!/usr/bin/env python3
"""A/B/C sample: the first open sub unit of each in scope unit OUTSIDE the Phase 2 scope, spec 9+, size matched.
Size = files its spec section names (backticked) + section length in hundreds of chars. Keeps the N nearest the median.
usage: pick_sample.py <launch tree> <N> > sample.json   (reads only; unreadable section = excluded, never guessed)"""
import json, os, re, statistics, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import plan_store  # noqa: E402  (scripts/loop/plan_store.py: the one landed test, finding 2 of 2026-09-27)
tree, n = sys.argv[1], int(sys.argv[2])
plan = json.load(open(os.path.join(tree, "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json")))
scores = json.load(open(os.path.expanduser("~/.claude/evidence/spec-scores.json")))
EXCLUDE = re.compile(r"^(D1|D11|D15|D3|L5f|D14|L1b|M3|M4|L5a|L5b|R2|C0|L5d)$")   # Phase 2 scope, cut session, unscored
# done check already GREEN on the base (measured 14:3x by the base red check): cannot measure a delivery
EXCLUDE_SUBS = {"M1.2"}
PATH_RE = re.compile(r"`([A-Za-z0-9_./-]+\.(?:py|sh|json|md|jsonl))`")
rows = []
for u in plan["units"]:
    if u["state"] == "DONE" or not u.get("sub_units") or not u.get("spec") or EXCLUDE.match(u["id"]): continue
    ev = u.get("evidence") or ""
    todo = [s for s in u["sub_units"] if not plan_store.sub_landed(s, ev)]
    if not todo or (scores.get(todo[0], {}).get("score") or 0) < 9: continue
    if todo[0] in EXCLUDE_SUBS: continue
    s = todo[0]
    try: spec = open(os.path.join(tree, u["spec"]), encoding="utf-8").read()
    except OSError: continue
    h = re.search(r"^(#{2,4}) %s\b" % re.escape(s), spec, flags=re.M)
    if not h: continue
    m = re.search(r"^#{2,4} %s\b.*?(?=^#{2,%d} |\Z)" % (re.escape(s), len(h.group(1))), spec, flags=re.M | re.S)
    files = sorted(set(PATH_RE.findall(m.group(0))))
    if not files: continue
    rows.append({"unit": u["id"], "sub": s, "files": files, "n_files": len(files), "chars": len(m.group(0)),
                 "size": len(files) + len(m.group(0)) / 100.0, "score": scores[s]["score"]})
med = statistics.median(r["size"] for r in rows)
pick = sorted(rows, key=lambda r: abs(r["size"] - med))[:n]
print(json.dumps({"median_size": med, "candidates": len(rows), "sample": pick}, indent=1))   # nearest first: the validator keeps the first N valid
