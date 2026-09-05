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
        answers = {c["id"]: c["expected"] for c in self.seed["cases"]}
        victim = next(c for c in self.seed["cases"]
                      if c["critical"] and c["expected"] == "REJECT MATCH")
        answers[victim["id"]] = "KEEP SEPARATE"
        path = self._write(answers)
        code, out = run("score", path)
        self.assertEqual(code, 1, out)
        self.assertIn("JBEQ-MDM NOT READY", out)
        self.assertIn("critical false merges: 0 of", out)
        self.assertIn("critical wrong: 1 of", out)
        self.assertIn(victim["id"], out)
        self.assertNotIn("FALSE MERGE", out)

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


if __name__ == "__main__":
    unittest.main()
