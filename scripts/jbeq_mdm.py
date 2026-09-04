#!/usr/bin/env python3
"""JBEQ-MDM seed suite: write the blind prompts, score a blind answer file.

WHY THIS EXISTS. The steering directive of 2026-09-05 (sections 10 to 28)
creates JBEQ, the Japanese Business Engineering Qualification, and its first
track JBEQ-MDM. A track is only worth a number if the number is mechanical, so
the seed at benchmarks/jbeq/mdm/seed-2026-09-05.json is scored by this script
and by nothing else.

THE RULE THAT MAKES THE SCORE MEAN ANYTHING. A case is scored only against an
ANSWER FILE produced by an agent that never saw the ground truth. `prompts`
writes one file per case carrying the input, the question and the allowed
answers, and NEVER the expected answer or the rationale. The answerer reads
those files and writes {"CASE-ID": "ANSWER"}. `score` reads that file.

SECTION 28 IS THE VERDICT. Critical cases require 100 percent. One critical
case answered wrong, and in particular a critical case answered with a merge
where the expected answer is not a merge, prints JBEQ-MDM NOT READY and exits
1. NO-DATA is never a pass: a case the answer file does not carry is named and
counted as unanswered, never as passed, and an answer file that answers nothing
exits 3.

Usage:
  python3 scripts/jbeq_mdm.py prompts <out dir>
  python3 scripts/jbeq_mdm.py score <answers.json> [--seed <path>]
"""
import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = os.path.join(REPO, "benchmarks", "jbeq", "mdm", "seed-2026-09-05.json")

EXIT_OK = 0
EXIT_NOT_READY = 1
EXIT_NODATA = 3


def load_seed(path):
    """Read the frozen seed. A missing seed is NO-DATA, never an empty pass."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        sys.stderr.write("NO-DATA: cannot read the seed at %s: %s\n" % (path, exc))
        return None


def write_prompts(seed, out_dir):
    """One blind prompt per case. The expected answer never enters this file.

    The allowed answers are printed in the seed's own order, which is the
    canonical order of that answer set, so no position in the list can leak
    which answer is expected.
    """
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for case in seed["cases"]:
        body = [
            "# JBEQ-MDM %s" % case["id"],
            "",
            "TRACK: %s" % case["track"],
            "",
            "## 入力",
            "",
            case["input"],
            "",
            "## 設問",
            "",
            case["question"],
            "",
            "## 許容される回答",
            "",
            "次のうち1つだけを、表記どおりに答えること。",
            "",
        ]
        body += ["- %s" % a for a in case["allowed"]]
        body += [
            "",
            "## 提出形式",
            "",
            '答案ファイルに {"%s": "<回答>"} の形式で1件を記入する。' % case["id"],
            "",
        ]
        path = os.path.join(out_dir, "%s.md" % case["id"])
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(body))
        written.append(path)
    return written


def score(seed, answers):
    """Return (per-track counts, critical failures, missing ids, passed)."""
    merge_answers = set(seed["scoring"]["merge_answers"])
    tracks = {}
    missing = []
    critical_failures = []
    passed = 0
    for case in seed["cases"]:
        track = tracks.setdefault(case["track"], {"passed": 0, "total": 0})
        track["total"] += 1
        given = answers.get(case["id"])
        if given is None:
            missing.append(case["id"])
            continue
        if given == case["expected"]:
            track["passed"] += 1
            passed += 1
            continue
        if case["critical"]:
            # Section 28: any critical case answered wrong counts, and the
            # merge shape is named separately because it is the one the
            # directive calls out by name.
            false_merge = (given in merge_answers
                           and case["expected"] not in merge_answers)
            critical_failures.append((case["id"], case["critical_class"],
                                      case["expected"], given, false_merge))
    return tracks, critical_failures, missing, passed


def cmd_score(args):
    seed = load_seed(args.seed)
    if seed is None:
        return EXIT_NODATA
    try:
        with open(args.answers, encoding="utf-8") as fh:
            answers = json.load(fh)
    except (OSError, ValueError) as exc:
        sys.stderr.write("NO-DATA: cannot read the answer file %s: %s\n"
                         % (args.answers, exc))
        return EXIT_NODATA
    if not isinstance(answers, dict):
        sys.stderr.write("NO-DATA: the answer file must be an object of "
                         "{case id: answer}, got %s\n" % type(answers).__name__)
        return EXIT_NODATA

    tracks, critical_failures, missing, passed = score(seed, answers)
    total = len(seed["cases"])
    n_critical = sum(1 for c in seed["cases"] if c["critical"])

    for name in sorted(tracks):
        row = tracks[name]
        print("%-20s %d of %d" % (name, row["passed"], row["total"]))

    if missing:
        print("NO-DATA: %d case(s) not answered, never counted as passed: %s"
              % (len(missing), ", ".join(missing)))
    for cid, klass, expected, given, false_merge in critical_failures:
        print("critical WRONG %s [%s] expected %s, answered %s%s"
              % (cid, klass, expected, given,
                 "  FALSE MERGE" if false_merge else ""))

    print("critical false merges: %d of %d" % (len(critical_failures), n_critical))

    if len(missing) == total:
        print("JBEQ-MDM NO-DATA: the answer file answered no case")
        return EXIT_NODATA
    if critical_failures:
        print("JBEQ-MDM NOT READY")
        return EXIT_NOT_READY
    print("JBEQ-MDM SEED: %d of %d" % (passed, total))
    return EXIT_OK


def cmd_prompts(args):
    seed = load_seed(args.seed)
    if seed is None:
        return EXIT_NODATA
    written = write_prompts(seed, args.out_dir)
    print("wrote %d blind prompt file(s) to %s" % (len(written), args.out_dir))
    return EXIT_OK


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd")
    p = sub.add_parser("prompts", help="write one blind prompt file per case")
    p.add_argument("out_dir")
    p.add_argument("--seed", default=SEED)
    p.set_defaults(func=cmd_prompts)
    s = sub.add_parser("score", help="score a blind answer file")
    s.add_argument("answers")
    s.add_argument("--seed", default=SEED)
    s.set_defaults(func=cmd_score)
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_NODATA
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
