#!/usr/bin/env python3
"""Keep the first N candidates (nearest the median size) whose done check exists and is RED on the base commit.
usage: validate_sample.py <candidates.json> <BASE sha> <N> > sample.json   (exit 1 when fewer than N are valid)"""
import json, os, sys
cand, base, n = json.load(open(sys.argv[1])), sys.argv[2], int(sys.argv[3])
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "score_arms.py")).read().split("def main")[0]
src = src.replace('BASE = open(os.path.join(AB, "BASE.txt")).read().strip()', 'BASE = None').replace('SAMPLE = json.load(open(os.path.join(AB, "sample.json")))["sample"]', 'SAMPLE = []')
ns = {}; exec(src, ns)
b = ns["worktree"](base); keep, dropped = [], []
try:
    for r in cand["sample"]:
        if len(keep) == n: break
        c = ns["done_check"](b, r["unit"], r["sub"])
        if not c: dropped.append((r["sub"], "no done check found")); continue
        if ns["green_both"](b, c)[0]: dropped.append((r["sub"], "green on base")); continue
        keep.append(dict(r, check=c))
finally:
    ns["drop"](b)
print(json.dumps({"base": base, "dropped": dropped, "sample": keep}, indent=1))
sys.exit(0 if len(keep) == n else 1)
