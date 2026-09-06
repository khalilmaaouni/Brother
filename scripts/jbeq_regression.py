#!/usr/bin/env python3
"""JBEQ-MDM regression harness: re-decide the pinned round 6 fact sheets
with the CURRENT engine and prove no previously correct case regressed.

WHY THIS EXISTS. ~/.claude/evidence/review-round6-2026-09-06.md (2026-09-06)
found the round 5 versus round 6 comparison was never a case-by-case
measurement, because each round's blind extractor produced a DIFFERENT
fact sheet from the same frozen prompt text, so a score change conflated
a rule change with a re-extraction. Pinning ONE extraction (round 6's,
the best engine-decided result of the three) as a frozen regression
fixture lets a rule change be measured against a fixed input: a score
change here is a fact about scripts/jbeq_decide.py, never about a
different reading of the prompts (review section E: "a rule change
evaluated against a fixed sheet set measures the rule, and nothing else
in this benchmark does").

THE FIXTURE, DEFAULT (fixture b, since 2026-09-06's REJECT-versus-KEEP
pass). benchmarks/jbeq/mdm/regression-sheets-2026-09-06b.json, a
byte-for-byte copy of benchmarks/jbeq/mdm/runs/blind-round8-2026-09-06/
fact-sheets.json (round 8's extraction, the widened requested_action
vocabulary needed to decide the REJECT-versus-KEEP boundary; review-u1-
2026-09-06.md section A), chmod 444, origin and sha256 recorded beside it
in regression-sheets-2026-09-06b.origin.txt. NEVER edit this fixture or
the run directory it was copied from.

THE OLD FIXTURE (fixture a, round 6, still runnable with --fixture a).
benchmarks/jbeq/mdm/regression-sheets-2026-09-06.json, chmod 444, origin
and sha256 in regression-sheets-2026-09-06.origin.txt. Predates the
widened requested_action vocabulary (only none/assignment), so running it
against the post-proposal_gate engine is not a meaningful regression
signal for that fix; kept runnable only so the fixture itself is never
lost. NEVER edit this fixture or the run directory it was copied from
(FIX-DIRECTIVE-2026-09-06.md section 31: "do not optimize indefinitely
against the frozen 70").

THE BASELINE. One JSON file per fixture, written ONCE via --write-baseline
from the engine as it stood before the rule changes being measured:
benchmarks/jbeq/mdm/regression-baseline-2026-09-06b.json (fixture b,
before the proposal_gate fix; see its own .origin.txt) and
benchmarks/jbeq/mdm/regression-baseline-2026-09-06.json (fixture a, 29 of
45 engine-decided, matching round 6's own RECORD.txt). Every later run
compares the current engine's per-case answer against the matching
baseline: a case the baseline had RIGHT that the current engine now gets
WRONG is a regression and this script exits non-zero. A case flipping
from wrong to right, or staying wrong, is never a regression.

WHAT THIS DOES NOT PROVE. This script splices the matching round's own
direct-answers.json (recorded, never regenerated) for the non-engine
tracks (survivorship, requirements, temporal for fixture a; temporal only
for fixture b, per round 8's own reclassification), so its
"engine-decided N of M" line is the only number that describes
scripts/jbeq_decide.py; the direct-answered half is carried through
unchanged and is not evidence about the engine (same caveat as
scripts/jbeq_mdm.py score's own header).

Usage:
  python3 scripts/jbeq_regression.py                     # fixture b, default
  python3 scripts/jbeq_regression.py --fixture a          # old fixture
  python3 scripts/jbeq_regression.py --write-baseline [--force] [--fixture a|b]
"""
import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import jbeq_decide  # noqa: E402
import jbeq_mdm  # noqa: E402

MDM_DIR = os.path.join(REPO, "benchmarks", "jbeq", "mdm")

# Two pinned fixtures. "b" (round 8, widened requested_action vocabulary) is
# the default since the 2026-09-06 REJECT-versus-KEEP pass; "a" (round 6)
# stays selectable with --fixture a so it is never lost, but predates that
# vocabulary and is not a meaningful regression signal for that fix.
FIXTURES = {
    "a": {
        "sheets": os.path.join(MDM_DIR, "regression-sheets-2026-09-06.json"),
        "baseline": os.path.join(MDM_DIR, "regression-baseline-2026-09-06.json"),
        "direct_answers": os.path.join(
            MDM_DIR, "runs", "blind-round6-2026-09-06", "direct-answers.json"
        ),
    },
    "b": {
        "sheets": os.path.join(MDM_DIR, "regression-sheets-2026-09-06b.json"),
        "baseline": os.path.join(MDM_DIR, "regression-baseline-2026-09-06b.json"),
        "direct_answers": os.path.join(
            MDM_DIR, "runs", "blind-round8-2026-09-06", "direct-answers.json"
        ),
    },
    "c": {
        "sheets": os.path.join(MDM_DIR, "regression-sheets-2026-09-06c.json"),
        "baseline": os.path.join(MDM_DIR, "regression-baseline-2026-09-06c.json"),
        "direct_answers": os.path.join(
            MDM_DIR, "runs", "blind-round9-2026-09-06", "direct-answers.json"
        ),
    },
}
# Fixture c (round 9, hub PR 413's sharpened requested_action/authoritative_
# identifier prompt) scored engine-decided 31 of 45 against the seed, BELOW
# the 40-of-45 bar fixture b's own pre-proposal_gate baseline set (see
# regression-sheets-2026-09-06c.origin.txt and
# runs/blind-round9-2026-09-06/RECORD.txt), so the default fixture stays "b"
# and is not switched to "c" by this change.
DEFAULT_FIXTURE = "b"

EXIT_OK = 0
EXIT_REGRESSION = 1
EXIT_NODATA = 3


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def decide_all(sheets):
    """Re-decide every sheet in the fixture with the CURRENT engine."""
    return {case_id: jbeq_decide.decide(sheet)["answer"]
            for case_id, sheet in sheets.items()}


def spliced_answers(sheets, direct_answers):
    """(merged, engine): merged carries direct_answers over the engine's
    own recomputed answers for those same case ids (the 25 non-engine
    tracks); engine is the current engine's raw answer for every case in
    the fixture, used for the regression comparison."""
    engine = decide_all(sheets)
    merged = dict(engine)
    merged.update(direct_answers)
    return merged, engine


def _print_score(seed, merged):
    tracks, critical_failures, missing, passed, equivalence_hits = jbeq_mdm.score(seed, merged)
    n_critical = sum(1 for c in seed["cases"] if c["critical"])
    engine_passed = sum(tracks[t]["passed"] for t in jbeq_mdm.ENGINE_TRACKS if t in tracks)
    engine_total = sum(tracks[t]["total"] for t in jbeq_mdm.ENGINE_TRACKS if t in tracks)
    print("scorer: %s (REJECT MATCH and KEEP SEPARATE score as one class "
          "for a refuted identity, founder ruling 2026-09-06)" % jbeq_mdm.SCORER_VERSION)
    for name in sorted(tracks):
        row = tracks[name]
        print("%-20s %d of %d" % (name, row["passed"], row["total"]))
    for cid, expected, given in equivalence_hits:
        print("equivalence class %s: expected %s, engine said %s (scored "
              "correct, scorer %s)" % (cid, expected, given, jbeq_mdm.SCORER_VERSION))
    print("engine-decided: %d of %d" % (engine_passed, engine_total))
    if missing:
        print("NO-DATA: %d case(s) not answered: %s"
              % (len(missing), ", ".join(missing)))
    for cid, klass, expected, given, false_merge, conservative in critical_failures:
        print("critical WRONG %s [%s] expected %s, answered %s"
              % (cid, klass, expected, given))
    print("critical wrong: %d of %d%s"
          % (len(critical_failures), n_critical,
             (" (%s)" % ", ".join(r[0] for r in critical_failures))
             if critical_failures else ""))
    return engine_passed, engine_total


def cmd_write_baseline(args):
    paths = FIXTURES[args.fixture]
    if os.path.exists(paths["baseline"]) and not args.force:
        sys.stderr.write(
            "REFUSED: %s already exists; pass --force to overwrite it "
            "deliberately (this would erase the very regression it exists "
            "to catch)\n" % paths["baseline"]
        )
        return EXIT_NODATA
    sheets = _load(paths["sheets"])
    direct = _load(paths["direct_answers"])
    merged, engine = spliced_answers(sheets, direct)
    with open(paths["baseline"], "w", encoding="utf-8") as fh:
        json.dump(engine, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    seed = jbeq_mdm.load_seed(jbeq_mdm.SEED)
    if seed is None:
        return EXIT_NODATA
    engine_passed, engine_total = _print_score(seed, merged)
    print("wrote baseline %s: engine-decided %d of %d"
          % (paths["baseline"], engine_passed, engine_total))
    return EXIT_OK


def cmd_run(args):
    paths = FIXTURES[args.fixture]
    sheets = _load(paths["sheets"])
    direct = _load(paths["direct_answers"])
    merged, engine = spliced_answers(sheets, direct)

    seed = jbeq_mdm.load_seed(jbeq_mdm.SEED)
    if seed is None:
        return EXIT_NODATA
    _print_score(seed, merged)

    if not os.path.exists(paths["baseline"]):
        print("NO-DATA: no baseline at %s; run --write-baseline once "
              "before the first rule change" % paths["baseline"])
        return EXIT_NODATA
    baseline = _load(paths["baseline"])
    seed_by_id = {c["id"]: c for c in seed["cases"]}
    regressions = []
    for case_id, prior_answer in baseline.items():
        case = seed_by_id.get(case_id)
        if case is None:
            continue
        was_right = jbeq_mdm.answers_equivalent(case["expected"], prior_answer)
        now_right = jbeq_mdm.answers_equivalent(case["expected"], engine.get(case_id))
        if was_right and not now_right:
            regressions.append((case_id, case["expected"], engine.get(case_id)))

    if regressions:
        for case_id, expected, now in regressions:
            print("REGRESSION %s: baseline was correct (%s), current "
                  "engine now answers %s" % (case_id, expected, now))
        print("jbeq-regression: %d case(s) regressed against the "
              "fixture %s baseline" % (len(regressions), args.fixture))
        return EXIT_REGRESSION
    print("jbeq-regression: no previously-correct case regressed against "
          "the fixture %s baseline" % args.fixture)
    return EXIT_OK


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fixture", choices=sorted(FIXTURES), default=DEFAULT_FIXTURE,
                         help="which pinned fixture to run against "
                              "(default: %s)" % DEFAULT_FIXTURE)
    parser.add_argument("--write-baseline", action="store_true",
                         help="write the selected fixture's baseline "
                              "from the engine as it stands right now")
    parser.add_argument("--force", action="store_true",
                         help="with --write-baseline, overwrite an "
                              "existing baseline file")
    args = parser.parse_args(argv)
    if args.write_baseline:
        return cmd_write_baseline(args)
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
