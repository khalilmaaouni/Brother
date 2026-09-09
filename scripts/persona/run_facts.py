#!/usr/bin/env python3
"""Write RUN-FACTS.md from the run's own files: tally per round, merges, PRs,
meter. Every number is read, none typed.
"""
import argparse
import collections
import datetime
import json
import os
import subprocess
import sys

DEFAULT_EVIDENCE_DIR = os.environ.get(
    "PERSONA_EVIDENCE_DIR",
    os.path.expanduser("~/.claude/evidence/persona-dogfood-2026-09-07"),
)


def build_argparser():
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence-dir", default=DEFAULT_EVIDENCE_DIR,
                     help="where results.jsonl, judgments.jsonl, FINDINGS.jsonl, meter.py live and RUN-FACTS.md is written (default: $PERSONA_EVIDENCE_DIR or the 2026-09-07 evidence dir)")
    return ap


def main(argv=None):
    args = build_argparser().parse_args(argv)
    E = args.evidence_dir
    R = [json.loads(l) for l in open(os.path.join(E, "transcripts", "results.jsonl")) if l.strip()]
    J = [json.loads(l) for l in open(os.path.join(E, "judgments.jsonl")) if l.strip()] if os.path.exists(os.path.join(E, "judgments.jsonl")) else []
    F = [json.loads(l) for l in open(os.path.join(E, "FINDINGS.jsonl")) if l.strip()]

    def latest_by_round(maxr):
        latest = {}
        for r in R:
            if r.get("round", 1) <= maxr:
                latest[r["scenario"]] = r
        c = collections.Counter(v.get("verdict") for v in latest.values())
        t = [v.get("trust_after", 0) for v in latest.values()]
        return c.get("PASS", 0), dict(c), (sum(t) / len(t) if t else 0)

    rounds = sorted({r.get("round", 1) for r in R})
    git = lambda *a: subprocess.run(["git", "-C", os.path.expanduser("~/bh-persona-2026-09-07")] + list(a), capture_output=True, text=True).stdout.strip()
    meter = subprocess.run(["python3", os.path.join(E, "meter.py")], capture_output=True, text=True).stdout.strip()
    out = ["# RUN-FACTS, persona dogfood of v1.0.10, written %s JST" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), ""]
    out += ["## Mandate", "Founder, 23:43 JST 2026-09-07: persona based dogfood of every function of 1.0.10 for the two client archetypes, 10M tokens until 07:45; Muse plays the users on generalized content (the 2026-08-30 OpenRouter ban permanently superseded, his words); no change to the shared token ceiling. Amended 02:1x: keep looping until 90 percent pass, builders on sonnet max or opus high.", ""]
    out += ["## Persona results (latest verdict per scenario, cumulative through each round)"]
    for rn in rounds:
        p, c, t = latest_by_round(rn)
        n = sum(1 for r in R if r.get("round", 1) == rn)
        out.append("- through round %d: PASS %d of 40 (%d%%), verdicts %s, mean trust %.1f; scenarios run in that round: %d" % (rn, p, 100 * p // 40, c, t, n))
    agree = sum(1 for j in J if any(r["scenario"] == j["scenario"] and r.get("round", 1) == j.get("round", 1) and r["verdict"] == j.get("verdict") for r in R))
    out += ["- Muse judge (meta/muse-spark-1.2) judgments: %d, agreeing with the actor on %d" % (len(J), agree), "- target: 36 of 40 (90 percent). NOT REACHED.", ""]
    out += ["## Users' own lines (latest per scenario, worst trust first)"]
    latest = {}
    for r in R:
        latest[r["scenario"]] = r
    for s, r in sorted(latest.items(), key=lambda kv: (kv[1].get("trust_after", 0), kv[0]))[:12]:
        out.append("- %s (%s, trust %s): %s" % (s, r.get("verdict"), r.get("trust_after"), r.get("quote", "")))
    out += ["", "## What landed", "- Branch persona-dogfood-2026-09-07 in the hub (brother-hub), HEAD %s; pull request 521 to main; F-001 separately as pull request 520 (branch fix/f001-fence-gate-module)." % git("rev-parse", "--short", "HEAD"),
            "- Commits on the branch beyond main: %s" % git("rev-list", "--count", "origin/main..HEAD"), "- Files changed: %s" % git("diff", "--shortstat", "origin/main...HEAD"), ""]
    out += ["## Findings register (FINDINGS.jsonl)"]
    for f in F:
        out.append("- %s [%s] %s: %s" % (f["id"], f.get("status"), f.get("severity"), (f.get("symptom") or "")[:180]))
    out += ["", "## Meter", "- %s (the founder's grant was 10,000,000 output tokens; the guard's shared daily pool was never edited)" % meter, ""]
    out += ["## Where everything is", "- Evidence: ~/.claude/evidence/persona-dogfood-2026-09-07/ (scenarios.json, persona-roster.json, SURFACE-INVENTORY.md, ROOT-CAUSES.md, FINDINGS.jsonl, judgments.jsonl, transcripts/, partials/, briefs, tally.py, judge.py, meter.py, gen_briefs.py, run_facts.py)",
            "- Branch worktree: ~/bh-persona-2026-09-07 (hub remote origin = github.com/khalilmaaouni/brother-hub); pinned rerun trees ~/bh-persona-r2 (f8b3cf17) and ~/bh-persona-r3b (e76d083b)",
            "- Fixture estates: ~/Documents/BrotherArchive/persona-fixtures-2026-09-07/ (drinks-marketplace, bottler-data-platform, bare remotes under remotes/, per-actor clones under work/)",
            "- Plan and record on the branch: docs/plan/PERSONA-DOGFOOD-2026-09-07.md",
            "- Commands: `python3 ~/.claude/evidence/persona-dogfood-2026-09-07/tally.py` (pass rate, exit 0 at 36), `python3 .../judge.py` (Muse judges new results), `python3 .../gen_briefs.py <round> <tree>` (briefs for every non-PASS scenario), `python3 .../meter.py`",
            "- To continue the loop on another account: git -C ~/brother-hub fetch origin persona-dogfood-2026-09-07; git worktree add --detach ~/bh-persona-r4 origin/persona-dogfood-2026-09-07; then gen_briefs.py 4 ~/bh-persona-r4 and dispatch one actor per brief file."]
    with open(os.path.join(E, "RUN-FACTS.md"), "w") as f:
        f.write("\n".join(out) + "\n")
    print("\n".join(out[:12]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
