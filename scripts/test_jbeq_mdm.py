#!/usr/bin/env python3
"""Calibration for the JBEQ-MDM seed suite and its scorer.

WHAT THIS DRIVES BACKWARDS, because a control nobody drove backwards is a
claim rather than a control:

  * the seed is exactly 70 cases in the directive's own mix (section 27),
  * every case's expected answer is inside its own allowed set, so no case can
    be unanswerable,
  * every critical case names a critical class from section 28's list,
  * NO EXPECTED ANSWER LEAKS INTO A PROMPT FILE: the blind answerer's file
    carries the input, the question and the allowed answers in the seed's own
    canonical order, and never the expected answer as a field, never the
    rationale, and never the critical flag,
  * EVERY PROMPT CARRIES THE DECISION VOCABULARY, verbatim and identical to
    the block quoted in benchmarks/jbeq/README.md, so a wrong answer means bad
    master data judgement rather than a guess at what a label means,
  * a perfect answer file scores 70 of 70 and exits 0,
  * one critical case answered with a merge prints JBEQ-MDM NOT READY and
    exits 1,
  * a critical case answered wrong WITHOUT a merge still prints NOT READY, is
    counted by `critical wrong` and NOT by `critical false merges`, which is
    the defect the two lines exist to separate,
  * a critical wrong that chose a more cautious label is counted by
    `conservative wrongs`, and a less cautious one is not,
  * an answer file that answers nothing exits 3, because NO-DATA is never a
    pass.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jbeq_mdm  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "scripts", "jbeq_mdm.py")
SEED_PATH = os.path.join(REPO, "benchmarks", "jbeq", "mdm", "seed-2026-09-05.json")
README_PATH = os.path.join(REPO, "benchmarks", "jbeq", "README.md")
PROMPTS_PATH = os.path.join(REPO, "benchmarks", "jbeq", "mdm", "prompts")

EXPECTED_MIX = {
    "entity-object": 10,
    "match-or-no-merge": 10,
    "hierarchy": 10,
    "survivorship": 10,
    "temporal": 10,
    "address": 10,
    "identifier": 5,
    "requirements": 5,
}


def load():
    with open(SEED_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def run(*args):
    """Run the script and return (exit code, stdout+stderr).

    The exit code is read off the completed process, never after a pipe.
    """
    proc = subprocess.run([sys.executable, SCRIPT] + list(args),
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


class SeedShape(unittest.TestCase):
    def setUp(self):
        self.seed = load()

    def test_exactly_seventy_cases_in_the_directive_mix(self):
        cases = self.seed["cases"]
        self.assertEqual(len(cases), 70, "section 27's morning seed is 70 cases")
        counted = {}
        for case in cases:
            counted[case["track"]] = counted.get(case["track"], 0) + 1
        self.assertEqual(counted, EXPECTED_MIX)
        self.assertEqual(self.seed["mix"], EXPECTED_MIX,
                         "the seed's own declared mix must match what it holds")

    def test_case_ids_are_unique(self):
        ids = [c["id"] for c in self.seed["cases"]]
        self.assertEqual(len(set(ids)), len(ids))

    def test_every_expected_answer_is_in_its_allowed_set(self):
        for case in self.seed["cases"]:
            self.assertIn(case["expected"], case["allowed"], case["id"])

    def test_every_critical_case_names_a_critical_class(self):
        classes = set(self.seed["critical_classes"])
        self.assertTrue(classes, "the seed must carry section 28's class list")
        n_critical = 0
        for case in self.seed["cases"]:
            if case["critical"]:
                n_critical += 1
                self.assertIn(case["critical_class"], classes, case["id"])
            else:
                self.assertIsNone(case["critical_class"], case["id"])
        self.assertGreater(n_critical, 0, "a suite with no critical case cannot fail section 28")

    def test_every_case_carries_a_japanese_input_question_and_rationale(self):
        for case in self.seed["cases"]:
            for field in ("input", "question", "rationale_ja"):
                self.assertTrue(case[field].strip(), "%s %s" % (case["id"], field))


class Prompts(unittest.TestCase):
    def setUp(self):
        self.seed = load()
        self.out = tempfile.mkdtemp(prefix="jbeq-prompts-")

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)

    def test_one_prompt_file_per_case(self):
        code, out = run("prompts", self.out)
        self.assertEqual(code, 0, out)
        names = sorted(os.listdir(self.out))
        self.assertEqual(len(names), 70, names)
        self.assertEqual(names, sorted("%s.md" % c["id"] for c in self.seed["cases"]))

    def test_no_expected_answer_leaks_into_any_prompt_file(self):
        code, out = run("prompts", self.out)
        self.assertEqual(code, 0, out)
        for case in self.seed["cases"]:
            with open(os.path.join(self.out, "%s.md" % case["id"]),
                      encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn(case["rationale_ja"], text,
                             "%s: the rationale reached the blind prompt" % case["id"])
            self.assertNotIn("expected", text.lower(),
                             "%s: an expected field reached the blind prompt" % case["id"])
            self.assertNotIn("critical", text.lower(),
                             "%s: the severity flag reached the blind prompt" % case["id"])
            # The allowed answers appear in the seed's own canonical order, so
            # position leaks nothing. A prompt that reordered them to put the
            # expected answer anywhere in particular would fail here.
            listed = [line[2:] for line in text.splitlines() if line.startswith("- ")]
            self.assertEqual(listed, case["allowed"], case["id"])
            self.assertIn(case["input"], text, case["id"])
            self.assertIn(case["question"], text, case["id"])

    def test_the_decision_vocabulary_reaches_every_prompt_verbatim(self):
        code, out = run("prompts", self.out)
        self.assertEqual(code, 0, out)
        for case in self.seed["cases"]:
            with open(os.path.join(self.out, "%s.md" % case["id"]),
                      encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn(jbeq_mdm.VOCABULARY_HEADING, text, case["id"])
            self.assertIn(jbeq_mdm.VOCABULARY, text,
                          "%s: the vocabulary block is not verbatim" % case["id"])
            for label in jbeq_mdm.CAUTION_RANK:
                self.assertIn(label, text, "%s: %s undefined" % (case["id"], label))

    def test_the_readme_quotes_the_same_vocabulary_block(self):
        with open(README_PATH, encoding="utf-8") as fh:
            readme = fh.read()
        self.assertIn(jbeq_mdm.VOCABULARY, readme,
                      "the README and the prompt template have drifted apart")

    def test_the_committed_prompts_are_in_step_with_the_seed(self):
        code, out = run("prompts", self.out)
        self.assertEqual(code, 0, out)
        for case in self.seed["cases"]:
            name = "%s.md" % case["id"]
            with open(os.path.join(self.out, name), encoding="utf-8") as fh:
                fresh = fh.read()
            with open(os.path.join(PROMPTS_PATH, name), encoding="utf-8") as fh:
                committed = fh.read()
            self.assertEqual(committed, fresh,
                             "%s: benchmarks/jbeq/mdm/prompts is stale, "
                             "regenerate it" % name)


class Scoring(unittest.TestCase):
    def setUp(self):
        self.seed = load()
        self.dir = tempfile.mkdtemp(prefix="jbeq-answers-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, answers):
        path = os.path.join(self.dir, "answers.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(answers, fh, ensure_ascii=False)
        return path

    def test_a_perfect_answer_file_scores_seventy_of_seventy(self):
        path = self._write({c["id"]: c["expected"] for c in self.seed["cases"]})
        code, out = run("score", path)
        self.assertEqual(code, 0, out)
        self.assertIn("JBEQ-MDM SEED: 70 of 70", out)
        n_critical = sum(1 for c in self.seed["cases"] if c["critical"])
        self.assertIn("critical false merges: 0 of %d" % n_critical, out)
        self.assertIn("critical wrong: 0 of %d" % n_critical, out)
        self.assertIn("conservative wrongs: 0", out)
        self.assertNotIn("NOT READY", out)
        for track, total in EXPECTED_MIX.items():
            self.assertIn("%s %d of %d" % (track.ljust(20), total, total), out)

    def test_one_critical_case_answered_with_a_merge_is_not_ready(self):
        answers = {c["id"]: c["expected"] for c in self.seed["cases"]}
        victim = next(c for c in self.seed["cases"]
                      if c["critical"] and c["expected"] not in ("AUTO-MERGE", "SUGGEST MERGE")
                      and "AUTO-MERGE" in c["allowed"])
        answers[victim["id"]] = "AUTO-MERGE"
        path = self._write(answers)
        code, out = run("score", path)
        self.assertEqual(code, 1, out)
        self.assertIn("JBEQ-MDM NOT READY", out)
        self.assertIn("critical false merges: 1 of", out)
        self.assertIn("critical wrong: 1 of", out)
        self.assertIn("FALSE MERGE", out)
        self.assertIn(victim["id"], out)
        self.assertNotIn("JBEQ-MDM SEED:", out)

    def test_a_critical_wrong_that_is_not_a_merge_is_counted_apart(self):
        """The defect the two lines exist to separate.

        Before 2026-09-05 the scorer printed every critical wrong under the
        heading `critical false merges`, so a run with nine non-merge wrongs
        read as nine false merges. This drives that backwards.
        """
        # KEEP SEPARATE no longer stands in here for a wrong answer: since
        # the 2026-09-06 founder ruling (SCORER_VERSION 2026-09-06.2) it is
        # in the same equivalence class as REJECT MATCH and would score
        # correct (see test_reject_match_and_keep_separate_score_as_one_
        # class_both_ways below). ESCALATE is outside that class and still a
        # genuine non-merge wrong answer.
        answers = {c["id"]: c["expected"] for c in self.seed["cases"]}
        victim = next(c for c in self.seed["cases"]
                      if c["critical"] and c["expected"] == "REJECT MATCH")
        answers[victim["id"]] = "ESCALATE"
        path = self._write(answers)
        code, out = run("score", path)
        self.assertEqual(code, 1, out)
        self.assertIn("JBEQ-MDM NOT READY", out)
        self.assertIn("critical false merges: 0 of", out)
        self.assertIn("critical wrong: 1 of", out)
        self.assertIn(victim["id"], out)
        self.assertNotIn("FALSE MERGE", out)

    def test_a_non_merge_wrong_answer_on_a_false_merge_class_case_is_not_tagged_false_merge(self):
        # 2026-09-06: critical_class is the case's OWN fixed danger label,
        # not a computed diagnosis of the given answer. ID-01 and ID-04 are
        # both classed FALSE MERGE but expect REJECT MATCH; answering either
        # with ESCALATE (not a merge) must never print the literal phrase
        # "FALSE MERGE" (a downstream grep for the exit-gate phrase would
        # read it as a real one, section D's own finding, reproduced here
        # for MM-06's own case in test_a_critical_wrong_that_is_not_a_merge_
        # is_counted_apart above, which happens not to be classed that way).
        answers = {c["id"]: c["expected"] for c in self.seed["cases"]}
        victim = next(c for c in self.seed["cases"]
                      if c["critical"] and c["critical_class"] == "FALSE MERGE"
                      and c["expected"] not in ("AUTO-MERGE", "SUGGEST MERGE"))
        answers[victim["id"]] = "ESCALATE"
        path = self._write(answers)
        code, out = run("score", path)
        self.assertEqual(code, 1, out)
        self.assertIn("critical wrong: 1 of", out)
        self.assertIn("critical false merges: 0 of", out)
        self.assertNotIn("FALSE MERGE", out)
        self.assertIn(victim["id"], out)

    def test_a_more_cautious_wrong_answer_is_named_conservative(self):
        answers = {c["id"]: c["expected"] for c in self.seed["cases"]}
        cautious = next(c for c in self.seed["cases"]
                        if c["critical"] and c["expected"] == "LINK AS RELATED")
        answers[cautious["id"]] = "KEEP SEPARATE"
        path = self._write(answers)
        code, out = run("score", path)
        self.assertEqual(code, 1, out)
        self.assertIn("conservative wrongs: 1 (%s)" % cautious["id"], out)
        self.assertIn("CONSERVATIVE", out)

    def test_a_less_cautious_wrong_answer_is_not_named_conservative(self):
        answers = {c["id"]: c["expected"] for c in self.seed["cases"]}
        reckless = next(c for c in self.seed["cases"]
                        if c["critical"] and c["expected"] == "KEEP SEPARATE")
        answers[reckless["id"]] = "LINK AS RELATED"
        path = self._write(answers)
        code, out = run("score", path)
        self.assertEqual(code, 1, out)
        self.assertIn("critical wrong: 1 of", out)
        self.assertIn("conservative wrongs: 0", out)
        self.assertNotIn("CONSERVATIVE", out)

    def test_the_scorer_version_string_appears_in_the_output(self):
        answers = {c["id"]: c["expected"] for c in self.seed["cases"]}
        path = self._write(answers)
        code, out = run("score", path)
        self.assertEqual(code, 0, out)
        self.assertIn("scorer: %s" % jbeq_mdm.SCORER_VERSION, out)
        self.assertIn("REJECT MATCH and KEEP SEPARATE score as one class "
                      "for a refuted identity, founder ruling 2026-09-06", out)

    def test_reject_match_and_keep_separate_score_as_one_class_both_ways(self):
        """Founder ruling 2026-09-06 (question UI): one equivalence class
        for scoring, and the engine's proposal gate still decides which of
        the two it says. Both directions score correct, and the record
        names which answer the engine actually gave."""
        answers = {c["id"]: c["expected"] for c in self.seed["cases"]}
        to_keep_separate = next(c for c in self.seed["cases"]
                                if c["critical"] and c["expected"] == "REJECT MATCH")
        to_reject_match = next(c for c in self.seed["cases"]
                               if c["critical"] and c["expected"] == "KEEP SEPARATE")
        answers[to_keep_separate["id"]] = "KEEP SEPARATE"
        answers[to_reject_match["id"]] = "REJECT MATCH"
        path = self._write(answers)
        code, out = run("score", path)
        self.assertEqual(code, 0, out)
        self.assertIn("JBEQ-MDM SEED: 70 of 70", out)
        self.assertNotIn("NOT READY", out)
        self.assertIn("critical wrong: 0 of", out)
        self.assertIn(
            "equivalence class %s: expected REJECT MATCH, engine said "
            "KEEP SEPARATE" % to_keep_separate["id"], out)
        self.assertIn(
            "equivalence class %s: expected KEEP SEPARATE, engine said "
            "REJECT MATCH" % to_reject_match["id"], out)

    def test_a_merge_against_a_keep_separate_expectation_still_scores_wrong(self):
        """The equivalence class holds only REJECT MATCH and KEEP SEPARATE;
        a merge answer is in neither, so it is unchanged by the ruling:
        still wrong, and still a false merge when the case is critical."""
        answers = {c["id"]: c["expected"] for c in self.seed["cases"]}
        victim = next(c for c in self.seed["cases"]
                      if c["critical"] and c["expected"] == "KEEP SEPARATE")
        answers[victim["id"]] = "AUTO-MERGE"
        path = self._write(answers)
        code, out = run("score", path)
        self.assertEqual(code, 1, out)
        self.assertIn("JBEQ-MDM NOT READY", out)
        self.assertIn("critical false merges: 1 of", out)
        self.assertIn("critical wrong: 1 of", out)
        self.assertIn(victim["id"], out)
        self.assertIn("FALSE MERGE", out)

    def test_a_non_critical_case_answered_wrong_still_scores(self):
        answers = {c["id"]: c["expected"] for c in self.seed["cases"]}
        victim = next(c for c in self.seed["cases"] if not c["critical"])
        answers[victim["id"]] = next(a for a in victim["allowed"]
                                     if a != victim["expected"])
        path = self._write(answers)
        code, out = run("score", path)
        self.assertEqual(code, 0, out)
        self.assertIn("JBEQ-MDM SEED: 69 of 70", out)

    def test_a_missing_case_is_named_and_never_counted_as_passed(self):
        answers = {c["id"]: c["expected"] for c in self.seed["cases"]}
        dropped = self.seed["cases"][0]["id"]
        del answers[dropped]
        path = self._write(answers)
        code, out = run("score", path)
        self.assertEqual(code, 0, out)
        self.assertIn("NO-DATA", out)
        self.assertIn(dropped, out)
        self.assertIn("JBEQ-MDM SEED: 69 of 70", out)

    def test_an_empty_answer_file_exits_three(self):
        path = self._write({})
        code, out = run("score", path)
        self.assertEqual(code, 3, out)
        self.assertIn("NO-DATA", out)
        self.assertNotIn("JBEQ-MDM SEED:", out)
        self.assertNotIn("NOT READY", out)

    def test_an_unreadable_answer_file_is_nodata_not_a_pass(self):
        code, out = run("score", os.path.join(self.dir, "no-such-file.json"))
        self.assertEqual(code, 3, out)
        self.assertIn("NO-DATA", out)

    def test_a_phantom_case_id_prints_no_data_and_exits_two(self):
        """M5 (~/.claude/evidence/reflection-measures-2026-09-07.md): the
        scorer once printed "direct-answered 28 of 25", an impossible
        ratio, and nobody read it for a day. A phantom case id (one absent
        from the seed) added to an otherwise perfect answer file drives
        the same shape: more answered cases than the seed's own
        population. The one ratio printer this refuses through catches it
        before any other ratio is computed, prints the NO-DATA line naming
        the impossible pair, and exits 2 rather than EXIT_NODATA (3): this
        is a corrupt/impossible input, never an ordinary missing record."""
        answers = {c["id"]: c["expected"] for c in self.seed["cases"]}
        answers["PHANTOM-DOES-NOT-EXIST"] = "AUTO-MERGE"
        path = self._write(answers)
        code, out = run("score", path)
        self.assertEqual(code, jbeq_mdm.EXIT_IMPOSSIBLE_RATIO, out)
        self.assertIn("NO-DATA: impossible ratio answered 71 of 70", out)
        self.assertNotIn("JBEQ-MDM SEED:", out)
        self.assertNotIn("NOT READY", out)


class EngineDecidedDerivation(unittest.TestCase):
    """review-u1-2026-09-06.md finding 1: engine-decided is derived from a
    decisions.jsonl beside the answers file (rule_fired != "track-
    unsupported"), a per-case fact about what the engine actually
    returned, never from the ENGINE_TRACKS hardcoded track-name set, which
    is wrong the moment the engine learns to decide a track that set does
    not name (temporal, since round 5 of scripts/jbeq_decide.py)."""

    def setUp(self):
        self.seed = load()
        self.dir = tempfile.mkdtemp(prefix="jbeq-engine-decided-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, answers, decisions_rows=None):
        path = os.path.join(self.dir, "answers.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(answers, fh, ensure_ascii=False)
        if decisions_rows is not None:
            with open(os.path.join(self.dir, "decisions.jsonl"), "w",
                      encoding="utf-8") as fh:
                for row in decisions_rows:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return path

    def test_temporal_track_counts_as_engine_decided_when_rule_fired_says_so(self):
        # The exact defect: ENGINE_TRACKS never names "temporal", but the
        # engine has decided that track since round 5. A decisions.jsonl
        # naming an ordinary rule id for every case (temporal included)
        # must count every one of them as engine-decided.
        answers = {c["id"]: c["expected"] for c in self.seed["cases"]}
        decisions_rows = [
            {"case_id": c["id"], "answer": answers[c["id"]],
             "rule_fired": "1", "why": "x"}
            for c in self.seed["cases"]
        ]
        path = self._write(answers, decisions_rows)
        code, out = run("score", path)
        self.assertEqual(code, 0, out)
        self.assertIn("engine-decided: 70 of 70", out)
        self.assertIn(
            "direct-answered (not blind, not evidence about the engine): 0 of 0",
            out,
        )

    def test_track_unsupported_rows_stay_direct_answered_even_when_correct(self):
        answers = {c["id"]: c["expected"] for c in self.seed["cases"]}
        unsupported_ids = {c["id"] for c in self.seed["cases"]
                           if c["track"] in ("requirements", "survivorship")}
        decisions_rows = [
            {"case_id": c["id"], "answer": answers[c["id"]],
             "rule_fired": ("track-unsupported" if c["id"] in unsupported_ids
                            else "1"),
             "why": "x"}
            for c in self.seed["cases"]
        ]
        path = self._write(answers, decisions_rows)
        code, out = run("score", path)
        self.assertEqual(code, 0, out)
        n = len(unsupported_ids)
        total = len(self.seed["cases"])
        self.assertIn("engine-decided: %d of %d" % (total - n, total - n), out)
        self.assertIn(
            "direct-answered (not blind, not evidence about the engine): "
            "%d of %d" % (n, n),
            out,
        )

    def test_falls_back_to_engine_tracks_when_no_decisions_file_present(self):
        answers = {c["id"]: c["expected"] for c in self.seed["cases"]}
        path = self._write(answers, decisions_rows=None)
        code, out = run("score", path)
        self.assertEqual(code, 0, out)
        engine_total = sum(1 for c in self.seed["cases"]
                          if c["track"] in jbeq_mdm.ENGINE_TRACKS)
        self.assertIn("engine-decided: %d of %d" % (engine_total, engine_total), out)

    def test_an_engine_decided_equivalence_hit_counts_toward_engine_decided(self):
        # Regression for a bug this same 2026-09-06 change introduced and
        # caught before landing: engine_passed here used to compare with a
        # bare ==, never answers_equivalent(), so an engine-decided
        # equivalence hit inflated `passed` (via score()) without inflating
        # `engine_passed`, and direct_passed = passed - engine_passed
        # absorbed the difference as a phantom direct-answered case
        # (observed on a real run: "direct-answered ... 28 of 25", more
        # answers than the seed has direct-answered cases).
        answers = {c["id"]: c["expected"] for c in self.seed["cases"]}
        victim = next(c for c in self.seed["cases"]
                      if c["track"] in jbeq_mdm.ENGINE_TRACKS
                      and c["expected"] == "REJECT MATCH")
        answers[victim["id"]] = "KEEP SEPARATE"
        decisions_rows = [
            {"case_id": c["id"], "answer": answers[c["id"]],
             "rule_fired": "1", "why": "x"}
            for c in self.seed["cases"]
        ]
        path = self._write(answers, decisions_rows)
        code, out = run("score", path)
        self.assertEqual(code, 0, out)
        self.assertIn("engine-decided: 70 of 70", out)
        self.assertIn(
            "direct-answered (not blind, not evidence about the engine): "
            "0 of 0", out,
        )


class DirectOverrideExcludedFromEngineDecided(unittest.TestCase):
    """audit-unseen-5-run-2026-09-07.md section 0: a human override recorded
    in direct-answers.json replaces the engine's answer in the merged
    answers.json, but decisions.jsonl's rule_fired is never touched by that
    override, so it still reads whatever the engine decided before being
    overridden. A case direct-answers.json names must never count as
    engine-decided, whatever its rule_fired says.

    Fixture: 4 cases, 2 the engine decided and nobody touched (T-01, T-02),
    2 the engine decided and a human then overrode via direct-answers.json
    (T-03, T-04), with rule_fired on all four still reading an ordinary
    rule id, never "track-unsupported". Before the fix engine-decided
    read 4 of 4 and direct-answered read 0 of 0, crediting the engine with
    two answers a person wrote (this is the U5 defect: engine-decided
    reported 25 of 30 instead of the honest 20 of 25). After the fix
    engine-decided must read 2 of 2 and direct-answered 2 of 2."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="jbeq-direct-override-")
        self.seed = {
            "cases": [
                {"id": "T-01", "track": "address",
                 "expected": "KEEP SEPARATE", "critical": False},
                {"id": "T-02", "track": "address",
                 "expected": "ESCALATE", "critical": False},
                {"id": "T-03", "track": "address",
                 "expected": "SUGGEST MERGE", "critical": False},
                {"id": "T-04", "track": "address",
                 "expected": "AUTO-MERGE", "critical": False},
            ],
            "scoring": {"merge_answers": ["AUTO-MERGE", "SUGGEST MERGE"]},
        }
        self.seed_path = os.path.join(self.dir, "seed.json")
        with open(self.seed_path, "w", encoding="utf-8") as fh:
            json.dump(self.seed, fh, ensure_ascii=False)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_overridden_cases_move_from_engine_decided_to_direct_answered(self):
        # The merged answers.json: all four correct, T-03 and T-04 correct
        # only because a human overrode the engine's original (wrong) call.
        answers = {
            "T-01": "KEEP SEPARATE",
            "T-02": "ESCALATE",
            "T-03": "SUGGEST MERGE",
            "T-04": "AUTO-MERGE",
        }
        answers_path = os.path.join(self.dir, "answers.json")
        with open(answers_path, "w", encoding="utf-8") as fh:
            json.dump(answers, fh, ensure_ascii=False)

        # decisions.jsonl: rule_fired is an ordinary rule id for all four,
        # including T-03/T-04, whose "answer" field is what the engine
        # said BEFORE the override (both wrong) and is never corrected.
        decisions_rows = [
            {"case_id": "T-01", "answer": "KEEP SEPARATE",
             "rule_fired": "1", "why": "x"},
            {"case_id": "T-02", "answer": "ESCALATE",
             "rule_fired": "1", "why": "x"},
            {"case_id": "T-03", "answer": "ESCALATE",
             "rule_fired": "1", "why": "x"},
            {"case_id": "T-04", "answer": "KEEP SEPARATE",
             "rule_fired": "B", "why": "x"},
        ]
        with open(os.path.join(self.dir, "decisions.jsonl"), "w",
                  encoding="utf-8") as fh:
            for row in decisions_rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

        # direct-answers.json: the human override record, exactly what the
        # two merge.py scripts on record write beside answers.json.
        direct = {"T-03": "SUGGEST MERGE", "T-04": "AUTO-MERGE"}
        with open(os.path.join(self.dir, "direct-answers.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(direct, fh, ensure_ascii=False)

        code, out = run("score", "--seed", self.seed_path, answers_path)
        self.assertEqual(code, 0, out)
        self.assertIn("engine-decided: 2 of 2", out)
        self.assertIn(
            "direct-answered (not blind, not evidence about the engine): "
            "2 of 2", out,
        )


class CanonicalTrackNames(unittest.TestCase):
    """A seed with an unknown track name is refused at load, not scored.

    U2's five requirements cases spelled "requirements-understanding",
    which is neither the seed's own vocabulary nor
    scripts/jbeq_decide.py's UNSUPPORTED_TRACKS, so the engine ran its
    general rule table over them instead of refusing them as
    track-unsupported (hub PR 411, cases V-22 and V-37). This drives the
    fix backwards: load_seed must catch the typo, distinguishing it from
    an omission by naming the offending case id and value.
    """

    EIGHT_TRACKS = {
        "address", "entity-object", "hierarchy", "identifier",
        "match-or-no-merge", "requirements", "survivorship", "temporal",
    }

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="jbeq-track-names-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write_seed(self, track):
        path = os.path.join(self.dir, "seed.json")
        seed = {
            "cases": [{
                "id": "Z-01",
                "track": track,
                "input": "x",
                "question": "x",
                "allowed": ["NO-DATA"],
                "expected": "NO-DATA",
                "critical": False,
                "critical_class": None,
                "rationale_ja": "x",
            }],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(seed, fh, ensure_ascii=False)
        return path

    def test_the_eight_canonical_tracks_load(self):
        self.assertEqual(jbeq_mdm.CANONICAL_TRACKS, self.EIGHT_TRACKS)
        for track in self.EIGHT_TRACKS:
            path = self._write_seed(track)
            seed = jbeq_mdm.load_seed(path)
            self.assertIsNotNone(seed, track)
            self.assertEqual(seed["cases"][0]["track"], track)

    def test_an_unknown_track_is_refused_naming_the_case_and_value(self):
        bad_seed = self._write_seed("requirements-understanding")
        answers_path = os.path.join(self.dir, "answers.json")
        with open(answers_path, "w", encoding="utf-8") as fh:
            json.dump({"Z-01": "NO-DATA"}, fh)
        code, out = run("score", answers_path, "--seed", bad_seed)
        self.assertEqual(code, jbeq_mdm.EXIT_NODATA, out)
        self.assertIn("NO-DATA", out)
        self.assertIn("Z-01", out)
        self.assertIn("requirements-understanding", out)

    def test_load_seed_returns_none_for_an_unknown_track(self):
        path = self._write_seed("requirements-understanding")
        self.assertIsNone(jbeq_mdm.load_seed(path))

    def test_the_real_seed_files_carry_only_canonical_track_names(self):
        for rel in (
            "benchmarks/jbeq/mdm/seed-2026-09-05.json",
            "benchmarks/jbeq/mdm/unseen-2026-09-06.json",
            "benchmarks/jbeq/mdm/unseen-2-2026-09-06.json",
        ):
            path = os.path.join(REPO, rel)
            seed = jbeq_mdm.load_seed(path)
            self.assertIsNotNone(seed, rel)


class UnseenGateWiring(unittest.TestCase):
    """M8: prompts and score refuse an unseen-* seed unless
    scripts/unseen_set_gate.py passes it, or the caller passes
    --regression. This drives the wiring, not the gate's own logic (see
    scripts/test_unseen_set_gate.py for that)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="jbeq-unseen-wiring-")
        self.seed_path = os.path.join(self.dir, "unseen-9-2026-09-07.json")
        seed = {
            "cases": [{
                "id": "Z-01",
                "track": "address",
                "input": "x",
                "question": "x",
                "allowed": ["NO-DATA"],
                "expected": "NO-DATA",
                "critical": False,
                "critical_class": None,
            }],
            "scoring": {"merge_answers": ["AUTO-MERGE", "SUGGEST MERGE"]},
        }
        with open(self.seed_path, "w", encoding="utf-8") as fh:
            json.dump(seed, fh)
        # No sibling -RECORD.md is written: the gate reads this as
        # NO-DATA (no audit at all), which refuses both subcommands
        # without --regression.

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_prompts_on_an_ungated_unseen_seed_exits_two(self):
        out_dir = os.path.join(self.dir, "prompts-out")
        code, out = run("prompts", out_dir, "--seed", self.seed_path)
        self.assertEqual(code, jbeq_mdm.EXIT_IMPOSSIBLE_RATIO, out)
        self.assertIn("REFUSED", out)
        self.assertIn("NO-DATA", out)
        self.assertFalse(os.path.isdir(out_dir), "refused, so nothing was written")

    def test_prompts_with_regression_proceeds(self):
        out_dir = os.path.join(self.dir, "prompts-out")
        code, out = run("prompts", out_dir, "--seed", self.seed_path, "--regression")
        self.assertEqual(code, 0, out)
        self.assertIn("REGRESSION", out)
        self.assertEqual(sorted(os.listdir(out_dir)), ["Z-01.md"])

    def test_score_on_an_ungated_unseen_seed_exits_two(self):
        answers_path = os.path.join(self.dir, "answers.json")
        with open(answers_path, "w", encoding="utf-8") as fh:
            json.dump({"Z-01": "NO-DATA"}, fh)
        code, out = run("score", answers_path, "--seed", self.seed_path)
        self.assertEqual(code, jbeq_mdm.EXIT_IMPOSSIBLE_RATIO, out)
        self.assertIn("REFUSED", out)
        self.assertNotIn("JBEQ-MDM SEED:", out)

    def test_score_with_regression_proceeds(self):
        answers_path = os.path.join(self.dir, "answers.json")
        with open(answers_path, "w", encoding="utf-8") as fh:
            json.dump({"Z-01": "NO-DATA"}, fh)
        code, out = run("score", answers_path, "--seed", self.seed_path, "--regression")
        self.assertEqual(code, 0, out)
        self.assertIn("REGRESSION", out)
        self.assertIn("JBEQ-MDM SEED: 1 of 1", out)


if __name__ == "__main__":
    unittest.main()
