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
  python3 scripts/jbeq_mdm.py extract --model-cmd "<argv>" --out <dir>
      [--prompt <extractor-prompt.md>] [--prompts-dir <dir>] [--timeout N]

`extract`, added round 5 (2026-09-06): runs the extractor prompt
(default benchmarks/jbeq/mdm/prompts/EXTRACTOR-PROMPT.md) once per case
prompt file under --prompts-dir (default benchmarks/jbeq/mdm/prompts, the
70 files `prompts` already wrote), through --model-cmd, and writes
fact-sheets.json, extractor-notes.md and RECORD.txt into --out. NO-DATA,
never a guess, when the model command cannot run at all (see
scripts/door.py's identical missing_reason contract); a single case's
failure is named and counted in errors, never silently dropped from the
fact sheet file.
"""
import argparse
import datetime
import glob
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = os.path.join(REPO, "benchmarks", "jbeq", "mdm", "seed-2026-09-05.json")
PROMPTS_DIR = os.path.join(REPO, "benchmarks", "jbeq", "mdm", "prompts")
EXTRACTOR_PROMPT = os.path.join(PROMPTS_DIR, "EXTRACTOR-PROMPT.md")
RUNS_DIR = os.path.join(REPO, "benchmarks", "jbeq", "mdm", "runs")

EXIT_OK = 0
EXIT_NOT_READY = 1
EXIT_NODATA = 3

# Founder ruling 2026-09-06 (question UI, ~17:57 JST): "One class for
# scoring; engine keeps the proposal gate". Decision record:
# docs/decisions/decision-p0-3-reject-vs-keep-2026-09-06.json/.html. Where a
# case's expected answer is REJECT MATCH or KEEP SEPARATE, either answer is a
# correct reading of a refuted identity (section 18 of the fix directive
# treats the two as interchangeable), so the scorer accepts both while
# scripts/jbeq_decide.py's proposal gate keeps deciding which of the two it
# says. "2026-09-06.2" because ".1" was the engine-half derivation of this
# same boundary (hub PR 408, the proposal gate itself); ".2" is the scorer
# half.
SCORER_VERSION = "2026-09-06.2"

# The one equivalence class this scorer knows: a refuted identity, answered
# either as "reject the proposed match" or "keep the records separate". Both
# mean the two records are not the same thing; the difference between them is
# whether the input stated a proposal, which the engine still records per
# case (see cmd_score's equivalence-class print), just not as a scoring
# distinction.
REFUTED_IDENTITY_CLASS = frozenset({"REJECT MATCH", "KEEP SEPARATE"})


def _under_runs_dir(path):
    """True if `path` resolves to RUNS_DIR itself or anything inside it,
    the committed-record tree a mutation seam must never write into.
    Mirrors scripts/jbeq_decide.py's identical guard (hub PR 386 security
    finding, 2026-09-06): closed at the record, so a mutated run can never
    become a committed record by accident."""
    if not path:
        return False
    resolved = os.path.abspath(path)
    runs = os.path.abspath(RUNS_DIR)
    return resolved == runs or resolved.startswith(runs + os.sep)


def _mutation_seam_disabled(answers_path, answers):
    """Return a sorted list of disabled rule ids if the answers file
    itself, or the decisions.jsonl file beside it, carries the mutation
    marker jbeq_decide.py's decide() and cmd_decide write while
    JBEQ_DECIDE_DISABLE_RULES is set; None if neither does. A mutated run
    is never a scorable record (see cmd_score)."""
    disabled = set()
    marker = answers.get("_mutation") if isinstance(answers, dict) else None
    if isinstance(marker, dict) and marker.get("disabled"):
        disabled.update(marker["disabled"])

    decisions_path = _decisions_path_beside(answers_path)
    try:
        with open(decisions_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue  # sbe: allow-silent reader over decisions.jsonl, one malformed row is skipped, never rewritten
                mutation = row.get("mutation") if isinstance(row, dict) else None
                if isinstance(mutation, dict) and mutation.get("disabled"):
                    disabled.update(mutation["disabled"])
    except OSError:
        pass  # sbe: allow-silent decisions.jsonl is optional beside answers.json, a missing file means no mutation seam is disabled

    return sorted(disabled) if disabled else None


def _decisions_path_beside(answers_path):
    """The decisions.jsonl a caller's answers.json normally sits beside,
    used both by _mutation_seam_disabled above and by
    _engine_decided_ids below."""
    return os.path.join(
        os.path.dirname(os.path.abspath(answers_path)), "decisions.jsonl"
    )


def _engine_decided_ids(decisions_path):
    """Return the set of case ids a decisions.jsonl file says the ENGINE
    decided (rule_fired != "track-unsupported"), or None if no readable
    decisions.jsonl with at least one row exists at that path.

    Review-u1-2026-09-06.md finding 1: "engine-decided" must be a fact
    about what scripts/jbeq_decide.py's rule table actually returned for
    THIS case, never a fact about a hardcoded track-name set (ENGINE_TRACKS
    above), which silently misreports the moment the engine learns to
    decide a track that set does not name (temporal, since round 5): its
    worst-scoring track was being filed under direct-answered, so the
    engine's own failures never counted against it.
    """
    if not decisions_path or not os.path.isfile(decisions_path):
        return None
    ids = set()
    found_any_row = False
    try:
        with open(decisions_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue  # sbe: allow-silent reader over decisions.jsonl, one malformed row is skipped, never rewritten
                if not isinstance(row, dict):
                    continue
                case_id = row.get("case_id")
                if not case_id:
                    continue
                found_any_row = True
                if row.get("rule_fired") != "track-unsupported":
                    ids.add(case_id)
    except OSError:
        return None  # sbe: allow-silent decisions.jsonl is optional, a missing or unreadable file reads as no engine-decided ids
    return ids if found_any_row else None


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

# FALLBACK ONLY as of the review-u1-2026-09-06.md reporting-defect fix
# (2026-09-06): this hardcoded track set is what cmd_score used to derive
# "engine-decided" from, and it is wrong the moment scripts/jbeq_decide.py
# learns to decide a track this set does not name (exactly what happened to
# "temporal" in round 5: it left UNSUPPORTED_TRACKS, but this set was never
# updated, so U1's five temporal cases were filed under direct-answered and
# the engine's own worst track never counted against it). cmd_score now
# derives engine-decided PER CASE from decisions.jsonl's own rule_fired
# (see _engine_decided_ids below), which is a fact about what the engine
# actually did rather than a fact about track names. This set is kept only
# as a fallback for an answers file with no decisions.jsonl beside it (a
# hand-authored test fixture, or a run that predates decide()'s per-case
# rule_fired record); scripts/jbeq_regression.py also still reads this
# constant directly for its own, deliberately track-based, reporting line.
ENGINE_TRACKS = {"address", "entity-object", "hierarchy", "identifier", "match-or-no-merge"}

# The eight track names JBEQ-MDM actually uses (README.md's own mix, section
# 27, and scripts/jbeq_decide.py's ENGINE_TRACKS plus UNSUPPORTED_TRACKS):
# ENGINE_TRACKS's five, plus "temporal" (engine-decided since round 5 but
# never folded into that stale constant, see its comment above), plus
# UNSUPPORTED_TRACKS's "survivorship" and "requirements". Defined ONCE here
# so a seed file cannot carry a track name no other part of the estate
# recognizes: U2's five cases spelled "requirements-understanding", which is
# neither this list nor jbeq_decide.py's UNSUPPORTED_TRACKS, so the engine
# ran its general rule table over them instead of refusing them as
# track-unsupported (hub PR 411, cases V-22 and V-37).
CANONICAL_TRACKS = ENGINE_TRACKS | {"temporal", "survivorship", "requirements"}

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
    """Read the frozen seed. A missing seed is NO-DATA, never an empty pass.

    Also refuses a seed carrying a case whose track is not one of
    CANONICAL_TRACKS: a typo (a track name close to a real one, like
    "requirements-understanding" for "requirements") is distinguishable
    from an omission only if something actually checks the spelling, and
    an unrecognized track used to fall through to the engine's general
    rule table instead of being routed to track-unsupported (see
    CANONICAL_TRACKS's own comment).
    """
    try:
        with open(path, encoding="utf-8") as fh:
            seed = json.load(fh)
    except (OSError, ValueError) as exc:
        sys.stderr.write("NO-DATA: cannot read the seed at %s: %s\n" % (path, exc))
        return None
    for case in seed.get("cases", []):
        track = case.get("track")
        if track not in CANONICAL_TRACKS:
            sys.stderr.write(
                "NO-DATA: %s in %s has an unknown track %r; must be one of %s\n"
                % (case.get("id"), path, track, ", ".join(sorted(CANONICAL_TRACKS)))
            )
            return None
    return seed


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


def answers_equivalent(expected, given):
    """True when `given` counts as correct for `expected`: an exact match, or
    both answers are members of REFUTED_IDENTITY_CLASS (founder ruling
    2026-09-06, SCORER_VERSION). A merge answer is never in that class, so
    this never widens what counts as correct for a merge or non-refuted
    expectation."""
    if given == expected:
        return True
    return expected in REFUTED_IDENTITY_CLASS and given in REFUTED_IDENTITY_CLASS


def score(seed, answers):
    """Return (per-track counts, critical failures, missing ids, passed,
    equivalence hits).

    A critical failure carries two flags that are counted on separate lines:
    false_merge for section 28's own shape (a merge answered where the expected
    answer is not a merge), and conservative for a wrong answer that chose a
    more cautious label than the expected one. Neither flag can fire for an
    equivalence hit (REFUTED_IDENTITY_CLASS holds no merge answer), so that
    classification is unchanged by the equivalence class below.

    equivalence hits is a list of (case id, expected, given) for every case
    where `given` differs from `expected` but both fall in
    REFUTED_IDENTITY_CLASS: scored correct, with which of the two the engine
    actually said kept on the record rather than collapsed into the count.
    """
    merge_answers = set(seed["scoring"]["merge_answers"])
    tracks = {}
    missing = []
    critical_failures = []
    equivalence_hits = []
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
        if answers_equivalent(case["expected"], given):
            track["passed"] += 1
            passed += 1
            equivalence_hits.append((case["id"], case["expected"], given))
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
    return tracks, critical_failures, missing, passed, equivalence_hits


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

    # Closed at the scorer (hub PR 386 security finding, 2026-09-06): a
    # mutation seam (JBEQ_DECIDE_DISABLE_RULES) marks every result it
    # touches, so a run produced under one is never a scorable record
    # unless the caller explicitly asked for a throwaway report.
    mutation_disabled = _mutation_seam_disabled(args.answers, answers)
    if mutation_disabled and not args.mutation_report:
        sys.stderr.write(
            "REFUSED: this answers file was produced under a mutation "
            "seam; a mutated run is never a scorable record\n"
        )
        sys.stderr.write(
            "mutation seam disabled rule(s): %s\n" % ", ".join(mutation_disabled)
        )
        return EXIT_NODATA
    if mutation_disabled:
        print("MUTATION REPORT, not a record (rules disabled: %s)"
              % ", ".join(mutation_disabled))

    tracks, critical_failures, missing, passed, equivalence_hits = score(seed, answers)
    total = len(seed["cases"])
    n_critical = sum(1 for c in seed["cases"] if c["critical"])

    print("scorer: %s (REJECT MATCH and KEEP SEPARATE score as one class "
          "for a refuted identity, founder ruling 2026-09-06)" % SCORER_VERSION)

    for name in sorted(tracks):
        row = tracks[name]
        print("%-20s %d of %d" % (name, row["passed"], row["total"]))

    for cid, expected, given in equivalence_hits:
        print("equivalence class %s: expected %s, engine said %s (scored "
              "correct, scorer %s)" % (cid, expected, given, SCORER_VERSION))

    # 2026-09-06 (FIX-DIRECTIVE, design review section G): 47/70 and 50/70
    # both blend two different systems into one number: an engine deciding
    # some cases and a non-blind model answering the rest directly, which
    # is not evidence about the engine at all. Report both subtotals beside
    # the blended one so a release gate can name the engine's own half
    # rather than a number that moves when the direct-answer half happens
    # to score differently.
    #
    # review-u1-2026-09-06.md finding 1 (2026-09-06): which cases the
    # engine actually decided is derived from decisions.jsonl's own
    # rule_fired (rule_fired != "track-unsupported"), a per-case fact about
    # what scripts/jbeq_decide.py returned, never from ENGINE_TRACKS's
    # hardcoded track names, which is wrong the moment the engine learns to
    # decide a track that set does not name (see ENGINE_TRACKS's own
    # comment). Falls back to ENGINE_TRACKS only when no decisions.jsonl
    # sits beside the answers file at all (a hand-authored answer file with
    # no engine run behind it).
    engine_ids = _engine_decided_ids(_decisions_path_beside(args.answers))
    if engine_ids is not None:
        seed_ids = {c["id"] for c in seed["cases"]}
        engine_ids &= seed_ids
        engine_total = len(engine_ids)
        # 2026-09-06 (SCORER_VERSION 2026-09-06.2): must use the same
        # answers_equivalent() test score() uses for `passed`, not a bare
        # == . Using == here while `passed` counts equivalence hits made
        # direct_passed = passed - engine_passed absorb every engine-side
        # equivalence hit as a phantom direct-answered case (observed:
        # "direct-answered ... 28 of 25", more answers than cases exist).
        engine_passed = sum(
            1 for c in seed["cases"]
            if c["id"] in engine_ids
            and answers_equivalent(c["expected"], answers.get(c["id"]))
        )
    else:
        engine_passed = sum(tracks[t]["passed"] for t in ENGINE_TRACKS if t in tracks)
        engine_total = sum(tracks[t]["total"] for t in ENGINE_TRACKS if t in tracks)
    direct_passed = passed - engine_passed
    direct_total = total - engine_total
    print("engine-decided: %d of %d" % (engine_passed, engine_total))
    print("direct-answered (not blind, not evidence about the engine): %d of %d"
          % (direct_passed, direct_total))

    if missing:
        print("NO-DATA: %d case(s) not answered, never counted as passed: %s"
              % (len(missing), ", ".join(missing)))
    for cid, klass, expected, given, false_merge, conservative in critical_failures:
        marks = ""
        if false_merge:
            marks += "  FALSE MERGE"
        if conservative:
            marks += "  CONSERVATIVE"
        # 2026-09-06 (review section D): klass is the case's OWN
        # critical_class, fixed at seed time to name the danger this case
        # tests for; it is never a computed diagnosis of what the given
        # answer actually did. Printing "FALSE MERGE" verbatim in the
        # bracket when the given answer was not a merge at all (MM-06,
        # answered ESCALATE) reads as a real false merge to a downstream
        # grep for the exit-gate phrase, producing a false positive. Show
        # the risk label only when it will not be confused with an actual
        # observed false merge.
        display_klass = klass
        if klass == "FALSE MERGE" and not false_merge:
            display_klass = "FALSE-MERGE RISK, not merged"
        print("critical WRONG %s [%s] expected %s, answered %s%s"
              % (cid, display_klass, expected, given, marks))

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


def _missing_model_reason(cmd):
    """None if cmd[0] looks runnable, else a message naming what is missing.
    Mirrors scripts/door.py's missing_reason so a NO-DATA line here reads
    the same way it does everywhere else this estate shells out to a model.
    """
    if not cmd:
        return "no model command was given"
    exe = cmd[0]
    if os.sep in exe or (os.altsep and os.altsep in exe):
        if not os.path.isfile(exe):
            return "the model command %r does not exist" % exe
        if not os.access(exe, os.X_OK):
            return "the model command %r is not executable" % exe
        return None
    if shutil.which(exe) is None:
        return "the model command %r was not found on PATH" % exe
    return None


def _extract_json_object(text):
    """Pull the first balanced {...} object out of a model's reply, which
    may wrap it in prose or a ```json fence. Returns (dict, None) or
    (None, reason)."""
    start = text.find("{")
    if start == -1:
        return None, "no '{' found in the model's reply"
    depth = 0
    end = None
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        return None, "no balanced '}' found closing the first '{'"
    candidate = text[start:end]
    try:
        return json.loads(candidate), None
    except ValueError as exc:
        return None, "the extracted JSON does not parse: %s" % exc


def cmd_extract(args):
    """Run the extractor prompt once per case prompt file through a model
    command, writing fact-sheets.json, extractor-notes.md and RECORD.txt.
    See benchmarks/jbeq/mdm/prompts/EXTRACTOR-PROMPT.md for the instruction
    itself. NO-DATA, never a guess, when the model command cannot run at
    all; a per-case failure is named and counted, never silently dropped.

    Closed at the record (hub PR 386 security finding, 2026-09-06): refuses
    outright, before any model call, when JBEQ_DECIDE_DISABLE_RULES is set
    and --out resolves under benchmarks/jbeq/mdm/runs/, so a mutation seam
    can never let this subcommand write into the committed-record tree.
    """
    seam = os.environ.get("JBEQ_DECIDE_DISABLE_RULES", "")
    if seam and _under_runs_dir(args.out):
        sys.stderr.write(
            "REFUSED: JBEQ_DECIDE_DISABLE_RULES=%r is set (mutation seam "
            "active); refusing to write into benchmarks/jbeq/mdm/runs/, "
            "where a mutated run could become a committed record by "
            "accident\n" % seam
        )
        return EXIT_NODATA
    try:
        with open(args.prompt, encoding="utf-8") as fh:
            extractor_prompt = fh.read()
    except OSError as exc:
        sys.stderr.write("NO-DATA: cannot read the extractor prompt %s: %s\n"
                         % (args.prompt, exc))
        return EXIT_NODATA
    prompt_sha256 = hashlib.sha256(extractor_prompt.encode("utf-8")).hexdigest()

    case_paths = sorted(
        p for p in glob.glob(os.path.join(args.prompts_dir, "*.md"))
        if os.path.basename(p) != os.path.basename(args.prompt)
    )
    if not case_paths:
        sys.stderr.write("NO-DATA: no case prompt file(*.md) found under %s\n"
                         % args.prompts_dir)
        return EXIT_NODATA

    cmd = shlex.split(args.model_cmd) if args.model_cmd else None
    missing = _missing_model_reason(cmd)
    if missing:
        sys.stderr.write("NO-DATA: %s, so extraction cannot run\n" % missing)
        return EXIT_NODATA

    os.makedirs(args.out, exist_ok=True)
    fact_sheets = {}
    notes = []
    errors = []
    for path in case_paths:
        case_id = os.path.splitext(os.path.basename(path))[0]
        with open(path, encoding="utf-8") as fh:
            case_prompt = fh.read()
        full_prompt = (
            extractor_prompt
            + "\n\n## CASE PROMPT (%s)\n\n" % case_id
            + case_prompt
            + "\n\nWrite ONLY the one JSON fact sheet object for %s, "
              "nothing else." % case_id
        )
        try:
            proc = subprocess.run(cmd, input=full_prompt, capture_output=True,
                                  text=True, timeout=args.timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append("%s: model call failed: %s" % (case_id, exc))
            continue
        if proc.returncode != 0:
            errors.append("%s: model exited %d: %s"
                          % (case_id, proc.returncode, proc.stderr.strip()[:500]))
            continue
        sheet, reason = _extract_json_object(proc.stdout)
        if sheet is None:
            errors.append("%s: %s" % (case_id, reason))
            continue
        fact_sheets[case_id] = sheet
        notes.append("%s: extracted (track=%r)" % (case_id, sheet.get("track")))

    fact_sheets_path = os.path.join(args.out, "fact-sheets.json")
    with open(fact_sheets_path, "w", encoding="utf-8") as fh:
        json.dump(fact_sheets, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")

    with open(os.path.join(args.out, "extractor-notes.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(notes) + ("\n" if notes else ""))
        if errors:
            fh.write("\nERRORS:\n")
            fh.write("\n".join("  %s" % e for e in errors) + "\n")

    with open(os.path.join(args.out, "RECORD.txt"), "w", encoding="utf-8") as fh:
        fh.write("extraction run: %s\n"
                 % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
        fh.write("extractor prompt: %s sha256 %s\n" % (args.prompt, prompt_sha256[:16]))
        fh.write("model command: %s\n" % args.model_cmd)
        fh.write("cases attempted: %d, extracted: %d, error(s): %d\n"
                 % (len(case_paths), len(fact_sheets), len(errors)))

    print("wrote %d fact sheet(s) to %s (%d error(s))"
          % (len(fact_sheets), fact_sheets_path, len(errors)))
    if errors:
        for e in errors:
            print("ERROR %s" % e, file=sys.stderr)
        return EXIT_NOT_READY
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
    s.add_argument(
        "--mutation-report", action="store_true",
        help="score an answers file produced under a mutation seam anyway, "
             "printing the result under a MUTATION REPORT header instead of "
             "refusing it; writes no record file either way",
    )
    s.set_defaults(func=cmd_score)
    e = sub.add_parser("extract", help="run the extractor prompt once per "
                       "case prompt through a model command")
    e.add_argument("--prompt", default=EXTRACTOR_PROMPT)
    e.add_argument("--prompts-dir", default=PROMPTS_DIR)
    e.add_argument("--model-cmd", required=True,
                   help="model command line (shlex syntax), e.g. \"claude -p\"")
    e.add_argument("--out", required=True)
    e.add_argument("--timeout", type=int, default=300)
    e.set_defaults(func=cmd_extract)
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_NODATA
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
