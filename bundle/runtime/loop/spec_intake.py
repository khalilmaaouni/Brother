#!/usr/bin/env python3
"""Intake NEW unit specifications (units with no sub units yet) from drafts, under the owner's 9 of 10 rule.
usage (repo root): spec_intake.py <drafts dir> [--skip UNIT ...]        drafts are <unit>-spec-<model>.md
For each unit and each draft: write docs/plan/specs/<unit>.md, give the plan its spec path and the sub unit ids read from the
draft's own '### <UNIT>.<n> title' headings, score it. KEEP only if: 3 to 9 sub units, EVERY one at 9 or more, the scorer's tree
truth points hold (PATHS TRUE, CALLS TRUE), no private term, no long dash. Otherwise BOTH files are restored byte for byte.
The best acceptable draft wins (highest minimum, then total). Prints one line per draft with the reason."""
import glob, json, os, re, subprocess, sys
BIN = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, BIN)
import grade_build as G
drafts = os.path.abspath(os.path.expanduser(sys.argv[1]))
skip = set(sys.argv[sys.argv.index("--skip") + 1:]) if "--skip" in sys.argv else set()
PLAN = "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"
tmp = os.path.expanduser("~/.claude/evidence/spec-intake-scores.json")


def dump(plan):
    open(PLAN, "w", encoding="utf-8").write(json.dumps(plan, indent=1, ensure_ascii=False) + "\n")


kept = []
for uid in sorted({re.sub(r"-spec-\w+\.md$", "", os.path.basename(f)) for f in glob.glob(os.path.join(drafts, "*-spec-*.md"))}):
    plan_text = open(PLAN, encoding="utf-8").read(); plan = json.loads(plan_text)
    unit = next((u for u in plan["units"] if u["id"] == uid), None)
    if unit is None or uid in skip or unit.get("sub_units"):
        print("%-5s skipped (%s)" % (uid, "in the skip list: no real code was shown to its drafters" if uid in skip else "already has sub units" if unit else "not in the plan")); continue
    path = "docs/plan/specs/%s.md" % uid
    existed = open(path, encoding="utf-8").read() if os.path.isfile(path) else None
    best = None
    for f in sorted(glob.glob(os.path.join(drafts, uid + "-spec-*.md"))):
        text = re.sub(r"^```(?:markdown|md)?\n|\n```\s*$", "", open(f, encoding="utf-8").read().strip()) + "\n"
        subs = list(dict.fromkeys(re.findall(r"^#{2,4} (%s[.-][A-Za-z0-9]+)\b" % re.escape(uid), text, flags=re.M)))
        why = None
        if G.private_hits(text) or re.search("[\u2014\u2013]", text):
            why = "private term or long dash"
        elif not 3 <= len(subs) <= 9:
            why = "%d sub unit headings (needs 3 to 9)" % len(subs)
        if why is None:
            open(path, "w", encoding="utf-8").write(text)
            unit["spec"], unit["sub_units"] = path, subs; dump(plan)
            r = subprocess.run([sys.executable, os.path.join(BIN, "spec_score.py"), uid, "--json", tmp], capture_output=True, text=True)
            try:
                sc = {} if r.returncode else json.load(open(tmp))
            except (OSError, ValueError):
                sc = {}
            if r.returncode:
                print("spec_score exit %d for %s: every section reads unscored" % (r.returncode, uid))
            low = [s for s in subs if sc.get(s, {}).get("score", 0) < 9]
            untrue = [s for s in subs if any(m.startswith(("PATHS TRUE", "CALLS TRUE")) for m in sc.get(s, {}).get("missing", []))]
            if low:
                why = "under 9: " + ", ".join("%s %s/10 (%s)" % (s, sc.get(s, {}).get("score"), "; ".join(sc.get(s, {}).get("missing", []))[:70]) for s in low[:2])
            elif untrue:
                why = "untrue about the tree in %s" % untrue[:3]
            else:
                key = (min(sc[s]["score"] for s in subs), sum(sc[s]["score"] for s in subs))
                if best is None or key > best[0]:
                    best = (key, f, text, subs)
                why = "ACCEPTABLE, %d sub units, minimum %d of 10" % (len(subs), key[0])
            # restore both files before judging the next draft
            open(PLAN, "w", encoding="utf-8").write(plan_text); plan = json.loads(plan_text); unit = next(u for u in plan["units"] if u["id"] == uid)
            if existed is None:
                os.remove(path)
            else:
                open(path, "w", encoding="utf-8").write(existed)
        print("%-5s %-26s %s" % (uid, os.path.basename(f), why))
    if best:
        open(path, "w", encoding="utf-8").write(best[2])
        unit["spec"], unit["sub_units"] = path, best[3]
        if unit.get("state") == "OPEN":
            unit["state"] = "SPECIFIED"
        dump(plan); kept.append(uid)
        print("%-5s KEPT %s: %s" % (uid, os.path.basename(best[1]), best[3]))
print("\nintake kept %d unit(s): %s" % (len(kept), kept))
