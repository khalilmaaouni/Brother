#!/usr/bin/env python3
"""Generate round-N actor briefs. Default: one per scenario whose LATEST
verdict is not PASS. --all: one per scenario of every persona, so a
promotion run can rerun the passing scenarios too and prove no regression.
Usage: gen_briefs.py ROUND TREE [--evidence-dir DIR] [--all]

CANDIDATE B (targets T4's malformed-line handling): the baseline silently
drops any line that fails `json.loads`, with no trace of how many lines
were corrupt or which line numbers -- fine for exit-code correctness, but
an evidence log that later needs to explain "why is A1-S2 missing" has
nothing to go on. This candidate keeps the exact same skip decision
(still `except ValueError`, still exit 0) but tracks the 1-based line
number and count of every skipped line and prints a one-line summary to
stderr ("skipped N malformed line(s) in results.jsonl: [...]") so a
corrupt-line run is diagnosable from evidence/logs without changing
stdout, the made list, or the exit code any T1-T5 check depends on.
"""
import argparse
import json
import os
import sys

D = os.path.dirname(os.path.abspath(__file__))
DEFAULT_EVIDENCE_DIR = os.environ.get(
    "PERSONA_EVIDENCE_DIR",
    os.path.expanduser("~/.claude/evidence/persona-dogfood-2026-09-07"),
)


def build_argparser():
    ap = argparse.ArgumentParser()
    ap.add_argument("round")
    ap.add_argument("tree")
    ap.add_argument("--evidence-dir", default=DEFAULT_EVIDENCE_DIR,
                     help="where transcripts/results.jsonl is read and brief-*.md is written (default: $PERSONA_EVIDENCE_DIR or the 2026-09-07 evidence dir)")
    ap.add_argument("--all", action="store_true",
                     help="brief every scenario of every persona, not only non-PASS ones")
    return ap


def main(argv=None):
    args = build_argparser().parse_args(argv)
    rnd, tree, evidence_dir = args.round, args.tree, args.evidence_dir

    d = json.load(open(os.path.join(D, "scenarios.json")))
    latest = {}
    skipped_lines = []
    results_path = os.path.join(evidence_dir, "transcripts", "results.jsonl")
    if os.path.exists(results_path):
        for lineno, line in enumerate(open(results_path), start=1):
            try:
                r = json.loads(line)
            except ValueError:  # sbe: allow-silent a malformed prior result cannot name a scenario status, so later valid result lines still decide reruns
                skipped_lines.append(lineno)
                continue
            latest[r["scenario"]] = r.get("verdict")
    if skipped_lines:
        print(f"warning: skipped {len(skipped_lines)} malformed line(s) "
              f"in {results_path}: lines {skipped_lines}", file=sys.stderr)
    tpl = open(os.path.join(D, "ACTOR-BRIEF-TEMPLATE-v2.md")).read()
    root = os.path.expanduser(
        "~/Documents/BrotherArchive/persona-fixtures-2026-09-07")
    tdir = os.path.join(evidence_dir, "transcripts")
    made = []
    for p in d["personas"]:
        pid = p["id"]
        repo = "drinks-marketplace" if p["estate"] == "A" else "bottler-data-platform"
        if args.all:
            sc = [s for s in d["scenarios"] if s["persona"] == pid]
        else:
            sc = [s for s in d["scenarios"] if s["persona"] == pid and latest.get(s["id"]) != "PASS"]
        if not sc:
            continue
        b = (tpl.replace("{PERSONA_JSON}", json.dumps(p, ensure_ascii=False))
               .replace("{FIXTURE_PATH}", f"{root}/{repo}").replace("{REMOTE_PATH}", f"{root}/remotes/{repo}.git")
               .replace("{SCENARIOS_JSON}", json.dumps(sc, ensure_ascii=False)).replace("{TRANSCRIPT_DIR}", tdir)
               .replace("{PERSONA_ID}", pid).replace("{TREE}", tree).replace("{ROUND}", rnd))
        b = b.replace(f"git clone {root}/remotes/{repo}.git {root}/work/{pid}/{repo}", f"git clone {root}/remotes/{repo}.git {root}/work/{pid}-r{rnd}/{repo}").replace(f"work ONLY inside {root}/work/{pid}/{repo}", f"work ONLY inside {root}/work/{pid}-r{rnd}/{repo}")
        out = os.path.join(evidence_dir, f"brief-{pid}-r{rnd}.md")
        with open(out, "w") as f:
            f.write(b)
        made.append((pid, len(sc)))
    print("round", rnd, "briefs:", made, "scenarios to rerun:", sum(n for _, n in made))
    return 0


if __name__ == "__main__":
    sys.exit(main())
