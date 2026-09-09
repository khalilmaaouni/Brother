#!/usr/bin/env python3
"""Pass rate from the LATEST result per scenario. Exit 0 at 36 of 40 or better, 1 otherwise, 2 NO-DATA.

--installed also prints the pinned tree (from <evidence-dir>/PINNED-TREE) and
flags any scenario that PASSed in an earlier round but is not PASS in its
latest round, as a regression. A promotion run is expected to rerun every
scenario (gen_briefs.py --all) so a silent regression on an already-PASSing
scenario is caught here, not missed because the loop only reruns failures.
"""
import argparse
import collections
import json
import os
import sys

DEFAULT_EVIDENCE_DIR = os.environ.get(
    "PERSONA_EVIDENCE_DIR",
    os.path.expanduser("~/.claude/evidence/persona-dogfood-2026-09-07"),
)


def build_argparser():
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence-dir", default=DEFAULT_EVIDENCE_DIR,
                     help="where transcripts/results.jsonl and PINNED-TREE live (default: $PERSONA_EVIDENCE_DIR or the 2026-09-07 evidence dir)")
    ap.add_argument("--installed", action="store_true",
                     help="also print the pinned tree line and any REGRESSION lines")
    return ap


def main(argv=None):
    args = build_argparser().parse_args(argv)
    evidence_dir = args.evidence_dir
    results_path = os.path.join(evidence_dir, "transcripts", "results.jsonl")

    rows = []
    try:
        with open(results_path) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("scenario"):
                    rows.append(r)
    except OSError:
        print("NO-DATA: no results file")
        return 2
    if not rows:
        print("NO-DATA: no results yet")
        return 2

    latest = {}
    for r in rows:
        latest[r["scenario"]] = r
    c = collections.Counter(r.get("verdict") for r in latest.values())
    passed = c.get("PASS", 0)
    print("scenarios with a result: %d of 40; latest verdicts %s; pass rate %d of 40 = %.0f%% (target 36, 90%%)"
          % (len(latest), dict(c), passed, 100.0 * passed / 40))
    for sid in sorted(latest):
        print(" ", sid, latest[sid].get("verdict"), "trust", latest[sid].get("trust_after"))

    code = 0 if passed >= 36 else 1

    if args.installed:
        pinned_path = os.path.join(evidence_dir, "PINNED-TREE")
        if os.path.exists(pinned_path):
            with open(pinned_path) as fh:
                lines = fh.read().splitlines()
            tree_path = lines[0] if len(lines) > 0 else ""
            tree_commit = lines[1] if len(lines) > 1 else ""
            print("tree: %s at %s" % (tree_path, tree_commit))
        else:
            print("tree: NO-DATA (no PINNED-TREE file)")

        by_scenario = collections.defaultdict(list)
        for r in rows:
            by_scenario[r["scenario"]].append((r.get("round", 1), r.get("verdict")))
        regressions = []
        for sid in sorted(by_scenario):
            entries = sorted(by_scenario[sid], key=lambda t: t[0])
            if len(entries) < 2:
                continue
            latest_verdict = entries[-1][1]
            earlier_verdicts = [v for _, v in entries[:-1]]
            if latest_verdict != "PASS" and "PASS" in earlier_verdicts:
                regressions.append(sid)
        for sid in regressions:
            print("REGRESSION: %s" % sid)
        if regressions:
            code = 1

    return code


if __name__ == "__main__":
    sys.exit(main())
