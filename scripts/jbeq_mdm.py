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

THE TWO COUNTS ARE NOT THE SAME COUNT. `critical false merges` is section 28's
own shape and counts ONLY a critical case answered AUTO-MERGE or SUGGEST MERGE
where the expected answer is not a merge. `critical wrong` counts every
critical case answered wrongly, merge or not. The verdict follows the second,
because section 28 requires 100 percent on critical cases; the first is
reported beside it because it is the harm the directive names by hand.
`conservative wrongs` names the critical wrongs that chose a MORE CAUTIOUS
label than the expected one, which is a different defect from a false merge and
is worth seeing separately.

THE VOCABULARY IS PART OF THE PROMPT. Until 2026-09-05 the prompts listed the
seven allowed answers and defined none of them, so a wrong answer could mean
either bad master data judgement or a guess at what the label meant. VOCABULARY
below is carried verbatim into every prompt file, so a wrong answer now means
the judgement.

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

VOCABULARY_HEADING = "決定語彙"

# The one canonical definition of the seven decision answers. It is written
# into every prompt file and quoted verbatim in benchmarks/jbeq/README.md; the
# test suite fails if the two ever drift apart.
VOCABULARY = """## 決定語彙

回答は次の意味で用いる。1件につき1つだけを選ぶ。

AUTO-MERGE
  JA: 同一のオブジェクトであり、統合の根拠が十分で人の確認を要さない。
  EN: The same object, and the evidence is enough to merge it without a person.
SUGGEST MERGE
  JA: 同一のオブジェクトである可能性が高いが、確定はデータスチュワードの確認を経る。
  EN: Probably the same object, and a data steward confirms before it is merged.
LINK AS RELATED
  JA: 別のマスタオブジェクトだが同一性を示す事実または階層を共有しており、レコードは分けたまま関係として明示的に記録する。
  EN: Different master data objects that share an identity fact or a hierarchy, kept as separate records with the relation written down.
KEEP SEPARATE
  JA: 別のオブジェクトであり、記録すべき関係も無く、両方のレコードがそのまま有効である。
  EN: Different objects with no relation worth recording, and both records stay valid as they are.
REJECT MATCH
  JA: 提示された照合または関係付けの依頼そのものが誤りであり、その関係を記録してはならず、既存の同種のリンクは削除する。
  EN: The proposed match or link is wrong, the records must not carry that relation, and any existing link of that kind is removed.
ESCALATE
  JA: 判断材料は揃っているがルールでは決着せず、業務側の判断に上げる。
  EN: The facts are there but the rule set cannot close it, so the business decides.
NO-DATA
  JA: 判断に必要な事実が入力に無く、決定できないものとして依頼元に差し戻す。
  EN: The input lacks a fact the decision needs, so nothing is decided and the request goes back to where it came from.

境界 (which answer when two look close):

1. LINK AS RELATED か KEEP SEPARATE か。入力自体が関係(共通の法人番号、親子、役割の対、商流上の経由など)を述べていれば LINK AS RELATED、述べていなければ KEEP SEPARATE。
   EN: Answer LINK AS RELATED when the input itself states a relation. Answer KEEP SEPARATE when it states none.
2. KEEP SEPARATE か REJECT MATCH か。同一性または提示された階層を否定する事実が入力にあれば REJECT MATCH、単に裏付けが無いだけなら KEEP SEPARATE。
   EN: Answer REJECT MATCH when the input carries a fact that refutes the proposed identity or the proposed hierarchy. Answer KEEP SEPARATE when the match is merely unsupported.
3. ESCALATE か NO-DATA か。一項目が空欄でも他の事実が裏付けるなら判断材料はあるとみなし ESCALATE、どの事実も裏付けないなら NO-DATA。
   EN: Answer ESCALATE when there is something to judge, including when one field is blank but another stated fact corroborates without confirming. Answer NO-DATA only when nothing corroborates.
4. AUTO-MERGE か SUGGEST MERGE か ESCALATE か。証拠が弱い(名寄せスコアのみ、または識別子の欠落)なら取引履歴や処理の不可逆性の有無にかかわらず ESCALATE。証拠が強いか中程度で、取引履歴があるか処理が取り消せないなら SUGGEST MERGE。証拠が強く、それが無く識別子と役割が一致するなら AUTO-MERGE。
   EN: Answer ESCALATE when the evidence is weak (a match score alone, or a missing identifier), whatever the history or irreversibility. Answer SUGGEST MERGE when the evidence is strong or medium and history or an irreversible step means a person should look. Answer AUTO-MERGE when the evidence is strong, identifiers and roles agree, and nothing needs a person.

上の語彙に無い回答(出典名、R1/R2/R3、DECIDED/ASSUMED/INFERRED/UNKNOWN など)は、その設問の許容回答欄の表記どおりに答える。"""

# How cautious each decision answer is, from the one that joins the most data
# to the one that writes nothing. A critical case answered wrongly with a
# HIGHER rank than the expected answer erred on the safe side.
CAUTION_RANK = {
    "AUTO-MERGE": 1,
    "SUGGEST MERGE": 2,
    "LINK AS RELATED": 3,
    "KEEP SEPARATE": 4,
    "REJECT MATCH": 5,
    "ESCALATE": 6,
    "NO-DATA": 6,
}


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
    which answer is expected. VOCABULARY goes into every file verbatim, so the
    answerer is scored on the master data judgement and not on a guess at what
    a label means.
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
            VOCABULARY,
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
    """Return (per-track counts, critical failures, missing ids, passed).

    A critical failure carries two flags that are counted on separate lines:
    false_merge for section 28's own shape (a merge answered where the expected
    answer is not a merge), and conservative for a wrong answer that chose a
    more cautious label than the expected one.
    """
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
            false_merge = (given in merge_answers
                           and case["expected"] not in merge_answers)
            want = CAUTION_RANK.get(case["expected"])
            got = CAUTION_RANK.get(given)
            conservative = (want is not None and got is not None and got > want)
            critical_failures.append((case["id"], case["critical_class"],
                                      case["expected"], given, false_merge,
                                      conservative))
    return tracks, critical_failures, missing, passed


def named(ids):
    """A count on its own hides which cases it is. Name them beside it."""
    return " (%s)" % ", ".join(ids) if ids else ""


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
    for cid, klass, expected, given, false_merge, conservative in critical_failures:
        marks = ""
        if false_merge:
            marks += "  FALSE MERGE"
        if conservative:
            marks += "  CONSERVATIVE"
        print("critical WRONG %s [%s] expected %s, answered %s%s"
              % (cid, klass, expected, given, marks))

    false_merges = [row[0] for row in critical_failures if row[4]]
    conservatives = [row[0] for row in critical_failures if row[5]]
    print("critical false merges: %d of %d%s"
          % (len(false_merges), n_critical, named(false_merges)))
    print("critical wrong: %d of %d%s"
          % (len(critical_failures), n_critical,
             named([row[0] for row in critical_failures])))
    print("conservative wrongs: %d%s" % (len(conservatives), named(conservatives)))

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
